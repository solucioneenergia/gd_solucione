from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from automacao_gd.application.contracts import OperationError, ProgressEvent
from automacao_gd.infrastructure.config import Settings
from automacao_gd.presentation.operational_output import format_operation_error

from apps.desktop.bridge import file_bridge
from apps.desktop.bridge.automation_bridge import (
    AutomationBridge,
    build_production_confirmation,
)


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {
        "APP_ENV": "test",
        "PORTAL_GD_URL": "https://example.com/",
        "PLANILHA_PATH": tmp_path / "planilha.xlsx",
        "CLIENTES_ROOT": tmp_path / "clientes",
        "DOWNLOADS_DIR": tmp_path / "downloads",
        "LOGS_DIR": tmp_path / "logs",
        "AUTH_STATE_PATH": tmp_path / "auth" / "state.json",
        "BROWSER_PROFILE_DIR": tmp_path / "browser",
        "CDP_ENDPOINT": "http://127.0.0.1:9222",
        "CDP_MODE": True,
        "DRY_RUN": True,
        "MAX_COMPLETED_TO_PROCESS": 7,
        "MAX_PORTAL_PAGES": 3,
        "APPLY_EXCEL": False,
        "APPLY_ARCHIVE": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_dashboard_reads_mode_and_batch_from_settings(tmp_path: Path) -> None:
    bridge = AutomationBridge(_settings(tmp_path), run_async=False)

    payload = json.loads(bridge.get_dashboard_data())

    assert payload["mode"] == "Simulação"
    assert payload["max_completed_to_process"] == 7
    assert payload["batch_label"] == "7 protocolos"
    assert payload["max_portal_pages"] == 3


def test_test_cdp_connection_updates_dashboard_connected_with_mock(tmp_path: Path) -> None:
    bridge = AutomationBridge(
        _settings(tmp_path),
        runners={"test_cdp_connection": lambda: {"ready": True}},
        run_async=False,
    )
    dashboards: list[dict] = []
    bridge.dashboardChanged.connect(dashboards.append)

    bridge.test_cdp_connection()

    assert dashboards[-1]["cdp_status"] == "Conectado"


def test_test_cdp_connection_updates_dashboard_error_with_mock(tmp_path: Path) -> None:
    def fail() -> None:
        raise ConnectionError("CDP indisponível")

    bridge = AutomationBridge(
        _settings(tmp_path),
        runners={"test_cdp_connection": fail},
        run_async=False,
    )
    dashboards: list[dict] = []
    bridge.dashboardChanged.connect(dashboards.append)

    bridge.test_cdp_connection()

    assert dashboards[-1]["cdp_status"] == "Erro"


def test_run_dry_run_uses_worker_progress_and_updates_recent_protocols(tmp_path: Path) -> None:
    def runner(progress_callback=None) -> dict:
        progress_callback(
            ProgressEvent(
                overall_percent=42,
                stage_percent=80,
                protocol_percent=50,
                stage="update_excel",
                protocol="2600000000",
                message="Atualizando planilha",
            )
        )
        return {
            "protocols": [
                {
                    "protocol": "2600000000",
                    "client": "Cliente Teste",
                    "pdf": "Reutilizado",
                    "excel": "simulation",
                    "archive": "simulação",
                    "status": "Simulação",
                }
            ]
        }

    bridge = AutomationBridge(_settings(tmp_path), runners={"run_dry_run": runner}, run_async=False)
    progress: list[dict] = []
    protocols: list[list[dict]] = []
    bridge.progressChanged.connect(progress.append)
    bridge.protocolsChanged.connect(protocols.append)

    bridge.run_dry_run()

    assert progress[-1]["overall_percent"] == 42
    assert protocols[-1][0]["protocol"] == "2600000000"
    assert protocols[-1][0]["status"] == "Simulação"


def test_production_confirmation_is_exact(tmp_path: Path) -> None:
    calls = 0

    def production(**_kwargs) -> dict:
        nonlocal calls
        calls += 1
        return {"success": True}

    bridge = AutomationBridge(
        _settings(tmp_path, APP_ENV="production", MAX_COMPLETED_TO_PROCESS=5),
        runners={"run_production": production},
        run_async=False,
    )

    for invalid in ["", "SIM", "sim, executar produção", "SIM, EXECUTAR PRODUÇÃO "]:
        bridge.run_production_confirmed(invalid)
    assert calls == 0

    bridge.run_production_confirmed(
        build_production_confirmation(bridge.settings, operation="pipeline")
    )
    assert calls == 1


def test_operation_error_is_rendered_without_traceback_and_sanitized() -> None:
    error = OperationError(
        run_id="run",
        protocol="2600000000",
        stage="Atualização da planilha",
        code="EXCEL_LOCKED",
        user_message="Planilha aberta. contato pessoa@example.com telefone: 81999999999",
        technical_cause="Traceback bruto não deve aparecer",
        recoverable=True,
        action_taken="Linha não atualizada.",
        suggested_action="Feche a planilha e execute novamente.",
    )

    rendered = format_operation_error(error)

    assert "Traceback bruto" not in rendered
    assert "pessoa@example.com" not in rendered
    assert "81999999999" not in rendered
    assert "Feche a planilha" in rendered


def test_file_bridge_opens_specific_report_and_handles_missing_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    settings.logs_dir_path.mkdir(parents=True)
    report = settings.logs_dir_path / "pipeline_cdp_completo.md"
    report.write_text("# relatório", encoding="utf-8")
    opened: list[Path] = []
    monkeypatch.setattr(file_bridge, "_open_with_platform_default", opened.append)

    bridge = AutomationBridge(settings, run_async=False)
    failures: list[str] = []
    bridge.operationFailed.connect(failures.append)

    assert bridge.open_pipeline_report() is True
    assert opened == [report]
    assert bridge.open_processing_report() is False
    assert failures


def test_recent_protocols_are_loaded_from_sanitized_report(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.logs_dir_path.mkdir(parents=True)
    (settings.logs_dir_path / "pipeline_cdp_completo.json").write_text(
        json.dumps(
            {
                "protocols": [
                    {
                        "protocol": "2600001104",
                        "client": "Cliente pessoa@example.com",
                        "pdf": "Baixado",
                        "excel": "update_existing",
                        "archive": "Entrada",
                        "status": "OK",
                        "raw_text": "não deve sair",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bridge = AutomationBridge(settings, run_async=False)

    payload = json.loads(bridge.get_recent_protocols())

    assert payload["protocols"][0]["protocol"] == "2600001104"
    assert "pessoa@example.com" not in repr(payload)
    assert "raw_text" not in repr(payload)


def test_cleanup_dry_run_uses_runner_without_deleting(tmp_path: Path) -> None:
    target = tmp_path / "temp.tmp"
    target.write_text("temp", encoding="utf-8")
    bridge = AutomationBridge(
        _settings(tmp_path),
        runners={
            "run_cleanup_dry_run": lambda: {
                "dry_run": True,
                "deleted_count": 0,
                "candidates": [str(target)],
            }
        },
        run_async=False,
    )

    bridge.run_cleanup_dry_run()

    assert target.exists()


def test_frontend_sources_do_not_reference_forbidden_runtime_dependencies() -> None:
    forbidden = [
        "playwright",
        "openpyxl",
        "cdp_service",
        "excel.service",
        "Z:\\Clientes",
    ]
    for path in Path("apps/desktop/frontend").rglob("*"):
        if path.is_file() and path.suffix in {".html", ".css", ".js", ".ts", ".tsx"}:
            source = path.read_text(encoding="utf-8")
            assert not any(item in source for item in forbidden), path
