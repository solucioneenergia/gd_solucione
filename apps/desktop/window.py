from __future__ import annotations

from pathlib import Path

from automacao_gd.infrastructure.config import PROJECT_ROOT, get_settings

from apps.desktop.bridge.automation_bridge import AutomationBridge


try:
    from PySide6.QtCore import QUrl
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow

    QT_AVAILABLE = True
    WEBENGINE_AVAILABLE = True
except ImportError:
    QApplication = None
    QMainWindow = object
    QT_AVAILABLE = False
    WEBENGINE_AVAILABLE = False


FRONTEND_ROOT = PROJECT_ROOT / "apps" / "desktop" / "frontend"
DEV_SERVER_URL = "http://localhost:5173"


class DesktopVisualWindow(QMainWindow):
    def __init__(self, *, dev: bool = False, bridge: AutomationBridge | None = None) -> None:
        if not QT_AVAILABLE or not WEBENGINE_AVAILABLE:
            raise RuntimeError(
                "PySide6 QtWebEngine não está instalado. Instale PySide6 com WebEngine "
                "para abrir a interface visual."
            )
        super().__init__()
        self.settings = get_settings()
        self.bridge = bridge or AutomationBridge(self.settings)
        self.channel = QWebChannel(self)
        self.web_view = QWebEngineView(self)
        self.setWindowTitle("Automação GD Neoenergia — Desktop Visual")
        self.setMinimumSize(1180, 700)
        self._resize_to_available_screen()
        self._configure_channel()
        self._load_frontend(dev=dev)
        self.setCentralWidget(self.web_view)

    def _resize_to_available_screen(self) -> None:
        screen = QApplication.primaryScreen() if QApplication is not None else None
        if screen is None:
            self.resize(1366, 768)
            return

        available = screen.availableGeometry()
        width = max(1180, min(1536, available.width()))
        height = max(700, min(960, available.height()))
        self.resize(width, height)

    def _configure_channel(self) -> None:
        self.channel.registerObject("backend", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

    def _load_frontend(self, *, dev: bool) -> None:
        frontend_url = resolve_frontend_url(dev=dev)
        if frontend_url is not None:
            self.web_view.setUrl(frontend_url)
            return

        self.web_view.setHtml(_missing_frontend_html(), QUrl.fromLocalFile(str(FRONTEND_ROOT)))


def run_desktop(*, dev: bool = False) -> int:
    if not QT_AVAILABLE or not WEBENGINE_AVAILABLE:
        raise RuntimeError(
            "PySide6 QtWebEngine não está instalado. Instale PySide6 para abrir a interface."
        )
    application = QApplication.instance() or QApplication([])
    window = DesktopVisualWindow(dev=dev)
    window.show()
    return int(application.exec())


def resolve_frontend_url(*, dev: bool) -> QUrl | None:
    if dev:
        return QUrl(DEV_SERVER_URL)

    entrypoint = _frontend_entrypoint()
    if entrypoint is None:
        return None
    return QUrl.fromLocalFile(str(entrypoint.resolve()))


def _frontend_entrypoint() -> Path | None:
    dist_entrypoint = FRONTEND_ROOT / "dist" / "index.html"
    if dist_entrypoint.exists():
        return dist_entrypoint

    static_entrypoint = FRONTEND_ROOT / "static" / "index.html"
    if static_entrypoint.exists():
        return static_entrypoint

    return None


def _missing_frontend_html() -> str:
    return """
    <!doctype html>
    <html lang="pt-BR">
      <head>
        <meta charset="utf-8" />
        <style>
          body {
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            background: #f5f7fb;
            color: #111827;
            font-family: "Segoe UI", Arial, sans-serif;
          }
          main {
            width: min(720px, calc(100vw - 48px));
            padding: 32px;
            background: white;
            border: 1px solid #dfe5ee;
            border-radius: 16px;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
          }
          code { color: #005bea; }
        </style>
      </head>
      <body>
        <main>
          <h1>Frontend visual não encontrado</h1>
          <p>Crie ou gere o frontend local em <code>apps/desktop/frontend</code>.</p>
          <p>Quando Node/NPM estiver disponível, execute:</p>
          <pre>cd apps/desktop/frontend
npm install
npm run build</pre>
        </main>
      </body>
    </html>
    """
