import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from automacao_gd.infrastructure.portal.cdp_service import (
    connect_to_existing_edge,
    download_completed_budgets_from_current_page,
    find_portal_page_from_cdp,
)
from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import logger, setup_logger
from automacao_gd.infrastructure.locking import ExecutionLock, ExecutionLockError
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text
from automacao_gd.infrastructure.state.pipeline_state import PipelineStateStore
from automacao_gd.application.contracts import (
    OperationStatus,
    ProgressCallback,
    ProgressTracker,
)
from automacao_gd.application.preflight import run_preflight
from automacao_gd.application.operational_guard import (
    OperationalLockProof,
    global_execution_lock_path,
)
from automacao_gd.application.processing_service import process_downloaded_pdfs
from automacao_gd.application.shareable_reports import build_shareable_report
from automacao_gd.application.reconciliation_service import (
    ReconciliationResult,
    reconcile_portal_workbook,
    reconciliation_summary,
    save_reconciliation_reports,
)
from automacao_gd.domain.errors import PreflightBlockedError


PIPELINE_JSON_REPORT_NAME = "pipeline_cdp_completo.json"
PIPELINE_MARKDOWN_REPORT_NAME = "pipeline_cdp_completo.md"
DOWNLOAD_JSON_REPORT_NAME = "downloads_orcamentos_concluidos_cdp.json"
PIPELINE_SHAREABLE_JSON_REPORT_NAME = "pipeline_cdp_shareable.json"
PIPELINE_SHAREABLE_MARKDOWN_REPORT_NAME = "pipeline_cdp_shareable.md"
DOWNLOAD_SHAREABLE_JSON_REPORT_NAME = "downloads_cdp_shareable.json"
DOWNLOAD_SHAREABLE_MARKDOWN_REPORT_NAME = "downloads_cdp_shareable.md"
VALID_DOWNLOAD_STATUSES_FOR_PROCESSING = {"downloaded", "existing_pdf_after_skip"}
CONTROLLED_PRODUCTION_AUTHORIZATION_SCOPE = "CONTROLLED_PRODUCTION_V2_0_1"
CONTROLLED_PRODUCTION_UP_TO_60_AUTHORIZATION_SCOPE = (
    "CONTROLLED_PRODUCTION_OPTION5_UP_TO_60"
)
SYNTHETIC_BATCH10_AUTHORIZATION_SCOPE = "SYNTHETIC_BATCH10_VALIDATION"


class BatchAuthorizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StrongConfirmationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FrozenBatchScopeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BatchAuthorizationPolicy:
    authorized_max_protocols: int
    authorization_scope: str


@dataclass(frozen=True)
class BatchAuthorization:
    requested_batch_limit: int
    authorized_batch_limit: int
    authorization_scope: str


@dataclass(frozen=True)
class FrozenProtocolBatch:
    requested_limit: int
    authorized_limit: int
    authorization_scope: str
    protocols: tuple[str, ...]
    unique_before_limit: int
    dropped_by_limit: int
    duplicate_protocols_in_frozen_batch: int = 0
    protocols_added_after_freeze: int = 0

    def __post_init__(self) -> None:
        if self.requested_limit <= 0 or self.requested_limit > self.authorized_limit:
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "Limites do lote congelado são inválidos.",
            )
        if len(self.protocols) > self.requested_limit:
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "Lote congelado excede o limite solicitado.",
            )
        if len(self.protocols) != len(set(self.protocols)):
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "Lote congelado contém protocolos duplicados.",
            )

    def validate_phase_protocols(self, protocols: list[str], *, phase: str) -> None:
        unknown = sorted({protocol for protocol in protocols if protocol not in self.protocols})
        if unknown:
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                f"Fase {phase} tentou processar protocolos fora do lote congelado.",
            )


@dataclass(frozen=True)
class FrozenPdfArtifact:
    protocol: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class FrozenPdfScope:
    artifacts: tuple[FrozenPdfArtifact, ...]
    digest: str

    def validate(self) -> None:
        for artifact in self.artifacts:
            if not artifact.path.is_file() or _file_sha256(artifact.path) != artifact.sha256:
                raise FrozenBatchScopeError(
                    "FROZEN_BATCH_SCOPE_VIOLATION",
                    "PDF do lote congelado foi removido ou alterado.",
                )


@dataclass(frozen=True)
class LimitedProtocolSelection:
    summary: dict
    frozen_batch: FrozenProtocolBatch


def default_batch_authorization_policy() -> BatchAuthorizationPolicy:
    return BatchAuthorizationPolicy(
        authorized_max_protocols=5,
        authorization_scope=CONTROLLED_PRODUCTION_AUTHORIZATION_SCOPE,
    )


def batch_authorization_policy_from_settings(settings: object) -> BatchAuthorizationPolicy:
    raw_authorized = getattr(settings, "OPTION5_AUTHORIZED_MAX_PROTOCOLS", 5)
    try:
        authorized_max_protocols = int(raw_authorized or 0)
    except (TypeError, ValueError) as exc:
        raise BatchAuthorizationError(
            "BATCH_LIMIT_NOT_AUTHORIZED",
            "Limite autorizado para opÃ§Ã£o 5 deve ser numÃ©rico.",
        ) from exc

    if 1 <= authorized_max_protocols <= 5:
        return default_batch_authorization_policy()
    if 5 < authorized_max_protocols <= 60:
        return BatchAuthorizationPolicy(
            authorized_max_protocols=authorized_max_protocols,
            authorization_scope=CONTROLLED_PRODUCTION_UP_TO_60_AUTHORIZATION_SCOPE,
        )
    raise BatchAuthorizationError(
        "BATCH_LIMIT_NOT_AUTHORIZED",
        "Limite autorizado para opÃ§Ã£o 5 deve estar entre 1 e 60.",
    )


def validate_requested_batch_limit(
    requested_batch_limit: int,
    policy: BatchAuthorizationPolicy | None = None,
) -> BatchAuthorization:
    active_policy = policy or default_batch_authorization_policy()
    requested = int(requested_batch_limit or 0)
    authorized = int(active_policy.authorized_max_protocols or 0)
    if requested <= 0 or authorized <= 0 or requested > authorized:
        raise BatchAuthorizationError(
            "BATCH_LIMIT_NOT_AUTHORIZED",
            "Limite de protocolos solicitado não está autorizado.",
        )
    return BatchAuthorization(
        requested_batch_limit=requested,
        authorized_batch_limit=authorized,
        authorization_scope=active_policy.authorization_scope,
    )


def build_option5_strong_confirmation(protocol_limit: int) -> str:
    limit = int(protocol_limit)
    if limit <= 0:
        raise BatchAuthorizationError(
            "BATCH_LIMIT_NOT_AUTHORIZED",
            "Limite de protocolos inválido para confirmação forte.",
        )
    return f"APLICAR OPÇÃO 5 COM CONCLUSÃO EM {limit} PROTOCOLOS"


def validate_option5_strong_confirmation(
    confirmation: str,
    authorization: BatchAuthorization,
) -> bool:
    expected = build_option5_strong_confirmation(authorization.requested_batch_limit)
    if confirmation != expected:
        raise StrongConfirmationError(
            "STRONG_CONFIRMATION_MISMATCH",
            "Confirmação forte não corresponde ao lote autorizado.",
        )
    return True


def main() -> int:
    from automacao_gd.presentation.controller import ApplicationController
    from automacao_gd.presentation.operational_output import print_operation_summary

    ensure_directories()
    setup_logger()
    settings = get_settings()

    if not confirm_real_run_if_needed(settings):
        print("Pipeline CDP completo cancelado.")
        return 2

    authorization = validate_requested_batch_limit(
        getattr(settings, "MAX_COMPLETED_TO_PROCESS", 0),
        batch_authorization_policy_from_settings(settings),
    )
    confirmation = build_option5_strong_confirmation(
        authorization.requested_batch_limit
    )
    result = ApplicationController(settings).run_pipeline(confirmation=confirmation)
    print_operation_summary("pipeline", result)
    status = (
        result.status
        if isinstance(result.status, OperationStatus)
        else OperationStatus(str(result.status))
    )
    return {
        OperationStatus.SUCESSO: 0,
        OperationStatus.FALHOU: 1,
        OperationStatus.BLOQUEADO: 2,
        OperationStatus.PARCIAL: 3,
    }[status]


