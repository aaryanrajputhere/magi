from copy import deepcopy
from types import SimpleNamespace

from app.brand import conflicts, context_scope
from app.brand.decisions import evidence_snapshot
from app.config import Settings


def test_status_messages_reuse_review_and_changed_intent_uses_delta(monkeypatch):
    evidence = [{'chunk_id': 'a', 'content': 'Exact product fact', 'relative_path': 'brand/products/a.md'},
                {'chunk_id': 'b', 'content': 'Other product', 'relative_path': 'brand/products/b.md'}]
    campaign = SimpleNamespace(id='c', brand_id='b', state='draft', data={'requirements': {'assets': []}})
    calls = []
    def review(client, settings, campaign, messages, originals, **kwargs):
        calls.append(kwargs)
        return {'findings': [], 'report': {'status': 'ready', 'coverage_challenged': True,
            'snapshot_key': str(len(calls)), 'source_snapshot': evidence_snapshot(originals),
            'source_count': len(originals), 'findings': [], 'limitations': [], 'treatments': []}}
    monkeypatch.setattr(conflicts, 'review_campaign', review)
    monkeypatch.setattr(context_scope, 'select_scope', lambda *a, **k: ({'a'}, False))
    settings = Settings(_env_file=None)
    messages = [{'role': 'user', 'text': 'Create an awareness campaign'}]
    conflicts.prepare_conflict_intake(None, settings, campaign, messages, evidence, 'one')
    original = deepcopy(campaign.data['conflict_coverage'])
    messages += [{'role': 'assistant', 'text': 'Review complete.'}]
    conflicts.prepare_conflict_intake(None, settings, campaign, messages, evidence, 'different-status-revision')
    assert len(calls) == 1
    assert campaign.data['conflict_coverage'] == original
    messages += [{'role': 'user', 'text': 'Omit the product example'}]
    conflicts.prepare_conflict_intake(None, settings, campaign, messages, evidence, 'new-intent')
    assert len(calls) == 2
    assert calls[-1]['source_scope_ids'] == {'a'}
    assert campaign.data['conflict_coverage']['scope'] == 'delta'
    assert campaign.data['conflict_coverage']['source_snapshot'] == evidence_snapshot(evidence)
    changed = [{**evidence[0], 'content': 'Changed source fact'}, evidence[1]]
    conflicts.prepare_conflict_intake(None, settings, campaign, messages, changed, 'changed-source')
    assert 'source_scope_ids' not in calls[-1]  # Changed existing originals require a full review.


def test_plan_prompt_removes_snapshots_and_duplicate_memberships_without_changing_sources():
    import json

    from app.llm.prompt_payloads import planning_prompt
    context = {'evidence': [{'chunk_id': 'original', 'ref': 'old', 'content': 'Exact **original** words.'}],
        'source_groups': {'g': ['old']}, 'asset_packages': {'a': {
            'guideline_collections': {'voice': ['old']}, 'requirement': {'id': 'a'}}},
        'requirements': {'assets': [{'id': 'a', 'provenance': [{'field': 'purpose',
            'origin': 'explicit', 'quote': 'Repeated brief quote'}]}]}}
    before = deepcopy(context)
    messages = [{'role': 'user', 'text': 'Exact original brief'}, {'role': 'assistant',
        'text': 'Which?', 'question': {'text': 'Which?', 'source_snapshot': {'id': 'x'*10000}}}]
    result = planning_prompt(context, {'conversation': messages})
    first, second = [json.loads(m['content']) for m in result]
    assert first['original_sources'][0]['content'] == 'Exact **original** words.'
    assert second['brand_context']['source_groups'] == {'g': [__import__('app.llm.prompt_payloads', fromlist=['stable_source_ref']).stable_source_ref('original')]}
    assert 'source_snapshot' not in str(result)
    assert 'guideline_collections' not in str(result)
    assert second['brand_context']['asset_packages']['a']['requirement_ref'] == 'a'
    assert second['conversation'][0]['text'] == 'Exact original brief'
    assert context == before


def test_answer_to_reviewed_choice_goes_straight_to_planning(monkeypatch):
    evidence = [{'chunk_id': 'a', 'content': 'Customer example needs permission.'}]
    campaign = SimpleNamespace(id='c', brand_id='b', state='draft', data={'requirements': {'assets': []}})
    calls = []
    def review(*args, **kwargs):
        calls.append(True)
        return {'findings': [], 'report': {'status': 'ready', 'coverage_challenged': True,
            'snapshot_key': 'verified', 'source_snapshot': evidence_snapshot(evidence),
            'source_count': 1, 'findings': [], 'limitations': [], 'treatments': []}}
    monkeypatch.setattr(conflicts, 'review_campaign', review)
    monkeypatch.setattr(context_scope, 'select_scope', lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('Reviewed choice must not trigger another scope call')))
    settings = Settings(_env_file=None)
    messages = [{'role': 'user', 'text': 'Create a campaign'}]
    conflicts.prepare_conflict_intake(None, settings, campaign, messages, evidence, 'initial')
    answer = 'Omit the customer story'
    question = {'id': 'q', 'conflict_id': 'known-conflict', 'text': 'Which route?',
                'kind': 'choices', 'options': [answer, 'Provide clearance'], 'answer': answer}
    campaign.data = {**campaign.data, 'questions': [question]}
    messages += [{'role': 'assistant', 'text': 'Which route?', 'question': question},
                 {'role': 'user', 'text': answer}]
    conflicts.prepare_conflict_intake(None, settings, campaign, messages, evidence, 'answered')
    assert len(calls) == 1
    assert campaign.data['conflict_coverage']['status'] == 'ready'


def test_normal_preparation_does_not_run_a_gap_model():
    from app.brand.supplemental import supplement
    campaign = SimpleNamespace(id='c', brand_id='b', data={
        'messages': [{'role': 'user', 'text': 'Create campaign'}], 'requirements': {'assets': []}})
    def unexpected(*args, **kwargs):
        raise AssertionError('Routine preparation must not create another gap-model call')
    result = supplement(None, campaign, {'id': 'retrieval', 'groups': []},
        SimpleNamespace(run=unexpected), Settings(_env_file=None), only_if_needed=True)
    assert result['queries'] == result['results'] == []
