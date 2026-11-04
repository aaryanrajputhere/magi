"""Prepare chunk vectors. Explicit CLI execution is the only billable entry point."""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import Engine, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.brand.chunking import CHUNKER_VERSION
from app.brand.retrieval import corpus_rows
from app.config import get_settings
from app.db import create_database_engine, initialize_database
from app.llm.client import ModelRunError, RunContext, RunLoggingError, TrackedOpenAI
from app.llm.costs import EMBEDDING_SMALL
from app.llm.embeddings import EmbeddingRequest, validate_vectors
from app.models import ChunkEmbedding, Document, DocumentChunk, LLMRun

MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
INPUT_VERSION = "title-headings-content-v1"
MAX_INPUT_TOKENS = 8191
MAX_BATCH_INPUTS = 32
MAX_BATCH_TOKENS = 32000


@lru_cache(maxsize=1)
def tokenizer():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        raise ValueError(
            "Token counter unavailable. Install backend dependencies and allow tiktoken to download its cl100k_base encoding once (or provide a populated TIKTOKEN_CACHE_DIR)."
        ) from exc


def count_tokens(value: str) -> int:
    return len(tokenizer().encode(value, disallowed_special=()))


def embedding_text(document: Document, chunk: DocumentChunk) -> str:
    return f"Title: {document.title or ''}\nHeadings: {' / '.join(chunk.heading_path)}\n\n{chunk.content}"


@dataclass(frozen=True)
class ChunkInput:
    chunk_id: str
    document_id: str
    source_hash: str
    chunker_version: str
    input_hash: str
    text: str
    tokens: int

    def reference(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source_hash": self.source_hash,
            "chunker_version": self.chunker_version,
            "input_hash": self.input_hash,
            "input_version": INPUT_VERSION,
        }

    def matches(self, row: ChunkEmbedding) -> bool:
        if not (
            row.model == MODEL
            and row.dimensions == DIMENSIONS
            and row.input_version == INPUT_VERSION
            and row.source_hash == self.source_hash
            and row.chunker_version == self.chunker_version
            and row.input_hash == self.input_hash
        ):
            return False
        try:
            validate_vectors(
                {"model": MODEL, "data": [{"index": 0, "embedding": row.vector}]},
                MODEL,
                DIMENSIONS,
                1,
            )
            return True
        except (TypeError, ValueError, OverflowError):
            return False


def snapshot(session: Session, brand_id: str, token_counter):
    rows, excluded = corpus_rows(session, brand_id)
    inputs = []
    for chunk, doc in rows:
        value = embedding_text(doc, chunk)
        inputs.append(
            ChunkInput(
                chunk.id,
                doc.id,
                chunk.source_hash,
                chunk.chunker_version,
                hashlib.sha256(value.encode()).hexdigest(),
                value,
                token_counter(value),
            )
        )
    return inputs, excluded


def stored(session: Session, brand_id: str) -> dict[str, ChunkEmbedding]:
    # Dry-run also works before the additive embedding table has been created.
    if not inspect(session.get_bind()).has_table("chunk_embeddings"):
        return {}
    return {
        row.chunk_id: row
        for row in session.scalars(
            select(ChunkEmbedding)
            .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.brand_id == brand_id,
                ChunkEmbedding.model == MODEL,
                ChunkEmbedding.dimensions == DIMENSIONS,
                ChunkEmbedding.input_version == INPUT_VERSION,
            )
        )
    }


def recoverable(session: Session, brand_id: str, pending: list[ChunkInput]):
    """Reuse only validated, exact, source-matched successful logged responses."""
    wanted = {item.chunk_id: item for item in pending}
    recovered = {}
    if not wanted:
        return recovered
    runs = session.scalars(
        select(LLMRun)
        .where(
            LLMRun.operation == "embed_chunks",
            LLMRun.status == "completed",
            LLMRun.requested_model == MODEL,
            LLMRun.returned_model == MODEL,
            LLMRun.context["brand_id"].as_string() == brand_id,
        )
        .order_by(LLMRun.started_at, LLMRun.id)
    )
    for run in runs:
        body, refs = run.request_body, run.context.get("source_references")
        if (
            not isinstance(body, dict)
            or body.get("model") != MODEL
            or body.get("dimensions") != DIMENSIONS
            or body.get("encoding_format") != "float"
        ):
            continue
        texts = body.get("input")
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= MAX_BATCH_INPUTS
            or not isinstance(refs, list)
            or len(refs) != len(texts)
        ):
            continue
        try:
            vectors = validate_vectors(run.response_body, MODEL, DIMENSIONS, len(texts))
        except (TypeError, ValueError, OverflowError):
            continue
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            item = wanted.get(ref.get("chunk_id"))
            if item is not None and ref == item.reference() and texts[index] == item.text:
                recovered[item.chunk_id] = (vectors[index], run.id)
    return recovered


