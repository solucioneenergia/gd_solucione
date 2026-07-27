import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from automacao_gd.application.contracts import OperationStatus
from automacao_gd.infrastructure.files.client_folder_service import (
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
def process_downloaded_pdfs(
    downloads_root: Path,
    workbook_path: Path,
    clientes_root: Path,
    dry_run: bool = True,
    pdf_paths: Iterable[Path] | None = None,
    apply_excel: bool = True,
    apply_archive: bool = True,
    state_store=None,
) -> dict:
    ensure_directories()
    settings = get_settings()
    clear_folder_cache()
    downloads_root = Path(downloads_root)
    workbook_path = Path(workbook_path)
    clientes_root = Path(clientes_root)
    logs_dir = settings.logs_dir_path
    logs_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now()
    logger.info(
        "Iniciando processamento offline de PDFs baixados: "
        f"downloads={downloads_root}, planilha={workbook_path}, "
        f"clientes={clientes_root}, dry_run={dry_run}, "
        f"apply_excel={apply_excel}, apply_archive={apply_archive}"
    )

    pdfs = _resolve_pdf_paths(downloads_root, pdf_paths)
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
    elif not dry_run:
        simulation_results = [
            _process_single_pdf(
                pdf_path,
                workbook_path,
                clientes_root,
                True,
                None,
                apply_excel,
                apply_archive,
                None,
            )
            for pdf_path in pdfs
        ]
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
                )
                systemic_issues = _systemic_real_apply_issues(results, apply_excel)
                if systemic_issues:
                    systemic_apply_failure = True
                    if backup_path is not None:
                        rollback_executed = True
                        try:
                            atomic_copy_file(backup_path, workbook_path, private=True)
                            rollback_status = "CONFIRMED"
                        except Exception as exc:  # pragma: no cover - defensive branch
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
        results = [
            _process_single_pdf(
                pdf_path,
                workbook_path,
                clientes_root,
                dry_run,
                backup_path,
                apply_excel,
                apply_archive,
                state_store,
            )
            for pdf_path in pdfs
        ]
        if apply_excel:
            results = project_dry_run_target_rows(results, workbook_path)
    finished_at = datetime.now()

    total_success = sum(1 for item in results if item["success"])
    total_errors = sum(1 for item in results if not item["success"])
    status, operation_message = _classify_processing_result(
        dry_run=dry_run,
        total_pdfs=len(pdfs),
        total_success=total_success,
        total_errors=total_errors,
        blocked_real_run=blocked_real_run,
        systemic_apply_failure=systemic_apply_failure,
    )
    metrics = _processing_metrics(results, dry_run, apply_excel)
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
        **metrics,
        "total_success": total_success,
        "total_errors": total_errors,
        "total_excel_updated": _count_excel_updates(results, dry_run, apply_excel),
        "total_updates_applied": _count_excel_updates(results, dry_run, apply_excel),
        "total_archived": _count_archived(results, dry_run, apply_archive),
        "total_pending_review": sum(
            1
            for item in results
            if item["client_folder_match_type"] in {"pending_review", "not_found"}
            or item.get("archive_match_type") == "pending_manual_review"
            or item.get("technical_review_required")
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

    json_path = logs_dir / JSON_REPORT_NAME
    markdown_path = logs_dir / MARKDOWN_REPORT_NAME
    report_payload = _privacy_safe_report_payload(payload)
    atomic_write_json(json_path, report_payload, private=True)
    atomic_write_text(
        markdown_path,
        _build_markdown_report(report_payload),
        private=True,
    )

    payload["json_report_path"] = str(json_path)
    payload["markdown_report_path"] = str(markdown_path)
    logger.info(f"Relatório JSON salvo em: {json_path}")
    logger.info(f"Relatório Markdown salvo em: {markdown_path}")
    return payload


def _process_single_pdf(
    pdf_path: Path,
    workbook_path: Path,
    clientes_root: Path,
    dry_run: bool,
    backup_path: Path | None = None,
    apply_excel: bool = True,
    apply_archive: bool = True,
    state_store=None,
) -> dict:
    logger.info(f"Processando PDF baixado: {pdf_path}")
    result = _empty_result(pdf_path)

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

        portal_metadata, metadata_source = load_portal_metadata(
            get_settings().downloads_dir_path,
            get_settings().logs_dir_path,
            protocol,
        )
        entry_date = portal_metadata.get("entry_date") if portal_metadata else None
        completion_date = (
            portal_metadata.get("completion_date") if portal_metadata else None
        )

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
        target_folder = archive_destination.destination_folder
        target_pdf_path = (
            build_destination_pdf_path(target_folder, protocol)
            if target_folder
            else None
        )

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
            )
        else:
            excel_status = _skipped_excel_status(protocol, dry_run)

        if state_store:
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
                state_store, protocol
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
                    "skipped": False,
                    "error": archive_result.error,
                    "match_type": archive_result.match_type,
                    "reason": archive_result.reason,
                    "created_folder": archive_result.created_folder,
                    "fallback_mode": archive_result.fallback_mode,
                    "legacy_gd_ignored": archive_result.legacy_gd_ignored,
                }
                archived_pdf_path = archive_result.archived_pdf_path
                if archive_result.destination_folder:
                    target_folder = Path(archive_result.destination_folder)

        if state_store:
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

        result.update(
            {
                "success": archive_status["success"] and excel_status["success"],
                "protocol": protocol,
                "client_name": client_name,
                "entry_date": entry_date,
                "completion_date": completion_date,
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
                "error": None,
            }
        )
        if not result["success"]:
            result["error"] = _join_errors(archive_status, excel_status)
            operational_excel_pending = (
                apply_excel and not dry_run and not excel_status.get("success")
            )
            if state_store and result["error"] and not operational_excel_pending:
                state_store.add_error(protocol, "processing", result["error"])
            logger.warning(
                f"PDF processado com pendências: protocolo={protocol}, "
                f"erro={result['error']}"
            )
        elif state_store:
            state_store.mark_completed(protocol)
        return result
    except Exception as exc:
        logger.exception(f"Falha ao processar PDF {pdf_path}: {exc}")
        result["error"] = str(exc)
        protocol = result.get("protocol") or _protocol_from_filename(pdf_path)
        if state_store and protocol:
            state_store.add_error(protocol, "processing", result["error"])
        return result


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


