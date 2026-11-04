import pytest
import test_semantic as semantic_fixtures
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.retrieval import SearchRequest, hybrid_status
from app.brand.hybrid import CANDIDATE_DEPTH, fuse, hybrid_readiness, hybrid_search
from app.brand.retrieval import RetrievalError, SearchHit, SearchResponse, build_index
from app.brand.retrieval_eval import evaluate_suite
from app.models import ChunkEmbedding, KeywordIndexState

corpus = semantic_fixtures.corpus
clients = semantic_fixtures.clients
suite = semantic_fixtures.suite


def response(ids, method="keyword"):
    return SearchResponse(
        query="question",
        method=method,
        terms=[],
        fingerprint="same",
        corpus_fingerprint="same",
        eligible_chunks=100,
        excluded_chunks=0,
        latency_ms=1,
        retrieved_characters=0,
        results=[
            SearchHit(
                rank=i,
                score=-i if method == "keyword" else 1 / i,
                chunk_id=identifier,
                document_id="doc",
                title="Title",
                relative_path=f"{identifier}.md",
                heading_path=[],
                start_line=1,
                end_line=2,
                content="evidence",
            )
            for i, identifier in enumerate(ids, 1)
        ],
    )


def test_rrf_math_component_details_and_deduplication():
    kw, sem = response(["a", "b"]), response(["b", "c"], "semantic")
    result = fuse(kw, sem)
    assert result.method == "hybrid"
    assert [hit.chunk_id for hit in result.results] == ["b", "a", "c"]
    hit = result.results[0]
    assert hit.score == pytest.approx(1 / 62 + 1 / 61)
    assert hit.keyword_rank == 2 and hit.semantic_rank == 1 and hit.score_type == "rrf"
    assert hit.keyword_score == -2 and hit.semantic_score == 1
    kw.results.append(kw.results[1].model_copy())
    assert fuse(kw, sem).results == result.results
    assert result.configuration["rrf_k"] == 60


def test_rankings_are_not_fused_across_snapshots():
    kw, sem = response(["a"]), response(["a"], "semantic")
    sem.corpus_fingerprint = "other"
    with pytest.raises(RetrievalError, match="different corpus"):
        fuse(kw, sem)


def test_ties_are_deterministic_and_limit_applies_after_fusion():
    kw, sem = response(["z"]), response(["a"], "semantic")
    assert fuse(kw, sem, 1).results[0].chunk_id == "a"
    kw = response([f"k{i}" for i in range(10)] + ["shared"])
    sem = response([f"s{i}" for i in range(10)] + ["shared"], "semantic")
    assert fuse(kw, sem, 1).results[0].chunk_id == "shared"
    assert len(fuse(kw, sem, 5).results) == 5


def test_candidate_depth_is_applied_before_fusion():
    kw = response([f"k{i}" for i in range(CANDIDATE_DEPTH)] + ["excluded"])
    sem = response(["excluded"], "semantic")
    hit = next(h for h in fuse(kw, sem, 50).results if h.chunk_id == "excluded")
    assert hit.keyword_rank is None and hit.score == pytest.approx(1 / 61)


def test_hybrid_requires_both_systems_before_api_calls(corpus, clients):
    engine, brand, _ = corpus
    client, calls = clients()
    with pytest.raises(RetrievalError) as error:
        hybrid_search(engine, brand, "orchid", client=client)
    assert error.value.code == "hybrid_not_ready" and not calls
    build_index(engine, brand)
    with Session(engine) as session:
        session.execute(delete(ChunkEmbedding))
        session.commit()
    status, _ = hybrid_readiness(engine, brand)
    assert status["keyword_state"] == "ready" and status["missing_vectors"] == 3
    with pytest.raises(RetrievalError):
        hybrid_search(engine, brand, "orchid", client=client)
    assert not calls


