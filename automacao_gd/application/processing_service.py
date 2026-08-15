import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from automacao_gd.application.contracts import OperationStatus
from automacao_gd.application.operational_guard import (
    OPTION4_AUTHORIZATION_SCOPE,
    OPTION4_OPERATION,
    OfflineOperationAuthorization,
    OperationalLockProof,
    guard_offline_operation,
    validate_offline_authorization,
    validate_operational_lock_proof,
)
from automacao_gd.application.op5_completed_index import record_completed_protocol
from automacao_gd.application.op5_optimization import run_limited_pdf_tasks
from automacao_gd.application.processing_results import (
    archive_effect as _archive_effect,
    blocked_result as _blocked_result,
    classify_processing_result as _classify_processing_result,
    compact_excel_status as _compact_excel_status,
    count_archived as _count_archived,
    count_excel_updates as _count_excel_updates,
    empty_result as _empty_result,
    excel_effect as _excel_effect,
    has_excel_updates_to_apply as _has_excel_updates_to_apply,
    has_processable_protocols as _has_processable_protocols,
    has_traceable_effects as _has_traceable_effects,
    is_protocol_application_error as _is_protocol_application_error,
    is_protocol_blocked_by_batch_policy as _is_protocol_blocked_by_batch_policy,
    is_protocol_no_change as _is_protocol_no_change,
    is_protocol_pending_review as _is_protocol_pending_review,
    is_protocol_safe_or_no_change as _is_protocol_safe_or_no_change,
    join_errors as _join_errors,
    mark_results_after_rollback as _mark_results_after_rollback,
    protocol_from_filename as _protocol_from_filename,
    real_run_blocked_from_simulation as _real_run_blocked_from_simulation,
    sha256 as _sha256,
    skipped_excel_status as _skipped_excel_status,
    state_archive_last_step as _state_archive_last_step,
    state_archive_payload as _state_archive_payload,
    state_archive_status as _state_archive_status,
    state_excel_last_step as _state_excel_last_step,
    state_excel_payload as _state_excel_payload,
    state_excel_status as _state_excel_status,
    systemic_real_apply_issues as _systemic_real_apply_issues,
    processing_metrics as _processing_metrics,
)
from automacao_gd.application.shareable_reports import build_shareable_report
from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.infrastructure.files.client_folder_service import (
    ArchiveDestinationResult,
    archive_pdf_to_client_folder,
    build_destination_pdf_path,
    clear_folder_cache,
    find_client_folder,
    resolve_archive_destination_folder,
)
from automacao_gd.domain.equipment_cache import (
    load_validated_equipment_cache,
    serialize_equipment_cache,
)
from automacao_gd.domain.equipment_semantics import format_canonical_collection
from automacao_gd.domain.equipment_validation import (
    EQUIPMENT_RULES_VERSION,
    TECHNICAL_PROCESSING_FORMAT_VERSION,
    TechnicalValidationResult,
    validate_canonical_equipment,
    validate_technical_equipment,
)
from automacao_gd.domain.models import GenerationData
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.excel.service import (
    create_workbook_backup,
    project_dry_run_target_rows,
    update_excel_from_pdf_data,
    validate_xlsx_integrity,
    validate_workbook_for_pdf_updates,
)
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.metadata.service import load_portal_metadata
from automacao_gd.infrastructure.persistence.atomic import (
    atomic_copy_file,
    atomic_write_json,
    atomic_write_text,
)
from automacao_gd.infrastructure.pdf.service import (
    extract_generation_data,
    extract_client_from_pdf_text,
    extract_pdf_text,
    extract_protocol_from_pdf_text,
)


JSON_REPORT_NAME = "processamento_pdfs_planilha_clientes.json"
MARKDOWN_REPORT_NAME = "processamento_pdfs_planilha_clientes.md"
SHAREABLE_JSON_REPORT_NAME = "processamento_pdfs_shareable.json"
SHAREABLE_MARKDOWN_REPORT_NAME = "processamento_pdfs_shareable.md"
OPEN_COMPLETION_TEXT = "EM ABERTO"
POINT_OF_CONNECTION_NO_DATE_STATUSES = {
    "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE",
    "POINT_OF_CONNECTION_STAGE_NOT_FOUND",
}
POINT_OF_CONNECTION_PENDING_STATUSES = {
    "AMBIGUOUS_POINT_OF_CONNECTION_COMPLETION_DATE",
    "AMBIGUOUS_POINT_OF_CONNECTION_STAGE",
    "INVALID_POINT_OF_CONNECTION_COMPLETION_DATE",
}


def process_downloaded_pdfs(
    downloads_root: Path,
    workbook_path: Path,
    clientes_root: Path,
    dry_run: bool = True,
    pdf_paths: Iterable[Path] | None = None,
    apply_excel: bool = True,
    apply_archive: bool = True,
    state_store=None,
    allowed_protocols: set[str] | None = None,
    metadata_only_protocols: set[str] | None = None,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None = None,
    authorization: OfflineOperationAuthorization | None = None,
    lock_proof: OperationalLockProof | None = None,
) -> dict:
    if dry_run:
        return _process_downloaded_pdfs_locked(
            downloads_root=downloads_root,
            workbook_path=workbook_path,
            clientes_root=clientes_root,
            dry_run=True,
            pdf_paths=pdf_paths,
            apply_excel=apply_excel,
            apply_archive=apply_archive,
            state_store=state_store,
            allowed_protocols=allowed_protocols,
            metadata_only_protocols=metadata_only_protocols,
            portal_metadata_by_protocol=portal_metadata_by_protocol,
            settings=get_settings(),
        )

    if authorization is None and lock_proof is None:
        raise OperationalBlockError(
            code="DIRECT_ROUTE_AUTHORIZATION_REQUIRED",
            user_message="Processamento real exige autorização operacional tipada.",
            stage="autorização operacional",
            technical_cause="DIRECT_ROUTE_AUTHORIZATION_REQUIRED",
        )

    settings = get_settings()
    if lock_proof is not None:
        proof = validate_operational_lock_proof(
            lock_proof,
            settings,
            allowed_operations={OPTION4_OPERATION, "option5"},
        )
        if proof.operation == OPTION4_OPERATION:
            batch = validate_offline_authorization(authorization)
            assert authorization is not None
            if (
                proof.execution_id != authorization.execution_id
                or proof.requested_limit != batch.requested_limit
                or proof.authorization_scope != OPTION4_AUTHORIZATION_SCOPE
            ):
                raise _processing_scope_mismatch()
            _validate_authorized_processing_scope(batch, pdf_paths, allowed_protocols)
        return _process_downloaded_pdfs_locked(
            downloads_root=downloads_root,
            workbook_path=workbook_path,
            clientes_root=clientes_root,
            dry_run=False,
            pdf_paths=pdf_paths,
            apply_excel=apply_excel,
            apply_archive=apply_archive,
            state_store=state_store,
            allowed_protocols=allowed_protocols,
            metadata_only_protocols=metadata_only_protocols,
            portal_metadata_by_protocol=portal_metadata_by_protocol,
            settings=settings,
        )

    with guard_offline_operation(settings, authorization) as (batch, proof):
        validate_operational_lock_proof(
            proof,
            settings,
            allowed_operations={OPTION4_OPERATION},
        )
        _validate_authorized_processing_scope(batch, pdf_paths, allowed_protocols)
        return _process_downloaded_pdfs_locked(
            downloads_root=downloads_root,
            workbook_path=workbook_path,
            clientes_root=clientes_root,
            dry_run=False,
            pdf_paths=pdf_paths,
            apply_excel=apply_excel,
            apply_archive=apply_archive,
            state_store=state_store,
            allowed_protocols=allowed_protocols,
            metadata_only_protocols=metadata_only_protocols,
            portal_metadata_by_protocol=portal_metadata_by_protocol,
            settings=settings,
        )


def _validate_authorized_processing_scope(
    batch,
    pdf_paths: Iterable[Path] | None,
    allowed_protocols: set[str] | None,
) -> None:
    if pdf_paths is None or allowed_protocols is None:
        raise _processing_scope_mismatch()
    resolved_paths = tuple(Path(path).resolve(strict=False) for path in pdf_paths)
    if resolved_paths != batch.pdf_paths or allowed_protocols != set(batch.protocols):
        raise _processing_scope_mismatch()


def _processing_scope_mismatch() -> OperationalBlockError:
    return OperationalBlockError(
        code="DIRECT_ROUTE_SCOPE_MISMATCH",
        user_message="A autorização tipada diverge do escopo real solicitado.",
        stage="autorização operacional",
        technical_cause="DIRECT_ROUTE_SCOPE_MISMATCH",
    )


