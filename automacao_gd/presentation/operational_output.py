from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from automacao_gd.application.contracts import (
    OperationError,
    OperationResult,
    OperationStatus,
    ProgressEvent,
)
from automacao_gd.infrastructure.config import PROJECT_ROOT


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_LABELED_PHONE_RE = re.compile(
    r"(?i)(\b(?:telefone|celular|whatsapp|phone|mobile)\s*[:=]\s*)"
    r"(?:\+?55\s*)?\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}"
)
_LABELED_PROTOCOL_RE = re.compile(
    r"(?i)(\b(?:protocol|protocolo|protocol_number|numero_protocolo|"
    r"protocolo_neoenergia)\s*[:=]\s*)(\d{6,})"
)
_PROTOCOL_FIELDS = {
    "protocol", "protocolo", "protocol_number", "numero_protocolo",
    "protocolo_neoenergia",
}
_PHONE_FIELDS = {"telefone", "phone", "celular", "whatsapp", "mobile"}
_MAX_STRING_LENGTH = 160
_MAX_LIST_ITEMS = 5


def sanitize_for_console(data: Any, *, field_name: str | None = None) -> Any:
    if isinstance(data, dict):
        return {
            key: sanitize_for_console(value, field_name=str(key))
            for key, value in data.items()
            if str(key).casefold() != "raw_text"
        }
    if isinstance(data, (list, tuple, set)):
        return [
            sanitize_for_console(value, field_name=field_name)
            for value in list(data)[:_MAX_LIST_ITEMS]
        ]
    if isinstance(data, Path):
        return _display_path(data)
    if isinstance(data, str):
        return _sanitize_text(data, field_name=field_name)
    return data


def format_operation_summary(operation_name: str, result: OperationResult) -> str:
    payload = result.payload or {}
    builders = {
        "preflight": _preflight_summary,
        "inspect_portal": _inspection_summary,
        "process_dry_run": _processing_summary,
        "process_real": _processing_summary,
        "pipeline": _pipeline_summary,
    }
    builder = builders.get(operation_name, _generic_summary)
    body = builder(result, payload)
    return f"Status: {result.status.value}\n\n{body}"


def print_operation_summary(operation_name: str, result: OperationResult) -> None:
    print(format_operation_summary(operation_name, result))


def format_progress_event(event: ProgressEvent) -> str:
    parts = [f"{event.overall_percent}%"]
    if event.protocol:
        parts.append(f"Protocolo: {sanitize_for_console(event.protocol, field_name='protocol')}")
    parts.append(f"Etapa: {sanitize_for_console(event.stage)}")
    if event.message:
        parts.append(str(sanitize_for_console(event.message)))
    return " | ".join(parts)


def format_operation_error(error: OperationError | dict[str, Any]) -> str:
    payload = error.to_dict() if isinstance(error, OperationError) else dict(error)
    protocol = sanitize_for_console(
        str(payload.get("protocol") or "-"),
        field_name="protocol",
    )
    lines = [
        f"Protocolo: {protocol}",
        f"Etapa: {sanitize_for_console(str(payload.get('stage') or '-'))}",
        f"Problema: {sanitize_for_console(str(payload.get('user_message') or '-'))}",
    ]
    action_taken = payload.get("action_taken")
    if action_taken:
        lines.append(f"Consequência: {sanitize_for_console(str(action_taken))}")
    suggested_action = payload.get("suggested_action")
    if suggested_action:
        lines.append(f"Ação recomendada: {sanitize_for_console(str(suggested_action))}")
    code = payload.get("code")
    if code:
        lines.append(f"Código: {sanitize_for_console(str(code))}")
    return "\n".join(lines)


def format_cleanup_summary(report: Any) -> str:
    payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    dry_run = bool(payload.get("dry_run", True))
    candidate_bytes = int(payload.get("total_bytes_candidate", 0) or 0)
    deleted_bytes = int(payload.get("total_bytes_deleted", 0) or 0)
    lines = [
        "Limpeza de temporários",
        "",
        f"Modo: {'Simulação' if dry_run else 'Aplicado'}",
        f"Arquivos analisados: {payload.get('scanned_count', 0)}",
        f"Candidatos temporários: {len(payload.get('would_remove') or [])}",
        f"Arquivos protegidos ignorados: {payload.get('skipped_count', 0)}",
    ]
    if dry_run:
        lines.append(
            f"Arquivos que seriam removidos: {len(payload.get('would_remove') or [])}"
        )
        lines.append(f"Espaço candidato: {_format_bytes(candidate_bytes)}")
    else:
        lines.append(f"Arquivos removidos: {payload.get('deleted_count', 0)}")
        lines.append(f"Espaço liberado: {_format_bytes(deleted_bytes)}")
    if payload.get("errors"):
        lines.extend(["", "Erros:"])
        lines.extend(f"- {sanitize_for_console(str(error))}" for error in payload["errors"])
    return "\n".join(lines)


