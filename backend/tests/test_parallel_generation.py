import json
from collections import Counter
from copy import deepcopy
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.api import campaigns as api
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.prompt_payloads import image_guidance, lean_package, lean_plan_context


def package():
    passages = [{'chunk_id': 'rule', 'content': 'Never use customer material without clearance. ' * 40,
                     'relative_path': 'brand/legal/customer-policy.md'},
                {'chunk_id': 'product', 'content': 'Product detail. ' * 40,
                     'relative_path': 'brand/products/overview.md'}]
    return {'mode': 'verified', 'rule_pack': {'version': 'v1', 'evidence': passages, 'limits': []},
            'evidence': passages, 'campaign_constraints': ['No screenshots'],
            'channel': {'report': {'supported': [{'text': e['content'], 'source_ids': [e['chunk_id']]} for e in passages]}},
            'topic': {'report': {'restricted': ['Never imply certification.']}}}


def test_payloads_preserve_rules_without_repeating_passages():
    original = package()
    before = deepcopy(original)
    lean = lean_package(original)
    assert original == before
    for passage in original['evidence']:
        assert json.dumps(lean).count(passage['content']) == 1
    assert lean['topic']['report']['restricted'] == ['Never imply certification.']
    image = image_guidance(original)
    assert [e['chunk_id'] for e in image['evidence']] == ['rule']
    assert image['campaign_constraints'] == ['No screenshots']
    context = lean_plan_context({'asset_packages': {'a': original, 'b': original}})
    assert len(context['rule_packs']) == 1
    assert len(context['evidence']) == 2
    assert context['asset_packages']['a']['rule_pack_ref'] == context['asset_packages']['b']['rule_pack_ref']


@pytest.mark.parametrize('fail_one', [False, True])
def test_three_asset_pipelines_preserve_results_and_retry(tmp_path, monkeypatch, fail_one):
    engine = create_database_engine(f'sqlite:///{tmp_path}/parallel.db')
    initialize_database(engine)
    assets = [{'id': f'a{i}', 'format': 'linkedin', 'purpose': 'Awareness'} for i in range(6)]
    plan = {'goal': 'Goal', 'review_id': 'review', 'assets': assets}
    with Session(engine) as session:
        session.add(api.Campaign(id='c', brand_id='b', state='generating', version=1,
            data={'messages': [], 'plans': [plan], 'generation': {'plan_index': 0, 'drafts': []}}))
        session.commit()
    monkeypatch.setattr(api, 'load_artifact', lambda *a: {'evidence': [], 'report': {}})
    monkeypatch.setattr(api, 'load_asset_package', lambda *a: package())
    from app.memory import Memory
    with Session(engine) as session:
        session.add_all([Memory(id='local', brand_id='b', text='Use short paragraphs', updated_at='now'),
                         Memory(id='other', brand_id='other-brand', text='Unrelated preference', updated_at='now')])
        session.commit()
    monkeypatch.setattr(api, 'image_catalog', lambda *a: (None, []))
    from app.brand import draft_validation
    def validate_with_memory(client, settings, campaign, asset, draft, pack):
        assert pack['brand_preferences'] == [
            {'id': 'local', 'text': 'Use short paragraphs', 'revision': 1}]
        return {**draft, 'status': 'completed'}
    monkeypatch.setattr(draft_validation, 'validate_and_repair',
        validate_with_memory)
    barrier = Barrier(3, timeout=5)
    lock = Lock()
    calls, clients, closed = [], [], []
    state = {'fail': fail_one, 'sync': True}
    class Client:
        def __init__(self, *a):
            clients.append(self)
        def close(self):
            closed.append(self)
        def run(self, request, **kwargs):
            if request.operation == 'route_asset_context':
                payload = {k: v for message in request.input for k, v in json.loads(message['content']).items()}
                refs = [p['chunk_id'] for p in payload['sources']]
                return SimpleNamespace(run_id='routing', parsed=kwargs['output_model'].model_validate({
                    'assets': [{'asset_id': a['id'], 'writing': refs, 'validation': refs,
                                'image': [], 'production_dependencies': []}
                               for a in payload['approved_plan']['assets']]}))
            with lock:
                calls.append(request.context.asset_id)
            if state['sync']:
                barrier.wait()
            if request.context.asset_id == 'a1' and state['fail']:
                raise RuntimeError('one failed')
            payload = {k: v for message in request.input for k, v in json.loads(message['content']).items()}
            assert 'evidence' not in payload['package']['rule_pack']
            assert payload['asset_sources']['evidence']
            assert payload['brand_preferences'] == [
                {'id': 'local', 'text': 'Use short paragraphs', 'revision': 1}]
            return SimpleNamespace(run_id='run', parsed=api.GeneratedDraft(title='Draft', body='Copy',
                source_ids=['rule'], subject=None, preheader=None, sender_name=None, byline=None))
    monkeypatch.setattr(api, 'TrackedOpenAI', Client)
    api.generate_drafts(engine, 'c', Settings(_env_file=None))
    with Session(engine) as session:
        campaign = api.load(session, 'c')
        assert campaign.state == ('generation_error' if fail_one else 'ready')
        drafts = campaign.data['generation']['drafts']
        assert [d['asset_id'] for d in drafts] == [a['id'] for a in assets if not (fail_one and a['id'] == 'a1')]
        assert campaign.data['generation']['active_assets'] == []
    assert len(clients) == len(closed) == 7  # One router client and six workers.
    if fail_one:
        state.update(fail=False, sync=False)
        api.generate_drafts(engine, 'c', Settings(_env_file=None))
        assert Counter(calls) == Counter({'a0': 1, 'a1': 2, 'a2': 1, 'a3': 1, 'a4': 1, 'a5': 1})
        with Session(engine) as session:
            assert api.load(session, 'c').state == 'ready'
    engine.dispose()
