from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from automacao_gd.application.historical_backfill.comparison import (
    contains_sensitive_or_path_text,
)
from automacao_gd.domain.backfill_models import (
    BackfillAction,
    HistoricalAuditResult,
)
from automacao_gd.domain.equipment import EQUIPMENT_FORMAT_VERSION
from automacao_gd.domain.equipment_semantics import (
    canonical_collection_from_dict,
    compare_equipment_row_transition,
    format_canonical_collection,
)
from automacao_gd.domain.equipment_validation import (
    EQUIPMENT_RULES_VERSION,
    TECHNICAL_PROCESSING_FORMAT_VERSION,
)
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


BACKFILL_PLAN_VERSION = 3
_PLAN_FIELDS = {
    "execution_id",
    "plan_version",
    "created_at",
    "workbook_fingerprint",
    "equipment_format_version",
    "technical_processing_format_version",
    "equipment_rules_version",
    "total_items",
    "total_updates",
    "plan_status",
    "quality_summary",
    "items",
    "plan_hash",
}
_ITEM_FIELDS = {
    "workbook_sheet",
    "workbook_row",
    "protocol",
    "current_module_text",
    "current_inverter_text",
    "proposed_module_text",
    "proposed_inverter_text",
    "technical_validation_status",
    "action",
    "reasons",
    "warnings",
    "pdf_status",
    "source_hash",
    "expected_row_fingerprint",
    "semantic_validation_status",
    "semantic_validation_errors",
    "semantic_validation_warnings",
    "blocking_violations",
    "current_semantic_fingerprint",
    "proposed_semantic_fingerprint",
    "current_canonical_collection",
    "proposed_canonical_collection",
}


class BackfillPlanError(ValueError):
    pass


def build_backfill_plan(
    audit: HistoricalAuditResult, *, created_at: str | None = None
) -> dict[str, Any]:
    items = [_plan_item(item) for item in audit.items]
    effective_created_at = created_at or audit.created_at
    payload: dict[str, Any] = {
        "execution_id": _execution_id(
            effective_created_at, audit.workbook_fingerprint
        ),
        "plan_version": BACKFILL_PLAN_VERSION,
        "created_at": effective_created_at,
        "workbook_fingerprint": audit.workbook_fingerprint,
        "equipment_format_version": EQUIPMENT_FORMAT_VERSION,
        "technical_processing_format_version": TECHNICAL_PROCESSING_FORMAT_VERSION,
        "equipment_rules_version": EQUIPMENT_RULES_VERSION,
        "total_items": len(items),
        "total_updates": sum(
            item["action"] == BackfillAction.UPDATE_EQUIPMENT.value for item in items
        ),
        "items": items,
    }
    payload["quality_summary"] = audit_plan_quality(payload)
    payload["plan_status"] = (
        "VALID" if _quality_gate_passed(payload["quality_summary"]) else "INVALID"
    )
    payload["plan_hash"] = calculate_plan_hash(payload)
    validate_backfill_plan(payload)
    return payload