def _process_downloaded_pdfs_locked(
    downloads_root: Path,
    workbook_path: Path,
    clientes_root: Path,
    dry_run: bool = True,
    pdf_paths: Iterable[Path] | None = None,
    apply_excel: bool = True,
    apply_archive: bool = True,
    state_store=None,
    allowed_protocols: set[str] | None = None,
    metadata_only_protocols: set[str] | None = None,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None = None,
    *,
    settings,
) -> dict:
    ensure_directories()
    clear_folder_cache()
    downloads_root = Path(downloads_root)
    workbook_path = Path(workbook_path)
    clientes_root = Path(clientes_root)
    logs_dir = settings.logs_dir_path
    logs_dir.mkdir(parents=True, exist_ok=True)

    if not dry_run and (pdf_paths is None or allowed_protocols is None):
        return _scope_violation_summary(
            [],
            ["explicit_scope_missing"],
            logs_dir=logs_dir,
            dry_run=False,
            apply_excel=apply_excel,
            apply_archive=apply_archive,
        )

    started_at = datetime.now()
    logger.info(
        "Iniciando processamento offline de PDFs baixados: "
        f"downloads={downloads_root}, planilha={workbook_path}, "
        f"clientes={clientes_root}, dry_run={dry_run}, "
        f"apply_excel={apply_excel}, apply_archive={apply_archive}"
    )

    pdfs = _resolve_pdf_paths(downloads_root, pdf_paths)
    metadata_only_protocols = {
        str(protocol).strip()
        for protocol in (metadata_only_protocols or set())
        if str(protocol).strip()
    }
    pdf_workers = int(getattr(settings, "OP5_PDF_WORKERS", 1) or 1)
    if allowed_protocols is not None:
        unknown_protocols = sorted(
            {
                protocol
                for protocol in (_protocol_from_filename(path) for path in pdfs)
                if protocol and protocol not in allowed_protocols
            }
        )
        if unknown_protocols:
            return _scope_violation_summary(
                pdfs,
                unknown_protocols,
                logs_dir=logs_dir,
                dry_run=dry_run,
                apply_excel=apply_excel,
                apply_archive=apply_archive,
            )
        unknown_metadata_only = sorted(metadata_only_protocols - allowed_protocols)
        if unknown_metadata_only:
            return _scope_violation_summary(
                pdfs,
                unknown_metadata_only,
                logs_dir=logs_dir,
                dry_run=dry_run,
                apply_excel=apply_excel,
                apply_archive=apply_archive,
            )
    initial_validation = _real_run_preflight_result(
        workbook_path,
        dry_run,
        apply_excel,
        stage="antes do processamento de PDFs",
    )
    preflight_error = initial_validation.get("error")
    backup_path = None
    blocked_real_run = bool(preflight_error)
    real_run_block_reason = preflight_error
    real_run_block_code = initial_validation.get("code")
    block_stage = initial_validation.get("stage") if preflight_error else None
    systemic_apply_failure = False
    rollback_executed = False
    rollback_status = None
    rollback_error = None

    if preflight_error:
        logger.error(preflight_error)
        results = [_blocked_result(pdf_path, preflight_error) for pdf_path in pdfs]
        results.extend(
            _blocked_metadata_only_result(protocol, preflight_error)
            for protocol in sorted(metadata_only_protocols)
        )
    elif not dry_run:
        simulation_results = run_limited_pdf_tasks(
            pdfs,
            worker_count=pdf_workers,
            task=lambda pdf_path: _process_single_pdf_for_metadata_scope(
                pdf_path,
                workbook_path,
                clientes_root,
                True,
                None,
                apply_excel,
                apply_archive,
                None,
                portal_metadata_by_protocol=portal_metadata_by_protocol,
            ),
        )
        simulation_results.extend(
            _process_metadata_only_protocols(
                metadata_only_protocols,
                workbook_path=workbook_path,
                dry_run=True,
                backup_path=None,
                apply_excel=apply_excel,
                state_store=None,
                portal_metadata_by_protocol=portal_metadata_by_protocol,
            )
        )
        extracted_scope_violations = _extracted_scope_violations(
            pdfs,
            simulation_results,
            allowed_protocols,
        )
        if extracted_scope_violations:
            return _scope_violation_summary(
                pdfs,
                extracted_scope_violations,
                logs_dir=logs_dir,
                dry_run=dry_run,
                apply_excel=apply_excel,
                apply_archive=apply_archive,
            )
        critical_issues = _critical_simulation_issues(simulation_results, apply_excel)
        if critical_issues:
            blocked_real_run = True
            real_run_block_code = "REAL_RUN_SIMULATION_FAILED"
            block_stage = "simulação de segurança"
            real_run_block_reason = (
                "Execução real bloqueada porque a simulação encontrou erro crítico "
                "ou protocolo sem permissão de gravação."
            )
            logger.error(real_run_block_reason)
            results = [
                _real_run_blocked_from_simulation(item, real_run_block_reason)
                for item in simulation_results
            ]
        elif not _has_processable_protocols(simulation_results, apply_excel):
            blocked_real_run = True
            real_run_block_code = "NO_SAFE_PROTOCOLS_TO_APPLY"
            block_stage = "simulaÃ§Ã£o de seguranÃ§a"
            real_run_block_reason = (
                "Nenhum protocolo seguro para aplicaÃ§Ã£o apÃ³s a triagem tÃ©cnica."
            )
            logger.warning(real_run_block_reason)
            results = simulation_results
        else:
            second_validation = _real_run_preflight_result(
                workbook_path,
                dry_run,
                apply_excel,
                stage="antes da primeira gravação real",
            )
            if second_validation.get("error"):
                blocked_real_run = True
                real_run_block_reason = second_validation["error"]
                real_run_block_code = second_validation.get("code")
                block_stage = "segunda validação da planilha"
                logger.warning(
                    "Gravação bloqueada após o início da operação: "
                    f"code={real_run_block_code}; cause="
                    f"{second_validation.get('technical_cause') or '-'}"
                )
                results = [
                    _real_run_blocked_from_simulation(
                        item,
                        "A planilha ficou indisponível ou bloqueada depois do início "
                        f"da operação. {real_run_block_reason}",
                    )
                    for item in simulation_results
                ]
            else:
                if (
                    settings.BACKUP_EXCEL
                    and apply_excel
                    and _has_excel_updates_to_apply(simulation_results)
                ):
                    backup_path = create_workbook_backup(workbook_path)
                results = _apply_processable_subset_from_simulation(
                    pdfs=pdfs,
                    simulation_results=simulation_results,
                    workbook_path=workbook_path,
                    clientes_root=clientes_root,
                    backup_path=backup_path,
                    apply_excel=apply_excel,
                    apply_archive=apply_archive,
                    state_store=state_store,
                    portal_metadata_by_protocol=portal_metadata_by_protocol,
                )
                results.extend(
                    _apply_metadata_only_subset_from_simulation(
                        metadata_only_protocols=metadata_only_protocols,
                        simulation_results=simulation_results,
                        workbook_path=workbook_path,
                        backup_path=backup_path,
                        apply_excel=apply_excel,
                        state_store=state_store,
                        portal_metadata_by_protocol=portal_metadata_by_protocol,
                    )
                )
                systemic_issues = _systemic_real_apply_issues(results, apply_excel)
                if systemic_issues:
                    systemic_apply_failure = True
                    if backup_path is not None:
                        rollback_executed = True
                        try:
                            atomic_copy_file(backup_path, workbook_path, private=True)
                            if not _verify_restored_workbook(backup_path, workbook_path):
                                raise ValueError("Workbook restaurado nao corresponde ao backup validado.")
                            rollback_status = "CONFIRMED"
                        except (OSError, ValueError) as exc:
                            rollback_status = "FAILED"
                            rollback_error = str(exc)
                            logger.exception(
                                "Falha ao restaurar backup apÃ³s erro sistÃªmico."
                            )
                    results = _mark_results_after_rollback(
                        results,
                        reason=(
                            "Rollback executado apÃ³s falha sistÃªmica na aplicaÃ§Ã£o "
                            "do subconjunto seguro."
                        ),
                    )
    else:
        worker_state_store = state_store if pdf_workers == 1 else None
        results = run_limited_pdf_tasks(
            pdfs,
            worker_count=pdf_workers,
            task=lambda pdf_path: _process_single_pdf_for_metadata_scope(
                pdf_path,
                workbook_path,
                clientes_root,
                dry_run,
                backup_path,
                apply_excel,
                apply_archive,
                worker_state_store,
                portal_metadata_by_protocol=portal_metadata_by_protocol,
            ),
        )
        results.extend(
            _process_metadata_only_protocols(
                metadata_only_protocols,
                workbook_path=workbook_path,
                dry_run=True,
                backup_path=backup_path,
                apply_excel=apply_excel,
                state_store=state_store,
                portal_metadata_by_protocol=portal_metadata_by_protocol,
            )
        )
        if apply_excel:
            results = project_dry_run_target_rows(results, workbook_path)
    finished_at = datetime.now()

    total_success = sum(1 for item in results if item["success"])
    metrics = _processing_metrics(results, dry_run, apply_excel)
    total_errors = (
        metrics["total_pending_review"]
        + metrics["total_real_extraction_errors"]
        + metrics["total_real_application_errors"]
    )
    status, operation_message = _classify_processing_result(
        dry_run=dry_run,
        total_pdfs=len(pdfs),
        total_success=total_success,
        total_errors=total_errors,
        blocked_real_run=blocked_real_run,
        systemic_apply_failure=systemic_apply_failure,
        partial_effects_present=any(_has_traceable_effects(item) for item in results),
    )
    completion_dates_changed = sum(
        1
        for item in results
        if item.get("completion_action") == "COMPLETION_DATE_UPDATED"
    )
    completion_open_changed = sum(
        1 for item in results if item.get("completion_action") == "MARKED_AS_OPEN"
    )
    payload = {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "apply_excel": apply_excel,
        "apply_archive": apply_archive,
        "downloads_root": str(downloads_root),
        "workbook_path": str(workbook_path),
        "clientes_root": str(clientes_root),
        "total_pdfs": len(pdfs),
        "total_metadata_only": len(metadata_only_protocols),
        "pdf_workers": pdf_workers,
        **metrics,
        "total_success": total_success,
        "total_errors": total_errors,
        "total_excel_updated": _count_excel_updates(results, dry_run, apply_excel),
        "total_updates_applied": _count_excel_updates(results, dry_run, apply_excel),
        "total_archived": _count_archived(results, dry_run, apply_archive),
        "total_client_folder_pending_review": sum(
            1
            for item in results
            if item["client_folder_match_type"] in {"pending_review", "not_found"}
            or item.get("archive_match_type") == "pending_manual_review"
        ),
        "total_technical_pending_review": sum(
            1 for item in results if item.get("technical_review_required")
        ),
        "total_client_folder_cache_hits": sum(
            1 for item in results if item.get("client_folder_cache_hit")
        ),
        "total_client_folder_searches": sum(
            1
            for item in results
            if item.get("client_folder_match_type")
            and not item.get("client_folder_cache_hit")
        ),
        "total_excel_already_updated": sum(
            1 for item in results if item.get("action") == "skipped_excel_already_updated"
        ),
        "total_archive_already_done": sum(
            1
            for item in results
            if item.get("archive_status", {}).get("reason") == "archive_already_done"
        ),
        "total_completion_dates_found": sum(
            1 for item in results if item.get("completion_normalized")
        ),
        "total_completion_dates_updated": completion_dates_changed,
        "total_completion_marked_open": completion_open_changed,
        "completion_dates_proposed": completion_dates_changed if dry_run else 0,
        "completion_dates_applied": 0 if dry_run else completion_dates_changed,
        "open_values_proposed": completion_open_changed if dry_run else 0,
        "open_values_applied": 0 if dry_run else completion_open_changed,
        "total_completion_no_change": sum(
            1 for item in results if item.get("completion_action") == "NO_CHANGE"
        ),
        "total_completion_pending_review": sum(
            1 for item in results if item.get("completion_pending_review")
        ),
        "blocked_real_run": blocked_real_run,
        "real_run_block_reason": real_run_block_reason,
        "real_run_block_code": real_run_block_code,
        "block_stage": block_stage,
        "rollback_executed": rollback_executed,
        "rollback_status": rollback_status,
        "rollback_error": rollback_error,
        "pending_pdf_paths": [
            item["pdf_path"]
            for item in results
            if item.get("technical_review_required")
            or (
                blocked_real_run
                and real_run_block_code != "NO_SAFE_PROTOCOLS_TO_APPLY"
            )
        ],
        "status": status.value,
        "operation_message": operation_message,
        "results": results,
    }

    return _persist_processing_reports(logs_dir, payload, state_store=state_store)


