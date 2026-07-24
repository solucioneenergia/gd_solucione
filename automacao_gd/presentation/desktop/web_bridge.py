from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from automacao_gd.application.contracts import OperationResult
from automacao_gd.infrastructure.config import PROJECT_ROOT, Settings, get_settings
from automacao_gd.infrastructure.portal.factory import create_portal_automation
from automacao_gd.presentation.controller import ApplicationController
from automacao_gd.presentation.desktop._qt import (
    QT_AVAILABLE,
    QObject,
    QThread,
    Signal,
    Slot,
)
from automacao_gd.presentation.desktop.worker import OperationWorker
from automacao_gd.presentation.operational_output import sanitize_for_console


_EDITABLE_SETTINGS = {
    "MAX_PORTAL_PAGES",
    "MAX_COMPLETED_TO_PROCESS",
    "DRY_RUN",
    "ENABLE_PORTAL_PAGINATION",
    "SKIP_ALREADY_COMPLETED",
    "APPLY_EXCEL",
    "APPLY_ARCHIVE",
    "BACKUP_EXCEL",
}
_BOOLEAN_SETTINGS = {
    "DRY_RUN",
    "ENABLE_PORTAL_PAGINATION",
    "SKIP_ALREADY_COMPLETED",
    "APPLY_EXCEL",
    "APPLY_ARCHIVE",
    "BACKUP_EXCEL",
}
_TOTAL_FIELDS = (
    "total_pages_read",
    "total_rows",
    "total_completed",
    "total_eligible_after_skip",
    "total_selected",
    "total_downloaded",
    "total_existing_reused",
    "total_processed_success",
    "total_errors",
    "total_excel_updated",
    "total_archived",
    "total_pending_review",
    "total_pdfs",
    "total_success",
)


