import hashlib
import re
from pathlib import Path
from typing import Any

from automacao_gd.application.contracts import OperationStatus


def skipped_excel_status(protocol: str, dry_run: bool) -> dict:
    return {
        "success": True,
        "can_write": False,
        "skipped": True,
        "protocol": protocol,
        "row_found": False,
        "row_number": None,
        "created_new_row": False,
        "backup_path": None,
        "dry_run": dry_run,
        "worksheet": None,
        "target_sheet": None,
        "existing_sheet": None,
        "source_sheet": None,
        "source_row": None,
        "target_row": None,
        "existing_row": None,
        "new_row": None,
        "moved_from": None,
        "moved_to": None,
        "entry_date": None,
        "action": "skipped_apply_excel_false",
        "warning": "Etapa de planilha desativada por APPLY_EXCEL=false.",
        "error": None,
    }


def empty_result(pdf_path: Path) -> dict:
    return {
        "success": False,
        "pdf_path": str(pdf_path),
        "protocol": None,
        "client_name": None,
        "entry_date": None,
        "completion_date": None,
        "metadata_source": None,
        "target_sheet": None,
        "source_sheet": None,
        "source_row": None,
        "target_row": None,
        "existing_row": None,
        "new_row": None,
        "moved_from": None,
        "moved_to": None,
        "action": None,
        "row_number": None,
        "warning": None,
        "module_excel": None,
        "inverter_excel": None,
        "placa_planilha": None,
        "inversor_planilha": None,
        "technical_validation_status": None,
        "technical_review_required": False,
        "technical_validation_errors": [],
        "technical_validation_warnings": [],
        "module_source": None,
        "inverter_source": None,
        "recommended_action": None,
        "client_folder_match_type": None,
        "client_folder_confidence": None,
        "client_folder_cache_hit": False,
        "client_folder_cache_key": None,
        "matched_path": None,
        "client_folder_reason": None,
        "reason": None,
        "found_by": None,
        "protocol_search_hit": None,
        "client_folder_search_elapsed_seconds": None,
        "target_folder": None,
        "archived_pdf_path": None,
        "archive_destination_folder": None,
        "archive_match_type": None,
        "archive_reason": None,
        "archive_created_folder": None,
        "archive_fallback_mode": None,
        "legacy_gd_ignored": None,
        "arquivo_final": None,
        "archive_status": {
            "success": False,
            "simulated": None,
            "skipped": False,
            "error": None,
        },
        "excel_status": {
            "success": False,
            "can_write": False,
            "skipped": False,
            "action": None,
            "target_sheet": None,
            "source_sheet": None,
            "source_row": None,
            "target_row": None,
            "existing_row": None,
            "new_row": None,
            "moved_from": None,
            "moved_to": None,
            "row_found": False,
            "row_number": None,
            "created_new_row": False,
            "backup_path": None,
            "error": None,
        },
        "error": None,
        "excel_effect": "not_applied",
        "archive_effect": "not_applied",
        "state_effect": "not_applied",
        "report_effect": "not_applied",
        "manual_action_required": False,
        "rollback_possible": False,
    }


def blocked_result(pdf_path: Path, error: str) -> dict:
    result = empty_result(pdf_path)
    result["error"] = error
    result["excel_status"]["error"] = error
    result["archive_status"]["error"] = "Arquivamento não executado por falha na planilha."
    return result


def is_protocol_pending_review(item: dict) -> bool:
    return (
        bool(item.get("technical_review_required"))
        or item.get("action") == "pending_technical_review"
        or item.get("technical_validation_status") == "pending_review"
    )


def is_protocol_no_change(item: dict) -> bool:
    excel_status = item.get("excel_status") or {}
    return (
        item.get("action") == "skipped_excel_already_updated"
        or excel_status.get("action") == "skipped_excel_already_updated"
    )


def is_protocol_safe_or_no_change(item: dict, apply_excel: bool) -> bool:
    if is_protocol_pending_review(item):
        return False
    if is_protocol_no_change(item):
        return True
    if item.get("error"):
        return False
    if not apply_excel:
        return True
    excel_status = item.get("excel_status") or {}
    return bool(excel_status.get("can_write")) or bool(excel_status.get("skipped"))


