import hashlib
from pathlib import Path

from automacao_gd.application.op5_contracts import (
    BatchAuthorization,
    FrozenBatchScopeError,
    FrozenPdfArtifact,
    FrozenPdfScope,
    FrozenProtocolBatch,
    LimitedProtocolSelection,
    file_sha256,
)


VALID_DOWNLOAD_STATUSES_FOR_PROCESSING = {"downloaded", "existing_pdf_after_skip"}
METADATA_ONLY_DOWNLOAD_STATUSES_FOR_PROCESSING = {"budget_unavailable"}


def pdf_paths_for_processing(download_summary: dict) -> list[Path]:
    selected: list[Path] = []
    seen_protocols: set[str] = set()
    seen: set[str] = set()
    for item in download_summary.get("results", []):
        protocol = str(item.get("protocol") or "")
        if not protocol or protocol in seen_protocols:
            continue
        if item.get("download_status") not in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING:
            continue
        if item.get("cdp_error"):
            continue
        raw_path = item.get("process_pdf_path")
        if not raw_path:
            continue
        path = Path(raw_path)
        if not is_valid_pdf(path):
            continue
        key = str(path.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen_protocols.add(protocol)
        seen.add(key)
        selected.append(path)
    return selected


def download_item_is_metadata_only_for_processing(item: dict) -> bool:
    return (
        item.get("download_status") in METADATA_ONLY_DOWNLOAD_STATUSES_FOR_PROCESSING
        and bool(item.get("selected_for_processing"))
        and bool(
            item.get("completion_date")
            or item.get("completion_date_raw")
            or item.get("completion_date_normalized")
        )
    )


def metadata_only_protocols_for_processing(download_summary: dict) -> set[str]:
    return {
        str(item.get("protocol") or "")
        for item in download_summary.get("results") or []
        if str(item.get("protocol") or "")
        and download_item_is_metadata_only_for_processing(item)
    }


def freeze_selected_pdf_scope(
    download_summary: dict,
    frozen_batch: FrozenProtocolBatch,
) -> FrozenPdfScope:
    return freeze_selected_pdf_or_metadata_scope(download_summary, frozen_batch)


def freeze_selected_pdf_or_metadata_scope(
    download_summary: dict,
    frozen_batch: FrozenProtocolBatch,
) -> FrozenPdfScope:
    download_by_protocol = {
        str(item.get("protocol") or ""): item
        for item in download_summary.get("results") or []
        if isinstance(item, dict) and str(item.get("protocol") or "")
    }
    artifacts: list[FrozenPdfArtifact] = []
    for protocol in frozen_batch.protocols:
        item = download_by_protocol.get(protocol)
        if not item:
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "Lote congelado contem protocolo sem registro de download.",
            )
        if download_item_is_metadata_only_for_processing(item):
            continue
        raw_path = item.get("process_pdf_path")
        path = Path(str(raw_path or ""))
        if (
            item.get("download_status") not in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING
            or item.get("cdp_error")
            or not raw_path
            or not is_valid_pdf(path)
        ):
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "PDFs validos ou metadados seguros nao correspondem ao lote congelado.",
            )
        artifacts.append(
            FrozenPdfArtifact(
                protocol=protocol,
                path=path.resolve(strict=True),
                sha256=file_sha256(path),
            )
        )
    digest_source = "\n".join(
        f"{artifact.protocol}:{artifact.sha256}" for artifact in artifacts
    )
    return FrozenPdfScope(
        artifacts=tuple(artifacts),
        digest=hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
    )


def freeze_selected_pdf_scope_legacy_unreachable(
    download_summary: dict,
    frozen_batch: FrozenProtocolBatch,
) -> FrozenPdfScope:
    paths = pdf_paths_for_processing(download_summary)
    if len(paths) != len(frozen_batch.protocols):
        raise FrozenBatchScopeError(
            "FROZEN_BATCH_SCOPE_VIOLATION",
            "PDFs válidos não correspondem ao lote de protocolos congelado.",
        )
    artifacts = tuple(
        FrozenPdfArtifact(
            protocol=protocol,
            path=path.resolve(strict=True),
            sha256=file_sha256(path),
        )
        for protocol, path in zip(frozen_batch.protocols, paths, strict=True)
    )
    digest_source = "\n".join(
        f"{artifact.protocol}:{artifact.sha256}" for artifact in artifacts
    )
    return FrozenPdfScope(
        artifacts=artifacts,
        digest=hashlib.sha256(digest_source.encode("utf-8")).hexdigest(),
    )


def attach_frozen_pdf_scope(download_summary: dict, scope: FrozenPdfScope) -> None:
    download_summary["frozen_pdf_scope_digest"] = scope.digest
    download_summary["frozen_pdf_scope_count"] = len(scope.artifacts)
    download_summary["frozen_pdf_scope"] = {
        "digest": scope.digest,
        "artifacts": [
            {
                "protocol": artifact.protocol,
                "path": str(artifact.path),
                "sha256": artifact.sha256,
            }
            for artifact in scope.artifacts
        ],
    }


