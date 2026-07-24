from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from automacao_gd.domain.equipment_semantics import (
    canonical_manufacturer,
    canonicalize_equipment,
)


EQUIPMENT_FORMAT_VERSION = 2

EquipmentType = Literal["module", "inverter", "microinverter"]
ViolationSeverity = Literal["error", "warning"]
_MODULE_ONLY_MANUFACTURER_KEYS = {"gokin"}
_SOLPLANET_ALIAS_TOKENS = {"solplanet", "aiswei"}


@dataclass(frozen=True)
class EquipmentItem:
    manufacturer: str | None = None
    model: str | None = None
    equipment_type: EquipmentType = "module"
    quantity: int | None = None
    source: str | None = None


@dataclass(frozen=True)
class EquipmentViolation:
    """Violation tied to the PDF section where the value originated."""

    code: str
    severity: ViolationSeverity = "error"
    blocking: bool = True
    manufacturer: str | None = None
    source_section: EquipmentType | None = None
    detail: str | None = None


@dataclass(frozen=True)
class EquipmentFormatResult:
    text: str
    warnings: list[str] = field(default_factory=list)
    format_version: int = EQUIPMENT_FORMAT_VERSION


def split_equipment_values(
    value: str | None,
    *,
    context: str,
    uppercase_manufacturers: bool = True,
) -> list[str]:
    if not value:
        return []
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if context == "manufacturer" and _is_solplanet_alias(normalized):
        return ["SOLPLANET"]
    separator_pattern = _separator_pattern(normalized, context=context)
    parts = re.split(separator_pattern, normalized)
    cleaned = [_clean_part(part) for part in parts if _clean_part(part)]
    if context == "manufacturer":
        return [
            normalize_manufacturer(part, uppercase=uppercase_manufacturers)
            for part in cleaned
        ]
    return cleaned


def normalize_manufacturer(value: str | None, *, uppercase: bool = True) -> str:
    """Retorna o nome canônico sem alterar textos de modelo."""
    canonical = canonical_manufacturer(value)
    if uppercase or canonical == "SOLPLANET":
        return canonical
    return _clean_part(value)


def is_module_only_manufacturer(value: str | None) -> bool:
    return _manufacturer_key(value) in _MODULE_ONLY_MANUFACTURER_KEYS


def pair_manufacturers_models(
    manufacturers: list[str],
    models: list[str],
    *,
    equipment_label: str,
    equipment_type: EquipmentType = "module",
) -> tuple[list[EquipmentItem], list[str]]:
    manufacturers = [normalize_manufacturer(item) for item in manufacturers if _clean_part(item)]
    models = [_clean_part(item) for item in models if _clean_part(item)]
    warnings: list[str] = []

    if not manufacturers and not models:
        return [], warnings

    if len(manufacturers) not in {0, 1, len(models)} and len(models) not in {0, 1}:
        warnings.append(
            f"Quantidade de fabricantes e modelos de {equipment_label} divergente: "
            f"{len(manufacturers)} fabricantes, {len(models)} modelos."
        )
    elif len(models) not in {0, 1, len(manufacturers)} and len(manufacturers) not in {0, 1}:
        warnings.append(
            f"Quantidade de fabricantes e modelos de {equipment_label} divergente: "
            f"{len(manufacturers)} fabricantes, {len(models)} modelos."
        )

    pairs: Iterable[tuple[str, str]]
    if len(manufacturers) == len(models):
        pairs = zip(manufacturers, models)
    elif len(manufacturers) == 1 and len(models) > 1:
        pairs = ((manufacturers[0], model) for model in models)
    elif len(models) == 1 and len(manufacturers) > 1:
        pairs = ((manufacturer, models[0]) for manufacturer in manufacturers)
    else:
        count = max(len(manufacturers), len(models))
        pairs = (
            (
                manufacturers[index] if index < len(manufacturers) else "",
                models[index] if index < len(models) else "",
            )
            for index in range(count)
        )

    items: list[EquipmentItem] = []
    skipped_module_only: list[str] = []
    for manufacturer, model in pairs:
        if equipment_type in {"inverter", "microinverter"} and is_module_only_manufacturer(
            manufacturer
        ):
            skipped_module_only.append(manufacturer)
            continue
        if manufacturer or model:
            canonical = canonicalize_equipment(
                equipment_type=equipment_type,
                manufacturer=manufacturer,
                model=model,
            )
            items.append(
                EquipmentItem(
                    manufacturer=canonical.canonical_manufacturer or None,
                    model=canonical.canonical_model or None,
                    equipment_type=equipment_type,
                )
            )
    warnings.extend(
        f"Fabricante {manufacturer} classificado como módulo fotovoltaico; "
        f"ignorado em {equipment_label}."
        for manufacturer in skipped_module_only
    )
    deduplicated: list[EquipmentItem] = []
    identities: set[tuple[str, str, str]] = set()
    for item in items:
        identity = (
            item.equipment_type,
            _manufacturer_key(item.manufacturer),
            _normalized_identity(item.model),
        )
        if identity in identities:
            continue
        identities.add(identity)
        deduplicated.append(item)
    if len(deduplicated) < len(items):
        warnings.append(
            f"Aliases duplicados de {equipment_label} foram consolidados após normalização."
        )
    return deduplicated, warnings


