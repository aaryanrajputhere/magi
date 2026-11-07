"""Direct review, source integrity, resume and approval contracts; no paid calls."""
import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from review_helpers import review_payload

from app.brand import conflict_coverage as coverage
from app.brand.retrieval import RetrievalError
from app.config import Settings
from app.llm.client import strict_output_schema


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(coverage, 'count_tokens', lambda text: len(text) // 4)


def scenario(kind='hero', compatible=False):
    if kind == 'hero':
        request = 'Use a working room on the product landing page.'
        rule = 'Product pages must use product UI in the hero.'
    else:
        request = 'Schedule the follow-up how-to X post for February 2.'
        rule = 'Launch posts are scheduled. All other X posts are posted live.'
    if compatible:
        request += ' Brand grants an explicit exception for this asset.'
    return request, rule


class ScriptedClient:
    def __init__(self, request, rule, *, compatible=False):
        self.request, self.rule, self.compatible = request, rule, compatible
        self.requests = []

    def run(self, request, output_model):
        self.requests.append(request)
        p = review_payload(request)
        assert request.operation in {'campaign_conflict_find', 'campaign_conflict_check'}
        assert p['conversation'][0]['text'] == self.request
        assert p['passages'][0]['content'] == self.rule
        findings = []
        if request.operation == 'campaign_conflict_check' and not self.compatible:
            findings = [dict(explanation='The requested action conflicts with channel guidance',
                statements=[self.request, self.rule], source_refs=[p['passages'][0]['ref']],
                affected_asset_ids=[], decision_type='campaign_choice',
                question='Which treatment should the campaign use?',
                options=['Follow the applicable channel rule', 'Request a documented exception'])]
        return SimpleNamespace(parsed=output_model.model_validate(
            dict(conflicts=findings, limitations=[], blocking_gaps=[])), run_id=str(len(self.requests)))


@pytest.mark.parametrize('kind', ['hero', 'scheduling'])
@pytest.mark.parametrize('compatible', [False, True])
def test_clean_first_pass_is_checked_against_original_evidence(kind, compatible):
    request, rule = scenario(kind, compatible)
    client = ScriptedClient(request, rule, compatible=compatible)
    c = SimpleNamespace(id='c', brand_id='b', data={})
    findings = coverage.review_campaign(client, Settings(_env_file=None), c,
        [{'role': 'user', 'text': request}], [{'chunk_id': 's1', 'content': rule}])
    assert bool(findings) is not compatible
    assert len(client.requests) == 2
    assert c.data['conflict_coverage']['mode'] == 'direct'
    assert c.data['conflict_coverage']['coverage_challenged']
    if findings:
        assert findings[0]['source_quotes'][0]['quote'] == rule
        assert findings[0]['source_ids'] == ['s1']
        assert findings[0]['question']


def test_reference_schema_forbids_invented_sources_and_assets():
    schema = coverage.direct_schema(['source_0'], ['a1'])
    strict_output_schema(schema)
    issue = dict(explanation='Conflict', statements=['Request', 'Restriction'], source_refs=['source_0'],
        affected_asset_ids=['a1'], decision_type='campaign_choice', question='Choose treatment?', options=['Omit', 'Wait'])
    schema.model_validate(dict(conflicts=[issue], limitations=[], blocking_gaps=[]))
    for field, value in [('source_refs', ['imaginary-rule']), ('affected_asset_ids', ['unknown'])]:
        with pytest.raises(ValidationError):
            schema.model_validate(dict(conflicts=[{**issue, field: value}], limitations=[], blocking_gaps=[]))
    strict_output_schema(coverage.direct_schema([], []))


def test_shared_pool_preserves_queries_and_current_version():
    c = SimpleNamespace(data={'evidence_ledger': {'old': {'chunk_id': 'one', 'content': 'old'}}})
    groups = [{'query': {'id': name}, 'results': [{'chunk_id': 'one', 'content': 'new'}]}
              for name in ['product', 'claims']]
    assert coverage.current_pool(c, groups[0]['results'], groups) == [
        {'chunk_id': 'one', 'content': 'new', 'query_ids': ['claims', 'product']}]


def test_cross_query_sources_are_reviewed_together_without_intermediate_extraction():
    sources = [{'chunk_id': 'product', 'content': 'The product prevents prompt injection.'},
               {'chunk_id': 'legal', 'content': 'Never claim the product prevents prompt injection.'}]
    seen = []
    def run(request, output_model):
        p = review_payload(request)
        seen.append(request.operation)
        assert {e['content'] for e in p['passages']} == {e['content'] for e in sources}
        assert 'rules' not in p and 'decisions' not in p
        value = dict(conflicts=[dict(explanation='Source contradiction',
            statements=[e['content'] for e in sources], source_refs=[e['ref'] for e in p['passages']],
            affected_asset_ids=[], decision_type='claim_wording', question='Which wording should we use?',
            options=['Use the limited approved outcome', 'Omit the outcome claim'])], limitations=[], blocking_gaps=[])
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='run')
    c = SimpleNamespace(id='c', brand_id='b', data={})
    findings = coverage.review_campaign(SimpleNamespace(run=run), Settings(_env_file=None), c, [], sources)
    assert seen == ['campaign_conflict_find', 'campaign_conflict_check']
    assert set(findings[0]['source_ids']) == {'product', 'legal'}


