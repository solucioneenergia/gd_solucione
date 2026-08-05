from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import tomllib
import zipfile
from pathlib import Path

import pytest

from automacao_gd.infrastructure.config import Settings


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "apps" / "desktop" / "frontend"
EXPECTED_VERSION = "2.0.2"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "production",
        "PORTAL_GD_URL": "https://example.invalid/",
        "PLANILHA_PATH": tmp_path / "synthetic.xlsx",
        "CLIENTES_ROOT": tmp_path / "synthetic-clients",
        "DOWNLOADS_DIR": tmp_path / "downloads",
        "LOGS_DIR": tmp_path / "logs",
        "AUTH_STATE_PATH": tmp_path / "auth" / "state.json",
        "BROWSER_PROFILE_DIR": tmp_path / "browser",
        "CDP_ENDPOINT": "http://127.0.0.1:9222",
        "CDP_MODE": True,
        "DRY_RUN": True,
        "MAX_COMPLETED_TO_PROCESS": 3,
        "APPLY_EXCEL": False,
        "APPLY_ARCHIVE": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_candidate_version_is_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    namespace: dict[str, object] = {}
    exec((ROOT / "automacao_gd" / "__init__.py").read_text(encoding="utf-8"), namespace)

    assert project["project"]["version"] == EXPECTED_VERSION
    assert namespace["__version__"] == EXPECTED_VERSION
    assert package["version"] == EXPECTED_VERSION


def test_test_runner_is_not_a_runtime_dependency() -> None:
    runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").lower()

    assert "pytest" not in runtime
    assert "pytest>=9.0.3,<10.0" in development


def test_canonical_adr_and_spec_are_current() -> None:
    adr = (ROOT / "docs" / "adr" / "0005-canonical-desktop-path.md").read_text(
        encoding="utf-8"
    )
    spec = (ROOT / "specs" / "desktop_production_readiness.md").read_text(
        encoding="utf-8"
    )

    assert "Status: aceita" in adr
    assert "apps/desktop" in adr
    assert "em homologação final controlada" in spec
    assert "implementação futura" not in spec.split("## 1. Problema", maxsplit=1)[0]


def test_setuptools_declares_canonical_desktop_and_package_data() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "apps*" in project["tool"]["setuptools"]["packages"]["find"]["include"]
    package_data = project["tool"]["setuptools"]["package-data"]["apps.desktop"]
    assert any("frontend/dist" in pattern for pattern in package_data)
    assert any("frontend/static" in pattern for pattern in package_data)
    assert (ROOT / "apps" / "__init__.py").is_file()


def test_frontend_has_pinned_pnpm_lock_build_and_tests() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))

    assert package["packageManager"] == "pnpm@11.9.0"
    assert (FRONTEND / "pnpm-lock.yaml").is_file()
    assert package["scripts"]["build"]
    assert package["scripts"]["test"]


def test_windows_build_uses_immutable_canonical_frontend_and_bundles_assets() -> None:
    source = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    assert "pnpm install --frozen-lockfile" in source
    assert "pnpm test" in source
    assert "pnpm build" in source
    assert '"apps\\desktop\\frontend"' in source
    assert 'apps/desktop/frontend/dist' in source
    assert 'apps/desktop/frontend/static' in source


def test_runtime_frontend_has_empty_state_and_no_operational_identifiers() -> None:
    table = (FRONTEND / "src" / "components" / "RecentProtocolsTable.tsx").read_text(
        encoding="utf-8"
    )
    assert "Nenhum protocolo recente encontrado" in table
    assert "recentProtocols" not in table

    runtime_files = [
        *FRONTEND.joinpath("src").rglob("*.ts"),
        *FRONTEND.joinpath("src").rglob("*.tsx"),
        *FRONTEND.joinpath("static").glob("*.html"),
        *FRONTEND.joinpath("static").glob("*.js"),
    ]
    for path in runtime_files:
        if path.name.endswith(".fixture.ts"):
            continue
        source = path.read_text(encoding="utf-8")
        assert re.search(r"(?<!\d)\d{8,}(?!\d)", source) is None, path


def test_placeholder_operations_fail_closed(tmp_path: Path) -> None:
    from apps.desktop.bridge.automation_bridge import AutomationBridge

    bridge = AutomationBridge(_settings(tmp_path), run_async=False)
    finished: list[str] = []
    failures: list[str] = []
    bridge.operationFinished.connect(finished.append)
    bridge.operationFailed.connect(failures.append)

    bridge.open_edge_cdp()
    bridge.inspect_portal()
    bridge.run_equipment_reformat_dry_run()

    assert finished == []
    assert len(failures) == 3
    assert all("indisponível nesta versão" in message.casefold() for message in failures)


