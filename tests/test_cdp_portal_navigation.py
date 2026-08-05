import json
from pathlib import Path

from src.cdp_portal_service import (
    _download_result_from_record,
    _is_unsafe_navigation_url,
    _reuse_existing_pdf_without_detail,
    find_existing_download_metadata,
    should_open_detail_for_budget,
)
from src.models import PortalSolicitation


def _record(protocol: str = "2601") -> PortalSolicitation:
    return PortalSolicitation(
        protocol=protocol,
        client_name="Cliente Teste",
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