def _persist_processing_reports(
    logs_dir: Path,
    payload: dict[str, Any],
    *,
    state_store=None,
) -> dict[str, Any]:
    json_path = Path(logs_dir) / JSON_REPORT_NAME
    markdown_path = Path(logs_dir) / MARKDOWN_REPORT_NAME
    shareable_json_path = Path(logs_dir) / SHAREABLE_JSON_REPORT_NAME
    shareable_markdown_path = Path(logs_dir) / SHAREABLE_MARKDOWN_REPORT_NAME
    for item in payload.get("results", []):
        item["report_effect"] = "persisted"
    payload["report_effect"] = "persisted"
    private_payload = _privacy_safe_report_payload(payload)
    shareable_payload = build_shareable_report(payload, report_type="processing")
    try:
        atomic_write_json(json_path, private_payload, private=True)
        atomic_write_text(markdown_path, _build_markdown_report(private_payload), private=True)
        atomic_write_json(shareable_json_path, shareable_payload, private=True)
        atomic_write_text(
            shareable_markdown_path,
            _build_shareable_markdown(shareable_payload),
            private=True,
        )
        _record_report_state(
            state_store,
            payload.get("results", []),
            report_effect="persisted",
            status=None,
        )
    except (OSError, TypeError, ValueError):
        partial_effects_present = any(
            _has_traceable_effects(item) for item in payload.get("results", [])
        )
        payload["status"] = (
            OperationStatus.PARCIAL.value
            if partial_effects_present
            else OperationStatus.FALHOU.value
        )
        payload["operation_message"] = (
            "Os efeitos foram processados, mas a evidencia minima nao pode ser persistida."
        )
        payload["report_effect"] = "failed"
        payload["report_error_code"] = "REPORT_PERSISTENCE_FAILED"
        for item in payload.get("results", []):
            item["report_effect"] = "failed"
            item["manual_action_required"] = True
            item["success"] = False
        payload["total_success"] = 0
        payload["total_errors"] = len(payload.get("results", []))
        _record_report_state(
            state_store,
            payload.get("results", []),
            report_effect="failed",
            status="operational_pending",
        )
        payload["json_report_path"] = None
        payload["markdown_report_path"] = None
        payload["shareable_json_report_path"] = None
        payload["shareable_markdown_report_path"] = None
        logger.error("Falha ao persistir relatorios operacionais; retomada manual necessaria.")
        return payload

    payload["json_report_path"] = str(json_path)
    payload["markdown_report_path"] = str(markdown_path)
    payload["shareable_json_report_path"] = str(shareable_json_path)
    payload["shareable_markdown_report_path"] = str(shareable_markdown_path)
    logger.info("Relatorios privados e compartilhiveis persistidos com sucesso.")
    return payload


def _record_report_state(
    state_store,
    results: list[dict[str, Any]],
    *,
    report_effect: str,
    status: str | None,
) -> None:
    if state_store is None:
        return
    for item in results:
        protocol = str(item.get("protocol") or "").strip()
        if not protocol:
            continue
        try:
            state_store.update_section(
                protocol,
                "report",
                {
                    "status": report_effect,
                    "error_code": (
                        "REPORT_PERSISTENCE_FAILED"
                        if report_effect == "failed"
                        else None
                    ),
                },
                last_step=(
                    "report_failed" if report_effect == "failed" else "report_persisted"
                ),
                status=status,
            )
        except (OSError, TypeError, ValueError):
            logger.error("Falha ao registrar efeito do relatorio no state privado.")


def _build_shareable_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Relatorio compartilhavel de processamento",
        "",
        f"- Classificacao: {payload['classification']}",
        f"- Status: {payload['status']}",
        f"- Modo: {payload['mode']}",
        f"- Bloqueado: {payload['blocked']}",
        "",
        "## Totais",
    ]
    lines.extend(f"- {key}: {value}" for key, value in payload["totals"].items())
    return "\n".join(lines) + "\n"


def _completion_value_from_metadata(portal_metadata: dict | None) -> dict[str, Any]:
    if not portal_metadata:
        return {
            "value": None,
            "raw": None,
            "normalized": None,
            "source_stage": None,
            "extraction_status": None,
            "reason": None,
            "pending_review": False,
        }
    status = str(portal_metadata.get("completion_extraction_status") or "")
    normalized = portal_metadata.get("completion_date") or portal_metadata.get(
        "completion_date_normalized"
    )
    raw = portal_metadata.get("completion_date_raw")
    completed_status = _is_completed_status(portal_metadata.get("status"))
    if normalized:
        return {
            "value": normalized,
            "raw": raw,
            "normalized": normalized,
            "source_stage": portal_metadata.get("completion_source_stage"),
            "extraction_status": status or "FOUND",
            "reason": "POINT_OF_CONNECTION_COMPLETION_DATE_FOUND",
            "pending_review": False,
        }
    if completed_status and status in POINT_OF_CONNECTION_NO_DATE_STATUSES:
        return {
            "value": OPEN_COMPLETION_TEXT,
            "raw": raw,
            "normalized": None,
            "source_stage": portal_metadata.get("completion_source_stage"),
            "extraction_status": status,
            "reason": "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE",
            "pending_review": False,
        }
    if status in POINT_OF_CONNECTION_PENDING_STATUSES:
        return {
            "value": None,
            "raw": raw,
            "normalized": None,
            "source_stage": portal_metadata.get("completion_source_stage"),
            "extraction_status": status,
            "reason": status,
            "pending_review": True,
        }
    if completed_status:
        return {
            "value": None,
            "raw": raw,
            "normalized": None,
            "source_stage": portal_metadata.get("completion_source_stage"),
            "extraction_status": status or "COMPLETION_DATE_MISSING",
            "reason": "COMPLETION_DATE_MISSING",
            "pending_review": True,
        }
    return {
        "value": None,
        "raw": raw,
        "normalized": normalized,
        "source_stage": portal_metadata.get("completion_source_stage"),
        "extraction_status": status or None,
        "reason": None,
        "pending_review": False,
    }


