from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.api.campaigns import PlanOutline, bind_plan_requirements
from app.brand.plan_checks import mechanical_findings
from app.brand.plan_verification import approval_error, verify_plan
from app.config import Settings
from app.llm.client import ModelRunError, strict_output_schema


def fixture():
    asset = {'id': 'a', 'format': 'blog', 'audience': 'Engineers', 'purpose': 'Explain containment',
                 'angle': 'Evidence', 'hook': 'Understand containment', 'cta': 'Read more', 'timing': None}
    plan = {'goal': 'Awareness', 'assumptions': [], 'constraints': [], 'questions': [], 'assets': [asset]}
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    return campaign, plan


@pytest.mark.parametrize('stage', ['initial_review', 'repair', 'confirmation'])
@pytest.mark.parametrize('severity', ['minor', 'material'])
def test_failure_keeps_candidate_and_known_material_blockers(stage, severity):
    campaign, plan = fixture()
    saved = []
    calls = []
    def run(request, output_model):
        if request.operation.startswith('campaign_plan_verification'):
            assert request.timeout_seconds == 120
        calls.append(request.operation)
        current = ('initial_review' if len(calls) == 1 else 'repair' if len(calls) == 2 else 'confirmation')
        if current == stage:
            raise ModelRunError('failed-run', 'timeout')
        if current == 'repair':
            value = {'patches': [{'asset_id': 'a', 'field': 'hook', 'replacement': 'A better opening'}]}
        else:
            value = {'reviewed_asset_ids': ['a'], 'defects': [{'asset_id': 'a', 'field': 'hook',
                'explanation': 'Correct the opening', 'kind': 'content', 'rule_refs': [],
                'severity': severity, 'brief_quote': 'Awareness' if severity == 'material' else None}]}
        strict_output_schema(output_model)
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='ok')
    before = deepcopy(plan)
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign,
        [{'role': 'user', 'text': 'Awareness'}], plan, checkpoint=lambda p: saved.append(deepcopy(p)))
    assert plan == before
    assert saved[0] == plan
    assert result['assets']
    assert result['plan_review']['status'] == 'incomplete'
    assert next(a for a in result['plan_review']['attempts'] if a['status'] == 'failed')['run_id'] == 'failed-run'
    if stage == 'confirmation':
        assert saved[-1]['assets'][0]['hook'] == 'A better opening'
        assert result['assets'][0]['hook'] == 'A better opening'
    campaign.data['plans'] = [result]
    assert bool(approval_error(campaign)) == (stage != 'initial_review' and severity == 'material')
    result['assets'][0]['hook'] += ' changed'
    assert approval_error(campaign)


def test_dependency_cycles_and_unverified_urls_are_material():
    _, plan = fixture()
    b = {**plan['assets'][0], 'id': 'b', 'publish_after_asset_ids': ['a']}
    plan['assets'][0].update(cta_destination='b', publish_after_asset_ids=['b'])
    plan['assets'].append(b)
    findings = mechanical_findings(plan)
    assert sum('cycle' in d['explanation'] for d in findings) == 2
    assert all(d['severity'] == 'material' for d in findings)
    b.update(cta_destination='https://invented.example', publish_after_asset_ids=[])
    assert any('supporting evidence' in d['explanation'] for d in mechanical_findings(plan))


def test_only_proposed_audience_can_be_refined():
    _, plan = fixture()
    requirement = {**plan['assets'][0], 'audience': 'Broad audience', 'provenance': [
        {'field': 'audience', 'origin': 'proposed'}]}
    assert bind_plan_requirements(plan, {'assets': [requirement]})['assets'][0]['audience'] == 'Engineers'
    requirement['provenance'][0]['origin'] = 'explicit'
    assert bind_plan_requirements(plan, {'assets': [requirement]})['assets'][0]['audience'] == 'Broad audience'
    strict_output_schema(PlanOutline)