def format_modules_for_excel_v2(generation_data) -> EquipmentFormatResult:
    manufacturers, models, quantities = _module_sources(generation_data)
    items, warnings = pair_manufacturers_models(
        manufacturers,
        models,
        equipment_label="módulos",
        equipment_type="module",
    )
    items = _apply_item_quantities(items, quantities)
    warnings = [*_existing_warnings(generation_data), *warnings]
    total_quantity = getattr(generation_data, "module_total_quantity", None) or getattr(
        generation_data, "module_quantity", None
    )

    if len(items) <= 1:
        item = items[0] if items else EquipmentItem(equipment_type="module")
        name = _join_name(item.manufacturer, item.model)
        if not name:
            return EquipmentFormatResult("", warnings)
        if total_quantity is not None:
            return EquipmentFormatResult(f"{total_quantity}x {name}", warnings)
        return EquipmentFormatResult(name, warnings)

    lines = [_join_module_line(item) for item in items]
    if total_quantity is not None:
        lines.append(f"Qtd. total: {total_quantity} módulos")
    return EquipmentFormatResult("\n".join(line for line in lines if line), warnings)


def format_inverters_for_excel_v2(generation_data) -> EquipmentFormatResult:
    inverter_items, inverter_warnings = _equipment_items_from_generation_data(
        generation_data,
        manufacturer_attr="inverter_manufacturer",
        model_attr="inverter_model",
        items_attr="inverters",
        equipment_label="inversores",
        equipment_type="inverter",
    )
    micro_items, micro_warnings = _equipment_items_from_generation_data(
        generation_data,
        manufacturer_attr=None,
        model_attr=None,
        items_attr="microinverters",
        equipment_label="microinversores",
        equipment_type="microinverter",
    )
    warnings = [
        *_existing_warnings(generation_data),
        *inverter_warnings,
        *micro_warnings,
    ]
    inverter_quantity = getattr(generation_data, "inverter_total_quantity", None) or getattr(
        generation_data, "inverter_quantity", None
    )
    micro_quantity = getattr(generation_data, "microinverter_total_quantity", None)
    if (
        len(inverter_items) > 1
        and inverter_quantity is not None
        and not _all_source_quantities_known(generation_data, "inverters")
    ):
        warnings.append(
            "Quantidade total de inversores não pôde ser distribuída com "
            "segurança entre os modelos; conferência necessária."
        )
    if (
        len(micro_items) > 1
        and micro_quantity is not None
        and not _all_source_quantities_known(generation_data, "microinverters")
    ):
        warnings.append(
            "Quantidade total de microinversores não pôde ser distribuída com "
            "segurança entre os modelos; conferência necessária."
        )

    if not inverter_items and not micro_items:
        return EquipmentFormatResult("", warnings)

    if not inverter_items and len(micro_items) == 1:
        item = micro_items[0]
        name = _join_name(item.manufacturer, item.model)
        if micro_quantity is not None and name:
            return EquipmentFormatResult(f"{micro_quantity}x MICROINVERSOR {name}", warnings)
        return EquipmentFormatResult(f"MICROINVERSOR {name}".strip(), warnings)

    if inverter_items and not micro_items and len(inverter_items) == 1:
        item = inverter_items[0]
        name = _join_name(item.manufacturer, item.model)
        if inverter_quantity is not None and name:
            return EquipmentFormatResult(f"{inverter_quantity}x {name}", warnings)
        return EquipmentFormatResult(name, warnings)

    lines: list[str] = []
    include_type = bool(inverter_items and micro_items) or bool(micro_items)
    for item in inverter_items:
        pair = _join_pair(item.manufacturer, item.model)
        if pair:
            lines.append(f"INVERSOR — {pair}" if include_type else pair)
    for item in micro_items:
        pair = _join_pair(item.manufacturer, item.model)
        if pair:
            lines.append(f"MICROINVERSOR — {pair}")

    total = _quantity_total_text(inverter_quantity, micro_quantity)
    if total:
        lines.append(f"Qtd. total: {total}")
    return EquipmentFormatResult("\n".join(lines), warnings)


