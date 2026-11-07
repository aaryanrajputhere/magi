import copy
import json

import httpx
import pytest
import test_semantic as semantic_fixtures
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_llm import response_body

from app.brand import context
from app.brand.retrieval import RetrievalError, build_index
from app.config import Settings
from app.llm.client import TrackedOpenAI, strict_output_schema
from app.models import ContextArtifact, Document, LLMRun

corpus = semantic_fixtures.corpus


def proposed():
    return {
        "analysis": {
            "topic": "FedRAMP",
            "products": [],
            "audience": None,
            "channels": ["linkedin"],
            "requested_assets": [{"kind": "social_post", "channel": "linkedin", "count": 5}],
            "unknowns": ["Audience unspecified"],
        },
        "queries": [
            {
                "id": "topic",
                "purpose": "claims",
                "query": "orchid gateway status approved claims",
                "reason": "Verify the requested claim",
            },
            {
                "id": "channel",
                "purpose": "channel",
                "query": "budgets approval",
                "reason": "Retrieve channel guidance",
            },
            {
                "id": "voice",
                "purpose": "voice",
                "query": "brand voice tone",
                "reason": "Retrieve writing guidance",
            },
        ],
    }


@pytest.fixture
def client(corpus):
    calls = []

    def transport(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, json=response_body(json.dumps(proposed())))
        return httpx.Response(
            200,
            json={
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 12, "total_tokens": 12},
                "data": [
                    {
                        "index": i,
                        "embedding": semantic_fixtures.vector(1 if "budget" in query else 0),
                    }
                    for i, query in enumerate(body["input"])
                ],
            },
        )

    sdk = OpenAI(
        api_key="sk-fake", http_client=httpx.Client(transport=httpx.MockTransport(transport))
    )
    tracked = TrackedOpenAI(corpus[0], Settings(_env_file=None, openai_api_key="sk-fake"), sdk=sdk)
    yield tracked, calls
    sdk.close()


def stages(corpus, client):
    engine, brand, _ = corpus
    build_index(engine, brand)
    tracked, _ = client
    plan = context.plan_retrieval(
        engine, brand, "Create five LinkedIn posts about FedRAMP", client=tracked
    )
    retrieved = context.execute_retrieval(engine, brand, plan["id"], client=tracked)
    return plan, retrieved


def test_plan_tracks_structured_output_and_preserves_unknowns(corpus, client):
    engine, brand, _ = corpus
    tracked, calls = client
    result = context.plan_retrieval(
        engine, brand, "Create five LinkedIn posts about FedRAMP", client=tracked,
        campaign_id="campaign-test"
    )
    assert result["plan"]["analysis"]["requested_assets"][0]["count"] == 5
    assert len(result["plan"]["queries"]) == 3
    assert result["plan"]["analysis"]["audience"] is None
    assert len(calls) == 1 and calls[0][0].endswith("/responses")
    assert result["usage"]["input_tokens"] == 1000
    assert result["usage"]["estimated_cost_usd"] is not None
    with Session(engine) as session:
        assert session.get(LLMRun, result["run_id"]).operation == "plan_retrieval"
        assert session.get(LLMRun, result["run_id"]).context["campaign_id"] == "campaign-test"
    assert strict_output_schema(context.RetrievalPlan)["format"]["strict"]


