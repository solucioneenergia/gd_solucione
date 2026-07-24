from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from automacao_gd.domain.equipment_semantics import (
    CanonicalEquipmentCollection,
    canonical_collection_from_dict,
    canonical_collection_from_generation_data,
    canonical_collection_to_dict,
    canonical_structure_violations,
    format_canonical_collection,
)
from automacao_gd.domain.equipment_validation import (
    EQUIPMENT_RULES_VERSION,
    TECHNICAL_PROCESSING_FORMAT_VERSION,
    TechnicalValidationResult,
    validate_canonical_equipment,
)
from automacao_gd.domain.models import GenerationData


@dataclass(frozen=True)
class ValidatedEquipmentCache:
    module_excel: str
    inverter_excel: str
    placa_planilha: str
    inversor_planilha: str
    validation: TechnicalValidationResult
    collection: CanonicalEquipmentCollection


def load_validated_equipment_cache(
    technical: object,
) -> ValidatedEquipmentCache | None:
    """Load only a complete, lossless and semantically valid V6 cache."""
    if not isinstance(technical, dict):
        return None
    if technical.get("status") not in {"validated", "operational_pending", "success"}:
        return None
    if technical.get("format_version") != TECHNICAL_PROCESSING_FORMAT_VERSION:
        return None
    if technical.get("equipment_rules_version") != EQUIPMENT_RULES_VERSION:
        return None
    try:
        collection = canonical_collection_from_dict(technical.get("equipment"))
    except (TypeError, ValueError):
        return None
    if canonical_structure_violations(collection):
        return None

    placa_planilha, inversor_planilha = format_canonical_collection(collection)
    validation = validate_canonical_equipment(
        collection,
        placa_planilha,
        inversor_planilha,
        module_source=_source_label(collection.modules),
        inverter_source=_source_label(
            (*collection.conventional_inverters, *collection.microinverters)
        ),
    )
    if not validation.approved:
        return None
    if (
        placa_planilha != (technical.get("placa_planilha") or "")
        or inversor_planilha != (technical.get("inversor_planilha") or "")
    ):
        return None
    module_excel = technical.get("module_excel")
    inverter_excel = technical.get("inverter_excel")
    if not isinstance(module_excel, str) or not isinstance(inverter_excel, str):
        return None
    return ValidatedEquipmentCache(
        module_excel=module_excel,
        inverter_excel=inverter_excel,
        placa_planilha=placa_planilha,
        inversor_planilha=inversor_planilha,
        validation=validation,
        collection=collection,
    )


def serialize_equipment_cache(
    data: GenerationData | CanonicalEquipmentCollection,
) -> dict[str, Any]:
    collection = (
        data
        if isinstance(data, CanonicalEquipmentCollection)
        else canonical_collection_from_generation_data(data)
    )
    return canonical_collection_to_dict(collection)


def _source_label(items: tuple) -> str | None:
    values = tuple(dict.fromkeys(item.source.origin_type.value for item in items))
    return ",".join(values) if values else None
