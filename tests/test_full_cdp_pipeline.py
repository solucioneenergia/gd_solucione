import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from scripts.run_full_cdp_pipeline import (
    _apply_global_protocol_limit,
    _build_protocol_rows,
    _pdf_paths_for_processing,
    build_pipeline_payload,
)
from automacao_gd.application import full_pipeline
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


class _SyntheticGenerationData:
    equipment_parse_warning = None

    def format_module_for_excel(self) -> str:
        return "MODULO SINTETICO | Total: 1 modulo"

    def format_inverter_for_excel(self) -> str:
        return "INVERSOR SINTETICO | Total: 1 inversor"

    def model_dump(self, mode: str = "json") -> dict:
        return {"source": "synthetic"}

    def multiple_module_models(self) -> bool:
        return False

    def multiple_inverter_models(self) -> bool:
        return False

    def module_pairs_count(self) -> int:
        return 1

    def inverter_pairs_count(self) -> int:
        return 1


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


def test_batch_fast_selection_skips_protocol_with_complete_workbook_row(
    tmp_path: Path,
) -> None:
    workbook_path = tmp_path / "planilha.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(
        [
            "Cliente",
            "Protocolo",
            "Data de ingresso",
            "Conclusão",
            "Parecer",
            "Placa",
            "Inversor",
        ]
    )
    ws.append(
        [
            "CLIENTE SINTETICO LTDA",
            "2600001048",
            "16/01/2026",
            "20/01/2026",
            "Sim",
            "10x FAB MOD",
            "1x FAB INV",
        ]
    )
    wb.save(workbook_path)
    wb.close()

    settings = DummySettings()
    settings.OP5_RECONCILIATION_MODE = "batch_fast"
    settings.planilha_path = workbook_path
    settings.APPLY_EXCEL = True
    requests = [
        _row("2600001048")["record"],
        _row("2600001049")["record"],
    ]

    selection = select_eligible_completed_requests(
        requests,
        pipeline_state=None,
        max_completed_to_process=1,
        skip_already_completed=True,
        force_reprocess_protocols=set(),
        settings=settings,
    )

    assert [record.protocol for record in selection["selected_records"]] == ["2600001049"]
    assert selection["skipped_completed"][0]["protocol"] == "2600001048"


def test_batch_fast_selection_reuses_cached_workbook_protocol_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workbook_path = tmp_path / "planilha.xlsx"
    workbook_path.write_bytes(b"workbook-sha-input")
    cache_dir = tmp_path / "logs"
    cache_dir.mkdir()

    class Settings(DummySettings):
        OP5_RECONCILIATION_MODE = "batch_fast"
        APPLY_EXCEL = True
        planilha_path = workbook_path
        logs_dir_path = cache_dir

    calls: list[str] = []

    def fake_protocol_row_has_required_values(path: Path, protocol: str) -> dict:
        calls.append(protocol)
        return {
            "success": True,
            "complete": protocol == "2600001048",
            "worksheet": "2026",
            "row": 49,
            "missing_columns": [] if protocol == "2600001048" else ["Conclusão"],
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "protocol_row_has_required_values",
        fake_protocol_row_has_required_values,
    )

    for _ in range(2):
        selection = select_eligible_completed_requests(
            [_row("2600001048")["record"], _row("2600001049")["record"]],
            pipeline_state=None,
            max_completed_to_process=1,
            skip_already_completed=True,
            force_reprocess_protocols=set(),
            settings=Settings(),
        )
        assert [record.protocol for record in selection["selected_records"]] == [
            "2600001049"
        ]

    assert calls == ["2600001048", "2600001049"]


def test_target_protocols_restrict_batch_fast_selection() -> None:
    requests = [
        _row("2600001048")["record"],
        _row("2600001049")["record"],
        _row("2600001050")["record"],
    ]

    selection = select_eligible_completed_requests(
        requests,
        {},
        max_completed_to_process=2,
        skip_already_completed=True,
        force_reprocess_protocols=set(),
        settings=type(
            "TargetSettings",
            (),
            {"op5_target_protocols": {"2600001050"}},
        )(),
    )

    assert [record.protocol for record in selection["selected_records"]] == [
        "2600001050"
    ]
    assert [record.protocol for record in selection["eligible_records"]] == [
        "2600001050"
    ]


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


def test_numeric_pagination_uses_locator_fallback_when_target_link_was_seen(
    monkeypatch,
) -> None:
    fallback_calls = []

    class NumericPage:
        def evaluate(self, script: str, target_page_number: int) -> dict:
            return {
                "found": False,
                "enabled": False,
                "clicked": False,
                "selector": None,
                "text": str(target_page_number),
                "class_name": None,
                "mode": "numeric",
                "current_page_number": target_page_number - 1,
                "target_page_number": target_page_number,
                "numeric_page_links_found": ["6", "7", "8", "9", "10", "11", "12", "13", "14", "15"],
                "numeric_page_links_count": 10,
                "stop_reason": "pagination_numeric_target_not_found",
            }

    def fake_locator_click(page, *, target_page_number: int) -> dict:
        fallback_calls.append(target_page_number)
        return {
            "clicked": True,
            "selector": ".ui-paginator a.ui-paginator-page",
            "index": 9,
            "text": str(target_page_number),
            "stop_reason": "pagination_numeric_page_clicked",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "_click_numeric_paginator_with_playwright",
        fake_locator_click,
    )

    diagnostic = find_and_click_next_numeric_page(NumericPage(), 14)

    assert fallback_calls == [15]
    assert diagnostic["found"] is True
    assert diagnostic["enabled"] is True
    assert diagnostic["clicked"] is True
    assert diagnostic["stop_reason"] == "pagination_numeric_page_clicked"


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


def test_numeric_pagination_locator_fallback_uses_generic_paginator_selector() -> None:
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
        def __init__(self, target: FakeLink):
            self.target = target
            self.selectors: list[str] = []

        def locator(self, selector: str) -> FakeLocator:
            self.selectors.append(selector)
            if selector == "[class*='paginator'] span":
                return FakeLocator([FakeLink("14", "ui-paginator-page"), self.target])
            return FakeLocator([])

    target_fifteen = FakeLink("15", "ui-paginator-page")
    page = FakePage(target_fifteen)

    result = _click_numeric_paginator_with_playwright(page, target_page_number=15)

    assert result["clicked"] is True
    assert result["selector"] == "[class*='paginator'] span"
    assert target_fifteen.clicked is True
    assert ".ui-paginator a.ui-paginator-page" in page.selectors


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


def test_reset_listing_recovers_after_context_destroyed_during_initial_reset(
    monkeypatch,
) -> None:
    active_pages = iter([2, 1])
    waits = []
    row_reads = []

    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: next(active_pages),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "navigate_to_numeric_page",
        lambda page, target: {
            "success": False,
            "status": "pagination_numeric_click_error",
            "method": "numeric_page_navigation",
            "target_page_number": target,
            "error": (
                "Page.evaluate: Execution context was destroyed, "
                "most likely because of a navigation"
            ),
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_wait_after_pagination_click",
        lambda page: waits.append("wait"),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: row_reads.append("rows") or [_row("2600001107")],
    )

    result = ensure_listing_starts_on_page_one(FakePage())

    assert result["success"] is True
    assert result["status"] == "reset_to_first_page_after_context_recovery"
    assert result["initial_active_page"] == 2
    assert result["active_page_after"] == 1
    assert waits == ["wait"]
    assert row_reads == ["rows"]


def test_reset_listing_fails_closed_when_context_destroyed_and_listing_not_confirmed(
    monkeypatch,
) -> None:
    active_pages = iter([2, None])
    waits = []

    monkeypatch.setattr(
        cdp_portal_service,
        "get_active_numeric_page",
        lambda page: next(active_pages),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "navigate_to_numeric_page",
        lambda page, target: {
            "success": False,
            "status": "pagination_numeric_click_error",
            "method": "numeric_page_navigation",
            "target_page_number": target,
            "error": (
                "Page.evaluate: Execution context was destroyed, "
                "most likely because of a navigation"
            ),
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_wait_after_pagination_click",
        lambda page: waits.append("wait"),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: [],
    )

    result = ensure_listing_starts_on_page_one(FakePage())

    assert result["success"] is False
    assert result["status"] == "pagination_context_destroyed_during_reset"
    assert "Execution context was destroyed" in result["error"]
    assert result["active_page_after"] is None
    assert waits == ["wait"]


def test_reset_listing_accepts_disabled_page_one_when_active_indicator_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: None)
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: [_row("2600001048")],
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        lambda page, current_page_number: {
            "found": True,
            "enabled": False,
            "clicked": False,
            "current_page_number": current_page_number,
            "target_page_number": 1,
            "numeric_page_links_found": ["1", "2", "3"],
            "stop_reason": "pagination_numeric_target_disabled",
        },
    )

    result = ensure_listing_starts_on_page_one(FakePage())

    assert result["success"] is True
    assert result["status"] == "assumed_first_page_active_unconfirmed"
    assert result["active_page_after"] == 1
    assert result["click_result"]["target_page_number"] == 1


