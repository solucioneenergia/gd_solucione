from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal, cast


EquipmentType = Literal["module", "inverter", "microinverter"]
TECHNICAL_FORMAT_VERSION = 7
CANONICAL_RULES_VERSION = "equipment-v2-technical-v7-rules-7"

_SOLPLANET_ALIASES = {"solplanet", "aiswei"}
_KNOWN_MULTI_TOKEN_MANUFACTURERS = (
    "GUANGZHOU SANJING ELECTRIC CO., LTD (SAJ)",
    "GROWATT NEW ENERGY CO., LTD.",
    "SUNGROW POWER SUPPLY CO., LTD.",
    "HUAWEI TECHNOLOGIES CO., LTD",
    "GINLONG (SOLIS)",
    "HUAWEI TECHNOLOGIES",
    "LIVOLTEK POWER",
    "PULLING ENERGY",
    "AUSTA SOLAR",
    "ERA SOLAR",
)
_GENERIC_LABELS = {
    "modulo",
    "modulos",
    "module",
    "modules",
    "placa",
    "placas",
    "inversor",
    "inversores",
    "inverter",
    "inverters",
    "microinversor",
    "microinversores",
    "fabricante",
    "fabricantes",
    "modelo",
    "modelos",
    "equipamento",
    "equipamentos",
}
_TECHNICAL_ATTRIBUTES = (
    "BIFACIAL",
    "MONOFACIAL",
    "MONOCRISTALINO",
    "POLICRISTALINO",
    "N-TYPE",
    "P-TYPE",
    "TOPCON",
    "HALF-CELL",
    "AFCI",
    "MONO",
    "PRO",
    "LITE",
    "MAX",
)
_CONTAMINATION_SOURCES = {
    "table_header_contamination",
    "field_label_contamination",
    "section_label",
    "generated_prefix",
}
_PROVEN_SOURCES = {
    "table_cell",
    "table_header_contamination",
    "linear_field_value",
    "field_label_contamination",
    "structured_cache",
    "catalog_value",
}
_ITEM_FIELDS = {
    "equipment_type",
    "canonical_manufacturer",
    "raw_manufacturer",
    "canonical_model",
    "raw_model",
    "quantity",
    "power_value",
    "power_unit",
    "technical_attributes",
    "source",
    "violations",
    "warnings",
}
_COLLECTION_FIELDS = {
    "modules",
    "conventional_inverters",
    "microinverters",
    "module_total_quantity",
    "inverter_total_quantity",
    "microinverter_total_quantity",
    "collection_violations",
    "collection_warnings",
    "technical_format_version",
    "equipment_rules_version",
}
_SHA_FIELDS = (
    "equipment_type",
    "canonical_manufacturer",
    "canonical_model",
    "quantity",
    "power_value",
    "power_unit",
    "technical_attributes",
)


class EquipmentSourceType(str, Enum):
    TABLE_CELL = "table_cell"
    TABLE_HEADER_CONTAMINATION = "table_header_contamination"
    LINEAR_FIELD_VALUE = "linear_field_value"
    FIELD_LABEL_CONTAMINATION = "field_label_contamination"
    SECTION_LABEL = "section_label"
    STRUCTURED_CACHE = "structured_cache"
    LEGACY_CACHE = "legacy_cache"
    CATALOG_VALUE = "catalog_value"
    GENERATED_PREFIX = "generated_prefix"
    FORMATTED_CELL = "formatted_cell"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EquipmentSource:
    origin_type: EquipmentSourceType
    section: str | None = None
    field: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalEquipment:
    equipment_type: EquipmentType
    canonical_manufacturer: str
    raw_manufacturer: str
    canonical_model: str
    raw_model: str
    quantity: int | None = None
    power_value: str | None = None
    power_unit: str | None = None
    technical_attributes: tuple[str, ...] = ()
    source: EquipmentSource = field(
        default_factory=lambda: EquipmentSource(EquipmentSourceType.UNKNOWN)
    )
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalEquipmentCollection:
    modules: tuple[CanonicalEquipment, ...] = ()
    conventional_inverters: tuple[CanonicalEquipment, ...] = ()
    microinverters: tuple[CanonicalEquipment, ...] = ()
    module_total_quantity: int | None = None
    inverter_total_quantity: int | None = None
    microinverter_total_quantity: int | None = None
    collection_violations: tuple[str, ...] = ()
    collection_warnings: tuple[str, ...] = ()
    technical_format_version: int = TECHNICAL_FORMAT_VERSION
    equipment_rules_version: str = CANONICAL_RULES_VERSION

    def all_items(self) -> tuple[CanonicalEquipment, ...]:
        return (
            *self.modules,
            *self.conventional_inverters,
            *self.microinverters,
        )


@dataclass(frozen=True, slots=True)
class SemanticComparison:
    status: Literal["approved", "pending_review"]
    equivalent: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    current_fingerprint: str
    proposed_fingerprint: str


def canonical_manufacturer(value: str | None) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return ""
    key = _key(cleaned)
    if _alias_tokens(key):
        return "SOLPLANET"
    return cleaned.upper()


def remove_redundant_manufacturer_prefix(
    manufacturer: str | None, model: str | None
) -> str:
    """Remove only complete, consecutive manufacturer aliases at the prefix."""
    canonical = canonical_manufacturer(manufacturer)
    words = _clean(model).split()
    if not canonical or not words:
        return " ".join(words)
    aliases = _manufacturer_alias_sequences(canonical)
    while True:
        matched = next(
            (size for alias in aliases if (size := _leading_match(words, alias))),
            0,
        )
        if not matched:
            break
        words = words[matched:]
    return " ".join(words)


