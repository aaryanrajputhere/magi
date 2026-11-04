"""Regression coverage for complete deliverables and independently scoped evidence."""
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from app.api import campaigns as api
from app.brand import campaign_tracks, review
from app.brand.campaign_requirements import (
    CampaignRequirements,
    deliverable_errors,
    extract_requirements,
    require_deliverables,
)
from app.brand.retrieval import RetrievalError
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import strict_output_schema


def launch_requirements():
    formats = ['landing_page', 'email', 'linkedin', 'linkedin', 'x', 'blog',
               'email', 'email', 'linkedin', 'blog', 'x']
    return {'topic': 'Leashgate launch status, scope and tool-call boundaries',
            'topic_details': [{'heading': 'Campaign context', 'content': 'Launch on January 26, 2027; no external announcement before launch.'}],
            'constraints': ['No borrowed customer proof', 'No early-access screenshots'],
            'unknowns': [], 'assets': [
                {'id': f'launch-{i}', 'format': format, 'audience': f'Audience {i}',
                 'purpose': f'Deliverable {i}', 'timing': '2027-01-26',
                 'sender_or_byline': 'Maren' if i == 4 else None,
                 'constraints': ['Draft only; Maren decides'] if i == 4 else []}
                for i, format in enumerate(formats, 1)]}


def outline(requirements):
    return {'goal': 'Launch awareness', 'questions': [], 'assumptions': [], 'constraints': [],
            'assets': [{k: a[k] for k in ('id', 'format', 'audience', 'purpose', 'timing')} |
                       {'angle': a['purpose'], 'hook': 'Review the tool-call boundary', 'cta': 'Book a demo'}
                       for a in requirements['assets']]}


def report(label):
    return {'supported': [{'text': label, 'source_ids': [label]}], 'restricted': [],
            'conflicts': [], 'unresolved': [], 'claims': []}


def reviewed_tracks(requirements):
    tracks = {name: {'review_id': f'review-{name}', 'report': report(name),
                     'evidence': [{'chunk_id': name, 'content': f'{name} evidence'}]}
              for name in ['topic', *dict.fromkeys(a['format'] for a in requirements['assets'])]}
    return {'id': 'joined', 'authority_id': 'authority', 'requirements': requirements,
            'tracks': tracks, 'report': {'supported': [], 'restricted': [], 'conflicts': [],
                                        'unresolved': [], 'claims': []},
            'evidence': [p for t in tracks.values() for p in t['evidence']]}


def test_extracts_unsummarized_brief_with_eleven_distinct_deliverables():
    brief = (Path(__file__).parents[2] /
             'wickhold-candidate-package/candidate/briefs/brief-1-leashgate-ga-launch.md').read_text()
    requirements = launch_requirements()
    seen = []

    class Client:
        def run(self, request, **kwargs):
            seen.append(request)
            payload = json.loads(request.input)
            assert payload['conversation'][0]['text'] == brief
            assert 'Maren' in payload['conversation'][0]['text']
            assert request.operation == 'campaign_requirements'
            strict_output_schema(kwargs['output_model'])
            return SimpleNamespace(parsed=CampaignRequirements.model_validate(requirements), run_id='extract')

    result = extract_requirements(Client(), Settings(_env_file=None), 'brand', 'campaign',
        {'conversation': [{'role': 'user', 'text': brief}], 'route': 'campaign'})
    assert len(result['assets']) == 11
    assert Counter(a['format'] for a in result['assets']) == {
        'landing_page': 1, 'email': 3, 'linkedin': 3, 'x': 2, 'blog': 2}
    assert result['assets'][3]['sender_or_byline'] == 'Maren'
    assert result['run_id'] == 'extract'
    assert len(seen) == 1


def test_bundled_search_is_replaced_by_separate_channel_queries():
    queries = campaign_tracks.channel_queries(launch_requirements())
    assert len(queries) == 10
    assert len({q['id'] for q in queries}) == 10
    assert all(q['purpose'] == 'channel' for q in queries)
    for channel in ('linkedin', 'x', 'email', 'blog', 'landing_page'):
        assert len([q for q in queries if q['id'].startswith(f'channel__{channel}__')]) == 2