def test_reset_listing_retries_empty_rows_before_rejecting_first_page(
    monkeypatch,
) -> None:
    row_reads = iter([[], [_row("2600001048")]])
    waits = []

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: None)
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: next(row_reads),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_wait_after_pagination_click",
        lambda page: waits.append("wait"),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        lambda page, current_page_number: {
            "found": True,
            "enabled": False,
            "clicked": False,
            "current_page_number": current_page_number,
            "target_page_number": 1,
            "numeric_page_links_found": ["1", "2", "3"],
            "stop_reason": "pagination_numeric_target_disabled",
        },
    )

    result = ensure_listing_starts_on_page_one(FakePage())

    assert result["success"] is True
    assert result["status"] == "assumed_first_page_active_unconfirmed"
    assert result["active_page_after"] == 1
    assert waits == ["wait"]


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


def test_batch_fast_collect_stops_when_requested_local_limit_is_reached(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 15
        MAX_COMPLETED_TO_PROCESS = 2
        OP5_RECONCILIATION_MODE = "batch_fast"
        APPLY_EXCEL = False
        downloads_dir_path = tmp_path

    for protocol in ("2600000001", "2600000002"):
        protocol_dir = tmp_path / protocol
        protocol_dir.mkdir()
        (protocol_dir / f"Orcamento_de_Conexao_{protocol}.pdf").write_bytes(
            b"%PDF-1.4\n%%EOF"
        )

    active_pages = iter([1, 2])
    reads: list[int] = []
    clicks: list[int] = []

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: next(active_pages))

    def fake_read(page):
        page_number = len(reads) + 1
        reads.append(page_number)
        return _page(1 if page_number == 1 else 3, 2)

    monkeypatch.setattr(cdp_portal_service, "read_current_page_table_with_row_handles", fake_read)

    def fake_next(page, current_page_number: int):
        clicks.append(current_page_number)
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": ".ui-paginator-next",
            "text": str(current_page_number + 1),
            "class_name": "",
            "stop_reason": None,
        }

    monkeypatch.setattr(cdp_portal_service, "find_and_click_next_listing_page", fake_next)
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(FakePage(), Settings())

    assert summary["pages_read"] == 1
    assert summary["total_completed"] == 2
    assert summary["pagination_stop_reason"] == "incremental_batch_limit_reached"
    assert summary["pagination_complete"] is False
    assert clicks == []


def test_op5_plan_limit_50_continues_across_pages_without_active_root_navigation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 10
        MAX_COMPLETED_TO_PROCESS = 50
        OP5_RECONCILIATION_MODE = "batch_fast"
        APPLY_EXCEL = False
        downloads_dir_path = tmp_path

    for index in range(50):
        protocol = f"2600{index:06d}"
        protocol_dir = tmp_path / protocol
        protocol_dir.mkdir()
        (protocol_dir / f"Orcamento_de_Conexao_{protocol}.pdf").write_bytes(
            b"%PDF-1.4\n%%EOF"
        )

    active_page = {"value": 1}
    reads: list[int] = []
    clicks: list[int] = []
    navigation_calls: list[str] = []

    class ListingPage(FakePage):
        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, *_args, **_kwargs) -> None:
            navigation_calls.append("go_back")

    def fake_read(page):
        reads.append(active_page["value"])
        start = (active_page["value"] - 1) * 15
        return _page(start, 15)

    def fake_next(page, current_page_number: int):
        clicks.append(current_page_number)
        active_page["value"] = current_page_number + 1
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

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: active_page["value"])
    monkeypatch.setattr(cdp_portal_service, "read_current_page_table_with_row_handles", fake_read)
    monkeypatch.setattr(cdp_portal_service, "find_and_click_next_listing_page", fake_next)
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    summary = _collect_completed_listing_rows_across_pages(ListingPage(), Settings())

    assert summary["pages_read"] == 4
    assert summary["total_completed"] == 60
    assert summary["pagination_stop_reason"] == "incremental_batch_limit_reached"
    assert summary["pagination_complete"] is False
    assert clicks == [1, 2, 3]
    assert navigation_calls == []


def test_op5_plan_counts_only_excel_write_actions_as_planned() -> None:
    payload = {
        "json_report_path": "data/logs/pipeline_cdp_completo.json",
        "status": "SUCESSO",
        "requested_batch_limit": 50,
        "authorized_batch_limit": 60,
        "authorization_scope": "CONTROLLED_PRODUCTION_OPTION5_UP_TO_60",
        "workbook_path": "pyproject.toml",
        "apply_excel": True,
        "apply_archive": True,
        "total_selected": 50,
        "total_updates_planned": 2,
        "total_updates_applied": 0,
        "total_errors": 0,
        "download": {
            "frozen_batch": {"protocols": ["2600001048", "2600001049", "2600001050"]},
            "frozen_pdf_scope": {"artifacts": []},
        },
        "processing": {
            "results": [
                {
                    "protocol": "2600001048",
                    "excel_status": {"action": "skipped_excel_already_updated"},
                },
                {
                    "protocol": "2600001049",
                    "excel_status": {"action": "update_existing"},
                },
                {
                    "protocol": "2600001050",
                    "excel_status": {"action": "insert_new_chronological"},
                },
            ]
        },
    }

    plan = full_pipeline._build_op5_plan_payload(payload)

    assert plan["total_selected"] == 2
    assert plan["total_updates_planned"] == 2
    assert plan["planned_excel_actions"] == [
        {"protocol": "2600001049", "action": "update_existing"},
        {"protocol": "2600001050", "action": "insert_new_chronological"},
    ]
    assert plan["download"]["frozen_batch"]["protocols"] == [
        "2600001049",
        "2600001050",
    ]

def test_batch_fast_does_not_require_global_reconciliation_before_lot(monkeypatch) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 15
        MAX_COMPLETED_TO_PROCESS = 2
        OP5_RECONCILIATION_MODE = "batch_fast"

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
        lambda page, settings, **_kwargs: {
            "pages_read": 1,
            "total_rows": 2,
            "total_completed": 1,
            "completed_records": records,
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_complete": False,
            "last_page_confirmed": False,
            "last_page_number": None,
            "pages_visited": [1],
            "next_page_available_after_stop": True,
            "pagination_stop_reason": "incremental_batch_limit_reached",
            "pagination_next_found": True,
            "pagination_safety_cap": 15,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": 2,
            "pagination_numeric_links_found": ["2"],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda *args, **kwargs: {
            "success": False,
            "error": "synthetic stop before real portal detail",
            "status": "protocol_not_found_on_origin_page",
            "row": None,
        },
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        reconciliation_callback=lambda *args: reconciliation_calls.append(args) or {},
    )

    assert summary.get("aborted") is not True
    assert summary["pagination_stop_reason"] == "incremental_batch_limit_reached"
    assert summary["reconciliation_mode"] == "batch_fast"
    assert summary["reconciliation"] == {
        "metrics_scope": "BATCH_FAST",
        "set_reconciliation_authoritative": False,
    }
    assert reconciliation_calls == []


