from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from automacao_gd.application.historical_backfill.apply_service import (
    BackfillApplyError,
    execute_backfill_application,
    prepare_backfill_application,
)
from automacao_gd.application.historical_backfill.audit_service import (
    HistoricalAuditConsistencyError,
    audit_historical_workbook,
)
from automacao_gd.application.historical_backfill.plan import (
    BackfillPlanError,
    build_backfill_plan,
    load_backfill_plan,
    write_backfill_plan,
)
from automacao_gd.application.historical_backfill.report import (
    backfill_artifact_paths,
    write_apply_reports,
    write_audit_reports,
)
from automacao_gd.application.contracts import OperationResult, OperationStatus
from automacao_gd.application.operational_guard import (
    authorize_offline_batch,
    build_option4_strong_confirmation,
    prepare_offline_batch,
)
from automacao_gd.application.op5_retention import (
    apply_download_retention_plan,
    audit_download_retention,
)
from automacao_gd.application.workbook_format_flow import (
    apply_workbook_format_plan,
    audit_workbook_format,
)
from automacao_gd.application.full_pipeline import (
    BatchAuthorizationError,
    BatchAuthorization,
    OP5_PLAN_JSON_REPORT_NAME,
    StrongConfirmationError,
    batch_authorization_policy_from_settings,
    build_option5_strong_confirmation,
    load_frozen_dry_run_plan,
    run_op5_archive_plan,
    run_op5_audit_global,
    validate_option5_strong_confirmation,
    validate_requested_batch_limit,
)
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.excel.availability import validate_workbook_availability
from automacao_gd.infrastructure.excel.service import create_workbook_backup
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.infrastructure.log_privacy import (
    audit_log_files,
    sanitize_log_with_backup,
)
from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.domain.errors import PreflightBlockedError
from automacao_gd.presentation.controller import ApplicationController
from automacao_gd.presentation.operational_output import print_operation_summary


_EXIT_CODES = {
    OperationStatus.SUCESSO: 0,
    OperationStatus.FALHOU: 1,
    OperationStatus.BLOQUEADO: 2,
    OperationStatus.PARCIAL: 3,
}
COMPLETION_SYNC_STRONG_CONFIRMATION = "APLICAR CONCLUSÃO 5 PROTOCOLOS"
OPTION5_COMPLETION_STRONG_CONFIRMATION = build_option5_strong_confirmation(5)