def test_worker_cancellation_is_cooperative_and_not_success(tmp_path: Path) -> None:
    from apps.desktop.workers.automation_worker import AutomationWorker

    reached: list[str] = []

    def operation(*, progress_callback, cancel_event) -> dict[str, bool]:
        reached.append("critical-section-completed")
        cancel_event.set()
        progress_callback({"stage": "next-step"})
        return {"success": True}

    worker = AutomationWorker(operation, run_async=False)
    finished: list[object] = []
    cancelled: list[str] = []
    worker.finished.connect(finished.append)
    worker.cancelled.connect(cancelled.append)

    worker.start()

    assert reached == ["critical-section-completed"]
    assert finished == []
    assert cancelled == ["Operação cancelada."]


def test_bridge_cancellation_reaches_pipeline_progress_boundary(tmp_path: Path) -> None:
    from automacao_gd.application.contracts import ProgressEvent
    from apps.desktop.bridge.automation_bridge import AutomationBridge

    try:
        from PySide6.QtCore import QCoreApplication
    except ImportError:
        application = None
    else:
        application = QCoreApplication.instance() or QCoreApplication([])

    entered = threading.Event()
    release_critical_section = threading.Event()

    def runner(*, progress_callback) -> dict[str, bool]:
        entered.set()
        assert release_critical_section.wait(3)
        progress_callback(ProgressEvent(overall_percent=10, stage_percent=10, stage="next"))
        return {"success": True}

    bridge = AutomationBridge(
        _settings(tmp_path),
        runners={"run_dry_run": runner},
        run_async=True,
    )
    finished: list[str] = []
    cancelled: list[str] = []
    bridge.operationFinished.connect(finished.append)
    bridge.operationCancelled.connect(cancelled.append)

    bridge.run_dry_run()
    assert entered.wait(3)
    worker = bridge.current_worker
    assert worker is not None
    bridge.stop_current_operation()
    release_critical_section.set()
    assert worker._thread is not None
    worker._thread.join(3)
    if application is not None:
        application.processEvents()

    assert finished == []
    assert cancelled == ["run_dry_run"]


def test_production_confirmation_is_bound_to_environment_operation_and_limit(
    tmp_path: Path,
) -> None:
    from apps.desktop.bridge.automation_bridge import (
        build_production_confirmation,
        validate_production_confirmation,
    )

    settings = _settings(tmp_path, MAX_COMPLETED_TO_PROCESS=3)
    expected = build_production_confirmation(settings, operation="pipeline")

    assert validate_production_confirmation(settings, expected, operation="pipeline") is True
    assert validate_production_confirmation(settings, expected, operation="maintenance") is False
    assert (
        validate_production_confirmation(
            settings.model_copy(update={"MAX_COMPLETED_TO_PROCESS": 2}),
            expected,
            operation="pipeline",
        )
        is False
    )
    assert (
        validate_production_confirmation(
            settings.model_copy(update={"APP_ENV": "test"}),
            expected,
            operation="pipeline",
        )
        is False
    )


def test_frontend_url_policy_blocks_external_and_outside_files(tmp_path: Path) -> None:
    from apps.desktop.window import is_authorized_frontend_url

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    local = frontend / "index.html"
    local.write_text("synthetic", encoding="utf-8")
    outside = tmp_path / "outside.html"
    outside.write_text("synthetic", encoding="utf-8")

    assert is_authorized_frontend_url(local.as_uri(), frontend_root=frontend, dev=False)
    assert not is_authorized_frontend_url(outside.as_uri(), frontend_root=frontend, dev=False)
    assert not is_authorized_frontend_url("https://example.invalid/", frontend_root=frontend, dev=False)
    assert not is_authorized_frontend_url("http://127.0.0.1:5173/", frontend_root=frontend, dev=False)
    assert is_authorized_frontend_url(
        "http://127.0.0.1:5173/", frontend_root=frontend, dev=True
    )


def test_bridge_attachment_is_guarded_and_remote_windows_are_blocked() -> None:
    source = (ROOT / "apps" / "desktop" / "window.py").read_text(encoding="utf-8")

    assert "loadFinished.connect(self._on_load_finished)" in source
    assert "is_authorized_frontend_url(" in source
    assert "self._attach_channel()" in source
    assert "self._detach_channel()" in source
    assert "def createWindow" in source
    assert "info.block(True)" in source


@pytest.fixture(scope="session")
def candidate_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    wheel_dir = tmp_path_factory.mktemp("candidate-wheel")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return next(wheel_dir.glob("*.whl"))


def test_built_wheel_contains_apps_and_frontend(candidate_wheel: Path) -> None:
    with zipfile.ZipFile(candidate_wheel) as archive:
        names = set(archive.namelist())

    assert "apps/__init__.py" in names
    assert "apps/desktop/__init__.py" in names
    assert "apps/desktop/frontend/dist/index.html" in names
    assert "apps/desktop/frontend/static/index.html" in names


def test_wheel_installs_and_imports_outside_workspace(
    candidate_wheel: Path, tmp_path: Path
) -> None:
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(candidate_wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [str(python), "-I", "-c", "import desktop_app; import apps.desktop"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
