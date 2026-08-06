from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from automacao_gd.application.contracts import OperationResult
from automacao_gd.application.preflight import run_preflight
from automacao_gd.infrastructure.config import Settings
from automacao_gd.presentation.controller import ApplicationController


class _CDPVersionHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/json/version":
            self.send_response(404)
            self.end_headers()
            return
        body = b'{"Browser":"Synthetic CDP","webSocketDebuggerUrl":"ws://127.0.0.1/devtools/browser/synthetic"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _local_cdp_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CDPVersionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _unused_local_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


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
    with _local_cdp_server() as endpoint:
        settings = _ready_settings(tmp_path, CDP_ENDPOINT=endpoint)
        report = run_preflight(settings, real_run=False, require_cdp=True)

    checks = {check.code: check for check in report.checks}

    assert report.ready is True
    assert report.blocking_errors == []
    assert checks["cdp_endpoint"].ok is True
    assert checks["cdp_connection"].ok is True


def test_preflight_blocks_when_required_local_cdp_is_unavailable(tmp_path: Path) -> None:
    settings = _ready_settings(tmp_path, CDP_ENDPOINT=_unused_local_endpoint())
    report = run_preflight(settings, real_run=False, require_cdp=True)
    checks = {check.code: check for check in report.checks}

    assert checks["cdp_endpoint"].ok is True
    assert checks["cdp_connection"].ok is False
    assert checks["cdp_connection"].blocking is True
    assert report.ready is False
    assert any("Conexão CDP indisponível" in error for error in report.blocking_errors)


def test_preflight_does_not_probe_cdp_when_not_required(
    tmp_path: Path, monkeypatch
) -> None:
    def fail_if_probe_is_called(*_args, **_kwargs):
        raise AssertionError("CDP probe não deve executar quando require_cdp=False")

    monkeypatch.setattr("automacao_gd.application.preflight.urlopen", fail_if_probe_is_called)

    settings = _ready_settings(tmp_path, CDP_ENDPOINT=_unused_local_endpoint())
    report = run_preflight(settings, real_run=False, require_cdp=False)
    checks = {check.code: check for check in report.checks}

    assert report.ready is True
    assert checks["cdp_endpoint"].ok is True
    assert "cdp_connection" not in checks


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
