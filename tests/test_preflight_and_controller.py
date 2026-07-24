from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from automacao_gd.application.contracts import OperationResult
from automacao_gd.application.preflight import run_preflight
from automacao_gd.infrastructure.config import Settings
from automacao_gd.presentation.controller import ApplicationController


def _ready_settings(tmp_path: Path, **overrides) -> Settings:
    workbook = tmp_path / "planilha.xlsx"
    if not workbook.exists():
        wb = Workbook()
        wb.save(workbook)
        wb.close()
    clients = tmp_path / "clientes"
    clients.mkdir(exist_ok=True)
    values = {
        "APP_ENV": "test",
        "PORTAL_GD_URL": "https://example.com/",
        "PLANILHA_PATH": workbook,
        "CLIENTES_ROOT": clients,
        "DOWNLOADS_DIR": tmp_path / "downloads",
        "LOGS_DIR": tmp_path / "logs",
        "AUTH_STATE_PATH": tmp_path / "auth" / "state.json",
        "BROWSER_PROFILE_DIR": tmp_path / "browser",
        "CDP_ENDPOINT": "http://127.0.0.1:9222",
        "APPLY_EXCEL": True,
        "APPLY_ARCHIVE": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_preflight_ready_for_valid_test_environment(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path)
    report = run_preflight(settings, real_run=False, require_cdp=True)
    assert report.ready is True
    assert report.blocking_errors == []


def test_preflight_blocks_missing_workbook(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path, PLANILHA_PATH=tmp_path / "missing.xlsx")
    report = run_preflight(settings)
    assert report.ready is False
    assert any("Planilha não encontrada" in message for message in report.blocking_errors)


def test_preflight_blocks_remote_cdp_when_required(tmp_path: Path) -> None:
    settings = _ready_settings(
        tmp_path,
        CDP_MODE=False,
        CDP_ENDPOINT="http://10.0.0.8:9222",
        ALLOW_REMOTE_CDP=False,
    )
    report = run_preflight(settings, require_cdp=True)
    assert report.ready is False
    assert any("CDP remoto" in message for message in report.blocking_errors)


def test_controller_converts_exception_into_operation_result(tmp_path: Path) -> None:
    controller = ApplicationController(_ready_settings(tmp_path))
    with patch(
        "automacao_gd.application.use_cases.process_downloads.ProcessDownloadedPdfsUseCase.execute",
        side_effect=RuntimeError("falha controlada"),
    ):
        result = controller.process_downloads(dry_run=True)
    assert isinstance(result, OperationResult)
    assert result.success is False
    assert result.payload["error"] == "falha controlada"


def test_controller_returns_success_payload(tmp_path: Path) -> None:
    controller = ApplicationController(_ready_settings(tmp_path))
    with patch(
        "automacao_gd.application.use_cases.process_downloads.ProcessDownloadedPdfsUseCase.execute",
        return_value={"total_pdfs": 2},
    ):
        result = controller.process_downloads(dry_run=True)
    assert result.success is True
    assert result.payload["total_pdfs"] == 2
