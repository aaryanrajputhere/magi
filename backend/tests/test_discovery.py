import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.brand.discovery import DiscoveryError, discover_folder
from app.config import PROJECT_ROOT


def test_package_inventory():
    result = discover_folder(PROJECT_ROOT / "wickhold-candidate-package/candidate")
    assert result.complete
    assert result.counts == {
        "brand_document": 83,
        "image_candidate": 133,
        "image_metadata": 1,
        "campaign_brief": 3,
        "other": 1,
    }
    assert len(result.files) == 221
    assert not result.warnings
    assert sum(record.extension == ".svg" for record in result.files) == 11
    assert any(record.relative_path == "brand/README_start_here.md" for record in result.files)


def test_custom_boundaries_and_nested_files(tmp_path):
    for folder in ("docs/nested", "art", "campaigns"):
        (tmp_path / folder).mkdir(parents=True)
    for name in (
        "docs/nested/z.MD",
        "docs/old.md",
        "art/example.PNG",
        "art/metadata.json",
        "campaigns/launch.md",
        "README.md",
        "docs/unsupported.pdf",
    ):
        (tmp_path / name).touch()
    result = discover_folder(tmp_path, "docs", "art", "campaigns")
    assert result.counts == {
        "brand_document": 2,
        "image_candidate": 1,
        "image_metadata": 1,
        "campaign_brief": 1,
        "other": 2,
    }
    paths = [file.relative_path for file in result.files]
    assert paths == sorted(paths)
    assert result == discover_folder(tmp_path, "docs", "art", "campaigns")


def test_missing_required_and_optional_folders(tmp_path):
    with pytest.raises(DiscoveryError):
        discover_folder(tmp_path / "missing")
    with pytest.raises(DiscoveryError):
        discover_folder(tmp_path)
    (tmp_path / "brand").mkdir()
    result = discover_folder(tmp_path)
    assert result.complete
    assert len(result.warnings) == 2


def test_hidden_entries_and_links_are_skipped(tmp_path):
    (tmp_path / "brand/.hidden").mkdir(parents=True)
    (tmp_path / "brand/.hidden/secret.md").touch()
    (tmp_path / "brand/.secret.md").touch()
    (tmp_path / "brand/link.md").symlink_to(tmp_path / "outside.md")
    (tmp_path / "brand/loop").symlink_to(tmp_path, target_is_directory=True)
    result = discover_folder(tmp_path)
    assert not result.files
    assert len([w for w in result.warnings if "Skipped" in w.reason]) == 4


@pytest.mark.parametrize(
    "folders",
    [
        ("../outside", "images", "briefs"),
        ("brand", "brand/images", "briefs"),
        ("brand", "images", "images"),
        ("/absolute", "images", "briefs"),
    ],
)
def test_invalid_boundaries(tmp_path, folders):
    with pytest.raises(DiscoveryError):
        discover_folder(tmp_path, *folders)


def test_directory_error_marks_inventory_incomplete(tmp_path):
    (tmp_path / "brand/broken").mkdir(parents=True)
    real_scandir = os.scandir

    def scandir(path):
        if Path(path).name == "broken":
            raise PermissionError("test access denied")
        return real_scandir(path)

    with patch("app.brand.discovery.os.scandir", side_effect=scandir):
        result = discover_folder(tmp_path)
    assert not result.complete
    assert any(w.path == "brand/broken" for w in result.warnings)


def test_relative_root_uses_project_directory(tmp_path, monkeypatch):
    (tmp_path / "source/brand").mkdir(parents=True)
    monkeypatch.setattr("app.brand.discovery.PROJECT_ROOT", tmp_path)
    assert discover_folder("source").source_root == str(tmp_path / "source")