@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'format', 'audience', 'timing'])
def test_rejects_silent_deliverable_changes(defect):
    requirements = launch_requirements()
    plan = outline(requirements)
    if defect == 'missing':
        plan['assets'] = plan['assets'][:5]
    elif defect == 'duplicate':
        plan['assets'][-1]['id'] = plan['assets'][0]['id']
    else:
        plan['assets'][0][defect] = 'changed'
    assert deliverable_errors(plan, requirements)
    with pytest.raises(RetrievalError):
        require_deliverables(plan, requirements)


def test_clarification_defers_outlines_but_keeps_requirements():
    requirements = launch_requirements()
    plan = {**outline(requirements), 'questions': ['Proceed without required statistics?'], 'assets': []}
    require_deliverables(plan, requirements)
    assert len(requirements['assets']) == 11
    mixed = {**outline(requirements), 'questions': ['Proceed?']}
    assert deliverable_errors(mixed, requirements)


def test_bounded_repair_restores_all_eleven_or_fails():
    requirements = launch_requirements()
    bad = outline(requirements)
    bad['assets'] = bad['assets'][:5]
    payloads = []

    class Client:
        def run(self, request, **kwargs):
            payloads.append(json.loads(request.input))
            return SimpleNamespace(parsed=api.PlanOutline.model_validate(outline(requirements)), run_id='repair')

    fixed = api.repair_deliverables(Client(), Settings(_env_file=None),
        SimpleNamespace(id='c', brand_id='b'), bad, requirements, {})
    assert len(fixed['assets']) == 11
    assert len(payloads[0]['requirements']['assets']) == 11
    assert payloads[0]['errors']

    class StillBad:
        def run(self, request, **kwargs):
            return SimpleNamespace(parsed=api.PlanOutline.model_validate(bad), run_id='bad-repair')

    with pytest.raises(RetrievalError, match='saved campaign requirements'):
        api.repair_deliverables(StillBad(), Settings(_env_file=None),
            SimpleNamespace(id='c', brand_id='b'), bad, requirements, {})


def test_asset_package_keeps_only_its_channel_and_preserves_conflicts():
    requirements = launch_requirements()
    combined = reviewed_tracks(requirements)
    conflict = {'status': 'unresolved', 'explanation': 'No scoped authority winner',
                'source_ids': ['x', 'topic'], 'preferred_source_id': None}
    combined['tracks']['x']['report']['conflicts'] = [conflict]
    package = campaign_tracks.asset_package(combined, requirements['assets'][4])
    assert {p['chunk_id'] for p in package['evidence']} == {'topic', 'x'}
    assert package['channel']['report']['conflicts'] == [conflict]
    assert package['requirement']['id'] == 'launch-5'
    assert 'linkedin' not in json.dumps(package)


def test_track_reviews_have_separate_channel_authority_scope(monkeypatch):
    requirements = launch_requirements()
    artifacts = {}
    calls = []

    def save(engine, brand_id, kind, fingerprint, payload, parent_id=None):
        identifier = f'{kind}-{len(artifacts)}'
        result = {'id': identifier, 'brand_id': brand_id, 'parent_id': parent_id,
                  'corpus_fingerprint': fingerprint, **payload}
        artifacts[identifier] = result
        return result

    def check(engine, brand_id, retrieval_id, **kwargs):
        retrieval = artifacts[retrieval_id]
        plan = artifacts[retrieval['parent_id']]
        name = plan['track']
        calls.append((name, plan['plan']['analysis']['channels']))
        return {'id': f'review-{name}', 'authority_id': 'authority', 'report': report(name),
                'evidence': [{'chunk_id': name, 'content': name}]}

    monkeypatch.setattr(campaign_tracks, 'save_artifact', save)
    monkeypatch.setattr(review, 'review_campaign', check)
    queries = [{'id': 'topic_1', 'purpose': 'topic', 'query': 'Leashgate status', 'reason': 'Verify'}]
    queries += campaign_tracks.channel_queries(requirements)
    searches = {'id': 'search', 'brief': 'The full launch brief',
                'plan': {'analysis': {'channels': ['all']}, 'queries': queries}}
    retrieved = {'id': 'retrieved', 'corpus_fingerprint': 'fp', 'status': 'success',
                 'groups': [{'query': q, 'status': 'success', 'results': []} for q in queries]}
    joined = campaign_tracks.review_tracks(None, 'b', retrieved, searches, requirements,
                                           Settings(_env_file=None), 'c')
    assert dict(calls)['topic'] == []
    assert dict(calls)['linkedin'] == ['LinkedIn']
    assert dict(calls)['x'] == ['X']
    assert len(calls) == 6
    assert len(joined['channel_review_ids']) == 5
    assert len(joined['tracks']) == 6


