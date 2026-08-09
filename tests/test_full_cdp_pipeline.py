from datetime import datetime
from pathlib import Path

from scripts.run_full_cdp_pipeline import (
    _apply_global_protocol_limit,
    _build_protocol_rows,
    _pdf_paths_for_processing,
    build_pipeline_payload,
)
import src.cdp_portal_service as cdp_portal_service
from src.cdp_portal_service import (
    _click_next_listing_page_diagnostic,
    _collect_completed_listing_rows_across_pages,
    _click_numeric_paginator_with_playwright,
    _click_next_listing_page,
    consolidate_listing_page_rows,
    download_connection_budget,
    download_completed_budgets_from_current_page,
    ensure_request_origin_page,
    ensure_listing_starts_on_page_one,
    find_and_click_next_numeric_page,
    get_active_numeric_page,
    freeze_eligible_completed_records,
    navigate_to_numeric_page,
    freeze_selected_completed_records,
    select_eligible_completed_requests,
)
from src.models import PortalSolicitation


class DummySettings:
    DRY_RUN = True
    APPLY_EXCEL = True
    APPLY_ARCHIVE = True
    MAX_COMPLETED_TO_PROCESS = 3
    REPROCESS_EXISTING_PDFS = False
    PROCESS_EXISTING_AFTER_SKIP = True
    RESUME_PIPELINE = True
    SKIP_ALREADY_COMPLETED = True
    CACHE_CLIENT_FOLDER_LOOKUP = True
    RESET_PIPELINE_STATE = False
    force_reprocess_protocols = set()
    CDP_ENDPOINT = "http://127.0.0.1:9222"
    ENABLE_PORTAL_PAGINATION = False
    MAX_PORTAL_PAGES = 1
    downloads_dir_path = Path("data/downloads")
    planilha_path = Path("planilha_teste.xlsx")
    clientes_root_path = Path("Z:/Clientes")


def _row(protocol: str, status: str = "CONCLUIDA") -> dict:
    return {
        "record": PortalSolicitation(
            protocol=protocol,
            client_name=f"Cliente {protocol}",
            status=status,
        )
    }


def _page(start: int, count: int, status: str = "CONCLUIDA") -> list[dict]:
    return [_row(f"2600{index:06d}", status=status) for index in range(start, start + count)]


class FakePage:
    url = "https://portal/listagem"


def test_selected_completed_records_are_unique() -> None:
    rows = [_row("2601"), _row("2601"), _row("2602"), _row("2603")]

    selected, duplicates = freeze_selected_completed_records(rows, max_completed=3)

    assert [record.protocol for record in selected] == ["2601", "2602", "2603"]
    assert [item["protocol"] for item in duplicates] == ["2601"]


def test_batch_limit_is_applied_after_skipping_completed_state() -> None:
    protocols = [f"26000000{index:02d}" for index in range(27)]
    completed_in_state = set(protocols[:10])
    requests = [_row(protocol)["record"] for protocol in protocols]

    selection = select_eligible_completed_requests(
        requests,
        {"protocols": {protocol: {"status": "completed"} for protocol in completed_in_state}},
        max_completed_to_process=10,
        skip_already_completed=True,
        force_reprocess_protocols=set(),
    )

    assert selection["duplicates_removed"] == []
    assert len(selection["skipped_completed"]) == 10
    assert len(selection["eligible_records"]) == 17
    assert [record.protocol for record in selection["selected_records"]] == protocols[10:20]


def test_limit_zero_selects_all_eligible_after_skip() -> None:
    protocols = [f"26000000{index:02d}" for index in range(27)]
    completed_in_state = set(protocols[:10])

    selection = select_eligible_completed_requests(
        [_row(protocol)["record"] for protocol in protocols],
        {"protocols": {protocol: {"status": "completed"} for protocol in completed_in_state}},
        max_completed_to_process=0,
        skip_already_completed=True,
        force_reprocess_protocols=set(),
    )

    assert len(selection["selected_records"]) == 17
    assert [record.protocol for record in selection["selected_records"]] == protocols[10:]


def test_all_completed_selects_zero_when_skip_enabled() -> None:
    protocols = [f"26000000{index:02d}" for index in range(10)]

    selection = select_eligible_completed_requests(
        [_row(protocol)["record"] for protocol in protocols],
        {"protocols": {protocol: {"status": "completed"} for protocol in protocols}},
        max_completed_to_process=10,
        skip_already_completed=True,
        force_reprocess_protocols=set(),
    )

    assert selection["selected_records"] == []
    assert len(selection["skipped_completed"]) == 10


def test_all_completed_selects_all_when_skip_disabled() -> None:
    protocols = [f"26000000{index:02d}" for index in range(10)]

    selection = select_eligible_completed_requests(
        [_row(protocol)["record"] for protocol in protocols],
        {"protocols": {protocol: {"status": "completed"} for protocol in protocols}},
        max_completed_to_process=10,
        skip_already_completed=False,
        force_reprocess_protocols=set(),
    )

    assert [record.protocol for record in selection["selected_records"]] == protocols


def test_force_reprocess_completed_protocol_is_selected_with_reason() -> None:
    protocols = ["2601", "2602"]

    selection = select_eligible_completed_requests(
        [_row(protocol)["record"] for protocol in protocols],
        {"protocols": {protocol: {"status": "completed"} for protocol in protocols}},
        max_completed_to_process=10,
        skip_already_completed=True,
        force_reprocess_protocols={"2601"},
    )

    assert [record.protocol for record in selection["selected_records"]] == ["2601"]
    assert selection["selected_records"][0].selection_reason == "force_reprocess"
    assert selection["total_force_reprocess"] == 1


def test_duplicate_requests_keep_first_occurrence_and_preserve_order() -> None:
    requests = [
        PortalSolicitation(protocol="2601", client_name="A", status="CONCLUIDA"),
        PortalSolicitation(protocol="2602", client_name="B", status="CONCLUIDA"),
        PortalSolicitation(protocol="2601", client_name="A2", status="CONCLUIDA"),
        PortalSolicitation(protocol="2603", client_name="C", status="CONCLUIDA"),
    ]

    selection = select_eligible_completed_requests(
        requests,
        {},
        max_completed_to_process=0,
        skip_already_completed=True,
        force_reprocess_protocols=set(),
    )

    assert [record.protocol for record in selection["selected_records"]] == [
        "2601",
        "2602",
        "2603",
    ]
    assert selection["duplicates_removed"][0]["protocol"] == "2601"
    assert selection["duplicates_removed"][0]["selection_reason"] == "duplicate_removed"


