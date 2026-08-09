from __future__ import annotations

from typing import Any


_TOTAL_FIELDS = (
    "total_pdfs",
    "total_success",
    "total_errors",
    "total_processed",
    "total_excel_updated",
    "total_archived",
    "total_pending_review",
    "total_technical_pending_review",
    "total_selected",
    "total_downloaded",
    "total_existing_reused",
    "total_skipped_existing",
    "total_skipped_duplicate",
    "total_cdp_errors",
    "total_download_errors",
)


def build_shareable_report(payload: dict[str, Any], *, report_type: str) -> dict[str, Any]:
    """Build an aggregate-only report; never copy arbitrary or per-item values."""

    totals = {
        key: int(payload[key])
        for key in _TOTAL_FIELDS
        if key in payload and _is_plain_count(payload[key])
    }
    return {
        "classification": "SHAREABLE",
        "schema_version": 1,
        "report_type": _report_type(report_type),
        "status": _status(payload.get("status")),
        "mode": "dry_run" if bool(payload.get("dry_run", True)) else "real_run",
        "blocked": bool(payload.get("blocked_real_run", False)),
        "totals": totals,
    }


def _is_plain_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _report_type(value: str) -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized in {"processing", "download", "pipeline"} else "operation"


def _status(value: Any) -> str:
    normalized = str(value or "UNKNOWN").strip().upper()
    return normalized if normalized in {"SUCESSO", "PARCIAL", "BLOQUEADO", "FALHOU"} else "UNKNOWN"