def test_manual_stages_and_local_source_preserving_assembly(corpus, client):
    engine, brand, _ = corpus
    plan, retrieved = stages(corpus, client)
    assert retrieved["parent_id"] == plan["id"] and retrieved["status"] == "success"
    assert len(retrieved["groups"]) == 3
    assert len(client[1]) == 2  # One planner call and one batch embedding call.
    compact = context.assemble_context(
        engine, brand, retrieved["id"], token_counter=lambda text: len(text) // 4
    )
    assert len(client[1]) == 2
    assert compact["context_tokens"] <= compact["max_tokens"]
    assert compact["unique_chunks"] == 3 and compact["duplicates_removed"] > 0
    assert len(compact["evidence"]) == 3
    assert not compact["authority_resolved"]
    for entry in compact["evidence"]:
        assert compact["context_text"].count(entry["source"]["content"]) == 1
        assert entry["query_ids"] and entry["purposes"]
        with Session(engine) as session:
            assert (
                entry["source"]["content"]
                in session.get(Document, entry["source"]["document_id"]).raw_text
            )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda plan: plan["queries"].append(copy.deepcopy(plan["queries"][0])),
        lambda plan: plan["queries"][1].update(query=plan["queries"][0]["query"].upper()),
        lambda plan: plan["queries"][0].update(query=" "),
        lambda plan: plan["queries"][0].update(query=" ".join(f"w{i}" for i in range(33))),
        lambda plan: plan["queries"][0].update(query="x" * 513),
        lambda plan: plan.update(queries=plan["queries"] * 3),
    ],
)
def test_invalid_query_plans_rejected(mutate):
    plan = proposed()
    mutate(plan)
    with pytest.raises(ValueError):
        context.RetrievalPlan.model_validate(plan)


def test_invalid_briefs_and_brands_make_no_calls(corpus, client):
    tracked, calls = client
    for brief in ["", " ", "x" * 6001]:
        with pytest.raises(RetrievalError):
            context.plan_retrieval(corpus[0], corpus[1], brief, client=tracked)
    with pytest.raises(RetrievalError):
        context.plan_retrieval(corpus[0], "missing", "Brief", client=tracked)
    assert not calls


def test_cross_brand_ids_cannot_be_executed_or_assembled(corpus, client):
    engine, _brand, other = corpus
    plan, retrieved = stages(corpus, client)
    with pytest.raises(RetrievalError) as error:
        context.execute_retrieval(engine, other, plan["id"], client=client[0])
    assert error.value.status == 404
    with pytest.raises(RetrievalError):
        context.assemble_context(engine, other, retrieved["id"], token_counter=len)


def test_source_change_invalidates_downstream_stages(corpus, client):
    engine, brand, _ = corpus
    plan, retrieved = stages(corpus, client)
    with Session(engine) as session:
        doc = session.scalar(select(Document))
        doc.title = "Changed title"
        session.commit()
    before = len(client[1])
    with pytest.raises(RetrievalError) as error:
        context.execute_retrieval(engine, brand, plan["id"], client=client[0])
    assert error.value.status == 409
    with pytest.raises(RetrievalError):
        context.assemble_context(engine, brand, retrieved["id"], token_counter=len)
    assert len(client[1]) == before


def test_failed_query_batch_is_visible_and_cannot_assemble(corpus, client, monkeypatch):
    engine, brand, _ = corpus
    build_index(engine, brand)
    plan = context.plan_retrieval(engine, brand, "Brief", client=client[0])

    def failed(*args, **kwargs):
        raise RetrievalError(
            "Provider failed, inspect saved run example", "query_embedding_failed", 502
        )

    monkeypatch.setattr(context, "query_vectors", failed)
    result = context.execute_retrieval(engine, brand, plan["id"], client=client[0])
    assert result["status"] == "failed"
    assert all(group["error"] for group in result["groups"])
    with pytest.raises(RetrievalError, match="No successful evidence"):
        context.assemble_context(engine, brand, result["id"], token_counter=len)


def test_partial_retrieval_warns_about_uncovered_searches(corpus, client, monkeypatch):
    engine, brand, _ = corpus
    build_index(engine, brand)
    plan = context.plan_retrieval(engine, brand, "Brief", client=client[0])
    original = context.fuse

    def partial(keyword, semantic, limit):
        if keyword.query == proposed()["queries"][1]["query"]:
            raise ValueError("Local ranking failed")
        return original(keyword, semantic, limit)

    monkeypatch.setattr(context, "fuse", partial)
    result = context.execute_retrieval(engine, brand, plan["id"], client=client[0])
    assert result["status"] == "partial"
    compact = context.assemble_context(
        engine, brand, result["id"], token_counter=lambda text: len(text) // 4
    )
    assert "channel" in compact["uncovered_query_ids"]
    assert any("Local ranking failed" in warning for warning in compact["warnings"])


