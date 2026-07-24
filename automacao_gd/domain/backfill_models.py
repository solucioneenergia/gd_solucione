from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class BackfillAction(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    UPDATE_EQUIPMENT = "UPDATE_EQUIPMENT"
    PENDING_TECHNICAL_REVIEW = "PENDING_TECHNICAL_REVIEW"
    PDF_NOT_FOUND = "PDF_NOT_FOUND"
    PROTOCOL_MISSING = "PROTOCOL_MISSING"
    DUPLICATE_PROTOCOL = "DUPLICATE_PROTOCOL"
    ROW_CONFLICT = "ROW_CONFLICT"
    INVALID_WORKBOOK_ROW = "INVALID_WORKBOOK_ROW"
    SKIPPED = "SKIPPED"


class BackfillApplyStatus(str, Enum):
    APPLIED_SUCCESSFULLY = "APPLIED_SUCCESSFULLY"
    ABORTED_PRE_FLIGHT = "ABORTED_PRE_FLIGHT"
    ABORTED_CONFIRMATION = "ABORTED_CONFIRMATION"
    ABORTED_FINGERPRINT_CONFLICT = "ABORTED_FINGERPRINT_CONFLICT"
    ABORTED_UNEXPECTED_CHANGE = "ABORTED_UNEXPECTED_CHANGE"
    FAILED_ROLLED_BACK = "FAILED_ROLLED_BACK"
    FAILED_ROLLBACK_UNCONFIRMED = "FAILED_ROLLBACK_UNCONFIRMED"


@dataclass(frozen=True, slots=True)
class HistoricalEquipmentAuditItem:
    workbook_sheet: str
    workbook_row: int
    protocol: str | None
    current_module_text: str
    current_inverter_text: str
    proposed_module_text: str | None
    proposed_inverter_text: str | None
    technical_validation_status: str
    action: BackfillAction
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    pdf_status: str = "not_checked"
    source_hash: str | None = None
    expected_row_fingerprint: str = ""
    semantic_validation_status: str = "not_checked"
    semantic_validation_errors: tuple[str, ...] = ()
    semantic_validation_warnings: tuple[str, ...] = ()
    blocking_violations: tuple[str, ...] = ()
    current_semantic_fingerprint: str | None = None
    proposed_semantic_fingerprint: str | None = None
    current_canonical_collection: dict[str, object] | None = None
    proposed_canonical_collection: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class HistoricalAuditSummary:
    sheets_analyzed: int
    rows_analyzed: int
    valid_protocols: int
    no_change: int
    total_updates: int
    pending_review: int
    pdf_not_found: int
    protocol_missing: int
    duplicate_protocols: int
    conflicts: int = 0
    unexpected_errors: int = 0
    classifications: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HistoricalAuditResult:
    workbook_fingerprint: str
    items: tuple[HistoricalEquipmentAuditItem, ...]
    summary: HistoricalAuditSummary
    created_at: str


@dataclass(frozen=True, slots=True)
class BackfillApplyItemResult:
    workbook_sheet: str
    workbook_row: int
    protocol: str | None
    action: BackfillAction
    previous_module_text: str
    final_module_text: str
    previous_inverter_text: str
    final_inverter_text: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BackfillApplyResult:
    status: BackfillApplyStatus
    items: tuple[BackfillApplyItemResult, ...] = ()
    backup_path: Path | None = None
    original_hash: str | None = None
    backup_hash: str | None = None
    final_hash: str | None = None
    planned_updates: int = 0
    applied_updates: int = 0
    conflicts: int = 0
    verification_passed: bool = False
    rollback_available: bool = False
    execution_id: str | None = None
    plan_hash: str | None = None
    plan_version: int | None = None
    technical_processing_format_version: int | None = None
    equipment_rules_version: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    updates_by_sheet: dict[str, int] = field(default_factory=dict)
    fingerprint_conflicts: int = 0
    unexpected_changes: int = 0
    confirmation_received: bool = False
    rollback_executed: bool = False
    rollback_status: str = "NOT_REQUIRED"
    idempotency_result: str = "NOT_CHECKED"
    error_code: str | None = None
    message: str | None = None

    @classmethod
    def preflight_abort(
        cls,
        *,
        error_code: str,
        message: str,
        **metadata: Any,
    ) -> BackfillApplyResult:
        return cls(
            status=BackfillApplyStatus.ABORTED_PRE_FLIGHT,
            error_code=error_code,
            message=message,
            **metadata,
        )