def run_full_cdp_pipeline(
    settings: Settings | None = None,
    progress_callback: ProgressCallback | None = None,
    confirmation: str | None = None,
) -> dict:
    settings = settings or get_settings()
    requested_batch_limit = getattr(settings, "MAX_COMPLETED_TO_PROCESS", 5)
    lock_path = global_execution_lock_path(settings)
    try:
        authorization = validate_requested_batch_limit(
            requested_batch_limit,
            batch_authorization_policy_from_settings(settings),
        )
    except BatchAuthorizationError as exc:
        raise PreflightBlockedError(
            code=exc.code,
            user_message=str(exc),
            stage="autorização do lote",
            technical_cause=exc.code,
        ) from exc
    try:
        validate_option5_strong_confirmation(str(confirmation or ""), authorization)
    except StrongConfirmationError as exc:
        raise PreflightBlockedError(
            code=exc.code,
            user_message=str(exc),
            stage="confirmacao operacional",
            technical_cause=exc.code,
        ) from exc
    execution_id = uuid4().hex
    try:
        with ExecutionLock(
            lock_path,
            execution_id=execution_id,
            operation="option5",
            requested_batch_limit=authorization.requested_batch_limit,
            authorization_scope=authorization.authorization_scope,
        ) as execution_lock:
            lock_proof = OperationalLockProof(
                lock=execution_lock,
                path=lock_path.resolve(strict=False),
                execution_id=execution_id,
                operation="option5",
                requested_limit=authorization.requested_batch_limit,
                authorization_scope=authorization.authorization_scope,
            )
            return _run_full_cdp_pipeline_locked(
                settings=settings,
                progress_callback=progress_callback,
                authorization=authorization,
                execution_id=execution_id,
                lock_proof=lock_proof,
            )
    except ExecutionLockError as exc:
        raise PreflightBlockedError(
            code=exc.code,
            user_message=str(exc),
            stage="lock global de execução",
            technical_cause=exc.code,
        ) from exc


def _run_full_cdp_pipeline_locked(
    *,
    settings: Settings,
    progress_callback: ProgressCallback | None,
    authorization: BatchAuthorization,
    execution_id: str,
    lock_proof: OperationalLockProof,
) -> dict:
    progress = ProgressTracker(progress_callback)
    progress.start("Iniciando pipeline CDP.")
    if not settings.CDP_MODE:
        progress.finish_failed("Pipeline CDP exige CDP_MODE=true.")
        raise PreflightBlockedError(
            code="CDP_MODE_REQUIRED",
            user_message="O pipeline CDP exige CDP_MODE=true.",
            stage="pré-voo",
        )
    progress.advance(
        stage="preflight",
        overall_percent=2,
        stage_percent=40,
        message="Validando ambiente.",
    )
    preflight = run_preflight(
        settings, real_run=not settings.DRY_RUN, require_cdp=True
    )
    if not preflight.ready:
        progress.finish_failed("Pré-voo reprovado.")
        preflight.raise_if_blocked()
    progress.advance(
        stage="preflight",
        overall_percent=5,
        stage_percent=100,
        message="Pré-voo aprovado.",
    )
    started_at = datetime.now()
    logger.info("Iniciando pipeline CDP completo.")
    logger.info(
        "Configuração efetiva pipeline CDP: "
        f"CDP_MODE={str(settings.CDP_MODE).lower()}; "
        f"CDP_ENDPOINT={settings.CDP_ENDPOINT}; "
        f"ENABLE_PORTAL_PAGINATION={settings.ENABLE_PORTAL_PAGINATION}; "
        f"MAX_PORTAL_PAGES={settings.MAX_PORTAL_PAGES}; "
        f"MAX_COMPLETED_TO_PROCESS={settings.MAX_COMPLETED_TO_PROCESS}; "
        f"SKIP_ALREADY_COMPLETED={settings.SKIP_ALREADY_COMPLETED}."
    )
    state_store = PipelineStateStore(
        path=settings.pipeline_state_path,
        resume=settings.RESUME_PIPELINE,
        reset=settings.RESET_PIPELINE_STATE,
        force_reprocess_protocols=settings.force_reprocess_protocols,
    )
    state_store.start_run(
        {
            "dry_run": settings.DRY_RUN,
            "apply_excel": settings.APPLY_EXCEL,
            "apply_archive": settings.APPLY_ARCHIVE,
            "max_completed_to_process": settings.MAX_COMPLETED_TO_PROCESS,
            "enable_portal_pagination": settings.ENABLE_PORTAL_PAGINATION,
            "max_portal_pages": settings.MAX_PORTAL_PAGES,
        }
    )

    progress.advance(
        stage="cdp_connection",
        overall_percent=10,
        stage_percent=100,
        message="Conexão CDP validada para início do download.",
    )
    download_summary = _run_download_step(settings, state_store)
    limited_selection = apply_authorized_global_protocol_limit(
        download_summary,
        authorization,
    )
    download_summary = limited_selection.summary
    progress.advance(
        stage="portal_read",
        overall_percent=25,
        stage_percent=100,
        message="Leitura do portal e seleção de protocolos concluídas.",
        current=download_summary.get("total_selected"),
        total=download_summary.get("total_completed"),
    )
    pdf_paths = _pdf_paths_for_processing(download_summary)
    frozen_pdf_scope = freeze_selected_pdf_scope(
        download_summary,
        limited_selection.frozen_batch,
    )
    download_summary["frozen_pdf_scope_digest"] = frozen_pdf_scope.digest
    download_summary["frozen_pdf_scope_count"] = len(frozen_pdf_scope.artifacts)
    download_report_path = _save_download_summary(settings.logs_dir_path, download_summary)
    progress.advance(
        stage="protocol_selection",
        overall_percent=30,
        stage_percent=100,
        message="PDFs definidos para processamento.",
        current=len(pdf_paths),
        total=download_summary.get("total_selected"),
    )

    if download_summary.get("run_error"):
        processing_summary = _empty_processing_summary(settings, pdf_paths)
        progress.advance(
            stage="protocol_processing",
            overall_percent=90,
            stage_percent=100,
            message="Processamento pulado por erro no download.",
        )
    else:
        try:
            frozen_pdf_scope.validate()
        except FrozenBatchScopeError as exc:
            raise PreflightBlockedError(
                code=exc.code,
                user_message=str(exc),
                stage="revalidacao do lote congelado",
                technical_cause=exc.code,
            ) from exc
        processing_summary = process_downloaded_pdfs(
            downloads_root=settings.downloads_dir_path,
            workbook_path=settings.planilha_path,
            clientes_root=settings.clientes_root_path,
            dry_run=settings.DRY_RUN,
            pdf_paths=pdf_paths,
            apply_excel=settings.APPLY_EXCEL,
            apply_archive=settings.APPLY_ARCHIVE,
            state_store=state_store,
            allowed_protocols=set(limited_selection.frozen_batch.protocols),
            lock_proof=lock_proof,
        )
        progress.advance(
            stage="protocol_processing",
            overall_percent=90,
            stage_percent=100,
            message="Processamento dos PDFs concluído.",
            current=processing_summary.get("total_success"),
            total=processing_summary.get("total_pdfs"),
        )

    progress.advance(
        stage="report_generation",
        overall_percent=95,
        stage_percent=50,
        message="Gerando relatórios consolidados.",
    )
    payload = build_pipeline_payload(
        settings=settings,
        started_at=started_at,
        finished_at=datetime.now(),
        download_summary=download_summary,
        processing_summary=processing_summary,
        download_report_path=download_report_path,
        pdf_paths=pdf_paths,
        authorization=authorization,
        execution_id=execution_id,
        global_lock_acquired=True,
    )
    json_path, markdown_path = _persist_pipeline_reports(
        settings.logs_dir_path,
        payload,
    )
    if json_path is not None and markdown_path is not None:
        payload["json_report_path"] = str(json_path)
        payload["markdown_report_path"] = str(markdown_path)
        logger.info(f"Relatorio consolidado JSON salvo em: {json_path}")
        logger.info(f"Relatorio consolidado Markdown salvo em: {markdown_path}")
    if payload["status"] == OperationStatus.SUCESSO.value:
        progress.finish_success("Pipeline concluído.")
    else:
        progress.finish_failed(payload["operation_message"])
    return payload