def calculate_plan_hash(payload: dict[str, Any]) -> str:
    canonical_payload = {key: value for key, value in payload.items() if key != "plan_hash"}
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_backfill_plan(payload: dict[str, Any]) -> None:
    if payload.get("plan_hash") != calculate_plan_hash(payload):
        raise BackfillPlanError("Plano bloqueado: hash inválido ou conteúdo alterado.")
    if set(payload) != _PLAN_FIELDS:
        raise BackfillPlanError("Plano bloqueado: campos não permitidos ou ausentes.")
    if not _is_sha256(payload.get("execution_id")):
        raise BackfillPlanError("Plano bloqueado: identificador de execução inválido.")
    if not isinstance(payload.get("created_at"), str):
        raise BackfillPlanError("Plano bloqueado: data de criação inválida.")
    if contains_sensitive_or_path_text(payload.get("created_at")):
        raise BackfillPlanError("Plano bloqueado: conteúdo sensível ou caminho detectado.")
    if not _is_sha256(payload.get("workbook_fingerprint")):
        raise BackfillPlanError("Plano bloqueado: fingerprint da planilha inválido.")
    if payload.get("execution_id") != _execution_id(
        payload["created_at"], payload["workbook_fingerprint"]
    ):
        raise BackfillPlanError("Plano bloqueado: identificador de execução divergente.")
    if payload.get("plan_version") != BACKFILL_PLAN_VERSION:
        raise BackfillPlanError("Plano bloqueado: versão incompatível.")
    if payload.get("equipment_format_version") != EQUIPMENT_FORMAT_VERSION:
        raise BackfillPlanError("Plano bloqueado: versão de formatação incompatível.")
    if (
        payload.get("technical_processing_format_version")
        != TECHNICAL_PROCESSING_FORMAT_VERSION
    ):
        raise BackfillPlanError("Plano bloqueado: versão técnica incompatível.")
    if payload.get("equipment_rules_version") != EQUIPMENT_RULES_VERSION:
        raise BackfillPlanError("Plano bloqueado: regras incompatível; regenere a auditoria.")
    items = payload.get("items")
    if (
        not isinstance(items, list)
        or isinstance(payload.get("total_items"), bool)
        or not isinstance(payload.get("total_items"), int)
        or payload.get("total_items") != len(items)
    ):
        raise BackfillPlanError("Plano bloqueado: estrutura de itens inválida.")
    updates = 0
    for item in items:
        if not isinstance(item, dict):
            raise BackfillPlanError("Plano bloqueado: item inválido.")
        if set(item) != _ITEM_FIELDS:
            raise BackfillPlanError("Plano bloqueado: campos de item inválidos.")
        if not isinstance(item.get("workbook_sheet"), str) or not item[
            "workbook_sheet"
        ].strip():
            raise BackfillPlanError("Plano bloqueado: aba inválida.")
        row = item.get("workbook_row")
        if isinstance(row, bool) or not isinstance(row, int) or row < 1:
            raise BackfillPlanError("Plano bloqueado: linha inválida.")
        protocol = item.get("protocol")
        if protocol is not None and (
            not isinstance(protocol, str) or not protocol.isdigit()
        ):
            raise BackfillPlanError("Plano bloqueado: protocolo inválido.")
        if any(
            value is not None and not isinstance(value, str)
            for value in (
                item.get("current_module_text"),
                item.get("current_inverter_text"),
                item.get("proposed_module_text"),
                item.get("proposed_inverter_text"),
                item.get("technical_validation_status"),
                item.get("pdf_status"),
            )
        ):
            raise BackfillPlanError("Plano bloqueado: conteúdo de item inválido.")
        if any(
            not isinstance(values, list)
            or any(not isinstance(value, str) for value in values)
            for values in (
                item.get("reasons"),
                item.get("warnings"),
                item.get("semantic_validation_errors"),
                item.get("semantic_validation_warnings"),
                item.get("blocking_violations"),
            )
        ):
            raise BackfillPlanError("Plano bloqueado: motivos ou avisos inválidos.")
        if item.get("source_hash") is not None and not _is_sha256(
            item.get("source_hash")
        ):
            raise BackfillPlanError("Plano bloqueado: hash de origem inválido.")
        if not _is_sha256(item.get("expected_row_fingerprint")):
            raise BackfillPlanError("Plano bloqueado: fingerprint de linha inválido.")
        try:
            action = BackfillAction(item.get("action"))
        except ValueError as exc:
            raise BackfillPlanError("Plano bloqueado: ação inválida.") from exc
        if action is BackfillAction.UPDATE_EQUIPMENT:
            updates += 1
            if (
                item.get("technical_validation_status") != "approved"
                or item.get("semantic_validation_status") != "approved"
                or item.get("semantic_validation_errors")
                or item.get("blocking_violations")
                or not item.get("proposed_module_text")
                or not item.get("proposed_inverter_text")
                or not item.get("expected_row_fingerprint")
                or not _is_sha256(item.get("source_hash"))
                or item.get("pdf_status") != "valid"
            ):
                raise BackfillPlanError("Plano bloqueado pelo quality gate: atualização incompleta.")
            try:
                current_collection = canonical_collection_from_dict(
                    item.get("current_canonical_collection")
                )
                proposed_collection = canonical_collection_from_dict(
                    item.get("proposed_canonical_collection")
                )
            except (TypeError, ValueError) as exc:
                raise BackfillPlanError(
                    "Plano bloqueado pelo quality gate: estrutura canônica inválida."
                ) from exc
            semantic = compare_equipment_row_transition(
                item.get("current_module_text") or "",
                item.get("current_inverter_text") or "",
                item.get("proposed_module_text") or "",
                item.get("proposed_inverter_text") or "",
                proposed_collection,
            )
            formatted_module, formatted_inverter = format_canonical_collection(
                proposed_collection
            )
            if (
                semantic.status != "approved"
                or semantic.errors
                or formatted_module != item.get("proposed_module_text")
                or formatted_inverter != item.get("proposed_inverter_text")
                or semantic.current_fingerprint
                != item.get("current_semantic_fingerprint")
                or semantic.proposed_fingerprint
                != item.get("proposed_semantic_fingerprint")
            ):
                raise BackfillPlanError(
                    "Plano bloqueado pelo quality gate: regressão semântica."
                )
        if _item_contains_unsafe_text(item):
            raise BackfillPlanError("Plano bloqueado: conteúdo sensível ou caminho detectado.")
    if (
        isinstance(payload.get("total_updates"), bool)
        or not isinstance(payload.get("total_updates"), int)
        or payload.get("total_updates") != updates
    ):
        raise BackfillPlanError("Plano bloqueado: contador de atualizações divergente.")
    expected_quality = audit_plan_quality(payload)
    if payload.get("quality_summary") != expected_quality:
        raise BackfillPlanError("Plano bloqueado pelo quality gate: contadores divergentes.")
    if payload.get("plan_status") != "VALID" or not _quality_gate_passed(
        expected_quality
    ):
        raise BackfillPlanError("Plano bloqueado pelo quality gate: plano INVALID.")