def canonicalize_equipment(
    *,
    equipment_type: EquipmentType | str,
    manufacturer: str | None,
    model: str | None,
    quantity: int | None = None,
    source: EquipmentSource | EquipmentSourceType | str | None = None,
) -> CanonicalEquipment:
    canonical_type = _equipment_type(equipment_type)
    canonical_source = _coerce_source(source)
    raw_manufacturer = _clean(manufacturer)
    raw_model = _clean(model)
    canonical_maker = canonical_manufacturer(raw_manufacturer)
    violations: list[str] = []
    has_generic_label = _contains_generic_label(raw_model)
    if canonical_source.origin_type.value in _CONTAMINATION_SOURCES:
        canonical_model = _remove_generic_labels(raw_model)
        canonical_model = remove_redundant_manufacturer_prefix(
            canonical_maker, canonical_model
        )
        if has_generic_label:
            canonical_model = _remove_manufacturer_sequences(
                canonical_maker, canonical_model
            )
    else:
        canonical_model = remove_redundant_manufacturer_prefix(
            canonical_maker, raw_model
        )
        if (
            has_generic_label
            and canonical_source.origin_type is not EquipmentSourceType.CATALOG_VALUE
        ):
            violations.extend(
                ("GENERIC_LABEL_IN_MODEL", "GENERIC_LABEL_ORIGIN_UNPROVEN")
            )
            if _contains_manufacturer_sequence(canonical_maker, raw_model):
                violations.append("DUPLICATED_MANUFACTURER_IN_MODEL")
            if canonical_source.origin_type is EquipmentSourceType.UNKNOWN:
                violations.append("GENERIC_LABEL_AMBIGUOUS")
    canonical_model = _normalize_explicit_watts(canonical_model)
    power_value, power_unit = _explicit_power(canonical_model)
    attributes = _technical_attributes(canonical_model)
    return CanonicalEquipment(
        equipment_type=canonical_type,
        canonical_manufacturer=canonical_maker,
        raw_manufacturer=raw_manufacturer,
        canonical_model=canonical_model,
        raw_model=raw_model,
        quantity=quantity,
        power_value=power_value,
        power_unit=power_unit,
        technical_attributes=attributes,
        source=canonical_source,
        violations=tuple(violations),
    )


def canonical_collection_from_generation_data(data: object) -> CanonicalEquipmentCollection:
    modules = _items_from_generation_data(
        data,
        items_attr="modules",
        legacy_manufacturer="module_manufacturer",
        legacy_model="module_model",
        legacy_quantity="module_quantity",
        total_quantity="module_total_quantity",
        equipment_type="module",
        default_source=getattr(data, "module_source", None),
    )
    inverters = _items_from_generation_data(
        data,
        items_attr="inverters",
        legacy_manufacturer="inverter_manufacturer",
        legacy_model="inverter_model",
        legacy_quantity="inverter_quantity",
        total_quantity="inverter_total_quantity",
        equipment_type="inverter",
        default_source=getattr(data, "inverter_source", None),
    )
    microinverters = _items_from_generation_data(
        data,
        items_attr="microinverters",
        legacy_manufacturer=None,
        legacy_model=None,
        legacy_quantity=None,
        total_quantity="microinverter_total_quantity",
        equipment_type="microinverter",
        default_source=getattr(data, "inverter_source", None),
    )
    violations = tuple(
        str(getattr(item, "code", item))
        for item in (getattr(data, "equipment_violations", None) or ())
    )
    warnings = tuple(
        part.strip()
        for part in str(getattr(data, "equipment_parse_warning", None) or "").split(";")
        if part.strip()
    )
    return CanonicalEquipmentCollection(
        modules=modules,
        conventional_inverters=inverters,
        microinverters=microinverters,
        module_total_quantity=getattr(data, "module_total_quantity", None)
        or getattr(data, "module_quantity", None),
        inverter_total_quantity=getattr(data, "inverter_total_quantity", None)
        or getattr(data, "inverter_quantity", None),
        microinverter_total_quantity=getattr(
            data, "microinverter_total_quantity", None
        ),
        collection_violations=violations,
        collection_warnings=warnings,
    )


def canonical_collection_from_formatted_cells(
    module_text: str,
    inverter_text: str,
    *,
    source: EquipmentSourceType = EquipmentSourceType.FORMATTED_CELL,
) -> CanonicalEquipmentCollection:
    module_items = _deduplicate_unquantified_aliases(
        _parse_cell(module_text, "module", source)
    )
    inverter_items = _deduplicate_unquantified_aliases(
        _parse_cell(inverter_text, "inverter", source)
    )
    return CanonicalEquipmentCollection(
        modules=module_items,
        conventional_inverters=tuple(
            item
            for item in inverter_items
            if item.equipment_type == "inverter"
        ),
        microinverters=tuple(
            item
            for item in inverter_items
            if item.equipment_type == "microinverter"
        ),
    )


def canonical_collection_to_dict(
    collection: CanonicalEquipmentCollection,
) -> dict[str, Any]:
    return {
        "modules": [_item_to_dict(item) for item in collection.modules],
        "conventional_inverters": [
            _item_to_dict(item) for item in collection.conventional_inverters
        ],
        "microinverters": [
            _item_to_dict(item) for item in collection.microinverters
        ],
        "module_total_quantity": collection.module_total_quantity,
        "inverter_total_quantity": collection.inverter_total_quantity,
        "microinverter_total_quantity": collection.microinverter_total_quantity,
        "collection_violations": list(collection.collection_violations),
        "collection_warnings": list(collection.collection_warnings),
        "technical_format_version": collection.technical_format_version,
        "equipment_rules_version": collection.equipment_rules_version,
    }


def canonical_collection_from_dict(payload: object) -> CanonicalEquipmentCollection:
    if not isinstance(payload, dict) or set(payload) != _COLLECTION_FIELDS:
        raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
    if payload.get("technical_format_version") != TECHNICAL_FORMAT_VERSION:
        raise ValueError("LEGACY_CACHE_INCOMPATIBLE")
    if payload.get("equipment_rules_version") != CANONICAL_RULES_VERSION:
        raise ValueError("LEGACY_CACHE_INCOMPATIBLE")
    return CanonicalEquipmentCollection(
        modules=_items_from_dict(payload["modules"], "module"),
        conventional_inverters=_items_from_dict(
            payload["conventional_inverters"], "inverter"
        ),
        microinverters=_items_from_dict(payload["microinverters"], "microinverter"),
        module_total_quantity=_optional_int(payload["module_total_quantity"]),
        inverter_total_quantity=_optional_int(payload["inverter_total_quantity"]),
        microinverter_total_quantity=_optional_int(
            payload["microinverter_total_quantity"]
        ),
        collection_violations=_string_tuple(payload["collection_violations"]),
        collection_warnings=_string_tuple(payload["collection_warnings"]),
        technical_format_version=cast(int, payload["technical_format_version"]),
        equipment_rules_version=cast(str, payload["equipment_rules_version"]),
    )


def format_canonical_equipment(equipment: CanonicalEquipment) -> str:
    name = " ".join(
        part
        for part in (
            equipment.canonical_manufacturer,
            equipment.canonical_model,
        )
        if part
    )
    return f"{equipment.quantity}x {name}" if equipment.quantity is not None else name


