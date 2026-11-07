import json
from itertools import combinations
from types import SimpleNamespace

import pytest
from review_helpers import review_payload

from app.brand import conflict_coverage as coverage
from app.brand.retrieval import RetrievalError
from app.brand.review_budget import review_inputs
from app.campaign_agent import recover_preparation
from app.config import Settings
from app.llm.client import ModelRunError, RunLoggingError


def test_batches_preserve_every_passage_pair_and_brief():
    passages = [{'ref': f's{i}', 'content': str(i) * 400} for i in range(8)]
    content = {'conversation': ['original brief'], 'passages': passages,
               'proposed_review': {'conflicts': [], 'limitations': ['earlier findings']}}
    fits = lambda value: len(json.dumps(value)) <= 2100
    batches = review_inputs(content, fits)
    assert len(batches) > 1
    pairs = set()
    for batch in batches:
        assert fits(batch)
        assert batch['conversation'] == content['conversation']
        assert batch['proposed_review'] == content['proposed_review']
        assert all(p in passages for p in batch['passages'])
        pairs.update(frozenset(pair) for pair in combinations([p['ref'] for p in batch['passages']], 2))
    assert pairs == {frozenset(pair) for pair in combinations([p['ref'] for p in passages], 2)}


def test_second_pass_budget_growth_recovers_without_truncating(monkeypatch):
    monkeypatch.setattr(coverage, 'token_count', len)
    monkeypatch.setattr(coverage, 'MAX_INPUT_TOKENS', 1_000_000)
    passages = [{'chunk_id': f's{i}', 'content': str(i) * 1200} for i in range(8)]
    requests = []
    def run(request, output_model):
        requests.append(request)
        if request.operation == 'campaign_conflict_find':
            # The first pass fits; its output pushes the independent check over.
            monkeypatch.setattr(coverage, 'MAX_INPUT_TOKENS',
                len(json.dumps(request.input) + request.instructions + json.dumps(output_model.model_json_schema())) + 100)
        return SimpleNamespace(run_id=str(len(requests)), parsed=output_model.model_validate({
            'conflicts': [], 'limitations': ['Review context ' * 80] if len(requests) == 1 else [],
            'blocking_gaps': []}))
    result = coverage.review_campaign(SimpleNamespace(run=run), Settings(_env_file=None),
        SimpleNamespace(id='c', brand_id='b', data={}), [], passages)
    assert result['report']['status'] == 'ready'
    assert result['report']['source_count'] == 8
    checks = [review_payload(r) for r in requests if r.operation == 'campaign_conflict_check']
    assert len(checks) > 1
    assert {p['content'] for batch in checks for p in batch['passages']} == {p['content'] for p in passages}


def test_impossible_budget_is_explicit_not_silent_truncation():
    with pytest.raises(RetrievalError) as error:
        review_inputs({'passages': [{'content': 'too large'}]}, lambda _: False)
    assert error.value.code == 'review_context_too_large'


@pytest.mark.parametrize('status,error', [
    ('timeout', {}), ('validation_error', {}),
    ('provider_error', {'status_code': 429}), ('provider_error', {'status_code': 503}),
    ('provider_error', {'root_cause': {'type': 'RemoteProtocolError'}})])
def test_preparation_recovers_once_and_records_it(monkeypatch, status, error):
    monkeypatch.setattr('app.campaign_agent.time.sleep', lambda _: None)
    events = []
    turn = SimpleNamespace(data={'executions': [{'tool': 'extract_requirements'}]})
    session = SimpleNamespace(get=lambda *a: SimpleNamespace(error=error),
        rollback=lambda: events.append('rollback'), expire_all=lambda: None,
        commit=lambda: events.append('commit'))
    client = SimpleNamespace(close=lambda: events.append('close'))
    calls = []
    def action():
        calls.append(True)
        if len(calls) == 1:
            raise ModelRunError('failed', status)
        return 'saved'
    assert recover_preparation(action, client, session, turn, 'retrieve_context') == 'saved'
    assert len(calls) == 2
    assert turn.data['executions'] == [{'tool': 'extract_requirements'}]
    assert turn.data['recoveries'][0]['run_id'] == 'failed'
    assert events == ['rollback', 'commit', 'close']