def test_batch_fast_uses_partial_page_when_pagination_stalls_with_local_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 15
        MAX_COMPLETED_TO_PROCESS = 2
        OP5_RECONCILIATION_MODE = "batch_fast"
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True

    record = PortalSolicitation(protocol="2600001048", status="CONCLUIDA", page_number=1)
    protocol_dir = tmp_path / record.protocol
    protocol_dir.mkdir()
    (protocol_dir / f"Orcamento_de_Conexao_{record.protocol}.pdf").write_bytes(
        b"%PDF-1.4\n%%EOF"
    )
    (protocol_dir / "metadata.json").write_text(
        '{"protocol": "2600001048", "completion_date_raw": "01/01/2026"}',
        encoding="utf-8",
    )

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
        lambda page, settings, **_kwargs: {
            "pages_read": 1,
            "total_rows": 50,
            "total_completed": 1,
            "completed_records": [record],
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_complete": False,
            "last_page_confirmed": False,
            "last_page_number": None,
            "pages_visited": [1],
            "next_page_available_after_stop": False,
            "pagination_stop_reason": "pagination_loop_detected",
            "pagination_next_found": True,
            "pagination_safety_cap": 15,
            "pagination_click_attempts": 1,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": 2,
            "pagination_numeric_links_found": ["1", "2"],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local PDF reuse must not navigate stalled CDP")
        ),
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        downloads_root=tmp_path,
        max_completed=2,
        settings=Settings(),
    )

    assert summary["aborted"] is False
    assert summary["run_error"] is None
    assert summary["pagination_complete"] is False
    assert summary["pagination_stop_reason"] == "pagination_loop_detected"
    assert summary["total_selected"] == 1
    assert summary["total_for_processing"] == 1
    assert summary["results"][0]["download_status"] == "existing_pdf_after_skip"


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


def test_numeric_navigation_to_previous_hidden_page_uses_previous_button(
    monkeypatch,
) -> None:
    state = {"page": 3}
    previous_clicks = []

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: state["page"])
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: _page(state["page"] * 10, 2),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        lambda page, target: {
            "found": False,
            "enabled": False,
            "clicked": False,
            "target_page_number": target,
            "numeric_page_links_found": ["2", "3"],
            "stop_reason": "pagination_numeric_target_not_found",
        },
    )

    def fake_previous(page, current_page_number: int) -> dict:
        previous_clicks.append(current_page_number)
        state["page"] = current_page_number - 1
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "current_page_number": current_page_number,
            "target_page_number": state["page"],
            "stop_reason": "pagination_previous_clicked",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_previous_listing_page",
        fake_previous,
        raising=False,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    result = navigate_to_numeric_page(FakePage(), 1)

    assert result["success"] is True
    assert result["active_page_after"] == 1
    assert result["method"] == "recovered_listing_by_reverse_sequential_numeric_page"
    assert previous_clicks == [3, 2]


def test_numeric_navigation_accepts_changed_table_when_active_indicator_is_stale(
    monkeypatch,
) -> None:
    state = {"after_click": False}

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: 1)
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: _page(50 if state["after_click"] else 0, 2),
    )

    def fake_click(page, target: int) -> dict:
        state["after_click"] = True
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "target_page_number": target,
            "numeric_page_links_found": ["1", "2"],
            "stop_reason": "pagination_numeric_page_clicked",
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        fake_click,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    result = navigate_to_numeric_page(FakePage(), 2)

    assert result["success"] is True
    assert result["status"] == "recovered_listing_by_numeric_page_unconfirmed_active"
    assert result["method"] == "recovered_listing_by_numeric_page_unconfirmed_active"
    assert result["active_page_after"] == 1


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


def test_navigate_to_numeric_page_steps_until_target_when_direct_link_is_hidden(
    monkeypatch,
) -> None:
    active_page = {"value": 1}
    clicks = []

    def fake_active_page(page):
        return active_page["value"]

    def fake_rows(page):
        return [_row(f"26000010{active_page['value']:02d}")]

    def fake_direct_numeric_click(page, current_page_number: int):
        assert current_page_number == 3
        return {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": "4",
            "mode": "numeric",
            "current_page_number": 3,
            "target_page_number": 4,
            "numeric_page_links_found": ["1", "2", "3"],
            "numeric_page_links_count": 3,
            "stop_reason": "pagination_numeric_target_not_found",
        }

    def fake_next_page(page, current_page_number: int):
        clicks.append(current_page_number + 1)
        active_page["value"] = current_page_number + 1
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": "div.paginator > a",
            "text": str(current_page_number + 1),
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1", "2", "3", "4"],
            "numeric_page_links_count": 4,
            "stop_reason": "pagination_numeric_page_clicked",
        }

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", fake_active_page)
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        fake_rows,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        fake_direct_numeric_click,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_listing_page",
        fake_next_page,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    result = navigate_to_numeric_page(FakePage(), 4)

    assert result["success"] is True
    assert result["status"] == "recovered_listing_by_numeric_page"
    assert result["method"] == "recovered_listing_by_sequential_numeric_page"
    assert result["active_page_after"] == 4
    assert clicks == [2, 3, 4]


def test_navigate_to_numeric_page_uses_sequential_fallback_when_direct_click_stays_on_same_page(
    monkeypatch,
) -> None:
    active_page = {"value": 1}
    rows_by_page = {
        1: [_row("2600001011"), _row("2600001012")],
        2: [_row("2600001048"), _row("2600001049")],
    }
    direct_clicks = []
    sequential_clicks = []

    def fake_active_page(page):
        return active_page["value"]

    def fake_rows(page):
        return rows_by_page[active_page["value"]]

    def fake_direct_numeric_click(page, current_page_number: int):
        direct_clicks.append(current_page_number + 1)
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": ".ui-paginator a",
            "text": str(current_page_number + 1),
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": ["1", "2"],
            "numeric_page_links_count": 2,
            "stop_reason": "pagination_numeric_page_clicked",
        }

    def forbidden_numeric_first_fallback(page, current_page_number: int):
        raise AssertionError("fallback must use next-button diagnostic directly")

    def fake_next_button(page):
        sequential_clicks.append(2)
        active_page["value"] = 2
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "selector": ".ui-paginator-next",
            "text": ">",
            "stop_reason": "pagination_next_clicked",
        }

    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", fake_active_page)
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        fake_rows,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_numeric_page",
        fake_direct_numeric_click,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_and_click_next_listing_page",
        forbidden_numeric_first_fallback,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_next_listing_page_diagnostic",
        fake_next_button,
    )
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    result = navigate_to_numeric_page(FakePage(), 2)

    assert result["success"] is True
    assert result["status"] == "recovered_listing_by_numeric_page"
    assert result["method"] == "recovered_listing_by_sequential_numeric_page"
    assert result["active_page_after"] == 2
    assert direct_clicks == [2]
    assert sequential_clicks == [2]


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


def test_origin_navigation_recovers_when_detail_return_leaves_listing_on_wrong_page(
    monkeypatch,
) -> None:
    request = PortalSolicitation(
        protocol="2603",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=3,
        row_index=2,
    )
    active_page = {"value": 1}
    rows_by_page = {
        1: [_row("2601")],
        2: [_row("2602")],
        3: [_row("2603")],
    }
    navigation_calls: list[str] = []

    class ListingPage(FakePage):
        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, *_args, **_kwargs) -> None:
            navigation_calls.append("go_back")

    monkeypatch.setattr(cdp_portal_service, "_has_minhas_solicitacoes_table", lambda page: True)
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )
    monkeypatch.setattr(cdp_portal_service, "get_active_numeric_page", lambda page: active_page["value"])
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: rows_by_page[active_page["value"]],
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
            "numeric_page_links_found": ["1"],
            "stop_reason": "pagination_numeric_target_not_found",
        },
    )

    def fake_next(page, current_page_number: int):
        active_page["value"] = current_page_number + 1
        return {
            "found": True,
            "enabled": True,
            "clicked": True,
            "mode": "next_button",
            "current_page_number": current_page_number,
            "target_page_number": current_page_number + 1,
            "numeric_page_links_found": [],
            "stop_reason": "pagination_next_clicked",
        }

    monkeypatch.setattr(cdp_portal_service, "find_and_click_next_listing_page", fake_next)
    monkeypatch.setattr(cdp_portal_service, "_wait_after_pagination_click", lambda page: None)

    result = ensure_request_origin_page(
        ListingPage(),
        request,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
        allow_active_navigation=False,
    )

    assert result["success"] is True
    assert result["status"] == "protocol_found_on_origin_page"
    assert result["method"] == "recovered_listing_by_sequential_numeric_page"
    assert result["protocol_found_on_origin_page"] is True
    assert navigation_calls == []