def _is_completed_status(status: Any) -> bool:
    normalized = str(status or "").upper()
    normalized = (
        normalized.replace("Ç", "C")
        .replace("Ã‡", "C")
        .replace("Í", "I")
        .replace("Ã", "A")
    )
    return "CONCLUID" in normalized


def _process_single_pdf(
    pdf_path: Path,
    workbook_path: Path,
    clientes_root: Path,
    dry_run: bool,
    backup_path: Path | None = None,
    apply_excel: bool = True,
    apply_archive: bool = True,
    state_store=None,
    *,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None = None,
) -> dict:
    logger.info(f"Processando PDF baixado: {pdf_path}")
    result = _empty_result(pdf_path)
    current_effect_stage = "extraction"

    try:
        (
            protocol,
            client_name,
            module_excel,
            inverter_excel,
            placa_planilha,
            inversor_planilha,
            technical_validation,
        ) = _load_or_extract_technical_data(pdf_path, state_store)
        if not protocol:
            raise ValueError(f"Não foi possível identificar o protocolo do PDF: {pdf_path}")
        result["protocol"] = protocol
        expected_protocol = _protocol_from_filename(pdf_path)
        if expected_protocol is None or protocol != expected_protocol:
            result.update(
                {
                    "success": False,
                    "status": OperationStatus.BLOQUEADO.value,
                    "error": "Protocolo extraído diverge do escopo congelado.",
                    "error_code": "FROZEN_BATCH_SCOPE_VIOLATION",
                }
            )
            return result
        if not technical_validation.approved:
            return _pending_technical_review_result(
                result=result,
                protocol=protocol,
                client_name=client_name,
                module_excel=module_excel,
                inverter_excel=inverter_excel,
                placa_planilha=placa_planilha,
                inversor_planilha=inversor_planilha,
                validation=technical_validation,
                state_store=state_store,
                dry_run=dry_run,
            )

        portal_metadata: dict[str, Any] | None
        metadata_source: str | None
        frozen_metadata = (
            portal_metadata_by_protocol.get(protocol)
            if portal_metadata_by_protocol is not None
            else None
        )
        if frozen_metadata is not None:
            portal_metadata = dict(frozen_metadata)
            metadata_source = "frozen_op5_plan"
        else:
            portal_metadata, metadata_source = load_portal_metadata(
                get_settings().downloads_dir_path,
                get_settings().logs_dir_path,
                protocol,
            )
        entry_date = portal_metadata.get("entry_date") if portal_metadata else None
        completion_decision = _completion_value_from_metadata(portal_metadata)
        completion_date = completion_decision["value"]

        match: Any
        archive_destination: Any
        if apply_archive:
            match = find_client_folder(clientes_root, protocol, client_name)
            if state_store:
                state_store.update_section(
                    protocol,
                    "client_folder",
                    {
                        "status": "found"
                        if match.match_type in {"protocol", "fuzzy_name"}
                        else match.match_type,
                        "matched_path": match.matched_path,
                        "match_type": match.match_type,
                        "confidence": match.confidence,
                        "cache_hit": match.cache_hit,
                    },
                    last_step="client_folder_found",
                )
            archive_destination = resolve_archive_destination_folder(
                client_folder=Path(match.matched_path) if match.matched_path else None,
                protocol=protocol,
                entry_date=entry_date,
                client_name=client_name,
                clientes_root=clientes_root,
            )
        else:
            match = SimpleNamespace(
                match_type=None,
                matched_path=None,
                confidence=None,
                cache_hit=False,
                cache_key=None,
                reason="APPLY_ARCHIVE=false",
                found_by=None,
                protocol_search_hit=False,
                search_elapsed_seconds=None,
            )
            archive_destination = ArchiveDestinationResult(
                destination_folder=None,
                match_type="not_applicable",
                confidence=0.0,
                reason="APPLY_ARCHIVE=false",
                should_create_folder=False,
                fallback_mode="disabled",
                legacy_gd_ignored=False,
            )
        target_folder = archive_destination.destination_folder
        target_pdf_path = (
            build_destination_pdf_path(target_folder, protocol)
            if target_folder
            else None
        )

        current_effect_stage = "excel"
        if apply_excel:
            excel_status = update_excel_from_pdf_data(
                workbook_path=workbook_path,
                protocol=protocol,
                client_name=client_name,
                entry_date=entry_date,
                completion_date=completion_date,
                module_text=placa_planilha,
                inverter_text=inversor_planilha,
                parecer="Sim",
                dry_run=dry_run,
                backup_path=backup_path,
                require_completion_date=bool(
                    portal_metadata
                    and _is_completed_status(portal_metadata.get("status"))
                ),
            )
        else:
            excel_status = _skipped_excel_status(protocol, dry_run)
        result["excel_effect"] = _excel_effect(excel_status, dry_run, apply_excel)
        result["rollback_possible"] = result["excel_effect"] == "applied"

        if state_store:
            current_effect_stage = "state"
            state_store.update_section(
                protocol,
                "excel",
                _state_excel_payload(
                    excel_status,
                    workbook_path,
                    dry_run,
                    apply_excel,
                ),
                last_step=_state_excel_last_step(excel_status, dry_run, apply_excel),
            )
            if apply_excel and not dry_run:
                if excel_status.get("success"):
                    state_store.update_section(
                        protocol,
                        "technical_processing",
                        {"status": "excel_updated"},
                        last_step="technical_excel_updated",
                    )
                    state_store.update_section(
                        protocol,
                        "technical_processing",
                        {"status": "success"},
                        last_step="technical_processed",
                    )
                else:
                    state_store.update_section(
                        protocol,
                        "technical_processing",
                        {"status": "operational_pending"},
                        last_step="pending_excel_retry",
                    )
                    state_store.update_protocol(
                        protocol,
                        status="operational_pending",
                        last_step="pending_excel_retry",
                    )
            result["state_effect"] = "persisted"

        archive_status = {
            "success": True,
            "simulated": dry_run,
            "skipped": False,
            "error": None,
            "match_type": archive_destination.match_type,
            "reason": archive_destination.reason,
            "created_folder": archive_destination.should_create_folder,
            "fallback_mode": archive_destination.fallback_mode,
            "legacy_gd_ignored": archive_destination.legacy_gd_ignored,
        }
        archived_pdf_path = str(target_pdf_path) if target_pdf_path else None
        current_effect_stage = "archive"
        if apply_archive and target_folder is None:
            archive_status = {
                "success": False,
                "simulated": dry_run,
                "skipped": True,
                "error": archive_destination.reason,
                "reason": archive_destination.reason,
                "match_type": archive_destination.match_type,
                "created_folder": False,
                "fallback_mode": archive_destination.fallback_mode,
                "legacy_gd_ignored": archive_destination.legacy_gd_ignored,
            }
        elif not apply_archive:
            archive_status = {
                "success": True,
                "simulated": dry_run,
                "skipped": True,
                "error": None,
                "reason": "APPLY_ARCHIVE=false",
                "match_type": archive_destination.match_type,
                "created_folder": archive_destination.should_create_folder,
                "fallback_mode": archive_destination.fallback_mode,
                "legacy_gd_ignored": archive_destination.legacy_gd_ignored,
            }
        elif not dry_run:
            already_archived_path = _already_archived_path_from_state(
                state_store, protocol, pdf_path
            )
            if already_archived_path:
                archive_status = {
                    "success": True,
                    "simulated": False,
                    "skipped": True,
                    "error": None,
                    "reason": "archive_already_done",
                    "match_type": archive_destination.match_type,
                    "created_folder": False,
                    "fallback_mode": archive_destination.fallback_mode,
                    "legacy_gd_ignored": archive_destination.legacy_gd_ignored,
                    "source_pdf_sha256": _sha256(pdf_path),
                    "archived_pdf_sha256": _sha256(Path(already_archived_path)),
                }
                archived_pdf_path = already_archived_path
            elif apply_excel and not excel_status.get("success"):
                archive_status = {
                    "success": False,
                    "simulated": False,
                    "skipped": False,
                    "error": "Arquivamento bloqueado porque a planilha não pode ser atualizada.",
                    "match_type": archive_destination.match_type,
                    "reason": archive_destination.reason,
                    "created_folder": False,
                    "fallback_mode": archive_destination.fallback_mode,
                    "legacy_gd_ignored": archive_destination.legacy_gd_ignored,
                }
            else:
                archive_result = archive_pdf_to_client_folder(
                    pdf_path,
                    protocol,
                    client_name,
                    clientes_root,
                    match=match,
                    entry_date=entry_date,
                )
                archive_status = {
                    "success": archive_result.success,
                    "simulated": False,
                    "skipped": archive_result.reason == "archive_already_done",
                    "error": archive_result.error,
                    "match_type": archive_result.match_type,
                    "reason": archive_result.reason,
                    "created_folder": archive_result.created_folder,
                    "fallback_mode": archive_result.fallback_mode,
                    "legacy_gd_ignored": archive_result.legacy_gd_ignored,
                    "source_pdf_sha256": getattr(
                        archive_result, "source_pdf_sha256", None
                    ),
                    "archived_pdf_sha256": getattr(
                        archive_result, "archived_pdf_sha256", None
                    ),
                }
                archived_pdf_path = archive_result.archived_pdf_path
                if archive_result.destination_folder:
                    target_folder = Path(archive_result.destination_folder)
        result["archive_effect"] = _archive_effect(
            archive_status,
            dry_run,
            apply_archive,
        )

        if state_store:
            current_effect_stage = "state"
            state_store.update_section(
                protocol,
                "archive",
                _state_archive_payload(
                    archive_status,
                    target_folder,
                    archived_pdf_path,
                    dry_run,
                    apply_archive,
                ),
                last_step=_state_archive_last_step(
                    archive_status, dry_run, apply_archive
                ),
            )
            result["state_effect"] = "persisted"

        completed_index_effect = _record_completed_index_best_effort(
            protocol=protocol,
            pdf_path=pdf_path,
            archived_pdf_path=Path(archived_pdf_path)
            if archived_pdf_path
            else None,
            workbook_path=workbook_path,
            excel_status=excel_status,
            archive_status=archive_status,
            portal_metadata=portal_metadata,
            dry_run=dry_run,
        )
        success = archive_status["success"] and excel_status["success"]
        if completed_index_effect == "failed":
            success = False
        result.update(
            {
                "success": success,
                "protocol": protocol,
                "client_name": client_name,
                "entry_date": entry_date,
                "completion_date": completion_date,
                "completion_raw": completion_decision["raw"],
                "completion_normalized": completion_decision["normalized"],
                "completion_source_stage": completion_decision["source_stage"],
                "completion_extraction_status": completion_decision["extraction_status"],
                "completion_action": excel_status.get("completion_action"),
                "completion_reason": completion_decision["reason"],
                "completion_pending_review": completion_decision["pending_review"],
                "metadata_source": metadata_source,
                "target_sheet": excel_status.get("target_sheet"),
                "source_sheet": excel_status.get("source_sheet"),
                "source_row": excel_status.get("source_row"),
                "target_row": excel_status.get("target_row"),
                "existing_row": excel_status.get("existing_row"),
                "new_row": excel_status.get("new_row"),
                "moved_from": excel_status.get("moved_from"),
                "moved_to": excel_status.get("moved_to"),
                "action": excel_status.get("action"),
                "row_number": excel_status.get("row_number"),
                "warning": excel_status.get("warning"),
                "module_excel": module_excel,
                "inverter_excel": inverter_excel,
                "placa_planilha": placa_planilha,
                "inversor_planilha": inversor_planilha,
                "technical_validation_status": technical_validation.status,
                "technical_review_required": False,
                "technical_validation_errors": technical_validation.errors,
                "technical_validation_warnings": technical_validation.warnings,
                "module_source": technical_validation.module_source,
                "inverter_source": technical_validation.inverter_source,
                "client_folder_match_type": match.match_type,
                "client_folder_confidence": match.confidence,
                "client_folder_cache_hit": match.cache_hit,
                "client_folder_cache_key": match.cache_key,
                "matched_path": match.matched_path,
                "client_folder_reason": match.reason,
                "reason": match.reason,
                "found_by": match.found_by,
                "protocol_search_hit": match.protocol_search_hit,
                "client_folder_search_elapsed_seconds": match.search_elapsed_seconds,
                "target_folder": str(target_folder) if target_folder else None,
                "archived_pdf_path": archived_pdf_path,
                "archive_destination_folder": str(target_folder) if target_folder else None,
                "archive_match_type": archive_status.get("match_type") or archive_destination.match_type,
                "archive_reason": archive_status.get("reason") or archive_destination.reason,
                "archive_created_folder": archive_status.get(
                    "created_folder", archive_destination.should_create_folder
                ),
                "archive_fallback_mode": archive_status.get("fallback_mode")
                or archive_destination.fallback_mode,
                "legacy_gd_ignored": archive_status.get(
                    "legacy_gd_ignored",
                    archive_destination.legacy_gd_ignored,
                ),
                "arquivo_final": Path(archived_pdf_path).name if archived_pdf_path else None,
                "archive_status": archive_status,
                "excel_status": _compact_excel_status(excel_status),
                "op5_completed_index_effect": completed_index_effect,
                "error": None,
            }
        )
        if completed_index_effect == "failed":
            result["error"] = "Falha ao persistir indice mestre OP5 privado."
            result["manual_action_required"] = True
        if not result["success"]:
            result["error"] = result["error"] or _join_errors(
                archive_status,
                excel_status,
            )
            operational_excel_pending = (
                apply_excel and not dry_run and not excel_status.get("success")
            )
            if state_store and result["error"] and not operational_excel_pending:
                _add_state_error_best_effort(
                    state_store,
                    protocol,
                    "processing",
                    result["error"],
                )
            logger.warning(
                f"PDF processado com pendências: protocolo={protocol}, "
                f"erro={result['error']}"
            )
        elif state_store:
            current_effect_stage = "state"
            state_store.mark_completed(protocol)
            result["state_effect"] = "persisted"
        return result
    except Exception as exc:
        state_persistence_failed = current_effect_stage == "state"
        if state_persistence_failed:
            logger.error("Falha ao persistir state; efeitos mantidos para retomada.")
        else:
            logger.exception(f"Falha ao processar PDF {pdf_path}: {exc}")
        result["success"] = False
        result["error"] = (
            "Falha ao persistir o estado operacional. Retomada manual necessária."
            if state_persistence_failed
            else str(exc)
        )
        if current_effect_stage == "excel":
            result["excel_effect"] = "failed"
        elif current_effect_stage == "archive":
            result["archive_effect"] = "failed"
        elif current_effect_stage == "state":
            result["state_effect"] = "failed"
            result["error_code"] = "STATE_PERSISTENCE_FAILED"
        result["status"] = (
            OperationStatus.PARCIAL.value
            if _has_traceable_effects(result)
            else OperationStatus.FALHOU.value
        )
        if not dry_run and (
            result.get("excel_effect") == "applied"
            or result.get("archive_effect") in {"archived", "already_present"}
            or result.get("state_effect") == "failed"
        ):
            result["manual_action_required"] = True
        protocol = result.get("protocol") or _protocol_from_filename(pdf_path)
        if state_store and protocol:
            _add_state_error_best_effort(
                state_store,
                protocol,
                "processing",
                result["error"],
            )
        return result


