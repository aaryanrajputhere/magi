"""Token reductions must preserve evidence, patch boundaries and approval checks."""
import json
from review_helpers import review_payload
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.brand.plan_verification import (
    FieldPatch,
    PlanDefect,
    apply_patches,
    verify_plan,
)
from app.brand.retrieval import RetrievalError
from app.config import Settings
from app.llm.client import strict_output_schema
from app.llm.prompt_payloads import lean_plan_context, shared_rule_context


def test_shared_rules_preserve_channel_membership_and_originals():
    common = {'chunk_id': 'common', 'content': 'Never imply certification.'}
    blog = {'chunk_id': 'blog', 'content': 'Include a byline.'}
    packs = {'linkedin': {'format': 'linkedin', 'evidence': [common], 'limits': []},
             'blog': {'format': 'blog', 'evidence': [common, blog], 'limits': []}}
    before = deepcopy(packs)
    result = shared_rule_context(packs)
    assert packs == before
    assert [{k: v for k, v in e.items() if k != 'ref'} for e in result['evidence']] == [common, blog]
    assert set(result['source_groups'][result['channels']['blog']['evidence_group']]) == {'s0', 's1'}
    assert json.dumps(result).count(common['content']) == 1
    with pytest.raises(ValueError, match='Conflicting rule evidence'):
        shared_rule_context({**packs, 'other': {'evidence': [{**common, 'content': 'Different'}]}})


def test_planning_cleanup_keeps_semantics_and_unassigned_evidence():
    package = {'campaign_context': ['Awareness, not demand generation'],
               'campaign_constraints': ['Keep exact qualifier'], 'evidence': []}
    context = {'source_catalog': [{'chunk_id': 'extra'}],
               'evidence': [{'chunk_id': 'extra', 'content': 'Important exception'}],
               'resolved_review': {'limitations': ['No public boilerplate'],
                                   'source_snapshot': {'extra': 'hash'}},
               'asset_packages': {'a': deepcopy(package), 'b': deepcopy(package)}}
    before = deepcopy(context)
    result = lean_plan_context(context)
    assert context == before
    assert [{k: v for k, v in e.items() if k != 'ref'} for e in result['evidence']] == context['evidence']
    assert result['resolved_review'] == {'limitations': ['No public boilerplate']}
    assert 'source_catalog' not in result
    assert result['shared_asset_context']['campaign_constraints'] == ['Keep exact qualifier']
    assert 'campaign_context' not in result['asset_packages']['a']


def defect(**kwargs):
    return PlanDefect(asset_id='a', field='purpose', explanation='Finish sentence',
                      kind='incomplete_text', rule_refs=[], **kwargs)


def test_patches_preserve_other_assets_and_fields():
    plan = {'goal': 'Awareness', 'assets': [
        {'id': 'a', 'purpose': 'unfinished', 'format': 'blog', 'hook': 'Keep'},
        {'id': 'b', 'purpose': 'Keep'}]}
    before = deepcopy(plan)
    result = apply_patches(plan, [defect()], [FieldPatch(
        asset_id='a', field='purpose', replacement='Completed')])
    assert plan == before
    assert result['assets'][0] == {**plan['assets'][0], 'purpose': 'Completed'}
    assert result['assets'][1] == plan['assets'][1]
    for patches in ([], [FieldPatch(asset_id='b', field='purpose', replacement='Wrong')],
                    [FieldPatch(asset_id='a', field='purpose', replacement=['Wrong type'])],
                    [FieldPatch(asset_id='a', field='purpose', replacement='x')] * 2):
        with pytest.raises(RetrievalError):
            apply_patches(plan, [defect()], patches)


