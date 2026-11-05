from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import campaigns
from app.config import Settings
from app.main import create_app
from app.models.brand import Brand


@pytest.mark.parametrize("questions", [[], ["Which product should this campaign cover?"]])
def test_planning_revision_approval_and_reload(tmp_path, monkeypatch, questions):
    monkeypatch.setattr(campaigns, "plan_retrieval", lambda *a, **kw: {"id": "search", "plan": {}})
    monkeypatch.setattr(campaigns, "execute_retrieval", lambda *a, **kw: {"id": "retrieval"})
    monkeypatch.setattr(campaigns, "review_campaign", lambda *a, **kw:
                        {"id": "review", "evidence": [], "report": {"supported": [], "restricted": [],
                         "conflicts": [], "unresolved": []}})
    plan = campaigns.Plan(reply="Proposed plan", reply_paragraphs=[campaigns.ReplyParagraph(text="Proposed plan", source_ids=[])], questions=questions, goal="Awareness",
        assumptions=[], constraints=[], assets=[campaigns.Asset(id="post-1", format="linkedin",
        audience="Leads", purpose="Explain evidence scope", angle="Evidence for vendor reviews",
        hook="What does your vendor assessment cover?", cta="Contact us", timing=None)] if not questions else [])
    def run(self, request, **kwargs):
        if request.operation == "campaign_message_route":
            return SimpleNamespace(parsed=campaigns.MessageRoute(target_asset_id=None, route="campaign", reply=""),
                                   run_id="route")
        return SimpleNamespace(parsed=plan, run_id="run")
    monkeypatch.setattr(campaigns.TrackedOpenAI, "run", run)
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(Brand(id="brand", name="Test", source_directory="test"))
            session.commit()
        c = client.post("/api/campaigns", json={"brand_id": "brand"}).json()
        path = f"/api/campaigns/{c['id']}"
        assert client.post(path + "/approve", json={"version": 0}).status_code == 409
        c = client.post(path + "/messages", json={"text": "Brief", "version": 0}).json()
        assert c["state"] == ("clarifying" if questions else "awaiting_approval")
        assert len(c["trace"]) == 5
        assert all(step["status"] == "completed" for step in c["trace"])
        assert client.post(path + "/messages", json={"text": "stale", "version": 0}).status_code == 409
        if questions:
            assert "Approve & generate" not in c["messages"][-1]["text"]
            assert client.post(path + "/approve", json={"version": 1}).status_code == 409
            assert client.post(path + "/execute", json={"version": 1}).status_code == 409
        else:
            assert c["plans"][-1]["assets"][0]["angle"] == "Evidence for vendor reviews"
            assert c["plans"][-1]["assets"][0]["hook"] == "What does your vendor assessment cover?"
            assert client.post(path + "/approve", json={"version": 1}).json()["state"] == "approved"
        c = client.post(path + "/messages", json={"text": "Revise", "version": 1}).json()
        assert c["state"] == ("clarifying" if questions else "awaiting_approval")
        assert len(c["plans"]) == 2
        assert len(client.get(path).json()["messages"]) == 4
    with TestClient(create_app(settings)) as client:
        assert client.get(path).json()["version"] == 2


@pytest.mark.parametrize("state", ["planning", "awaiting_approval", "approved", "planning_error"])
def test_direct_reply_skips_evidence_and_preserves_plan(tmp_path, monkeypatch, state):
    def no_retrieval(*args, **kwargs):
        pytest.fail("Direct replies must not enter the evidence workflow")

    for name in ("plan_retrieval", "execute_retrieval", "review_campaign"):
        monkeypatch.setattr(campaigns, name, no_retrieval)

    def run(self, request, **kwargs):
        if request.operation == "campaign_direct_answer":
            return SimpleNamespace(parsed=campaigns.ReplyParagraph(text="5 + 5 = 10.", source_ids=[]), run_id="answer")
        assert request.operation == "campaign_message_route"
        assert kwargs["output_model"] is campaigns.MessageRoute
        return SimpleNamespace(parsed=campaigns.MessageRoute(target_asset_id=None, route="direct", reply="5 + 5 = 10."),
                               run_id="route")

    monkeypatch.setattr(campaigns.TrackedOpenAI, "run", run)
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'test.db'}")
    plans = [{"goal": "Existing campaign"}] if state != "planning" else []
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(campaigns.Campaign(id="campaign", brand_id="brand", state=state,
                data={"messages": [], "plans": plans}))
            session.commit()
        path = "/api/campaigns/campaign"
        response = client.post(path + "/messages", json={"text": "what is 5+5?", "version": 0})
        assert response.status_code == 200
        c = response.json()
        assert c["messages"][-1] == {"role": "assistant", "text": "5 + 5 = 10."}
        assert c["plans"] == plans
        assert c["state"] == state
        assert c["version"] == 1
        assert [step["name"] for step in c["trace"]] == ["Understand request"]
        assert c["trace"][0]["status"] == "completed"
        assert client.get(path).json() == c
        assert client.post(path + "/messages", json={"text": "hi", "version": 0}).status_code == 409


def test_review_failure_is_preserved(tmp_path, monkeypatch):
    from app.brand.retrieval import RetrievalError
    monkeypatch.setattr(campaigns, "plan_retrieval", lambda *a, **k: {"id": "p", "plan": {}})
    monkeypatch.setattr(campaigns, "execute_retrieval", lambda *a, **k: {"id": "r"})
    def review(*args, **kwargs):
        raise RetrievalError("Evidence exceeds review budget")
    monkeypatch.setattr(campaigns, "review_campaign", review)
    monkeypatch.setattr(campaigns.TrackedOpenAI, "run", lambda *a, **k:
                        SimpleNamespace(parsed=campaigns.MessageRoute(target_asset_id=None, route="campaign", reply=""), run_id="route"))
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(campaigns.Campaign(id="c", brand_id="b", data={"messages": [], "plans": []}))
            session.commit()
        assert client.post("/api/campaigns/c/messages", json={"text": "Brief", "version": 0}).status_code == 422
        c = client.get("/api/campaigns/c").json()
        assert c["trace"][-1]["status"] == "failed"
        assert c["plans"] == []