def _persist_pipeline_reports(
    logs_dir: Path,
    payload: dict,
) -> tuple[Path | None, Path | None]:
    try:
        paths = _save_pipeline_reports(logs_dir, payload)
    except (OSError, RuntimeError, ValueError):
        payload["report_effect"] = "failed"
        payload["manual_action_required"] = True
        if payload.get("status") == OperationStatus.SUCESSO.value:
            payload["status"] = OperationStatus.PARCIAL.value
        payload["operation_message"] = (
            "Efeitos operacionais concluídos, mas a evidência consolidada não foi "
            "persistida; regeneração manual necessária."
        )
        payload["report_error_code"] = "PIPELINE_REPORT_PERSISTENCE_FAILED"
        logger.error("Falha ao persistir relatorios consolidados do pipeline.")
        return None, None
    payload["report_effect"] = "persisted"
    return paths


def confirm_real_run_if_needed(settings) -> bool:
    print("")
    if settings.DRY_RUN:
        print("ATENCAO: a simulacao acessara Portal/CDP e podera baixar PDFs.")
    else:
        print("ATENCAO: DRY_RUN=false.")
        print(f"Planilha configurada: {settings.planilha_path}")
        print(f"Pasta de clientes configurada: {settings.clientes_root_path}")
    try:
        authorization = validate_requested_batch_limit(
            getattr(settings, "MAX_COMPLETED_TO_PROCESS", 0),
            batch_authorization_policy_from_settings(settings),
        )
    except BatchAuthorizationError:
        return False
    required_confirmation = build_option5_strong_confirmation(
        authorization.requested_batch_limit
    )
    print(f"Digite {required_confirmation} para permitir a execucao real.")
    confirmacao = input("Confirmar execucao real? ")
    try:
        validate_option5_strong_confirmation(confirmacao.strip(), authorization)
    except StrongConfirmationError:
        return False
    return True


def _run_download_step(settings, state_store=None) -> dict:
    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        browser = connect_to_existing_edge(playwright, settings.CDP_ENDPOINT)
        page = find_portal_page_from_cdp(browser, settings.PORTAL_GD_URL)
        if page is None:
            raise RuntimeError("Nenhuma aba do Portal GD foi encontrada via CDP.")

        summary = download_completed_budgets_from_current_page(
            page=page,
            downloads_root=settings.downloads_dir_path,
            max_completed=settings.MAX_COMPLETED_TO_PROCESS,
            reprocess_existing_pdfs=settings.REPROCESS_EXISTING_PDFS,
            process_existing_after_skip=settings.PROCESS_EXISTING_AFTER_SKIP,
            state_store=state_store,
            skip_already_completed=settings.SKIP_ALREADY_COMPLETED,
            reconciliation_callback=_reconciliation_callback(settings),
        )
        summary["run_error"] = summary.get("run_error")
        return summary
    except PlaywrightError as exc:
        message = (
            f"Falha de Playwright/CDP. Verifique se o Edge esta aberto em "
            f"{settings.CDP_ENDPOINT}. Detalhe: {exc}"
        )
        logger.error(message)
        return _download_error_summary(settings, message)
    except Exception as exc:
        logger.exception(f"Falha no download CDP do pipeline completo: {exc}")
        return _download_error_summary(settings, str(exc))
    finally:
        if browser:
            logger.info("Encerrando conexao CDP sem fechar o Edge aberto manualmente.")
        if playwright:
            playwright.stop()


def _reconciliation_callback(settings):
    state: dict[str, object] = {}

    def callback(
        stage: str,
        completed_records: list,
        selected_protocols: list[str],
        portal_context: dict | None = None,
    ) -> dict:
        if stage == "before_limit":
            logger.info("Calculando reconciliação global Portal x planilha.")
            result = reconcile_portal_workbook(
                completed_records,
                settings.planilha_path,
                operational_selected_protocols=[],
                pagination=portal_context,
            )
            if portal_context:
                result.portal_summary.update(portal_context)
            state["result"] = result
            state["timestamp"] = datetime.now().strftime("%Y%m%dT%H%M%SZ")
            json_path, markdown_path = save_reconciliation_reports(
                result,
                settings.logs_dir_path,
                timestamp=str(state["timestamp"]),
                filename_prefix="portal_workbook_reconciliation_global",
            )
            logger.info(
                "Reconciliação global salva antes da seleção operacional: "
                f"{markdown_path}"
            )
            summary = reconciliation_summary(result, _display_path(markdown_path))
            summary["json_report_path"] = _display_path(json_path)
            summary["markdown_report_path"] = _display_path(markdown_path)
            summary["decision"] = result.decision
            return summary
        stored_result = state.get("result")
        cached_result = (
            stored_result if isinstance(stored_result, ReconciliationResult) else None
        )
        if cached_result is None:
            cached_result = reconcile_portal_workbook(
                completed_records,
                settings.planilha_path,
                operational_selected_protocols=selected_protocols,
                pagination=portal_context,
            )
            if portal_context:
                cached_result.portal_summary.update(portal_context)
            state["result"] = cached_result
            state.setdefault("timestamp", datetime.now().strftime("%Y%m%dT%H%M%SZ"))
        result = cached_result
        result.operational_batch["operational_protocols_selected"] = len(
            selected_protocols
        )
        result.operational_batch["operational_protocols_processed"] = len(
            selected_protocols
        )
        json_path, markdown_path = save_reconciliation_reports(
            result,
            settings.logs_dir_path,
            timestamp=str(state.get("timestamp")),
            filename_prefix="portal_workbook_reconciliation_global",
        )
        summary = reconciliation_summary(result, _display_path(markdown_path))
        summary["json_report_path"] = _display_path(json_path)
        summary["markdown_report_path"] = _display_path(markdown_path)
        summary["decision"] = result.decision
        return summary

    return callback


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve(strict=False).relative_to(Path.cwd().resolve(strict=False)))
    except (OSError, ValueError):
        return path.name or str(path)


def _download_error_summary(settings, message: str) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "started_at": now,
        "finished_at": now,
        "downloads_root": str(settings.downloads_dir_path),
        "max_completed_to_process": settings.MAX_COMPLETED_TO_PROCESS,
        "enable_portal_pagination": settings.ENABLE_PORTAL_PAGINATION,
        "max_portal_pages": settings.MAX_PORTAL_PAGES,
        "reprocess_existing_pdfs": settings.REPROCESS_EXISTING_PDFS,
        "process_existing_after_skip": settings.PROCESS_EXISTING_AFTER_SKIP,
        "total_pages_read": 0,
        "total_rows": 0,
        "total_completed": 0,
        "total_already_completed_in_state": 0,
        "total_eligible_after_skip": 0,
        "total_force_reprocess": 0,
        "total_selected": 0,
        "total_processed": 0,
        "total_downloaded": 0,
        "total_existing_reused": 0,
        "total_skipped_existing": 0,
        "total_skipped_duplicate": 0,
        "total_for_processing": 0,
        "total_sent_to_processing": 0,
        "total_cdp_errors": 1,
        "total_download_errors": 0,
        "total_errors": 1,
        "run_error": message,
        "selected_protocols": [],
        "duplicates_skipped": [],
        "already_completed_skipped": [],
        "pagination_warnings": [],
        "pagination_stop_reason": "download_step_error",
        "pagination_next_found": False,
        "pagination_click_attempts": 0,
        "pagination_mode": None,
        "pagination_current_page": None,
        "pagination_target_page": None,
        "pagination_numeric_links_found": [],
        "pagination_diagnostics": [],
        "results": [],
    }