def _preflight_summary(result: OperationResult, payload: dict) -> str:
    checks = {
        item.get("code"): item
        for item in payload.get("checks", [])
        if isinstance(item, dict)
    }
    errors = payload.get("blocking_errors") or []
    lines = [
        "Pré-voo concluído.",
        f"- Status: {'OK' if result.success else 'FALHA'}",
        f"- Planilha: {_check_label(checks.get('workbook'))}",
        f"- Pasta de clientes: {_check_label(checks.get('clientes_root'))}",
        f"- CDP: {_check_label(checks.get('cdp_endpoint'), 'acessível', 'não acessível')}",
        f"- Erros críticos: {len(errors)}",
    ]
    return _finish_summary(lines, payload)


def _inspection_summary(result: OperationResult, payload: dict) -> str:
    records = payload.get("records") or []
    completed = sum(
        1
        for record in records
        if isinstance(record, dict) and _is_completed(record.get("status"))
    )
    active_page = payload.get("page_number") or payload.get("active_page") or "-"
    lines = [
        "Inspeção do portal concluída." if result.success else "Falha na inspeção do portal.",
        f"- CDP: {'conectado' if result.success else 'não conectado'}",
        f"- Página ativa: {active_page}",
        f"- Linhas encontradas: {len(records)}",
        f"- Solicitações concluídas: {completed}",
        f"- Erros: {0 if result.success else 1}",
    ]
    return _finish_summary(lines, payload, result=result)


def _processing_summary(result: OperationResult, payload: dict) -> str:
    dry_run = bool(payload.get("dry_run"))
    title = (
        "Processamento em simulação concluído."
        if dry_run
        else "Processamento real concluído."
    )
    if not result.success:
        title = "Falha no processamento de PDFs."
    lines = [
        title,
        f"- PDFs encontrados: {payload.get('total_pdfs', 0)}",
        f"- Sucessos: {payload.get('total_success', 0)}",
        f"- Erros: {payload.get('total_errors', 0)}",
    ]
    if dry_run:
        lines.extend(
            [
                "- Atualizações de planilha: simulação",
                "- Arquivamentos: simulação",
            ]
        )
    else:
        lines.extend(
            [
                f"- Planilha atualizada: {payload.get('total_excel_updated', 0)}",
                f"- PDFs arquivados: {payload.get('total_archived', 0)}",
                f"- Pendentes de conferência: {payload.get('total_pending_review', 0)}",
            ]
        )
    return _finish_summary(lines, payload, result=result)


def _pipeline_summary(result: OperationResult, payload: dict) -> str:
    dry_run = bool(payload.get("dry_run", True))
    processing = payload.get("processing") if isinstance(payload.get("processing"), dict) else {}
    if result.status is OperationStatus.BLOQUEADO:
        lines = [
            "Execução não iniciada.",
            f"- Etapa: {sanitize_for_console(str(payload.get('stage') or payload.get('block_stage') or 'pré-voo'))}",
            f"- Motivo: {sanitize_for_console(result.message)}",
            f"- Páginas lidas: {payload.get('total_pages_read', 0)}",
            f"- PDFs baixados: {payload.get('total_downloaded', 0)}",
            f"- Planilha atualizada: {payload.get('total_excel_updated', 0)}",
            "",
            "Ação recomendada:",
            _recommended_action_for_block(str(payload.get("code") or "")),
        ]
        return _finish_summary(lines, payload)
    if result.status is OperationStatus.PARCIAL:
        lines = [
            "O portal foi processado, mas a gravação da planilha foi bloqueada."
            if processing.get("blocked_real_run")
            else "O pipeline terminou com resultados parciais.",
            f"- PDFs baixados: {payload.get('total_downloaded', 0)}",
            f"- PDFs processados: {payload.get('total_processed_success', 0)}",
            f"- Planilha atualizada: {payload.get('total_excel_updated', 0)}",
        ]
        if processing.get("blocked_real_run"):
            lines.extend(["", "Os PDFs permanecem disponíveis para retomada."])
        return _finish_summary(lines, payload, result=result)
    if result.status is OperationStatus.FALHOU:
        title = "Falha no pipeline CDP."
    elif result.message == "Nenhuma atualização necessária.":
        title = result.message
    else:
        title = "Pipeline CDP concluído."
    lines = [
        title,
        "",
        "Resumo:",
        f"- Modo: {'Simulação' if dry_run else 'Produção'}",
        f"- Páginas lidas: {payload.get('total_pages_read', 0)}",
        f"- Linhas lidas: {payload.get('total_rows', 0)}",
        f"- Solicitações concluídas: {payload.get('total_completed', 0)}",
        f"- Protocolos elegíveis: {payload.get('total_eligible_after_skip', 0)}",
        f"- Protocolos selecionados: {payload.get('total_selected', 0)}",
        f"- PDFs baixados: {payload.get('total_downloaded', 0)}",
        f"- PDFs reutilizados: {payload.get('total_existing_reused', 0)}",
        f"- PDFs processados com sucesso: {payload.get('total_processed_success', 0)}",
        f"- Erros: {payload.get('total_errors', 0)}",
        f"- Planilha atualizada: {payload.get('total_excel_updated', 0)}",
        f"- PDFs arquivados: {payload.get('total_archived', 0)}",
        f"- Pendentes de conferência: {payload.get('total_pending_review', 0)}",
    ]
    protocol_lines = _pipeline_protocol_lines(payload, dry_run=dry_run)
    if protocol_lines:
        lines.extend(["", "Protocolos:", *protocol_lines])
    if dry_run:
        lines.extend(
            [
                "",
                "Simulação: nenhuma alteração real foi feita em planilha ou "
                "pastas de clientes.",
            ]
        )
    error_lines = _pipeline_error_lines(payload)
    if error_lines:
        lines.extend(["", "Erros operacionais:", *error_lines])
    attention = _attention_lines(result, payload)
    if attention:
        lines.extend(["", "Atenção:", *attention, "- Consulte o relatório detalhado."])
    return _finish_summary(lines, payload)