def _skipped_excel_status(protocol: str, dry_run: bool) -> dict:
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


def _empty_result(pdf_path: Path) -> dict:
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
    }


def _blocked_result(pdf_path: Path, error: str) -> dict:
    result = _empty_result(pdf_path)
    result["error"] = error
    result["excel_status"]["error"] = error
    result["archive_status"]["error"] = "Arquivamento não executado por falha na planilha."
    return result


def _critical_simulation_issues(results: list[dict], apply_excel: bool) -> list[dict]:
    critical = []
    for item in results:
        if _is_protocol_pending_review(item):
            continue
        excel_status = item.get("excel_status", {})
        if item.get("error"):
            critical.append(item)
            continue
        if apply_excel and not excel_status.get("can_write") and not excel_status.get("skipped"):
            critical.append(item)
    return critical


def _is_protocol_pending_review(item: dict) -> bool:
    return (
        bool(item.get("technical_review_required"))
        or item.get("action") == "pending_technical_review"
        or item.get("technical_validation_status") == "pending_review"
    )


def _is_protocol_no_change(item: dict) -> bool:
    excel_status = item.get("excel_status") or {}
    return (
        item.get("action") == "skipped_excel_already_updated"
        or excel_status.get("action") == "skipped_excel_already_updated"
    )


def _is_protocol_safe_or_no_change(item: dict, apply_excel: bool) -> bool:
    if _is_protocol_pending_review(item) or item.get("error"):
        return False
    if not apply_excel:
        return True
    excel_status = item.get("excel_status") or {}
    return bool(excel_status.get("can_write")) or bool(excel_status.get("skipped"))


def _has_processable_protocols(results: list[dict], apply_excel: bool) -> bool:
    return any(_is_protocol_safe_or_no_change(item, apply_excel) for item in results)