def test_origin_navigation_fails_closed_when_wrong_page_cannot_be_recovered_safely(
    monkeypatch,
) -> None:
    request = PortalSolicitation(
        protocol="2603",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=3,
        row_index=2,
    )
    navigation_calls: list[str] = []

    class ListingPage(FakePage):
        def __init__(self) -> None:
            self.context = type("Context", (), {"pages": [self]})()

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, *_args, **_kwargs) -> None:
            navigation_calls.append("go_back")

    monkeypatch.setattr(cdp_portal_service, "_has_minhas_solicitacoes_table", lambda page: False)
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "read_current_page_table_with_row_handles",
        lambda page: [_row("2601")],
    )

    result = ensure_request_origin_page(
        ListingPage(),
        request,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
        allow_active_navigation=False,
    )

    assert result["success"] is False
    assert result["status"] in {
        "failed_return_to_listing",
        "pagination_numeric_target_not_found",
        "pagination_active_page_mismatch",
        "protocol_not_found_on_origin_page",
    }
    assert "PowerShell" in result["error"]
    assert navigation_calls == []


def test_op5_plan_never_recovers_listing_by_root_http_or_unsafe_url(
    monkeypatch,
) -> None:
    request = PortalSolicitation(
        protocol="2603",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=3,
        row_index=2,
    )
    navigation_calls: list[str] = []

    class AccessDeniedPage(FakePage):
        url = "http://gdneoenergiapernambuco.neoenergia.com/index.jsf"

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, *_args, **_kwargs) -> None:
            navigation_calls.append("go_back")

    monkeypatch.setattr(cdp_portal_service, "_has_minhas_solicitacoes_table", lambda page: False)
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )

    result = ensure_request_origin_page(
        AccessDeniedPage(),
        request,
        "http://gdneoenergiapernambuco.neoenergia.com/index.jsf",
        allow_active_navigation=False,
    )

    assert result["success"] is False
    assert result["status"] == "failed_return_to_listing"
    assert "PowerShell" in result["error"]
    assert navigation_calls == []


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

    def fake_origin(page, request, listing_url=None, **_kwargs):
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


def test_download_preserves_partial_batch_after_failed_return_with_reusable_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 1
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True

    records = [
        PortalSolicitation(protocol="2600001048", status="CONCLUIDA", page_number=1),
        PortalSolicitation(protocol="2600001049", status="CONCLUIDA", page_number=1),
    ]

    class ListingPage:
        url = "https://portal/listagem"

        def __init__(self) -> None:
            self.context = type("Context", (), {"pages": [self]})()

        def wait_for_timeout(self, timeout: int) -> None:
            return None

    existing_pdf = tmp_path / "Orcamento_de_Conexao_2600001048.pdf"
    existing_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    origin_calls: list[str] = []

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
        lambda page, settings, **kwargs: {
            "pages_read": 1,
            "total_rows": 2,
            "total_completed": 2,
            "completed_records": records,
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_stop_reason": "last_page_reached",
            "pagination_next_found": False,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": None,
            "pagination_numeric_links_found": [],
            "pagination_diagnostics": [],
        },
    )

    def fake_origin(page, request, listing_url=None, **_kwargs):
        origin_calls.append(request.protocol)
        return {
            "success": True,
            "status": "protocol_found_on_origin_page",
            "method": "current_page",
            "protocol_found_on_origin_page": True,
            "url_after": "https://portal/listagem",
            "error": None,
            "row": {
                "record": request,
                "row_locator": object(),
                "action_cell_index": 6,
            },
        }

    monkeypatch.setattr(cdp_portal_service, "ensure_request_origin_page", fake_origin)
    monkeypatch.setattr(cdp_portal_service, "click_follow_eye_button", lambda *args: None)
    monkeypatch.setattr(cdp_portal_service, "wait_detail_loaded", lambda *args: None)
    monkeypatch.setattr(cdp_portal_service, "extract_detail_header", lambda page: {})
    monkeypatch.setattr(cdp_portal_service, "detail_has_completed_status", lambda page: True)
    monkeypatch.setattr(
        cdp_portal_service,
        "extract_point_of_connection_completion",
        lambda page, protocol: {
            "completion_date_raw": "01/01/2026",
            "completion_date_normalized": "2026-01-01",
            "completion_source_stage": "synthetic",
            "completion_source_selector": "synthetic",
            "completion_extraction_status": "found",
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "save_download_metadata",
        lambda protocol_dir, payload: tmp_path / "metadata.json",
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "find_existing_connection_budget_pdf",
        lambda protocol, downloads_root: existing_pdf
        if protocol == "2600001048"
        else None,
    )
    downloaded_pdf = tmp_path / "Orcamento_de_Conexao_2600001049.pdf"

    monkeypatch.setattr(
        cdp_portal_service,
        "find_connection_budget_target",
        lambda page: object(),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "download_connection_budget",
        lambda page, protocol, downloads_root, target: downloaded_pdf,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "extract_generation_data",
        lambda pdf_path: _SyntheticGenerationData(),
    )
    downloaded_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(
        cdp_portal_service,
        "_return_to_listing_after_detail",
        lambda detail_page, listing_page, listing_url: (
            listing_page,
            {
                "success": False,
                "status": "failed_return_to_listing",
                "method": "passive_return",
                "url_after": "https://portal/detalhe",
                "error": "manual recovery required",
            },
        ),
    )

    summary = download_completed_budgets_from_current_page(
        ListingPage(),
        downloads_root=tmp_path,
        max_completed=2,
        settings=Settings(),
    )

    assert summary["aborted"] is False
    assert summary["run_error"] is None
    assert summary["partial_batch_due_to_listing_recovery"] is True
    assert summary["abort_reason"] == "stopped_after_failed_return_to_listing"
    assert origin_calls == ["2600001049"]
    assert [item["protocol"] for item in summary["results"]] == [
        "2600001048",
        "2600001049",
    ]
    assert summary["results"][0]["download_status"] == "existing_pdf_after_skip"
    assert summary["results"][0]["selected_for_processing"] is True


def test_batch_fast_does_not_stop_pagination_until_local_reusable_limit(
    tmp_path: Path,
) -> None:
    class Settings(DummySettings):
        OP5_RECONCILIATION_MODE = "batch_fast"
        APPLY_EXCEL = True
        downloads_dir_path = tmp_path
        planilha_path = tmp_path / "planilha.xlsx"
        logs_dir_path = tmp_path / "logs"

    settings = Settings()
    settings.planilha_path.write_bytes(b"workbook")
    settings.logs_dir_path.mkdir()
    reusable_protocol = "2600001048"
    reusable_dir = tmp_path / reusable_protocol
    reusable_dir.mkdir()
    (reusable_dir / f"Orcamento_de_Conexao_{reusable_protocol}.pdf").write_bytes(
        b"%PDF-1.4\n%%EOF"
    )

    rows = [
        [
            _row("2600001047"),
            {
                "record": PortalSolicitation(
                    protocol=reusable_protocol,
                    status="CONCLUIDA",
                    completion_date="01/02/2026",
                )
            },
        ]
    ]

    reached = cdp_portal_service._incremental_batch_limit_reached(
        rows,
        settings,
        max_completed=2,
    )

    assert reached is False


def test_batch_fast_prioritizes_local_reusable_pdfs_before_opening_detail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 3
        OP5_RECONCILIATION_MODE = "batch_fast"
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True
        APPLY_EXCEL = True
        logs_dir_path = tmp_path / "logs"
        planilha_path = tmp_path / "planilha.xlsx"

    settings = Settings()
    settings.logs_dir_path.mkdir()
    settings.planilha_path.write_bytes(b"workbook")
    local_protocols = ["2600001049", "2600001050"]
    for protocol in local_protocols:
        protocol_dir = tmp_path / protocol
        protocol_dir.mkdir()
        (protocol_dir / f"Orcamento_de_Conexao_{protocol}.pdf").write_bytes(
            b"%PDF-1.4\n%%EOF"
        )

    records = [
        PortalSolicitation(
            protocol="2600001048",
            status="CONCLUIDA",
            page_number=1,
            completion_date="01/02/2026",
        ),
        PortalSolicitation(
            protocol="2600001049",
            status="CONCLUIDA",
            page_number=1,
            completion_date="01/02/2026",
        ),
        PortalSolicitation(
            protocol="2600001050",
            status="CONCLUIDA",
            page_number=2,
            completion_date="01/02/2026",
        ),
    ]

    monkeypatch.setattr(cdp_portal_service, "get_settings", lambda: settings)
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
        lambda page, settings, **kwargs: {
            "pages_read": 2,
            "total_rows": 3,
            "total_completed": 3,
            "completed_records": records,
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_complete": False,
            "last_page_confirmed": False,
            "last_page_number": None,
            "next_page_available_after_stop": True,
            "pagination_safety_cap": 3,
            "pages_visited": [1, 2],
            "pagination_stop_reason": "incremental_batch_limit_reached",
            "pagination_next_found": True,
            "pagination_click_attempts": 1,
            "pagination_mode": "numeric",
            "pagination_current_page": 2,
            "pagination_target_page": 3,
            "pagination_numeric_links_found": ["1", "2", "3"],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local reusable PDFs must be processed before detail")
        ),
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        downloads_root=tmp_path,
        max_completed=2,
        settings=settings,
    )

    assert summary["aborted"] is False
    assert summary["total_errors"] == 0
    assert [item["protocol"] for item in summary["results"]] == local_protocols
    assert all(item["abriu_detalhe"] is False for item in summary["results"])
    assert summary["total_for_processing"] == 2


