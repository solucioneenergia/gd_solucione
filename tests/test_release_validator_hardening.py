from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.validate_engineering_foundation import REQUIRED_FILES as FOUNDATION_REQUIRED_FILES
from scripts.validate_release_zip import main as validate_release_main
from scripts.validate_release_zip import validate_release_zip
from scripts.create_clean_release_zip import should_exclude_from_release


VERSION = "2.0.2"
HEAD_SHA = "a" * 40


def _entries(extra: dict[str, bytes] | None = None) -> dict[str, bytes]:
    entries = {
        "README.md": b"synthetic",
        "requirements.txt": b"synthetic",
        ".env.example": b"SYNTHETIC_VALUE=",
        "pyproject.toml": b'[project]\nversion = "2.0.2"\n',
        "desktop_app.py": b"synthetic",
        "automacao_gd/__init__.py": b'__version__ = "2.0.2"\n',
        "apps/__init__.py": b"synthetic",
        "apps/desktop/__init__.py": b"synthetic",
        "apps/desktop/frontend/package.json": b'{"version":"2.0.2"}',
        "apps/desktop/frontend/pnpm-lock.yaml": b"synthetic",
        "apps/desktop/frontend/dist/index.html": b"synthetic",
        "apps/desktop/frontend/dist/assets/index.js": b"synthetic",
        "docs/guide.md": b"synthetic",
        "scripts/tool.py": b"synthetic",
        "tests/test_ok.py": b"synthetic",
    }
    for relative in FOUNDATION_REQUIRED_FILES:
        entries.setdefault(relative, b"synthetic")
    entries.update(extra or {})
    file_hashes = {
        name.replace("\\", "/"): hashlib.sha256(content).hexdigest()
        for name, content in entries.items()
    }
    source_files = sorted(
        name
        for name in file_hashes
        if name.startswith("apps/desktop/frontend/")
        and not name.startswith("apps/desktop/frontend/dist/")
    )
    source_digest = hashlib.sha256(
        "\n".join(f"{name}:{file_hashes[name]}" for name in source_files).encode()
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "head_sha": HEAD_SHA,
        "branch": "synthetic",
        "detached": False,
        "tags": [],
        "describe": HEAD_SHA[:7],
        "versions": {
            "project": VERSION,
            "python_package": VERSION,
            "frontend": VERSION,
        },
        "privacy_scan": {"valid": True, "finding_count": 0},
        "generated_artifacts": sorted(
            name.replace("\\", "/")
            for name in entries
            if name.replace("\\", "/").startswith("apps/desktop/frontend/dist/")
        ),
        "generated_source": {
            "root": "apps/desktop/frontend",
            "source_files": source_files,
            "source_digest": source_digest,
        },
        "files": file_hashes,
    }
    entries["release-manifest.json"] = json.dumps(
        manifest, sort_keys=True
    ).encode("utf-8")
    return entries


def _zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    target = tmp_path / "synthetic-release.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo("synthetic-entry")
            info.filename = name
            archive.writestr(info, content)
    return target


def test_valid_synthetic_release_is_accepted(tmp_path: Path) -> None:
    report = validate_release_zip(
        _zip(tmp_path, _entries()),
        expected_version=VERSION,
        expected_head_sha=HEAD_SHA,
    )

    assert report["valid"] is True
    assert report["errors"] == []


@pytest.mark.parametrize(
    "forbidden_path",
    [
        ".env.production",
        "nested.zip",
        "profiles/Default/Local State",
        "apps/desktop/frontend/node_modules/pkg/index.js",
        "data/runtime/state.json",
        "outputs/local-backup.zip",
    ],
)
def test_contaminated_release_is_rejected(tmp_path: Path, forbidden_path: str) -> None:
    report = validate_release_zip(
        _zip(tmp_path, _entries({forbidden_path: b"synthetic"})),
        expected_version=VERSION,
    )

    assert report["valid"] is False
    assert report["sensitive_paths"]


