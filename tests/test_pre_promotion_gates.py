from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_frontend_has_no_operational_identifiers() -> None:
    source_root = ROOT / "frontend" / "src"
    runtime_files = [
        *source_root.rglob("*.ts"),
        *source_root.rglob("*.tsx"),
        *source_root.rglob("*.js"),
        *source_root.rglob("*.jsx"),
    ]

    violations: list[str] = []
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        if re.search(r"(?<!\d)\d{8,}(?!\d)", source):
            violations.append(path.relative_to(ROOT).as_posix())

    assert violations == []


def test_gitleaks_version_is_pinned_for_local_parity() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "GITLEAKS_VERSION: 8.30.1" in workflow
    assert "gitleaks detect --source . --no-banner --redact --verbose --exit-code 1" in workflow


def test_release_candidate_staging_is_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/artifacts/release-candidate/" in ignore


def test_smoke_environment_is_synthetic_and_fail_closed(tmp_path: Path) -> None:
    from automacao_gd.infrastructure.config import Settings
    from scripts.smoke_desktop_executable import build_synthetic_environment

    environment = build_synthetic_environment(tmp_path)
    root = tmp_path.resolve()

    assert environment["APP_ENV"] == "test"
    assert environment["PORTAL_GD_URL"].startswith("https://127.0.0.1:")
    assert environment["DRY_RUN"] == "true"
    assert environment["APPLY_EXCEL"] == "false"
    assert environment["APPLY_ARCHIVE"] == "false"
    assert environment["CDP_MODE"] == "false"
    Settings.model_validate(environment)
    for key in (
        "PLANILHA_PATH",
        "CLIENTES_ROOT",
        "DOWNLOADS_DIR",
        "LOGS_DIR",
        "AUTH_STATE_PATH",
        "BROWSER_PROFILE_DIR",
    ):
        Path(environment[key]).resolve().relative_to(root)


def test_smoke_report_requires_frontend_bridge_empty_and_idle() -> None:
    from scripts.smoke_desktop_executable import smoke_report_is_approved

    approved = {
        "process_started": True,
        "window_created": True,
        "frontend_loaded": True,
        "bridge_initialized": True,
        "empty_state_visible": True,
        "operation_started": False,
        "external_requests": 0,
        "controlled_shutdown": True,
    }

    assert smoke_report_is_approved(approved)
    for key in (
        "window_created",
        "frontend_loaded",
        "bridge_initialized",
        "empty_state_visible",
        "controlled_shutdown",
    ):
        rejected = dict(approved)
        rejected[key] = False
        assert not smoke_report_is_approved(rejected)

    rejected = dict(approved)
    rejected["operation_started"] = True
    assert not smoke_report_is_approved(rejected)

    rejected = dict(approved)
    rejected["external_requests"] = 1
    assert not smoke_report_is_approved(rejected)


def test_smoke_selects_only_canonical_file_target_and_local_cdp() -> None:
    from scripts.smoke_desktop_executable import (
        _open_websocket,
        _select_frontend_target,
    )

    target = {
        "type": "page",
        "url": "file:///C:/synthetic/apps/desktop/frontend/dist/index.html",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/synthetic",
    }

    assert _select_frontend_target([target]) == target
    assert _select_frontend_target([{**target, "url": "https://example.invalid"}]) is None
    with pytest.raises(RuntimeError, match="not local"):
        _open_websocket("ws://example.invalid/devtools/page/test", 0.1)
