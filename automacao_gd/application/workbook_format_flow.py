from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automacao_gd.infrastructure.excel.service import repair_workbook_format
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


WORKBOOK_FORMAT_PLAN_SCHEMA_VERSION = "workbook-format-plan-v1"


def audit_workbook_format(workbook_path: Path, logs_dir: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    workbook_path = Path(workbook_path)
    logs_dir = Path(logs_dir)
    workbook_sha256 = _sha256_file(workbook_path) if workbook_path.is_file() else None
    audit = repair_workbook_format(workbook_path=workbook_path, dry_run=True)
    plan = {
        "schema_version": WORKBOOK_FORMAT_PLAN_SCHEMA_VERSION,
        "created_at": now.isoformat(timespec="seconds"),
        "dry_run": True,
        "workbook_path": str(workbook_path),
        "workbook_sha256": workbook_sha256,
        "audit": audit,
    }
    plan_path = logs_dir / "workbook_format_plan_latest.json"
    plan["plan_path"] = str(plan_path)
    atomic_write_json(plan_path, plan, private=True)
    return plan


def apply_workbook_format_plan(
    plan_path: Path,
    *,
    workbook_path: Path,
    logs_dir: Path,
) -> dict[str, Any]:
    plan = _load_plan(Path(plan_path))
    if plan.get("schema_version") != WORKBOOK_FORMAT_PLAN_SCHEMA_VERSION:
        return _blocked_result("WORKBOOK_FORMAT_PLAN_INVALID", "Plano de formatação inválido.")
    workbook_path = Path(workbook_path)
    current_sha256 = _sha256_file(workbook_path) if workbook_path.is_file() else None
    if current_sha256 != plan.get("workbook_sha256"):
        return _blocked_result(
            "WORKBOOK_FORMAT_WORKBOOK_CHANGED",
            "Planilha mudou depois da auditoria de formatação.",
        )
    result = repair_workbook_format(workbook_path=workbook_path, dry_run=False)
    payload = {
        "schema_version": "workbook-format-apply-result-v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "success": bool(result.get("success")),
        "status": "SUCESSO" if result.get("success") else "FALHOU",
        "plan_path": str(plan_path),
        "workbook_path": str(workbook_path),
        "workbook_sha256": current_sha256,
        "result": result,
    }
    report_path = Path(logs_dir) / "workbook_format_apply_latest.json"
    payload["report_path"] = str(report_path)
    atomic_write_json(report_path, payload, private=True)
    return payload


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
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