def test_budget_keeps_whole_passages_and_reports_exclusions(corpus, client):
    engine, brand, _ = corpus
    _, retrieved = stages(corpus, client)

    # Controlled token counter charges each complete passage 550 tokens.
    def count(text):
        return 100 + sum(
            550
            for phrase in [
                "Orchid gateway refuses unsafe calls.",
                "Budgets require approval.",
                "Another gateway example.",
            ]
            if phrase in text
        )

    compact = context.assemble_context(
        engine, brand, retrieved["id"], max_tokens=1000, token_counter=count
    )
    assert len(compact["evidence"]) == 1 and len(compact["excluded"]) == 2
    assert compact["context_tokens"] == 650
    assert all(item["reason"] == "context_budget" for item in compact["excluded"])
    with pytest.raises(RetrievalError):
        context.assemble_context(
            engine, brand, retrieved["id"], max_tokens=999, token_counter=count
        )


def test_wrapper_calls_stages_in_order(corpus, monkeypatch):
    order = []
    monkeypatch.setattr(
        context, "plan_retrieval", lambda *args, **kwargs: order.append("plan") or {"id": "p"}
    )
    monkeypatch.setattr(
        context,
        "execute_retrieval",
        lambda *args, **kwargs: order.append("retrieve") or {"id": "r"},
    )
    monkeypatch.setattr(
        context,
        "assemble_context",
        lambda *args, **kwargs: order.append("assemble") or {"context_text": "Evidence"},
    )
    assert context.get_brand_context(corpus[0], corpus[1], "Brief")["context_text"] == "Evidence"
    assert order == ["plan", "retrieve", "assemble"]


def test_missing_key_is_actionable_and_preserves_failed_run(corpus, client):
    tracked, calls = client
    tracked.settings.openai_api_key = None
    with pytest.raises(RetrievalError) as error:
        context.plan_retrieval(corpus[0], corpus[1], "Brief", client=tracked)
    assert error.value.status == 503 and not calls
    with Session(corpus[0]) as session:
        run = session.scalar(select(LLMRun).where(LLMRun.operation == "plan_retrieval"))
        assert run.status == "configuration_error" and run.estimated_cost_usd == 0
        assert session.scalar(select(ContextArtifact)) is None


def test_corpus_changes_during_planning_are_not_saved(corpus, client, monkeypatch):
    tracked, _ = client
    original = tracked.run

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(corpus[0]) as session:
            document = session.scalar(select(Document))
            document.title = "Changed during planning"
            session.commit()
        return result

    monkeypatch.setattr(tracked, "run", changing)
    with pytest.raises(RetrievalError) as error:
        context.plan_retrieval(corpus[0], corpus[1], "Brief", client=tracked)
    assert error.value.status == 409
    with Session(corpus[0]) as session:
        assert session.scalar(select(ContextArtifact)) is None
        assert session.scalar(select(LLMRun)).status == "completed"