def exit_code_for_status(status: OperationStatus | str) -> int:
    normalized = status if isinstance(status, OperationStatus) else OperationStatus(status)
    return _EXIT_CODES[normalized]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command is not None:
        if args.command == "logs-audit":
            return _run_logs_audit()
        if args.command == "logs-sanitize":
            return _run_logs_sanitize(args.input, args.confirmation)
        setup_logger(verbose=args.verbose)
        if args.command == "backfill-audit":
            return _run_backfill_audit()
        if args.command == "backfill-apply":
            return _run_backfill_apply(args.plan)
        if args.command == "op5-plan":
            return _run_op5_plan(args.limit, protocols=args.protocols)
        if args.command == "op5-apply":
            return _run_op5_apply(args.plan)
        if args.command == "op5-archive-plan":
            return _run_op5_archive_plan(args.plan)
        if args.command == "op5-audit-global":
            return _run_op5_audit_global()
        if args.command == "op5-retention-audit":
            return _run_op5_retention_audit()
        if args.command == "op5-retention-apply":
            return _run_op5_retention_apply(args.plan)
        if args.command == "workbook-format-audit":
            return _run_workbook_format_audit()
        return _run_workbook_format_apply(args.plan)

    ensure_directories()
    setup_logger(verbose=args.verbose)
    controller = ApplicationController()
    last_exit_code = 0

    actions = {
        "1": ("preflight", "Executando pré-voo...", lambda: controller.preflight()),
        "2": ("inspect_portal", "Inspecionando Portal GD...", lambda: controller.inspect_portal()),
        "3": ("process_dry_run", "Executando processamento em simulação...", lambda: controller.process_downloads(dry_run=True)),
        "4": ("process_real", "Executando processamento real...", lambda: _confirmed_real_processing(controller)),
        "5": (
            "pipeline",
            "Executando pipeline CDP seguro...",
            lambda: _interactive_option5_plan_apply(controller),
        ),
    }
    while True:
        print("\nAutomação GD Neoenergia — versão 2")
        print("1 - Verificar ambiente (pré-voo)")
        print("2 - Inspecionar tabela do portal")
        print("3 - Processar PDFs em simulação")
        print("4 - Processar PDFs e aplicar alterações")
        print("5 - Executar pipeline CDP completo")
        print("6 - Abrir interface desktop visual")
        print("0 - Sair")
        option = input("Escolha uma opção: ").strip()
        if option == "0":
            return last_exit_code
        if option == "6":
            _open_desktop_visual()
            continue
        selected = actions.get(option)
        if selected is None:
            print("Opção inválida.")
            continue
        operation_name, progress_message, action = selected
        print(f"\n{progress_message}")
        result = action()
        print_operation_summary(operation_name, result)
        last_exit_code = exit_code_for_status(result.status)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automação GD Neoenergia — versão 2")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="exibe logs técnicos INFO no terminal",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "backfill-audit",
            "backfill-apply",
            "logs-audit",
            "logs-sanitize",
            "op5-plan",
            "op5-apply",
            "op5-archive-plan",
            "op5-audit-global",
            "op5-retention-audit",
            "op5-retention-apply",
            "workbook-format-audit",
            "workbook-format-apply",
        ),
        help="executa auditoria histórica ou aplica um plano previamente aprovado",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        help="plano JSON versionado exigido por backfill-apply",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="limite solicitado para op5-plan",
    )
    parser.add_argument(
        "--protocols",
        help="lista separada por virgula/ponto e virgula para restringir op5-plan",
    )
    parser.add_argument("--input", type=Path, help="arquivo para logs-sanitize")
    parser.add_argument(
        "--confirmation",
        help="confirmação forte exigida por logs-sanitize",
    )
    return parser.parse_args(argv)


def _run_op5_plan(limit: int | None, *, protocols: str | None = None) -> int:
    if limit is None or limit <= 0:
        print("Status: BLOQUEADO")
        print("op5-plan exige --limit N positivo.")
        return 2
    target_protocols = _normalize_protocol_list(protocols)
    if target_protocols and len(target_protocols) > limit:
        print("Status: BLOQUEADO")
        print("op5-plan --protocols nao pode exceder --limit.")
        return 2
    settings = get_settings().model_copy(
        update={
            "DRY_RUN": True,
            "MAX_COMPLETED_TO_PROCESS": limit,
            "OP5_RECONCILIATION_MODE": "batch_fast",
            "APPLY_EXCEL": True,
            "OP5_TARGET_PROTOCOLS": ",".join(target_protocols),
        }
    )
    authorization = validate_requested_batch_limit(
        limit,
        batch_authorization_policy_from_settings(settings),
    )
    confirmation = build_option5_strong_confirmation(authorization.requested_batch_limit)
    result = ApplicationController(settings).run_pipeline(confirmation=confirmation)
    print_operation_summary("pipeline", result)
    return exit_code_for_status(result.status or OperationStatus.FALHOU)


def _normalize_protocol_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    protocols = tuple(
        item.strip()
        for item in value.replace(";", ",").split(",")
        if item.strip()
    )
    if len(protocols) != len(set(protocols)):
        raise SystemExit("op5-plan --protocols contem protocolo duplicado.")
    return protocols


def _run_op5_apply(plan_path: Path | None) -> int:
    if plan_path is None:
        print("Status: BLOQUEADO")
        print("op5-apply exige --plan <arquivo>.")
        return 2
    settings = get_settings().model_copy(
        update={
            "DRY_RUN": False,
            "OP5_PLAN_PATH": plan_path,
        }
    )
    controller = ApplicationController(settings)
    result = _confirmed_pipeline(controller)
    print_operation_summary("pipeline", result)
    return exit_code_for_status(result.status or OperationStatus.FALHOU)