def test_budget_overflow_never_truncates_or_calls_provider(monkeypatch):
    monkeypatch.setattr(coverage, 'MAX_INPUT_TOKENS', 1)
    with pytest.raises(RetrievalError, match='no passages were truncated'):
        coverage.review_campaign(None, Settings(_env_file=None),
            SimpleNamespace(id='c', brand_id='b', data={}), [], [{'chunk_id': 's', 'content': 'Original evidence'}])


def test_independent_check_can_remove_false_positive():
    request, rule = scenario(compatible=True)
    client = ScriptedClient(request, rule, compatible=True)
    original = client.run
    def run(request, output_model):
        result = original(request, output_model)
        if request.operation == 'campaign_conflict_find':
            result.parsed.conflicts = output_model.model_validate(dict(conflicts=[dict(
                explanation='Incorrect first-pass finding', statements=['Prohibited', 'Explicit exception'],
                source_refs=['source_0'], affected_asset_ids=[], decision_type='campaign_choice',
                question='Choose?', options=['Omit', 'Wait'])], limitations=[], blocking_gaps=[])).conflicts
        else:
            assert review_payload(request)['proposed_review']['conflicts']
        return result
    client.run = run
    assert not coverage.review_campaign(client, Settings(_env_file=None),
        SimpleNamespace(id='c', brand_id='b', data={}), [{'role': 'user', 'text': request}],
        [{'chunk_id': 's1', 'content': rule}])


def test_verification_receipt_is_bound_to_saved_content():
    plan = {'goal': 'Launch', 'assets': [{'id': 'a1', 'hook': 'approved wording'}],
                'constraints': [], 'questions': [], 'assumptions': [], 'conflict_review_key': 'review'}
    plan['conflict_verification'] = {'signature': coverage.plan_signature(plan)}
    c = SimpleNamespace(data={'plans': [plan], 'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    assert coverage.approval_error(c) is None
    plan['assets'][0]['hook'] = 'changed wording'
    assert coverage.approval_error(c)


def test_plan_is_rechecked_after_one_repair():
    c = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review',
        'decisions': [{'id': 'd1'}]}})
    plan = {'goal': 'Launch', 'assets': [], 'constraints': [], 'questions': [], 'assumptions': []}
    operations = []
    def run(request, output_model):
        operations.append(request.operation)
        if request.operation == 'campaign_plan_coverage_repair':
            value = {**copy.deepcopy(plan), 'goal': 'Corrected launch'}
        else:
            value = {'reviewed_asset_ids': ['a1'], 'defects': ['Fix goal'] if len(operations) == 1 else []}
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='test')
    result = coverage.verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), c, [], plan)
    c.data['plans'] = [result]
    assert coverage.approval_error(c) is None
    assert result['goal'] == 'Corrected launch'
    assert operations == ['campaign_plan_verification', 'campaign_plan_coverage_repair', 'campaign_plan_verification']


def test_successful_stages_resume_after_provider_failure(tmp_path):
    from sqlalchemy.orm import Session

    from app.brand.chunking import CHUNKER_VERSION
    from app.db import create_database_engine, initialize_database
    from app.models import Brand, Document, DocumentChunk

    request, rule = scenario()
    engine = create_database_engine(f'sqlite:///{tmp_path / "resume.db"}')
    initialize_database(engine)
    with Session(engine) as session:
        session.add(Brand(id='b', name='Example', source_directory='example'))
        session.commit()
        session.add(Document(id='doc', brand_id='b', relative_path='rules.md',
                             content_hash='hash', raw_text=rule))
        session.commit()
        session.add(DocumentChunk(id='s1', document_id='doc', position=0,
            heading_path=[], content=rule, start_offset=0, end_offset=len(rule), start_line=1,
            end_line=1, source_hash='hash', chunker_version=CHUNKER_VERSION,
            target_chars=2400, oversized=False))
        session.commit()
    client = ScriptedClient(request, rule)
    original = client.run
    def fail_challenge(request, output_model):
        if request.operation == 'campaign_conflict_check':
            raise RuntimeError('Provider unavailable')
        return original(request, output_model)
    client.run = fail_challenge
    c = SimpleNamespace(id='c', brand_id='b', data={})
    messages, evidence = [{'role': 'user', 'text': request}], [{'chunk_id': 's1', 'content': rule}]
    try:
        with pytest.raises(RuntimeError, match='Provider unavailable'):
            coverage.review_campaign(client, Settings(_env_file=None), c, messages, evidence, engine=engine)
        assert len(client.requests) == 1
        client.run = original
        assert coverage.review_campaign(client, Settings(_env_file=None), c, messages, evidence, engine=engine)
        assert len(client.requests) == 2  # Only the unfinished independent check is called again.
        assert coverage.review_campaign(client, Settings(_env_file=None), c, messages, evidence, engine=engine)
        assert len(client.requests) == 2
    finally:
        engine.dispose()


