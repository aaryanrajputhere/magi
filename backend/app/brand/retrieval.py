"""Keyword evidence retrieval and CLI. No models, embeddings or policy filtering."""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from pydantic import BaseModel, Field
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.brand.chunking import CHUNKER_VERSION
from app.config import get_settings
from app.db import create_database_engine, initialize_database
from app.models import Brand, Document, DocumentChunk, KeywordIndexState

INDEX_VERSION = "fts5-brand-unicode61-bm25-2-2-1-v2"


def index_table(brand_id: str) -> str:
    # Only a fixed prefix and hexadecimal digest enter SQL identifiers.
    return "chunk_keyword_index_" + hashlib.sha256(brand_id.encode()).hexdigest()


class RetrievalError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 422):
        self.code, self.status = code, status
        super().__init__(message)


class IndexStatus(BaseModel):
    brand_id: str
    state: str
    fingerprint: str
    indexed_chunks: int
    eligible_chunks: int
    excluded_chunks: int
    version: str = INDEX_VERSION
    corpus_scope: str = "stored_snapshot"


class SearchHit(BaseModel):
    rank: int
    score: float
    score_type: str = "bm25"
    keyword_rank: int | None = None
    keyword_score: float | None = None
    semantic_rank: int | None = None
    semantic_score: float | None = None
    chunk_id: str
    document_id: str
    title: str | None
    relative_path: str
    heading_path: list[str]
    start_line: int
    end_line: int
    content: str


class SearchResponse(BaseModel):
    query: str
    method: str = "keyword"
    corpus_fingerprint: str = ""
    vector_fingerprint: str | None = None
    configuration: dict = Field(default_factory=dict)
    query_embedding: dict | None = None
    terms: list[str]
    fingerprint: str
    eligible_chunks: int
    excluded_chunks: int
    latency_ms: float
    retrieved_characters: int
    results: list[SearchHit]


def require_brand(session: Session, brand_id: str):
    if session.get(Brand, brand_id) is None:
        raise RetrievalError("Brand not found", "brand_not_found", 404)


def corpus_rows(session: Session, brand_id: str):
    require_brand(session, brand_id)
    rows = session.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.brand_id == brand_id)
        .order_by(Document.relative_path, DocumentChunk.position, DocumentChunk.id)
        .execution_options(populate_existing=True)
    ).all()
    eligible = [
        (chunk, doc)
        for chunk, doc in rows
        if chunk.source_hash == doc.content_hash and chunk.chunker_version == CHUNKER_VERSION
    ]
    return eligible, len(rows) - len(eligible)


def corpus_fingerprint(rows, version: str = INDEX_VERSION) -> str:
    digest = hashlib.sha256(version.encode())
    for chunk, doc in rows:
        payload = [
            chunk.id,
            doc.id,
            doc.content_hash,
            doc.title,
            doc.relative_path,
            chunk.source_hash,
            chunk.chunker_version,
            chunk.heading_path,
            chunk.content,
            chunk.position,
            chunk.start_line,
            chunk.end_line,
        ]
        digest.update(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def evidence_fingerprint(rows) -> str:
    return corpus_fingerprint(rows, version="stored-evidence-v1")


def index_status(session: Session, brand_id: str) -> IndexStatus:
    fts_name = index_table(brand_id)
    rows, excluded = corpus_rows(session, brand_id)
    fingerprint = corpus_fingerprint(rows)
    state = session.get(KeywordIndexState, brand_id, populate_existing=True)
    table_exists = session.scalar(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"), {"name": fts_name}
    )
    actual_count = (
        session.scalar(
            text(f"SELECT count(*) FROM {fts_name} WHERE brand_id=:brand"), {"brand": brand_id}
        )
        if table_exists
        else None
    )
    return IndexStatus(
        brand_id=brand_id,
        state="missing"
        if state is None or not table_exists
        else "ready"
        if state.version == INDEX_VERSION
        and state.fingerprint == fingerprint
        and actual_count == state.chunk_count
        else "stale",
        fingerprint=fingerprint,
        indexed_chunks=state.chunk_count if state else 0,
        eligible_chunks=len(rows),
        excluded_chunks=excluded,
    )


def build_index(engine: Engine, brand_id: str) -> IndexStatus:
    fts_name = index_table(brand_id)
    try:
        with Session(engine) as session, session.begin():
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            rows, excluded = corpus_rows(session, brand_id)
            session.execute(
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts_name} USING fts5(chunk_id UNINDEXED, brand_id UNINDEXED, title, headings, content, tokenize='unicode61')"
                )
            )
            session.execute(
                text(f"DELETE FROM {fts_name} WHERE brand_id = :brand"), {"brand": brand_id}
            )
            if rows:
                session.execute(
                    text(
                        f"INSERT INTO {fts_name}(chunk_id,brand_id,title,headings,content) VALUES (:chunk,:brand,:title,:headings,:content)"
                    ),
                    [
                        {
                            "chunk": chunk.id,
                            "brand": brand_id,
                            "title": doc.title or "",
                            "headings": " / ".join(chunk.heading_path),
                            "content": chunk.content,
                        }
                        for chunk, doc in rows
                    ],
                )
            state = session.get(KeywordIndexState, brand_id)
            if state is None:
                state = KeywordIndexState(brand_id=brand_id)
                session.add(state)
            state.version = INDEX_VERSION
            state.fingerprint = corpus_fingerprint(rows)
            state.chunk_count = len(rows)
            state.built_at = datetime.now(UTC)
            result = IndexStatus(
                brand_id=brand_id,
                state="ready",
                fingerprint=state.fingerprint,
                indexed_chunks=len(rows),
                eligible_chunks=len(rows),
                excluded_chunks=excluded,
            )
        return result
    except SQLAlchemyError as exc:
        raise RetrievalError(
            "Could not prepare the keyword index. Check SQLite FTS5 support and database write access.",
            "index_build_failed",
            503,
        ) from exc


