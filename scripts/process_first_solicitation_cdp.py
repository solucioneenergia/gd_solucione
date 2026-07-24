import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories, sanitize_filename
from automacao_gd.infrastructure.logging import logger, setup_logger
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json
from automacao_gd.infrastructure.pdf.service import extract_generation_data
from automacao_gd.infrastructure.portal.factory import create_portal_automation


OUTPUT_FILE_NAME = "processamento_primeira_solicitacao_cdp.json"
DOWNLOAD_TIMEOUT_MS = 30_000
DETAIL_TIMEOUT_MS = 20_000
BUDGET_BUTTON_TIMEOUT_MS = 10_000


def main() -> None:
    ensure_directories()
    setup_logger()
    settings = get_settings()
    result = _empty_result()
    automation = create_portal_automation(settings)

    try:
        logger.info("Iniciando processamento da primeira solicitação via CDP.")
        logger.info(f"Endpoint CDP: {settings.CDP_ENDPOINT}")
        automation.start_browser()
        pages = _collect_pages(automation.browser)
        _print_open_tabs(pages)
        portal_page = automation.page
        records = automation.read_current_page_table()
        if not records:
            raise RuntimeError(
                "A tela atual não parece exibir a tabela 'Minhas Solicitações'."
            )

        first_record = records[0]
        result.update(
            {
                "protocol_from_table": first_record.protocol,
                "client_from_table": first_record.client_name,
                "status_from_table": first_record.status,
                "consumer_unit_code": first_record.consumer_unit_code,
                "address": first_record.address,
                "entry_date": first_record.entry_date,
            }
        )
        logger.info(
            "Primeira solicitação capturada da tabela: "
            f"protocolo={first_record.protocol}, cliente={first_record.client_name}"
        )

        row_locator = _find_first_solicitation_row(portal_page)
        detail_page = _open_detail_from_row(portal_page, row_locator)
        detail_data = _extract_detail_data(
            detail_page,
            protocol_hint=first_record.protocol,
            client_hint=first_record.client_name,
        )
        result.update(detail_data)

        budget_target = _find_connection_budget_target(detail_page)
        if budget_target is None:
            result["has_connection_budget"] = False
            message = "Botão/link 'Orçamento de Conexão' não encontrado no detalhe."
            logger.warning(message)
            result["errors"].append(message)
            return

        result["has_connection_budget"] = True
        downloaded_pdf_path = _download_connection_budget(
            detail_page,
            budget_target,
            settings.downloads_dir_path,
            result["protocol_from_detail"] or result["protocol_from_table"],
            result["errors"],
        )
        if downloaded_pdf_path is None:
            return

        result["downloaded_pdf_path"] = str(downloaded_pdf_path)
        generation_data = extract_generation_data(downloaded_pdf_path)
        result["generation_data"] = generation_data.model_dump(mode="json")
        result["module_excel"] = generation_data.format_module_for_excel()
        result["inverter_excel"] = generation_data.format_inverter_for_excel()
        logger.info(f"Placa para Excel: {result['module_excel']}")
        logger.info(f"Inversor para Excel: {result['inverter_excel']}")
    except PlaywrightError as exc:
        message = (
            f"Falha de Playwright/CDP. Verifique se o Edge está aberto em "
            f"{settings.CDP_ENDPOINT}. Detalhe: {exc}"
        )
        logger.error(message)
        result["errors"].append(message)
        raise
    except Exception as exc:
        logger.exception(f"Falha ao processar primeira solicitação via CDP: {exc}")
        result["errors"].append(str(exc))
        raise
    finally:
        output_path = _save_result(settings.logs_dir_path, result)
        print(f"JSON salvo em: {output_path}")
        automation.close()


def _empty_result() -> dict:
    return {
        "protocol_from_table": None,
        "client_from_table": None,
        "status_from_table": None,
        "consumer_unit_code": None,
        "address": None,
        "entry_date": None,
        "protocol_from_detail": None,
        "client_from_detail": None,
        "is_completed": False,
        "completion_date": None,
        "has_connection_budget": False,
        "downloaded_pdf_path": None,
        "module_excel": None,
        "inverter_excel": None,
        "generation_data": None,
        "errors": [],
    }