def test_approval_and_execution_reject_changed_verified_plan(tmp_path):
    from fastapi import BackgroundTasks, HTTPException
    from sqlalchemy.orm import Session

    from app.api import campaigns
    from app.db import create_database_engine, initialize_database

    engine = create_database_engine(f'sqlite:///{tmp_path / "gate.db"}')
    initialize_database(engine)
    plan = {'goal': 'Launch', 'assets': [{'id': 'a1', 'hook': 'changed'}],
            'questions': [], 'conflict_review_key': 'review',
            'conflict_verification': {'signature': 'previous-content'}}
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='awaiting_approval',
                data={'plans': [plan], 'conflict_coverage': {'status': 'ready',
                      'coverage_challenged': True, 'snapshot_key': 'review'}}))
            session.commit()
            with pytest.raises(HTTPException, match='verification'):
                campaigns.approve('c', campaigns.Approval(version=0), session)
            with pytest.raises(HTTPException, match='verification'):
                campaigns.execute('c', campaigns.Approval(version=0), BackgroundTasks(), session,
                                  Settings(_env_file=None))
    finally:
        engine.dispose()


def test_explicit_review_failure_cannot_fall_back_to_legacy_approval():
    c = SimpleNamespace(data={'plans': [{'assets': []}],
                             'conflict_coverage': {'status': 'incomplete'}})
    assert coverage.approval_error(c)


def test_explicit_review_keeps_history_and_invalidates_old_intake(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from app.api import campaigns
    from app.db import create_database_engine, initialize_database

    def retrieve(session, c, settings, mode, **kwargs):
        assert c.data['conflict_intake'] == {}
        assert c.state == 'planning_busy'
        c.data = {**c.data, 'conflict_coverage': {'status': 'ready', 'snapshot_key': 'new'}}
    def verify(client, settings, c, messages, plan):
        return {**plan, 'conflict_review_key': 'new'}
    monkeypatch.setattr(campaigns, 'retrieve_campaign_context', retrieve)
    monkeypatch.setattr(coverage, 'verify_plan', verify)
    monkeypatch.setattr(campaigns, 'TrackedOpenAI', lambda *a: SimpleNamespace(close=lambda: None))
    engine = create_database_engine(f'sqlite:///{tmp_path / "review.db"}')
    initialize_database(engine)
    original = {'assets': [{'id': 'a1'}], 'questions': []}
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='awaiting_approval',
                data={'plans': [original], 'requirements': {'assets': []},
                      'conflict_intake': {'revision': 'old'}, 'messages': []}))
            session.commit()
            result = campaigns.review_saved_campaign('c', campaigns.Approval(version=0),
                                                      session, Settings(_env_file=None))
            assert result['state'] == 'awaiting_approval'
            assert result['plans'][0] == original
            assert result['plans'][1]['conflict_review_key'] == 'new'
            assert result['messages'][-1]['plan_index'] == 1
    finally:
        engine.dispose()