@pytest.fixture
def workspace(tmp_path):
    engine = create_database_engine(f'sqlite:///{tmp_path / "pipeline.db"}')
    initialize_database(engine)
    yield engine
    engine.dispose()


def test_plan_repair_and_persisted_packages_end_to_end(workspace, monkeypatch):
    requirements = launch_requirements()
    combined = reviewed_tracks(requirements)
    monkeypatch.setattr(api, 'plan_retrieval', lambda *a, **k:
                        {'id': 'search', 'plan': {}, 'requirements': requirements})
    monkeypatch.setattr(api, 'execute_retrieval', lambda *a, **k: {'id': 'retrieved'})
    monkeypatch.setattr(api, 'compact_tracks', lambda *a, **k: combined)
    operations = []

    def run(self, request, **kwargs):
        operations.append(request.operation)
        strict_output_schema(kwargs['output_model'])
        if request.operation == 'campaign_plan_check':
            return SimpleNamespace(parsed=api.PlanCheck(violations=[], supported_correction_available=False), run_id='check')
        plan = outline(requirements)
        if request.operation == 'campaign_plan':
            payload = json.loads(request.input)
            context = payload['brand_context']
            assert 'asset_packages' not in context
            assert context['requirements'] == requirements
            assert context['topic']['report'] == combined['tracks']['topic']['report']
            for asset in requirements['assets']:
                assert context['channels'][asset['format']]['report'] == combined['tracks'][asset['format']]['report']
            plan['assets'] = plan['assets'][:5]
        assert request.operation in {'campaign_plan', 'campaign_deliverables_repair'}
        return SimpleNamespace(parsed=api.PlanOutline.model_validate(plan), run_id=request.operation)

    monkeypatch.setattr(api.TrackedOpenAI, 'run', run)
    with Session(workspace) as session:
        session.add(api.Campaign(id='c', brand_id='b', data={'messages': [], 'plans': []}))
        session.commit()
        saved = api.process_message('c', api.Message(text='Eleven launch assets', version=0), session,
            Settings(_env_file=None), forced_route=api.MessageRoute(route='campaign', target_asset_id=None, reply=''))
        assert saved['state'] == 'awaiting_approval'
        assert len(saved['plans'][-1]['assets']) == 11
        assert len(saved['plans'][-1]['asset_packages']) == 11
        assert len(saved['requirements']['assets']) == 11
        assert 'generation' not in saved
        assert operations == ['campaign_plan', 'campaign_deliverables_repair']
        session.expire_all()
        assert len(api.load(session, 'c').data['plans'][-1]['assets']) == 11