def _interactive_option5_plan_apply(controller: ApplicationController):
    base_settings = controller.settings
    requested_limit = _prompt_option5_limit(base_settings)
    if requested_limit is None:
        return _cancelled_operation_result("INVALID_BATCH_LIMIT", require_cdp=True)
    try:
        authorization = validate_requested_batch_limit(
            requested_limit,
            batch_authorization_policy_from_settings(base_settings),
        )
    except BatchAuthorizationError as exc:
        return _cancelled_operation_result(exc.code, require_cdp=True)
    confirmation = build_option5_strong_confirmation(
        authorization.requested_batch_limit
    )
    reusable_plan = _load_reusable_interactive_option5_plan(
        base_settings,
        authorization,
    )
    if reusable_plan is not None:
        plan_result = reusable_plan
        print("")
        print("Fase 1/2: plano OP5 existente validado; pulando Portal/CDP.")
    else:
        plan_settings = _copy_settings(
            base_settings,
            {
                "DRY_RUN": True,
                "MAX_COMPLETED_TO_PROCESS": authorization.requested_batch_limit,
                "OP5_RECONCILIATION_MODE": "batch_fast",
                "APPLY_EXCEL": True,
            },
        )
        print("")
        print("Fase 1/2: gerando plano OP5 em simulacao.")
        plan_result = ApplicationController(plan_settings).run_pipeline(
            confirmation=confirmation
        )
        print_operation_summary("pipeline", plan_result)
        if _operation_status(plan_result) is not OperationStatus.SUCESSO:
            return plan_result
    plan_block = _validate_interactive_option5_plan(
        plan_result.payload,
        requested_limit=authorization.requested_batch_limit,
    )
    if plan_block is not None:
        return plan_block

    plan_path = Path(
        str(
            plan_result.payload.get("op5_plan_path")
            or Path(base_settings.logs_dir_path) / OP5_PLAN_JSON_REPORT_NAME
        )
    )
    print("")
    print("Fase 2/2: aplicacao real a partir do plano congelado.")
    print(f"Plano: {plan_path}")
    print(f"Updates planejados: {plan_result.payload.get('total_updates_planned')}")
    print(f"Arquivamento: {plan_result.payload.get('apply_archive')}")
    print("Para aplicar este plano, digite exatamente:")
    print(confirmation)
    operator_confirmation = input("Confirmar apply OP5: ").strip()
    try:
        validate_option5_strong_confirmation(operator_confirmation, authorization)
    except StrongConfirmationError as exc:
        return _cancelled_operation_result(
            exc.code,
            require_cdp=False,
            confirmation_required=confirmation,
        )

    availability = validate_workbook_availability(
        base_settings.planilha_path,
        require_writable=True,
        stage="aplicacao OP5 interativa",
    )
    if not availability.ok:
        return OperationResult(
            False,
            availability.user_message,
            {
                "code": getattr(availability, "code", "WORKBOOK_UNAVAILABLE"),
                "stage": "preflight workbook",
                "total_updates_applied": 0,
            },
            status=OperationStatus.BLOQUEADO,
        )

    try:
        workbook_sha = _sha256_file(base_settings.planilha_path)
        backup_path = create_workbook_backup(base_settings.planilha_path)
        backup_sha = _sha256_file(backup_path)
    except OSError as exc:
        return OperationResult(
            False,
            "Backup da planilha nao pode ser criado com seguranca.",
            {
                "code": "WORKBOOK_BACKUP_FAILED",
                "stage": "backup workbook",
                "technical_cause": type(exc).__name__,
                "total_updates_applied": 0,
            },
            status=OperationStatus.BLOQUEADO,
        )
    if workbook_sha != backup_sha:
        return OperationResult(
            False,
            "Backup da planilha nao confere com o SHA-256 original.",
            {
                "code": "WORKBOOK_BACKUP_HASH_MISMATCH",
                "stage": "backup workbook",
                "workbook_sha256": workbook_sha,
                "backup_sha256": backup_sha,
                "total_updates_applied": 0,
            },
            status=OperationStatus.BLOQUEADO,
        )
    print(f"SHA-256 planilha antes da escrita: {workbook_sha}")
    print(f"Backup criado e validado: {backup_path}")
    print(f"SHA-256 backup: {backup_sha}")

    apply_settings = _copy_settings(
        base_settings,
        {
            "DRY_RUN": False,
            "MAX_COMPLETED_TO_PROCESS": authorization.requested_batch_limit,
            "OP5_PLAN_PATH": plan_path,
        },
    )
    return ApplicationController(apply_settings).run_pipeline(
        confirmation=operator_confirmation
    )