def has_processable_protocols(results: list[dict], apply_excel: bool) -> bool:
    return any(is_protocol_safe_or_no_change(item, apply_excel) for item in results)


def has_excel_updates_to_apply(results: list[dict]) -> bool:
    return any(
        (item.get("excel_status") or {}).get("can_write")
        and not (item.get("excel_status") or {}).get("skipped")
        for item in results
        if not is_protocol_pending_review(item)
    )


def systemic_real_apply_issues(results: list[dict], apply_excel: bool) -> list[dict]:
    if not apply_excel:
        return []
    issues = []
    for item in results:
        if is_protocol_pending_review(item):
            continue
        excel_status = item.get("excel_status") or {}
        if not excel_status.get("success") and not excel_status.get("skipped"):
            issues.append(item)
    return issues


def mark_results_after_rollback(results: list[dict], *, reason: str) -> list[dict]:
    marked = []
    for item in results:
        result = dict(item)
        excel_status = dict(result.get("excel_status") or {})
        if excel_status.get("can_write") and not excel_status.get("skipped"):
            excel_status.update(
                {
                    "success": False,
                    "can_write": False,
                    "error": reason,
                    "rollback_executed": True,
                }
            )
            result["success"] = False
            result["error"] = reason
            result["excel_status"] = excel_status
            result["excel_effect"] = "rolled_back"
            result["manual_action_required"] = result.get("archive_effect") in {
                "archived",
                "already_present",
            }
            result["rollback_possible"] = False
        marked.append(result)
    return marked


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excel_effect(status: dict[str, Any], dry_run: bool, apply_excel: bool) -> str:
    if dry_run or not apply_excel:
        return "not_applied"
    if not status.get("success"):
        return "failed"
    if status.get("skipped") or status.get("action") in {
        "no_change",
        "skipped_excel_already_updated",
    }:
        return "no_change"
    return "applied"


def archive_effect(status: dict[str, Any], dry_run: bool, apply_archive: bool) -> str:
    if dry_run or not apply_archive:
        return "not_applied"
    if status.get("reason") == "archive_already_done":
        return "already_present"
    if status.get("success") and status.get("skipped"):
        return "no_change"
    if status.get("success") and not status.get("skipped"):
        return "archived"
    return "failed"


def processing_metrics(results: list[dict], dry_run: bool, apply_excel: bool) -> dict:
    technically_approved = [
        item for item in results if item.get("technical_validation_status") == "approved"
    ]
    pending = [item for item in results if is_protocol_pending_review(item)]
    blocked_by_batch_policy = [
        item for item in results if is_protocol_blocked_by_batch_policy(item)
    ]
    no_change = [item for item in results if is_protocol_no_change(item)]
    safe = [
        item
        for item in results
        if is_protocol_safe_or_no_change(item, apply_excel)
        and not is_protocol_no_change(item)
    ]
    real_application_errors = [
        item
        for item in results
        if item.get("error")
        and not is_protocol_pending_review(item)
        and not is_protocol_blocked_by_batch_policy(item)
        and is_protocol_application_error(item)
    ]
    real_extraction_errors = [
        item
        for item in results
        if item.get("error")
        and not is_protocol_pending_review(item)
        and not is_protocol_no_change(item)
        and not is_protocol_blocked_by_batch_policy(item)
        and not is_protocol_application_error(item)
    ]
    failed = [
        item
        for item in results
        if item.get("error")
        and not is_protocol_pending_review(item)
        and not is_protocol_no_change(item)
        and not is_protocol_blocked_by_batch_policy(item)
        and not is_protocol_safe_or_no_change(item, apply_excel)
    ]
    updates_planned = sum(
        1
        for item in results
        if (item.get("excel_status") or {}).get("can_write")
        and not (item.get("excel_status") or {}).get("skipped")
    )
    updates_applied = count_excel_updates(results, dry_run, apply_excel)
    return {
        "total_pdfs_analyzed": len(results),
        "total_technically_approved": len(technically_approved),
        "total_safe_protocols": len(safe),
        "total_no_change_protocols": len(no_change),
        "total_pending_protocols": len(pending),
        "total_pending_review": len(pending),
        "total_failed_protocols": len(failed),
        "total_excel_already_updated": len(no_change),
        "total_updates_planned": updates_planned,
        "total_updates_applied": updates_applied,
        "total_blocked_by_batch_policy": len(blocked_by_batch_policy),
        "total_real_extraction_errors": len(real_extraction_errors),
        "total_real_application_errors": len(real_application_errors),
        "total_pdfs_retained_for_retry": len(pending),
    }


