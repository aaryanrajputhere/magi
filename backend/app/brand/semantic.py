"""Exact local cosine retrieval; only explicit query embedding calls use OpenAI."""

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.brand.embeddings import (
    DIMENSIONS,
    INPUT_VERSION,
    MODEL,
    ChunkInput,
    embedding_text,
    stored,
)
from app.brand.retrieval import (
    RetrievalError,
    SearchHit,
    SearchResponse,
    corpus_rows,
    evidence_fingerprint,
)
from app.config import Settings
from app.llm.client import ModelRunError, RunContext, RunLoggingError, TrackedOpenAI
from app.llm.embeddings import EmbeddingRequest
from app.models import LLMRun

CONFIGURATION = {
    "algorithm": "exact-cosine-v1",
    "model": MODEL,
    "dimensions": DIMENSIONS,
    "document_input_version": INPUT_VERSION,
    "query_input_version": "question-only-v1",
    "similarity_threshold": None,
}


class VectorStatus(BaseModel):
    state: str
    eligible_chunks: int
    excluded_chunks: int
    current_vectors: int
    missing_vectors: int
    stale_vectors: int
    invalid_vectors: int
    corpus_fingerprint: str
    vector_fingerprint: str
    configuration: dict = CONFIGURATION


@dataclass
class VectorSnapshot:
    status: VectorStatus
    hits: list[SearchHit]
    vectors: list[list[float]]


def vector_snapshot(engine: Engine, brand_id: str) -> VectorSnapshot:
    with Session(engine) as session:
        rows, excluded = corpus_rows(session, brand_id)
        embeddings = stored(session, brand_id)
        missing = stale = invalid = 0
        hits, vectors = [], []
        digest = hashlib.sha256(json.dumps(CONFIGURATION, sort_keys=True).encode())
        for chunk, doc in rows:
            value = embedding_text(doc, chunk)
            item = ChunkInput(
                chunk.id,
                doc.id,
                chunk.source_hash,
                chunk.chunker_version,
                hashlib.sha256(value.encode()).hexdigest(),
                value,
                0,
            )
            row = embeddings.get(chunk.id)
            if row is None:
                missing += 1
                continue
            if (
                row.source_hash != item.source_hash
                or row.chunker_version != item.chunker_version
                or row.input_hash != item.input_hash
            ):
                stale += 1
                continue
            if not item.matches(row):
                invalid += 1
                continue
            digest.update(
                json.dumps([chunk.id, row.input_hash, row.vector], separators=(",", ":")).encode()
            )
            norm = math.hypot(*row.vector)
            vectors.append([number / norm for number in row.vector])
            hits.append(
                SearchHit(
                    rank=0,
                    score=0,
                    score_type="cosine",
                    chunk_id=chunk.id,
                    document_id=doc.id,
                    title=doc.title,
                    relative_path=doc.relative_path,
                    heading_path=chunk.heading_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                )
            )
        status = VectorStatus(
            state="ready" if rows and not (missing or stale or invalid) else "not_ready",
            eligible_chunks=len(rows),
            excluded_chunks=excluded,
            current_vectors=len(vectors),
            missing_vectors=missing,
            stale_vectors=stale,
            invalid_vectors=invalid,
            corpus_fingerprint=evidence_fingerprint(rows),
            vector_fingerprint=digest.hexdigest(),
        )
        return VectorSnapshot(status, hits, vectors)


def require_ready(snapshot: VectorSnapshot):
    if snapshot.status.state != "ready":
        raise RetrievalError(
            "Semantic search needs current, valid embeddings for all eligible chunks. Import and chunk documents, then run python -m app.brand.embeddings --brand-id <brand-id>.",
            "embeddings_not_ready",
            409,
        )


def validate_query(query: str, limit: int = 10):
    if not query.strip() or len(query) > 512:
        raise RetrievalError("Enter a nonblank question of at most 512 characters")
    if not 1 <= limit <= 50:
        raise RetrievalError("Result limit must be between 1 and 50")


