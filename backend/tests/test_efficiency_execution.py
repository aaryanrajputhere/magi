import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Lock
from types import SimpleNamespace

from app.brand.campaign_requirements import RequirementUpdate, structured_answer_update
from app.brand.plan_verification import verify_plan
from app.config import Settings
from app.llm.client import ModelRunError
from app.llm.parallel import campaign_slot, run_jobs
from app.llm.prompt_cache import prefix_metadata
from app.llm.prompt_payloads import compact_requirement_context


def requirement():
    return {'topic': 'Campaign', 'topic_details': [], 'constraints': [], 'unknowns': [], 'issues': [],
        'assets': [{'id': 'a', 'format': 'blog', 'audience': 'Engineers', 'purpose': 'Explain',
            'timing': None, 'sender_or_byline': None, 'constraints': [], 'provenance': []}]}


def test_lossless_requirement_refs_do_not_change_stored_requirements():
    requirements = requirement()
    requirements['assets'][0]['audience'] = 'Security leaders at regulated organizations. ' * 3
    requirements['assets'].append({**deepcopy(requirements['assets'][0]), 'id': 'b'})
    original = deepcopy(requirements)
    compact = compact_requirement_context(requirements)
    assert compact['values']
    def expand(value):
        if isinstance(value, dict):
            if set(value) == {'value_ref'}:
                return compact['values'][value['value_ref']]
            return {k: expand(v) for k, v in value.items()}
        return [expand(v) for v in value] if isinstance(value, list) else value
    assert expand(compact['requirements']) == original
    assert requirements == original
    assert len(json.dumps(compact)) < len(json.dumps(original))


def test_structured_answer_changes_only_requested_fields_and_free_text_falls_back():
    previous = requirement()
    source = {'previous_requirements': previous, 'conversation': [
        {'role': 'assistant', 'question': {'text': 'Use customer story?', 'options': ['Omit', 'Include']}},
        {'role': 'user', 'text': 'Omit'}]}
    calls = []
    def run(request, output_model):
        calls.append(request)
        assert output_model is RequirementUpdate
        return SimpleNamespace(run_id='update', parsed=output_model.model_validate({
            'topic_details': [], 'constraints': ['Omit customer story'], 'unknowns': [],
            'issues': [], 'asset_updates': []}))
    client = SimpleNamespace(run=run)
    result = structured_answer_update(client, Settings(_env_file=None), 'b', 'c', source)
    assert result['assets'] == previous['assets']
    assert result['constraints'] == ['Omit customer story']
    assert previous['constraints'] == []
    source['conversation'][-1]['text'] = 'Omit, and add two emails'
    assert structured_answer_update(client, Settings(_env_file=None), 'b', 'c', source) is None
    assert len(calls) == 1


def test_parallel_workers_keep_order_and_sibling_successes():
    barrier = Barrier(3)
    def job(i):
        def run(child):
            barrier.wait(timeout=2)
            if i == 1:
                raise ValueError('failed')
            return i
        return run
    results = run_jobs(object(), [job(i) for i in range(3)])
    assert results[0] == (0, None)
    assert isinstance(results[1][1], ValueError)
    assert results[2] == (2, None)


def test_campaign_budget_is_shared_across_nested_pools():
    from time import sleep
    lock = Lock()
    active = maximum = 0
    def run(_):
        nonlocal active, maximum
        with campaign_slot('same-campaign'):
            with lock:
                active += 1
                maximum = max(maximum, active)
            sleep(.01)
            with lock:
                active -= 1
    with ThreadPoolExecutor(max_workers=9) as pool:
        list(pool.map(run, range(18)))
    assert maximum == 3


def review_fixture():
    assets = [{'id': key, 'format': channel, 'audience': 'Engineers', 'purpose': 'Explain',
               'angle': 'Evidence', 'hook': 'Opening', 'cta': 'Learn more', 'timing': None}
              for key, channel in [('a', 'blog'), ('b', 'email')]]
    return SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 's'}}), {
        'goal': 'Awareness', 'assumptions': [], 'constraints': [], 'questions': [], 'assets': assets}


