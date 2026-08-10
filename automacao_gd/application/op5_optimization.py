from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


ELIGIBILITY_CACHE_SCHEMA_VERSION = "op5-eligibility-cache-v1"
RECONCILIATION_CACHE_SCHEMA_VERSION = "op5-reconciliation-cache-v1"
WORKBOOK_INDEX_CACHE_SCHEMA_VERSION = "op5-workbook-index-cache-v1"
MAX_OP5_PDF_WORKERS = 4

_T = TypeVar("_T")


def write_eligibility_cache(
    cache_path: Path,
    *,
    records: Sequence[dict[str, Any]],
    requested_limit: int,
    reconciliation_mode: str,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    captured = _normalize_datetime(captured_at or datetime.now(timezone.utc))
    sanitized_records = [_sanitize_eligibility_record(record) for record in records]
    payload = {
        "schema_version": ELIGIBILITY_CACHE_SCHEMA_VERSION,
        "captured_at": captured.isoformat(),
        "requested_limit": int(requested_limit),
        "reconciliation_mode": str(reconciliation_mode),
        "structural_hash": _stable_hash(
            {
                "requested_limit": int(requested_limit),
                "reconciliation_mode": str(reconciliation_mode),
                "records": sanitized_records,
            }
        ),
        "records": sanitized_records,
    }
    atomic_write_json(Path(cache_path), payload, private=True)
    return payload


def load_eligibility_cache(
    cache_path: Path,
    *,
    requested_limit: int,
    reconciliation_mode: str,
    now: datetime | None = None,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    path = Path(cache_path)
    if not path.is_file():
        return {"cache_hit": False, "reason": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"cache_hit": False, "reason": "unreadable"}

    if payload.get("schema_version") != ELIGIBILITY_CACHE_SCHEMA_VERSION:
        return {"cache_hit": False, "reason": "schema_mismatch"}
    if str(payload.get("reconciliation_mode")) != str(reconciliation_mode):
        return {"cache_hit": False, "reason": "mode_mismatch"}
    if int(payload.get("requested_limit") or 0) < int(requested_limit):
        return {"cache_hit": False, "reason": "limit_mismatch"}
    try:
        captured_at = _parse_datetime(str(payload["captured_at"]))
    except (KeyError, ValueError):
        return {"cache_hit": False, "reason": "invalid_timestamp"}
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    if (current_time - captured_at).total_seconds() > int(ttl_minutes) * 60:
        return {"cache_hit": False, "reason": "expired"}
    records = list(payload.get("records") or [])
    expected_hash = _stable_hash(
        {
            "requested_limit": int(payload.get("requested_limit") or 0),
            "reconciliation_mode": str(payload.get("reconciliation_mode")),
            "records": records,
        }
    )
    if expected_hash != payload.get("structural_hash"):
        return {"cache_hit": False, "reason": "hash_mismatch"}
    return {
        "cache_hit": True,
        "reason": "valid",
        "protocols": [str(record["protocol"]) for record in records],
        "records": records,
        "structural_hash": expected_hash,
        "cache_path": str(path),
    }


def portal_protocols_hash(protocols: Iterable[str]) -> str:
    normalized = sorted({str(protocol).strip() for protocol in protocols if str(protocol).strip()})
    return _stable_hash(normalized)


def write_reconciliation_cache(
    cache_path: Path,
    *,
    workbook_sha256: str,
    portal_protocols_hash: str,
    reconciliation_mode: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": RECONCILIATION_CACHE_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workbook_sha256": workbook_sha256,
        "portal_protocols_hash": portal_protocols_hash,
        "reconciliation_mode": str(reconciliation_mode),
        "summary": summary,
    }
    atomic_write_json(Path(cache_path), payload, private=True)
    return payload


def load_reconciliation_cache(
    cache_path: Path,
    *,
    workbook_sha256: str,
    portal_protocols_hash: str,
    reconciliation_mode: str,
) -> dict[str, Any]:
    path = Path(cache_path)
    if not path.is_file():
        return {"cache_hit": False, "reason": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"cache_hit": False, "reason": "unreadable"}
    if payload.get("schema_version") != RECONCILIATION_CACHE_SCHEMA_VERSION:
        return {"cache_hit": False, "reason": "schema_mismatch"}
    if payload.get("workbook_sha256") != workbook_sha256:
        return {"cache_hit": False, "reason": "workbook_sha_mismatch"}
    if payload.get("portal_protocols_hash") != portal_protocols_hash:
        return {"cache_hit": False, "reason": "portal_hash_mismatch"}
    if payload.get("reconciliation_mode") != str(reconciliation_mode):
        return {"cache_hit": False, "reason": "mode_mismatch"}
    return {
        "cache_hit": True,
        "reason": "valid",
        "summary": dict(payload.get("summary") or {}),
        "cache_path": str(path),
    }


def load_or_build_workbook_index(
    workbook_path: Path,
    cache_path: Path,
    *,
    builder: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    workbook = Path(workbook_path)
    workbook_sha256 = _file_sha256(workbook)
    cache = Path(cache_path)
    if cache.is_file():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if (
            payload.get("schema_version") == WORKBOOK_INDEX_CACHE_SCHEMA_VERSION
            and payload.get("workbook_sha256") == workbook_sha256
        ):
            index = dict(payload.get("index") or {})
            index["cache_hit"] = True
            index["workbook_sha256"] = workbook_sha256
            return index

    index = dict(builder(workbook))
    payload = {
        "schema_version": WORKBOOK_INDEX_CACHE_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workbook_sha256": workbook_sha256,
        "index": index,
    }
    atomic_write_json(cache, payload, private=True)
    index["cache_hit"] = False
    index["workbook_sha256"] = workbook_sha256
    return index


def run_limited_pdf_tasks(
    pdf_paths: Sequence[Path],
    *,
    worker_count: int,
    task: Callable[[Path], _T],
) -> list[_T]:
    workers = int(worker_count)
    if workers < 1 or workers > MAX_OP5_PDF_WORKERS:
        raise ValueError("OP5_PDF_WORKERS deve estar entre 1 e 4.")
    paths = [Path(path) for path in pdf_paths]
    if workers == 1 or len(paths) <= 1:
        return [task(path) for path in paths]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(task, paths))


def _sanitize_eligibility_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": str(record.get("protocol") or "").strip(),
        "page_number": record.get("page_number"),
        "row_index": record.get("row_index"),
        "status": str(record.get("status") or "").strip(),
        "selection_reason": str(record.get("selection_reason") or "").strip(),
    }


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    return _normalize_datetime(datetime.fromisoformat(value))