def write_backfill_plan(path: Path, payload: dict[str, Any]) -> Path:
    validate_backfill_plan(payload)
    return atomic_write_json(Path(path), payload, private=True)


def load_backfill_plan(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackfillPlanError("Plano não pôde ser lido.") from exc
    if not isinstance(payload, dict):
        raise BackfillPlanError("Plano bloqueado: raiz inválida.")
    validate_backfill_plan(payload)
    return payload


def _plan_item(item) -> dict[str, Any]:
    unsafe = contains_sensitive_or_path_text(item.current_module_text) or (
        contains_sensitive_or_path_text(item.current_inverter_text)
    )
    result = {
        "workbook_sheet": item.workbook_sheet,
        "workbook_row": item.workbook_row,
        "protocol": item.protocol,
        "current_module_text": "[REDACTED]" if unsafe else item.current_module_text,
        "current_inverter_text": "[REDACTED]" if unsafe else item.current_inverter_text,
        "proposed_module_text": item.proposed_module_text,
        "proposed_inverter_text": item.proposed_inverter_text,
        "technical_validation_status": item.technical_validation_status,
        "action": item.action.value,
        "reasons": list(item.reasons),
        "warnings": list(item.warnings),
        "pdf_status": item.pdf_status,
        "source_hash": item.source_hash,
        "expected_row_fingerprint": item.expected_row_fingerprint,
        "semantic_validation_status": item.semantic_validation_status,
        "semantic_validation_errors": list(item.semantic_validation_errors),
        "semantic_validation_warnings": list(item.semantic_validation_warnings),
        "blocking_violations": list(item.blocking_violations),
        "current_semantic_fingerprint": item.current_semantic_fingerprint,
        "proposed_semantic_fingerprint": item.proposed_semantic_fingerprint,
        "current_canonical_collection": item.current_canonical_collection,
        "proposed_canonical_collection": item.proposed_canonical_collection,
    }
    if (
        item.action is BackfillAction.UPDATE_EQUIPMENT
        and item.proposed_canonical_collection is not None
    ):
        try:
            proposed_collection = canonical_collection_from_dict(
                item.proposed_canonical_collection
            )
            semantic = compare_equipment_row_transition(
                item.current_module_text,
                item.current_inverter_text,
                item.proposed_module_text or "",
                item.proposed_inverter_text or "",
                proposed_collection,
            )
        except (TypeError, ValueError):
            return result
        result["semantic_validation_warnings"] = list(
            dict.fromkeys((*result["semantic_validation_warnings"], *semantic.warnings))
        )
        result["warnings"] = list(
            dict.fromkeys((*result["warnings"], *semantic.warnings))
        )
    return result


def audit_plan_quality(payload: dict[str, Any]) -> dict[str, int]:
    raw_items = payload.get("items")
    items: list[Any] = raw_items if isinstance(raw_items, list) else []
    updates: list[dict[str, Any]] = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("action") == BackfillAction.UPDATE_EQUIPMENT.value
    ]
    no_change: list[dict[str, Any]] = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("action") == BackfillAction.NO_CHANGE.value
    ]

    def update_error_count(code: str) -> int:
        return sum(
            code
            in {
                *item.get("semantic_validation_errors", []),
                *item.get("blocking_violations", []),
            }
            for item in updates
        )

    def all_error_count(code: str) -> int:
        return sum(
            code
            in {
                *item.get("semantic_validation_errors", []),
                *item.get("blocking_violations", []),
            }
            for item in items
            if isinstance(item, dict)
        )

    return {
        "total_items": len(items),
        "total_updates": len(updates),
        "no_change": len(no_change),
        "pdf_not_found": sum(
            item.get("action") == BackfillAction.PDF_NOT_FOUND.value
            for item in items
            if isinstance(item, dict)
        ),
        "pending_technical_review": sum(
            item.get("action") == BackfillAction.PENDING_TECHNICAL_REVIEW.value
            for item in items
            if isinstance(item, dict)
        ),
        "semantic_regressions_blocked": sum(
            "SEMANTIC_REGRESSION" in item.get("blocking_violations", [])
            for item in items
            if isinstance(item, dict)
        ),
        "duplicated_manufacturers_found": all_error_count(
            "DUPLICATED_MANUFACTURER_IN_MODEL"
        ),
        "generic_labels_found": all_error_count("GENERIC_LABEL_IN_MODEL"),
        "power_units_lost_found": all_error_count("POWER_UNIT_LOST"),
        "models_truncated_found": all_error_count("MODEL_TRUNCATED"),
        "equipment_items_lost_found": all_error_count("EQUIPMENT_ITEM_LOST"),
        "individual_quantities_lost_found": all_error_count("QUANTITY_LOST"),
        "ambiguous_matches_found": all_error_count("AMBIGUOUS_EQUIPMENT_MATCH"),
        "unknown_label_origins_found": all_error_count("GENERIC_LABEL_AMBIGUOUS"),
        "incomplete_canonical_structures_found": all_error_count(
            "CANONICAL_STRUCTURE_INCOMPLETE"
        ),
        "duplicated_manufacturers": update_error_count(
            "DUPLICATED_MANUFACTURER_IN_MODEL"
        ),
        "generic_labels": update_error_count("GENERIC_LABEL_IN_MODEL"),
        "power_units_lost": update_error_count("POWER_UNIT_LOST"),
        "models_truncated": update_error_count("MODEL_TRUNCATED"),
        "equipment_items_lost": update_error_count("EQUIPMENT_ITEM_LOST"),
        "individual_quantities_lost": update_error_count("QUANTITY_LOST"),
        "ambiguous_matches": update_error_count("AMBIGUOUS_EQUIPMENT_MATCH"),
        "unknown_label_origins": update_error_count("GENERIC_LABEL_AMBIGUOUS"),
        "incomplete_canonical_structures": update_error_count(
            "CANONICAL_STRUCTURE_INCOMPLETE"
        ),
        "unsafe_log_entries_generated": 0,
        "semantic_regressions": update_error_count("SEMANTIC_REGRESSION"),
        "empty_proposed_fields": sum(
            not item.get("proposed_module_text")
            or not item.get("proposed_inverter_text")
            for item in updates
        ),
        "blocking_violations": sum(
            bool(item.get("blocking_violations")) for item in updates
        ),
        "cosmetic_changes_avoided": sum(
            (
                item.get("current_module_text") != item.get("proposed_module_text")
                or item.get("current_inverter_text")
                != item.get("proposed_inverter_text")
            )
            for item in no_change
        ),
    }


