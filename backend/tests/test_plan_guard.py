"""The plan gate checks original evidence even if the earlier review was wrong."""
import json
from types import SimpleNamespace

import pytest

from app.api.campaigns import CorrectedPlanOutline, PlanCheck, PlanOutline, validate_campaign_plan
from app.config import Settings


def outline(question):
    return PlanOutline(goal="Two security posts for May 2027", assumptions=[], constraints=[],
                       questions=[question] if question else [], assets=[])


@pytest.mark.parametrize("repair_passes", [True, False])
def test_prohibited_statistic_question_is_repaired_and_rechecked(repair_passes):
    original = outline("Should both posts include the 68% statistic?")
    repaired = outline("Shall I proceed without report statistics?")
    evidence = [{"chunk_id": "policy", "content": "Market statistics expire after 12 months."}]
    # Deliberately incorrect prior review: the gate must also see original evidence.
    reviewed = {"report": {"supported": [{"text": "68% is allowed"}]}, "evidence": evidence}
    requests = []

    class Client:
        def run(self, request, **kwargs):
            requests.append(request)
            payload = json.loads(request.input)
            assert payload["source_evidence"] == evidence
            if request.operation == "campaign_plan_repair":
                assert payload["violations"]
                return SimpleNamespace(parsed=repaired)
            assert kwargs["output_model"] is PlanCheck
            assert "conversation" not in payload
            assert "BRAND COMPLIANCE audit" in request.instructions
            if len(requests) == 1:
                assert payload["candidate_plan"] == original.model_dump()
            else:
                assert payload["candidate_plan"] == repaired.model_dump()
            violations = [] if len(requests) == 3 and repair_passes else ["Requested statistic is expired; ask about omission."]
            return SimpleNamespace(parsed=PlanCheck(violations=violations, supported_correction_available=False))

    args = (Client(), Settings(_env_file=None), SimpleNamespace(id="c", brand_id="b"),
            [{"role": "user", "text": "Use 68% in May 2027"}], reviewed, original.model_dump())
    if repair_passes:
        result = validate_campaign_plan(*args)
        assert result["questions"] == repaired.questions
        assert result["assets"] == []
    else:
        result = validate_campaign_plan(*args)
        assert result["assets"] == []
        assert len(result["questions"]) == 1
        assert "reframe" in result["questions"][0]
        assert "68%" not in json.dumps(result)
        assert result["goal"] != original.goal
    expected = ["campaign_plan_check", "campaign_plan_repair", "campaign_plan_check"]
    if not repair_passes:
        expected += ["campaign_plan_repair", "campaign_plan_check"]
    assert [r.operation for r in requests] == expected


def test_valid_plan_requires_no_repair():
    calls = []

    class Client:
        def run(self, request, **kwargs):
            calls.append(request.operation)
            return SimpleNamespace(parsed=PlanCheck(violations=[], supported_correction_available=False))

    plan = outline("Shall I proceed without report statistics?").model_dump()
    assert validate_campaign_plan(Client(), Settings(_env_file=None),
        SimpleNamespace(id="c", brand_id="b"), [], {"report": {}, "evidence": []}, plan) == plan
    assert calls == ["campaign_plan_check"]


def test_failed_launch_repair_returns_clarification_without_rejected_copy():
    unsafe = PlanOutline(goal="Announce general availability", assumptions=[], constraints=[],
                         questions=["Which launch date?"], assets=[])

    class Client:
        def run(self, request, **kwargs):
            if request.operation == "campaign_plan_repair":
                return SimpleNamespace(parsed=unsafe)
            return SimpleNamespace(parsed=PlanCheck(violations=[
                "The product is early access; general availability is not approved."], supported_correction_available=False))

    result = validate_campaign_plan(Client(), Settings(_env_file=None),
        SimpleNamespace(id="c", brand_id="b"), [],
        {"report": {}, "evidence": [{"content": "Early access; no announced GA date."}]},
        unsafe.model_dump())
    assert result["assets"] == []
    assert len(result["questions"]) == 1
    assert "general availability" not in json.dumps(result)
    assert "Which launch date" not in json.dumps(result)
    assert "supported by the brand guidance" in result["questions"][0]


def test_approved_status_correction_proceeds_without_permission_question():
    candidate = outline("May I use the approved early-access wording instead?")
    approved = PlanOutline(goal="Early-access awareness", assumptions=[],
        constraints=["Use early access; no general availability announcement is supported."],
        questions=[], assets=[{
            "id": f"post-{i}", "format": "linkedin", "audience": "Platform teams",
            "purpose": "Explain approved tool-call controls", "angle": f"Control example {i}",
            "hook": f"Consider tool-call boundary {i}", "cta": "Contact us",
            "timing": f"2027-01-{25+i}",
        } for i in range(1, 6)])
    operations = []

    class Client:
        def run(self, request, **kwargs):
            operations.append(request.operation)
            if request.operation == "campaign_plan_repair":
                assert kwargs["output_model"] is CorrectedPlanOutline
                assert "requested number of outlines with questions=[]" in request.instructions
                assert "do not invent a new early-access launch date" in request.instructions
                return SimpleNamespace(parsed=approved)
            assert "asking permission to use approved early-access wording" in request.instructions
            return SimpleNamespace(parsed=PlanCheck(violations=(
                ["Use approved status directly instead of asking permission"] if len(operations) == 1 else []),
                supported_correction_available=True))

    result = validate_campaign_plan(Client(), Settings(_env_file=None),
        SimpleNamespace(id="c", brand_id="b"), [],
        {"report": {}, "evidence": [{"content": "Product remains in early access."}]},
        candidate.model_dump())
    assert result["questions"] == []
    assert len(result["assets"]) == 5
    assert [asset["timing"] for asset in result["assets"]] == [f"2027-01-{day}" for day in range(26, 31)]
    assert "early access" in result["constraints"][0]


def test_corrected_plan_schema_disallows_confirmation_questions():
    from pydantic import ValidationError

    from app.llm.client import strict_output_schema

    schema = strict_output_schema(CorrectedPlanOutline)["format"]["schema"]
    strict_output_schema(PlanCheck)
    assert schema["properties"]["questions"]["maxItems"] == 0
    assert schema["properties"]["assets"]["minItems"] == 1
    with pytest.raises(ValidationError):
        CorrectedPlanOutline(**outline("May I use approved wording?").model_dump())


def test_corrected_outlines_get_wording_repair_without_reopening_questions():
    plan = outline(None).model_dump()
    plan['assets'] = [{
        'id': 'post-1', 'format': 'linkedin', 'audience': 'Platform teams',
        'purpose': 'Explain runtime policy', 'angle': 'Runtime boundaries',
        'hook': 'Product, in early access, applies runtime tool-call policy.',
        'cta': 'Contact the account team', 'timing': '2027-01-26',
    }]
    operations = []

    class Client:
        def run(self, request, **kwargs):
            operations.append(request.operation)
            if request.operation == 'campaign_plan_repair':
                assert kwargs['output_model'] is CorrectedPlanOutline
                return SimpleNamespace(parsed=CorrectedPlanOutline(**plan))
            assert 'This is an OUTLINE, not final public copy' in request.instructions
            return SimpleNamespace(parsed=PlanCheck(
                violations=['Fix a public hook qualifier'] if len(operations) < 5 else [],
                supported_correction_available=False))

    result = validate_campaign_plan(Client(), Settings(_env_file=None),
        SimpleNamespace(id='c', brand_id='b'), [], {'report': {}, 'evidence': []}, plan)
    assert result['questions'] == []
    assert len(result['assets']) == 1
    assert operations.count('campaign_plan_repair') == 2
