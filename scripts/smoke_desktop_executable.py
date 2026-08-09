from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_REQUIRED_TRUE_FIELDS = (
    "process_started",
    "window_created",
    "frontend_loaded",
    "bridge_initialized",
    "empty_state_visible",
    "controlled_shutdown",
)


def build_synthetic_environment(root: Path) -> dict[str, str]:
    root = Path(root).resolve()
    paths = {
        "PLANILHA_PATH": root / "documents" / "synthetic-workbook.xlsx",
        "CLIENTES_ROOT": root / "clients",
        "DOWNLOADS_DIR": root / "downloads",
        "LOGS_DIR": root / "logs",
        "AUTH_STATE_PATH": root / "auth" / "synthetic-state.json",
        "BROWSER_PROFILE_DIR": root / "browser-profile",
    }
    return {
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
        "PORTAL_GD_URL": "https://127.0.0.1:1/synthetic",
        **{key: str(value) for key, value in paths.items()},
        "HEADLESS": "true",
        "DRY_RUN": "true",
        "USE_PERSISTENT_CONTEXT": "false",
        "CDP_MODE": "false",
        "CDP_ENDPOINT": "http://127.0.0.1:1",
        "ALLOW_REMOTE_CDP": "false",
        "APPLY_EXCEL": "false",
        "APPLY_ARCHIVE": "false",
        "SYNC_COMPLETION_STATUS": "false",
        "APPLY_COMPLETION_STATUS": "false",
        "RESET_PIPELINE_STATE": "false",
        "MAX_COMPLETED_TO_PROCESS": "1",
        "MAX_PORTAL_PAGES": "1",
    }


def smoke_report_is_approved(report: dict[str, Any]) -> bool:
    return (
        all(report.get(field) is True for field in _REQUIRED_TRUE_FIELDS)
        and report.get("operation_started") is False
        and report.get("external_requests") == 0
    )


