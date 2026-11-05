"""Audits must route decisions to questions and validate claimed defects."""
import json
from types import SimpleNamespace

import pytest

from app.api.campaigns import PlanOutline, validate_campaign_plan
from app.brand.plan_audit import AuditFinding, PlanCheck, PlanNeedsDecision, PlanAuditFailure
from app.config import Settings


def outline():
    return PlanOutline(goal='Regulated teams campaign', assumptions=[], constraints=[], questions=[], assets=[{
        'id': 'post', 'format': 'linkedin', 'audience': 'Security leaders', 'purpose': 'Explain trust',
        'angle': 'Verified status', 'hook': 'Wickhold is pursuing FedRAMP Moderate authorization',
        'cta': 'Book a demo', 'timing': '2027-02-16'}]).model_dump()


def finding(status='pass', **changes):
    return AuditFinding(**(dict(status=status, field='/assets/0/hook',
        candidate_quote='Wickhold is pursuing FedRAMP Moderate authorization',
        explanation='The candidate uses the selected wording', source_quotes=[],
        correction=None, question=None, options=[]) | changes))


def campaign():
    return SimpleNamespace(id='c', brand_id='b', data={'questions': [{
        'id': 'fedramp', 'text': 'Which FedRAMP wording?', 'options': ['Use pursuing authorization'],
        'answer': 'Use pursuing authorization'}]})


def test_pass_findings_do_not_block_or_repair():
    calls = []
    class Client:
        def run(self, request, **kwargs):
            calls.append(request.operation)
            payload = json.loads(request.input)
            assert payload['current_decisions'][0]['answer'] == 'Use pursuing authorization'
            return SimpleNamespace(parsed=PlanCheck(findings=[finding(), finding(
                field='/assets/0/cta', candidate_quote='Book a demo', explanation='CTA is correct')]))
    plan = outline()
    assert validate_campaign_plan(Client(), Settings(_env_file=None), campaign(), [],
                                  {'evidence': [], 'requirements': {}}, plan) == plan
    assert calls == ['campaign_plan_check']


def test_audit_customer_clearance_becomes_question_without_repair():
    plan = outline()
    plan['assets'][0]['hook'] = 'Vaskell Health cut audit prep by three weeks'
    calls = []
    class Client:
        def run(self, request, **kwargs):
            calls.append(request.operation)
            if request.operation == 'campaign_decision_match':
                return SimpleNamespace(parsed=kwargs['output_model'].model_validate({'matches': [{
                    'finding_index': 0, 'decision_id': None, 'disposition': 'new',
                    'reason': 'Customer permission differs from FedRAMP status',
                    'changed_source_ids': [], 'changed_user_instruction': None}]}))
            return SimpleNamespace(parsed=PlanCheck(findings=[finding('user_decision_needed',
                candidate_quote=plan['assets'][0]['hook'], explanation='Customer external use is on hold',
                source_quotes=[{'source_id': 'legal', 'quote': 'Not cleared for external use'}],
                question='How should we handle the customer story?', options=['Omit it', 'Keep it pending clearance'])]))
    with pytest.raises(PlanNeedsDecision) as result:
        validate_campaign_plan(Client(), Settings(_env_file=None), campaign(), [],
            {'evidence': [{'chunk_id': 'legal', 'content': 'Not cleared for external use'}]}, plan)
    assert result.value.findings[0]['affected_asset_ids'] == ['post']
    assert result.value.candidate == plan
    assert 'campaign_plan_repair' not in calls


@pytest.mark.parametrize('bad', [
    {'field': '/assets/99/hook'}, {'candidate_quote': 'Wickhold is FedRAMP Authorized'},
    {'source_quotes': [{'source_id': 'invented', 'quote': 'Unknown'}]},
])
def test_bad_audit_references_retry_the_audit_not_the_plan(bad):
    calls = []
    class Client:
        def run(self, request, **kwargs):
            calls.append(request.operation)
            if len(calls) == 1:
                return SimpleNamespace(parsed=PlanCheck(findings=[finding('repair_needed',
                    correction='Fix wording', **bad)]))
            assert json.loads(request.input)['validation_feedback']
            return SimpleNamespace(parsed=PlanCheck(findings=[]))
    plan = outline()
    assert validate_campaign_plan(Client(), Settings(_env_file=None), campaign(), [],
                                  {'evidence': []}, plan) == plan
    assert calls == ['campaign_plan_check', 'campaign_plan_check']


def test_repeated_invalid_audit_keeps_candidate_and_specific_error():
    class Client:
        def run(self, request, **kwargs):
            return SimpleNamespace(parsed=PlanCheck(findings=[finding(field='/missing')]))
    plan = outline()
    with pytest.raises(PlanAuditFailure, match='invalid references twice') as failure:
        validate_campaign_plan(Client(), Settings(_env_file=None), campaign(), [], {'evidence': []}, plan)
    assert failure.value.candidate == plan
    assert len(failure.value.audit_history) == 2