def _recommended_action_for_block(code: str) -> str:
    if code == "WORKBOOK_LOCKED":
        return "Feche a planilha no Excel e execute novamente."
    if code == "NETWORK_DRIVE_UNAVAILABLE":
        return "Restabeleça a unidade de rede e execute novamente."
    if code == "ENVIRONMENT_NOT_PRODUCTION":
        return "Configure APP_ENV=production somente no ambiente real autorizado."
    if code == "WORKBOOK_NOT_FOUND":
        return "Confirme o caminho da planilha configurada e execute novamente."
    return "Corrija a condição indicada e execute novamente."


def _pipeline_protocol_lines(payload: dict, *, dry_run: bool) -> list[str]:
    protocol_results = payload.get("protocol_results")
    if not isinstance(protocol_results, list):
        return []

    processed = [
        row
        for row in protocol_results
        if isinstance(row, dict)
        and (
            row.get("sent_to_processing")
            or row.get("processing_result") in {"success", "error"}
        )
    ]
    return [_format_protocol_line(row, dry_run=dry_run) for row in processed[:5]]


def _format_protocol_line(row: dict, *, dry_run: bool) -> str:
    protocol = sanitize_for_console(
        str(row.get("protocol") or "-"),
        field_name="protocol",
    )
    client = sanitize_for_console(str(row.get("client_name") or "Cliente não informado"))
    pdf_status = {
        "downloaded": "PDF baixado",
        "existing_pdf_after_skip": "PDF reutilizado",
    }.get(row.get("download_status"), "PDF não disponível")

    excel_action = sanitize_for_console(
        str(row.get("excel_action") or row.get("excel_state") or "não aplicável")
    )
    excel_label = f"simulação/{excel_action}" if dry_run else excel_action

    destination = row.get("archive_destination_folder")
    archive_state = sanitize_for_console(str(row.get("archive_state") or "não arquivado"))
    if dry_run:
        archive_label = "simulação"
        if destination:
            archive_label += f"/{sanitize_for_console(_display_path(Path(destination)))}"
    elif destination:
        archive_label = sanitize_for_console(_display_path(Path(destination)))
    else:
        archive_label = archive_state

    return (
        f"- {protocol} | {client} | {pdf_status} | "
        f"Excel: {excel_label} | Arquivo: {archive_label}"
    )


def _pipeline_error_lines(payload: dict) -> list[str]:
    protocol_results = payload.get("protocol_results")
    if not isinstance(protocol_results, list):
        return []

    lines: list[str] = []
    for row in protocol_results:
        if not isinstance(row, dict):
            continue
        error = row.get("error") or row.get("processing_error") or row.get("download_error")
        if not error:
            continue
        protocol = sanitize_for_console(
            str(row.get("protocol") or "-"),
            field_name="protocol",
        )
        client = sanitize_for_console(str(row.get("client_name") or "Cliente nÃ£o informado"))
        problem = sanitize_for_console(str(error))
        lines.append(f"- {protocol} | {client}")
        lines.append(f"  Problema: {problem}")
        lines.append(f"  AÃ§Ã£o recomendada: {_suggested_action_for_pipeline_error(str(error))}")
        if len(lines) >= 15:
            lines.append("- Demais erros: consulte o relatÃ³rio detalhado.")
            break
    return lines


