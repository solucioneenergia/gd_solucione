from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

from openpyxl import Workbook

from automacao_gd.application.contracts import OperationResult
from automacao_gd.infrastructure.config import Settings
from automacao_gd.presentation import cli
from automacao_gd.presentation.desktop.web_bridge import (
    AutomationBridge,
    _build_edge_cdp_command,
    build_frontend_protocols,
    build_frontend_summary,
)

try:
    from PySide6.QtCore import QCoreApplication
except ImportError:
    QCoreApplication = None


def _ensure_qt_application():
    if QCoreApplication is None:
        return None
    return QCoreApplication.instance() or QCoreApplication([])


def _settings(tmp_path: Path, **overrides) -> Settings:
    workbook = tmp_path / "planilha.xlsx"
    book = Workbook()
    book.save(workbook)
    book.close()
    clients = tmp_path / "clientes"
    clients.mkdir()
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
        "CDP_MODE": True,
        "DRY_RUN": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeController:
    def __init__(
        self,
        settings: Settings,
        *,
        result: OperationResult | None = None,
        callback=None,
    ) -> None:
        self.settings = settings
        self.result = result or OperationResult(
            True,
            "Pipeline concluído.",
            {"dry_run": True, "total_processed_success": 1},
        )
        self.callback = callback
        self.pipeline_calls = 0

    def run_pipeline(self) -> OperationResult:
        self.pipeline_calls += 1
        if self.callback:
            self.callback()
        return self.result

    def preflight(self, **_kwargs) -> OperationResult:
        return OperationResult(True, "Ambiente pronto.", {})

    def inspect_portal(self, **_kwargs) -> OperationResult:
        return OperationResult(True, "Portal inspecionado.", {"records": []})


def _wait_until(predicate, timeout: float = 3.0) -> None:
    application = _ensure_qt_application()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if application is not None:
            application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Tempo excedido aguardando operação assíncrona.")


def test_automation_bridge_exposes_main_webchannel_methods(tmp_path: Path) -> None:
    bridge = AutomationBridge(FakeController(_settings(tmp_path)))
    expected = {
        "check_environment",
        "open_edge_cdp",
        "test_cdp_connection",
        "inspect_portal",
        "run_pipeline_dry_run",
        "run_pipeline_production",
        "stop_current_operation",
        "open_pipeline_report",
        "open_processing_report",
        "open_downloads_folder",
        "open_logs_folder",
        "open_workbook",
        "open_clients_root",
    }
    assert expected <= set(dir(bridge))


def test_run_pipeline_dry_run_calls_existing_controller(tmp_path: Path) -> None:
    controller = FakeController(_settings(tmp_path))
    bridge = AutomationBridge(controller)
    finished: list[str] = []
    bridge.operationFinished.connect(finished.append)

    bridge.run_pipeline_dry_run()

    _wait_until(lambda: bool(finished))
    assert controller.pipeline_calls == 1
    assert finished == ["pipeline_dry_run"]


def test_run_pipeline_production_requires_explicit_confirmation(tmp_path: Path) -> None:
    controller = FakeController(_settings(tmp_path))
    bridge = AutomationBridge(controller)
    failures: list[str] = []
    bridge.operationFailed.connect(failures.append)

    bridge.run_pipeline_production("não")

    assert controller.pipeline_calls == 0
    assert failures
    assert "SIM" in failures[0]


def test_frontend_payload_is_whitelisted_and_sanitized() -> None:
    payload = {
        "dry_run": True,
        "total_processed_success": 1,
        "raw_text": "conteúdo integral proibido",
        "cpf": "123.456.789-09",
        "email": "pessoa@example.com",
        "telefone": "81999999999",
        "protocol_results": [
            {
                "protocol": "2606184625",
                "client_name": (
                    "CLIENTE | CPF: 123.456.789-09 | "
                    "e-mail: pessoa@example.com | telefone: 81999999999"
                ),
                "download_status": "downloaded",
                "excel_action": "update_existing",
                "archive_state": "archived",
                "raw_text": "não enviar",
                "phone": "81999999999",
            }
        ],
    }
    result = OperationResult(True, "Concluído.", payload)

    summary = build_frontend_summary("pipeline_dry_run", result)
    protocols = build_frontend_protocols(payload)
    rendered = repr((summary, protocols))

    assert protocols[0]["protocol"] == "2606184625"
    assert "raw_text" not in rendered
    assert "conteúdo integral proibido" not in rendered
    assert "123.456.789-09" not in rendered
    assert "pessoa@example.com" not in rendered
    assert "81999999999" not in rendered
    assert "[CPF/CNPJ REMOVIDO]" in protocols[0]["client"]
    assert "[E-MAIL REMOVIDO]" in protocols[0]["client"]
    assert "[TELEFONE REMOVIDO]" in protocols[0]["client"]


def test_worker_executes_pipeline_outside_calling_thread(tmp_path: Path) -> None:
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []
    controller = FakeController(
        _settings(tmp_path),
        callback=lambda: worker_threads.append(threading.get_ident()),
    )
    bridge = AutomationBridge(controller)
    finished = threading.Event()
    bridge.operationFinished.connect(lambda _name: finished.set())

    bridge.run_pipeline_dry_run()

    _wait_until(finished.is_set)
    assert worker_threads
    assert worker_threads[0] != caller_thread


def test_cli_option_six_opens_new_visual_desktop(monkeypatch) -> None:
    answers = iter(["6", "0"])
    opened: list[bool] = []
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "setup_logger", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_open_desktop_visual", lambda: opened.append(True))
    monkeypatch.setattr(cli, "ApplicationController", lambda: object())

    cli.main([])

    assert opened == [True]
    source = inspect.getsource(cli.main)
    assert "interface desktop visual" in source
    assert "tkinter_app" not in source


def test_open_edge_cdp_command_does_not_open_portal_automatically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import automacao_gd.presentation.desktop.web_bridge as web_bridge

    monkeypatch.setattr(
        web_bridge,
        "_find_edge_executable",
        lambda: tmp_path / "msedge.exe",
    )
    settings = _settings(
        tmp_path,
        PORTAL_GD_URL="https://gdneoenergiapernambuco.neoenergia.com/",
    )

    command = _build_edge_cdp_command(settings)

    assert "about:blank" in command
    assert settings.PORTAL_GD_URL not in command