def test_no_progress_repair_stops_after_one_attempt():
    calls = []
    class Client:
        def run(self, request, **kwargs):
            calls.append(request.operation)
            if request.operation == 'campaign_plan_repair':
                assert json.loads(request.input)['current_decisions']
                return SimpleNamespace(parsed=PlanOutline.model_validate(outline()))
            return SimpleNamespace(parsed=PlanCheck(findings=[finding('repair_needed', correction='Use the chosen wording')]))
    with pytest.raises(PlanAuditFailure, match='no progress'):
        validate_campaign_plan(Client(), Settings(_env_file=None), campaign(), [], {'evidence': []}, outline())
    assert calls == ['campaign_plan_check', 'campaign_plan_repair']


def test_concrete_repair_is_rechecked_with_same_decisions():
    calls = []
    fixed = outline()
    fixed['assets'][0]['cta'] = 'Read the report'
    class Client:
        def run(self, request, **kwargs):
            calls.append(request.operation)
            assert json.loads(request.input)['current_decisions']
            if request.operation == 'campaign_plan_repair':
                return SimpleNamespace(parsed=PlanOutline.model_validate(fixed))
            return SimpleNamespace(parsed=PlanCheck(findings=[finding('repair_needed',
                field='/assets/0/cta', candidate_quote='Book a demo', correction='Use Read the report')]
                if len(calls) == 1 else []))
    assert validate_campaign_plan(Client(), Settings(_env_file=None), campaign(), [],
                                  {'evidence': []}, outline()) == fixed
    assert calls == ['campaign_plan_check', 'campaign_plan_repair', 'campaign_plan_check']


def test_schemas_are_strict():
    from app.llm.client import strict_output_schema
    strict_output_schema(PlanCheck)


def test_answered_audit_question_is_reclassified_instead_of_repeated():
    operations = []
    class Client:
        def run(self, request, **kwargs):
            operations.append(request.operation)
            if request.operation == 'campaign_decision_match':
                return SimpleNamespace(parsed=kwargs['output_model'].model_validate({'matches': [dict(
                    finding_index=0, decision_id='fedramp', disposition='resolved',
                    reason='User already selected pursuing', changed_source_ids=[], changed_user_instruction=None)]}))
            if operations.count('campaign_plan_check') == 1:
                return SimpleNamespace(parsed=PlanCheck(findings=[finding('user_decision_needed',
                    question='Authorized or pursuing?', options=['Authorized', 'Pursuing'])]))
            assert json.loads(request.input)['validation_feedback']
            return SimpleNamespace(parsed=PlanCheck(findings=[finding()]))
    plan = outline()
    assert validate_campaign_plan(Client(), Settings(_env_file=None), campaign(), [], {'evidence': []}, plan) == plan
    assert operations == ['campaign_plan_check', 'campaign_decision_match', 'campaign_plan_check']


def test_provider_schema_selects_existing_fields_and_server_copies_quotes():
    from pydantic import ValidationError
    from app.brand.plan_audit import audit_schema, cited_check, coverage_errors
    from app.llm.client import strict_output_schema
    plan = outline()
    evidence = [{'chunk_id': 'legal', 'content': 'An exact original passage.'}]
    schema = audit_schema(plan, evidence)
    strict_output_schema(schema)
    item = {'status': 'pass', 'field': '/assets/0/hook', 'explanation': 'Supported',
            'source_ids': ['legal'], 'correction': None, 'question': None, 'options': []}
    coverage = {'source_0': {'status': 'consistent', 'reason': 'Applicable', 'finding_indices': [0]}}
    parsed = schema.model_validate({'findings': [item], 'source_checks': coverage})
    check = cited_check(parsed, plan, evidence)
    assert check.findings[0].candidate_quote == plan['assets'][0]['hook']
    assert check.findings[0].source_quotes[0].quote == evidence[0]['content']
    assert coverage_errors(parsed, evidence) == []
    for bad in [{'field': '/assets/0/subject_line'}, {'source_ids': ['document-id']}]:
        with pytest.raises(ValidationError):
            schema.model_validate({'findings': [{**item, **bad}], 'source_checks': coverage})
    parsed = schema.model_validate({'findings': [], 'source_checks': {'source_0':
        {**coverage['source_0'], 'status': 'conflict', 'finding_indices': []}}})
    assert coverage_errors(parsed, evidence) == []
    fallback = cited_check(parsed, plan, evidence)
    assert fallback.findings[0].status == 'user_decision_needed'
    assert fallback.findings[0].question


def test_source_conflict_cannot_be_sent_to_editorial_repair():
    from app.brand.plan_audit import audit_schema, cited_check
    plan = {'goal': 'Feature the customer story', 'assets': []}
    evidence = [{'chunk_id': 'hold', 'content': 'Customer quote is not cleared.'}]
    parsed = audit_schema(plan, evidence).model_validate({
        'findings': [{'status': 'repair_needed', 'field': '/goal',
                      'explanation': 'Customer clearance needs a decision.',
                      'correction': 'Obtain clearance or omit the story.',
                      'question': None, 'options': [], 'source_ids': ['hold']}],
        'source_checks': {'source_0': {'status': 'conflict',
                                     'reason': 'Publication hold', 'finding_indices': [0]}}})
    finding = cited_check(parsed, plan, evidence).findings[0]
    assert finding.status == 'user_decision_needed'
    assert finding.question and len(finding.options) >= 2
    assert finding.correction is None
