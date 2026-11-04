from hashlib import sha256
from unittest.mock import patch

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.brand.discovery import DiscoveryError, discover_folder
from app.brand.ingestion import discover_sources, ingest_documents
from app.brand.parsers import markdown_title, parse_markdown
from app.config import PROJECT_ROOT, Settings
from app.db import create_database_engine, initialize_database
from app.models import Brand, Document


@pytest.fixture
def engine(tmp_path):
    database = create_database_engine(f"sqlite:///{tmp_path / 'database.db'}")
    initialize_database(database)
    yield database
    database.dispose()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    (root / "brand/voice.md").write_text("# Voice\nPlain language.\n")
    return root


def run_import(engine, source, name="Example"):
    return ingest_documents(engine, discover_folder(source), name)


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("# First\n# Second", "First"),
        ("```md\n# Fake\n```\n# Actual", "Actual"),
        ("---\n# Not a title\nname: example\n---\n# Actual", "Actual"),
        ("Title\n=====\n", "Title"),
        ("## Smaller heading", "fallback"),
        ("# A **bold** [link](https://example.com) with `code`", "A bold link with code"),
        ("\ufeff# Heading", "Heading"),
        ("", "fallback"),
        ("> # Quoted title\n\n# Main", "Main"),
    ],
)
def test_title_extraction(text, title):
    assert markdown_title(text, "fallback") == title


def test_original_bytes_and_markdown_are_preserved(tmp_path):
    content = b"\xef\xbb\xbf# Title\r\n\r\n| A | B |\r\n| - | - |\r\n"
    path = tmp_path / "source.md"
    path.write_bytes(content)
    parsed = parse_markdown(path)
    assert parsed.raw_text.encode("utf-8") == content
    assert parsed.content_hash == sha256(content).hexdigest()


def test_repeat_import_and_single_document_update(engine, source):
    (source / "brand/other.md").write_text("No heading")
    first = run_import(engine, source)
    assert first.counts["added"] == 2
    with Session(engine) as session:
        originals = {
            d.relative_path: (d.id, d.created_at, d.updated_at)
            for d in session.scalars(select(Document))
        }
    repeat = run_import(engine, source, "Do not rename")
    assert repeat.brand_id == first.brand_id
    assert repeat.counts["unchanged"] == 2
    with Session(engine) as session:
        assert session.scalar(select(Brand.name)) == "Example"
        for d in session.scalars(select(Document)):
            assert (d.id, d.created_at, d.updated_at) == originals[d.relative_path]
    (source / "brand/voice.md").write_text("# Changed\nNew content")
    changed = run_import(engine, source)
    assert changed.updated == ["brand/voice.md"]
    assert changed.unchanged == ["brand/other.md"]
    with Session(engine) as session:
        docs = {d.relative_path: d for d in session.scalars(select(Document))}
        assert docs["brand/voice.md"].title == "Changed"
        assert docs["brand/voice.md"].id == originals["brand/voice.md"][0]
        assert docs["brand/voice.md"].created_at == originals["brand/voice.md"][1]
        assert docs["brand/other.md"].title == "other"
        assert docs["brand/other.md"].updated_at == originals["brand/other.md"][2]


def test_invalid_utf8_retains_previous_content_and_imports_valid_files(engine, source):
    run_import(engine, source)
    (source / "brand/voice.md").write_bytes(b"\xff")
    (source / "brand/new.md").write_text("# New")
    report = run_import(engine, source)
    assert report.status == "partial"
    assert report.failed[0].path == "brand/voice.md"
    assert report.added == ["brand/new.md"]
    with Session(engine) as session:
        assert (
            session.scalar(
                select(Document.raw_text).where(Document.relative_path == "brand/voice.md")
            )
            == "# Voice\nPlain language.\n"
        )


def test_read_failure_is_reported(engine, source):
    with patch("app.brand.ingestion.parse_markdown", side_effect=PermissionError("denied")):
        report = run_import(engine, source)
    assert report.status == "partial"
    assert report.failed[0].reason == "denied"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0


def test_missing_files_are_retained(engine, source):
    run_import(engine, source)
    (source / "brand/voice.md").unlink()
    report = run_import(engine, source)
    assert report.status == "success"
    assert report.missing == ["brand/voice.md"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 1


def test_incomplete_scan_writes_nothing(engine, source):
    inventory = discover_folder(source)
    inventory.complete = False
    with pytest.raises(DiscoveryError):
        ingest_documents(engine, inventory, "Example")
    settings = Settings(_env_file=None, magi_brand_source_dir=source)
    with (
        patch("app.brand.ingestion.discover_folder", return_value=inventory),
        pytest.raises(DiscoveryError),
    ):
        discover_sources(settings)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Brand)) == 0


def test_database_failure_rolls_back_brand_and_documents(engine, source):
    def fail_insert(_conn, _cursor, statement, parameters, _context, _many):
        if statement.startswith("INSERT INTO documents"):
            raise OperationalError(statement, parameters, Exception("simulated failure"))

    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        report = run_import(engine, source)
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)
    assert report.status == "failed"
    assert report.brand_id is None
    assert report.added == []
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Brand)) == 0
        assert session.scalar(select(func.count()).select_from(Document)) == 0


def test_source_roots_are_separate_brands(engine, source, tmp_path):
    other = tmp_path / "other"
    (other / "brand").mkdir(parents=True)
    (other / "brand/voice.md").write_text("# Other")
    assert run_import(engine, source).brand_id != run_import(engine, other).brand_id


def test_real_package_imports_only_83_brand_documents(engine):
    source = PROJECT_ROOT / "wickhold-candidate-package/candidate"
    report = run_import(engine, source, "Wickhold")
    assert report.status == "success"
    assert report.counts["added"] == 83
    with Session(engine) as session:
        docs = list(session.scalars(select(Document)))
        assert len(docs) == 83
        assert all(d.relative_path.startswith("brand/") for d in docs)
    assert run_import(engine, source).counts["unchanged"] == 83


def test_cli_report_and_exit_status(source, tmp_path, monkeypatch, capsys):
    import json

    from app.brand.ingestion import main

    settings = Settings(
        _env_file=None,
        magi_brand_source_dir=source,
        magi_database_url=f"sqlite:///{tmp_path / 'cli.db'}",
    )
    monkeypatch.setattr("app.brand.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["ingestion", "--brand-name", "Example"])
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["added"] == 1
    (source / "brand/voice.md").write_bytes(b"\xff")
    assert main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "partial"


def test_cli_incomplete_discovery_does_not_create_database(source, tmp_path, monkeypatch, capsys):
    from app.brand.ingestion import main

    target = tmp_path / "not-created/database.db"
    settings = Settings(
        _env_file=None,
        magi_brand_source_dir=source,
        magi_database_url=f"sqlite:///{target}",
    )
    inventory = discover_folder(source)
    inventory.complete = False
    monkeypatch.setattr("app.brand.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr("app.brand.ingestion.discover_folder", lambda *_args: inventory)
    monkeypatch.setattr("sys.argv", ["ingestion", "--brand-name", "Example"])
    assert main() == 1
    assert "incomplete" in capsys.readouterr().err
    assert not target.exists()
