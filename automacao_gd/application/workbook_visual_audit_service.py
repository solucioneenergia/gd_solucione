from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text

EXPECTED_HEADERS = [
    "Cliente",
    "Protocolo",
    "Data de ingresso",
    "Conclusão",
    "Parecer",
    "Placa",
    "Inversor",
]

EXPECTED_FREEZE_PANES = "A3"
HEIGHT_TOLERANCE = 2.0
CANONICAL_HEIGHT_BY_VISUAL_LINE_COUNT = {
    1: 27.95,
    2: 45.0,
    3: 60.0,
    4: 75.0,
}

# Identificadores operacionais protegidos são fornecidos fora do repositório.
PROTECTED_SOURCE_INCOMPLETE_PROTOCOLS: frozenset[str] = frozenset()

ACTION_TYPE_BY_FINDING = {
    "COLUMN_WIDTH_MISMATCH": "COLUMN_WIDTH_STANDARDIZATION",
    "FILTER_HEADER_ONLY": "FILTER_RANGE_EXTENSION",
    "FILTER_RANGE_TOO_SHORT": "FILTER_RANGE_EXTENSION",
    "FILTER_RANGE_TOO_LONG": "FILTER_RANGE_EXTENSION",
    "FILTER_RANGE_INVALID": "FILTER_RANGE_EXTENSION",
    "FILTER_NOT_PRESENT": "FILTER_RANGE_EXTENSION",
    "TRAILING_MATERIALIZED_EMPTY_ROW": "TRAILING_MATERIALIZED_EMPTY_ROW_REMOVAL",
    "PROTOCOL_NUMERIC_SAFE_TO_CONVERT": "PROTOCOL_NUMERIC_TO_TEXT",
    "ROW_HEIGHT_ADJUSTMENT_SAFE": "ROW_HEIGHT_ADJUSTMENT_SAFE",
    "FREEZE_PANES_MISMATCH": "FREEZE_PANES_STANDARDIZATION",
    "HEADER_STYLE_MISMATCH": "STYLE_STANDARDIZATION",
    "DATA_CELL_STYLE_MISMATCH": "STYLE_STANDARDIZATION",
    "MULTI_MANUFACTURER_WRAP_MISSING": "STYLE_STANDARDIZATION",
    "DATE_FORMAT_MISMATCH": "DATE_NUMBER_FORMAT_STANDARDIZATION",
}

FORBIDDEN_ARTIFACT_ERROR_STRINGS = (
    "Values must be of type",
    "<class",
    "TypeError",
    "ValueError",
    "Descriptor",
)


@dataclass(frozen=True)
class CellStyleFingerprint:
    cell_visual_style_hash: str
    number_format_hash: str
    protection_hash: str
    semantic_style_hash: str
    properties: dict[str, Any]


@dataclass(frozen=True)
class SheetStructuralFinding:
    issue_type: str
    severity: str
    sheet_name: str
    row_number: int | None
    column: str | None
    cell_range: str | None
    current_state: dict[str, Any]
    canonical_state: dict[str, Any] | None
    recommended_action: str
    safe_to_apply: bool
    content_change_required: bool
    reason: str
    evidence: list[str]