def query_terms(query: str) -> list[str]:
    if len(query) > 512:
        raise RetrievalError("Queries may contain at most 512 characters")
    terms = list(dict.fromkeys(word.lower() for word in re.findall(r"[^\W_]+", query, re.UNICODE)))
    if not terms:
        raise RetrievalError("Enter at least one searchable word or number")
    if len(terms) > 32:
        raise RetrievalError("Queries may contain at most 32 distinct terms")
    return terms


def search_chunks(session: Session, brand_id: str, query: str, limit: int = 10, *, document_ids: list[str] | None = None) -> SearchResponse:
    fts_name = index_table(brand_id)
    started = perf_counter()
    if not 1 <= limit <= 50:
        raise RetrievalError("Result limit must be between 1 and 50")
    terms = query_terms(query)
    status = index_status(session, brand_id)
    if status.state != "ready":
        raise RetrievalError(
            "Keyword index is missing or stale. Prepare the index before searching.",
            "index_not_ready",
            409,
        )
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    document_filter = ""
    document_params = {}
    if document_ids is not None:
        document_params = {f"doc_{i}": identifier for i, identifier in enumerate(document_ids)}
        document_filter = " AND d.id IN (" + (", ".join(":" + k for k in document_params) or "NULL") + ")"
    try:
        matches = session.execute(
            text(f"""
            SELECT f.chunk_id, bm25({fts_name},0,0,2,2,1) AS score
            FROM {fts_name} AS f
            JOIN document_chunks AS c ON c.id=f.chunk_id
            JOIN documents AS d ON d.id=c.document_id
            WHERE {fts_name} MATCH :match AND f.brand_id=:brand AND d.brand_id=:brand
              AND c.source_hash=d.content_hash AND c.chunker_version=:version
            {document_filter}
            ORDER BY score, d.relative_path, c.position, c.id LIMIT :limit
        """),
            {"match": match, "brand": brand_id, "version": CHUNKER_VERSION, "limit": limit, **document_params},
        ).all()
    except SQLAlchemyError as exc:
        raise RetrievalError(
            "Keyword index is unavailable. Prepare it again.", "index_not_ready", 409
        ) from exc
    hits = []
    for rank, (identifier, score) in enumerate(matches, 1):
        chunk = session.get(DocumentChunk, identifier)
        doc = session.get(Document, chunk.document_id)
        hits.append(
            SearchHit(
                rank=rank,
                score=score,
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
    return SearchResponse(
        query=query,
        corpus_fingerprint=evidence_fingerprint(corpus_rows(session, brand_id)[0]),
        configuration={"algorithm": INDEX_VERSION},
        terms=terms,
        fingerprint=status.fingerprint,
        eligible_chunks=status.eligible_chunks,
        excluded_chunks=status.excluded_chunks,
        latency_ms=round((perf_counter() - started) * 1000, 3),
        retrieved_characters=sum(len(hit.content) for hit in hits),
        results=hits,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "search", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--brand-id", required=True)
        if name != "index":
            command.add_argument(
                "--method", choices=["keyword", "semantic", "hybrid"], default="keyword"
            )
        if name == "search":
            command.add_argument("--query", required=True)
            command.add_argument("--limit", type=int, default=10)
        if name == "evaluate":
            command.add_argument("--cases", type=Path)
            command.add_argument("--suite-id", default="wickhold-baseline-v1")
            command.add_argument("--output", type=Path)
    args = parser.parse_args()
    engine = None
    try:
        settings = get_settings()
        engine = create_database_engine(settings.magi_database_url)
        initialize_database(engine)
        if args.command == "index":
            result = build_index(engine, args.brand_id).model_dump()
        else:
            with Session(engine) as session:
                if args.command == "search":
                    if args.method == "hybrid":
                        from app.brand.hybrid import hybrid_search

                        result = hybrid_search(
                            engine, args.brand_id, args.query, args.limit, settings=settings
                        ).model_dump()
                    elif args.method == "semantic":
                        from app.brand.semantic import semantic_search

                        result = semantic_search(
                            engine, args.brand_id, args.query, args.limit, settings=settings
                        ).model_dump()
                    else:
                        result = search_chunks(
                            session, args.brand_id, args.query, args.limit
                        ).model_dump()
                else:
                    from app.brand.retrieval_eval import evaluate_suite, load_suite

                    suite = load_suite(
                        args.cases
                        or settings.magi_retrieval_evaluation_dir / f"{args.suite_id}.json"
                    )
                    result = evaluate_suite(
                        session, args.brand_id, suite, method=args.method, settings=settings
                    )
        output = json.dumps(result, indent=2, ensure_ascii=False, default=str)
        if args.command == "evaluate" and args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        print(output)
        return 0
    except (ValueError, SQLAlchemyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
