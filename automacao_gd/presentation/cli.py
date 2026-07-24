from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

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
from automacao_gd.application.contracts import OperationStatus
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.excel.availability import validate_workbook_availability
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.infrastructure.log_privacy import (
    audit_log_files,
    sanitize_log_with_backup,
)
from automacao_gd.presentation.controller import ApplicationController
from automacao_gd.presentation.operational_output import print_operation_summary


_EXIT_CODES = {
    OperationStatus.SUCESSO: 0,
    OperationStatus.FALHOU: 1,
    OperationStatus.BLOQUEADO: 2,
    OperationStatus.PARCIAL: 3,
}


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
        return _run_backfill_apply(args.plan)

    ensure_directories()
    setup_logger(verbose=args.verbose)
    controller = ApplicationController()
    last_exit_code = 0

    actions = {
        "1": ("preflight", "Executando pré-voo...", lambda: controller.preflight()),
        "2": ("inspect_portal", "Inspecionando Portal GD...", lambda: controller.inspect_portal()),
        "3": ("process_dry_run", "Executando processamento em simulação...", lambda: controller.process_downloads(dry_run=True)),
        "4": ("process_real", "Executando processamento real...", lambda: _confirmed_real_processing(controller)),
        "5": ("pipeline", "Executando pipeline CDP...", lambda: _confirmed_pipeline(controller)),
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
        choices=("backfill-audit", "backfill-apply", "logs-audit", "logs-sanitize"),
        help="executa auditoria histórica ou aplica um plano previamente aprovado",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        help="plano JSON versionado exigido por backfill-apply",
    )
    parser.add_argument("--input", type=Path, help="arquivo para logs-sanitize")
    parser.add_argument(
        "--confirmation",
        help="confirmação forte exigida por logs-sanitize",
    )
    return parser.parse_args(argv)


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
    if input("Digite SIM para confirmar alterações reais: ").strip().upper() != "SIM":
        return controller.preflight()
    return controller.process_downloads(dry_run=False)


def _confirmed_pipeline(controller: ApplicationController):
    if not controller.settings.DRY_RUN:
        if input("DRY_RUN=false. Digite SIM para confirmar: ").strip().upper() != "SIM":
            return controller.preflight(require_cdp=True)
    return controller.run_pipeline()


def _open_desktop_visual() -> None:
    try:
        from desktop_app import main as desktop_main

        desktop_main([])
    except RuntimeError as exc:
        print(f"Não foi possível abrir a interface desktop: {exc}")
