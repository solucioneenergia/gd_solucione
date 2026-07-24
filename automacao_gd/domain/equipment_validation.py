from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from automacao_gd.domain.equipment import is_module_only_manufacturer
from automacao_gd.domain.equipment_semantics import (
    CANONICAL_RULES_VERSION,
    TECHNICAL_FORMAT_VERSION,
    CanonicalEquipmentCollection,
    CanonicalEquipment,
    canonical_collection_from_generation_data,
    canonical_structure_violations,
)
from automacao_gd.domain.models import GenerationData


PENDING_REVIEW_MESSAGE = (
    "Extração técnica inconclusiva: os dados de Placa e Inversor "
    "não puderam ser separados com segurança."
)
PENDING_REVIEW_ACTION = (
    "Confira a seção 3. GERAÇÃO do orçamento de conexão e reprocese o "
    "protocolo após corrigir o parser ou os dados de origem."
)
TECHNICAL_PROCESSING_FORMAT_VERSION = TECHNICAL_FORMAT_VERSION
EQUIPMENT_RULES_VERSION = CANONICAL_RULES_VERSION


@dataclass(frozen=True)
class TechnicalValidationResult:
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    module_source: str | None = None
    inverter_source: str | None = None
    user_message: str | None = None
    recommended_action: str | None = None

    @property
    def approved(self) -> bool:
        return self.status == "approved" and not self.errors

    @property
    def technical_review_required(self) -> bool:
        return not self.approved


def validate_technical_equipment(
    data: GenerationData,
    module_text: str,
    inverter_text: str,
) -> TechnicalValidationResult:
    return validate_canonical_equipment(
        canonical_collection_from_generation_data(data),
        module_text,
        inverter_text,
        module_source=data.module_source,
        inverter_source=data.inverter_source,
    )


def validate_canonical_equipment(
    collection: CanonicalEquipmentCollection,
    module_text: str,
    inverter_text: str,
    *,
    module_source: str | None = None,
    inverter_source: str | None = None,
) -> TechnicalValidationResult:
    errors: list[str] = []
    warnings = list(collection.collection_warnings)
    modules = collection.modules
    inverters = collection.conventional_inverters
    microinverters = collection.microinverters
    errors.extend(collection.collection_violations)
    errors.extend(canonical_structure_violations(collection))
    errors.extend(
        violation for item in collection.all_items() for violation in item.violations
    )
    warnings.extend(warning for item in collection.all_items() for warning in item.warnings)

    if not module_text.strip():
        errors.append("MODULE_TEXT_EMPTY")
    if not inverter_text.strip():
        errors.append("INVERTER_TEXT_EMPTY")
    if not modules:
        errors.append("MODULE_IDENTITY_MISSING")
    elif any(
        not item.canonical_manufacturer or not item.canonical_model for item in modules
    ):
        errors.append("MODULE_PAIRING_UNSAFE")
    if not _category_quantity_valid(modules, collection.module_total_quantity):
        errors.append("MODULE_QUANTITY_MISSING")

    inverter_equipment = [*inverters, *microinverters]
    if not inverter_equipment:
        errors.append("INVERTER_IDENTITY_MISSING")
    elif any(
        not item.canonical_manufacturer or not item.canonical_model
        for item in inverter_equipment
    ):
        errors.append("INVERTER_PAIRING_UNSAFE")
    if inverters and not _category_quantity_valid(
        inverters, collection.inverter_total_quantity
    ):
        errors.append("INVERTER_QUANTITY_MISSING")
    if microinverters and not _category_quantity_valid(
        microinverters, collection.microinverter_total_quantity
    ):
        errors.append("MICROINVERTER_QUANTITY_MISSING")

    if any(
        is_module_only_manufacturer(item.canonical_manufacturer)
        for item in inverter_equipment
    ):
        errors.append("GOKIN_INVALID_INVERTER")
    if _canonical_has_module_contamination(modules, inverter_equipment, inverter_text):
        errors.append("INVERTER_MODULE_CONTAMINATION")

    errors = list(dict.fromkeys(errors))
    if errors:
        return TechnicalValidationResult(
            status="pending_review",
            errors=errors,
            warnings=list(dict.fromkeys(warnings)),
            module_source=module_source,
            inverter_source=inverter_source,
            user_message=PENDING_REVIEW_MESSAGE,
            recommended_action=PENDING_REVIEW_ACTION,
        )
    return TechnicalValidationResult(
        status="approved",
        warnings=list(dict.fromkeys(warnings)),
        module_source=module_source,
        inverter_source=inverter_source,
    )