def test_question_limit_preserves_legacy_plans():
    from pydantic import ValidationError

    fields = {"goal": "Five posts", "assumptions": [], "constraints": [], "assets": [],
              "questions": ["Which product?", "Which tone?"]}
    with pytest.raises(ValidationError):
        campaigns.PlanOutline(**fields)
    assert len(campaigns.Plan(reply="", reply_paragraphs=[], **fields).questions) == 2


def test_campaign_plan_schema_is_valid_for_model_requests():
    from app.llm.client import strict_output_schema

    schema = strict_output_schema(campaigns.PlanOutline)["format"]["schema"]
    asset = schema["$defs"]["PlannedAsset"]
    assert {"angle", "hook"} <= set(asset["required"])
    legacy = campaigns.Asset(id="old", format="linkedin", audience="Leads",
                             purpose="Review", cta="Contact us", timing=None)
    assert legacy.angle == legacy.hook == ""


def test_history_orders_chats_by_recent_activity(tmp_path):
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'history.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            for identifier, timestamp in [("active", "2026-11-03T12:00:00+00:00"),
                                          ("older", "2026-11-02T12:00:00+00:00")]:
                session.add(campaigns.Campaign(id=identifier, brand_id="brand",
                    data={"messages": [], "plans": [], "updated_at": timestamp}))
            session.commit()
        assert [c["id"] for c in client.get("/api/campaigns").json()] == ["active", "older"]


def test_inspector_includes_legacy_retrieval_planning_run(tmp_path):
    from app.models.run import LLMRun

    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'runs.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(campaigns.Campaign(id="campaign", brand_id="brand", data={
                "trace": [{"name": "Understand brief and plan searches",
                           "details": {"run_id": "planner"}}]}))
            for identifier, context in [
                ("planner", {"brand_id": "brand"}),
                ("unrelated", {"brand_id": "brand"}),
                ("current", {"campaign_id": "campaign"}),
                ("other-campaign", {"campaign_id": "other"}),
            ]:
                session.add(LLMRun(id=identifier, operation="plan_retrieval",
                    context=context, requested_model="test",
                    request_body={}, response_body={"output_text": "Questions"},
                    timeout_seconds=30, status="completed"))
            session.commit()
        runs = client.get("/api/campaigns/campaign/runs").json()
        assert [run["id"] for run in runs] == ["planner", "current"]


def test_message_route_schema_requires_nullable_target():
    from app.llm.client import strict_output_schema

    schema = strict_output_schema(campaigns.MessageRoute)["format"]["schema"]
    assert set(schema["required"]) == {"route", "reply", "target_asset_id"}
    assert campaigns.MessageRoute(route="campaign", reply="", target_asset_id=None).target_asset_id is None
    assert campaigns.MessageRoute(route="revise", reply="", target_asset_id="post-1").target_asset_id == "post-1"


def test_draft_citations_are_bound_to_evidence():
    from pydantic import ValidationError

    from app.llm.client import strict_output_schema

    model = campaigns.draft_output_model([{"chunk_id": "evidence-1"}])
    strict_output_schema(model)
    fields = {"title": "Post", "subject": None, "preheader": None, "sender_name": None,
              "byline": None, "body": "Text"}
    assert model(**fields, source_ids=["evidence-1"]).source_ids == ["evidence-1"]
    with pytest.raises(ValidationError):
        model(**fields, source_ids=["post4"])
    empty = campaigns.draft_output_model([])
    strict_output_schema(empty)
    assert empty(**fields, source_ids=[]).source_ids == []
    with pytest.raises(ValidationError):
        empty(**fields, source_ids=["invented"])


def test_long_conversation_is_condensed_for_followup_retrieval():
    import json

    messages = [
        {"role": "user", "text": "Launch the containment product for security leads."},
        {"role": "assistant", "text": "Earlier campaign detail. " * 400},
        {"role": "user", "text": "write an email campaign for it"},
    ]
    plan = {"goal": "Containment launch", "assets": [{"id": "post-1", "format": "linkedin"}]}
    campaign = SimpleNamespace(id="c", brand_id="b", data={"plans": [plan]})
    requests = []

    class Client:
        def run(self, request, **kwargs):
            requests.append(request)
            assert kwargs["output_model"] is campaigns.RetrievalBrief
            payload = json.loads(request.input)
            assert payload["conversation"] == messages
            assert payload["current_plan"] == [plan]
            assert payload["latest_instruction"] == messages[-1]["text"]
            return SimpleNamespace(parsed=campaigns.RetrievalBrief(
                brief="Add an email campaign for the containment launch, targeting security leads."))

    brief = campaigns.campaign_retrieval_brief(
        Client(), Settings(_env_file=None), campaign, messages, messages[-1]["text"])
    assert len(brief) <= 6000
    assert "email" in brief
    assert len(requests) == 1
    assert requests[0].context.campaign_id == "c"
    assert campaign.data["plans"] == [plan]


def test_short_conversation_needs_no_condensation_call():
    import json

    class Client:
        def run(self, *args, **kwargs):
            pytest.fail("Short histories should not incur a condensation call")

    messages = [{"role": "user", "text": "Create one email"}]
    assert campaigns.campaign_retrieval_brief(
        Client(), Settings(_env_file=None), None, messages, "Create one email") == json.dumps(messages)
