from types import SimpleNamespace

import pytest

from app.brand.context_scope import SourceScope, select_scope
from app.brand.retrieval import RetrievalError
from app.config import Settings


@pytest.mark.parametrize('persistent', [False, True])
def test_scope_unknown_reference_recovers_once_without_saving_invalid_scope(persistent):
    campaign = SimpleNamespace(id='c', brand_id='b', data={})
    calls = []

    def run(request, output_model):
        calls.append(request)
        import json
        payload = {k: v for m in request.input for k, v in json.loads(m['content']).items()}
        assert payload['catalog'] == [{'ref': 's0'}]
        if len(calls) == 2:
            assert payload['correction']['invalid_refs'] == ['invented-uuid']
        return SimpleNamespace(run_id=str(len(calls)), parsed=SourceScope(
            source_ids=['invented-uuid'] if persistent or len(calls) == 1 else ['s0'],
            full_review_required=False, reason='Selected factual evidence'))

    action = lambda: select_scope(SimpleNamespace(run=run), Settings(_env_file=None),
        campaign, [], [{'chunk_id': 'original-id', 'content': 'Exact original evidence'}],
        operation='scope_campaign_plan')
    if persistent:
        with pytest.raises(RetrievalError, match='after correction'):
            action()
        assert 'context_scopes' not in campaign.data
    else:
        refs, full = action()
        assert refs == {'original-id'}
        assert full is False
        assert next(iter(campaign.data['context_scopes'].values()))['source_ids'] == ['original-id']
    assert len(calls) == 2


@pytest.mark.parametrize('operation,complete,full,expected', [
    ('scope_campaign_plan', True, True, {'fact', 'rule', 'exception', 'prior'}),
    ('scope_campaign_plan', True, False, {'fact', 'rule', 'exception', 'prior'}),
    ('scope_campaign_plan', False, False, {'fact', 'rule', 'exception', 'prior', 'unused'}),
    ('scope_campaign_changes', True, True, {'fact', 'rule', 'exception', 'prior', 'unused'}),
])
def test_planning_selection_is_independent_of_review_invalidation(operation, complete, full, expected):
    from app.brand.context_scope import PlanningSourceScope
    from app.llm.client import strict_output_schema

    evidence = [
        {'chunk_id': 'fact', 'relative_path': 'brand/products/main.md',
         'content': 'Original product fact', 'dependency_ids': ['exception']},
        {'chunk_id': 'exception', 'relative_path': 'brand/products/main.md',
         'content': 'Original qualification'},
        {'chunk_id': 'rule', 'relative_path': 'brand/legal/claims.md',
         'content': 'Original mandatory restriction'},
        {'chunk_id': 'prior', 'relative_path': 'brand/archive/old.md',
         'content': 'Original opposing claim'},
        {'chunk_id': 'unused', 'relative_path': 'brand/examples/other.md',
         'content': 'Unrelated example'},
    ]
    campaign = SimpleNamespace(id='c', brand_id='b', data={
        'conflicts': [{'source_ids': ['prior']}]})
    calls = []

    def run(request, output_model):
        import json
        calls.append(request)
        strict_output_schema(output_model)
        catalog = json.loads(request.input[0]['content'])['catalog']
        ref = next(p['ref'] for p in catalog if p['relative_path'] == 'brand/products/main.md'
                   and p['ref'] == 's1')
        values = {'source_ids': [ref], 'full_review_required': full, 'reason': 'Changed central claim'}
        if output_model is PlanningSourceScope:
            values['source_selection_complete'] = complete
        return SimpleNamespace(run_id='run', parsed=output_model.model_validate(values))

    client = SimpleNamespace(run=run)
    args = (client, Settings(_env_file=None), campaign, [], evidence)
    refs, invalidated = select_scope(*args, operation=operation)
    assert refs == expected
    assert invalidated is full
    assert evidence[0]['content'] == 'Original product fact'
    assert select_scope(*args, operation=operation) == (refs, invalidated)
    assert len(calls) == 1
    receipt = next(iter(campaign.data['context_scopes'].values()))
    assert receipt['version'] == ('planning_scope_v3' if operation == 'scope_campaign_plan' else 'stage_scope_v1')