def _pdf_paths_for_processing(download_summary: dict) -> list[Path]:
    selected: list[Path] = []
    seen_protocols: set[str] = set()
    seen: set[str] = set()
    for item in download_summary.get("results", []):
        protocol = str(item.get("protocol") or "")
        if not protocol or protocol in seen_protocols:
            continue
        if item.get("download_status") not in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING:
            continue
        if item.get("cdp_error"):
            continue
        raw_path = item.get("process_pdf_path")
        if not raw_path:
            continue
        path = Path(raw_path)
        if not _is_valid_pdf(path):
            continue
        key = str(path.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen_protocols.add(protocol)
        seen.add(key)
        selected.append(path)
    return selected


def freeze_selected_pdf_scope(
    download_summary: dict,
    frozen_batch: FrozenProtocolBatch,
) -> FrozenPdfScope:
    paths = _pdf_paths_for_processing(download_summary)
    if len(paths) != len(frozen_batch.protocols):
        raise FrozenBatchScopeError(
            "FROZEN_BATCH_SCOPE_VIOLATION",
            "PDFs válidos não correspondem ao lote de protocolos congelado.",
        )
    artifacts = tuple(
        FrozenPdfArtifact(
            protocol=protocol,
            path=path.resolve(strict=True),
            sha256=_file_sha256(path),
        )
        for protocol, path in zip(frozen_batch.protocols, paths, strict=True)
    )
    digest_source = "\n".join(
        f"{artifact.protocol}:{artifact.sha256}" for artifact in artifacts
    )
    return FrozenPdfScope(
        artifacts=artifacts,
        digest=hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_authorized_global_protocol_limit(
    download_summary: dict,
    authorization: BatchAuthorization,
) -> LimitedProtocolSelection:
    """Limit the final processing set and freeze the selected protocol batch."""
    limit = authorization.requested_batch_limit
    results = download_summary.get("results") or []
    selected_protocols: list[str] = []
    dropped_protocols: list[str] = []
    seen_processable_protocols: set[str] = set()

    for item in results:
        if not _download_item_sent_to_processing(item):
            item["selected_by_global_limit"] = False
            item.setdefault("global_limit_status", "not_processable")
            continue

        protocol = str(item.get("protocol") or "")
        if not protocol:
            _exclude_from_processing(item, "missing_protocol")
            continue
        if protocol in seen_processable_protocols:
            _exclude_from_processing(item, "duplicate_protocol")
            continue

        seen_processable_protocols.add(protocol)
        if limit > 0 and len(selected_protocols) >= limit:
            _exclude_from_processing(item, "excluded_by_global_limit")
            dropped_protocols.append(protocol)
            continue

        item["selected_by_global_limit"] = True
        item["global_limit_status"] = "selected"
        selected_protocols.append(protocol)

    download_summary["global_protocol_limit"] = limit
    download_summary["global_protocol_limit_enforced"] = True
    download_summary["requested_batch_limit"] = authorization.requested_batch_limit
    download_summary["authorized_batch_limit"] = authorization.authorized_batch_limit
    download_summary["authorization_scope"] = authorization.authorization_scope
    download_summary["protocols_selected_by_global_limit"] = selected_protocols
    download_summary["total_protocols_selected_by_global_limit"] = len(
        selected_protocols
    )
    download_summary["protocols_dropped_by_global_limit"] = dropped_protocols
    download_summary["total_protocols_dropped_by_global_limit"] = len(
        dropped_protocols
    )
    download_summary["protocols_unique_before_limit"] = len(seen_processable_protocols)
    download_summary["protocols_added_after_limit"] = 0
    download_summary["duplicate_protocols_in_frozen_batch"] = 0
    frozen_batch = FrozenProtocolBatch(
        requested_limit=authorization.requested_batch_limit,
        authorized_limit=authorization.authorized_batch_limit,
        authorization_scope=authorization.authorization_scope,
        protocols=tuple(selected_protocols),
        unique_before_limit=len(seen_processable_protocols),
        dropped_by_limit=len(dropped_protocols),
        duplicate_protocols_in_frozen_batch=0,
        protocols_added_after_freeze=0,
    )
    download_summary["frozen_batch_created"] = True
    download_summary["frozen_batch"] = {
        "requested_limit": frozen_batch.requested_limit,
        "authorized_limit": frozen_batch.authorized_limit,
        "authorization_scope": frozen_batch.authorization_scope,
        "protocols": list(frozen_batch.protocols),
        "unique_before_limit": frozen_batch.unique_before_limit,
        "dropped_by_limit": frozen_batch.dropped_by_limit,
        "duplicate_protocols_in_frozen_batch": (
            frozen_batch.duplicate_protocols_in_frozen_batch
        ),
        "protocols_added_after_freeze": frozen_batch.protocols_added_after_freeze,
    }
    _refresh_processing_selection_totals(download_summary)
    return LimitedProtocolSelection(summary=download_summary, frozen_batch=frozen_batch)


def _apply_global_protocol_limit(download_summary: dict, max_protocols: int) -> dict:
    """Compatibility wrapper for the historical limit helper."""
    authorization = BatchAuthorization(
        requested_batch_limit=int(max_protocols or 0),
        authorized_batch_limit=max(1, int(max_protocols or 0)),
        authorization_scope="LEGACY_COMPATIBILITY_LIMIT",
    )
    return apply_authorized_global_protocol_limit(download_summary, authorization).summary


def _exclude_from_processing(item: dict, status: str) -> None:
    item["selected_by_global_limit"] = False
    item["global_limit_status"] = status
    item["process_pdf_path"] = None
    item["selected_for_processing"] = False
    item["processing_reason"] = status


def _refresh_processing_selection_totals(download_summary: dict) -> None:
    results = download_summary.get("results") or []
    total_for_processing = sum(1 for item in results if _download_item_sent_to_processing(item))
    download_summary["total_for_processing"] = total_for_processing
    download_summary["total_sent_to_processing"] = total_for_processing


def _is_valid_pdf(path: Path) -> bool:
    return path.exists() and path.is_file() and path.suffix.lower() == ".pdf"


def build_pipeline_payload(
    settings,
    started_at: datetime,
    finished_at: datetime,
    download_summary: dict,
    processing_summary: dict,
    download_report_path: Path,
    pdf_paths: list[Path],
    authorization: BatchAuthorization | None = None,
    execution_id: str | None = None,
    global_lock_acquired: bool = False,
) -> dict:
    protocol_rows = _build_protocol_rows(download_summary, processing_summary)
    totals = _build_pipeline_totals(download_summary, processing_summary, protocol_rows)
    payload = {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "dry_run": settings.DRY_RUN,
        "apply_excel": settings.APPLY_EXCEL,
        "apply_archive": settings.APPLY_ARCHIVE,
        "max_completed_to_process": settings.MAX_COMPLETED_TO_PROCESS,
        "requested_batch_limit": (
            authorization.requested_batch_limit
            if authorization
            else settings.MAX_COMPLETED_TO_PROCESS
        ),
        "authorized_batch_limit": (
            authorization.authorized_batch_limit
            if authorization
            else settings.MAX_COMPLETED_TO_PROCESS
        ),
        "authorization_scope": (
            authorization.authorization_scope if authorization else "LEGACY_COMPATIBILITY"
        ),
        "strong_confirmation_contract": build_option5_strong_confirmation(
            authorization.requested_batch_limit
            if authorization
            else settings.MAX_COMPLETED_TO_PROCESS
        ),
        "global_lock_acquired": global_lock_acquired,
        "global_lock_status": "acquired" if global_lock_acquired else "not_acquired",
        "global_lock_waited": False,
        "execution_id": execution_id,
        "batch_10_authorized_for_production": False,
        "synthetic_validation": False,
        "enable_portal_pagination": settings.ENABLE_PORTAL_PAGINATION,
        "max_portal_pages": settings.MAX_PORTAL_PAGES,
        "reprocess_existing_pdfs": settings.REPROCESS_EXISTING_PDFS,
        "process_existing_after_skip": settings.PROCESS_EXISTING_AFTER_SKIP,
        "resume_pipeline": settings.RESUME_PIPELINE,
        "skip_already_completed": settings.SKIP_ALREADY_COMPLETED,
        "cache_client_folder_lookup": settings.CACHE_CLIENT_FOLDER_LOOKUP,
        "force_reprocess_protocols": sorted(settings.force_reprocess_protocols),
        "reset_pipeline_state": settings.RESET_PIPELINE_STATE,
        "cdp_endpoint": settings.CDP_ENDPOINT,
        "downloads_root": str(settings.downloads_dir_path),
        "workbook_path": str(settings.planilha_path),
        "clientes_root": str(settings.clientes_root_path),
        "download_report_path": str(download_report_path),
        "total_pdfs_for_processing": len(pdf_paths),
        "pdfs_for_processing": [str(path) for path in pdf_paths],
        "protocol_results": protocol_rows,
        "download": download_summary,
        "processing": processing_summary,
        "run_error": download_summary.get("run_error"),
        **totals,
    }
    status, operation_message = _classify_pipeline_result(payload)
    payload["status"] = status.value
    payload["operation_message"] = operation_message
    return payload


def _classify_pipeline_result(payload: dict) -> tuple[OperationStatus, str]:
    downloaded = int(payload.get("total_downloaded", 0) or 0)
    reused = int(payload.get("total_existing_reused", 0) or 0)
    processed = int(payload.get("total_processed_success", 0) or 0)
    updated = int(payload.get("total_excel_updated", 0) or 0)
    errors = int(payload.get("total_errors", 0) or 0)
    selected = int(payload.get("total_selected", 0) or 0)
    eligible = int(payload.get("total_eligible_after_skip", 0) or 0)
    processing = payload.get("processing") or {}
    blocked_after_download = bool(processing.get("blocked_real_run"))
    useful_work = downloaded + reused + processed + updated

    if blocked_after_download:
        if processing.get("real_run_block_code") == "NO_SAFE_PROTOCOLS_TO_APPLY":
            return (
                OperationStatus.BLOQUEADO,
                "Nenhum protocolo seguro para aplicação após a triagem técnica.",
            )
        message = (
            "O portal foi processado, mas a gravação da planilha foi bloqueada. "
            "Os PDFs permanecem disponíveis para retomada."
        )
        status = OperationStatus.PARCIAL if downloaded + reused else OperationStatus.BLOQUEADO
        return status, message
    if payload.get("run_error"):
        if payload.get("download", {}).get("abort_reason") == "PORTAL_PAGINATION_INCOMPLETE":
            return (
                OperationStatus.BLOQUEADO,
                "Reconciliação global não concluída; páginas do Portal ainda não lidas.",
            )
        status = OperationStatus.PARCIAL if useful_work else OperationStatus.FALHOU
        return status, "O pipeline foi interrompido por uma falha operacional."
    if errors:
        if processed or updated:
            return OperationStatus.PARCIAL, "O pipeline terminou com resultados parciais."
        return OperationStatus.FALHOU, "Nenhum PDF foi processado com sucesso."
    if not payload.get("dry_run", True) and (selected or downloaded or reused) and not processed:
        return (
            OperationStatus.FALHOU,
            "Nenhum PDF selecionado foi processado com sucesso.",
        )
    if selected == 0 and eligible == 0:
        return OperationStatus.SUCESSO, "Nenhuma atualização necessária."
    return OperationStatus.SUCESSO, "Pipeline CDP concluído com sucesso."


def _build_protocol_rows(download_summary: dict, processing_summary: dict) -> list[dict]:
    processing_by_protocol = {
        str(item.get("protocol")): item
        for item in processing_summary.get("results", [])
        if item.get("protocol")
    }
    download_by_protocol = {
        str(item.get("protocol")): item
        for item in download_summary.get("results", [])
        if item.get("protocol")
    }

    rows: list[dict] = []
    seen: set[str] = set()
    selected_protocols = download_summary.get("selected_protocols") or []
    for selected in selected_protocols:
        protocol = str(selected.get("protocol") or "")
        if not protocol or protocol in seen:
            continue
        seen.add(protocol)
        download_item = download_by_protocol.get(protocol, {})
        processing_item = processing_by_protocol.get(protocol)
        rows.append(_protocol_row(selected, download_item, processing_item))

    for protocol, download_item in download_by_protocol.items():
        if protocol in seen:
            continue
        seen.add(protocol)
        rows.append(_protocol_row(download_item, download_item, processing_by_protocol.get(protocol)))

    for skipped in download_summary.get("already_completed_skipped") or []:
        protocol = str(skipped.get("protocol") or "")
        if not protocol or protocol in seen:
            continue
        seen.add(protocol)
        item = {
            **skipped,
            "download_status": "skipped_already_completed",
            "previous_state": skipped.get("previous_state") or "completed",
        }
        rows.append(_protocol_row(item, item, None))

    for duplicate in download_summary.get("duplicates_skipped") or []:
        protocol = str(duplicate.get("protocol") or "")
        if not protocol or protocol in seen:
            continue
        seen.add(protocol)
        item = {
            **duplicate,
            "download_status": "skipped_duplicate_in_run",
        }
        rows.append(_protocol_row(item, item, None))

    return rows


def _protocol_row(selected: dict, download_item: dict, processing_item: dict | None) -> dict:
    sent_to_processing = _download_item_sent_to_processing(download_item)
    if processing_item is None:
        processing_result = "not_sent" if not sent_to_processing else "not_processed"
        excel_action = None
        excel_state = None
        archive_state = None
        client_folder_status = None
        processing_error = None
        archive_destination_folder = None
        archive_match_type = None
        archive_reason = None
        archive_created_folder = None
        archive_fallback_mode = None
        legacy_gd_ignored = None
        arquivo_final = None
        completion_action = None
        completion_reason = None
    else:
        processing_result = "success" if processing_item.get("success") else "error"
        excel_status = processing_item.get("excel_status", {})
        archive_status = processing_item.get("archive_status", {})
        excel_action = processing_item.get("action") or excel_status.get("action")
        excel_state = excel_action or ("success" if excel_status.get("success") else "error")
        archive_state = _archive_label(archive_status)
        client_folder_status = _client_folder_label(processing_item)
        processing_error = processing_item.get("error")
        archive_destination_folder = processing_item.get("archive_destination_folder")
        archive_match_type = processing_item.get("archive_match_type")
        archive_reason = processing_item.get("archive_reason")
        archive_created_folder = processing_item.get("archive_created_folder")
        archive_fallback_mode = processing_item.get("archive_fallback_mode")
        legacy_gd_ignored = processing_item.get("legacy_gd_ignored")
        arquivo_final = processing_item.get("arquivo_final")
        completion_action = processing_item.get("completion_action")
        completion_reason = processing_item.get("completion_reason")

    error = _first_non_empty(
        download_item.get("cdp_error"),
        download_item.get("navigation_error"),
        download_item.get("download_error"),
        processing_error,
    )
    outcome = _row_outcome(download_item, processing_item, sent_to_processing)
    last_step = _row_last_step(download_item, processing_item, outcome)

    return {
        "protocol": selected.get("protocol") or download_item.get("protocol"),
        "client_name": (
            download_item.get("detail_client_name")
            or download_item.get("client_name")
            or selected.get("client_name")
        ),
        "previous_state": download_item.get("previous_state") or selected.get("previous_state"),
        "previous_last_step": (
            download_item.get("previous_last_step")
            or selected.get("previous_last_step")
        ),
        "page_number": download_item.get("page_number") or selected.get("page_number"),
        "row_index": download_item.get("row_index") or selected.get("row_index"),
        "selection_reason": (
            download_item.get("selection_reason")
            or selected.get("selection_reason")
            or "-"
        ),
        "last_step": last_step,
        "download_status": download_item.get("download_status") or "not_processed",
        "sent_to_processing": sent_to_processing,
        "processing_result": processing_result,
        "excel_action": excel_action,
        "excel_state": excel_state,
        "completion_current": (
            processing_item.get("excel_status", {}).get("current_completion")
            if processing_item
            else None
        ),
        "completion_raw": (
            processing_item.get("completion_raw")
            if processing_item
            else download_item.get("completion_date_raw")
        ),
        "completion_normalized": (
            processing_item.get("completion_normalized")
            if processing_item
            else download_item.get("completion_date_normalized")
        ),
        "completion_source_stage": (
            processing_item.get("completion_source_stage")
            if processing_item
            else download_item.get("completion_source_stage")
        ),
        "completion_extraction_status": (
            processing_item.get("completion_extraction_status")
            if processing_item
            else download_item.get("completion_extraction_status")
        ),
        "completion_action": completion_action,
        "completion_reason": completion_reason,
        "completion_excel_updated": completion_action
        in {"COMPLETION_DATE_UPDATED", "MARKED_AS_OPEN"},
        "archive_state": archive_state,
        "archive_destination_folder": archive_destination_folder,
        "archive_match_type": archive_match_type,
        "archive_reason": archive_reason,
        "archive_created_folder": archive_created_folder,
        "archive_fallback_mode": archive_fallback_mode,
        "legacy_gd_ignored": legacy_gd_ignored,
        "arquivo_final": arquivo_final,
        "client_folder_status": client_folder_status,
        "outcome": outcome,
        "abriu_detalhe": bool(download_item.get("abriu_detalhe")),
        "motivo_nao_abriu_detalhe": download_item.get("motivo_nao_abriu_detalhe"),
        "retorno_listagem_status": download_item.get("retorno_listagem_status"),
        "metodo_retorno_listagem": download_item.get("metodo_retorno_listagem"),
        "origin_page_navigation": download_item.get("origin_page_navigation"),
        "origin_page_navigation_status": download_item.get(
            "origin_page_navigation_status"
        ),
        "protocol_found_on_origin_page": download_item.get(
            "protocol_found_on_origin_page"
        ),
        "url_antes_detalhe": download_item.get("url_antes_detalhe"),
        "url_depois_detalhe": download_item.get("url_depois_detalhe"),
        "url_apos_retorno": download_item.get("url_apos_retorno"),
        "cdp_error": download_item.get("cdp_error") or download_item.get("navigation_error"),
        "download_error": download_item.get("download_error"),
        "processing_error": processing_error,
        "error": error,
        "process_pdf_path": download_item.get("process_pdf_path"),
    }


def _download_item_sent_to_processing(download_item: dict) -> bool:
    raw_path = download_item.get("process_pdf_path")
    return bool(
        download_item.get("download_status") in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING
        and raw_path
        and not download_item.get("cdp_error")
        and _is_valid_pdf(Path(raw_path))
    )


def _client_folder_label(processing_item: dict) -> str | None:
    match_type = processing_item.get("client_folder_match_type")
    if not match_type:
        return None
    if processing_item.get("client_folder_cache_hit"):
        return f"{match_type} (cache)"
    return str(match_type)


def _archive_label(archive_status: dict) -> str | None:
    if not archive_status:
        return None
    if archive_status.get("reason") == "archive_already_done":
        return "archive_already_done"
    if archive_status.get("skipped"):
        return archive_status.get("reason") or "skipped"
    if archive_status.get("success"):
        return "archived" if not archive_status.get("simulated") else "dry_run"
    return "failed"


def _row_outcome(
    download_item: dict,
    processing_item: dict | None,
    sent_to_processing: bool,
) -> str:
    download_status = download_item.get("download_status")
    if download_status == "skipped_already_completed":
        return "skipped_already_completed"
    if download_status == "skipped_duplicate_in_run":
        return "skipped_duplicate_in_run"
    if download_item.get("retorno_listagem_status") == "failed_return_to_listing":
        return "failed_return_to_listing"
    if download_item.get("cdp_error") or download_item.get("navigation_error"):
        return "failed_cdp_navigation"
    if download_item.get("download_error"):
        return "failed_download"
    if processing_item is None:
        return "not_processed" if sent_to_processing else "not_sent"
    if not processing_item.get("success"):
        excel_status = processing_item.get("excel_status", {})
        archive_status = processing_item.get("archive_status", {})
        if excel_status.get("error"):
            return "failed_excel"
        if archive_status.get("error"):
            return "failed_archive"
        return "failed_processing"
    if _row_was_resumed(download_item):
        if download_status == "existing_pdf_after_skip":
            return "resumed_from_pdf_reused"
        return "resumed"
    return "completed"


def _row_last_step(
    download_item: dict,
    processing_item: dict | None,
    outcome: str,
) -> str:
    if outcome == "skipped_already_completed":
        return download_item.get("previous_last_step") or "completed"
    if outcome == "skipped_duplicate_in_run":
        return "selected"
    if outcome.startswith("failed"):
        return "failed"
    if processing_item is not None:
        if processing_item.get("success"):
            return "completed"
        return "failed"

    download_status = download_item.get("download_status")
    if download_status == "downloaded":
        return "pdf_downloaded"
    if download_status == "existing_pdf_after_skip":
        return "pdf_reused"
    if download_status == "pending":
        return "selected"
    return str(download_status or "-")


def _row_was_resumed(download_item: dict) -> bool:
    previous_state = download_item.get("previous_state")
    previous_last_step = download_item.get("previous_last_step")
    return bool(
        previous_state
        and previous_state not in {"pending", "skipped_already_completed"}
        and previous_last_step
    )


def _first_non_empty(*values) -> str | None:
    for value in values:
        if value:
            return str(value)
    return None


def _processing_item_is_pending_review(item: dict) -> bool:
    return (
        bool(item.get("technical_review_required"))
        or item.get("action") == "pending_technical_review"
        or item.get("technical_validation_status") == "pending_review"
    )


def _processing_item_is_batch_policy_block(item: dict) -> bool:
    if item.get("success") or item.get("technical_validation_status") != "approved":
        return False
    marker = " ".join(
        str(value or "")
        for value in (
            item.get("error"),
            item.get("warning"),
            item.get("real_run_block_code"),
            item.get("real_run_skipped_reason"),
        )
    )
    return "FROZEN_BATCH_SCOPE_VIOLATION" in marker or "BATCH_POLICY" in marker


def _processing_item_is_application_error(item: dict) -> bool:
    excel_status = item.get("excel_status") or {}
    archive_status = item.get("archive_status") or {}
    return bool(excel_status.get("error") or archive_status.get("error"))


def _build_pipeline_totals(
    download_summary: dict, processing_summary: dict, protocol_rows: list[dict]
) -> dict:
    processing_results = list(processing_summary.get("results", []))
    total_pending_review = int(
        processing_summary.get(
            "total_pending_review",
            processing_summary.get("total_pending_protocols", 0),
        )
        or 0
    )
    total_blocked_by_batch_policy = int(
        processing_summary.get("total_blocked_by_batch_policy", 0) or 0
    ) or sum(
        1 for item in processing_results if _processing_item_is_batch_policy_block(item)
    )
    total_real_application_errors = int(
        processing_summary.get("total_real_application_errors", 0) or 0
    ) or sum(
        1
        for item in processing_results
        if item.get("error")
        and not _processing_item_is_pending_review(item)
        and not _processing_item_is_batch_policy_block(item)
        and _processing_item_is_application_error(item)
    )
    total_real_extraction_errors = int(
        processing_summary.get("total_real_extraction_errors", 0) or 0
    ) or (
        sum(
            1
            for row in protocol_rows
            if row.get("outcome") in {"failed_cdp_navigation", "failed_download"}
        )
        + sum(
            1
            for item in processing_results
            if item.get("error")
            and not _processing_item_is_pending_review(item)
            and not _processing_item_is_batch_policy_block(item)
            and not _processing_item_is_application_error(item)
        )
    )
    if download_summary.get("run_error"):
        total_real_extraction_errors += 1
    total_errors = (
        total_pending_review
        + total_real_extraction_errors
        + total_real_application_errors
    )

    return {
        "total_rows": download_summary.get("total_rows", 0),
        "total_pages_read": download_summary.get("total_pages_read", 0),
        "pagination_stop_reason": download_summary.get("pagination_stop_reason"),
        "pagination_complete": download_summary.get("pagination_complete"),
        "last_page_confirmed": download_summary.get("last_page_confirmed"),
        "last_page_number": download_summary.get("last_page_number"),
        "next_page_available_after_stop": download_summary.get(
            "next_page_available_after_stop"
        ),
        "pagination_safety_cap": download_summary.get("pagination_safety_cap"),
        "pages_visited": download_summary.get("pages_visited", []),
        "pagination_next_found": download_summary.get("pagination_next_found", False),
        "pagination_click_attempts": download_summary.get(
            "pagination_click_attempts", 0
        ),
        "pagination_mode": download_summary.get("pagination_mode"),
        "pagination_current_page": download_summary.get("pagination_current_page"),
        "pagination_target_page": download_summary.get("pagination_target_page"),
        "pagination_numeric_links_found": download_summary.get(
            "pagination_numeric_links_found", []
        ),
        "pagination_initial_active_page": download_summary.get(
            "pagination_initial_active_page"
        ),
        "pagination_reset_to_first_page": download_summary.get(
            "pagination_reset_to_first_page"
        ),
        "total_completed": download_summary.get("total_completed", 0),
        "total_already_completed_in_state": download_summary.get(
            "total_already_completed_in_state", 0
        ),
        "total_eligible_after_skip": download_summary.get(
            "total_eligible_after_skip", download_summary.get("total_selected", 0)
        ),
        "total_force_reprocess": download_summary.get("total_force_reprocess", 0),
        "total_selected": download_summary.get("total_selected", 0),
        "global_protocol_limit": download_summary.get("global_protocol_limit", 0),
        "total_protocols_selected_by_global_limit": download_summary.get(
            "total_protocols_selected_by_global_limit",
            sum(1 for row in protocol_rows if row.get("sent_to_processing")),
        ),
        "total_protocols_dropped_by_global_limit": download_summary.get(
            "total_protocols_dropped_by_global_limit", 0
        ),
        "protocols_unique_before_limit": download_summary.get(
            "protocols_unique_before_limit",
            download_summary.get("total_protocols_selected_by_global_limit", 0),
        ),
        "protocols_added_after_limit": download_summary.get(
            "protocols_added_after_limit",
            processing_summary.get("protocols_added_after_freeze", 0),
        ),
        "duplicate_protocols_in_frozen_batch": download_summary.get(
            "duplicate_protocols_in_frozen_batch", 0
        ),
        "frozen_batch_created": bool(download_summary.get("frozen_batch_created")),
        "total_skipped_already_completed": max(
            int(download_summary.get("total_already_completed_in_state", 0) or 0),
            sum(
                1
                for row in protocol_rows
                if row.get("download_status") == "skipped_already_completed"
            ),
        ),
        "total_resumed": sum(
            1
            for row in protocol_rows
            if str(row.get("outcome", "")).startswith("resumed")
        ),
        "total_downloaded": sum(
            1 for row in protocol_rows if row["download_status"] == "downloaded"
        ),
        "total_existing_reused": sum(
            1
            for row in protocol_rows
            if row["download_status"] == "existing_pdf_after_skip"
        ),
        "total_skipped_duplicate": download_summary.get("total_skipped_duplicate", 0),
        "total_duplicates_removed": download_summary.get("total_skipped_duplicate", 0),
        "total_cdp_errors": max(
            int(download_summary.get("total_cdp_errors", 0) or 0),
            sum(1 for row in protocol_rows if row.get("cdp_error")),
        ),
        "total_download_errors": max(
            int(download_summary.get("total_download_errors", 0) or 0),
            sum(1 for row in protocol_rows if row.get("download_error")),
        ),
        "total_sent_to_processing": sum(
            1 for row in protocol_rows if row.get("sent_to_processing")
        ),
        "total_processed_success": processing_summary.get("total_success", 0),
        "total_processed_errors": processing_summary.get("total_errors", 0),
        "total_pdfs_analyzed": processing_summary.get(
            "total_pdfs_analyzed", processing_summary.get("total_pdfs", 0)
        ),
        "total_technically_approved": processing_summary.get(
            "total_technically_approved", 0
        ),
        "total_safe_protocols": processing_summary.get("total_safe_protocols", 0),
        "total_no_change_protocols": processing_summary.get(
            "total_no_change_protocols", 0
        ),
        "total_pending_protocols": processing_summary.get(
            "total_pending_protocols", 0
        ),
        "total_failed_protocols": processing_summary.get("total_failed_protocols", 0),
        "total_updates_planned": processing_summary.get("total_updates_planned", 0),
        "total_updates_applied": processing_summary.get("total_updates_applied", 0),
        "total_blocked_by_batch_policy": total_blocked_by_batch_policy,
        "total_real_extraction_errors": total_real_extraction_errors,
        "total_real_application_errors": total_real_application_errors,
        "total_pdfs_retained_for_retry": processing_summary.get(
            "total_pdfs_retained_for_retry", 0
        ),
        "total_excel_updated": processing_summary.get("total_excel_updated", 0),
        "total_archived": processing_summary.get("total_archived", 0),
        "total_pending_review": total_pending_review,
        "total_client_folder_cache_hits": processing_summary.get(
            "total_client_folder_cache_hits", 0
        ),
        "total_client_folder_searches": processing_summary.get(
            "total_client_folder_searches", 0
        ),
        "total_excel_already_updated": processing_summary.get(
            "total_excel_already_updated", 0
        ),
        "total_archive_already_done": processing_summary.get(
            "total_archive_already_done", 0
        ),
        "total_completion_dates_found": processing_summary.get(
            "total_completion_dates_found", 0
        ),
        "total_completion_dates_updated": processing_summary.get(
            "total_completion_dates_updated", 0
        ),
        "total_completion_marked_open": processing_summary.get(
            "total_completion_marked_open", 0
        ),
        "completion_dates_proposed": processing_summary.get(
            "completion_dates_proposed", 0
        ),
        "completion_dates_applied": processing_summary.get(
            "completion_dates_applied", 0
        ),
        "open_values_proposed": processing_summary.get("open_values_proposed", 0),
        "open_values_applied": processing_summary.get("open_values_applied", 0),
        "total_completion_no_change": processing_summary.get(
            "total_completion_no_change", 0
        ),
        "total_completion_pending_review": processing_summary.get(
            "total_completion_pending_review", 0
        ),
        "total_errors": total_errors,
        "reconciliation": download_summary.get("reconciliation") or {},
    }


def _empty_processing_summary(settings, pdf_paths: list[Path]) -> dict:
    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": settings.DRY_RUN,
        "apply_excel": settings.APPLY_EXCEL,
        "apply_archive": settings.APPLY_ARCHIVE,
        "downloads_root": str(settings.downloads_dir_path),
        "workbook_path": str(settings.planilha_path),
        "clientes_root": str(settings.clientes_root_path),
        "total_pdfs": len(pdf_paths),
        "total_pdfs_analyzed": 0,
        "total_technically_approved": 0,
        "total_safe_protocols": 0,
        "total_no_change_protocols": 0,
        "total_pending_protocols": 0,
        "total_failed_protocols": len(pdf_paths),
        "total_updates_planned": 0,
        "total_updates_applied": 0,
        "total_pdfs_retained_for_retry": 0,
        "total_success": 0,
        "total_errors": len(pdf_paths),
        "total_excel_updated": 0,
        "total_archived": 0,
        "total_pending_review": 0,
        "total_client_folder_cache_hits": 0,
        "total_client_folder_searches": 0,
        "total_excel_already_updated": 0,
        "total_archive_already_done": 0,
        "blocked_real_run": False,
        "real_run_block_reason": None,
        "results": [],
        "json_report_path": None,
        "markdown_report_path": None,
    }


def _save_download_summary(logs_dir: Path, summary: dict) -> Path:
    output_path = logs_dir / DOWNLOAD_JSON_REPORT_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, summary, private=True)
    shareable = build_shareable_report(summary, report_type="download")
    atomic_write_json(logs_dir / DOWNLOAD_SHAREABLE_JSON_REPORT_NAME, shareable, private=True)
    atomic_write_text(
        logs_dir / DOWNLOAD_SHAREABLE_MARKDOWN_REPORT_NAME,
        _build_shareable_markdown(shareable),
        private=True,
    )
    return output_path


