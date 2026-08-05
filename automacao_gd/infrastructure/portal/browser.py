from pathlib import Path
from typing import TYPE_CHECKING, Any

from tenacity import retry, stop_after_attempt, wait_fixed

from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.persistence.atomic import (
    _set_private_permissions,
    atomic_write_json,
)
from automacao_gd.domain.models import PortalSolicitation

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright


class PersistentBrowserPortalGDAutomation:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def start_browser(self) -> None:
        from playwright.sync_api import sync_playwright

        downloads_dir = self.settings.downloads_dir_path
        downloads_dir.mkdir(parents=True, exist_ok=True)
        profile_dir = self.settings.browser_profile_dir_path
        profile_dir.mkdir(parents=True, exist_ok=True)
        browser_channel = (self.settings.BROWSER_CHANNEL or "chromium").lower()

        logger.info(
            f"Usando contexto persistente: "
            f"{str(self.settings.USE_PERSISTENT_CONTEXT).lower()}"
        )
        logger.info(
            f"Perfil do navegador: {self.settings.BROWSER_PROFILE_DIR.as_posix()}"
        )
        logger.info(f"Navegador: {browser_channel}")
        logger.info(
            f"Iniciando navegador: {browser_channel}. "
            f"headless={self.settings.HEADLESS}; downloads={downloads_dir}"
        )
        playwright = sync_playwright().start()
        self.playwright = playwright

        try:
            if self.settings.USE_PERSISTENT_CONTEXT:
                context_options: dict[str, Any] = {
                    "headless": self.settings.HEADLESS,
                    "accept_downloads": True,
                    "downloads_path": str(downloads_dir),
                    "args": ["--start-maximized"],
                    "viewport": {"width": 1366, "height": 768},
                }
                if browser_channel in {"msedge", "chrome"}:
                    context_options["channel"] = browser_channel

                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    **context_options,
                )
                self.context = context
                self.browser = None
                self.page = (
                    context.pages[0]
                    if context.pages
                    else context.new_page()
                )
                return

            launch_options: dict[str, Any] = {
                "headless": self.settings.HEADLESS,
                "downloads_path": str(downloads_dir),
                "args": ["--start-maximized"],
            }
            if browser_channel in {"msedge", "chrome"}:
                launch_options["channel"] = browser_channel

            browser = playwright.chromium.launch(**launch_options)
            self.browser = browser
        except Exception as exc:
            if browser_channel == "msedge":
                message = (
                    "Microsoft Edge não foi encontrado. Verifique se o Edge está "
                    "instalado ou altere BROWSER_CHANNEL no .env."
                )
                logger.error(message)
                print(message)
            else:
                logger.error(
                    f"Falha ao iniciar navegador '{browser_channel}': {exc}"
                )
            playwright.stop()
            self.playwright = None
            raise

        storage_state = (
            str(self.settings.auth_state_path)
            if self.settings.auth_state_path.exists()
            else None
        )
        context = browser.new_context(
            accept_downloads=True,
            storage_state=storage_state,
            viewport={"width": 1366, "height": 768},
        )
        self.context = context
        self.page = context.new_page()

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def open_portal(self) -> None:
        if not self.page:
            raise RuntimeError("Navegador ainda não foi iniciado.")

        logger.info(f"Acessando Portal GD: {self.settings.PORTAL_GD_URL}")
        self.page.goto(self.settings.PORTAL_GD_URL, wait_until="domcontentloaded")

    def wait_manual_login(self) -> None:
        print(
            "Faça o login manualmente no portal. "
            "Depois pressione ENTER aqui para continuar."
        )
        input()

    def save_auth_state(self) -> None:
        if not self.context:
            raise RuntimeError("Contexto do navegador ainda não foi iniciado.")

        if self.settings.USE_PERSISTENT_CONTEXT:
            logger.info(
                "Contexto persistente ativo; cookies e dados locais ficam no "
                "perfil do navegador. O código não salva usuário nem senha."
            )
            return

        self.settings.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.context.storage_state(path=str(self.settings.auth_state_path))
        _set_private_permissions(self.settings.auth_state_path)
        logger.warning(
            f"Estado de autenticação salvo em {self.settings.auth_state_path}. "
            "Este arquivo pode conter cookies e tokens de sessão e deve permanecer privado."
        )

    def read_current_page_table(self) -> list[PortalSolicitation]:
        if not self.page:
            raise RuntimeError("Página do portal ainda não foi aberta.")

        logger.info("Lendo tabela 'Minhas Solicitações' diretamente do HTML.")
        records = self.page.evaluate(
            """
            () => {
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();

              const cellText = (cell) => (cell?.innerText || "")
                .replace(/\\s+/g, " ")
                .trim();

              const splitProtocolAndClient = (value) => {
                const text = (value || "").replace(/\\s+/g, " ").trim();
                const match = text.match(/\\b\\d{6,}\\b/);
                if (!match) {
                  return { protocol: "", client_name: text || null };
                }
                const protocol = match[0];
                const client = (text.slice(0, match.index) + " " +
                  text.slice(match.index + protocol.length))
                  .replace(/\\s+/g, " ")
                  .trim();
                return { protocol, client_name: client || null };
              };

              const mapHeader = (header) => {
                const text = normalize(header);
                if (text.includes("PROTOCOLO")) return "protocol_client";
                if (text.includes("STATUS")) return "status";
                if (text.includes("CODIGO") && text.includes("UNIDADE")) {
                  return "consumer_unit_code";
                }
                if (text.includes("ENDERECO")) return "address";
                if (text.includes("DATA") && text.includes("INGRESSO")) {
                  return "entry_date";
                }
                return null;
              };

              const tables = Array.from(document.querySelectorAll("table"));
              let selected = null;

              for (const table of tables) {
                const rows = Array.from(table.querySelectorAll("tr"));
                for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
                  const cells = Array.from(rows[rowIndex].querySelectorAll("th,td"));
                  const headers = cells.map((cell) => mapHeader(cellText(cell)));
                  const found = new Set(headers.filter(Boolean));
                  if (
                    found.has("protocol_client") &&
                    found.has("status") &&
                    found.has("consumer_unit_code") &&
                    found.has("address") &&
                    found.has("entry_date")
                  ) {
                    selected = { table, rows, rowIndex, headers };
                    break;
                  }
                }
                if (selected) break;
              }

              if (!selected) {
                return [];
              }

              const indexes = {};
              selected.headers.forEach((key, index) => {
                if (key && indexes[key] === undefined) indexes[key] = index;
              });

              const output = [];
              const bodyRows = selected.rows.slice(selected.rowIndex + 1);
              for (const row of bodyRows) {
                const cells = Array.from(row.querySelectorAll("td"));
                if (!cells.length) continue;

                const protocolCell = cellText(cells[indexes.protocol_client]);
                const parsed = splitProtocolAndClient(protocolCell);
                if (!parsed.protocol) continue;

                output.push({
                  protocol: parsed.protocol,
                  client_name: parsed.client_name,
                  status: cellText(cells[indexes.status]) || null,
                  consumer_unit_code: cellText(cells[indexes.consumer_unit_code]) || null,
                  address: cellText(cells[indexes.address]) || null,
                  entry_date: cellText(cells[indexes.entry_date]) || null,
                });
              }

              return output;
            }
            """
        )

        solicitations = [PortalSolicitation(**record) for record in records]
        logger.info(f"{len(solicitations)} solicitações encontradas na página atual.")
        if not solicitations:
            logger.warning(
                "Nenhum registro foi capturado. Verifique se a página atual exibe "
                "'Minhas Solicitações' e se os cabeçalhos esperados estão visíveis."
            )
        return solicitations

    def save_table_snapshot(self, records: list[PortalSolicitation]) -> Path:
        output_path = self.settings.logs_dir_path / "registros_pagina_atual.json"
        payload = [record.model_dump(mode="json") for record in records]
        atomic_write_json(output_path, payload, private=True)
        logger.info(f"Snapshot da tabela salvo em {output_path}.")
        return output_path

    def close(self) -> None:
        logger.info("Fechando recursos do navegador.")
        try:
            if self.context:
                self.context.close()
        finally:
            self.context = None
            try:
                if self.browser:
                    self.browser.close()
            finally:
                self.browser = None
                if self.playwright:
                    self.playwright.stop()
                self.playwright = None
                self.page = None
    # O fluxo de download/paginação fica no adaptador CDP, isolado desta classe
    # de inspeção via navegador iniciado pela aplicação.


PortalGDAutomation = PersistentBrowserPortalGDAutomation
