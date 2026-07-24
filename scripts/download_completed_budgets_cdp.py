import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError

from automacao_gd.infrastructure.portal.cdp_service import (
    download_completed_budgets_from_current_page,
)
from automacao_gd.infrastructure.portal.factory import create_portal_automation
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import logger, setup_logger
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json


OUTPUT_FILE_NAME = "downloads_orcamentos_concluidos_cdp.json"


def main() -> None:
    ensure_directories()
    setup_logger()
    settings = get_settings()

    summary = run_download_completed_budgets_cdp()
    output_path = _save_summary(settings.logs_dir_path, summary)
    print(f"JSON consolidado salvo em: {output_path}")


def run_download_completed_budgets_cdp() -> dict:
    settings = get_settings()
    automation = create_portal_automation(settings)
    summary: dict | None = None

    try:
        automation.start_browser()
        page = automation.page

        summary = download_completed_budgets_from_current_page(
            page=page,
            downloads_root=settings.downloads_dir_path,
            max_completed=settings.MAX_COMPLETED_TO_PROCESS,
            reprocess_existing_pdfs=settings.REPROCESS_EXISTING_PDFS,
            process_existing_after_skip=settings.PROCESS_EXISTING_AFTER_SKIP,
        )
        summary["run_error"] = None
        return summary
    except PlaywrightError as exc:
        message = (
            f"Falha de Playwright/CDP. Verifique se o Edge esta aberto em "
            f"{settings.CDP_ENDPOINT}. Detalhe: {exc}"
        )
        logger.error(message)
        return _error_summary(message, summary)
    except Exception as exc:
        logger.exception(f"Falha geral no lote CDP: {exc}")
        return _error_summary(str(exc), summary)
    finally:
        automation.close()


def _error_summary(message: str, partial_summary: dict | None = None) -> dict:
    if partial_summary is not None:
        partial_summary["run_error"] = message
        partial_summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        partial_summary["total_errors"] = partial_summary.get("total_errors", 0) + 1
        return partial_summary

    now = datetime.now().isoformat(timespec="seconds")
    return {
        "started_at": now,
        "finished_at": now,
        "downloads_root": str(get_settings().downloads_dir_path),
        "max_completed_to_process": get_settings().MAX_COMPLETED_TO_PROCESS,
        "reprocess_existing_pdfs": get_settings().REPROCESS_EXISTING_PDFS,
        "process_existing_after_skip": get_settings().PROCESS_EXISTING_AFTER_SKIP,
        "total_rows": 0,
        "total_completed": 0,
        "total_selected": 0,
        "total_processed": 0,
        "total_downloaded": 0,
        "total_existing_reused": 0,
        "total_skipped_existing": 0,
        "total_skipped_duplicate": 0,
        "total_for_processing": 0,
        "total_sent_to_processing": 0,
        "total_cdp_errors": 1,
        "total_download_errors": 0,
        "total_errors": 1,
        "run_error": message,
        "selected_protocols": [],
        "duplicates_skipped": [],
        "results": [],
    }


def _save_summary(logs_dir: Path, summary: dict) -> Path:
    output_path = logs_dir / OUTPUT_FILE_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, summary, private=True)
    logger.info(f"JSON consolidado salvo em {output_path}.")
    return output_path


if __name__ == "__main__":
    main()