def _load_reusable_interactive_option5_plan(
    base_settings,
    authorization: BatchAuthorization,
) -> OperationResult | None:
    plan_path = Path(base_settings.logs_dir_path) / OP5_PLAN_JSON_REPORT_NAME
    if not plan_path.is_file():
        return None
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if _validate_interactive_option5_plan(
        payload,
        requested_limit=authorization.requested_batch_limit,
    ) is not None:
        return None
    validation_settings = _copy_settings(
        base_settings,
        {
            "DRY_RUN": False,
            "MAX_COMPLETED_TO_PROCESS": authorization.requested_batch_limit,
            "OP5_PLAN_PATH": plan_path,
        },
    )
    try:
        plan = load_frozen_dry_run_plan(validation_settings, authorization)
    except PreflightBlockedError:
        return None
    payload = dict(payload)
    payload["op5_plan_path"] = str(plan.source_path)
    payload["op5_plan_reused_by_interactive_menu"] = True
    payload["op5_plan_source_kind"] = plan.source_kind
    payload["cdp_selection_skipped"] = True
    return OperationResult(
        True,
        "Plano OP5 existente validado e reutilizado.",
        payload,
        status=OperationStatus.SUCESSO,
    )


def _prompt_option5_limit(settings) -> int | None:
    raw = input(
        "Limite do lote OP5 (1-"
        f"{getattr(settings, 'OPTION5_AUTHORIZED_MAX_PROTOCOLS', 5)}): "
    ).strip()
    if not raw:
        raw = str(getattr(settings, "MAX_COMPLETED_TO_PROCESS", 0) or "")
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _copy_settings(settings, updates: dict[str, object]):
    if hasattr(settings, "model_copy"):
        return settings.model_copy(update=updates)
    data = {
        name: getattr(settings, name)
        for name in dir(settings)
        if not name.startswith("_") and not callable(getattr(settings, name))
    }
    data.update(updates)
    from types import SimpleNamespace

    return SimpleNamespace(**data)


def _operation_status(result: OperationResult) -> OperationStatus:
    if isinstance(result.status, OperationStatus):
        return result.status
    return OperationStatus(str(result.status))