def format_canonical_collection(
    collection: CanonicalEquipmentCollection,
) -> tuple[str, str]:
    module_text = _format_category(
        collection.modules,
        singular_label="módulo",
        plural_label="módulos",
        total_quantity=collection.module_total_quantity,
    )
    inverter_text = _format_inverter_categories(
        collection.conventional_inverters,
        collection.microinverters,
        inverter_total=collection.inverter_total_quantity,
        microinverter_total=collection.microinverter_total_quantity,
    )
    return module_text, inverter_text


def compare_canonical_collections(
    current: CanonicalEquipmentCollection,
    proposed: CanonicalEquipmentCollection,
) -> SemanticComparison:
    errors: list[str] = [
        *proposed.collection_violations,
        *(violation for item in proposed.all_items() for violation in item.violations),
    ]
    warnings: list[str] = [
        *proposed.collection_warnings,
        *(warning for item in proposed.all_items() for warning in item.warnings),
    ]
    for current_items, proposed_items in (
        (current.modules, proposed.modules),
        (current.conventional_inverters, proposed.conventional_inverters),
        (current.microinverters, proposed.microinverters),
    ):
        category_errors, category_warnings = _compare_category(
            current_items, proposed_items
        )
        errors.extend(category_errors)
        warnings.extend(category_warnings)
    errors.extend(canonical_structure_violations(proposed))
    if errors:
        errors.append("SEMANTIC_REGRESSION")
    current_fingerprint = semantic_fingerprint(current.all_items())
    proposed_fingerprint = semantic_fingerprint(proposed.all_items())
    unique_errors = tuple(dict.fromkeys(errors))
    return SemanticComparison(
        status="pending_review" if unique_errors else "approved",
        equivalent=current_fingerprint == proposed_fingerprint,
        errors=unique_errors,
        warnings=tuple(dict.fromkeys(warnings)),
        current_fingerprint=current_fingerprint,
        proposed_fingerprint=proposed_fingerprint,
    )


def semantic_compare_equipment_cells(
    current_module: str,
    current_inverter: str,
    proposed_module: str,
    proposed_inverter: str,
) -> SemanticComparison:
    current = canonical_collection_from_formatted_cells(
        current_module, current_inverter
    )
    proposed = canonical_collection_from_formatted_cells(
        proposed_module,
        proposed_inverter,
        source=EquipmentSourceType.STRUCTURED_CACHE,
    )
    result = compare_canonical_collections(current, proposed)
    raw_errors = tuple(
        dict.fromkeys(
            (
                *_raw_proposal_violations(proposed_module, "module"),
                *_raw_proposal_violations(proposed_inverter, "inverter"),
            )
        )
    )
    if (
        not raw_errors
        and _key(current_module) == _key(proposed_module)
        and _key(current_inverter) == _key(proposed_inverter)
    ):
        return SemanticComparison(
            status="approved",
            equivalent=True,
            errors=(),
            warnings=result.warnings,
            current_fingerprint=result.current_fingerprint,
            proposed_fingerprint=result.proposed_fingerprint,
        )
    if not raw_errors:
        return result
    errors = tuple(dict.fromkeys((*result.errors, *raw_errors, "SEMANTIC_REGRESSION")))
    return SemanticComparison(
        status="pending_review",
        equivalent=result.equivalent,
        errors=errors,
        warnings=result.warnings,
        current_fingerprint=result.current_fingerprint,
        proposed_fingerprint=result.proposed_fingerprint,
    )


def compare_equipment_row_transition(
    current_module: str,
    current_inverter: str,
    proposed_module: str,
    proposed_inverter: str,
    source: CanonicalEquipmentCollection,
) -> SemanticComparison:
    """Validate a row-level equipment rewrite against the approved document source.

    The workbook may contain historical contamination where module tokens were stored
    in the inverter cell. For update safety, compare the combined workbook row to the
    combined proposed/source row before treating moved tokens as a regression.
    """
    current = canonical_collection_from_formatted_cells(
        current_module, current_inverter
    )
    proposed = canonical_collection_from_formatted_cells(
        proposed_module,
        proposed_inverter,
        source=EquipmentSourceType.STRUCTURED_CACHE,
    )
    source_to_proposed = compare_canonical_collections(source, proposed)
    raw_errors = tuple(
        dict.fromkeys(
            (
                *_raw_proposal_violations(proposed_module, "module"),
                *_raw_proposal_violations(proposed_inverter, "inverter"),
            )
        )
    )
    current_fingerprint = semantic_fingerprint(current.all_items())
    proposed_fingerprint = semantic_fingerprint(source.all_items())
    if source_to_proposed.errors or raw_errors:
        errors = tuple(
            dict.fromkeys(
                (*source_to_proposed.errors, *raw_errors, "SEMANTIC_REGRESSION")
            )
        )
        return SemanticComparison(
            status="pending_review",
            equivalent=False,
            errors=errors,
            warnings=source_to_proposed.warnings,
            current_fingerprint=current_fingerprint,
            proposed_fingerprint=proposed_fingerprint,
        )

    current_to_source = compare_canonical_collections(current, source)
    if current_to_source.status == "approved":
        cleanup_warnings = row_text_cleanup_warnings(
            current_module, current_inverter, source
        )
        return SemanticComparison(
            status="approved",
            equivalent=current_to_source.equivalent,
            errors=(),
            warnings=tuple(
                dict.fromkeys((*current_to_source.warnings, *cleanup_warnings))
            ),
            current_fingerprint=current_fingerprint,
            proposed_fingerprint=proposed_fingerprint,
        )

    if _cross_field_contamination_resolved(
        current_module=current_module,
        current_inverter=current_inverter,
        source=source,
    ):
        return SemanticComparison(
            status="approved",
            equivalent=False,
            errors=(),
            warnings=tuple(
                dict.fromkeys(
                    (*source_to_proposed.warnings, "CROSS_FIELD_CONTAMINATION_RESOLVED")
                )
            ),
            current_fingerprint=current_fingerprint,
            proposed_fingerprint=proposed_fingerprint,
        )

    if _proven_structural_contamination_removed(current, source):
        return SemanticComparison(
            status="approved",
            equivalent=False,
            errors=(),
            warnings=tuple(
                dict.fromkeys(
                    (
                        *source_to_proposed.warnings,
                        "PROVEN_STRUCTURAL_CONTAMINATION_REMOVED",
                    )
                )
            ),
            current_fingerprint=current_fingerprint,
            proposed_fingerprint=proposed_fingerprint,
        )

    return SemanticComparison(
        status="pending_review",
        equivalent=False,
        errors=current_to_source.errors,
        warnings=tuple(
            dict.fromkeys((*current_to_source.warnings, *source_to_proposed.warnings))
        ),
        current_fingerprint=current_fingerprint,
        proposed_fingerprint=proposed_fingerprint,
    )


