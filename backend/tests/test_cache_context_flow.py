import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.api import campaigns as api
from app.brand.retrieval import RetrievalError
from app.campaign_agent import agent_state, tool_receipt
from app.llm.client import strict_output_schema
from app.llm.prompt_payloads import (
    draft_prompt,
    lean_plan_context,
    planning_prompt,
    shared_draft_groups,
)


def packet(extra, channel='linkedin'):
    passages = [
        {'chunk_id': 'brand', 'relative_path': 'brand/voice/voice.md', 'content': 'Exact brand wording.'},
        {'chunk_id': 'channel', 'relative_path': 'brand/channels/linkedin.md', 'content': 'Keep the hook early.'},
        {'chunk_id': extra, 'relative_path': 'brand/products/product.md', 'content': f'Exact support {extra}.'}]
    return {'evidence': passages, 'rule_pack': {'format': channel, 'limits': [], 'evidence': passages}}


def test_draft_schema_is_identical_across_disjoint_citation_sets():
    first = api.draft_output_model([{'chunk_id': 'first'}])
    second = api.draft_output_model([{'chunk_id': 'second'}])
    assert first is second is api.GeneratedDraft
    strict_output_schema(first)
    assert first.model_json_schema()['properties']['source_ids']['items'] == {'type': 'string'}


def test_draft_prefixes_are_shared_without_adding_or_duplicating_evidence():
    packets = {'a': packet('a'), 'b': packet('b')}
    before = deepcopy(packets)
    groups = shared_draft_groups(packets)
    first, second = [draft_prompt(packets[key], {'asset': {'id': key}}, shared=groups) for key in packets]
    assert first[:2] == second[:2]
    assert first[2:] != second[2:]
    for key, messages in zip(packets, (first, second), strict=True):
        passages = [p for message in messages[:3] for section in json.loads(message['content']).values()
                    for p in section['evidence']]
        assert {p['chunk_id'] for p in passages} == {'brand', 'channel', key}
        assert len(passages) == 3
        assert all(p['ref'] == p['chunk_id'] for p in passages)
    assert packets == before


def test_planning_originals_precede_campaign_ids_and_keep_resolvable_refs():
    context = lean_plan_context({'evidence': [], 'asset_packages': {'a': packet('a')}})
    messages = planning_prompt(context, {'conversation': [{'role': 'user', 'text': 'Changing brief'}]})
    first = json.loads(messages[0]['content'])
    assert list(first) == ['original_sources']
    assert 'asset_packages' not in messages[0]['content']
    payload = json.loads(messages[1]['content'])
    refs = {p['ref'] for p in first['original_sources']}
    assert all(set(ids) <= refs for ids in payload['brand_context']['source_groups'].values())


def test_mutation_receipt_excludes_copy_and_source_snapshots_but_read_exposes_copy():
    pending = {'id': 'question', 'text': 'Which claim?', 'options': ['Keep', 'Omit'],
               'source_snapshot': {'source': 'hash' * 1000}}
    campaign = SimpleNamespace(id='c', version=2, state='clarifying', data={
        'pending_question': pending, 'requirements': {'topic': 'Awareness', 'assets': ['large']},
        'generation': {'drafts': [{'asset_id': 'a', 'body': 'Long saved copy', 'validation': {'history': ['long']}}]}})
    receipt = tool_receipt(campaign, 'revise_draft', {'asset_id': 'a'}, {'version': 2})
    assert receipt['status'] == 'needs_clarification'
    assert receipt['question'] == 'Which claim?'
    assert receipt['asset_id'] == 'a'
    assert 'Long saved copy' not in str(receipt)
    assert 'source_snapshot' not in str(receipt)
    assert agent_state(campaign, full=True)['drafts'][0]['body'] == 'Long saved copy'


def test_generation_rejects_invalid_citations_before_validation_or_saving(monkeypatch):
    closed = []
    class Client:
        def __init__(self, *args): pass
        def close(self): closed.append(True)
        def run(self, request, output_model):
            return SimpleNamespace(run_id='bad-citation', parsed=output_model.model_validate({
                'title': 'Title', 'body': 'Copy', 'source_ids': ['invented'],
                'subject': None, 'preheader': None, 'sender_name': None, 'byline': None}))
    monkeypatch.setattr(api, 'TrackedOpenAI', Client)
    monkeypatch.setattr(api, 'load_asset_package', lambda *args: packet('a'))
    campaign = SimpleNamespace(id='c', brand_id='b', data={})
    asset = {'id': 'a', 'format': 'linkedin', 'purpose': 'Awareness'}
    with pytest.raises(RetrievalError, match='unknown evidence'):
        api.generate_asset(None, SimpleNamespace(magi_writer_model='test'), campaign,
                           {'goal': 'Awareness', 'assets': [asset]}, asset, {}, [], [])
    assert closed == [True]


@pytest.mark.parametrize('recover', [True, False])
def test_fixed_verifier_logs_incorrect_coverage_without_extra_retry(recover):
    from app.brand.plan_verification import verify_plan
    from app.config import Settings
    calls = []
    def run(request, output_model):
        calls.append(request)
        ids = [] if recover and len(calls) == 2 else ['unchanged-asset']
        return SimpleNamespace(run_id=str(len(calls)), parsed=output_model.model_validate({
            'reviewed_asset_ids': ids, 'defects': []}))
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    plan = {'assets': [], 'goal': 'Awareness', 'assumptions': [], 'constraints': [], 'questions': []}
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert result['plan_review']['status'] == 'incomplete'
    assert result['assets'] == []
    assert len(calls) == 1
    assert any('coverage' in attempt.get('error', '') for attempt in result['plan_review']['attempts'])


def test_one_citation_only_correction_preserves_copy_and_still_validates(monkeypatch):
    from app.brand import draft_validation
    calls=[]
    original={'title':'Title','body':'Unchanged supported copy.','source_ids':['spliced-id'],
              'subject':None,'preheader':None,'sender_name':None,'byline':None}
    class Client:
        def __init__(self,*args):pass
        def close(self):pass
        def run(self,request,output_model):
            calls.append(request.operation)
            if request.operation=='generate_campaign_asset':
                return SimpleNamespace(run_id='writer',parsed=output_model.model_validate(original))
            assert request.operation=='campaign_asset_citation_repair'
            payload=json.loads(request.input[-1]['content'])['citation_repair']
            assert payload['draft']==original
            assert 'a' in payload['allowed_source_ids']
            assert 'body' not in output_model.model_json_schema()['properties']
            return SimpleNamespace(run_id='citation',parsed=output_model.model_validate({'source_ids':['a']}))
    def validate(client,settings,campaign,asset,draft,package):
        assert draft['body']==original['body']
        assert draft['source_ids']==['a']
        return {**draft,'status':'needs_review'}
    monkeypatch.setattr(api,'TrackedOpenAI',Client)
    monkeypatch.setattr(api,'load_asset_package',lambda *args:packet('a'))
    monkeypatch.setattr(draft_validation,'validate_and_repair',validate)
    asset={'id':'a','format':'linkedin','purpose':'Awareness'}
    result=api.generate_asset(None,SimpleNamespace(magi_writer_model='test'),
        SimpleNamespace(id='c',brand_id='b',data={}),{'goal':'Awareness','assets':[asset]},asset,{},[],[])
    assert calls==['generate_campaign_asset','campaign_asset_citation_repair']
    assert result['citation_repair_run_id']=='citation'
    assert result['run_id']=='writer'