def _validate_interactive_option5_plan(
    payload: dict[str, Any],
    *,
    requested_limit: int,
) -> OperationResult | None:
    blockers: list[str] = []
    if str(payload.get("status") or "") != OperationStatus.SUCESSO.value:
        blockers.append("status_not_success")
    if payload.get("dry_run") is not True:
        blockers.append("not_dry_run")
    if payload.get("apply_archive") is not True:
        blockers.append("apply_archive_not_enabled")
    if int(payload.get("total_updates_applied", 0) or 0) != 0:
        blockers.append("dry_run_applied_updates")
    if int(payload.get("total_errors", 0) or 0) != 0:
        blockers.append("dry_run_errors")
    planned = int(payload.get("total_updates_planned", 0) or 0)
    if planned <= 0:
        blockers.append("planned_updates_empty")
    if planned > requested_limit:
        blockers.append("planned_updates_mismatch")
    if payload.get("stale_after_failed_plan") is True:
        blockers.append("stale_plan")
    if payload.get("run_error"):
        blockers.append("run_error")
    download_raw = payload.get("download")
    download = download_raw if isinstance(download_raw, dict) else {}
    if download.get("partial_batch_due_to_listing_recovery"):
        blockers.append("partial_listing_recovery")
    if download.get("abort_reason") == "stopped_after_failed_return_to_listing":
        blockers.append("listing_recovery_stopped")
    if blockers:
        return OperationResult(
            False,
            "Plano OP5 interativo nao atende aos criterios de aplicacao real.",
            {
                "code": "OP5_INTERACTIVE_PLAN_NOT_APPROVED",
                "stage": "validacao do plano OP5",
                "blockers": blockers,
                "total_updates_planned": planned,
                "total_updates_applied": int(
                    payload.get("total_updates_applied", 0) or 0
                ),
                "total_errors": int(payload.get("total_errors", 0) or 0),
            },
            status=OperationStatus.BLOQUEADO,
        )
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _run_op5_archive_plan(plan_path: Path | None) -> int:
    if plan_path is None:
        print("Status: BLOQUEADO")
        print("op5-archive-plan exige --plan <arquivo>.")
        return 2
    settings = get_settings().model_copy(
        update={
            "DRY_RUN": True,
            "APPLY_EXCEL": True,
            "APPLY_ARCHIVE": True,
            "OP5_RECONCILIATION_MODE": "archive_only_local",
        }
    )
    result = run_op5_archive_plan(settings, source_plan_path=plan_path)
    print_operation_summary("pipeline", result)
    return exit_code_for_status(result.status or OperationStatus.FALHOU)


def _run_op5_audit_global() -> int:
    settings = get_settings().model_copy(
        update={
            "DRY_RUN": True,
            "APPLY_EXCEL": False,
            "APPLY_ARCHIVE": False,
            "OP5_RECONCILIATION_MODE": "audit_global",
        }
    )
    try:
        payload = run_op5_audit_global(settings)
    except OperationalBlockError as exc:
        print("Status: BLOQUEADO")
        print(exc.user_message)
        return 2
    except Exception:
        print("Status: FALHOU")
        print("Auditoria global OP5 nao pode ser concluida com seguranca.")
        return 1
    status = payload.get("status", OperationStatus.FALHOU.value)
    print(f"Status: {status}")
    print(str(payload.get("operation_message") or "Auditoria global OP5 concluida."))
    return exit_code_for_status(str(status))


def _run_op5_retention_audit() -> int:
    settings = get_settings()
    payload = audit_download_retention(
        downloads_root=settings.downloads_dir_path,
        master_index_path=settings.op5_completed_index_path,
        workbook_path=settings.planilha_path,
        logs_dir=settings.logs_dir_path,
    )
    print("Status: SUCESSO")
    print("Auditoria de retenção OP5 concluída em modo somente leitura.")
    print(f"- PDFs analisados: {payload['total_pdfs_scanned']}")
    print(f"- Candidatos para exclusão: {payload['total_delete_candidates']}")
    print(f"- Bloqueados/preservados: {payload['total_blocked']}")
    print(f"- Plano: {payload['plan_path']}")
    return 0


def _run_op5_retention_apply(plan_path: Path | None) -> int:
    if plan_path is None:
        print("Status: BLOQUEADO")
        print("op5-retention-apply exige --plan <arquivo>.")
        return 2
    settings = get_settings()
    payload = apply_download_retention_plan(
        plan_path,
        downloads_root=settings.downloads_dir_path,
        master_index_path=settings.op5_completed_index_path,
        workbook_path=settings.planilha_path,
        logs_dir=settings.logs_dir_path,
    )
    print(f"Status: {payload['status']}")
    print(f"- PDFs excluídos: {payload.get('deleted_count', 0)}")
    print(f"- Bloqueados/preservados: {payload.get('blocked_count', 0)}")
    if payload.get("error"):
        print(str(payload["error"]))
    return 0 if payload.get("success") else 2