@pytest.mark.parametrize('fails_recheck', [False, True])
@pytest.mark.parametrize('kind', ['incomplete_text', 'content'])
def test_focused_repair_rechecks_original_rules_and_never_approves_failure(fails_recheck, kind):
    assets = [{'id': i, 'format': channel, 'audience': 'Engineers', 'timing': None,
               'purpose': 'unfinished', 'angle': 'Evidence', 'hook': 'Hello', 'cta': 'Read'}
              for i, channel in [('a', 'blog'), ('b', 'linkedin')]]
    packs = {channel: {'format': channel, 'limits': [], 'evidence': [
        {'chunk_id': channel, 'content': f'{channel} original exception'}]}
        for channel in ('blog', 'linkedin')}
    plan = {'goal': 'Awareness', 'assumptions': [], 'constraints': [], 'questions': [],
            'assets': assets, 'requirements': {'assets': deepcopy(assets)},
            'asset_packages': {a['id']: {'rule_pack': packs[a['format']]} for a in assets}}
    before = deepcopy(plan)
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review',
        'source_snapshot': {'blog': 'do-not-send-hash'}}})
    calls = []
    problem = defect().model_copy(update={'kind': kind, 'severity': 'minor' if kind == 'incomplete_text' else 'material',
                                         'rule_refs': ['blog'] if kind == 'content' else []})

    def run(request, output_model):
        strict_output_schema(output_model)
        payload = review_payload(request)
        assert 'source_snapshot' not in payload['review']
        calls.append((request.operation, payload))
        if request.operation == 'campaign_plan_coverage_repair':
            assert [a['id'] for a in payload['candidate']['assets']] == ['a']
            assert [a['id'] for a in payload['requirements']['assets']] == ['a']
            assert [{k: v for k, v in e.items() if k != 'ref'} for e in payload['governing_rules']['evidence']] == (
                [] if kind == 'incomplete_text' else packs['blog']['evidence'])
            value = {'patches': [{'asset_id': 'a', 'field': 'purpose',
                                  'replacement': 'Complete' if len(calls) == 2 else 'Complete again'}]}
        else:
            recheck = len(calls) > 1
            if recheck:
                assert payload['candidate']['assets'][0]['purpose'].startswith('Complete')
                assert [a['id'] for a in payload['candidate']['assets']] == ['a']
                assert [{k: v for k, v in e.items() if k != 'ref'} for e in payload['governing_rules']['evidence']] == packs['blog']['evidence']
                assert payload['unchanged_assets'] == [before['assets'][1]]
            value = {'reviewed_asset_ids': ['a'] if recheck else ['a', 'b'],
                     'defects': [problem.model_dump()] if not recheck or fails_recheck else []}
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id=f'run-{len(calls)}')

    if fails_recheck:
        result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
        assert bool(result.get('recovery')) == (kind == 'content')
        assert 'conflict_verification' not in result
    else:
        result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
        assert result['assets'][1] == before['assets'][1]
        assert len(result['conflict_verification']['history']) == 2
    assert plan == before
    assert [op for op, _ in calls] == ['campaign_plan_verification'] + [
        'campaign_plan_coverage_repair', 'campaign_plan_verification'] * 1


def test_campaign_level_patch_is_verified_with_every_channel():
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    plan = {'goal': 'unfinished', 'assets': [], 'questions': [],
            'assumptions': [], 'constraints': [], 'asset_packages': {
                c: {'rule_pack': {'format': c, 'evidence': [
                    {'chunk_id': c, 'content': c + ' rules'}], 'limits': []}}
                for c in ('blog', 'linkedin')}}
    calls = []

    def run(request, output_model):
        calls.append(request.operation)
        payload = review_payload(request)
        if request.operation == 'campaign_plan_coverage_repair':
            assert set(payload['governing_rules']['channels']) == {'blog', 'linkedin'}
            value = {'patches': [{'asset_id': None, 'field': 'goal', 'replacement': 'Awareness'}]}
        else:
            assert set(payload['governing_rules']['channels']) == {'blog', 'linkedin'}
            value = {'reviewed_asset_ids': [], 'defects': [{
                'asset_id': None, 'field': 'goal', 'explanation': 'Correct campaign goal',
                'kind': 'content', 'rule_refs': ['blog']}] if len(calls) == 1 else []}
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='test')

    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert result['goal'] == 'Awareness'
    assert plan['goal'] == 'unfinished'
    assert len(calls) == 3


