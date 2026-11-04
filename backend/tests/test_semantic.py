import hashlib
import json
from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException
from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api import retrieval as api
from app.brand.chunking import build_chunks
from app.brand.discovery import discover_folder
from app.brand.embeddings import DIMENSIONS, INPUT_VERSION, MODEL, embedding_text
from app.brand.ingestion import ingest_documents
from app.brand.retrieval import RetrievalError, build_index, index_status, search_chunks
from app.brand.retrieval_eval import EvaluationSuite, evaluate_suite
from app.brand.semantic import query_vectors, semantic_search, vector_snapshot
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import TrackedOpenAI
from app.models import Brand, ChunkEmbedding, Document, DocumentChunk, LLMRun


def vector(axis=0, scale=1.0):
    values = [0.0] * DIMENSIONS
    values[axis] = scale
    return values


@pytest.fixture
def corpus(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialize_database(engine)
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    for name, body in [
        ("a", "Orchid gateway refuses unsafe calls."),
        ("b", "Budgets require approval."),
        ("c", "Another gateway example."),
    ]:
        (root / "brand" / f"{name}.md").write_text(f"# {name}\n\n{body}\n")
    brand = ingest_documents(engine, discover_folder(root), "Example").brand_id
    build_chunks(engine, brand)
    with Session(engine) as session:
        run = LLMRun(
            operation="embed_chunks",
            context={"brand_id": brand},
            requested_model=MODEL,
            request_body={},
            status="completed",
            timeout_seconds=60,
        )
        session.add(run)
        session.flush()
        rows = session.execute(
            select(DocumentChunk, Document)
            .join(Document, DocumentChunk.document_id == Document.id)
            .order_by(Document.relative_path)
        ).all()
        for index, (chunk, doc) in enumerate(rows):
            session.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    model=MODEL,
                    dimensions=DIMENSIONS,
                    input_version=INPUT_VERSION,
                    source_hash=chunk.source_hash,
                    chunker_version=chunk.chunker_version,
                    input_hash=hashlib.sha256(embedding_text(doc, chunk).encode()).hexdigest(),
                    vector=vector(1 if index == 1 else 0, 10.0),
                    run_id=run.id,
                )
            )
        other = Brand(name="Other", source_directory="/other")
        session.add(other)
        session.commit()
        other_id = other.id
    yield engine, brand, other_id
    engine.dispose()


@pytest.fixture
def clients(corpus):
    sdks = []

    def create(handler=None, key="sk-fake-test-key"):
        calls = []

        def transport(request):
            body = json.loads(request.content)
            calls.append(body)
            if handler:
                return handler(request, body)
            data = [
                {
                    "index": i,
                    "embedding": vector(1 if "budget" in q.lower() else 0, 3.0),
                    "object": "embedding",
                }
                for i, q in enumerate(body["input"])
            ]
            return httpx.Response(
                200,
                json={
                    "model": MODEL,
                    "data": data,
                    "usage": {"prompt_tokens": 10, "total_tokens": 10},
                },
            )

        sdk = OpenAI(
            api_key="sk-fake-test-key",
            max_retries=3,
            http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        )
        sdks.append(sdk)
        return TrackedOpenAI(
            corpus[0], Settings(_env_file=None, openai_api_key=key), sdk=sdk
        ), calls

    yield create
    for sdk in sdks:
        sdk.close()


def suite():
    def group(id, path, quote):
        return {"id": id, "alternatives": [{"document_path": path, "quotation": quote}]}

    return EvaluationSuite.model_validate(
        {
            "id": "test",
            "name": "Test",
            "description": "Evidence",
            "cases": [
                {
                    "id": "one",
                    "category": "direct",
                    "query": "orchid",
                    "expected_groups": [
                        group("gateway", "brand/a.md", "Orchid gateway refuses unsafe calls.")
                    ],
                },
                {
                    "id": "two",
                    "category": "multi",
                    "query": "budgets and gateway",
                    "expected_groups": [
                        group("budget", "brand/b.md", "Budgets require approval."),
                        group("gateway", "brand/a.md", "Orchid gateway refuses unsafe calls."),
                    ],
                },
                {
                    "id": "absent",
                    "category": "negative",
                    "query": "AbsentProductZZZ",
                    "expected_groups": [],
                },
            ],
        }
    )


def test_cosine_ranking_normalization_ties_sources_and_no_fts_dependency(corpus, clients):
    engine, brand, _ = corpus
    client, calls = clients()
    with Session(engine) as session:
        assert index_status(session, brand).state == "missing"
    result = semantic_search(engine, brand, "Question about gateway", client=client)
    assert result.method == "semantic" and result.terms == []
    assert [hit.relative_path for hit in result.results] == [
        "brand/a.md",
        "brand/c.md",
        "brand/b.md",
    ]
    assert [hit.score for hit in result.results] == pytest.approx([1, 1, 0])
    assert all(hit.score_type == "cosine" for hit in result.results)
    assert result.query_embedding["input_tokens"] == 10
    assert Decimal(result.query_embedding["estimated_cost_usd"]) == Decimal(".0000002")
    assert calls[0]["input"] == ["Question about gateway"]
    assert len(calls) == 1
    with Session(engine) as session:
        run = session.get(LLMRun, result.query_embedding["run_ids"][0])
        assert run.operation == "embed_queries"
        for hit in result.results:
            document = session.get(Document, hit.document_id)
            assert hit.content in document.raw_text
            assert hit.start_line <= hit.end_line


