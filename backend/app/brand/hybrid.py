"""Equal-weight reciprocal rank fusion over keyword and semantic candidates."""

from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.brand.retrieval import (
    RetrievalError,
    SearchResponse,
    corpus_rows,
    evidence_fingerprint,
    index_status,
    query_terms,
    search_chunks,
)
from app.brand.semantic import (
    VectorSnapshot,
    check_unchanged,
    query_vectors,
    ranked,
    require_ready,
    validate_query,
    vector_snapshot,
)
from app.config import Settings
from app.llm.client import TrackedOpenAI

CANDIDATE_DEPTH = 50
RRF_K = 60


@dataclass
class HybridSnapshot:
    vectors: VectorSnapshot
    keyword_fingerprint: str


def hybrid_readiness(engine: Engine, brand_id: str):
    vectors = vector_snapshot(engine, brand_id)
    with Session(engine) as session:
        keyword = index_status(session, brand_id)
        common = evidence_fingerprint(corpus_rows(session, brand_id)[0])
    status = vectors.status.model_dump()
    status.update(
        keyword_state=keyword.state,
        keyword_fingerprint=keyword.fingerprint,
        state="ready"
        if keyword.state == "ready"
        and vectors.status.state == "ready"
        and common == vectors.status.corpus_fingerprint
        else "not_ready",
    )
    return status, HybridSnapshot(vectors, keyword.fingerprint)


def check_hybrid(engine: Engine, brand_id: str, snapshot: HybridSnapshot):
    check_unchanged(engine, brand_id, snapshot.vectors)
    with Session(engine) as session:
        status = index_status(session, brand_id)
    if status.state != "ready" or status.fingerprint != snapshot.keyword_fingerprint:
        raise RetrievalError(
            "Keyword index changed during hybrid retrieval. Refresh readiness and rerun.",
            "corpus_changed",
            409,
        )


def prepare_candidates(engine: Engine, brand_id: str, queries: list[str], limit: int = 10):
    for query in queries:
        validate_query(query, limit)
        query_terms(query)  # Validate keyword limits before any billable call.
    status, snapshot = hybrid_readiness(engine, brand_id)
    if status["state"] != "ready":
        raise RetrievalError(
            "Hybrid search needs a current keyword index and valid embeddings for the same corpus. Prepare the keyword index and run the embedding command, then refresh readiness.",
            "hybrid_not_ready",
            409,
        )
    require_ready(snapshot.vectors)
    with Session(engine) as session:
        candidates = [search_chunks(session, brand_id, query, CANDIDATE_DEPTH) for query in queries]
    if any(
        response.fingerprint != snapshot.keyword_fingerprint
        or response.corpus_fingerprint != snapshot.vectors.status.corpus_fingerprint
        for response in candidates
    ):
        raise RetrievalError(
            "Corpus changed before hybrid retrieval. Refresh readiness and rerun.",
            "corpus_changed",
            409,
        )
    return snapshot, candidates


def fuse(keyword: SearchResponse, semantic: SearchResponse, limit: int = 10) -> SearchResponse:
    started = perf_counter()
    validate_query(keyword.query, limit)
    if keyword.query != semantic.query:
        raise RetrievalError("Cannot fuse rankings for different questions")
    if keyword.corpus_fingerprint != semantic.corpus_fingerprint or not keyword.corpus_fingerprint:
        raise RetrievalError(
            "Cannot fuse rankings from different corpus snapshots", "corpus_changed", 409
        )
    combined = {}
    for name, response in (("keyword", keyword), ("semantic", semantic)):
        for hit in response.results[:CANDIDATE_DEPTH]:
            entry = combined.setdefault(
                hit.chunk_id, {"hit": hit, "keyword": None, "semantic": None}
            )
            previous = entry[name]
            if previous is None or hit.rank < previous.rank:
                entry[name] = hit
    scored = []
    for entry in combined.values():
        ranks = [hit.rank for name in ("keyword", "semantic") if (hit := entry[name]) is not None]
        score = sum(1 / (RRF_K + rank) for rank in ranks)
        scored.append((score, min(ranks), entry))
    scored.sort(
        key=lambda row: (
            -row[0],
            row[1],
            row[2]["hit"].relative_path,
            row[2]["hit"].start_line,
            row[2]["hit"].chunk_id,
        )
    )
    hits = []
    for rank, (score, _, entry) in enumerate(scored[:limit], 1):
        kw, sem = entry["keyword"], entry["semantic"]
        hits.append(
            entry["hit"].model_copy(
                update={
                    "rank": rank,
                    "score": score,
                    "score_type": "rrf",
                    "keyword_rank": kw.rank if kw else None,
                    "keyword_score": kw.score if kw else None,
                    "semantic_rank": sem.rank if sem else None,
                    "semantic_score": sem.score if sem else None,
                }
            )
        )
    configuration = {
        "algorithm": "equal-weight-rrf-v1",
        "rrf_k": RRF_K,
        "candidate_depth": CANDIDATE_DEPTH,
        "keyword": keyword.configuration,
        "semantic": semantic.configuration,
    }
    return SearchResponse(
        query=keyword.query,
        method="hybrid",
        terms=keyword.terms,
        fingerprint=semantic.corpus_fingerprint,
        corpus_fingerprint=semantic.corpus_fingerprint,
        vector_fingerprint=semantic.vector_fingerprint,
        configuration=configuration,
        eligible_chunks=semantic.eligible_chunks,
        excluded_chunks=semantic.excluded_chunks,
        query_embedding=semantic.query_embedding,
        results=hits,
        retrieved_characters=sum(len(hit.content) for hit in hits),
        latency_ms=round(
            keyword.latency_ms + semantic.latency_ms + (perf_counter() - started) * 1000, 3
        ),
    )


def hybrid_search(
    engine: Engine,
    brand_id: str,
    query: str,
    limit: int = 10,
    *,
    settings: Settings | None = None,
    client: TrackedOpenAI | None = None,
):
    started = perf_counter()
    snapshot, candidates = prepare_candidates(engine, brand_id, [query], limit)
    check_hybrid(engine, brand_id, snapshot)
    vectors, usage = query_vectors(engine, brand_id, [query], settings=settings, client=client)
    semantic = ranked(snapshot.vectors, query, vectors[0], CANDIDATE_DEPTH)
    semantic.query_embedding = usage
    response = fuse(candidates[0], semantic, limit)
    check_hybrid(engine, brand_id, snapshot)
    response.latency_ms = round((perf_counter() - started) * 1000, 3)
    return response