def _process_single_pdf_for_metadata_scope(
    pdf_path: Path,
    workbook_path: Path,
    clientes_root: Path,
    dry_run: bool,
    backup_path: Path | None = None,
    apply_excel: bool = True,
    apply_archive: bool = True,
    state_store=None,
    *,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None = None,
) -> dict:
    if portal_metadata_by_protocol is None:
        return _process_single_pdf(
            pdf_path,
            workbook_path,
            clientes_root,
            dry_run,
            backup_path,
            apply_excel,
            apply_archive,
            state_store,
        )
    return _process_single_pdf(
        pdf_path,
        workbook_path,
        clientes_root,
        dry_run,
        backup_path,
        apply_excel,
        apply_archive,
        state_store,
        portal_metadata_by_protocol=portal_metadata_by_protocol,
    )


def _process_metadata_only_protocols(
    protocols: set[str],
    *,
    workbook_path: Path,
    dry_run: bool,
    backup_path: Path | None,
    apply_excel: bool,
    state_store,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None,
) -> list[dict]:
    return [
        _process_metadata_only_protocol(
            protocol,
            workbook_path=workbook_path,
            dry_run=dry_run,
            backup_path=backup_path,
            apply_excel=apply_excel,
            state_store=state_store,
            portal_metadata_by_protocol=portal_metadata_by_protocol,
        )
        for protocol in sorted(protocols)
    ]


