import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from automacao_gd.application.contracts import OperationStatus
from automacao_gd.application.op5_contracts import (
    BatchAuthorization,
    build_option5_strong_confirmation,
    file_sha256,
)
from automacao_gd.application.op5_selection import refresh_processing_selection_totals
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


OP5_PLAN_JSON_REPORT_NAME = "op5_plan_latest.json"

EXCEL_WRITE_ACTIONS = {
    "insert_new_chronological",
    "update_existing",
    "move_wrong_sheet_to_correct_sheet",
    "reformat_existing_excel_row",
}


def persist_op5_plan(logs_dir: Path, payload: dict) -> tuple[Path, dict]:
    plan = build_op5_plan_payload(payload)
    path = logs_dir / OP5_PLAN_JSON_REPORT_NAME
    atomic_write_json(path, plan)
    return path, plan


def project_accepted_op5_plan_payload(payload: dict, plan: dict) -> None:
    planned_protocols = [
        str(item.get("protocol") or "")
        for item in plan.get("planned_excel_actions") or []
        if str(item.get("protocol") or "")
    ]
    planned_set = set(planned_protocols)
    payload["source_total_selected"] = payload.get("total_selected", 0)
    payload["source_total_updates_planned"] = payload.get("total_updates_planned", 0)
    payload["source_total_eligible_after_skip"] = payload.get(
        "total_eligible_after_skip",
        0,
    )
    payload["source_total_errors"] = payload.get("total_errors", 0)
    for key in (
        "status",
        "total_selected",
        "total_eligible_after_skip",
        "total_updates_planned",
        "total_updates_applied",
        "total_errors",
        "total_pending_review",
        "source_total_no_change_protocols",
        "frozen_batch",
        "frozen_pdf_scope",
        "planned_excel_actions",
        "op5_workbook_coverage",
        "download",
    ):
        if key in plan:
            payload[key] = deepcopy(plan[key])
    payload["total_protocols_selected_by_global_limit"] = len(planned_protocols)
    payload["protocols_selected_by_global_limit"] = planned_protocols
    if not planned_set:
        return
    processing = payload.get("processing")
    if isinstance(processing, dict):
        processing["source_total_updates_planned"] = processing.get(
            "total_updates_planned",
            0,
        )
        processing["results"] = [
            item
            for item in processing.get("results") or []
            if isinstance(item, dict)
            and str(item.get("protocol") or "") in planned_set
        ]
        processing["total_updates_planned"] = len(planned_protocols)
    payload["protocol_results"] = [
        item
        for item in payload.get("protocol_results") or []
        if isinstance(item, dict)
        and str(item.get("protocol") or "") in planned_set
    ]
    payload["op5_workbook_coverage"] = build_op5_workbook_coverage(payload)


def mark_op5_plan_accepted(payload: dict, *, source_status: object) -> None:
    if source_status == OperationStatus.SUCESSO.value:
        return
    payload["op5_plan_accepted_from_partial"] = True
    payload["source_status_before_op5_plan"] = source_status
    payload["status"] = OperationStatus.SUCESSO.value
    payload["operation_message"] = (
        "Plano OP5 congelado gerado com acoes seguras; pendencias fora do "
        "plano foram preservadas no relatorio."
    )


def invalidate_latest_op5_plan(logs_dir: Path, payload: dict) -> Path:
    path = logs_dir / OP5_PLAN_JSON_REPORT_NAME
    run_error = payload.get("run_error") or (payload.get("download") or {}).get(
        "run_error"
    )
    marker: dict[str, object] = {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": True,
        "status": payload.get("status") or OperationStatus.FALHOU.value,
        "stale_after_failed_plan": True,
        "run_error": run_error or "op5_plan_failed",
        "error_code": "OP5_PLAN_COMMAND_FAILED",
        "requested_batch_limit": payload.get("requested_batch_limit"),
        "authorized_batch_limit": payload.get("authorized_batch_limit"),
        "authorization_scope": payload.get("authorization_scope"),
        "total_errors": max(int(payload.get("total_errors", 0) or 0), 1),
        "planned_excel_actions": [],
        "op5_workbook_coverage": build_op5_workbook_coverage(payload),
        "source_report_path": payload.get("json_report_path"),
    }
    atomic_write_json(path, marker)
    return path


