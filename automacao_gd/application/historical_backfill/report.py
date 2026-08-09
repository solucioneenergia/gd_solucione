from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automacao_gd.application.historical_backfill.comparison import (
    contains_sensitive_or_path_text,
)
from automacao_gd.application.historical_backfill.plan import (
    build_backfill_plan,
    validate_backfill_plan,
)
from automacao_gd.domain.backfill_models import (
    BackfillApplyResult,
    HistoricalAuditResult,
)
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text


AUDIT_JSON_NAME = "historical_equipment_backfill_audit_rules5.json"
AUDIT_MARKDOWN_NAME = "historical_equipment_backfill_audit_rules5.md"
PLAN_JSON_NAME = "historical_equipment_backfill_plan_rules5.json"
APPLY_JSON_NAME = "historical_equipment_backfill_apply_rules5.json"
APPLY_MARKDOWN_NAME = "historical_equipment_backfill_apply_rules5.md"

_CLASSIFICATION_LABELS = {
    "MODULE_EMPTY": "Placa vazia",
    "INVERTER_EMPTY": "Inversor vazio",
    "MODULE_IN_INVERTER": "Módulo dentro de Inversor",
    "INVERTER_IN_MODULE": "Inversor dentro de Placa",
    "GOKIN_IN_INVERTER": "GOKIN em Inversor",
    "SOLPLANET_ALIAS_NORMALIZED": "SOLPLANET/AISWEI normalizado",
    "DUPLICATE_EQUIPMENT": "Duplicidade de equipamento",
    "QUANTITY_DIVERGENT": "Quantidade divergente",
    "MODEL_DIVERGENT": "Modelo divergente",
    "MANUFACTURER_DIVERGENT": "Fabricante divergente",
    "LEGACY_FORMAT": "Formatação antiga",
    "DUPLICATED_MANUFACTURER_IN_MODEL": "Fabricante duplicado bloqueado",
    "GENERIC_LABEL_IN_MODEL": "Rótulo genérico bloqueado",
    "POWER_UNIT_LOST": "Perda de unidade bloqueada",
    "TECHNICAL_ATTRIBUTE_LOST": "Perda de atributo bloqueada",
    "QUANTITY_LOST": "Perda de quantidade bloqueada",
    "MODEL_TRUNCATED": "Modelo truncado bloqueado",
    "MANUFACTURER_LOST": "Perda de fabricante bloqueada",
    "EQUIPMENT_TYPE_LOST": "Perda de tipo bloqueada",
    "SEMANTIC_REGRESSION": "Regressão semântica bloqueada",
}


_SHARED_ARTIFACT_FIELDS = (
    "execution_id",
    "created_at",
    "plan_hash",
    "workbook_fingerprint",
    "plan_version",
    "equipment_format_version",
    "technical_processing_format_version",
    "equipment_rules_version",
)


def rules_artifact_suffix(equipment_rules_version: str | None) -> str:
    """Return the rules-N suffix encoded in an equipment rules version."""
    value = str(equipment_rules_version or "").strip()
    marker = "rules-"
    if marker not in value:
        return "rules-unknown"
    label = value.rsplit(marker, 1)[-1].strip()
    return f"rules{label}" if label.isdigit() else "rules-unknown"