def _save_pipeline_reports(logs_dir: Path, payload: dict) -> tuple[Path, Path]:
    json_path = logs_dir / PIPELINE_JSON_REPORT_NAME
    markdown_path = logs_dir / PIPELINE_MARKDOWN_REPORT_NAME
    json_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(json_path, payload, private=True)
    atomic_write_text(markdown_path, _build_markdown_report(payload), private=True)
    shareable = build_shareable_report(payload, report_type="pipeline")
    atomic_write_json(
        logs_dir / PIPELINE_SHAREABLE_JSON_REPORT_NAME,
        shareable,
        private=True,
    )
    atomic_write_text(
        logs_dir / PIPELINE_SHAREABLE_MARKDOWN_REPORT_NAME,
        _build_shareable_markdown(shareable),
        private=True,
    )
    return json_path, markdown_path


def _build_shareable_markdown(payload: dict) -> str:
    lines = [
        "# Relatorio compartilhavel",
        "",
        f"- Classificacao: {payload['classification']}",
        f"- Tipo: {payload['report_type']}",
        f"- Status: {payload['status']}",
        f"- Modo: {payload['mode']}",
        f"- Bloqueado: {payload['blocked']}",
        "",
        "## Totais",
    ]
    lines.extend(f"- {key}: {value}" for key, value in payload["totals"].items())
    return "\n".join(lines) + "\n"


