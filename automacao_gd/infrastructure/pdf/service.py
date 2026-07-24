import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, cast

from automacao_gd.domain.equipment import (
    EquipmentType,
    EquipmentViolation,
    is_module_only_manufacturer,
    normalize_manufacturer,
    pair_manufacturers_models as pair_equipment_values,
    split_equipment_values,
)
from automacao_gd.domain.equipment_semantics import (
    canonical_collection_from_generation_data,
)
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.logging import logger
from automacao_gd.domain.models import (
    GenerationData,
    InverterEquipment,
    ModuleEquipment,
    split_equipment_field,
)


MIN_TEXT_LENGTH_FOR_PYMUPDF = 80


@dataclass(frozen=True)
class PositionedText:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int = 0


class QuantifiedEquipment(Protocol):
    quantity: int | None


def extract_pdf_text(pdf_path: Path) -> str:
    pdf_path = validate_pdf_input(pdf_path)
    logger.info(f"Extraindo texto do PDF: {pdf_path}")

    text = _extract_with_pymupdf(pdf_path)
    if len(text.strip()) >= MIN_TEXT_LENGTH_FOR_PYMUPDF:
        logger.debug("Texto extraído com PyMuPDF.")
        return text

    logger.warning("Texto via PyMuPDF insuficiente; tentando fallback com pdfplumber.")
    fallback_text = _extract_with_pdfplumber(pdf_path)
    if fallback_text.strip():
        logger.debug("Texto extraído com pdfplumber.")
        return fallback_text

    logger.warning("Nenhum texto útil extraído do PDF. OCR não será usado nesta etapa.")
    return text


def validate_pdf_input(pdf_path: Path) -> Path:
    """Valida tipo, tamanho e assinatura antes de abrir o documento."""
    path = Path(pdf_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"PDF não encontrado: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Extensão inválida para PDF: {path.suffix}")
    max_bytes = int(getattr(get_settings(), "MAX_PDF_SIZE_MB", 25)) * 1024 * 1024
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"PDF vazio: {path}")
    if size > max_bytes:
        raise ValueError(f"PDF excede o limite configurado de {max_bytes // (1024 * 1024)} MB.")
    with path.open("rb") as handle:
        signature = handle.read(5)
    if signature != b"%PDF-":
        raise ValueError(f"Arquivo não possui assinatura PDF válida: {path}")
    return path


def validate_generation_data(data: GenerationData) -> list[str]:
    """Retorna pendências técnicas que exigem conferência humana."""
    issues: list[str] = []
    has_microinverter = bool(data.microinverters)
    if not data.module_manufacturer and not data.modules:
        issues.append("fabricante do módulo não identificado")
    if not data.module_model and not data.modules:
        issues.append("modelo do módulo não identificado")
    if data.module_quantity is None and data.module_total_quantity is None:
        issues.append("quantidade de módulos não identificada")
    if not data.inverter_manufacturer and not data.inverters and not has_microinverter:
        issues.append("fabricante do inversor não identificado")
    if not data.inverter_model and not data.inverters and not has_microinverter:
        issues.append("modelo do inversor não identificado")
    if (
        data.inverter_quantity is None
        and data.inverter_total_quantity is None
        and data.microinverter_total_quantity is None
    ):
        issues.append("quantidade de inversores não identificada")
    return issues


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_protocol_from_pdf_text(text: str) -> str | None:
    normalized = normalize_text(text)
    title_match = re.search(
        r"OR[ÇC]AMENTO\s+DE\s+CONEX[ÃA]O\s+PARA\s+CONEX[ÃA]O\s+DE\s+MINI\s+E\s+"
        r"MICROGERA[ÇC][ÃA]O\s*-\s*(\d{6,})",
        normalized,
        flags=re.IGNORECASE,
    )
    if title_match:
        return title_match.group(1)

    fallback_match = re.search(
        r"\b(?:protocolo|solicita[çc][ãa]o)\b\D{0,30}(\d{6,})",
        normalized,
        flags=re.IGNORECASE,
    )
    return fallback_match.group(1) if fallback_match else None


