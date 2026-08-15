import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.cdp_portal_service import (
    _download_result_from_record,
    _is_portal_root_or_index_url,
    _is_unsafe_authenticated_control_context_url,
    _is_unsafe_navigation_url,
    _locator_href_is_unsafe,
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


def test_numeric_paginator_uses_dom_fallback_when_loader_intercepts(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cdp_service, "PlaywrightError", RuntimeError)
    monkeypatch.setattr(cdp_service, "_wait_after_pagination_click", lambda page: None)

    class Loader:
        def wait_for(self, **kwargs) -> None:
            return None

    class Link:
        dom_clicked = False

        def inner_text(self, **kwargs) -> str:
            return "12"

        def get_attribute(self, name: str, **kwargs) -> str:
            return ""

        def click(self, **kwargs) -> None:
            raise RuntimeError("page-loader intercepts pointer events")

        def evaluate(self, script: str) -> None:
            self.dom_clicked = True

    class Links:
        def __init__(self, link: Link) -> None:
            self.link = link

        def count(self) -> int:
            return 1

        def nth(self, index: int) -> Link:
            return self.link

    class Page:
        def __init__(self) -> None:
            self.link = Link()

        def locator(self, selector: str):
            if selector.startswith("#page-loader"):
                return SimpleNamespace(first=Loader())
            return Links(self.link)

    page = Page()

    result = cdp_service._click_numeric_paginator_with_playwright(
        page,
        target_page_number=12,
    )

    assert result["clicked"] is True
    assert result["stop_reason"] == "pagination_numeric_page_clicked_dom_fallback"
    assert page.link.dom_clicked is True


def test_numeric_paginator_ignores_active_or_disabled_target(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cdp_service, "PlaywrightError", RuntimeError)

    class Link:
        clicked = False

        def __init__(self, class_name: str) -> None:
            self.class_name = class_name

        def inner_text(self, **kwargs) -> str:
            return "12"

        def get_attribute(self, name: str, **kwargs) -> str:
            assert name == "class"
            return self.class_name

        def click(self, **kwargs) -> None:
            self.clicked = True

    class Links:
        def __init__(self) -> None:
            self.links = [
                Link("ui-state-active"),
                Link("ui-state-disabled"),
            ]

        def count(self) -> int:
            return len(self.links)

        def nth(self, index: int) -> Link:
            return self.links[index]

    class Page:
        def __init__(self) -> None:
            self.links = Links()

        def locator(self, selector: str):
            return self.links

    page = Page()

    result = cdp_service._click_numeric_paginator_with_playwright(
        page,
        target_page_number=12,
    )

    assert result["clicked"] is False
    assert result["text"] == "12"
    assert result["stop_reason"] == "pagination_numeric_locator_target_not_found"
    assert [link.clicked for link in page.links.links] == [False, False]


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


def test_navigation_root_and_index_are_rejected_for_history() -> None:
    listing_url = "https://gdneoenergiapernambuco.neoenergia.com/minhas"

    assert _is_portal_root_or_index_url(
        "https://gdneoenergiapernambuco.neoenergia.com/"
    )
    assert _is_portal_root_or_index_url(
        "https://gdneoenergiapernambuco.neoenergia.com/index.jsf"
    )
    assert _is_unsafe_navigation_url(
        "https://gdneoenergiapernambuco.neoenergia.com/",
        listing_url,
    ) is True
    assert _is_unsafe_navigation_url(
        "https://gdneoenergiapernambuco.neoenergia.com/index.jsf",
        listing_url,
    ) is True


def test_authenticated_control_context_rejects_external_or_internal_urls() -> None:
    listing_url = "https://gdneoenergiapernambuco.neoenergia.com/minhas"

    assert _is_unsafe_authenticated_control_context_url("about:blank", listing_url) is True
    assert (
        _is_unsafe_authenticated_control_context_url(
            "https://example.com/home",
            listing_url,
        )
        is True
    )
    assert (
        _is_unsafe_authenticated_control_context_url(
            "https://gdneoenergiapernambuco.neoenergia.com/detalhe",
            listing_url,
        )
        is False
    )


def test_locator_href_root_policy_is_explicit() -> None:
    listing_url = "https://gdneoenergiapernambuco.neoenergia.com/minhas"

    class Locator:
        def get_attribute(self, name: str, **kwargs) -> str:
            assert name == "href"
            return "https://gdneoenergiapernambuco.neoenergia.com/"

    locator = Locator()

    assert (
        _locator_href_is_unsafe(
            locator,
            listing_url,
            allow_same_host_root_or_index=False,
        )
        is True
    )
    assert (
        _locator_href_is_unsafe(
            locator,
            listing_url,
            allow_same_host_root_or_index=True,
        )
        is False
    )


def test_previous_listing_page_reports_clicked_result(monkeypatch) -> None:
    monkeypatch.setattr(
        cdp_service,
        "_click_previous_listing_page_diagnostic",
        lambda page: {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": ".ui-paginator-prev",
            "text": "Anterior",
            "class_name": "",
            "stop_reason": None,
        },
    )

    result = cdp_service.find_and_click_previous_listing_page(
        page=object(),
        current_page_number=7,
    )

    assert result["found"] is True
    assert result["enabled"] is True
    assert result["previous_page_available"] is True
    assert result["mode"] == "previous_button"
    assert result["current_page_number"] == 7
    assert result["target_page_number"] == 6
    assert result["stop_reason"] == "pagination_previous_clicked"


def test_previous_listing_page_reports_first_page_when_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        cdp_service,
        "_click_previous_listing_page_diagnostic",
        lambda page: {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": None,
            "class_name": None,
            "stop_reason": "pagination_previous_not_found",
        },
    )

    result = cdp_service.find_and_click_previous_listing_page(
        page=object(),
        current_page_number=1,
    )

    assert result["found"] is False
    assert result["enabled"] is False
    assert result["previous_page_available"] is False
    assert result["mode"] == "previous_button"
    assert result["current_page_number"] == 1
    assert result["target_page_number"] == 1
    assert result["stop_reason"] == "pagination_previous_not_found"


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
    workbook = tmp_path / "planilha.xlsx"
    workbook.write_bytes(b"workbook-current")
    workbook_sha = hashlib.sha256(workbook.read_bytes()).hexdigest()
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
                        "workbook_sha256": workbook_sha,
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
        planilha_path=workbook,
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


