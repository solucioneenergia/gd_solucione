import re
import unicodedata

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.portal.cdp_extractors import (
    extract_point_of_connection_completion_from_text,
)


def click_follow_eye_button(
    row_locator,
    action_cell_index: int | None = None,
    *,
    click_first_visible,
    logger=logger,
) -> None:
    if action_cell_index is None:
        cells = row_locator.locator("td")
        action_cell_index = max(cells.count() - 1, 0)

    action_cell = row_locator.locator("td").nth(action_cell_index)
    logger.info(
        "Clicando no primeiro botão/link visível da coluna ACOMPANHAR "
        f"(cell_index={action_cell_index})."
    )

    primary = action_cell.locator(
        "button, a, input[type='button'], input[type='submit'], [role='button']"
    )
    if click_first_visible(primary):
        return

    fallback = action_cell.locator("[onclick], span, i, svg, img")
    if click_first_visible(fallback):
        return

    raise RuntimeError(
        "Não foi encontrado botão/link visível na coluna ACOMPANHAR da linha."
    )


def extract_detail_header(page) -> dict[str, str | None]:
    text = detail_text(page)
    title_text = "\n".join(title_candidates(page))
    combined = f"{title_text}\n{text}"

    detail_match = re.search(
        r"Solicita[cç][aã]o\s+(\d{6,})\s*:?\s*([^\n\r]+)?",
        combined,
        flags=re.IGNORECASE,
    )
    if detail_match:
        protocol = detail_match.group(1)
        client_name = clean_client_candidate(detail_match.group(2) or "", protocol)
        return {"detail_protocol": protocol, "detail_client_name": client_name}

    protocol_match = re.search(r"\b\d{6,}\b", combined)
    protocol = protocol_match.group(0) if protocol_match else None
    client_name = extract_client_from_detail_text(title_text, text, protocol)
    return {"detail_protocol": protocol, "detail_client_name": client_name}


def extract_completion_date(page) -> str | None:
    extraction = extract_point_of_connection_completion(page)
    return extraction["completion_date_raw"]


def extract_point_of_connection_completion(page, protocol: str | None = None) -> dict:
    return extract_point_of_connection_completion_from_text(
        detail_text(page),
        protocol=protocol,
        source_selector="body:text_block",
    )


def detail_has_completed_status(page) -> bool:
    return "SOLICITACAO CONCLUIDA" in _normalize_search(detail_text(page))


def wait_detail_loaded(
    page,
    protocol: str | None = None,
    *,
    detail_timeout_ms: int = 20_000,
    playwright_error=PlaywrightError,
    playwright_timeout_error=PlaywrightTimeoutError,
    logger=logger,
) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=detail_timeout_ms)
    except playwright_timeout_error:
        logger.warning("Timeout aguardando DOM da tela de detalhe.")

    if protocol:
        try:
            page.get_by_text(protocol).first.wait_for(timeout=detail_timeout_ms)
        except playwright_error:
            logger.warning(
                f"Protocolo {protocol} não apareceu na tela de detalhe dentro do timeout."
            )
    else:
        try:
            page.get_by_text(re.compile(r"Solicita[cç][aã]o", re.IGNORECASE)).first.wait_for(
                timeout=detail_timeout_ms
            )
        except playwright_error:
            logger.warning("Texto 'Solicitação' não apareceu na tela de detalhe.")


def detail_text(page, *, playwright_error=PlaywrightError, logger=logger) -> str:
    try:
        return page.locator("body").inner_text(timeout=10_000)
    except playwright_error as exc:
        logger.warning(f"Não foi possível ler o texto do detalhe: {exc}")
        return ""


def title_candidates(page, *, playwright_error=PlaywrightError) -> list[str]:
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
    except playwright_error:
        return []


def extract_client_from_detail_text(
    title_text: str, body_text: str, protocol: str | None
) -> str | None:
    patterns = [
        r"Cliente\s*:?\s*([^\n\r]+)",
        r"Titular\s+(?:da\s+UC\s*)?:?\s*([^\n\r]+)",
        r"Nome\s*:?\s*([^\n\r]+)",
    ]
    for text in [title_text, body_text]:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                client = clean_client_candidate(match.group(1), protocol)
                if client:
                    return client
    return None


def clean_client_candidate(value: str, protocol: str | None) -> str | None:
    value = re.sub(r"\s+", " ", value or "").strip(" :-|")
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


def _normalize_search(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.upper()
    return re.sub(r"\s+", " ", text).strip()
