"""Collect explicit source statements as unverified, inspectable evidence."""

import argparse
import json
import re
import sys
from collections import defaultdict
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import create_database_engine, initialize_database
from app.models import Brand, Document, DocumentChunk, SourceAnnotation

EXTRACTOR_VERSION = "source-statements-v1"
Category = Literal["owner", "date", "authority", "supersession"]
MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE = rf"(?:\b\d{{4}}(?:-\d{{2}}(?:-\d{{2}})?)?\b|\b{MONTHS}\b)"
RULES: tuple[tuple[Category, str, re.Pattern], ...] = (
    (
        "owner",
        "explicit-owner",
        re.compile(
            r"\b(?:owner\s*:|maintained by\b|(?:sheet|document|guide) (?:is )?kept by\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "date",
        "explicit-document-date",
        re.compile(
            rf"\b(?:last (?:edit(?:ed)?|checked|updated|revised)|issued|revised|reviewed|revision|rev\.)[^\r\n]{{0,45}}?{DATE}",
            re.IGNORECASE,
        ),
    ),
    (
        "authority",
        "explicit-precedence",
        re.compile(
            r"\bcurrent (?:guidelines|policy|source)\s*:|\bthe one to follow\b|\b(?:governs?|takes? precedence|overrides?)\b|\b(?:sheet|document|guide|policy)\b.{0,45}\bto use\b|\bif\b.{0,180}\b(?:different|disagrees?)\b.{0,180}\b(?:out of date|trust|use)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "supersession",
        "explicit-replacement",
        re.compile(r"\b(?:replaces?|supersedes?|superseded by)\b", re.IGNORECASE),
    ),
)


class MetadataCandidate(BaseModel):
    category: Category
    statement: str
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    rule: str
    verification_status: Literal["candidate"] = "candidate"


def extract_source_metadata(text: str) -> list[MetadataCandidate]:
    """Match explicit statements, never infer authority from a filename or year."""
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
    if lines and not lines[-1]:
        lines.pop()
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    view = text.removeprefix("\ufeff")
    if lines and lines[0].removeprefix("\ufeff").strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
        if end is not None:
            # Preserve original line numbers, but do not interpret arbitrary YAML.
            view = "\n" * (end + 1) + "".join(lines[end + 1 :])
    tokens = MarkdownIt("commonmark").enable("table").parse(view)
    quote_depth = 0
    example_section = False
    example_level = 0
    result = []
    seen = set()
    for index, token in enumerate(tokens):
        if token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        elif token.type == "heading_open" and quote_depth == 0:
            level = int(token.tag[1:])
            if example_section and level <= example_level:
                example_section = False
            title = tokens[index + 1].content.strip()
            if title.lower().rstrip(":") in {"good", "bad", "approved", "not approved"} or re.match(
                r"^(?:worked )?examples?(?:\s|:|$)", title, re.IGNORECASE
            ):
                example_section, example_level = True, level
        elif (
            token.type == "paragraph_open" and token.map and not quote_depth and not example_section
        ):
            for line_index in range(*token.map):
                statement = lines[line_index].rstrip("\r\n")
                plain = re.sub(r"[*_`]+", "", statement).strip()
                if not plain or plain.startswith(('"', "'", "“", "‘")):
                    continue
                for category, rule, pattern in RULES:
                    key = (line_index, category)
                    if key not in seen and pattern.search(plain):
                        seen.add(key)
                        result.append(
                            MetadataCandidate(
                                category=category,
                                statement=statement,
                                start_offset=starts[line_index],
                                end_offset=starts[line_index] + len(statement),
                                start_line=line_index + 1,
                                end_line=line_index + 1,
                                rule=rule,
                            )
                        )
    return sorted(result, key=lambda item: (item.start_offset, item.category))


class DocumentMetadata(BaseModel):
    document_id: str
    relative_path: str
    candidates: dict[str, list[MetadataCandidate]]
    unknown_categories: list[Category] = Field(default_factory=list)


class MetadataReport(BaseModel):
    brand_id: str
    status: Literal["success", "failed"] = "success"
    processed: int = 0
    rebuilt: int = 0
    unchanged: int = 0
    documents: list[DocumentMetadata] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)


def build_source_metadata(engine: Engine, brand_id: str) -> MetadataReport:
    report = MetadataReport(brand_id=brand_id)
    try:
        with Session(engine) as session, session.begin():
            if session.get(Brand, brand_id) is None:
                raise ValueError(f"Unknown brand: {brand_id}")
            documents = session.scalars(
                select(Document)
                .where(Document.brand_id == brand_id)
                .order_by(Document.relative_path)
            )
            for document in documents:
                report.processed += 1
                annotation = session.scalar(
                    select(SourceAnnotation).where(SourceAnnotation.document_id == document.id)
                )
                if (
                    annotation
                    and annotation.source_hash == document.content_hash
                    and annotation.extractor_version == EXTRACTOR_VERSION
                ):
                    candidates = [
                        MetadataCandidate.model_validate(item) for item in annotation.candidates
                    ]
                    report.unchanged += 1
                else:
                    candidates = extract_source_metadata(document.raw_text)
                    if annotation is None:
                        annotation = SourceAnnotation(document_id=document.id)
                        session.add(annotation)
                    annotation.source_hash = document.content_hash
                    annotation.extractor_version = EXTRACTOR_VERSION
                    annotation.candidates = [item.model_dump() for item in candidates]
                    report.rebuilt += 1
                grouped = defaultdict(list)
                for candidate in candidates:
                    grouped[candidate.category].append(candidate)
                report.documents.append(
                    DocumentMetadata(
                        document_id=document.id,
                        relative_path=document.relative_path,
                        candidates=dict(grouped),
                        unknown_categories=[
                            category
                            for category in ("owner", "date", "authority", "supersession")
                            if category not in grouped
                        ],
                    )
                )
                if not candidates:
                    report.unknown.append(document.relative_path)
    except SQLAlchemyError as exc:
        return MetadataReport(
            brand_id=brand_id,
            status="failed",
            failures=[f"Transaction rolled back ({type(exc).__name__})"],
        )
    return report


def get_chunk_source_metadata(session: Session, chunk_id: str) -> list[MetadataCandidate]:
    """Expose current document candidates to a chunk without copying metadata."""
    row = session.execute(
        select(Document, DocumentChunk, SourceAnnotation)
        .join(DocumentChunk, DocumentChunk.document_id == Document.id)
        .outerjoin(SourceAnnotation, SourceAnnotation.document_id == Document.id)
        .where(DocumentChunk.id == chunk_id)
    ).one_or_none()
    if row is None:
        raise ValueError(f"Unknown chunk: {chunk_id}")
    document, chunk, annotation = row
    if (
        annotation is None
        or chunk.source_hash != document.content_hash
        or annotation.source_hash != document.content_hash
        or annotation.extractor_version != EXTRACTOR_VERSION
    ):
        return []
    return [MetadataCandidate.model_validate(item) for item in annotation.candidates]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand-id", required=True)
    arguments = parser.parse_args()
    engine = None
    try:
        engine = create_database_engine(get_settings().magi_database_url)
        initialize_database(engine)
        report = build_source_metadata(engine, arguments.brand_id)
        print(report.model_dump_json(indent=2))
        return 0 if report.status == "success" else 1
    except (ValueError, SQLAlchemyError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
