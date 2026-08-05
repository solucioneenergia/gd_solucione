from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import inspect_portal_table
from scripts.create_clean_release_zip import create_clean_release_zip
from scripts.migrate_v1_operational_data import migrate_operational_data
from scripts.validate_engineering_foundation import REQUIRED_FILES as FOUNDATION_REQUIRED_FILES


def _write(path: Path, content: bytes = b"data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _prepare_migration_roots(tmp_path: Path) -> tuple[Path, Path]:
    v1 = tmp_path / "v1"
    v2 = tmp_path / "v2"
    v1.mkdir()
    v2.mkdir()
    _write(v1 / "data/state/pipeline_cdp_state.json", b"state-v1")
    _write(v1 / "data/cache/client_folder_cache.json", b"cache-v1")
    _write(v1 / "data/downloads/2601/orcamento.pdf", b"%PDF-v1")
    _write(v1 / "data/downloads/2601/metadata.json", b"metadata-v1")
    _write(v1 / "data/downloads/ignorar.xlsx", b"spreadsheet")
    _write(v1 / "data/downloads/parcial.tmp", b"temporary")
    _write(v1 / "data/downloads/cookies.json", b"cookie")
    _write(v1 / ".env", b"SECRET=1")
    _write(v1 / "data/auth/storage_state.json", b"cookie")
    _write(v1 / "data/browser_profile_edge/Cookies", b"cookie")
    _write(v1 / "data/edge_cdp_profile/Local State", b"session")
    _write(v1 / "data/logs/old.log", b"old log")
    _write(v1 / "planilha.xlsx", b"real spreadsheet")
    _write(v1 / "outside.pdf", b"%PDF-outside")
    return v1, v2


def test_migration_dry_run_does_not_change_v2(tmp_path: Path) -> None:
    v1, v2 = _prepare_migration_roots(tmp_path)
    before = sorted(path.relative_to(v2).as_posix() for path in v2.rglob("*"))

    report = migrate_operational_data(v1, v2)

    after = sorted(path.relative_to(v2).as_posix() for path in v2.rglob("*"))
    assert report["mode"] == "dry-run"
    assert report["planned_count"] == 4
    assert report["migrated_count"] == 0
    assert before == after == []


def test_migration_apply_copies_only_allowed_data_and_creates_backups(
    tmp_path: Path,
) -> None:
    v1, v2 = _prepare_migration_roots(tmp_path)
    _write(v2 / "data/state/pipeline_cdp_state.json", b"state-v2-old")
    _write(v2 / "data/cache/client_folder_cache.json", b"cache-v2-old")
    _write(v2 / "data/downloads/old/existing.pdf", b"%PDF-v2-old")

    report = migrate_operational_data(v1, v2, apply=True)

    assert (v2 / "data/state/pipeline_cdp_state.json").read_bytes() == b"state-v1"
    assert (v2 / "data/cache/client_folder_cache.json").read_bytes() == b"cache-v1"
    assert (v2 / "data/downloads/2601/orcamento.pdf").read_bytes() == b"%PDF-v1"
    assert (v2 / "data/downloads/2601/metadata.json").read_bytes() == b"metadata-v1"
    assert not (v2 / ".env").exists()
    assert not (v2 / "data/auth").exists()
    assert not (v2 / "data/browser_profile_edge").exists()
    assert not (v2 / "data/edge_cdp_profile").exists()
    assert not (v2 / "planilha.xlsx").exists()
    assert not (v2 / "outside.pdf").exists()
    assert not (v2 / "data/downloads/ignorar.xlsx").exists()
    assert not (v2 / "data/downloads/parcial.tmp").exists()
    assert not (v2 / "data/downloads/cookies.json").exists()
    assert report["backup_count"] == 3
    backup_root = next((v2 / "data/migration_backups").iterdir())
    assert (backup_root / "data/state/pipeline_cdp_state.json").read_bytes() == b"state-v2-old"
    assert (backup_root / "data/cache/client_folder_cache.json").read_bytes() == b"cache-v2-old"
    assert (backup_root / "data/downloads/old/existing.pdf").read_bytes() == b"%PDF-v2-old"
    assert (v2 / "data/logs/migracao_v1_v2.json").is_file()
    assert (v2 / "data/logs/migracao_v1_v2.md").is_file()
    assert report["errors"] == []


def _prepare_release_project(root: Path) -> None:
    for relative in FOUNDATION_REQUIRED_FILES:
        _write(root / relative, b"synthetic")
    _write(root / "README.md", b"readme")
    _write(root / "requirements.txt", b"pytest")
    _write(root / ".env.example", b"DRY_RUN=true")
    _write(root / "pyproject.toml", b'[project]\nversion = "2.0.2"\n')
    _write(root / "desktop_app.py", b"def main(): return 0")
    _write(root / "automacao_gd/__init__.py", b'__version__ = "2.0.2"\n')
    _write(root / "apps/__init__.py", b"")
    _write(root / "apps/desktop/__init__.py", b"")
    _write(root / "apps/desktop/frontend/package.json", b'{"version":"2.0.2"}')
    _write(root / "apps/desktop/frontend/pnpm-lock.yaml", b"synthetic")
    _write(root / "apps/desktop/frontend/dist/index.html", b"synthetic")
    _write(root / "apps/desktop/frontend/dist/assets/index.js", b"synthetic")
    _write(root / "docs/guide.md", b"docs")
    _write(root / "scripts/tool.py", b"print('ok')")
    _write(root / "tests/test_ok.py", b"def test_ok(): pass")
    _write(root / ".env", b"SECRET=1")
    _write(root / "data/auth/storage_state.json", b"token")
    _write(root / "data/browser_profile_edge/Cookies", b"cookie")
    _write(root / "data/edge_cdp_profile/Local State", b"session")
    _write(root / "data/downloads/real.pdf", b"%PDF")
    _write(root / "data/logs/app.log", b"log")
    _write(root / "data/state/state.json", b"state")
    _write(root / "data/cache/cache.json", b"cache")
    _write(root / "planilha.xlsx", b"xlsx")
    _write(root / "automacao_gd/__pycache__/module.pyc", b"bytecode")
    _write(root / ".pytest_cache/cache", b"pytest")
    _write(root / "frontend/node_modules/react/index.js", b"dependency")
    _write(root / "frontend/dist/assets/index.js", b"compiled")
    _write(root / "scratch.tmp", b"temporary")


def test_clean_release_contains_required_files_and_no_sensitive_data(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _prepare_release_project(project)
    output = tmp_path / "release.zip"

    report = create_clean_release_zip(project, output)

    with zipfile.ZipFile(output, "r") as archive:
        names = set(archive.namelist())
    assert {"README.md", "requirements.txt", ".env.example"} <= names
    assert "automacao_gd/__init__.py" in names
    assert "docs/guide.md" in names
    assert "scripts/tool.py" in names
    assert "tests/test_ok.py" in names
    forbidden = {
        ".env",
        "data/auth/storage_state.json",
        "data/browser_profile_edge/Cookies",
        "data/edge_cdp_profile/Local State",
        "data/downloads/real.pdf",
        "data/logs/app.log",
        "data/state/state.json",
        "data/cache/cache.json",
        "planilha.xlsx",
        "automacao_gd/__pycache__/module.pyc",
        ".pytest_cache/cache",
        "frontend/node_modules/react/index.js",
        "frontend/dist/assets/index.js",
        "scratch.tmp",
    }
    assert names.isdisjoint(forbidden)
    assert report["validation"]["valid"] is True
    assert (tmp_path / "release_reports/release_validation.json").is_file()
    assert (tmp_path / "release_reports/release_validation.md").is_file()


def test_inspect_portal_script_uses_factory_and_disconnects_cdp(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    settings = SimpleNamespace(CDP_MODE=True)
    record = SimpleNamespace(
        protocol="2601",
        client_name="Cliente",
        status="CONCLUÍDA",
        entry_date="01/01/2026",
    )

    class FakeAutomation:
        def start_browser(self): calls.append("start")
        def open_portal(self): calls.append("open")
        def wait_manual_login(self): calls.append("login")
        def save_auth_state(self): calls.append("auth")
        def read_current_page_table(self): return [record]
        def save_table_snapshot(self, records): return Path("snapshot.json")
        def close(self): calls.append("close")

    monkeypatch.setattr(inspect_portal_table, "ensure_directories", lambda: None)
    monkeypatch.setattr(inspect_portal_table, "setup_logger", lambda: None)
    monkeypatch.setattr(inspect_portal_table, "get_settings", lambda: settings)
    monkeypatch.setattr(
        inspect_portal_table,
        "create_portal_automation",
        lambda effective: FakeAutomation(),
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    inspect_portal_table.main()

    output = capsys.readouterr().out
    assert calls == ["start", "open", "login", "auth", "close"]
    assert "Edge permanecerá aberto" in output


@pytest.mark.parametrize(
    "script_name",
    [
        "inspect_portal_table.py",
        "connect_existing_edge.py",
        "download_completed_budgets_cdp.py",
        "process_first_solicitation_cdp.py",
    ],
)
def test_portal_scripts_route_browser_lifecycle_through_factory(
    script_name: str,
) -> None:
    source = (Path(__file__).parents[1] / "scripts" / script_name).read_text(
        encoding="utf-8"
    )

    assert "create_portal_automation" in source
    assert "chromium.launch(" not in source
    assert "launch_persistent_context" not in source