def test_download_aborts_after_failed_return_without_reusable_pdf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 1
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True

    record = PortalSolicitation(protocol="2600001048", status="CONCLUIDA", page_number=1)

    class ListingPage:
        url = "https://portal/listagem"

        def __init__(self) -> None:
            self.context = type("Context", (), {"pages": [self]})()

        def wait_for_timeout(self, timeout: int) -> None:
            return None

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
        lambda page, settings, **kwargs: {
            "pages_read": 1,
            "total_rows": 1,
            "total_completed": 1,
            "completed_records": [record],
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_stop_reason": "last_page_reached",
            "pagination_next_found": False,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": None,
            "pagination_numeric_links_found": [],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda page, request, listing_url=None, **_kwargs: {
            "success": True,
            "status": "protocol_found_on_origin_page",
            "method": "current_page",
            "protocol_found_on_origin_page": True,
            "url_after": "https://portal/listagem",
            "error": None,
            "row": {
                "record": request,
                "row_locator": object(),
                "action_cell_index": 6,
            },
        },
    )
    monkeypatch.setattr(cdp_portal_service, "click_follow_eye_button", lambda *args: None)
    monkeypatch.setattr(cdp_portal_service, "wait_detail_loaded", lambda *args: None)
    monkeypatch.setattr(cdp_portal_service, "extract_detail_header", lambda page: {})
    monkeypatch.setattr(cdp_portal_service, "detail_has_completed_status", lambda page: True)
    monkeypatch.setattr(
        cdp_portal_service,
        "extract_point_of_connection_completion",
        lambda page, protocol: {},
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "download_connection_budget",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_return_to_listing_after_detail",
        lambda detail_page, listing_page, listing_url: (
            listing_page,
            {
                "success": False,
                "status": "failed_return_to_listing",
                "method": "passive_return",
                "url_after": "https://portal/detalhe",
                "error": "manual recovery required",
            },
        ),
    )

    summary = download_completed_budgets_from_current_page(
        ListingPage(),
        downloads_root=tmp_path,
        max_completed=1,
        settings=Settings(),
    )

    assert summary["aborted"] is True
    assert summary["run_error"] == "failed_return_to_listing"
    assert summary["abort_reason"] == "failed_return_to_listing"
    assert summary["results"][0]["selected_for_processing"] is False


def test_download_reuses_existing_pdf_without_opening_detail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 1
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True

    record = PortalSolicitation(
        protocol="2600001048",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=1,
        entry_date="10/01/2026",
    )
    protocol_dir = tmp_path / record.protocol
    protocol_dir.mkdir()
    pdf_path = protocol_dir / f"Orcamento_de_Conexao_{record.protocol}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    metadata_path = protocol_dir / "metadata.json"
    metadata_path.write_text(
        """
        {
          "protocol": "2600001048",
          "client_name": "CLIENTE SINTETICO LTDA",
          "completion_date_raw": "01/02/2026",
          "completion_date": "2026-02-01",
          "completion_date_normalized": "2026-02-01",
          "completion_source_stage": "synthetic",
          "completion_source_selector": "synthetic",
          "completion_extraction_status": "found"
        }
        """,
        encoding="utf-8",
    )

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
        lambda page, settings, **kwargs: {
            "pages_read": 1,
            "total_rows": 1,
            "total_completed": 1,
            "completed_records": [record],
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_stop_reason": "last_page_reached",
            "pagination_next_found": False,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": None,
            "pagination_numeric_links_found": [],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing PDF must not open/navigate detail")
        ),
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        downloads_root=tmp_path,
        max_completed=1,
        settings=Settings(),
    )

    assert summary["aborted"] is False
    assert summary["total_errors"] == 0
    assert summary["total_selected"] == 1
    assert summary["total_for_processing"] == 1
    [result] = summary["results"]
    assert result["abriu_detalhe"] is False
    assert result["motivo_nao_abriu_detalhe"] == "skipped_existing_pdf_no_detail"
    assert result["download_status"] == "existing_pdf_after_skip"
    assert result["process_pdf_path"] == str(pdf_path)
    assert result["completion_date_raw"] == "01/02/2026"
    assert result["completion_date_normalized"] == "2026-02-01"


def test_batch_fast_reuses_existing_pdf_with_listing_completion_without_opening_detail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 1
        OP5_RECONCILIATION_MODE = "batch_fast"
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True
        APPLY_EXCEL = True

    record = PortalSolicitation(
        protocol="2600001048",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=1,
        entry_date="10/01/2026",
        completion_date="01/02/2026",
    )
    protocol_dir = tmp_path / record.protocol
    protocol_dir.mkdir()
    pdf_path = protocol_dir / f"Orcamento_de_Conexao_{record.protocol}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    metadata_path = protocol_dir / "metadata.json"
    metadata_path.write_text(
        """
        {
          "protocol": "2600001048",
          "client_name": "CLIENTE SINTETICO LTDA"
        }
        """,
        encoding="utf-8",
    )

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
        lambda page, settings, **kwargs: {
            "pages_read": 1,
            "total_rows": 1,
            "total_completed": 1,
            "completed_records": [record],
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_stop_reason": "last_page_reached",
            "pagination_next_found": False,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": None,
            "pagination_numeric_links_found": [],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("listing completion plus existing PDF must not open detail")
        ),
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        downloads_root=tmp_path,
        max_completed=1,
        settings=Settings(),
    )

    assert summary["aborted"] is False
    assert summary["total_errors"] == 0
    assert summary["total_selected"] == 1
    assert summary["total_for_processing"] == 1
    [result] = summary["results"]
    assert result["abriu_detalhe"] is False
    assert result["download_status"] == "existing_pdf_after_skip"
    assert result["process_pdf_path"] == str(pdf_path)
    assert result["completion_date_raw"] == "01/02/2026"


def test_batch_fast_reuses_existing_pdf_without_metadata_when_listing_completion_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 1
        OP5_RECONCILIATION_MODE = "batch_fast"
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True
        APPLY_EXCEL = True

    record = PortalSolicitation(
        protocol="2600001050",
        client_name="CLIENTE SINTETICO LTDA",
        status="CONCLUIDA",
        page_number=1,
        entry_date="10/01/2026",
        completion_date="01/02/2026",
    )
    protocol_dir = tmp_path / record.protocol
    protocol_dir.mkdir()
    pdf_path = protocol_dir / f"Orcamento_de_Conexao_{record.protocol}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

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
        lambda page, settings, **kwargs: {
            "pages_read": 1,
            "total_rows": 1,
            "total_completed": 1,
            "completed_records": [record],
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_stop_reason": "last_page_reached",
            "pagination_next_found": False,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": None,
            "pagination_numeric_links_found": [],
            "pagination_diagnostics": [],
        },
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing PDF plus listing completion must not open detail")
        ),
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        downloads_root=tmp_path,
        max_completed=1,
        settings=Settings(),
    )

    assert summary["aborted"] is False
    assert summary["total_errors"] == 0
    [result] = summary["results"]
    assert result["abriu_detalhe"] is False
    assert result["download_status"] == "existing_pdf_after_skip"
    assert result["metadata_created_from_listing"] is True
    assert result["process_pdf_path"] == str(pdf_path)
    assert result["completion_date_raw"] == "01/02/2026"


