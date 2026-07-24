import json
from pathlib import Path
from typing import Any

from automacao_gd.infrastructure.dates import date_to_iso
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


METADATA_FILE_NAME = "metadata.json"
DOWNLOADS_LOG_NAME = "downloads_orcamentos_concluidos_cdp.json"
TABLE_LOG_NAME = "registros_cdp_edge.json"


def build_portal_metadata(data: dict[str, Any]) -> dict:
    entry_date_raw = data.get("entry_date_raw") or data.get("entry_date")
    completion_date_raw = data.get("completion_date_raw") or data.get("completion_date")

    return {
        "protocol": data.get("protocol") or data.get("detail_protocol"),
        "client_name": data.get("client_name") or data.get("detail_client_name"),
        "status": data.get("status"),
        "entry_date_raw": entry_date_raw,
        "entry_date": date_to_iso(entry_date_raw),
        "completion_date_raw": completion_date_raw,
        "completion_date": date_to_iso(completion_date_raw),
        "consumer_unit_code": data.get("consumer_unit_code"),
        "address": data.get("address"),
    }


def save_download_metadata(protocol_dir: Path, data: dict[str, Any]) -> Path:
    protocol_dir = Path(protocol_dir)
    protocol_dir.mkdir(parents=True, exist_ok=True)
    metadata = build_portal_metadata(data)
    output_path = protocol_dir / METADATA_FILE_NAME
    atomic_write_json(output_path, metadata, private=True)
    logger.info(f"Metadados do portal salvos em: {output_path}")
    return output_path


def load_portal_metadata(
    downloads_root: Path, logs_dir: Path, protocol: str
) -> tuple[dict | None, str | None]:
    metadata_path = Path(downloads_root) / protocol / METADATA_FILE_NAME
    metadata = _load_json(metadata_path)
    if isinstance(metadata, dict):
        return build_portal_metadata(metadata), str(metadata_path)

    downloads_log = _load_json(Path(logs_dir) / DOWNLOADS_LOG_NAME)
    if isinstance(downloads_log, dict):
        for item in downloads_log.get("results", []):
            if str(item.get("protocol")) == str(protocol):
                return build_portal_metadata(item), str(Path(logs_dir) / DOWNLOADS_LOG_NAME)

    table_log = _load_json(Path(logs_dir) / TABLE_LOG_NAME)
    if isinstance(table_log, list):
        for item in table_log:
            if str(item.get("protocol")) == str(protocol):
                return build_portal_metadata(item), str(Path(logs_dir) / TABLE_LOG_NAME)

    return None, None


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Não foi possível ler JSON de metadados '{path}': {exc}")
        return None