def _has_excel_updates_to_apply(results: list[dict]) -> bool:
    return any(
        (item.get("excel_status") or {}).get("can_write")
        and not (item.get("excel_status") or {}).get("skipped")
        for item in results
        if not _is_protocol_pending_review(item)
    )


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
        if simulation_item is not None and not _is_protocol_safe_or_no_change(
            simulation_item,
            apply_excel,
        ):
            continue
        real_result = _process_single_pdf(
            pdf_path,
            workbook_path,
            clientes_root,
            False,
            backup_path,
            apply_excel,
            apply_archive,
            state_store,
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
        simulation_item["real_run_skipped_reason"] = "protocol_not_safe_to_apply"
        results.append(simulation_item)
    return results


def _systemic_real_apply_issues(results: list[dict], apply_excel: bool) -> list[dict]:
    if not apply_excel:
        return []
    issues = []
    for item in results:
        if _is_protocol_pending_review(item):
            continue
        excel_status = item.get("excel_status") or {}
        if not excel_status.get("success") and not excel_status.get("skipped"):
            issues.append(item)
    return issues


def _mark_results_after_rollback(results: list[dict], *, reason: str) -> list[dict]:
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
        marked.append(result)
    return marked


def _processing_metrics(results: list[dict], dry_run: bool, apply_excel: bool) -> dict:
    technically_approved = [
        item for item in results if item.get("technical_validation_status") == "approved"
    ]
    pending = [item for item in results if _is_protocol_pending_review(item)]
    no_change = [item for item in results if _is_protocol_no_change(item)]
    safe = [
        item
        for item in results
        if _is_protocol_safe_or_no_change(item, apply_excel)
        and not _is_protocol_no_change(item)
    ]
    failed = [
        item
        for item in results
        if item.get("error")
        and not _is_protocol_pending_review(item)
        and not _is_protocol_safe_or_no_change(item, apply_excel)
    ]
    updates_planned = sum(
        1
        for item in results
        if (item.get("excel_status") or {}).get("can_write")
        and not (item.get("excel_status") or {}).get("skipped")
    )
    updates_applied = _count_excel_updates(results, dry_run, apply_excel)
    return {
        "total_pdfs_analyzed": len(results),
        "total_technically_approved": len(technically_approved),
        "total_safe_protocols": len(safe),
        "total_no_change_protocols": len(no_change),
        "total_pending_protocols": len(pending),
        "total_failed_protocols": len(failed),
        "total_updates_planned": updates_planned,
        "total_updates_applied": updates_applied,
        "total_pdfs_retained_for_retry": len(pending),
    }


def _real_run_blocked_from_simulation(item: dict, reason: str) -> dict:
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


def _classify_processing_result(
    *,
    dry_run: bool,
    total_pdfs: int,
    total_success: int,
    total_errors: int,
    blocked_real_run: bool,
    systemic_apply_failure: bool = False,
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
    if total_errors:
        return OperationStatus.FALHOU, "Nenhum PDF foi processado com sucesso."
    if total_pdfs == 0:
        return OperationStatus.SUCESSO, "Nenhuma atualização necessária."
    return OperationStatus.SUCESSO, (
        "Simulação concluída com sucesso."
        if dry_run
        else "Processamento concluído com sucesso."
    )


def _compact_excel_status(status: dict[str, Any]) -> dict:
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
    }


def _state_excel_payload(
    excel_status: dict[str, Any],
    workbook_path: Path,
    dry_run: bool,
    apply_excel: bool,
) -> dict:
    return {
        "status": _state_excel_status(excel_status, dry_run, apply_excel),
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


def _state_excel_status(
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


def _state_excel_last_step(
    excel_status: dict[str, Any],
    dry_run: bool,
    apply_excel: bool,
) -> str:
    status = _state_excel_status(excel_status, dry_run, apply_excel)
    if status == "applied":
        return "excel_applied"
    if excel_status.get("success"):
        return "excel_skipped"
    return "failed"


def _already_archived_path_from_state(state_store, protocol: str) -> str | None:
    if not state_store:
        return None

    entry = state_store.get_protocol(protocol) or {}
    archived_pdf_path = entry.get("archive", {}).get("archived_pdf_path")
    if archived_pdf_path and Path(archived_pdf_path).exists():
        return str(archived_pdf_path)
    return None


def _state_archive_payload(
    archive_status: dict[str, Any],
    target_folder: Path | None,
    archived_pdf_path: str | None,
    dry_run: bool,
    apply_archive: bool,
) -> dict:
    return {
        "status": _state_archive_status(archive_status, dry_run, apply_archive),
        "target_folder": str(target_folder) if target_folder else None,
        "archived_pdf_path": archived_pdf_path,
        "dry_run": dry_run,
        "reason": archive_status.get("reason"),
        "error": archive_status.get("error"),
        "match_type": archive_status.get("match_type"),
        "created_folder": archive_status.get("created_folder"),
        "fallback_mode": archive_status.get("fallback_mode"),
        "legacy_gd_ignored": archive_status.get("legacy_gd_ignored"),
    }


def _state_archive_status(
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


def _state_archive_last_step(
    archive_status: dict[str, Any],
    dry_run: bool,
    apply_archive: bool,
) -> str:
    status = _state_archive_status(archive_status, dry_run, apply_archive)
    if status == "archived":
        return "archived"
    if archive_status.get("success"):
        return "archive_skipped"
    return "failed"


def _count_excel_updates(
    results: list[dict], dry_run: bool, apply_excel: bool
) -> int:
    if dry_run or not apply_excel:
        return 0
    return sum(
        1
        for item in results
        if item["excel_status"].get("can_write")
        and not item["excel_status"].get("skipped")
    )


def _count_archived(
    results: list[dict], dry_run: bool, apply_archive: bool
) -> int:
    if dry_run or not apply_archive:
        return 0
    return sum(
        1
        for item in results
        if item["archive_status"].get("success")
        and not item["archive_status"].get("skipped")
    )


def _join_errors(archive_status: dict, excel_status: dict) -> str | None:
    errors = [
        str(error)
        for error in [archive_status.get("error"), excel_status.get("error")]
        if error
    ]
    return "; ".join(errors) if errors else None


def _protocol_from_filename(pdf_path: Path) -> str | None:
    match = re.search(r"Orcamento_de_Conexao_(\d+)", pdf_path.stem)
    return match.group(1) if match else None


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
        "total_technical_pending_review",
        "blocked_real_run",
        "real_run_block_code",
        "block_stage",
        "rollback_executed",
        "rollback_status",
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
        "client_folder_match_type",
    }
    report = {
        key: payload[key]
        for key in top_level_allowlist
        if key in payload
    }
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