def test_selected_item_preserves_page_and_row_metadata() -> None:
    request = PortalSolicitation(
        protocol="2601",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=2,
        row_index=17,
    )

    selection = select_eligible_completed_requests(
        [request],
        {},
        max_completed_to_process=10,
        skip_already_completed=True,
        force_reprocess_protocols=set(),
    )

    selected = selection["selected_records"][0]
    assert selected.page_number == 2
    assert selected.row_index == 17
    assert selected.selection_reason == "eligible_new"


def test_three_pages_are_consolidated_before_selection() -> None:
    summary = consolidate_listing_page_rows(
        [
            _page(0, 50),
            _page(50, 50),
            _page(100, 50),
        ]
    )

    assert summary["pages_read"] == 3
    assert summary["total_rows"] == 150
    assert summary["total_completed"] == 150
    assert len(summary["completed_records"]) == 150
    assert summary["completed_records"][0].page_number == 1
    assert summary["completed_records"][-1].page_number == 3


def test_pagination_disabled_next_button_stops_without_error() -> None:
    class DisabledNextPage:
        def evaluate(self, script: str) -> bool:
            return False

    assert _click_next_listing_page(DisabledNextPage()) is False


def test_pagination_enabled_next_button_returns_true() -> None:
    class EnabledNextPage:
        def evaluate(self, script: str) -> bool:
            return True

    assert _click_next_listing_page(EnabledNextPage()) is True


def test_pagination_diagnostic_reports_disabled_next_button() -> None:
    class DisabledNextPage:
        def evaluate(self, script: str) -> dict:
            return {
                "found": True,
                "enabled": False,
                "clicked": False,
                "selector": ".ui-paginator-next",
                "text": "Proxima",
                "class_name": "ui-paginator-next ui-state-disabled",
                "stop_reason": "pagination_next_disabled",
            }

    diagnostic = _click_next_listing_page_diagnostic(DisabledNextPage())

    assert diagnostic["found"] is True
    assert diagnostic["enabled"] is False
    assert diagnostic["clicked"] is False
    assert diagnostic["selector"] == ".ui-paginator-next"
    assert diagnostic["stop_reason"] == "pagination_next_disabled"


def test_numeric_pagination_click_reports_target_page() -> None:
    class NumericPage:
        def evaluate(self, script: str, target_page_number: int) -> dict:
            return {
                "found": True,
                "enabled": True,
                "clicked": True,
                "selector": "div.paginator > a",
                "text": str(target_page_number),
                "class_name": "",
                "mode": "numeric",
                "current_page_number": target_page_number - 1,
                "target_page_number": target_page_number,
                "numeric_page_links_found": ["1", "2", "3"],
                "numeric_page_links_count": 3,
                "stop_reason": "pagination_numeric_page_clicked",
            }

    diagnostic = find_and_click_next_numeric_page(NumericPage(), 1)

    assert diagnostic["mode"] == "numeric"
    assert diagnostic["clicked"] is True
    assert diagnostic["current_page_number"] == 1
    assert diagnostic["target_page_number"] == 2
    assert diagnostic["numeric_page_links_found"] == ["1", "2", "3"]


def test_numeric_pagination_click_uses_exact_visible_locator() -> None:
    class FakeLink:
        def __init__(self, text: str, class_name: str = ""):
            self.text = text
            self.class_name = class_name
            self.clicked = False

        def inner_text(self, timeout: int) -> str:
            return self.text

        def get_attribute(self, name: str, timeout: int) -> str:
            assert name == "class"
            return self.class_name

        def click(self, timeout: int) -> None:
            self.clicked = True

    class FakeLocator:
        def __init__(self, links: list[FakeLink]):
            self.links = links

        def count(self) -> int:
            return len(self.links)

        def nth(self, index: int) -> FakeLink:
            return self.links[index]

    class FakePage:
        def __init__(self, links: list[FakeLink]):
            self.links = links

        def locator(self, selector: str) -> FakeLocator:
            assert selector == ".ui-paginator a.ui-paginator-page"
            return FakeLocator(self.links)

    active_one = FakeLink("1", "ui-paginator-page ui-state-active")
    target_two = FakeLink("2", "ui-paginator-page")
    page = FakePage([active_one, target_two, FakeLink("3", "ui-paginator-page")])

    result = _click_numeric_paginator_with_playwright(page, target_page_number=2)

    assert result["clicked"] is True
    assert result["text"] == "2"
    assert target_two.clicked is True
    assert active_one.clicked is False


def test_download_connection_budget_click_does_not_wait_for_navigation(tmp_path: Path) -> None:
    class FakeDownload:
        suggested_filename = "orcamento.pdf"

        def save_as(self, destination: str) -> None:
            Path(destination).write_bytes(b"%PDF-1.4\n")

    class FakeDownloadContext:
        value = FakeDownload()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeContext:
        def __init__(self, page):
            self.pages = [page]

    class FakePage:
        url = "https://portal.test/detalhe"

        def __init__(self):
            self.context = FakeContext(self)
            self.download_timeout = None

        def expect_download(self, timeout: int):
            self.download_timeout = timeout
            return FakeDownloadContext()

    class FakeTarget:
        def __init__(self):
            self.click_kwargs = None

        def click(self, **kwargs):
            self.click_kwargs = kwargs

    page = FakePage()
    target = FakeTarget()

    path = download_connection_budget(
        page,
        "2600000000",
        downloads_root=tmp_path,
        target=target,
    )

    assert path is not None
    assert path.exists()
    assert target.click_kwargs["timeout"] == 10_000
    assert target.click_kwargs["no_wait_after"] is True


