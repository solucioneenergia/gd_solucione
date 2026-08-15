from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from automacao_gd.domain.equipment import EquipmentViolation
from automacao_gd.domain.equipment_semantics import CanonicalEquipmentCollection


class PortalSolicitation(BaseModel):
    protocol: str
    client_name: str | None = None
    status: str | None = None
    consumer_unit_code: str | None = None
    address: str | None = None
    entry_date: str | None = None
    completion_date: str | None = None
    page_number: int | None = None
    row_index: int | None = None
    selection_reason: str | None = None


class ModuleEquipment(BaseModel):
    manufacturer: str | None = None
    model: str | None = None
    quantity: int | None = None
    total_kwp: str | None = None
    source: str | None = None


class InverterEquipment(BaseModel):
    manufacturer: str | None = None
    model: str | None = None
    quantity: int | None = None
    total_kw: str | None = None
    equipment_type: Literal["inverter", "microinverter"] = "inverter"
    source: str | None = None


def split_equipment_field(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    separator_pattern = r"\s*(?:\||;|\n)\s*"
    if not re.search(separator_pattern, normalized) and re.search(r"\S\s{3,}\S", normalized):
        separator_pattern = r"\s{3,}"
    parts = re.split(separator_pattern, normalized)
    return [re.sub(r"\s+", " ", part).strip(" :-") for part in parts if part.strip(" :-")]


def pair_manufacturers_models(
    manufacturers: list[str],
    models: list[str],
) -> list[tuple[str, str]]:
    manufacturers = [item for item in manufacturers if item]
    models = [item for item in models if item]
    if not manufacturers and not models:
        return []
    if len(manufacturers) == len(models):
        return list(zip(manufacturers, models))
    if len(manufacturers) == 1 and len(models) > 1:
        return [(manufacturers[0], model) for model in models]
    if len(models) == 1 and len(manufacturers) > 1:
        return [(manufacturer, models[0]) for manufacturer in manufacturers]

    count = max(len(manufacturers), len(models))
    return [
        (
            manufacturers[index] if index < len(manufacturers) else "",
            models[index] if index < len(models) else "",
        )
        for index in range(count)
    ]


def format_modules_for_excel(
    modules: list[ModuleEquipment] | "GenerationData",
    total_quantity: int | None = None,
    total_kwp: str | None = None,
) -> str:
    if isinstance(modules, GenerationData):
        data = modules
        modules = data.modules or _legacy_modules(data)
        total_quantity = data.module_total_quantity or data.module_quantity
        total_kwp = data.module_total_kwp
    modules = [item for item in modules if item.manufacturer or item.model]
    if len(modules) <= 1:
        item = modules[0] if modules else ModuleEquipment()
        name = _join_equipment_name(item.manufacturer, item.model)
        if total_quantity is not None and name:
            return _clean_technical_text(f"{total_quantity}x {name}", uppercase=False)
        return _clean_technical_text(name, uppercase=False)

    lines = []
    for item in modules:
        pair = _join_equipment_pair(item.manufacturer, item.model)
        if pair:
            lines.append(pair)
    total_parts: list[str] = []
    if total_quantity is not None:
        total_parts.append(f"{total_quantity} módulos")
    if total_kwp:
        total_parts.append(f"{total_kwp} kWp")
    if total_parts:
        lines.append(f"Total: {' | '.join(total_parts)}")
    return "\n".join(lines)


def format_inverters_for_excel(
    inverters: list[InverterEquipment] | "GenerationData",
    total_quantity: int | None = None,
    total_kw: str | None = None,
) -> str:
    microinverters: list[InverterEquipment] = []
    if isinstance(inverters, GenerationData):
        data = inverters
        microinverters = data.microinverters
        inverters = data.inverters or _legacy_inverters(data)
        total_quantity = data.inverter_total_quantity or data.inverter_quantity
        total_kw = data.inverter_total_kw
        if microinverters:
            return _format_combined_inverters_for_planilha(data)
    inverters = [item for item in inverters if item.manufacturer or item.model]
    if len(inverters) <= 1:
        item = inverters[0] if inverters else InverterEquipment()
        name = _join_equipment_name(item.manufacturer, item.model)
        if total_quantity is not None and name:
            return _clean_technical_text(f"{total_quantity}x {name}", uppercase=False)
        return _clean_technical_text(name, uppercase=False)

    lines: list[str] = []
    for item in inverters:
        pair = _join_equipment_pair(item.manufacturer, item.model)
        if pair:
            lines.append(pair)
    total_parts: list[str] = []
    if total_quantity is not None:
        total_parts.append(f"{total_quantity} inversores")
    if total_kw:
        total_parts.append(f"{total_kw} kW")
    if total_parts:
        lines.append(f"Total: {' | '.join(total_parts)}")
    return "\n".join(lines)


class GenerationData(BaseModel):
    module_manufacturer: str | None = None
    module_model: str | None = None
    module_quantity: int | None = None
    module_total_kwp: str | None = None
    inverter_manufacturer: str | None = None
    inverter_model: str | None = None
    inverter_quantity: int | None = None
    inverter_total_kw: str | None = None
    modules: list[ModuleEquipment] = Field(default_factory=list)
    module_total_quantity: int | None = None
    inverters: list[InverterEquipment] = Field(default_factory=list)
    inverter_total_quantity: int | None = None
    microinverters: list[InverterEquipment] = Field(default_factory=list)
    microinverter_total_quantity: int | None = None
    microinverter_total_kw: str | None = None
    equipment_parse_warning: str | None = None
    module_source: str | None = None
    inverter_source: str | None = None
    equipment_violations: list[EquipmentViolation] = Field(default_factory=list)
    raw_text: str | None = None
    canonical_equipment: CanonicalEquipmentCollection | None = Field(
        default=None, exclude=True
    )

    def to_canonical_collection(self) -> CanonicalEquipmentCollection:
        from automacao_gd.domain.equipment_semantics import (
            canonical_collection_from_generation_data,
        )

        if self.canonical_equipment is None:
            self.canonical_equipment = canonical_collection_from_generation_data(self)
        return self.canonical_equipment

    def format_module_for_excel(self) -> str:
        modules = self.modules or _legacy_modules(self)
        if len([item for item in modules if item.manufacturer or item.model]) > 1:
            return format_modules_for_excel(
                modules,
                self.module_total_quantity or self.module_quantity,
                self.module_total_kwp,
            )

        module_name = " ".join(
            part for part in [self.module_manufacturer, self.module_model] if part
        ).strip()
        parts: list[str] = []

        if module_name:
            parts.append(module_name)
        if self.module_quantity is not None:
            suffix = "módulo" if self.module_quantity == 1 else "módulos"
            parts.append(f"{self.module_quantity} {suffix}")
        if self.module_total_kwp:
            parts.append(f"{self.module_total_kwp} kWp")

        return " | ".join(parts)

    def format_inverter_for_excel(self) -> str:
        if self.microinverters:
            return _format_combined_inverters_for_planilha(self)
        inverters = self.inverters or _legacy_inverters(self)
        if len([item for item in inverters if item.manufacturer or item.model]) > 1:
            return format_inverters_for_excel(
                inverters,
                self.inverter_total_quantity or self.inverter_quantity,
                self.inverter_total_kw,
            )

        inverter_name = " ".join(
            part for part in [self.inverter_manufacturer, self.inverter_model] if part
        ).strip()
        parts: list[str] = []

        if inverter_name:
            parts.append(inverter_name)
        if self.inverter_quantity is not None:
            suffix = "inversor" if self.inverter_quantity == 1 else "inversores"
            parts.append(f"{self.inverter_quantity} {suffix}")
        if self.inverter_total_kw:
            parts.append(f"{self.inverter_total_kw} kW")

        return " | ".join(parts)

    def format_module_for_planilha(self) -> str:
        from automacao_gd.domain.equipment import format_modules_for_excel_v2

        return format_modules_for_excel_v2(self).text

    def format_inverter_for_planilha(self) -> str:
        from automacao_gd.domain.equipment import format_inverters_for_excel_v2

        return format_inverters_for_excel_v2(self).text

    def multiple_module_models(self) -> bool:
        return len([item for item in self.modules if item.manufacturer or item.model]) > 1

    def multiple_inverter_models(self) -> bool:
        inverters = [item for item in self.inverters if item.manufacturer or item.model]
        microinverters = [
            item for item in self.microinverters if item.manufacturer or item.model
        ]
        return len(inverters) > 1 or len(microinverters) > 1 or bool(inverters and microinverters)

    def module_pairs_count(self) -> int:
        return len([item for item in self.modules if item.manufacturer or item.model])

    def inverter_pairs_count(self) -> int:
        return len(
            [item for item in [*self.inverters, *self.microinverters] if item.manufacturer or item.model]
        )


class ClientFolderMatch(BaseModel):
    protocol: str
    client_name: str
    matched_path: str | None = None
    match_type: Literal["protocol", "fuzzy_name", "pending_review", "not_found"]
    confidence: float | None = None
    reason: str
    found_by: str | None = None
    protocol_search_hit: str | None = None
    search_elapsed_seconds: float | None = None
    cache_hit: bool = False
    cache_key: str | None = None


class ArchiveResult(BaseModel):
    original_pdf_path: str
    archived_pdf_path: str
    match_type: str
    confidence: float | None = None
    success: bool
    error: str | None = None
    destination_folder: str | None = None
    reason: str | None = None
    should_create_folder: bool | None = None
    fallback_mode: str | None = None
    created_folder: bool | None = None
    legacy_gd_ignored: bool | None = None
    source_pdf_sha256: str | None = None
    archived_pdf_sha256: str | None = None


def _clean_technical_text(text: str | None, uppercase: bool = True) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", str(text))
    normalized = normalized.replace("|", " ")
    normalized = re.sub(r"\b(?:m[óo]dulos?|inversores?|inversor)\b", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if uppercase:
        normalized = normalized.upper()
    return normalized


def _join_equipment_name(manufacturer: str | None, model: str | None) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join(part for part in [manufacturer, model] if part).strip(),
    )


def _join_equipment_pair(manufacturer: str | None, model: str | None) -> str:
    manufacturer = re.sub(r"\s+", " ", str(manufacturer or "")).strip()
    model = re.sub(r"\s+", " ", str(model or "")).strip()
    if manufacturer and model:
        return f"{manufacturer} | {model}"
    return manufacturer or model


def _format_combined_inverters_for_planilha(
    data: GenerationData,
    *,
    include_power: bool = True,
) -> str:
    inverters = [item for item in (data.inverters or _legacy_inverters(data)) if item.manufacturer or item.model]
    microinverters = [item for item in data.microinverters if item.manufacturer or item.model]

    if not inverters and len(microinverters) == 1:
        item = microinverters[0]
        name = _join_equipment_name(item.manufacturer, item.model)
        quantity = data.microinverter_total_quantity or item.quantity
        if quantity is not None and name:
            return _clean_technical_text(f"{quantity}x MICROINVERSOR {name}", uppercase=False)

    if inverters and not microinverters and len(inverters) <= 1:
        quantity = data.inverter_total_quantity or data.inverter_quantity
        item = inverters[0] if inverters else InverterEquipment()
        name = _join_equipment_name(item.manufacturer, item.model)
        if quantity is not None and name:
            return _clean_technical_text(f"{quantity}x {name}", uppercase=False)
        return _clean_technical_text(name, uppercase=False)

    lines: list[str] = []
    include_type = bool(inverters and microinverters) or bool(microinverters)
    for item in inverters:
        pair = _join_equipment_pair(item.manufacturer, item.model)
        if pair:
            lines.append(f"INVERSOR: {pair}" if include_type else pair)
    for item in microinverters:
        pair = _join_equipment_pair(item.manufacturer, item.model)
        if pair:
            lines.append(f"MICROINVERSOR: {pair}")

    total_parts: list[str] = []
    quantity_part = _combined_inverter_quantity_text(data)
    total_kw = _combined_inverter_kw_text(data) if include_power else None
    if quantity_part:
        total_parts.append(quantity_part)
    if total_kw:
        total_parts.append(f"{total_kw} kW")
    if total_parts:
        lines.append(f"Total: {' | '.join(total_parts)}")
    return "\n".join(lines)


def _combined_inverter_quantity_text(data: GenerationData) -> str:
    parts: list[str] = []
    inverter_quantity = data.inverter_total_quantity or data.inverter_quantity
    micro_quantity = data.microinverter_total_quantity
    if inverter_quantity is not None:
        suffix = "inversor" if inverter_quantity == 1 else "inversores"
        parts.append(f"{inverter_quantity} {suffix}")
    if micro_quantity is not None:
        suffix = "microinversor" if micro_quantity == 1 else "microinversores"
        parts.append(f"{micro_quantity} {suffix}")
    return " + ".join(parts)


def _combined_inverter_kw_text(data: GenerationData) -> str | None:
    values = [
        _decimal_to_float(data.inverter_total_kw),
        _decimal_to_float(data.microinverter_total_kw),
    ]
    numeric_values = [value for value in values if value is not None]
    if len(numeric_values) == 2:
        return _format_decimal_pt(sum(numeric_values))
    return data.inverter_total_kw or data.microinverter_total_kw


def _decimal_to_float(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:[,.]\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def _format_decimal_pt(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _legacy_modules(data: GenerationData) -> list[ModuleEquipment]:
    if not data.module_manufacturer and not data.module_model:
        return []
    return [
        ModuleEquipment(
            manufacturer=data.module_manufacturer,
            model=data.module_model,
            quantity=data.module_quantity,
            total_kwp=data.module_total_kwp,
        )
    ]


def _legacy_inverters(data: GenerationData) -> list[InverterEquipment]:
    if not data.inverter_manufacturer and not data.inverter_model:
        return []
    return [
        InverterEquipment(
            manufacturer=data.inverter_manufacturer,
            model=data.inverter_model,
            quantity=data.inverter_quantity,
            total_kw=data.inverter_total_kw,
        )
    ]


def _ensure_module_power_suffix(model: str) -> str:
    if not model:
        return model
    if re.search(r"\b\d+(?:[,.]\d+)?\s*W\b", model, flags=re.IGNORECASE):
        return re.sub(r"\s+W\b", "W", model, flags=re.IGNORECASE)
    return re.sub(r"(?<![A-Z])\b(\d{3,4})\b$", r"\1W", model)