def test_failed_confirmation_id_coverage_does_not_retry_or_erase_plan():
    campaign, plan = fixture()
    calls = []
    def run(request, output_model):
        if request.operation.startswith('campaign_plan_verification'):
            assert request.timeout_seconds == 120
        calls.append(request.operation)
        return SimpleNamespace(run_id='bad-coverage', parsed=output_model.model_validate(
            {'reviewed_asset_ids': [], 'defects': []}))
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert len(calls) == 1
    assert result['assets'] == plan['assets']
    assert result['plan_review']['status'] == 'incomplete'
    assert next(a for a in result['plan_review']['attempts'] if a.get('run_id') == 'bad-coverage')['run_id'] == 'bad-coverage'


def test_all_fields_repaired_in_one_batch_with_one_confirmation():
    import json
    campaign, plan = fixture()
    plan['assets'].append({**plan['assets'][0], 'id': 'b'})
    calls = []
    def run(request, output_model):
        if request.operation.startswith('campaign_plan_verification'):
            assert request.timeout_seconds == 120
        calls.append(request.operation)
        if len(calls) == 1:
            value = {'reviewed_asset_ids': ['a', 'b'], 'defects': [
                {'asset_id': 'a', 'field': field, 'explanation': 'Keep the label and dependency separate',
                 'kind': 'content', 'rule_refs': [], 'severity': 'minor', 'brief_quote': None}
                for field in ('cta', 'cta_destination', 'publish_after_asset_ids', 'audience')]}
        elif len(calls) == 2:
            payload = json.loads(request.input[1]['content'])
            assert len(payload['defects']) == 4
            value = {'patches': [{'asset_id': 'a', 'field': field, 'replacement': replacement}
                for field, replacement in [('cta', 'Read the article'), ('cta_destination', 'b'),
                    ('publish_after_asset_ids', ['b']), ('audience', 'Platform engineers')]]}
        else:
            payload = json.loads(request.input[1]['content'])
            assert payload['candidate']['assets'][0]['cta'] == 'Read the article'
            assert payload['unchanged_assets'][0]['id'] == 'b'
            value = {'reviewed_asset_ids': ['a'], 'defects': []}
        strict_output_schema(output_model)
        return SimpleNamespace(run_id=str(len(calls)), parsed=output_model.model_validate(value))
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert len(calls) == 3
    assert result['plan_review']['status'] == 'passed'
    assert result['assets'][0]['publish_after_asset_ids'] == ['b']


def test_database_checkpoint_retains_repaired_candidate_after_confirmation_failure(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.api.campaigns import Campaign
    from app.db import Base
    campaign, plan = fixture()
    engine = create_engine(f'sqlite:///{tmp_path / "checkpoint.db"}')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        saved = Campaign(id='c', brand_id='b', state='planning_busy', data=campaign.data)
        session.add(saved)
        session.commit()
        count = 0
        def run(request, output_model):
            nonlocal count
            count += 1
            if count == 3:
                raise ModelRunError('confirmation-timeout', 'timeout')
            value = ({'reviewed_asset_ids': ['a'], 'defects': [{'asset_id': 'a', 'field': 'hook',
                     'explanation': 'Complete opening', 'kind': 'incomplete_text', 'rule_refs': [],
                     'severity': 'minor', 'brief_quote': None}]} if count == 1 else
                     {'patches': [{'asset_id': 'a', 'field': 'hook', 'replacement': 'Completed opening'}]})
            return SimpleNamespace(run_id=str(count), parsed=output_model.model_validate(value))
        def checkpoint(candidate):
            saved.data = {**saved.data, 'plans': [deepcopy(candidate)]}
            session.commit()
        result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), saved, [], plan,
                             checkpoint=checkpoint)
        session.expire_all()
        assert session.get(Campaign, 'c').data['plans'][0]['assets'][0]['hook'] == 'Completed opening'
        assert result['plan_review']['status'] == 'incomplete'
    engine.dispose()