def test_get_active_numeric_page_extracts_page_two_from_active_text() -> None:
    class ActivePage:
        def evaluate(self, script: str) -> int:
            assert "ui-state-active" in script
            return 2

    assert get_active_numeric_page(ActivePage()) == 2


def test_reset_listing_to_first_page_clicks_page_one(monkeypatch) -> None:
    active_pages = [2, 1]
    targets = []

    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: active_pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "navigate_to_numeric_page",
        lambda page, target: targets.append(target)
        or {
            "success": True,
            "status": "recovered_listing_by_numeric_page",
            "method": "recovered_listing_by_numeric_page",
            "target_page_number": target,
            "error": None,
        },
    )

    result = ensure_listing_starts_on_page_one(FakePage())

    assert result["success"] is True
    assert result["initial_active_page"] == 2
    assert result["active_page_after"] == 1
    assert targets == [1]


def test_max_portal_pages_one_reads_only_current_page() -> None:
    summary = consolidate_listing_page_rows([_page(0, 50), _page(50, 50)], max_pages=1)

    assert summary["pages_read"] == 1
    assert summary["total_rows"] == 50
    assert len(summary["completed_records"]) == 50


def test_max_portal_pages_two_reads_at_most_two_pages() -> None:
    summary = consolidate_listing_page_rows(
        [_page(0, 50), _page(50, 50), _page(100, 50)],
        max_pages=2,
    )

    assert summary["pages_read"] == 2
    assert summary["total_rows"] == 100
    assert len(summary["completed_records"]) == 100


def test_repeated_page_signature_stops_with_warning() -> None:
    repeated = _page(0, 50)

    summary = consolidate_listing_page_rows([repeated, repeated])

    assert summary["pages_read"] == 1
    assert len(summary["completed_records"]) == 50
    assert summary["pagination_warnings"]
    assert "Pagina repetida" in summary["pagination_warnings"][0]


