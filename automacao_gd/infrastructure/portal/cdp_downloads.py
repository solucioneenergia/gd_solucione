import json
import re
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import sanitize_filename
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.portal.cdp_errors import DownloadNotProducedError
from automacao_gd.infrastructure.portal.cdp_extractors import (
    POINT_OF_CONNECTION_NO_DATE_STATUSES,
)


DOWNLOAD_TIMEOUT_MS = 5_000
BUDGET_BUTTON_TIMEOUT_MS = 10_000


def find_connection_budget_target(
    page,
    *,
    first_visible=None,
    budget_button_timeout_ms: int = BUDGET_BUTTON_TIMEOUT_MS,
    playwright_error=PlaywrightError,
    logger=logger,
):
    logger.info("Localizando botão/link 'Orçamento de Conexão'.")
    first_visible = first_visible or _first_visible
    text_pattern = re.compile(r"Or[çc]amento\s+de\s+Conex[aã]o", re.IGNORECASE)
    candidates = [
        page.get_by_role("link", name=text_pattern),
        page.get_by_role("button", name=text_pattern),
        page.get_by_text(text_pattern),
        page.locator("text=Orçamento de Conexão"),
        page.locator("a:has-text('Orçamento de Conexão')"),
        page.locator("button:has-text('Orçamento de Conexão')"),
        page.locator("input[value*='Orçamento de Conexão']"),
    ]

    for locator in candidates:
        target = first_visible(locator, timeout_ms=budget_button_timeout_ms)
        if target is not None:
            logger.info("'Orçamento de Conexão' encontrado por seletor de texto.")
            return target

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
        element = handle.as_element()
        if element is not None:
            logger.info("'Orçamento de Conexão' encontrado por fallback DOM.")
        return element
    except playwright_error as exc:
        logger.debug(f"Fallback DOM para orçamento falhou: {exc}")
        return None


def download_connection_budget(
    page,
    protocol: str,
    downloads_root: Path | None = None,
    target=None,
    *,
    find_connection_budget_target=find_connection_budget_target,
    get_settings=get_settings,
    sanitize_filename=sanitize_filename,
    download_timeout_ms: int = DOWNLOAD_TIMEOUT_MS,
    click_timeout_ms: int = 10_000,
    playwright_timeout_error=PlaywrightTimeoutError,
    download_not_produced_error=DownloadNotProducedError,
    logger=logger,
) -> Path | None:
    target = target or find_connection_budget_target(page)
    if target is None:
        return None

    settings = get_settings()
    root = downloads_root or settings.downloads_dir_path
    protocol_dir = Path(root) / sanitize_filename(protocol)
    protocol_dir.mkdir(parents=True, exist_ok=True)
    before_pages = list(page.context.pages)

    logger.info(
        f"Clicando em 'Orçamento de Conexão' para o protocolo {protocol} "
        f"e aguardando download."
    )
    try:
        with page.expect_download(timeout=download_timeout_ms) as download_info:
            target.click(timeout=click_timeout_ms, no_wait_after=True)
        download = download_info.value
    except playwright_timeout_error as exc:
        raise download_not_produced_error(
            describe_no_download(
                page,
                before_pages,
                download_timeout_ms=download_timeout_ms,
            )
        ) from exc

    destination = next_budget_path(protocol_dir, protocol, download.suggested_filename)
    download.save_as(str(destination))
    logger.info(f"PDF salvo em {destination}.")
    return destination


def find_existing_connection_budget_pdfs(
    protocol: str,
    downloads_root: Path | None = None,
    *,
    get_settings=get_settings,
    sanitize_filename=sanitize_filename,
) -> list[Path]:
    settings = get_settings()
    root = Path(downloads_root or settings.downloads_dir_path)
    protocol_dir = root / sanitize_filename(protocol)
    if not protocol_dir.exists():
        return []

    protocol_part = sanitize_filename(protocol)
    return sorted(
        protocol_dir.glob(f"Orcamento_de_Conexao_{protocol_part}*.pdf"),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def find_existing_connection_budget_pdf(
    protocol: str,
    downloads_root: Path | None = None,
    *,
    find_existing_connection_budget_pdfs=find_existing_connection_budget_pdfs,
) -> Path | None:
    existing = find_existing_connection_budget_pdfs(protocol, downloads_root)
    return existing[0] if existing else None


def find_existing_download_metadata(
    protocol: str,
    downloads_root: Path | None = None,
    *,
    get_settings=get_settings,
    sanitize_filename=sanitize_filename,
) -> Path | None:
    settings = get_settings()
    root = Path(downloads_root or settings.downloads_dir_path)
    metadata_path = root / sanitize_filename(protocol) / "metadata.json"
    return metadata_path if metadata_path.exists() and metadata_path.is_file() else None


def should_open_detail_for_budget(
    protocol: str,
    downloads_root: Path | None = None,
    reprocess_existing_pdfs: bool = False,
    require_completion_metadata: bool = False,
    *,
    find_existing_connection_budget_pdf=find_existing_connection_budget_pdf,
    find_existing_download_metadata=find_existing_download_metadata,
    metadata_has_completion_value=None,
) -> bool:
    if reprocess_existing_pdfs:
        return True
    existing_pdf = find_existing_connection_budget_pdf(protocol, downloads_root)
    metadata_path = find_existing_download_metadata(protocol, downloads_root)
    metadata_has_completion_value = (
        metadata_has_completion_value or _metadata_has_completion_value
    )
    if (
        require_completion_metadata
        and metadata_path is not None
        and not metadata_has_completion_value(metadata_path)
    ):
        return True
    return not (_is_valid_pdf(existing_pdf) and metadata_path is not None)


def describe_no_download(
    page,
    before_pages: list,
    *,
    download_timeout_ms: int = DOWNLOAD_TIMEOUT_MS,
) -> str:
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
        f"de {download_timeout_ms / 1000:.0f}s."
    )


def next_budget_path(
    protocol_dir: Path,
    protocol: str,
    suggested_filename: str | None,
    *,
    sanitize_filename=sanitize_filename,
) -> Path:
    suffix = Path(suggested_filename or "").suffix.lower()
    if suffix != ".pdf":
        suffix = ".pdf"

    base_name = sanitize_filename(f"Orcamento_de_Conexao_{protocol}")
    candidate = protocol_dir / f"{base_name}{suffix}"
    if not candidate.exists():
        return candidate

    version = 2
    while True:
        versioned = protocol_dir / f"{base_name}_v{version}{suffix}"
        if not versioned.exists():
            return versioned
        version += 1


def _metadata_has_completion_value(metadata_path: Path) -> bool:
    try:
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata = {}
    return bool(
        metadata.get("completion_date")
        or metadata.get("completion_date_raw")
        or metadata.get("completion_date_normalized")
        or metadata.get("completion_extraction_status")
        in POINT_OF_CONNECTION_NO_DATE_STATUSES
    )


def _is_valid_pdf(path: Path | str | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    return candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".pdf"


def _first_visible(locator, timeout_ms: int = 1_000):
    try:
        count = locator.count()
    except PlaywrightError:
        return None

    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible(timeout=timeout_ms):
                return item
        except PlaywrightError:
            continue
    return None