class AutomationBridge(QObject):
    statusChanged = Signal(str)
    progressChanged = Signal(int)
    operationStarted = Signal(str)
    operationFinished = Signal(str)
    operationFailed = Signal(str)
    logMessage = Signal(str)
    summaryChanged = Signal(dict)
    protocolUpdated = Signal(dict)

    def __init__(
        self,
        controller: ApplicationController | None = None,
        *,
        controller_factory: Callable[[Settings], ApplicationController] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or ApplicationController()
        self._base_settings = self.controller.settings
        self._controller_factory = controller_factory or ApplicationController
        self._temporary_settings: dict[str, Any] = {}
        self._busy = False
        self._operation_name = ""
        self._cancel_event = threading.Event()
        self._thread: Any = None
        self._worker: OperationWorker | None = None

    @property
    def is_busy(self) -> bool:
        return self._busy

    @Slot(result="QVariantMap")
    def get_initial_state(self) -> dict[str, Any]:
        settings = self._effective_settings()
        return {
            "mode": "simulation" if settings.DRY_RUN else "production",
            "cdp_mode": bool(settings.CDP_MODE),
            "cdp_endpoint": str(settings.CDP_ENDPOINT),
            "max_portal_pages": settings.MAX_PORTAL_PAGES,
            "max_completed_to_process": settings.MAX_COMPLETED_TO_PROCESS,
            "enable_portal_pagination": settings.ENABLE_PORTAL_PAGINATION,
            "skip_already_completed": settings.SKIP_ALREADY_COMPLETED,
            "apply_excel": settings.APPLY_EXCEL,
            "apply_archive": settings.APPLY_ARCHIVE,
            "backup_excel": settings.BACKUP_EXCEL,
            "busy": self._busy,
        }

    @Slot(result="QVariantMap")
    def get_production_confirmation(self) -> dict[str, Any]:
        settings = self._settings_for_run(dry_run=False)
        return {
            "DRY_RUN": False,
            "PLANILHA_PATH": str(settings.planilha_path),
            "CLIENTES_ROOT": str(settings.clientes_root_path),
            "APPLY_EXCEL": settings.APPLY_EXCEL,
            "APPLY_ARCHIVE": settings.APPLY_ARCHIVE,
            "BACKUP_EXCEL": settings.BACKUP_EXCEL,
            "MAX_PORTAL_PAGES": settings.MAX_PORTAL_PAGES,
            "MAX_COMPLETED_TO_PROCESS": settings.MAX_COMPLETED_TO_PROCESS,
        }

    @Slot(str, result=bool)
    def apply_temporary_settings(self, payload_json: str) -> bool:
        try:
            payload = json.loads(payload_json or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Configuração temporária inválida.")
            normalized = self._normalize_overrides(payload)
            self._temporary_settings = normalized
            self.logMessage.emit("Configurações temporárias aplicadas à interface.")
            return True
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.operationFailed.emit(str(sanitize_for_console(str(exc))))
            return False

    @Slot()
    def check_environment(self) -> None:
        settings = self._effective_settings()
        controller = self._controller_for(settings)
        self._start_operation(
            "check_environment",
            lambda: controller.preflight(require_cdp=settings.CDP_MODE),
            initial_status="idle",
        )

    @Slot()
    def open_edge_cdp(self) -> None:
        try:
            command = _build_edge_cdp_command(self._effective_settings())
            subprocess.Popen(
                command,
                close_fds=True,
                creationflags=(
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
            )
            self.statusChanged.emit("idle")
            self.logMessage.emit(
                "Edge CDP aberto. Faça login manual e acesse Minhas Solicitações."
            )
        except Exception as exc:
            self.operationFailed.emit(str(sanitize_for_console(str(exc))))

    @Slot()
    def test_cdp_connection(self) -> None:
        settings = self._effective_settings()

        def operation() -> OperationResult:
            automation = create_portal_automation(settings)
            try:
                automation.start_browser()
                automation.open_portal()
                return OperationResult(
                    True,
                    "Conexão CDP validada.",
                    {"cdp_connected": True},
                )
            finally:
                automation.close()

        self._start_operation(
            "test_cdp_connection",
            operation,
            initial_status="cdp_connected",
        )

    @Slot()
    def inspect_portal(self) -> None:
        settings = self._effective_settings()
        controller = self._controller_for(settings)
        self._start_operation(
            "inspect_portal",
            lambda: controller.inspect_portal(confirm_login=lambda _message: True),
            initial_status="reading_portal",
        )

    @Slot()
    def run_pipeline_dry_run(self) -> None:
        settings = self._settings_for_run(dry_run=True)
        controller = self._controller_for(settings)
        self._start_operation(
            "pipeline_dry_run",
            controller.run_pipeline,
            initial_status="reading_portal",
        )

    @Slot(str)
    def run_pipeline_production(self, confirmation: str) -> None:
        if confirmation.strip().upper() != "SIM":
            self.operationFailed.emit(
                "Produção não iniciada: confirme explicitamente digitando SIM."
            )
            return
        settings = self._settings_for_run(dry_run=False)
        controller = self._controller_for(settings)
        self._start_operation(
            "pipeline_production",
            controller.run_pipeline,
            initial_status="reading_portal",
        )

    @Slot()
    def stop_current_operation(self) -> None:
        if not self._busy:
            self.logMessage.emit("Nenhuma operação está em execução.")
            return
        self._cancel_event.set()
        self.logMessage.emit(
            "Cancelamento solicitado. A etapa atual será concluída com segurança quando possível."
        )

    @Slot()
    def open_pipeline_report(self) -> None:
        self._open_path(self._effective_settings().logs_dir_path / "pipeline_cdp_completo.md")

    @Slot()
    def open_processing_report(self) -> None:
        self._open_path(
            self._effective_settings().logs_dir_path
            / "processamento_pdfs_planilha_clientes.md"
        )

    @Slot()
    def open_downloads_folder(self) -> None:
        self._open_path(self._effective_settings().downloads_dir_path)

    @Slot()
    def open_logs_folder(self) -> None:
        self._open_path(self._effective_settings().logs_dir_path)

    @Slot()
    def open_workbook(self) -> None:
        self._open_path(self._effective_settings().planilha_path)

    @Slot()
    def open_clients_root(self) -> None:
        self._open_path(self._effective_settings().clientes_root_path)

    def _start_operation(
        self,
        name: str,
        operation: Callable[[], object],
        *,
        initial_status: str,
    ) -> None:
        if self._busy:
            self.operationFailed.emit("Já existe uma operação em andamento.")
            return

        self._busy = True
        self._operation_name = name
        self._cancel_event = threading.Event()
        self.operationStarted.emit(name)
        self.progressChanged.emit(0)
        self.logMessage.emit(f"Operação iniciada: {name}.")
        worker = OperationWorker(
            operation,
            initial_status=initial_status,
            cancel_event=self._cancel_event,
        )
        self._worker = worker
        worker.statusChanged.connect(self.statusChanged.emit)
        worker.progressChanged.connect(self.progressChanged.emit)
        worker.resultReady.connect(self._handle_result)
        worker.errorOccurred.connect(self._handle_error)

        if QT_AVAILABLE:
            thread = QThread(self)
            self._thread = thread
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._clear_thread)
            thread.start()
            return

        thread = threading.Thread(
            target=worker.run,
            name=f"automation-{name}",
            daemon=True,
        )
        self._thread = thread
        worker.finished.connect(self._clear_thread)
        thread.start()

    @Slot(object)
    def _handle_result(self, result: object) -> None:
        if not isinstance(result, OperationResult):
            result = OperationResult(True, "Operação concluída.", {})
        summary = build_frontend_summary(self._operation_name, result)
        self.summaryChanged.emit(summary)
        for protocol in build_frontend_protocols(result.payload):
            self.protocolUpdated.emit(protocol)

        self.progressChanged.emit(100)
        if result.success:
            final_status = (
                "cdp_connected"
                if self._operation_name == "test_cdp_connection"
                else "completed"
            )
            self.statusChanged.emit(final_status)
            self.operationFinished.emit(self._operation_name)
            self.logMessage.emit(str(sanitize_for_console(result.message)))
        else:
            self.statusChanged.emit("error")
            self.operationFailed.emit(str(sanitize_for_console(result.message)))

    @Slot(str)
    def _handle_error(self, message: str) -> None:
        safe_message = str(sanitize_for_console(message))
        self.statusChanged.emit("error")
        self.operationFailed.emit(safe_message)
        self.logMessage.emit(safe_message)

    @Slot()
    def _clear_thread(self) -> None:
        self._busy = False
        self._worker = None
        self._thread = None

    def _effective_settings(self) -> Settings:
        values = self._base_settings.model_dump()
        values.update(self._temporary_settings)
        return Settings(_env_file=None, **values)

    def _settings_for_run(self, *, dry_run: bool) -> Settings:
        values = self._effective_settings().model_dump()
        values["DRY_RUN"] = dry_run
        return Settings(_env_file=None, **values)

    def _controller_for(self, settings: Settings) -> ApplicationController:
        if settings == self.controller.settings:
            return self.controller
        return self._controller_factory(settings)

    @staticmethod
    def _normalize_overrides(payload: dict[str, Any]) -> dict[str, Any]:
        unknown = set(payload) - _EDITABLE_SETTINGS
        if unknown:
            raise ValueError(
                "Configurações não permitidas: " + ", ".join(sorted(unknown))
            )
        normalized: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _BOOLEAN_SETTINGS:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} deve ser verdadeiro ou falso.")
                normalized[key] = value
            else:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{key} deve ser um inteiro maior ou igual a zero.")
                normalized[key] = value
        return normalized

    def _open_path(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=True)
            if os.name == "nt":
                os.startfile(resolved)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(resolved)], close_fds=True)
            self.logMessage.emit(f"Aberto: {resolved.name}")
        except Exception as exc:
            self.operationFailed.emit(str(sanitize_for_console(str(exc))))


def build_frontend_summary(operation_name: str, result: OperationResult) -> dict[str, Any]:
    payload = result.payload if isinstance(result.payload, dict) else {}
    summary: dict[str, Any] = {
        "operation": operation_name,
        "success": bool(result.success),
        "status": result.status.value,
        "message": sanitize_for_console(result.message),
        "dry_run": bool(payload.get("dry_run", True)),
    }
    for field in _TOTAL_FIELDS:
        value = payload.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            summary[field] = value
    if "records" in payload and isinstance(payload["records"], list):
        summary["total_records"] = len(payload["records"])
    if isinstance(payload.get("cdp_connected"), bool):
        summary["cdp_connected"] = payload["cdp_connected"]
    return sanitize_for_console(summary)


def build_frontend_protocols(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("protocol_results")
    if not isinstance(rows, list):
        return []
    protocols: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        protocol = {
            "protocol": sanitize_for_console(
                str(row.get("protocol") or "-"),
                field_name="protocol",
            ),
            "client": sanitize_for_console(
                str(row.get("client_name") or "Cliente não informado")
            ),
            "pdf_status": sanitize_for_console(
                str(row.get("download_status") or "não processado")
            ),
            "excel_status": sanitize_for_console(
                str(row.get("excel_action") or row.get("excel_state") or "não aplicável")
            ),
            "archive_status": sanitize_for_console(
                str(row.get("archive_state") or "não arquivado")
            ),
            "error": sanitize_for_console(str(row.get("error") or "")),
        }
        protocols.append(protocol)
    return protocols


def _build_edge_cdp_command(settings: Settings) -> list[str]:
    parsed = urlparse(settings.CDP_ENDPOINT)
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("A interface só pode abrir Edge CDP em endpoint local.")
    port = parsed.port or 9222
    executable = _find_edge_executable()
    profile = PROJECT_ROOT / "data" / "edge_cdp_profile"
    profile.mkdir(parents=True, exist_ok=True)
    return [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]


def _find_edge_executable() -> Path:
    discovered = shutil.which("msedge") or shutil.which("msedge.exe")
    candidates = [
        Path(discovered) if discovered else None,
        Path(os.environ.get("PROGRAMFILES(X86)", ""))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", ""))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise FileNotFoundError("Microsoft Edge não foi encontrado neste computador.")