def test_collect_across_pages_attempts_next_when_pagination_enabled(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 2

    pages = [_page(0, 2), _page(2, 2)]
    click_attempts = []
    active_pages = [1, 2]

    def fake_read_current_page(page):
        return pages.pop(0)

    def fake_click_next(page, current_page_number: int):
        click_attempts.append(1)
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": "div.paginator > a",
            "text": str(current_page_number + 1),
            "class_name": "",
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1", "2"],
            "numeric_page_links_count": 2,
            "stop_reason": "pagination_numeric_page_clicked",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        fake_read_current_page,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: active_pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        fake_click_next,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 2
    assert summary["total_rows"] == 4
    assert summary["pagination_next_found"] is True
    assert summary["pagination_click_attempts"] == 1
    assert summary["pagination_mode"] == "numeric"
    assert summary["pagination_target_page"] == 3
    assert summary["pagination_complete"] is True
    assert summary["last_page_confirmed"] is True
    assert summary["pagination_stop_reason"] == "last_page_reached"
    assert len(click_attempts) == 1


def test_collect_waits_for_active_page_to_stabilize_after_click(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 2

    pages = [_page(0, 2), _page(2, 2)]
    active_pages = [1, 1, 2]

    def fake_click_next(page, current_page_number: int):
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": "div.paginator > a",
            "text": str(current_page_number + 1),
            "class_name": "",
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1", "2"],
            "numeric_page_links_count": 2,
            "stop_reason": "pagination_numeric_page_clicked",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: active_pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_listing_page",
        fake_click_next,
        raising=False,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 2
    assert summary["pagination_stop_reason"] == "last_page_reached"
    assert summary["pagination_complete"] is True


def test_collect_reads_last_page_above_eleven_when_safety_cap_allows(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 20

    pages = [_page(index * 2, 2) for index in range(14)]
    active_pages = list(range(1, 15))

    def fake_click_next(page, current_page_number: int):
        if current_page_number >= 14:
            return {
                "found": False,
                "enabled": False,
                "clicked": False,
                "mode": "numeric",
                "current_page_number": current_page_number,
                "target_page_number": current_page_number + 1,
                "numeric_page_links_found": ["11", "12", "13", "14"],
                "stop_reason": "last_page_reached",
            }
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": [str(current_page_number + 1)],
            "stop_reason": "pagination_numeric_page_clicked",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: active_pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_listing_page",
        fake_click_next,
        raising=False,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 14
    assert summary["pagination_complete"] is True
    assert summary["last_page_confirmed"] is True
    assert summary["last_page_number"] == 14
    assert summary["next_page_available_after_stop"] is False
    assert summary["pagination_stop_reason"] == "last_page_reached"


def test_safety_cap_with_next_page_marks_pagination_incomplete(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 11

    pages = [_page(index * 2, 2) for index in range(11)]
    active_pages = list(range(1, 12))

    def fake_click_next(page, current_page_number: int):
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": [str(current_page_number + 1)],
            "stop_reason": "pagination_numeric_page_clicked",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: active_pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_listing_page",
        fake_click_next,
        raising=False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "inspect_next_page_availability",
        lambda page, current_page_number: {
            "next_page_available": True,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["11", "12", "13", "14"],
        },
        raising=False,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 11
    assert summary["pagination_complete"] is False
    assert summary["last_page_confirmed"] is False
    assert summary["next_page_available_after_stop"] is True
    assert summary["pagination_stop_reason"] == "safety_cap_reached_with_next_page"


def test_next_button_is_used_when_future_numeric_page_is_not_visible(
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 2

    pages = [_page(0, 2), _page(2, 2)]
    active_pages = [1, 2]

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: active_pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        lambda page, current_page_number: {
            "found": False,
            "enabled": False,
            "clicked": False,
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1", "…", "10", "11"],
            "stop_reason": "pagination_numeric_target_not_found",
        },
    )
    next_clicks = []
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_next_listing_page_diagnostic",
        lambda page: next_clicks.append(True)
        or {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": ".ui-paginator-next",
            "text": "Próxima",
            "class_name": "",
            "stop_reason": None,
        },
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 2
    assert next_clicks
    assert summary["pagination_diagnostics"][0]["mode"] == "next_button"


def test_download_blocks_operational_lot_when_reconciliation_pagination_incomplete(
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 11

    records = [PortalSolicitation(protocol="2601", status="CONCLUIDA", page_number=1)]
    reconciliation_calls = []

    monkeypatch.setattr(cdp_portal_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_listing_starts_on_page_one",
        lambda page: {
            "success": True,
            "status": "already_on_first_page",
            "initial_active_page": 1,
            "active_page_after": 1,
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_collect_completed_listing_rows_across_pages",
        lambda page, settings: {
            "pages_read": 11,
            "total_rows": 550,
            "total_completed": 1,
            "completed_records": records,
            "duplicates_skipped": [],
            "pagination_warnings": ["MAX_PORTAL_PAGES atingido: 11."],
            "pagination_enabled": True,
            "pagination_complete": False,
            "last_page_confirmed": False,
            "last_page_number": None,
            "pages_visited": [*range(1, 12)],
            "next_page_available_after_stop": True,
            "pagination_stop_reason": "safety_cap_reached_with_next_page",
            "pagination_next_found": True,
            "pagination_safety_cap": 11,
            "pagination_click_attempts": 10,
            "pagination_mode": "numeric",
            "pagination_current_page": 11,
            "pagination_target_page": 12,
            "pagination_numeric_links_found": ["11", "12", "13", "14"],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("operational lot should not start")
        ),
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        reconciliation_callback=lambda *args: reconciliation_calls.append(args) or {},
    )

    assert summary["aborted"] is True
    assert summary["abort_reason"] == "PORTAL_PAGINATION_INCOMPLETE"
    assert summary["total_selected"] == 0
    assert summary["results"] == []
    assert reconciliation_calls and reconciliation_calls[0][0] == "before_limit"


def test_collect_uses_real_active_page_number(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 1

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: 2)
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: [_row("2602")],
    )

    summary = _collect_completed_listing_rows_across_pages(FakePage(), Settings())

    assert summary["pages_read"] == 1
    assert summary["completed_records"][0].page_number == 2
    assert summary["pagination_current_page"] == 2


def test_collect_across_pages_reports_numeric_target_not_found(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 2

    def fake_click_next(page, current_page_number: int):
        return {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": str(current_page_number + 1),
            "class_name": None,
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1"],
            "numeric_page_links_count": 1,
            "stop_reason": "pagination_numeric_target_not_found",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: _page(0, 2),
    )
    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: 1)
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        fake_click_next,
    )

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 1
    assert summary["pagination_next_found"] is False
    assert summary["pagination_click_attempts"] == 0
    assert summary["pagination_complete"] is True
    assert summary["last_page_confirmed"] is True
    assert summary["pagination_stop_reason"] == "last_page_reached"


def test_origin_navigation_pagination_failure_is_logged_as_warning(monkeypatch) -> None:
    warning_calls = []
    error_calls = []

    monkeypatch.setattr(
        cdp_portal_service.logger,
        "warning",
        lambda message: warning_calls.append(message),
    )
    monkeypatch.setattr(
        cdp_portal_service.logger,
        "error",
        lambda message: error_calls.append(message),
    )

    cdp_portal_service._log_origin_navigation_failure(
        "Pagina numerica de origem nao encontrada: 3",
        "pagination_numeric_target_not_found",
    )

    assert warning_calls == ["Pagina numerica de origem nao encontrada: 3"]
    assert error_calls == []


def test_origin_navigation_active_page_mismatch_is_logged_as_warning(monkeypatch) -> None:
    warning_calls = []
    error_calls = []

    monkeypatch.setattr(
        cdp_portal_service.logger,
        "warning",
        lambda message: warning_calls.append(message),
    )
    monkeypatch.setattr(
        cdp_portal_service.logger,
        "error",
        lambda message: error_calls.append(message),
    )

    cdp_portal_service._log_origin_navigation_failure(
        "Pagina ativa apos clique: 1; esperado: 8.",
        "pagination_active_page_mismatch",
    )

    assert warning_calls == ["Pagina ativa apos clique: 1; esperado: 8."]
    assert error_calls == []


def test_collect_across_pages_reports_disabled_numeric_target(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 2

    def fake_click_next(page, current_page_number: int):
        return {
            "found": True,
            "enabled": False,
            "clicked": False,
            "selector": "div.paginator > span",
            "text": str(current_page_number + 1),
            "class_name": "ui-state-disabled",
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1", "2"],
            "numeric_page_links_count": 2,
            "stop_reason": "pagination_numeric_target_disabled",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: _page(0, 2),
    )
    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: 1)
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        fake_click_next,
    )

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 1
    assert summary["pagination_next_found"] is True
    assert summary["pagination_click_attempts"] == 1
    assert summary["pagination_stop_reason"] == "pagination_numeric_target_disabled"
    assert "pagination_numeric_target_disabled" in summary["pagination_warnings"]


def test_collect_across_pages_reports_repeated_signature(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 2

    pages = [_page(0, 2), _page(0, 2)]
    active_pages = [1, 2]

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: active_pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        lambda page, current_page_number: {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": "div.paginator > a",
            "text": str(current_page_number + 1),
            "class_name": "",
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1", "2"],
            "numeric_page_links_count": 2,
            "stop_reason": "pagination_numeric_page_clicked",
        },
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(object(), Settings())

    assert summary["pages_read"] == 1
    assert summary["pagination_stop_reason"] == "pagination_click_no_change"
    assert summary["pagination_diagnostics"][0]["signature_after"] == (
        "2600000000",
        "2600000001",
        2,
    )
    assert summary["pagination_diagnostics"][0]["stop_reason"] == (
        "pagination_click_no_change"
    )


def test_origin_page_does_not_navigate_when_protocol_is_visible(monkeypatch) -> None:
    request = PortalSolicitation(
        protocol="2601",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=2,
        row_index=4,
    )
    navigations = []

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: [_row("2601"), _row("2602")],
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "navigate_to_numeric_page",
        lambda page, target: navigations.append(target),
    )

    result = ensure_request_origin_page(FakePage(), request)

    assert result["success"] is True
    assert result["status"] == "protocol_visible_current_page"
    assert result["protocol_found_on_origin_page"] is True
    assert result["row"]["record"].protocol == "2601"
    assert navigations == []


def test_navigate_to_numeric_page_returns_already_on_target(monkeypatch) -> None:
    reads = []

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: 2)
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: reads.append(1) or [_row("2601")],
    )

    result = navigate_to_numeric_page(FakePage(), 2)

    assert result["success"] is True
    assert result["status"] == "already_on_target_page"
    assert reads == []


def test_origin_page_navigates_to_numeric_page_when_protocol_is_not_visible(
    monkeypatch,
) -> None:
    request = PortalSolicitation(
        protocol="2603",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=2,
        row_index=7,
    )
    pages = [[_row("2601"), _row("2602")], [_row("2603")]]
    numeric_targets = []

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: pages.pop(0),
    )

    def fake_navigate(page, target):
        numeric_targets.append(target)
        return {
            "success": True,
            "status": "recovered_listing_by_numeric_page",
            "method": "recovered_listing_by_numeric_page",
            "target_page_number": target,
            "url_after": "https://portal/listagem",
            "error": None,
        }

    monkeypatch.setattr(cdp_portal_service, "navigate_to_numeric_page", fake_navigate)
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_next_listing_page",
        lambda page: (_ for _ in ()).throw(AssertionError("old pagination used")),
    )

    result = ensure_request_origin_page(FakePage(), request)

    assert numeric_targets == [2]
    assert result["success"] is True
    assert result["status"] == "protocol_found_on_origin_page"
    assert result["method"] == "recovered_listing_by_numeric_page"
    assert result["protocol_found_on_origin_page"] is True
    assert result["row"]["record"].protocol == "2603"


