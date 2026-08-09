from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from automacao_gd.infrastructure.config import get_settings

from apps.desktop.bridge.automation_bridge import AutomationBridge


_QApplication: Any
_QMainWindow: Any
_QUrl: Any
_QWebChannel: Any
_QWebEnginePage: Any
_QWebEngineSettings: Any
_QWebEngineUrlRequestInterceptor: Any
_QWebEngineView: Any

_FORCE_QT_FALLBACK = os.environ.get("AUTOMACAO_GD_QT_FALLBACK") == "1"

if _FORCE_QT_FALLBACK:
    QT_AVAILABLE = False
    WEBENGINE_AVAILABLE = False
    _QApplication = None
    _QMainWindow = object
    _QUrl = None
    _QWebChannel = None
    _QWebEnginePage = object
    _QWebEngineSettings = None
    _QWebEngineUrlRequestInterceptor = object
    _QWebEngineView = None
else:
    try:
        from PySide6.QtCore import QUrl as _ImportedQUrl
        from PySide6.QtWebChannel import QWebChannel as _ImportedQWebChannel
        from PySide6.QtWebEngineCore import (
            QWebEnginePage as _ImportedQWebEnginePage,
            QWebEngineSettings as _ImportedQWebEngineSettings,
            QWebEngineUrlRequestInterceptor as _ImportedQWebEngineUrlRequestInterceptor,
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView as _ImportedQWebEngineView
        from PySide6.QtWidgets import (
            QApplication as _ImportedQApplication,
            QMainWindow as _ImportedQMainWindow,
        )
    except ImportError:
        QT_AVAILABLE = False
        WEBENGINE_AVAILABLE = False
        _QApplication = None
        _QMainWindow = object
        _QUrl = None
        _QWebChannel = None
        _QWebEnginePage = object
        _QWebEngineSettings = None
        _QWebEngineUrlRequestInterceptor = object
        _QWebEngineView = None
    else:
        QT_AVAILABLE = True
        WEBENGINE_AVAILABLE = True
        _QApplication = _ImportedQApplication
        _QMainWindow = _ImportedQMainWindow
        _QUrl = _ImportedQUrl
        _QWebChannel = _ImportedQWebChannel
        _QWebEnginePage = _ImportedQWebEnginePage
        _QWebEngineSettings = _ImportedQWebEngineSettings
        _QWebEngineUrlRequestInterceptor = _ImportedQWebEngineUrlRequestInterceptor
        _QWebEngineView = _ImportedQWebEngineView


FRONTEND_ROOT = Path(__file__).resolve().parent / "frontend"
DEV_SERVER_URL = "http://127.0.0.1:5173"
_DEV_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_authorized_frontend_url(
    url: str,
    *,
    frontend_root: Path = FRONTEND_ROOT,
    dev: bool,
) -> bool:
    parsed = urlparse(str(url))
    scheme = parsed.scheme.casefold()
    if scheme == "file":
        try:
            candidate = Path(url2pathname(parsed.path)).resolve(strict=False)
            root = Path(frontend_root).resolve(strict=False)
            candidate.relative_to(root)
        except (OSError, ValueError):
            return False
        return True
    if scheme == "http" and dev:
        return (
            parsed.username is None
            and parsed.password is None
            and (parsed.hostname or "").casefold() in _DEV_HOSTS
            and parsed.port == 5173
        )
    return False


def _is_authorized_request_url(url: str, *, frontend_root: Path, dev: bool) -> bool:
    scheme = urlparse(str(url)).scheme.casefold()
    if scheme in {"data", "qrc"}:
        return True
    return is_authorized_frontend_url(url, frontend_root=frontend_root, dev=dev)


class LocalOnlyRequestInterceptor(_QWebEngineUrlRequestInterceptor):
    def __init__(self, *, frontend_root: Path, dev: bool, parent: Any = None) -> None:
        super().__init__(parent)
        self.frontend_root = frontend_root
        self.dev = dev

    def interceptRequest(self, info: Any) -> None:  # Qt callback name
        allowed = _is_authorized_request_url(
            info.requestUrl().toString(),
            frontend_root=self.frontend_root,
            dev=self.dev,
        )
        if not allowed:
            info.block(True)


class LocalOnlyPage(_QWebEnginePage):
    def __init__(self, window: "DesktopVisualWindow", *, dev: bool) -> None:
        super().__init__(window)
        self.window = window
        self.dev = dev

    def acceptNavigationRequest(
        self,
        url: Any,
        _navigation_type: Any,
        _is_main_frame: bool,
    ) -> bool:
        allowed = is_authorized_frontend_url(
            url.toString(),
            frontend_root=FRONTEND_ROOT,
            dev=self.dev,
        )
        if not allowed:
            self.window._detach_channel()
        return allowed

    def createWindow(self, _window_type: Any) -> None:
        return None


class DesktopVisualWindow(_QMainWindow):
    def __init__(self, *, dev: bool = False, bridge: AutomationBridge | None = None) -> None:
        if not QT_AVAILABLE or not WEBENGINE_AVAILABLE:
            raise RuntimeError(
                "PySide6 QtWebEngine não está instalado. Instale PySide6 com WebEngine "
                "para abrir a interface visual."
            )
        super().__init__()
        self.dev = dev
        self.settings = get_settings()
        self.bridge = bridge or AutomationBridge(self.settings)
        self.channel = _QWebChannel(self)
        self._channel_attached = False
        self.web_view = _QWebEngineView(self)
        self.page = LocalOnlyPage(self, dev=dev)
        self.interceptor = LocalOnlyRequestInterceptor(
            frontend_root=FRONTEND_ROOT,
            dev=dev,
            parent=self.page,
        )
        self.page.profile().setUrlRequestInterceptor(self.interceptor)
        self.web_view.setPage(self.page)
        self.setWindowTitle("Automação GD Neoenergia — Desktop Visual")
        self.setMinimumSize(1180, 700)
        self._resize_to_available_screen()
        self._configure_security()
        self.web_view.loadStarted.connect(self._detach_channel)
        self.web_view.loadFinished.connect(self._on_load_finished)
        self._load_frontend(dev=dev)
        self.setCentralWidget(self.web_view)

    def _resize_to_available_screen(self) -> None:
        screen = _QApplication.primaryScreen()
        if screen is None:
            self.resize(1366, 768)
            return

        available = screen.availableGeometry()
        width = max(1180, min(1536, available.width()))
        height = max(700, min(960, available.height()))
        self.resize(width, height)

    def _configure_security(self) -> None:
        settings = self.page.settings()
        settings.setAttribute(
            _QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            False,
        )
        settings.setAttribute(
            _QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            True,
        )
        settings.setAttribute(
            _QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            False,
        )

    def _attach_channel(self) -> None:
        if self._channel_attached:
            return
        self.channel.registerObject("backend", self.bridge)
        self.page.setWebChannel(self.channel)
        self._channel_attached = True
        self.page.runJavaScript(
            'window.dispatchEvent(new Event("qt-channel-ready"));'
        )

    def _detach_channel(self) -> None:
        if not self._channel_attached:
            return
        self.page.setWebChannel(None)
        self.channel.deregisterObject(self.bridge)
        self._channel_attached = False

    def _on_load_finished(self, succeeded: bool) -> None:
        if succeeded and is_authorized_frontend_url(
            self.web_view.url().toString(),
            frontend_root=FRONTEND_ROOT,
            dev=self.dev,
        ):
            self._attach_channel()
            return
        self._detach_channel()

    def _load_frontend(self, *, dev: bool) -> None:
        frontend_url = resolve_frontend_url(dev=dev)
        if frontend_url is not None:
            self.web_view.setUrl(frontend_url)
            return

        self._detach_channel()
        self.web_view.setHtml(_missing_frontend_html(), _QUrl("qrc:///frontend-missing/"))


def run_desktop(*, dev: bool = False) -> int:
    if not QT_AVAILABLE or not WEBENGINE_AVAILABLE:
        raise RuntimeError(
            "PySide6 QtWebEngine não está instalado. Instale PySide6 para abrir a interface."
        )
    application = _QApplication.instance() or _QApplication([])
    window = DesktopVisualWindow(dev=dev)
    window.show()
    return int(application.exec())


def resolve_frontend_url(*, dev: bool) -> Any | None:
    if dev:
        return _QUrl(DEV_SERVER_URL)

    entrypoint = _frontend_entrypoint(allow_static_fallback=False)
    if entrypoint is None:
        return None
    return _QUrl.fromLocalFile(str(entrypoint.resolve()))


def _frontend_entrypoint(*, allow_static_fallback: bool = False) -> Path | None:
    dist_entrypoint = FRONTEND_ROOT / "dist" / "index.html"
    if dist_entrypoint.exists():
        return dist_entrypoint

    if allow_static_fallback:
        static_entrypoint = FRONTEND_ROOT / "static" / "index.html"
        if static_entrypoint.exists():
            return static_entrypoint

    return None


def _missing_frontend_html() -> str:
    return """
    <!doctype html>
    <html lang="pt-BR">
      <head><meta charset="utf-8" /><title>Frontend indisponível</title></head>
      <body>
        <main>
          <h1>Frontend canônico não encontrado</h1>
          <p>Esta instalação está incompleta e não pode iniciar a interface operacional.</p>
          <p>Gere o frontend com <code>pnpm install --frozen-lockfile</code> e
          <code>pnpm build</code> antes de empacotar.</p>
        </main>
      </body>
    </html>
    """
