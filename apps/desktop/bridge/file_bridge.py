from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.presentation.desktop._qt import QObject, Signal, Slot
from automacao_gd.presentation.operational_output import sanitize_for_console


class FileBridge(QObject):
    operationFailed = Signal(str)
    logMessage = Signal(str)

    def __init__(self, settings: Settings | None = None, parent: Any = None) -> None:
        super().__init__(parent)
        self.settings = settings or get_settings()

    @Slot()
    def open_reports_folder(self) -> bool:
        return self._open_path(self.settings.logs_dir_path)

    @Slot()
    def open_pipeline_report(self) -> bool:
        return self._open_path(self.settings.logs_dir_path / "pipeline_cdp_completo.md")

    @Slot()
    def open_processing_report(self) -> bool:
        return self._open_path(
            self.settings.logs_dir_path / "processamento_pdfs_planilha_clientes.md"
        )

    @Slot()
    def open_downloads_folder(self) -> bool:
        return self._open_path(self.settings.downloads_dir_path)

    @Slot()
    def open_workbook(self) -> bool:
        return self._open_path(self.settings.planilha_path)

    def _open_path(self, path: Path) -> bool:
        target = Path(path)
        try:
            if not target.exists():
                self.operationFailed.emit(
                    str(sanitize_for_console(f"Caminho não encontrado: {target}"))
                )
                return False
            _open_with_platform_default(target)
            self.logMessage.emit(str(sanitize_for_console(f"Abrindo: {target}")))
            return True
        except Exception as exc:
            self.operationFailed.emit(str(sanitize_for_console(str(exc))))
            return False


def _open_with_platform_default(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, close_fds=True)