def _apply_metadata_only_subset_from_simulation(
    *,
    metadata_only_protocols: set[str],
    simulation_results: list[dict],
    workbook_path: Path,
    backup_path: Path | None,
    apply_excel: bool,
    state_store,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None,
) -> list[dict]:
    simulation_by_protocol = {
        str(item.get("protocol") or ""): item
        for item in simulation_results
        if str(item.get("protocol") or "") in metadata_only_protocols
    }
    results: list[dict] = []
    for protocol in sorted(metadata_only_protocols):
        simulation_item = simulation_by_protocol.get(protocol)
        if simulation_item is not None:
            if _is_protocol_no_change(simulation_item):
                item = dict(simulation_item)
                item["processing_phase"] = "simulation_only"
                item["real_run_skipped_reason"] = "excel_already_updated"
                results.append(item)
                continue
            if not _is_protocol_safe_or_no_change(simulation_item, apply_excel):
                item = dict(simulation_item)
                item["processing_phase"] = "simulation_only"
                item["real_run_skipped_reason"] = "protocol_not_safe_to_apply"
                results.append(item)
                continue
        result = _process_metadata_only_protocol(
            protocol,
            workbook_path=workbook_path,
            dry_run=False,
            backup_path=backup_path,
            apply_excel=apply_excel,
            state_store=state_store,
            portal_metadata_by_protocol=portal_metadata_by_protocol,
        )
        result["processing_phase"] = "application"
        results.append(result)
    return results


def _process_metadata_only_protocol(
    protocol: str,
    *,
    workbook_path: Path,
    dry_run: bool,
    backup_path: Path | None,
    apply_excel: bool,
    state_store,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None,
) -> dict:
    result = _empty_result(Path(f"metadata_only_{protocol}.pdf"))
    result["pdf_path"] = None
    result["protocol"] = protocol
    result["processing_reason"] = "budget_unavailable_metadata_only"
    result["download_status"] = "budget_unavailable"
    result["budget_unavailable"] = True

    portal_metadata, metadata_source = _load_metadata_for_protocol(
        protocol,
        portal_metadata_by_protocol,
    )
    if not portal_metadata:
        message = "Metadados congelados ausentes para protocolo sem orcamento disponivel."
        result.update(
            {
                "success": False,
                "error": message,
                "technical_validation_status": "metadata_only",
                "archive_status": {
                    **result["archive_status"],
                    "success": False,
                    "simulated": dry_run,
                    "skipped": True,
                    "error": message,
                    "reason": "frozen_metadata_missing",
                },
            }
        )
        return result

    client_name = (
        portal_metadata.get("detail_client_name")
        or portal_metadata.get("client_name")
        or "CLIENTE_NAO_IDENTIFICADO"
    )
    entry_date = portal_metadata.get("entry_date") or portal_metadata.get("entry_date_raw")
    completion_decision = _completion_value_from_metadata(portal_metadata)
    completion_date = completion_decision["value"]

    if apply_excel:
        excel_status = update_excel_from_pdf_data(
            workbook_path=workbook_path,
            protocol=protocol,
            client_name=str(client_name),
            entry_date=entry_date,
            completion_date=completion_date,
            module_text=None,
            inverter_text=None,
            parecer="False",
            dry_run=dry_run,
            backup_path=backup_path,
            require_completion_date=bool(_is_completed_status(portal_metadata.get("status"))),
        )
    else:
        excel_status = _skipped_excel_status(protocol, dry_run)

    archive_status = {
        "success": True,
        "simulated": dry_run,
        "skipped": True,
        "error": None,
        "match_type": "not_applicable",
        "reason": "budget_unavailable",
        "created_folder": False,
        "fallback_mode": "disabled",
        "legacy_gd_ignored": False,
    }
    result["excel_effect"] = _excel_effect(excel_status, dry_run, apply_excel)
    result["archive_effect"] = _archive_effect(archive_status, dry_run, True)
    result["rollback_possible"] = result["excel_effect"] == "applied"

    if state_store:
        state_store.update_section(
            protocol,
            "excel",
            _state_excel_payload(excel_status, workbook_path, dry_run, apply_excel),
            last_step=_state_excel_last_step(excel_status, dry_run, apply_excel),
        )
        state_store.update_section(
            protocol,
            "archive",
            {
                **_state_archive_payload(
                    archive_status,
                    None,
                    None,
                    dry_run,
                    True,
                ),
                "status": "skipped_budget_unavailable",
            },
            last_step="archive_skipped_budget_unavailable",
        )
        result["state_effect"] = "persisted"

    success = bool(excel_status.get("success")) and archive_status["success"]
    result.update(
        {
            "success": success,
            "client_name": client_name,
            "entry_date": entry_date,
            "completion_date": completion_date,
            "completion_raw": completion_decision["raw"],
            "completion_normalized": completion_decision["normalized"],
            "completion_source_stage": completion_decision["source_stage"],
            "completion_extraction_status": completion_decision["extraction_status"],
            "completion_action": excel_status.get("completion_action"),
            "completion_reason": completion_decision["reason"],
            "completion_pending_review": completion_decision["pending_review"],
            "metadata_source": metadata_source,
            "target_sheet": excel_status.get("target_sheet"),
            "source_sheet": excel_status.get("source_sheet"),
            "source_row": excel_status.get("source_row"),
            "target_row": excel_status.get("target_row"),
            "existing_row": excel_status.get("existing_row"),
            "new_row": excel_status.get("new_row"),
            "moved_from": excel_status.get("moved_from"),
            "moved_to": excel_status.get("moved_to"),
            "action": excel_status.get("action"),
            "row_number": excel_status.get("row_number"),
            "warning": excel_status.get("warning"),
            "technical_validation_status": "metadata_only",
            "technical_review_required": False,
            "technical_validation_errors": [],
            "technical_validation_warnings": ["BUDGET_UNAVAILABLE"],
            "client_folder_match_type": "not_applicable",
            "client_folder_confidence": 0.0,
            "client_folder_cache_hit": False,
            "client_folder_reason": "budget_unavailable",
            "reason": "budget_unavailable",
            "archive_reason": "budget_unavailable",
            "archive_match_type": "not_applicable",
            "archive_created_folder": False,
            "archive_fallback_mode": "disabled",
            "legacy_gd_ignored": False,
            "archive_status": archive_status,
            "excel_status": _compact_excel_status(excel_status),
            "error": None if success else excel_status.get("error"),
        }
    )
    if success and state_store:
        state_store.mark_completed(protocol)
        result["state_effect"] = "persisted"
    return result