def save_batch(
    engine: Engine, brand_id: str, inputs: list[ChunkInput], vectors: list[list[float]], run_id: str
):
    with Session(engine) as session, session.begin():
        # A short write transaction prevents ingestion racing the final source check.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        checked = []
        for item, vector in zip(inputs, vectors, strict=True):
            chunk = session.get(DocumentChunk, item.chunk_id)
            doc = session.get(Document, item.document_id)
            if (
                chunk is None
                or doc is None
                or chunk.document_id != doc.id
                or doc.brand_id != brand_id
                or chunk.source_hash != doc.content_hash
                or chunk.source_hash != item.source_hash
                or chunk.chunker_version != CHUNKER_VERSION
                or chunk.chunker_version != item.chunker_version
                or hashlib.sha256(embedding_text(doc, chunk).encode()).hexdigest()
                != item.input_hash
            ):
                raise ValueError(
                    f"Source changed during embedding; result not saved for chunk {item.chunk_id}. Rebuild chunks and rerun."
                )
            row = session.scalar(
                select(ChunkEmbedding).where(
                    ChunkEmbedding.chunk_id == item.chunk_id,
                    ChunkEmbedding.model == MODEL,
                    ChunkEmbedding.dimensions == DIMENSIONS,
                    ChunkEmbedding.input_version == INPUT_VERSION,
                )
            )
            checked.append((item, vector, row))
        for item, vector, row in checked:
            if row is None:
                row = ChunkEmbedding(
                    chunk_id=item.chunk_id,
                    model=MODEL,
                    dimensions=DIMENSIONS,
                    input_version=INPUT_VERSION,
                )
                session.add(row)
            row.source_hash, row.chunker_version = item.source_hash, item.chunker_version
            row.input_hash, row.vector, row.run_id = item.input_hash, vector, run_id


def batches(inputs: list[ChunkInput]):
    batch, tokens = [], 0
    for item in inputs:
        if batch and (len(batch) == MAX_BATCH_INPUTS or tokens + item.tokens > MAX_BATCH_TOKENS):
            yield batch
            batch, tokens = [], 0
        batch.append(item)
        tokens += item.tokens
    if batch:
        yield batch