def test_parallel_verification_retains_blocker_on_sibling_failure_and_reuses_success():
    campaign, plan = review_fixture()
    calls = []
    lock = Lock()
    def run(request, output_model):
        with lock:
            calls.append(request.operation)
        payload = {k: v for m in request.input for k, v in json.loads(m['content']).items()}
        ids = payload.get('target_asset_ids', [])
        if payload.get('review_scope') == 'global':
            return SimpleNamespace(run_id='global', parsed=output_model.model_validate({
                'reviewed_asset_ids': ids, 'defects': [{'asset_id': None, 'field': 'goal',
                'explanation': 'Missing required number', 'severity': 'material', 'brief_quote': '25 demos',
                'kind': 'content', 'rule_refs': []}]}))
        if ids == ['b']:
            raise ModelRunError('failed-email', 'timeout')
        return SimpleNamespace(run_id='blog', parsed=output_model.model_validate({'reviewed_asset_ids': ids, 'defects': []}))
    settings = Settings(_env_file=None, magi_parallel_verification=True)
    messages = [{'role': 'user', 'text': '25 demos'}]
    result = verify_plan(SimpleNamespace(run=run), settings, campaign, messages, plan)
    assert result['plan_review']['status'] == 'incomplete'
    assert result['plan_review']['approval_blockers'][0]['field'] == 'goal'
    assert len(campaign.data['verification_checks']) == 2
    verify_plan(SimpleNamespace(run=run), settings, campaign, messages, plan)
    assert len(calls) == 4  # retry only failed email check
    changed = deepcopy(plan)
    changed['goal'] = 'New strategy'
    verify_plan(SimpleNamespace(run=run), settings, campaign, messages, changed)
    assert len(calls) == 7


def test_cache_metadata_changes_for_schema_but_not_dynamic_suffix():
    base = {'model': 'gpt-6-luna', 'instructions': 'Rules', 'input': [
        {'role': 'user', 'content': 'Stable original'}, {'role': 'user', 'content': 'One'}]}
    other = deepcopy(base)
    other['input'][1]['content'] = 'Two'
    assert prefix_metadata(base, 1) == prefix_metadata(other, 1)
    other['text'] = {'format': {'type': 'json_schema', 'name': 'Different'}}
    assert prefix_metadata(base, 1)['prefix_fingerprint'] != prefix_metadata(other, 1)['prefix_fingerprint']


def test_parallel_planning_preserves_asset_order_strategy_and_scoped_originals():
    from app.api.campaigns import PlanOutline
    from app.brand.parallel_planning import run_campaign_plan
    from app.llm.client import ModelRequest, strict_output_schema
    from app.llm.prompt_payloads import lean_plan_context, planning_prompt
    campaign, outline = review_fixture()
    requirements = requirement()
    requirements['assets'] = [{**requirements['assets'][0], 'id': a['id'], 'format': a['format']}
                               for a in outline['assets']]
    context = {'requirements': requirements, 'asset_packages': {
        a['id']: {'requirement': r, 'evidence': [{'chunk_id': a['id'], 'content': a['id'] + ' original',
            'relative_path': 'brand/products/' + a['id'] + '.md'}]}
        for a, r in zip(outline['assets'], requirements['assets'])}}
    original = deepcopy(context)
    request = ModelRequest(operation='campaign_plan', model='gpt-6-luna', instructions='Plan',
        input=planning_prompt(lean_plan_context(context), {}), max_output_tokens=12000)
    lock = Lock()
    calls = []
    def run(request, output_model):
        strict_output_schema(output_model)
        with lock:
            calls.append(request.operation)
        if request.operation == 'campaign_plan_strategy':
            value = {'goal': 'Shared strategy', 'assumptions': [], 'constraints': [], 'assets': [
                {'id': a['id'], 'audience': 'Engineers', 'timing': None, 'publish_after_asset_ids': []}
                for a in outline['assets']]}
        else:
            directive = json.loads(request.input[-1]['content'])
            ids = directive['target_asset_ids']
            assert len(ids) == 1
            originals = json.loads(request.input[0]['content'])['original_sources']
            assert [p['chunk_id'] for p in originals] == ids
            value = {**outline, 'assets': [a for a in outline['assets'] if a['id'] in ids]}
        return SimpleNamespace(run_id=request.operation, parsed=output_model.model_validate(value))
    result = run_campaign_plan(SimpleNamespace(run=run), Settings(_env_file=None, magi_parallel_planning=True),
        campaign, request, requirements, context, PlanOutline)
    assert [a.id for a in result.parsed.assets] == ['a', 'b']
    assert result.parsed.goal == 'Shared strategy'
    assert calls.count('campaign_plan_channel') == 2
    assert context == original