def build_op5_plan_payload(payload: dict) -> dict:
    download = deepcopy(payload.get("download") or {})
    processing = payload.get("processing") or {}
    planned_actions = planned_excel_actions(processing)
    requested_limit = int(payload.get("requested_batch_limit", 0) or 0)
    if requested_limit > 0:
        download["requested_batch_limit"] = requested_limit
    if requested_limit > 0:
        planned_actions = planned_actions[:requested_limit]
    download = download_summary_for_planned_actions(download, planned_actions)
    source_total_eligible = int(payload.get("total_eligible_after_skip", 0) or 0)
    source_total_no_change = max(
        int(payload.get("total_no_change_protocols", 0) or 0),
        int(payload.get("total_excel_already_updated", 0) or 0),
    )
    download["source_total_eligible_after_skip"] = source_total_eligible
    download["source_total_no_change_protocols"] = source_total_no_change
    planned_count = len(planned_actions)
    persistable_plan = op5_payload_can_persist_plan(
        payload,
        planned_actions=planned_actions,
    )
    return {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_report_path": payload.get("json_report_path")
        or str(Path(str(payload.get("download_report_path") or ""))),
        "dry_run": True,
        "status": OperationStatus.SUCESSO.value
        if persistable_plan
        else payload.get("status"),
        "source_status": payload.get("status"),
        "requested_batch_limit": payload.get("requested_batch_limit"),
        "authorized_batch_limit": payload.get("authorized_batch_limit"),
        "authorization_scope": payload.get("authorization_scope"),
        "strong_confirmation_contract": payload.get("strong_confirmation_contract"),
        "workbook_sha256": file_sha256(Path(str(payload.get("workbook_path") or ""))),
        "workbook_path": payload.get("workbook_path"),
        "apply_excel": payload.get("apply_excel"),
        "apply_archive": payload.get("apply_archive"),
        "archive_only_local": bool(payload.get("archive_only_local")),
        "source_plan_path": payload.get("source_plan_path"),
        "total_selected": planned_count,
        "total_updates_planned": planned_count,
        "total_updates_applied": payload.get("total_updates_applied", 0),
        "total_errors": 0 if persistable_plan else payload.get("total_errors", 0),
        "source_total_errors": payload.get("total_errors", 0),
        "total_eligible_after_skip": source_total_eligible,
        "source_total_no_change_protocols": source_total_no_change,
        "total_pending_review": processing.get("total_pending_review", 0)
        if isinstance(processing, dict)
        else 0,
        "frozen_batch": download.get("frozen_batch"),
        "frozen_pdf_scope": download.get("frozen_pdf_scope"),
        "planned_excel_actions": planned_actions,
        "op5_workbook_coverage": build_op5_workbook_coverage(
            {
                **payload,
                "planned_excel_actions": planned_actions,
                "download": download,
            }
        ),
        "download": download,
    }


def planned_excel_actions(processing_summary: dict) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for item in processing_summary.get("results") or []:
        if not isinstance(item, dict):
            continue
        protocol = str(item.get("protocol") or "")
        excel_status = item.get("excel_status")
        excel_status = excel_status if isinstance(excel_status, dict) else {}
        action = str(item.get("action") or excel_status.get("action") or "")
        if protocol and action in EXCEL_WRITE_ACTIONS:
            actions.append({"protocol": protocol, "action": action})
    return actions