def build_audit_public_payload(
    audit: HistoricalAuditResult,
    *,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    linked_plan = plan or build_backfill_plan(audit)
    validate_backfill_plan(linked_plan)
    if (
        linked_plan["created_at"] != audit.created_at
        or linked_plan["workbook_fingerprint"] != audit.workbook_fingerprint
    ):
        raise ValueError("Plano e auditoria pertencem a execuções diferentes.")
    summary = asdict(audit.summary)
    summary["classifications"] = {
        key: audit.summary.classifications.get(key, 0)
        for key in _CLASSIFICATION_LABELS
    }
    items = []
    for item in audit.items:
        unsafe = contains_sensitive_or_path_text(item.current_module_text) or (
            contains_sensitive_or_path_text(item.current_inverter_text)
        )
        items.append(
            {
                "sheet": item.workbook_sheet,
                "row": item.workbook_row,
                "protocol": item.protocol,
                "action": item.action.value,
                "current_module_text": (
                    "[REDACTED]" if unsafe else item.current_module_text
                ),
                "proposed_module_text": _public_text(item.proposed_module_text),
                "current_inverter_text": (
                    "[REDACTED]" if unsafe else item.current_inverter_text
                ),
                "proposed_inverter_text": _public_text(item.proposed_inverter_text),
                "reasons": [_public_text(value) for value in item.reasons],
                "warnings": [_public_text(value) for value in item.warnings],
                "technical_validation_status": _public_text(
                    item.technical_validation_status
                ),
                "semantic_validation_status": _public_text(
                    item.semantic_validation_status
                ),
                "semantic_validation_errors": [
                    _public_text(value) for value in item.semantic_validation_errors
                ],
                "semantic_validation_warnings": [
                    _public_text(value) for value in item.semantic_validation_warnings
                ],
                "pdf_status": _public_text(item.pdf_status),
            }
        )
    return {
        **{field: linked_plan[field] for field in _SHARED_ARTIFACT_FIELDS},
        "status": "AUDITED",
        "summary": summary,
        "items": items,
    }


def write_audit_reports(
    logs_dir: Path,
    audit: HistoricalAuditResult,
    *,
    plan: dict[str, Any],
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> tuple[Path, Path]:
    directory = Path(logs_dir)
    payload = build_audit_public_payload(audit, plan=plan)
    json_path = atomic_write_json(
        json_path or next_available_artifact_path(directory, AUDIT_JSON_NAME),
        payload,
        private=True,
    )
    markdown_path = atomic_write_text(
        markdown_path or next_available_artifact_path(directory, AUDIT_MARKDOWN_NAME),
        build_audit_markdown(payload),
        private=True,
    )
    return json_path, markdown_path


def next_available_artifact_path(directory: Path, name: str) -> Path:
    """Keep prior audit evidence instead of silently replacing it."""
    target = Path(directory) / name
    if not target.exists():
        return target
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = target.with_name(f"{target.stem}_{suffix}{target.suffix}")
    sequence = 1
    while candidate.exists():
        candidate = target.with_name(
            f"{target.stem}_{suffix}_{sequence}{target.suffix}"
        )
        sequence += 1
    return candidate


def backfill_artifact_paths(
    directory: Path, created_at: str
) -> tuple[Path, Path, Path]:
    timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    sequence = 0
    while True:
        suffix = f"_{timestamp}" + (f"_{sequence}" if sequence else "")
        paths = (
            _artifact_path(directory, AUDIT_JSON_NAME, suffix),
            _artifact_path(directory, AUDIT_MARKDOWN_NAME, suffix),
            _artifact_path(directory, PLAN_JSON_NAME, suffix),
        )
        if not any(path.exists() for path in paths):
            return paths
        sequence += 1


def _artifact_path(directory: Path, name: str, suffix: str) -> Path:
    template = Path(name)
    return Path(directory) / f"{template.stem}{suffix}{template.suffix}"


def build_audit_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Auditoria histórica de equipamentos",
        "",
        f"Status: {payload['status']}",
        "",
        f"- Execution ID: {payload['execution_id']}",
        f"- Criado em: {payload['created_at']}",
        f"- Hash do plano: {payload['plan_hash']}",
        f"- Fingerprint da planilha: {payload['workbook_fingerprint']}",
        f"- Versão do plano: {payload['plan_version']}",
        f"- Versão do formato Excel: {payload['equipment_format_version']}",
        f"- Versão técnica: {payload['technical_processing_format_version']}",
        f"- Versão das regras: {payload['equipment_rules_version']}",
        "",
        f"- Abas analisadas: {summary['sheets_analyzed']}",
        f"- Linhas analisadas: {summary['rows_analyzed']}",
        f"- Protocolos válidos: {summary['valid_protocols']}",
        f"- Sem alteração: {summary['no_change']}",
        f"- Alterações propostas: {summary['total_updates']}",
        f"- Pendências técnicas: {summary['pending_review']}",
        f"- PDFs não encontrados: {summary['pdf_not_found']}",
        f"- Protocolos ausentes: {summary['protocol_missing']}",
        f"- Protocolos duplicados: {summary['duplicate_protocols']}",
        f"- Conflitos: {summary['conflicts']}",
        f"- Erros inesperados: {summary['unexpected_errors']}",
        "",
        "## Por classificação",
        "",
        *(
            f"- {_CLASSIFICATION_LABELS[key]}: {count}"
            for key, count in summary["classifications"].items()
        ),
        "",
        "## Linhas auditadas",
        "",
        "| Aba | Linha | Protocolo | Ação | Placa atual | Placa proposta | Inversor atual | Inversor proposto | Motivos | Avisos | Status técnico | Status semântico |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload["items"]:
        fields = (
            item["sheet"],
            item["row"],
            item["protocol"],
            item["action"],
            item["current_module_text"],
            item["proposed_module_text"],
            item["current_inverter_text"],
            item["proposed_inverter_text"],
            ", ".join(item["reasons"]),
            ", ".join(item["warnings"]),
            item["technical_validation_status"],
            item["semantic_validation_status"],
        )
        lines.append("| " + " | ".join(_md(value) for value in fields) + " |")
    return "\n".join(lines) + "\n"


def _md(value: object) -> str:
    return str(value or "-").replace("|", r"\|")


def _public_text(value: object) -> str | None:
    if value is None:
        return None
    return "[REDACTED]" if contains_sensitive_or_path_text(value) else str(value)


def build_apply_public_payload(result: BackfillApplyResult) -> dict[str, Any]:
    failures = []
    if result.status.value != "APPLIED_SUCCESSFULLY":
        failures = [
            {
                "sheet": item.workbook_sheet,
                "row": item.workbook_row,
                "protocol": item.protocol,
                "error_codes": [
                    value
                    for value in item.reasons
                    if not contains_sensitive_or_path_text(value)
                ],
            }
            for item in result.items
        ]
    return {
        "execution_id": result.execution_id,
        "plan_hash": result.plan_hash,
        "plan_version": result.plan_version,
        "technical_processing_format_version": (
            result.technical_processing_format_version
        ),
        "equipment_rules_version": result.equipment_rules_version,
        "workbook_hash_before": result.original_hash,
        "backup_hash": result.backup_hash,
        "workbook_hash_after": result.final_hash,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "status": result.status.value,
        "updates_planned": result.planned_updates,
        "updates_applied": result.applied_updates,
        "updates_by_sheet": dict(sorted(result.updates_by_sheet.items())),
        "fingerprint_conflicts": result.fingerprint_conflicts,
        "unexpected_changes": result.unexpected_changes,
        "confirmation_received": result.confirmation_received,
        "rollback_executed": result.rollback_executed,
        "rollback_status": result.rollback_status,
        "idempotency_result": result.idempotency_result,
        "error_code": result.error_code,
        "backup_created": result.backup_path is not None,
        "failures": failures,
    }


def write_apply_reports(
    logs_dir: Path, result: BackfillApplyResult
) -> tuple[Path, Path]:
    directory = Path(logs_dir)
    payload = build_apply_public_payload(result)
    json_path, markdown_path = _apply_report_paths(
        directory,
        result.finished_at,
        result.equipment_rules_version,
    )
    json_path = atomic_write_json(json_path, payload, private=True)
    markdown_path = atomic_write_text(
        markdown_path,
        build_apply_markdown(payload),
        private=True,
    )
    return json_path, markdown_path


def _apply_report_paths(
    directory: Path,
    finished_at: str | None,
    equipment_rules_version: str | None,
) -> tuple[Path, Path]:
    moment = datetime.fromisoformat(
        (finished_at or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    timestamp = moment.strftime("%Y%m%dT%H%M%SZ")
    sequence = 0
    suffix_name = rules_artifact_suffix(equipment_rules_version)
    json_name = f"historical_equipment_backfill_apply_{suffix_name}.json"
    markdown_name = f"historical_equipment_backfill_apply_{suffix_name}.md"
    while True:
        suffix = f"_{timestamp}" + (f"_{sequence}" if sequence else "")
        paths = (
            _artifact_path(directory, json_name, suffix),
            _artifact_path(directory, markdown_name, suffix),
        )
        if not any(path.exists() for path in paths):
            return paths
        sequence += 1


def build_apply_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Aplicação histórica de equipamentos",
        "",
        f"Status: {payload['status']}",
        "",
        f"- Execution ID: {payload['execution_id'] or '-'}",
        f"- Plano utilizado (hash): {payload['plan_hash']}",
        f"- Versão do plano: {payload['plan_version']}",
        f"- Versão técnica: {payload['technical_processing_format_version']}",
        f"- Versão das regras: {payload['equipment_rules_version']}",
        f"- Início: {payload['started_at'] or '-'}",
        f"- Término: {payload['finished_at'] or '-'}",
        f"- Backup criado: {payload['backup_created']}",
        f"- Hash original: {payload['workbook_hash_before'] or '-'}",
        f"- Hash do backup: {payload['backup_hash'] or '-'}",
        f"- Hash final: {payload['workbook_hash_after'] or '-'}",
        f"- Alterações planejadas: {payload['updates_planned']}",
        f"- Alterações aplicadas: {payload['updates_applied']}",
        f"- Atualizações por aba: {payload['updates_by_sheet']}",
        f"- Conflitos de fingerprint: {payload['fingerprint_conflicts']}",
        f"- Mudanças inesperadas: {payload['unexpected_changes']}",
        f"- Confirmação recebida: {payload['confirmation_received']}",
        f"- Rollback executado: {payload['rollback_executed']}",
        f"- Status do rollback: {payload['rollback_status']}",
        f"- Idempotência: {payload['idempotency_result']}",
        f"- Código de erro: {payload['error_code'] or '-'}",
        "",
        "## Falhas",
        "",
        "| Aba | Linha | Protocolo | Códigos |",
        "| --- | ---: | --- | --- |",
    ]
    for item in payload["failures"]:
        fields = (
            item["sheet"],
            item["row"],
            item["protocol"],
            ", ".join(item["error_codes"]),
        )
        lines.append("| " + " | ".join(_md(value) for value in fields) + " |")
    return "\n".join(lines) + "\n"