def test_origin_page_reports_protocol_not_found_after_numeric_navigation(
    monkeypatch,
) -> None:
    request = PortalSolicitation(
        protocol="2603",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=2,
        row_index=7,
    )
    pages = [[_row("2601")], [_row("2602")]]

    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: pages.pop(0),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "navigate_to_numeric_page",
        lambda page, target: {
            "success": True,
            "status": "recovered_listing_by_numeric_page",
            "method": "recovered_listing_by_numeric_page",
            "target_page_number": target,
            "url_after": "https://portal/listagem",
            "error": None,
        },
    )

    result = ensure_request_origin_page(FakePage(), request)

    assert result["success"] is False
    assert result["status"] == "protocol_not_found_on_origin_page"
    assert result["protocol_found_on_origin_page"] is False
    assert result["error"] == "protocol_not_found_on_origin_page"


def test_production_aborts_with_report_when_active_page_cannot_be_confirmed(
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        DRY_RUN = False
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 2

    monkeypatch.setattr(cdp_portal_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_listing_starts_on_page_one",
        lambda page: {
            "success": False,
            "status": "cannot_confirm_active_page",
            "initial_active_page": None,
            "active_page_after": None,
            "error": "Nao foi possivel detectar a pagina ativa.",
        },
    )

    summary = download_completed_budgets_from_current_page(FakePage())

    assert summary["aborted"] is True
    assert "cannot_confirm_active_page_in_production" in summary["run_error"]
    assert summary["pagination_reset_to_first_page"]["status"] == (
        "cannot_confirm_active_page"
    )
    assert summary["results"] == []


def test_download_aborts_after_three_protocol_not_found_errors(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 1
        MAX_PROTOCOL_NOT_FOUND_ERRORS = 3

    records = [
        PortalSolicitation(protocol=f"260{i}", status="CONCLUIDA", page_number=1)
        for i in range(1, 5)
    ]
    calls = []

    monkeypatch.setattr(cdp_portal_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_listing_starts_on_page_one",
        lambda page: {
            "success": True,
            "status": "already_on_first_page",
            "initial_active_page": 1,
            "active_page_after": 1,
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_collect_completed_listing_rows_across_pages",
        lambda page, settings: {
            "pages_read": 1,
            "total_rows": 4,
            "total_completed": 4,
            "completed_records": records,
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_stop_reason": "max_portal_pages_reached",
            "pagination_next_found": False,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": None,
            "pagination_numeric_links_found": [],
            "pagination_diagnostics": [],
        },
    )

    def fake_origin(page, request, listing_url=None):
        calls.append(request.protocol)
        return {
            "success": False,
            "status": "protocol_not_found_on_origin_page",
            "method": "recovered_listing_by_numeric_page",
            "protocol_found_on_origin_page": False,
            "url_after": "https://portal/listagem",
            "error": "protocol_not_found_on_origin_page",
            "row": None,
        }

    monkeypatch.setattr(cdp_portal_service, "ensure_request_origin_page", fake_origin)

    summary = download_completed_budgets_from_current_page(FakePage(), max_completed=4)

    assert summary["aborted"] is True
    assert summary["run_error"] == "max_protocol_not_found_errors_reached: 3"
    assert len(summary["results"]) == 3
    assert calls == ["2601", "2602", "2603"]


def test_duplicate_protocol_between_pages_is_kept_once() -> None:
    page_1 = [_row("2601"), _row("2602")]
    page_2 = [_row("2602"), _row("2603")]

    summary = consolidate_listing_page_rows([page_1, page_2])

    assert [record.protocol for record in summary["completed_records"]] == [
        "2601",
        "2602",
        "2603",
    ]
    assert [item["protocol"] for item in summary["duplicates_skipped"]] == ["2602"]


def test_completed_protocol_is_skipped_before_limit() -> None:
    rows = [_row("2601"), _row("2602")]

    class StateStore:
        def should_skip_completed(self, protocol: str, settings=None) -> bool:
            return protocol == "2601"

        def get_protocol(self, protocol: str) -> dict:
            return {"status": "completed", "last_step": "completed"} if protocol == "2601" else {}

    selected, _, skipped_completed, eligible = freeze_eligible_completed_records(
        rows,
        max_completed=10,
        state_store=StateStore(),
        skip_already_completed=True,
        settings=DummySettings(),
    )

    assert [item["protocol"] for item in skipped_completed] == ["2601"]
    assert [record.protocol for record in eligible] == ["2602"]
    assert [record.protocol for record in selected] == ["2602"]


def test_force_reprocess_protocol_is_eligible_even_when_completed() -> None:
    rows = [_row("2601"), _row("2602")]
    force = {"2601"}

    class StateStore:
        def should_skip_completed(self, protocol: str, settings=None) -> bool:
            return protocol not in force

        def get_protocol(self, protocol: str) -> dict:
            return {"status": "completed", "last_step": "completed"}

    selected, _, skipped_completed, eligible = freeze_eligible_completed_records(
        rows,
        max_completed=10,
        state_store=StateStore(),
        skip_already_completed=True,
        settings=DummySettings(),
    )

    assert [item["protocol"] for item in skipped_completed] == ["2602"]
    assert [record.protocol for record in eligible] == ["2601"]
    assert [record.protocol for record in selected] == ["2601"]


def test_downloaded_state_with_local_pdf_is_sent_to_offline_resume(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Orcamento_de_Conexao_2601.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    paths = _pdf_paths_for_processing(
        {
            "results": [
                {
                    "protocol": "2601",
                    "previous_state": "downloaded",
                    "previous_last_step": "pdf_downloaded",
                    "download_status": "existing_pdf_after_skip",
                    "process_pdf_path": str(pdf_path),
                }
            ]
        }
    )

    assert paths == [pdf_path]


def test_consolidated_protocol_rows_do_not_duplicate_protocol() -> None:
    download_summary = {
        "selected_protocols": [
            {"protocol": "2601", "client_name": "Cliente 1"},
            {"protocol": "2601", "client_name": "Cliente 1"},
            {"protocol": "2602", "client_name": "Cliente 2"},
        ],
        "results": [
            {"protocol": "2601", "download_status": "downloaded"},
            {"protocol": "2601", "download_status": "downloaded"},
            {"protocol": "2602", "download_status": "cdp_error"},
        ],
    }

    rows = _build_protocol_rows(download_summary, {"results": []})

    assert [row["protocol"] for row in rows] == ["2601", "2602"]


def test_cdp_error_without_valid_pdf_is_not_sent_to_processing() -> None:
    download_summary = {
        "results": [
            {
                "protocol": "2601",
                "download_status": "cdp_error",
                "cdp_error": "Nao retornou para listagem",
                "process_pdf_path": None,
            }
        ]
    }

    assert _pdf_paths_for_processing(download_summary) == []


def test_existing_pdf_after_skip_can_be_sent_to_processing(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Orcamento_de_Conexao_2601.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    download_summary = {
        "results": [
            {
                "protocol": "2601",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf_path),
            }
        ]
    }

    assert _pdf_paths_for_processing(download_summary) == [pdf_path]


def test_global_protocol_limit_caps_existing_pdfs_after_skip(tmp_path: Path) -> None:
    results = []
    for index in range(1, 11):
        portal_pdf = tmp_path / f"portal_{index}.pdf"
        portal_pdf.write_bytes(b"%PDF-1.4\n")
        results.append(
            {
                "protocol": f"2600{index:04d}",
                "download_status": "downloaded",
                "process_pdf_path": str(portal_pdf),
            }
        )
    for index in range(11, 21):
        existing_pdf = tmp_path / f"existing_{index}.pdf"
        existing_pdf.write_bytes(b"%PDF-1.4\n")
        results.append(
            {
                "protocol": f"2600{index:04d}",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(existing_pdf),
                "previous_state": "completed",
                "previous_last_step": "pdf_reused",
            }
        )
    summary = {"results": results, "selected_protocols": [{"protocol": r["protocol"]} for r in results]}

    limited = _apply_global_protocol_limit(summary, 5)

    assert len(_pdf_paths_for_processing(limited)) == 5
    assert limited["protocols_selected_by_global_limit"] == [
        "26000001",
        "26000002",
        "26000003",
        "26000004",
        "26000005",
    ]
    assert limited["total_protocols_selected_by_global_limit"] == 5
    assert limited["total_protocols_dropped_by_global_limit"] == 15
    assert limited["total_sent_to_processing"] == 5


def test_global_protocol_limit_deduplicates_same_protocol(tmp_path: Path) -> None:
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    third_pdf = tmp_path / "third.pdf"
    for pdf_path in [first_pdf, second_pdf, third_pdf]:
        pdf_path.write_bytes(b"%PDF-1.4\n")
    summary = {
        "results": [
            {
                "protocol": "2601",
                "download_status": "downloaded",
                "process_pdf_path": str(first_pdf),
            },
            {
                "protocol": "2601",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(second_pdf),
            },
            {
                "protocol": "2602",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(third_pdf),
            },
        ],
        "selected_protocols": [{"protocol": "2601"}, {"protocol": "2601"}, {"protocol": "2602"}],
    }

    limited = _apply_global_protocol_limit(summary, 5)

    assert [path.name for path in _pdf_paths_for_processing(limited)] == [
        "first.pdf",
        "third.pdf",
    ]
    assert limited["protocols_selected_by_global_limit"] == ["2601", "2602"]
    assert limited["results"][1]["process_pdf_path"] is None
    assert limited["results"][1]["global_limit_status"] == "duplicate_protocol"


def test_global_protocol_limit_counts_resumed_protocols(tmp_path: Path) -> None:
    pdfs = []
    for index in range(3):
        pdf = tmp_path / f"resume_{index}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        pdfs.append(pdf)
    summary = {
        "results": [
            {
                "protocol": f"260{index}",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf),
                "previous_state": "failed",
                "previous_last_step": "pdf_reused",
            }
            for index, pdf in enumerate(pdfs, start=1)
        ]
    }

    limited = _apply_global_protocol_limit(summary, 2)

    assert limited["protocols_selected_by_global_limit"] == ["2601", "2602"]
    assert len(_pdf_paths_for_processing(limited)) == 2
    assert limited["results"][2]["global_limit_status"] == "excluded_by_global_limit"


def test_pipeline_totals_match_protocol_rows(tmp_path: Path) -> None:
    pdf_1 = tmp_path / "Orcamento_de_Conexao_2601.pdf"
    pdf_2 = tmp_path / "Orcamento_de_Conexao_2602.pdf"
    pdf_1.write_bytes(b"%PDF-1.4\n")
    pdf_2.write_bytes(b"%PDF-1.4\n")
    download_summary = {
        "total_rows": 5,
        "total_completed": 4,
        "total_selected": 3,
        "total_skipped_duplicate": 1,
        "selected_protocols": [
            {"protocol": "2601", "client_name": "Cliente 1"},
            {"protocol": "2602", "client_name": "Cliente 2"},
            {"protocol": "2603", "client_name": "Cliente 3"},
        ],
        "results": [
            {
                "protocol": "2601",
                "download_status": "downloaded",
                "process_pdf_path": str(pdf_1),
            },
            {
                "protocol": "2602",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf_2),
            },
            {
                "protocol": "2603",
                "download_status": "cdp_error",
                "cdp_error": "Linha nao encontrada",
            },
        ],
    }
    processing_summary = {
        "total_success": 1,
        "total_errors": 1,
        "total_excel_updated": 1,
        "total_archived": 0,
        "total_pending_review": 1,
        "results": [
            {"protocol": "2601", "success": True, "action": "update_existing"},
            {"protocol": "2602", "success": False, "error": "Falha offline"},
        ],
    }

    payload = build_pipeline_payload(
        settings=DummySettings(),
        started_at=datetime(2026, 1, 1, 10, 0, 0),
        finished_at=datetime(2026, 1, 1, 10, 1, 0),
        download_summary=download_summary,
        processing_summary=processing_summary,
        download_report_path=tmp_path / "download.json",
        pdf_paths=[pdf_1, pdf_2],
    )

    assert payload["total_selected"] == 3
    assert payload["total_downloaded"] == 1
    assert payload["total_existing_reused"] == 1
    assert payload["total_skipped_duplicate"] == 1
    assert payload["total_cdp_errors"] == 1
    assert payload["total_sent_to_processing"] == 2
    assert payload["total_processed_success"] == 1
    assert payload["total_processed_errors"] == 1
    assert len(payload["protocol_results"]) == 3


def test_pipeline_no_safe_protocols_to_apply_remains_blocked(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Orcamento_de_Conexao_2601.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    download_summary = {
        "total_rows": 1,
        "total_completed": 1,
        "total_selected": 1,
        "total_existing_reused": 1,
        "selected_protocols": [{"protocol": "2601", "client_name": "Cliente 1"}],
        "results": [
            {
                "protocol": "2601",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf_path),
            }
        ],
    }
    processing_summary = {
        "blocked_real_run": True,
        "real_run_block_code": "NO_SAFE_PROTOCOLS_TO_APPLY",
        "total_success": 0,
        "total_errors": 1,
        "total_excel_updated": 0,
        "total_archived": 0,
        "total_pending_review": 1,
        "results": [
            {
                "protocol": "2601",
                "success": False,
                "action": "pending_technical_review",
                "technical_review_required": True,
                "excel_status": {"success": False, "skipped": True},
                "archive_status": {"success": False, "skipped": True},
            }
        ],
    }

    payload = build_pipeline_payload(
        settings=DummySettings(),
        started_at=datetime(2026, 1, 1, 10, 0, 0),
        finished_at=datetime(2026, 1, 1, 10, 1, 0),
        download_summary=download_summary,
        processing_summary=processing_summary,
        download_report_path=tmp_path / "download.json",
        pdf_paths=[pdf_path],
    )

    assert payload["status"] == "BLOQUEADO"
    assert payload["total_excel_updated"] == 0


def test_global_limit_metrics_close_with_processing_categories(tmp_path: Path) -> None:
    pdfs = []
    results = []
    for index in range(4):
        pdf = tmp_path / f"Orcamento_de_Conexao_260{index}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        pdfs.append(pdf)
        results.append(
            {
                "protocol": f"260{index}",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf),
            }
        )
    download_summary = _apply_global_protocol_limit(
        {
            "total_rows": 4,
            "total_completed": 4,
            "total_selected": 4,
            "results": results,
            "selected_protocols": [{"protocol": f"260{index}"} for index in range(4)],
        },
        4,
    )
    processing_summary = {
        "total_success": 2,
        "total_errors": 1,
        "total_pdfs_analyzed": 4,
        "total_safe_protocols": 1,
        "total_no_change_protocols": 1,
        "total_pending_protocols": 1,
        "total_failed_protocols": 1,
        "total_excel_updated": 1,
        "total_archived": 0,
        "results": [
            {"protocol": "2600", "success": True, "action": "update_existing"},
            {"protocol": "2601", "success": True, "action": "skipped_excel_already_updated"},
            {"protocol": "2602", "success": False, "action": "pending_technical_review"},
            {"protocol": "2603", "success": False, "error": "Falha sistemica"},
        ],
    }

    payload = build_pipeline_payload(
        settings=DummySettings(),
        started_at=datetime(2026, 1, 1, 10, 0, 0),
        finished_at=datetime(2026, 1, 1, 10, 1, 0),
        download_summary=download_summary,
        processing_summary=processing_summary,
        download_report_path=tmp_path / "download.json",
        pdf_paths=pdfs,
    )

    assert payload["total_protocols_selected_by_global_limit"] == 4
    assert (
        payload["total_safe_protocols"]
        + payload["total_no_change_protocols"]
        + payload["total_pending_protocols"]
        + payload["total_failed_protocols"]
    ) == payload["total_protocols_selected_by_global_limit"]


def test_pipeline_metrics_separate_batch_policy_and_real_errors(tmp_path: Path) -> None:
    pdfs = []
    download_results = []
    for index in range(5):
        protocol = f"26000010{index}"
        pdf = tmp_path / f"Orcamento_de_Conexao_{protocol}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        pdfs.append(pdf)
        download_results.append(
            {
                "protocol": protocol,
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf),
            }
        )
    download_summary = _apply_global_protocol_limit(
        {
            "total_rows": 5,
            "total_completed": 5,
            "total_selected": 5,
            "results": download_results,
            "selected_protocols": [
                {"protocol": item["protocol"]} for item in download_results
            ],
        },
        5,
    )
    processing_summary = {
        "blocked_real_run": True,
        "real_run_block_code": "FROZEN_BATCH_SCOPE_VIOLATION",
        "total_success": 3,
        "total_errors": 2,
        "total_pdfs_analyzed": 5,
        "total_technically_approved": 4,
        "total_safe_protocols": 1,
        "total_no_change_protocols": 3,
        "total_pending_protocols": 1,
        "total_failed_protocols": 0,
        "total_excel_already_updated": 3,
        "total_updates_planned": 1,
        "total_updates_applied": 0,
        "results": [
            {
                "protocol": "260000100",
                "success": True,
                "technical_validation_status": "approved",
                "action": "skipped_excel_already_updated",
                "excel_status": {
                    "success": True,
                    "skipped": True,
                    "action": "skipped_excel_already_updated",
                },
            },
            {
                "protocol": "260000101",
                "success": True,
                "technical_validation_status": "approved",
                "action": "skipped_excel_already_updated",
                "excel_status": {
                    "success": True,
                    "skipped": True,
                    "action": "skipped_excel_already_updated",
                },
            },
            {
                "protocol": "260000102",
                "success": True,
                "technical_validation_status": "approved",
                "action": "skipped_excel_already_updated",
                "excel_status": {
                    "success": True,
                    "skipped": True,
                    "action": "skipped_excel_already_updated",
                },
            },
            {
                "protocol": "260000103",
                "success": False,
                "technical_validation_status": "approved",
                "error": "FROZEN_BATCH_SCOPE_VIOLATION",
                "excel_status": {"success": False, "can_write": False},
            },
            {
                "protocol": "260000104",
                "success": False,
                "technical_validation_status": "pending_review",
                "technical_review_required": True,
                "action": "pending_technical_review",
                "excel_status": {"success": False, "skipped": True},
            },
        ],
    }

    payload = build_pipeline_payload(
        settings=DummySettings(),
        started_at=datetime(2026, 1, 1, 10, 0, 0),
        finished_at=datetime(2026, 1, 1, 10, 1, 0),
        download_summary=download_summary,
        processing_summary=processing_summary,
        download_report_path=tmp_path / "download.json",
        pdf_paths=pdfs,
    )

    assert payload["total_pdfs_analyzed"] == 5
    assert payload["total_technically_approved"] == 4
    assert payload["total_pending_review"] == 1
    assert payload["total_excel_already_updated"] == 3
    assert payload["total_updates_planned"] == 1
    assert payload["total_updates_applied"] == 0
    assert payload["total_blocked_by_batch_policy"] == 1
    assert payload["total_real_extraction_errors"] == 0
    assert payload["total_real_application_errors"] == 0
    assert payload["total_errors"] == 1


