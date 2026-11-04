import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.api.retrieval import EvaluationRequest, evaluate, get_index, prepare_index, search, suites
from app.brand.chunking import build_chunks
from app.brand.discovery import discover_folder
from app.brand.ingestion import ingest_documents
from app.brand.retrieval import (
    RetrievalError,
    build_index,
    index_status,
    index_table,
    search_chunks,
)
from app.brand.retrieval_eval import EvaluationSuite, evaluate_suite, registered_suite
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.models import Brand, Document, DocumentChunk


@pytest.fixture
def corpus(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialize_database(engine)
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    (root / "brand/a.md").write_text("# Alpha\n\nOrchid gateway refuses unsafe calls.\n")
    (root / "brand/b.md").write_text("# Beta\n\nOrchid budgets require approval.\n")
    brand = ingest_documents(engine, discover_folder(root), "Example").brand_id
    build_chunks(engine, brand)
    with Session(engine) as session:
        other = Brand(name="Other", source_directory="/other")
        session.add(other)
        session.commit()
        other_id = other.id
    yield engine, brand, other_id
    engine.dispose()


def suite():
    def group(identifier, path, quotation):
        return {"id": identifier, "alternatives": [{"document_path": path, "quotation": quotation}]}

    return EvaluationSuite.model_validate(
        {
            "id": "test",
            "name": "Test",
            "description": "Test evidence",
            "cases": [
                {
                    "id": "one",
                    "category": "direct",
                    "query": "gateway",
                    "expected_groups": [
                        group("gateway", "brand/a.md", "Orchid gateway refuses unsafe calls.")
                    ],
                },
                {
                    "id": "two",
                    "category": "multi",
                    "query": "Orchid",
                    "expected_groups": [
                        group("gateway", "brand/a.md", "Orchid gateway refuses unsafe calls."),
                        group("budget", "brand/b.md", "Orchid budgets require approval."),
                    ],
                },
                {
                    "id": "negative",
                    "category": "negative",
                    "query": "absentzzq9384",
                    "expected_groups": [],
                },
            ],
        }
    )


def test_index_rebuild_and_brand_isolation(corpus):
    engine, brand, other = corpus
    with Session(engine) as session:
        assert index_status(session, brand).state == "missing"
        with pytest.raises(RetrievalError, match="missing or stale"):
            search_chunks(session, brand, "Orchid")
    first = build_index(engine, brand)
    second = build_index(engine, brand)
    assert first.fingerprint == second.fingerprint
    assert first.indexed_chunks == 2
    build_index(engine, other)
    with Session(engine) as session:
        assert len(search_chunks(session, brand, "Orchid").results) == 2
        assert search_chunks(session, other, "Orchid").results == []
        assert session.scalar(text(f"SELECT count(*) FROM {index_table(brand)}")) == 2
        with pytest.raises(RetrievalError) as error:
            search_chunks(session, "unknown", "Orchid")
        assert error.value.status == 404


def test_stale_source_and_chunker_exclusion(corpus):
    engine, brand, _ = corpus
    build_index(engine, brand)
    with Session(engine) as session:
        chunk = session.scalar(select(DocumentChunk))
        chunk.source_hash = "obsolete"
        session.commit()
        assert index_status(session, brand).state == "stale"
        with pytest.raises(RetrievalError):
            search_chunks(session, brand, "Orchid")
    assert build_index(engine, brand).excluded_chunks == 1
    with Session(engine) as session:
        assert len(search_chunks(session, brand, "Orchid").results) == 1
        chunk = session.scalar(select(DocumentChunk).where(DocumentChunk.source_hash != "obsolete"))
        chunk.chunker_version = "old"
        session.commit()
    assert build_index(engine, brand).eligible_chunks == 0
    with Session(engine) as session:
        assert search_chunks(session, brand, "Orchid").results == []


def test_metadata_changes_make_index_stale(corpus):
    engine, brand, _ = corpus
    build_index(engine, brand)
    with Session(engine) as session:
        doc = session.scalar(select(Document))
        doc.title = "Changed heading"
        session.commit()
        assert index_status(session, brand).state == "stale"


def test_search_validation_literal_operators_and_source_spans(corpus):
    engine, brand, _ = corpus
    build_index(engine, brand)
    with Session(engine) as session:
        for query in ["", "---", "a" * 513, " ".join(f"word{i}" for i in range(33))]:
            with pytest.raises(RetrievalError):
                search_chunks(session, brand, query)
        for limit in [0, 51]:
            with pytest.raises(RetrievalError):
                search_chunks(session, brand, "Orchid", limit)
        response = search_chunks(session, brand, '"Orchid" OR (gateway*) NOT : {x}')
        assert response.results
        assert response.terms == ["orchid", "or", "gateway", "not", "x"]
        for hit in response.results:
            doc = session.get(Document, hit.document_id)
            assert hit.content in doc.raw_text
            assert hit.start_line <= hit.end_line
        assert (
            search_chunks(session, brand, "Orchid").results
            == search_chunks(session, brand, "Orchid").results
        )


def test_evaluation_groups_metrics_and_read_only_operations(corpus):
    engine, brand, _ = corpus
    build_index(engine, brand)
    statements = []

    def record(_connection, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", record)
    with Session(engine) as session:
        report = evaluate_suite(session, brand, suite())
    event.remove(engine, "before_cursor_execute", record)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP"}
    summary = report["summary"]
    assert summary["evidence_groups"] == 3
    assert summary["coverage_at_5"] == summary["coverage_at_10"] == 1
    assert summary["all_evidence_at_10"] == 2
    assert summary["mean_reciprocal_rank_at_10"] == 1
    assert summary["negative_zero_results"] == 1
    assert report["cases"][0]["first_relevant_rank"] == 1


def test_missed_evidence_is_not_counted_and_invalid_gold_rejected(corpus):
    engine, brand, _ = corpus
    build_index(engine, brand)
    spec = suite()
    spec.cases[0].query = "absentzzq9384"
    with Session(engine) as session:
        report = evaluate_suite(session, brand, spec)
        assert report["summary"]["coverage_at_10"] == pytest.approx(2 / 3)
        assert report["summary"]["mean_reciprocal_rank_at_10"] == 0.5
        assert report["cases"][0]["missing_at_10"] == ["gateway"]
        spec.cases[0].expected_groups[0].alternatives[0].quotation = "invented quotation"
        with pytest.raises(RetrievalError, match="Invalid evaluation references"):
            evaluate_suite(session, brand, spec)


def test_registered_suite_paths_and_api_errors(corpus, tmp_path):
    engine, brand, _ = corpus
    settings = Settings(_env_file=None, magi_retrieval_evaluation_dir=tmp_path)
    (tmp_path / "test.json").write_text(suite().model_dump_json())
    assert suites(settings)[0]["case_count"] == 3
    for identifier in ["../test", "/etc/passwd", "absent"]:
        with pytest.raises(RetrievalError):
            registered_suite(tmp_path, identifier)
    with Session(engine) as session:
        with pytest.raises(HTTPException) as error:
            search(brand, session, "Orchid")
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "index_not_ready"
        assert get_index(brand, session).state == "missing"
        assert prepare_index(brand, session).state == "ready"
        assert (
            evaluate(brand, EvaluationRequest(suite_id="test"), session, settings)["summary"][
                "cases"
            ]
            == 3
        )
        assert len(search(brand, session, "Orchid").results) == 2


def test_fixture_is_fixed_twenty_cases_with_unique_anchors():
    directory = Path(__file__).resolve().parents[2] / "evaluation"
    spec = registered_suite(directory, "wickhold-baseline-v1")
    assert [
        sum(c.category == category for c in spec.cases)
        for category in ["direct", "paraphrase", "multi", "negative"]
    ] == [6, 6, 6, 2]
    source = directory.parent / "wickhold-candidate-package/candidate"
    if not source.exists():
        pytest.skip("Optional supplied brand package is not present")
    for case in spec.cases:
        for group in case.expected_groups:
            for ref in group.alternatives:
                assert (source / ref.document_path).read_text().count(ref.quotation) == 1
    assert len(json.loads((directory / "wickhold-baseline-v1.json").read_text())["cases"]) == 20


def test_missing_index_table_requires_rebuild(corpus):
    engine, brand, _ = corpus
    build_index(engine, brand)
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE {index_table(brand)}"))
    with Session(engine) as session:
        assert index_status(session, brand).state == "missing"
        with pytest.raises(RetrievalError):
            search_chunks(session, brand, "Orchid")
    assert build_index(engine, brand).state == "ready"


def test_other_brand_content_cannot_change_rankings(corpus):
    engine, brand, other = corpus
    build_index(engine, brand)
    with Session(engine) as session:
        before = search_chunks(session, brand, "Orchid gateway")
        original = session.scalar(select(Document))
        other_doc = Document(
            brand_id=other,
            relative_path="brand/other.md",
            title="Gateway",
            content_hash=original.content_hash,
            raw_text=original.raw_text,
        )
        session.add(other_doc)
        session.flush()
        original_chunk = session.scalar(select(DocumentChunk))
        copied = {
            column.name: getattr(original_chunk, column.name)
            for column in DocumentChunk.__table__.columns
            if column.name not in {"id", "document_id"}
        }
        session.add(DocumentChunk(document_id=other_doc.id, **copied))
        session.commit()
    build_index(engine, other)
    with Session(engine) as session:
        after = search_chunks(session, brand, "Orchid gateway")
    assert before.fingerprint == after.fingerprint
    assert before.results == after.results


def test_cutoffs_and_alternative_evidence_do_not_inflate_metrics(corpus, monkeypatch):
    from app.brand import retrieval_eval
    from app.brand.retrieval import SearchResponse

    engine, brand, _ = corpus
    build_index(engine, brand)
    spec = suite()
    spec.cases = [spec.cases[1]]
    # Two alternative citations to the same evidence still form one requirement.
    spec.cases[0].expected_groups[0].alternatives *= 2
    with Session(engine) as session:
        actual = search_chunks(session, brand, "gateway")
        hits = [
            actual.results[0].model_copy(update={"chunk_id": f"irrelevant-{rank}", "rank": rank})
            for rank in range(1, 11)
        ]
        hits[5] = actual.results[0].model_copy(update={"rank": 6})
        monkeypatch.setattr(
            retrieval_eval,
            "search_chunks",
            lambda *_args: SearchResponse(
                query="Orchid",
                terms=["orchid"],
                fingerprint=actual.fingerprint,
                eligible_chunks=2,
                excluded_chunks=0,
                latency_ms=1,
                retrieved_characters=100,
                results=hits,
            ),
        )
        summary = evaluate_suite(session, brand, spec)["summary"]
    assert summary["evidence_groups"] == 2
    assert summary["coverage_at_5"] == 0
    assert summary["coverage_at_10"] == 0.5
    assert summary["all_evidence_at_10"] == 0
    assert summary["mean_reciprocal_rank_at_10"] == pytest.approx(1 / 6)