def build_op5_workbook_coverage(payload: dict) -> dict[str, object]:
    processing = payload.get("processing")
    processing = processing if isinstance(processing, dict) else {}
    download = payload.get("download")
    download = download if isinstance(download, dict) else {}
    reconciliation = payload.get("reconciliation")
    reconciliation = reconciliation if isinstance(reconciliation, dict) else {}
    planned_actions = payload.get("planned_excel_actions")
    if isinstance(planned_actions, list):
        planned_count = len(
            [
                item
                for item in planned_actions
                if isinstance(item, dict) and str(item.get("protocol") or "").strip()
            ]
        )
    else:
        planned_count = len(planned_excel_actions(processing))
    if planned_count == 0:
        planned_count = int(payload.get("total_updates_planned", 0) or 0)
    dry_run = bool(payload.get("dry_run", True))
    applied_count = 0 if dry_run else int(payload.get("total_excel_updated", 0) or 0)
    already_covered = max(
        int(payload.get("source_total_no_change_protocols", 0) or 0),
        int(download.get("source_total_no_change_protocols", 0) or 0),
        int(payload.get("total_no_change_protocols", 0) or 0),
        int(payload.get("total_excel_already_updated", 0) or 0),
    )
    known_eligible = max(
        int(payload.get("total_eligible_after_skip", 0) or 0),
        int(payload.get("source_total_eligible_after_skip", 0) or 0),
        int(download.get("source_total_eligible_after_skip", 0) or 0),
    )
    covered_after_apply = already_covered + applied_count
    known_remaining = max(known_eligible - covered_after_apply, 0)
    planned_batch_applied = (not dry_run) and (
        planned_count == 0 or applied_count >= planned_count
    )
    all_known_added = planned_batch_applied and known_remaining == 0
    global_authoritative = bool(reconciliation.get("set_reconciliation_authoritative"))
    missing_global = int(reconciliation.get("missing_in_workbook_unique", 0) or 0)
    global_complete = global_authoritative and missing_global == 0
    if dry_run:
        guarantee_status = "SIMULATION_ONLY"
    elif not planned_batch_applied:
        guarantee_status = "PLANNED_BATCH_NOT_FULLY_APPLIED"
    elif global_complete:
        guarantee_status = "GLOBAL_AUTHORITATIVE_COMPLETE"
    elif known_remaining > 0:
        guarantee_status = "KNOWN_ELIGIBLE_REMAINING"
    elif all_known_added:
        guarantee_status = "ALL_KNOWN_ELIGIBLE_COVERED_NOT_GLOBAL"
    else:
        guarantee_status = "NOT_AUTHORITATIVE"
    return {
        "schema_version": 1,
        "mode": "simulation" if dry_run else "production",
        "reconciliation_mode": str(
            payload.get("reconciliation_mode")
            or download.get("op5_reconciliation_mode")
            or ""
        ),
        "planned_excel_actions": planned_count,
        "applied_excel_actions": applied_count,
        "already_covered_protocols": already_covered,
        "known_eligible_protocols": known_eligible,
        "known_eligible_remaining": known_remaining,
        "planned_batch_applied": planned_batch_applied,
        "all_known_eligible_added_to_workbook": all_known_added,
        "global_coverage_authoritative": global_authoritative,
        "global_missing_in_workbook": missing_global,
        "all_portal_eligible_added_to_workbook": global_complete,
        "guarantee_status": guarantee_status,
    }


def op5_planning_candidate_limit(
    settings,
    authorization: BatchAuthorization,
) -> int:
    requested = int(authorization.requested_batch_limit)
    authorized = int(authorization.authorized_batch_limit)
    if not bool(getattr(settings, "DRY_RUN", True)):
        return requested
    if str(getattr(settings, "OP5_RECONCILIATION_MODE", "") or "").lower() != "batch_fast":
        return requested
    if getattr(settings, "op5_target_protocols", set()) or set():
        return requested
    return max(requested, authorized)


def op5_payload_can_persist_plan(
    payload: dict,
    *,
    planned_actions: list[dict[str, str]] | None = None,
) -> bool:
    if payload.get("run_error"):
        return False
    status = str(payload.get("status") or "")
    if status not in {OperationStatus.SUCESSO.value, OperationStatus.PARCIAL.value}:
        return False
    processing = payload.get("processing")
    processing = processing if isinstance(processing, dict) else {}
    if processing.get("blocked_real_run"):
        return False
    planned_actions = planned_actions or planned_excel_actions(processing)
    if not planned_actions:
        return False
    requested_limit = int(payload.get("requested_batch_limit", 0) or 0)
    has_requested_safe_actions = (
        requested_limit > 0 and len(planned_actions) >= requested_limit
    )
    if status == OperationStatus.SUCESSO.value:
        return True
    total_errors = int(payload.get("total_errors", 0) or 0)
    total_pending_review = int(
        payload.get("total_pending_review", processing.get("total_pending_review", 0))
        or 0
    )
    blocking_error_total = sum(
        int(payload.get(key, 0) or 0)
        for key in (
            "total_cdp_errors",
            "total_download_errors",
            "total_real_extraction_errors",
            "total_real_application_errors",
            "total_failed_protocols",
        )
    )
    if blocking_error_total and not has_requested_safe_actions:
        return False
    if total_errors != total_pending_review and not has_requested_safe_actions:
        return False
    planned_protocols = {
        str(item.get("protocol") or "")
        for item in planned_actions
        if str(item.get("protocol") or "")
    }
    for item in processing.get("results") or []:
        if not isinstance(item, dict):
            continue
        protocol = str(item.get("protocol") or "")
        if has_requested_safe_actions and protocol and protocol not in planned_protocols:
            continue
        excel_status = item.get("excel_status")
        excel_status = excel_status if isinstance(excel_status, dict) else {}
        action = str(item.get("action") or excel_status.get("action") or "")
        if processing_item_is_pending_review(item):
            continue
        if item.get("success") is False:
            return False
        if action in EXCEL_WRITE_ACTIONS:
            continue
    return True