def query_vectors(
    engine: Engine,
    brand_id: str,
    queries: list[str],
    *,
    settings: Settings | None = None,
    client: TrackedOpenAI | None = None,
):
    for query in queries:
        validate_query(query)
    owned = client is None
    client = client or TrackedOpenAI(engine, settings)
    vectors, run_ids = [], []
    started = perf_counter()
    try:
        # UTF-8 byte length is a conservative upper bound on token count.
        batches, batch, bound = [], [], 0
        for query in queries:
            size = len(query.encode("utf-8"))
            if batch and (len(batch) == 32 or bound + size > 32000):
                batches.append(batch)
                batch, bound = [], 0
            batch.append(query)
            bound += size
        if batch:
            batches.append(batch)
        for batch in batches:
            result = client.embed(
                EmbeddingRequest(
                    operation="embed_queries",
                    input=batch,
                    context=RunContext(
                        brand_id=brand_id,
                        source_references=[{"query_input_version": "question-only-v1"}],
                    ),
                )
            )
            vectors.extend(result.vectors)
            run_ids.append(result.run_id)
    except (ModelRunError, RunLoggingError) as exc:
        status = (
            503 if isinstance(exc, ModelRunError) and exc.status == "configuration_error" else 502
        )
        reason = (
            "Set OPENAI_API_KEY in the backend environment."
            if status == 503
            else "Inspect the saved run before retrying; provider billing may already have occurred."
        )
        raise RetrievalError(
            f"Query embedding failed (runs {', '.join([*run_ids, exc.run_id])}). {reason}",
            "query_embedding_failed",
            status,
        ) from exc
    finally:
        if owned:
            client.close()
    with Session(engine) as session:
        runs = [session.get(LLMRun, run_id) for run_id in run_ids]
        tokens = (
            sum(run.input_tokens for run in runs)
            if all(run and run.input_tokens is not None for run in runs)
            else None
        )
        cost = (
            sum((run.estimated_cost_usd for run in runs), Decimal(0))
            if all(run and run.estimated_cost_usd is not None for run in runs)
            else None
        )
    return vectors, {
        "run_ids": run_ids,
        "input_tokens": tokens,
        "estimated_cost_usd": str(cost) if cost is not None else None,
        "cost_status": "estimated" if cost is not None else "unknown",
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }


def ranked(
    snapshot: VectorSnapshot, query: str, vector: list[float], limit: int = 10
) -> SearchResponse:
    started = perf_counter()
    norm = math.hypot(*vector)
    normalized = [value / norm for value in vector]
    scores = [
        (math.fsum(a * b for a, b in zip(normalized, stored_vector, strict=True)), index)
        for index, stored_vector in enumerate(snapshot.vectors)
    ]
    # Snapshot ordering is path, chunk position, ID; Python's stable sort preserves ties.
    scores.sort(key=lambda pair: -pair[0])
    hits = [
        snapshot.hits[index].model_copy(update={"rank": rank, "score": max(-1.0, min(1.0, score))})
        for rank, (score, index) in enumerate(scores[:limit], 1)
    ]
    return SearchResponse(
        query=query,
        method="semantic",
        terms=[],
        fingerprint=snapshot.status.corpus_fingerprint,
        corpus_fingerprint=snapshot.status.corpus_fingerprint,
        vector_fingerprint=snapshot.status.vector_fingerprint,
        configuration=CONFIGURATION,
        eligible_chunks=snapshot.status.eligible_chunks,
        excluded_chunks=snapshot.status.excluded_chunks,
        results=hits,
        retrieved_characters=sum(len(hit.content) for hit in hits),
        latency_ms=round((perf_counter() - started) * 1000, 3),
    )


def check_unchanged(engine: Engine, brand_id: str, original: VectorSnapshot):
    current = vector_snapshot(engine, brand_id)
    if current.status.model_dump() != original.status.model_dump():
        raise RetrievalError(
            "Corpus or embeddings changed during retrieval. Refresh readiness and rerun.",
            "corpus_changed",
            409,
        )


def semantic_search(
    engine: Engine,
    brand_id: str,
    query: str,
    limit: int = 10,
    *,
    settings: Settings | None = None,
    client: TrackedOpenAI | None = None,
):
    started = perf_counter()
    validate_query(query, limit)
    snapshot = vector_snapshot(engine, brand_id)
    require_ready(snapshot)
    vectors, usage = query_vectors(engine, brand_id, [query], settings=settings, client=client)
    response = ranked(snapshot, query, vectors[0], limit)
    check_unchanged(engine, brand_id, snapshot)
    response.query_embedding = usage
    response.latency_ms = round((perf_counter() - started) * 1000, 3)
    return response
