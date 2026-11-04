import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.sources import get_document, list_brands, list_documents
from app.brand.chunking import build_chunks
from app.brand.discovery import discover_folder
from app.brand.ingestion import ingest_documents
from app.brand.source_metadata import build_source_metadata
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.main import create_app
from app.models import Brand, Document, DocumentChunk, SourceAnnotation


@pytest.fixture
def sources(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'magi.db'}")
    initialize_database(engine)
    root = tmp_path / "materials"
    (root / "brand").mkdir(parents=True)
    (root / "brand/z.md").write_text("# Last\n\nOwner: Sample Person\n\n## Detail\nMore text")
    (root / "brand/a.md").write_text("# First\nNo explicit metadata")
    brand = ingest_documents(engine, discover_folder(root), "Example").brand_id
    build_chunks(engine, brand)
    build_source_metadata(engine, brand)
    with Session(engine) as session:
        other = Brand(name="Other", source_directory="/tmp/other")
        session.add(other)
        session.commit()
        other_id = other.id
    yield engine, brand, other_id
    engine.dispose()


def test_summaries_and_details_match_stored_sources(sources):
    engine, brand, _ = sources
    with Session(engine) as session:
        brands = list_brands(session)
        assert next(b.document_count for b in brands if b.id == brand) == 2
        documents = list_documents(brand, session)
        assert [d.relative_path for d in documents] == ["brand/a.md", "brand/z.md"]
        detail = get_document(brand, documents[1].id, session)
        assert detail.chunk_state == detail.metadata_state == "current"
        assert [c.position for c in detail.chunks] == list(range(len(detail.chunks)))
        assert len(detail.chunks) == detail.chunk_count
        assert detail.annotation.candidates[0].verification_status == "candidate"
        for chunk in detail.chunks:
            assert detail.raw_text[chunk.start_offset : chunk.end_offset] == chunk.content
        for candidate in detail.annotation.candidates:
            assert (
                detail.raw_text[candidate.start_offset : candidate.end_offset]
                == candidate.statement
            )
        assert get_document(brand, documents[0].id, session).annotation.candidates == []


def test_brand_boundaries_and_missing_ids(sources):
    engine, brand, other = sources
    with Session(engine) as session:
        document = session.scalar(select(Document).where(Document.brand_id == brand))
        for args in [(other, document.id), (brand, "missing"), ("missing", document.id)]:
            with pytest.raises(HTTPException) as error:
                get_document(*args, session)
            assert error.value.status_code == 404
        with pytest.raises(HTTPException) as error:
            list_documents("missing", session)
        assert error.value.status_code == 404
        assert list_documents(other, session) == []


def test_stale_hashes_and_versions_are_visible(sources):
    engine, brand, _ = sources
    with Session(engine) as session:
        document = session.scalar(select(Document).where(Document.relative_path == "brand/z.md"))
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.position)
            )
        )
        chunks[0].source_hash = "old-hash"
        annotation = session.scalar(
            select(SourceAnnotation).where(SourceAnnotation.document_id == document.id)
        )
        annotation.extractor_version = "old-version"
        session.commit()
        detail = get_document(brand, document.id, session)
        assert detail.chunk_state == detail.metadata_state == "stale"
        assert detail.chunks[0].state == "stale"
        assert detail.annotation.candidates  # Evidence stays inspectable.
        summary = next(d for d in list_documents(brand, session) if d.id == document.id)
        assert summary.chunk_state == summary.metadata_state == "stale"
        chunks[0].source_hash = document.content_hash
        chunks[0].chunker_version = "old-version"
        annotation.extractor_version = "source-statements-v1"
        annotation.source_hash = "old-hash"
        session.commit()
        assert get_document(brand, document.id, session).chunks[0].state == "stale"
        assert get_document(brand, document.id, session).metadata_state == "stale"


def test_missing_derived_data_and_empty_library(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    initialize_database(engine)
    with Session(engine) as session:
        assert list_brands(session) == []
        brand = Brand(name="New", source_directory="/tmp/new")
        session.add(brand)
        session.flush()
        document = Document(
            brand_id=brand.id, relative_path="brand/new.md", raw_text="", content_hash="a" * 64
        )
        session.add(document)
        session.commit()
        detail = get_document(brand.id, document.id, session)
        assert detail.chunk_state == detail.metadata_state == "missing"
        assert detail.chunks == [] and detail.annotation is None
    engine.dispose()


def test_sources_are_read_only(sources):
    engine, brand, _ = sources

    def reject_write(_conn, _cursor, statement, *_args):
        assert statement.lstrip().upper().startswith("SELECT"), statement

    event.listen(engine, "before_cursor_execute", reject_write)
    try:
        with Session(engine) as session:
            list_brands(session)
            for document in list_documents(brand, session):
                get_document(brand, document.id, session).model_dump(mode="json")
    finally:
        event.remove(engine, "before_cursor_execute", reject_write)


def test_sources_http_routes(sources):
    engine, brand, other = sources
    app = create_app(Settings(_env_file=None, magi_database_url=str(engine.url)))
    with TestClient(app) as client:
        assert client.get("/api/brands").status_code == 200
        listing = client.get(f"/api/brands/{brand}/documents")
        assert listing.status_code == 200
        identifier = listing.json()[0]["id"]
        detail = client.get(f"/api/brands/{brand}/documents/{identifier}")
        assert detail.status_code == 200 and "raw_text" in detail.json()
        assert client.get(f"/api/brands/{other}/documents/{identifier}").status_code == 404
        assert client.post("/api/brands").status_code == 405