def download_summary_for_planned_actions(
    download: dict,
    planned_actions: list[dict[str, str]],
) -> dict:
    planned_protocols = [
        str(item.get("protocol") or "")
        for item in planned_actions
        if str(item.get("protocol") or "")
    ]
    planned_set = set(planned_protocols)
    planned_order = {protocol: index for index, protocol in enumerate(planned_protocols)}
    if not planned_set:
        return download

    download["results"] = sorted(
        [
            item
            for item in download.get("results") or []
            if str(item.get("protocol") or "") in planned_set
        ],
        key=lambda item: planned_order.get(str(item.get("protocol") or ""), len(planned_order)),
    )
    download["selected_protocols"] = sorted(
        [
            item
            for item in download.get("selected_protocols") or []
            if str(item.get("protocol") or "") in planned_set
        ],
        key=lambda item: planned_order.get(str(item.get("protocol") or ""), len(planned_order)),
    )
    download["protocols_selected_by_global_limit"] = planned_protocols
    download["total_protocols_selected_by_global_limit"] = len(planned_protocols)
    download["total_selected"] = len(planned_protocols)
    download["total_for_processing"] = len(planned_protocols)
    download["total_sent_to_processing"] = len(planned_protocols)

    frozen_batch = download.get("frozen_batch")
    if isinstance(frozen_batch, dict):
        frozen_batch["protocols"] = planned_protocols
        requested_batch_limit = int(download.get("requested_batch_limit") or 0)
        if requested_batch_limit > 0:
            frozen_batch["requested_limit"] = requested_batch_limit

    frozen_scope = download.get("frozen_pdf_scope")
    if isinstance(frozen_scope, dict):
        artifacts = sorted(
            [
                artifact
                for artifact in frozen_scope.get("artifacts") or []
                if str(artifact.get("protocol") or "") in planned_set
            ],
            key=lambda artifact: planned_order.get(
                str(artifact.get("protocol") or ""), len(planned_order)
            ),
        )
        digest_source = "\n".join(
            f"{str(artifact.get('protocol') or '')}:{str(artifact.get('sha256') or '')}"
            for artifact in artifacts
        )
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
        frozen_scope["artifacts"] = artifacts
        frozen_scope["digest"] = digest
        download["frozen_pdf_scope_count"] = len(artifacts)
        download["frozen_pdf_scope_digest"] = digest
    frozen_metadata = frozen_portal_metadata_by_protocol(download)
    if frozen_metadata:
        download["frozen_portal_metadata"] = {
            protocol: frozen_metadata[protocol]
            for protocol in planned_protocols
            if protocol in frozen_metadata
        }
    refresh_processing_selection_totals(download)
    return download


def frozen_portal_metadata_by_protocol(download_summary: dict) -> dict[str, dict]:
    explicit = download_summary.get("frozen_portal_metadata")
    if isinstance(explicit, dict):
        return {
            str(protocol): dict(metadata)
            for protocol, metadata in explicit.items()
            if protocol and isinstance(metadata, dict)
        }
    metadata_by_protocol: dict[str, dict] = {}
    metadata_keys = {
        "protocol",
        "client_name",
        "detail_protocol",
        "detail_client_name",
        "status",
        "entry_date",
        "entry_date_raw",
        "completion_date",
        "completion_date_raw",
        "completion_date_normalized",
        "completion_source_stage",
        "completion_source_selector",
        "completion_extraction_status",
        "page_number",
        "row_index",
        "op5_selection_scope",
    }
    for item in download_summary.get("results") or []:
        if not isinstance(item, dict):
            continue
        protocol = str(item.get("protocol") or "").strip()
        if not protocol:
            continue
        snapshot = {
            key: item.get(key)
            for key in metadata_keys
            if item.get(key) not in (None, "")
        }
        snapshot["protocol"] = protocol
        if snapshot:
            metadata_by_protocol[protocol] = snapshot
    return metadata_by_protocol


def processing_item_is_pending_review(item: dict) -> bool:
    return (
        bool(item.get("technical_review_required"))
        or item.get("action") == "pending_technical_review"
        or item.get("technical_validation_status") == "pending_review"
    )


def processing_item_is_batch_policy_block(item: dict) -> bool:
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


def processing_item_is_application_error(item: dict) -> bool:
    excel_status = item.get("excel_status") or {}
    archive_status = item.get("archive_status") or {}
    return bool(excel_status.get("error") or archive_status.get("error"))