def _build_markdown_report(payload: dict) -> str:
    lines = [
        "# Pipeline CDP Completo",
        "",
        "## Resumo",
        "",
        f"- Inicio: {payload['started_at']}",
        f"- Fim: {payload['finished_at']}",
        f"- DRY_RUN: {payload['dry_run']}",
        f"- APPLY_EXCEL: {payload['apply_excel']}",
        f"- APPLY_ARCHIVE: {payload['apply_archive']}",
        f"- MAX_COMPLETED_TO_PROCESS: {payload['max_completed_to_process']}",
        f"- requested_batch_limit: {payload.get('requested_batch_limit')}",
        f"- authorized_batch_limit: {payload.get('authorized_batch_limit')}",
        f"- authorization_scope: {payload.get('authorization_scope')}",
        "- strong_confirmation_contract: "
        f"{payload.get('strong_confirmation_contract')}",
        f"- global_lock_acquired: {payload.get('global_lock_acquired')}",
        f"- global_lock_status: {payload.get('global_lock_status')}",
        f"- frozen_batch_created: {payload.get('frozen_batch_created')}",
        f"- protocols_unique_before_limit: {payload.get('protocols_unique_before_limit')}",
        f"- protocols_added_after_limit: {payload.get('protocols_added_after_limit')}",
        "- duplicate_protocols_in_frozen_batch: "
        f"{payload.get('duplicate_protocols_in_frozen_batch')}",
        "- batch_10_authorized_for_production: "
        f"{payload.get('batch_10_authorized_for_production')}",
        f"- ENABLE_PORTAL_PAGINATION: {payload['enable_portal_pagination']}",
        f"- MAX_PORTAL_PAGES: {payload['max_portal_pages']}",
        f"- pagination_stop_reason: {payload.get('pagination_stop_reason')}",
        f"- pagination_next_found: {payload.get('pagination_next_found')}",
        f"- pagination_click_attempts: {payload.get('pagination_click_attempts')}",
        f"- pagination_mode: {payload.get('pagination_mode')}",
        f"- pagination_current_page: {payload.get('pagination_current_page')}",
        f"- pagination_target_page: {payload.get('pagination_target_page')}",
        f"- pagination_numeric_links_found: {payload.get('pagination_numeric_links_found')}",
        f"- pagination_initial_active_page: {payload.get('pagination_initial_active_page')}",
        f"- pagination_reset_to_first_page: {payload.get('pagination_reset_to_first_page')}",
        f"- REPROCESS_EXISTING_PDFS: {payload['reprocess_existing_pdfs']}",
        f"- PROCESS_EXISTING_AFTER_SKIP: {payload['process_existing_after_skip']}",
        f"- Total paginas lidas: {payload['total_pages_read']}",
        f"- Total linhas lidas em todas as paginas: {payload['total_rows']}",
        f"- Total concluidas em todas as paginas: {payload['total_completed']}",
        f"- Total ja completed no state: {payload['total_already_completed_in_state']}",
        f"- Total elegiveis apos skip: {payload['total_eligible_after_skip']}",
        f"- Total forcados por FORCE_REPROCESS_PROTOCOLS: {payload['total_force_reprocess']}",
        f"- Total selecionadas: {payload['total_selected']}",
        "- Total selecionado pelo limite global: "
        f"{payload.get('total_protocols_selected_by_global_limit', 0)}",
        "- Total excluido pelo limite global: "
        f"{payload.get('total_protocols_dropped_by_global_limit', 0)}",
        f"- Total ja concluidos pulados: {payload['total_skipped_already_completed']}",
        f"- Total retomados: {payload['total_resumed']}",
        f"- Total baixadas: {payload['total_downloaded']}",
        f"- Total existentes reutilizadas: {payload['total_existing_reused']}",
        f"- Total hits cache pasta cliente: {payload['total_client_folder_cache_hits']}",
        f"- Total buscas pasta cliente: {payload['total_client_folder_searches']}",
        f"- Total Excel ja atualizado: {payload['total_excel_already_updated']}",
        f"- Total atualizacoes reais na planilha: {payload['total_excel_updated']}",
        f"- Total PDFs arquivados: {payload['total_archived']}",
        f"- Total arquivos ja arquivados: {payload['total_archive_already_done']}",
        *_completion_summary_markdown_lines(payload),
        f"- Total erros: {payload['total_errors']}",
        f"- Total de linhas: {payload['total_rows']}",
        f"- Total duplicados evitados: {payload['total_skipped_duplicate']}",
        f"- Total duplicados removidos: {payload['total_duplicates_removed']}",
        f"- Total erros CDP: {payload['total_cdp_errors']}",
        f"- Total erros download: {payload['total_download_errors']}",
        f"- Total enviado ao processamento: {payload['total_sent_to_processing']}",
        f"- Total PDFs analisados: {payload.get('total_pdfs_analyzed', 0)}",
        f"- Total tecnicamente aprovados: {payload.get('total_technically_approved', 0)}",
        f"- Total protocolos seguros: {payload.get('total_safe_protocols', 0)}",
        f"- Total protocolos sem alteração: {payload.get('total_no_change_protocols', 0)}",
        f"- Total protocolos pendentes: {payload.get('total_pending_protocols', 0)}",
        f"- Total protocolos falhos: {payload.get('total_failed_protocols', 0)}",
        f"- Total updates planejados: {payload.get('total_updates_planned', 0)}",
        f"- Total updates aplicados: {payload.get('total_updates_applied', 0)}",
        f"- Total bloqueados por politica de lote: {payload.get('total_blocked_by_batch_policy', 0)}",
        f"- Total erros reais de extracao: {payload.get('total_real_extraction_errors', 0)}",
        f"- Total erros reais de aplicacao: {payload.get('total_real_application_errors', 0)}",
        f"- Total PDFs mantidos para retomada: {payload.get('total_pdfs_retained_for_retry', 0)}",
        f"- Total sucesso processamento: {payload['total_processed_success']}",
        f"- Total erros processamento: {payload['total_processed_errors']}",
        f"- Total pendencias de pasta: {payload['total_pending_review']}",
        f"- Relatorio de download: {payload['download_report_path']}",
        f"- Relatorio offline JSON: {payload['processing'].get('json_report_path')}",
        f"- Relatorio offline Markdown: {payload['processing'].get('markdown_report_path')}",
        "",
        "## Protocolos",
        "",
        "| Protocolo | Cliente | Pagina | Linha | Navegacao origem | Status origem | Protocolo na origem | Motivo selecao | Estado anterior | Ultima etapa | Download | Pasta cliente | Excel | Arquivo | Destino arquivo | Match arquivo | Fallback | Criou pasta | Legado ignorado | Arquivo final | Resultado | Erro |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for item in payload.get("protocol_results", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.get("protocol")),
                    _md(item.get("client_name")),
                    _md(item.get("page_number") or "-"),
                    _md(item.get("row_index") if item.get("row_index") is not None else "-"),
                    _md(item.get("origin_page_navigation") or "-"),
                    _md(item.get("origin_page_navigation_status") or "-"),
                    _md(item.get("protocol_found_on_origin_page")),
                    _md(item.get("selection_reason") or "-"),
                    _md(item.get("previous_state") or "-"),
                    _md(item.get("last_step") or "-"),
                    _md(_download_label(item)),
                    _md(item.get("client_folder_status") or "-"),
                    _md(item.get("excel_state") or "-"),
                    _md(item.get("archive_state") or "-"),
                    _md(item.get("archive_destination_folder") or "-"),
                    _md(item.get("archive_match_type") or "-"),
                    _md(item.get("archive_fallback_mode") or "-"),
                    _md(item.get("archive_created_folder")),
                    _md(item.get("legacy_gd_ignored")),
                    _md(item.get("arquivo_final") or "-"),
                    _md(item.get("outcome") or "-"),
                    _md(item.get("error") or "-"),
                ]
            )
            + " |"
        )

    if payload.get("run_error"):
        lines.extend(["", "## Erro geral", "", f"- {payload['run_error']}"])

    return "\n".join(lines) + "\n"