def _module_sources(generation_data) -> tuple[list[str], list[str], list[int | None]]:
    existing_items = [
        item
        for item in getattr(generation_data, "modules", []) or []
        if getattr(item, "manufacturer", None) or getattr(item, "model", None)
    ]
    raw_manufacturer = getattr(generation_data, "module_manufacturer", None)
    raw_model = getattr(generation_data, "module_model", None)
    if len(existing_items) > 1:
        return (
            [_text(getattr(item, "manufacturer", None)) for item in existing_items],
            [_text(getattr(item, "model", None)) for item in existing_items],
            [getattr(item, "quantity", None) for item in existing_items],
        )
    if _has_list_separator(raw_manufacturer) or _has_list_separator(raw_model):
        return (
            split_equipment_values(raw_manufacturer, context="manufacturer"),
            _split_models_with_expected_count(
                raw_model,
                len(split_equipment_values(raw_manufacturer, context="manufacturer")),
            ),
            [],
        )
    if existing_items:
        return (
            [_text(getattr(item, "manufacturer", None)) for item in existing_items],
            [_text(getattr(item, "model", None)) for item in existing_items],
            [getattr(item, "quantity", None) for item in existing_items],
        )
    return (
        split_equipment_values(raw_manufacturer, context="manufacturer"),
        _split_models_with_expected_count(raw_model, 1),
        [],
    )


def _equipment_items_from_generation_data(
    generation_data,
    *,
    manufacturer_attr: str | None,
    model_attr: str | None,
    items_attr: str,
    equipment_label: str,
    equipment_type: EquipmentType,
) -> tuple[list[EquipmentItem], list[str]]:
    existing_items = [
        item
        for item in getattr(generation_data, items_attr, []) or []
        if getattr(item, "manufacturer", None) or getattr(item, "model", None)
    ]
    raw_manufacturer = (
        getattr(generation_data, manufacturer_attr, None) if manufacturer_attr else None
    )
    raw_model = getattr(generation_data, model_attr, None) if model_attr else None

    if not raw_manufacturer and not raw_model and existing_items:
        manufacturers = [_text(getattr(item, "manufacturer", None)) for item in existing_items]
        models = [_text(getattr(item, "model", None)) for item in existing_items]
    else:
        manufacturers = split_equipment_values(raw_manufacturer, context="manufacturer")
        models = _split_models_with_expected_count(raw_model, len(manufacturers))
    return pair_manufacturers_models(
        manufacturers,
        models,
        equipment_label=equipment_label,
        equipment_type=equipment_type,
    )