def test_one_query_embedding_and_source_metadata(corpus, clients):
    engine, brand, _ = corpus
    build_index(engine, brand)
    client, calls = clients()
    result = hybrid_search(engine, brand, "orchid", 2, client=client)
    assert result.method == "hybrid" and len(result.results) == 2
    assert result.results[0].relative_path == "brand/a.md"
    assert result.results[0].keyword_rank == result.results[0].semantic_rank == 1
    assert result.query_embedding["run_ids"] and len(calls) == 1
    assert calls[0]["input"] == ["orchid"]
    assert result.vector_fingerprint and result.corpus_fingerprint
    with Session(engine) as session:
        assert hybrid_status(brand, session)["state"] == "ready"
    assert SearchRequest(query="orchid", method="hybrid").method == "hybrid"


def test_keyword_limits_rejected_before_embedding(corpus, clients):
    engine, brand, _ = corpus
    build_index(engine, brand)
    client, calls = clients()
    with pytest.raises(RetrievalError, match="32 distinct"):
        hybrid_search(engine, brand, " ".join(f"w{i}" for i in range(33)), client=client)
    assert not calls


def test_index_manifest_change_during_query_rejects_results(corpus, clients):
    import httpx
    from test_semantic import vector

    from app.brand.embeddings import MODEL

    engine, brand, _ = corpus
    build_index(engine, brand)

    def handler(request, body):
        with Session(engine) as session:
            state = session.get(KeywordIndexState, brand)
            state.version = "outdated"
            session.commit()
        return httpx.Response(
            200, json={"model": MODEL, "data": [{"index": 0, "embedding": vector()}]}
        )

    client, calls = clients(handler)
    with pytest.raises(RetrievalError, match="index changed"):
        hybrid_search(engine, brand, "orchid", client=client)
    assert len(calls) == 1


def test_hybrid_evaluation_preserves_scoring_and_batches_once(corpus, clients):
    engine, brand, _ = corpus
    build_index(engine, brand)
    client, calls = clients()
    with Session(engine) as session:
        report = evaluate_suite(session, brand, suite(), method="hybrid", client=client)
    assert report["method"] == "hybrid"
    assert report["summary"]["coverage_at_10"] == 1
    assert report["summary"]["negative_zero_results"] == 0
    assert report["configuration"]["candidate_depth"] == 50
    assert len(calls) == 1 and len(calls[0]["input"]) == 3
    assert all(hit["score_type"] == "rrf" for case in report["cases"] for hit in case["results"])


def test_hybrid_evaluation_validates_all_queries_before_calls(corpus, clients):
    engine, brand, _ = corpus
    build_index(engine, brand)
    client, calls = clients()
    cases = suite()
    cases.cases[-1].query = " ".join(f"w{i}" for i in range(33))
    with Session(engine) as session, pytest.raises(RetrievalError):
        evaluate_suite(session, brand, cases, method="hybrid", client=client)
    assert not calls


def test_hybrid_brand_boundaries(corpus, clients):
    engine, brand, other = corpus
    build_index(engine, brand)
    client, calls = clients()
    with pytest.raises(RetrievalError):
        hybrid_search(engine, other, "orchid", client=client)
    assert not calls


def test_cli_hybrid_search(corpus, clients, monkeypatch, capsys):
    import json

    from app.brand import hybrid, retrieval
    from app.config import Settings

    engine, brand, _ = corpus
    build_index(engine, brand)
    client, calls = clients()
    original = hybrid.hybrid_search

    def mocked(*args, **kwargs):
        kwargs["client"] = client
        return original(*args, **kwargs)

    monkeypatch.setattr(hybrid, "hybrid_search", mocked)
    monkeypatch.setattr(
        retrieval,
        "get_settings",
        lambda: Settings(_env_file=None, magi_database_url=str(engine.url)),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["retrieval", "search", "--brand-id", brand, "--method", "hybrid", "--query", "orchid"],
    )
    assert retrieval.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["method"] == "hybrid" and result["results"][0]["score_type"] == "rrf"
    assert len(calls) == 1
