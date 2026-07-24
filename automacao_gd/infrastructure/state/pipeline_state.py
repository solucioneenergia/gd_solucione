import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.domain.equipment import EQUIPMENT_FORMAT_VERSION
from automacao_gd.domain.equipment_cache import load_validated_equipment_cache
from automacao_gd.infrastructure.excel.service import validate_excel_protocol_updated
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


STATE_VERSION = 1
DEFAULT_STATE_PATH = Path("data/state/pipeline_cdp_state.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_force_reprocess_protocols(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        item.strip()
        for item in str(value).split(",")
        if item.strip()
    }


def initial_state() -> dict:
    return {
        "version": STATE_VERSION,
        "updated_at": utc_now_iso(),
        "runs": [],
        "protocols": {},
    }


def _default_protocol_steps() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "input_fingerprint": None,
            "output_fingerprint": None,
            "error": None,
            "equipment_format_version": EQUIPMENT_FORMAT_VERSION,
        }
        for name in [
            "discovered",
            "selected",
            "detail_opened",
            "pdf_downloaded",
            "pdf_validated",
            "pdf_parsed",
            "client_folder_resolved",
            "excel_updated",
            "pdf_archived",
            "completed",
        ]
    ]


class PipelineStateStore:
    def __init__(
        self,
        path: Path | None = None,
        resume: bool = True,
        reset: bool = False,
        force_reprocess_protocols: set[str] | None = None,
    ) -> None:
        settings = get_settings()
        self.path = settings.resolve_path(path or DEFAULT_STATE_PATH)
        self.force_reprocess_protocols = force_reprocess_protocols or set()

        if reset:
            self.state = initial_state()
            self.save()
        elif resume and self.path.exists():
            self.state = self._load()
        else:
            self.state = initial_state()

    def _load(self) -> dict:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("state nao e um objeto JSON")
            loaded.setdefault("version", STATE_VERSION)
            loaded.setdefault("updated_at", utc_now_iso())
            loaded.setdefault("runs", [])
            loaded.setdefault("protocols", {})
            return loaded
        except Exception as exc:
            logger.warning(f"Falha ao carregar state '{self.path}': {exc}")
            return initial_state()

    def save(self) -> None:
        self.state["updated_at"] = utc_now_iso()
        atomic_write_json(self.path, self.state, private=True)

    def start_run(self, config: dict[str, Any] | None = None) -> str:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.state.setdefault("runs", []).append(
            {
                "run_id": run_id,
                "started_at": utc_now_iso(),
                "config": config or {},
            }
        )
        self.save()
        return run_id

    def protocol_entry(self, protocol: str) -> dict:
        protocols = self.state.setdefault("protocols", {})
        if protocol not in protocols:
            protocols[protocol] = {
                "protocol": protocol,
                "status": "pending",
                "last_step": None,
                "errors": [],
                "steps": _default_protocol_steps(),
            }
        protocols[protocol].setdefault("errors", [])
        protocols[protocol].setdefault("steps", _default_protocol_steps())
        return protocols[protocol]

    def get_protocol(self, protocol: str) -> dict | None:
        entry = self.state.get("protocols", {}).get(protocol)
        return entry if isinstance(entry, dict) else None

    def is_force_reprocess(self, protocol: str) -> bool:
        return protocol in self.force_reprocess_protocols

    def mark_discovered(self, record: Any) -> dict:
        protocol = str(record.protocol)
        entry = self.protocol_entry(protocol)
        now = utc_now_iso()
        entry.update(
            {
                "protocol": protocol,
                "client_name": getattr(record, "client_name", None),
                "entry_date": getattr(record, "entry_date", None),
                "portal_status": getattr(record, "status", None),
                "page_number": getattr(record, "page_number", None),
                "row_index": getattr(record, "row_index", None),
                "updated_at": now,
            }
        )
        entry.setdefault("started_at", now)
        if entry.get("status") != "completed":
            entry["status"] = "discovered"
            entry["last_step"] = "discovered"
        self.save()
        return entry

    def mark_selected(self, record: Any) -> dict:
        protocol = str(record.protocol)
        entry = self.protocol_entry(protocol)
        now = utc_now_iso()
        entry.update(
            {
                "protocol": protocol,
                "client_name": getattr(record, "client_name", None),
                "entry_date": getattr(record, "entry_date", None),
                "portal_status": getattr(record, "status", None),
                "page_number": getattr(record, "page_number", None),
                "row_index": getattr(record, "row_index", None),
                "updated_at": now,
            }
        )
        entry.setdefault("selected_at", now)
        if entry.get("status") not in {"completed"}:
            entry["status"] = "in_progress"
            entry["last_step"] = "selected"
        self.save()
        return entry

    def update_section(
        self,
        protocol: str,
        section: str,
        data: dict[str, Any],
        *,
        last_step: str | None = None,
        status: str | None = None,
    ) -> dict:
        entry = self.protocol_entry(protocol)
        current = entry.get(section)
        if not isinstance(current, dict):
            current = {}
        current.update(data)
        entry[section] = current
        if last_step:
            entry["last_step"] = last_step
        if status:
            entry["status"] = status
        elif entry.get("status") not in {"completed"}:
            entry["status"] = "in_progress"
        entry["updated_at"] = utc_now_iso()
        self.save()
        return entry

    def update_protocol(
        self,
        protocol: str,
        *,
        status: str | None = None,
        last_step: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict:
        entry = self.protocol_entry(protocol)
        if data:
            entry.update(data)
        if status:
            entry["status"] = status
        if last_step:
            entry["last_step"] = last_step
        entry["updated_at"] = utc_now_iso()
        self.save()
        return entry

    def add_error(
        self, protocol: str, step: str, message: str, *, status: str = "failed"
    ) -> dict:
        entry = self.protocol_entry(protocol)
        entry.setdefault("errors", []).append(
            {
                "step": step,
                "message": message,
                "created_at": utc_now_iso(),
            }
        )
        entry["status"] = status
        entry["last_step"] = "failed"
        entry["updated_at"] = utc_now_iso()
        self.save()
        return entry

    def mark_completed(self, protocol: str, last_step: str = "completed") -> dict:
        return self.update_protocol(
            protocol,
            status="completed",
            last_step=last_step,
        )

    def needs_equipment_reformat(
        self,
        protocol: str,
        *,
        target_version: int = EQUIPMENT_FORMAT_VERSION,
    ) -> bool:
        entry = self.get_protocol(protocol)
        if not entry or entry.get("status") != "completed":
            return False
        current = _equipment_format_version(entry)
        return current is None or current < target_version

    def next_step_for_protocol(
        self,
        protocol: str,
        *,
        current_fingerprint: str | None = None,
        equipment_format_version: int = EQUIPMENT_FORMAT_VERSION,
        previous_equipment_format_version: int | None = None,
    ) -> str:
        entry = self.get_protocol(protocol)
        if previous_equipment_format_version is not None:
            if previous_equipment_format_version < equipment_format_version:
                return "reformat_existing_excel_row"
            return "completed"
        if entry and self.needs_equipment_reformat(
            protocol, target_version=equipment_format_version
        ):
            return "reformat_existing_excel_row"
        if entry and entry.get("status") == "completed":
            return "completed"
        return "selected"

    def should_skip_completed(self, protocol: str, settings=None) -> bool:
        settings = settings or get_settings()
        if self.is_force_reprocess(protocol):
            return False
        if not settings.SKIP_ALREADY_COMPLETED:
            return False
        entry = self.get_protocol(protocol)
        if not entry or entry.get("status") != "completed":
            return False
        return self.validate_completed_artifacts(protocol, settings)

    def validate_completed_artifacts(self, protocol: str, settings=None) -> bool:
        settings = settings or get_settings()
        entry = self.get_protocol(protocol)
        if not entry:
            return False

        if not _metadata_exists(entry, settings, protocol):
            return False
        if not _download_pdf_exists(entry, settings, protocol):
            return False
        technical = entry.get("technical_processing", {})
        if technical.get("status") != "success":
            return False
        if load_validated_equipment_cache(technical) is None:
            return False
        if not _client_folder_valid(entry):
            return False
        if settings.APPLY_EXCEL and not _excel_valid(entry, settings, protocol):
            return False
        if settings.APPLY_ARCHIVE and not _archive_valid(entry):
            return False
        return True


def _metadata_exists(entry: dict, settings, protocol: str) -> bool:
    path = entry.get("metadata", {}).get("path")
    candidates = [Path(path)] if path else []
    candidates.append(settings.downloads_dir_path / protocol / "metadata.json")
    return any(candidate.exists() for candidate in candidates)


def _download_pdf_exists(entry: dict, settings, protocol: str) -> bool:
    path = entry.get("download", {}).get("pdf_path")
    candidates = [Path(path)] if path else []
    candidates.extend(
        sorted((settings.downloads_dir_path / protocol).glob(
            f"Orcamento_de_Conexao_{protocol}*.pdf"
        ))
    )
    return any(
        candidate.exists()
        and candidate.is_file()
        and candidate.suffix.lower() == ".pdf"
        for candidate in candidates
    )


def _client_folder_valid(entry: dict) -> bool:
    folder = entry.get("client_folder", {})
    matched_path = folder.get("matched_path")
    if not matched_path:
        return True
    return Path(matched_path).exists()


def _excel_valid(entry: dict, settings, protocol: str) -> bool:
    excel = entry.get("excel", {})
    if excel.get("status") not in {
        "applied",
        "already_updated",
        "skipped_excel_already_updated",
    }:
        return False
    technical = entry.get("technical_processing", {})
    validation = validate_excel_protocol_updated(
        workbook_path=settings.planilha_path,
        protocol=protocol,
        entry_date=entry.get("entry_date"),
        module_text=technical.get("placa_planilha"),
        inverter_text=technical.get("inversor_planilha"),
    )
    return bool(validation.get("success") and validation.get("already_updated"))


def _archive_valid(entry: dict) -> bool:
    archive = entry.get("archive", {})
    archived_pdf_path = archive.get("archived_pdf_path")
    if archive.get("status") in {"archived", "already_done"}:
        return bool(archived_pdf_path and Path(archived_pdf_path).exists())
    return False


def _equipment_format_version(entry: dict) -> int | None:
    value = entry.get("equipment_format_version")
    technical = entry.get("technical_processing")
    if value is None and isinstance(technical, dict):
        value = technical.get("equipment_format_version")
    if value is None and isinstance(technical, dict):
        value = technical.get("format_version")
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