def _suggested_action_for_pipeline_error(error: str) -> str:
    normalized = _normalize_for_matching(error)
    if "planilha" in normalized or "xlsx" in normalized or "excel" in normalized:
        return (
            "Feche a planilha, verifique permissÃ£o de escrita e sincronizaÃ§Ã£o/rede, "
            "depois execute novamente o protocolo pendente."
        )
    if "download" in normalized or "orcamento de conexao" in normalized:
        return (
            "Confira se o Portal GD estÃ¡ logado, se o botÃ£o de orÃ§amento abre o PDF "
            "e execute novamente em lote menor."
        )
    if "pasta" in normalized or "cliente" in normalized or "arquiv" in normalized:
        return "Confira a pasta do cliente, resolva ambiguidades de nome e execute novamente."
    if "pdf" in normalized:
        return (
            "Abra o PDF baixado, confirme se Ã© um orÃ§amento de conexÃ£o vÃ¡lido e "
            "reprocesse o arquivo."
        )
    return "Consulte o relatÃ³rio detalhado, corrija a causa indicada e execute novamente."


def _generic_summary(result: OperationResult, payload: dict) -> str:
    status = "concluída" if result.success else "falhou"
    return _finish_summary([f"Operação {status}."], payload, result=result)


def _finish_summary(
    lines: list[str],
    payload: dict,
    *,
    result: OperationResult | None = None,
) -> str:
    if result is not None and not result.success:
        safe_message = sanitize_for_console(result.message)
        if safe_message:
            lines.extend(["", "Atenção:", f"- {safe_message}"])
    reports = _report_paths(payload)
    if reports:
        lines.extend(["", "Relatórios gerados:"])
        lines.extend(f"- {path}" for path in reports)
    return "\n".join(lines)


def _report_paths(payload: dict) -> list[str]:
    paths: list[str] = []

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
        elif (
            isinstance(value, (str, Path))
            and (key.endswith("_report_path") or key == "snapshot_path")
            and value
        ):
            display = _display_path(Path(value))
            if display not in paths:
                paths.append(display)

    visit(payload)
    return paths


def _attention_lines(result: OperationResult, payload: dict) -> list[str]:
    download = payload.get("download") if isinstance(payload.get("download"), dict) else {}
    candidates = {
        "run_error": payload.get("run_error") or download.get("run_error"),
        "abort_reason": payload.get("abort_reason") or download.get("abort_reason"),
    }
    lines = [
        f"- {key}: {sanitize_for_console(value)}"
        for key, value in candidates.items()
        if value
    ]
    if not result.success and not lines:
        lines.append(f"- erro: {sanitize_for_console(result.message)}")
    return lines


def _check_label(
    check: dict | None,
    success_label: str = "encontrada",
    failure_label: str = "não encontrada",
) -> str:
    if not check:
        return "não verificado"
    return success_label if check.get("ok") else failure_label


def _is_completed(status: Any) -> bool:
    normalized = unicodedata.normalize("NFD", str(status or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return "CONCLUIDA" in normalized.upper()


def _sanitize_text(value: str, *, field_name: str | None = None) -> str:
    normalized_field = _normalize_field_name(field_name)
    if normalized_field in _PROTOCOL_FIELDS:
        return _truncate_console_text(value)
    if normalized_field in _PHONE_FIELDS:
        return "[TELEFONE REMOVIDO]"

    sanitized, protected_protocols = _protect_labeled_protocols(value)
    sanitized = _EMAIL_RE.sub("[E-MAIL REMOVIDO]", sanitized)
    sanitized = _LABELED_PHONE_RE.sub(r"\1[TELEFONE REMOVIDO]", sanitized)
    sanitized = _CNPJ_RE.sub("[CPF/CNPJ REMOVIDO]", sanitized)
    sanitized = _CPF_RE.sub("[CPF/CNPJ REMOVIDO]", sanitized)
    for placeholder, protocol in protected_protocols.items():
        sanitized = sanitized.replace(placeholder, protocol)
    return _truncate_console_text(sanitized)


def _normalize_field_name(field_name: str | None) -> str:
    normalized = unicodedata.normalize("NFD", str(field_name or ""))
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold()


def _normalize_for_matching(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value or ""))
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold()


def _protect_labeled_protocols(value: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        placeholder = f"__PROTOCOLO_PRESERVADO_{len(protected)}__"
        protected[placeholder] = match.group(2)
        return match.group(1) + placeholder

    return _LABELED_PROTOCOL_RE.sub(replace, value), protected


def _truncate_console_text(value: str) -> str:
    if len(value) > _MAX_STRING_LENGTH:
        return value[: _MAX_STRING_LENGTH - 3].rstrip() + "..."
    return value


def _display_path(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
        return str(resolved.relative_to(PROJECT_ROOT.resolve(strict=False)))
    except (OSError, ValueError):
        return path.name or str(path)


def _format_bytes(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1024
    return f"{size_bytes} B"
