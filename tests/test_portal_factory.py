from __future__ import annotations

from types import SimpleNamespace

import pytest

from automacao_gd.application.use_cases import inspect_portal
from automacao_gd.application.use_cases import run_pipeline
from automacao_gd.infrastructure.config import Settings
from automacao_gd.infrastructure.portal import cdp_browser
from automacao_gd.infrastructure.portal.cdp_service import find_portal_page_from_cdp
from automacao_gd.infrastructure.portal.browser import (
    PersistentBrowserPortalGDAutomation,
)
from automacao_gd.infrastructure.portal.cdp_browser import CDPPortalGDAutomation
from automacao_gd.infrastructure.portal.factory import create_portal_automation


def _settings(**overrides) -> Settings:
    values = {
        "PLANILHA_PATH": "planilha.xlsx",
        "CLIENTES_ROOT": "clientes",
        "PORTAL_GD_URL": "https://example.com/",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_factory_returns_cdp_automation_when_cdp_mode_is_enabled() -> None:
    automation = create_portal_automation(_settings(CDP_MODE=True))

    assert isinstance(automation, CDPPortalGDAutomation)


def test_factory_returns_persistent_automation_when_cdp_mode_is_disabled() -> None:
    automation = create_portal_automation(_settings(CDP_MODE=False))

    assert isinstance(automation, PersistentBrowserPortalGDAutomation)
    assert not isinstance(automation, CDPPortalGDAutomation)


def test_inspect_portal_uses_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeAutomation:
        def start_browser(self) -> None:
            calls.append("start")

        def open_portal(self) -> None:
            calls.append("open")

        def wait_manual_login(self) -> None:
            calls.append("login")

        def save_auth_state(self) -> None:
            calls.append("auth")

        def read_current_page_table(self) -> list:
            calls.append("read")
            return []

        def save_table_snapshot(self, records: list) -> str:
            calls.append("snapshot")
            return "snapshot.json"

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(
        inspect_portal,
        "create_portal_automation",
        lambda settings: FakeAutomation(),
    )

    result = inspect_portal.InspectPortalUseCase(_settings()).execute()

    assert result["cancelled"] is False
    assert calls == ["start", "open", "login", "auth", "read", "snapshot", "close"]


def test_cdp_mode_connects_without_launching_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(url="https://example.com/portal")
    context = SimpleNamespace(pages=[page])
    page.context = context

    class Chromium:
        def connect_over_cdp(self, endpoint: str):
            assert endpoint == "http://127.0.0.1:9222"
            return SimpleNamespace(contexts=[context])

        def launch(self, **kwargs):
            raise AssertionError("launch não deve ser chamado em modo CDP")

        def launch_persistent_context(self, **kwargs):
            raise AssertionError(
                "launch_persistent_context não deve ser chamado em modo CDP"
            )

    playwright = SimpleNamespace(chromium=Chromium(), stop=lambda: None)
    monkeypatch.setattr(cdp_browser, "_start_sync_playwright", lambda: playwright)
    automation = CDPPortalGDAutomation(_settings(CDP_MODE=True))

    automation.start_browser()

    assert automation.page is page


def test_cdp_close_does_not_close_manual_browser() -> None:
    calls: list[str] = []
    automation = CDPPortalGDAutomation(_settings(CDP_MODE=True))
    automation.browser = SimpleNamespace(close=lambda: calls.append("browser.close"))
    automation.context = SimpleNamespace(close=lambda: calls.append("context.close"))
    automation.playwright = SimpleNamespace(stop=lambda: calls.append("playwright.stop"))

    automation.close()

    assert calls == ["playwright.stop"]


def test_cdp_mode_fails_closed_when_only_http_blocked_tab_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    navigations: list[str] = []
    created_pages: list[str] = []

    class FakePage:
        def __init__(self, url: str) -> None:
            self.url = url
            self.context = None

        def goto(self, url: str, **_kwargs) -> None:
            navigations.append(url)
            self.url = url

    blocked = FakePage("http://gdneoenergiapernambuco.neoenergia.com/")
    created = FakePage("about:blank")

    class FakeContext:
        def __init__(self) -> None:
            self.pages = [blocked]

        def new_page(self):
            created_pages.append("new_page")
            self.pages.append(created)
            created.context = self
            return created

    context = FakeContext()
    blocked.context = context

    class Chromium:
        def connect_over_cdp(self, endpoint: str):
            assert endpoint == "http://127.0.0.1:9222"
            return SimpleNamespace(contexts=[context])

    playwright = SimpleNamespace(chromium=Chromium(), stop=lambda: None)
    monkeypatch.setattr(cdp_browser, "_start_sync_playwright", lambda: playwright)
    settings = _settings(
        CDP_MODE=True,
        PORTAL_GD_URL="https://gdneoenergiapernambuco.neoenergia.com/",
    )
    automation = CDPPortalGDAutomation(settings)

    with pytest.raises(RuntimeError, match="Abra o Edge.*PowerShell.*login manual"):
        automation.start_browser()

    assert created_pages == []
    assert navigations == []


def test_find_portal_page_selects_https_portal_tab_without_dom_probe() -> None:
    probe_calls: list[str] = []

    class FakeLocator:
        def inner_text(self, timeout: int = 0) -> str:
            probe_calls.append("body")
            return "Minhas Solicitacoes"

    class FakePage:
        def __init__(self, url: str) -> None:
            self.url = url

        def title(self) -> str:
            probe_calls.append("title")
            return "Sistema de Solicitacao"

        def locator(self, _selector: str) -> FakeLocator:
            return FakeLocator()

    portal_tab = FakePage(
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf"
    )
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[portal_tab])])

    page = find_portal_page_from_cdp(
        browser,
        "https://gdneoenergiapernambuco.neoenergia.com/",
    )

    assert page is portal_tab
    assert probe_calls == []


