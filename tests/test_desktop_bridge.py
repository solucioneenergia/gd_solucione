from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from automacao_gd.application.contracts import ProgressEvent
from automacao_gd.infrastructure.config import Settings

from apps.desktop.bridge import file_bridge
from apps.desktop.bridge.automation_bridge import (
    AutomationBridge,
    build_production_confirmation,
    production_confirmation_text,
)
from apps.desktop.bridge.progress_bridge import ProgressBridge
from apps.desktop.workers.automation_worker import AutomationWorker, operation_error_payload


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
        "APPLY_EXCEL": False,
        "APPLY_ARCHIVE": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_progress_bridge_consumes_progress_event_safely() -> None:
    bridge = ProgressBridge()

    payload = bridge.consume(
        ProgressEvent(
            overall_percent=40,
            stage_percent=80,
            protocol_percent=55,
            stage="update_excel",
            protocol="2600000000",
            message="Atualizando planilha telefone: 81999999999",
        )
    )

    assert payload["overall_percent"] == 40
    assert payload["protocol"] == "2600000000"
    assert "81999999999" not in payload["message"]


def test_progress_bridge_prevents_percent_regression() -> None:
    bridge = ProgressBridge()
    bridge.consume(ProgressEvent(overall_percent=70, stage_percent=10, stage="a"))
    payload = bridge.consume(ProgressEvent(overall_percent=20, stage_percent=20, stage="b"))

    assert payload["overall_percent"] == 70


def test_worker_accepts_progress_callback_and_emits_progress() -> None:
    received: list[ProgressEvent] = []

    def operation(progress_callback=None) -> dict:
        progress_callback(ProgressEvent(overall_percent=10, stage_percent=50, stage="test"))
        return {"ok": True}

    worker = AutomationWorker(operation, run_async=False)
    worker.progress.connect(received.append)
    results: list[dict] = []
    worker.finished.connect(results.append)

    worker.start()

    assert received
    assert results == [{"ok": True}]


def test_worker_converts_exception_to_operation_error() -> None:
    def operation() -> None:
        raise PermissionError("planilha bloqueada")

    worker = AutomationWorker(operation, stage="excel_update", run_async=False)
    failures: list[Any] = []
    worker.failed.connect(failures.append)

    worker.start()

    payload = operation_error_payload(failures[0])
    assert payload["code"] == "EXCEL_LOCKED"
    assert "Feche a planilha" in payload["suggested_action"]


def test_production_without_confirmation_does_not_execute(tmp_path: Path) -> None:
    calls = {"production": 0}

    bridge = AutomationBridge(
        _settings(tmp_path, APP_ENV="production"),
        runners={"run_production": lambda **_kwargs: calls.__setitem__("production", 1)},
        run_async=False,
    )
    failures: list[str] = []
    bridge.operationFailed.connect(failures.append)

    bridge.run_production_confirmed("SIM")

    assert calls["production"] == 0
    assert failures


def test_production_with_confirmation_calls_mocked_use_case(tmp_path: Path) -> None:
    calls = {"production": 0}

    def production_runner(**_kwargs) -> dict:
        calls["production"] += 1
        return {"success": True}

    bridge = AutomationBridge(
        _settings(tmp_path, APP_ENV="production"),
        runners={"run_production": production_runner},
        run_async=False,
    )
    finished: list[str] = []
    bridge.operationFinished.connect(finished.append)

    bridge.run_production_confirmed(
        build_production_confirmation(bridge.settings, operation="pipeline")
    )

    assert calls["production"] == 1
    assert finished == ["run_production"]


def test_bridge_messages_to_ui_are_sanitized(tmp_path: Path) -> None:
    bridge = AutomationBridge(
        _settings(tmp_path),
        runners={
            "run_dry_run": lambda **_kwargs: {
                "message": "telefone: 81999999999 email pessoa@example.com",
                "raw_text": "não enviar",
            }
        },
        run_async=False,
    )
    summaries: list[dict] = []
    bridge.summaryChanged.connect(summaries.append)

    bridge.run_dry_run()

    rendered = repr(summaries)
    assert "81999999999" not in rendered
    assert "pessoa@example.com" not in rendered
    assert "raw_text" not in rendered


def test_check_environment_and_cdp_are_safe_injected_calls(tmp_path: Path) -> None:
    calls: list[str] = []
    bridge = AutomationBridge(
        _settings(tmp_path),
        runners={
            "check_environment": lambda: calls.append("preflight") or {"ready": True},
            "test_cdp_connection": lambda: calls.append("cdp") or {"ready": True},
        },
        run_async=False,
    )

    bridge.check_environment()
    bridge.test_cdp_connection()

    assert calls == ["preflight", "cdp"]


def test_production_confirmation_text_contains_operational_warnings(tmp_path: Path) -> None:
    text = production_confirmation_text(_settings(tmp_path))

    assert "APLICAR OPÇÃO 5" in text
    assert "5 PROTOCOLOS" in text
    assert "PLANILHA_PATH" in text
    assert "CLIENTES_ROOT" in text


def test_worker_can_run_outside_calling_thread() -> None:
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def operation() -> dict:
        worker_threads.append(threading.get_ident())
        return {"ok": True}

    worker = AutomationWorker(operation, run_async=True)
    worker.start()

    assert worker._thread is not None
    worker._thread.join(3)
    assert worker_threads[0] != caller_thread
    assert worker.last_result == {"ok": True}


def test_file_bridge_uses_platform_default_opener_outside_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    opened: list[list[str]] = []

    monkeypatch.setattr(file_bridge.os, "name", "posix", raising=False)
    monkeypatch.setattr(file_bridge.sys, "platform", "linux")
    monkeypatch.setattr(
        file_bridge.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command),
    )

    target = tmp_path / "logs"
    target.mkdir()

    file_bridge._open_with_platform_default(target)

    assert opened == [["xdg-open", str(target)]]