def test_release_without_canonical_desktop_is_rejected(tmp_path: Path) -> None:
    entries = _entries()
    del entries["apps/desktop/__init__.py"]

    report = validate_release_zip(_zip(tmp_path, entries), expected_version=VERSION)

    assert report["valid"] is False
    assert "apps/desktop/__init__.py" in report["missing_required"]


def test_release_with_wrong_version_is_rejected(tmp_path: Path) -> None:
    entries = _entries()
    entries["pyproject.toml"] = b'[project]\nversion = "9.9.9"\n'

    report = validate_release_zip(_zip(tmp_path, entries), expected_version=VERSION)

    assert report["valid"] is False
    assert report["version"] == "9.9.9"


def test_release_with_windows_separators_is_read_through_raw_zip_info(
    tmp_path: Path,
) -> None:
    entries = _entries()
    windows_entries = {name.replace("/", "\\"): value for name, value in entries.items()}

    report = validate_release_zip(
        _zip(tmp_path, windows_entries),
        expected_version=VERSION,
        expected_head_sha=HEAD_SHA,
    )

    assert report["valid"] is True


def test_release_rejects_normalized_path_collision(tmp_path: Path) -> None:
    entries = _entries()
    entries["docs\\guide.md"] = entries["docs/guide.md"]

    report = validate_release_zip(_zip(tmp_path, entries), expected_version=VERSION)

    assert report["valid"] is False
    assert "duplicate_path" in report["sensitive_paths"]


def test_release_rejects_manifest_sha_mismatch(tmp_path: Path) -> None:
    report = validate_release_zip(
        _zip(tmp_path, _entries()),
        expected_version=VERSION,
        expected_head_sha="b" * 40,
    )

    assert report["valid"] is False
    assert "RELEASE_PROVENANCE_INVALID" in report["errors"]


def test_release_rejects_missing_generated_source_binding(tmp_path: Path) -> None:
    entries = _entries()
    manifest = json.loads(entries["release-manifest.json"])
    del manifest["generated_source"]
    entries["release-manifest.json"] = json.dumps(manifest).encode("utf-8")

    report = validate_release_zip(_zip(tmp_path, entries), expected_version=VERSION)

    assert report["valid"] is False
    assert "RELEASE_PROVENANCE_INVALID" in report["errors"]


def test_release_validator_cli_requires_expected_head_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _zip(tmp_path, _entries())
    monkeypatch.setattr(sys, "argv", ["validate_release_zip.py", str(archive)])

    with pytest.raises(SystemExit) as exc_info:
        validate_release_main()

    assert exc_info.value.code == 2


def test_release_rejects_content_that_does_not_match_manifest(tmp_path: Path) -> None:
    entries = _entries()
    entries["README.md"] = b"tampered after manifest"

    report = validate_release_zip(_zip(tmp_path, entries), expected_version=VERSION)

    assert report["valid"] is False
    assert "RELEASE_CONTENT_MISMATCH" in report["errors"]


def test_shareable_validation_reports_do_not_persist_absolute_paths(
    tmp_path: Path,
) -> None:
    reports = tmp_path / "reports"
    validate_release_zip(
        _zip(tmp_path, _entries()),
        reports_dir=reports,
        expected_version=VERSION,
    )

    serialized = (reports / "release_validation.json").read_text(encoding="utf-8")
    serialized += (reports / "release_validation.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert str(tmp_path).replace("\\", "/") not in serialized


def test_only_declared_synthetic_document_fixtures_are_allowed() -> None:
    assert not should_exclude_from_release(
        Path("tests/fixtures/synthetic/synthetic_document.pdf")
    )
    assert should_exclude_from_release(Path("tests/fixtures/document.pdf"))
    assert should_exclude_from_release(Path("tests/fixtures/synthetic/customer_document.pdf"))


def test_legacy_frontend_is_preserved_in_repository_but_not_shipped() -> None:
    assert should_exclude_from_release(Path("frontend/src/App.tsx"))
    assert not should_exclude_from_release(
        Path("apps/desktop/frontend/src/App.tsx")
    )
