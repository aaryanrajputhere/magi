import re

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.brand.chunking import build_chunks
from app.brand.discovery import discover_folder
from app.brand.ingestion import ingest_documents
from app.brand.source_metadata import (
    build_source_metadata,
    extract_source_metadata,
    get_chunk_source_metadata,
)
from app.config import PROJECT_ROOT, Settings
from app.db import create_database_engine, initialize_database
from app.models import Document, DocumentChunk, SourceAnnotation


@pytest.fixture
def engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'database.db'}")
    initialize_database(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    (root / "brand/guide.md").write_text(
        "# Guide\n\nOwner: Aster Vale\n\nRevised September 2026.\n\nThis guide replaces the 2024 guide.\n"
    )
    (root / "brand/unknown.md").write_text("# No metadata\nContent only")
    return root


def imported(engine, source):
    report = ingest_documents(engine, discover_folder(source), "Different Brand")
    assert report.status == "success"
    return report.brand_id


def test_explicit_statements_have_exact_source_locations():
    text = "\ufeff# Guide\r\n\r\n**Owner:** Aster Vale\r\nLast edit Nov 2025\r\nThis guide supersedes the old guide.\r\nThis document governs external copy.\r\n"
    candidates = extract_source_metadata(text)
    assert {c.category for c in candidates} == {"owner", "date", "authority", "supersession"}
    for candidate in candidates:
        assert candidate.statement == text[candidate.start_offset : candidate.end_offset]
        assert candidate.start_line == candidate.end_line
        assert (
            candidate.start_line
            == len(re.findall(r"\r\n|\r|\n", text[: candidate.start_offset])) + 1
        )
        assert candidate.verification_status == "candidate"
    assert next(c for c in candidates if c.category == "date").statement == "Last edit Nov 2025"


def test_examples_and_code_are_ignored_but_real_policy_heading_is_not():
    text = """# Approved claims

Owner: Actual Person

```md
Owner: Code Example
This guide replaces the old one.
```

> Owner: Quoted Person
>
> Last checked 2026-02-01

"This document governs examples."

## Examples

Owner: Example Person

### Nested

Revised January 2026

## Governance

This document governs external claims.
"""
    candidates = extract_source_metadata(text)
    assert [c.statement for c in candidates] == [
        "Owner: Actual Person",
        "This document governs external claims.",
    ]


def test_unrelated_dates_and_unknown_information_are_not_inferred():
    text = "# 2026 Guide\n\nSOC 2 renewed January 2026.\n\nProduct launched March 2026.\n\nSomeone said this looks final.\n"
    assert extract_source_metadata(text) == []
    assert extract_source_metadata("# Guide\n\nReviewed September\n")[0].category == "date"


def test_frontmatter_is_not_interpreted():
    text = '---\nowner: "Example\u2028continued"\n---\n\nOwner: Real Person\n'
    candidates = extract_source_metadata(text)
    assert len(candidates) == 1 and candidates[0].start_line == 5
    assert candidates[0].statement == text[candidates[0].start_offset : candidates[0].end_offset]


def test_repeat_runs_chunk_access_and_chunk_rebuild_independence(engine, source):
    brand = imported(engine, source)
    first = build_source_metadata(engine, brand)
    assert first.processed == first.rebuilt == 2
    assert first.unknown == ["brand/unknown.md"]
    with Session(engine) as session:
        ids = set(session.scalars(select(SourceAnnotation.id)))
    repeat = build_source_metadata(engine, brand)
    assert repeat.rebuilt == 0 and repeat.unchanged == 2
    build_chunks(engine, brand)
    with Session(engine) as session:
        chunk = session.scalar(
            select(DocumentChunk).join(Document).where(Document.relative_path == "brand/guide.md")
        )
        assert {c.category for c in get_chunk_source_metadata(session, chunk.id)} == {
            "owner",
            "date",
            "supersession",
        }
        assert set(session.scalars(select(SourceAnnotation.id))) == ids
        annotation = session.scalar(
            select(SourceAnnotation).where(SourceAnnotation.document_id == chunk.document_id)
        )
        annotation.source_hash = "stale"
        session.commit()
        assert get_chunk_source_metadata(session, chunk.id) == []
    build_source_metadata(engine, brand)
    build_chunks(engine, brand, 10)
    with Session(engine) as session:
        assert set(session.scalars(select(SourceAnnotation.id))) == ids


def test_source_change_invalidates_only_its_annotations(engine, source):
    brand = imported(engine, source)
    build_source_metadata(engine, brand)
    (source / "brand/guide.md").write_text("# Guide\n\nOwner: New Person")
    imported(engine, source)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceAnnotation)) == 1
    assert build_source_metadata(engine, brand).rebuilt == 1