def row_text_cleanup_warnings(
    current_module: str,
    current_inverter: str,
    source: CanonicalEquipmentCollection,
) -> tuple[str, ...]:
    current = canonical_collection_from_formatted_cells(
        current_module, current_inverter
    )
    warnings: list[str] = []
    if _has_current_redundant_manufacturer_prefix(current, source):
        warnings.append("DUPLICATED_MANUFACTURER_IN_MODEL_REMOVED")
    if _proven_structural_contamination_removed(current, source):
        warnings.append("PROVEN_STRUCTURAL_CONTAMINATION_REMOVED")
    return tuple(dict.fromkeys(warnings))


def semantic_fingerprint(equipment: tuple[CanonicalEquipment, ...]) -> str:
    payload = sorted(
        (
            {field: _semantic_field_value(item, field) for field in _SHA_FIELDS}
            for item in equipment
        ),
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compare_category(
    current: tuple[CanonicalEquipment, ...],
    proposed: tuple[CanonicalEquipment, ...],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    unmatched = set(range(len(proposed)))
    for current_item in current:
        if not _protectable_current_item(current_item):
            errors.extend(
                (
                    "CURRENT_CANONICAL_STRUCTURE_INCOMPLETE",
                    "EQUIPMENT_ITEM_LOST",
                )
            )
            continue
        exact = [
            index
            for index in unmatched
            if _technical_identity(current_item) == _technical_identity(proposed[index])
        ]
        match = _choose_exact_quantity_match(current_item, proposed, exact)
        if match is None and len(exact) > 1:
            errors.append("AMBIGUOUS_EQUIPMENT_MATCH")
            continue
        if match is None:
            partial = [
                index
                for index in unmatched
                if _safe_partial_match(current_item, proposed[index])
            ]
            if len(partial) > 1:
                errors.append("AMBIGUOUS_EQUIPMENT_MATCH")
                continue
            if len(partial) == 1:
                match = partial[0]
                if _model_tokens_lost(current_item, proposed[match]):
                    errors.extend(("MODEL_TRUNCATED", "MODEL_TOKEN_LOST"))
        if match is None:
            errors.append("EQUIPMENT_ITEM_LOST")
            continue
        unmatched.remove(match)
        proposed_item = proposed[match]
        if (
            current_item.power_value
            and current_item.power_unit == "W"
            and proposed_item.power_unit is None
            and re.search(
                rf"(?<!\d){re.escape(current_item.power_value)}(?!\d)",
                proposed_item.canonical_model,
            )
        ):
            errors.append("POWER_UNIT_LOST")
        if current_item.quantity is not None and (
            proposed_item.quantity is None or proposed_item.quantity <= 0
        ):
            errors.append("QUANTITY_LOST")
        elif current_item.quantity != proposed_item.quantity and not _has_evidence(
            proposed_item.source
        ):
            errors.append("QUANTITY_CHANGED_WITHOUT_EVIDENCE")
    for index in unmatched:
        if not _has_evidence(proposed[index].source):
            errors.append("EQUIPMENT_ITEM_ADDED_WITHOUT_EVIDENCE")
    return errors, warnings


def _choose_exact_quantity_match(
    current: CanonicalEquipment,
    proposed: tuple[CanonicalEquipment, ...],
    candidates: list[int],
) -> int | None:
    if not candidates:
        return None
    same_quantity = [
        index for index in candidates if proposed[index].quantity == current.quantity
    ]
    if same_quantity:
        return min(same_quantity)
    return candidates[0] if len(candidates) == 1 else None


def _technical_identity(item: CanonicalEquipment) -> tuple[object, ...]:
    return (
        item.equipment_type,
        _key(item.canonical_manufacturer),
        _normalized_model_identity(item.canonical_model),
        item.power_value,
        item.power_unit,
        tuple(sorted(_key(value) for value in item.technical_attributes)),
    )


def _safe_partial_match(
    current: CanonicalEquipment, proposed: CanonicalEquipment
) -> bool:
    if current.equipment_type != proposed.equipment_type:
        return False
    if canonical_manufacturer(current.canonical_manufacturer) != canonical_manufacturer(
        proposed.canonical_manufacturer
    ):
        return False
    current_model = _normalized_model_identity(current.canonical_model)
    proposed_model = _normalized_model_identity(proposed.canonical_model)
    return bool(
        current_model
        and proposed_model
        and (
            current_model.startswith(proposed_model)
            or proposed_model.startswith(current_model)
            or _is_subsequence(
                _model_tokens(proposed.canonical_model),
                _model_tokens(current.canonical_model),
            )
            or _is_subsequence(
                _model_tokens(current.canonical_model),
                _model_tokens(proposed.canonical_model),
            )
            or _identity_without_w_unit(current_model)
            == _identity_without_w_unit(proposed_model)
        )
    )


def _model_tokens_lost(
    current: CanonicalEquipment, proposed: CanonicalEquipment
) -> bool:
    if _only_proven_redundant_manufacturer_tokens_removed(current, proposed):
        return False
    current_identity = _normalized_model_identity(current.canonical_model)
    proposed_identity = _normalized_model_identity(proposed.canonical_model)
    return len(proposed_identity) < len(current_identity) and _is_subsequence(
        _model_tokens(proposed.canonical_model),
        _model_tokens(current.canonical_model),
    )


def _only_proven_redundant_manufacturer_tokens_removed(
    current: CanonicalEquipment, proposed: CanonicalEquipment
) -> bool:
    if not _has_evidence(proposed.source):
        return False
    if proposed.source.origin_type.value not in _CONTAMINATION_SOURCES:
        return False
    if (
        current.equipment_type != proposed.equipment_type
        or canonical_manufacturer(current.canonical_manufacturer)
        != canonical_manufacturer(proposed.canonical_manufacturer)
        or current.quantity != proposed.quantity
        or current.power_value != proposed.power_value
        or current.power_unit != proposed.power_unit
        or set(current.technical_attributes) != set(proposed.technical_attributes)
    ):
        return False
    current_tokens = list(_model_tokens(current.canonical_model))
    proposed_tokens = list(_model_tokens(proposed.canonical_model))
    maker_tokens = list(_model_tokens(current.canonical_manufacturer))
    if not current_tokens or not proposed_tokens or not maker_tokens:
        return False
    return _remove_sequence_occurrences(current_tokens, maker_tokens) == proposed_tokens


def _remove_sequence_occurrences(
    tokens: list[str], sequence: list[str]
) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index : index + len(sequence)] == sequence:
            index += len(sequence)
            continue
        result.append(tokens[index])
        index += 1
    return result


def _identity_without_w_unit(value: str) -> str:
    return re.sub(r"(?<=\d)w(?=$|\d)", "", value)


def _model_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z]+|\d+[a-z]*", _key(value)))


