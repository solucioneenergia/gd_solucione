from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automacao_gd.application.op5_completed_index import load_valid_completed_entry
from automacao_gd.infrastructure.files.paths import ensure_path_within_root
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


RETENTION_PLAN_SCHEMA_VERSION = "op5-retention-plan-v1"
_PROTOCOL_PATTERN = re.compile(r"Orcamento_de_Conexao_(\d+)(?:_v\d+)?\.pdf$", re.I)


def audit_download_retention(
    *,
    downloads_root: Path,
    master_index_path: Path,
    workbook_path: Path,
    logs_dir: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    downloads_root = Path(downloads_root)
    logs_dir = Path(logs_dir)
    workbook_path = Path(workbook_path)
    workbook_sha256 = _sha256_file(workbook_path) if workbook_path.is_file() else None
    items = [
        _build_retention_item(
            pdf_path=pdf_path,
            downloads_root=downloads_root,
            master_index_path=master_index_path,
            workbook_path=workbook_path,
            workbook_sha256=workbook_sha256,
            now=current_time,
        )
        for pdf_path in _iter_download_pdfs(downloads_root)
    ]
    plan = {
        "schema_version": RETENTION_PLAN_SCHEMA_VERSION,
        "created_at": current_time.isoformat(timespec="seconds"),
        "dry_run": True,
        "downloads_root": str(downloads_root),
        "master_index_path": str(master_index_path),
        "workbook_path": str(workbook_path),
        "workbook_sha256": workbook_sha256,
        "items": items,
        "total_pdfs_scanned": len(items),
        "total_delete_candidates": sum(
            1 for item in items if item["action"] == "delete_local_pdf"
        ),
        "total_blocked": sum(1 for item in items if item["action"] != "delete_local_pdf"),
    }
    plan_path = logs_dir / "op5_retention_plan_latest.json"
    plan["plan_path"] = str(plan_path)
    atomic_write_json(plan_path, plan, private=True)
    return plan


def apply_download_retention_plan(
    plan_path: Path,
    *,
    downloads_root: Path,
    master_index_path: Path,
    workbook_path: Path,
    logs_dir: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = _normalize_datetime(now or datetime.now(timezone.utc))
    plan = _load_plan(Path(plan_path))
    if plan.get("schema_version") != RETENTION_PLAN_SCHEMA_VERSION or not plan.get("dry_run"):
        return _blocked_result("OP5_RETENTION_PLAN_INVALID", "Plano de retenção inválido.")

    deleted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict) or item.get("action") != "delete_local_pdf":
            continue
        pdf_path = Path(str(item.get("download_pdf_path", "")))
        current = _build_retention_item(
            pdf_path=pdf_path,
            downloads_root=Path(downloads_root),
            master_index_path=Path(master_index_path),
            workbook_path=Path(workbook_path),
            workbook_sha256=_sha256_file(Path(workbook_path))
            if Path(workbook_path).is_file()
            else None,
            now=current_time,
        )
        if current["action"] != "delete_local_pdf":
            blocked.append(current)
            continue
        try:
            pdf_path.unlink()
        except OSError as exc:
            current["action"] = "keep"
            current["reasons"].append(type(exc).__name__)
            blocked.append(current)
            continue
        deleted.append(current)

    success = not blocked
    result = {
        "schema_version": "op5-retention-apply-result-v1",
        "created_at": current_time.isoformat(timespec="seconds"),
        "success": success,
        "status": "SUCESSO" if success else "BLOQUEADO",
        "error_code": None if success else "OP5_RETENTION_VALIDATION_FAILED",
        "plan_path": str(plan_path),
        "deleted_count": len(deleted),
        "blocked_count": len(blocked),
        "deleted": deleted,
        "blocked": blocked,
    }
    report_path = Path(logs_dir) / "op5_retention_apply_latest.json"
    result["report_path"] = str(report_path)
    atomic_write_json(report_path, result, private=True)
    return result


def _build_retention_item(
    *,
    pdf_path: Path,
    downloads_root: Path,
    master_index_path: Path,
    workbook_path: Path,
    workbook_sha256: str | None,
    now: datetime,
) -> dict[str, Any]:
    reasons: list[str] = []
    protocol = _protocol_from_pdf_name(pdf_path)
    try:
        safe_pdf_path = ensure_path_within_root(downloads_root, pdf_path)
    except ValueError:
        safe_pdf_path = Path(pdf_path)
        reasons.append("download_pdf_outside_root")
    download_sha256 = _sha256_file(safe_pdf_path) if safe_pdf_path.is_file() else None
    entry = load_valid_completed_entry(master_index_path, protocol, now=now) if protocol else None
    if entry is None:
        reasons.append("master_index_missing_or_expired")

    archived_path = Path(str(entry.get("archived_pdf_path"))) if entry else None
    archived_sha256 = (
        _sha256_file(archived_path)
        if archived_path is not None and archived_path.is_file()
        else None
    )
    if entry and download_sha256 != entry.get("download_pdf_sha256"):
        reasons.append("download_pdf_sha_mismatch")
    if entry and not archived_sha256:
        reasons.append("archived_pdf_missing")
    if entry and archived_sha256 != entry.get("archived_pdf_sha256"):
        reasons.append("archived_pdf_sha_mismatch")
    if entry and download_sha256 != archived_sha256:
        reasons.append("download_archived_sha_mismatch")
    if entry and (not entry.get("workbook_sheet") or not entry.get("workbook_row")):
        reasons.append("workbook_location_missing")
    if entry and workbook_sha256 != entry.get("workbook_sha256"):
        reasons.append("workbook_sha_mismatch")

    action = "keep" if reasons else "delete_local_pdf"
    return {
        "protocol": protocol,
        "download_pdf_path": str(pdf_path),
        "download_pdf_sha256": download_sha256,
        "archived_pdf_path": str(archived_path) if archived_path else None,
        "archived_pdf_sha256": archived_sha256,
        "workbook_sheet": entry.get("workbook_sheet") if entry else None,
        "workbook_row": entry.get("workbook_row") if entry else None,
        "action": action,
        "reasons": reasons,
    }


def _iter_download_pdfs(downloads_root: Path) -> list[Path]:
    root = Path(downloads_root)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.pdf") if path.is_file())


def _protocol_from_pdf_name(path: Path) -> str | None:
    match = _PROTOCOL_PATTERN.search(Path(path).name)
    return match.group(1) if match else None


def _load_plan(plan_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _blocked_result(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "status": "BLOQUEADO",
        "error_code": code,
        "error": message,
        "deleted_count": 0,
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
