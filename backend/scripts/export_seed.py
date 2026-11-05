"""Export only corpus data and embedding provenance, never private campaign history."""
import argparse
import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.db import create_database_engine, initialize_database


def export(source: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / 'seed.db'
        engine = create_database_engine(f'sqlite:///{target}')
        initialize_database(engine)
        engine.dispose()
        with sqlite3.connect(target) as db:
            db.execute('ATTACH DATABASE ? AS original', (source.resolve().as_uri() + '?mode=ro',))
            for table in ('brands', 'documents', 'document_chunks', 'source_annotations'):
                columns = ','.join(row[1] for row in db.execute(f'PRAGMA table_info({table})'))
                db.execute(f'INSERT INTO {table} ({columns}) SELECT {columns} FROM original.{table}')
            db.execute("UPDATE brands SET source_directory='/app/brand-source'")
            # Foreign keys require run IDs, but no original request/response bodies are exported.
            db.execute('''INSERT INTO llm_runs
                (id, operation, context, requested_model, request_body, started_at,
                 timeout_seconds, status, cost_status, cost_reason)
                SELECT id, 'seed_embedding_import', '{}', requested_model, '{}', started_at,
                       0, 'imported', 'unknown', 'Bundled embedding; original run log excluded'
                FROM original.llm_runs
                WHERE id IN (SELECT run_id FROM original.chunk_embeddings)''')
            columns = ','.join(row[1] for row in db.execute('PRAGMA table_info(chunk_embeddings)'))
            db.execute(f'INSERT INTO chunk_embeddings ({columns}) SELECT {columns} FROM original.chunk_embeddings')
            db.commit()
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with target.open('rb') as reader, gzip.open(destination, 'wb') as writer:
            shutil.copyfileobj(reader, writer)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    export(args.source, args.destination)