def _is_subsequence(candidate: tuple[str, ...], full: tuple[str, ...]) -> bool:
    iterator = iter(full)
    return bool(candidate) and all(any(token == value for value in iterator) for token in candidate)


def _protectable_current_item(item: CanonicalEquipment) -> bool:
    return bool(
        item.canonical_manufacturer
        and item.canonical_model
        and _key(item.canonical_manufacturer) not in _GENERIC_LABELS
        and not _contains_generic_label(item.canonical_model)
        and not (
            item.equipment_type in {"inverter", "microinverter"}
            and _key(item.canonical_manufacturer) == "gokin"
        )
    )


def _deduplicate_unquantified_aliases(
    items: tuple[CanonicalEquipment, ...],
) -> tuple[CanonicalEquipment, ...]:
    result: list[CanonicalEquipment] = []
    seen_unquantified: set[tuple[object, ...]] = set()
    for item in items:
        identity = _technical_identity(item)
        if item.quantity is None and identity in seen_unquantified:
            continue
        if item.quantity is None:
            seen_unquantified.add(identity)
        result.append(item)
    return tuple(result)


def _has_evidence(source: EquipmentSource) -> bool:
    return source.origin_type.value in _PROVEN_SOURCES


def _cross_field_contamination_resolved(
    *,
    current_module: str,
    current_inverter: str,
    source: CanonicalEquipmentCollection,
) -> bool:
    if _clean(current_module):
        return False
    if not source.modules or not (
        source.conventional_inverters or source.microinverters
    ):
        return False
    current = canonical_collection_from_formatted_cells("", current_inverter)
    if current.modules or current.microinverters:
        return False
    if not current.conventional_inverters:
        return False
    return all(
        _contaminated_inverter_item_explained_by_source(item, source)
        for item in current.conventional_inverters
    )


def _contaminated_inverter_item_explained_by_source(
    item: CanonicalEquipment,
    source: CanonicalEquipmentCollection,
) -> bool:
    inverter_items = (*source.conventional_inverters, *source.microinverters)
    inverter_match = any(
        item.equipment_type == source_item.equipment_type
        and canonical_manufacturer(item.canonical_manufacturer)
        == canonical_manufacturer(source_item.canonical_manufacturer)
        and _is_subsequence(
            _model_tokens(source_item.canonical_model),
            _model_tokens(item.canonical_model),
        )
        for source_item in inverter_items
    )
    module_match = any(
        _item_tokens_present_in_model(source_item, item.canonical_model)
        for source_item in source.modules
    )
    return inverter_match and module_match


def _item_tokens_present_in_model(
    source_item: CanonicalEquipment, combined_model: str
) -> bool:
    combined_tokens = _model_tokens(combined_model)
    maker_tokens = _model_tokens(source_item.canonical_manufacturer)
    model_tokens = _model_tokens(source_item.canonical_model)
    return _is_subsequence(maker_tokens, combined_tokens) and _is_subsequence(
        model_tokens, combined_tokens
    )


def _proven_structural_contamination_removed(
    current: CanonicalEquipmentCollection,
    source: CanonicalEquipmentCollection,
) -> bool:
    if not any(
        item.source.origin_type.value in _CONTAMINATION_SOURCES
        for item in source.all_items()
    ):
        return False
    return all(
        _current_category_explained_by_source(current_items, source_items)
        for current_items, source_items in (
            (current.modules, source.modules),
            (current.conventional_inverters, source.conventional_inverters),
            (current.microinverters, source.microinverters),
        )
    )


def _current_category_explained_by_source(
    current_items: tuple[CanonicalEquipment, ...],
    source_items: tuple[CanonicalEquipment, ...],
) -> bool:
    unmatched = set(range(len(source_items)))
    for current_item in current_items:
        match = next(
            (
                index
                for index in unmatched
                if _technical_identity(current_item) == _technical_identity(source_items[index])
                or _structural_cleanup_matches(current_item, source_items[index])
            ),
            None,
        )
        if match is None:
            return False
        unmatched.remove(match)
    return True


def _structural_cleanup_matches(
    current: CanonicalEquipment, source: CanonicalEquipment
) -> bool:
    if source.source.origin_type.value not in _CONTAMINATION_SOURCES:
        return False
    if (
        current.equipment_type != source.equipment_type
        or canonical_manufacturer(current.canonical_manufacturer)
        != canonical_manufacturer(source.canonical_manufacturer)
        or current.quantity != source.quantity
        or current.power_value != source.power_value
        or current.power_unit != source.power_unit
        or set(current.technical_attributes) != set(source.technical_attributes)
    ):
        return False
    return _structurally_cleaned_model_tokens(current) == list(
        _model_tokens(source.canonical_model)
    )


def _structurally_cleaned_model_tokens(item: CanonicalEquipment) -> list[str]:
    tokens = [
        token
        for token in _model_tokens(item.raw_model or item.canonical_model)
        if token not in {_key(label) for label in _GENERIC_LABELS}
    ]
    maker_tokens = list(_model_tokens(item.canonical_manufacturer))
    if maker_tokens:
        tokens = _remove_sequence_occurrences(tokens, maker_tokens)
    return tokens


def _has_current_redundant_manufacturer_prefix(
    current: CanonicalEquipmentCollection,
    source: CanonicalEquipmentCollection,
) -> bool:
    return any(
        _source_contains_item_after_prefix_cleanup(item, source_items)
        for current_items, source_items in (
            (current.modules, source.modules),
            (current.conventional_inverters, source.conventional_inverters),
            (current.microinverters, source.microinverters),
        )
        for item in current_items
    )