@pytest.mark.parametrize(
    "change,field",
    [
        (lambda session, row: session.delete(row), "missing_vectors"),
        (lambda session, row: setattr(row, "input_hash", "outdated"), "stale_vectors"),
        (lambda session, row: setattr(row, "vector", [0.0] * DIMENSIONS), "invalid_vectors"),
        (lambda session, row: setattr(row, "vector", [1.0]), "invalid_vectors"),
    ],
)
def test_incomplete_or_invalid_vectors_reject_before_api(corpus, clients, change, field):
    engine, brand, _ = corpus
    with Session(engine) as session:
        change(session, session.scalar(select(ChunkEmbedding)))
        session.commit()
    status = vector_snapshot(engine, brand).status
    assert status.state == "not_ready" and getattr(status, field) == 1
    client, calls = clients()
    with pytest.raises(RetrievalError) as error:
        semantic_search(engine, brand, "question", client=client)
    assert error.value.status == 409 and error.value.code == "embeddings_not_ready"
    assert not calls


def test_brand_isolation_and_no_eligible_chunks(corpus, clients):
    engine, _, other = corpus
    client, calls = clients()
    with pytest.raises(RetrievalError):
        semantic_search(engine, other, "question", client=client)
    with pytest.raises(RetrievalError) as error:
        semantic_search(engine, "unknown", "question", client=client)
    assert error.value.status == 404 and not calls


@pytest.mark.parametrize(
    "query,limit", [("", 10), ("  ", 10), ("x" * 513, 10), ("question", 0), ("question", 51)]
)
def test_invalid_queries_never_call_provider(corpus, clients, query, limit):
    client, calls = clients()
    with pytest.raises(RetrievalError):
        semantic_search(corpus[0], corpus[1], query, limit, client=client)
    assert not calls


def test_source_change_during_request_rejects_result(corpus, clients):
    engine, brand, _ = corpus

    def handler(request, body):
        with Session(engine) as session:
            document = session.scalar(select(Document))
            document.title = "Changed during request"
            session.commit()
        return httpx.Response(
            200, json={"model": MODEL, "data": [{"index": 0, "embedding": vector()}]}
        )

    client, calls = clients(handler)
    with pytest.raises(RetrievalError) as error:
        semantic_search(engine, brand, "question", client=client)
    assert error.value.code == "corpus_changed" and len(calls) == 1


def test_vector_replacement_during_request_rejects_result(corpus, clients):
    engine, brand, _ = corpus

    def handler(request, body):
        with Session(engine) as session:
            row = session.scalar(select(ChunkEmbedding))
            row.vector = vector(2)
            session.commit()
        return httpx.Response(
            200, json={"model": MODEL, "data": [{"index": 0, "embedding": vector()}]}
        )

    client, _ = clients(handler)
    with pytest.raises(RetrievalError, match="changed during retrieval"):
        semantic_search(engine, brand, "question", client=client)


def test_missing_key_and_provider_failure_have_actionable_errors(corpus, clients):
    engine, brand, _ = corpus
    client, calls = clients(key=None)
    with pytest.raises(RetrievalError, match="OPENAI_API_KEY") as error:
        semantic_search(engine, brand, "question", client=client)
    assert error.value.status == 503 and not calls

    def handler(request, body):
        return httpx.Response(429, json={"error": {"message": "Rate limit", "type": "rate_limit"}})

    client, calls = clients(handler)
    with pytest.raises(RetrievalError, match="billing may already") as error:
        semantic_search(engine, brand, "question", client=client)
    assert error.value.status == 502 and len(calls) == 1


def test_unknown_query_cost_is_not_invented(corpus, clients):
    def handler(request, body):
        return httpx.Response(
            200, json={"model": MODEL, "data": [{"index": 0, "embedding": vector()}]}
        )

    client, _ = clients(handler)
    result = semantic_search(corpus[0], corpus[1], "question", client=client)
    assert result.query_embedding["input_tokens"] is None
    assert result.query_embedding["estimated_cost_usd"] is None
    assert result.query_embedding["cost_status"] == "unknown"


