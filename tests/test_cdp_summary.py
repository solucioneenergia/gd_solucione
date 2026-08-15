from datetime import datetime
from pathlib import Path

from automacao_gd.domain.models import PortalSolicitation
from automacao_gd.infrastructure.portal import cdp_summary


def _record() -> PortalSolicitation:
    return PortalSolicitation(
        protocol="2600000001",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        consumer_unit_code="12345",
        address="Rua Teste",
        entry_date="10/01/2026",
        page_number=2,
        row_index=4,
        selection_reason="eligible",
    )


def test_initial_download_summary_preserves_operational_defaults(tmp_path: Path) -> None:
    started_at = datetime(2026, 8, 15, 7, 30, 0)

    summary = cdp_summary.initial_download_summary(
        started_at,
        tmp_path,
        max_completed=5,
        reprocess_existing_pdfs=False,
        process_existing_after_skip=True,
    )

    assert summary["started_at"] == "2026-08-15T07:30:00"
    assert summary["finished_at"] is None
    assert summary["downloads_root"] == str(tmp_path)
    assert summary["max_completed_to_process"] == 5
    assert summary["total_processed"] == 0
    assert summary["total_errors"] == 0
    assert summary["aborted"] is False
    assert summary["selected_protocols"] == []
    assert summary["pagination_diagnostics"] == []
    assert summary["results"] == []


def test_download_result_from_record_preserves_all_default_fields() -> None:
    result = cdp_summary.download_result_from_record(_record())

    assert result["protocol"] == "2600000001"
    assert result["client_name"] == "CLIENTE SINTETICO LTDA"
    assert result["status"] == "CONCLUIDA"
    assert result["entry_date"] == "10/01/2026"
    assert result["page_number"] == 2
    assert result["row_index"] == 4
    assert result["selection_reason"] == "eligible"
    assert result["download_status"] == "pending"
    assert result["selected_for_processing"] is False
    assert result["abriu_detalhe"] is False
    assert result["cdp_error"] is None
    assert result["download_error"] is None


def test_refresh_download_totals_counts_processing_errors_and_budget_unavailable(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "Orcamento_de_Conexao_2600000001.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    summary = {
        "duplicates_skipped": [{"protocol": "duplicado"}],
        "results": [
            {
                "download_status": "downloaded",
                "process_pdf_path": str(pdf_path),
            },
            {
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf_path),
                "skipped_download": True,
            },
            {
                "download_status": "budget_unavailable",
                "selected_for_processing": True,
                "completion_date_raw": "10/01/2026",
            },
            {
                "download_status": "skipped_origin_page_unavailable",
                "navigation_error": "origin page unavailable",
            },
            {
                "download_status": "download_error",
                "download_error": "invalid pdf",
            },
        ],
    }

    cdp_summary.refresh_download_totals(summary)

    assert summary["total_processed"] == 5
    assert summary["total_downloaded"] == 1
    assert summary["total_existing_reused"] == 1
    assert summary["total_skipped_existing"] == 1
    assert summary["total_skipped_duplicate"] == 1
    assert summary["total_skipped_origin_page_unavailable"] == 1
    assert summary["total_for_processing"] == 2
    assert summary["total_metadata_only_for_processing"] == 1
    assert summary["total_budget_unavailable"] == 1
    assert summary["total_sent_to_processing"] == 3
    assert summary["total_cdp_errors"] == 1
    assert summary["total_download_errors"] == 1
    assert summary["total_errors"] == 2
