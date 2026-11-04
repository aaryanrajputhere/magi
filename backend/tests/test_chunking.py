import re

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.brand.chunking import CHUNKER_VERSION, build_chunks, chunk_markdown
from app.brand.discovery import discover_folder
from app.brand.ingestion import ingest_documents
from app.config import PROJECT_ROOT, Settings
from app.db import create_database_engine, initialize_database
from app.models import Document, DocumentChunk


def assert_coverage(text, chunks):
    cursor = 0
    starts = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", text))]
    for position, chunk in enumerate(chunks):
        assert chunk.position == position
        assert chunk.start_offset >= cursor
        assert not text[cursor : chunk.start_offset].strip()
        assert chunk.content == text[chunk.start_offset : chunk.end_offset]
        assert chunk.content.strip()
        assert chunk.start_line == sum(start <= chunk.start_offset for start in starts)
        assert chunk.end_line == sum(start < chunk.end_offset for start in starts)
        cursor = chunk.end_offset
    assert not text[cursor:].strip()


@pytest.fixture
def engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'magi.db'}")
    initialize_database(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    (root / "brand/one.md").write_text(
        "# One\n\nFirst paragraph.\n\n## Detail\nSecond paragraph.\n"
    )
    (root / "brand/empty.md").write_text(" \n\n")
    return root


def imported(engine, source):
    report = ingest_documents(engine, discover_folder(source), "Example")
    assert report.status == "success"
    return report.brand_id


def test_headings_preamble_and_nested_paths():
    text = "Introduction.\n\n# Root\nText\n\n## Child\nMore\n\n### Deep\nEnd\n\n## Sibling\nFinal\n"
    chunks = chunk_markdown(text)
    assert [c.heading_path for c in chunks] == [
        [],
        ["Root"],
        ["Root", "Child"],
        ["Root", "Child", "Deep"],
        ["Root", "Sibling"],
    ]
    assert_coverage(text, chunks)
    assert chunk_markdown(text) == chunks


def test_frontmatter_and_code_headings_are_not_sections():
    text = "---\n# Metadata\nkey: value\n---\n\n# Actual\n\n```md\n# Fake\n```\n"
    chunks = chunk_markdown(text, 30)
    assert all("Fake" not in c.heading_path and "Metadata" not in c.heading_path for c in chunks)
    assert chunks[-1].heading_path == ["Actual"]
    assert any("```md\n# Fake\n```" in c.content for c in chunks)
    assert_coverage(text, chunks)


@pytest.mark.parametrize(
    "block",
    [
        "| Column | Value |\n| --- | --- |\n" + "| cell | value |\n" * 10,
        "```python\n" + "print('hello')\n" * 10 + "```\n",
        "- First item\n" + "  - Nested item\n" * 10 + "- Last item\n",
        "A single long paragraph " * 30 + "\n",
    ],
)
def test_oversized_blocks_remain_intact(block):
    text = "# Heading\n\n" + block + "\nAfter.\n"
    chunks = chunk_markdown(text, 50)
    containing = [c for c in chunks if block in c.content]
    assert len(containing) == 1
    assert containing[0].oversized
    assert_coverage(text, chunks)


@pytest.mark.parametrize(
    "text",
    [
        "No headings\n\n" + "Another paragraph.\n\n" * 10,
        "\ufeff# Title\r\n\r\nText\r\n\r\n## Next\r\nMore\r\n",
        "Title\n=====\n\nBody\n\nNext\n----\n\nOther",
        "[reference]: https://example.com\n\n# Title\n\n[reference]\n",
        "# One\r\rText\r\r## Two\rMore",
    ],
)
def test_source_coverage_and_line_endings(text):
    chunks = chunk_markdown(text, 40)
    assert_coverage(text, chunks)


def test_empty_and_invalid_target():
    assert chunk_markdown(" \n\t\r\n") == []
    with pytest.raises(ValueError):
        chunk_markdown("text", 0)


def test_repeat_build_preserves_ids_and_target_changes_rebuild(engine, source):
    brand = imported(engine, source)
    first = build_chunks(engine, brand)
    assert first.processed == 2 and first.empty == 1 and first.rebuilt == 1
    with Session(engine) as session:
        ids = list(session.scalars(select(DocumentChunk.id).order_by(DocumentChunk.position)))
    repeat = build_chunks(engine, brand)
    assert repeat.rebuilt == 0 and repeat.unchanged == 2
    with Session(engine) as session:
        assert (
            list(session.scalars(select(DocumentChunk.id).order_by(DocumentChunk.position))) == ids
        )
        assert all(
            c.chunker_version == CHUNKER_VERSION for c in session.scalars(select(DocumentChunk))
        )
    changed = build_chunks(engine, brand, 10)
    assert changed.rebuilt == 1
    assert changed.oversized
    with Session(engine) as session:
        assert not set(ids) & set(session.scalars(select(DocumentChunk.id)))


def test_ingestion_invalidates_only_changed_documents(engine, source):
    (source / "brand/unchanged.md").write_text("# Stay\nUnchanged text")
    brand = imported(engine, source)
    build_chunks(engine, brand)
    with Session(engine) as session:
        original = {d.relative_path: d.id for d in session.scalars(select(Document))}
        stable_ids = list(
            session.scalars(
                select(DocumentChunk.id).where(
                    DocumentChunk.document_id == original["brand/unchanged.md"]
                )
            )
        )
    (source / "brand/one.md").write_text("# Changed\nUpdated text")
    imported(engine, source)
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == original["brand/one.md"])
            )
            == 0
        )
        assert (
            list(
                session.scalars(
                    select(DocumentChunk.id).where(
                        DocumentChunk.document_id == original["brand/unchanged.md"]
                    )
                )
            )
            == stable_ids
        )
    assert build_chunks(engine, brand).rebuilt == 1


