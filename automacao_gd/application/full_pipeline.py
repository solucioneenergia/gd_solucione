import json
from datetime import datetime
from pathlib import Path

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
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text
from automacao_gd.infrastructure.state.pipeline_state import PipelineStateStore
from automacao_gd.application.contracts import (
    OperationStatus,
    ProgressCallback,
    ProgressTracker,
)
from automacao_gd.application.preflight import run_preflight
from automacao_gd.application.processing_service import process_downloaded_pdfs
from automacao_gd.domain.errors import PreflightBlockedError


PIPELINE_JSON_REPORT_NAME = "pipeline_cdp_completo.json"
PIPELINE_MARKDOWN_REPORT_NAME = "pipeline_cdp_completo.md"
DOWNLOAD_JSON_REPORT_NAME = "downloads_orcamentos_concluidos_cdp.json"
VALID_DOWNLOAD_STATUSES_FOR_PROCESSING = {"downloaded", "existing_pdf_after_skip"}


def main() -> None:
    from automacao_gd.presentation.controller import ApplicationController
    from automacao_gd.presentation.operational_output import print_operation_summary

    ensure_directories()
    setup_logger()
    settings = get_settings()

    if not confirm_real_run_if_needed(settings):
        print("Pipeline CDP completo cancelado.")
        return

    result = ApplicationController(settings).run_pipeline()
    print_operation_summary("pipeline", result)


def run_full_cdp_pipeline(
    settings: Settings | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    settings = settings or get_settings()
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
    progress.advance(
        stage="portal_read",
        overall_percent=25,
        stage_percent=100,
        message="Leitura do portal e seleção de protocolos concluídas.",
        current=download_summary.get("total_selected"),
        total=download_summary.get("total_completed"),
    )
    download_report_path = _save_download_summary(settings.logs_dir_path, download_summary)
    pdf_paths = _pdf_paths_for_processing(download_summary)
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
        processing_summary = process_downloaded_pdfs(
            downloads_root=settings.downloads_dir_path,
            workbook_path=settings.planilha_path,
            clientes_root=settings.clientes_root_path,
            dry_run=settings.DRY_RUN,
            pdf_paths=pdf_paths,
            apply_excel=settings.APPLY_EXCEL,
            apply_archive=settings.APPLY_ARCHIVE,
            state_store=state_store,
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
    )
    json_path, markdown_path = _save_pipeline_reports(settings.logs_dir_path, payload)
    payload["json_report_path"] = str(json_path)
    payload["markdown_report_path"] = str(markdown_path)
    logger.info(f"Relatorio consolidado JSON salvo em: {json_path}")
    logger.info(f"Relatorio consolidado Markdown salvo em: {markdown_path}")
    if payload["status"] == OperationStatus.SUCESSO.value:
        progress.finish_success("Pipeline concluído.")
    else:
        progress.finish_failed(payload["operation_message"])
    return payload


def confirm_real_run_if_needed(settings) -> bool:
    if settings.DRY_RUN or not (settings.APPLY_EXCEL or settings.APPLY_ARCHIVE):
        return True

    print("")
    print("ATENCAO: DRY_RUN=false.")
    print(f"Planilha configurada: {settings.planilha_path}")
    print(f"Pasta de clientes configurada: {settings.clientes_root_path}")
    print("Digite SIM para permitir atualizacao real de planilha/arquivamento.")
    confirmacao = input("Confirmar execucao real? ")
    return confirmacao.strip().upper() == "SIM"


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


def _build_pipeline_totals(
    download_summary: dict, processing_summary: dict, protocol_rows: list[dict]
) -> dict:
    total_errors = sum(
        1 for row in protocol_rows if str(row.get("outcome", "")).startswith("failed")
    )
    if download_summary.get("run_error"):
        total_errors += 1

    return {
        "total_rows": download_summary.get("total_rows", 0),
        "total_pages_read": download_summary.get("total_pages_read", 0),
        "pagination_stop_reason": download_summary.get("pagination_stop_reason"),
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
        "total_pdfs_retained_for_retry": processing_summary.get(
            "total_pdfs_retained_for_retry", 0
        ),
        "total_excel_updated": processing_summary.get("total_excel_updated", 0),
        "total_archived": processing_summary.get("total_archived", 0),
        "total_pending_review": processing_summary.get("total_pending_review", 0),
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
        "total_errors": total_errors,
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
    return output_path


def _save_pipeline_reports(logs_dir: Path, payload: dict) -> tuple[Path, Path]:
    json_path = logs_dir / PIPELINE_JSON_REPORT_NAME
    markdown_path = logs_dir / PIPELINE_MARKDOWN_REPORT_NAME
    json_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(json_path, payload, private=True)
    atomic_write_text(markdown_path, _build_markdown_report(payload), private=True)
    return json_path, markdown_path


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
    main()
