"""Read-only source inventory. Run with python -m app.brand.discovery."""

import json
import os
import stat
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import PROJECT_ROOT, get_settings

Category = Literal["brand_document", "image_candidate", "image_metadata", "campaign_brief", "other"]
CATEGORIES = ("brand_document", "image_candidate", "image_metadata", "campaign_brief", "other")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".svg", ".webp"}


class DiscoveryError(ValueError):
    """The configured source cannot be inventoried."""


class FileRecord(BaseModel):
    relative_path: str
    category: Category
    extension: str
    size_bytes: int
    modified_at: datetime


class DiscoveryWarning(BaseModel):
    path: str
    reason: str


class Inventory(BaseModel):
    source_root: str
    complete: bool = True
    files: list[FileRecord] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[DiscoveryWarning] = Field(default_factory=list)


def discover_folder(
    source_root: str | Path,
    document_directory: str = "brand",
    image_directory: str = "images",
    brief_directory: str = "briefs",
) -> Inventory:
    root = Path(source_root).expanduser()
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root = root.resolve()
    if not root.is_dir():
        raise DiscoveryError(f"Source root is not an accessible directory: {root}")

    boundaries = []
    for value in (document_directory, image_directory, brief_directory):
        path = Path(value)
        if (
            path.is_absolute()
            or not path.parts
            or any(part == ".." or part.startswith(".") for part in path.parts)
        ):
            raise DiscoveryError(f"Source subdirectories must be visible relative paths: {value}")
        boundaries.append(path)
    for index, path in enumerate(boundaries):
        for other in boundaries[index + 1 :]:
            if path == other or path in other.parents or other in path.parents:
                raise DiscoveryError("Source subdirectories must be distinct and non-overlapping")

    inventory = Inventory(source_root=str(root))
    documents, images, briefs = boundaries
    for boundary, required in ((Path(), True), (documents, True), (images, False), (briefs, False)):
        target = root / boundary
        linked = any((root / parent).is_symlink() for parent in [boundary, *boundary.parents])
        try:
            if linked or not target.is_dir():
                raise OSError("missing directory or symbolic link")
            with os.scandir(target):
                pass
        except OSError as exc:
            if required:
                raise DiscoveryError(f"Required directory is unavailable: {target}: {exc}") from exc
            inventory.warnings.append(DiscoveryWarning(path=boundary.as_posix(), reason=str(exc)))

    def scan(directory: Path) -> None:
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            inventory.complete = False
            inventory.warnings.append(
                DiscoveryWarning(
                    path=directory.relative_to(root).as_posix(),
                    reason=f"Cannot scan directory: {exc}",
                )
            )
            return
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(root)
            if entry.name.startswith(".") or entry.is_symlink():
                inventory.warnings.append(
                    DiscoveryWarning(
                        path=relative.as_posix(), reason="Skipped hidden entry or symbolic link"
                    )
                )
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    scan(path)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    inventory.warnings.append(
                        DiscoveryWarning(
                            path=relative.as_posix(), reason="Skipped non-regular file"
                        )
                    )
                    continue
                extension = path.suffix.lower()
                category: Category = "other"
                if relative.is_relative_to(documents) and extension == ".md":
                    category = "brand_document"
                elif relative == images / "metadata.json":
                    category = "image_metadata"
                elif relative.is_relative_to(images) and extension in IMAGE_EXTENSIONS:
                    category = "image_candidate"
                elif relative.is_relative_to(briefs) and extension == ".md":
                    category = "campaign_brief"
                inventory.files.append(
                    FileRecord(
                        relative_path=relative.as_posix(),
                        category=category,
                        extension=extension,
                        size_bytes=info.st_size,
                        modified_at=datetime.fromtimestamp(info.st_mtime, UTC),
                    )
                )
            except OSError as exc:
                inventory.complete = False
                inventory.warnings.append(
                    DiscoveryWarning(
                        path=relative.as_posix(), reason=f"Cannot inspect entry: {exc}"
                    )
                )

    scan(root)
    inventory.files.sort(key=lambda record: record.relative_path)
    inventory.warnings.sort(key=lambda warning: (warning.path, warning.reason))
    counts = Counter(record.category for record in inventory.files)
    inventory.counts = {category: counts[category] for category in CATEGORIES}
    return inventory


def main() -> int:
    settings = get_settings()
    try:
        inventory = discover_folder(
            settings.magi_brand_source_dir,
            settings.magi_document_directory,
            settings.magi_image_directory,
            settings.magi_brief_directory,
        )
    except DiscoveryError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(inventory.model_dump_json(indent=2))
    return 0 if inventory.complete else 1


if __name__ == "__main__":
    sys.exit(main())