def test_unknown_brand_and_invalid_target(engine):
    with pytest.raises(ValueError, match="Unknown brand"):
        build_chunks(engine, "missing")
    with pytest.raises(ValueError, match="positive"):
        build_chunks(engine, "missing", 0)


def test_database_failure_rolls_back_all_chunk_replacements(engine, source):
    brand = imported(engine, source)
    build_chunks(engine, brand)
    with Session(engine) as session:
        ids = list(session.scalars(select(DocumentChunk.id)))

    def fail(_conn, _cursor, statement, params, _context, _many):
        if statement.startswith("INSERT INTO document_chunks"):
            raise OperationalError(statement, params, Exception("test failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        report = build_chunks(engine, brand, 10)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert report.status == "failed" and report.rebuilt == 0
    with Session(engine) as session:
        assert list(session.scalars(select(DocumentChunk.id))) == ids


def test_all_brand_documents_have_exact_coverage(engine):
    root = PROJECT_ROOT / "wickhold-candidate-package/candidate"
    brand = imported(engine, root)
    report = build_chunks(engine, brand)
    assert report.status == "success" and report.processed == 83
    assert report.rebuilt == 83
    with Session(engine) as session:
        for doc in session.scalars(select(Document)):
            chunks = list(
                session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == doc.id)
                    .order_by(DocumentChunk.position)
                )
            )
            assert_coverage(doc.raw_text, chunks)
            assert all(c.source_hash == doc.content_hash for c in chunks)
    assert build_chunks(engine, brand).unchanged == 83


def test_cli_report(engine, source, monkeypatch, capsys):
    import json

    from app.brand.chunking import main

    brand = imported(engine, source)
    settings = Settings(_env_file=None, magi_database_url=str(engine.url))
    monkeypatch.setattr("app.brand.chunking.get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["chunking", "--brand-id", brand])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 2
    monkeypatch.setattr("sys.argv", ["chunking", "--brand-id", "unknown"])
    assert main() == 1
    assert "Unknown brand" in capsys.readouterr().err


def test_chunker_version_change_rebuilds(engine, source, monkeypatch):
    brand = imported(engine, source)
    build_chunks(engine, brand)
    monkeypatch.setattr("app.brand.chunking.CHUNKER_VERSION", "test-next-version")
    assert build_chunks(engine, brand).rebuilt == 1
    with Session(engine) as session:
        assert all(
            c.chunker_version == "test-next-version" for c in session.scalars(select(DocumentChunk))
        )


def test_failed_ingestion_restores_invalidated_chunks(engine, source):
    brand = imported(engine, source)
    build_chunks(engine, brand)
    with Session(engine) as session:
        ids = set(session.scalars(select(DocumentChunk.id)))
        old_text = session.scalar(
            select(Document.raw_text).where(Document.relative_path == "brand/one.md")
        )
    (source / "brand/one.md").write_text("# Changed\nReplacement")

    def fail(_conn, _cursor, statement, params, _context, _many):
        if statement.startswith("UPDATE documents"):
            raise OperationalError(statement, params, Exception("test failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        report = ingest_documents(engine, discover_folder(source), "Example")
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert report.status == "failed"
    with Session(engine) as session:
        assert set(session.scalars(select(DocumentChunk.id))) == ids
        assert (
            session.scalar(
                select(Document.raw_text).where(Document.relative_path == "brand/one.md")
            )
            == old_text
        )


def test_unicode_separators_in_frontmatter_do_not_shift_offsets():
    text = '---\nkey: "value\u2028continued"\n---\n\n# Actual\n\nBody\n'
    chunks = chunk_markdown(text)
    assert chunks[-1].heading_path == ["Actual"]
    assert chunks[-1].start_line == 5
    assert chunks[-1].content.startswith("# Actual")
    assert_coverage(text, chunks)


def test_changed_document_becoming_empty_removes_chunks(engine, source):
    brand = imported(engine, source)
    build_chunks(engine, brand)
    (source / "brand/one.md").write_text(" \n")
    imported(engine, source)
    report = build_chunks(engine, brand)
    assert report.empty == 2 and report.total_chunks == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


@pytest.mark.parametrize("target", ["0", "-1", "abc"])
def test_cli_rejects_invalid_targets(monkeypatch, target):
    from app.brand.chunking import main

    monkeypatch.setattr("sys.argv", ["chunking", "--brand-id", "unused", "--target-chars", target])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