def test_batch_fast_completed_master_index_requires_current_workbook_sha(
    tmp_path: Path,
) -> None:
    completed_protocol = "2600001048"
    pdf = tmp_path / "downloads" / completed_protocol / f"Orcamento_de_Conexao_{completed_protocol}.pdf"
    archived = tmp_path / "clientes" / f"Orcamento_de_Conexao_{completed_protocol}.pdf"
    workbook = tmp_path / "planilha.xlsx"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    archived.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 synthetic completed")
    archived.write_bytes(pdf.read_bytes())
    workbook.write_bytes(b"workbook-current")
    pdf_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
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
                        "expires_at": (
                            datetime.now(timezone.utc) + timedelta(days=14)
                        ).isoformat(timespec="seconds"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        OP5_RECONCILIATION_MODE="batch_fast",
        APPLY_EXCEL=True,
        planilha_path=workbook,
        op5_completed_index_path=index_path,
        force_reprocess_protocols=set(),
    )

    selection = cdp_service.select_eligible_completed_requests(
        completed_requests=[_record(completed_protocol)],
        pipeline_state=None,
        max_completed_to_process=1,
        skip_already_completed=True,
        settings=settings,
    )

    assert [record.protocol for record in selection["selected_records"]] == [
        completed_protocol
    ]
    assert selection["skipped_completed"] == []