def test_batch_fast_uses_cached_local_candidates_without_reopening_portal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from automacao_gd.application.op5_optimization import write_eligibility_cache

    class Settings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_PORTAL_PAGES = 15
        OP5_RECONCILIATION_MODE = "batch_fast"
        REPROCESS_EXISTING_PDFS = False
        PROCESS_EXISTING_AFTER_SKIP = True
        APPLY_EXCEL = True
        logs_dir_path = tmp_path / "logs"

    settings = Settings()
    settings.logs_dir_path.mkdir()
    records = []
    for index in range(2):
        protocol = f"260000105{index}"
        protocol_dir = tmp_path / protocol
        protocol_dir.mkdir()
        pdf_path = protocol_dir / f"Orcamento_de_Conexao_{protocol}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
        (protocol_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "protocol": protocol,
                    "completion_date_raw": "01/02/2026",
                    "completion_date": "2026-02-01",
                    "completion_date_normalized": "2026-02-01",
                    "completion_extraction_status": "found",
                }
            ),
            encoding="utf-8",
        )
        records.append(
            {
                "protocol": protocol,
                "page_number": 3,
                "row_index": index + 10,
                "status": "CONCLUIDA",
                "completion_date": "01/02/2026",
                "selection_reason": "eligible_new",
                "client_name": "CLIENTE SINTETICO LTDA",
            }
        )
    write_eligibility_cache(
        settings.logs_dir_path / "op5_portal_eligibility_cache.json",
        records=records,
        requested_limit=2,
        reconciliation_mode="batch_fast",
    )

    monkeypatch.setattr(cdp_portal_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_listing_starts_on_page_one",
        lambda page: (_ for _ in ()).throw(
            AssertionError("cache local suficiente nao deve resetar paginacao")
        ),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_collect_completed_listing_rows_across_pages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cache local suficiente nao deve reler portal")
        ),
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        downloads_root=tmp_path,
        max_completed=2,
        settings=settings,
    )

    assert summary["aborted"] is False
    assert summary["eligibility_cache_hit"] is True
    assert summary["total_selected"] == 2
    assert summary["total_pages_read"] == 0
    assert summary["total_errors"] == 0
    assert [item["download_status"] for item in summary["results"]] == [
        "existing_pdf_after_skip",
        "existing_pdf_after_skip",
    ]


def test_download_uses_injected_settings_for_target_protocols(monkeypatch) -> None:
    class GlobalSettings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_COMPLETED_TO_PROCESS = 2
        OP5_RECONCILIATION_MODE = "batch_fast"
        op5_target_protocols = set()

    class InjectedSettings(DummySettings):
        ENABLE_PORTAL_PAGINATION = True
        MAX_COMPLETED_TO_PROCESS = 2
        OP5_RECONCILIATION_MODE = "batch_fast"
        op5_target_protocols = {"2600001050"}

    records = [
        PortalSolicitation(protocol="2600001048", status="CONCLUIDA", page_number=1),
        PortalSolicitation(protocol="2600001049", status="CONCLUIDA", page_number=1),
        PortalSolicitation(protocol="2600001050", status="CONCLUIDA", page_number=1),
    ]
    seen_collection_settings: list[object] = []

    monkeypatch.setattr(cdp_portal_service, "get_settings", lambda: GlobalSettings())
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

    def fake_collect(
        page,
        settings,
        *,
        state_store=None,
        max_completed=None,
        skip_already_completed=True,
    ):
        seen_collection_settings.append(settings)
        return {
            "pages_read": 1,
            "total_rows": 3,
            "total_completed": 3,
            "completed_records": records,
            "duplicates_skipped": [],
            "pagination_warnings": [],
            "pagination_enabled": True,
            "pagination_complete": True,
            "last_page_confirmed": True,
            "last_page_number": 1,
            "next_page_available_after_stop": False,
            "pagination_safety_cap": 1,
            "pages_visited": [1],
            "pagination_stop_reason": "last_page_reached",
            "pagination_next_found": False,
            "pagination_click_attempts": 0,
            "pagination_mode": "numeric",
            "pagination_current_page": 1,
            "pagination_target_page": None,
            "pagination_numeric_links_found": [],
            "pagination_diagnostics": [],
        }

    monkeypatch.setattr(
        cdp_portal_service,
        "_collect_completed_listing_rows_across_pages",
        fake_collect,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_request_origin_page",
        lambda page, request, listing_url=None: {
            "success": False,
            "status": "protocol_not_found_on_origin_page",
            "method": "current_page",
            "protocol_found_on_origin_page": False,
            "url_after": "https://portal/listagem",
            "error": "stop_after_selection_for_test",
            "row": None,
        },
    )

    summary = download_completed_budgets_from_current_page(
        FakePage(),
        max_completed=2,
        settings=InjectedSettings(),
    )

    assert len(seen_collection_settings) == 1
    assert isinstance(seen_collection_settings[0], InjectedSettings)
    assert summary["total_selected"] == 1
    assert summary["target_protocols"] == ["2600001050"]
    assert [item["protocol"] for item in summary["selected_protocols"]] == [
        "2600001050"
    ]
    assert [item["protocol"] for item in summary["results"]] == ["2600001050"]


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