def _run_workbook_format_audit() -> int:
    settings = get_settings()
    payload = audit_workbook_format(settings.planilha_path, settings.logs_dir_path)
    print("Status: SUCESSO")
    print("Auditoria de formatação da planilha concluída em modo somente leitura.")
    print(f"- Plano: {payload['plan_path']}")
    return 0


def _run_workbook_format_apply(plan_path: Path | None) -> int:
    if plan_path is None:
        print("Status: BLOQUEADO")
        print("workbook-format-apply exige --plan <arquivo>.")
        return 2
    settings = get_settings()
    payload = apply_workbook_format_plan(
        plan_path,
        workbook_path=settings.planilha_path,
        logs_dir=settings.logs_dir_path,
    )
    print(f"Status: {payload['status']}")
    if payload.get("error"):
        print(str(payload["error"]))
    return 0 if payload.get("success") else 2


def _run_logs_audit() -> int:
    report = audit_log_files(get_settings().logs_dir_path)
    print("Status: SUCESSO")
    print("Auditoria de privacidade dos logs concluída em modo somente leitura.")
    print(f"- Arquivos analisados: {report.files_analyzed}")
    print(f"- Tamanho total: {report.total_bytes} bytes")
    print(f"- Caminhos absolutos detectados: {report.absolute_paths_detected}")
    print(f"- Padrões de e-mail: {report.email_patterns}")
    print(f"- Padrões de CPF: {report.cpf_patterns}")
    print(f"- Padrões de CNPJ: {report.cnpj_patterns}")
    print(f"- Tokens/cookies potenciais: {report.tokens_or_cookies}")
    print(f"- Mensagens sem estrutura: {report.unstructured_messages}")
    return 0


def _run_logs_sanitize(path: Path | None, confirmation: str | None) -> int:
    if path is None or confirmation != "SANITIZAR LOGS":
        print("Status: BLOQUEADO")
        print("logs-sanitize exige --input e --confirmation 'SANITIZAR LOGS'.")
        return 2
    try:
        report = sanitize_log_with_backup(path, confirmation=confirmation)
    except (OSError, ValueError):
        print("Status: FALHOU")
        print("A sanitização dos logs não pôde ser concluída com segurança.")
        return 1
    print("Status: SUCESSO")
    print(f"- Entradas sanitizadas: {report.entries_sanitized}")
    print(f"- Backup criado: {report.backup_created}")
    print(f"- Entradas sensíveis remanescentes: {report.remaining_sensitive_entries}")
    return 0


def _run_backfill_audit() -> int:
    settings = get_settings()
    availability = validate_workbook_availability(
        settings.planilha_path,
        require_writable=False,
        stage="auditoria histórica",
    )
    if not availability.ok:
        print("Status: BLOQUEADO")
        print(availability.user_message)
        return 2
    try:
        audit = audit_historical_workbook(
            settings.planilha_path,
            downloads_root=settings.downloads_dir_path,
            state_path=settings.pipeline_state_path,
        )
        plan = build_backfill_plan(audit)
        audit_json_path, audit_markdown_path, plan_path = backfill_artifact_paths(
            settings.logs_dir_path, plan["created_at"]
        )
        write_audit_reports(
            settings.logs_dir_path,
            audit,
            plan=plan,
            json_path=audit_json_path,
            markdown_path=audit_markdown_path,
        )
        write_backfill_plan(plan_path, plan)
    except (HistoricalAuditConsistencyError, OSError, ValueError) as exc:
        print("Status: FALHOU")
        print(f"A auditoria histórica não pôde ser concluída: {type(exc).__name__}.")
        return 1
    summary = audit.summary
    print("Status: SUCESSO")
    print("Auditoria histórica concluída em modo somente leitura.")
    print(f"- Abas analisadas: {summary.sheets_analyzed}")
    print(f"- Linhas analisadas: {summary.rows_analyzed}")
    print(f"- Alterações propostas: {summary.total_updates}")
    print(f"- Pendências técnicas: {summary.pending_review}")
    print(f"- PDFs não encontrados: {summary.pdf_not_found}")
    quality = plan["quality_summary"]
    print(f"- Regressões semânticas bloqueadas: {quality['semantic_regressions_blocked']}")
    print(f"- Mudanças cosméticas evitadas: {quality['cosmetic_changes_avoided']}")
    print(f"- Quality gate do plano: {plan['plan_status']}")
    print("Relatórios e plano gravados no diretório de logs configurado.")
    return 0


