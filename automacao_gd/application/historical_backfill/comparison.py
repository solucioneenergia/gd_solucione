from __future__ import annotations

import hashlib
import json
import re
import unicodedata


def normalize_protocol(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", "", value)
    return normalized if normalized.isdigit() else None


def normalize_equipment_text(value: object) -> str:
    normalized = unicodedata.normalize("NFC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def equipment_texts_equal(left: object, right: object) -> bool:
    return normalize_equipment_text(left) == normalize_equipment_text(right)


def row_fingerprint(
    sheet: str,
    row: int,
    protocol: str | None,
    module_text: object,
    inverter_text: object,
) -> str:
    payload = [
        unicodedata.normalize("NFC", sheet),
        row,
        protocol,
        str(module_text or ""),
        str(inverter_text or ""),
    ]
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def comparison_reasons(
    current_module: str,
    current_inverter: str,
    proposed_module: str,
    proposed_inverter: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    module_key = normalize_equipment_text(current_module)
    inverter_key = normalize_equipment_text(current_inverter)
    if not module_key:
        reasons.append("MODULE_EMPTY")
    if not inverter_key:
        reasons.append("INVERTER_EMPTY")
    if "gokin" in inverter_key:
        reasons.append("GOKIN_IN_INVERTER")
    if "aiswei" in inverter_key and "solplanet" in normalize_equipment_text(
        proposed_inverter
    ):
        reasons.append("SOLPLANET_ALIAS_NORMALIZED")
    if any(signal in inverter_key for signal in ("bifacial", "n-type", "monocristalino")):
        reasons.append("MODULE_IN_INVERTER")
    if any(signal in module_key for signal in ("microinversor", "inversor —", "inversor |")):
        reasons.append("INVERTER_IN_MODULE")
    current_identities = _equipment_identities(current_module, current_inverter)
    proposed_identities = _equipment_identities(proposed_module, proposed_inverter)
    if _quantities(current_module, current_inverter) != _quantities(
        proposed_module, proposed_inverter
    ):
        reasons.append("QUANTITY_DIVERGENT")
    current_manufacturers = {item[0] for item in current_identities}
    proposed_manufacturers = {item[0] for item in proposed_identities}
    if (
        current_manufacturers
        and proposed_manufacturers
        and current_manufacturers != proposed_manufacturers
    ):
        reasons.append("MANUFACTURER_DIVERGENT")
    current_models = {item[1] for item in current_identities if item[1]}
    proposed_models = {item[1] for item in proposed_identities if item[1]}
    if current_models and proposed_models and current_models != proposed_models:
        reasons.append("MODEL_DIVERGENT")
    if _has_duplicate_equipment(current_module) or _has_duplicate_equipment(
        current_inverter
    ):
        reasons.append("DUPLICATE_EQUIPMENT")
    if any(
        marker in f"{module_key}\n{inverter_key}"
        for marker in ("qtd. total:", "quantidade total:", " | ")
    ):
        reasons.append("LEGACY_FORMAT")
    if not equipment_texts_equal(current_module, proposed_module):
        reasons.append("MODULE_DIFFERENT")
    if not equipment_texts_equal(current_inverter, proposed_inverter):
        reasons.append("INVERTER_DIFFERENT")
    return tuple(dict.fromkeys(reasons))


def _equipment_identities(*values: str) -> tuple[tuple[str, str], ...]:
    identities: list[tuple[str, str]] = []
    for value in values:
        for raw_line in re.split(r"[\r\n]+", str(value or "")):
            line = normalize_equipment_text(raw_line)
            if not line or line.startswith(("qtd. total:", "quantidade total:")):
                continue
            line = re.sub(r"^\d+\s*x\s*", "", line)
            line = re.sub(r"^(?:micro)?inversor\s*[—|-]\s*", "", line)
            parts = [part.strip() for part in line.split(" | ", maxsplit=1)]
            if len(parts) == 1:
                parts = line.split(" ", maxsplit=1)
            manufacturer = _canonical_manufacturer(parts[0])
            model = parts[1] if len(parts) > 1 else ""
            identities.append((manufacturer, model))
    return tuple(identities)


def _canonical_manufacturer(value: str) -> str:
    return "solplanet" if value in {"solplanet", "aiswei"} else value


def _quantities(*values: str) -> tuple[int, ...]:
    return tuple(
        int(match)
        for value in values
        for match in re.findall(r"(?im)^\s*(\d+)\s*x\b", str(value or ""))
    )


def _has_duplicate_equipment(value: str) -> bool:
    identities = _equipment_identities(value)
    return len(identities) != len(set(identities))


def contains_sensitive_or_path_text(value: object) -> bool:
    text = str(value or "")
    patterns = (
        r"[A-Za-z]:\\",
        r"\\\\[^\\]+\\",
        r"/(?:home|Users|var|tmp)/",
        r"\.(?:pdf|xlsx?)\b",
        r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b",
        r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
        r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