def _completion_summary_markdown_lines(payload: dict) -> list[str]:
    dry_run = bool(payload.get("dry_run", True))
    lines = [
        f"- Datas de conclusao encontradas: {payload.get('total_completion_dates_found', 0)}",
    ]
    if dry_run:
        lines.extend(
            [
                f"- Conclusoes propostas com data: {payload.get('completion_dates_proposed', 0)}",
                f"- Conclusoes propostas EM ABERTO: {payload.get('open_values_proposed', 0)}",
            ]
        )
    else:
        lines.extend(
            [
                f"- Conclusoes atualizadas com data: {payload.get('completion_dates_applied', 0)}",
                f"- Conclusoes marcadas EM ABERTO: {payload.get('open_values_applied', 0)}",
            ]
        )
    lines.extend(
        [
            f"- Conclusoes sem alteracao: {payload.get('total_completion_no_change', 0)}",
            f"- Conclusoes pendentes: {payload.get('total_completion_pending_review', 0)}",
        ]
    )
    return lines


def _md(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def _download_label(item: dict) -> str:
    status = item.get("download_status") or "-"
    if item.get("motivo_nao_abriu_detalhe"):
        status = f"{status} ({item['motivo_nao_abriu_detalhe']})"
    if item.get("download_error"):
        return f"{status}: {item['download_error']}"
    return str(status)


if __name__ == "__main__":
    raise SystemExit(main())