def test_failed_preplan_review_resumes_without_reextracting_or_duplicating_brief(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from app.api import campaigns
    from app.db import create_database_engine, initialize_database

    messages = [{'role': 'user', 'text': 'Original launch brief'}]
    def retrieve(session, c, settings, mode, **kwargs):
        assert c.data['requirements'] == {'topic': 'Saved topic', 'assets': []}
        assert c.data['retrieved_context']['retrieval_id'] == 'saved-retrieval'
        c.data = {**c.data, 'conflict_coverage': {'status': 'ready'}}
        c.state = 'draft'
        session.commit()
    def process(identifier, body, session, settings, **kwargs):
        assert kwargs['internal'] is True
        assert body.text == messages[0]['text']
        c = campaigns.load(session, identifier)
        assert c.data['messages'] == messages
        return campaigns.snapshot(c)
    monkeypatch.setattr(campaigns, 'retrieve_campaign_context', retrieve)
    monkeypatch.setattr(campaigns, 'process_message', process)
    monkeypatch.setattr(campaigns, 'TrackedOpenAI', lambda *a: SimpleNamespace(close=lambda: None))
    engine = create_database_engine(f'sqlite:///{tmp_path / "preplan.db"}')
    initialize_database(engine)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='planning', version=2,
                data={'messages': messages, 'plans': [], 'requirements': {'topic': 'Saved topic', 'assets': []},
                      'retrieved_context': {'retrieval_id': 'saved-retrieval'},
                      'conflict_coverage': {'status': 'incomplete'}}))
            session.commit()
            result = campaigns.review_saved_campaign('c', campaigns.Approval(version=2),
                                                      session, Settings(_env_file=None))
            assert result['messages'] == messages
    finally:
        engine.dispose()


def test_planning_route_saves_verified_plan_before_approval(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from app.api import campaigns
    from app.brand import conflicts, supplemental
    from app.db import create_database_engine, initialize_database

    request, rule = scenario(compatible=True)
    passage = {'chunk_id': 's1', 'content': rule}
    asset = {'id': 'a1', 'format': 'landing_page', 'audience': 'Platform leads', 'purpose': 'Launch page',
                 'timing': None, 'sender_or_byline': None, 'constraints': []}
    requirements = {'topic': 'Launch', 'topic_details': [], 'constraints': [], 'assets': [asset], 'unknowns': [], 'issues': []}
    retrieved = {'id': 'retrieval', 'groups': [{'query': {'id': 'channel__landing_page__rules'},
                  'status': 'success', 'results': [passage]}]}
    report = {'supported': [], 'restricted': [], 'conflicts': [], 'unresolved': [], 'claims': []}
    reviewed = {'id': 'review', 'mode': 'retrieval_checkpoint', 'report': report,
        'evidence': [passage], 'query_groups': retrieved['groups'], 'tracks': {}}
    combined = {**reviewed, 'id': 'compact', 'requirements': requirements, 'tracks': {name: {'review_id': name, 'report': report, 'evidence': [passage]}
                 for name in ['topic', 'landing_page']}}
    monkeypatch.setattr(campaigns, 'plan_retrieval', lambda *a, **kw: {'id': 'search', 'plan': {}, 'requirements': requirements})
    monkeypatch.setattr(campaigns, 'execute_retrieval', lambda *a, **kw: retrieved)
    monkeypatch.setattr(campaigns, 'retrieval_checkpoint', lambda *a, **kw: reviewed)
    monkeypatch.setattr(campaigns, 'load_artifact', lambda *a, **kw: retrieved)
    monkeypatch.setattr(campaigns, 'compact_tracks', lambda *a, **kw: combined)
    monkeypatch.setattr(supplemental, 'supplement', lambda *a: {'results': []})
    # Persistence is tested above; exercise the real coordinator here with a synthetic corpus.
    monkeypatch.setattr(conflicts, 'review_campaign', lambda client, settings, c, messages, evidence, **kw:
        coverage.review_campaign(client, settings, c, messages, evidence))
    client = ScriptedClient(request, rule, compatible=True)
    run = client.run
    def model(request, output_model):
        if request.operation == 'campaign_plan':
            value = {'questions': [], 'goal': 'Launch', 'assumptions': [], 'constraints': [],
                'assets': [{k: v for k, v in asset.items() if k not in {'sender_or_byline', 'constraints'}} |
                        {'angle': 'A clear launch', 'hook': 'Define agent boundaries', 'cta': 'Read docs'}]}
        elif request.operation == 'campaign_plan_verification':
            value = {'reviewed_asset_ids': ['a1'], 'defects': []}
        else:
            return run(request, output_model)
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='plan')
    monkeypatch.setattr(campaigns, 'TrackedOpenAI', lambda *a: SimpleNamespace(run=model, close=lambda: None))
    engine = create_database_engine(f'sqlite:///{tmp_path / "planning.db"}')
    initialize_database(engine)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': [], 'plans': []}))
            session.commit()
            saved = campaigns.process_message('c', campaigns.Message(text=request, version=0), session,
                Settings(_env_file=None), forced_route=campaigns.MessageRoute(route='campaign', target_asset_id=None, reply=''))
            assert saved['state'] == 'awaiting_approval'
            assert saved['plans'][-1]['conflict_review_key']
            assert coverage.approval_error(campaigns.load(session, 'c')) is None
            assert campaigns.approve('c', campaigns.Approval(version=saved['version']), session)['state'] == 'approved'
    finally:
        engine.dispose()