def test_find_portal_page_ignores_access_denied_tab() -> None:
    class FakeLocator:
        def __init__(self, text: str) -> None:
            self.text = text

        def inner_text(self, timeout: int = 0) -> str:
            return self.text

    class FakePage:
        def __init__(self, url: str, title: str, body: str) -> None:
            self.url = url
            self._title = title
            self._body = body

        def title(self) -> str:
            return self._title

        def locator(self, _selector: str) -> FakeLocator:
            return FakeLocator(self._body)

    blocked = FakePage(
        "https://gdneoenergiapernambuco.neoenergia.com/index.jsf",
        "Access Denied",
        "You don't have permission to access this server.",
    )
    valid = FakePage(
        "https://gdneoenergiapernambuco.neoenergia.com/minhas-solicitacoes",
        "Portal GD",
        "Minhas Solicitações",
    )
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[blocked, valid])])

    page = find_portal_page_from_cdp(
        browser,
        "https://gdneoenergiapernambuco.neoenergia.com/",
    )

    assert page is valid


def test_find_portal_page_rejects_http_index_access_denied_tab_without_body() -> None:
    class BrokenLocator:
        def inner_text(self, timeout: int = 0) -> str:
            raise RuntimeError("body indisponivel")

    class FakePage:
        def __init__(self, url: str, title: str = "") -> None:
            self.url = url
            self._title = title

        def title(self) -> str:
            return self._title

        def locator(self, _selector: str) -> BrokenLocator:
            return BrokenLocator()

    blocked = FakePage("http://gdneoenergiapernambuco.neoenergia.com/index.jsf")
    valid = FakePage(
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
        "Portal GD",
    )
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[blocked, valid])])

    page = find_portal_page_from_cdp(
        browser,
        "https://gdneoenergiapernambuco.neoenergia.com/",
    )

    assert page is valid


def test_find_portal_page_rejects_any_http_portal_tab_without_body() -> None:
    class BrokenLocator:
        def inner_text(self, timeout: int = 0) -> str:
            raise RuntimeError("body indisponivel")

    class FakePage:
        def __init__(self, url: str, title: str = "") -> None:
            self.url = url
            self._title = title

        def title(self) -> str:
            return self._title

        def locator(self, _selector: str) -> BrokenLocator:
            return BrokenLocator()

    blocked = FakePage("http://gdneoenergiapernambuco.neoenergia.com/minhas-solicitacoes")
    valid = FakePage(
        "https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf",
        "Portal GD",
    )
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[blocked, valid])])

    page = find_portal_page_from_cdp(
        browser,
        "https://gdneoenergiapernambuco.neoenergia.com/",
    )

    assert page is valid


@pytest.mark.parametrize(
    ("module_name",),
    [
        ("connect_existing_edge",),
        ("process_first_solicitation_cdp",),
    ],
)
def test_legacy_cdp_page_finders_reject_http_portal_tabs(module_name: str) -> None:
    module = __import__(f"scripts.{module_name}", fromlist=["_find_portal_page"])
    blocked = SimpleNamespace(
        url="http://gdneoenergiapernambuco.neoenergia.com/minhas-solicitacoes"
    )
    valid = SimpleNamespace(
        url="https://gdneoenergiapernambuco.neoenergia.com/pages/acompanhamento/index.jsf"
    )

    page = module._find_portal_page(
        [blocked, valid],
        "https://gdneoenergiapernambuco.neoenergia.com/",
    )

    assert page is valid


def test_full_pipeline_use_case_propagates_cdp_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(CDP_MODE=True)
    received: list[Settings] = []
    monkeypatch.setattr(
        run_pipeline,
        "run_preflight",
        lambda *args, **kwargs: SimpleNamespace(ready=True, blocking_errors=[]),
    )
    monkeypatch.setattr(
        run_pipeline,
        "run_full_cdp_pipeline",
        lambda effective_settings: received.append(effective_settings) or {},
    )

    run_pipeline.RunFullPipelineUseCase(settings).execute()

    assert received == [settings]