def is_protocol_blocked_by_batch_policy(item: dict) -> bool:
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


def is_protocol_application_error(item: dict) -> bool:
    if is_protocol_no_change(item):
        return False
    excel_status = item.get("excel_status") or {}
    archive_status = item.get("archive_status") or {}
    return bool(excel_status.get("error") or archive_status.get("error"))


def real_run_blocked_from_simulation(item: dict, reason: str) -> dict:
    result = dict(item)
    archive_status = dict(result.get("archive_status") or {})
    excel_status = dict(result.get("excel_status") or {})

    issue = item.get("error") or item.get("warning") or excel_status.get("error")
    result["success"] = False
    result["warning"] = issue or result.get("warning")
    result["error"] = reason if not issue else f"{reason} Detalhe: {issue}"
    archive_status.update(
        {
            "success": False,
            "simulated": False,
            "skipped": False,
            "error": "Arquivamento não executado porque a execução real foi bloqueada.",
        }
    )
    excel_status.update(
        {
            "success": False,
            "can_write": False,
            "error": excel_status.get("error") or issue or reason,
        }
    )
    result["archive_status"] = archive_status
    result["excel_status"] = excel_status
    return result


def classify_processing_result(
    *,
    dry_run: bool,
    total_pdfs: int,
    total_success: int,
    total_errors: int,
    blocked_real_run: bool,
    systemic_apply_failure: bool = False,
    partial_effects_present: bool = False,
) -> tuple[OperationStatus, str]:
    if systemic_apply_failure:
        return (
            OperationStatus.FALHOU,
            "Falha sistêmica durante a aplicação; rollback executado quando disponível.",
        )
    if blocked_real_run:
        return OperationStatus.BLOQUEADO, "A gravação real foi bloqueada com segurança."
    if total_errors and total_success:
        return OperationStatus.PARCIAL, "O processamento terminou com resultados parciais."
    if total_errors and partial_effects_present:
        return (
            OperationStatus.PARCIAL,
            "O processamento aplicou efeitos parciais e requer retomada.",
        )
    if total_errors:
        return OperationStatus.FALHOU, "Nenhum PDF foi processado com sucesso."
    if total_pdfs == 0 and total_success == 0:
        return OperationStatus.SUCESSO, "Nenhuma atualização necessária."
    return OperationStatus.SUCESSO, (
        "Simulação concluída com sucesso."
        if dry_run
        else "Processamento concluído com sucesso."
    )


def has_traceable_effects(item: dict[str, Any]) -> bool:
    return (
        item.get("excel_effect") in {"applied", "rolled_back"}
        or item.get("archive_effect") in {"archived", "already_present"}
        or item.get("state_effect") == "persisted"
    )


def compact_excel_status(status: dict[str, Any]) -> dict:
    return {
        "success": status.get("success", False),
        "skipped": status.get("skipped", False),
        "row_found": status.get("row_found", False),
        "row_number": status.get("row_number"),
        "created_new_row": status.get("created_new_row", False),
        "backup_path": status.get("backup_path"),
        "worksheet": status.get("worksheet"),
        "dry_run": status.get("dry_run"),
        "warning": status.get("warning"),
        "error": status.get("error"),
        "can_write": status.get("can_write", False),
        "action": status.get("action"),
        "target_sheet": status.get("target_sheet"),
        "existing_sheet": status.get("existing_sheet"),
        "source_sheet": status.get("source_sheet"),
        "source_row": status.get("source_row"),
        "target_row": status.get("target_row"),
        "existing_row": status.get("existing_row"),
        "new_row": status.get("new_row"),
        "moved_from": status.get("moved_from"),
        "moved_to": status.get("moved_to"),
        "entry_date": status.get("entry_date"),
        "ingress_no_change": status.get("ingress_no_change"),
        "equipment_no_change": status.get("equipment_no_change"),
        "completion_no_change": status.get("completion_no_change"),
        "completion_action": status.get("completion_action"),
    }


