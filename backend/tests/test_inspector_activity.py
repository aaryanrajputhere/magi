import pytest
from fastapi.testclient import TestClient

from app.api.campaigns import Campaign
from app.config import Settings
from app.main import create_app
from app.models.brand import Brand
from app.models.context_artifact import ContextArtifact


@pytest.mark.parametrize('verified', [False, True])
def test_activity_preserves_failed_retrieval_and_scopes_policy_context(tmp_path, verified):
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(Brand(id="brand", name="Test", source_directory="test"))
            session.commit()
            session.add(ContextArtifact(id="retrieval", brand_id="brand", kind="retrieval",
                                        corpus_fingerprint="test", payload={}))
            session.commit()
            rule = {"chunk_id": "rule", "content": "Original governing rule"}
            checkpoint_payload = {"tracks": {"x": {"rule_pack": {"evidence": [rule]}}}} if verified else {"query_groups": [
                    {"query": {"id": "channel__x__governing", "query": "x governing rules"},
                     "results": [rule]}]}
            session.add(ContextArtifact(id="checkpoint", brand_id="brand", kind="campaign_review",
                corpus_fingerprint="test", payload=checkpoint_payload))
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
                "retrieved_context": {"artifact_id": "checkpoint"},
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
        assert data["governing_evidence"][0]["results"][0]["content"] == "Original governing rule"
        assert data["search_plans"][0]["run_id"] == "planner"
        assert [q["id"] for q in data["search_plans"][0]["plan"]["queries"]] == [
            "topic_1", "channel__x__rules"]
        assert data["retrievals"][0]["groups"][0]["results"][0]["content"] == "Original source"
        assert data["trace"][-1]["error"] == "Incomplete"
        assert [s["id"] for s in data["selections"]] == ["linked"]
        assert [s["id"] for s in data["selections"][0]["sections"]] == ["kept"]
        assert client.get("/api/campaigns/missing/activity").status_code == 404


@pytest.mark.parametrize('summary', [False, True])
def test_inspector_recovers_unpriced_luna_runs_without_changing_saved_costs(tmp_path, summary):
    from decimal import Decimal

    from app.models.run import LLMRun

    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'costs.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(Campaign(id="campaign", brand_id="brand", data={}))
            for identifier, cost, reason, usage in [
                ('recover', None, 'No verified pricing for returned model', {
                    'input_tokens': 1000, 'output_tokens': 100,
                    'input_tokens_details': {'cached_tokens': 200}}),
                ('saved', Decimal('0.123'), None, None),
                ('missing', None, 'Usage not reported; provider billing is unknown', None),
                ('tier', None, 'Returned service tier is unsupported by standard pricing', {
                    'input_tokens': 1000, 'output_tokens': 100,
                    'input_tokens_details': {'cached_tokens': 200}}),
            ]:
                session.add(LLMRun(id=identifier, operation='campaign_plan',
                    context={'campaign_id': 'campaign'}, requested_model='gpt-6-luna',
                    returned_model='gpt-6-luna', request_body={}, response_body={},
                    timeout_seconds=30, status='completed', raw_usage=usage,
                    estimated_cost_usd=cost, cost_reason=reason))
            session.commit()
        response = client.get('/api/campaigns/campaign/runs', params={'summary': summary})
        assert response.status_code == 200
        costs = {run['id']: run['cost'] for run in response.json()}
        assert Decimal(str(costs['recover'])) == Decimal('0.000132')
        assert Decimal(str(costs['saved'])) == Decimal('0.123')
        assert costs['missing'] is None and costs['tier'] is None
        with client.app.state.sessions() as session:
            assert session.get(LLMRun, 'recover').estimated_cost_usd is None


@pytest.mark.parametrize('summary', [False, True])
def test_inspector_exposes_cache_usage_including_zero_and_unknown(tmp_path, summary):
    from app.models.run import LLMRun

    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'cache.db'}")
    with TestClient(create_app(settings)) as client:
        with client.app.state.sessions() as session:
            session.add(Campaign(id='campaign', brand_id='brand', data={}))
            for identifier, cached in [('hit', 600), ('miss', 0), ('unknown', None)]:
                session.add(LLMRun(id=identifier, operation='campaign_agent_step',
                    requested_model='test', request_body={}, timeout_seconds=60,
                    status='completed', input_tokens=1000,
                    cached_input_tokens=cached, context={'campaign_id': 'campaign'}))
            session.commit()
        response = client.get('/api/campaigns/campaign/runs', params={'summary': summary})
        assert response.status_code == 200
        rows = {row['id']: row for row in response.json()}
        assert {key: row['cached_input_tokens'] for key, row in rows.items()} == {
            'hit': 600, 'miss': 0, 'unknown': None}
        assert all(row['input_tokens'] == 1000 for row in rows.values())
