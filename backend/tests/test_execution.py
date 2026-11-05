from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from app.api import campaigns as api
from app.config import Settings
from app.db import create_database_engine, initialize_database


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    initialize_database(engine)
    review = {"id": "review", "authority_id": "authority", "report": {
        "supported": [], "restricted": [], "conflicts": [], "unresolved": []}, "evidence": []}
    monkeypatch.setattr(api, "load_artifact", lambda *a, **k: review)
    monkeypatch.setattr(api, "current_authority", lambda *a, **k: None)
    plan = {"goal": "Awareness", "questions": [], "assumptions": [], "constraints": [], "review_id": "review",
                "assets": [{"id": f"post-{i}", "format": "linkedin", "purpose": "Concept", "angle": f"Angle {i}", "hook": "Review evidence", "audience": "Leads", "cta": "Docs", "timing": None} for i in range(3)]}
    with Session(engine) as session:
        session.add(api.Campaign(id="c", brand_id="b", state="awaiting_approval",
                                version=2, data={"messages": [], "plans": [plan]}))
        session.commit()
    yield engine, Settings(_env_file=None), review
    engine.dispose()


def test_execute_only_current_plan_and_no_duplicate_start(workspace, monkeypatch):
    engine, settings, _ = workspace
    calls = []
    def run(self, request, **kwargs):
        calls.append(request.context.asset_id)
        from app.llm.client import strict_output_schema
        strict_output_schema(kwargs["output_model"])
        return SimpleNamespace(parsed=api.GeneratedDraft(title="Draft", subject=None, preheader=None, sender_name=None, byline=None,
                               body="Approved draft", source_ids=[]), run_id="run")
    monkeypatch.setattr(api.TrackedOpenAI, "run", run)
    with Session(engine) as session:
        with pytest.raises(HTTPException):
            api.execute("c", api.Approval(version=1), BackgroundTasks(), session, settings)
        tasks = BackgroundTasks()
        result = api.execute("c", api.Approval(version=2), tasks, session, settings)
        assert result["state"] == "generating"
        assert not calls
        with pytest.raises(HTTPException):
            api.execute("c", api.Approval(version=2), BackgroundTasks(), session, settings)
    api.generate_drafts(engine, "c", settings)
    with Session(engine) as session:
        c = api.load(session, "c")
        assert c.state == "ready"
        assert len(c.data["generation"]["drafts"]) == 3
        assert c.data["generation"]["drafts"][0]["preheader"] is None
    assert calls == ["post-0", "post-1", "post-2"]


def test_plan_adjustment_cannot_return_drafts(workspace, monkeypatch):
    engine, settings, review = workspace
    monkeypatch.setattr(api, "plan_retrieval", lambda *a, **k: {"id": "search", "plan": {}})
    monkeypatch.setattr(api, "execute_retrieval", lambda *a, **k: {"id": "retrieval"})
    monkeypatch.setattr(api, "review_campaign", lambda *a, **k: review)
    def run(self, request, **kwargs):
        assert request.operation != "generate_campaign_asset"
        if request.operation == "campaign_message_route":
            return SimpleNamespace(parsed=api.MessageRoute(target_asset_id=None, route="campaign", reply=""), run_id="r")
        assert kwargs["output_model"] is api.PlanOutline
        with Session(engine) as session:
            plan = api.load(session, "c").data["plans"][-1]
        return SimpleNamespace(parsed=api.PlanOutline.model_validate({k: v for k, v in plan.items() if k in api.PlanOutline.model_fields}), run_id="plan")
    monkeypatch.setattr(api.TrackedOpenAI, "run", run)
    with Session(engine) as session:
        c = api.message("c", api.Message(text="Actually create only three", version=2), session, settings)
        assert c["state"] == "awaiting_approval"
        assert len(c["plans"][-1]["assets"]) == 3
        assert "Updated the plan" in c["messages"][-1]["text"]
        assert "generation" not in c


def test_retry_preserves_completed_drafts(workspace, monkeypatch, caplog):
    engine, settings, _ = workspace
    calls = []
    fail = [True]
    def run(self, request, **kwargs):
        asset = request.context.asset_id
        calls.append(asset)
        if asset == "post-1" and fail[0]:
            raise RuntimeError("Temporary failure")
        return SimpleNamespace(parsed=api.GeneratedDraft(title="Draft", subject=None, preheader=None, sender_name=None, byline=None,
            body="Approved draft", source_ids=[]), run_id="run")
    monkeypatch.setattr(api.TrackedOpenAI, "run", run)
    with Session(engine) as session:
        api.execute("c", api.Approval(version=2), BackgroundTasks(), session, settings)
    api.generate_drafts(engine, "c", settings)
    with Session(engine) as session:
        c = api.load(session, "c")
        assert c.state == "generation_error"
        assert len(c.data["generation"]["drafts"]) == 1
        api.execute("c", api.Approval(version=2), BackgroundTasks(), session, settings)
    assert "Draft generation failed for campaign c, asset post-1" in caplog.text
    assert "Temporary failure" in caplog.text
    assert caplog.records[-1].exc_info is not None
    fail[0] = False
    api.generate_drafts(engine, "c", settings)
    assert calls.count("post-0") == 1
    with Session(engine) as session:
        assert api.load(session, "c").state == "ready"


