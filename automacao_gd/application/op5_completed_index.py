from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from automacao_gd.domain.equipment_validation import (
    EQUIPMENT_RULES_VERSION,
    TECHNICAL_PROCESSING_FORMAT_VERSION,
)
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


SCHEMA_VERSION = "op5-completed-index-v1"
DEFAULT_TTL_DAYS = 14


def record_completed_protocol(
    *,
    index_path: Path,
    protocol: str,
    download_pdf_path: Path,
    archived_pdf_path: Path | None,
    workbook_path: Path,
    workbook_sheet: str | None,
    workbook_row: int | None,
    source_pdf_sha256: str | None = None,
    archived_pdf_sha256: str | None = None,
    portal_page_number: int | None = None,
    portal_row_index: int | None = None,
    portal_anchor_scope: str | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    now = _normalize_datetime(updated_at or datetime.now(timezone.utc))
    expires_at = now + timedelta(days=DEFAULT_TTL_DAYS)
    download_path = Path(download_pdf_path)
    archive_path = Path(archived_pdf_path) if archived_pdf_path else None
    entry = {
        "status": "completed",
        "download_pdf_path": str(download_path),
        "download_pdf_sha256": source_pdf_sha256 or _sha256_file(download_path),
        "archived_pdf_path": str(archive_path) if archive_path else None,
        "archived_pdf_sha256": archived_pdf_sha256
        or (_sha256_file(archive_path) if archive_path and archive_path.is_file() else None),
        "workbook_sheet": workbook_sheet,
        "workbook_row": workbook_row,
        "workbook_sha256": _sha256_file(Path(workbook_path)),
        "portal_page_number": portal_page_number,
        "portal_row_index": portal_row_index,
        "portal_anchor_scope": portal_anchor_scope,
        "technical_extractor_version": TECHNICAL_PROCESSING_FORMAT_VERSION,
        "equipment_rules_version": EQUIPMENT_RULES_VERSION,
        "updated_at": now.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
    }
    payload = _load_index(index_path)
    payload.setdefault("protocols", {})[str(protocol)] = entry
    payload["updated_at"] = now.isoformat(timespec="seconds")
    atomic_write_json(Path(index_path), payload, private=True)
    return entry


def load_valid_completed_entries(
    index_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    payload = _load_index(index_path)
    valid: dict[str, dict[str, Any]] = {}
    for protocol, entry in (payload.get("protocols") or {}).items():
        if not isinstance(entry, dict) or entry.get("status") != "completed":
            continue
        try:
            expires_at = datetime.fromisoformat(str(entry["expires_at"]))
        except (KeyError, ValueError):
            continue
        current = _normalize_datetime(now or datetime.now(timezone.utc))
        if expires_at <= current:
            continue
        valid[str(protocol)] = entry
    return valid


def load_valid_completed_entry(
    index_path: Path,
    protocol: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    return load_valid_completed_entries(index_path, now=now).get(str(protocol))


def _load_index(index_path: Path) -> dict[str, Any]:
    path = Path(index_path)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("schema_version") == SCHEMA_VERSION:
                payload.setdefault("protocols", {})
                return payload
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "schema_version": SCHEMA_VERSION,
        "protocols": {},
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
