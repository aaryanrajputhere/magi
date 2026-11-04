"""Explicit, repeatable import of discovered brand documents into SQLite."""

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.brand.discovery import DiscoveryError, DiscoveryWarning, Inventory, discover_folder
from app.brand.parsers import ParsedDocument, parse_markdown
from app.config import Settings, get_settings
from app.db import create_database_engine, initialize_database
from app.models import Brand, Document, DocumentChunk, PolicyExtraction, SourceAnnotation


class FileFailure(BaseModel):
    path: str
    reason: str


class ImportReport(BaseModel):
    brand_id: str | None = None
    source_root: str
    status: Literal["success", "partial", "failed"] = "success"
    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    failed: list[FileFailure] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    warnings: list[DiscoveryWarning] = Field(default_factory=list)
    error: str | None = None

    @computed_field
    @property
    def counts(self) -> dict[str, int]:
        return {
            key: len(getattr(self, key))
            for key in ("added", "updated", "unchanged", "failed", "missing")
        }


def discover_sources(settings: Settings) -> Inventory:
    inventory = discover_folder(
        settings.magi_brand_source_dir,
        settings.magi_document_directory,
        settings.magi_image_directory,
        settings.magi_brief_directory,
    )
    if not inventory.complete:
        raise DiscoveryError("Discovery is incomplete; import cancelled before database writes")
    return inventory


def ingest_documents(engine: Engine, inventory: Inventory, brand_name: str) -> ImportReport:
    if not inventory.complete:
        raise DiscoveryError("Discovery is incomplete; import cancelled before database writes")
    if not brand_name.strip():
        raise ValueError("Brand name cannot be blank")
    root = Path(inventory.source_root).resolve()
    report = ImportReport(source_root=str(root), warnings=inventory.warnings)
    discovered = {
        file.relative_path for file in inventory.files if file.category == "brand_document"
    }
    parsed: dict[str, ParsedDocument] = {}
    for relative in sorted(discovered):
        try:
            path = root / relative
            if not path.resolve().is_relative_to(root) or any(
                (root / parent).is_symlink() for parent in [Path(relative), *Path(relative).parents]
            ):
                raise ValueError("Source changed to a symbolic link or escaped the source root")
            parsed[relative] = parse_markdown(path)
        except (OSError, UnicodeError, ValueError) as exc:
            report.failed.append(FileFailure(path=relative, reason=str(exc)))

    try:
        with Session(engine) as session, session.begin():
            brand = session.scalars(
                select(Brand).where(Brand.source_directory == str(root))
            ).one_or_none()
            if brand is None:
                brand = Brand(name=brand_name.strip(), source_directory=str(root))
                session.add(brand)
                session.flush()
            report.brand_id = brand.id
            existing = {
                document.relative_path: document
                for document in session.scalars(
                    select(Document).where(Document.brand_id == brand.id)
                )
            }
            report.missing = sorted(set(existing) - discovered)
            for relative, source in parsed.items():
                document = existing.get(relative)
                if document is None:
                    session.add(
                        Document(
                            brand_id=brand.id,
                            relative_path=relative,
                            title=source.title,
                            raw_text=source.raw_text,
                            content_hash=source.content_hash,
                        )
                    )
                    report.added.append(relative)
                elif document.content_hash != source.content_hash:
                    session.execute(
                        delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
                    )
                    session.execute(
                        delete(SourceAnnotation).where(SourceAnnotation.document_id == document.id)
                    )
                    session.execute(
                        delete(PolicyExtraction).where(PolicyExtraction.document_id == document.id)
                    )
                    document.title = source.title
                    document.raw_text = source.raw_text
                    document.content_hash = source.content_hash
                    report.updated.append(relative)
                else:
                    report.unchanged.append(relative)
    except SQLAlchemyError as exc:
        report.status = "failed"
        report.error = f"Database transaction rolled back ({type(exc).__name__})"
        report.brand_id = None
        report.added.clear()
        report.updated.clear()
        report.unchanged.clear()
        report.missing.clear()
        return report
    report.status = "partial" if report.failed else "success"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand-name", required=True, help="Display name for first import")
    arguments = parser.parse_args()
    if not arguments.brand_name.strip():
        parser.error("--brand-name cannot be blank")
    engine = None
    try:
        settings = get_settings()
        inventory = discover_sources(settings)
        engine = create_database_engine(settings.magi_database_url)
        initialize_database(engine)
        report = ingest_documents(engine, inventory, arguments.brand_name)
        print(report.model_dump_json(indent=2))
        return 0 if report.status == "success" else 1
    except (DiscoveryError, SQLAlchemyError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
