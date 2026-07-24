from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automacao_gd.domain.equipment import EQUIPMENT_FORMAT_VERSION
from automacao_gd.domain.equipment_semantics import format_canonical_collection
from automacao_gd.domain.equipment_validation import validate_canonical_equipment
from automacao_gd.domain.models import GenerationData
from automacao_gd.infrastructure.excel.service import (
    create_workbook_backup,
    update_excel_equipment_columns,
)
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.pdf.service import (
    extract_pdf_text,
    parse_generation_data_from_text,
)


REFORMAT_ACTION = "reformat_existing_excel_row"


def reformat_completed_protocol_if_needed(
    *,
    protocol: str,
    state_entry: dict[str, Any] | None,
    workbook_path: Path | None = None,
    state_store: Any | None = None,
    downloads_root: Path | None = None,
    pdf_path: Path | str | None = None,
    metadata: dict[str, Any] | GenerationData | None = None,
    dry_run: bool = True,
    apply_changes: bool = True,
    backup_path: Path | None = None,
    backup_excel: bool = True,
    old_excel_cells: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reformata Placa/Inversor de protocolo completed legado sem download/arquivo.

    A fonte técnica aceita é metadata estruturado ou PDF local já existente. O texto
    antigo da planilha é aceito apenas para relatório, nunca para reconstruir pares.
    """
    entry = state_entry or {}
    result = _base_result(protocol, dry_run)
    result["previous_equipment_format_version"] = _equipment_format_version(entry)

    if entry.get("status") != "completed":
        result.update(
            {
                "action": "skipped_not_completed",
                "reformat_status": "skipped_not_completed",
                "warning": "Protocolo não está completed no state.",
            }
        )
        return result

    if not needs_equipment_reformat(entry):
        result.update(
            {
                "success": True,
                "action": "skipped_already_v2",
                "reformat_status": "skipped_already_v2",
                "equipment_format_version": EQUIPMENT_FORMAT_VERSION,
            }
        )
        return result

    source = _load_generation_data_source(
        protocol=protocol,
        state_entry=entry,
        metadata=metadata,
        pdf_path=pdf_path,
        downloads_root=downloads_root,
    )
    result["source_used"] = source["source_used"]
    result["source_path"] = source.get("source_path")

    if source.get("error"):
        result.update(
            {
                "action": "pending_manual_review",
                "reformat_status": "pending_review",
                "warning": source["error"],
            }
        )
        _update_reformat_state(
            state_store,
            protocol,
            result,
            dry_run=dry_run,
            success=False,
        )
        return result

    generation_data: GenerationData = source["generation_data"]
    collection = generation_data.to_canonical_collection()
    module_text, inverter_text = format_canonical_collection(collection)
    validation = validate_canonical_equipment(
        collection,
        module_text,
        inverter_text,
        module_source=generation_data.module_source,
        inverter_source=generation_data.inverter_source,
    )
    if not validation.approved:
        result.update(
            {
                "action": "pending_manual_review",
                "reformat_status": "pending_review",
                "warning": "; ".join(validation.errors),
            }
        )
        _update_reformat_state(
            state_store,
            protocol,
            result,
            dry_run=dry_run,
            success=False,
        )
        return result
    result.update(
        {
            "action": REFORMAT_ACTION,
            "placa_nova": module_text,
            "inversor_novo": inverter_text,
            "warning": "; ".join(validation.warnings) or None,
        }
    )
    if old_excel_cells:
        result["placa_antiga"] = old_excel_cells.get("Placa")
        result["inversor_antigo"] = old_excel_cells.get("Inversor")

    if workbook_path is None:
        result.update(
            {
                "reformat_status": "failed",
                "error": "workbook_path é obrigatório para reformatação.",
            }
        )
        _update_reformat_state(
            state_store,
            protocol,
            result,
            dry_run=dry_run,
            success=False,
        )
        return result

    effective_backup_path = backup_path
    if (
        effective_backup_path is None
        and not dry_run
        and apply_changes
        and backup_excel
        and Path(workbook_path).exists()
    ):
        effective_backup_path = create_workbook_backup(Path(workbook_path))

    excel_status = update_excel_equipment_columns(
        workbook_path=Path(workbook_path),
        protocol=protocol,
        module_text=module_text,
        inverter_text=inverter_text,
        dry_run=dry_run,
        backup_path=effective_backup_path,
        apply_changes=apply_changes,
    )
    result["excel_status"] = excel_status
    result.update(
        {
            "worksheet": excel_status.get("worksheet"),
            "row": excel_status.get("row_number"),
            "placa_antiga": excel_status.get("old_module_text", result.get("placa_antiga")),
            "placa_nova": excel_status.get("new_module_text", result.get("placa_nova")),
            "inversor_antigo": excel_status.get(
                "old_inverter_text", result.get("inversor_antigo")
            ),
            "inversor_novo": excel_status.get(
                "new_inverter_text", result.get("inversor_novo")
            ),
            "updated_columns": excel_status.get("updated_columns", ["Placa", "Inversor"]),
        }
    )

    if excel_status.get("success"):
        result.update(
            {
                "success": True,
                "reformat_status": "success" if not dry_run else "planned",
                "equipment_format_version": EQUIPMENT_FORMAT_VERSION,
                "reformatted_at": _utc_now_iso() if not dry_run else None,
            }
        )
        _update_reformat_state(
            state_store,
            protocol,
            result,
            dry_run=dry_run,
            success=True,
        )
        return result

    result.update(
        {
            "reformat_status": "failed",
            "error": excel_status.get("error") or "Falha ao atualizar Placa/Inversor.",
        }
    )
    _update_reformat_state(
        state_store,
        protocol,
        result,
        dry_run=dry_run,
        success=False,
    )
    return result


def needs_equipment_reformat(
    state_entry: dict[str, Any] | None,
    *,
    target_version: int = EQUIPMENT_FORMAT_VERSION,
) -> bool:
    if not state_entry or state_entry.get("status") != "completed":
        return False
    current = _equipment_format_version(state_entry)
    return current is None or current < target_version


def select_completed_protocols_for_equipment_reformat(
    state: dict[str, Any],
    *,
    target_version: int = EQUIPMENT_FORMAT_VERSION,
) -> list[str]:
    protocols = state.get("protocols", {}) if isinstance(state, dict) else {}
    return [
        str(protocol)
        for protocol, entry in protocols.items()
        if isinstance(entry, dict)
        and needs_equipment_reformat(entry, target_version=target_version)
    ]


def _base_result(protocol: str, dry_run: bool) -> dict[str, Any]:
    return {
        "success": False,
        "protocol": str(protocol),
        "action": None,
        "dry_run": dry_run,
        "equipment_format_version": None,
        "previous_equipment_format_version": None,
        "source_used": None,
        "source_path": None,
        "reformat_status": None,
        "reformatted_at": None,
        "worksheet": None,
        "row": None,
        "updated_columns": [],
        "placa_antiga": None,
        "placa_nova": None,
        "inversor_antigo": None,
        "inversor_novo": None,
        "warning": None,
        "error": None,
        "excel_status": None,
    }


def _load_generation_data_source(
    *,
    protocol: str,
    state_entry: dict[str, Any],
    metadata: dict[str, Any] | GenerationData | None,
    pdf_path: Path | str | None,
    downloads_root: Path | None,
) -> dict[str, Any]:
    metadata_source = metadata or _metadata_from_state_entry(state_entry)
    generation_data = _generation_data_from_metadata(metadata_source)
    if generation_data is not None:
        return {
            "source_used": "metadata",
            "generation_data": generation_data,
        }

    local_pdf = _resolve_local_pdf(protocol, state_entry, pdf_path, downloads_root)
    if local_pdf:
        try:
            text = extract_pdf_text(local_pdf)
            return {
                "source_used": "local_pdf",
                "source_path": str(local_pdf),
                "generation_data": parse_generation_data_from_text(text),
            }
        except Exception as exc:
            logger.warning(f"Falha ao usar PDF local para reformatação {protocol}: {exc}")
            return {
                "source_used": "local_pdf",
                "source_path": str(local_pdf),
                "error": str(exc),
            }

    return {
        "source_used": "none",
        "error": "Metadata técnica e PDF local inexistentes; conferência manual necessária.",
    }


def _metadata_from_state_entry(state_entry: dict[str, Any]) -> Any:
    technical = state_entry.get("technical_processing")
    candidates = [
        state_entry.get("technical_metadata"),
        state_entry.get("generation_data"),
        technical.get("generation_data") if isinstance(technical, dict) else None,
        technical.get("raw_text") if isinstance(technical, dict) else None,
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _generation_data_from_metadata(metadata: Any) -> GenerationData | None:
    if isinstance(metadata, GenerationData):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        return parse_generation_data_from_text(metadata)
    if not isinstance(metadata, dict):
        return None

    raw_text = metadata.get("raw_text") or metadata.get("pdf_text")
    if raw_text:
        return parse_generation_data_from_text(str(raw_text))

    payload = metadata.get("generation_data") if isinstance(metadata.get("generation_data"), dict) else metadata
    try:
        data = GenerationData.model_validate(payload)
    except Exception:
        return None

    if data.modules or data.inverters or data.microinverters:
        return data
    if data.module_manufacturer or data.module_model or data.inverter_manufacturer or data.inverter_model:
        return data
    return None


def _resolve_local_pdf(
    protocol: str,
    state_entry: dict[str, Any],
    pdf_path: Path | str | None,
    downloads_root: Path | None,
) -> Path | None:
    candidates: list[Path] = []
    if pdf_path:
        candidates.append(Path(pdf_path))

    download = state_entry.get("download")
    if isinstance(download, dict):
        for key in ("pdf_path", "downloaded_pdf_path", "process_pdf_path"):
            if download.get(key):
                candidates.append(Path(download[key]))

    if downloads_root:
        root = Path(downloads_root)
        candidates.extend(
            sorted((root / str(protocol)).glob(f"Orcamento_de_Conexao_{protocol}*.pdf"))
        )

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _update_reformat_state(
    state_store: Any | None,
    protocol: str,
    result: dict[str, Any],
    *,
    dry_run: bool,
    success: bool,
) -> None:
    if state_store is None or dry_run:
        return

    data = {
        "last_action": result.get("action"),
        "source_used": result.get("source_used"),
        "reformat_status": result.get("reformat_status"),
        "reformat_warning": result.get("warning"),
        "reformat_error": result.get("error"),
        "reformatted_at": result.get("reformatted_at"),
    }
    if success:
        data["equipment_format_version"] = EQUIPMENT_FORMAT_VERSION

    if hasattr(state_store, "update_protocol"):
        state_store.update_protocol(
            protocol,
            status="completed",
            last_step=result.get("action") or "equipment_reformat",
            data=data,
        )


def _equipment_format_version(state_entry: dict[str, Any]) -> int | None:
    value = state_entry.get("equipment_format_version")
    if value is None and isinstance(state_entry.get("technical_processing"), dict):
        value = state_entry["technical_processing"].get("equipment_format_version")
    if value is None and isinstance(state_entry.get("technical_processing"), dict):
        value = state_entry["technical_processing"].get("format_version")
    if isinstance(value, (int, str)):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