def test_evaluation_same_evidence_metrics_and_common_corpus(corpus, clients):
    engine, brand, _ = corpus
    client, calls = clients()
    with Session(engine) as session:
        semantic = evaluate_suite(session, brand, suite(), method="semantic", client=client)
    assert len(calls) == 1 and len(calls[0]["input"]) == 3
    assert semantic["summary"]["coverage_at_10"] == 1
    assert semantic["summary"]["mean_reciprocal_rank_at_10"] == 1
    assert semantic["summary"]["negative_zero_results"] == 0
    assert semantic["categories"]["direct"]["coverage_at_10"] == 1
    assert semantic["query_embedding"]["run_ids"]
    build_index(engine, brand)
    with Session(engine) as session:
        keyword = evaluate_suite(session, brand, suite())
        assert search_chunks(session, brand, "orchid").method == "keyword"
    assert keyword["corpus_fingerprint"] == semantic["corpus_fingerprint"]
    assert keyword["suite_hash"] == semantic["suite_hash"]
    assert keyword["query_embedding"] is None
    assert keyword["summary"]["negative_zero_results"] == 1


def test_invalid_gold_and_queries_fail_before_any_embedding_request(corpus, clients):
    client, calls = clients()
    invalid = suite()
    invalid.cases[0].expected_groups[0].alternatives[0].quotation = "Missing quotation"
    with (
        Session(corpus[0]) as session,
        pytest.raises(RetrievalError, match="Invalid evaluation references"),
    ):
        evaluate_suite(session, corpus[1], invalid, method="semantic", client=client)
    invalid = suite()
    invalid.cases[0].query = ""
    with Session(corpus[0]) as session, pytest.raises(RetrievalError):
        evaluate_suite(session, corpus[1], invalid, method="semantic", client=client)
    assert not calls


def test_query_batches_obey_limits_without_cache(corpus, clients):
    client, calls = clients()
    queries = ["短" * 512] * 34
    vectors, usage = query_vectors(corpus[0], corpus[1], queries, client=client)
    assert len(vectors) == 34
    assert len(calls) == len(usage["run_ids"])
    assert all(
        len(call["input"]) <= 32 and sum(len(q.encode()) for q in call["input"]) <= 32000
        for call in calls
    )
    semantic_search(corpus[0], corpus[1], "repeat", client=client)
    before = len(calls)
    semantic_search(corpus[0], corpus[1], "repeat", client=client)
    assert len(calls) == before + 1


def test_api_routing_status_legacy_keyword_and_validation(corpus, monkeypatch):
    engine, brand, _ = corpus
    settings = Settings(_env_file=None)
    with Session(engine) as session:
        assert api.embedding_status(brand, session).current_vectors == 3
        build_index(engine, brand)
        assert api.search(brand, session, "orchid").method == "keyword"
        assert (
            api.search_post(brand, api.SearchRequest(query="orchid"), session, settings).method
            == "keyword"
        )
        monkeypatch.setattr(
            api, "semantic_search", lambda *_args, **_kwargs: {"method": "semantic"}
        )
        assert (
            api.search_post(
                brand, api.SearchRequest(query="orchid", method="semantic"), session, settings
            )["method"]
            == "semantic"
        )
    for body in [
        {"query": "x", "method": "bad"},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": True},
    ]:
        with pytest.raises(ValidationError):
            api.SearchRequest.model_validate(body)
    with Session(engine) as session:
        session.execute(delete(ChunkEmbedding))
        session.commit()
        with pytest.raises(HTTPException) as error:
            api.run(lambda: semantic_search(engine, brand, "question"))
        assert error.value.status_code == 409


def test_cli_semantic_search_uses_new_method(corpus, clients, monkeypatch, capsys):
    from app.brand import retrieval, semantic

    engine, brand, _ = corpus
    client, calls = clients()
    original = semantic.semantic_search

    def mocked(*args, **kwargs):
        kwargs["client"] = client
        return original(*args, **kwargs)

    monkeypatch.setattr(semantic, "semantic_search", mocked)
    monkeypatch.setattr(
        retrieval,
        "get_settings",
        lambda: Settings(_env_file=None, magi_database_url=str(engine.url)),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["retrieval", "search", "--brand-id", brand, "--method", "semantic", "--query", "orchid"],
    )
    assert retrieval.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["method"] == "semantic" and result["results"][0]["score_type"] == "cosine"
    assert len(calls) == 1


def test_cli_semantic_evaluation_exports_report(corpus, clients, monkeypatch, capsys, tmp_path):
    from app.brand import retrieval, semantic

    engine, brand, _ = corpus
    client, calls = clients()
    original = semantic.query_vectors

    def mocked(*args, **kwargs):
        kwargs["client"] = client
        return original(*args, **kwargs)

    monkeypatch.setattr(semantic, "query_vectors", mocked)
    monkeypatch.setattr(
        retrieval,
        "get_settings",
        lambda: Settings(_env_file=None, magi_database_url=str(engine.url)),
    )
    cases, output = tmp_path / "suite.json", tmp_path / "result.json"
    cases.write_text(suite().model_dump_json())
    monkeypatch.setattr(
        "sys.argv",
        [
            "retrieval",
            "evaluate",
            "--brand-id",
            brand,
            "--method",
            "semantic",
            "--cases",
            str(cases),
            "--output",
            str(output),
        ],
    )
    assert retrieval.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(output.read_text()) == result
    assert result["method"] == "semantic" and result["summary"]["coverage_at_10"] == 1
    assert len(calls) == 1