def test_new_naming_defect_after_cta_repair_gets_one_followup():
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    plan = {'goal': 'Awareness', 'questions': [], 'assumptions': [], 'constraints': [],
            'assets': [{'id': 'a', 'format': 'linkedin', 'audience': 'Engineers',
                        'purpose': 'Explain containment', 'angle': 'Evidence',
                        'hook': 'Quenchline contains incidents', 'cta': 'Read the long introduction',
                        'timing': None}]}
    calls = []
    def run(request, output_model):
        payload = review_payload(request)
        calls.append(request.operation)
        stage = len(calls)
        if request.operation == 'campaign_plan_coverage_repair':
            field = 'cta' if stage == 2 else 'hook'
            if stage == 4:
                assert payload['candidate']['assets'][0]['cta'] == 'Read more'
            value = {'patches': [{'asset_id': 'a', 'field': field,
                                 'replacement': 'Read more' if field == 'cta' else
                                                'Wickhold Quenchline contains incidents'}]}
        else:
            value = {'reviewed_asset_ids': ['a'], 'defects': [] if stage == 5 else [{
                'asset_id': 'a', 'field': 'cta' if stage == 1 else 'hook',
                'kind': 'content', 'rule_refs': [], 'explanation': 'Correct wording'}]}
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id=str(stage))
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert result['assets'][0]['cta'] == 'Read more'
    assert result['assets'][0]['hook'].startswith('Quenchline')
    assert result['recovery']['status'] == 'review_required'
    assert len(calls) == 3
    assert plan['assets'][0]['hook'].startswith('Quenchline')


def test_verifier_rejects_invented_source_ids():

    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    plan = {'goal': 'Awareness', 'assets': [], 'questions': [], 'assumptions': [],
            'constraints': [], 'asset_packages': {'a': {'rule_pack': {
                'format': 'blog', 'limits': [],
                'evidence': [{'chunk_id': 'real-source', 'content': 'Exact source text'}]}}}}
    def run(request, output_model):
        strict_output_schema(output_model)
        return SimpleNamespace(parsed=output_model.model_validate({
            'reviewed_asset_ids': [], 'defects': [{'asset_id': None, 'field': 'goal',
                'explanation': 'A purported violation', 'kind': 'content',
                'rule_refs': ['invented-source']}]}), run_id='test')
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert result['plan_review']['status'] == 'incomplete'
    assert 'invalid defect' in next(a for a in result['plan_review']['attempts'] if a['status'] == 'failed')['error']
    assert 'conflict_verification' not in result


def test_verifier_schema_disallows_asset_id_with_campaign_field():
    from pydantic import ValidationError
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    plan = {'goal': 'Awareness', 'assets': [{'id': 'landing', 'format': 'landing_page', 'audience': 'Engineers', 'purpose': 'Explain', 'angle': 'Evidence', 'hook': 'Hello', 'cta': 'Read more', 'timing': None}],
            'questions': [], 'assumptions': [], 'constraints': []}
    def run(request, output_model):
        strict_output_schema(output_model)
        invalid = {'reviewed_asset_ids': ['landing'], 'defects': [{
            'asset_id': 'landing', 'field': 'assumptions', 'explanation': 'Narrow audience',
            'kind': 'content', 'rule_refs': []}]}
        with pytest.raises(ValidationError):
            output_model.model_validate(invalid)
        invalid['defects'][0]['asset_id'] = None
        assert output_model.model_validate(invalid).defects[0].field == 'assumptions'
        return SimpleNamespace(parsed=output_model.model_validate({
            'reviewed_asset_ids': ['landing'], 'defects': []}), run_id='test')
    assert verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)


@pytest.mark.parametrize('field,limit', [('purpose', 350), ('angle', 160), ('hook', 250), ('cta', 200)])
def test_internal_outline_repairs_do_not_have_arbitrary_character_caps(field, limit):
    from app.brand.plan_verification import patch_output_schema
    problem = defect().model_copy(update={'field': field})
    schema = patch_output_schema([problem])
    strict_output_schema(schema)
    payload = {'patches': [{'asset_id': 'a', 'field': field, 'replacement': 'x' * limit}]}
    assert schema.model_validate(payload)
    payload['patches'][0]['replacement'] = 'x' * (limit + 1)
    assert schema.model_validate(payload)
    assert 'maxLength' not in str(schema.model_json_schema())
    payload['patches'][0]['replacement'] = 'Concise complete sentence.'
    assert schema.model_validate(payload)


