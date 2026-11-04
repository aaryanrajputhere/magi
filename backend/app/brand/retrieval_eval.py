"""Human-authored evidence groups evaluated without generated answers."""

import hashlib
import re
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand.retrieval import (
    INDEX_VERSION,
    RetrievalError,
    corpus_rows,
    evidence_fingerprint,
    index_status,
    search_chunks,
)
from app.config import Settings
from app.models import Document


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_path: str
    quotation: str = Field(min_length=1)


class EvidenceGroup(BaseModel):
    id: str
    alternatives: list[EvidenceReference] = Field(min_length=1)


class EvaluationCase(BaseModel):
    id: str
    category: Literal["direct", "paraphrase", "multi", "negative"]
    query: str
    expected_groups: list[EvidenceGroup]

    @model_validator(mode="after")
    def check_groups(self):
        if (self.category == "negative") != (len(self.expected_groups) == 0):
            raise ValueError("Only negative cases have no evidence groups")
        if self.category == "multi" and len(self.expected_groups) < 2:
            raise ValueError("Multi-evidence cases require at least two groups")
        if len({g.id for g in self.expected_groups}) != len(self.expected_groups):
            raise ValueError("Evidence group IDs must be unique per case")
        return self


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    name: str
    description: str
    cases: list[EvaluationCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        return self


def load_suite(path: Path) -> EvaluationSuite:
    try:
        return EvaluationSuite.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RetrievalError(
            f"Evaluation suite is unavailable or invalid: {path.name}", "invalid_suite"
        ) from exc


def registered_suite(directory: Path, suite_id: str) -> EvaluationSuite:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", suite_id):
        raise RetrievalError("Invalid evaluation suite ID", "invalid_suite")
    path = directory / f"{suite_id}.json"
    if path.is_symlink():
        raise RetrievalError("Invalid evaluation suite path", "invalid_suite")
    suite = load_suite(path)
    if suite.id != suite_id:
        raise RetrievalError("Suite ID does not match registered filename", "invalid_suite")
    return suite


def resolve_groups(session: Session, brand_id: str, suite: EvaluationSuite):
    rows, _ = corpus_rows(session, brand_id)
    documents = {
        doc.relative_path: doc
        for doc in session.scalars(select(Document).where(Document.brand_id == brand_id))
    }
    resolved = {}
    problems = []
    for case in suite.cases:
        groups = []
        for group in case.expected_groups:
            identifiers = set()
            for reference in group.alternatives:
                doc = documents.get(reference.document_path)
                start = doc.raw_text.find(reference.quotation) if doc else -1
                if start < 0 or doc.raw_text.find(reference.quotation, start + 1) >= 0:
                    problems.append(
                        f"{case.id}/{group.id}: absent or ambiguous source quotation in {reference.document_path}"
                    )
                    continue
                stop = start + len(reference.quotation)
                matching = {
                    chunk.id
                    for chunk, _ in rows
                    if chunk.document_id == doc.id
                    and chunk.start_offset <= start
                    and chunk.end_offset >= stop
                }
                if not matching:
                    problems.append(
                        f"{case.id}/{group.id}: no eligible chunk contains the complete evidence quotation"
                    )
                identifiers.update(matching)
            groups.append((group.id, identifiers))
        resolved[case.id] = groups
    if problems:
        raise RetrievalError(
            "Invalid evaluation references: " + "; ".join(problems), "invalid_references"
        )
    return resolved


def evaluate_suite(
    session: Session,
    brand_id: str,
    suite: EvaluationSuite,
    *,
    method: str = "keyword",
    settings: Settings | None = None,
    client=None,
) -> dict:
    started = perf_counter()
    if method not in {"keyword", "semantic", "hybrid"}:
        raise RetrievalError("Unknown retrieval method")
    engine = session.get_bind()
    common_fingerprint = evidence_fingerprint(corpus_rows(session, brand_id)[0])
    query_usage = None
    vector_state = None
    semantic_responses = []
    hybrid_state = None
    keyword_candidates = []
    if method == "keyword":
        initial = index_status(session, brand_id)
        if initial.state != "ready":
            raise RetrievalError(
                "Prepare the keyword index before evaluating", "index_not_ready", 409
            )
    else:
        from app.brand.semantic import (
            check_unchanged,
            query_vectors,
            ranked,
            require_ready,
            vector_snapshot,
        )

        if method == "hybrid":
            from app.brand.hybrid import CANDIDATE_DEPTH, check_hybrid, fuse, prepare_candidates

            hybrid_state, keyword_candidates = prepare_candidates(
                engine, brand_id, [case.query for case in suite.cases]
            )
            vector_state = hybrid_state.vectors
        else:
            vector_state = vector_snapshot(engine, brand_id)
        require_ready(vector_state)
    resolved = resolve_groups(session, brand_id, suite)
    if method != "keyword":
        # Materialize evidence first and release the caller's read transaction before I/O.
        if (
            evidence_fingerprint(corpus_rows(session, brand_id)[0]) != common_fingerprint
            or vector_state.status.corpus_fingerprint != common_fingerprint
        ):
            raise RetrievalError(
                "Corpus changed before query embedding. Refresh readiness and rerun.",
                "corpus_changed",
                409,
            )
        session.rollback()
        if hybrid_state:
            check_hybrid(engine, brand_id, hybrid_state)
        vectors, query_usage = query_vectors(
            engine, brand_id, [case.query for case in suite.cases], settings=settings, client=client
        )
        semantic_responses = [
            ranked(vector_state, case.query, vector, CANDIDATE_DEPTH if hybrid_state else 10)
            for case, vector in zip(suite.cases, vectors, strict=True)
        ]
        if hybrid_state:
            semantic_responses = [
                fuse(keyword, semantic)
                for keyword, semantic in zip(keyword_candidates, semantic_responses, strict=True)
            ]
            check_hybrid(engine, brand_id, hybrid_state)
        else:
            check_unchanged(engine, brand_id, vector_state)
    cases = []
    totals = {5: 0, 10: 0}
    all_found = {5: 0, 10: 0}
    expected_total = 0
    reciprocal = []
    negative_zero = 0
    for case_index, case in enumerate(suite.cases):
        response = (
            semantic_responses[case_index]
            if method != "keyword"
            else search_chunks(session, brand_id, case.query, 10)
        )
        if method == "keyword" and response.fingerprint != initial.fingerprint:
            raise RetrievalError(
                "Corpus changed during evaluation. Rebuild the index and rerun.",
                "index_not_ready",
                409,
            )
        groups = resolved[case.id]
        expected_total += len(groups)
        found = {}
        missed = {}
        for k in (5, 10):
            top = {hit.chunk_id for hit in response.results[:k]}
            found[k] = [identifier for identifier, ids in groups if ids & top]
            missed[k] = [identifier for identifier, ids in groups if not ids & top]
            totals[k] += len(found[k])
            if groups and not missed[k]:
                all_found[k] += 1
        relevant = set().union(*(ids for _, ids in groups)) if groups else set()
        first = next((hit.rank for hit in response.results if hit.chunk_id in relevant), None)
        if groups:
            reciprocal.append(1 / first if first else 0)
        else:
            negative_zero += not response.results
        cases.append(
            {
                "id": case.id,
                "category": case.category,
                "query": case.query,
                "expected_groups": [group.model_dump() for group in case.expected_groups],
                "coverage_at_5": len(found[5]) / len(groups) if groups else None,
                "coverage_at_10": len(found[10]) / len(groups) if groups else None,
                "all_evidence_at_5": not missed[5] if groups else None,
                "all_evidence_at_10": not missed[10] if groups else None,
                "missing_at_5": missed[5],
                "missing_at_10": missed[10],
                "first_relevant_rank": first,
                **response.model_dump(
                    exclude={"query", "fingerprint", "eligible_chunks", "excluded_chunks"}
                ),
            }
        )
    if hybrid_state:
        check_hybrid(engine, brand_id, hybrid_state)
    elif method != "keyword":
        check_unchanged(engine, brand_id, vector_state)
    elif index_status(session, brand_id).fingerprint != initial.fingerprint:
        raise RetrievalError("Corpus changed during evaluation", "index_not_ready", 409)
    if evidence_fingerprint(corpus_rows(session, brand_id)[0]) != common_fingerprint:
        raise RetrievalError("Corpus changed during evaluation", "corpus_changed", 409)
    categories = {}
    for category in {case["category"] for case in cases}:
        grouped = [
            case for case in cases if case["category"] == category and case["expected_groups"]
        ]
        total = sum(len(case["expected_groups"]) for case in grouped)
        categories[category] = {
            "cases": sum(case["category"] == category for case in cases),
            "coverage_at_5": sum(
                len(case["expected_groups"]) - len(case["missing_at_5"]) for case in grouped
            )
            / total
            if total
            else None,
            "coverage_at_10": sum(
                len(case["expected_groups"]) - len(case["missing_at_10"]) for case in grouped
            )
            / total
            if total
            else None,
        }
    return {
        "suite_id": suite.id,
        "suite_name": suite.name,
        "suite_hash": hashlib.sha256(suite.model_dump_json().encode()).hexdigest(),
        "brand_id": brand_id,
        "method": method,
        "fingerprint": vector_state.status.corpus_fingerprint
        if method != "keyword"
        else initial.fingerprint,
        "corpus_fingerprint": common_fingerprint,
        "vector_fingerprint": vector_state.status.vector_fingerprint if vector_state else None,
        "configuration": semantic_responses[0].configuration
        if semantic_responses
        else {"algorithm": INDEX_VERSION},
        "query_embedding": query_usage,
        "categories": categories,
        "corpus_scope": "stored_snapshot",
        "eligible_chunks": vector_state.status.eligible_chunks
        if vector_state
        else initial.eligible_chunks,
        "excluded_chunks": vector_state.status.excluded_chunks
        if vector_state
        else initial.excluded_chunks,
        "summary": {
            "cases": len(cases),
            "positive_cases": len(reciprocal),
            "evidence_groups": expected_total,
            "coverage_at_5": totals[5] / expected_total if expected_total else None,
            "coverage_at_10": totals[10] / expected_total if expected_total else None,
            "all_evidence_at_5": all_found[5],
            "all_evidence_at_10": all_found[10],
            "mean_reciprocal_rank_at_10": sum(reciprocal) / len(reciprocal) if reciprocal else None,
            "negative_cases": len(cases) - len(reciprocal),
            "negative_zero_results": negative_zero,
            "mean_query_latency_ms": sum(c["latency_ms"] for c in cases) / len(cases),
            "duration_ms": round((perf_counter() - started) * 1000, 3),
        },
        "cases": cases,
    }
