from datetime import datetime
from pathlib import Path

from automacao_gd.domain.models import PortalSolicitation


VALID_DOWNLOAD_STATUSES_FOR_PROCESSING = {"downloaded", "existing_pdf_after_skip"}
METADATA_ONLY_DOWNLOAD_STATUSES_FOR_PROCESSING = {"budget_unavailable"}


def initial_download_summary(
    started_at: datetime,
    downloads_root: Path,
    max_completed: int,
    reprocess_existing_pdfs: bool,
    process_existing_after_skip: bool,
) -> dict:
    return {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": None,
        "downloads_root": str(downloads_root),
        "max_completed_to_process": max_completed,
        "enable_portal_pagination": None,
        "max_portal_pages": None,
        "skip_already_completed": None,
        "reprocess_existing_pdfs": reprocess_existing_pdfs,
        "process_existing_after_skip": process_existing_after_skip,
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
        "total_skipped_origin_page_unavailable": 0,
        "total_for_processing": 0,
        "total_sent_to_processing": 0,
        "total_metadata_only_for_processing": 0,
        "total_budget_unavailable": 0,
        "total_cdp_errors": 0,
        "total_download_errors": 0,
        "total_errors": 0,
        "run_error": None,
        "aborted": False,
        "abort_reason": None,
        "selected_protocols": [],
        "duplicates_skipped": [],
        "already_completed_skipped": [],
        "pagination_warnings": [],
        "pagination_enabled": False,
        "pagination_stop_reason": None,
        "pagination_next_found": False,
        "pagination_click_attempts": 0,
        "pagination_mode": None,
        "pagination_initial_active_page": None,
        "pagination_reset_to_first_page": None,
        "pagination_current_page": None,
        "pagination_target_page": None,
        "pagination_numeric_links_found": [],
        "pagination_diagnostics": [],
        "results": [],
    }


def download_result_from_record(record: PortalSolicitation) -> dict:
    return {
        "protocol": record.protocol,
        "client_name": record.client_name,
        "status": record.status,
        "consumer_unit_code": record.consumer_unit_code,
        "address": record.address,
        "entry_date": record.entry_date,
        "entry_date_raw": record.entry_date,
        "page_number": record.page_number,
        "row_index": record.row_index,
        "selection_reason": record.selection_reason,
        "detail_protocol": None,
        "detail_client_name": None,
        "is_completed": False,
        "completion_date": None,
        "completion_date_raw": None,
        "completion_date_normalized": None,
        "completion_source_stage": None,
        "completion_source_selector": None,
        "completion_extraction_status": None,
        "has_connection_budget": None,
        "download_status": "pending",
        "existing_pdf_path": None,
        "skipped_download": False,
        "skip_reason": None,
        "downloaded_pdf_path": None,
        "process_pdf_path": None,
        "selected_for_processing": False,
        "processing_reason": None,
        "metadata_path": None,
        "module_excel": None,
        "inverter_excel": None,
        "generation_data": None,
        "multiple_module_models": False,
        "multiple_inverter_models": False,
        "module_pairs_count": 0,
        "inverter_pairs_count": 0,
        "equipment_parse_warning": None,
        "previous_state": None,
        "previous_last_step": None,
        "abriu_detalhe": False,
        "motivo_nao_abriu_detalhe": None,
        "retorno_listagem_status": None,
        "metodo_retorno_listagem": None,
        "origin_page_navigation": None,
        "origin_page_navigation_status": None,
        "protocol_found_on_origin_page": None,
        "url_antes_detalhe": None,
        "url_depois_detalhe": None,
        "url_apos_retorno": None,
        "cdp_error": None,
        "download_error": None,
        "navigation_error": None,
        "error": None,
    }


def result_has_valid_pdf_for_processing(result: dict) -> bool:
    return (
        result.get("download_status") in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING
        and _is_valid_pdf(result.get("process_pdf_path"))
    )


def result_is_metadata_only_for_processing(result: dict) -> bool:
    return (
        result.get("download_status") in METADATA_ONLY_DOWNLOAD_STATUSES_FOR_PROCESSING
        and bool(result.get("selected_for_processing"))
        and bool(
            result.get("completion_date")
            or result.get("completion_date_raw")
            or result.get("completion_date_normalized")
        )
    )


def refresh_download_totals(summary: dict) -> None:
    results = summary["results"]
    summary["total_processed"] = len(results)
    summary["total_downloaded"] = sum(
        1 for result in results if result.get("download_status") == "downloaded"
    )
    summary["total_existing_reused"] = sum(
        1
        for result in results
        if result.get("download_status") == "existing_pdf_after_skip"
    )
    summary["total_skipped_existing"] = sum(
        1 for result in results if result.get("skipped_download")
    )
    summary["total_skipped_duplicate"] = len(summary.get("duplicates_skipped", []))
    summary["total_skipped_origin_page_unavailable"] = sum(
        1
        for result in results
        if result.get("download_status") == "skipped_origin_page_unavailable"
    )
    summary["total_for_processing"] = sum(
        1 for result in results if result_has_valid_pdf_for_processing(result)
    )
    summary["total_metadata_only_for_processing"] = sum(
        1 for result in results if result_is_metadata_only_for_processing(result)
    )
    summary["total_budget_unavailable"] = sum(
        1 for result in results if result.get("download_status") == "budget_unavailable"
    )
    summary["total_sent_to_processing"] = (
        summary["total_for_processing"] + summary["total_metadata_only_for_processing"]
    )
    summary["total_cdp_errors"] = sum(
        1
        for result in results
        if result.get("cdp_error") or result.get("navigation_error")
    )
    summary["total_download_errors"] = sum(
        1 for result in results if result.get("download_error")
    )
    summary["total_errors"] = (
        summary["total_cdp_errors"] + summary["total_download_errors"]
    )


def _is_valid_pdf(path: Path | str | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    return candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".pdf"