def _load_metadata_for_protocol(
    protocol: str,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if portal_metadata_by_protocol and protocol in portal_metadata_by_protocol:
        return dict(portal_metadata_by_protocol[protocol]), "frozen_op5_plan"
    metadata, source = load_portal_metadata(
        get_settings().downloads_dir_path,
        get_settings().logs_dir_path,
        protocol,
    )
    return metadata, source


def _blocked_metadata_only_result(protocol: str, error: str) -> dict:
    result = _empty_result(Path(f"metadata_only_{protocol}.pdf"))
    result.update(
        {
            "pdf_path": None,
            "protocol": protocol,
            "download_status": "budget_unavailable",
            "budget_unavailable": True,
            "processing_reason": "budget_unavailable_metadata_only",
            "technical_validation_status": "metadata_only",
            "error": error,
        }
    )
    result["excel_status"]["error"] = error
    result["archive_status"].update(
        {
            "success": False,
            "simulated": None,
            "skipped": True,
            "error": "Arquivamento nao executado por falha na planilha.",
            "reason": "preflight_blocked",
        }
    )
    return result


def _record_completed_index_best_effort(
    *,
    protocol: str,
    pdf_path: Path,
    archived_pdf_path: Path | None,
    workbook_path: Path,
    excel_status: dict[str, Any],
    archive_status: dict[str, Any],
    dry_run: bool,
    portal_metadata: dict[str, Any] | None = None,
) -> str:
    if dry_run:
        return "not_applicable"
    if not excel_status.get("success") or not archive_status.get("success"):
        return "not_applicable"
    if not archived_pdf_path:
        return "not_applicable"
    if not portal_metadata or not str(portal_metadata.get("op5_selection_scope") or "").strip():
        return "not_applicable"
    try:
        settings = get_settings()
        index_path = getattr(settings, "op5_completed_index_path", None)
        if index_path is None:
            return "not_applicable"
        record_completed_protocol(
            index_path=index_path,
            protocol=protocol,
            download_pdf_path=pdf_path,
            archived_pdf_path=archived_pdf_path,
            workbook_path=workbook_path,
            workbook_sheet=excel_status.get("target_sheet")
            or excel_status.get("worksheet")
            or excel_status.get("source_sheet"),
            workbook_row=excel_status.get("target_row")
            or excel_status.get("row_number")
            or excel_status.get("existing_row"),
            source_pdf_sha256=archive_status.get("source_pdf_sha256"),
            archived_pdf_sha256=archive_status.get("archived_pdf_sha256"),
            portal_page_number=_optional_int(portal_metadata.get("page_number"))
            if portal_metadata
            else None,
            portal_row_index=_optional_int(portal_metadata.get("row_index"))
            if portal_metadata
            else None,
            portal_anchor_scope=str(portal_metadata.get("op5_selection_scope") or "")
            if portal_metadata
            else None,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        logger.error("Falha ao persistir indice mestre OP5 privado.")
        return "failed"
    return "persisted"


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _add_state_error_best_effort(
    state_store,
    protocol: str,
    step: str,
    message: str,
) -> None:
    try:
        state_store.add_error(protocol, step, message)
    except (OSError, TypeError, ValueError):
        logger.error("Falha ao persistir erro secundário no state privado.")


def _load_or_extract_technical_data(pdf_path: Path, state_store=None) -> tuple:
    protocol_hint = _protocol_from_filename(pdf_path)
    state_entry = (
        state_store.get_protocol(protocol_hint)
        if state_store and protocol_hint
        else None
    )
    technical = state_entry.get("technical_processing") if state_entry else None
    cached = (
        _validated_technical_cache(technical)
        if protocol_hint
        and state_store
        and not state_store.is_force_reprocess(protocol_hint)
        and _technical_cache_matches_pdf(technical, pdf_path, protocol_hint)
        else None
    )
    if cached is not None and protocol_hint:
        state_entry = state_entry or {}
        return (
            protocol_hint,
            state_entry.get("client_name") or "CLIENTE_NAO_IDENTIFICADO",
            cached[0],
            cached[1],
            cached[2],
            cached[3],
            cached[4],
        )

    text = extract_pdf_text(pdf_path)
    protocol = extract_protocol_from_pdf_text(text) or protocol_hint
    client_name = extract_client_from_pdf_text(text) or "CLIENTE_NAO_IDENTIFICADO"
    if not protocol:
        return (
            None,
            client_name,
            "",
            "",
            "",
            "",
            TechnicalValidationResult(
                status="pending_review",
                errors=["PROTOCOL_MISSING"],
                user_message="Protocolo não identificado no PDF.",
            ),
        )
    if protocol_hint is None or protocol != protocol_hint:
        return (
            protocol,
            client_name,
            "",
            "",
            "",
            "",
            TechnicalValidationResult(
                status="pending_review",
                errors=["FROZEN_BATCH_SCOPE_VIOLATION"],
                user_message="Protocolo extraído diverge do escopo congelado.",
            ),
        )

    generation_data = extract_generation_data(pdf_path, text=text)
    canonical_collection = generation_data.to_canonical_collection()
    module_excel = generation_data.format_module_for_excel()
    inverter_excel = generation_data.format_inverter_for_excel()
    placa_planilha, inversor_planilha = format_canonical_collection(
        canonical_collection
    )
    technical_validation = validate_canonical_equipment(
        canonical_collection,
        placa_planilha,
        inversor_planilha,
        module_source=generation_data.module_source,
        inverter_source=generation_data.inverter_source,
    )
    if state_store:
        multiple_module_models = generation_data.multiple_module_models()
        multiple_inverter_models = generation_data.multiple_inverter_models()
        module_pairs_count = generation_data.module_pairs_count()
        inverter_pairs_count = generation_data.inverter_pairs_count()
        equipment_warnings = list(canonical_collection.collection_warnings)
        equipment_parse_warning = "; ".join(
            dict.fromkeys(
                warning for warning in equipment_warnings if str(warning).strip()
            )
        ) or generation_data.equipment_parse_warning
        state_store.update_section(
            protocol,
            "technical_processing",
            {
                "status": "validated" if technical_validation.approved else "pending_review",
                "module_excel": module_excel,
                "inverter_excel": inverter_excel,
                "placa_planilha": placa_planilha,
                "inversor_planilha": inversor_planilha,
                "multiple_module_models": multiple_module_models,
                "multiple_inverter_models": multiple_inverter_models,
                "module_pairs_count": module_pairs_count,
                "inverter_pairs_count": inverter_pairs_count,
                "equipment_parse_warning": equipment_parse_warning,
                "technical_validation_status": technical_validation.status,
                "technical_review_required": technical_validation.technical_review_required,
                "technical_validation_errors": technical_validation.errors,
                "technical_validation_warnings": technical_validation.warnings,
                "module_source": technical_validation.module_source,
                "inverter_source": technical_validation.inverter_source,
                "equipment_rules_version": (
                    EQUIPMENT_RULES_VERSION if technical_validation.approved else None
                ),
                "equipment": serialize_equipment_cache(canonical_collection),
                "source_protocol": protocol,
                "source_pdf_sha256": (
                    _sha256(pdf_path) if Path(pdf_path).is_file() else None
                ),
                "format_version": (
                    TECHNICAL_PROCESSING_FORMAT_VERSION
                    if technical_validation.approved
                    else None
                ),
            },
            last_step=(
                "technical_processed"
                if technical_validation.approved
                else "pending_technical_review"
            ),
        )
    return (
        protocol,
        client_name,
        module_excel,
        inverter_excel,
        placa_planilha,
        inversor_planilha,
        technical_validation,
    )


def _validated_technical_cache(
    technical: object,
) -> tuple[str, str, str, str, TechnicalValidationResult] | None:
    cached = load_validated_equipment_cache(technical)
    if cached is None:
        return None
    return (
        cached.module_excel,
        cached.inverter_excel,
        cached.placa_planilha,
        cached.inversor_planilha,
        cached.validation,
    )


def _technical_cache_matches_pdf(
    technical: object,
    pdf_path: Path,
    protocol_hint: str,
) -> bool:
    if not isinstance(technical, dict) or not Path(pdf_path).is_file():
        return False
    return (
        technical.get("source_protocol") == protocol_hint
        and technical.get("source_pdf_sha256") == _sha256(pdf_path)
    )


def _equipment_cache_payload(data: GenerationData) -> dict[str, Any]:
    return serialize_equipment_cache(data)


def _pending_technical_review_result(
    *,
    result: dict,
    protocol: str,
    client_name: str,
    module_excel: str,
    inverter_excel: str,
    placa_planilha: str,
    inversor_planilha: str,
    validation: TechnicalValidationResult,
    state_store,
    dry_run: bool,
) -> dict:
    message = validation.user_message or "Extração técnica inconclusiva."
    result.update(
        {
            "success": False,
            "protocol": protocol,
            "client_name": client_name,
            "module_excel": module_excel,
            "inverter_excel": inverter_excel,
            "placa_planilha": placa_planilha,
            "inversor_planilha": inversor_planilha,
            "action": "pending_technical_review",
            "technical_validation_status": validation.status,
            "technical_review_required": True,
            "technical_validation_errors": validation.errors,
            "technical_validation_warnings": validation.warnings,
            "module_source": validation.module_source,
            "inverter_source": validation.inverter_source,
            "recommended_action": validation.recommended_action,
            "error": message,
            "warning": message,
            "excel_status": {
                **result["excel_status"],
                "success": False,
                "skipped": True,
                "error": message,
                "action": "pending_technical_review",
            },
            "archive_status": {
                **result["archive_status"],
                "success": False,
                "simulated": dry_run,
                "skipped": True,
                "error": "PDF preservado para nova tentativa após conferência técnica.",
                "reason": "pending_technical_review",
            },
        }
    )
    if state_store:
        state_store.update_protocol(
            protocol,
            status="pending_review",
            last_step="pending_technical_review",
        )
    logger.warning(
        "Protocolo mantido pendente de conferência técnica: "
        f"protocol={protocol}; errors={','.join(validation.errors) or '-'}"
    )
    return result


def _resolve_pdf_paths(
    downloads_root: Path, pdf_paths: Iterable[Path] | None
) -> list[Path]:
    if pdf_paths is None:
        return sorted(downloads_root.rglob("Orcamento_de_Conexao_*.pdf"))

    resolved: list[Path] = []
    seen: set[str] = set()
    for pdf_path in pdf_paths:
        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = downloads_root / candidate
        key = str(candidate.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(candidate)
    return resolved


def _scope_violation_summary(
    pdfs: list[Path],
    unknown_protocols: list[str],
    *,
    logs_dir: Path,
    dry_run: bool,
    apply_excel: bool,
    apply_archive: bool,
) -> dict:
    message = "FROZEN_BATCH_SCOPE_VIOLATION"
    now = datetime.now().isoformat(timespec="seconds")
    payload = {
        "started_at": now,
        "finished_at": now,
        "dry_run": dry_run,
        "apply_excel": apply_excel,
        "apply_archive": apply_archive,
        "status": "BLOQUEADO",
        "operation_message": "Processamento bloqueado por protocolo fora do lote congelado.",
        "real_run_block_code": message,
        "real_run_block_reason": message,
        "blocked_real_run": True,
        "blocked_pending_protocols": 0,
        "scope_violation_count": len(unknown_protocols),
        "protocols_added_after_freeze": len(unknown_protocols),
        "total_pdfs": len(pdfs),
        "total_success": 0,
        "total_errors": len(pdfs),
        "total_excel_updated": 0,
        "total_archived": 0,
        "results": [],
    }
    return _persist_processing_reports(logs_dir, payload)


def _extracted_scope_violations(
    pdfs: list[Path],
    simulation_results: list[dict],
    allowed_protocols: set[str] | None,
) -> list[str]:
    if allowed_protocols is None:
        return []
    violations: set[str] = set()
    for pdf_path, item in zip(pdfs, simulation_results, strict=False):
        expected = _protocol_from_filename(pdf_path)
        extracted = str(item.get("protocol") or "").strip()
        if not expected or not extracted or extracted != expected or extracted not in allowed_protocols:
            violations.add(extracted or expected or "PROTOCOL_NOT_IDENTIFIED")
    return sorted(violations)


def _critical_simulation_issues(results: list[dict], apply_excel: bool) -> list[dict]:
    critical = []
    for item in results:
        if _is_protocol_pending_review(item):
            continue
        if _is_protocol_no_change(item):
            continue
        excel_status = item.get("excel_status", {})
        if item.get("error"):
            critical.append(item)
            continue
        if apply_excel and not excel_status.get("can_write") and not excel_status.get("skipped"):
            critical.append(item)
    return critical


def _apply_processable_subset_from_simulation(
    *,
    pdfs: list[Path],
    simulation_results: list[dict],
    workbook_path: Path,
    clientes_root: Path,
    backup_path: Path | None,
    apply_excel: bool,
    apply_archive: bool,
    state_store,
    portal_metadata_by_protocol: dict[str, dict[str, Any]] | None = None,
) -> list[dict]:
    simulation_by_path: dict[str, dict] = {}
    for item in simulation_results:
        raw_pdf_path = item.get("pdf_path")
        if not raw_pdf_path:
            continue
        simulation_by_path[str(Path(str(raw_pdf_path)).resolve(strict=False))] = item
    real_results_by_path: dict[str, dict] = {}

    for pdf_path in pdfs:
        key = str(Path(pdf_path).resolve(strict=False))
        simulation_item = simulation_by_path.get(key)
        if simulation_item is not None:
            if _is_protocol_no_change(simulation_item):
                continue
            if not _is_protocol_safe_or_no_change(simulation_item, apply_excel):
                continue
        real_result = _process_single_pdf_for_metadata_scope(
            pdf_path,
            workbook_path,
            clientes_root,
            False,
            backup_path,
            apply_excel,
            apply_archive,
            state_store,
            portal_metadata_by_protocol=portal_metadata_by_protocol,
        )
        real_result["processing_phase"] = "application"
        real_results_by_path[key] = real_result

    results: list[dict] = []
    for pdf_path in pdfs:
        key = str(Path(pdf_path).resolve(strict=False))
        if key in real_results_by_path:
            results.append(real_results_by_path[key])
            continue
        simulation_item = dict(simulation_by_path.get(key) or _blocked_result(
            pdf_path,
            "Resultado de simulacao nao localizado para o protocolo.",
        ))
        simulation_item["processing_phase"] = "simulation_only"
        simulation_item["real_run_skipped_reason"] = (
            "excel_already_updated"
            if _is_protocol_no_change(simulation_item)
            else "protocol_not_safe_to_apply"
        )
        results.append(simulation_item)
    return results


def _verify_restored_workbook(backup_path: Path, workbook_path: Path) -> bool:
    backup = Path(backup_path)
    restored = Path(workbook_path)
    backup_ok, _backup_errors = validate_xlsx_integrity(backup)
    restored_ok, _restored_errors = validate_xlsx_integrity(restored)
    if not backup_ok or not restored_ok:
        return False
    return _sha256(backup) == _sha256(restored)


def _real_run_preflight_error(
    workbook_path: Path, dry_run: bool, apply_excel: bool
) -> str | None:
    return _real_run_preflight_result(
        workbook_path,
        dry_run,
        apply_excel,
        stage="antes da execução real",
    ).get("error")


def _real_run_preflight_result(
    workbook_path: Path,
    dry_run: bool,
    apply_excel: bool,
    *,
    stage: str,
) -> dict[str, Any]:
    if dry_run or not apply_excel:
        return {"success": True, "error": None, "code": None, "stage": stage}

    validation = validate_workbook_for_pdf_updates(
        workbook_path, require_writable=True
    )
    validation["stage"] = stage
    if validation["success"]:
        return validation
    validation["error"] = validation.get("error") or (
        "Falha ao validar planilha antes da execução real."
    )
    return validation


def _already_archived_path_from_state(
    state_store, protocol: str, source_pdf_path: Path
) -> str | None:
    if not state_store:
        return None

    entry = state_store.get_protocol(protocol) or {}
    archived_pdf_path = entry.get("archive", {}).get("archived_pdf_path")
    if archived_pdf_path and Path(archived_pdf_path).exists():
        try:
            if _sha256(source_pdf_path) == _sha256(Path(archived_pdf_path)):
                return str(archived_pdf_path)
        except OSError:
            return None
    return None


def _build_markdown_report(payload: dict) -> str:
    lines = [
        "# Processamento de PDFs, Planilha e Pastas de Clientes",
        "",
        "## Resumo",
        "",
        f"- Status: {payload.get('status')}",
        f"- Início: {payload.get('started_at')}",
        f"- Fim: {payload.get('finished_at')}",
        f"- Total de PDFs: {payload.get('total_pdfs', 0)}",
        f"- PDFs analisados: {payload.get('total_pdfs_analyzed', 0)}",
        f"- Protocolos seguros: {payload.get('total_safe_protocols', 0)}",
        f"- Protocolos sem alteração: {payload.get('total_no_change_protocols', 0)}",
        f"- Protocolos pendentes: {payload.get('total_pending_protocols', 0)}",
        f"- Updates planejados: {payload.get('total_updates_planned', 0)}",
        f"- Updates aplicados: {payload.get('total_updates_applied', 0)}",
        f"- Bloqueados por politica de lote: {payload.get('total_blocked_by_batch_policy', 0)}",
        f"- Erros reais de extracao: {payload.get('total_real_extraction_errors', 0)}",
        f"- Erros reais de aplicacao: {payload.get('total_real_application_errors', 0)}",
        f"- Sucessos: {payload.get('total_success', 0)}",
        f"- Erros: {payload.get('total_errors', 0)}",
        f"- Pendências técnicas: {payload.get('total_technical_pending_review', 0)}",
        "",
        "## Protocolos Processados",
        "",
        "| Protocolo | Ação | Validação técnica | Conferência |",
        "| --- | --- | --- | --- |",
    ]
    for item in payload.get("results", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.get("protocol")),
                    _md(item.get("action")),
                    _md(item.get("technical_validation_status")),
                    _md(item.get("technical_review_required")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _privacy_safe_report_payload(payload: dict) -> dict:
    top_level_allowlist = {
        "status",
        "operation_message",
        "started_at",
        "finished_at",
        "duration_seconds",
        "dry_run",
        "apply_excel",
        "apply_archive",
        "total_pdfs",
        "total_pdfs_analyzed",
        "total_technically_approved",
        "total_safe_protocols",
        "total_no_change_protocols",
        "total_pending_protocols",
        "total_failed_protocols",
        "total_updates_planned",
        "total_updates_applied",
        "total_pdfs_retained_for_retry",
        "total_success",
        "total_errors",
        "total_excel_updated",
        "total_archived",
        "total_pending_review",
        "total_client_folder_pending_review",
        "total_technical_pending_review",
        "total_completion_dates_found",
        "total_completion_dates_updated",
        "total_completion_marked_open",
        "completion_dates_proposed",
        "completion_dates_applied",
        "open_values_proposed",
        "open_values_applied",
        "total_completion_no_change",
        "total_completion_pending_review",
        "blocked_real_run",
        "real_run_block_code",
        "block_stage",
        "rollback_executed",
        "rollback_status",
        "report_effect",
        "report_error_code",
    }
    result_allowlist = {
        "protocol",
        "action",
        "technical_validation_status",
        "technical_review_required",
        "technical_validation_errors",
        "technical_validation_warnings",
        "module_source",
        "inverter_source",
        "recommended_action",
        "completion_date",
        "completion_raw",
        "completion_normalized",
        "completion_source_stage",
        "completion_extraction_status",
        "completion_action",
        "completion_reason",
        "completion_pending_review",
        "client_folder_match_type",
        "excel_effect",
        "archive_effect",
        "state_effect",
        "report_effect",
        "manual_action_required",
        "rollback_possible",
    }
    report = {
        key: payload[key]
        for key in top_level_allowlist
        if key in payload
    }
    report["classification"] = "PRIVATE_OPERATIONAL"
    report["schema_version"] = 1
    report["results"] = []
    for item in payload.get("results", []):
        safe_item = {
            key: item[key]
            for key in result_allowlist
            if key in item
        }
        excel = item.get("excel_status")
        if isinstance(excel, dict):
            safe_item["excel_status"] = {
                key: excel[key]
                for key in ("success", "skipped", "action")
                if key in excel
            }
        archive = item.get("archive_status")
        if isinstance(archive, dict):
            safe_item["archive_status"] = {
                key: archive[key]
                for key in ("success", "skipped", "simulated")
                if key in archive
            }
        report["results"].append(safe_item)
    return report


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")