def _source_contains_item_after_prefix_cleanup(
    current: CanonicalEquipment,
    source_items: tuple[CanonicalEquipment, ...],
) -> bool:
    if not _raw_model_has_redundant_manufacturer_prefix(current):
        return False
    cleaned = canonicalize_equipment(
        equipment_type=current.equipment_type,
        manufacturer=current.canonical_manufacturer,
        model=remove_redundant_manufacturer_prefix(
            current.canonical_manufacturer, current.raw_model
        ),
        quantity=current.quantity,
        source=EquipmentSource(EquipmentSourceType.STRUCTURED_CACHE),
    )
    return any(
        _technical_identity(cleaned) == _technical_identity(source_item)
        and current.quantity == source_item.quantity
        for source_item in source_items
    )


def _raw_model_has_redundant_manufacturer_prefix(item: CanonicalEquipment) -> bool:
    words = list(_model_tokens(item.raw_model))
    if not words:
        return False
    return any(
        _leading_match(words, alias)
        for alias in _manufacturer_alias_sequences(item.canonical_manufacturer)
    )


def _canonical_item_incomplete(
    item: CanonicalEquipment, *, total_quantity: int | None
) -> bool:
    return bool(
        not item.canonical_manufacturer
        or not item.canonical_model
        or (
            (item.quantity is None or item.quantity <= 0)
            and (total_quantity is None or total_quantity <= 0)
        )
        or item.source.origin_type
        in {EquipmentSourceType.UNKNOWN, EquipmentSourceType.LEGACY_CACHE}
    )


def canonical_structure_violations(
    collection: CanonicalEquipmentCollection,
) -> tuple[str, ...]:
    errors: list[str] = []
    if (
        collection.technical_format_version != TECHNICAL_FORMAT_VERSION
        or collection.equipment_rules_version != CANONICAL_RULES_VERSION
    ):
        errors.append("LEGACY_CACHE_INCOMPATIBLE")
    if any(
        _canonical_item_incomplete(
            item,
            total_quantity=total,
        )
        for items, total in (
            (collection.modules, collection.module_total_quantity),
            (
                collection.conventional_inverters,
                collection.inverter_total_quantity,
            ),
            (collection.microinverters, collection.microinverter_total_quantity),
        )
        for item in items
    ):
        errors.append("CANONICAL_STRUCTURE_INCOMPLETE")
    return tuple(dict.fromkeys(errors))


def _semantic_field_value(item: CanonicalEquipment, field_name: str) -> object:
    value = getattr(item, field_name)
    if field_name in {"canonical_manufacturer", "canonical_model"}:
        return _normalized_model_identity(str(value))
    if field_name == "technical_attributes":
        return sorted(_key(attribute) for attribute in value)
    return value


def _parse_cell(
    text: str,
    default_type: EquipmentType,
    source_type: EquipmentSourceType,
) -> tuple[CanonicalEquipment, ...]:
    items: list[CanonicalEquipment] = []
    for raw_line in re.split(r"[\r\n]+", str(text or "")):
        line = _clean(raw_line)
        if not line or _is_total_line(line):
            continue
        quantity: int | None = None
        quantity_match = re.match(r"^(\d+)\s*x\s*", line, flags=re.IGNORECASE)
        if quantity_match:
            quantity = int(quantity_match.group(1))
            line = line[quantity_match.end() :].strip()
        equipment_type = default_type
        type_match = re.match(
            r"^(microinversor|inversor)\s*(?:[|:—-]\s*)?",
            line,
            flags=re.IGNORECASE,
        )
        if type_match:
            equipment_type = (
                "microinverter"
                if _key(type_match.group(1)) == "microinversor"
                else "inverter"
            )
            line = line[type_match.end() :].strip()
        if not line:
            continue
        manufacturer, model = _split_cell_manufacturer_model(line)
        items.append(
            canonicalize_equipment(
                equipment_type=equipment_type,
                manufacturer=manufacturer,
                model=model,
                quantity=quantity,
                source=EquipmentSource(
                    origin_type=source_type,
                    section="workbook",
                    field=(
                        "module_cell" if default_type == "module" else "inverter_cell"
                    ),
                ),
            )
        )
    return tuple(items)