def test_wickhold_corrections_proceed_with_five_angles(workspace, monkeypatch):
    engine, settings, review = workspace
    wording = ["Wickhold holds SOC 2 Type II.",
               "Wickhold is pursuing FedRAMP Moderate authorization."]
    review["report"]["claims"] = [{"claim_key": key, "decision": "corrected",
        "approved_wording": text, "source_ids": ["legal"]} for key, text in
        zip(["soc2_status", "fedramp_status"], wording)]
    review["evidence"] = [{"chunk_id": "legal", "title": "Certification status",
        "content": " ".join(wording), "source_ids": []}]
    monkeypatch.setattr(api, "plan_retrieval", lambda *a, **k: {"id": "search", "plan": {}})
    monkeypatch.setattr(api, "execute_retrieval", lambda *a, **k: {"id": "retrieval"})
    monkeypatch.setattr(api, "review_campaign", lambda *a, **k: review)

    def run(self, request, **kwargs):
        if request.operation == "campaign_message_route":
            return SimpleNamespace(parsed=api.MessageRoute(target_asset_id=None, route="campaign", reply=""), run_id="route")
        if request.operation == "campaign_plan_check":
            return SimpleNamespace(parsed=api.PlanCheck(findings=[]), run_id="check")
        assert "Never ask whether to use a corrected claim" in request.instructions
        return SimpleNamespace(parsed=api.PlanOutline(goal="Five posts", questions=[],
            assumptions=[], constraints=wording, assets=[api.PlannedAsset(
                id=f"post-{i}", format="linkedin", audience="Security leaders",
                purpose="Vendor diligence", angle=f"Angle {i}", hook=wording[i % 2],
                cta="Contact the account team", timing=None) for i in range(5)]), run_id="plan")

    monkeypatch.setattr(api.TrackedOpenAI, "run", run)
    with Session(engine) as session:
        result = api.message("c", api.Message(text="Write five LinkedIn posts", version=2),
                             session, settings)
    assert result["state"] == "awaiting_approval"
    assert len(result["plans"][-1]["assets"]) == 5
    assert not result["plans"][-1]["questions"]
    paragraphs = result["messages"][-1]["paragraphs"]
    assert len(paragraphs) == 2
    assert paragraphs[1]["source_ids"] == ["legal"]
    assert all(text in paragraphs[1]["text"] for text in wording)
    assert "Evidence issue" not in result["messages"][-1]["text"]


def test_extension_preserves_edited_drafts(workspace, monkeypatch):
    engine, settings, _ = workspace
    with Session(engine) as session:
        c = api.load(session, "c")
        base = c.data["plans"][0]
        addition = {**base["assets"][0], "id": "extra"}
        extended = {**base, "assets": [*base["assets"], addition],
                    "extension": {"base_plan_index": 0, "added_asset_ids": ["extra"]}}
        originals = [{"asset_id": a["id"], "body": "Edited without link", "status": "completed",
                      "run_id": "old", "format": "linkedin"} for a in base["assets"]]
        c.data = {**c.data, "plans": [base, extended], "generation": {
            "plan_index": 0, "drafts": originals}}
        session.commit()
        result = api.execute("c", api.Approval(version=2), BackgroundTasks(), session, settings)
        assert [d["body"] for d in result["generation"]["drafts"]] == ["Edited without link"] * 3
    calls = []
    def run(self, request, **kwargs):
        calls.append(request.context.asset_id)
        return SimpleNamespace(parsed=api.GeneratedDraft(title="New", subject=None, preheader=None, sender_name=None, byline=None, body="New post", source_ids=[]), run_id="new")
    monkeypatch.setattr(api.TrackedOpenAI, "run", run)
    api.generate_drafts(engine, "c", settings)
    assert calls == ["extra"]
    with Session(engine) as session:
        job = api.load(session, "c").data["generation"]
        assert len(job["drafts"]) == 4
        assert job["last_revised_asset_id"] == "extra"


def test_failed_plan_revision_retains_approval(workspace, monkeypatch):
    from app.brand.retrieval import RetrievalError

    engine, settings, _ = workspace
    def fail(*args, **kwargs):
        raise RetrievalError('Invalid question quotation', 'invalid_review', 422)
    monkeypatch.setattr(api, 'plan_retrieval', fail)
    with Session(engine) as session:
        original = api.load(session, 'c').data['plans']
        with pytest.raises(HTTPException):
            api.process_message('c', api.Message(text='Change the hook', version=2), session, settings,
                forced_route=api.MessageRoute(route='campaign', target_asset_id=None, reply=''))
        c = api.load(session, 'c')
        assert c.state == 'awaiting_approval'
        assert c.data['plans'] == original
        assert c.data['trace'][-1]['status'] == 'failed'
        assert c.data['planning_error'] == 'Invalid question quotation'
        assert api.approve('c', api.Approval(version=c.version), session)['state'] == 'approved'