def test_database_failure_rolls_back_run(engine, source):
    brand = imported(engine, source)

    def fail(_conn, _cursor, statement, params, _context, _many):
        if statement.startswith("INSERT INTO source_annotations"):
            raise OperationalError(statement, params, Exception("test failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        report = build_source_metadata(engine, brand)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert report.status == "failed" and report.rebuilt == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceAnnotation)) == 0


def test_supplied_package_contains_key_authority_evidence(engine):
    brand = imported(engine, PROJECT_ROOT / "wickhold-candidate-package/candidate")
    report = build_source_metadata(engine, brand)
    assert report.status == "success" and report.processed == 83
    docs = {d.relative_path: d.candidates for d in report.documents}
    assert any(
        "replace the 2024 guidelines in full" in c.statement
        for c in docs["brand/guidelines/Brand_Guidelines_2026.md"]["supersession"]
    )
    assert any(
        "sheet to use" in c.statement
        for c in docs["brand/legal/certifications-status.md"]["authority"]
    )
    assert any(
        "Brand_Guidelines_v3_FINAL" in c.statement
        for c in docs["brand/README_start_here.md"]["authority"]
    )


def test_version_change_rebuilds_annotations(engine, source, monkeypatch):
    brand = imported(engine, source)
    build_source_metadata(engine, brand)
    monkeypatch.setattr("app.brand.source_metadata.EXTRACTOR_VERSION", "test-next")
    assert build_source_metadata(engine, brand).rebuilt == 2


def test_cli_and_unknown_ids(engine, source, monkeypatch, capsys):
    import json

    from app.brand.source_metadata import main

    brand = imported(engine, source)
    settings = Settings(_env_file=None, magi_database_url=str(engine.url))
    monkeypatch.setattr("app.brand.source_metadata.get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["source_metadata", "--brand-id", brand])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 2
    monkeypatch.setattr("sys.argv", ["source_metadata", "--brand-id", "unknown"])
    assert main() == 1
    assert "Unknown brand" in capsys.readouterr().err
    with Session(engine) as session, pytest.raises(ValueError, match="Unknown chunk"):
        get_chunk_source_metadata(session, "unknown")


def test_unknown_categories_are_explicit(engine, source):
    brand = imported(engine, source)
    report = build_source_metadata(engine, brand)
    docs = {d.relative_path: d for d in report.documents}
    assert docs["brand/unknown.md"].unknown_categories == [
        "owner",
        "date",
        "authority",
        "supersession",
    ]
    assert docs["brand/guide.md"].unknown_categories == ["authority"]


def test_failed_document_update_restores_source_annotations(engine, source):
    brand = imported(engine, source)
    build_source_metadata(engine, brand)
    with Session(engine) as session:
        ids = set(session.scalars(select(SourceAnnotation.id)))
    (source / "brand/guide.md").write_text("# Changed\n\nOwner: Replacement")

    def fail(_conn, _cursor, statement, params, _context, _many):
        if statement.startswith("UPDATE documents"):
            raise OperationalError(statement, params, Exception("test update failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        report = ingest_documents(engine, discover_folder(source), "Different Brand")
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert report.status == "failed"
    with Session(engine) as session:
        assert set(session.scalars(select(SourceAnnotation.id))) == ids