def _collect_pages(browser) -> list:
    pages = []
    for context_index, context in enumerate(browser.contexts):
        for page_index, page in enumerate(context.pages):
            pages.append(page)
            logger.info(
                f"Aba encontrada via CDP: contexto={context_index}, "
                f"aba={page_index}, url={page.url}"
            )
    return pages


def _print_open_tabs(pages: list) -> None:
    print("Abas abertas no Edge conectado:")
    if not pages:
        print("  Nenhuma aba encontrada.")
        return

    for index, page in enumerate(pages, start=1):
        try:
            title = page.title()
        except PlaywrightError:
            title = "<titulo indisponível>"
        print(f"  {index}. {title} | {page.url}")


def _find_portal_page(pages: list, portal_url: str):
    expected_host = urlparse(portal_url).netloc.lower()
    for page in pages:
        parsed = urlparse(page.url or "")
        if expected_host and parsed.netloc.lower() == expected_host:
            return page

    for page in pages:
        if "gdneoenergiapernambuco.neoenergia.com" in (page.url or "").lower():
            return page
    return None


def _find_first_solicitation_row(page):
    table_info = page.evaluate(
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
          for (let tableIndex = 0; tableIndex < tables.length; tableIndex += 1) {
            const rows = Array.from(tables[tableIndex].querySelectorAll("tr"));
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
                for (let dataRowIndex = rowIndex + 1; dataRowIndex < rows.length; dataRowIndex += 1) {
                  const dataCells = Array.from(rows[dataRowIndex].querySelectorAll("td"));
                  if (!dataCells.length) continue;
                  const rowText = dataCells.map(cellText).join(" ");
                  if (/\\b\\d{6,}\\b/.test(rowText)) {
                    return { tableIndex, rowIndex: dataRowIndex };
                  }
                }
              }
            }
          }
          return null;
        }
        """
    )
    if not table_info:
        raise RuntimeError("Não foi possível localizar a primeira linha da tabela.")

    logger.info(
        "Linha da primeira solicitação localizada: "
        f"tableIndex={table_info['tableIndex']}, rowIndex={table_info['rowIndex']}"
    )
    return (
        page.locator("table")
        .nth(table_info["tableIndex"])
        .locator("tr")
        .nth(table_info["rowIndex"])
    )


def _open_detail_from_row(page, row_locator):
    before_pages = list(page.context.pages)
    logger.info("Clicando no comando de acompanhar/visualizar da primeira linha.")

    if not _click_row_action(row_locator):
        raise RuntimeError(
            "Não foi encontrado comando de acompanhar/visualizar na primeira linha."
        )

    page.wait_for_timeout(1_000)
    after_pages = list(page.context.pages)
    new_pages = [candidate for candidate in after_pages if candidate not in before_pages]
    detail_page = new_pages[-1] if new_pages else page

    try:
        detail_page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning("Timeout aguardando DOM da tela de detalhe.")

    try:
        detail_page.wait_for_load_state("networkidle", timeout=5_000)
    except PlaywrightTimeoutError:
        logger.debug("Network idle não ocorreu; seguindo com a página atual.")

    logger.info(f"Tela de detalhe carregada ou ativa: {detail_page.url}")
    return detail_page


def _click_row_action(row_locator) -> bool:
    action_pattern = re.compile(
        r"acompanhar|visualizar|detalh|consultar|ver", re.IGNORECASE
    )
    candidates = [
        row_locator.get_by_role("link", name=action_pattern),
        row_locator.get_by_role("button", name=action_pattern),
        row_locator.locator("a:has-text('Acompanhar')"),
        row_locator.locator("button:has-text('Acompanhar')"),
        row_locator.locator("a:has-text('Visualizar')"),
        row_locator.locator("button:has-text('Visualizar')"),
        row_locator.locator("a:has-text('Ver')"),
        row_locator.locator("button:has-text('Ver')"),
    ]

    for locator in candidates:
        if _click_first_visible(locator):
            return True

    fallback = row_locator.locator(
        "a, button, input[type='button'], input[type='submit'], [role='button']"
    )
    count = fallback.count()
    for index in range(count - 1, -1, -1):
        item = fallback.nth(index)
        try:
            if item.is_visible(timeout=1_000):
                item.click(timeout=10_000)
                logger.info(f"Clique realizado em fallback de ação, índice {index}.")
                return True
        except PlaywrightError as exc:
            logger.debug(f"Fallback de ação não clicável no índice {index}: {exc}")
    return False


def _click_first_visible(locator) -> bool:
    try:
        count = locator.count()
    except PlaywrightError:
        return False

    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible(timeout=1_000):
                item.click(timeout=10_000)
                logger.info(f"Clique realizado em candidato de ação, índice {index}.")
                return True
        except PlaywrightError as exc:
            logger.debug(f"Candidato de ação não clicável no índice {index}: {exc}")
    return False


def _extract_detail_data(page, protocol_hint: str | None, client_hint: str | None) -> dict:
    body_text = _safe_body_text(page)
    title_candidates = _title_candidates(page)
    title_text = "\n".join(title_candidates)
    combined_text = f"{title_text}\n{body_text}"

    protocol = _extract_protocol(combined_text) or protocol_hint
    client = _extract_client(title_text, body_text, protocol) or client_hint
    normalized_body = _normalize_search(body_text)
    is_completed = "SOLICITACAO CONCLUIDA" in normalized_body
    completion_date = _extract_completion_date(body_text)

    logger.info(
        "Detalhe extraído: "
        f"protocolo={protocol}, cliente={client}, concluida={is_completed}, "
        f"data_conclusao={completion_date}"
    )
    return {
        "protocol_from_detail": protocol,
        "client_from_detail": client,
        "is_completed": is_completed,
        "completion_date": completion_date,
    }


def _safe_body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=10_000)
    except PlaywrightError as exc:
        logger.warning(f"Não foi possível ler texto do body: {exc}")
        return ""


def _title_candidates(page) -> list[str]:
    try:
        values = page.evaluate(
            """
            () => {
              const selectors = [
                "h1", "h2", "h3", "h4", "legend", ".title", ".titulo",
                ".page-title", ".card-title", "[class*='title']", "[class*='titulo']"
              ].join(",");
              const nodes = Array.from(document.querySelectorAll(selectors));
              const texts = [document.title, ...nodes.map((node) => node.innerText || node.textContent || "")];
              return texts
                .map((text) => text.replace(/\\s+/g, " ").trim())
                .filter(Boolean);
            }
            """
        )
        return list(dict.fromkeys(values))
    except PlaywrightError as exc:
        logger.warning(f"Não foi possível ler títulos do detalhe: {exc}")
        return []


def _extract_protocol(text: str) -> str | None:
    match = re.search(r"\b\d{6,}\b", text)
    return match.group(0) if match else None


def _extract_client(title_text: str, body_text: str, protocol: str | None) -> str | None:
    texts = [title_text, body_text]
    patterns = [
        r"Cliente\s*:?\s*([^\n\r]+)",
        r"Titular\s+(?:da\s+UC\s*)?:?\s*([^\n\r]+)",
        r"Nome\s*:?\s*([^\n\r]+)",
    ]
    for text in texts:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                client = _clean_client_candidate(match.group(1), protocol)
                if client:
                    return client

    for line in title_text.splitlines():
        if protocol and protocol in line:
            client = _clean_client_candidate(line.replace(protocol, " "), protocol)
            if client:
                return client
    return None


def _clean_client_candidate(value: str, protocol: str | None) -> str | None:
    value = re.sub(r"\s+", " ", value).strip(" :-|")
    if protocol:
        value = value.replace(protocol, " ")
    value = re.sub(
        r"(?i)\b(protocolo|solicita[cç][aã]o|cliente|titular|uc|n[ºo°])\b",
        " ",
        value,
    )
    value = re.sub(r"\s+", " ", value).strip(" :-|")
    if len(value) < 3 or not re.search(r"[A-Za-zÀ-ÿ]{3}", value):
        return None
    return value


def _extract_completion_date(text: str) -> str | None:
    patterns = [
        r"Data\s+de\s+conclus[aã]o\s*:?\s*(\d{2}/\d{2}/\d{4})",
        r"Conclus[aã]o\s*:?\s*(\d{2}/\d{2}/\d{4})",
        r"Solicita[cç][aã]o\s+Conclu[ií]da\D{0,80}(\d{2}/\d{2}/\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _normalize_search(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.upper()
    return re.sub(r"\s+", " ", text).strip()


def _find_connection_budget_target(page):
    logger.info("Procurando botão/link 'Orçamento de Conexão'.")
    text_pattern = re.compile(r"Or[çc]amento\s+de\s+Conex[aã]o", re.IGNORECASE)
    candidates = [
        page.get_by_role("link", name=text_pattern),
        page.get_by_role("button", name=text_pattern),
        page.get_by_text(text_pattern),
        page.locator("a:has-text('Orçamento de Conexão')"),
        page.locator("button:has-text('Orçamento de Conexão')"),
        page.locator("input[value*='Orçamento de Conexão']"),
    ]

    deadline = datetime.now().timestamp() + (BUDGET_BUTTON_TIMEOUT_MS / 1000)
    while datetime.now().timestamp() < deadline:
        for locator in candidates:
            target = _first_visible_locator(locator)
            if target is not None:
                logger.info("Botão/link 'Orçamento de Conexão' encontrado.")
                return target
        page.wait_for_timeout(500)

    fallback = _find_connection_budget_element_handle(page)
    if fallback is not None:
        logger.info("'Orçamento de Conexão' encontrado por fallback no DOM.")
        return fallback
    return None


def _first_visible_locator(locator):
    try:
        count = locator.count()
    except PlaywrightError:
        return None

    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible(timeout=500):
                return item
        except PlaywrightError:
            continue
    return None


def _find_connection_budget_element_handle(page):
    try:
        handle = page.evaluate_handle(
            """
            () => {
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();
              const elements = Array.from(document.querySelectorAll(
                "a, button, input, [role='button'], [onclick], span, div"
              ));
              return elements.find((element) => {
                const text = [
                  element.innerText,
                  element.textContent,
                  element.value,
                  element.getAttribute("aria-label"),
                  element.getAttribute("title")
                ].filter(Boolean).join(" ");
                return normalize(text).includes("ORCAMENTO DE CONEXAO");
              }) || null;
            }
            """
        )
        return handle.as_element()
    except PlaywrightError as exc:
        logger.debug(f"Fallback de busca do orçamento falhou: {exc}")
        return None


def _download_connection_budget(
    page,
    target,
    downloads_dir: Path,
    protocol: str | None,
    errors: list[str],
) -> Path | None:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    before_pages = list(page.context.pages)
    logger.info("Clicando em 'Orçamento de Conexão' e aguardando download do PDF.")

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            target.click(timeout=10_000)
        download = download_info.value
    except PlaywrightTimeoutError:
        message = _describe_no_download(page, before_pages)
        logger.error(message)
        errors.append(message)
        return None
    except PlaywrightError as exc:
        message = f"Falha ao clicar em 'Orçamento de Conexão': {exc}"
        logger.error(message)
        errors.append(message)
        return None

    destination = _next_download_path(downloads_dir, protocol, download.suggested_filename)
    download.save_as(str(destination))
    logger.info(f"PDF do Orçamento de Conexão salvo em {destination}.")
    return destination


def _describe_no_download(page, before_pages: list) -> str:
    new_pages = [candidate for candidate in page.context.pages if candidate not in before_pages]
    if new_pages:
        urls = ", ".join(candidate.url for candidate in new_pages)
        return (
            "O Orçamento de Conexão abriu em nova aba em vez de baixar. "
            f"Abas novas: {urls}"
        )
    if "pdf" in (page.url or "").lower():
        return (
            "O Orçamento de Conexão parece ter aberto na aba atual em vez de baixar. "
            f"URL atual: {page.url}"
        )
    return (
        "Clique em 'Orçamento de Conexão' não gerou download dentro do timeout "
        f"de {DOWNLOAD_TIMEOUT_MS / 1000:.0f}s."
    )


def _next_download_path(
    downloads_dir: Path, protocol: str | None, suggested_filename: str | None
) -> Path:
    protocol_part = sanitize_filename(protocol or "sem_protocolo")
    base_name = sanitize_filename(f"Orcamento_de_Conexao_{protocol_part}")
    suffix = Path(suggested_filename or "").suffix.lower()
    if suffix != ".pdf":
        suffix = ".pdf"

    candidate = downloads_dir / f"{base_name}{suffix}"
    if not candidate.exists():
        return candidate

    version = 2
    while True:
        versioned = downloads_dir / f"{base_name}_v{version}{suffix}"
        if not versioned.exists():
            return versioned
        version += 1


def _save_result(logs_dir: Path, result: dict) -> Path:
    output_path = logs_dir / OUTPUT_FILE_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, result, private=True)
    logger.info(f"Resultado do processamento salvo em {output_path}.")
    return output_path


if __name__ == "__main__":
    main()