def _split_cell_manufacturer_model(line: str) -> tuple[str, str]:
    if "|" in line:
        manufacturer, model = (part.strip() for part in line.split("|", 1))
        return manufacturer, model
    for manufacturer in sorted(
        _KNOWN_MULTI_TOKEN_MANUFACTURERS,
        key=lambda value: len(_normalized_model_identity(value)),
        reverse=True,
    ):
        matched_model = _model_after_manufacturer_prefix(line, manufacturer)
        if matched_model is not None:
            return manufacturer, matched_model
    parts = line.split(maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _model_after_manufacturer_prefix(line: str, manufacturer: str) -> str | None:
    line_key = _normalized_model_identity(line)
    manufacturer_key = _normalized_model_identity(manufacturer)
    if not line_key.startswith(manufacturer_key):
        return None
    tokens = line.split()
    for size in range(len(tokens), 0, -1):
        prefix = " ".join(tokens[:size])
        if _normalized_model_identity(prefix) == manufacturer_key:
            model = " ".join(tokens[size:]).strip()
            return model
    return None


def _raw_proposal_violations(
    text: str, default_type: EquipmentType
) -> tuple[str, ...]:
    del default_type
    violations: list[str] = []
    for raw_line in re.split(r"[\r\n]+", str(text or "")):
        line = _clean(raw_line)
        if not line or _is_total_line(line):
            continue
        line = re.sub(r"^\d+\s*x\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(
            r"^(?:microinversor|inversor)\s*(?:[|:—-]\s*)?",
            "",
            line,
            flags=re.IGNORECASE,
        )
        if "|" in line:
            manufacturer, model = (part.strip() for part in line.split("|", 1))
        else:
            parts = line.split(maxsplit=1)
            manufacturer = parts[0]
            model = parts[1] if len(parts) > 1 else ""
        canonical = canonical_manufacturer(manufacturer)
        aliases = _manufacturer_alias_sequences(canonical)
        model_words = model.split()
        if any(_leading_match(model_words, alias) for alias in aliases):
            violations.append("DUPLICATED_MANUFACTURER_IN_MODEL")
        if any(_key(token.strip("|:;,-")) in _GENERIC_LABELS for token in model_words):
            violations.append("GENERIC_LABEL_IN_MODEL")
    return tuple(dict.fromkeys(violations))


def _items_from_generation_data(
    data: object,
    *,
    items_attr: str,
    legacy_manufacturer: str | None,
    legacy_model: str | None,
    legacy_quantity: str | None,
    total_quantity: str,
    equipment_type: EquipmentType,
    default_source: object,
) -> tuple[CanonicalEquipment, ...]:
    raw_items = [
        item
        for item in (getattr(data, items_attr, None) or ())
        if getattr(item, "manufacturer", None) or getattr(item, "model", None)
    ]
    if not raw_items and legacy_manufacturer and legacy_model:
        manufacturer = getattr(data, legacy_manufacturer, None)
        model = getattr(data, legacy_model, None)
        if manufacturer or model:
            raw_items = [
                _LegacyItem(
                    manufacturer=manufacturer,
                    model=model,
                    quantity=(getattr(data, legacy_quantity, None) if legacy_quantity else None),
                    source=default_source,
                )
            ]
    total = getattr(data, total_quantity, None)
    result: list[CanonicalEquipment] = []
    for raw in raw_items:
        quantity = getattr(raw, "quantity", None)
        if quantity is None and len(raw_items) == 1:
            quantity = total
        result.append(
            canonicalize_equipment(
                equipment_type=equipment_type,
                manufacturer=getattr(raw, "manufacturer", None),
                model=getattr(raw, "model", None),
                quantity=quantity,
                source=_source_from_parser(
                    getattr(raw, "source", None) or default_source,
                    equipment_type,
                ),
            )
        )
    return tuple(result)


@dataclass(slots=True)
class _LegacyItem:
    manufacturer: object
    model: object
    quantity: object
    source: object


def _source_from_parser(value: object, equipment_type: EquipmentType) -> EquipmentSource:
    key = _key(str(value or ""))
    if key in {"parallel_table", "parallel_positioned", "synthetic_table"}:
        origin = EquipmentSourceType.TABLE_CELL
    elif key == "table_header_contamination":
        origin = EquipmentSourceType.TABLE_HEADER_CONTAMINATION
    elif key == "field_label_contamination":
        origin = EquipmentSourceType.FIELD_LABEL_CONTAMINATION
    elif key == "section_label":
        origin = EquipmentSourceType.SECTION_LABEL
    elif key == "catalog_value":
        origin = EquipmentSourceType.CATALOG_VALUE
    elif key == "linear":
        origin = EquipmentSourceType.LINEAR_FIELD_VALUE
    elif key == "structured_cache":
        origin = EquipmentSourceType.STRUCTURED_CACHE
    else:
        origin = EquipmentSourceType.UNKNOWN
    return EquipmentSource(
        origin_type=origin,
        section="generation",
        field=f"{equipment_type}_equipment",
    )


def _item_to_dict(item: CanonicalEquipment) -> dict[str, Any]:
    result = asdict(item)
    result["source"]["origin_type"] = item.source.origin_type.value
    result["technical_attributes"] = list(item.technical_attributes)
    result["violations"] = list(item.violations)
    result["warnings"] = list(item.warnings)
    return result


def _items_from_dict(
    payload: object, expected_type: EquipmentType
) -> tuple[CanonicalEquipment, ...]:
    if not isinstance(payload, list):
        raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
    items: list[CanonicalEquipment] = []
    for raw in payload:
        if not isinstance(raw, dict) or set(raw) != _ITEM_FIELDS:
            raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
        if raw["equipment_type"] != expected_type:
            raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
        source = raw["source"]
        if not isinstance(source, dict) or set(source) != {
            "origin_type",
            "section",
            "field",
        }:
            raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
        try:
            origin_type = EquipmentSourceType(source["origin_type"])
        except (TypeError, ValueError) as exc:
            raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE") from exc
        values = {
            "canonical_manufacturer": raw["canonical_manufacturer"],
            "raw_manufacturer": raw["raw_manufacturer"],
            "canonical_model": raw["canonical_model"],
            "raw_model": raw["raw_model"],
        }
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
        item = CanonicalEquipment(
            equipment_type=expected_type,
            **values,
            quantity=_optional_int(raw["quantity"]),
            power_value=_optional_str(raw["power_value"]),
            power_unit=_optional_str(raw["power_unit"]),
            technical_attributes=_string_tuple(raw["technical_attributes"]),
            source=EquipmentSource(
                origin_type=origin_type,
                section=_source_identifier(source["section"]),
                field=_source_identifier(source["field"]),
            ),
            violations=_string_tuple(raw["violations"]),
            warnings=_string_tuple(raw["warnings"]),
        )
        if not _serialized_item_is_canonical(item):
            raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
        items.append(item)
    return tuple(items)


def _serialized_item_is_canonical(item: CanonicalEquipment) -> bool:
    recalculated = canonicalize_equipment(
        equipment_type=item.equipment_type,
        manufacturer=item.raw_manufacturer,
        model=item.raw_model,
        quantity=item.quantity,
        source=item.source,
    )
    return bool(
        item.raw_manufacturer == recalculated.raw_manufacturer
        and item.raw_model == recalculated.raw_model
        and item.canonical_manufacturer == recalculated.canonical_manufacturer
        and item.canonical_model == recalculated.canonical_model
        and item.power_value == recalculated.power_value
        and item.power_unit == recalculated.power_unit
        and item.technical_attributes == recalculated.technical_attributes
        and set(recalculated.violations).issubset(item.violations)
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
    return tuple(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
    return value


def _source_identifier(value: object) -> str | None:
    result = _optional_str(value)
    if result is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", result):
        raise ValueError("CANONICAL_STRUCTURE_INCOMPLETE")
    return result


def _format_category(
    items: tuple[CanonicalEquipment, ...],
    *,
    singular_label: str,
    plural_label: str,
    total_quantity: int | None = None,
) -> str:
    if not items:
        return ""
    lines = [
        f"{item.canonical_manufacturer} | {item.canonical_model}"
        for item in items
    ]
    quantities = [item.quantity for item in items]
    calculated_total = (
        sum(cast(int, value) for value in quantities)
        if all(quantity is not None for quantity in quantities)
        else None
    )
    total = total_quantity if total_quantity is not None else calculated_total
    if total is not None:
        label = singular_label if total == 1 else plural_label
        lines.append(f"Qtd. total: {total} {label}")
    return "\n".join(lines)


def _format_inverter_categories(
    inverters: tuple[CanonicalEquipment, ...],
    microinverters: tuple[CanonicalEquipment, ...],
    *,
    inverter_total: int | None = None,
    microinverter_total: int | None = None,
) -> str:
    if not microinverters:
        return _format_category(
            inverters,
            singular_label="inversor",
            plural_label="inversores",
            total_quantity=inverter_total,
        )
    if not inverters and len(microinverters) == 1:
        item = microinverters[0]
        text = f"{item.canonical_manufacturer} | {item.canonical_model}"
        total = microinverter_total if microinverter_total is not None else item.quantity
        if total is not None:
            label = "microinversor" if total == 1 else "microinversores"
            return f"{text}\nQtd. total: {total} {label}"
        return text
    lines = [
        f"{item.canonical_manufacturer} | {item.canonical_model}"
        for item in inverters
    ]
    lines.extend(
        f"{item.canonical_manufacturer} | {item.canonical_model}"
        for item in microinverters
    )
    parts: list[str] = []
    inverter_quantity = (
        inverter_total if inverter_total is not None else _known_total(inverters)
    )
    micro_quantity = (
        microinverter_total
        if microinverter_total is not None
        else _known_total(microinverters)
    )
    if inverter_quantity is not None:
        parts.append(
            f"{inverter_quantity} "
            f"{'inversor' if inverter_quantity == 1 else 'inversores'}"
        )
    if micro_quantity is not None:
        parts.append(
            f"{micro_quantity} "
            f"{'microinversor' if micro_quantity == 1 else 'microinversores'}"
        )
    if parts:
        lines.append(f"Qtd. total: {' + '.join(parts)}")
    return "\n".join(lines)


def _known_total(items: tuple[CanonicalEquipment, ...]) -> int | None:
    if not items or any(item.quantity is None for item in items):
        return None
    return sum(cast(int, item.quantity) for item in items)


def _equipment_type(value: object) -> EquipmentType:
    if value not in {"module", "inverter", "microinverter"}:
        raise ValueError(f"invalid equipment type: {value!r}")
    return cast(EquipmentType, value)


def _coerce_source(
    source: EquipmentSource | EquipmentSourceType | str | None,
) -> EquipmentSource:
    if isinstance(source, EquipmentSource):
        return source
    if isinstance(source, EquipmentSourceType):
        return EquipmentSource(source)
    if isinstance(source, str):
        try:
            return EquipmentSource(EquipmentSourceType(source))
        except ValueError:
            return _source_from_parser(source, "module")
    return EquipmentSource(EquipmentSourceType.UNKNOWN)


def _remove_generic_labels(model: str) -> str:
    return " ".join(
        token
        for token in model.split()
        if _key(token.strip("|:;,-")) not in _GENERIC_LABELS
    )


def _contains_generic_label(model: str) -> bool:
    return any(
        _key(token.strip("|:;,-")) in _GENERIC_LABELS for token in model.split()
    )


def _remove_manufacturer_sequences(manufacturer: str, model: str) -> str:
    words = model.split()
    aliases = _manufacturer_alias_sequences(manufacturer)
    result: list[str] = []
    index = 0
    while index < len(words):
        matched = next(
            (
                len(alias)
                for alias in aliases
                if _sequence_matches(words[index : index + len(alias)], alias)
            ),
            0,
        )
        if matched:
            index += matched
            continue
        result.append(words[index])
        index += 1
    return " ".join(result)


def _contains_manufacturer_sequence(manufacturer: str, model: str) -> bool:
    words = model.split()
    return any(
        _contains_sequence(words, alias)
        for alias in _manufacturer_alias_sequences(manufacturer)
    )


def _normalize_explicit_watts(model: str) -> str:
    return re.sub(
        r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s+(kWp|kW|Wp|W)\b",
        lambda match: f"{match.group(1)}{_canonical_power_unit(match.group(2))}",
        model,
        flags=re.IGNORECASE,
    )


def _explicit_power(model: str) -> tuple[str | None, str | None]:
    matches = list(
        re.finditer(
            r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(kWp|kW|Wp|W)\b",
            model,
            flags=re.IGNORECASE,
        )
    )
    if not matches:
        return None, None
    value = _decimal_text(matches[-1].group(1))
    return value, _canonical_power_unit(matches[-1].group(2))


def _canonical_power_unit(value: str) -> str:
    key = value.casefold()
    if key == "kwp":
        return "kWp"
    if key == "kw":
        return "kW"
    if key == "wp":
        return "Wp"
    return "W"


def _technical_attributes(model: str) -> tuple[str, ...]:
    key = _key(model).replace(" ", "-")
    values = []
    for attribute in _TECHNICAL_ATTRIBUTES:
        attribute_key = _key(attribute).replace(" ", "-")
        if re.search(rf"(?<![a-z0-9]){re.escape(attribute_key)}(?![a-z0-9])", key):
            values.append(attribute)
    values.extend(
        match.upper()
        for match in re.findall(r"(?<!\w)\d+(?:[.,]\d+)?MM\b", model, flags=re.IGNORECASE)
    )
    return tuple(dict.fromkeys(values))


def _manufacturer_alias_sequences(manufacturer: str) -> tuple[tuple[str, ...], ...]:
    if canonical_manufacturer(manufacturer) == "SOLPLANET":
        return (("solplanet",), ("aiswei",))
    tokens = tuple(_key(manufacturer).split())
    return (tokens,) if tokens else ()


def _leading_match(words: list[str], alias: tuple[str, ...]) -> int:
    return len(alias) if _sequence_matches(words[: len(alias)], alias) else 0


def _contains_sequence(words: list[str], alias: tuple[str, ...]) -> bool:
    return any(
        _sequence_matches(words[index : index + len(alias)], alias)
        for index in range(0, len(words) - len(alias) + 1)
    )


def _sequence_matches(words: list[str], alias: tuple[str, ...]) -> bool:
    return len(words) == len(alias) and tuple(
        _key(word.strip("|:;,-")) for word in words
    ) == alias


def _alias_tokens(key: str) -> bool:
    tokens = [token for token in re.split(r"[\s/-]+", key) if token]
    return bool(tokens) and set(tokens).issubset(_SOLPLANET_ALIASES)


def _is_total_line(value: str) -> bool:
    key = re.sub(r"[^a-z0-9]+", " ", _key(value)).strip()
    return key.startswith(("qtd total ", "quantidade total ", "total "))


def _normalized_model_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _key(value))


def _decimal_text(value: str) -> str:
    try:
        number = Decimal(value.replace(",", "."))
    except InvalidOperation:
        return value
    return format(number.normalize(), "f")


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" :-")


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_accents).strip().casefold()