def apply_authorized_global_protocol_limit(
    download_summary: dict,
    authorization: BatchAuthorization,
    *,
    processing_limit: int | None = None,
) -> LimitedProtocolSelection:
    """Limit the final processing set and freeze the selected protocol batch."""
    limit = int(processing_limit or authorization.requested_batch_limit)
    results = download_summary.get("results") or []
    selected_protocols: list[str] = []
    dropped_protocols: list[str] = []
    seen_processable_protocols: set[str] = set()

    for item in results:
        if not download_item_sent_to_processing(item):
            item["selected_by_global_limit"] = False
            item.setdefault("global_limit_status", "not_processable")
            continue

        protocol = str(item.get("protocol") or "")
        if not protocol:
            exclude_from_processing(item, "missing_protocol")
            continue
        if protocol in seen_processable_protocols:
            exclude_from_processing(item, "duplicate_protocol")
            continue

        seen_processable_protocols.add(protocol)
        if limit > 0 and len(selected_protocols) >= limit:
            exclude_from_processing(item, "excluded_by_global_limit")
            dropped_protocols.append(protocol)
            continue

        item["selected_by_global_limit"] = True
        item["global_limit_status"] = "selected"
        selected_protocols.append(protocol)

    download_summary["global_protocol_limit"] = authorization.requested_batch_limit
    download_summary["planning_candidate_limit"] = limit
    download_summary["global_protocol_limit_enforced"] = True
    download_summary["requested_batch_limit"] = authorization.requested_batch_limit
    download_summary["authorized_batch_limit"] = authorization.authorized_batch_limit
    download_summary["authorization_scope"] = authorization.authorization_scope
    download_summary["protocols_selected_by_global_limit"] = selected_protocols
    download_summary["total_protocols_selected_by_global_limit"] = len(
        selected_protocols
    )
    download_summary["protocols_dropped_by_global_limit"] = dropped_protocols
    download_summary["total_protocols_dropped_by_global_limit"] = len(
        dropped_protocols
    )
    download_summary["protocols_unique_before_limit"] = len(seen_processable_protocols)
    download_summary["protocols_added_after_limit"] = 0
    download_summary["duplicate_protocols_in_frozen_batch"] = 0
    frozen_batch = FrozenProtocolBatch(
        requested_limit=limit,
        authorized_limit=authorization.authorized_batch_limit,
        authorization_scope=authorization.authorization_scope,
        protocols=tuple(selected_protocols),
        unique_before_limit=len(seen_processable_protocols),
        dropped_by_limit=len(dropped_protocols),
        duplicate_protocols_in_frozen_batch=0,
        protocols_added_after_freeze=0,
    )
    download_summary["frozen_batch_created"] = True
    download_summary["frozen_batch"] = {
        "requested_limit": frozen_batch.requested_limit,
        "authorized_limit": frozen_batch.authorized_limit,
        "authorization_scope": frozen_batch.authorization_scope,
        "protocols": list(frozen_batch.protocols),
        "unique_before_limit": frozen_batch.unique_before_limit,
        "dropped_by_limit": frozen_batch.dropped_by_limit,
        "duplicate_protocols_in_frozen_batch": (
            frozen_batch.duplicate_protocols_in_frozen_batch
        ),
        "protocols_added_after_freeze": frozen_batch.protocols_added_after_freeze,
    }
    refresh_processing_selection_totals(download_summary)
    return LimitedProtocolSelection(summary=download_summary, frozen_batch=frozen_batch)


def apply_global_protocol_limit(download_summary: dict, max_protocols: int) -> dict:
    """Compatibility wrapper for the historical limit helper."""
    authorization = BatchAuthorization(
        requested_batch_limit=int(max_protocols or 0),
        authorized_batch_limit=max(1, int(max_protocols or 0)),
        authorization_scope="LEGACY_COMPATIBILITY_LIMIT",
    )
    return apply_authorized_global_protocol_limit(download_summary, authorization).summary


def exclude_from_processing(item: dict, status: str) -> None:
    item["selected_by_global_limit"] = False
    item["global_limit_status"] = status
    item["process_pdf_path"] = None
    item["selected_for_processing"] = False
    item["processing_reason"] = status


def refresh_processing_selection_totals(download_summary: dict) -> None:
    results = download_summary.get("results") or []
    total_pdf_for_processing = sum(
        1
        for item in results
        if item.get("download_status") in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING
        and download_item_sent_to_processing(item)
    )
    total_metadata_only = sum(
        1 for item in results if download_item_is_metadata_only_for_processing(item)
    )
    total_for_processing = total_pdf_for_processing + total_metadata_only
    download_summary["total_for_processing"] = total_for_processing
    download_summary["total_pdf_for_processing"] = total_pdf_for_processing
    download_summary["total_metadata_only_for_processing"] = total_metadata_only
    download_summary["total_sent_to_processing"] = total_for_processing


def is_valid_pdf(path: Path) -> bool:
    return path.exists() and path.is_file() and path.suffix.lower() == ".pdf"


def download_item_sent_to_processing(download_item: dict) -> bool:
    if download_item_is_metadata_only_for_processing(download_item):
        return True
    raw_path = download_item.get("process_pdf_path")
    return bool(
        download_item.get("download_status") in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING
        and raw_path
        and not download_item.get("cdp_error")
        and is_valid_pdf(Path(raw_path))
    )
