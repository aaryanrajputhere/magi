import pytest
from pydantic import ValidationError

from app.api.campaigns import requirement_bound_plan_schema
from app.llm.client import strict_output_schema


def test_plan_schema_rejects_unexpected_assets_and_accepts_saved_id():
    schema = requirement_bound_plan_schema({'assets': [{'id': 'saved-post'}]})
    asset = {'id': 'extra-post', 'format': 'linkedin', 'audience': 'Engineers',
             'purpose': 'Introduce product', 'angle': 'Boundaries', 'hook': 'Set boundaries',
             'cta': 'Read more', 'timing': None}
    plan = {'questions': [], 'goal': 'Awareness', 'assumptions': [], 'constraints': [], 'assets': [asset]}
    with pytest.raises(ValidationError):
        schema.model_validate(plan)
    asset['id'] = 'saved-post'
    assert schema.model_validate(plan).assets[0].id == 'saved-post'
    strict_output_schema(schema)
    with pytest.raises(ValidationError):
        schema.model_validate({**plan, 'assets': [asset, asset]})


def test_empty_requirements_do_not_allow_model_to_invent_assets():
    schema = requirement_bound_plan_schema({'assets': []})
    assert schema.model_json_schema()['properties']['assets']['maxItems'] == 0


def test_fixed_fields_are_restored_without_model_repair():
    from app.api.campaigns import repair_deliverables
    requirements = {'assets': [{'id': 'post', 'format': 'x',
        'audience': 'Security leaders following the brand on X', 'timing': None}]}
    plan = {'questions': [], 'assets': [{'id': 'post', 'format': 'linkedin',
        'audience': 'Security leaders following the brand on X (formerly Twitter)',
        'timing': 'Tomorrow', 'angle': 'User-selected replacement story'}]}
    # No client is needed when only fixed contract fields differ.
    result = repair_deliverables(None, None, None, plan, requirements, {})
    assert result['assets'][0] == {**requirements['assets'][0],
                                 'angle': 'User-selected replacement story'}
    assert plan['assets'][0]['timing'] == 'Tomorrow'


def test_binding_does_not_hide_missing_duplicate_or_unknown_assets():
    from app.api.campaigns import bind_plan_requirements
    from app.brand.campaign_requirements import deliverable_errors
    requirements = {'assets': [{'id': 'one', 'format': 'x', 'audience': 'Leaders', 'timing': None},
                               {'id': 'two', 'format': 'email', 'audience': 'Leaders', 'timing': None}]}
    plan = {'questions': [], 'assets': [{'id': 'one'}, {'id': 'one'}, {'id': 'unknown'}]}
    errors = deliverable_errors(bind_plan_requirements(plan, requirements), requirements)
    assert 'Duplicate asset IDs.' in errors
    assert any("Missing: ['two']; unexpected: ['unknown']" in e for e in errors)


@pytest.mark.parametrize('repair', [False, True])
def test_plan_is_shown_only_after_optional_patch_reverification(repair):
    from types import SimpleNamespace

    from app.brand.plan_verification import approval_error, plan_signature, verify_plan

    requirement = {'id': 'post', 'format': 'linkedin',
                   'audience': 'Security leaders', 'timing': None}
    asset = {**requirement, 'hook': 'Original hook', 'purpose': 'Launch',
             'angle': 'Evidence', 'cta': 'Read more'}
    plan = {'goal': 'Awareness', 'questions': [], 'assumptions': [], 'constraints': [],
            'assets': [asset], 'requirements': {'assets': [requirement]}}
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    from app.config import Settings
    operations = []

    def run(request, output_model):
        operations.append(request.operation)
        if request.operation == 'campaign_plan_verification':
            value = {'reviewed_asset_ids': ['post'], 'defects': [{
                'asset_id': 'post', 'field': 'hook', 'explanation': 'Fix hook',
                'kind': 'incomplete_text', 'rule_refs': []}]
                if repair and len(operations) == 1 else []}
        else:
            assert request.operation == 'campaign_plan_coverage_repair'
            value = {'patches': [{'asset_id': 'post', 'field': 'hook',
                                  'replacement': 'Corrected hook'}]}
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='test')

    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert result['assets'][0]['hook'] == ('Corrected hook' if repair else 'Original hook')
    assert operations == ['campaign_plan_verification'] + (
        ['campaign_plan_coverage_repair', 'campaign_plan_verification'] if repair else [])
    assert result['conflict_verification']['signature'] == plan_signature(result)
    assert len(result['conflict_verification']['history']) == (2 if repair else 1)
    campaign.data['plans'] = [result]
    assert approval_error(campaign) is None
    result['assets'][0]['hook'] = 'Edited'
    assert approval_error(campaign)