def _canonical_has_module_contamination(
    modules: Sequence[CanonicalEquipment],
    inverters: Sequence[CanonicalEquipment],
    inverter_text: str,
) -> bool:
    inverter_identity_text = " ".join(
        f"{item.canonical_manufacturer} {item.canonical_model}" for item in inverters
    )
    comparison = _comparison_text(f"{inverter_identity_text} {inverter_text}")
    signals = (
        r"\bbifacial\b",
        r"\bn[\s-]*type\b",
        r"\bmonocristalino\b",
        r"\bpolicristalino\b",
        r"\b\d+(?:[,.]\d+)?\s*wp\b",
        r"potencia\s+unitaria\s+(?:de\s+)?modulo",
    )
    if any(re.search(pattern, comparison) for pattern in signals):
        return True
    inverter_manufacturers = {
        _comparison_text(item.canonical_manufacturer) for item in inverters
    }
    inverter_models = {_comparison_text(item.canonical_model) for item in inverters}
    for module in modules:
        maker = _comparison_text(module.canonical_manufacturer)
        model = _comparison_text(module.canonical_model)
        if maker and any(maker in value and maker != value for value in inverter_manufacturers):
            return True
        if len(model) >= 5 and any(model in value for value in inverter_models):
            return True
    return False


def technical_validation_from_cache(payload: dict) -> TechnicalValidationResult:
    approved = (
        payload.get("technical_validation_status") == "approved"
        and not payload.get("technical_review_required")
        and not payload.get("technical_validation_errors")
    )
    return TechnicalValidationResult(
        status="approved" if approved else "pending_review",
        errors=list(payload.get("technical_validation_errors") or []),
        warnings=list(payload.get("technical_validation_warnings") or []),
        module_source=payload.get("module_source"),
        inverter_source=payload.get("inverter_source"),
        user_message=None if approved else PENDING_REVIEW_MESSAGE,
        recommended_action=None if approved else PENDING_REVIEW_ACTION,
    )


def _module_identities(data: GenerationData) -> list[tuple[str | None, str | None, int | None]]:
    if data.modules:
        return [
            (item.manufacturer, item.model, item.quantity)
            for item in data.modules
            if item.manufacturer or item.model
        ]
    if data.module_manufacturer or data.module_model:
        return [(data.module_manufacturer, data.module_model, data.module_quantity)]
    return []


def _inverter_identities(
    data: GenerationData,
) -> list[tuple[str | None, str | None, int | None]]:
    if data.inverters:
        return [
            (item.manufacturer, item.model, item.quantity)
            for item in data.inverters
            if item.manufacturer or item.model
        ]
    if data.inverter_manufacturer or data.inverter_model:
        return [(data.inverter_manufacturer, data.inverter_model, data.inverter_quantity)]
    return []


def _microinverter_identities(
    data: GenerationData,
) -> list[tuple[str | None, str | None, int | None]]:
    return [
        (item.manufacturer, item.model, item.quantity)
        for item in data.microinverters
        if item.manufacturer or item.model
    ]


def _positive_quantity(
    total: object,
    legacy: object,
    items: list[tuple[str | None, str | None, int | None]],
) -> bool:
    return any(_is_positive_integer(value) for value in [total, legacy]) or any(
        _is_positive_integer(quantity) for _, _, quantity in items
    )


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _category_quantity_valid(items: Sequence[CanonicalEquipment], total: object) -> bool:
    if len(items) == 1:
        return _is_positive_integer(items[0].quantity) or _is_positive_integer(total)
    return bool(items) and all(_is_positive_integer(item.quantity) for item in items)


def _has_module_contamination(
    data: GenerationData,
    modules: list[tuple[str | None, str | None, int | None]],
    inverters: list[tuple[str | None, str | None, int | None]],
    inverter_text: str,
) -> bool:
    inverter_identity_text = " ".join(
        part or "" for item in inverters for part in item[:2]
    )
    comparison = _comparison_text(f"{inverter_identity_text} {inverter_text}")
    signals = (
        r"\bbifacial\b",
        r"\bn[\s-]*type\b",
        r"\bmonocristalino\b",
        r"\bpolicristalino\b",
        r"\b\d+(?:[,.]\d+)?\s*wp\b",
        r"potencia\s+unitaria\s+(?:de\s+)?modulo",
    )
    if any(re.search(pattern, comparison) for pattern in signals):
        return True
    for manufacturer, model, _ in modules:
        manufacturer_key = _comparison_text(manufacturer or "")
        model_key = _comparison_text(model or "")
        inverter_manufacturer = _comparison_text(data.inverter_manufacturer or "")
        inverter_model = _comparison_text(data.inverter_model or "")
        if manufacturer_key and manufacturer_key != inverter_manufacturer:
            if manufacturer_key in inverter_manufacturer:
                return True
        if len(model_key) >= 5 and model_key in inverter_model:
            return True
    return False


def _warnings(data: GenerationData) -> list[str]:
    if not data.equipment_parse_warning:
        return []
    return list(
        dict.fromkeys(
            part.strip()
            for part in data.equipment_parse_warning.split(";")
            if part.strip()
        )
    )


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_accents).strip().casefold()
