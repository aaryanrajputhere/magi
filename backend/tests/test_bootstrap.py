"""Startup must not expose the API after incomplete corpus preparation."""

import subprocess

import pytest

from app import bootstrap


def test_failed_preparation_never_starts_server(monkeypatch):
    started = []

    def fail():
        raise RuntimeError("embedding provider unavailable")

    monkeypatch.setattr(bootstrap, "prepare", fail)
    monkeypatch.setattr(bootstrap.os, "execvp", lambda *args: started.append(args))
    assert bootstrap.main() == 1
    assert started == []


def test_missing_sources_stop_before_indexing_or_embedding(monkeypatch):
    stages = []

    def run(module, *args):
        stages.append(module)
        return {"brand_id": "brand-1", "missing": ["brand/retired.md"]}

    monkeypatch.setattr(bootstrap, "run_stage", run)
    with pytest.raises(RuntimeError, match="source files are missing"):
        bootstrap.prepare()
    assert stages == ["app.brand.ingestion"]


def test_nonzero_stage_exit_is_not_treated_as_success(monkeypatch):
    monkeypatch.setattr(
        bootstrap.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, '{"status":"failed"}'),
    )
    with pytest.raises(RuntimeError, match="failed"):
        bootstrap.run_stage("app.brand.embeddings", "--brand-id", "brand-1")


def test_bundled_seed_is_clean_current_and_never_overwrites_local_state(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.brand.embeddings import snapshot, stored
    from app.config import Settings
    from app.db import create_database_engine

    target = tmp_path / 'magi.db'
    settings = Settings(_env_file=None, magi_database_url=f'sqlite:///{target}')
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    monkeypatch.setenv('MAGI_SEED_PATH', str(Path(__file__).parents[1] / 'seed/brand.db.gz'))
    bootstrap.restore_seed()
    with sqlite3.connect(target) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT count(*) FROM documents').fetchone()[0] == 83
        assert db.execute('SELECT count(*) FROM chunk_embeddings').fetchone()[0] == 927
        assert db.execute("SELECT count(*) FROM llm_runs WHERE request_body != '{}' OR response_body IS NOT NULL").fetchone()[0] == 0
        # No history-bearing tables contain records, including tables added in future exports.
        allowed = {'brands', 'documents', 'document_chunks', 'source_annotations',
                   'chunk_embeddings', 'llm_runs'}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if table not in allowed and not table.startswith('sqlite_'):
                assert db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
        brand_id = db.execute('SELECT id FROM brands').fetchone()[0]
        db.execute("UPDATE brands SET name='Preserved local brand'")
    engine = create_database_engine(settings.magi_database_url)
    try:
        with Session(engine) as session:
            inputs, excluded = snapshot(session, brand_id, lambda _: 1)
            rows = stored(session, brand_id)
            assert not excluded and len(inputs) == 927
            assert all(item.chunk_id in rows and item.matches(rows[item.chunk_id]) for item in inputs)
    finally:
        engine.dispose()
    bootstrap.restore_seed()
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT name FROM brands').fetchone()[0] == 'Preserved local brand'