def test_persistent_transient_error_is_bounded(monkeypatch):
    monkeypatch.setattr('app.campaign_agent.time.sleep', lambda _: None)
    calls = []
    def action():
        calls.append(True)
        raise ModelRunError('failed', 'timeout')
    session = SimpleNamespace(get=lambda *a: None, rollback=lambda: None,
        expire_all=lambda: None, commit=lambda: None)
    with pytest.raises(ModelRunError):
        recover_preparation(action, SimpleNamespace(close=lambda: None), session,
            SimpleNamespace(data={}), 'retrieve_context')
    assert len(calls) == 2


@pytest.mark.parametrize('error', [RetrievalError('Missing essential evidence'),
    RunLoggingError('run'), ModelRunError('run', 'refused')])
def test_substantive_and_logging_errors_are_not_retried(error):
    calls = []
    def action():
        calls.append(True)
        raise error
    with pytest.raises(type(error)):
        recover_preparation(action, None, SimpleNamespace(get=lambda *a: None),
            SimpleNamespace(data={}), 'retrieve_context')
    assert len(calls) == 1


def test_agent_model_selection_uses_bounded_runtime_recovery(monkeypatch):
    from app.campaign_agent import run_agent_step
    monkeypatch.setattr('app.campaign_agent.time.sleep', lambda _: None)
    calls = []
    def run(request):
        calls.append(request)
        if len(calls) == 1:
            raise ModelRunError('timeout', 'timeout')
        return 'selected tool'
    session = SimpleNamespace(get=lambda *a: None, rollback=lambda: None,
        expire_all=lambda: None, commit=lambda: None)
    turn = SimpleNamespace(data={})
    assert run_agent_step(SimpleNamespace(run=run, close=lambda: None),
        SimpleNamespace(operation='campaign_agent_step'), session, turn=turn) == 'selected tool'
    assert len(calls) == 2
    assert turn.data['recoveries'][0]['tool'] == 'campaign_agent_step'


def test_overlap_merges_same_choice_but_preserves_distinct_conflicts():
    from app.brand.review_budget import deduplicate_findings
    first = {'id': 'one', 'affected_asset_ids': ['a'], 'decision_type': 'campaign_choice',
        'grounding': [{'origin': 'user', 'source_ref': None, 'quote': 'Send these posts for review and scheduling'},
                      {'origin': 'source', 'source_ref': 'rule', 'quote': 'Post nonlaunch content live'}],
        'source_ids': ['rule']}
    second = {**first, 'id': 'two', 'source_ids': ['rule', 'extra']}
    different = {**first, 'id': 'three', 'affected_asset_ids': ['other']}
    result = deduplicate_findings([first, second, different])
    assert [f['id'] for f in result] == ['one', 'three']
    assert result[0]['source_ids'] == ['rule', 'extra']
    assert first['source_ids'] == ['rule']


def test_unverified_citations_offer_recovery_without_recording_approval():
    from app.api.campaigns import ask_review_recovery
    from app.brand.decisions import record_answer

    campaign = SimpleNamespace(data={'messages': [], 'decisions': [],
                                     'conflict_coverage': {'status': 'incomplete'}},
                               state='planning_error', version=1)
    assert ask_review_recovery(campaign, RetrievalError(
        'Citations failed', 'conflict_citations_unverified'))
    question = campaign.data['pending_question']
    assert question['options'][0] == 'Retry evidence review'
    assert campaign.state == 'clarifying'
    assert campaign.data['messages'][-1]['question'] == question
    record_answer(campaign, question, 'Retry evidence review')
    assert campaign.data['pending_question'] is None
    assert campaign.data['decisions'] == []
    assert campaign.data['questions'][-1]['answer'] == 'Retry evidence review'
    assert campaign.data['conflict_coverage']['status'] == 'incomplete'


def test_unrelated_errors_do_not_offer_citation_recovery():
    from app.api.campaigns import ask_review_recovery

    campaign = SimpleNamespace(data={}, state='planning_error', version=1)
    assert not ask_review_recovery(campaign, RetrievalError('Missing essential evidence'))
    assert campaign.data == {}
