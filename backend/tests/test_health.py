from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.main import create_app


def test_health_initializes_sqlite_without_api_keys(tmp_path):
    database = tmp_path / "nested" / "magi.db"
    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{database}")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "magi-api",
            "version": "0.1.0",
            "database": "connected",
        }
        assert database.exists()

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    engine.dispose()


def test_health_returns_503_when_database_is_unavailable(tmp_path):
    from sqlalchemy.exc import OperationalError

    class UnavailableSession:
        def __enter__(self):
            raise OperationalError("SELECT 1", {}, Exception("unavailable"))

        def __exit__(self, *_args):
            pass

    settings = Settings(_env_file=None, magi_database_url=f"sqlite:///{tmp_path / 'magi.db'}")
    with TestClient(create_app(settings)) as client:
        client.app.state.sessions = UnavailableSession
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
        assert "SELECT" not in response.text
