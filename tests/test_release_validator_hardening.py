from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts.validate_engineering_foundation import REQUIRED_FILES as FOUNDATION_REQUIRED_FILES
from scripts.validate_release_zip import validate_release_zip
from scripts.create_clean_release_zip import should_exclude_from_release


VERSION = "2.0.2"


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
    return entries


def _zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    target = tmp_path / "synthetic-release.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return target


def test_valid_synthetic_release_is_accepted(tmp_path: Path) -> None:
    report = validate_release_zip(_zip(tmp_path, _entries()), expected_version=VERSION)

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
    entries = _entries({"pyproject.toml": b'[project]\nversion = "9.9.9"\n'})

    report = validate_release_zip(_zip(tmp_path, entries), expected_version=VERSION)

    assert report["valid"] is False
    assert report["version"] == "9.9.9"


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
