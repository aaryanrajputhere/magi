from fastapi.testclient import TestClient

from app.api.campaigns import Campaign
from app.config import Settings
from app.main import create_app
from app.models.brand import Brand
from app.models.context_artifact import ContextArtifact


def test_activity_preserves_failed_retrieval_and_scopes_policy_context(tmp_path):
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(Brand(id="brand", name="Test", source_directory="test"))
            session.commit()
            session.add(ContextArtifact(id="retrieval", brand_id="brand", kind="retrieval",
                                        corpus_fingerprint="test", payload={}))
            session.commit()
            for ident, parent in [("linked", "retrieval"), ("unrelated", None)]:
                session.add(ContextArtifact(id=ident, brand_id="brand", kind="selection",
                    parent_id=parent, corpus_fingerprint="test", payload={
                        "coverage_complete": False, "limitations": ["Missing dependency"],
                        "sections": [{"id": "kept", "included": True,
                                      "passages": [{"chunk_id": "policy", "content": "Policy text"}]},
                                     {"id": "excluded", "included": False, "passages": []}]}))
            session.add(Campaign(id="campaign", brand_id="brand", state="planning_error", data={
                "messages": [{"role": "user", "text": "Launch brief"}],
                "trace": [{"name": "Understand brief and plan searches", "status": "completed",
                           "details": {"run_id": "planner", "plan": {"queries": [
                               {"id": "topic_1", "purpose": "product", "query": "Leashgate scope"},
                               {"id": "channel__x__rules", "purpose": "channel", "query": "X guidelines"}
                           ]}}}, {"name": "Retrieve evidence", "status": "completed", "details": {
                    "id": "retrieval", "groups": [{"query": {"id": "Q1", "query": "Product"},
                    "results": [{"chunk_id": "chunk", "content": "Original source", "rank": 1}]}]}},
                    {"name": "Review brand evidence", "status": "failed", "error": "Incomplete"}]}))
            session.commit()
        response = client.get("/api/campaigns/campaign/activity")
        assert response.status_code == 200
        data = response.json()
        assert data["brief"] == "Launch brief"
        assert data["search_plans"][0]["run_id"] == "planner"
        assert [q["id"] for q in data["search_plans"][0]["plan"]["queries"]] == [
            "topic_1", "channel__x__rules"]
        assert data["retrievals"][0]["groups"][0]["results"][0]["content"] == "Original source"
        assert data["trace"][-1]["error"] == "Incomplete"
        assert [s["id"] for s in data["selections"]] == ["linked"]
        assert [s["id"] for s in data["selections"][0]["sections"]] == ["kept"]
        assert client.get("/api/campaigns/missing/activity").status_code == 404