def state_excel_payload(
    excel_status: dict[str, Any],
    workbook_path: Path,
    dry_run: bool,
    apply_excel: bool,
) -> dict:
    return {
        "status": state_excel_status(excel_status, dry_run, apply_excel),
        "worksheet": excel_status.get("target_sheet") or excel_status.get("worksheet"),
        "row": (
            excel_status.get("target_row")
            or excel_status.get("row_number")
            or excel_status.get("existing_row")
        ),
        "action": excel_status.get("action"),
        "workbook_path": str(workbook_path),
        "dry_run": dry_run,
        "error": excel_status.get("error"),
        "warning": excel_status.get("warning"),
    }


def state_excel_status(
    excel_status: dict[str, Any],
    dry_run: bool,
    apply_excel: bool,
) -> str:
    if not apply_excel:
        return "skipped_apply_excel_false"
    if excel_status.get("action") == "skipped_excel_already_updated":
        return "already_updated"
    if dry_run and excel_status.get("success"):
        return "skipped_dry_run"
    if excel_status.get("success"):
        return "applied"
    return "failed"


def state_excel_last_step(
    excel_status: dict[str, Any],
    dry_run: bool,
    apply_excel: bool,
) -> str:
    status = state_excel_status(excel_status, dry_run, apply_excel)
    if status == "applied":
        return "excel_applied"
    if excel_status.get("success"):
        return "excel_skipped"
    return "failed"


def state_archive_payload(
    archive_status: dict[str, Any],
    target_folder: Path | None,
    archived_pdf_path: str | None,
    dry_run: bool,
    apply_archive: bool,
) -> dict:
    return {
        "status": state_archive_status(archive_status, dry_run, apply_archive),
        "target_folder": str(target_folder) if target_folder else None,
        "archived_pdf_path": archived_pdf_path,
        "dry_run": dry_run,
        "reason": archive_status.get("reason"),
        "error": archive_status.get("error"),
        "match_type": archive_status.get("match_type"),
        "created_folder": archive_status.get("created_folder"),
        "fallback_mode": archive_status.get("fallback_mode"),
        "legacy_gd_ignored": archive_status.get("legacy_gd_ignored"),
        "source_pdf_sha256": archive_status.get("source_pdf_sha256"),
        "archived_pdf_sha256": archive_status.get("archived_pdf_sha256"),
    }


def state_archive_status(
    archive_status: dict[str, Any],
    dry_run: bool,
    apply_archive: bool,
) -> str:
    if not apply_archive:
        return "skipped_apply_archive_false"
    if archive_status.get("reason") == "archive_already_done":
        return "already_done"
    if dry_run and archive_status.get("success"):
        return "skipped_dry_run"
    if archive_status.get("success"):
        return "archived"
    return "failed"


def state_archive_last_step(
    archive_status: dict[str, Any],
    dry_run: bool,
    apply_archive: bool,
) -> str:
    status = state_archive_status(archive_status, dry_run, apply_archive)
    if status == "archived":
        return "archived"
    if archive_status.get("success"):
        return "archive_skipped"
    return "failed"


def count_excel_updates(results: list[dict], dry_run: bool, apply_excel: bool) -> int:
    if dry_run or not apply_excel:
        return 0
    return sum(
        1
        for item in results
        if item["excel_status"].get("can_write")
        and not item["excel_status"].get("skipped")
    )


def count_archived(results: list[dict], dry_run: bool, apply_archive: bool) -> int:
    if dry_run or not apply_archive:
        return 0
    return sum(
        1
        for item in results
        if item["archive_status"].get("success")
        and not item["archive_status"].get("skipped")
    )


def join_errors(archive_status: dict, excel_status: dict) -> str | None:
    errors = [
        str(error)
        for error in [archive_status.get("error"), excel_status.get("error")]
        if error
    ]
    return "; ".join(errors) if errors else None


def protocol_from_filename(pdf_path: Path) -> str | None:
    match = re.search(r"Orcamento_de_Conexao_(\d+)", pdf_path.stem)
    return match.group(1) if match else None
