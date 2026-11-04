"""Build exact-source Markdown chunks without model calls or search indexing."""

import argparse
import json
import re
import sys
from bisect import bisect_right
from itertools import pairwise

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import create_database_engine, initialize_database
from app.models import Brand, Document, DocumentChunk

CHUNKER_VERSION = "markdown-blocks-v2"
DEFAULT_TARGET_CHARS = 2400


class ChunkDraft(BaseModel):
    position: int
    heading_path: list[str]
    content: str
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    oversized: bool


def chunk_markdown(text: str, target_chars: int = DEFAULT_TARGET_CHARS) -> list[ChunkDraft]:
    if target_chars < 1:
        raise ValueError("target_chars must be positive")
    if not text.strip():
        return []
    # markdown-it normalizes CRLF and CR internally. These offsets retain the
    # corresponding original characters, including original line endings.
    line_starts = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", text))]
    view = text.removeprefix("\ufeff")
    view_lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", view)
    if view_lines and view_lines[-1] == "":
        view_lines.pop()
    if view_lines and view_lines[0].strip() == "---":
        end = next(
            (i for i in range(1, len(view_lines)) if view_lines[i].strip() in {"---", "..."}), None
        )
        if end is not None:
            view = "".join("\n" for _ in view_lines[: end + 1]) + "".join(view_lines[end + 1 :])
    tokens = MarkdownIt("commonmark").enable("table").parse(view)
    boundaries = {0, len(text)}
    sections: list[tuple[int, list[str]]] = [(0, [])]
    headings: list[tuple[int, str]] = []
    for index, token in enumerate(tokens):
        if token.level != 0 or token.map is None or token.nesting == -1:
            continue
        offset = line_starts[token.map[0]]
        boundaries.add(offset)
        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline = tokens[index + 1]
            title = "".join(
                child.content if child.type not in {"softbreak", "hardbreak"} else " "
                for child in inline.children or []
                if child.type in {"text", "code_inline", "image", "softbreak", "hardbreak"}
            ).strip()
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            path = [heading for _, heading in headings]
            if sections[-1][0] == offset:
                sections[-1] = (offset, path)
            else:
                sections.append((offset, path))
    # Every interval between top-level block starts belongs to exactly one
    # section. Gaps (comments, reference definitions and whitespace) are retained.
    result: list[ChunkDraft] = []

    def emit(start: int, end: int, heading_path: list[str]) -> None:
        content = text[start:end]
        if not content.strip():
            return
        result.append(
            ChunkDraft(
                position=len(result),
                heading_path=heading_path,
                content=content,
                start_offset=start,
                end_offset=end,
                start_line=bisect_right(line_starts, start),
                end_line=bisect_right(line_starts, end - 1),
                oversized=len(content) > target_chars,
            )
        )

    ordered = sorted(boundaries)
    for index, (start, path) in enumerate(sections):
        end = sections[index + 1][0] if index + 1 < len(sections) else len(text)
        points = [point for point in ordered if start <= point < end] + [end]
        chunk_start = start
        chunk_end = start
        for block_start, block_end in pairwise(points):
            if chunk_end > chunk_start and block_end - chunk_start > target_chars:
                emit(chunk_start, chunk_end, path)
                chunk_start = block_start
            chunk_end = block_end
        emit(chunk_start, chunk_end, path)
    return result


class OversizedReference(BaseModel):
    document_id: str
    relative_path: str
    chunk_id: str
    position: int
    characters: int


class ChunkReport(BaseModel):
    brand_id: str
    status: str = "success"
    processed: int = 0
    rebuilt: int = 0
    unchanged: int = 0
    empty: int = 0
    total_chunks: int = 0
    oversized: list[OversizedReference] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)


def build_chunks(
    engine: Engine, brand_id: str, target_chars: int = DEFAULT_TARGET_CHARS
) -> ChunkReport:
    if target_chars < 1:
        raise ValueError("target_chars must be positive")
    report = ChunkReport(brand_id=brand_id)
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
                existing = list(
                    session.scalars(
                        select(DocumentChunk)
                        .where(DocumentChunk.document_id == document.id)
                        .order_by(DocumentChunk.position)
                    )
                )
                if not document.raw_text.strip():
                    report.empty += 1
                    if existing:
                        session.execute(
                            delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
                        )
                        report.rebuilt += 1
                    else:
                        report.unchanged += 1
                    continue
                if existing and all(
                    chunk.source_hash == document.content_hash
                    and chunk.chunker_version == CHUNKER_VERSION
                    and chunk.target_chars == target_chars
                    for chunk in existing
                ):
                    chunks = existing
                    report.unchanged += 1
                else:
                    drafts = chunk_markdown(document.raw_text, target_chars)
                    session.execute(
                        delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
                    )
                    chunks = [
                        DocumentChunk(
                            document_id=document.id,
                            source_hash=document.content_hash,
                            chunker_version=CHUNKER_VERSION,
                            target_chars=target_chars,
                            **draft.model_dump(),
                        )
                        for draft in drafts
                    ]
                    session.add_all(chunks)
                    session.flush()
                    report.rebuilt += 1
                report.total_chunks += len(chunks)
                for chunk in chunks:
                    if chunk.oversized:
                        report.oversized.append(
                            OversizedReference(
                                document_id=document.id,
                                relative_path=document.relative_path,
                                chunk_id=chunk.id,
                                position=chunk.position,
                                characters=len(chunk.content),
                            )
                        )
    except SQLAlchemyError as exc:
        # A rolled-back run must not claim that any rebuild was persisted.
        return ChunkReport(
            brand_id=brand_id,
            status="failed",
            failures=[f"Transaction rolled back ({type(exc).__name__})"],
        )
    return report


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand-id", required=True)
    parser.add_argument("--target-chars", type=positive_int, default=DEFAULT_TARGET_CHARS)
    arguments = parser.parse_args()
    engine = None
    try:
        engine = create_database_engine(get_settings().magi_database_url)
        initialize_database(engine)
        report = build_chunks(engine, arguments.brand_id, arguments.target_chars)
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