def test_consolidated_report_does_not_count_client_folder_pending_as_error(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "Orcamento_de_Conexao_2600001107.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    download_summary = {
        "total_rows": 1,
        "total_completed": 1,
        "total_selected": 1,
        "selected_protocols": [{"protocol": "2600001107"}],
        "results": [
            {
                "protocol": "2600001107",
                "download_status": "existing_pdf_after_skip",
                "process_pdf_path": str(pdf_path),
                "sent_to_processing": True,
            }
        ],
    }
    processing_summary = {
        "status": "SUCESSO",
        "total_success": 1,
        "total_errors": 0,
        "total_pdfs_analyzed": 1,
        "total_technically_approved": 1,
        "total_safe_protocols": 1,
        "total_pending_protocols": 0,
        "total_pending_review": 0,
        "total_client_folder_pending_review": 1,
        "total_updates_planned": 1,
        "total_updates_applied": 0,
        "total_real_extraction_errors": 0,
        "total_real_application_errors": 0,
        "results": [
            {
                "protocol": "2600001107",
                "success": True,
                "technical_validation_status": "approved",
                "technical_review_required": False,
                "client_folder_match_type": "not_found",
                "archive_match_type": "pending_manual_review",
                "excel_status": {"success": True, "can_write": True},
                "archive_status": {"success": True, "simulated": True},
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

    assert payload["total_pending_review"] == 0
    assert payload["total_client_folder_pending_review"] == 1
    assert payload["total_errors"] == 0


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


def test_return_to_listing_fails_closed_without_opening_new_portal_page(
    monkeypatch,
) -> None:
    class SameTabDetailPage:
        def __init__(self) -> None:
            self.name = "detail"
            self.url = "https://gdneoenergiapernambuco.neoenergia.com/detalhe"
            self.context = RecoveryContext()
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def go_back(self, **kwargs) -> None:
            raise cdp_portal_service.PlaywrightError("history unavailable")

    class RecoveryContext:
        def __init__(self) -> None:
            self.pages = []
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    detail_page = SameTabDetailPage()
    detail_page.context.pages.append(detail_page)

    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: False,
    )

    monkeypatch.setattr(
        cdp_portal_service,
        "_goto_listing_url",
        lambda page, listing_url: (_ for _ in ()).throw(
            cdp_portal_service.PlaywrightError("same tab stuck")
        ),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_reload_page",
        lambda page: (_ for _ in ()).throw(
            cdp_portal_service.PlaywrightError("reload unavailable")
        ),
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        detail_page,
        detail_page,
        "https://gdneoenergiapernambuco.neoenergia.com/minhas",
    )

    assert recovered_page is detail_page
    assert recovery["success"] is False
    assert recovery["status"] == "failed_return_to_listing"
    assert "reabra o Edge" in recovery["error"]
    assert detail_page.context.new_page_called is False


def test_return_to_listing_after_detail_is_passive_when_listing_not_visible(
    monkeypatch,
) -> None:
    class SameTabDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/pages/detalhe/index.jsf"

        def __init__(self) -> None:
            self.context = RecoveryContext(self)
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def go_back(self, **kwargs) -> None:
            navigation_calls.append("go_back")
            raise cdp_portal_service.PlaywrightError("history would affect portal")

    class RecoveryContext:
        def __init__(self, page: SameTabDetailPage) -> None:
            self.pages = [page]

    navigation_calls: list[str] = []
    page = SameTabDetailPage()

    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_goto_listing_url",
        lambda page, url: navigation_calls.append("goto"),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_reload_page",
        lambda page: navigation_calls.append("reload"),
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        page,
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is page
    assert recovery["success"] is False
    assert recovery["status"] == "failed_return_to_listing"
    assert "PowerShell" in recovery["error"]
    assert navigation_calls == []


def test_recover_listing_does_not_navigate_when_current_page_is_http_access_denied(
    monkeypatch,
) -> None:
    class HttpBlockedPage:
        url = "http://gdneoenergiapernambuco.neoenergia.com/index.jsf"

        def __init__(self) -> None:
            self.back_called = False

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def go_back(self, **kwargs) -> None:
            self.back_called = True

    page = HttpBlockedPage()
    navigation_calls: list[str] = []

    monkeypatch.setattr(cdp_portal_service, "_has_minhas_solicitacoes_table", lambda page: False)
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_goto_listing_url",
        lambda page, url: navigation_calls.append(f"goto:{url}"),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_reload_page",
        lambda page: navigation_calls.append("reload"),
    )

    recovery = cdp_portal_service._recover_minhas_solicitacoes(
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovery["success"] is False
    assert recovery["status"] == "failed_return_to_listing"
    assert "PowerShell" in recovery["error"]
    assert navigation_calls == []
    assert page.back_called is False


def test_recover_listing_does_not_goto_http_listing_url(monkeypatch) -> None:
    class HttpsDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/detalhe"

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def go_back(self, **kwargs) -> None:
            raise AssertionError("history must not run when listing URL is unsafe")

    navigation_calls: list[str] = []

    monkeypatch.setattr(cdp_portal_service, "_has_minhas_solicitacoes_table", lambda page: False)
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_goto_listing_url",
        lambda page, url: navigation_calls.append(f"goto:{url}"),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_reload_page",
        lambda page: navigation_calls.append("reload"),
    )

    recovery = cdp_portal_service._recover_minhas_solicitacoes(
        HttpsDetailPage(),
        "http://gdneoenergiapernambuco.neoenergia.com/index.jsf",
    )

    assert recovery["success"] is False
    assert recovery["status"] == "failed_return_to_listing"
    assert "PowerShell" in recovery["error"]
    assert navigation_calls == []


def test_return_to_listing_uses_existing_context_listing_when_original_is_closed(
    monkeypatch,
) -> None:
    class ContextPage:
        def __init__(self, name: str, context: "RecoveryContext") -> None:
            self.name = name
            self.url = f"https://gdneoenergiapernambuco.neoenergia.com/{name}"
            self.context = context
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def go_back(self, **kwargs) -> None:
            raise cdp_portal_service.PlaywrightError("history unavailable")

    class RecoveryContext:
        def __init__(self) -> None:
            self.pages = []

        def new_page(self):
            raise cdp_portal_service.PlaywrightError("new page unavailable")

    context = RecoveryContext()
    listing_page = ContextPage("closed-listing", context)
    listing_page.closed = True
    detail_page = ContextPage("detail", context)
    sibling_listing = ContextPage("minhas", context)
    context.pages.extend([listing_page, detail_page, sibling_listing])

    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: getattr(page, "name", "") == "minhas",
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_goto_listing_url",
        lambda page, url: (_ for _ in ()).throw(
            cdp_portal_service.PlaywrightError("navigation unavailable")
        ),
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_reload_page",
        lambda page: (_ for _ in ()).throw(
            cdp_portal_service.PlaywrightError("reload unavailable")
        ),
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        detail_page,
        listing_page,
        "https://gdneoenergiapernambuco.neoenergia.com/minhas",
    )

    assert recovered_page is sibling_listing
    assert recovery["success"] is True
    assert recovery["status"] == "recovered_listing_by_existing_context_page"
    assert recovery["method"] == "recovered_listing_by_existing_context_page"


def test_op5_plan_recovers_listing_after_partial_selection_and_detail_return(
    monkeypatch,
) -> None:
    navigation_calls: list[str] = []

    class ContextPage:
        def __init__(self, name: str, context: "RecoveryContext") -> None:
            self.name = name
            self.url = f"https://gdneoenergiapernambuco.neoenergia.com/{name}"
            self.context = context
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, **kwargs) -> None:
            navigation_calls.append("go_back")

    class RecoveryContext:
        def __init__(self) -> None:
            self.pages = []
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    context = RecoveryContext()
    stale_listing_page = ContextPage("stale-listing", context)
    detail_page = ContextPage("detail", context)
    safe_listing_page = ContextPage("minhas-solicitacoes", context)
    context.pages.extend([stale_listing_page, detail_page, safe_listing_page])

    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: getattr(page, "name", "") == "minhas-solicitacoes",
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        detail_page,
        stale_listing_page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is safe_listing_page
    assert recovery["success"] is True
    assert recovery["method"] == "recovered_listing_by_existing_context_page"
    assert navigation_calls == []
    assert context.new_page_called is False


def test_op5_plan_preserves_listing_context_before_opening_detail(
    monkeypatch,
) -> None:
    navigation_calls: list[str] = []
    state = {"returned_to_listing": False}

    class SameTabDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/pages/detalhe/index.jsf"

        def __init__(self) -> None:
            self.context = RecoveryContext(self)

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, **_kwargs) -> None:
            navigation_calls.append("go_back")

    class RecoveryContext:
        def __init__(self, page: SameTabDetailPage) -> None:
            self.pages = [page]
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    page = SameTabDetailPage()

    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: state["returned_to_listing"],
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_listing_has_rows",
        lambda page: state["returned_to_listing"],
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_minhas_solicitacoes_navigation",
        lambda page: navigation_calls.append("menu") or False,
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_click_detail_return_to_listing",
        lambda page: state.__setitem__("returned_to_listing", True) or True,
        raising=False,
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        page,
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is page
    assert recovery["success"] is True
    assert recovery["status"] == "recovered_listing_by_detail_return_control"
    assert recovery["method"] == "recovered_listing_by_detail_return_control"
    assert navigation_calls == []
    assert page.context.new_page_called is False


def test_op5_plan_recovers_listing_with_detail_input_return_control(
    monkeypatch,
) -> None:
    state = {"returned_to_listing": False}
    navigation_calls: list[str] = []

    class FakeLocator:
        def __init__(self, visible: bool = False) -> None:
            self.visible = visible

        def count(self) -> int:
            return 1 if self.visible else 0

        def nth(self, index: int):
            return self

        def is_visible(self, timeout: int = 0) -> bool:
            return self.visible

        def click(self, timeout: int = 0) -> None:
            state["returned_to_listing"] = True

    class SameTabDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/pages/detalhe/index.jsf"

        def __init__(self) -> None:
            self.context = RecoveryContext(self)

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def wait_for_load_state(self, *_args, **_kwargs) -> None:
            return None

        def get_by_role(self, *_args, **_kwargs):
            return FakeLocator(False)

        def locator(self, selector: str):
            return FakeLocator(selector == "input[type='submit'][value*='Voltar']")

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, **_kwargs) -> None:
            navigation_calls.append("go_back")

    class RecoveryContext:
        def __init__(self, page: SameTabDetailPage) -> None:
            self.pages = [page]
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    page = SameTabDetailPage()
    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: state["returned_to_listing"],
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_listing_has_rows",
        lambda page: state["returned_to_listing"],
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        page,
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is page
    assert recovery["success"] is True
    assert recovery["method"] == "recovered_listing_by_detail_return_control"
    assert navigation_calls == []
    assert page.context.new_page_called is False