def _split_models_with_expected_count(value: str | None, expected_count: int) -> list[str]:
    if not value:
        return []
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if _has_non_slash_list_separator(normalized):
        return split_equipment_values(normalized, context="model")
    slash_parts = [_clean_part(part) for part in normalized.split("/") if _clean_part(part)]
    if expected_count > 1 and len(slash_parts) == expected_count:
        return slash_parts
    return [_clean_part(normalized)]


def _separator_pattern(value: str, *, context: str) -> str:
    if _has_non_slash_list_separator(value):
        return r"\s*(?:\||;|\n)\s*"
    if context in {"manufacturer", "model_list"} and "/" in value:
        return r"\s*/\s*"
    if re.search(r"\S\s{3,}\S", value):
        return r"\s{3,}"
    return r"\s*(?:\||;|\n)\s*"


def _has_list_separator(value: str | None) -> bool:
    if not value:
        return False
    text = str(value)
    return _has_non_slash_list_separator(text) or "/" in text


def _has_non_slash_list_separator(value: str) -> bool:
    return bool(re.search(r"[|;\n]", value))


def _quantity_total_text(
    inverter_quantity: int | None,
    micro_quantity: int | None,
) -> str:
    parts: list[str] = []
    if inverter_quantity is not None:
        suffix = "inversor" if inverter_quantity == 1 else "inversores"
        parts.append(f"{inverter_quantity} {suffix}")
    if micro_quantity is not None:
        suffix = "microinversor" if micro_quantity == 1 else "microinversores"
        parts.append(f"{micro_quantity} {suffix}")
    return " + ".join(parts)


def _existing_warnings(generation_data) -> list[str]:
    warning = getattr(generation_data, "equipment_parse_warning", None)
    if not warning:
        return []
    return [part.strip() for part in str(warning).split(";") if part.strip()]


def _all_source_quantities_known(generation_data, items_attr: str) -> bool:
    items = [
        item
        for item in getattr(generation_data, items_attr, []) or []
        if getattr(item, "manufacturer", None) or getattr(item, "model", None)
    ]
    return bool(items) and all(
        getattr(item, "quantity", None) is not None for item in items
    )


def _join_name(manufacturer: str | None, model: str | None) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join(part for part in [manufacturer, model] if part).strip(),
    )


def _join_pair(manufacturer: str | None, model: str | None) -> str:
    manufacturer = _text(manufacturer)
    model = _text(model)
    if manufacturer and model:
        return f"{manufacturer} | {model}"
    return manufacturer or model


def _join_module_line(item: EquipmentItem) -> str:
    pair = _join_pair(item.manufacturer, item.model)
    if pair and item.quantity is not None:
        return f"{item.quantity}x {pair}"
    return pair


def _apply_item_quantities(
    items: list[EquipmentItem],
    quantities: list[int | None],
) -> list[EquipmentItem]:
    if len(items) != len(quantities):
        return items
    return [
        EquipmentItem(
            manufacturer=item.manufacturer,
            model=item.model,
            equipment_type=item.equipment_type,
            quantity=quantity,
            source=item.source,
        )
        for item, quantity in zip(items, quantities)
    ]


def _clean_part(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" :-")


def _text(value: str | None) -> str:
    return _clean_part(value)


def _manufacturer_key(value: str | None) -> str:
    return _normalized_identity(value)


def _normalized_identity(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", _clean_part(value))
    without_accents = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_accents).casefold()


def _is_solplanet_alias(value: str | None) -> bool:
    key = _normalized_identity(value)
    tokens = [token for token in re.split(r"[\s/\-]+", key) if token]
    return bool(tokens) and set(tokens).issubset(_SOLPLANET_ALIAS_TOKENS)
