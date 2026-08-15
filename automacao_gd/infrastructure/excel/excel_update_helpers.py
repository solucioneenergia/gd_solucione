import re
from datetime import date
from typing import Any

from automacao_gd.infrastructure.dates import parse_date


OPEN_COMPLETION_TEXT = "EM ABERTO"


def base_excel_result(protocol: str, dry_run: bool) -> dict:
    return {
        "success": False,
        "can_write": False,
        "protocol": protocol,
        "row_found": False,
        "row_number": None,
        "created_new_row": False,
        "backup_path": None,
        "dry_run": dry_run,
        "worksheet": None,
        "target_sheet": None,
        "existing_sheet": None,
        "source_sheet": None,
        "source_row": None,
        "target_row": None,
        "existing_row": None,
        "new_row": None,
        "moved_from": None,
        "moved_to": None,
        "entry_date": None,
        "ingress_no_change": None,
        "module_no_change": None,
        "inverter_no_change": None,
        "equipment_no_change": None,
        "completion_no_change": None,
        "completion_action": None,
        "action": None,
        "warning": None,
        "error": None,
        "code": None,
        "technical_cause": None,
    }


def status_payload(item: dict) -> dict:
    status = item.get("excel_status")
    return status if isinstance(status, dict) else item


def payload_value(item: dict, status: dict, key: str) -> Any:
    value = item.get(key)
    return status.get(key) if value is None else value


def set_payload_value(item: dict, status: dict, key: str, value: Any) -> None:
    item[key] = value
    if status is not item:
        status[key] = value


def sheet_name_for_entry_date(entry_dt: date) -> str:
    return sheet_name_for_year(entry_dt.year)


def sheet_name_for_year(year: int) -> str:
    if year in {2022, 2023}:
        return "2022 - 2023"
    return str(year)


def completion_value_matches(current: Any, expected: Any) -> bool:
    if expected is None:
        return True
    if is_open_completion_value(expected):
        return normalize_cell(current) == OPEN_COMPLETION_TEXT
    expected_date = parse_date(expected)
    if expected_date is None:
        return False
    return parse_date(current) == expected_date


def is_open_completion_value(value: Any) -> bool:
    return normalize_cell(value) == normalize_cell(OPEN_COMPLETION_TEXT)


def equipment_text_matches(current: Any, expected: str | None) -> bool:
    if expected is None:
        return True
    if not has_text(expected):
        return not has_text(current)
    return normalize_equipment_text(current) == normalize_equipment_text(expected)


def normalize_equipment_text(value: Any) -> str:
    text = "" if value is None else str(value)
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    return "\n".join(lines)


def normalize_cell(value: Any) -> str:
    return re.sub(r"\s+", "", "" if value is None else str(value)).strip()


def has_text(value: Any) -> bool:
    return bool(str(value or "").strip())