def test_op5_plan_recovers_listing_by_authenticated_home_icon_after_detail(
    monkeypatch,
) -> None:
    state = {"home_clicked": False, "listing_clicked": False}
    navigation_calls: list[str] = []

    class FakeLocator:
        def __init__(self, name: str, visible: bool = False, href: str | None = None) -> None:
            self.name = name
            self.visible = visible
            self.href = href

        def count(self) -> int:
            return 1 if self.visible else 0

        def nth(self, index: int):
            return self

        def is_visible(self, timeout: int = 0) -> bool:
            return self.visible

        def get_attribute(self, name: str, timeout: int = 0):
            return self.href if name == "href" else None

        def click(self, timeout: int = 0) -> None:
            if self.name == "home":
                state["home_clicked"] = True
            if self.name == "listing":
                state["listing_clicked"] = True

    class SameTabDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/pages/detalhe/index.jsf"

        def __init__(self) -> None:
            self.context = RecoveryContext(self)

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def wait_for_load_state(self, *_args, **_kwargs) -> None:
            return None

        def get_by_role(self, role: str, name=None):
            pattern_text = getattr(name, "pattern", str(name or ""))
            if role in {"button", "link"} and "home" in pattern_text.lower():
                return FakeLocator("home", True)
            if role in {"button", "link"} and "solicita" in pattern_text.lower():
                return FakeLocator("listing", state["home_clicked"])
            return FakeLocator("none", False)

        def locator(self, selector: str):
            if "Voltar" in selector or "Retornar" in selector:
                return FakeLocator("none", False)
            if "fa-home" in selector:
                return FakeLocator("home", True)
            if "Minhas" in selector or "Solicitacoes" in selector:
                return FakeLocator("listing", state["home_clicked"])
            return FakeLocator("none", False)

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, **_kwargs) -> None:
            navigation_calls.append("go_back")

    class RecoveryContext:
        def __init__(self, page: SameTabDetailPage) -> None:
            self.pages = [page]
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    page = SameTabDetailPage()
    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: state["listing_clicked"],
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_listing_has_rows",
        lambda page: state["listing_clicked"],
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        page,
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is page
    assert recovery["success"] is True
    assert recovery["method"] == "recovered_listing_by_authenticated_home_icon"
    assert state == {"home_clicked": True, "listing_clicked": True}
    assert navigation_calls == []
    assert page.context.new_page_called is False


def test_op5_plan_recovers_listing_by_authenticated_home_icon_with_jsf_root_href(
    monkeypatch,
) -> None:
    state = {"home_clicked": False, "listing_clicked": False}
    navigation_calls: list[str] = []

    class FakeLocator:
        def __init__(self, name: str, visible: bool = False, href: str | None = None) -> None:
            self.name = name
            self.visible = visible
            self.href = href
            self.page = page

        def count(self) -> int:
            return 1 if self.visible else 0

        def nth(self, index: int):
            return self

        def is_visible(self, timeout: int = 0) -> bool:
            return self.visible

        def get_attribute(self, name: str, timeout: int = 0):
            return self.href if name == "href" else None

        def click(self, timeout: int = 0) -> None:
            if self.name == "home":
                state["home_clicked"] = True
            if self.name == "listing":
                state["listing_clicked"] = True

    class SameTabDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/index.jsf"

        def __init__(self) -> None:
            self.context = RecoveryContext(self)

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def wait_for_load_state(self, *_args, **_kwargs) -> None:
            return None

        def get_by_role(self, role: str, name=None):
            pattern_text = getattr(name, "pattern", str(name or "")).lower()
            if role in {"button", "link"} and "home" in pattern_text:
                return FakeLocator("home", True, "/index.jsf")
            if role in {"button", "link"} and "solicita" in pattern_text:
                return FakeLocator("listing", state["home_clicked"])
            return FakeLocator("none", False)

        def locator(self, selector: str):
            if "Voltar" in selector or "Retornar" in selector:
                return FakeLocator("none", False)
            if "fa-home" in selector:
                return FakeLocator("home", True, "/index.jsf")
            if "Minhas" in selector or "Solicitacoes" in selector:
                return FakeLocator("listing", state["home_clicked"])
            return FakeLocator("none", False)

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, **_kwargs) -> None:
            navigation_calls.append("go_back")

    class RecoveryContext:
        def __init__(self, page: SameTabDetailPage) -> None:
            self.pages = [page]
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    page = SameTabDetailPage()
    monkeypatch.setattr(
        cdp_portal_service,
        "_has_minhas_solicitacoes_table",
        lambda page: state["listing_clicked"],
    )
    monkeypatch.setattr(
        cdp_portal_service,
        "_listing_has_rows",
        lambda page: state["listing_clicked"],
    )

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        page,
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is page
    assert recovery["success"] is True
    assert recovery["method"] == "recovered_listing_by_authenticated_home_icon"
    assert state == {"home_clicked": True, "listing_clicked": True}
    assert navigation_calls == []
    assert page.context.new_page_called is False


def test_op5_plan_rejects_home_icon_when_it_points_to_http_or_access_denied(
    monkeypatch,
) -> None:
    state = {"home_clicked": False}
    navigation_calls: list[str] = []

    class FakeLocator:
        def count(self) -> int:
            return 1

        def nth(self, index: int):
            return self

        def is_visible(self, timeout: int = 0) -> bool:
            return True

        def get_attribute(self, name: str, timeout: int = 0):
            if name == "href":
                return "http://gdneoenergiapernambuco.neoenergia.com/"
            return None

        def click(self, timeout: int = 0) -> None:
            state["home_clicked"] = True

    class EmptyLocator:
        def count(self) -> int:
            return 0

    class SameTabDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/pages/detalhe/index.jsf"

        def __init__(self) -> None:
            self.context = RecoveryContext(self)

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def get_by_role(self, role: str, name=None):
            pattern_text = getattr(name, "pattern", str(name or "")).lower()
            if "home" in pattern_text:
                return FakeLocator()
            return EmptyLocator()

        def locator(self, selector: str):
            if "fa-home" in selector:
                return FakeLocator()
            return EmptyLocator()

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, **_kwargs) -> None:
            navigation_calls.append("go_back")

    class RecoveryContext:
        def __init__(self, page: SameTabDetailPage) -> None:
            self.pages = [page]
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    page = SameTabDetailPage()
    monkeypatch.setattr(cdp_portal_service, "_has_minhas_solicitacoes_table", lambda page: False)
    monkeypatch.setattr(cdp_portal_service, "_listing_has_rows", lambda page: False)

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        page,
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is page
    assert recovery["success"] is False
    assert recovery["status"] == "failed_return_to_listing"
    assert state["home_clicked"] is False
    assert navigation_calls == []
    assert page.context.new_page_called is False


def test_op5_plan_rejects_home_icon_root_when_listing_is_not_reconfirmed(
    monkeypatch,
) -> None:
    state = {"home_clicked": False}
    navigation_calls: list[str] = []

    class FakeLocator:
        def __init__(self, href: str) -> None:
            self.href = href

        def count(self) -> int:
            return 1

        def nth(self, index: int):
            return self

        def is_visible(self, timeout: int = 0) -> bool:
            return True

        def get_attribute(self, name: str, timeout: int = 0):
            if name == "href":
                return self.href
            return None

        def click(self, timeout: int = 0) -> None:
            state["home_clicked"] = True

    class EmptyLocator:
        def count(self) -> int:
            return 0

    class SameTabDetailPage:
        url = "https://gdneoenergiapernambuco.neoenergia.com/pages/detalhe/index.jsf"

        def __init__(self) -> None:
            self.context = RecoveryContext(self)

        def is_closed(self) -> bool:
            return False

        def wait_for_timeout(self, timeout: int) -> None:
            return None

        def get_by_role(self, role: str, name=None):
            pattern_text = getattr(name, "pattern", str(name or "")).lower()
            if "home" in pattern_text or "cio" in pattern_text:
                return FakeLocator("/")
            return EmptyLocator()

        def locator(self, selector: str):
            if "fa-home" in selector:
                return FakeLocator("/index.jsf")
            return EmptyLocator()

        def goto(self, *_args, **_kwargs) -> None:
            navigation_calls.append("goto")

        def reload(self, *_args, **_kwargs) -> None:
            navigation_calls.append("reload")

        def go_back(self, **_kwargs) -> None:
            navigation_calls.append("go_back")

    class RecoveryContext:
        def __init__(self, page: SameTabDetailPage) -> None:
            self.pages = [page]
            self.new_page_called = False

        def new_page(self):
            self.new_page_called = True
            raise AssertionError("CDP recovery must not open a new portal page")

    page = SameTabDetailPage()
    monkeypatch.setattr(cdp_portal_service, "_has_minhas_solicitacoes_table", lambda page: False)
    monkeypatch.setattr(cdp_portal_service, "_listing_has_rows", lambda page: False)

    recovered_page, recovery = cdp_portal_service._return_to_listing_after_detail(
        page,
        page,
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
    )

    assert recovered_page is page
    assert recovery["success"] is False
    assert recovery["status"] == "failed_return_to_listing"
    assert state["home_clicked"] is True
    assert navigation_calls == []
    assert page.context.new_page_called is False
