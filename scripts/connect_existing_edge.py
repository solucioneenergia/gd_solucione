import json
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError

from automacao_gd.application.operational_guard import (
    CONNECT_EXISTING_EDGE_OPERATION,
    DirectRouteAuthorization,
    guard_direct_route,
)
from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import logger, setup_logger
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json
from automacao_gd.infrastructure.portal.factory import create_portal_automation
from automacao_gd.infrastructure.portal.cdp_service import is_insecure_portal_http_url


OUTPUT_FILE_NAME = "registros_cdp_edge.json"


def main() -> int:
    print("Status: BLOQUEADO")
    print("Diagnóstico CDP direto desativado nesta etapa.")
    return 2


def _legacy_connect_existing_edge(
    authorization: DirectRouteAuthorization | None = None,
) -> None:
    settings = get_settings()
    with guard_direct_route(
        settings,
        authorization,
        operation=CONNECT_EXISTING_EDGE_OPERATION,
    ):
        _connect_existing_edge_locked(settings)


def _connect_existing_edge_locked(settings) -> None:
    ensure_directories()
    setup_logger()

    logger.info(f"CDP_MODE={str(settings.CDP_MODE).lower()}")
    _print_manual_instructions(settings.CDP_ENDPOINT)
    input("Pressione ENTER depois de fazer login e abrir 'Minhas Solicitações'.")

    automation = create_portal_automation(settings)
    try:
        automation.start_browser()
        pages = _collect_pages(automation.browser)
        _print_open_tabs(pages)
        records = automation.read_current_page_table()

        output_path = settings.logs_dir_path / OUTPUT_FILE_NAME
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            output_path,
            [record.model_dump(mode="json") for record in records],
            private=True,
        )
        logger.info(f"Registros CDP salvos em {output_path}.")
        print(f"Registros capturados: {len(records)}")
        print(f"JSON salvo em: {output_path}")
    except PlaywrightError as exc:
        message = (
            f"Não foi possível conectar ao Edge em {settings.CDP_ENDPOINT}. "
            "Confirme se o Edge foi aberto com --remote-debugging-port=9222."
        )
        logger.error(f"{message} Detalhe: {exc}")
        print(message)
        raise
    except Exception as exc:
        logger.exception(f"Falha no modo CDP Edge: {exc}")
        print(f"Falha no modo CDP Edge: {exc}")
        raise
    finally:
        automation.close()


def _print_manual_instructions(cdp_endpoint: str) -> None:
    print()
    print("Modo experimental: conectar ao Microsoft Edge já aberto")
    print()
    print("1. Feche todas as janelas do Edge.")
    print("2. Abra o Edge manualmente com:")
    print(
        '   msedge.exe --remote-debugging-port=9222 '
        '--user-data-dir="<PROJETO>\\data\\edge_cdp_profile" '
        '--no-first-run --no-default-browser-check --new-window '
        '"https://gdneoenergiapernambuco.neoenergia.com/"'
    )
    print("3. Confirme que a aba aberta usa HTTPS, nunca HTTP.")
    print("4. Faça login manual.")
    print('5. Vá para a tela "Minhas Solicitações".')
    print("6. Depois volte ao terminal e pressione ENTER.")
    print()
    print(f"Endpoint CDP configurado: {cdp_endpoint}")
    print("O script não abrirá navegador novo e não tentará fazer login.")
    print()


def _collect_pages(browser) -> list:
    pages = []
    for context_index, context in enumerate(browser.contexts):
        for page_index, page in enumerate(context.pages):
            pages.append(page)
            logger.info(
                f"Aba encontrada via CDP: contexto={context_index}, "
                f"aba={page_index}, url={page.url}"
            )
    if not pages:
        logger.warning("Nenhuma aba aberta foi retornada pelo CDP.")
    return pages


def _print_open_tabs(pages: list) -> None:
    print("Abas abertas no Edge conectado:")
    if not pages:
        print("  Nenhuma aba encontrada.")
        return

    for index, page in enumerate(pages, start=1):
        title = ""
        try:
            title = page.title()
        except PlaywrightError:
            title = "<titulo indisponível>"
        print(f"  {index}. {title} | {page.url}")


def _find_portal_page(pages: list, portal_url: str):
    expected_host = urlparse(portal_url).netloc.lower()
    for page in pages:
        page_url = page.url or ""
        if is_insecure_portal_http_url(page_url):
            continue
        parsed = urlparse(page_url)
        if expected_host and parsed.netloc.lower() == expected_host:
            return page

    for page in pages:
        page_url = (page.url or "").lower()
        if is_insecure_portal_http_url(page_url):
            continue
        if "gdneoenergiapernambuco.neoenergia.com" in page_url:
            return page

    return None


if __name__ == "__main__":
    raise SystemExit(main())