def test_stage_api_uses_saved_ids_and_validates_budget(corpus, client, monkeypatch):
    from app.api import retrieval as api
    from app.api.retrieval import AssembleRequest, BriefRequest, ExecuteRequest

    engine, brand, _ = corpus
    build_index(engine, brand)
    settings = Settings(_env_file=None)
    original_plan, original_retrieve, original_assemble = (
        context.plan_retrieval,
        context.execute_retrieval,
        context.assemble_context,
    )
    monkeypatch.setattr(
        context,
        "plan_retrieval",
        lambda *args, **kwargs: original_plan(*args, **kwargs, client=client[0]),
    )
    monkeypatch.setattr(
        context,
        "execute_retrieval",
        lambda *args, **kwargs: original_retrieve(*args, **kwargs, client=client[0]),
    )
    monkeypatch.setattr(
        context,
        "assemble_context",
        lambda *args, **kwargs: original_assemble(
            *args, **kwargs, token_counter=lambda text: len(text) // 4
        ),
    )
    with Session(engine) as session:
        plan = api.plan_context(brand, BriefRequest(brief="Brief"), session, settings)
        retrieved = api.retrieve_context(
            brand, ExecuteRequest(plan_id=plan["id"]), session, settings
        )
        compact = api.compact_context(brand, AssembleRequest(retrieval_id=retrieved["id"]), session)
    assert compact["parent_id"] == retrieved["id"]
    with pytest.raises(ValueError):
        AssembleRequest(retrieval_id="id", max_tokens=True)
    with pytest.raises(ValueError):
        ExecuteRequest(plan_id=plan["id"], results=["client cannot supply evidence"])


def test_unavailable_tokenizer_fails_without_model_call(corpus, client):
    _, retrieved = stages(corpus, client)

    def unavailable(text):
        raise ValueError("Token counter unavailable")

    before = len(client[1])
    with pytest.raises(RetrievalError) as error:
        context.assemble_context(corpus[0], corpus[1], retrieved["id"], token_counter=unavailable)
    assert error.value.status == 503 and len(client[1]) == before


def test_campaign_plan_saves_full_requirements_and_expands_each_channel(corpus, monkeypatch):
    from types import SimpleNamespace

    from app.brand.campaign_requirements import CampaignRequirements

    engine, brand, _ = corpus
    source = {'conversation': [{'role': 'user', 'text': 'Full original launch brief ' * 400}],
              'route': 'campaign'}
    requirements = {'topic_details': [], 'topic': 'Leashgate scope', 'constraints': ['No customer proof'], 'unknowns': [], 'issues': [],
                    'assets': [{'id': f'asset-{i}', 'format': channel, 'audience': 'Platform leads',
                                'purpose': 'Launch', 'timing': '2027-01-26',
                                'sender_or_byline': None, 'constraints': [], 'provenance': []}
                               for i, channel in enumerate(['linkedin', 'x', 'email', 'blog', 'landing_page'])]}
    seen = []

    class Client:
        def run(self, request, **kwargs):
            seen.append(request.operation)
            payload = (json.loads(request.input) if isinstance(request.input, str) else
                       {k: v for m in request.input for k, v in json.loads(m['content']).items()})
            payload.pop('conversation_followup', None)
            if request.operation == 'campaign_requirements':
                assert payload == source
                return SimpleNamespace(parsed=CampaignRequirements.model_validate(requirements), run_id='extract')
            assert payload['requirements']['assets'] == requirements['assets']
            # Requirements have already been persisted before planning searches.
            with Session(engine) as session:
                saved = session.scalar(select(ContextArtifact).where(ContextArtifact.kind == 'campaign_requirements'))
                assert saved.payload['source'] == source
            return SimpleNamespace(parsed=context.RetrievalPlan.model_validate(proposed()), run_id='search')

    monkeypatch.setattr(context, 'usage_for_run', lambda *a: {})
    saved = context.plan_retrieval(engine, brand, 'Bounded search seed', client=Client(),
                                  campaign_id='c', campaign_source=source)
    assert seen == ['campaign_requirements', 'plan_retrieval']
    assert saved['requirements']['assets'] == requirements['assets']
    queries = saved['plan']['queries']
    assert len(queries) == 6  # One topic search, one search for each of five channels.
    assert len([q for q in queries if q['purpose'] == 'channel']) == 5
    assert saved['plan']['analysis']['channels'] == ['linkedin', 'x', 'email', 'blog', 'landing_page']
    with Session(engine) as session:
        persisted = session.get(ContextArtifact, saved['requirements']['artifact_id'])
        assert persisted.payload['source']['conversation'][0]['text'] == source['conversation'][0]['text']