def _run_backfill_apply(plan_path: Path | None) -> int:
    if plan_path is None:
        print("Status: ABORTED_PRE_FLIGHT")
        print("backfill-apply exige --plan <arquivo>.")
        return 2
    try:
        payload = load_backfill_plan(plan_path)
    except BackfillPlanError as exc:
        print("Status: ABORTED_PRE_FLIGHT")
        print(str(exc))
        return 2
    settings = get_settings()
    try:
        preparation = prepare_backfill_application(
            settings.planilha_path,
            payload,
            settings=settings,
        )
    except BackfillApplyError as exc:
        if exc.result is not None:
            write_apply_reports(settings.logs_dir_path, exc.result)
        print("Status: ABORTED_PRE_FLIGHT")
        print(str(exc))
        return 2

    print(
        "Plano: "
        f"V{payload['plan_version']} / "
        f"V{payload['technical_processing_format_version']} / "
        f"{str(payload['equipment_rules_version']).rsplit('-', 1)[-1]}"
    )
    print(f"Hash: {str(payload['plan_hash'])[:8]}...")
    print(f"Execution ID: {str(payload['execution_id'])[:8]}...")
    print("Planilha validada pelo SHA-256: SIM")
    print(f"Atualizações previstas: {payload['total_updates']}")
    for sheet, count in preparation.updates_by_sheet.items():
        print(f"Aba {sheet}: {count}")
    print(f"Pendências excluídas: {payload['quality_summary']['pending_technical_review']}")
    print(f"PDFs ausentes excluídos: {payload['quality_summary']['pdf_not_found']}")
    print("Colunas que serão alteradas: Placa e Inversor")
    print("Backup será criado: SIM")
    print("Rollback disponível: SIM")
    confirmation = input(
        f"Digite {preparation.required_confirmation} para confirmar: "
    ).strip()
    try:
        result = execute_backfill_application(preparation, confirmation)
        write_apply_reports(settings.logs_dir_path, result)
    except BackfillApplyError as exc:
        error_result = exc.result
        if error_result is not None:
            write_apply_reports(settings.logs_dir_path, error_result)
            print(f"Status: {error_result.status.value}")
        else:
            print("Status: ABORTED_PRE_FLIGHT")
        print(str(exc))
        if error_result is not None and error_result.rollback_executed:
            print(f"Rollback: {error_result.rollback_status}")
        return (
            1
            if error_result is not None
            and error_result.status.value.startswith("FAILED_")
            else 2
        )
    print(f"Status: {result.status.value}")
    print(f"- Alterações planejadas: {result.planned_updates}")
    print(f"- Alterações aplicadas: {result.applied_updates}")
    print(f"- Conflitos: {result.conflicts}")
    return 0


def _confirmed_real_processing(controller: ApplicationController):
    requested_limit = int(
        getattr(controller.settings, "MAX_COMPLETED_TO_PROCESS", 0) or 0
    )
    try:
        required_confirmation = build_option4_strong_confirmation(requested_limit)
        raw_selection = input(
            "Informe exatamente os PDFs autorizados no formato "
            "arquivo_relativo.pdf|protocolo, separados por ponto e vírgula: "
        ).strip()
        selections = _parse_offline_selections(raw_selection)
        batch = prepare_offline_batch(
            controller.settings.downloads_dir_path,
            requested_limit=requested_limit,
            selections=selections,
        )
    except OperationalBlockError as exc:
        return _cancelled_operation_result(exc.code)
    print(f"Modo: REAL | Operação: opção 4 | Limite: {requested_limit}")
    print(f"PDFs congelados: {len(batch.items)} | Digest: {batch.digest[:12]}...")
    confirmation = input(
        f"Digite {required_confirmation} para confirmar alterações reais: "
    ).strip()
    try:
        authorization = authorize_offline_batch(batch, confirmation)
    except OperationalBlockError as exc:
        return _cancelled_operation_result(exc.code)
    return controller.process_downloads(
        dry_run=False,
        authorization=authorization,
    )