def prepare_embeddings(
    engine: Engine,
    brand_id: str,
    *,
    dry_run: bool = False,
    client: TrackedOpenAI | None = None,
    token_counter=None,
) -> dict:
    token_counter = token_counter or count_tokens
    with Session(engine) as session:
        inputs, excluded = snapshot(session, brand_id, token_counter)
        existing = stored(session, brand_id)
        pending = [
            item
            for item in inputs
            if item.chunk_id not in existing or not item.matches(existing[item.chunk_id])
        ]
        stale = sum(item.chunk_id in existing for item in pending)
        recovery = recoverable(session, brand_id, pending)
    oversized = [item for item in pending if item.tokens > MAX_INPUT_TOKENS]
    to_send = [
        item
        for item in pending
        if item.chunk_id not in recovery and item.tokens <= MAX_INPUT_TOKENS
    ]
    pending_tokens = sum(item.tokens for item in to_send)
    report = {
        "brand_id": brand_id,
        "status": "dry_run" if dry_run else "success",
        "model": MODEL,
        "dimensions": DIMENSIONS,
        "input_version": INPUT_VERSION,
        "eligible": len(inputs),
        "reused": len(inputs) - len(pending),
        "stale_embeddings": stale,
        "stale_chunks_excluded": excluded,
        "recoverable": len(recovery),
        "recovered": 0,
        "generated": 0,
        "pending": len(pending),
        "pending_tokens": pending_tokens,
        "planned_batches": sum(1 for _ in batches(to_send)),
        "oversized": len(oversized),
        "estimated_pending_cost_usd": str(
            Decimal(pending_tokens) * EMBEDDING_SMALL.input_per_million / Decimal(1_000_000)
        ),
        "pricing_snapshot": EMBEDDING_SMALL.model_dump(mode="json"),
        "input_tokens": 0,
        "estimated_cost_usd": "0",
        "cost_status": "not_sent",
        "run_ids": [],
        "recovered_run_ids": [],
        "failed_chunk_ids": [item.chunk_id for item in oversized],
        "error": None,
    }
    if dry_run:
        return report
    if oversized:
        report.update(
            status="failed",
            error=f"{len(oversized)} embedding inputs exceed {MAX_INPUT_TOKENS} tokens. Rechunk these sources; no provider request was sent.",
        )
        return report
    owned = client is None
    active = []
    cost, usage_known, cost_known, any_sent = Decimal(0), True, True, False
    try:
        # Group replay by originating run so each recovered batch is transactional.
        for run_id in dict.fromkeys(run for _, run in recovery.values()):
            active = [
                item
                for item in pending
                if item.chunk_id in recovery and recovery[item.chunk_id][1] == run_id
            ]
            save_batch(
                engine, brand_id, active, [recovery[item.chunk_id][0] for item in active], run_id
            )
            report["recovered"] += len(active)
            report["pending"] -= len(active)
            report["recovered_run_ids"].append(run_id)
        for active in batches(to_send):
            if client is None:
                client = TrackedOpenAI(engine)
            result = client.embed(
                EmbeddingRequest(
                    input=[item.text for item in active],
                    context=RunContext(
                        brand_id=brand_id, source_references=[item.reference() for item in active]
                    ),
                )
            )
            report["run_ids"].append(result.run_id)
            save_batch(engine, brand_id, active, result.vectors, result.run_id)
            report["generated"] += len(active)
            report["pending"] -= len(active)
        # Verify completion against the current snapshot, including newly added chunks.
        with Session(engine) as session:
            latest, latest_excluded = snapshot(session, brand_id, token_counter)
            rows = stored(session, brand_id)
            remaining = [
                item.chunk_id
                for item in latest
                if item.chunk_id not in rows or not item.matches(rows[item.chunk_id])
            ]
        report["eligible"] = len(latest)
        report["pending"] = len(remaining)
        report["stale_chunks_excluded"] = latest_excluded
        if remaining:
            raise ValueError(
                "Corpus changed during preparation. Rerun to process outstanding chunks."
            )
    except (ModelRunError, RunLoggingError, ValueError, SQLAlchemyError) as exc:
        report.update(
            status="failed",
            error=str(exc)
            if not isinstance(exc, SQLAlchemyError)
            else "Could not persist embeddings. Check database write access; do not automatically repeat paid requests.",
            failed_chunk_ids=[item.chunk_id for item in active],
        )
        if (
            isinstance(exc, (ModelRunError, RunLoggingError))
            and exc.run_id not in report["run_ids"]
        ):
            report["run_ids"].append(exc.run_id)
    finally:
        if owned and client is not None:
            client.close()
    # Count only attempts made now. Recovered runs retain their original cost.
    try:
        with Session(engine) as session:
            for run_id in report["run_ids"]:
                run = session.get(LLMRun, run_id)
                if run is None:
                    usage_known = cost_known = False
                    continue
                if run.cost_status == "not_sent":
                    continue
                any_sent = True
                if run.input_tokens is None:
                    usage_known = False
                else:
                    report["input_tokens"] += run.input_tokens
                if run.estimated_cost_usd is None:
                    cost_known = False
                else:
                    cost += run.estimated_cost_usd
    except SQLAlchemyError:
        usage_known = cost_known = False
        report["status"] = "failed"
        report["error"] = (
            report["error"]
            or "Could not read run costs. Check database access and inspect the listed runs before retrying."
        )
    report["input_tokens"] = report["input_tokens"] if usage_known else None
    report["estimated_cost_usd"] = str(cost) if cost_known else None
    report["cost_status"] = (
        "estimated" if any_sent and cost_known else "unknown" if not cost_known else "not_sent"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    engine = None
    try:
        engine = create_database_engine(get_settings().magi_database_url)
        if not args.dry_run:
            initialize_database(engine)
        report = prepare_embeddings(engine, args.brand_id, dry_run=args.dry_run)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1 if report["status"] == "failed" else 0
    except (ValueError, SQLAlchemyError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc)
                    if isinstance(exc, ValueError)
                    else "Embedding storage unavailable. Import and chunk the brand first; check database access.",
                }
            )
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
