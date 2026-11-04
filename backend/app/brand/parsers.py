"""Lossless Markdown loading with a separate title extraction view."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from markdown_it import MarkdownIt


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    content_hash: str
    raw_text: str


def markdown_title(text: str, fallback: str) -> str:
    view = text.removeprefix("\ufeff")
    lines = view.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
        if end is not None:
            view = "".join(lines[end + 1 :])
    tokens = MarkdownIt("commonmark").parse(view)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h1" and token.level == 0:
            inline = tokens[index + 1]
            title = "".join(
                child.content if child.type not in {"softbreak", "hardbreak"} else " "
                for child in (inline.children or [])
                if child.type in {"text", "code_inline", "image", "softbreak", "hardbreak"}
            ).strip()
            if title:
                return title
    return fallback


def parse_markdown(path: Path) -> ParsedDocument:
    content = path.read_bytes()
    text = content.decode("utf-8", errors="strict")
    return ParsedDocument(
        title=markdown_title(text, path.stem),
        content_hash=sha256(content).hexdigest(),
        raw_text=text,
    )