def run_smoke(
    executable: Path,
    synthetic_root: Path,
    report_path: Path,
    *,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("O smoke do bundle Windows exige Windows.")

    executable = Path(executable).resolve(strict=True)
    synthetic_root = Path(synthetic_root).resolve()
    report_path = Path(report_path).resolve()
    synthetic_root.mkdir(parents=True, exist_ok=False)
    for directory in ("clients", "documents", "temp"):
        (synthetic_root / directory).mkdir()

    port = _reserve_loopback_port()
    environment = _minimal_process_environment(synthetic_root)
    environment.update(build_synthetic_environment(synthetic_root))
    environment["QTWEBENGINE_REMOTE_DEBUGGING"] = str(port)

    report: dict[str, Any] = {
        "schema_version": 1,
        "process_started": False,
        "window_created": False,
        "frontend_loaded": False,
        "bridge_initialized": False,
        "empty_state_visible": False,
        "operation_started": False,
        "external_requests": -1,
        "controlled_shutdown": False,
    }
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [str(executable)],
            cwd=synthetic_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        report["process_started"] = process.poll() is None
        window = _wait_for_window(process.pid, timeout_seconds)
        report["window_created"] = window is not None
        _wait_for_cdp(port, timeout_seconds)
        report.update(_inspect_frontend(port, timeout_seconds))

        if window is not None:
            ctypes.windll.user32.PostMessageW(window, 0x0010, 0, 0)
            try:
                report["exit_code"] = process.wait(timeout=10)
                report["controlled_shutdown"] = report["exit_code"] == 0
            except subprocess.TimeoutExpired:
                report["controlled_shutdown"] = False
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        report["approved"] = smoke_report_is_approved(report)
        _write_report(report_path, report)
    return report


def _minimal_process_environment(root: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment["USERPROFILE"] = str(root / "user-profile")
    environment["TEMP"] = str(root / "temp")
    environment["TMP"] = str(root / "temp")
    return environment


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_cdp(port: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    endpoint = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("Qt WebEngine não expôs o endpoint CDP local.")


def _inspect_frontend_with_playwright(
    port: int, timeout_seconds: float
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}",
            timeout=int(timeout_seconds * 1000),
        )
        deadline = time.monotonic() + timeout_seconds
        page = None
        while time.monotonic() < deadline:
            pages = [page for context in browser.contexts for page in context.pages]
            page = next((candidate for candidate in pages if candidate.url.startswith("file:")), None)
            if page is not None:
                break
            time.sleep(0.2)
        if page is None:
            raise TimeoutError("Frontend canônico não foi localizado no CDP local.")

        page.wait_for_function(
            """() => Boolean(
              window.qt?.webChannelTransport &&
              window.backend &&
              typeof window.backend.refresh_dashboard === 'function'
            )""",
            timeout=int(timeout_seconds * 1000),
        )
        observation = page.evaluate(
            """() => {
              const resources = performance.getEntriesByType('resource')
                .map((entry) => String(entry.name || ''));
              const external = resources.filter((url) => /^https?:/i.test(url));
              const text = document.body?.innerText || '';
              return {
                frontend_loaded:
                  location.protocol === 'file:' &&
                  location.pathname.replace(/\\\\/g, '/').includes(
                    '/apps/desktop/frontend/dist/index.html'
                  ),
                bridge_initialized: Boolean(
                  window.qt?.webChannelTransport &&
                  window.backend &&
                  typeof window.backend.refresh_dashboard === 'function'
                ),
                empty_state_visible: text.includes(
                  'Nenhum protocolo recente encontrado'
                ),
                operation_started: text.includes('Operação iniciada.'),
                external_requests: external.length,
              };
            }"""
        )
        return dict(observation)
    finally:
        playwright.stop()


def _inspect_frontend(port: int, timeout_seconds: float) -> dict[str, Any]:
    target = _wait_for_frontend_target(port, timeout_seconds)
    observation = _evaluate_cdp(
        str(target["webSocketDebuggerUrl"]),
        """new Promise((resolve, reject) => {
          const deadline = Date.now() + 30000;
          const inspect = () => {
            if (!(
              window.qt?.webChannelTransport &&
              window.backend &&
              typeof window.backend.refresh_dashboard === 'function'
            )) {
              if (Date.now() < deadline) {
                setTimeout(inspect, 100);
                return;
              }
              reject(new Error('bridge-timeout'));
              return;
            }
            const resources = performance.getEntriesByType('resource')
              .map((entry) => String(entry.name || ''));
            const external = resources.filter((url) => /^https?:/i.test(url));
            const text = document.body?.innerText || '';
            resolve({
              frontend_loaded:
                location.protocol === 'file:' &&
                location.pathname.replace(/\\\\/g, '/').includes(
                  '/apps/desktop/frontend/dist/index.html'
                ),
              bridge_initialized: Boolean(
                window.qt?.webChannelTransport &&
                window.backend &&
                typeof window.backend.refresh_dashboard === 'function'
              ),
              empty_state_visible: text.includes(
                'Nenhum protocolo recente encontrado'
              ),
              operation_started: text.includes('Operação iniciada.'),
              external_requests: external.length,
            });
          };
          inspect();
        })""",
        timeout_seconds,
    )
    if not isinstance(observation, dict):
        raise RuntimeError("CDP returned an invalid observation.")
    return dict(observation)


def _wait_for_frontend_target(port: int, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    endpoint = f"http://127.0.0.1:{port}/json/list"
    while time.monotonic() < deadline:
        with urllib.request.urlopen(endpoint, timeout=1) as response:
            targets = json.load(response)
        target = _select_frontend_target(targets)
        if target is not None:
            return target
        time.sleep(0.2)
    raise TimeoutError("Canonical frontend target was not found in local CDP.")


def _select_frontend_target(targets: Any) -> dict[str, Any] | None:
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, dict):
            continue
        url = str(target.get("url", ""))
        websocket_url = str(target.get("webSocketDebuggerUrl", ""))
        if (
            target.get("type") == "page"
            and url.startswith("file:")
            and "/apps/desktop/frontend/dist/index.html" in url.replace("\\", "/")
            and websocket_url.startswith("ws://")
        ):
            return target
    return None


def _evaluate_cdp(
    websocket_url: str,
    expression: str,
    timeout_seconds: float,
) -> Any:
    connection = _open_websocket(websocket_url, timeout_seconds)
    try:
        request_id = 1
        _send_websocket_text(
            connection,
            json.dumps(
                {
                    "id": request_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            ),
        )
        while True:
            payload = json.loads(_receive_websocket_text(connection))
            if payload.get("id") != request_id:
                continue
            if "error" in payload or payload.get("result", {}).get("exceptionDetails"):
                raise RuntimeError("CDP evaluation failed.")
            return payload["result"]["result"].get("value")
    finally:
        connection.close()


def _open_websocket(websocket_url: str, timeout_seconds: float) -> socket.socket:
    parsed = urlparse(websocket_url)
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("CDP endpoint is not local.")
    port = parsed.port
    if port is None:
        raise RuntimeError("CDP endpoint has no port.")
    connection = socket.create_connection((parsed.hostname, port), timeout_seconds)
    connection.settimeout(timeout_seconds)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    connection.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        response += connection.recv(4096)
        if len(response) > 65536:
            raise RuntimeError("Invalid WebSocket response.")
    if not response.startswith(b"HTTP/1.1 101"):
        raise RuntimeError("Local CDP endpoint rejected WebSocket upgrade.")
    return connection


def _send_websocket_text(connection: socket.socket, message: str) -> None:
    payload = message.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    size = len(payload)
    if size < 126:
        header.append(0x80 | size)
    elif size <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", size))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", size))
    header.extend(mask)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    connection.sendall(bytes(header) + masked)


def _receive_websocket_text(connection: socket.socket) -> str:
    fragments = bytearray()
    while True:
        first, second = _receive_exact(connection, 2)
        final = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", _receive_exact(connection, 2))[0]
        elif size == 127:
            size = struct.unpack("!Q", _receive_exact(connection, 8))[0]
        mask = _receive_exact(connection, 4) if masked else b""
        payload = _receive_exact(connection, size)
        if masked:
            payload = bytes(
                value ^ mask[index % 4] for index, value in enumerate(payload)
            )
        if opcode == 0x8:
            raise RuntimeError("CDP endpoint closed the WebSocket.")
        if opcode == 0x9:
            connection.sendall(b"\x8a\x00")
            continue
        if opcode in {0x0, 0x1}:
            fragments.extend(payload)
            if final:
                return fragments.decode("utf-8")


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        chunk = connection.recv(size - len(payload))
        if not chunk:
            raise RuntimeError("CDP endpoint closed the connection.")
        payload.extend(chunk)
    return bytes(payload)


def _wait_for_window(process_id: int, timeout_seconds: float) -> int | None:
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        windows: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(handle: int, _parameter: int) -> bool:
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == process_id and user32.IsWindowVisible(handle):
                windows.append(int(handle))
            return True

        user32.EnumWindows(callback, 0)
        if windows:
            window = windows[0]
            title_size = user32.GetWindowTextLengthW(window) + 1
            title = ctypes.create_unicode_buffer(title_size)
            user32.GetWindowTextW(window, title, title_size)
            if title.value == "Unhandled exception in script":
                raise RuntimeError("O executável abriu o diálogo de falha do bootloader.")
            return window
        time.sleep(0.2)
    return None


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke offline do bundle desktop 2.0.2.")
    parser.add_argument("executable", type=Path)
    parser.add_argument("--synthetic-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()
    report = run_smoke(
        args.executable,
        args.synthetic_root,
        args.report,
        timeout_seconds=args.timeout,
    )
    return 0 if report["approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
