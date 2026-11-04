import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import create_database_engine, initialize_database, session_factory
from app.main import create_app
from app.models import Brand, Document


@pytest.fixture
def storage(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'magi.db'}")
    initialize_database(engine)
    yield engine, session_factory(engine)
    engine.dispose()


def document(brand_id, path="brand/voice.md"):
    return Document(
        brand_id=brand_id,
        relative_path=path,
        content_hash="a" * 64,
        raw_text="# Voice\nPlain language.",
    )


def test_tables_and_document_round_trip(storage):
    engine, sessions = storage
    assert {"brands", "documents"} <= set(inspect(engine).get_table_names())
    with sessions() as session:
        brand = Brand(name="Example", source_directory="/tmp/example")
        session.add(brand)
        session.flush()
        source = document(brand.id)
        session.add(source)
        session.commit()
        source_id = source.id
    with sessions() as session:
        saved = session.get(Document, source_id)
        assert saved.raw_text == "# Voice\nPlain language."
        assert saved.title is None
        assert saved.created_at is not None
        assert saved.updated_at is not None


def test_paths_are_unique_within_brand_but_shared_across_brands(storage):
    _, sessions = storage
    with sessions() as session:
        first = Brand(name="First", source_directory="/tmp/first")
        second = Brand(name="Second", source_directory="/tmp/second")
        session.add_all([first, second])
        session.flush()
        first_id = first.id
        session.add_all([document(first.id), document(second.id)])
        session.commit()
        session.add(document(first_id))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_nonexistent_brand_is_rejected(storage):
    _, sessions = storage
    with sessions() as session:
        session.add(document("missing"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_application_restart_preserves_records(tmp_path):
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'magi.db'}")
    with TestClient(create_app(settings)) as client, client.app.state.sessions() as session:
        brand = Brand(name="Persistent", source_directory="/tmp/persistent")
        session.add(brand)
        session.flush()
        session.add(document(brand.id))
        session.commit()
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200
        with client.app.state.sessions() as session:
            assert session.scalar(select(Brand.name)) == "Persistent"
            assert session.scalar(select(Document.relative_path)) == "brand/voice.md"
