import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.cdp_portal_service import (
    _download_result_from_record,
    _is_unsafe_navigation_url,
    _reuse_existing_pdf_without_detail,
    find_existing_download_metadata,
    should_open_detail_for_budget,
)
from src.models import PortalSolicitation

from automacao_gd.infrastructure.portal import cdp_service
from automacao_gd.infrastructure.portal.browser import PersistentBrowserPortalGDAutomation


def _record(protocol: str = "2601") -> PortalSolicitation:
    return PortalSolicitation(
        protocol=protocol,
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        consumer_unit_code="12345",
        address="Rua Teste",
        entry_date="10/07/2026",
    )


def test_existing_pdf_and_metadata_do_not_open_detail(tmp_path: Path) -> None:
    record = _record()
    protocol_dir = tmp_path / record.protocol
    protocol_dir.mkdir()
    pdf_path = protocol_dir / f"Orcamento_de_Conexao_{record.protocol}.pdf"
    metadata_path = protocol_dir / "metadata.json"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    metadata_path.write_text('{"protocol": "2601"}', encoding="utf-8")
    result = _download_result_from_record(record)

    _reuse_existing_pdf_without_detail(
        record=record,
        existing_pdf=pdf_path,
        downloads_root=tmp_path,
        result=result,
        process_existing_after_skip=True,
    )

    assert result["abriu_detalhe"] is False
    assert result["motivo_nao_abriu_detalhe"] == "skipped_existing_pdf_no_detail"
    assert result["download_status"] == "existing_pdf_after_skip"
    assert result["metadata_created_from_listing"] is False
    assert result["metadata_path"] == str(metadata_path)
    assert result["process_pdf_path"] == str(pdf_path)


def test_existing_pdf_without_metadata_creates_metadata_from_listing(
    tmp_path: Path,
) -> None:
    record = _record()
    protocol_dir = tmp_path / record.protocol
    protocol_dir.mkdir()
    pdf_path = protocol_dir / f"Orcamento_de_Conexao_{record.protocol}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    result = _download_result_from_record(record)

    _reuse_existing_pdf_without_detail(
        record=record,
        existing_pdf=pdf_path,
        downloads_root=tmp_path,
        result=result,
        process_existing_after_skip=True,
    )

    metadata_path = find_existing_download_metadata(record.protocol, tmp_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert result["abriu_detalhe"] is False
    assert result["metadata_created_from_listing"] is True
    assert metadata["protocol"] == record.protocol
    assert metadata["client_name"] == record.client_name
    assert metadata["consumer_unit_code"] == record.consumer_unit_code
    assert metadata["address"] == record.address


def test_missing_pdf_requires_opening_detail(tmp_path: Path) -> None:
    assert should_open_detail_for_budget("2601", tmp_path, False) is True


def test_existing_pdf_and_metadata_skip_opening_detail(tmp_path: Path) -> None:
    protocol_dir = tmp_path / "2601"
    protocol_dir.mkdir()
    (protocol_dir / "Orcamento_de_Conexao_2601.pdf").write_bytes(b"%PDF-1.4\n")
    (protocol_dir / "metadata.json").write_text(
        '{"protocol": "2601", "completion_date_raw": "01/01/2026"}',
        encoding="utf-8",
    )

    assert should_open_detail_for_budget("2601", tmp_path, False) is False


def test_existing_pdf_and_metadata_without_completion_requires_detail_for_op5(
    tmp_path: Path,
) -> None:
    protocol_dir = tmp_path / "2601"
    protocol_dir.mkdir()
    (protocol_dir / "Orcamento_de_Conexao_2601.pdf").write_bytes(b"%PDF-1.4\n")
    (protocol_dir / "metadata.json").write_text(
        '{"protocol": "2601"}',
        encoding="utf-8",
    )

    assert (
        should_open_detail_for_budget(
            "2601",
            tmp_path,
            False,
            require_completion_metadata=True,
        )
        is True
    )


def test_existing_pdf_still_requires_opening_detail_for_completion(tmp_path: Path) -> None:
    protocol_dir = tmp_path / "2601"
    protocol_dir.mkdir()
    (protocol_dir / "Orcamento_de_Conexao_2601.pdf").write_bytes(b"%PDF-1.4\n")

    assert should_open_detail_for_budget("2601", tmp_path, False) is True


def test_unsafe_navigation_urls_are_rejected_for_history() -> None:
    listing_url = "https://gdneoenergiapernambuco.neoenergia.com/minhas"

    assert _is_unsafe_navigation_url("edge://newtab", listing_url) is True
    assert _is_unsafe_navigation_url("chrome://newtab", listing_url) is True
    assert _is_unsafe_navigation_url("about:blank", listing_url) is True
    assert _is_unsafe_navigation_url("https://example.com/home", listing_url) is True
    assert _is_unsafe_navigation_url(listing_url, listing_url) is False


def test_portal_listing_reader_accepts_identification_code_header() -> None:
    source = cdp_service.read_current_page_table_with_row_handles.__code__.co_consts
    script = next(item for item in source if isinstance(item, str) and "mapHeader" in item)

    assert "IDENTIFICACAO" in script


def test_persistent_portal_reader_accepts_identification_code_header() -> None:
    source = PersistentBrowserPortalGDAutomation.read_current_page_table.__code__.co_consts
    script = next(item for item in source if isinstance(item, str) and "mapHeader" in item)

    assert "IDENTIFICACAO" in script


def test_batch_fast_selection_skips_valid_op5_completed_master_index(
    tmp_path: Path,
) -> None:
    completed_protocol = "2600001048"
    new_protocol = "2600001049"
    pdf = tmp_path / "downloads" / completed_protocol / f"Orcamento_de_Conexao_{completed_protocol}.pdf"
    archived = tmp_path / "clientes" / f"Orcamento_de_Conexao_{completed_protocol}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    archived.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 synthetic completed")
    archived.write_bytes(pdf.read_bytes())
    pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    index_path = tmp_path / "state" / "op5_completed_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "op5-completed-index-v1",
                "protocols": {
                    completed_protocol: {
                        "status": "completed",
                        "download_pdf_path": str(pdf),
                        "download_pdf_sha256": pdf_sha,
                        "archived_pdf_path": str(archived),
                        "archived_pdf_sha256": pdf_sha,
                        "workbook_sheet": "2026",
                        "workbook_row": 42,
                        "workbook_sha256": "0" * 64,
                        "technical_extractor_version": "synthetic",
                        "equipment_rules_version": "synthetic",
                        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "expires_at": expires_at.isoformat(timespec="seconds"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        OP5_RECONCILIATION_MODE="batch_fast",
        APPLY_EXCEL=True,
        op5_completed_index_path=index_path,
        force_reprocess_protocols=set(),
    )

    selection = cdp_service.select_eligible_completed_requests(
        completed_requests=[_record(completed_protocol), _record(new_protocol)],
        pipeline_state=None,
        max_completed_to_process=1,
        skip_already_completed=True,
        settings=settings,
    )

    assert [record.protocol for record in selection["selected_records"]] == [new_protocol]
    assert selection["skipped_completed"][0]["protocol"] == completed_protocol