def _parse_offline_selections(value: str) -> tuple[tuple[str, str], ...]:
    selections: list[tuple[str, str]] = []
    for raw_item in value.split(";"):
        item = raw_item.strip()
        if not item:
            continue
        raw_path, separator, raw_protocol = item.partition("|")
        if not separator or not raw_path.strip() or not raw_protocol.strip():
            raise OperationalBlockError(
                code="FROZEN_BATCH_SCOPE_VIOLATION",
                user_message="Seleção offline explícita inválida.",
                stage="lote congelado",
                technical_cause="FROZEN_BATCH_SCOPE_VIOLATION",
            )
        selections.append((raw_path.strip(), raw_protocol.strip()))
    if not selections:
        raise OperationalBlockError(
            code="FROZEN_BATCH_SCOPE_VIOLATION",
            user_message="Seleção offline explícita obrigatória.",
            stage="lote congelado",
            technical_cause="FROZEN_BATCH_SCOPE_VIOLATION",
        )
    return tuple(selections)


def _confirmed_pipeline(controller: ApplicationController):
    try:
        authorization = validate_requested_batch_limit(
            getattr(controller.settings, "MAX_COMPLETED_TO_PROCESS", 5),
            batch_authorization_policy_from_settings(controller.settings),
        )
    except BatchAuthorizationError as exc:
        return _cancelled_operation_result(exc.code, require_cdp=True)
    required_confirmation = build_option5_strong_confirmation(
        authorization.requested_batch_limit
    )
    mode = "simulacao com acesso ao Portal/CDP" if controller.settings.DRY_RUN else "execucao real"
    print(f"Confirme {mode}.")
    print(f"Digite {required_confirmation}")
    confirmation = input("Confirmar: ").strip()
    try:
        validate_option5_strong_confirmation(confirmation, authorization)
    except StrongConfirmationError as exc:
        return _cancelled_operation_result(
            exc.code,
            require_cdp=True,
            confirmation_required=required_confirmation,
        )
    return controller.run_pipeline(confirmation=confirmation)


def _cancelled_operation_result(code: str, **details: object) -> OperationResult:
    payload: dict[str, Any] = {
        "code": code,
        "stage": "confirmação operacional",
    }
    payload.update(details)
    return OperationResult(
        False,
        "Operação cancelada ou confirmação forte inválida.",
        payload,
        status=OperationStatus.BLOQUEADO,
    )


def _confirmed_completion_sync(controller: ApplicationController):
    real_completion_write = (
        not controller.settings.DRY_RUN
        and controller.settings.APPLY_COMPLETION_STATUS
    )
    if real_completion_write:
        confirmation = input(
            f"Digite {COMPLETION_SYNC_STRONG_CONFIRMATION} para confirmar: "
        ).strip()
        if confirmation != COMPLETION_SYNC_STRONG_CONFIRMATION:
            return OperationResult(
                False,
                "Confirmação forte da sincronização de conclusão não recebida.",
                {
                    "code": "STRONG_CONFIRMATION_REQUIRED",
                    "operation_message": (
                        "Confirmação forte da sincronização de conclusão não recebida."
                    ),
                    "confirmation_required": COMPLETION_SYNC_STRONG_CONFIRMATION,
                },
                status=OperationStatus.BLOQUEADO,
            )
    return controller.sync_completion_status()


def _open_desktop_visual() -> None:
    try:
        from desktop_app import main as desktop_main

        desktop_main([])
    except RuntimeError as exc:
        print(f"Não foi possível abrir a interface desktop: {exc}")
