"""Prepare the mounted corpus before starting the Docker API server."""

import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def run_stage(module: str, *arguments: str) -> dict:
    print(f"Preparing brand: {module}", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    print(result.stdout, flush=True)
    if result.returncode:
        raise RuntimeError(f"{module} failed; fix the reported error and rerun startup.")
    return json.loads(result.stdout)


def restore_seed() -> None:
    from sqlalchemy.engine import make_url

    from app.config import get_settings
    from app.db import create_database_engine

    settings = get_settings()
    seed = Path(os.environ.get("MAGI_SEED_PATH", "/app/seed/brand.db.gz"))
    if not seed.is_file():
        return
    engine = create_database_engine(settings.magi_database_url)
    target = Path(make_url(str(engine.url)).database)
    engine.dispose()
    if target.exists():
        return
    print("Installing bundled brand database (no model calls).", flush=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".db") as temporary:
        with gzip.open(seed, "rb") as source:
            shutil.copyfileobj(source, temporary)
        temporary.flush()
        with sqlite3.connect(temporary.name) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Bundled database failed integrity validation")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Bundled database has invalid references")
        try:
            os.link(temporary.name, target)
        except FileExistsError:
            pass  # Another startup already installed it; never overwrite local state.


def prepare() -> None:
    restore_seed()
    imported = run_stage(
        "app.brand.ingestion", "--brand-name", os.environ.get("MAGI_BRAND_NAME", "Wickhold")
    )
    if imported.get("missing"):
        raise RuntimeError(
            "Previously imported source files are missing. Restore them or use a fresh database "
            "before startup; retained stale documents must not enter campaign retrieval."
        )
    brand_id = imported["brand_id"]
    for module in ("app.brand.chunking", "app.brand.source_metadata"):
        run_stage(module, "--brand-id", brand_id)
    run_stage("app.brand.retrieval", "index", "--brand-id", brand_id)
    from sqlalchemy.orm import Session

    from app.brand.embeddings import snapshot, stored
    from app.config import get_settings
    from app.db import create_database_engine

    engine = create_database_engine(get_settings().magi_database_url)
    try:
        with Session(engine) as session:
            # Comparing exact hashes/vectors requires no tokenization or provider call.
            inputs, excluded = snapshot(session, brand_id, lambda _: 0)
            rows = stored(session, brand_id)
            ready = bool(inputs) and not excluded and all(
                item.chunk_id in rows and item.matches(rows[item.chunk_id]) for item in inputs
            )
    finally:
        engine.dispose()
    if ready:
        print("All embeddings are current; no embedding calls needed.", flush=True)
    else:
        run_stage("app.brand.embeddings", "--brand-id", brand_id)


def main() -> int:
    try:
        prepare()
    except (RuntimeError, KeyError, ValueError, OSError) as exc:
        print(f"Brand preparation stopped: {exc}", file=sys.stderr, flush=True)
        return 1
    print("Brand ready. Starting API on port 8000.", flush=True)
    os.execvp(
        "uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