def test_consolidated_report_counts_skipped_completed_and_resumed(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "Orcamento_de_Conexao_2602.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    download_summary = {
        "total_rows": 2,
        "total_completed": 2,
        "total_selected": 2,
        "selected_protocols": [
            {"protocol": "2601", "client_name": "Cliente 1"},
            {"protocol": "2602", "client_name": "Cliente 2"},
        ],
        "results": [
            {
                "protocol": "2601",
                "client_name": "Cliente 1",
                "download_status": "skipped_already_completed",
                "previous_state": "completed",
                "previous_last_step": "completed",
            },
            {
                "protocol": "2602",
                "client_name": "Cliente 2",
                "download_status": "existing_pdf_after_skip",
                "previous_state": "failed",
                "previous_last_step": "pdf_reused",
                "process_pdf_path": str(pdf_path),
            },
        ],
    }
    processing_summary = {
        "total_success": 1,
        "total_errors": 0,
        "total_excel_updated": 0,
        "total_archived": 0,
        "total_pending_review": 0,
        "total_client_folder_cache_hits": 1,
        "total_client_folder_searches": 0,
        "total_excel_already_updated": 1,
        "total_archive_already_done": 1,
        "results": [
            {
                "protocol": "2602",
                "success": True,
                "client_folder_match_type": "fuzzy_name",
                "client_folder_cache_hit": True,
                "action": "skipped_excel_already_updated",
                "excel_status": {"success": True, "action": "skipped_excel_already_updated"},
                "archive_status": {
                    "success": True,
                    "skipped": True,
                    "reason": "archive_already_done",
                },
            }
        ],
    }

    payload = build_pipeline_payload(
        settings=DummySettings(),
        started_at=datetime(2026, 1, 1, 10, 0, 0),
        finished_at=datetime(2026, 1, 1, 10, 1, 0),
        download_summary=download_summary,
        processing_summary=processing_summary,
        download_report_path=tmp_path / "download.json",
        pdf_paths=[pdf_path],
    )

    outcomes = {row["protocol"]: row["outcome"] for row in payload["protocol_results"]}
    assert outcomes == {
        "2601": "skipped_already_completed",
        "2602": "resumed_from_pdf_reused",
    }
    assert payload["total_skipped_already_completed"] == 1
    assert payload["total_resumed"] == 1
    assert payload["total_client_folder_cache_hits"] == 1
    assert payload["total_excel_already_updated"] == 1
    assert payload["total_archive_already_done"] == 1


def test_protocol_row_includes_listing_recovery_diagnostics() -> None:
    rows = _build_protocol_rows(
        {
            "selected_protocols": [{"protocol": "2601", "client_name": "Cliente"}],
            "results": [
                {
                    "protocol": "2601",
                    "client_name": "Cliente",
                    "download_status": "downloaded",
                    "abriu_detalhe": True,
                    "retorno_listagem_status": "recovered_listing_by_url",
                    "metodo_retorno_listagem": "recovered_listing_by_url",
                    "url_antes_detalhe": "https://gdneoenergiapernambuco.neoenergia.com/lista",
                    "url_depois_detalhe": "https://gdneoenergiapernambuco.neoenergia.com/detalhe",
                    "url_apos_retorno": "https://gdneoenergiapernambuco.neoenergia.com/lista",
                }
            ],
        },
        {"results": []},
    )

    assert rows[0]["abriu_detalhe"] is True
    assert rows[0]["retorno_listagem_status"] == "recovered_listing_by_url"
    assert rows[0]["metodo_retorno_listagem"] == "recovered_listing_by_url"
    assert rows[0]["url_depois_detalhe"].endswith("/detalhe")