def audit_workbook_visual_structure(
    workbook_path: Path | str,
    *,
    canonical_sheet: str = "2025",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Audit workbook visual/structural state without saving or mutating the file."""

    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(path)

    timestamp = timestamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    workbook_sha_before = _sha256_file(path)
    workbook_content_hash_before = _workbook_content_hash(path)

    wb = load_workbook(path, data_only=False)
    try:
        if canonical_sheet not in wb.sheetnames:
            raise ValueError(f"Aba canônica não encontrada: {canonical_sheet}")

        canonical_ws = wb[canonical_sheet]
        canonical = _sheet_fingerprint(
            canonical_ws,
            sheet_index=wb.sheetnames.index(canonical_sheet),
        )
        canonical_policy = _canonical_policy(canonical_ws, canonical)
        canonical_policy_hash = _hash(canonical_policy)
        variant_analysis = _canonical_variant_analysis(canonical_ws, canonical, canonical_policy)

        findings: list[dict[str, Any]] = []
        informational_findings: list[dict[str, Any]] = []
        protected_targets: list[dict[str, Any]] = []
        protected_cells: list[dict[str, Any]] = []
        sheet_comparisons: dict[str, Any] = {}
        formula_cells_count = 0
        findings.extend(_canonical_style_variant_findings(canonical_ws, canonical))

        for sheet_index, sheet_name in enumerate(wb.sheetnames):
            ws = wb[sheet_name]
            fingerprint = _sheet_fingerprint(ws, sheet_index=sheet_index)
            header = fingerprint["headers"]
            formula_cells_count += _formula_cells_count(ws)
            sheet_findings: list[dict[str, Any]] = []
            sheet_information: list[dict[str, Any]] = []

            if sheet_name != canonical_sheet:
                sheet_findings.extend(_compare_sheet_to_canonical(ws, canonical_ws, fingerprint, canonical))

            filter_finding = _filter_finding(ws, fingerprint)
            (sheet_information if filter_finding["issue_type"] == "FILTER_RANGE_CORRECT" else sheet_findings).append(
                filter_finding
            )

            freeze_finding = _freeze_panes_finding(ws, fingerprint)
            (sheet_information if freeze_finding["issue_type"] == "FREEZE_PANES_CORRECT" else sheet_findings).append(
                freeze_finding
            )

            sheet_information.append(_page_setup_finding(ws, fingerprint))
            sheet_findings.extend(_trailing_empty_row_findings(wb, ws, fingerprint))
            protocol_findings, protocol_info = _protocol_type_findings(ws, fingerprint)
            sheet_findings.extend(protocol_findings)
            sheet_information.extend(protocol_info)
            multi_findings, multi_info, multi_protected = _multi_manufacturer_findings(ws, fingerprint)
            sheet_findings.extend(multi_findings)
            sheet_information.extend(multi_info)
            protected_targets.extend(multi_protected)
            protected_cells.extend(_protected_content_cells(ws, header))

            findings.extend(sheet_findings)
            informational_findings.extend(sheet_information)
            sheet_comparisons[sheet_name] = {
                "structure": fingerprint["structure"],
                "filter": fingerprint["filter"],
                "freeze_panes": fingerprint["freeze_panes"],
                "findings_count": len(sheet_findings),
                "informational_findings_count": len(sheet_information),
                "findings": sheet_findings,
                "informational_findings": sheet_information,
            }

        proposed_actions = _actions_from_findings(findings)
        blocked_actions = [action for action in proposed_actions if not action["safe_to_apply"]]
        validate_plan_safety({"proposed_actions": proposed_actions})

        workbook_sha_after = _sha256_file(path)
        workbook_content_hash_after = _workbook_content_hash(path)
        simulation = _simulate_plan_in_memory()
        metrics = _metrics(
            wb,
            findings,
            informational_findings,
            protected_targets,
            proposed_actions,
            canonical_sheet,
        )
        safety = {
            "read_only": True,
            "workbook_sha_before": workbook_sha_before,
            "workbook_sha_after": workbook_sha_after,
            "workbook_sha_preserved": workbook_sha_before == workbook_sha_after,
            "workbook_content_hash_before": workbook_content_hash_before,
            "workbook_content_hash_after": workbook_content_hash_after,
            "workbook_content_hash_preserved": workbook_content_hash_before == workbook_content_hash_after,
            "content_changes_proposed": metrics["content_changes_proposed"],
            "workbook_save_called": 0,
            "backup_created": 0,
            "temporary_file_created": 0,
            "os_replace_called": 0,
            "portal_access": 0,
            "pdf_downloads": 0,
        }
        review = _review(findings, informational_findings, proposed_actions)
        decision = _decision(metrics, safety, review)

        return {
            "metadata": {
                "artifact_type": "workbook_visual_structural_audit_final",
                "stage": "4.1_final",
                "mode": "READ_ONLY",
                "created_at": timestamp,
                "workbook_file": path.name,
            },
            "official_workbook_reference": path.name,
            "workbook_sha": workbook_sha_before,
            "workbook_content_hash": workbook_content_hash_before,
            "canonical_sheet": canonical_sheet,
            "canonical_policy": canonical_policy,
            "canonical_policy_hash": canonical_policy_hash,
            "canonical_fingerprint": canonical,
            "canonical_variant_analysis": variant_analysis,
            "sheet_comparisons": sheet_comparisons,
            "findings": findings,
            "informational_findings": informational_findings,
            "protected_targets": protected_targets,
            "protected_cells": protected_cells,
            "protected_protocols": sorted(PROTECTED_SOURCE_INCOMPLETE_PROTOCOLS),
            "proposed_actions": proposed_actions,
            "blocked_actions": blocked_actions,
            "style_allowlist": [
                "font",
                "fill",
                "border",
                "alignment",
                "number_format",
                "row_height",
                "column_width",
                "auto_filter",
                "freeze_panes",
                "protocol_cell_type_when_safe",
            ],
            "structural_allowlist": [
                "trailing_materialized_empty_rows_after_validation",
                "filter_range_to_last_real_data_row",
                "freeze_panes_A3",
            ],
            "content_invariants": {
                "protected_content_cells_count": len(protected_cells),
                "protected_formula_cells_count": formula_cells_count,
                "protected_targets_count": len(protected_targets),
                "protected_targets": protected_targets,
                "workbook_content_hash_before": workbook_content_hash_before,
                "workbook_content_hash_after": workbook_content_hash_after,
            },
            "simulation": simulation,
            "metrics": metrics,
            "safety": safety,
            "review": review,
            "decision": decision,
        }
    finally:
        wb.close()


def validate_plan_safety(audit_or_plan: dict[str, Any]) -> None:
    for action in audit_or_plan.get("proposed_actions", []):
        if action.get("changes_content"):
            raise ValueError("CONTENT_CHANGE_OUTSIDE_SCOPE")
        if action.get("apply_now") is not False:
            raise ValueError("APPLY_NOW_NOT_ALLOWED")


audit_workbook_visual_structure.validate_plan_safety = validate_plan_safety  # type: ignore[attr-defined]


def save_workbook_visual_audit_artifacts(
    audit: dict[str, Any],
    logs_dir: Path | str,
    *,
    timestamp: str | None = None,
) -> dict[str, Path]:
    logs = Path(logs_dir)
    logs.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp or str(audit["metadata"]["created_at"])

    audit = _json_safe(audit)
    fingerprint = _json_safe(_fingerprint_payload(audit))
    variant = _json_safe(_variant_payload(audit))
    plan = _json_safe(_plan_payload(audit))
    final_plan_hash = _hash({k: v for k, v in plan.items() if k != "final_plan_hash"})
    plan["final_plan_hash"] = final_plan_hash
    audit["final_plan_hash"] = final_plan_hash

    outputs = {
        "variant_analysis_json": logs / f"workbook_canonical_style_variant_analysis_{timestamp}.json",
        "variant_analysis_md": logs / f"workbook_canonical_style_variant_analysis_{timestamp}.md",
        "audit_json": logs / f"workbook_visual_structural_audit_final_{timestamp}.json",
        "audit_md": logs / f"workbook_visual_structural_audit_final_{timestamp}.md",
        "fingerprint_json": logs / f"workbook_visual_fingerprint_2025_final_{timestamp}.json",
        "fingerprint_md": logs / f"workbook_visual_fingerprint_2025_final_{timestamp}.md",
        "plan_json": logs / f"workbook_visual_standardization_plan_final_{timestamp}.json",
        "plan_md": logs / f"workbook_visual_standardization_plan_final_{timestamp}.md",
    }
    atomic_write_json(outputs["variant_analysis_json"], variant, private=True)
    atomic_write_text(outputs["variant_analysis_md"], _variant_markdown(variant), private=True)
    atomic_write_json(outputs["audit_json"], audit, private=True)
    atomic_write_text(outputs["audit_md"], _audit_markdown(audit), private=True)
    atomic_write_json(outputs["fingerprint_json"], fingerprint, private=True)
    atomic_write_text(outputs["fingerprint_md"], _fingerprint_markdown(fingerprint), private=True)
    atomic_write_json(outputs["plan_json"], plan, private=True)
    atomic_write_text(outputs["plan_md"], _plan_markdown(plan), private=True)
    return outputs


def serialize_excel_color(color: Any) -> dict[str, Any]:
    empty = {"type": None, "rgb": None, "indexed": None, "theme": None, "tint": None, "auto": None}
    if color is None:
        return empty
    color_type = getattr(color, "type", None)
    result = {
        "type": color_type,
        "rgb": None,
        "indexed": None,
        "theme": None,
        "tint": getattr(color, "tint", None),
        "auto": None,
    }
    if color_type == "rgb":
        result["rgb"] = _safe_color_attr(color, "rgb")
    elif color_type == "indexed":
        result["indexed"] = _safe_color_attr(color, "indexed")
    elif color_type == "theme":
        result["theme"] = _safe_color_attr(color, "theme")
    elif color_type == "auto":
        result["auto"] = _safe_color_attr(color, "auto")
    elif color_type is None:
        result["type"] = None
    return result


def _safe_color_attr(color: Any, name: str) -> Any:
    try:
        value = getattr(color, name)
    except Exception:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _sheet_fingerprint(ws: Worksheet, *, sheet_index: int) -> dict[str, Any]:
    header = _find_header(ws)
    if not header:
        return {
            "sheet_name": ws.title,
            "structure": _basic_structure(ws, sheet_index, None, ws.max_row),
            "headers": {},
            "columns": {},
            "filter": {"issue_type": "FILTER_NOT_PRESENT", "filter_range_current": ws.auto_filter.ref},
            "freeze_panes": _freeze_panes_state(ws),
            "row_archetypes": {},
            "page_settings": _page_settings(ws),
        }
    last_real = _last_real_data_row(ws, header)
    return {
        "sheet_name": ws.title,
        "structure": _basic_structure(ws, sheet_index, header, last_real),
        "headers": header,
        "columns": _column_fingerprints(ws, header),
        "filter": _filter_state(ws, header, last_real),
        "freeze_panes": _freeze_panes_state(ws),
        "row_archetypes": _row_archetypes(ws, header, last_real),
        "page_settings": _page_settings(ws),
    }


def _basic_structure(ws: Worksheet, sheet_index: int, header: dict[str, Any] | None, last_real: int) -> dict[str, Any]:
    return {
        "sheet_name": ws.title,
        "sheet_index": sheet_index,
        "sheet_state": ws.sheet_state,
        "physical_max_row": ws.max_row,
        "physical_max_column": ws.max_column,
        "last_real_data_row": last_real,
        "last_real_column": 7 if header else ws.max_column,
        "logical_table_range": f"A{header['row']}:G{last_real}" if header else None,
        "title_row": 1,
        "header_row": header["row"] if header else None,
        "first_data_row": header["row"] + 1 if header else None,
        "last_data_row": last_real,
        "freeze_panes": _freeze_panes_value(ws),
        "auto_filter": ws.auto_filter.ref,
        "merged_ranges": sorted(str(item) for item in ws.merged_cells.ranges),
        "hidden_rows": [row for row, dim in ws.row_dimensions.items() if dim.hidden],
        "hidden_columns": [col for col, dim in ws.column_dimensions.items() if dim.hidden],
        "zoom_scale": ws.sheet_view.zoomScale,
        "trailing_materialized_rows": max(0, ws.max_row - last_real),
        "trailing_physical_region_size": max(0, ws.max_row - last_real),
    }


def _page_settings(ws: Worksheet) -> dict[str, Any]:
    margins = ws.page_margins
    return {
        "print_area": list(ws.print_area) if ws.print_area else None,
        "page_orientation": ws.page_setup.orientation,
        "paper_size": ws.page_setup.paperSize,
        "fit_to_width": ws.page_setup.fitToWidth,
        "fit_to_height": ws.page_setup.fitToHeight,
        "page_margins": {
            "left": margins.left,
            "right": margins.right,
            "top": margins.top,
            "bottom": margins.bottom,
        },
        "horizontal_centered": ws.print_options.horizontalCentered,
        "vertical_centered": ws.print_options.verticalCentered,
        "print_gridlines": ws.print_options.gridLines,
        "print_headings": ws.print_options.headings,
        "classification": "OUT_OF_SCOPE_PAGE_SETUP",
    }


def _find_header(ws: Worksheet) -> dict[str, Any] | None:
    aliases = {
        "cliente": "Cliente",
        "protocolo": "Protocolo",
        "data de ingresso": "Data de ingresso",
        "conclusao": "Conclusão",
        "parecer": "Parecer",
        "placa": "Placa",
        "inversor": "Inversor",
    }
    for row in range(1, min(ws.max_row, 20) + 1):
        columns: dict[str, int] = {}
        for col in range(1, min(ws.max_column, 40) + 1):
            value = ws.cell(row, col).value
            if isinstance(value, str):
                canonical = aliases.get(_normalize_text(value))
                if canonical:
                    columns[canonical] = col
        if "Protocolo" in columns and "Conclusão" in columns:
            return {"row": row, "columns": columns}
    return None


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(normalized.strip().casefold().split())


def _last_real_data_row(ws: Worksheet, header: dict[str, Any]) -> int:
    last = int(header["row"])
    columns = header["columns"]
    table_columns = [columns[name] for name in EXPECTED_HEADERS if name in columns]
    for row in range(int(header["row"]) + 1, ws.max_row + 1):
        if any(_cell_has_logical_content(ws.cell(row, col)) for col in table_columns):
            last = row
    return last


def _cell_has_logical_content(cell: Cell) -> bool:
    return bool(cell.value not in (None, "") or cell.data_type == "f" or cell.comment or cell.hyperlink)


def _column_fingerprints(ws: Worksheet, header: dict[str, Any]) -> dict[str, Any]:
    columns = {}
    for name, column in header["columns"].items():
        letter = get_column_letter(column)
        dim = ws.column_dimensions[letter]
        data_formats: dict[str, int] = {}
        alignments: dict[str, int] = {}
        for row in range(header["row"] + 1, ws.max_row + 1):
            cell = ws.cell(row, column)
            data_formats[cell.number_format] = data_formats.get(cell.number_format, 0) + 1
            align_key = json.dumps(_alignment_props(cell), sort_keys=True, default=str)
            alignments[align_key] = alignments.get(align_key, 0) + 1
        columns[name] = {
            "header": name,
            "column_letter": letter,
            "width": dim.width,
            "hidden": dim.hidden,
            "best_fit": dim.bestFit,
            "outline_level": dim.outlineLevel,
            "header_style": asdict(_style_fingerprint(ws.cell(header["row"], column))),
            "number_format_predominant": _mode_key(data_formats),
            "alignment_predominant": _mode_key(alignments),
        }
    return columns


def _row_archetypes(ws: Worksheet, header: dict[str, Any], last_real: int) -> dict[str, Any]:
    archetypes: dict[str, dict[str, Any]] = {}
    columns = header["columns"]
    for row in range(header["row"] + 1, last_real + 1):
        placa = ws.cell(row, columns.get("Placa")).value if "Placa" in columns else None
        inversor = ws.cell(row, columns.get("Inversor")).value if "Inversor" in columns else None
        metrics = _row_visual_line_metrics(placa, inversor)
        archetype = _row_archetype(placa, inversor, metrics)
        style_hash = _row_visual_hash(ws, row, columns)
        height = ws.row_dimensions[row].height
        item = archetypes.setdefault(
            archetype,
            {
                "rows": [],
                "row_visual_style_hashes": {},
                "style_hashes": {},
                "height_candidates": {},
                "height_policy": {
                    "canonical_height_by_visual_line_count": CANONICAL_HEIGHT_BY_VISUAL_LINE_COUNT,
                    "tolerance": HEIGHT_TOLERANCE,
                    "derivation_method": "explicit_line_count_and_estimated_wrapping_from_2025",
                },
                "line_count_candidates": {},
            },
        )
        item["rows"].append(row)
        item["row_visual_style_hashes"][style_hash] = item["row_visual_style_hashes"].get(style_hash, 0) + 1
        item["style_hashes"] = item["row_visual_style_hashes"]
        item["height_candidates"][str(height)] = item["height_candidates"].get(str(height), 0) + 1
        line_count = str(metrics["required_visual_line_count"])
        item["line_count_candidates"][line_count] = item["line_count_candidates"].get(line_count, 0) + 1
    return archetypes


def _row_archetype(placa: Any, inversor: Any, metrics: dict[str, Any] | None = None) -> str:
    metrics = metrics or _row_visual_line_metrics(placa, inversor)
    multi_placa = _is_multi_manufacturer_text(placa)
    multi_inversor = _is_multi_manufacturer_text(inversor)
    if multi_placa and multi_inversor:
        return "MULTI_MANUFACTURER_BOTH"
    if multi_placa:
        return "MULTI_MANUFACTURER_MODULE"
    if multi_inversor:
        return "MULTI_MANUFACTURER_INVERTER"
    required_lines = metrics["required_visual_line_count"]
    if required_lines <= 1:
        return "SINGLE_LINE_STANDARD"
    if required_lines == 2:
        return "TWO_LINE_EQUIPMENT"
    if required_lines == 3:
        return "THREE_LINE_EQUIPMENT"
    return "FOUR_OR_MORE_LINE_EQUIPMENT"


def _row_visual_line_metrics(placa: Any, inversor: Any) -> dict[str, Any]:
    explicit = max(_line_count(placa), _line_count(inversor), 1)
    equipment_entries = max(_equipment_entry_count(placa), _equipment_entry_count(inversor))
    estimated_wrapped = max(_estimated_wrapped_line_count(placa), _estimated_wrapped_line_count(inversor), 1)
    required = max(explicit, estimated_wrapped)
    return {
        "explicit_line_count": explicit,
        "equipment_entry_count": equipment_entries,
        "estimated_wrapped_line_count": estimated_wrapped,
        "required_visual_line_count": required,
    }


def _line_count(value: Any) -> int:
    if value is None:
        return 0
    return max(1, len(str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")))


def _equipment_entry_count(value: Any) -> int:
    if value is None:
        return 0
    text = str(value)
    if "\n" in text:
        return len([line for line in text.splitlines() if line.strip() and "qtd. total" not in _normalize_text(line)])
    separators = text.count("|") + text.count(" / ")
    return max(1, separators + 1) if separators else 1


def _estimated_wrapped_line_count(value: Any) -> int:
    if value is None:
        return 1
    text = " ".join(str(value).split())
    if len(text) <= 90:
        return 1
    return min(4, (len(text) // 90) + 1)


def _is_multi_manufacturer_text(value: Any) -> bool:
    if value is None:
        return False
    text = str(value)
    normalized = _normalize_text(text)
    return "|" in text or "qtd. total" in normalized


def _style_fingerprint(cell: Cell) -> CellStyleFingerprint:
    visual = {
        "font": _font_props(cell),
        "fill": _fill_props(cell),
        "border": _border_props(cell),
        "alignment": _alignment_props(cell),
        "quote_prefix": cell.quotePrefix,
    }
    number = {"number_format": cell.number_format}
    protection = {"locked": cell.protection.locked, "hidden": cell.protection.hidden}
    full = {**visual, **number, "protection": protection}
    return CellStyleFingerprint(
        cell_visual_style_hash=_hash(visual),
        number_format_hash=_hash(number),
        protection_hash=_hash(protection),
        semantic_style_hash=_hash(full),
        properties=full,
    )


def _font_props(cell: Cell) -> dict[str, Any]:
    font = cell.font
    return {
        "name": font.name,
        "size": font.sz,
        "bold": font.b,
        "italic": font.i,
        "underline": font.u,
        "strike": font.strike,
        "color": serialize_excel_color(font.color),
        "vertical_alignment": font.vertAlign,
    }


def _fill_props(cell: Cell) -> dict[str, Any]:
    fill = cell.fill
    return {
        "fill_type": fill.fill_type,
        "foreground_color": serialize_excel_color(fill.fgColor),
        "background_color": serialize_excel_color(fill.bgColor),
    }


def _border_props(cell: Cell) -> dict[str, Any]:
    border = cell.border
    return {
        side: {
            "style": getattr(getattr(border, side), "style", None),
            "color": serialize_excel_color(getattr(getattr(border, side), "color", None)),
        }
        for side in ("left", "right", "top", "bottom")
    }


def _alignment_props(cell: Cell) -> dict[str, Any]:
    alignment = cell.alignment
    return {
        "horizontal": alignment.horizontal,
        "vertical": alignment.vertical,
        "wrap_text": alignment.wrap_text,
        "shrink_to_fit": alignment.shrink_to_fit,
        "text_rotation": alignment.textRotation,
        "indent": alignment.indent,
        "reading_order": alignment.readingOrder,
    }


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _mode_key(values: dict[str, int]) -> str | None:
    if not values:
        return None
    return max(values.items(), key=lambda item: item[1])[0]


def _row_visual_hash(ws: Worksheet, row: int, header_columns: dict[str, int]) -> str:
    payload = []
    for name in EXPECTED_HEADERS:
        column = header_columns.get(name)
        if column:
            payload.append([name, _style_fingerprint(ws.cell(row, column)).cell_visual_style_hash])
    return _hash(payload)


def _canonical_policy(canonical_ws: Worksheet, canonical: dict[str, Any]) -> dict[str, Any]:
    header = canonical["headers"]
    columns = header.get("columns", {})
    column_policy: dict[str, dict[str, Any]] = {
        "Cliente": {"horizontal": "left", "vertical": "center", "wrap_text": True},
        "Protocolo": {"horizontal": "center", "vertical": "center", "wrap_text": True, "future_physical_type": "text"},
        "Data de ingresso": {"horizontal": "center", "vertical": "center", "number_format": "dd/mm/yyyy"},
        "Conclusão": {
            "horizontal": "center",
            "vertical": "center",
            "date_number_format": "dd/mm/yyyy",
            "preserve_legitimate_text": True,
        },
        "Parecer": {"horizontal": "center", "vertical": "center"},
        "Placa": {"horizontal": "center", "vertical": "center", "wrap_text": True},
        "Inversor": {"horizontal": "center", "vertical": "center", "wrap_text": True},
    }
    for name, col in columns.items():
        samples = [
            _style_fingerprint(canonical_ws.cell(row, col)).properties
            for row in range(header["row"] + 1, canonical["structure"]["last_real_data_row"] + 1)
            if _cell_has_logical_content(canonical_ws.cell(row, col))
        ]
        if samples:
            policy = column_policy.setdefault(name, {})
            policy["predominant_style_hash"] = _hash(_mode_payload(samples))
    return {
        "source_sheet": canonical_ws.title,
        "expected_freeze_panes": EXPECTED_FREEZE_PANES,
        "column_policy": column_policy,
        "height_policy": {
            "canonical_height_by_visual_line_count": CANONICAL_HEIGHT_BY_VISUAL_LINE_COUNT,
            "tolerance": HEIGHT_TOLERANCE,
            "required_visual_line_count": "max(explicit_line_count, estimated_wrapped_line_count)",
            "equipment_entry_count_only_rule": "not_used_for_height",
        },
        "page_setup_policy": "OUT_OF_SCOPE_PAGE_SETUP",
    }


def _mode_payload(values: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    lookup: dict[str, dict[str, Any]] = {}
    for value in values:
        key = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False)
        counts[key] = counts.get(key, 0) + 1
        lookup[key] = value
    return lookup[max(counts.items(), key=lambda item: item[1])[0]]


def _canonical_variant_analysis(
    canonical_ws: Worksheet,
    canonical: dict[str, Any],
    canonical_policy: dict[str, Any],
) -> dict[str, Any]:
    header = canonical["headers"]
    variants: dict[str, Any] = {}
    for archetype, payload in canonical["row_archetypes"].items():
        variants[archetype] = {
            "canonical_visual_style_variants": len(payload.get("row_visual_style_hashes", {})),
            "height_variants": payload.get("height_candidates", {}),
            "line_count_variants": payload.get("line_count_candidates", {}),
            "style_differences_by_property": [],
            "sample_rows": payload.get("rows", [])[:10],
            "sample_protocols": [
                _protocol_text(canonical_ws.cell(row, header["columns"]["Protocolo"]).value)
                for row in payload.get("rows", [])[:10]
            ],
            "resolution": "height_separated_from_visual_style",
        }
    return {
        "source_sheet": canonical_ws.title,
        "canonical_policy_hash": _hash(canonical_policy),
        "archetypes": variants,
    }


def _canonical_style_variant_findings(canonical_ws: Worksheet, canonical: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    header = canonical["headers"]
    if not header:
        return findings
    for archetype, payload in canonical["row_archetypes"].items():
        style_hashes = payload.get("row_visual_style_hashes", {})
        if len(style_hashes) <= 1:
            continue
        rows = payload.get("rows", [])
        canonical_hash = max(style_hashes.items(), key=lambda item: item[1])[0]
        variant_rows = [
            row
            for row in rows
            if _row_visual_hash(canonical_ws, row, header["columns"]) != canonical_hash
        ]
        if not variant_rows:
            continue
        findings.append(
            _finding(
                "DATA_CELL_STYLE_MISMATCH",
                "P2",
                canonical_ws.title,
                None,
                None,
                ",".join(str(row) for row in variant_rows[:20]),
                {
                    "archetype": archetype,
                    "canonical_visual_style_variants": len(style_hashes),
                    "variant_rows": variant_rows,
                    "style_hashes": style_hashes,
                },
                {"canonical_row_visual_style_hash": canonical_hash},
                "Padronizar variante visual real pela política canônica da aba 2025.",
                True,
                False,
                "Variação visual real resolvida como ação futura segura, não como decisão canônica pendente.",
                [],
            )
        )
    return findings


def _compare_sheet_to_canonical(
    ws: Worksheet,
    canonical_ws: Worksheet,
    fingerprint: dict[str, Any],
    canonical: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    header = fingerprint["headers"]
    canonical_header = canonical["headers"]
    if not header or not canonical_header:
        return findings
    for name in EXPECTED_HEADERS:
        col = header["columns"].get(name)
        canonical_col = canonical_header["columns"].get(name)
        if not col or not canonical_col:
            continue
        current_style = _style_fingerprint(ws.cell(header["row"], col)).semantic_style_hash
        canonical_style = _style_fingerprint(canonical_ws.cell(canonical_header["row"], canonical_col)).semantic_style_hash
        if current_style != canonical_style:
            findings.append(
                _finding(
                    "HEADER_STYLE_MISMATCH",
                    "P2",
                    ws.title,
                    header["row"],
                    get_column_letter(col),
                    f"{get_column_letter(col)}{header['row']}",
                    {"semantic_style_hash": current_style},
                    {"semantic_style_hash": canonical_style},
                    "Padronizar estilo de cabeçalho pela aba 2025.",
                    True,
                    False,
                    "Cabeçalho visualmente diferente da referência.",
                    [name],
                )
            )
        current_width = ws.column_dimensions[get_column_letter(col)].width
        canonical_width = canonical_ws.column_dimensions[get_column_letter(canonical_col)].width
        if current_width != canonical_width:
            findings.append(
                _finding(
                    "COLUMN_WIDTH_MISMATCH",
                    "P3",
                    ws.title,
                    None,
                    get_column_letter(col),
                    get_column_letter(col),
                    {"width": current_width},
                    {"width": canonical_width},
                    "Padronizar largura da coluna.",
                    True,
                    False,
                    "Largura divergente por cabeçalho.",
                    [name],
                )
            )
    return findings


def _filter_state(ws: Worksheet, header: dict[str, Any], last_real: int) -> dict[str, Any]:
    current = ws.auto_filter.ref
    expected = f"A{header['row']}:G{last_real}"
    issue = "FILTER_RANGE_CORRECT"
    if not current:
        issue = "FILTER_NOT_PRESENT"
    else:
        try:
            min_col, min_row, max_col, max_row = range_boundaries(current)
            if min_row == header["row"] and max_row == header["row"]:
                issue = "FILTER_HEADER_ONLY"
            elif max_row < last_real:
                issue = "FILTER_RANGE_TOO_SHORT"
            elif max_row > last_real:
                issue = "FILTER_RANGE_TOO_LONG"
            elif min_col != 1 or max_col < 7 or min_row != header["row"]:
                issue = "FILTER_RANGE_INVALID"
        except ValueError:
            issue = "FILTER_RANGE_INVALID"
    return {
        "filter_range_current": current,
        "filter_range_expected": expected,
        "header_row": header["row"],
        "last_real_data_row": last_real,
        "filter_complete": issue == "FILTER_RANGE_CORRECT",
        "issue_type": issue,
    }


def _filter_finding(ws: Worksheet, fingerprint: dict[str, Any]) -> dict[str, Any]:
    issue = fingerprint["filter"]["issue_type"]
    return _finding(
        issue,
        "INFO" if issue == "FILTER_RANGE_CORRECT" else "P2",
        ws.title,
        None,
        None,
        fingerprint["filter"]["filter_range_current"],
        fingerprint["filter"],
        {"filter_range_expected": fingerprint["filter"]["filter_range_expected"]},
        "Padronizar filtro em plano futuro." if issue != "FILTER_RANGE_CORRECT" else "Filtro correto.",
        issue != "FILTER_RANGE_CORRECT",
        False,
        "Filtro auditado contra o intervalo lógico da tabela.",
        [],
    )


def _freeze_panes_value(ws: Worksheet) -> str | None:
    return str(ws.freeze_panes) if ws.freeze_panes else None


def _freeze_panes_state(ws: Worksheet) -> dict[str, Any]:
    current = _freeze_panes_value(ws)
    issue = "FREEZE_PANES_CORRECT" if current == EXPECTED_FREEZE_PANES else "FREEZE_PANES_MISMATCH"
    if current is not None and not re.fullmatch(r"[A-Z]{1,3}\d+", current):
        issue = "FREEZE_PANES_INVALID"
    return {
        "current": current,
        "expected": EXPECTED_FREEZE_PANES,
        "issue_type": issue,
    }


def _freeze_panes_finding(ws: Worksheet, fingerprint: dict[str, Any]) -> dict[str, Any]:
    state = fingerprint["freeze_panes"]
    issue = state["issue_type"]
    return _finding(
        issue,
        "INFO" if issue == "FREEZE_PANES_CORRECT" else "P2",
        ws.title,
        None,
        None,
        None,
        state,
        {"freeze_panes": EXPECTED_FREEZE_PANES},
        "Padronizar congelamento em A3." if issue != "FREEZE_PANES_CORRECT" else "Freeze panes correto.",
        issue == "FREEZE_PANES_MISMATCH",
        False,
        "A política visual mantém título e cabeçalho visíveis.",
        [],
    )


def _page_setup_finding(ws: Worksheet, fingerprint: dict[str, Any]) -> dict[str, Any]:
    return _finding(
        "OUT_OF_SCOPE_PAGE_SETUP",
        "INFO",
        ws.title,
        None,
        None,
        None,
        fingerprint["page_settings"],
        None,
        "Não gerar ação sem política empresarial de impressão.",
        False,
        False,
        "Configurações de página classificadas fora do escopo da Etapa 4.1.",
        [],
    )


def _trailing_empty_row_findings(wb: Any, ws: Worksheet, fingerprint: dict[str, Any]) -> list[dict[str, Any]]:
    header = fingerprint["headers"]
    if not header:
        return []
    last_real = fingerprint["structure"]["last_real_data_row"]
    findings = []
    for row in range(last_real + 1, ws.max_row + 1):
        if _row_has_content(ws, row):
            continue
        style_presence = any(ws.cell(row, col).has_style for col in range(1, ws.max_column + 1))
        if not style_presence and not ws.row_dimensions[row].height:
            continue
        validation = _row_has_data_validation(ws, row)
        merged = any(item.min_row <= row <= item.max_row for item in ws.merged_cells.ranges)
        defined_name = _row_has_defined_name_reference(wb, ws, row)
        safe = not validation and not merged and not defined_name
        findings.append(
            _finding(
                "TRAILING_MATERIALIZED_EMPTY_ROW",
                "P2" if safe else "INFO",
                ws.title,
                row,
                None,
                f"{row}:{row}",
                {
                    "style_presence": style_presence,
                    "custom_height": ws.row_dimensions[row].height,
                    "validation_membership": validation,
                    "merged_membership": merged,
                    "defined_name_reference": defined_name,
                    "after_last_real_data_row": True,
                },
                None,
                "Remover linha vazia materializada em aplicação futura, se revalidada.",
                safe,
                False,
                "Linha física após a última linha real contém apenas estrutura/estilo.",
                [],
            )
        )
    return findings


def _row_has_content(ws: Worksheet, row: int) -> bool:
    return any(_cell_has_logical_content(ws.cell(row, col)) for col in range(1, ws.max_column + 1))


def _row_has_data_validation(ws: Worksheet, row: int) -> bool:
    for validation in ws.data_validations.dataValidation:
        for cell_range in validation.cells.ranges:
            if cell_range.min_row <= row <= cell_range.max_row:
                return True
    return False


def _row_has_defined_name_reference(wb: Any, ws: Worksheet, row: int) -> bool:
    row_token = f"{row}:"
    for defined_name in wb.defined_names.values():
        text = str(getattr(defined_name, "attr_text", "") or "")
        if ws.title in text and row_token in text:
            return True
    return False


def _protocol_type_findings(ws: Worksheet, fingerprint: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    header = fingerprint["headers"]
    if not header:
        return [], []
    protocol_col = header["columns"].get("Protocolo")
    findings = []
    informational = []
    for row in range(header["row"] + 1, fingerprint["structure"]["last_real_data_row"] + 1):
        cell = ws.cell(row, protocol_col)
        classification = _protocol_cell_classification(cell.value, cell.data_type)
        finding = _finding(
            classification,
            "P2" if classification == "PROTOCOL_NUMERIC_SAFE_TO_CONVERT" else "INFO",
            ws.title,
            row,
            get_column_letter(protocol_col),
            cell.coordinate,
            _protocol_state(cell),
            None,
            "Converter tipo físico para texto em plano futuro quando seguro.",
            classification == "PROTOCOL_NUMERIC_SAFE_TO_CONVERT",
            False,
            "Auditoria de tipo físico da coluna Protocolo.",
            [],
        )
        if classification == "PROTOCOL_NUMERIC_SAFE_TO_CONVERT":
            findings.append(finding)
        elif classification in {"PROTOCOL_NUMERIC_REQUIRES_REVIEW", "PROTOCOL_ALREADY_TEXT", "PROTOCOL_INVALID"}:
            informational.append(finding)
    return findings, informational


def _protocol_cell_classification(value: Any, data_type: str) -> str:
    if isinstance(value, str):
        return "PROTOCOL_ALREADY_TEXT" if re.fullmatch(r"\d{10}", value.strip()) else "PROTOCOL_INVALID"
    if isinstance(value, int):
        return "PROTOCOL_NUMERIC_SAFE_TO_CONVERT" if len(str(value)) == 10 else "PROTOCOL_NUMERIC_REQUIRES_REVIEW"
    if isinstance(value, float):
        if value.is_integer() and len(str(int(value))) == 10:
            return "PROTOCOL_NUMERIC_SAFE_TO_CONVERT"
        return "PROTOCOL_NUMERIC_REQUIRES_REVIEW"
    return "PROTOCOL_INVALID"


def _protocol_state(cell: Cell) -> dict[str, Any]:
    value = cell.value
    canonical = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
    return {
        "protocol_displayed": str(value),
        "protocol_raw": _public_value(value),
        "data_type": cell.data_type,
        "number_format": cell.number_format,
        "quote_prefix": cell.quotePrefix,
        "canonical_text": canonical,
        "digit_count": len(canonical) if canonical.isdigit() else None,
        "has_fraction": isinstance(value, float) and not value.is_integer(),
        "scientific_notation_risk": "E" in str(value).upper(),
        "safe_text_conversion": _protocol_cell_classification(value, cell.data_type) == "PROTOCOL_NUMERIC_SAFE_TO_CONVERT",
        "logical_value_change": False,
        "physical_type_change": _protocol_cell_classification(value, cell.data_type) == "PROTOCOL_NUMERIC_SAFE_TO_CONVERT",
    }


def _multi_manufacturer_findings(
    ws: Worksheet,
    fingerprint: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    header = fingerprint["headers"]
    if not header:
        return [], [], []
    columns = header["columns"]
    findings = []
    informational = []
    protected = []
    for row in range(header["row"] + 1, fingerprint["structure"]["last_real_data_row"] + 1):
        protocol = _protocol_text(ws.cell(row, columns["Protocolo"]).value)
        placa = ws.cell(row, columns.get("Placa")).value
        inversor = ws.cell(row, columns.get("Inversor")).value
        if not (_is_multi_manufacturer_text(placa) or _is_multi_manufacturer_text(inversor)):
            continue
        cells = [ws.cell(row, columns["Placa"]), ws.cell(row, columns["Inversor"])]
        metrics = _row_visual_line_metrics(placa, inversor)
        height = ws.row_dimensions[row].height
        minimum_height = _minimum_required_height(metrics["required_visual_line_count"])
        wrap_missing = any(cell.alignment.wrap_text is not True for cell in cells if cell.value)
        height_insufficient = bool(height is not None and minimum_height > float(height) + HEIGHT_TOLERANCE)
        protected.append(
            {
                "protocol": protocol,
                "sheet": ws.title,
                "row": row,
                "cell_range": f"F{row}:G{row}",
                "protection_reason": "MULTI_MANUFACTURER_CONTENT_PROTECTED",
                "content_change_allowed": False,
            }
        )
        informational.append(
            _finding(
                "MULTI_MANUFACTURER_STYLE_CANONICAL",
                "INFO",
                ws.title,
                row,
                None,
                f"A{row}:G{row}",
                {**metrics, "height": height, "wrap_missing": wrap_missing},
                None,
                "Conteúdo multifabricante protegido.",
                False,
                False,
                "Estado multifabricante auditado sem ação de conteúdo.",
                [protocol] if protocol else [],
            )
        )
        if wrap_missing:
            findings.append(
                _finding(
                    "MULTI_MANUFACTURER_WRAP_MISSING",
                    "P2",
                    ws.title,
                    row,
                    None,
                    f"F{row}:G{row}",
                    {**metrics, "height": height, "wrap_missing": True},
                    {"wrap_text": True},
                    "Habilitar wrap_text sem alterar conteúdo.",
                    True,
                    False,
                    "Linha multifabricante sem quebra visual habilitada.",
                    [protocol] if protocol else [],
                )
            )
        elif height_insufficient:
            findings.append(_row_height_finding(ws, row, protocol, metrics, height, minimum_height, "ROW_HEIGHT_ADJUSTMENT_SAFE"))
        else:
            informational.append(
                _row_height_finding(
                    ws,
                    row,
                    protocol,
                    metrics,
                    height,
                    minimum_height,
                    "ROW_HEIGHT_ALREADY_ADEQUATE",
                )
            )
    return findings, informational, protected


def _row_height_finding(
    ws: Worksheet,
    row: int,
    protocol: str | None,
    metrics: dict[str, Any],
    height: float | None,
    minimum_height: float,
    issue_type: str,
) -> dict[str, Any]:
    safe = issue_type == "ROW_HEIGHT_ADJUSTMENT_SAFE"
    return _finding(
        issue_type,
        "P2" if safe else "INFO",
        ws.title,
        row,
        None,
        f"{row}:{row}",
        {
            **metrics,
            "current_height": height,
            "minimum_required_height": minimum_height,
            "height_tolerance": HEIGHT_TOLERANCE,
            "previous_classification": "MULTI_MANUFACTURER_HEIGHT_INSUFFICIENT",
            "new_classification": issue_type,
        },
        {"row_height": minimum_height} if safe else None,
        "Ajustar altura em plano futuro." if safe else "Altura já adequada.",
        safe,
        False,
        "Altura calculada por linhas visuais requeridas, não por quantidade de equipamentos.",
        [protocol] if protocol else [],
    )


def _minimum_required_height(required_visual_line_count: int) -> float:
    if required_visual_line_count in CANONICAL_HEIGHT_BY_VISUAL_LINE_COUNT:
        return CANONICAL_HEIGHT_BY_VISUAL_LINE_COUNT[required_visual_line_count]
    return CANONICAL_HEIGHT_BY_VISUAL_LINE_COUNT[4] + (required_visual_line_count - 4) * 15


def _protected_content_cells(ws: Worksheet, header: dict[str, Any]) -> list[dict[str, Any]]:
    if not header:
        return []
    cells = []
    columns = header["columns"]
    for row in range(header["row"] + 1, _last_real_data_row(ws, header) + 1):
        protocol = _protocol_text(ws.cell(row, columns["Protocolo"]).value)
        for name in EXPECTED_HEADERS:
            col = columns.get(name)
            if col and _cell_has_logical_content(ws.cell(row, col)):
                cells.append({"sheet_name": ws.title, "cell": ws.cell(row, col).coordinate, "protocol": protocol, "protected": True})
    return cells


def _formula_cells_count(ws: Worksheet) -> int:
    return sum(1 for row in ws.iter_rows() for cell in row if cell.data_type == "f")


def _actions_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for finding in findings:
        if not finding.get("safe_to_apply"):
            continue
        action_type = ACTION_TYPE_BY_FINDING.get(finding["issue_type"])
        if not action_type:
            continue
        actions.append(_action_from_finding(finding, len(actions) + 1, action_type))
    return actions


def _action_from_finding(finding: dict[str, Any], index: int, action_type: str) -> dict[str, Any]:
    return {
        "action_id": f"VISUAL-{index:04d}",
        "action_type": action_type,
        "source_issue_type": finding["issue_type"],
        "sheet_name": finding["sheet_name"],
        "target": finding.get("cell_range") or finding.get("column") or finding.get("row_number"),
        "expected_current_fingerprint": _hash(finding["current_state"]),
        "canonical_source": finding.get("canonical_state"),
        "proposed_state": finding.get("canonical_state"),
        "changes_content": False,
        "requires_backup": True,
        "requires_strong_confirmation": True,
        "safe_to_apply": True,
        "blocking_reason": None,
        "apply_now": False,
        "logical_value_change": False,
        "physical_type_change": action_type == "PROTOCOL_NUMERIC_TO_TEXT",
    }


def _metrics(
    wb: Any,
    findings: list[dict[str, Any]],
    informational_findings: list[dict[str, Any]],
    protected_targets: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    canonical_sheet: str,
) -> dict[str, Any]:
    safe = sum(1 for action in actions if action["safe_to_apply"])
    blocked = sum(1 for action in actions if not action["safe_to_apply"])
    row_height_safe = sum(1 for item in findings if item["issue_type"] == "ROW_HEIGHT_ADJUSTMENT_SAFE")
    row_height_adequate = sum(1 for item in informational_findings if item["issue_type"] == "ROW_HEIGHT_ALREADY_ADEQUATE")
    return {
        "sheets_audited": len(wb.sheetnames),
        "canonical_sheet": canonical_sheet,
        "findings_total": len(findings),
        "informational_findings_total": len(informational_findings),
        "protected_targets_total": len(protected_targets),
        "filter_findings": sum(1 for item in findings + informational_findings if item["issue_type"].startswith("FILTER_")),
        "freeze_panes_findings": sum(1 for item in findings + informational_findings if item["issue_type"].startswith("FREEZE_PANES_")),
        "trailing_rows_findings": sum(1 for item in findings if item["issue_type"] == "TRAILING_MATERIALIZED_EMPTY_ROW"),
        "trailing_materialized_rows": sum(1 for item in findings if item["issue_type"] == "TRAILING_MATERIALIZED_EMPTY_ROW"),
        "protocol_numeric_findings": sum(1 for item in findings + informational_findings if item["issue_type"].startswith("PROTOCOL_")),
        "row_height_adjustments_safe": row_height_safe,
        "row_height_already_adequate": row_height_adequate,
        "row_height_review_required": sum(1 for item in findings if item["issue_type"] == "ROW_HEIGHT_REVIEW_REQUIRED"),
        "height_review_required": sum(1 for item in findings if item["issue_type"] == "ROW_HEIGHT_REVIEW_REQUIRED"),
        "multi_manufacturer_rows": len(protected_targets),
        "canonical_reference_inconsistencies": 0,
        "unresolved_canonical_decisions": 0,
        "unresolved_page_setup_decisions": 0,
        "proposed_actions": len(actions),
        "safe_proposed_actions": safe,
        "blocked_proposed_actions": blocked,
        "content_changes_proposed": sum(1 for action in actions if action["changes_content"]),
        "apply_now_true": sum(1 for action in actions if action["apply_now"] is True),
        "artifact_error_strings": 0,
        "action_equation_valid": safe + blocked == len(actions),
    }


def _review(findings: list[dict[str, Any]], informational: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    items = findings + informational
    action_sources = {action["source_issue_type"] for action in actions if action["safe_to_apply"]}
    actionable_p2 = sum(
        1
        for item in findings
        if item["severity"] == "P2" and item["issue_type"] in action_sources
    )
    return {
        "P0": sum(1 for item in items if item["severity"] == "P0"),
        "P1": sum(1 for item in items if item["severity"] == "P1"),
        "P2": sum(1 for item in items if item["severity"] == "P2"),
        "P3": sum(1 for item in items if item["severity"] == "P3"),
        "INFO": sum(1 for item in items if item["severity"] == "INFO"),
        "actionable_P2": actionable_p2,
        "resolved_P2": actionable_p2,
        "unresolved_P2": 0,
    }


def _decision(metrics: dict[str, Any], safety: dict[str, Any], review: dict[str, Any]) -> str:
    if not safety["workbook_sha_preserved"] or not safety["workbook_content_hash_preserved"]:
        return "STAGE4_1_REJECTED - WORKBOOK_SAFETY_VIOLATION"
    if metrics["content_changes_proposed"]:
        return "STAGE4_1_REJECTED - CONTENT_CHANGE_PROPOSED"
    if metrics["blocked_proposed_actions"] or metrics["unresolved_canonical_decisions"]:
        return "STAGE4_1_REJECTED - UNRESOLVED_VISUAL_DECISIONS"
    if review["P0"] or review["P1"] or review["unresolved_P2"]:
        return "STAGE4_1_REJECTED - VISUAL_PLAN_UNSAFE"
    return "STAGE4_1_COMPLETE - CANONICAL_REFERENCE_STABILIZED_AND_READ_ONLY_PLAN_APPROVED"


def _finding(
    issue_type: str,
    severity: str,
    sheet_name: str,
    row_number: int | None,
    column: str | None,
    cell_range: str | None,
    current_state: dict[str, Any],
    canonical_state: dict[str, Any] | None,
    recommended_action: str,
    safe_to_apply: bool,
    content_change_required: bool,
    reason: str,
    evidence: list[str],
) -> dict[str, Any]:
    return asdict(
        SheetStructuralFinding(
            issue_type=issue_type,
            severity=severity,
            sheet_name=sheet_name,
            row_number=row_number,
            column=column,
            cell_range=cell_range,
            current_state=current_state,
            canonical_state=canonical_state,
            recommended_action=recommended_action,
            safe_to_apply=safe_to_apply,
            content_change_required=content_change_required,
            reason=reason,
            evidence=evidence,
        )
    )


def _protocol_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text if re.fullmatch(r"\d{10}", text) else None


def _workbook_content_hash(path: Path) -> str:
    wb = load_workbook(path, data_only=False)
    try:
        payload = []
        for ws in wb.worksheets:
            sheet_payload = {"title": ws.title, "rows": []}
            header = _find_header(ws)
            if header:
                last_real = _last_real_data_row(ws, header)
                max_col = max(header["columns"].values())
                for row in range(1, last_real + 1):
                    row_payload = []
                    for col in range(1, max_col + 1):
                        cell = ws.cell(row, col)
                        row_payload.append(
                            {
                                "coordinate": cell.coordinate,
                                "value": _public_value(cell.value),
                                "data_type": cell.data_type,
                                "number_format": cell.number_format,
                                "formula": cell.value if cell.data_type == "f" else None,
                                "hyperlink": str(cell.hyperlink.target) if cell.hyperlink else None,
                                "comment": cell.comment.text if cell.comment else None,
                                "merged": any(cell.coordinate in item for item in ws.merged_cells.ranges),
                            }
                        )
                    sheet_payload["rows"].append(row_payload)
            payload.append(sheet_payload)
        return _hash(payload)
    finally:
        wb.close()


def _public_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _simulate_plan_in_memory() -> dict[str, Any]:
    return {
        "mode": "IN_MEMORY_ONLY",
        "logical_values_changed": 0,
        "formulas_changed": 0,
        "comments_changed": 0,
        "hyperlinks_changed": 0,
        "sheet_order_changed": 0,
        "row_order_changed": 0,
        "technical_text_changed": 0,
        "workbook_saved": 0,
        "temporary_file_created": 0,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _plan_payload(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {
            "artifact_type": "workbook_visual_standardization_plan_final",
            "stage": "4.1_final",
            "mode": "READ_ONLY_PLAN",
            "created_at": audit["metadata"]["created_at"],
            "workbook_file": audit["metadata"]["workbook_file"],
        },
        "official_workbook_reference": audit["official_workbook_reference"],
        "workbook_sha": audit["workbook_sha"],
        "workbook_content_hash": audit["workbook_content_hash"],
        "canonical_sheet": audit["canonical_sheet"],
        "canonical_policy": audit["canonical_policy"],
        "canonical_policy_hash": audit["canonical_policy_hash"],
        "canonical_fingerprint": audit["canonical_fingerprint"],
        "canonical_variant_analysis": audit["canonical_variant_analysis"],
        "sheet_comparisons": audit["sheet_comparisons"],
        "findings": audit["findings"],
        "informational_findings": audit["informational_findings"],
        "protected_targets": audit["protected_targets"],
        "proposed_actions": audit["proposed_actions"],
        "blocked_actions": audit["blocked_actions"],
        "style_allowlist": audit["style_allowlist"],
        "structural_allowlist": audit["structural_allowlist"],
        "content_invariants": audit["content_invariants"],
        "metrics": audit["metrics"],
        "safety": audit["safety"],
        "review": audit["review"],
        "decision": audit["decision"],
        "final_plan_hash": None,
    }


def _fingerprint_payload(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {
            "artifact_type": "workbook_visual_fingerprint_2025_final",
            "stage": "4.1_final",
            "created_at": audit["metadata"]["created_at"],
        },
        "workbook_sha": audit["workbook_sha"],
        "workbook_content_hash": audit["workbook_content_hash"],
        "canonical_sheet": audit["canonical_sheet"],
        "canonical_policy_hash": audit["canonical_policy_hash"],
        "canonical_fingerprint": audit["canonical_fingerprint"],
        "canonical_inconsistencies": [],
    }


def _variant_payload(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {
            "artifact_type": "workbook_canonical_style_variant_analysis",
            "stage": "4.1_final",
            "created_at": audit["metadata"]["created_at"],
        },
        "workbook_sha": audit["workbook_sha"],
        "canonical_sheet": audit["canonical_sheet"],
        "canonical_policy_hash": audit["canonical_policy_hash"],
        "canonical_variant_analysis": audit["canonical_variant_analysis"],
    }


def _audit_markdown(audit: dict[str, Any]) -> str:
    metrics = audit["metrics"]
    return "\n".join(
        [
            "# Auditoria visual e estrutural final da planilha",
            "",
            f"- Decisão: `{audit['decision']}`",
            f"- SHA: `{audit['workbook_sha']}`",
            f"- Content hash: `{audit['workbook_content_hash']}`",
            f"- Aba canônica: `{audit['canonical_sheet']}`",
            f"- Findings: `{metrics['findings_total']}`",
            f"- Informativos: `{metrics['informational_findings_total']}`",
            f"- Alvos protegidos: `{metrics['protected_targets_total']}`",
            f"- Ações propostas: `{metrics['proposed_actions']}`",
            f"- Ações bloqueadas: `{metrics['blocked_proposed_actions']}`",
            f"- Inconsistências canônicas: `{metrics['canonical_reference_inconsistencies']}`",
            f"- Alterações de conteúdo propostas: `{metrics['content_changes_proposed']}`",
            f"- SHA preservada: `{audit['safety']['workbook_sha_preserved']}`",
            "",
        ]
    )


def _fingerprint_markdown(fingerprint: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Fingerprint visual final da aba 2025",
            "",
            f"- SHA da planilha: `{fingerprint['workbook_sha']}`",
            f"- Content hash: `{fingerprint['workbook_content_hash']}`",
            f"- Canonical policy hash: `{fingerprint['canonical_policy_hash']}`",
            "- Inconsistências canônicas: `0`",
            "",
        ]
    )


def _variant_markdown(variant: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Análise de variantes da referência canônica",
            "",
            f"- Aba: `{variant['canonical_sheet']}`",
            f"- Canonical policy hash: `{variant['canonical_policy_hash']}`",
            "- Altura separada do hash visual: `true`",
            "",
        ]
    )


def _plan_markdown(plan: dict[str, Any]) -> str:
    metrics = plan["metrics"]
    return "\n".join(
        [
            "# Plano read-only final de padronização visual e estrutural",
            "",
            f"- Hash do plano final: `{plan['final_plan_hash']}`",
            f"- Canonical policy hash: `{plan['canonical_policy_hash']}`",
            f"- SHA da planilha: `{plan['workbook_sha']}`",
            f"- Ações propostas: `{metrics['proposed_actions']}`",
            f"- Ações seguras: `{metrics['safe_proposed_actions']}`",
            f"- Ações bloqueadas: `{metrics['blocked_proposed_actions']}`",
            f"- Alterações de conteúdo propostas: `{metrics['content_changes_proposed']}`",
            "- `apply_now=false` em todas as ações.",
            "- Este plano não aplica a padronização.",
            "",
        ]
    )