def _quality_gate_passed(summary: dict[str, int]) -> bool:
    return all(
        summary.get(field) == 0
        for field in (
            "duplicated_manufacturers",
            "generic_labels",
            "power_units_lost",
            "models_truncated",
            "equipment_items_lost",
            "individual_quantities_lost",
            "ambiguous_matches",
            "unknown_label_origins",
            "incomplete_canonical_structures",
            "unsafe_log_entries_generated",
            "semantic_regressions",
            "empty_proposed_fields",
            "blocking_violations",
        )
    )


def _item_contains_unsafe_text(item: dict[str, Any]) -> bool:
    scalar_fields = (
        "workbook_sheet",
        "current_module_text",
        "current_inverter_text",
        "proposed_module_text",
        "proposed_inverter_text",
        "technical_validation_status",
        "pdf_status",
    )
    for field in scalar_fields:
        value = item.get(field)
        if value != "[REDACTED]" and contains_sensitive_or_path_text(value):
            return True
    for field in (
        "reasons",
        "warnings",
        "semantic_validation_errors",
        "semantic_validation_warnings",
        "blocking_violations",
    ):
        if any(contains_sensitive_or_path_text(value) for value in item.get(field, ())):
            return True
    for field in ("current_canonical_collection", "proposed_canonical_collection"):
        if _nested_contains_unsafe_text(item.get(field)):
            return True
    return False


def _nested_contains_unsafe_text(value: object) -> bool:
    if isinstance(value, dict):
        return any(_nested_contains_unsafe_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_nested_contains_unsafe_text(item) for item in value)
    return isinstance(value, str) and contains_sensitive_or_path_text(value)


def _execution_id(created_at: str, workbook_fingerprint: str) -> str:
    identity = "\0".join((created_at, workbook_fingerprint))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )
