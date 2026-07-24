from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from automacao_gd.application.contracts import (
    OperationError,
    OperationResult,
    ProgressCallback,
    map_exception_to_operation_error,
)
from automacao_gd.application.preflight import run_preflight
from automacao_gd.application.use_cases.cleanup_temp import CleanupTemporaryFilesUseCase
from automacao_gd.application.use_cases.process_downloads import ProcessDownloadedPdfsUseCase
from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.presentation.desktop._qt import QObject, Signal, Slot
from automacao_gd.presentation.operational_output import (
    format_cleanup_summary,
    format_operation_error,
    sanitize_for_console,
)

from apps.desktop.bridge.file_bridge import FileBridge
from apps.desktop.bridge.progress_bridge import ProgressBridge
from apps.desktop.workers.automation_worker import AutomationWorker


PRODUCTION_CONFIRMATION = "SIM, EXECUTAR PRODUÇÃO"


class AutomationBridge(QObject):
    statusChanged = Signal(str)
    progressChanged = Signal(dict)
    operationStarted = Signal(str)
    operationFinished = Signal(str)
    operationFailed = Signal(str)
    logMessage = Signal(str)
    summaryChanged = Signal(dict)
    dashboardChanged = Signal(dict)
    protocolsChanged = Signal(list)

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        runners: dict[str, Callable[..., Any]] | None = None,
        run_async: bool = True,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings or get_settings()
        self.runners = runners or {}
        self.run_async = run_async
        self.progress_bridge = ProgressBridge()
        self.file_bridge = FileBridge(self.settings)
        self.current_worker: AutomationWorker | None = None
        self.cdp_status = "Não testado"
        self.system_status = "Pronto"
        self.recent_protocols: list[dict[str, Any]] = []
        self.progress_bridge.progressChanged.connect(self.progressChanged.emit)
        self.file_bridge.logMessage.connect(self.logMessage.emit)
        self.file_bridge.operationFailed.connect(self.operationFailed.emit)

    @Slot(result=str)
    def get_dashboard_data(self) -> str:
        return json.dumps(self._dashboard_payload(), ensure_ascii=False)

    @Slot(result=str)
    def get_recent_protocols(self) -> str:
        protocols = self._recent_protocols_payload()
        self.recent_protocols = protocols
        return json.dumps({"protocols": protocols}, ensure_ascii=False)

    @Slot()
    def refresh_dashboard(self) -> None:
        protocols = self._recent_protocols_payload()
        self.recent_protocols = protocols
        self.dashboardChanged.emit(self._dashboard_payload())
        self.protocolsChanged.emit(protocols)

    @Slot()
    def check_environment(self) -> None:
        self._run_operation("check_environment", self._check_environment, run_async=False)

    @Slot()
    def open_edge_cdp(self) -> None:
        self._emit_placeholder(
            "Abra o Edge com CDP pela rotina operacional validada e faça login manual no Portal GD."
        )

    @Slot()
    def test_cdp_connection(self) -> None:
        self._run_operation("test_cdp_connection", self._test_cdp_connection, run_async=False)

    @Slot()
    def inspect_portal(self) -> None:
        self._emit_placeholder("Inspeção visual do portal será conectada em etapa posterior.")

    @Slot()
    def run_dry_run(self) -> None:
        self._run_operation("run_dry_run", self._run_dry_run)

    @Slot(str)
    def run_production_confirmed(self, confirmation: str = "") -> None:
        if str(confirmation) != PRODUCTION_CONFIRMATION:
            self.operationFailed.emit(
                "Produção bloqueada. Confirme com: SIM, EXECUTAR PRODUÇÃO"
            )
            return
        self._run_operation("run_production", self._run_production)

    @Slot()
    def run_equipment_reformat_dry_run(self) -> None:
        self._emit_placeholder("Reformatação dry-run será exposta como manutenção dedicada.")

    @Slot()
    def run_cleanup_dry_run(self) -> None:
        self._run_operation("run_cleanup_dry_run", self._run_cleanup_dry_run, run_async=False)

    @Slot()
    def open_reports_folder(self) -> bool:
        return self.file_bridge.open_reports_folder()

    @Slot()
    def open_pipeline_report(self) -> bool:
        return self.file_bridge.open_pipeline_report()

    @Slot()
    def open_processing_report(self) -> bool:
        return self.file_bridge.open_processing_report()

    @Slot()
    def open_downloads_folder(self) -> bool:
        return self.file_bridge.open_downloads_folder()

    @Slot()
    def open_workbook(self) -> bool:
        return self.file_bridge.open_workbook()

    @Slot()
    def stop_current_operation(self) -> None:
        if self.current_worker is not None:
            self.current_worker.stop()
            self.statusChanged.emit("Parada solicitada.")

    def _run_operation(
        self,
        name: str,
        operation: Callable[..., Any],
        *,
        run_async: bool | None = None,
    ) -> None:
        if self.current_worker is not None:
            self.operationFailed.emit("Já existe uma operação em execução.")
            return
        self.operationStarted.emit(name)
        worker = AutomationWorker(
            operation,
            stage=name,
            run_async=self.run_async if run_async is None else run_async,
        )
        self.current_worker = worker
        worker.progress.connect(self.progress_bridge.callback)
        worker.finished.connect(lambda result, operation_name=name: self._finish(operation_name, result))
        worker.failed.connect(lambda error, operation_name=name: self._fail(operation_name, error))
        worker.start()

    def _finish(self, operation_name: str, result: Any) -> None:
        self.current_worker = None
        payload = _safe_payload(result)
        self._update_runtime_state(operation_name, payload)
        self.summaryChanged.emit(payload)
        self.dashboardChanged.emit(self._dashboard_payload())
        if self.recent_protocols:
            self.protocolsChanged.emit(self.recent_protocols)
        self.operationFinished.emit(operation_name)
        self.statusChanged.emit("Operação concluída.")

    def _fail(self, operation_name: str, error: OperationError | dict[str, Any]) -> None:
        self.current_worker = None
        rendered = format_operation_error(error)
        self.operationFailed.emit(rendered)
        self.statusChanged.emit(f"Falha em {operation_name}.")
        if operation_name == "test_cdp_connection":
            self.cdp_status = "Erro"
            self.dashboardChanged.emit(self._dashboard_payload())

    def _check_environment(self) -> dict[str, Any]:
        runner = self.runners.get("check_environment")
        if runner:
            return _as_payload(runner())
        return run_preflight(self.settings, real_run=False).to_dict()

    def _test_cdp_connection(self) -> dict[str, Any]:
        runner = self.runners.get("test_cdp_connection")
        if runner:
            return _as_payload(runner())
        return run_preflight(self.settings, real_run=False, require_cdp=True).to_dict()

    def _run_dry_run(self, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
        runner = self.runners.get("run_dry_run")
        if runner:
            return _as_payload(_call_runner(runner, progress_callback=progress_callback))
        return ProcessDownloadedPdfsUseCase(self.settings).execute(dry_run=True)

    def _run_production(self, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
        runner = self.runners.get("run_production")
        if runner:
            return _as_payload(_call_runner(runner, progress_callback=progress_callback))
        from automacao_gd.application.full_pipeline import run_full_cdp_pipeline

        production_settings = self.settings.model_copy(update={"DRY_RUN": False})
        return run_full_cdp_pipeline(
            production_settings,
            progress_callback=progress_callback,
        )

    def _run_cleanup_dry_run(self) -> dict[str, Any]:
        runner = self.runners.get("run_cleanup_dry_run")
        if runner:
            return _as_payload(runner())
        temp_root = self.settings.resolve_path(Path("data/temp"))
        if not temp_root.exists():
            report = {
                "dry_run": True,
                "scanned_count": 0,
                "deleted_count": 0,
                "skipped_count": 0,
                "total_bytes_candidate": 0,
                "total_bytes_deleted": 0,
                "candidates": [],
                "deleted_files": [],
                "skipped_files": [],
                "warnings": ["Diretório de temporários não encontrado. Nenhum arquivo foi apagado."],
                "errors": [],
            }
            self.logMessage.emit("Cleanup dry-run: nenhum arquivo foi apagado.")
            return report
        report = CleanupTemporaryFilesUseCase(self.settings).execute(dry_run=True)
        self.logMessage.emit(format_cleanup_summary(report))
        return report

    def _emit_placeholder(self, message: str) -> None:
        safe_message = str(sanitize_for_console(message))
        self.logMessage.emit(safe_message)
        self.operationFinished.emit("placeholder")

    def _dashboard_payload(self) -> dict[str, Any]:
        return sanitize_for_console(
            {
                "system_status": self.system_status,
                "cdp_status": self.cdp_status,
                "mode": "Simulação" if self.settings.DRY_RUN else "Produção",
                "batch_label": f"{self.settings.MAX_COMPLETED_TO_PROCESS} protocolos",
                "max_completed_to_process": self.settings.MAX_COMPLETED_TO_PROCESS,
                "max_portal_pages": self.settings.MAX_PORTAL_PAGES,
                "enable_portal_pagination": self.settings.ENABLE_PORTAL_PAGINATION,
                "skip_already_completed": self.settings.SKIP_ALREADY_COMPLETED,
                "apply_excel": self.settings.APPLY_EXCEL,
                "apply_archive": self.settings.APPLY_ARCHIVE,
                "backup_excel": self.settings.BACKUP_EXCEL,
                "reports": _reports_status(self.settings),
            }
        )

    def _recent_protocols_payload(self) -> list[dict[str, Any]]:
        if self.recent_protocols:
            return self.recent_protocols[:10]
        return _load_recent_protocols_from_reports(self.settings, limit=10)

    def _update_runtime_state(self, operation_name: str, payload: dict[str, Any]) -> None:
        if operation_name == "check_environment":
            ready = bool(payload.get("ready", payload.get("success", True)))
            self.system_status = "Pronto" if ready else "Pendente"
        elif operation_name == "test_cdp_connection":
            ready = bool(payload.get("ready", payload.get("success", False)))
            self.cdp_status = "Conectado" if ready else "Não conectado"

        protocols = _extract_protocol_rows(payload)
        if protocols:
            self.recent_protocols = protocols[:10]


def production_confirmation_text(settings: Settings) -> str:
    return "\n".join(
        [
            "A execução em produção pode alterar a planilha e arquivar documentos.",
            "Confirme que:",
            "- o Edge está aberto com CDP;",
            "- a planilha está fechada;",
            f"- BACKUP_EXCEL={settings.BACKUP_EXCEL};",
            f"- PLANILHA_PATH={settings.planilha_path};",
            f"- CLIENTES_ROOT={settings.clientes_root_path};",
            "",
            f"Digite exatamente: {PRODUCTION_CONFIRMATION}",
        ]
    )


def _safe_payload(result: Any) -> dict[str, Any]:
    return sanitize_for_console(_as_payload(result))


def _as_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, OperationResult):
        return {"success": result.success, "message": result.message, **result.payload}
    if isinstance(result, dict):
        return result
    return {"result": result}


def _call_runner(
    runner: Callable[..., Any],
    *,
    progress_callback: ProgressCallback | None,
) -> Any:
    try:
        import inspect

        signature = inspect.signature(runner)
        if "progress_callback" in signature.parameters:
            return runner(progress_callback=progress_callback)
    except (TypeError, ValueError):
        pass
    return runner()


def _reports_status(settings: Settings) -> dict[str, bool]:
    logs_dir = settings.logs_dir_path
    return {
        "pipeline": (logs_dir / "pipeline_cdp_completo.md").exists(),
        "processing": (logs_dir / "processamento_pdfs_planilha_clientes.md").exists(),
        "logs_folder": logs_dir.exists(),
        "workbook": settings.planilha_path.exists(),
    }


def _load_recent_protocols_from_reports(
    settings: Settings,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    logs_dir = settings.logs_dir_path
    candidates = [
        logs_dir / "pipeline_cdp_completo.json",
        logs_dir / "processamento_pdfs_planilha_clientes.json",
    ]
    rows: list[dict[str, Any]] = []
    for path in candidates:
        rows.extend(_extract_protocol_rows(_read_json(path)))
        if len(rows) >= limit:
            break
    return rows[:limit]


def _read_json(path: Path) -> Any:
    try:
        if not path.exists() or not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _extract_protocol_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _iter_candidate_rows(payload):
        protocol = _first_value(row, "protocol", "protocolo", "protocol_number", "numero_protocolo")
        if not protocol:
            continue
        rows.append(
            sanitize_for_console(
                {
                    "protocol": str(protocol),
                    "client": str(_first_value(row, "client", "cliente", "client_name", "nome_cliente") or ""),
                    "pdf": str(
                        _first_value(
                            row,
                            "pdf",
                            "pdf_status",
                            "download_status",
                            "pdf_state",
                        )
                        or _pdf_label(row)
                    ),
                    "excel": str(
                        _first_value(
                            row,
                            "excel",
                            "excel_status",
                            "excel_action",
                            "action",
                            "acao",
                        )
                        or ""
                    ),
                    "archive": str(
                        _first_value(
                            row,
                            "archive",
                            "arquivo",
                            "archive_status",
                            "archive_state",
                            "destination",
                        )
                        or ""
                    ),
                    "status": str(_first_value(row, "status", "result", "resultado") or _status_label(row)),
                }
            )
        )
    return rows


def _iter_candidate_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    rows: list[dict[str, Any]] = []
    for key in (
        "protocols",
        "protocol_results",
        "results",
        "processed",
        "processed_protocols",
        "recent_protocols",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    summary = payload.get("summary")
    if isinstance(summary, dict):
        rows.extend(_iter_candidate_rows(summary))
    return rows


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _pdf_label(row: dict[str, Any]) -> str:
    if row.get("pdf_reused") or row.get("reused_pdf"):
        return "Reutilizado"
    if row.get("pdf_downloaded") or row.get("downloaded"):
        return "Baixado"
    return ""


def _status_label(row: dict[str, Any]) -> str:
    error = row.get("error") or row.get("erro")
    if error:
        return "Erro"
    if row.get("dry_run") or row.get("simulation"):
        return "Simulação"
    return "OK"