def test_repair_question_defers_assets_without_failing_or_approving():
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    asset = {'id': 'landing', 'format': 'landing_page', 'audience': 'Broad audience',
             'purpose': 'Explain containment', 'angle': 'Awareness', 'hook': 'Containment matters',
             'cta': 'Read more', 'timing': None}
    plan = {'goal': 'Awareness', 'assets': [asset], 'questions': [], 'assumptions': [],
            'constraints': [], 'requirements': {'assets': [deepcopy(asset)]}}
    calls = []
    def run(request, output_model):
        calls.append(request.operation)
        if request.operation == 'campaign_plan_verification':
            value = {'reviewed_asset_ids': ['landing'], 'defects': [{
                'asset_id': None, 'field': 'questions', 'explanation': 'Essential audience decision',
                'kind': 'content', 'rule_refs': []}]}
        else:
            value = {'patches': [{'asset_id': None, 'field': 'questions',
                                 'replacement': [{'text': 'Which audience should the page serve?',
                                     'reason': 'The audience is essential to the page goal.',
                                     'kind': 'text', 'options': []}]}]}
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='test')
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert result['assets'] == [asset]
    assert result['recovery']['status'] == 'review_required'
    assert result['questions'][0]['text'] == 'Which audience should the page serve?'
    assert result['requirements'] == plan['requirements']
    assert result['deferred_candidate']['assets'] == [asset]
    assert 'conflict_verification' not in result
    assert plan['questions'] == []
    assert len(calls) == 2


def test_complete_470_character_purpose_is_valid_for_outline_and_repair():
    from app.api.campaigns import PlanOutline
    from app.brand.plan_verification import patch_output_schema
    purpose = 'Explain containment and its qualifications. ' * 11 + 'Read the campaign landing page.'
    assert len(purpose) > 350
    asset = {'id': 'a', 'format': 'blog', 'audience': 'Engineers', 'purpose': purpose,
             'angle': 'Awareness', 'hook': 'Containment matters.', 'cta': 'Read more', 'timing': None}
    assert PlanOutline.model_validate({'goal': 'Awareness', 'questions': [], 'assumptions': [],
                                      'constraints': [], 'assets': [asset]})
    schema = patch_output_schema([defect()])
    assert schema.model_validate({'patches': [{'asset_id': 'a', 'field': 'purpose',
                                             'replacement': purpose}]})


def test_noop_repair_preserves_candidate_without_another_verification():
    from app.brand.plan_verification import approval_error
    campaign = SimpleNamespace(id='c', brand_id='b', data={'conflict_coverage': {
        'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}})
    plan = {'goal': 'Awareness', 'questions': [], 'assets': [], 'assumptions': [], 'constraints': []}
    calls = []
    def run(request, output_model):
        calls.append(request.operation)
        value = ({'reviewed_asset_ids': [], 'defects': [{'asset_id': None, 'field': 'goal',
                  'explanation': 'Correct goal', 'kind': 'content', 'rule_refs': []}]}
                 if request.operation == 'campaign_plan_verification' else
                 {'patches': [{'asset_id': None, 'field': 'goal', 'replacement': 'Awareness'}]})
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='test')
    result = verify_plan(SimpleNamespace(run=run), Settings(_env_file=None), campaign, [], plan)
    assert len(calls) == 2
    assert result['goal'] == 'Awareness'
    assert result['recovery']['issues'][0]['explanation'] == 'Correct goal'
    assert 'conflict_verification' not in result
    campaign.data['plans'] = [result]
    assert approval_error(campaign)


def test_planning_audit_projection_preserves_evidence_and_storage():
    from app.llm.prompt_payloads import planning_review, planning_research
    review = {'status': 'ready', 'source_snapshot': {'x': 'hash'}, 'findings': [
        {'id': 'issue', 'source_snapshot': {'x': 'hash'}, 'explanation': 'Keep hash literally',
         'grounding': [{'origin': 'source', 'source_ref': 'x', 'quote': 'Exact exception'}],
         'treatment': 'Do not publish', 'affected_asset_ids': ['a']}],
        'blocking_gaps': ['Missing permission']}
    before = deepcopy(review)
    projected = planning_review(review)
    assert review == before
    assert 'source_snapshot' not in json.dumps(projected)
    assert projected['findings'][0]['grounding'] == review['findings'][0]['grounding']
    assert projected['blocking_gaps'] == review['blocking_gaps']
    assert planning_review(projected) == projected
    research = {'status': 'partial', 'requests': ['transport'], 'sources': [
        {'id': str(i), 'url': f'https://example.com/{i}', 'content': body,
         'truncated': False, 'retrieved_at': 10} for i, body in enumerate(
             ['Exact body', 'Exact body', 'Unique navigation text'])]}
    before = deepcopy(research)
    result = planning_research(research)
    assert research == before
    assert list(result['contents'].values()) == ['Exact body', 'Unique navigation text']
    assert len(result['sources']) == 3
    for original, source in zip(research['sources'], result['sources'], strict=True):
        assert result['contents'][source['content_ref']] == original['content']
        assert source['url'] == original['url']
    assert 'requests' not in result