def extract_client_from_pdf_text(text: str) -> str | None:
    normalized = normalize_text(text)
    match = re.search(
        r"Titular\s+da\s+UC\s*:?\s*(.+?)(?=\n|CPF|CNPJ|Código|Codigo|Endere[cç]o|$)",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    client_name = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
    return client_name or None


def extract_generation_data(
    pdf_path: Path, *, text: str | None = None
) -> GenerationData:
    extracted_text = text if text is not None else extract_pdf_text(pdf_path)
    positioned = _extract_positioned_text(pdf_path)
    if positioned:
        return parse_generation_data_from_positioned_elements(
            positioned, fallback_text=extracted_text
        )
    return parse_generation_data_from_text(extracted_text)


def parse_generation_data_from_text(text: str) -> GenerationData:
    normalized = normalize_text(text)
    lines = _useful_lines(normalized)
    parallel_data = _parse_parallel_equipment_table(lines)

    module_header_idx = _find_line_index(lines, ["fabricante", "modulo"])
    inverter_header_idx = _find_line_index_excluding(
        lines, ["fabricante", "inversor"], ["micro"]
    )
    inverter_quantity_idx = _find_line_index_excluding(
        lines, ["qtd", "inversor"], ["micro"]
    )
    microinverter_header_idx = _find_line_index(lines, ["fabricante", "micro", "inversor"])
    microinverter_quantity_idx = _find_line_index(lines, ["qtd", "micro", "inversor"])

    module_manufacturer = None
    module_model = None
    module_quantity = None
    module_total_kwp = None
    inverter_manufacturer = None
    inverter_model = None
    inverter_quantity = None
    inverter_total_kw = None
    microinverter_quantity = None
    microinverter_total_kw = None

    module_items: list[ModuleEquipment] = []
    inverter_items: list[InverterEquipment] = []
    microinverter_items: list[InverterEquipment] = []
    parse_warnings: list[str] = []
    equipment_violations: list[EquipmentViolation] = []

    module_source = "parallel_table" if parallel_data else "linear"
    inverter_source = "parallel_table" if parallel_data else "linear"
    if parallel_data:
        module_data = parallel_data["module"]
        module_manufacturer = module_data["manufacturer"]
        module_model = module_data["model"]
        module_quantity = module_data["quantity"]
        module_total_kwp = module_data["total_kwp"]
        module_items = module_data.get("modules") or []
        inverter_data = parallel_data["inverter"]
        inverter_manufacturer = inverter_data["manufacturer"]
        inverter_model = inverter_data["model"]
        inverter_quantity = inverter_data["quantity"]
        inverter_total_kw = inverter_data["total_kw"]
        inverter_items = inverter_data.get("inverters") or []
        parse_warnings.extend(parallel_data.get("warnings") or [])
        equipment_violations.extend(parallel_data.get("violations") or [])
    else:
        module_end_idx = _first_existing_index(
            inverter_header_idx,
            microinverter_header_idx,
            len(lines),
        )
        if module_header_idx is not None and module_end_idx is not None:
            module_lines = lines[module_header_idx:module_end_idx]
            module_data = _parse_module_lines(module_lines)
            module_manufacturer = module_data["manufacturer"]
            module_model = module_data["model"]
            module_quantity = module_data["quantity"]
            module_total_kwp = module_data["total_kwp"]
            module_items = module_data.get("modules") or []
            parse_warnings.extend(module_data.get("warnings") or [])
        else:
            logger.warning("Não foi possível localizar a seção de módulos no texto do PDF.")

        if inverter_header_idx is not None:
            end_idx = _first_existing_index(
                inverter_quantity_idx,
                microinverter_header_idx,
                len(lines),
            )
            inverter_lines = lines[inverter_header_idx:end_idx]
            inverter_data = _parse_inverter_identity_lines(
                inverter_lines, source_section="inverter"
            )
            inverter_manufacturer = inverter_data["manufacturer"]
            inverter_model = inverter_data["model"]
            inverter_items = inverter_data.get("inverters") or []
            parse_warnings.extend(inverter_data.get("warnings") or [])
            equipment_violations.extend(inverter_data.get("violations") or [])
        else:
            logger.warning("Não foi possível localizar a seção de inversores no texto do PDF.")

        if inverter_quantity_idx is not None:
            quantity_lines = _slice_until_next_inverter_section(lines[inverter_quantity_idx:])
            quantity_data = _parse_inverter_quantity_lines(quantity_lines)
            inverter_quantity = quantity_data["quantity"]
            inverter_total_kw = quantity_data["total_kw"]
            parse_warnings.extend(
                _assign_equipment_quantities(
                    "inversores",
                    inverter_items,
                    quantity_data.get("quantities") or [],
                    inverter_quantity,
                )
            )
        else:
            logger.warning("Não foi possível localizar quantidade/potência de inversores.")

    if microinverter_header_idx is not None:
        end_idx = microinverter_quantity_idx or len(lines)
        micro_lines = lines[microinverter_header_idx:end_idx]
        micro_data = _parse_inverter_identity_lines(
            micro_lines, source_section="microinverter"
        )
        microinverter_items = [
            item.model_copy(update={"equipment_type": "microinverter"})
            for item in micro_data.get("inverters") or []
        ]
        parse_warnings.extend(micro_data.get("warnings") or [])
        equipment_violations.extend(micro_data.get("violations") or [])

    if microinverter_quantity_idx is not None:
        quantity_lines = _slice_until_next_non_equipment_section(
            lines[microinverter_quantity_idx:]
        )
        quantity_data = _parse_microinverter_quantity_lines(quantity_lines)
        microinverter_quantity = quantity_data["quantity"]
        microinverter_total_kw = quantity_data["total_kw"]
        parse_warnings.extend(
            _assign_equipment_quantities(
                "microinversores",
                microinverter_items,
                quantity_data.get("quantities") or [],
                microinverter_quantity,
            )
        )

    data = GenerationData(
        module_manufacturer=module_manufacturer,
        module_model=module_model,
        module_quantity=module_quantity,
        module_total_kwp=module_total_kwp,
        inverter_manufacturer=inverter_manufacturer,
        inverter_model=inverter_model,
        inverter_quantity=inverter_quantity,
        inverter_total_kw=inverter_total_kw,
        modules=module_items,
        module_total_quantity=module_quantity,
        inverters=inverter_items,
        inverter_total_quantity=inverter_quantity,
        microinverters=microinverter_items,
        microinverter_total_quantity=microinverter_quantity,
        microinverter_total_kw=microinverter_total_kw,
        equipment_parse_warning="; ".join(parse_warnings) if parse_warnings else None,
        module_source=module_source,
        inverter_source=inverter_source,
        equipment_violations=equipment_violations,
        raw_text=normalized,
    )
    validation_issues = validate_generation_data(data)
    if validation_issues:
        for issue in validation_issues:
            logger.info(f"Pendência na extração técnica: {issue}")
        warnings = [item for item in [data.equipment_parse_warning, *validation_issues] if item]
        data.equipment_parse_warning = "; ".join(warnings)
    logger.info(
        "Dados de geração extraídos: "
        f"placa='{data.format_module_for_excel()}', "
        f"inversor='{data.format_inverter_for_excel()}'"
    )
    return _attach_canonical_equipment(data)


def _extract_with_pymupdf(pdf_path: Path) -> str:
    try:
        import fitz

        with fitz.open(pdf_path) as document:
            return "\n".join(page.get_text("text") for page in document)
    except Exception as exc:
        logger.exception(f"Falha ao extrair texto com PyMuPDF: {exc}")
        return ""


def _extract_positioned_text(pdf_path: Path) -> list[PositionedText]:
    """Read text blocks with coordinates; an unavailable geometry is a safe fallback."""
    if not pdf_path.exists():
        return []
    try:
        import fitz

        elements: list[PositionedText] = []
        with fitz.open(pdf_path) as document:
            for page_number, page in enumerate(document):
                grouped_words: dict[tuple[int, int], list[tuple[Any, ...]]] = {}
                for word in page.get_text("words"):
                    grouped_words.setdefault((int(word[5]), int(word[6])), []).append(word)
                for words in grouped_words.values():
                    ordered = sorted(words, key=lambda item: int(item[7]))
                    text = " ".join(str(item[4]) for item in ordered).strip()
                    if not text:
                        continue
                    elements.append(
                        PositionedText(
                            text=text,
                            x0=min(float(item[0]) for item in ordered),
                            y0=min(float(item[1]) for item in ordered),
                            x1=max(float(item[2]) for item in ordered),
                            y1=max(float(item[3]) for item in ordered),
                            page=page_number,
                        )
                    )
        return elements
    except Exception as exc:
        logger.warning(f"Geometria do PDF indisponível; usando extração linear: {exc}")
        return []


def parse_generation_data_from_positioned_elements(
    elements: list[PositionedText], *, fallback_text: str
) -> GenerationData:
    """Reconstruct the parallel equipment table using header-relative geometry."""
    fallback = parse_generation_data_from_text(fallback_text)
    page_groups: dict[int, list[PositionedText]] = {}
    for element in elements:
        page_groups.setdefault(element.page, []).append(element)

    for page_elements in page_groups.values():
        headers = _positioned_parallel_headers(page_elements)
        if headers is None:
            continue
        values = _positioned_parallel_values(page_elements, headers)
        if values is None:
            continue

        module_manufacturer = normalize_manufacturer(
            values["module_manufacturer"], uppercase=False
        )
        inverter_manufacturer = normalize_manufacturer(
            values["inverter_manufacturer"], uppercase=False
        )
        module_model = _clean_equipment_text(values["module_model"])
        inverter_model = _clean_equipment_text(values["inverter_model"])
        module_quantity = _parse_int_from_text(values["module_quantity"])
        inverter_quantity = _parse_int_from_text(values["inverter_quantity"])
        violations = list(fallback.equipment_violations)
        if is_module_only_manufacturer(inverter_manufacturer):
            violations.append(
                _module_brand_violation(inverter_manufacturer, "inverter")
            )

        module_items = _positioned_module_items(
            module_manufacturer, module_model, module_quantity
        )
        inverter_items = _positioned_inverter_items(
            inverter_manufacturer, inverter_model, inverter_quantity
        )
        fallback.module_manufacturer = module_manufacturer or None
        fallback.module_model = module_model
        fallback.module_quantity = module_quantity
        fallback.module_total_quantity = module_quantity
        fallback.module_total_kwp = _parse_decimal_from_text(values["module_power"])
        fallback.modules = module_items
        fallback.inverter_manufacturer = inverter_manufacturer or None
        fallback.inverter_model = inverter_model
        fallback.inverter_quantity = inverter_quantity
        fallback.inverter_total_quantity = inverter_quantity
        fallback.inverter_total_kw = _parse_decimal_from_text(values["inverter_power"])
        fallback.inverters = inverter_items
        fallback.module_source = "parallel_positioned"
        fallback.inverter_source = "parallel_positioned"
        fallback.equipment_violations = _unique_violations(violations)
        return _attach_canonical_equipment(fallback)
    return _attach_canonical_equipment(fallback)


def _attach_canonical_equipment(data: GenerationData) -> GenerationData:
    data.canonical_equipment = canonical_collection_from_generation_data(data)
    return data


def _positioned_parallel_headers(
    elements: list[PositionedText],
) -> dict[str, PositionedText] | None:
    definitions = {
        "inverter_manufacturer": (["fabricante", "inversor"], ["micro"]),
        "module_manufacturer": (["fabricante", "modulo"], []),
        "inverter_model": (["modelo", "inversor"], ["micro"]),
        "module_model": (["modelo", "modulo"], []),
        "inverter_quantity": (["qtd", "inversor"], ["micro"]),
        "inverter_power": (["pot", "inversor"], ["micro"]),
        "module_quantity": (["qtd", "modulo"], []),
        "module_power": (["pot", "placa"], []),
    }
    headers: dict[str, PositionedText] = {}
    for name, (required, excluded) in definitions.items():
        for element in elements:
            key = _search_key(element.text)
            if all(term in key for term in required) and not any(
                term in key for term in excluded
            ):
                headers[name] = element
                break
    return headers if len(headers) == len(definitions) else None


def _positioned_parallel_values(
    elements: list[PositionedText], headers: dict[str, PositionedText]
) -> dict[str, str] | None:
    manufacturer_names = ["inverter_manufacturer", "module_manufacturer"]
    model_names = ["inverter_model", "module_model"]
    quantity_names = [
        "inverter_quantity",
        "inverter_power",
        "module_quantity",
        "module_power",
    ]
    manufacturer_values = _values_in_positioned_band(
        elements,
        [headers[name] for name in manufacturer_names],
        max(headers[name].y1 for name in manufacturer_names),
        min(headers[name].y0 for name in model_names),
    )
    model_values = _values_in_positioned_band(
        elements,
        [headers[name] for name in model_names],
        max(headers[name].y1 for name in model_names),
        min(headers[name].y0 for name in quantity_names),
    )
    section_end = min(
        (
            element.y0
            for element in elements
            if element.y0 > max(headers[name].y1 for name in quantity_names)
            and _is_non_equipment_section_header(element.text)
        ),
        default=float("inf"),
    )
    quantity_values = _values_in_positioned_band(
        elements,
        [headers[name] for name in quantity_names],
        max(headers[name].y1 for name in quantity_names),
        section_end,
    )
    combined = {
        **dict(zip(manufacturer_names, manufacturer_values)),
        **dict(zip(model_names, model_values)),
        **dict(zip(quantity_names, quantity_values)),
    }
    if len(combined) != 8 or any(not combined.get(name) for name in combined):
        return None
    return combined


def _values_in_positioned_band(
    elements: list[PositionedText],
    headers: list[PositionedText],
    start_y: float,
    end_y: float,
) -> list[str]:
    groups: list[list[PositionedText]] = [[] for _ in headers]
    centers = [(header.x0 + header.x1) / 2 for header in headers]
    for element in elements:
        if element.y0 < start_y or element.y0 >= end_y:
            continue
        center = (element.x0 + element.x1) / 2
        index = min(range(len(centers)), key=lambda item: abs(centers[item] - center))
        groups[index].append(element)
    return [
        _join_positioned_fragments(sorted(group, key=lambda item: (item.y0, item.x0)))
        for group in groups
    ]


def _join_positioned_fragments(elements: list[PositionedText]) -> str:
    parts = [re.sub(r"\s+", " ", element.text).strip() for element in elements]
    if len(parts) == 2 and all(re.fullmatch(r"[A-Z]{2,5}", part) for part in parts):
        return "".join(parts)
    text = " ".join(parts)
    text = re.sub(r"\s+-\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_non_equipment_section_header(text: str) -> bool:
    key = _search_key(text)
    return "tipo de protecao" in key or "tipo de conexao" in key


def _positioned_module_items(
    manufacturer: str, model: str | None, quantity: int | None
) -> list[ModuleEquipment]:
    manufacturers = split_equipment_values(
        manufacturer, context="manufacturer", uppercase_manufacturers=False
    )
    models = _split_pdf_models(model, len(manufacturers))
    pairs, _ = pair_equipment_values(
        manufacturers, models, equipment_label="módulos", equipment_type="module"
    )
    items = [
        ModuleEquipment(
            manufacturer=item.manufacturer,
            model=item.model,
            quantity=quantity if len(pairs) == 1 else None,
            source="parallel_positioned",
        )
        for item in pairs
    ]
    return items


def _positioned_inverter_items(
    manufacturer: str, model: str | None, quantity: int | None
) -> list[InverterEquipment]:
    manufacturers = split_equipment_values(
        manufacturer, context="manufacturer", uppercase_manufacturers=False
    )
    models = _split_pdf_models(model, len(manufacturers))
    pairs, _ = pair_equipment_values(
        manufacturers, models, equipment_label="inversores", equipment_type="inverter"
    )
    return [
        InverterEquipment(
            manufacturer=item.manufacturer,
            model=item.model,
            quantity=quantity if len(pairs) == 1 else None,
            source="parallel_positioned",
        )
        for item in pairs
    ]


def _unique_violations(
    violations: list[EquipmentViolation],
) -> list[EquipmentViolation]:
    unique: dict[tuple[str, str | None, str | None], EquipmentViolation] = {}
    for violation in violations:
        key = (violation.code, violation.manufacturer, violation.source_section)
        unique[key] = violation
    return list(unique.values())


def _extract_with_pdfplumber(pdf_path: Path) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        logger.exception(f"Falha ao extrair texto com pdfplumber: {exc}")
        return ""


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def _search_key(text: str) -> str:
    text = _strip_accents(text).lower()
    return re.sub(r"\s+", " ", text).strip()


def _useful_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_parallel_equipment_table(lines: list[str]) -> dict | None:
    """Reconstrói colunas pela ordem relativa dos cabeçalhos da tabela."""
    manufacturer_headers = {
        "inverter_manufacturer": _find_line_index_excluding(
            lines, ["fabricante", "inversor"], ["micro"]
        ),
        "module_manufacturer": _find_line_index(lines, ["fabricante", "modulo"]),
    }
    model_headers = {
        "inverter_model": _find_line_index_excluding(
            lines, ["modelo", "inversor"], ["micro"]
        ),
        "module_model": _find_line_index(lines, ["modelo", "modulo"]),
    }
    quantity_headers = {
        "inverter_quantity": _find_line_index_excluding(
            lines, ["qtd", "inversor"], ["micro"]
        ),
        "inverter_power": _find_line_index_excluding(
            lines, ["pot", "inversor"], ["micro"]
        ),
        "module_quantity": _find_line_index(lines, ["qtd", "modulo"]),
        "module_power": _find_first_matching_index(
            lines, (["pot", "placa"], ["pot", "modulo"])
        ),
    }
    indexes = [
        *manufacturer_headers.values(),
        *model_headers.values(),
        *quantity_headers.values(),
    ]
    if any(index is None for index in indexes):
        return None
    manufacturer_positions = cast(dict[str, int], manufacturer_headers)
    model_positions = cast(dict[str, int], model_headers)
    quantity_positions = cast(dict[str, int], quantity_headers)
    if max(manufacturer_positions.values()) >= min(model_positions.values()):
        return None
    if max(model_positions.values()) >= min(quantity_positions.values()):
        return None

    manufacturer_values = _clean_generation_lines(
        lines[max(manufacturer_positions.values()) + 1 : min(model_positions.values())]
    )
    model_values = _clean_generation_lines(
        lines[max(model_positions.values()) + 1 : min(quantity_positions.values())]
    )
    numeric_values = _parallel_numeric_values(
        lines[max(quantity_positions.values()) + 1 :]
    )
    if len(manufacturer_values) < 2 or len(model_values) < 2 or len(numeric_values) < 4:
        return None

    violations: list[EquipmentViolation] = []
    manufacturers, manufacturer_ambiguous = _map_parallel_values(
        manufacturer_positions, manufacturer_values
    )
    models, model_ambiguous = _map_parallel_values(
        model_positions, model_values, allow_single_continuation=True
    )
    quantities, quantity_ambiguous = _map_parallel_values(
        quantity_positions, numeric_values
    )
    if manufacturer_ambiguous or model_ambiguous or quantity_ambiguous:
        violations.append(
            EquipmentViolation(
                code="AMBIGUOUS_PARALLEL_CARDINALITY",
                source_section="inverter",
                detail="Quantidade de valores incompatível com os cabeçalhos paralelos.",
            )
        )
    module_manufacturer = normalize_manufacturer(
        manufacturers["module_manufacturer"], uppercase=False
    )
    inverter_manufacturer = normalize_manufacturer(
        manufacturers["inverter_manufacturer"], uppercase=False
    )
    module_model = _clean_equipment_text(models["module_model"])
    inverter_model = _clean_equipment_text(models["inverter_model"])
    module_quantity = _parse_int_from_text(quantities["module_quantity"])
    inverter_quantity = _parse_int_from_text(quantities["inverter_quantity"])
    module_manufacturers = split_equipment_values(
        module_manufacturer,
        context="manufacturer",
        uppercase_manufacturers=False,
    )
    inverter_manufacturers = split_equipment_values(
        inverter_manufacturer,
        context="manufacturer",
        uppercase_manufacturers=False,
    )
    for manufacturer in inverter_manufacturers:
        if is_module_only_manufacturer(manufacturer):
            violations.append(_module_brand_violation(manufacturer, "inverter"))
    module_pairs, module_warnings = pair_equipment_values(
        module_manufacturers,
        _split_pdf_models(module_model, len(module_manufacturers)),
        equipment_label="módulos",
        equipment_type="module",
    )
    inverter_pairs, inverter_warnings = pair_equipment_values(
        inverter_manufacturers,
        _split_pdf_models(inverter_model, len(inverter_manufacturers)),
        equipment_label="inversores",
        equipment_type="inverter",
    )

    return {
        "module": {
            "manufacturer": module_manufacturer or None,
            "model": module_model,
            "quantity": module_quantity,
            "total_kwp": _parse_decimal_from_text(quantities["module_power"]),
            "modules": [
                ModuleEquipment(
                    manufacturer=item.manufacturer,
                    model=item.model,
                    quantity=module_quantity if len(module_pairs) == 1 else None,
                    source="parallel_table",
                )
                for item in module_pairs
            ],
        },
        "inverter": {
            "manufacturer": inverter_manufacturer or None,
            "model": inverter_model,
            "quantity": inverter_quantity,
            "total_kw": _parse_decimal_from_text(quantities["inverter_power"]),
            "inverters": [
                InverterEquipment(
                    manufacturer=item.manufacturer,
                    model=item.model,
                    quantity=inverter_quantity if len(inverter_pairs) == 1 else None,
                    source="parallel_table",
                )
                for item in inverter_pairs
            ],
        },
        "warnings": [*module_warnings, *inverter_warnings],
        "violations": violations,
    }


def _find_first_matching_index(
    lines: list[str], alternatives: tuple[list[str], ...]
) -> int | None:
    return _first_existing_index(
        *(_find_line_index(lines, terms) for terms in alternatives)
    )


def _map_parallel_values(
    headers: dict[str, int],
    values: list[str],
    *,
    allow_single_continuation: bool = False,
) -> tuple[dict[str, str], bool]:
    ordered_headers = sorted(headers, key=headers.__getitem__)
    expected = len(ordered_headers)
    if len(values) == expected:
        return (
            {header: values[index] for index, header in enumerate(ordered_headers)},
            False,
        )
    if allow_single_continuation and len(values) == expected + 1:
        mapped = {
            header: values[index] for index, header in enumerate(ordered_headers)
        }
        last_header = ordered_headers[-1]
        mapped[last_header] = _join_wrapped_equipment_value(
            [mapped[last_header], values[-1]]
        ) or mapped[last_header]
        ambiguous = any(
            value.lstrip().startswith(("-", "/", "|"))
            for value in values[1:]
        )
        return mapped, ambiguous

    mapped = {
        header: values[index] if index < len(values) else ""
        for index, header in enumerate(ordered_headers)
    }
    return mapped, True


def _parallel_numeric_values(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        key = _search_key(line)
        if any(
            marker in key
            for marker in ("tipo de conexao", "tipo de protecao", "fabricante", "modelo")
        ):
            break
        values.extend(re.findall(r"\b\d+(?:[,.]\d+)?\b", line))
        if len(values) >= 4:
            break
    return values


def _find_line_index(lines: list[str], terms: list[str]) -> int | None:
    normalized_terms = [_search_key(term) for term in terms]
    for index, line in enumerate(lines):
        key = _search_key(line)
        if _line_matches_terms(key, normalized_terms):
            return index
    return None


def _find_line_index_excluding(
    lines: list[str], terms: list[str], excluded_terms: list[str]
) -> int | None:
    normalized_terms = [_search_key(term) for term in terms]
    normalized_excluded = [_search_key(term) for term in excluded_terms]
    for index, line in enumerate(lines):
        key = _search_key(line)
        if any(term in key for term in normalized_excluded):
            continue
        if _line_matches_terms(key, normalized_terms):
            return index
    return None


def _first_existing_index(*indexes: int | None) -> int | None:
    existing = [index for index in indexes if index is not None]
    return min(existing) if existing else None


def _line_matches_terms(key: str, normalized_terms: list[str]) -> bool:
    return all(_term_matches_key(term, key) for term in normalized_terms)


def _term_matches_key(term: str, key: str) -> bool:
    if term in key:
        return True
    if term == "modulo":
        return bool(re.search(r"m.dulo", key))
    return False


def _is_header_like(line: str) -> bool:
    key = _search_key(line)
    header_phrases = [
        "fabricante",
        "modelo",
        "qtd",
        "pot.",
        "pot total",
        "potencia",
        "tipo de conexao",
        "tipo de protecao",
        "protecao",
        "micro-inversor",
        "micro inversor",
    ]
    return any(phrase in key for phrase in header_phrases)


def _join_data_lines(lines: list[str]) -> str:
    data_lines = [line for line in lines if not _is_header_like(line)]
    return re.sub(r"\s+", " ", " ".join(data_lines)).strip()


def _clean_generation_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        line = re.sub(r"\s+", " ", line).strip()
        if not line or _is_header_like(line) or _is_connection_type_value(line):
            continue
        cleaned.append(line)
    return cleaned


def _is_connection_type_value(line: str) -> bool:
    key = _search_key(line)
    return key in {"monofasica", "bifasica", "trifasica"}


def _parse_module_lines(lines: list[str]) -> dict:
    structured = _parse_module_structured_lines(lines)
    if structured["manufacturer"] or structured["model"]:
        return structured
    return _parse_module_row(" ".join(_clean_generation_lines(lines)))


def _parse_inverter_identity_lines(
    lines: list[str], *, source_section: EquipmentType = "inverter"
) -> dict:
    structured = _parse_inverter_structured_lines(
        lines, source_section=source_section
    )
    if structured["manufacturer"] or structured["model"] or structured.get("warnings"):
        return structured
    return _parse_inverter_identity_row(
        " ".join(_clean_generation_lines(lines)), source_section=source_section
    )


def _parse_inverter_quantity_lines(lines: list[str]) -> dict:
    quantity_raw = _extract_labeled_value(lines, ["qtd", "inversor"])
    total_raw = _extract_labeled_value(lines, ["pot", "inversor"])
    quantities = _parse_int_values(quantity_raw)
    structured = {
        "quantity": sum(quantities) if len(quantities) > 1 else (quantities[0] if quantities else None),
        "total_kw": _parse_decimal_from_text(total_raw),
        "quantities": quantities,
    }
    if structured["quantity"] is not None or structured["total_kw"] is not None:
        return structured

    cleaned = _clean_generation_lines(lines)
    result = _parse_inverter_quantity_row(" ".join(cleaned[:8]))
    if cleaned and (result["quantity"] is None or result["total_kw"] is None):
        logger.warning(
            "Não foi possível extrair quantidade/potência dos inversores "
            f"a partir das linhas: {cleaned[:8]}"
        )
    result["quantities"] = [result["quantity"]] if result["quantity"] is not None else []
    return result


def _parse_microinverter_quantity_lines(lines: list[str]) -> dict:
    quantity_raw = _extract_labeled_value(lines, ["qtd", "micro", "inversor"])
    total_raw = _extract_labeled_value(lines, ["pot", "micro", "inversor"])
    quantities = _parse_int_values(quantity_raw)
    structured = {
        "quantity": sum(quantities) if len(quantities) > 1 else (quantities[0] if quantities else None),
        "total_kw": _parse_decimal_from_text(total_raw),
        "quantities": quantities,
    }
    if structured["quantity"] is not None or structured["total_kw"] is not None:
        return structured

    cleaned = _clean_generation_lines(lines)
    result = _parse_inverter_quantity_row(" ".join(cleaned[:8]))
    result["quantities"] = [result["quantity"]] if result["quantity"] is not None else []
    return result


def _slice_until_next_inverter_section(lines: list[str]) -> list[str]:
    sliced: list[str] = []
    for line in lines:
        key = _search_key(line)
        if "fabricante" in key and "micro" in key and "inversor" in key:
            break
        if "tipo de protecao cc" in key:
            break
        sliced.append(line)
    return sliced


def _slice_until_next_non_equipment_section(lines: list[str]) -> list[str]:
    sliced: list[str] = []
    for line in lines:
        key = _search_key(line)
        if "tipo de protecao cc" in key:
            break
        sliced.append(line)
    return sliced


def _parse_module_structured_lines(lines: list[str]) -> dict:
    manufacturer_raw = _extract_labeled_value(lines, ["fabricante", "modulo"])
    model_raw = _extract_labeled_value(lines, ["modelo", "modulo"])
    quantity_raw = _extract_labeled_value(lines, ["qtd", "modulo"])
    total_raw = _extract_labeled_value(lines, ["pot", "placa"])
    if not manufacturer_raw and not model_raw:
        table_data = _parse_module_table_lines(lines)
        if table_data["manufacturer"] or table_data["model"]:
            return table_data

    manufacturers = split_equipment_values(
        manufacturer_raw,
        context="manufacturer",
        uppercase_manufacturers=False,
    )
    models = _split_pdf_models(model_raw, len(manufacturers))
    quantities = _parse_int_values(quantity_raw)
    quantity = (
        sum(quantities)
        if len(quantities) > 1
        else (quantities[0] if quantities else None)
    )
    total_kwp = _parse_decimal_from_text(total_raw)
    models = _restore_module_power_units(models, quantities, quantity, total_kwp)
    paired_items, warnings = pair_equipment_values(
        manufacturers,
        models,
        equipment_label="módulos",
        equipment_type="module",
    )
    modules = [
        ModuleEquipment(
            manufacturer=item.manufacturer,
            model=item.model,
            source=_model_source(item.manufacturer, item.model),
        )
        for item in paired_items
    ]
    warnings.extend(
        _assign_equipment_quantities("modulos", modules, quantities, quantity)
    )
    return {
        "manufacturer": " | ".join(manufacturers) or None,
        "model": " | ".join(models) or None,
        "quantity": quantity,
        "total_kwp": total_kwp,
        "modules": modules,
        "quantities": quantities,
        "warnings": warnings,
    }


def _parse_inverter_structured_lines(
    lines: list[str], *, source_section: EquipmentType = "inverter"
) -> dict:
    manufacturer_raw = _extract_labeled_value(lines, ["fabricante", "inversor"])
    model_raw = _extract_labeled_value(lines, ["modelo", "inversor"])
    if not manufacturer_raw and not model_raw:
        table_data = _parse_inverter_table_lines(
            lines, source_section=source_section
        )
        if table_data["manufacturer"] or table_data["model"]:
            return table_data

    manufacturers = split_equipment_values(
        manufacturer_raw,
        context="manufacturer",
        uppercase_manufacturers=False,
    )
    models = _split_pdf_models(model_raw, len(manufacturers))
    manufacturers, module_only_warnings, violations = (
        _filter_module_only_inverter_manufacturers(
            manufacturers, source_section=source_section
        )
    )
    paired_items, pairing_warnings = pair_equipment_values(
        manufacturers,
        models,
        equipment_label="inversores",
        equipment_type="inverter",
    )
    warnings = [
        *module_only_warnings,
        *pairing_warnings,
    ]
    inverters = [
        InverterEquipment(
            manufacturer=item.manufacturer,
            model=item.model,
            source=_model_source(item.manufacturer, item.model),
        )
        for item in paired_items
    ]

    return {
        "manufacturer": " | ".join(manufacturers) or None,
        "model": " | ".join(models) or None,
        "inverters": inverters,
        "warnings": warnings,
        "violations": violations,
    }


def _parse_module_table_lines(lines: list[str]) -> dict:
    values = _clean_generation_lines(lines)
    result: dict[str, Any] = {
        "manufacturer": None,
        "model": None,
        "quantity": None,
        "total_kwp": None,
        "modules": [],
        "quantities": [],
        "warnings": [],
    }
    if len(values) < 2:
        return result

    manufacturer_raw = values[0]
    if "|" not in " ".join(values) and len(values) == 3:
        model_quantity_match = re.search(r"(?P<model>.+?)\s+(?P<quantity>\d{1,4})$", values[1])
        if model_quantity_match and re.fullmatch(r"\d+(?:[,.]\d+)?", values[2]):
            manufacturer = normalize_manufacturer(manufacturer_raw, uppercase=False)
            model = _clean_equipment_text(model_quantity_match.group("model"))
            quantity = int(model_quantity_match.group("quantity"))
            total_kwp = _parse_decimal_from_text(values[2])
            model = _restore_module_power_unit(model, quantity, total_kwp)
            result.update(
                {
                    "manufacturer": manufacturer,
                    "model": model,
                    "quantity": quantity,
                    "total_kwp": total_kwp,
                    "modules": [
                        ModuleEquipment(
                            manufacturer=manufacturer,
                            model=model,
                            source=_model_source(manufacturer, model),
                        )
                    ] if manufacturer or model else [],
                    "quantities": [quantity],
                }
            )
            return result
        return result
    if "|" not in " ".join(values):
        return result

    numeric_tail: list[str] = []
    model_values = values[1:]
    while model_values and re.fullmatch(r"\d+(?:[,.]\d+)?", model_values[-1]):
        numeric_tail.insert(0, model_values.pop())
    if numeric_tail:
        result["quantity"] = _parse_int_from_text(numeric_tail[0])
        result["quantities"] = [result["quantity"]] if result["quantity"] is not None else []
    if len(numeric_tail) >= 2:
        result["total_kwp"] = _parse_decimal_from_text(numeric_tail[1])

    model_raw = _join_wrapped_equipment_value(model_values)
    manufacturers = split_equipment_values(
        manufacturer_raw,
        context="manufacturer",
        uppercase_manufacturers=False,
    )
    models = _split_pdf_models(model_raw, len(manufacturers))
    models = _restore_module_power_units(
        models,
        [value for value in result["quantities"] if value is not None],
        result["quantity"],
        result["total_kwp"],
    )
    paired_items, warnings = pair_equipment_values(
        manufacturers,
        models,
        equipment_label="módulos",
        equipment_type="module",
    )
    modules = [
        ModuleEquipment(
            manufacturer=item.manufacturer,
            model=item.model,
            source=_model_source(item.manufacturer, item.model),
        )
        for item in paired_items
    ]
    result.update(
        {
            "manufacturer": " | ".join(manufacturers) or None,
            "model": " | ".join(models) or None,
            "modules": modules,
            "warnings": warnings,
        }
    )
    return result


def _parse_inverter_table_lines(
    lines: list[str], *, source_section: EquipmentType = "inverter"
) -> dict:
    values = _clean_generation_lines(lines)
    result: dict[str, Any] = {
        "manufacturer": None,
        "model": None,
        "inverters": [],
        "warnings": [],
        "violations": [],
    }
    if len(values) < 2:
        return result

    manufacturer_raw = values[0]
    model_raw = _join_wrapped_equipment_value(values[1:])
    manufacturers = split_equipment_values(
        manufacturer_raw,
        context="manufacturer",
        uppercase_manufacturers=False,
    )
    models = _split_pdf_models(model_raw, len(manufacturers))
    manufacturers, module_only_warnings, violations = (
        _filter_module_only_inverter_manufacturers(
            manufacturers, source_section=source_section
        )
    )
    paired_items, pairing_warnings = pair_equipment_values(
        manufacturers,
        models,
        equipment_label="inversores",
        equipment_type="inverter",
    )
    warnings = [
        *module_only_warnings,
        *pairing_warnings,
    ]
    inverters = [
        InverterEquipment(
            manufacturer=item.manufacturer,
            model=item.model,
            source=_model_source(item.manufacturer, item.model),
        )
        for item in paired_items
    ]
    result.update(
        {
            "manufacturer": " | ".join(manufacturers) or None,
            "model": " | ".join(models) or None,
            "inverters": inverters,
            "warnings": warnings,
            "violations": violations,
        }
    )
    return result


def _extract_labeled_value(lines: list[str], terms: list[str]) -> str | None:
    normalized_terms = [_search_key(term) for term in terms]
    for index, line in enumerate(lines):
        key = _search_key(line)
        if not _line_matches_terms(key, normalized_terms):
            continue
        if ":" in line:
            value = line.split(":", 1)[1]
            values = [_clean_equipment_text(value)]
            for continuation in lines[index + 1:]:
                if _is_header_like(continuation):
                    break
                values.append(_clean_equipment_text(continuation))
        else:
            value = _remove_known_headers(line)
            values = [_clean_equipment_text(value)]
        cleaned_values = [item for item in values if item]
        if cleaned_values:
            return "\n".join(cleaned_values)
    return None


def _join_wrapped_equipment_value(values: list[str]) -> str | None:
    if not values:
        return None
    text = " ".join(value.strip() for value in values if value.strip())
    text = re.sub(r"-\s+", "-", text)
    text = re.sub(r"\|\s+", "| ", text)
    text = re.sub(r"\s+\|", " |", text)
    text = re.sub(r"\s+", " ", text).strip(" :-|")
    return text or None


def _parse_int_from_text(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\b\d+\b", text)
    return int(match.group(0)) if match else None


def _parse_int_values(text: str | None) -> list[int]:
    values = []
    for item in split_equipment_field(text):
        parsed = _parse_int_from_text(item)
        if parsed is not None:
            values.append(parsed)
    return values


def _restore_module_power_units(
    models: list[str],
    quantities: list[int],
    total_quantity: int | None,
    total_kwp: str | None,
) -> list[str]:
    if not models:
        return models
    if len(models) == 1:
        restored = _restore_module_power_unit(models[0], total_quantity, total_kwp)
        return [restored or models[0]]
    if len(models) != len(quantities):
        return models
    return [
        _restore_module_power_unit(model, quantity, None) or model
        for model, quantity in zip(models, quantities)
    ]


def _restore_module_power_unit(
    model: str | None,
    quantity: int | None,
    total_kwp: str | None,
) -> str | None:
    if not model or quantity is None or quantity <= 0 or not total_kwp:
        return model
    if re.search(r"\b\d+(?:[,.]\d+)?\s*(?:W|Wp|kW|kWp)\b", model, re.IGNORECASE):
        return model
    candidate = _last_power_like_number(model)
    if candidate is None:
        return model
    expected = _unit_power_from_total_kwp(total_kwp, quantity)
    if expected is None or abs(expected - Decimal(candidate)) > Decimal("1"):
        return model
    return re.sub(
        rf"(?<!\d){re.escape(candidate)}(?!\d)(?!\s*(?:W|Wp|kW|kWp)\b)",
        f"{candidate}W",
        model,
        count=1,
        flags=re.IGNORECASE,
    )


def _last_power_like_number(model: str) -> str | None:
    matches = list(re.finditer(r"(?<![A-Za-z0-9])(\d{3,4})(?![A-Za-z0-9])", model))
    return matches[-1].group(1) if matches else None


def _unit_power_from_total_kwp(total_kwp: str, quantity: int) -> Decimal | None:
    try:
        total = Decimal(str(total_kwp).replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None
    if total <= 0:
        return None
    return (total * Decimal(1000)) / Decimal(quantity)


def _model_source(manufacturer: str | None, model: str | None) -> str:
    if _has_structural_label_contamination(manufacturer, model):
        return "field_label_contamination"
    return "linear"


def _has_structural_label_contamination(
    manufacturer: str | None, model: str | None
) -> bool:
    if not manufacturer or not model:
        return False
    tokens = str(model).split()
    if not tokens or _search_key(tokens[0].strip("|:;,-")) not in {
        "modulo",
        "modulos",
        "inversor",
        "inversores",
        "microinversor",
        "microinversores",
    }:
        return False
    manufacturer_key = _search_key(str(manufacturer))
    model_keys = [_search_key(token.strip("|:;,-")) for token in tokens[1:]]
    return manufacturer_key in model_keys


def _split_pdf_models(value: str | None, expected_count: int) -> list[str]:
    models = split_equipment_field(value)
    if expected_count <= 1 or len(models) != 1 or not value or "/" not in value:
        return models
    slash_models = split_equipment_values(value, context="model_list")
    return slash_models if len(slash_models) == expected_count else models


def _assign_equipment_quantities(
    section: str,
    equipment: Sequence[QuantifiedEquipment],
    quantities: list[int],
    total_quantity: int | None,
) -> list[str]:
    if not equipment:
        return []
    if len(equipment) == 1:
        equipment[0].quantity = total_quantity
        return []
    if len(quantities) == len(equipment):
        for item, quantity in zip(equipment, quantities):
            item.quantity = quantity
        return []
    if len(quantities) == 1:
        return []
    return [
        f"Quantidade total de {section} não pôde ser distribuída com segurança "
        f"entre {len(equipment)} modelos; conferência necessária."
    ]


def _parse_decimal_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"\b\d+(?:[,.]\d+)?\b", text)
    return match.group(0).replace(".", ",") if match else None


def _parse_module_row(row: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "manufacturer": None,
        "model": None,
        "quantity": None,
        "total_kwp": None,
    }
    if not row:
        return result

    row = _remove_known_headers(row)
    match = re.search(r"(?P<quantity>\d{1,4})\s+(?P<total>\d+(?:[,.]\d+)?)\s*$", row)
    if not match:
        logger.warning(f"Linha de módulos não reconhecida: {row}")
        return result

    prefix = row[: match.start()].strip()
    parts = prefix.split()
    result["manufacturer"] = (
        normalize_manufacturer(parts[0], uppercase=False) if parts else None
    )
    result["model"] = _clean_equipment_text(" ".join(parts[1:])) if len(parts) > 1 else None
    result["quantity"] = int(match.group("quantity"))
    result["total_kwp"] = match.group("total").replace(".", ",")
    result["model"] = _restore_module_power_unit(
        result["model"], result["quantity"], result["total_kwp"]
    )
    result["modules"] = [
        ModuleEquipment(
            manufacturer=result["manufacturer"],
            model=result["model"],
            source=_model_source(result["manufacturer"], result["model"]),
        )
    ] if result["manufacturer"] or result["model"] else []
    result["warnings"] = []
    return result


def _parse_inverter_identity_row(
    row: str, *, source_section: EquipmentType = "inverter"
) -> dict[str, Any]:
    result: dict[str, Any] = {"manufacturer": None, "model": None}
    if not row:
        return result

    row = _remove_known_headers(row)
    parts = row.split()
    result["manufacturer"] = (
        normalize_manufacturer(parts[0], uppercase=False) if parts else None
    )
    result["model"] = _clean_equipment_text(" ".join(parts[1:])) if len(parts) > 1 else None
    if _is_module_only_inverter_manufacturer(result["manufacturer"]):
        manufacturer = result["manufacturer"]
        result.update(
            {
                "manufacturer": None,
                "model": None,
                "inverters": [],
                "warnings": [_module_only_inverter_warning(manufacturer)],
                "violations": [
                    _module_brand_violation(manufacturer, source_section)
                ],
            }
        )
        return result
    result["inverters"] = [
        InverterEquipment(
            manufacturer=result["manufacturer"],
            model=result["model"],
            source=_model_source(result["manufacturer"], result["model"]),
        )
    ] if result["manufacturer"] or result["model"] else []
    result["warnings"] = []
    result["violations"] = []
    return result


def _filter_module_only_inverter_manufacturers(
    manufacturers: list[str],
    *,
    source_section: EquipmentType = "inverter",
) -> tuple[list[str], list[str], list[EquipmentViolation]]:
    kept: list[str] = []
    warnings: list[str] = []
    violations: list[EquipmentViolation] = []
    for manufacturer in manufacturers:
        if _is_module_only_inverter_manufacturer(manufacturer):
            warnings.append(_module_only_inverter_warning(manufacturer))
            violations.append(
                _module_brand_violation(manufacturer, source_section)
            )
            continue
        kept.append(manufacturer)
    return kept, warnings, violations


def _module_brand_violation(
    manufacturer: str | None, source_section: EquipmentType
) -> EquipmentViolation:
    return EquipmentViolation(
        code="MODULE_BRAND_IN_INVERTER_SECTION",
        manufacturer=normalize_manufacturer(manufacturer),
        source_section=source_section,
        detail="Fabricante exclusivo de módulo encontrado na seção de inversor.",
    )


def _is_module_only_inverter_manufacturer(manufacturer: str | None) -> bool:
    return is_module_only_manufacturer(manufacturer)


def _module_only_inverter_warning(manufacturer: str | None) -> str:
    return (
        f"Fabricante {manufacturer} classificado como módulo fotovoltaico; "
        "ignorado em inversores."
    )


def _parse_inverter_quantity_row(row: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "quantity": None,
        "total_kw": None,
    }
    if not row:
        return result

    row = _remove_known_headers(row)
    numeric_tokens = re.findall(r"\b\d+(?:[,.]\d+)?\b", row)
    if len(numeric_tokens) >= 2:
        result["quantity"] = int(float(numeric_tokens[0].replace(",", ".")))
        result["total_kw"] = numeric_tokens[1].replace(".", ",")
        return result

    match = re.search(r"\b(?P<quantity>\d{1,4})\s+(?P<total>\d+(?:[,.]\d+)?)\b", row)
    if not match:
        logger.warning(f"Linha de quantidade dos inversores não reconhecida: {row}")
        return result

    result["quantity"] = int(match.group("quantity"))
    result["total_kw"] = match.group("total").replace(".", ",")
    return result


def _remove_known_headers(text: str) -> str:
    replacements = [
        r"Fabricante\(s\)\s+do\(s\)\s+m[óo]dulos?\(s\)",
        r"Modelo\(s\)\s+do\(s\)\s+m[óo]dulos?\(s\)",
        r"Qtd\s+m[óo]dulos",
        r"Pot\.\s*total\s+da\(s\)\s+placa\(s\)\s+\(kWp\)",
        r"Fabricante\(s\)\s+do\(s\)\s+micro-?inversor\(es\)",
        r"Modelo\(s\)\s+do\(s\)\s+micro-?inversor\(es\)",
        r"Qtd\s+micro-?inversores",
        r"Pot\.\s*total\s+do\(s\)\s+micro-?inversor\(es\)\s+\(kW\)",
        r"Fabricante\(s\)\s+do\(s\)\s+inversor\(es\)",
        r"Modelo\(s\)\s+do\(s\)\s+inversor\(es\)",
        r"Qtd\s+inversores",
        r"Pot\.\s*total\s+do\(s\)\s+inversor\(es\)\s+\(kW\)",
        r"Tipo\s+de\s+Conex[ãa]o",
    ]
    cleaned = text
    for pattern in replacements:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _clean_equipment_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _remove_known_headers(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :-|")
    return cleaned or None
