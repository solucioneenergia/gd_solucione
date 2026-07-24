from __future__ import annotations

from urllib.parse import urlparse

from automacao_gd.presentation.desktop.web_assets import resolve_frontend_source
from automacao_gd.presentation.desktop.web_bridge import AutomationBridge


try:
    from PySide6.QtCore import QUrl
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow
except ImportError as exc:
    _PYSIDE_IMPORT_ERROR = exc
else:
    _PYSIDE_IMPORT_ERROR = None


if _PYSIDE_IMPORT_ERROR is None:

    class LocalOnlyPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            parsed = urlparse(url.toString())
            allowed = parsed.scheme in {"file", "qrc", "data"} or (
                parsed.scheme == "http"
                and (parsed.hostname or "").lower()
                in {"localhost", "127.0.0.1", "::1"}
            )
            return bool(allowed)


    class MainWindow(QMainWindow):
        def __init__(
            self,
            *,
            development: bool = False,
            development_url: str | None = None,
            bridge: AutomationBridge | None = None,
        ) -> None:
            super().__init__()
            self.setWindowTitle("Automação GD Neoenergia")
            self.resize(1440, 900)
            self.setMinimumSize(1080, 700)

            self.view = QWebEngineView(self)
            self.page = LocalOnlyPage(self.view)
            self.view.setPage(self.page)
            self.setCentralWidget(self.view)

            self.page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                False,
            )
            self.page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                True,
            )

            self.bridge = bridge or AutomationBridge(parent=self)
            self.channel = QWebChannel(self.page)
            self.channel.registerObject("backend", self.bridge)
            self.page.setWebChannel(self.channel)

            source = resolve_frontend_source(
                development=development,
                development_url=development_url,
            )
            if source.kind == "url":
                self.view.load(QUrl(source.location))
            elif source.kind == "file":
                self.view.load(QUrl.fromLocalFile(source.location))
            else:
                self.view.setHtml(source.location, QUrl("qrc:///"))

        def closeEvent(self, event) -> None:
            if self.bridge.is_busy:
                self.bridge.stop_current_operation()
                event.ignore()
                return
            super().closeEvent(event)


else:

    class MainWindow:
        def __init__(self, **_kwargs) -> None:
            raise RuntimeError(
                "PySide6 com Qt WebEngine não está instalado. "
                "Execute: pip install -r requirements.txt"
            ) from _PYSIDE_IMPORT_ERROR


def run_desktop(
    *,
    development: bool = False,
    development_url: str | None = None,
) -> int:
    if _PYSIDE_IMPORT_ERROR is not None:
        raise RuntimeError(
            "PySide6 com Qt WebEngine não está instalado. "
            "Execute: pip install -r requirements.txt"
        ) from _PYSIDE_IMPORT_ERROR

    application = QApplication.instance() or QApplication([])
    window = MainWindow(
        development=development,
        development_url=development_url,
    )
    window.show()
    return application.exec()
