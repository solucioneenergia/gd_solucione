from __future__ import annotations

from urllib.parse import urlparse
from typing import TYPE_CHECKING

from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.portal.browser import (
    PersistentBrowserPortalGDAutomation,
)
from automacao_gd.infrastructure.portal.cdp_service import (
    collect_completed_requests_across_pages,
    connect_to_existing_edge,
    download_connection_budget,
    find_portal_page_from_cdp,
)


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

if TYPE_CHECKING:
    from playwright.sync_api import Playwright


class CDPPortalGDAutomation(PersistentBrowserPortalGDAutomation):
    def connect(self) -> None:
        self._validate_endpoint()
        playwright = _start_sync_playwright()
        self.playwright = playwright
        try:
            self.browser = connect_to_existing_edge(
                playwright,
                self.settings.CDP_ENDPOINT,
            )
            self.page = self.find_portal_tab()
            if self.page is None:
                self.page = self._open_https_portal_tab()
            self.context = self.page.context
        except Exception:
            playwright.stop()
            self.playwright = None
            self.browser = None
            self.context = None
            self.page = None
            raise

    def start_browser(self) -> None:
        self.connect()

    def find_portal_tab(self):
        if self.browser is None:
            raise RuntimeError("Conexão CDP ainda não foi iniciada.")
        return find_portal_page_from_cdp(
            self.browser,
            self.settings.PORTAL_GD_URL,
        )

    def _open_https_portal_tab(self):
        if self.browser is None:
            raise RuntimeError("Conexão CDP ainda não foi iniciada.")
        if not self.browser.contexts:
            raise RuntimeError("Nenhum contexto do Edge foi encontrado via CDP.")
        context = self.browser.contexts[0]
        page = context.new_page()
        logger.warning(
            "Nenhuma aba HTTPS válida do Portal GD foi encontrada via CDP; "
            "abrindo nova aba HTTPS no Edge conectado."
        )
        page.goto(
            self.settings.PORTAL_GD_URL,
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        return page

    def open_portal(self) -> None:
        if self.page is None:
            raise RuntimeError("Aba do Portal GD não foi encontrada via CDP.")
        logger.info(f"Reutilizando aba já aberta do Portal GD: {self.page.url}")

    def save_auth_state(self) -> None:
        logger.info(
            "Modo CDP ativo; a autenticação permanece no Edge aberto manualmente."
        )

    def collect_completed_requests_across_pages(self):
        if self.page is None:
            raise RuntimeError("Aba do Portal GD não foi encontrada via CDP.")
        return collect_completed_requests_across_pages(self.page, self.settings)

    def download_connection_budget(self, protocol: str):
        if self.page is None:
            raise RuntimeError("Aba do Portal GD não foi encontrada via CDP.")
        return download_connection_budget(
            self.page,
            protocol,
            downloads_root=self.settings.downloads_dir_path,
        )

    def close(self) -> None:
        logger.info(
            "Encerrando conexão CDP sem fechar o Edge aberto manualmente."
        )
        try:
            if self.playwright:
                self.playwright.stop()
        finally:
            self.playwright = None
            self.browser = None
            self.context = None
            self.page = None

    def _validate_endpoint(self) -> None:
        host = (urlparse(self.settings.CDP_ENDPOINT).hostname or "").lower()
        if host not in _LOOPBACK_HOSTS and not self.settings.ALLOW_REMOTE_CDP:
            raise ValueError(
                "CDP remoto bloqueado. Use endpoint local ou defina "
                "ALLOW_REMOTE_CDP=true de forma consciente."
            )


def _start_sync_playwright() -> Playwright:
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()