@pytest.mark.parametrize('repair_succeeds', [True, False])
def test_generation_audits_scoped_package_before_completing(workspace, monkeypatch, repair_succeeds):
    requirements = launch_requirements()
    requirements['assets'] = [requirements['assets'][4]]  # One X asset.
    combined = reviewed_tracks(requirements)
    package = campaign_tracks.asset_package(combined, requirements['assets'][0])
    plan = {**outline(requirements), 'review_id': 'joined', 'requirements': requirements,
            'asset_packages': {'launch-5': package}}
    monkeypatch.setattr(api, 'load_artifact', lambda *a, **k: combined)
    monkeypatch.setattr(api, 'load_asset_package', lambda *a, **k: package)
    monkeypatch.setattr(api, 'image_catalog', lambda *a, **k: (None, []))
    seen = []

    def run(self, request, **kwargs):
        payload = json.loads(request.input)
        seen.append(request.operation)
        strict_output_schema(kwargs['output_model'])
        assert {p['chunk_id'] for p in payload['package']['evidence']} == {'x', 'topic'}
        if request.operation == 'campaign_asset_check':
            valid = payload['draft']['body'] == 'Short supported copy.' and repair_succeeds
            return SimpleNamespace(parsed=api.DraftCheck(violations=[] if valid else ['Exceeds channel limit']), run_id='audit')
        assert request.operation in {'generate_campaign_asset', 'campaign_asset_repair'}
        body = 'Too long. ' * 50 if request.operation == 'generate_campaign_asset' else 'Short supported copy.'
        return SimpleNamespace(parsed=api.GeneratedDraft(title='X', subject=None, preheader=None,
            sender_name=None, byline=None, body=body, source_ids=['topic']), run_id=request.operation)

    monkeypatch.setattr(api.TrackedOpenAI, 'run', run)
    with Session(workspace) as session:
        session.add(api.Campaign(id='c', brand_id='b', state='awaiting_approval', version=1,
            data={'plans': [plan], 'messages': []}))
        session.commit()
        api.execute('c', api.Approval(version=1), BackgroundTasks(), session, Settings(_env_file=None))
    api.generate_drafts(workspace, 'c', Settings(_env_file=None))
    with Session(workspace) as session:
        saved = api.load(session, 'c')
        assert saved.state == 'ready'
        drafts = saved.data['generation']['drafts']
        assert len(drafts) == 1
        assert 'audit_run_ids' not in drafts[0]
    assert seen == ['generate_campaign_asset']


def test_execution_rejects_tampered_asset_count(workspace):
    requirements = launch_requirements()
    plan = {**outline(requirements), 'requirements': requirements}
    plan['assets'] = plan['assets'][:5]
    with Session(workspace) as session:
        session.add(api.Campaign(id='c', brand_id='b', state='awaiting_approval', version=1,
                                data={'plans': [plan]}))
        session.commit()
        with pytest.raises(HTTPException, match='saved campaign requirements'):
            api.execute('c', api.Approval(version=1), BackgroundTasks(), session, Settings(_env_file=None))


def test_channel_queries_bound_full_asset_requirements():
    from app.brand.context import PlannedQuery
    from app.brand.retrieval import query_terms

    requirements = launch_requirements()
    for asset in requirements['assets']:
        asset['constraints'] = [' '.join(f'restriction{i}' for i in range(200)),
                                'x' * 600]
    queries = campaign_tracks.channel_queries(requirements)
    assert len(queries) == 10
    for query in queries:
        PlannedQuery.model_validate(query)
        assert query_terms(query['query'])


def test_channel_queries_use_one_search_without_voice_or_visual_requirements():
    requirements = launch_requirements()
    requirements['constraints'] = []
    queries = campaign_tracks.channel_queries(requirements)
    assert len(queries) == 5
    assert all(q['id'].endswith('__rules') for q in queries)


def test_channel_queries_add_voice_search_only_for_affected_channel():
    requirements = launch_requirements()
    requirements['constraints'] = []
    requirements['assets'][0]['constraints'] = ['Use a working room hero image']
    queries = campaign_tracks.channel_queries(requirements)
    assert len(queries) == 6
    assert [q['id'] for q in queries if q['id'].endswith('__voice')] == [
        'channel__landing_page__voice']


def test_compact_audit_pass_findings_do_not_block_draft():
    class Client:
        def run(self, request, **kwargs):
            strict_output_schema(kwargs['output_model'])
            return SimpleNamespace(parsed=api.CompactDraftCheck(findings=[
                api.DraftFinding(status='pass', message='Correct GA naming.'),
                api.DraftFinding(status='pass', message='Primary CTA included.'),
                api.DraftFinding(status='fail', message='Remove placeholder URL.'),
            ]), run_id='audit')

    violations, run_id = api.audit_draft(
        Client(), Settings(_env_file=None), SimpleNamespace(brand_id='b', id='c'),
        {'id': 'asset'}, {'body': 'Draft'}, {'mode': 'compact'})
    assert violations == ['Remove placeholder URL.']
    assert run_id == 'audit'