def test_explicit_cache_mode_and_lifetime_reach_saved_request_without_prompt_metadata(tmp_path):
    import pytest
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db import create_database_engine, initialize_database
    from app.llm.client import ModelRequest, ModelRunError, TrackedOpenAI
    from app.models import LLMRun
    engine = create_database_engine(f'sqlite:///{tmp_path}/cache.db')
    initialize_database(engine)
    settings = Settings(_env_file=None, openai_api_key=None)
    client = TrackedOpenAI(engine, settings)
    request = ModelRequest(operation='test', model='gpt-6-luna', instructions='Rules',
        input=[{'role': 'user', 'content': 'Stable'}, {'role': 'user', 'content': 'Changing'}],
        max_output_tokens=20, cache_prefix_messages=1, prompt_cache_ttl='30m')
    with pytest.raises(ModelRunError):
        client.run(request)
    with Session(engine) as session:
        run = session.scalar(select(LLMRun))
        assert run.request_body['prompt_cache_options'] == {'mode': 'explicit'}
        marker = run.request_body['input'][0]['content'][0]['prompt_cache_breakpoint']
        assert marker == {'mode': 'explicit', 'ttl': '30m'}
        assert 'prefix_fingerprint' in run.context['prompt_metadata']
        assert 'prompt_metadata' not in run.request_body
    client.close()
    engine.dispose()


def test_global_strategy_keeps_original_dependencies_and_protected_channel_rules():
    from app.brand.evidence_scope import global_source_ids
    evidence = [
        {'chunk_id': 'legal', 'relative_path': 'brand/legal/claims.md', 'dependency_ids': ['channel']},
        {'chunk_id': 'channel', 'relative_path': 'brand/channels/email.md'},
        {'chunk_id': 'protected', 'relative_path': 'brand/templates/blog.md'},
        {'chunk_id': 'other', 'relative_path': 'brand/visuals/export.md'}]
    assert global_source_ids(evidence, {'protected'}) == {'legal', 'channel', 'protected'}


def test_memory_guard_only_skips_confident_campaign_instructions():
    from app.memory import is_campaign_only
    assert is_campaign_only('For this campaign, create two emails.')
    assert not is_campaign_only('For this campaign, remember that I prefer concise copy going forward.')
    assert not is_campaign_only('Create two emails. I always prefer concise copy.')


def test_evaluation_harness_binds_requirements_before_verification(tmp_path, monkeypatch):
    import runpy
    from pathlib import Path

    from app.api.campaigns import PlanOutline
    from app.brand import parallel_planning, plan_verification
    module = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/evaluate_campaign_efficiency.py'))
    campaign, outline = review_fixture()
    requirements = requirement()
    requirements['assets'].append({**deepcopy(requirements['assets'][0]), 'id': 'b', 'format': 'email'})
    exported = {**campaign.data, 'id': 'original', 'brand_id': 'b', 'messages': [],
                'requirements': requirements, 'plans': [{**outline, 'asset_packages': {}}]}
    original = deepcopy(exported)
    mismatched = deepcopy(outline)
    for asset in mismatched['assets']:
        asset['audience'] = 'Model paraphrased this audience'
    monkeypatch.setattr(parallel_planning, 'run_campaign_plan', lambda *args:
        SimpleNamespace(parsed=PlanOutline.model_validate(mismatched)))
    def verify(client, settings, clone, messages, plan):
        assert all(a['audience'] == 'Engineers' for a in plan['assets'])
        assert clone.id != 'original'
        return {**plan, 'plan_review': {'status': 'passed'}}
    monkeypatch.setattr(plan_verification, 'verify_plan', verify)
    class Client:
        def __init__(self, *args):
            pass
        def close(self):
            pass
    module['live'].__globals__['EvaluationClient'] = Client
    result = module['live']([{'operation': 'campaign_plan', 'request': {
        'model': 'gpt-6-luna', 'instructions': 'Plan', 'input': '{}'}}],
        exported, tmp_path / 'evaluation.sqlite', pairs=1)
    assert len(result['trials']) == 4
    assert all(r['status'] == 'completed' for r in result['trials'])
    assert exported == original


def test_stable_instruction_families_have_a_separate_cache_boundary(tmp_path):
    import pytest
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db import create_database_engine, initialize_database
    from app.llm.client import ModelRequest, ModelRunError, TrackedOpenAI
    from app.models import LLMRun
    engine = create_database_engine(f'sqlite:///{tmp_path}/instructions.db')
    initialize_database(engine)
    client = TrackedOpenAI(engine, Settings(_env_file=None, openai_api_key=None))
    with pytest.raises(ModelRunError):
        client.run(ModelRequest(operation='campaign_decision_match', model='gpt-6-luna',
            instructions='Fixed decision rubric', input='Changing findings', max_output_tokens=50))
    with Session(engine) as session:
        body = session.scalar(select(LLMRun)).request_body
        assert body['instructions'] == ''
        assert body['input'][0]['role'] == 'developer'
        assert body['input'][0]['content'][0]['text'] == 'Fixed decision rubric'
        assert body['input'][1]['content'] == 'Changing findings'
        assert body['prompt_cache_options']['mode'] == 'explicit'
    client.close()
    engine.dispose()
