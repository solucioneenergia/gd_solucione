import json
import re
import time
import unicodedata
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.dates import parse_date
from automacao_gd.infrastructure.excel.service import protocol_row_has_required_values
from automacao_gd.infrastructure.files.file_service import sanitize_filename
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.metadata.service import save_download_metadata
from automacao_gd.application.op5_completed_index import (
    load_valid_completed_entries,
    load_valid_completed_entry,
)
from automacao_gd.application.op5_optimization import (
    cached_workbook_protocol_check,
    load_eligibility_cache,
    load_or_build_workbook_index,
)
from automacao_gd.application.reconciliation_service import build_workbook_protocol_index
from automacao_gd.domain.models import PortalSolicitation
from automacao_gd.infrastructure.portal.cdp_errors import DownloadNotProducedError
from automacao_gd.infrastructure.portal.cdp_extractors import (
    POINT_OF_CONNECTION_NO_DATE_STATUSES,
    POINT_OF_CONNECTION_STAGE,
    POINT_OF_CONNECTION_STAGE_LABEL,
    _find_stage_ranges,
    _next_timeline_stage_index,
    extract_point_of_connection_completion_from_text,
)
from automacao_gd.infrastructure.portal import cdp_detail as cdp_detail_helpers
from automacao_gd.infrastructure.portal.cdp_navigation import (
    PORTAL_GD_HOST,
    _is_portal_root_or_index_url,
    _is_unsafe_authenticated_control_context_url,
    _is_unsafe_navigation_url,
    is_insecure_portal_http_url,
)
from automacao_gd.infrastructure.portal import cdp_navigation as cdp_navigation_helpers
from automacao_gd.infrastructure.pdf.service import extract_generation_data


DOWNLOAD_TIMEOUT_MS = 5_000
BUDGET_BUTTON_TIMEOUT_MS = 10_000
DETAIL_TIMEOUT_MS = 20_000
LISTING_RECOVERY_ATTEMPTS = 3
VALID_DOWNLOAD_STATUSES_FOR_PROCESSING = {"downloaded", "existing_pdf_after_skip"}
METADATA_ONLY_DOWNLOAD_STATUSES_FOR_PROCESSING = {"budget_unavailable"}
OPERATIONAL_ORIGIN_NAVIGATION_STATUSES = {
    "pagination_numeric_target_not_found",
    "pagination_numeric_target_disabled",
    "pagination_click_failed",
    "pagination_active_page_mismatch",
    "cannot_confirm_active_page",
    "pagination_target_beyond_last_page",
}
INCOMPLETE_PAGINATION_STOP_REASONS = {
    "safety_cap_reached_with_next_page",
    "pagination_click_no_change",
    "pagination_repeated_signature",
    "pagination_loop_detected",
    "pagination_duplicate_page_content",
    "cannot_confirm_active_page",
}
_CDP_EXTRACTOR_FACADE_EXPORTS = (
    POINT_OF_CONNECTION_STAGE_LABEL,
    _find_stage_ranges,
    _next_timeline_stage_index,
)
_CDP_NAVIGATION_FACADE_EXPORTS = (
    PORTAL_GD_HOST,
    _is_portal_root_or_index_url,
    _is_unsafe_authenticated_control_context_url,
    _is_unsafe_navigation_url,
    is_insecure_portal_http_url,
)


def _is_cancelled_status(status: str | None) -> bool:
    normalized = _normalize_search(status or "")
    return "CANCELAD" in normalized


def _log_origin_navigation_failure(message: str, status: str | None) -> None:
    if status in OPERATIONAL_ORIGIN_NAVIGATION_STATUSES:
        logger.warning(message)
        return
    logger.error(message)


def _origin_navigation_failure_is_skippable(navigation: dict) -> bool:
    return navigation.get("status") == "pagination_target_beyond_last_page"


def connect_to_existing_edge(playwright, cdp_endpoint: str):
    logger.info(f"Conectando ao Edge existente via CDP: {cdp_endpoint}")
    return playwright.chromium.connect_over_cdp(cdp_endpoint)


def find_portal_page_from_cdp(browser, portal_url: str):
    expected_host = urlparse(portal_url).netloc.lower()
    pages = []
    for context_index, context in enumerate(browser.contexts):
        for page_index, page in enumerate(context.pages):
            pages.append(page)
            logger.info(
                f"Aba encontrada via CDP: contexto={context_index}, "
                f"aba={page_index}, url={page.url}"
            )

    for page in pages:
        parsed = urlparse(page.url or "")
        if _page_url_is_blocked_or_insecure(page.url or ""):
            logger.warning(f"Aba bloqueada do Portal GD ignorada: {page.url}")
            continue
        if expected_host and parsed.netloc.lower() == expected_host and _is_listing_like_portal_url(
            page.url or ""
        ):
            logger.info(f"Aba do Portal GD identificada: {page.url}")
            return page

    for page in pages:
        parsed = urlparse(page.url or "")
        if _page_url_is_blocked_or_insecure(page.url or ""):
            logger.warning(f"Aba bloqueada do Portal GD ignorada: {page.url}")
            continue
        if expected_host and parsed.netloc.lower() == expected_host:
            logger.info(f"Aba do Portal GD identificada: {page.url}")
            return page
        if "gdneoenergiapernambuco.neoenergia.com" in (page.url or "").lower():
            logger.info(f"Aba do Portal GD identificada por fallback: {page.url}")
            return page

    return None


def _page_url_is_blocked_or_insecure(url: str) -> bool:
    lowered = (url or "").lower()
    return is_insecure_portal_http_url(lowered) or "errors.edgesuite.net" in lowered


def _is_listing_like_portal_url(url: str) -> bool:
    lowered = (url or "").lower()
    return (
        "/pages/acompanhamento/" in lowered
        or "minhas-solicitacoes" in lowered
        or "minhas_solicitacoes" in lowered
    )


def _page_looks_access_denied(page) -> bool:
    url = (page.url or "").lower()
    if _page_url_is_blocked_or_insecure(url):
        return True
    try:
        title = (page.title() or "").strip().lower()
        if "access denied" in title or "acesso negado" in title:
            return True
        body_text = page.locator("body").inner_text(timeout=500).lower()
        return "access denied" in body_text or "you don't have permission" in body_text
    except Exception:
        return False


def _page_looks_login_required(page) -> bool:
    try:
        url = (page.url or "").lower()
        body_text = page.locator("body").inner_text(timeout=500).lower()
    except Exception:
        return False
    if "captcha" in body_text and "entrar" in body_text:
        return True
    if "esqueceu sua senha" in body_text or "cadastrar novo" in body_text:
        return True
    return _is_portal_root_or_index_url(url) and "entrar" in body_text


def portal_login_required_message() -> str:
    return (
        "Sessao do Portal GD expirada ou tela de login/CAPTCHA detectada. "
        "Faca login manual no Edge e deixe a listagem 'Minhas Solicitacoes' aberta."
    )


def read_current_page_table_with_row_handles(page) -> list[dict]:
    logger.info("Lendo tabela 'Minhas Solicitações' com handles das linhas.")
    rows_data = page.evaluate(
        """
        () => {
          const normalize = (value) => (value || "")
            .normalize("NFD")
            .replace(/[\\u0300-\\u036f]/g, "")
            .toUpperCase()
            .replace(/\\s+/g, " ")
            .trim();

          const cellText = (cell) => (cell?.innerText || cell?.textContent || "")
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
            if (
              text.includes("CODIGO") &&
              (text.includes("UNIDADE") || text.includes("IDENTIFICACAO"))
            ) {
              return "consumer_unit_code";
            }
            if (text.includes("ENDERECO")) return "address";
            if (text.includes("DATA") && text.includes("INGRESSO")) {
              return "entry_date";
            }
            if (text.includes("ACOMPANHAR") || text.includes("ACOES") || text.includes("AÇÕES")) {
              return "follow";
            }
            return null;
          };

          const tables = Array.from(document.querySelectorAll("table"));
          for (let tableIndex = 0; tableIndex < tables.length; tableIndex += 1) {
            const rows = Array.from(tables[tableIndex].querySelectorAll("tr"));
            for (let headerRowIndex = 0; headerRowIndex < rows.length; headerRowIndex += 1) {
              const headerCells = Array.from(rows[headerRowIndex].querySelectorAll("th,td"));
              const headers = headerCells.map((cell) => mapHeader(cellText(cell)));
              const found = new Set(headers.filter(Boolean));
              if (
                found.has("protocol_client") &&
                found.has("status") &&
                found.has("consumer_unit_code") &&
                found.has("address") &&
                found.has("entry_date")
              ) {
                const indexes = {};
                headers.forEach((key, index) => {
                  if (key && indexes[key] === undefined) indexes[key] = index;
                });

                const output = [];
                for (let rowIndex = headerRowIndex + 1; rowIndex < rows.length; rowIndex += 1) {
                  const cells = Array.from(rows[rowIndex].querySelectorAll("td"));
                  if (!cells.length) continue;

                  const protocolCell = cellText(cells[indexes.protocol_client]);
                  const parsed = splitProtocolAndClient(protocolCell);
                  if (!parsed.protocol) continue;

                  const followIndex = indexes.follow !== undefined
                    ? indexes.follow
                    : cells.length - 1;

                  output.push({
                    table_index: tableIndex,
                    row_index: rowIndex,
                    action_cell_index: followIndex,
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
            }
          }
          return [];
        }
        """
    )

    rows = []
    for item in rows_data:
        row_locator = (
            page.locator("table")
            .nth(item["table_index"])
            .locator("tr")
            .nth(item["row_index"])
        )
        rows.append(
            {
                "record": PortalSolicitation(
                    protocol=item["protocol"],
                    client_name=item.get("client_name"),
                    status=item.get("status"),
                    consumer_unit_code=item.get("consumer_unit_code"),
                    address=item.get("address"),
                    entry_date=item.get("entry_date"),
                ),
                "row_locator": row_locator,
                "action_cell_index": item["action_cell_index"],
                "table_index": item["table_index"],
                "row_index": item["row_index"],
            }
        )

    logger.info(f"{len(rows)} linhas encontradas na tabela da página atual.")
    return rows


def collect_completed_requests_across_pages(page, settings) -> list[PortalSolicitation]:
    return _collect_completed_listing_rows_across_pages(page, settings)[
        "completed_records"
    ]


def _collect_completed_listing_rows_across_pages(
    page,
    settings,
    *,
    state_store=None,
    max_completed: int | None = None,
    skip_already_completed: bool = True,
    max_portal_pages: int | None = None,
) -> dict:
    pagination_enabled = bool(getattr(settings, "ENABLE_PORTAL_PAGINATION", False))
    max_pages = int(
        max_portal_pages
        if max_portal_pages is not None
        else getattr(settings, "MAX_PORTAL_PAGES", 0)
        or 0
    )
    logger.info(
        "Configuracao efetiva de paginacao do portal: "
        f"ENABLE_PORTAL_PAGINATION={pagination_enabled}; "
        f"MAX_PORTAL_PAGES={max_pages if max_pages > 0 else 'sem limite'}."
    )

    if not pagination_enabled:
        active_page = get_active_numeric_page(page) or 1
        rows = _annotate_listing_rows(
            read_current_page_table_with_row_handles(page), active_page
        )
        summary = consolidate_listing_page_rows([rows], max_pages=1)
        summary.update(
            {
                "pagination_enabled": False,
                "pagination_complete": True,
                "last_page_confirmed": True,
                "last_page_number": active_page,
                "next_page_available_after_stop": False,
                "pagination_safety_cap": 1,
                "pages_visited": [active_page],
                "pagination_stop_reason": "pagination_disabled",
                "pagination_next_found": False,
                "pagination_click_attempts": 0,
                "pagination_mode": "single_page",
                "pagination_current_page": active_page,
                "pagination_target_page": None,
                "pagination_numeric_links_found": [],
                "pagination_diagnostics": [],
            }
        )
        return summary

    page_rows: list[list[dict]] = []
    seen_signatures: set[tuple[str | None, str | None, int]] = set()
    warnings: list[str] = []
    diagnostics: list[dict] = []
    pagination_stop_reason: str | None = None
    pagination_next_found = False
    pagination_click_attempts = 0
    pagination_current_page = 1
    pagination_target_page = None
    pagination_numeric_links_found: list[str] = []
    pending_click_diagnostic: dict | None = None
    pagination_complete = False
    last_page_confirmed = False
    last_page_number = None
    next_page_available_after_stop = False
    pages_visited: list[int] = []
    visited_page_numbers: set[int] = set()
    completed_pages_skipped_already_completed: list[int] = []

    while True:
        if max_pages > 0 and len(page_rows) >= max_pages:
            availability = inspect_next_page_availability(page, pagination_current_page)
            next_page_available_after_stop = bool(
                availability.get("next_page_available")
            )
            pagination_target_page = availability.get("target_page_number")
            pagination_numeric_links_found = list(
                availability.get("numeric_page_links_found") or []
            )
            if next_page_available_after_stop:
                pagination_stop_reason = "safety_cap_reached_with_next_page"
            else:
                pagination_stop_reason = "last_page_reached"
                pagination_complete = True
                last_page_confirmed = True
                last_page_number = pagination_current_page
            warnings.append(f"MAX_PORTAL_PAGES atingido: {max_pages}.")
            break

        current_active_page = get_active_numeric_page(page)
        if current_active_page is None:
            pagination_stop_reason = "cannot_confirm_active_page"
            warnings.append("cannot_confirm_active_page")
            break
        if current_active_page in visited_page_numbers:
            if pending_click_diagnostic is not None:
                stabilized_page = _wait_for_active_page_change(
                    page,
                    previous_page_number=current_active_page,
                )
                pending_click_diagnostic["active_page_after_retry"] = stabilized_page
                if stabilized_page is not None and stabilized_page not in visited_page_numbers:
                    current_active_page = stabilized_page
                else:
                    pagination_stop_reason = "pagination_loop_detected"
                    warnings.append("PAGINATION_LOOP_DETECTED")
                    break
            else:
                pagination_stop_reason = "pagination_loop_detected"
                warnings.append("PAGINATION_LOOP_DETECTED")
                break
        if current_active_page in visited_page_numbers:
            pagination_stop_reason = "pagination_loop_detected"
            warnings.append("PAGINATION_LOOP_DETECTED")
            break
        visited_page_numbers.add(current_active_page)
        pages_visited.append(current_active_page)
        pagination_current_page = current_active_page
        rows = _annotate_listing_rows(
            read_current_page_table_with_row_handles(page),
            current_active_page,
        )
        signature = _listing_rows_signature(rows)
        if pending_click_diagnostic is not None:
            pending_click_diagnostic["signature_after"] = signature
        if signature in seen_signatures:
            pagination_stop_reason = (
                "pagination_click_no_change"
                if pending_click_diagnostic is not None
                else "pagination_repeated_signature"
            )
            if pending_click_diagnostic is not None:
                pending_click_diagnostic["stop_reason"] = pagination_stop_reason
            warnings.append(
                "Pagina repetida detectada apos clique de paginacao; coleta interrompida."
            )
            break
        seen_signatures.add(signature)
        page_rows.append(rows)
        logger.info(
            f"Pagina {current_active_page} lida no portal: "
            f"{len(rows)} linhas, assinatura={signature}."
        )
        if _completed_page_is_fully_local_done(
            rows,
            settings,
            state_store=state_store,
            skip_already_completed=skip_already_completed,
        ):
            completed_pages_skipped_already_completed.append(current_active_page)
            logger.info(
                f"Pagina {current_active_page} pulada para processamento: todos os "
                "protocolos concluidos ja estao completos localmente."
            )
        if _incremental_batch_limit_reached(
            page_rows,
            settings,
            state_store=state_store,
            max_completed=max_completed,
            skip_already_completed=skip_already_completed,
        ):
            pagination_stop_reason = "incremental_batch_limit_reached"
            pagination_complete = False
            last_page_confirmed = False
            next_page_available_after_stop = True
            break

        if max_pages > 0 and len(page_rows) >= max_pages:
            availability = inspect_next_page_availability(page, current_active_page)
            next_page_available_after_stop = bool(
                availability.get("next_page_available")
            )
            pagination_target_page = availability.get("target_page_number")
            pagination_numeric_links_found = list(
                availability.get("numeric_page_links_found") or []
            )
            diagnostics.append(
                {
                    "mode": "availability_after_safety_cap",
                    "page_number": current_active_page,
                    **availability,
                }
            )
            if next_page_available_after_stop:
                pagination_stop_reason = "safety_cap_reached_with_next_page"
            else:
                pagination_stop_reason = "last_page_reached"
                pagination_complete = True
                last_page_confirmed = True
                last_page_number = current_active_page
            warnings.append(f"MAX_PORTAL_PAGES atingido: {max_pages}.")
            break

        logger.info(f"Tentando avancar para pagina alvo: {current_active_page + 1}")
        next_diagnostic = find_and_click_next_listing_page(page, current_active_page)
        next_diagnostic["page_number"] = current_active_page
        next_diagnostic["signature_before"] = signature
        diagnostics.append(next_diagnostic)
        pagination_next_found = pagination_next_found or bool(
            next_diagnostic.get("next_page_available", next_diagnostic.get("found"))
        )
        pagination_current_page = current_active_page
        pagination_target_page = next_diagnostic.get("target_page_number")
        pagination_numeric_links_found = list(
            next_diagnostic.get("numeric_page_links_found") or []
        )
        if next_diagnostic.get("found"):
            pagination_click_attempts += 1
        logger.info(f"Diagnostico de paginacao: {next_diagnostic}")

        if next_diagnostic.get("stop_reason") == "last_page_reached":
            pagination_stop_reason = "last_page_reached"
            pagination_complete = True
            last_page_confirmed = True
            last_page_number = current_active_page
            next_page_available_after_stop = False
            break
        if not next_diagnostic.get("found"):
            pagination_stop_reason = next_diagnostic.get("stop_reason") or (
                "pagination_numeric_target_not_found"
            )
            warnings.append(pagination_stop_reason)
            break
        if not next_diagnostic.get("enabled"):
            pagination_stop_reason = (
                next_diagnostic.get("stop_reason")
                or "pagination_numeric_target_disabled"
            )
            warnings.append(pagination_stop_reason)
            break
        if not next_diagnostic.get("clicked"):
            pagination_stop_reason = (
                next_diagnostic.get("stop_reason") or "pagination_click_failed"
            )
            warnings.append(pagination_stop_reason)
            break

        pending_click_diagnostic = next_diagnostic
        _wait_after_pagination_click(page)

    summary = consolidate_listing_page_rows(page_rows, max_pages=max_pages)
    summary["pagination_warnings"].extend(warnings)
    if pagination_stop_reason is None and page_rows:
        pagination_stop_reason = "last_page_reached"
        pagination_complete = True
        last_page_confirmed = True
        last_page_number = pagination_current_page
    if pagination_stop_reason in INCOMPLETE_PAGINATION_STOP_REASONS:
        pagination_complete = False
        last_page_confirmed = False
    summary.update(
        {
            "pagination_enabled": True,
            "pagination_complete": pagination_complete,
            "last_page_confirmed": last_page_confirmed,
            "last_page_number": last_page_number,
            "next_page_available_after_stop": next_page_available_after_stop,
            "pagination_safety_cap": max_pages,
            "pages_visited": pages_visited,
            "pagination_stop_reason": (
                pagination_stop_reason or "pagination_completed_without_explicit_reason"
            ),
            "pagination_next_found": pagination_next_found,
            "pagination_click_attempts": pagination_click_attempts,
            "pagination_mode": "numeric",
            "pagination_current_page": pagination_current_page,
            "pagination_target_page": pagination_target_page,
            "pagination_numeric_links_found": pagination_numeric_links_found,
            "pagination_diagnostics": diagnostics,
            "completed_pages_skipped_already_completed": (
                completed_pages_skipped_already_completed
            ),
            "total_completed_pages_skipped_already_completed": len(
                completed_pages_skipped_already_completed
            ),
        }
    )
    return summary


def _op5_reconciliation_mode(settings) -> str:
    return str(getattr(settings, "OP5_RECONCILIATION_MODE", "inline_global") or "inline_global").strip().lower()


def _is_batch_fast_mode(settings) -> bool:
    return _op5_reconciliation_mode(settings) == "batch_fast"


def _incremental_batch_limit_reached(
    page_rows: list[list[dict]],
    settings,
    *,
    state_store=None,
    max_completed: int | None = None,
    skip_already_completed: bool = True,
) -> bool:
    if not _is_batch_fast_mode(settings):
        return False
    limit = int(max_completed if max_completed is not None else getattr(settings, "MAX_COMPLETED_TO_PROCESS", 0) or 0)
    if limit <= 0:
        return False
    completed_records = consolidate_listing_page_rows(page_rows)["completed_records"]
    selection = select_eligible_completed_requests(
        completed_requests=completed_records,
        pipeline_state=state_store,
        max_completed_to_process=limit,
        skip_already_completed=skip_already_completed,
        force_reprocess_protocols=getattr(settings, "force_reprocess_protocols", set()),
        settings=settings,
    )
    target_protocols = set(getattr(settings, "op5_target_protocols", set()) or set())
    if target_protocols:
        selected_protocols = {record.protocol for record in selection["selected_records"]}
        return target_protocols <= selected_protocols
    downloads_root = Path(getattr(settings, "downloads_dir_path", Path("data/downloads")))
    reusable_records = _batch_fast_local_reusable_records(
        selection["eligible_records"],
        downloads_root=downloads_root,
        settings=settings,
        reprocess_existing_pdfs=bool(
            getattr(settings, "REPROCESS_EXISTING_PDFS", False)
        ),
    )
    return len(reusable_records) >= limit


def _completed_page_is_fully_local_done(
    rows: list[dict],
    settings,
    *,
    state_store=None,
    skip_already_completed: bool = True,
) -> bool:
    if not skip_already_completed or not _is_batch_fast_mode(settings):
        return False
    completed_records: list[PortalSolicitation] = []
    for row in rows:
        record = _record_with_origin(row, int(row.get("page_number") or 1))
        if is_completed_status(record.status):
            completed_records.append(record)
    if not completed_records:
        return False
    return all(
        _state_should_skip_completed(state_store, record.protocol, settings)
        for record in completed_records
    )


def _batch_fast_page_local_state(
    rows: list[dict],
    settings,
    *,
    state_store=None,
    skip_already_completed: bool = True,
) -> dict:
    completed_protocols: list[str] = []
    locally_complete_protocols: list[str] = []
    pending_protocols: list[str] = []
    for row in rows:
        record = _record_with_origin(row, int(row.get("page_number") or 1))
        if not is_completed_status(record.status):
            continue
        completed_protocols.append(record.protocol)
        if skip_already_completed and _state_should_skip_completed(
            state_store,
            record.protocol,
            settings,
        ):
            locally_complete_protocols.append(record.protocol)
        else:
            pending_protocols.append(record.protocol)
    return {
        "completed_count": len(completed_protocols),
        "completed_protocols": completed_protocols,
        "locally_complete_count": len(locally_complete_protocols),
        "locally_complete_protocols": locally_complete_protocols,
        "pending_count": len(pending_protocols),
        "pending_protocols": pending_protocols,
        "fully_local_done": bool(completed_protocols) and not pending_protocols,
    }


def _batch_fast_light_scan_first_pending_page(
    page,
    settings,
    *,
    state_store=None,
    skip_already_completed: bool = True,
) -> dict | None:
    if not _is_batch_fast_mode(settings):
        return None
    if not bool(getattr(settings, "ENABLE_PORTAL_PAGINATION", False)):
        return None
    if not skip_already_completed:
        return None
    if getattr(settings, "op5_target_protocols", set()) or set():
        return None

    max_pages = int(getattr(settings, "MAX_PORTAL_PAGES", 0) or 0)
    pages_visited: list[int] = []
    completed_pages_skipped: list[int] = []
    page_states: list[dict] = []
    diagnostics: list[dict] = []
    warnings: list[str] = []
    visited_page_numbers: set[int] = set()
    seen_signatures: set[tuple[str | None, str | None, int]] = set()
    pagination_click_attempts = 0
    next_page_available_after_stop = False
    pagination_target_page = None
    pagination_numeric_links_found: list[str] = []

    while True:
        if max_pages > 0 and len(pages_visited) >= max_pages:
            current_page = pages_visited[-1] if pages_visited else 1
            availability = inspect_next_page_availability(page, current_page)
            next_page_available_after_stop = bool(
                availability.get("next_page_available")
            )
            pagination_target_page = availability.get("target_page_number")
            pagination_numeric_links_found = list(
                availability.get("numeric_page_links_found") or []
            )
            warnings.append(f"MAX_PORTAL_PAGES atingido: {max_pages}.")
            return {
                "success": False,
                "status": (
                    "light_scan_safety_cap_reached_with_next_page"
                    if next_page_available_after_stop
                    else "light_scan_no_pending_until_last_page"
                ),
                "pages_visited": pages_visited,
                "completed_pages_skipped_already_completed": completed_pages_skipped,
                "page_states": page_states,
                "warnings": warnings,
                "diagnostics": diagnostics,
                "pagination_click_attempts": pagination_click_attempts,
                "next_page_available_after_stop": next_page_available_after_stop,
                "target_page_number": None,
                "pagination_target_page": pagination_target_page,
                "pagination_numeric_links_found": pagination_numeric_links_found,
            }

        current_active_page = get_active_numeric_page(page)
        if current_active_page is None:
            return {
                "success": False,
                "status": "light_scan_cannot_confirm_active_page",
                "pages_visited": pages_visited,
                "completed_pages_skipped_already_completed": completed_pages_skipped,
                "page_states": page_states,
                "warnings": [*warnings, "cannot_confirm_active_page"],
                "diagnostics": diagnostics,
                "pagination_click_attempts": pagination_click_attempts,
                "next_page_available_after_stop": next_page_available_after_stop,
                "target_page_number": None,
                "pagination_target_page": pagination_target_page,
                "pagination_numeric_links_found": pagination_numeric_links_found,
            }
        if current_active_page in visited_page_numbers:
            return {
                "success": False,
                "status": "light_scan_pagination_loop_detected",
                "pages_visited": pages_visited,
                "completed_pages_skipped_already_completed": completed_pages_skipped,
                "page_states": page_states,
                "warnings": [*warnings, "PAGINATION_LOOP_DETECTED"],
                "diagnostics": diagnostics,
                "pagination_click_attempts": pagination_click_attempts,
                "next_page_available_after_stop": next_page_available_after_stop,
                "target_page_number": None,
                "pagination_target_page": pagination_target_page,
                "pagination_numeric_links_found": pagination_numeric_links_found,
            }

        rows = _annotate_listing_rows(
            read_current_page_table_with_row_handles(page),
            current_active_page,
        )
        signature = _listing_rows_signature(rows)
        if signature in seen_signatures:
            return {
                "success": False,
                "status": "light_scan_repeated_signature",
                "pages_visited": pages_visited,
                "completed_pages_skipped_already_completed": completed_pages_skipped,
                "page_states": page_states,
                "warnings": [
                    *warnings,
                    "Pagina repetida detectada durante varredura leve.",
                ],
                "diagnostics": diagnostics,
                "pagination_click_attempts": pagination_click_attempts,
                "next_page_available_after_stop": next_page_available_after_stop,
                "target_page_number": None,
                "pagination_target_page": pagination_target_page,
                "pagination_numeric_links_found": pagination_numeric_links_found,
            }

        visited_page_numbers.add(current_active_page)
        seen_signatures.add(signature)
        pages_visited.append(current_active_page)
        local_state = _batch_fast_page_local_state(
            rows,
            settings,
            state_store=state_store,
            skip_already_completed=skip_already_completed,
        )
        page_state = {
            "page_number": current_active_page,
            "completed_count": local_state["completed_count"],
            "locally_complete_count": local_state["locally_complete_count"],
            "pending_count": local_state["pending_count"],
            "pending_protocols": local_state["pending_protocols"],
        }
        page_states.append(page_state)
        logger.info(
            f"Varredura leve OP5 pagina {current_active_page}: "
            f"{local_state['completed_count']} concluidos, "
            f"{local_state['locally_complete_count']} completos localmente, "
            f"{local_state['pending_count']} pendentes locais."
        )
        if local_state["pending_count"] > 0:
            return {
                "success": True,
                "status": "light_scan_first_pending_page_found",
                "target_page_number": current_active_page,
                "pages_visited": pages_visited,
                "completed_pages_skipped_already_completed": completed_pages_skipped,
                "page_states": page_states,
                "warnings": warnings,
                "diagnostics": diagnostics,
                "pagination_click_attempts": pagination_click_attempts,
                "next_page_available_after_stop": True,
                "pagination_target_page": current_active_page,
                "pagination_numeric_links_found": pagination_numeric_links_found,
                "pending_protocols": local_state["pending_protocols"],
            }
        if local_state["fully_local_done"]:
            completed_pages_skipped.append(current_active_page)

        if max_pages > 0 and len(pages_visited) >= max_pages:
            continue

        next_diagnostic = find_and_click_next_listing_page(page, current_active_page)
        next_diagnostic["page_number"] = current_active_page
        next_diagnostic["signature_before"] = signature
        diagnostics.append(next_diagnostic)
        pagination_target_page = next_diagnostic.get("target_page_number")
        pagination_numeric_links_found = list(
            next_diagnostic.get("numeric_page_links_found") or []
        )
        if next_diagnostic.get("found"):
            pagination_click_attempts += 1
        if next_diagnostic.get("stop_reason") == "last_page_reached":
            return {
                "success": False,
                "status": "light_scan_no_pending_until_last_page",
                "target_page_number": None,
                "pages_visited": pages_visited,
                "completed_pages_skipped_already_completed": completed_pages_skipped,
                "page_states": page_states,
                "warnings": warnings,
                "diagnostics": diagnostics,
                "pagination_click_attempts": pagination_click_attempts,
                "next_page_available_after_stop": False,
                "pagination_target_page": pagination_target_page,
                "pagination_numeric_links_found": pagination_numeric_links_found,
            }
        if not (
            next_diagnostic.get("found")
            and next_diagnostic.get("enabled")
            and next_diagnostic.get("clicked")
        ):
            status = next_diagnostic.get("stop_reason") or "light_scan_click_failed"
            return {
                "success": False,
                "status": status,
                "target_page_number": None,
                "pages_visited": pages_visited,
                "completed_pages_skipped_already_completed": completed_pages_skipped,
                "page_states": page_states,
                "warnings": [*warnings, status],
                "diagnostics": diagnostics,
                "pagination_click_attempts": pagination_click_attempts,
                "next_page_available_after_stop": bool(
                    next_diagnostic.get("next_page_available")
                ),
                "pagination_target_page": pagination_target_page,
                "pagination_numeric_links_found": pagination_numeric_links_found,
            }
        _wait_after_pagination_click(page)


def _batch_fast_remaining_collection_page_budget(
    light_scan: dict | None,
    settings,
) -> int | None:
    if not light_scan:
        return None
    max_pages = int(getattr(settings, "MAX_PORTAL_PAGES", 0) or 0)
    if max_pages <= 0:
        return None
    pages_visited = list(light_scan.get("pages_visited") or [])
    pages_already_consumed_before_collection = max(0, len(pages_visited) - 1)
    return max(1, max_pages - pages_already_consumed_before_collection)


def _merge_batch_fast_light_scan_collection(
    collection: dict,
    light_scan: dict | None,
) -> None:
    if light_scan is None:
        return
    skipped_pages = [
        *list(light_scan.get("completed_pages_skipped_already_completed") or []),
        *list(collection.get("completed_pages_skipped_already_completed") or []),
    ]
    collection["completed_pages_skipped_already_completed"] = list(
        dict.fromkeys(skipped_pages)
    )
    collection["total_completed_pages_skipped_already_completed"] = len(
        collection["completed_pages_skipped_already_completed"]
    )
    collection["batch_fast_light_scan_status"] = light_scan.get("status")
    collection["batch_fast_light_scan_pages_visited"] = list(
        light_scan.get("pages_visited") or []
    )
    collection["batch_fast_light_scan_completed_pages_skipped"] = list(
        light_scan.get("completed_pages_skipped_already_completed") or []
    )
    collection["batch_fast_light_scan_first_pending_page"] = light_scan.get(
        "target_page_number"
    )
    collection["batch_fast_light_scan_stop_reason"] = light_scan.get("status")
    collection["batch_fast_light_scan_page_states"] = list(
        light_scan.get("page_states") or []
    )


def consolidate_listing_page_rows(
    page_rows: list[list[dict]], max_pages: int = 0
) -> dict:
    completed_records: list[PortalSolicitation] = []
    duplicate_skips: list[dict] = []
    seen_protocols: set[str] = set()
    total_rows = 0
    pages_read = 0
    signatures: set[tuple[str | None, str | None, int]] = set()
    warnings: list[str] = []

    for index, rows in enumerate(page_rows, start=1):
        if max_pages > 0 and pages_read >= max_pages:
            warnings.append(f"MAX_PORTAL_PAGES atingido: {max_pages}.")
            break

        signature = _listing_rows_signature(rows)
        if signature in signatures:
            warnings.append(
                "Pagina repetida detectada durante consolidacao; coleta interrompida."
            )
            break
        signatures.add(signature)
        pages_read += 1
        total_rows += len(rows)

        for row in rows:
            record = _record_with_origin(row, index)
            if not is_completed_status(record.status):
                continue
            if record.protocol in seen_protocols:
                duplicate_skips.append(
                    {
                        "protocol": record.protocol,
                        "client_name": record.client_name,
                        "status": record.status,
                        "page_number": record.page_number,
                        "row_index": record.row_index,
                        "skip_status": "skipped_duplicate_in_run",
                        "selection_reason": "duplicate_removed",
                    }
                )
                continue
            seen_protocols.add(record.protocol)
            completed_records.append(record)

    return {
        "pages_read": pages_read,
        "total_rows": total_rows,
        "total_completed": len(completed_records) + len(duplicate_skips),
        "completed_records": completed_records,
        "duplicates_skipped": duplicate_skips,
        "pagination_warnings": warnings,
    }


def _annotate_listing_rows(rows: list[dict], page_number: int) -> list[dict]:
    annotated: list[dict] = []
    for row in rows:
        clone = dict(row)
        record = row["record"].model_copy(deep=True)
        record.page_number = page_number
        record.row_index = row.get("row_index")
        clone["record"] = record
        clone["page_number"] = page_number
        annotated.append(clone)
    return annotated


def _record_with_origin(row: dict, fallback_page_number: int) -> PortalSolicitation:
    record = row["record"].model_copy(deep=True)
    record.page_number = record.page_number or row.get("page_number") or fallback_page_number
    record.row_index = (
        record.row_index if record.row_index is not None else row.get("row_index")
    )
    return record


def _listing_rows_signature(rows: list[dict]) -> tuple[str | None, str | None, int]:
    protocols = [row["record"].protocol for row in rows if row.get("record")]
    first = protocols[0] if protocols else None
    last = protocols[-1] if protocols else None
    return first, last, len(protocols)


def _selection_skip_dict(
    record: PortalSolicitation,
    selection_reason: str,
    pipeline_state=None,
) -> dict:
    return {
        "protocol": record.protocol,
        "client_name": record.client_name,
        "status": record.status,
        "page_number": record.page_number,
        "row_index": record.row_index,
        "skip_status": (
            "skipped_duplicate_in_run"
            if selection_reason == "duplicate_removed"
            else "skipped_already_completed"
        ),
        "selection_reason": selection_reason,
        "previous_state": "completed"
        if selection_reason == "skipped_completed"
        else None,
        "previous_last_step": _state_last_step(pipeline_state, record.protocol)
        if selection_reason == "skipped_completed"
        else None,
    }


def _state_should_skip_completed(pipeline_state, protocol: str, settings=None) -> bool:
    if _op5_completed_index_should_skip(protocol, settings):
        return True
    if pipeline_state is None:
        return _workbook_should_skip_completed(protocol, settings)
    should_skip_completed = getattr(pipeline_state, "should_skip_completed", None)
    if callable(should_skip_completed):
        return bool(should_skip_completed(protocol, settings)) or (
            _workbook_should_skip_completed(protocol, settings)
        )
    entry = _state_entry(pipeline_state, protocol)
    return bool(entry and entry.get("status") == "completed") or (
        _workbook_should_skip_completed(protocol, settings)
    )


def _op5_completed_index_should_skip(protocol: str, settings=None) -> bool:
    if settings is None or _op5_reconciliation_mode(settings) != "batch_fast":
        return False
    index_path = getattr(settings, "op5_completed_index_path", None)
    if index_path is None:
        return False
    entry = load_valid_completed_entry(Path(index_path), protocol)
    if entry is None:
        return False
    return _op5_completed_index_entry_is_locally_valid(entry, settings)


def _op5_completed_index_entry_is_locally_valid(
    entry: dict[str, Any],
    settings=None,
    *,
    current_workbook_sha: str | None = None,
) -> bool:
    current_workbook_sha = current_workbook_sha or _settings_workbook_sha256(settings)
    if not current_workbook_sha:
        return False
    if str(entry.get("workbook_sha256") or "").lower() != current_workbook_sha:
        return False
    if not entry.get("workbook_sheet") or not entry.get("workbook_row"):
        return False
    download_path = Path(str(entry.get("download_pdf_path") or ""))
    archived_path = Path(str(entry.get("archived_pdf_path") or ""))
    download_sha = str(entry.get("download_pdf_sha256") or "")
    archived_sha = str(entry.get("archived_pdf_sha256") or "")
    if not _file_matches_sha256(download_path, download_sha):
        return False
    if not _file_matches_sha256(archived_path, archived_sha):
        return False
    return True


def _settings_workbook_sha256(settings) -> str | None:
    workbook_path = getattr(settings, "planilha_path", None)
    if workbook_path is None:
        return None
    workbook = Path(workbook_path)
    if not workbook.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with workbook.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        logger.warning(f"SHA da planilha nao pode ser calculado para OP5: {exc}")
        return None
    return digest.hexdigest()


def _navigate_to_batch_fast_completed_index_anchor(
    page,
    settings,
    *,
    state_store=None,
    skip_already_completed: bool,
) -> dict | None:
    target_page = _batch_fast_completed_index_anchor_page(
        settings,
        state_store=state_store,
        skip_already_completed=skip_already_completed,
    )
    anchor_source = "completed_index"
    if target_page is None:
        target_page = _batch_fast_workbook_completion_estimated_anchor_page(
            settings,
            skip_already_completed=skip_already_completed,
        )
        anchor_source = "workbook_completion_estimate"
    if target_page is None or target_page <= 1:
        return None
    logger.info(
        "Navegando diretamente para pagina ancora OP5 "
        f"({anchor_source}): {target_page}."
    )
    navigation = navigate_to_numeric_page(page, target_page)
    navigation["anchor_source"] = anchor_source
    if navigation.get("active_page_after") != target_page:
        return {
            **navigation,
            "success": False,
            "status": "completed_index_anchor_unconfirmed_page",
            "error": (
                "Pagina ativa apos navegacao por indice mestre OP5: "
                f"{navigation.get('active_page_after')}; esperado: {target_page}."
            ),
        }
    if navigation.get("success"):
        return {
            **navigation,
            "status": navigation.get("status") or "completed_index_anchor_page",
            "method": navigation.get("method") or "completed_index_anchor_page",
            "anchor_source": anchor_source,
        }
    logger.warning(
        "Nao foi possivel usar pagina ancora do indice mestre OP5; "
        "voltando ao reset seguro para pagina 1."
    )
    return navigation


def _batch_fast_completed_index_anchor_page(
    settings,
    *,
    state_store=None,
    skip_already_completed: bool,
) -> int | None:
    if not skip_already_completed or not _is_batch_fast_mode(settings):
        return None
    if getattr(settings, "op5_target_protocols", set()) or set():
        return None
    index_path = getattr(settings, "op5_completed_index_path", None)
    if index_path is None:
        return None
    try:
        current_workbook_sha = _settings_workbook_sha256(settings)
    except OSError as exc:
        logger.warning(
            "SHA da planilha nao pode ser calculado para ancora OP5; "
            f"usando paginacao segura. Motivo: {exc}"
        )
        return None
    if not current_workbook_sha:
        return None
    pages: list[int] = []
    for protocol, entry in load_valid_completed_entries(Path(index_path)).items():
        if entry.get("portal_anchor_scope") != "global_batch_fast":
            continue
        if not _op5_completed_index_entry_is_locally_valid(
            entry, settings, current_workbook_sha=current_workbook_sha
        ):
            continue
        is_force_reprocess = getattr(state_store, "is_force_reprocess", None)
        if callable(is_force_reprocess):
            if is_force_reprocess(protocol):
                continue
        if protocol in (getattr(settings, "force_reprocess_protocols", set()) or set()):
            continue
        try:
            page_number = int(entry.get("portal_page_number") or 0)
        except (TypeError, ValueError):
            continue
        if page_number > 1:
            pages.append(page_number)
    return max(pages) if pages else None


def _batch_fast_workbook_completion_estimated_anchor_page(
    settings,
    *,
    skip_already_completed: bool,
) -> int | None:
    if not skip_already_completed or not _is_batch_fast_mode(settings):
        return None
    if getattr(settings, "op5_target_protocols", set()) or set():
        return None
    if not bool(getattr(settings, "APPLY_EXCEL", False)):
        return None
    workbook_path = getattr(settings, "planilha_path", None)
    if workbook_path is None:
        return None
    workbook = Path(workbook_path)
    if not workbook.is_file():
        return None
    logs_dir = Path(getattr(settings, "logs_dir_path", Path("data/logs")))
    try:
        index = load_or_build_workbook_index(
            workbook,
            cache_path=logs_dir / "op5_workbook_completion_index_cache.json",
            builder=_build_workbook_completion_index,
        )
    except Exception as exc:
        logger.warning(
            "Indice de completude da planilha indisponivel para ancora OP5; "
            f"usando paginacao segura. Motivo: {exc}"
        )
        return None
    protocols = index.get("protocols") if isinstance(index, dict) else None
    if not isinstance(protocols, dict):
        return None
    complete_count = sum(
        1
        for item in protocols.values()
        if isinstance(item, dict)
        and item.get("success")
        and item.get("complete")
    )
    page_size = int(getattr(settings, "PORTAL_LISTING_PAGE_SIZE", 50) or 50)
    if page_size <= 0:
        page_size = 50
    anchor_page = complete_count // page_size
    if anchor_page <= 1:
        return None
    return anchor_page


def _file_matches_sha256(path: Path, expected_sha256: str) -> bool:
    if len(expected_sha256) != 64 or not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256.lower()


def _workbook_should_skip_completed(protocol: str, settings=None) -> bool:
    if settings is None:
        return False
    if not bool(getattr(settings, "APPLY_EXCEL", False)):
        return False
    mode = str(getattr(settings, "OP5_RECONCILIATION_MODE", "") or "").lower()
    if mode != "batch_fast":
        return False
    workbook_path = getattr(settings, "planilha_path", None)
    if workbook_path is None:
        return False
    workbook = Path(workbook_path)
    if not workbook.is_file():
        return False
    logs_dir = Path(getattr(settings, "logs_dir_path", Path("data/logs")))
    validation = _workbook_completion_validation(
        workbook,
        protocol,
        cache_path=logs_dir / "op5_workbook_completion_index_cache.json",
        fallback_cache_path=logs_dir / "op5_workbook_protocol_check_cache.json",
    )
    return bool(validation.get("success") and validation.get("complete"))


def _workbook_completion_validation(
    workbook: Path,
    protocol: str,
    *,
    cache_path: Path,
    fallback_cache_path: Path,
) -> dict[str, Any]:
    try:
        index = load_or_build_workbook_index(
            workbook,
            cache_path=cache_path,
            builder=_build_workbook_completion_index,
        )
    except Exception:
        return cached_workbook_protocol_check(
            workbook,
            protocol,
            cache_path=fallback_cache_path,
            checker=protocol_row_has_required_values,
        )
    protocols = index.get("protocols") if isinstance(index, dict) else None
    validation = (
        dict(protocols.get(str(protocol).strip()) or {})
        if isinstance(protocols, dict)
        else {}
    )
    if validation:
        validation["cache_hit"] = bool(index.get("cache_hit"))
        validation["workbook_sha256"] = index.get("workbook_sha256")
        return validation
    return {
        "success": False,
        "complete": False,
        "protocol": str(protocol).strip(),
        "worksheet": None,
        "row": None,
        "missing_columns": [],
        "error": "Protocolo nao encontrado na planilha",
        "cache_hit": bool(index.get("cache_hit")) if isinstance(index, dict) else False,
        "workbook_sha256": index.get("workbook_sha256") if isinstance(index, dict) else None,
    }


def _build_workbook_completion_index(workbook: Path) -> dict[str, Any]:
    index = build_workbook_protocol_index(workbook)
    protocols: dict[str, dict[str, Any]] = {}
    for protocol, records in index.records_by_protocol.items():
        if len(records) != 1:
            protocols[protocol] = {
                "success": False,
                "complete": False,
                "worksheet": None,
                "row": None,
                "missing_columns": [],
                "error": "Protocolo duplicado na planilha",
            }
            continue
        record = records[0]
        missing = _missing_required_workbook_columns(record)
        protocols[protocol] = {
            "success": True,
            "complete": not missing,
            "worksheet": str(getattr(record, "sheet_name", "") or ""),
            "row": getattr(record, "row_number", None),
            "missing_columns": missing,
            "error": "",
        }
    return {"protocols": protocols}


def _missing_required_workbook_columns(record: object) -> list[str]:
    missing: list[str] = []
    checks = (
        ("Cliente", bool(getattr(record, "client_present", False))),
        ("Protocolo", _has_workbook_value(getattr(record, "protocol_normalized", None))),
        (
            "Data de ingresso",
            _has_workbook_value(getattr(record, "ingress_date_raw", None)),
        ),
        ("Conclusao", _has_workbook_value(getattr(record, "completion_raw", None))),
        ("Parecer", _has_workbook_value(getattr(record, "parecer_raw", None))),
        ("Placa", _has_workbook_value(getattr(record, "placa_raw", None))),
        ("Inversor", _has_workbook_value(getattr(record, "inversor_raw", None))),
    )
    for column, present in checks:
        if not present:
            missing.append(column)
    return missing


def _has_workbook_value(value: object) -> bool:
    return bool(str(value or "").strip())


def _state_entry(pipeline_state, protocol: str) -> dict | None:
    if pipeline_state is None:
        return None
    get_protocol = getattr(pipeline_state, "get_protocol", None)
    if callable(get_protocol):
        entry = get_protocol(protocol)
        return entry if isinstance(entry, dict) else None
    if isinstance(pipeline_state, dict):
        protocols = pipeline_state.get("protocols", pipeline_state)
        entry = protocols.get(protocol) if isinstance(protocols, dict) else None
        return entry if isinstance(entry, dict) else None
    return None


def _call_state_store(state_store, method_name: str, *args, **kwargs):
    method = getattr(state_store, method_name, None) if state_store else None
    if callable(method):
        return method(*args, **kwargs)
    return None


def _state_last_step(pipeline_state, protocol: str) -> str | None:
    entry = _state_entry(pipeline_state, protocol)
    return entry.get("last_step") if entry else None


def is_completed_status(status: str | None) -> bool:
    normalized = _normalize_search(status or "")
    return "CONCLUIDA" in normalized


def freeze_selected_completed_records(
    rows: list[dict], max_completed: int
) -> tuple[list[PortalSolicitation], list[dict]]:
    selected, duplicates, _, _ = freeze_eligible_completed_records(
        rows,
        max_completed=max_completed,
        state_store=None,
        skip_already_completed=False,
        settings=None,
    )
    return selected, duplicates


def select_eligible_completed_requests(
    completed_requests: list[PortalSolicitation],
    pipeline_state=None,
    max_completed_to_process: int = 0,
    skip_already_completed: bool = True,
    force_reprocess_protocols: set[str] | None = None,
    settings=None,
) -> dict:
    force_reprocess_protocols = force_reprocess_protocols or set()
    target_protocols = set(getattr(settings, "op5_target_protocols", set()) or set())
    selected: list[PortalSolicitation] = []
    eligible: list[PortalSolicitation] = []
    duplicates_removed: list[dict] = []
    skipped_completed: list[dict] = []
    force_reprocess_records: list[PortalSolicitation] = []
    seen_protocols: set[str] = set()

    for request in completed_requests:
        if target_protocols and request.protocol not in target_protocols:
            continue
        if request.protocol in seen_protocols:
            duplicates_removed.append(
                _selection_skip_dict(request, "duplicate_removed", pipeline_state)
            )
            continue
        seen_protocols.add(request.protocol)

        is_force_reprocess = getattr(pipeline_state, "is_force_reprocess", None)
        is_forced = request.protocol in force_reprocess_protocols or (
            callable(is_force_reprocess) and is_force_reprocess(request.protocol)
        )
        if (
            skip_already_completed
            and not is_forced
            and _state_should_skip_completed(pipeline_state, request.protocol, settings)
        ):
            skipped_completed.append(
                _selection_skip_dict(request, "skipped_completed", pipeline_state)
            )
            continue

        reason = "force_reprocess" if is_forced else "eligible_new"
        record = request.model_copy(deep=True)
        record.selection_reason = reason
        eligible.append(record)
        if is_forced:
            force_reprocess_records.append(record)

    if max_completed_to_process <= 0:
        selected = eligible
    else:
        selected = eligible[:max_completed_to_process]

    return {
        "selected_records": selected,
        "duplicates_removed": duplicates_removed,
        "skipped_completed": skipped_completed,
        "eligible_records": eligible,
        "force_reprocess_records": force_reprocess_records,
        "total_force_reprocess": len(force_reprocess_records),
    }


def freeze_eligible_completed_records(
    rows: list[dict],
    max_completed: int,
    state_store=None,
    skip_already_completed: bool = True,
    settings=None,
) -> tuple[list[PortalSolicitation], list[dict], list[dict], list[PortalSolicitation]]:
    duplicates: list[dict] = []
    seen_protocols: set[str] = set()
    completed_requests: list[PortalSolicitation] = []

    for row in rows:
        record = _record_with_origin(row, 1)
        if not is_completed_status(record.status):
            continue

        if record.protocol in seen_protocols:
            duplicates.append(
                {
                    "protocol": record.protocol,
                    "client_name": record.client_name,
                    "status": record.status,
                    "page_number": record.page_number,
                    "row_index": record.row_index,
                    "skip_status": "skipped_duplicate_in_run",
                    "selection_reason": "duplicate_removed",
                }
            )
            continue

        seen_protocols.add(record.protocol)
        completed_requests.append(record)

    selection = select_eligible_completed_requests(
        completed_requests=completed_requests,
        pipeline_state=state_store,
        max_completed_to_process=max_completed,
        skip_already_completed=skip_already_completed,
        force_reprocess_protocols=getattr(settings, "force_reprocess_protocols", set())
        if settings
        else set(),
        settings=settings,
    )

    selected = selection["selected_records"]
    skipped_completed = selection["skipped_completed"]
    eligible = selection["eligible_records"]

    return selected, duplicates, skipped_completed, eligible


def click_follow_eye_button(row_locator, action_cell_index: int | None = None) -> None:
    return cdp_detail_helpers.click_follow_eye_button(
        row_locator,
        action_cell_index,
        click_first_visible=_click_first_visible,
        logger=logger,
    )


def extract_detail_header(page) -> dict[str, str | None]:
    return cdp_detail_helpers.extract_detail_header(page)


def extract_completion_date(page) -> str | None:
    return cdp_detail_helpers.extract_completion_date(page)


def extract_point_of_connection_completion(page, protocol: str | None = None) -> dict:
    return cdp_detail_helpers.extract_point_of_connection_completion(
        page,
        protocol=protocol,
    )


def detail_has_completed_status(page) -> bool:
    return cdp_detail_helpers.detail_has_completed_status(page)


def find_connection_budget_target(page):
    logger.info("Localizando botão/link 'Orçamento de Conexão'.")
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
        target = _first_visible(locator, timeout_ms=BUDGET_BUTTON_TIMEOUT_MS)
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
    except PlaywrightError as exc:
        logger.debug(f"Fallback DOM para orçamento falhou: {exc}")
        return None


def download_connection_budget(
    page,
    protocol: str,
    downloads_root: Path | None = None,
    target=None,
) -> Path | None:
    target = target or find_connection_budget_target(page)
    if target is None:
        return None

    settings = get_settings()
    root = downloads_root or settings.downloads_dir_path
    protocol_dir = root / sanitize_filename(protocol)
    protocol_dir.mkdir(parents=True, exist_ok=True)
    before_pages = list(page.context.pages)

    logger.info(
        f"Clicando em 'Orçamento de Conexão' para o protocolo {protocol} "
        f"e aguardando download."
    )
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            target.click(timeout=10_000, no_wait_after=True)
        download = download_info.value
    except PlaywrightTimeoutError as exc:
        raise DownloadNotProducedError(
            _describe_no_download(page, before_pages)
        ) from exc

    destination = _next_budget_path(protocol_dir, protocol, download.suggested_filename)
    download.save_as(str(destination))
    logger.info(f"PDF salvo em {destination}.")
    return destination


def return_to_listing(page, listing_url: str) -> None:
    return cdp_navigation_helpers.return_to_listing(
        page,
        listing_url,
        ensure_minhas_solicitacoes=ensure_minhas_solicitacoes,
        logger=logger,
    )


def wait_detail_loaded(page, protocol: str | None = None) -> None:
    return cdp_detail_helpers.wait_detail_loaded(
        page,
        protocol,
        detail_timeout_ms=DETAIL_TIMEOUT_MS,
        playwright_error=PlaywrightError,
        playwright_timeout_error=PlaywrightTimeoutError,
        logger=logger,
    )


def find_existing_connection_budget_pdfs(
    protocol: str, downloads_root: Path | None = None
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
    protocol: str, downloads_root: Path | None = None
) -> Path | None:
    existing = find_existing_connection_budget_pdfs(protocol, downloads_root)
    return existing[0] if existing else None


def find_existing_download_metadata(
    protocol: str, downloads_root: Path | None = None
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
) -> bool:
    if reprocess_existing_pdfs:
        return True
    existing_pdf = find_existing_connection_budget_pdf(protocol, downloads_root)
    metadata_path = find_existing_download_metadata(protocol, downloads_root)
    if (
        require_completion_metadata
        and metadata_path is not None
        and not _metadata_has_completion_value(metadata_path)
    ):
        return True
    return not (_is_valid_pdf(existing_pdf) and metadata_path is not None)


def _can_reuse_existing_pdf_without_detail(
    *,
    record: PortalSolicitation,
    existing_pdf: Path | None,
    downloads_root: Path,
    reprocess_existing_pdfs: bool,
    require_completion_metadata: bool,
) -> bool:
    if reprocess_existing_pdfs or not _is_valid_pdf(existing_pdf):
        return False
    metadata_path = find_existing_download_metadata(record.protocol, downloads_root)
    if metadata_path is None:
        return not require_completion_metadata or _record_has_completion_value(record)
    if require_completion_metadata and not _metadata_has_completion_value(metadata_path):
        return _record_has_completion_value(record)
    return True


def _record_can_reuse_existing_pdf_without_detail(
    record: PortalSolicitation,
    *,
    downloads_root: Path,
    settings,
    reprocess_existing_pdfs: bool,
) -> bool:
    existing_pdf = find_existing_connection_budget_pdf(record.protocol, downloads_root)
    return _can_reuse_existing_pdf_without_detail(
        record=record,
        existing_pdf=existing_pdf,
        downloads_root=downloads_root,
        reprocess_existing_pdfs=reprocess_existing_pdfs,
        require_completion_metadata=bool(
            getattr(settings, "APPLY_EXCEL", False) and _is_batch_fast_mode(settings)
        ),
    )


def _batch_fast_local_reusable_records(
    records: list[PortalSolicitation],
    *,
    downloads_root: Path,
    settings,
    reprocess_existing_pdfs: bool,
) -> list[PortalSolicitation]:
    return [
        record
        for record in records
        if _record_can_reuse_existing_pdf_without_detail(
            record,
            downloads_root=downloads_root,
            settings=settings,
            reprocess_existing_pdfs=reprocess_existing_pdfs,
        )
    ]


def _prioritize_batch_fast_selected_records(
    eligible_records: list[PortalSolicitation],
    *,
    limit: int,
    downloads_root: Path,
    settings,
    reprocess_existing_pdfs: bool,
) -> list[PortalSolicitation]:
    if not _is_batch_fast_mode(settings):
        return eligible_records if limit <= 0 else eligible_records[:limit]
    if getattr(settings, "op5_target_protocols", set()) or set():
        return eligible_records if limit <= 0 else eligible_records[:limit]

    local_records = _batch_fast_local_reusable_records(
        eligible_records,
        downloads_root=downloads_root,
        settings=settings,
        reprocess_existing_pdfs=reprocess_existing_pdfs,
    )
    local_protocols = {record.protocol for record in local_records}
    detail_records = [
        record for record in eligible_records if record.protocol not in local_protocols
    ]
    ordered = [*local_records, *detail_records]
    return ordered if limit <= 0 else ordered[:limit]


def _metadata_has_completion_value(metadata_path: Path) -> bool:
    metadata = _load_existing_download_metadata(Path(metadata_path))
    return bool(
        metadata.get("completion_date")
        or metadata.get("completion_date_raw")
        or metadata.get("completion_date_normalized")
        or metadata.get("completion_extraction_status")
        in POINT_OF_CONNECTION_NO_DATE_STATUSES
    )


def _record_has_completion_value(record: PortalSolicitation) -> bool:
    return parse_date(record.completion_date) is not None


def _batch_fast_cached_local_collection(
    *,
    settings,
    downloads_root: Path,
    limit: int | None,
    reprocess_existing_pdfs: bool,
    state_store=None,
    skip_already_completed: bool = True,
) -> dict | None:
    if not _is_batch_fast_mode(settings):
        return None
    if int(limit or 0) <= 0:
        return None
    if getattr(settings, "op5_target_protocols", set()) or set():
        return None
    logs_dir = getattr(settings, "logs_dir_path", None)
    if logs_dir is None:
        return None
    cache = load_eligibility_cache(
        Path(logs_dir) / "op5_portal_eligibility_cache.json",
        requested_limit=int(limit or 0),
        reconciliation_mode="batch_fast",
        ttl_minutes=int(getattr(settings, "OP5_ELIGIBILITY_CACHE_TTL_MINUTES", 30) or 30),
    )
    if not cache.get("cache_hit"):
        return None

    reusable_records: list[PortalSolicitation] = []
    skipped_completed: list[str] = []
    for item in cache.get("records") or []:
        record = PortalSolicitation(
            protocol=str(item.get("protocol") or "").strip(),
            status=str(item.get("status") or "").strip(),
            entry_date=str(item.get("entry_date") or "").strip() or None,
            completion_date=str(item.get("completion_date") or "").strip() or None,
            page_number=item.get("page_number"),
            row_index=item.get("row_index"),
            selection_reason=str(item.get("selection_reason") or "").strip() or None,
        )
        if not record.protocol:
            continue
        if skip_already_completed and _state_should_skip_completed(
            state_store, record.protocol, settings
        ):
            skipped_completed.append(record.protocol)
            continue
        existing_pdf = find_existing_connection_budget_pdf(record.protocol, downloads_root)
        if not _can_reuse_existing_pdf_without_detail(
            record=record,
            existing_pdf=existing_pdf,
            downloads_root=downloads_root,
            reprocess_existing_pdfs=reprocess_existing_pdfs,
            require_completion_metadata=bool(
                getattr(settings, "APPLY_EXCEL", False)
                and _is_batch_fast_mode(settings)
            ),
        ):
            continue
        reusable_records.append(record)
        if len(reusable_records) >= int(limit or 0):
            break

    if len(reusable_records) < int(limit or 0):
        return None

    return {
        "pages_read": 0,
        "total_rows": len(reusable_records),
        "total_completed": len(reusable_records),
        "completed_records": reusable_records,
        "duplicates_skipped": [],
        "pagination_warnings": [],
        "pagination_enabled": False,
        "pagination_complete": None,
        "last_page_confirmed": None,
        "last_page_number": None,
        "next_page_available_after_stop": None,
        "pagination_safety_cap": None,
        "pages_visited": [],
        "pagination_stop_reason": "eligibility_cache_hit",
        "pagination_next_found": False,
        "pagination_click_attempts": 0,
        "pagination_mode": "eligibility_cache",
        "pagination_current_page": None,
        "pagination_target_page": None,
        "pagination_numeric_links_found": [],
        "pagination_diagnostics": [],
        "eligibility_cache_hit": True,
        "eligibility_cache_path": cache.get("cache_path"),
        "eligibility_cache_structural_hash": cache.get("structural_hash"),
        "eligibility_cache_skipped_completed": skipped_completed,
    }


def download_completed_budgets_from_current_page(
    page,
    downloads_root: Path | None = None,
    max_completed: int | None = None,
    reprocess_existing_pdfs: bool = False,
    process_existing_after_skip: bool = True,
    listing_url: str | None = None,
    state_store=None,
    skip_already_completed: bool = True,
    reconciliation_callback=None,
    settings=None,
) -> dict:
    settings = settings or get_settings()
    downloads_root = Path(downloads_root or settings.downloads_dir_path)
    limit = settings.MAX_COMPLETED_TO_PROCESS if max_completed is None else max_completed
    started_at = datetime.now()
    listing_url = listing_url or page.url
    summary = _initial_download_summary(
        started_at,
        downloads_root,
        limit,
        reprocess_existing_pdfs,
        process_existing_after_skip,
    )
    summary["enable_portal_pagination"] = bool(settings.ENABLE_PORTAL_PAGINATION)
    summary["max_portal_pages"] = int(settings.MAX_PORTAL_PAGES or 0)
    summary["skip_already_completed"] = bool(skip_already_completed)
    summary["target_protocols"] = sorted(
        getattr(settings, "op5_target_protocols", set()) or []
    )
    logger.info(
        "Configuracao efetiva do lote CDP: "
        f"ENABLE_PORTAL_PAGINATION={summary['enable_portal_pagination']}; "
        f"MAX_PORTAL_PAGES={summary['max_portal_pages']}; "
        f"MAX_COMPLETED_TO_PROCESS={limit}; "
        f"SKIP_ALREADY_COMPLETED={summary['skip_already_completed']}."
    )

    if _page_looks_access_denied(page):
        summary["run_error"] = (
            "Access Denied detectado no Portal GD. "
            f"{manual_cdp_listing_recovery_message()}"
        )
        summary["aborted"] = True
        summary["abort_reason"] = summary["run_error"]
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.error(summary["run_error"])
        _refresh_download_totals(summary)
        summary["total_cdp_errors"] = 1
        summary["total_errors"] = 1
        return summary
    if _page_looks_login_required(page):
        summary["run_error"] = portal_login_required_message()
        summary["aborted"] = True
        summary["abort_reason"] = summary["run_error"]
        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.error(summary["run_error"])
        _refresh_download_totals(summary)
        summary["total_cdp_errors"] = 1
        summary["total_errors"] = 1
        return summary

    batch_fast_mode = _is_batch_fast_mode(settings)
    collection = _batch_fast_cached_local_collection(
        settings=settings,
        downloads_root=downloads_root,
        limit=limit,
        reprocess_existing_pdfs=reprocess_existing_pdfs,
        state_store=state_store,
        skip_already_completed=skip_already_completed,
    )
    batch_fast_light_scan = None

    if collection is None and settings.ENABLE_PORTAL_PAGINATION:
        anchor_navigation = (
            _navigate_to_batch_fast_completed_index_anchor(
                page,
                settings,
                state_store=state_store,
                skip_already_completed=skip_already_completed,
            )
            if batch_fast_mode
            else None
        )
        if anchor_navigation is not None:
            summary["completed_index_anchor_navigation"] = anchor_navigation
        if anchor_navigation is not None and anchor_navigation.get("success"):
            summary["pagination_initial_active_page"] = anchor_navigation.get(
                "active_page_before"
            )
            summary["pagination_reset_to_first_page"] = {
                "success": True,
                "status": "skipped_reset_to_first_page_due_to_completed_index_anchor",
                "initial_active_page": anchor_navigation.get("active_page_before"),
                "active_page_after": anchor_navigation.get("active_page_after"),
                "anchor_navigation": anchor_navigation,
            }
        else:
            reset_result = _ensure_listing_starts_on_page_one_with_optional_url(
                page,
                listing_url,
            )
            summary["pagination_initial_active_page"] = reset_result.get(
                "initial_active_page"
            )
            summary["pagination_reset_to_first_page"] = reset_result
            if not reset_result["success"]:
                message = (
                    "cannot_confirm_active_page_in_production"
                    if not settings.DRY_RUN
                    else "pagination_could_not_reset_to_first_page"
                )
                detail = reset_result.get("error") or reset_result.get("status")
                summary["run_error"] = f"{message}: {detail}"
                summary["aborted"] = True
                summary["abort_reason"] = summary["run_error"]
                summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
                logger.error(summary["run_error"])
                _refresh_download_totals(summary)
                return summary
        try:
            batch_fast_light_scan = _batch_fast_light_scan_first_pending_page(
                page,
                settings,
                state_store=state_store,
                skip_already_completed=skip_already_completed,
            )
        except Exception as exc:
            logger.warning(f"Varredura leve OP5 indisponivel; usando coleta padrao: {exc}")
            batch_fast_light_scan = {
                "success": False,
                "status": "light_scan_unavailable_fallback_to_standard_collection",
                "pages_visited": [],
                "completed_pages_skipped_already_completed": [],
                "page_states": [],
                "warnings": ["light_scan_unavailable_fallback_to_standard_collection"],
                "diagnostics": [{"error": str(exc)}],
                "target_page_number": None,
            }
        if batch_fast_light_scan is not None:
            summary["batch_fast_light_scan_status"] = batch_fast_light_scan.get(
                "status"
            )
            summary["batch_fast_light_scan_pages_visited"] = list(
                batch_fast_light_scan.get("pages_visited") or []
            )
            summary["batch_fast_light_scan_completed_pages_skipped"] = list(
                batch_fast_light_scan.get(
                    "completed_pages_skipped_already_completed"
                )
                or []
            )
            summary["batch_fast_light_scan_first_pending_page"] = (
                batch_fast_light_scan.get("target_page_number")
            )
            summary["batch_fast_light_scan_stop_reason"] = batch_fast_light_scan.get(
                "status"
            )

    active_reconciliation_callback = None if batch_fast_mode else reconciliation_callback
    if collection is not None:
        summary["eligibility_cache_hit"] = True
        summary["eligibility_cache_path"] = collection.get("eligibility_cache_path")
        summary["eligibility_cache_structural_hash"] = collection.get(
            "eligibility_cache_structural_hash"
        )
    elif batch_fast_mode:
        collection_kwargs = {
            "state_store": state_store,
            "max_completed": limit,
            "skip_already_completed": skip_already_completed,
        }
        remaining_page_budget = _batch_fast_remaining_collection_page_budget(
            batch_fast_light_scan,
            settings,
        )
        if remaining_page_budget is not None:
            collection_kwargs["max_portal_pages"] = remaining_page_budget
        collection = _collect_completed_listing_rows_across_pages(
            page,
            settings,
            **collection_kwargs,
        )
        _merge_batch_fast_light_scan_collection(collection, batch_fast_light_scan)
    else:
        collection = _collect_completed_listing_rows_across_pages(page, settings)
    if (
        collection["pages_read"] == 0
        and collection.get("pagination_stop_reason") != "eligibility_cache_hit"
    ) or collection["total_rows"] == 0:
        raise RuntimeError("A pagina atual nao contem a tabela 'Minhas Solicitacoes'.")
    listing_url = listing_url or page.url

    completed_records = collection["completed_records"]
    reconciliation_context = {
        "pages_read": collection["pages_read"],
        "rows_read": collection["total_rows"],
        "completed_records": collection["total_completed"],
        "pagination_complete": collection.get("pagination_complete"),
        "last_page_confirmed": collection.get("last_page_confirmed"),
        "last_page_number": collection.get("last_page_number"),
        "next_page_available_after_stop": collection.get(
            "next_page_available_after_stop"
        ),
        "pagination_safety_cap": collection.get("pagination_safety_cap"),
        "pages_visited": collection.get("pages_visited", []),
        "pagination_stop_reason": collection.get("pagination_stop_reason"),
        "pagination_enabled": collection.get("pagination_enabled", False),
        "pagination_mode": collection.get("pagination_mode"),
    }
    reconciliation_pre_limit = None
    if active_reconciliation_callback is not None:
        logger.info(
            "Reconciliação global Portal x planilha iniciada antes do limite: "
            f"{len(completed_records)} registros concluídos."
        )
        reconciliation_pre_limit = active_reconciliation_callback(
            "before_limit",
            completed_records,
            [],
            reconciliation_context,
        )
        logger.info("Reconciliação global Portal x planilha concluída antes do limite.")
    batch_fast_can_continue_with_partial_lot = (
        batch_fast_mode and len(completed_records) > 0
    )
    if collection.get("pagination_complete") is False and not (
        batch_fast_can_continue_with_partial_lot
    ):
        summary.update(
            {
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "aborted": True,
                "abort_reason": "PORTAL_PAGINATION_INCOMPLETE",
                "run_error": (
                    "Reconciliação global não concluída. "
                    "O Portal ainda possui páginas não lidas."
                ),
                "total_pages_read": collection["pages_read"],
                "total_rows": collection["total_rows"],
                "total_completed": collection["total_completed"],
                "total_selected": 0,
                "selected_protocols": [],
                "results": [],
                "pagination_warnings": collection["pagination_warnings"],
                "pagination_enabled": collection.get("pagination_enabled", False),
                "pagination_complete": collection.get("pagination_complete"),
                "last_page_confirmed": collection.get("last_page_confirmed"),
                "last_page_number": collection.get("last_page_number"),
                "next_page_available_after_stop": collection.get(
                    "next_page_available_after_stop"
                ),
                "pagination_safety_cap": collection.get("pagination_safety_cap"),
                "pages_visited": collection.get("pages_visited", []),
                "pagination_stop_reason": collection.get("pagination_stop_reason"),
                "pagination_next_found": collection.get(
                    "pagination_next_found", False
                ),
                "pagination_click_attempts": collection.get(
                    "pagination_click_attempts", 0
                ),
                "pagination_mode": collection.get("pagination_mode"),
                "pagination_current_page": collection.get("pagination_current_page"),
                "pagination_target_page": collection.get("pagination_target_page"),
                "pagination_numeric_links_found": collection.get(
                    "pagination_numeric_links_found", []
                ),
                "pagination_diagnostics": collection.get(
                    "pagination_diagnostics", []
                ),
                "reconciliation": reconciliation_pre_limit,
                "reconciliation_pre_limit": reconciliation_pre_limit,
            }
        )
        _refresh_download_totals(summary)
        logger.error(summary["run_error"])
        return summary
    if state_store:
        logger.info(
            "Registrando protocolos descobertos no estado de retomada: "
            f"{len(completed_records)} registros."
        )
        mark_discovered_many = getattr(state_store, "mark_discovered_many", None)
        mark_discovered = getattr(state_store, "mark_discovered", None)
        if callable(mark_discovered_many):
            mark_discovered_many(completed_records)
        elif callable(mark_discovered):
            for record in completed_records:
                mark_discovered(record)
        else:
            logger.info(
                "State store sem API de registro de descoberta; "
                "mantendo apenas consulta de conclusao local."
            )
        logger.info("Registro de protocolos descobertos concluído.")
    selection = select_eligible_completed_requests(
        completed_requests=completed_records,
        pipeline_state=state_store,
        max_completed_to_process=0 if batch_fast_mode else limit,
        skip_already_completed=skip_already_completed,
        force_reprocess_protocols=settings.force_reprocess_protocols,
        settings=settings,
    )
    selected_records = _prioritize_batch_fast_selected_records(
        selection["selected_records"],
        limit=int(limit or 0),
        downloads_root=downloads_root,
        settings=settings,
        reprocess_existing_pdfs=reprocess_existing_pdfs,
    )
    if batch_fast_mode:
        selection["selected_records"] = selected_records
    if active_reconciliation_callback is not None:
        summary["reconciliation"] = active_reconciliation_callback(
            "after_selection",
            completed_records,
            [record.protocol for record in selected_records],
            reconciliation_context,
        )
        summary["reconciliation_pre_limit"] = reconciliation_pre_limit
    elif batch_fast_mode:
        summary["reconciliation_mode"] = "batch_fast"
        summary["reconciliation"] = {
            "metrics_scope": "BATCH_FAST",
            "set_reconciliation_authoritative": False,
        }
        if collection.get("pagination_complete") is False:
            summary["partial_batch_due_to_pagination"] = True
    skipped_completed = selection["skipped_completed"]
    eligible_records = selection["eligible_records"]
    duplicate_skips = [
        *collection["duplicates_skipped"],
        *selection["duplicates_removed"],
    ]
    summary["total_pages_read"] = collection["pages_read"]
    summary["total_rows"] = collection["total_rows"]
    summary["total_completed"] = collection["total_completed"]
    summary["total_already_completed_in_state"] = len(skipped_completed)
    summary["total_eligible_after_skip"] = len(eligible_records)
    summary["total_force_reprocess"] = selection["total_force_reprocess"]
    summary["pagination_warnings"] = collection["pagination_warnings"]
    summary["pagination_enabled"] = collection.get("pagination_enabled", False)
    summary["pagination_complete"] = collection.get("pagination_complete")
    summary["last_page_confirmed"] = collection.get("last_page_confirmed")
    summary["last_page_number"] = collection.get("last_page_number")
    summary["next_page_available_after_stop"] = collection.get(
        "next_page_available_after_stop"
    )
    summary["pagination_safety_cap"] = collection.get("pagination_safety_cap")
    summary["pages_visited"] = collection.get("pages_visited", [])
    summary["pagination_stop_reason"] = collection.get("pagination_stop_reason")
    summary["pagination_next_found"] = collection.get("pagination_next_found", False)
    summary["pagination_click_attempts"] = collection.get(
        "pagination_click_attempts", 0
    )
    summary["pagination_mode"] = collection.get("pagination_mode")
    summary["pagination_current_page"] = collection.get("pagination_current_page")
    summary["pagination_target_page"] = collection.get("pagination_target_page")
    summary["pagination_numeric_links_found"] = collection.get(
        "pagination_numeric_links_found", []
    )
    summary["pagination_diagnostics"] = collection.get("pagination_diagnostics", [])
    summary["batch_fast_light_scan_status"] = collection.get(
        "batch_fast_light_scan_status",
        summary.get("batch_fast_light_scan_status"),
    )
    summary["batch_fast_light_scan_pages_visited"] = collection.get(
        "batch_fast_light_scan_pages_visited",
        summary.get("batch_fast_light_scan_pages_visited", []),
    )
    summary["batch_fast_light_scan_completed_pages_skipped"] = collection.get(
        "batch_fast_light_scan_completed_pages_skipped",
        summary.get("batch_fast_light_scan_completed_pages_skipped", []),
    )
    summary["batch_fast_light_scan_first_pending_page"] = collection.get(
        "batch_fast_light_scan_first_pending_page",
        summary.get("batch_fast_light_scan_first_pending_page"),
    )
    summary["batch_fast_light_scan_stop_reason"] = collection.get(
        "batch_fast_light_scan_stop_reason",
        summary.get("batch_fast_light_scan_stop_reason"),
    )
    summary["batch_fast_light_scan_page_states"] = collection.get(
        "batch_fast_light_scan_page_states", []
    )
    summary["completed_pages_skipped_already_completed"] = collection.get(
        "completed_pages_skipped_already_completed", []
    )
    summary["total_completed_pages_skipped_already_completed"] = collection.get(
        "total_completed_pages_skipped_already_completed", 0
    )
    summary["listing_context"] = {
        "pages_read": collection["pages_read"],
        "pagination_warnings": collection["pagination_warnings"],
        "pagination_initial_active_page": summary.get(
            "pagination_initial_active_page"
        ),
        "pagination_reset_to_first_page": summary.get("pagination_reset_to_first_page"),
        "pagination_enabled": summary["pagination_enabled"],
        "pagination_complete": summary["pagination_complete"],
        "last_page_confirmed": summary["last_page_confirmed"],
        "last_page_number": summary["last_page_number"],
        "next_page_available_after_stop": summary["next_page_available_after_stop"],
        "pagination_safety_cap": summary["pagination_safety_cap"],
        "pages_visited": summary["pages_visited"],
        "pagination_stop_reason": summary["pagination_stop_reason"],
        "pagination_next_found": summary["pagination_next_found"],
        "pagination_click_attempts": summary["pagination_click_attempts"],
        "pagination_mode": summary["pagination_mode"],
        "pagination_current_page": summary["pagination_current_page"],
        "pagination_target_page": summary["pagination_target_page"],
        "pagination_numeric_links_found": summary["pagination_numeric_links_found"],
        "pagination_diagnostics": summary["pagination_diagnostics"],
        "batch_fast_light_scan_status": summary.get("batch_fast_light_scan_status"),
        "batch_fast_light_scan_pages_visited": summary.get(
            "batch_fast_light_scan_pages_visited", []
        ),
        "batch_fast_light_scan_completed_pages_skipped": summary.get(
            "batch_fast_light_scan_completed_pages_skipped", []
        ),
        "batch_fast_light_scan_first_pending_page": summary.get(
            "batch_fast_light_scan_first_pending_page"
        ),
        "batch_fast_light_scan_stop_reason": summary.get(
            "batch_fast_light_scan_stop_reason"
        ),
        "batch_fast_light_scan_page_states": summary.get(
            "batch_fast_light_scan_page_states", []
        ),
        "completed_pages_skipped_already_completed": summary[
            "completed_pages_skipped_already_completed"
        ],
        "total_completed_pages_skipped_already_completed": summary[
            "total_completed_pages_skipped_already_completed"
        ],
    }
    summary["total_selected"] = len(selected_records)
    summary["selected_protocols"] = [
        record.model_dump(mode="json") for record in selected_records
    ]
    summary["duplicates_skipped"] = duplicate_skips
    summary["already_completed_skipped"] = skipped_completed
    logger.info(f"Total paginas lidas: {summary['total_pages_read']}")
    logger.info(f"Total linhas lidas em todas as paginas: {summary['total_rows']}")
    logger.info(
        f"Total concluidas em todas as paginas: {summary['total_completed']}"
    )
    logger.info(
        "Total ja completed no state: "
        f"{summary['total_already_completed_in_state']}"
    )
    logger.info(
        "Total elegiveis apos skip: "
        f"{summary['total_eligible_after_skip']}"
    )
    logger.info(
        "Total forcados por FORCE_REPROCESS_PROTOCOLS: "
        f"{summary['total_force_reprocess']}"
    )
    logger.info(
        "Total selecionado para processamento: "
        f"{summary['total_selected']} (limite={limit or 'sem limite'})"
    )

    processed_protocols: set[str] = set()
    protocol_not_found_errors = 0
    abort_requested = False
    max_protocol_not_found_errors = int(
        getattr(settings, "MAX_PROTOCOL_NOT_FOUND_ERRORS", 3) or 3
    )
    for record in selected_records:
        if abort_requested:
            break
        result = _download_result_from_record(record)
        listing_page = page
        detail_page = None
        protocol = record.protocol
        append_result = True
        if batch_fast_mode:
            result["op5_selection_scope"] = (
                "target_protocols"
                if (getattr(settings, "op5_target_protocols", set()) or set())
                else "global_batch_fast"
            )
        try:
            previous_entry = _state_entry(state_store, protocol)
            if previous_entry:
                result["previous_state"] = previous_entry.get("status")
                result["previous_last_step"] = previous_entry.get("last_step")
            if state_store:
                _call_state_store(state_store, "mark_selected", record)
                if (
                    skip_already_completed
                    and _state_should_skip_completed(state_store, protocol, settings)
                ):
                    result["download_status"] = "skipped_already_completed"
                    result["skip_reason"] = "skipped_already_completed"
                    result["previous_state"] = "completed"
                    result["previous_last_step"] = _state_last_step(
                        state_store,
                        protocol,
                    )
                    logger.info(
                        f"Protocolo {protocol} pulado: ja concluido no state."
                    )
                    continue
                _call_state_store(
                    state_store,
                    "update_protocol",
                    protocol, status="in_progress", last_step="selected"
                )

            if protocol in processed_protocols:
                summary["duplicates_skipped"].append(
                    {
                        "protocol": protocol,
                        "client_name": record.client_name,
                        "status": record.status,
                        "skip_status": "skipped_duplicate_in_run",
                        "page_number": record.page_number,
                        "row_index": record.row_index,
                        "selection_reason": "duplicate_removed",
                    }
                )
                result["download_status"] = "skipped_duplicate_in_run"
                result["skip_reason"] = "skipped_duplicate_in_run"
                result["selection_reason"] = "duplicate_removed"
                append_result = False
                logger.warning(
                    f"Protocolo duplicado ignorado na mesma execucao: {protocol}"
                )
                continue

            processed_protocols.add(protocol)
            logger.info(f"Processando protocolo concluido via CDP: {protocol}")
            existing_pdf = find_existing_connection_budget_pdf(
                protocol, downloads_root
            )
            if existing_pdf is not None:
                result["existing_pdf_path"] = str(existing_pdf)
            if (
                existing_pdf is not None
                and _can_reuse_existing_pdf_without_detail(
                    record=record,
                    existing_pdf=existing_pdf,
                    downloads_root=downloads_root,
                    reprocess_existing_pdfs=reprocess_existing_pdfs,
                    require_completion_metadata=bool(
                        settings.APPLY_EXCEL and _is_batch_fast_mode(settings)
                    ),
                )
            ):
                _reuse_existing_pdf_without_detail(
                    record=record,
                    existing_pdf=existing_pdf,
                    downloads_root=downloads_root,
                    result=result,
                    process_existing_after_skip=process_existing_after_skip,
                    state_store=state_store,
                )
                logger.info(
                    "Detalhe do portal pulado porque ja existe PDF e metadata "
                    f"validos para o protocolo {protocol}: {existing_pdf}"
                )
                continue
            origin_navigation = ensure_request_origin_page(
                listing_page,
                record,
                listing_url,
                allow_active_navigation=False,
            )
            _apply_origin_page_navigation_result(result, origin_navigation)
            if not origin_navigation["success"]:
                if _origin_navigation_failure_is_skippable(origin_navigation):
                    result["download_status"] = "skipped_origin_page_unavailable"
                    result["skip_reason"] = "origin_page_beyond_last_page"
                    result["selected_for_processing"] = False
                    result["processing_reason"] = "origin_page_beyond_last_page"
                    result["origin_page_unavailable"] = True
                    logger.warning(
                        "Protocolo ignorado porque a pagina de origem congelada "
                        "nao esta mais disponivel no Portal: "
                        f"protocol={protocol}; page={record.page_number}; "
                        f"reason={origin_navigation.get('error')}"
                    )
                    continue
                result["download_status"] = "cdp_error"
                result["cdp_error"] = origin_navigation["error"]
                _log_origin_navigation_failure(
                    result["cdp_error"],
                    origin_navigation.get("status"),
                )
                if (
                    origin_navigation.get("status")
                    == "protocol_not_found_on_origin_page"
                ):
                    protocol_not_found_errors += 1
                    if protocol_not_found_errors >= max_protocol_not_found_errors:
                        summary["run_error"] = (
                            "max_protocol_not_found_errors_reached: "
                            f"{protocol_not_found_errors}"
                        )
                        summary["aborted"] = True
                        summary["abort_reason"] = summary["run_error"]
                        logger.error(summary["run_error"])
                        abort_requested = True
                continue
            page = listing_page
            current_row = origin_navigation["row"]

            current_record = current_row["record"]
            result["status"] = current_record.status
            if not is_completed_status(current_record.status):
                if _is_cancelled_status(current_record.status):
                    result["download_status"] = "skipped_cancelled"
                    result["skip_reason"] = "status_cancelled"
                    result["selected_for_processing"] = False
                    result["processing_reason"] = "status_cancelled"
                    logger.info(
                        "Protocolo cancelado ignorado sem abrir detalhe: "
                        f"{protocol}."
                    )
                    continue
                result["download_status"] = "cdp_error"
                result["cdp_error"] = (
                    f"Protocolo {protocol} nao esta mais concluido. "
                    f"Status atual: {current_record.status}"
                )
                logger.error(result["cdp_error"])
                continue

            before_pages = list(listing_page.context.pages)
            result["abriu_detalhe"] = True
            result["url_antes_detalhe"] = listing_page.url
            click_follow_eye_button(
                current_row["row_locator"],
                current_row["action_cell_index"],
            )
            listing_page.wait_for_timeout(1_000)
            new_pages = [
                candidate
                for candidate in listing_page.context.pages
                if candidate not in before_pages
            ]
            detail_page = new_pages[-1] if new_pages else listing_page
            wait_detail_loaded(detail_page, protocol)
            result["url_depois_detalhe"] = detail_page.url

            detail_header = extract_detail_header(detail_page)
            result["detail_protocol"] = (
                detail_header.get("detail_protocol") or protocol
            )
            result["detail_client_name"] = (
                detail_header.get("detail_client_name") or record.client_name
            )
            result["is_completed"] = detail_has_completed_status(
                detail_page
            ) or is_completed_status(current_record.status)
            completion_extraction = extract_point_of_connection_completion(
                detail_page,
                protocol=protocol,
            )
            result.update(completion_extraction)
            result["completion_date"] = completion_extraction["completion_date_raw"]

            protocol_dir = downloads_root / sanitize_filename(protocol)
            metadata_path = save_download_metadata(
                protocol_dir,
                {
                    **result,
                    "protocol": protocol,
                    "client_name": result.get("detail_client_name")
                    or record.client_name,
                    "entry_date": record.entry_date,
                    "completion_date": result.get("completion_date"),
                    "completion_date_raw": result.get("completion_date_raw"),
                    "completion_date_normalized": result.get("completion_date_normalized"),
                    "completion_source_stage": result.get("completion_source_stage"),
                    "completion_source_selector": result.get("completion_source_selector"),
                    "completion_extraction_status": result.get("completion_extraction_status"),
                },
            )
            result["metadata_path"] = str(metadata_path)
            if state_store:
                _call_state_store(
                    state_store,
                    "update_section",
                    protocol,
                    "metadata",
                    {"exists": True, "path": str(metadata_path)},
                    last_step="metadata_saved",
                )

            if existing_pdf is not None and not reprocess_existing_pdfs:
                result["skipped_download"] = True
                result["skip_reason"] = "existing_pdf"
                result["has_connection_budget"] = True
                result["download_status"] = "existing_pdf_after_skip"
                if _is_valid_pdf(existing_pdf) and state_store:
                    _call_state_store(
                        state_store,
                        "update_section",
                        protocol,
                        "download",
                        {
                            "status": "existing_pdf_after_skip",
                            "pdf_exists": True,
                            "pdf_path": str(existing_pdf),
                        },
                        last_step="pdf_reused",
                    )
                if process_existing_after_skip and _is_valid_pdf(existing_pdf):
                    result["process_pdf_path"] = str(existing_pdf)
                    result["selected_for_processing"] = True
                    result["processing_reason"] = "existing_pdf_after_skip"
                elif not _is_valid_pdf(existing_pdf):
                    result["process_pdf_path"] = None
                    result["selected_for_processing"] = False
                    result["download_status"] = "download_error"
                    result["download_error"] = f"PDF existente invalido: {existing_pdf}"
                    if state_store:
                        _call_state_store(
                            state_store,
                            "add_error",
                            protocol, "download", result["download_error"]
                        )
                logger.info(
                    "Download pulado porque ja existe PDF para o protocolo "
                    f"{protocol}: {existing_pdf}"
                )
            else:
                budget_target = find_connection_budget_target(detail_page)
                if budget_target is None:
                    result["has_connection_budget"] = False
                    result["download_status"] = "budget_unavailable"
                    result["budget_unavailable"] = True
                    result["selected_for_processing"] = True
                    result["processing_reason"] = "budget_unavailable_metadata_only"
                    result["skip_reason"] = "budget_unavailable"
                    result["archive_status"] = "skipped_budget_unavailable"
                    result["process_pdf_path"] = None
                    if state_store:
                        _call_state_store(
                            state_store,
                            "update_section",
                            protocol,
                            "download",
                            {
                                "status": "budget_unavailable",
                                "pdf_exists": False,
                                "reason": "connection_budget_link_not_found",
                            },
                            last_step="budget_unavailable",
                        )
                    logger.warning(
                        "Orcamento de Conexao nao encontrado para "
                        f"{protocol}."
                    )
                else:
                    result["has_connection_budget"] = True
                    pdf_path = download_connection_budget(
                        detail_page,
                        protocol,
                        downloads_root,
                        target=budget_target,
                    )
                    if pdf_path is None:
                        raise RuntimeError(
                            "Botao/link 'Orcamento de Conexao' nao encontrado."
                        )

                    result["downloaded_pdf_path"] = str(pdf_path)
                    if _is_valid_pdf(pdf_path):
                        result["download_status"] = "downloaded"
                        result["process_pdf_path"] = str(pdf_path)
                        result["selected_for_processing"] = True
                        result["processing_reason"] = "downloaded"
                        generation_data = extract_generation_data(pdf_path)
                        result["module_excel"] = generation_data.format_module_for_excel()
                        result["inverter_excel"] = (
                            generation_data.format_inverter_for_excel()
                        )
                        result["generation_data"] = generation_data.model_dump(mode="json")
                        result["multiple_module_models"] = (
                            generation_data.multiple_module_models()
                        )
                        result["multiple_inverter_models"] = (
                            generation_data.multiple_inverter_models()
                        )
                        result["module_pairs_count"] = generation_data.module_pairs_count()
                        result["inverter_pairs_count"] = (
                            generation_data.inverter_pairs_count()
                        )
                        result["equipment_parse_warning"] = (
                            generation_data.equipment_parse_warning
                        )
                        if state_store:
                            _call_state_store(
                                state_store,
                                "update_section",
                                protocol,
                                "download",
                                {
                                    "status": "downloaded",
                                    "pdf_exists": True,
                                    "pdf_path": str(pdf_path),
                                },
                                last_step="pdf_downloaded",
                            )
                        logger.info(
                            f"PDF e dados tecnicos processados para {protocol}."
                        )
                    else:
                        result["download_status"] = "download_error"
                        result["download_error"] = f"PDF baixado invalido: {pdf_path}"
                        if state_store:
                            _call_state_store(
                                state_store,
                                "add_error",
                                protocol, "download", result["download_error"]
                            )
        except DownloadNotProducedError as exc:
            logger.warning(
                "Orcamento de Conexao indisponivel para download; "
                f"protocolo={protocol}; motivo={exc}"
            )
            result["has_connection_budget"] = False
            result["download_status"] = "budget_unavailable"
            result["budget_unavailable"] = True
            result["selected_for_processing"] = True
            result["processing_reason"] = "budget_unavailable_metadata_only"
            result["skip_reason"] = "budget_unavailable"
            result["budget_unavailable_reason"] = str(exc)
            result["process_pdf_path"] = None
            result["cdp_error"] = None
            result["download_error"] = None
            if state_store:
                _call_state_store(
                    state_store,
                    "update_section",
                    protocol,
                    "download",
                    {
                        "status": "budget_unavailable",
                        "pdf_exists": False,
                        "reason": str(exc),
                    },
                    last_step="budget_unavailable",
                )
        except Exception as exc:
            logger.exception(f"Erro ao processar protocolo {protocol}: {exc}")
            result["download_status"] = (
                result.get("download_status")
                if result.get("process_pdf_path")
                else "cdp_error"
            )
            result["cdp_error"] = str(exc)
            if state_store:
                _call_state_store(
                    state_store,
                    "add_error",
                    protocol,
                    "cdp",
                    result["cdp_error"],
                )
        finally:
            if detail_page is not None:
                try:
                    page, listing_recovery = _return_to_listing_after_detail(
                        detail_page,
                        listing_page,
                        listing_url,
                        allow_active_navigation=_result_is_metadata_only_for_processing(
                            result
                        ),
                    )
                    _apply_listing_recovery_result(result, listing_recovery)
                    if not listing_recovery["success"]:
                        raise RuntimeError(listing_recovery["error"])
                except Exception as return_exc:
                    message = str(return_exc)
                    logger.error(
                        "Nao foi possivel garantir retorno a listagem apos "
                        f"protocolo {protocol}: {message}"
                    )
                    abort_requested = True
                    if _result_has_valid_pdf_for_processing(result):
                        summary["partial_batch_due_to_listing_recovery"] = True
                        summary["abort_reason"] = (
                            "stopped_after_failed_return_to_listing"
                        )
                        result["retorno_listagem_status"] = (
                            "stopped_after_failed_return_to_listing"
                        )
                        result["metodo_retorno_listagem"] = (
                            result.get("metodo_retorno_listagem") or "failed"
                        )
                        result["navigation_warning"] = message
                    else:
                        summary["run_error"] = "failed_return_to_listing"
                        summary["aborted"] = True
                        summary["abort_reason"] = "failed_return_to_listing"
                        result["retorno_listagem_status"] = "failed_return_to_listing"
                        result["metodo_retorno_listagem"] = (
                            result.get("metodo_retorno_listagem") or "failed"
                        )
                        result["navigation_error"] = message
                        result["cdp_error"] = result.get("cdp_error") or message
                        if not result.get("download_status"):
                            result["download_status"] = "cdp_error"
                        if state_store:
                            _call_state_store(
                                state_store,
                                "add_error",
                                protocol,
                                "navigation",
                                message,
                            )

            if result.get("download_status") == "pending":
                result["download_status"] = "cdp_error" if result.get("cdp_error") else "skipped"
            result["error"] = result.get("download_error") or result.get("cdp_error")
            if not _result_has_valid_pdf_for_processing(
                result
            ) and not _result_is_metadata_only_for_processing(result):
                result["process_pdf_path"] = None
                result["selected_for_processing"] = False
            if append_result:
                summary["results"].append(result)
                _refresh_download_totals(summary)

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _refresh_download_totals(summary)
    return summary


def _initial_download_summary(
    started_at: datetime,
    downloads_root: Path,
    max_completed: int,
    reprocess_existing_pdfs: bool,
    process_existing_after_skip: bool,
) -> dict:
    return {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": None,
        "downloads_root": str(downloads_root),
        "max_completed_to_process": max_completed,
        "enable_portal_pagination": None,
        "max_portal_pages": None,
        "skip_already_completed": None,
        "reprocess_existing_pdfs": reprocess_existing_pdfs,
        "process_existing_after_skip": process_existing_after_skip,
        "total_pages_read": 0,
        "total_rows": 0,
        "total_completed": 0,
        "total_already_completed_in_state": 0,
        "total_eligible_after_skip": 0,
        "total_force_reprocess": 0,
        "total_selected": 0,
        "total_processed": 0,
        "total_downloaded": 0,
        "total_existing_reused": 0,
        "total_skipped_existing": 0,
        "total_skipped_duplicate": 0,
        "total_skipped_origin_page_unavailable": 0,
        "total_for_processing": 0,
        "total_sent_to_processing": 0,
        "total_metadata_only_for_processing": 0,
        "total_budget_unavailable": 0,
        "total_cdp_errors": 0,
        "total_download_errors": 0,
        "total_errors": 0,
        "run_error": None,
        "aborted": False,
        "abort_reason": None,
        "selected_protocols": [],
        "duplicates_skipped": [],
        "already_completed_skipped": [],
        "pagination_warnings": [],
        "pagination_enabled": False,
        "pagination_stop_reason": None,
        "pagination_next_found": False,
        "pagination_click_attempts": 0,
        "pagination_mode": None,
        "pagination_initial_active_page": None,
        "pagination_reset_to_first_page": None,
        "pagination_current_page": None,
        "pagination_target_page": None,
        "pagination_numeric_links_found": [],
        "pagination_diagnostics": [],
        "results": [],
    }


def _download_result_from_record(record: PortalSolicitation) -> dict:
    return {
        "protocol": record.protocol,
        "client_name": record.client_name,
        "status": record.status,
        "consumer_unit_code": record.consumer_unit_code,
        "address": record.address,
        "entry_date": record.entry_date,
        "entry_date_raw": record.entry_date,
        "page_number": record.page_number,
        "row_index": record.row_index,
        "selection_reason": record.selection_reason,
        "detail_protocol": None,
        "detail_client_name": None,
        "is_completed": False,
        "completion_date": None,
        "completion_date_raw": None,
        "completion_date_normalized": None,
        "completion_source_stage": None,
        "completion_source_selector": None,
        "completion_extraction_status": None,
        "has_connection_budget": None,
        "download_status": "pending",
        "existing_pdf_path": None,
        "skipped_download": False,
        "skip_reason": None,
        "downloaded_pdf_path": None,
        "process_pdf_path": None,
        "selected_for_processing": False,
        "processing_reason": None,
        "metadata_path": None,
        "module_excel": None,
        "inverter_excel": None,
        "generation_data": None,
        "multiple_module_models": False,
        "multiple_inverter_models": False,
        "module_pairs_count": 0,
        "inverter_pairs_count": 0,
        "equipment_parse_warning": None,
        "previous_state": None,
        "previous_last_step": None,
        "abriu_detalhe": False,
        "motivo_nao_abriu_detalhe": None,
        "retorno_listagem_status": None,
        "metodo_retorno_listagem": None,
        "origin_page_navigation": None,
        "origin_page_navigation_status": None,
        "protocol_found_on_origin_page": None,
        "url_antes_detalhe": None,
        "url_depois_detalhe": None,
        "url_apos_retorno": None,
        "cdp_error": None,
        "download_error": None,
        "navigation_error": None,
        "error": None,
    }


def _reuse_existing_pdf_without_detail(
    record: PortalSolicitation,
    existing_pdf: Path,
    downloads_root: Path,
    result: dict,
    process_existing_after_skip: bool,
    state_store=None,
) -> dict:
    protocol = record.protocol
    result["abriu_detalhe"] = False
    result["motivo_nao_abriu_detalhe"] = "skipped_existing_pdf_no_detail"
    result["retorno_listagem_status"] = "not_needed"
    result["metodo_retorno_listagem"] = "not_opened"
    result["url_apos_retorno"] = result.get("url_antes_detalhe")
    result["skipped_download"] = True
    result["skip_reason"] = "existing_pdf"
    result["has_connection_budget"] = True
    result["download_status"] = "existing_pdf_after_skip"
    result["existing_pdf_path"] = str(existing_pdf)

    protocol_dir = downloads_root / sanitize_filename(protocol)
    metadata_path = find_existing_download_metadata(protocol, downloads_root)
    if metadata_path is None:
        metadata_path = save_download_metadata(
            protocol_dir,
            {
                "protocol": protocol,
                "client_name": record.client_name,
                "status": record.status,
                "entry_date": record.entry_date,
                "entry_date_raw": record.entry_date,
                "consumer_unit_code": record.consumer_unit_code,
                "address": record.address,
                "op5_selection_scope": result.get("op5_selection_scope"),
            },
        )
        result["metadata_created_from_listing"] = True
    else:
        result["metadata_created_from_listing"] = False
    result["metadata_path"] = str(metadata_path)
    metadata = _load_existing_download_metadata(metadata_path)
    if metadata:
        for key in (
            "detail_protocol",
            "detail_client_name",
            "completion_date",
            "completion_date_raw",
            "completion_date_normalized",
            "completion_source_stage",
            "completion_source_selector",
            "completion_extraction_status",
        ):
            if metadata.get(key):
                result[key] = metadata[key]
        if metadata.get("protocol"):
            result["detail_protocol"] = result.get("detail_protocol") or metadata[
                "protocol"
            ]
        if metadata.get("client_name"):
            result["detail_client_name"] = result.get("detail_client_name") or metadata[
                "client_name"
            ]
    _apply_listing_completion_to_result(record, result)

    if state_store:
        _call_state_store(
            state_store,
            "update_section",
            protocol,
            "metadata",
            {"exists": True, "path": str(metadata_path)},
            last_step="metadata_saved",
        )

    if _is_valid_pdf(existing_pdf):
        if state_store:
            _call_state_store(
                state_store,
                "update_section",
                protocol,
                "download",
                {
                    "status": "existing_pdf_after_skip",
                    "pdf_exists": True,
                    "pdf_path": str(existing_pdf),
                },
                last_step="pdf_reused",
            )
        if process_existing_after_skip:
            result["process_pdf_path"] = str(existing_pdf)
            result["selected_for_processing"] = True
            result["processing_reason"] = "existing_pdf_after_skip"
    else:
        result["process_pdf_path"] = None
        result["selected_for_processing"] = False
        result["download_status"] = "download_error"
        result["download_error"] = f"PDF existente invalido: {existing_pdf}"
        if state_store:
            _call_state_store(
                state_store,
                "add_error",
                protocol,
                "download",
                result["download_error"],
            )

    return result


def _apply_listing_completion_to_result(
    record: PortalSolicitation,
    result: dict,
) -> None:
    if result.get("completion_date_raw") or result.get("completion_date_normalized"):
        return
    parsed = parse_date(record.completion_date)
    if parsed is None:
        return
    raw = str(record.completion_date).strip()
    result["completion_date_raw"] = raw
    result["completion_date"] = parsed.isoformat()
    result["completion_date_normalized"] = parsed.isoformat()
    result["completion_source_stage"] = POINT_OF_CONNECTION_STAGE
    result["completion_source_selector"] = "listing_completion_date"
    result["completion_extraction_status"] = "FOUND_FROM_LISTING"


def _load_existing_download_metadata(metadata_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning(f"Nao foi possivel ler metadata local existente: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _find_listing_row_by_protocol(page, protocol: str) -> dict | None:
    rows = read_current_page_table_with_row_handles(page)
    for row in rows:
        if row["record"].protocol == protocol:
            return row
    return None


def _find_listing_row_in_rows(rows: list[dict], protocol: str) -> dict | None:
    for row in rows:
        if row["record"].protocol == protocol:
            return row
    return None


def get_active_numeric_page(page) -> int | None:
    try:
        value = page.evaluate(
            """
            () => {
              const numberFrom = (value) => {
                const text = (value || "").replace(/\\s+/g, " ").trim();
                const pageMatch = text.match(/\\bPage\\s+(\\d+)\\b/i);
                if (pageMatch) return Number(pageMatch[1]);
                const numbers = text.match(/\\b\\d+\\b/g) || [];
                if (!numbers.length) return null;
                return Number(numbers[numbers.length - 1]);
              };
              const candidates = [
                "a.ui-paginator-page.ui-state-active",
                ".ui-paginator-page.ui-state-active",
                "span.ui-paginator-pages .ui-state-active",
                ".ui-paginator .ui-state-active",
                "[class*='paginator'] .ui-state-active",
                "[class*='pagination'] .ui-state-active"
              ];
              for (const selector of candidates) {
                for (const element of Array.from(document.querySelectorAll(selector))) {
                  const text = [
                    element.innerText,
                    element.textContent,
                    element.getAttribute("aria-label"),
                    element.getAttribute("title")
                  ].filter(Boolean).join(" ");
                  const pageNumber = numberFrom(text);
                  if (pageNumber) return pageNumber;
                }
              }
              return null;
            }
            """
        )
        page_number = int(value) if value else None
        logger.info(f"Pagina numerica ativa detectada: {page_number}")
        return page_number
    except (PlaywrightError, AttributeError, TypeError, ValueError) as exc:
        logger.warning(f"Nao foi possivel detectar pagina numerica ativa: {exc}")
        return None


def ensure_listing_starts_on_page_one(page, listing_url: str | None = None) -> dict:
    result: dict[str, Any] = {
        "success": False,
        "status": None,
        "initial_active_page": None,
        "active_page_after": None,
        "click_result": None,
        "error": None,
    }
    active_page = get_active_numeric_page(page)
    result["initial_active_page"] = active_page
    if active_page is None:
        first_page_probe = _recover_first_page_without_active_indicator(page)
        result["click_result"] = first_page_probe
        if first_page_probe["success"]:
            result.update(
                {
                    "success": True,
                    "status": first_page_probe["status"],
                    "active_page_after": 1,
                    "error": None,
                }
            )
            return result
        listing_recovery = _recover_first_page_after_reset_failure(
            page,
            first_page_probe,
            listing_url=listing_url,
        )
        result["listing_reset_recovery"] = listing_recovery
        result["active_page_after"] = listing_recovery.get("active_page_after")
        if listing_recovery.get("success"):
            result.update(
                {
                    "success": True,
                    "status": "reset_to_first_page_after_listing_recovery",
                    "error": None,
                }
            )
            return result
        result.update(
            {
                "status": "cannot_confirm_active_page",
                "error": (
                    first_page_probe.get("error")
                    or "Nao foi possivel detectar a pagina ativa do paginador."
                ),
            }
        )
        return result
    if active_page == 1:
        result.update(
            {
                "success": True,
                "status": "already_on_first_page",
                "active_page_after": 1,
            }
        )
        return result

    logger.info(f"Pagina ativa inicial e {active_page}; resetando para pagina 1.")
    navigation = navigate_to_numeric_page(page, 1)
    result["click_result"] = navigation
    if _is_execution_context_destroyed_error(navigation.get("error")):
        recovery = _recover_first_page_after_context_destruction(page, navigation)
        result["context_recovery"] = recovery
        result["active_page_after"] = recovery.get("active_page_after")
        if recovery.get("success"):
            result.update(
                {
                    "success": True,
                    "status": "reset_to_first_page_after_context_recovery",
                    "error": None,
                }
            )
            return result
        result.update(
            {
                "status": "pagination_context_destroyed_during_reset",
                "error": recovery.get("error") or navigation.get("error"),
            }
        )
        return result
    active_after = get_active_numeric_page(page)
    result["active_page_after"] = active_after
    if navigation["success"] and active_after == 1:
        result.update({"success": True, "status": "reset_to_first_page"})
        return result

    listing_recovery = _recover_first_page_after_reset_failure(
        page,
        navigation,
        listing_url=listing_url,
    )
    result["listing_reset_recovery"] = listing_recovery
    result["active_page_after"] = listing_recovery.get("active_page_after", active_after)
    if listing_recovery.get("success"):
        result.update(
            {
                "success": True,
                "status": "reset_to_first_page_after_listing_recovery",
                "error": None,
            }
        )
        return result

    result.update(
        {
            "status": "pagination_could_not_reset_to_first_page",
            "error": (
                navigation.get("error")
                or f"Pagina ativa apos reset: {active_after}; esperado: 1."
            ),
        }
    )
    return result


def _ensure_listing_starts_on_page_one_with_optional_url(
    page,
    listing_url: str | None,
) -> dict:
    try:
        return ensure_listing_starts_on_page_one(page, listing_url=listing_url)
    except TypeError as exc:
        if "unexpected keyword argument 'listing_url'" not in str(exc):
            raise
        return ensure_listing_starts_on_page_one(page)


def _recover_first_page_after_reset_failure(
    page,
    navigation: dict,
    *,
    listing_url: str | None = None,
) -> dict:
    result = {
        "success": False,
        "status": "failed_first_page_listing_recovery",
        "method": None,
        "active_page_after": None,
        "error": navigation.get("error") or navigation.get("status"),
    }
    if _click_minhas_solicitacoes_navigation(page):
        result["method"] = "recovered_first_page_by_menu"
        if not _wait_minhas_solicitacoes(page):
            result["error"] = manual_cdp_listing_recovery_message()
            return result
    elif listing_url:
        recovery = _recover_minhas_solicitacoes(page, listing_url)
        result["full_listing_recovery"] = recovery
        if recovery.get("success"):
            result["method"] = recovery.get("method") or "recovered_first_page_by_listing"
        else:
            previous_error = recovery.get("error") or result["error"]
            if not isinstance(previous_error, str):
                previous_error = None
            home_recovery = _recover_listing_by_authenticated_home_icon(
                page,
                listing_url,
                previous_error=previous_error,
            )
            result["authenticated_home_recovery"] = home_recovery
            if not home_recovery.get("success"):
                result["error"] = home_recovery.get("error") or recovery.get("error")
                return result
            result["method"] = (
                home_recovery.get("method") or "recovered_listing_by_authenticated_home_icon"
            )
    else:
        return result
    try:
        active_after = get_active_numeric_page(page)
        rows_after = read_current_page_table_with_row_handles(page)
    except Exception as exc:
        result["error"] = str(exc)
        return result
    result["active_page_after"] = active_after
    if active_after == 1 and rows_after:
        result.update(
            {
                "success": True,
                "status": "recovered_first_page_by_menu",
                "error": None,
            }
        )
        return result
    if active_after is not None and active_after > 1 and rows_after:
        numeric_recovery = navigate_to_numeric_page(page, 1)
        result["numeric_first_page_recovery"] = numeric_recovery
        result["active_page_after"] = numeric_recovery.get("active_page_after")
        if numeric_recovery.get("success") and numeric_recovery.get(
            "active_page_after"
        ) == 1:
            try:
                rows_first_page = read_current_page_table_with_row_handles(page)
            except Exception as exc:
                result["error"] = str(exc)
                return result
            if rows_first_page:
                result.update(
                    {
                        "success": True,
                        "status": "recovered_first_page_by_menu",
                        "method": f"{result['method']}_then_numeric_page",
                        "error": None,
                    }
                )
                return result
    result["error"] = (
        f"Pagina ativa apos reabrir Minhas Solicitacoes: {active_after}; esperado: 1."
    )
    return result


def _is_execution_context_destroyed_error(error: Any) -> bool:
    if not error:
        return False
    error_text = str(error).casefold()
    return (
        "execution context was destroyed" in error_text
        or "cannot find context with specified id" in error_text
    )


def _navigate_to_numeric_page_via_visible_window(
    page,
    *,
    target_page_number: int,
    active_page_before: int,
    signature_before: tuple,
    click_result: dict,
) -> dict:
    target = int(target_page_number)
    active_before = int(active_page_before)
    visible_pages = sorted(
        {
            int(str(link))
            for link in click_result.get("numeric_page_links_found", [])
            if str(link).isdigit()
        }
    )
    intermediate_candidates = [
        page_number
        for page_number in visible_pages
        if target < page_number < active_before
    ]
    diagnostics: list[dict] = []
    result = {
        "success": False,
        "status": "pagination_visible_window_not_available",
        "method": "visible_numeric_window_navigation",
        "target_page_number": target,
        "active_page_before": active_before,
        "active_page_after": active_before,
        "signature_before": signature_before,
        "signature_after": signature_before,
        "visible_pages": visible_pages,
        "intermediate_page_number": None,
        "diagnostics": diagnostics,
        "url_after": _safe_page_url(page),
        "error": None,
    }
    if not intermediate_candidates:
        result["error"] = (
            "Nao ha pagina numerica intermediaria visivel para recalcular o paginador."
        )
        return result

    intermediate = intermediate_candidates[0]
    result["intermediate_page_number"] = intermediate
    first_click = find_and_click_next_numeric_page(page, intermediate - 1)
    diagnostics.append(first_click)
    if (
        not first_click.get("found")
        or not first_click.get("enabled")
        or not first_click.get("clicked")
    ):
        result["status"] = (
            first_click.get("stop_reason") or "pagination_intermediate_click_failed"
        )
        result["error"] = f"Nao foi possivel clicar na pagina intermediaria {intermediate}."
        result["url_after"] = _safe_page_url(page)
        return result

    _wait_after_pagination_click(page)
    rows_intermediate = read_current_page_table_with_row_handles(page)
    signature_intermediate = _listing_rows_signature(rows_intermediate)
    active_intermediate = get_active_numeric_page(page)
    result["active_page_after"] = active_intermediate
    result["signature_after"] = signature_intermediate
    result["url_after"] = _safe_page_url(page)
    if active_intermediate != intermediate:
        result["status"] = "pagination_intermediate_active_page_mismatch"
        result["error"] = (
            f"Pagina ativa apos clique intermediario: {active_intermediate}; "
            f"esperado: {intermediate}."
        )
        return result
    if signature_intermediate == signature_before:
        result["status"] = "pagination_intermediate_click_no_change"
        result["error"] = (
            f"Clique intermediario na pagina {intermediate} nao alterou a tabela."
        )
        return result

    target_click = find_and_click_next_numeric_page(page, target - 1)
    diagnostics.append(target_click)
    if (
        not target_click.get("found")
        or not target_click.get("enabled")
        or not target_click.get("clicked")
    ):
        result["status"] = target_click.get("stop_reason") or "pagination_target_click_failed"
        result["error"] = (
            f"Nao foi possivel clicar na pagina alvo {target} apos janela visivel."
        )
        result["url_after"] = _safe_page_url(page)
        return result

    _wait_after_pagination_click(page)
    rows_after = read_current_page_table_with_row_handles(page)
    signature_after = _listing_rows_signature(rows_after)
    active_after = get_active_numeric_page(page)
    result["active_page_after"] = active_after
    result["signature_after"] = signature_after
    result["url_after"] = _safe_page_url(page)
    if active_after != target:
        result["status"] = "pagination_target_active_page_mismatch"
        result["error"] = (
            f"Pagina ativa apos janela visivel: {active_after}; esperado: {target}."
        )
        return result
    if signature_after == signature_intermediate:
        result["status"] = "pagination_target_click_no_change"
        result["error"] = f"Clique na pagina alvo {target} nao alterou a tabela."
        return result
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_visible_numeric_window",
            "error": None,
        }
    )
    return result


def _navigate_to_numeric_page_forward_via_visible_window(
    page,
    *,
    target_page_number: int,
    active_page_before: int,
    signature_before: tuple,
    click_result: dict,
) -> dict:
    target = int(target_page_number)
    active_before = int(active_page_before)
    visible_pages = sorted(
        {
            int(str(link))
            for link in click_result.get("numeric_page_links_found", [])
            if str(link).isdigit()
        }
    )
    intermediate_candidates = [
        page_number
        for page_number in visible_pages
        if active_before < page_number < target
    ]
    diagnostics: list[dict] = []
    result = {
        "success": False,
        "status": "pagination_forward_visible_window_not_available",
        "method": "forward_visible_numeric_window_navigation",
        "target_page_number": target,
        "active_page_before": active_before,
        "active_page_after": active_before,
        "signature_before": signature_before,
        "signature_after": signature_before,
        "visible_pages": visible_pages,
        "intermediate_page_number": None,
        "diagnostics": diagnostics,
        "url_after": _safe_page_url(page),
        "error": None,
    }
    if target - active_before <= 3 or not intermediate_candidates:
        result["error"] = (
            "Nao ha pagina numerica intermediaria futura visivel para recalcular "
            "o paginador."
        )
        return result

    intermediate = intermediate_candidates[-1]
    if target - intermediate > 5:
        result["error"] = (
            "Janela numerica visivel ainda esta distante da pagina alvo."
        )
        return result
    result["intermediate_page_number"] = intermediate
    first_click = find_and_click_next_numeric_page(page, intermediate - 1)
    diagnostics.append(first_click)
    if (
        not first_click.get("found")
        or not first_click.get("enabled")
        or not first_click.get("clicked")
    ):
        result["status"] = (
            first_click.get("stop_reason") or "pagination_intermediate_click_failed"
        )
        result["error"] = f"Nao foi possivel clicar na pagina intermediaria {intermediate}."
        result["url_after"] = _safe_page_url(page)
        return result

    _wait_after_pagination_click(page)
    rows_intermediate = read_current_page_table_with_row_handles(page)
    signature_intermediate = _listing_rows_signature(rows_intermediate)
    active_intermediate = get_active_numeric_page(page)
    result["active_page_after"] = active_intermediate
    result["signature_after"] = signature_intermediate
    result["url_after"] = _safe_page_url(page)
    if active_intermediate != intermediate:
        result["status"] = "pagination_intermediate_active_page_mismatch"
        result["error"] = (
            f"Pagina ativa apos clique intermediario: {active_intermediate}; "
            f"esperado: {intermediate}."
        )
        return result
    if signature_intermediate == signature_before:
        result["status"] = "pagination_intermediate_click_no_change"
        result["error"] = (
            f"Clique intermediario na pagina {intermediate} nao alterou a tabela."
        )
        return result

    target_click = find_and_click_next_numeric_page(page, target - 1)
    diagnostics.append(target_click)
    if (
        not target_click.get("found")
        or not target_click.get("enabled")
        or not target_click.get("clicked")
    ):
        result["status"] = target_click.get("stop_reason") or "pagination_target_click_failed"
        result["error"] = (
            f"Nao foi possivel clicar na pagina alvo {target} apos janela futura."
        )
        result["url_after"] = _safe_page_url(page)
        return result

    _wait_after_pagination_click(page)
    rows_after = read_current_page_table_with_row_handles(page)
    signature_after = _listing_rows_signature(rows_after)
    active_after = get_active_numeric_page(page)
    result["active_page_after"] = active_after
    result["signature_after"] = signature_after
    result["url_after"] = _safe_page_url(page)
    if active_after != target:
        result["status"] = "pagination_target_active_page_mismatch"
        result["error"] = (
            f"Pagina ativa apos janela futura: {active_after}; esperado: {target}."
        )
        return result
    if signature_after == signature_intermediate:
        result["status"] = "pagination_target_click_no_change"
        result["error"] = f"Clique na pagina alvo {target} nao alterou a tabela."
        return result
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_forward_visible_numeric_window",
            "error": None,
        }
    )
    return result


def _recover_first_page_after_context_destruction(page, navigation: dict) -> dict:
    result = {
        "success": False,
        "status": "pagination_context_destroyed_during_reset",
        "method": "passive_context_revalidation_after_reset",
        "active_page_after": None,
        "error": navigation.get("error"),
    }
    _wait_after_pagination_click(page)
    try:
        active_after = get_active_numeric_page(page)
        result["active_page_after"] = active_after
        rows_after = read_current_page_table_with_row_handles(page)
    except Exception as exc:
        result["error"] = str(exc)
        return result
    if active_after == 1 and rows_after:
        result.update(
            {
                "success": True,
                "status": "reset_to_first_page_after_context_recovery",
                "error": None,
            }
        )
        return result
    result["error"] = (
        navigation.get("error")
        or f"Pagina ativa apos contexto destruido: {active_after}; esperado: 1."
    )
    return result


def _recover_first_page_without_active_indicator(page) -> dict:
    result = {
        "success": False,
        "status": "cannot_confirm_active_page",
        "method": "first_page_without_active_indicator",
        "target_page_number": 1,
        "click_result": None,
        "error": "Nao foi possivel detectar a pagina ativa do paginador.",
    }
    try:
        rows_before = read_current_page_table_with_row_handles(page)
    except Exception as exc:
        result["error"] = str(exc)
        return result
    if not rows_before:
        _wait_after_pagination_click(page)
        try:
            rows_before = read_current_page_table_with_row_handles(page)
        except Exception as exc:
            result["error"] = str(exc)
            return result
        if not rows_before:
            reload_probe = _reload_current_listing_when_rows_are_empty(page)
            result["empty_listing_reload"] = reload_probe
            if reload_probe.get("success"):
                rows_before = reload_probe.get("rows") or []
            else:
                result["error"] = (
                    reload_probe.get("error")
                    or "Listagem visivel sem linhas para confirmar pagina inicial."
                )
                return result

    click_result = find_and_click_next_numeric_page(page, 0)
    result["click_result"] = click_result
    target_links = {str(link) for link in click_result.get("numeric_page_links_found", [])}
    if (
        click_result.get("found")
        and not click_result.get("enabled")
        and str(click_result.get("target_page_number")) == "1"
        and "1" in target_links
    ):
        result.update(
            {
                "success": True,
                "status": "assumed_first_page_active_unconfirmed",
                "error": None,
            }
        )
        return result
    if click_result.get("clicked"):
        _wait_after_pagination_click(page)
        try:
            if read_current_page_table_with_row_handles(page):
                result.update(
                    {
                        "success": True,
                        "status": "reset_to_first_page_unconfirmed_active",
                        "error": None,
                    }
                )
                return result
        except Exception as exc:
            result["error"] = str(exc)
            return result

    result["error"] = (
        click_result.get("stop_reason")
        or "Nao foi possivel confirmar ou selecionar a pagina 1 do paginador."
    )
    return result


def _reload_current_listing_when_rows_are_empty(page) -> dict:
    url_before = _safe_page_url(page)
    result = {
        "success": False,
        "status": "empty_listing_reload_not_available",
        "method": "reload_current_listing",
        "url_before": url_before,
        "url_after": None,
        "rows": [],
        "error": None,
    }
    if not _is_listing_like_portal_url(url_before or ""):
        result["error"] = "Pagina atual nao parece ser a listagem do portal."
        return result
    reload = getattr(page, "reload", None)
    if not callable(reload):
        result["error"] = "Pagina CDP nao suporta reload controlado."
        return result
    try:
        try:
            reload(wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
        except TypeError:
            reload()
        _wait_after_pagination_click(page)
        rows_after = read_current_page_table_with_row_handles(page)
    except Exception as exc:
        result["error"] = str(exc)
        result["url_after"] = _safe_page_url(page)
        return result
    result["url_after"] = _safe_page_url(page)
    result["rows"] = rows_after
    if not rows_after:
        result["status"] = "empty_listing_after_reload"
        result["error"] = "Listagem continuou sem linhas apos reload."
        return result
    result.update(
        {
            "success": True,
            "status": "listing_rows_available_after_reload",
            "error": None,
        }
    )
    return result


def navigate_to_numeric_page(page, target_page_number: int) -> dict:
    target = int(target_page_number or 1)
    result = cdp_navigation_helpers.build_numeric_page_navigation_result(target)
    active_before = get_active_numeric_page(page)
    result["active_page_before"] = active_before
    if active_before == target:
        return cdp_navigation_helpers.mark_numeric_page_navigation_already_on_target(
            result,
            active_page=active_before,
            url_after=_safe_page_url(page),
        )
    if active_before is None:
        has_listing_table = _has_minhas_solicitacoes_table(page)
        active_before, should_return, result = (
            cdp_navigation_helpers.resolve_numeric_page_navigation_active_before(
                result,
                active_before,
                has_listing_table=has_listing_table,
                url_after=None if has_listing_table else _safe_page_url(page),
            )
        )
        if should_return:
            return result

    try:
        rows_before = read_current_page_table_with_row_handles(page)
        signature_before = _listing_rows_signature(rows_before)
        result["signature_before"] = signature_before
        click_result = find_and_click_next_numeric_page(page, target - 1)
        result["click_result"] = click_result
        result["url_after"] = _safe_page_url(page)

        if not click_result.get("found"):
            if target > active_before:
                visible_window = _navigate_to_numeric_page_forward_via_visible_window(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                    click_result=click_result,
                )
                result["visible_window_navigation"] = visible_window
                result["url_after"] = visible_window.get("url_after")
                if visible_window.get("success"):
                    cdp_navigation_helpers.mark_numeric_page_navigation_recovery_success(
                        result,
                        recovery_result=visible_window,
                        method="recovered_listing_by_forward_visible_numeric_window",
                    )
                    return result
                sequential = _navigate_to_numeric_page_sequentially(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                )
                result["sequential_navigation"] = sequential
                result["url_after"] = sequential.get("url_after")
                result["active_page_after"] = sequential.get("active_page_after")
                result["signature_after"] = sequential.get("signature_after")
                if sequential.get("success"):
                    cdp_navigation_helpers.mark_numeric_page_navigation_recovery_success(
                        result,
                        recovery_result=sequential,
                        method="recovered_listing_by_sequential_numeric_page",
                    )
                    return result
                cdp_navigation_helpers.mark_numeric_page_navigation_target_not_found(
                    result,
                    target_page_number=target,
                    click_result=click_result,
                    sequential_result=sequential,
                )
                return result
            if target < active_before:
                visible_window = _navigate_to_numeric_page_via_visible_window(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                    click_result=click_result,
                )
                result["visible_window_navigation"] = visible_window
                result["url_after"] = visible_window.get("url_after")
                if visible_window.get("success"):
                    cdp_navigation_helpers.mark_numeric_page_navigation_recovery_success(
                        result,
                        recovery_result=visible_window,
                        method="recovered_listing_by_visible_numeric_window",
                    )
                    return result
                sequential = _navigate_to_numeric_page_backwards_sequentially(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                )
                result["sequential_navigation"] = sequential
                result["url_after"] = sequential.get("url_after")
                result["active_page_after"] = sequential.get("active_page_after")
                result["signature_after"] = sequential.get("signature_after")
                if sequential.get("success"):
                    cdp_navigation_helpers.mark_numeric_page_navigation_recovery_success(
                        result,
                        recovery_result=sequential,
                        method="recovered_listing_by_reverse_sequential_numeric_page",
                    )
                    return result
                cdp_navigation_helpers.mark_numeric_page_navigation_target_not_found(
                    result,
                    target_page_number=target,
                    click_result=click_result,
                    sequential_result=sequential,
                )
                return result
            cdp_navigation_helpers.mark_numeric_page_navigation_target_not_found(
                result,
                target_page_number=target,
                click_result=click_result,
                sequential_result=None,
            )
            return result
        if not click_result.get("enabled"):
            cdp_navigation_helpers.mark_numeric_page_navigation_direct_click_disabled(
                result,
                target_page_number=target,
                click_result=click_result,
            )
            return result
        if not click_result.get("clicked"):
            cdp_navigation_helpers.mark_numeric_page_navigation_direct_click_not_performed(
                result,
                target_page_number=target,
                click_result=click_result,
            )
            return result

        _wait_after_pagination_click(page)
        rows_after = read_current_page_table_with_row_handles(page)
        signature_after = _listing_rows_signature(rows_after)
        active_after = get_active_numeric_page(page)
        result["signature_after"] = signature_after
        result["active_page_after"] = active_after
        result["url_after"] = _safe_page_url(page)
        if active_after != target:
            if signature_after != signature_before:
                cdp_navigation_helpers.mark_numeric_page_navigation_unconfirmed_active(
                    result
                )
                return result
            if target > active_before:
                sequential = _navigate_to_numeric_page_sequentially(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                    use_numeric_first=False,
                )
                result["sequential_navigation_after_no_change"] = sequential
                result["url_after"] = sequential.get("url_after")
                if sequential.get("success"):
                    cdp_navigation_helpers.mark_numeric_page_navigation_recovery_success(
                        result,
                        recovery_result=sequential,
                        method="recovered_listing_by_sequential_numeric_page",
                    )
                    return result
            if target < active_before:
                sequential = _navigate_to_numeric_page_backwards_sequentially(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                )
                result["sequential_navigation_after_no_change"] = sequential
                result["url_after"] = sequential.get("url_after")
                if sequential.get("success"):
                    cdp_navigation_helpers.mark_numeric_page_navigation_recovery_success(
                        result,
                        recovery_result=sequential,
                        method="recovered_listing_by_reverse_sequential_numeric_page",
                    )
                    return result
            cdp_navigation_helpers.mark_numeric_page_navigation_active_mismatch(
                result,
                target_page_number=target,
                active_after=active_after,
            )
            return result
        if signature_after == signature_before:
            cdp_navigation_helpers.mark_numeric_page_navigation_click_no_change(
                result,
                target_page_number=target,
            )
            return result

        cdp_navigation_helpers.mark_numeric_page_navigation_success(result)
        return result
    except PlaywrightError as exc:
        cdp_navigation_helpers.mark_numeric_page_navigation_click_error(
            result,
            error=exc,
            url_after=_safe_page_url(page),
        )
        return result


def _try_click_target_after_forward_window_shift(
    page,
    *,
    target_page_number: int,
    current_page_number: int,
    previous_signature: tuple,
    click_result: dict,
) -> dict:
    target = int(target_page_number)
    current = int(current_page_number)
    result = {
        "success": False,
        "status": "pagination_forward_window_shift_unavailable",
        "method": "forward_window_shift_target_navigation",
        "target_page_number": target,
        "active_page_before": current,
        "active_page_after": current,
        "signature_after": previous_signature,
        "click_result": None,
        "url_after": _safe_page_url(page),
        "error": None,
    }
    if (
        click_result.get("mode") != "next_button"
        or click_result.get("stop_reason") != "pagination_next_clicked"
        or target <= current
    ):
        result["error"] = "Clique anterior nao abriu uma janela numerica futura."
        return result

    target_click = find_and_click_next_numeric_page(page, target - 1)
    result["click_result"] = target_click
    if (
        not target_click.get("found")
        or not target_click.get("enabled")
        or not target_click.get("clicked")
    ):
        result["status"] = (
            target_click.get("stop_reason")
            or "pagination_forward_window_target_not_found"
        )
        result["error"] = (
            "Janela futura do paginador nao expos a pagina alvo "
            f"{target}."
        )
        result["url_after"] = _safe_page_url(page)
        return result

    _wait_after_pagination_click(page)
    rows_after = read_current_page_table_with_row_handles(page)
    signature_after = _listing_rows_signature(rows_after)
    active_after = get_active_numeric_page(page)
    result["active_page_after"] = active_after
    result["signature_after"] = signature_after
    result["url_after"] = _safe_page_url(page)
    if active_after != target:
        result["status"] = "pagination_forward_window_target_active_mismatch"
        result["error"] = (
            f"Pagina ativa apos janela futura: {active_after}; esperado: {target}."
        )
        return result
    if signature_after == previous_signature:
        result["status"] = "pagination_forward_window_target_click_no_change"
        result["error"] = f"Clique na pagina alvo {target} nao alterou a tabela."
        return result
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_forward_window_target",
            "error": None,
        }
    )
    return result


def _navigate_to_numeric_page_sequentially(
    page,
    *,
    target_page_number: int,
    active_page_before: int,
    signature_before: tuple,
    use_numeric_first: bool = True,
) -> dict:
    current_page_number = int(active_page_before)
    previous_signature = signature_before
    diagnostics: list[dict] = []
    result = {
        "success": False,
        "status": "pagination_numeric_target_not_found",
        "method": "sequential_numeric_page_navigation",
        "target_page_number": int(target_page_number),
        "active_page_before": current_page_number,
        "active_page_after": current_page_number,
        "signature_after": previous_signature,
        "diagnostics": diagnostics,
        "url_after": _safe_page_url(page),
        "error": None,
    }
    while current_page_number < target_page_number:
        if use_numeric_first:
            click_result = find_and_click_next_listing_page(page, current_page_number)
        else:
            next_button = _click_next_listing_page_diagnostic(page)
            click_result = {
                **next_button,
                "mode": "next_button",
                "current_page_number": current_page_number,
                "target_page_number": current_page_number + 1,
                "numeric_page_links_found": [],
                "numeric_page_links_count": 0,
            }
            if next_button.get("clicked"):
                click_result["found"] = True
                click_result["enabled"] = True
                click_result["next_page_available"] = True
                click_result["stop_reason"] = "pagination_next_clicked"
        diagnostics.append(click_result)
        if (
            not click_result.get("found")
            or not click_result.get("enabled")
            or not click_result.get("clicked")
        ):
            stop_reason = (
                click_result.get("stop_reason")
                or "pagination_numeric_target_not_found"
            )
            if stop_reason == "last_page_reached":
                result["status"] = "pagination_target_beyond_last_page"
                result["error"] = (
                    "A pagina "
                    f"{target_page_number} nao esta mais disponivel no Portal. "
                    "A navegacao sequencial encontrou a ultima pagina antes da "
                    f"pagina {current_page_number + 1}."
                )
            else:
                result["status"] = stop_reason
                result["error"] = (
                    "Nao foi possivel navegar sequencialmente ate a pagina "
                    f"{target_page_number}. Parou antes da pagina "
                    f"{current_page_number + 1}. Motivo: {result['status']}"
                )
            result["url_after"] = _safe_page_url(page)
            return result
        _wait_after_pagination_click(page)
        rows_after = read_current_page_table_with_row_handles(page)
        signature_after = _listing_rows_signature(rows_after)
        active_after = get_active_numeric_page(page)
        result["active_page_after"] = active_after
        result["signature_after"] = signature_after
        result["url_after"] = _safe_page_url(page)
        if active_after is None:
            result["status"] = "cannot_confirm_active_page"
            result["error"] = (
                "Nao foi possivel detectar a pagina ativa apos navegacao "
                "sequencial."
            )
            return result
        if active_after <= current_page_number:
            window_target = _try_click_target_after_forward_window_shift(
                page,
                target_page_number=target_page_number,
                current_page_number=current_page_number,
                previous_signature=previous_signature,
                click_result=click_result,
            )
            result["forward_window_shift_navigation"] = window_target
            if window_target.get("success"):
                result.update(
                    {
                        "success": True,
                        "status": "recovered_listing_by_sequential_numeric_page",
                        "active_page_after": window_target.get("active_page_after"),
                        "signature_after": window_target.get("signature_after"),
                        "url_after": window_target.get("url_after"),
                        "error": None,
                    }
                )
                return result
            result["status"] = "pagination_active_page_mismatch"
            result["error"] = (
                f"Pagina ativa apos clique: {active_after}; esperado maior que "
                f"{current_page_number}."
            )
            return result
        if signature_after == previous_signature:
            result["status"] = "pagination_click_no_change"
            result["error"] = (
                "Clique sequencial na pagina numerica nao alterou a tabela: "
                f"{active_after}."
            )
            return result
        current_page_number = active_after
        previous_signature = signature_after

    if current_page_number != target_page_number:
        result["status"] = "pagination_active_page_mismatch"
        result["error"] = (
            f"Pagina ativa apos navegacao sequencial: {current_page_number}; "
            f"esperado: {target_page_number}."
        )
        return result
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_sequential_numeric_page",
            "error": None,
        }
    )
    return result


def _navigate_to_numeric_page_backwards_sequentially(
    page,
    *,
    target_page_number: int,
    active_page_before: int,
    signature_before: tuple,
) -> dict:
    current_page_number = int(active_page_before)
    previous_signature = signature_before
    diagnostics: list[dict] = []
    result = {
        "success": False,
        "status": "pagination_numeric_target_not_found",
        "method": "reverse_sequential_numeric_page_navigation",
        "target_page_number": int(target_page_number),
        "active_page_before": current_page_number,
        "active_page_after": current_page_number,
        "signature_after": previous_signature,
        "diagnostics": diagnostics,
        "url_after": _safe_page_url(page),
        "error": None,
    }
    while current_page_number > target_page_number:
        click_result = find_and_click_previous_listing_page(page, current_page_number)
        diagnostics.append(click_result)
        if (
            not click_result.get("found")
            or not click_result.get("enabled")
            or not click_result.get("clicked")
        ):
            result["status"] = (
                click_result.get("stop_reason")
                or "pagination_numeric_target_not_found"
            )
            result["error"] = (
                "Nao foi possivel navegar sequencialmente ate a pagina "
                f"{target_page_number}. Parou antes da pagina "
                f"{current_page_number - 1}. Motivo: {result['status']}"
            )
            result["url_after"] = _safe_page_url(page)
            return result
        _wait_after_pagination_click(page)
        rows_after = read_current_page_table_with_row_handles(page)
        signature_after = _listing_rows_signature(rows_after)
        active_after = get_active_numeric_page(page)
        result["active_page_after"] = active_after
        result["signature_after"] = signature_after
        result["url_after"] = _safe_page_url(page)
        if active_after is None:
            result["status"] = "cannot_confirm_active_page"
            result["error"] = (
                "Nao foi possivel detectar a pagina ativa apos navegacao "
                "sequencial reversa."
            )
            return result
        if active_after >= current_page_number:
            result["status"] = "pagination_active_page_mismatch"
            result["error"] = (
                f"Pagina ativa apos clique: {active_after}; esperado menor que "
                f"{current_page_number}."
            )
            return result
        if signature_after == previous_signature:
            result["status"] = "pagination_click_no_change"
            result["error"] = (
                "Clique sequencial reverso na pagina numerica nao alterou a tabela: "
                f"{active_after}."
            )
            return result
        current_page_number = active_after
        previous_signature = signature_after

    if current_page_number != target_page_number:
        result["status"] = "pagination_active_page_mismatch"
        result["error"] = (
            f"Pagina ativa apos navegacao sequencial reversa: {current_page_number}; "
            f"esperado: {target_page_number}."
        )
        return result
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_reverse_sequential_numeric_page",
            "error": None,
        }
    )
    return result


def ensure_request_origin_page(
    page,
    request: PortalSolicitation,
    listing_url: str | None = None,
    *,
    allow_active_navigation: bool = True,
) -> dict:
    target_page = int(request.page_number or 1)
    protocol = request.protocol
    result = {
        "success": False,
        "status": "protocol_not_found_on_origin_page",
        "method": None,
        "protocol": protocol,
        "target_page_number": target_page,
        "protocol_found_on_origin_page": False,
        "current_page_contains_protocol": False,
        "navigation": None,
        "row": None,
        "url_after": None,
        "error": None,
    }
    logger.info(f"Protocolo {protocol} pertence a pagina {target_page}.")

    if listing_url:
        recovery = _recover_minhas_solicitacoes(
            page,
            listing_url,
            allow_active_navigation=allow_active_navigation,
        )
        if not recovery["success"]:
            result.update(
                {
                    "status": "failed_return_to_listing",
                    "method": recovery.get("method"),
                    "url_after": recovery.get("url_after"),
                    "error": recovery.get("error"),
                }
            )
            return result

    rows = read_current_page_table_with_row_handles(page)
    current_row = _find_listing_row_in_rows(rows, protocol)
    if current_row is not None:
        logger.info(f"Protocolo {protocol} encontrado na pagina atual.")
        result.update(
            {
                "success": True,
                "status": "protocol_visible_current_page",
                "method": "current_page",
                "protocol_found_on_origin_page": True,
                "current_page_contains_protocol": True,
                "row": current_row,
                "url_after": _safe_page_url(page),
            }
        )
        return result

    logger.info(f"Protocolo {protocol} nao esta visivel na pagina atual.")
    if target_page <= 1:
        active_page = get_active_numeric_page(page)
        if active_page is not None and active_page != target_page:
            navigation = navigate_to_numeric_page(page, target_page)
            result["navigation"] = navigation
            result["url_after"] = navigation.get("url_after")
            if navigation["success"]:
                rows = read_current_page_table_with_row_handles(page)
                current_row = _find_listing_row_in_rows(rows, protocol)
                if current_row is not None:
                    result.update(
                        {
                            "success": True,
                            "status": "protocol_found_on_origin_page",
                            "method": navigation.get("method"),
                            "protocol_found_on_origin_page": True,
                            "row": current_row,
                            "url_after": _safe_page_url(page),
                        }
                    )
                    return result
            elif not allow_active_navigation:
                result.update(
                    {
                        "status": navigation.get("status")
                        or "protocol_not_found_on_origin_page",
                        "method": navigation.get("method"),
                        "error": navigation.get("error")
                        or "protocol_not_found_on_origin_page",
                    }
                )
                return result
        if listing_url and allow_active_navigation:
            try:
                logger.info(
                    f"Retornando para primeira pagina da listagem para o protocolo {protocol}."
                )
                _goto_listing_url(page, listing_url)
                _wait_minhas_solicitacoes(page)
                rows = read_current_page_table_with_row_handles(page)
                current_row = _find_listing_row_in_rows(rows, protocol)
                if current_row is not None:
                    result.update(
                        {
                            "success": True,
                            "status": "protocol_found_after_first_page_reset",
                            "method": "recovered_listing_by_url",
                            "protocol_found_on_origin_page": True,
                            "row": current_row,
                            "url_after": _safe_page_url(page),
                        }
                    )
                    return result
            except PlaywrightError as exc:
                result["error"] = str(exc)
                result["url_after"] = _safe_page_url(page)
                return result
        if listing_url and not allow_active_navigation:
            result["method"] = "passive_origin_page_check"
        result["error"] = "protocol_not_found_on_origin_page"
        result["url_after"] = _safe_page_url(page)
        return result

    logger.info(f"Navegando para pagina numerica de origem: {target_page}")
    navigation = navigate_to_numeric_page(page, target_page)
    result["navigation"] = navigation
    result["url_after"] = navigation.get("url_after")
    logger.info(f"Resultado da navegacao para pagina de origem: {navigation}")
    if not navigation["success"]:
        result.update(
            {
                "status": navigation.get("status") or "failed_return_to_listing",
                "method": navigation.get("method"),
                "error": navigation.get("error"),
            }
        )
        return result

    rows = read_current_page_table_with_row_handles(page)
    current_row = _find_listing_row_in_rows(rows, protocol)
    found = current_row is not None
    logger.info(f"Protocolo {protocol} encontrado apos navegacao de origem: {found}.")
    if not found:
        result.update(
            {
                "status": "protocol_not_found_on_origin_page",
                "method": navigation.get("method"),
                "protocol_found_on_origin_page": False,
                "error": "protocol_not_found_on_origin_page",
                "url_after": _safe_page_url(page),
            }
        )
        return result

    result.update(
        {
            "success": True,
            "status": "protocol_found_on_origin_page",
            "method": navigation.get("method"),
            "protocol_found_on_origin_page": True,
            "row": current_row,
            "url_after": _safe_page_url(page),
        }
    )
    return result


def _navigate_to_listing_page_number(
    page,
    listing_url: str,
    target_page_number: int | None,
    settings,
) -> dict:
    recovery = _recover_minhas_solicitacoes(page, listing_url)
    if not recovery["success"]:
        return recovery

    target = int(target_page_number or 1)
    if target_page_number is not None:
        try:
            _goto_listing_url(page, listing_url)
            if not _wait_minhas_solicitacoes(page):
                recovery.update(
                    {
                        "success": False,
                        "status": "failed_return_to_listing",
                        "method": "pagination_origin_reset_failed",
                        "error": "Nao foi possivel reabrir a primeira pagina da listagem.",
                    }
                )
                return recovery
            recovery.update(
                {
                    "success": True,
                    "status": "recovered_listing_by_url",
                    "method": "recovered_listing_by_url",
                    "url_after": _safe_page_url(page),
                }
            )
        except PlaywrightError as exc:
            recovery.update(
                {
                    "success": False,
                    "status": "failed_return_to_listing",
                    "method": "pagination_origin_reset_failed",
                    "error": str(exc),
                }
            )
            return recovery

    if target <= 1:
        recovery["target_page_number"] = target
        return recovery

    max_pages = int(getattr(settings, "MAX_PORTAL_PAGES", 0) or 0)
    if max_pages > 0:
        target = min(target, max_pages)

    try:
        for page_number in range(1, target):
            rows_before = read_current_page_table_with_row_handles(page)
            signature_before = _listing_rows_signature(rows_before)
            if not _click_next_listing_page(page):
                recovery.update(
                    {
                        "success": False,
                        "status": "failed_return_to_listing",
                        "method": "pagination_to_origin_failed",
                        "error": (
                            "Nao foi possivel navegar ate a pagina de origem "
                            f"{target}. Parou antes da pagina {page_number + 1}."
                        ),
                    }
                )
                return recovery
            _wait_after_pagination_click(page)
            rows_after = read_current_page_table_with_row_handles(page)
            if _listing_rows_signature(rows_after) == signature_before:
                recovery.update(
                    {
                        "success": False,
                        "status": "failed_return_to_listing",
                        "method": "pagination_loop_detected",
                        "error": (
                            "Paginacao repetiu a mesma assinatura ao tentar "
                            f"chegar na pagina {target}."
                        ),
                    }
                )
                return recovery
        recovery.update(
            {
                "success": True,
                "status": "recovered_listing_by_pagination",
                "method": "recovered_listing_by_pagination",
                "target_page_number": target,
                "url_after": _safe_page_url(page),
            }
        )
        return recovery
    except PlaywrightError as exc:
        recovery.update(
            {
                "success": False,
                "status": "failed_return_to_listing",
                "method": "pagination_to_origin_failed",
                "error": str(exc),
            }
        )
        return recovery


def find_and_click_next_numeric_page(page, current_page_number: int) -> dict:
    target_page_number = current_page_number + 1
    try:
        result = page.evaluate(
            """
            (targetPageNumber) => {
              const targetText = String(targetPageNumber);
              const currentText = String(targetPageNumber - 1);
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();
              const rawText = (element) => [
                element?.innerText,
                element?.textContent,
                element?.value,
                element?.getAttribute?.("aria-label"),
                element?.getAttribute?.("title")
              ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim();
              const directText = (element) => (
                element?.innerText ||
                element?.textContent ||
                element?.value ||
                ""
              ).replace(/\\s+/g, " ").trim();
              const isVisible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== "none" &&
                  style.visibility !== "hidden" &&
                  rect.width > 0 &&
                  rect.height > 0;
              };
              const isDisabled = (element) => {
                if (!element) return true;
                const classes = normalize(String(element.className || ""));
                return Boolean(
                  element.disabled ||
                  element.getAttribute("aria-disabled") === "true" ||
                  element.getAttribute("disabled") !== null ||
                  classes.includes("DISABLED") ||
                  classes.includes("UI STATE DISABLED")
                );
              };
              const isSelectLike = (element) =>
                Boolean(element?.closest?.("select") || ["SELECT", "OPTION"].includes(element?.tagName));
              const isClickable = (element) => {
                if (!element || isSelectLike(element)) return false;
                const tag = element.tagName;
                const role = element.getAttribute("role");
                const cursor = window.getComputedStyle(element).cursor;
                return tag === "A" ||
                  tag === "BUTTON" ||
                  tag === "INPUT" ||
                  role === "button" ||
                  element.getAttribute("onclick") !== null ||
                  element.getAttribute("tabindex") !== null ||
                  cursor === "pointer";
              };
              const clickableFor = (element) => {
                const parent = element?.closest?.("a,button,input,[role='button']");
                if (parent && !isSelectLike(parent)) return parent;
                return element;
              };
              const cssPath = (element) => {
                if (!element) return null;
                if (element.id) return `#${element.id}`;
                const parts = [];
                let current = element;
                for (let depth = 0; current && depth < 4; depth += 1) {
                  let part = current.tagName.toLowerCase();
                  if (current.className && typeof current.className === "string") {
                    const className = current.className.trim().split(/\\s+/).slice(0, 2).join(".");
                    if (className) part += `.${className}`;
                  }
                  parts.unshift(part);
                  current = current.parentElement;
                }
                return parts.join(" > ");
              };
              const containerSelectors = [
                ".ui-paginator",
                "[class*='paginator']",
                "[class*='pagination']",
                "[id*='paginator']",
                "[id*='pagination']",
                "nav"
              ];
              const containers = [];
              const seenContainers = new Set();
              const addContainer = (element, reason) => {
                if (!element || seenContainers.has(element) || !isVisible(element)) return;
                const text = normalize(element.textContent || "");
                if (text.length > 1200) return;
                seenContainers.add(element);
                containers.push({ element, reason, textLength: text.length });
              };
              for (const selector of containerSelectors) {
                for (const element of Array.from(document.querySelectorAll(selector))) {
                  addContainer(element, selector);
                }
              }
              for (const element of Array.from(document.querySelectorAll("div,span,td,th,p,label"))) {
                const text = normalize(element.textContent || "");
                if (
                  text.includes("PAGINA") ||
                  text.includes("QUANTIDADE DE SOLICITACOES POR PAGINA")
                ) {
                  addContainer(element, "pagination-text");
                  let parent = element.parentElement;
                  for (let depth = 0; parent && depth < 4; depth += 1) {
                    const parentText = normalize(parent.textContent || "");
                    if (
                      parentText.includes("PAGINA") &&
                      parentText.includes(targetText) &&
                      parentText.length <= 1200
                    ) {
                      addContainer(parent, "pagination-text-parent");
                    }
                    parent = parent.parentElement;
                  }
                }
              }
              containers.sort((left, right) => left.textLength - right.textLength);

              const numericLinks = [];
              const targetCandidates = [];
              const seenElements = new Set();
              for (const containerInfo of containers) {
                const elements = Array.from(containerInfo.element.querySelectorAll(
                  "a,button,input,[role='button'],span,li"
                ));
                if (["A", "BUTTON", "INPUT", "SPAN", "LI"].includes(containerInfo.element.tagName)) {
                  elements.unshift(containerInfo.element);
                }
                for (const raw of elements) {
                  const clickElement = clickableFor(raw);
                  if (!clickElement || seenElements.has(clickElement) || !isVisible(clickElement)) {
                    continue;
                  }
                  seenElements.add(clickElement);
                  if (isSelectLike(raw) || isSelectLike(clickElement)) continue;
                  const text = directText(raw) || directText(clickElement);
                  if (!/^\\d+$/.test(text)) continue;
                  numericLinks.push(text);
                  const disabled = isDisabled(clickElement) || isDisabled(raw);
                  const clickable = isClickable(clickElement) || isClickable(raw);
                  if (text !== targetText) continue;
                  targetCandidates.push({
                    raw,
                    clickElement,
                    disabled,
                    clickable,
                    containerReason: containerInfo.reason
                  });
                }
              }

              const uniqueNumericLinks = Array.from(new Set(numericLinks));
              const target = targetCandidates.find((candidate) =>
                !candidate.disabled && candidate.clickable
              ) || targetCandidates[0];
              if (!target) {
                return {
                  found: false,
                  enabled: false,
                  clicked: false,
                  selector: null,
                  text: targetText,
                  class_name: null,
                  mode: "numeric",
                  current_page_number: targetPageNumber - 1,
                  target_page_number: targetPageNumber,
                  numeric_page_links_found: uniqueNumericLinks,
                  numeric_page_links_count: uniqueNumericLinks.length,
                  stop_reason: "pagination_numeric_target_not_found"
                };
              }
              const details = {
                found: true,
                enabled: !target.disabled && target.clickable,
                clicked: false,
                selector: cssPath(target.clickElement),
                text: rawText(target.clickElement) || targetText,
                class_name: String(target.clickElement.className || target.raw.className || ""),
                mode: "numeric",
                current_page_number: targetPageNumber - 1,
                target_page_number: targetPageNumber,
                numeric_page_links_found: uniqueNumericLinks,
                numeric_page_links_count: uniqueNumericLinks.length,
                stop_reason: null
              };
              if (!details.enabled) {
                details.stop_reason = "pagination_numeric_target_disabled";
                return details;
              }
              details.clicked = false;
              details.stop_reason = "pagination_numeric_target_ready";
              return details;
            }
            """,
            target_page_number,
        )
        if (
            isinstance(result, dict)
            and result.get("found")
            and result.get("enabled")
            and not result.get("clicked")
        ):
            locator_click = _click_numeric_paginator_with_playwright(
                page,
                target_page_number=target_page_number,
            )
            result["locator_click"] = locator_click
            result["clicked"] = bool(locator_click.get("clicked"))
            if result["clicked"]:
                result["stop_reason"] = "pagination_numeric_page_clicked"
                result["selector"] = locator_click.get("selector") or result.get("selector")
                result["text"] = locator_click.get("text") or result.get("text")
            else:
                result["stop_reason"] = (
                    locator_click.get("stop_reason")
                    or result.get("stop_reason")
                    or "pagination_numeric_click_failed"
                )
        if (
            isinstance(result, dict)
            and not result.get("found")
            and str(target_page_number)
            in {str(link) for link in result.get("numeric_page_links_found", [])}
        ):
            locator_click = _click_numeric_paginator_with_playwright(
                page,
                target_page_number=target_page_number,
            )
            result["locator_click"] = locator_click
            result["clicked"] = bool(locator_click.get("clicked"))
            if result["clicked"]:
                result["found"] = True
                result["enabled"] = True
                result["stop_reason"] = "pagination_numeric_page_clicked"
                result["selector"] = locator_click.get("selector") or result.get("selector")
                result["text"] = locator_click.get("text") or result.get("text")
            else:
                result["stop_reason"] = (
                    locator_click.get("stop_reason")
                    or result.get("stop_reason")
                    or "pagination_numeric_target_not_found"
                )
        if not isinstance(result, dict):
            result = {
                "found": False,
                "enabled": False,
                "clicked": False,
                "selector": None,
                "text": str(target_page_number),
                "class_name": None,
                "mode": "numeric",
                "current_page_number": current_page_number,
                "target_page_number": target_page_number,
                "numeric_page_links_found": [],
                "numeric_page_links_count": 0,
                "stop_reason": "pagination_numeric_target_not_found",
            }
        logger.info(
            "Resultado da paginacao numerica: "
            f"current={result.get('current_page_number')}, "
            f"target={result.get('target_page_number')}, "
            f"found={result.get('found')}, clicked={result.get('clicked')}, "
            f"selector={result.get('selector')}, "
            f"links={result.get('numeric_page_links_found')}."
        )
        return result
    except (PlaywrightError, AttributeError) as exc:
        logger.warning(f"Falha ao clicar na pagina numerica {target_page_number}: {exc}")
        return {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": str(target_page_number),
            "class_name": None,
            "mode": "numeric",
            "current_page_number": current_page_number,
            "target_page_number": target_page_number,
            "numeric_page_links_found": [],
            "numeric_page_links_count": 0,
            "stop_reason": f"pagination_numeric_click_error: {exc}",
        }


def _click_numeric_paginator_with_playwright(page, *, target_page_number: int) -> dict:
    return cdp_navigation_helpers.click_numeric_paginator_with_playwright(
        page,
        target_page_number=target_page_number,
        playwright_error=PlaywrightError,
        wait_portal_loader_idle=_wait_portal_loader_idle,
        click_locator_via_dom=_click_locator_via_dom,
        wait_after_pagination_click=_wait_after_pagination_click,
    )


def _wait_portal_loader_idle(page, *, timeout_ms: int = 8_000) -> bool:
    try:
        loader = page.locator("#page-loader, .ui-blockui, .ui-widget-overlay").first
        loader.wait_for(state="hidden", timeout=timeout_ms)
        return True
    except (PlaywrightError, AttributeError):
        return False


def _click_locator_via_dom(locator) -> bool:
    try:
        locator.evaluate("(element) => element.click()")
        return True
    except (PlaywrightError, AttributeError):
        return False


def find_and_click_next_listing_page(page, current_page_number: int) -> dict:
    return cdp_navigation_helpers.find_and_click_next_listing_page(
        page,
        current_page_number,
        find_and_click_next_numeric_page=find_and_click_next_numeric_page,
        click_next_listing_page_diagnostic=_click_next_listing_page_diagnostic,
    )


def find_and_click_previous_listing_page(page, current_page_number: int) -> dict:
    return cdp_navigation_helpers.find_and_click_previous_listing_page(
        page,
        current_page_number,
        click_previous_listing_page_diagnostic=_click_previous_listing_page_diagnostic,
    )


def inspect_next_page_availability(page, current_page_number: int) -> dict:
    target_page_number = int(current_page_number or 0) + 1
    try:
        result = page.evaluate(
            """
            (targetPageNumber) => {
              const targetText = String(targetPageNumber);
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();
              const textFor = (element) => [
                element?.innerText,
                element?.textContent,
                element?.value,
                element?.getAttribute?.("aria-label"),
                element?.getAttribute?.("title")
              ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim();
              const directText = (element) => (
                element?.innerText ||
                element?.textContent ||
                element?.value ||
                ""
              ).replace(/\\s+/g, " ").trim();
              const isVisible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== "none" &&
                  style.visibility !== "hidden" &&
                  rect.width > 0 &&
                  rect.height > 0;
              };
              const isDisabled = (element) => {
                if (!element) return true;
                const classes = normalize(String(element.className || ""));
                return Boolean(
                  element.disabled ||
                  element.getAttribute("aria-disabled") === "true" ||
                  element.getAttribute("disabled") !== null ||
                  classes.includes("DISABLED") ||
                  classes.includes("UI STATE DISABLED")
                );
              };
              const clickableFor = (element) =>
                element?.closest?.("a,button,input,[role='button']") || element;
              const containers = Array.from(document.querySelectorAll(
                ".ui-paginator,[class*='paginator'],[class*='pagination'],[id*='paginator'],[id*='pagination'],nav"
              )).filter(isVisible);
              const numericLinks = [];
              let targetNumericAvailable = false;
              let nextButtonFound = false;
              let nextButtonEnabled = false;
              for (const container of containers) {
                const elements = Array.from(container.querySelectorAll(
                  "a,button,input,[role='button'],span,li"
                ));
                for (const raw of elements) {
                  if (!isVisible(raw)) continue;
                  const clickElement = clickableFor(raw);
                  if (!clickElement || !isVisible(clickElement)) continue;
                  const rawText = textFor(clickElement) || textFor(raw);
                  const normalized = normalize(rawText);
                  const direct = directText(raw) || directText(clickElement);
                  const disabled = isDisabled(clickElement) || isDisabled(raw);
                  if (/^\\d+$/.test(direct)) {
                    numericLinks.push(direct);
                    if (direct === targetText && !disabled) {
                      targetNumericAvailable = true;
                    }
                  }
                  const classText = normalize(
                    String(clickElement.className || "") + " " + String(raw.className || "")
                  );
                  const isNext = normalized.includes("PROXIMA") ||
                    normalized.includes("NEXT") ||
                    classText.includes("NEXT") ||
                    rawText === ">" ||
                    rawText === "›" ||
                    rawText === "»";
                  if (!isNext) continue;
                  nextButtonFound = true;
                  if (!disabled) nextButtonEnabled = true;
                }
              }
              const uniqueNumericLinks = Array.from(new Set(numericLinks));
              return {
                next_page_available: targetNumericAvailable || nextButtonEnabled,
                target_page_number: targetPageNumber,
                target_numeric_available: targetNumericAvailable,
                next_button_found: nextButtonFound,
                next_button_enabled: nextButtonEnabled,
                numeric_page_links_found: uniqueNumericLinks,
              };
            }
            """,
            target_page_number,
        )
        if isinstance(result, dict):
            return result
    except (PlaywrightError, AttributeError) as exc:
        logger.warning(f"Falha ao inspecionar disponibilidade da proxima pagina: {exc}")
    return {
        "next_page_available": False,
        "target_page_number": target_page_number,
        "target_numeric_available": False,
        "next_button_found": False,
        "next_button_enabled": False,
        "numeric_page_links_found": [],
    }


def _click_next_listing_page_diagnostic(page) -> dict:
    try:
        result = page.evaluate(
            """
            () => {
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();
              const labelFor = (element) => [
                element?.innerText,
                element?.textContent,
                element?.value,
                element?.getAttribute?.("aria-label"),
                element?.getAttribute?.("title")
              ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim();
              const isDisabled = (element) => {
                if (!element) return false;
                const classes = normalize(String(element.className || ""));
                return Boolean(
                  element.disabled ||
                  element.getAttribute("aria-disabled") === "true" ||
                  element.getAttribute("disabled") !== null ||
                  classes.includes("DISABLED") ||
                  classes.includes("UI STATE DISABLED")
                );
              };
              const clickableFor = (element) =>
                element?.closest?.("a,button,input,[role='button']") || element;
              const selectors = [
                ".ui-paginator-next",
                "a.ui-paginator-next",
                "span.ui-paginator-next",
                ".paginate_button.next",
                "li.next a",
                "a[aria-label*='Próxima']",
                "a[aria-label*='Proxima']",
                "a[aria-label*='Next']",
                "button[aria-label*='Próxima']",
                "button[aria-label*='Proxima']",
                "button[aria-label*='Next']",
                "a[title*='Próxima']",
                "a[title*='Proxima']",
                "a[title*='Next']",
                "button[title*='Próxima']",
                "button[title*='Proxima']",
                "button[title*='Next']",
                "[class*='paginator'][class*='next']",
                "[class*='pagination'][class*='next']",
                "[class*='next']"
              ];
              const candidates = [];
              for (const selector of selectors) {
                for (const element of Array.from(document.querySelectorAll(selector))) {
                  candidates.push({ raw: element, selector });
                }
              }
              for (const element of Array.from(document.querySelectorAll(
                "a,button,input,[role='button'],li,span"
              ))) {
                candidates.push({ raw: element, selector: "text-fallback" });
              }
              const seen = new Set();
              let disabledCandidate = null;
              for (const candidate of candidates) {
                const raw = candidate.raw;
                const element = clickableFor(raw);
                if (!element || seen.has(element)) continue;
                seen.add(element);
                const rawText = labelFor(element) || labelFor(raw);
                const text = normalize(rawText);
                const classText = normalize(String(element.className || "") + " " + String(raw.className || ""));
                const isNext = text.includes("PROXIMA") ||
                  text.includes("NEXT") ||
                  classText.includes("NEXT") ||
                  rawText === ">" ||
                  rawText === "›" ||
                  rawText === "»";
                if (!isNext) continue;
                const disabled = isDisabled(element) || isDisabled(element.closest("li")) || isDisabled(raw);
                const details = {
                  found: true,
                  enabled: !disabled,
                  clicked: false,
                  selector: candidate.selector,
                  text: rawText,
                  class_name: String(element.className || raw.className || ""),
                  stop_reason: disabled ? "pagination_next_disabled" : null
                };
                if (disabled) {
                  disabledCandidate = disabledCandidate || details;
                  continue;
                }
                element.scrollIntoView({ block: "center", inline: "center" });
                element.click();
                details.clicked = true;
                return details;
              }
              if (disabledCandidate) return disabledCandidate;
              return {
                found: false,
                enabled: false,
                clicked: false,
                selector: null,
                text: null,
                class_name: null,
                stop_reason: "pagination_next_not_found"
              };
            }
            """
        )
        return cdp_navigation_helpers.finish_listing_page_click_diagnostic(
            result=result,
            legacy_not_found_stop_reason="pagination_next_not_found",
            fallback_not_found_stop_reason="pagination_next_not_found",
            click_error_stop_reason_prefix="pagination_click_error",
            clicked_log_message=(
                "Avancando para a proxima pagina da listagem "
                "(selector={selector}, text={text})."
            ),
            not_clicked_log_message="Proxima pagina nao clicada: {result}",
            click_error_log_message="Falha ao clicar na proxima pagina",
            logger=logger,
        )
    except (PlaywrightError, AttributeError) as exc:
        return cdp_navigation_helpers.finish_listing_page_click_diagnostic(
            result=None,
            legacy_not_found_stop_reason="pagination_next_not_found",
            fallback_not_found_stop_reason="pagination_next_not_found",
            click_error_stop_reason_prefix="pagination_click_error",
            clicked_log_message=(
                "Avancando para a proxima pagina da listagem "
                "(selector={selector}, text={text})."
            ),
            not_clicked_log_message="Proxima pagina nao clicada: {result}",
            click_error_log_message="Falha ao clicar na proxima pagina",
            logger=logger,
            error=exc,
        )


def _click_previous_listing_page_diagnostic(page) -> dict:
    try:
        result = page.evaluate(
            """
            () => {
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();
              const labelFor = (element) => [
                element?.innerText,
                element?.textContent,
                element?.value,
                element?.getAttribute?.("aria-label"),
                element?.getAttribute?.("title")
              ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim();
              const isDisabled = (element) => {
                if (!element) return false;
                const classes = normalize(String(element.className || ""));
                return Boolean(
                  element.disabled ||
                  element.getAttribute("aria-disabled") === "true" ||
                  element.getAttribute("disabled") !== null ||
                  classes.includes("DISABLED") ||
                  classes.includes("UI STATE DISABLED")
                );
              };
              const clickableFor = (element) =>
                element?.closest?.("a,button,input,[role='button']") || element;
              const selectors = [
                ".ui-paginator-prev",
                ".ui-paginator-previous",
                "a.ui-paginator-prev",
                "span.ui-paginator-prev",
                ".paginate_button.previous",
                "li.previous a",
                "a[aria-label*='Anterior']",
                "a[aria-label*='Previous']",
                "button[aria-label*='Anterior']",
                "button[aria-label*='Previous']",
                "a[title*='Anterior']",
                "a[title*='Previous']",
                "button[title*='Anterior']",
                "button[title*='Previous']",
                "[class*='paginator'][class*='prev']",
                "[class*='pagination'][class*='prev']",
                "[class*='previous']",
                "[class*='prev']"
              ];
              const candidates = [];
              for (const selector of selectors) {
                for (const element of Array.from(document.querySelectorAll(selector))) {
                  candidates.push({ raw: element, selector });
                }
              }
              for (const element of Array.from(document.querySelectorAll(
                "a,button,input,[role='button'],li,span"
              ))) {
                candidates.push({ raw: element, selector: "text-fallback" });
              }
              const seen = new Set();
              let disabledCandidate = null;
              for (const candidate of candidates) {
                const raw = candidate.raw;
                const element = clickableFor(raw);
                if (!element || seen.has(element)) continue;
                seen.add(element);
                const rawText = labelFor(element) || labelFor(raw);
                const text = normalize(rawText);
                const classText = normalize(String(element.className || "") + " " + String(raw.className || ""));
                const isPrevious = text.includes("ANTERIOR") ||
                  text.includes("PREVIOUS") ||
                  classText.includes("PREV") ||
                  classText.includes("PREVIOUS") ||
                  rawText === "<" ||
                  rawText === "‹" ||
                  rawText === "«";
                if (!isPrevious) continue;
                const disabled = isDisabled(element) || isDisabled(element.closest("li")) || isDisabled(raw);
                const details = {
                  found: true,
                  enabled: !disabled,
                  clicked: false,
                  selector: candidate.selector,
                  text: rawText,
                  class_name: String(element.className || raw.className || ""),
                  stop_reason: disabled ? "pagination_previous_disabled" : null
                };
                if (disabled) {
                  disabledCandidate = disabledCandidate || details;
                  continue;
                }
                element.scrollIntoView({ block: "center", inline: "center" });
                element.click();
                details.clicked = true;
                return details;
              }
              if (disabledCandidate) return disabledCandidate;
              return {
                found: false,
                enabled: false,
                clicked: false,
                selector: null,
                text: null,
                class_name: null,
                stop_reason: "pagination_previous_not_found"
              };
            }
            """
        )
        return cdp_navigation_helpers.finish_listing_page_click_diagnostic(
            result=result,
            legacy_not_found_stop_reason="pagination_previous_not_found",
            fallback_not_found_stop_reason="pagination_previous_not_found",
            click_error_stop_reason_prefix="pagination_previous_click_error",
            clicked_log_message=(
                "Retornando para a pagina anterior da listagem "
                "(selector={selector}, text={text})."
            ),
            not_clicked_log_message="Pagina anterior nao clicada: {result}",
            click_error_log_message="Falha ao clicar na pagina anterior",
            logger=logger,
        )
    except (PlaywrightError, AttributeError) as exc:
        return cdp_navigation_helpers.finish_listing_page_click_diagnostic(
            result=None,
            legacy_not_found_stop_reason="pagination_previous_not_found",
            fallback_not_found_stop_reason="pagination_previous_not_found",
            click_error_stop_reason_prefix="pagination_previous_click_error",
            clicked_log_message=(
                "Retornando para a pagina anterior da listagem "
                "(selector={selector}, text={text})."
            ),
            not_clicked_log_message="Pagina anterior nao clicada: {result}",
            click_error_log_message="Falha ao clicar na pagina anterior",
            logger=logger,
            error=exc,
        )


def _click_next_listing_page(page) -> bool:
    return bool(_click_next_listing_page_diagnostic(page).get("clicked"))
    try:
        clicked = page.evaluate(
            """
            () => {
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();
              const isDisabled = (element) => {
                if (!element) return true;
                const disabled = element.disabled ||
                  element.getAttribute("aria-disabled") === "true" ||
                  element.getAttribute("disabled") !== null;
                const classes = normalize(String(element.className || ""));
                return Boolean(disabled || classes.includes("DISABLED"));
              };
              const clickableFor = (element) => {
                return element.closest("a,button,input,[role='button'],li") || element;
              };
              const selectorCandidates = [
                ".paginate_button.next:not(.disabled)",
                "li.next:not(.disabled) a",
                "a[aria-label*='Próxima']",
                "a[aria-label*='Proxima']",
                "button[aria-label*='Próxima']",
                "button[aria-label*='Proxima']",
                "a[title*='Próxima']",
                "a[title*='Proxima']",
                "button[title*='Próxima']",
                "button[title*='Proxima']"
              ];
              const candidates = [];
              for (const selector of selectorCandidates) {
                candidates.push(...Array.from(document.querySelectorAll(selector)));
              }
              candidates.push(...Array.from(document.querySelectorAll(
                "a,button,input,[role='button'],li,span"
              )));
              const seen = new Set();
              for (const raw of candidates) {
                const element = clickableFor(raw);
                if (!element || seen.has(element)) continue;
                seen.add(element);
                if (isDisabled(element) || isDisabled(element.closest("li"))) continue;
                const text = normalize([
                  element.innerText,
                  element.textContent,
                  element.value,
                  element.getAttribute("aria-label"),
                  element.getAttribute("title")
                ].filter(Boolean).join(" "));
                const rawText = [
                  element.innerText,
                  element.textContent,
                  element.value,
                  element.getAttribute("aria-label"),
                  element.getAttribute("title")
                ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim();
                const isNext = text.includes("PROXIMA") ||
                  text.includes("NEXT") ||
                  rawText === ">" ||
                  rawText === "›" ||
                  rawText === "»";
                if (!isNext) continue;
                element.scrollIntoView({ block: "center", inline: "center" });
                element.click();
                return true;
              }
              return false;
            }
            """
        )
        if clicked:
            logger.info("Avancando para a proxima pagina da listagem.")
        return bool(clicked)
    except PlaywrightError as exc:
        logger.warning(f"Falha ao clicar na proxima pagina: {exc}")
        return False


def _wait_after_pagination_click(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
    except PlaywrightError:
        pass
    try:
        page.wait_for_timeout(1_000)
    except PlaywrightError:
        pass


def _wait_for_active_page_change(
    page,
    *,
    previous_page_number: int,
    timeout_ms: int = 8_000,
    interval_ms: int = 250,
) -> int | None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        try:
            active_page = get_active_numeric_page(page)
        except (PlaywrightError, AttributeError):
            active_page = None
        if active_page is not None and active_page != previous_page_number:
            return active_page
        try:
            page.wait_for_timeout(interval_ms)
        except (PlaywrightError, AttributeError):
            time.sleep(interval_ms / 1000)
    return None


def ensure_listing_page(page, listing_url: str):
    return cdp_navigation_helpers.ensure_listing_page(
        page,
        listing_url,
        recover_minhas_solicitacoes=_recover_minhas_solicitacoes,
    )


def ensure_minhas_solicitacoes(page, listing_url: str) -> bool:
    return cdp_navigation_helpers.ensure_minhas_solicitacoes(
        page,
        listing_url,
        recover_minhas_solicitacoes=_recover_minhas_solicitacoes,
    )


def _ensure_listing_page(page, listing_url: str):
    return ensure_listing_page(page, listing_url)


def _return_to_listing_after_detail(
    detail_page,
    listing_page,
    listing_url: str,
    *,
    allow_active_navigation: bool = False,
):
    return cdp_navigation_helpers.return_to_listing_after_detail(
        detail_page,
        listing_page,
        listing_url,
        allow_active_navigation=allow_active_navigation,
        page_is_closed=_page_is_closed,
        recover_minhas_solicitacoes=_recover_minhas_solicitacoes,
        recover_listing_in_new_context_page=_recover_listing_in_new_context_page,
        recover_listing_by_detail_return_control=_recover_listing_by_detail_return_control,
        recover_listing_by_authenticated_home_icon=_recover_listing_by_authenticated_home_icon,
        playwright_error=PlaywrightError,
        logger=logger,
    )


def _recover_listing_in_new_context_page(
    page,
    listing_url: str,
    *,
    previous_error: str | None = None,
    allow_active_navigation: bool = True,
) -> tuple[object, dict]:
    return cdp_navigation_helpers.recover_listing_in_new_context_page(
        page,
        listing_url,
        previous_error=previous_error,
        allow_active_navigation=allow_active_navigation,
        page_is_closed=_page_is_closed,
        safe_page_url=_safe_page_url,
        recover_minhas_solicitacoes=_recover_minhas_solicitacoes,
        manual_recovery_message=manual_cdp_listing_recovery_message,
        playwright_error=PlaywrightError,
        logger=logger,
    )


def _recover_listing_by_detail_return_control(
    page,
    listing_url: str,
    *,
    previous_error: str | None = None,
) -> dict:
    return cdp_navigation_helpers.recover_listing_by_detail_return_control(
        page,
        listing_url,
        previous_error=previous_error,
        page_is_closed=_page_is_closed,
        safe_page_url=_safe_page_url,
        is_unsafe_authenticated_control_context_url=_is_unsafe_authenticated_control_context_url,
        click_detail_return_to_listing=_click_detail_return_to_listing,
        wait_minhas_solicitacoes=_wait_minhas_solicitacoes,
        listing_has_rows=_listing_has_rows,
        manual_recovery_message=manual_cdp_listing_recovery_message,
        playwright_error=PlaywrightError,
    )


def _recover_listing_by_authenticated_home_icon(
    page,
    listing_url: str,
    *,
    previous_error: str | None = None,
) -> dict:
    return cdp_navigation_helpers.recover_listing_by_authenticated_home_icon(
        page,
        listing_url,
        previous_error=previous_error,
        page_is_closed=_page_is_closed,
        page_looks_access_denied=_page_looks_access_denied,
        is_unsafe_authenticated_control_context_url=_is_unsafe_authenticated_control_context_url,
        safe_page_url=_safe_page_url,
        context_pages_snapshot=_context_pages_snapshot,
        click_authenticated_home_icon=_click_authenticated_home_icon,
        context_pages_changed=_context_pages_changed,
        wait_minhas_solicitacoes=_wait_minhas_solicitacoes,
        listing_has_rows=_listing_has_rows,
        click_home_minhas_solicitacoes_control=_click_home_minhas_solicitacoes_control,
        is_insecure_portal_http_url=is_insecure_portal_http_url,
        manual_recovery_message=manual_cdp_listing_recovery_message,
        playwright_error=PlaywrightError,
    )


def _successful_home_listing_recovery(page, result: dict) -> dict:
    return cdp_navigation_helpers.successful_home_listing_recovery(
        page,
        result,
        safe_page_url=_safe_page_url,
    )


def _recover_minhas_solicitacoes(
    page,
    listing_url: str,
    *,
    allow_active_navigation: bool = True,
) -> dict:
    return cdp_navigation_helpers.recover_minhas_solicitacoes(
        page,
        listing_url,
        allow_active_navigation=allow_active_navigation,
        has_minhas_solicitacoes_table=_has_minhas_solicitacoes_table,
        safe_page_url=_safe_page_url,
        is_unsafe_navigation_url=_is_unsafe_navigation_url,
        click_minhas_solicitacoes_navigation=_click_minhas_solicitacoes_navigation,
        wait_minhas_solicitacoes=_wait_minhas_solicitacoes,
        goto_listing_url=_goto_listing_url,
        reload_page=_reload_page,
        playwright_error=PlaywrightError,
        logger=logger,
        manual_recovery_message=manual_cdp_listing_recovery_message,
        detail_timeout_ms=DETAIL_TIMEOUT_MS,
    )


def manual_cdp_listing_recovery_message() -> str:
    return (
        "Nao foi possivel retornar para a tabela de listagem com seguranca. "
        "reabra o Edge pelo comando PowerShell aprovado, faca login manual no "
        "Portal GD e deixe a listagem aberta."
    )


def _apply_listing_recovery_result(target: dict, recovery: dict) -> None:
    target["retorno_listagem_status"] = recovery.get("status")
    target["metodo_retorno_listagem"] = recovery.get("method")
    target["url_apos_retorno"] = recovery.get("url_after")


def _apply_origin_page_navigation_result(target: dict, navigation: dict) -> None:
    target["origin_page_navigation"] = navigation.get("method")
    target["origin_page_navigation_status"] = navigation.get("status")
    target["protocol_found_on_origin_page"] = navigation.get(
        "protocol_found_on_origin_page"
    )
    target["url_apos_retorno"] = navigation.get("url_after")


def _capture_listing_context(page, rows: list[dict]) -> dict:
    state = _listing_table_state(page)
    return {
        "listing_url": _safe_page_url(page),
        "table_found": state.get("table_found", False),
        "has_minhas_solicitacoes_text": state.get("has_title", False),
        "headers_found": state.get("headers_found", []),
        "row_count": len(rows),
    }


def _has_minhas_solicitacoes_table(page) -> bool:
    state = _listing_table_state(page)
    return bool(
        state.get("table_found")
        and (state.get("row_count", 0) >= 1 or state.get("headers_valid"))
    )


def _wait_minhas_solicitacoes(page, attempts: int = 5) -> bool:
    for _ in range(attempts):
        _wait_portal_loader_idle(page, timeout_ms=5_000)
        if _has_minhas_solicitacoes_table(page):
            return True
        try:
            page.wait_for_timeout(2_000)
        except PlaywrightError:
            return False
    return False


def _listing_table_state(page) -> dict:
    if _page_is_closed(page):
        return {
            "table_found": False,
            "headers_valid": False,
            "row_count": 0,
            "has_title": False,
            "headers_found": [],
        }
    try:
        return page.evaluate(
            """
            () => {
              const normalize = (value) => (value || "")
                .normalize("NFD")
                .replace(/[\\u0300-\\u036f]/g, "")
                .toUpperCase()
                .replace(/\\s+/g, " ")
                .trim();
              const bodyText = normalize(document.body?.innerText || "");
              const hasTitle = bodyText.includes("MINHAS SOLICITACOES");
              const tables = Array.from(document.querySelectorAll("table"));
              for (const table of tables) {
                const rows = Array.from(table.querySelectorAll("tr"));
                const headerText = rows
                  .slice(0, 3)
                  .map((row) => normalize(row.innerText || row.textContent || ""))
                  .join(" ");
                const headers = [];
                if (headerText.includes("PROTOCOLO")) headers.push("PROTOCOLO");
                if (headerText.includes("STATUS")) headers.push("STATUS");
                if (headerText.includes("DATA DE INGRESSO")) headers.push("DATA DE INGRESSO");
                if (headerText.includes("ACOMPANHAR")) headers.push("ACOMPANHAR");
                const headersValid = headers.includes("PROTOCOLO")
                  && headers.includes("STATUS")
                  && headers.includes("DATA DE INGRESSO");
                if (headersValid) {
                  const dataRows = rows.filter((row) => {
                    const cells = Array.from(row.querySelectorAll("td"));
                    return cells.some((cell) => /\\b\\d{6,}\\b/.test(cell.innerText || cell.textContent || ""));
                  });
                  return {
                    table_found: true,
                    headers_valid: true,
                    row_count: dataRows.length,
                    has_title: hasTitle,
                    headers_found: headers,
                  };
                }
              }
              return {
                table_found: false,
                headers_valid: false,
                row_count: 0,
                has_title: hasTitle,
                headers_found: [],
              };
            }
            """
        )
    except PlaywrightError as exc:
        logger.debug(f"Falha ao validar tabela de Minhas Solicitacoes: {exc}")
        return {
            "table_found": False,
            "headers_valid": False,
            "row_count": 0,
            "has_title": False,
            "headers_found": [],
        }


def _click_minhas_solicitacoes_navigation(page) -> bool:
    patterns = [
        re.compile(r"minhas\s+solicita[cç][oõ]es", re.IGNORECASE),
        re.compile(r"consultar\s+solicita[cç][oõ]es", re.IGNORECASE),
        re.compile(r"\bsolicita[cç][oõ]es\b", re.IGNORECASE),
    ]
    for pattern in patterns:
        candidates = [
            page.get_by_role("link", name=pattern),
            page.get_by_role("button", name=pattern),
            page.locator("a, button, [role='button']").filter(has_text=pattern),
        ]
        for locator in candidates:
            if _click_first_visible(locator):
                try:
                    page.wait_for_load_state("networkidle", timeout=DETAIL_TIMEOUT_MS)
                except PlaywrightError:
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
                    except PlaywrightError:
                        pass
                return True
    return False


def _goto_listing_url(page, listing_url: str) -> None:
    try:
        page.goto(listing_url, wait_until="networkidle", timeout=DETAIL_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        page.goto(listing_url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT_MS)


def _reload_page(page) -> None:
    try:
        page.reload(wait_until="networkidle", timeout=DETAIL_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        page.reload(wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT_MS)


def _safe_page_url(page) -> str:
    try:
        return page.url or ""
    except PlaywrightError:
        return ""


def _listing_has_rows(page) -> bool:
    if _page_is_closed(page):
        return False
    try:
        return bool(read_current_page_table_with_row_handles(page))
    except Exception as exc:
        logger.debug(f"Pagina atual ainda nao esta na listagem: {exc}")
        return False


def _wait_listing_rows_with_retries(page, attempts: int = 3) -> bool:
    for _ in range(attempts):
        if _listing_has_rows(page):
            return True
        try:
            page.wait_for_timeout(1_000)
        except PlaywrightError:
            return False
    return False


def _click_internal_back_to_listing(page) -> bool:
    pattern = re.compile(
        r"voltar|retornar|minhas\s+solicita[cç][oõ]es", re.IGNORECASE
    )
    candidates = [
        page.get_by_role("button", name=pattern),
        page.get_by_role("link", name=pattern),
        page.locator("button:has-text('Voltar')"),
        page.locator("a:has-text('Voltar')"),
        page.locator("button:has-text('Retornar')"),
        page.locator("a:has-text('Retornar')"),
        page.locator("a:has-text('Minhas Solicitações')"),
        page.locator("a:has-text('Minhas Solicitacoes')"),
    ]
    for locator in candidates:
        if _click_first_visible(locator):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
            except PlaywrightError:
                pass
            return True
    return False


def _click_detail_return_to_listing(page) -> bool:
    pattern = re.compile(r"^\s*(voltar|retornar)\s*$", re.IGNORECASE)
    try:
        candidates = [
            page.get_by_role("button", name=pattern),
            page.get_by_role("link", name=pattern),
            page.locator("button:has-text('Voltar')"),
            page.locator("a:has-text('Voltar')"),
            page.locator("button:has-text('Retornar')"),
            page.locator("a:has-text('Retornar')"),
            page.locator("input[type='submit'][value*='Voltar']"),
            page.locator("input[type='button'][value*='Voltar']"),
            page.locator("input[value*='Voltar']"),
            page.locator("input[type='submit'][value*='Retornar']"),
            page.locator("input[type='button'][value*='Retornar']"),
            page.locator("input[value*='Retornar']"),
        ]
    except AttributeError:
        return False
    for locator in candidates:
        if _click_first_visible(locator):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
            except PlaywrightError:
                pass
            return True
    return False


def _click_authenticated_home_icon(page, listing_url: str) -> bool:
    pattern = re.compile(r"^\s*(home|in[iÃ­]cio|inicio)\s*$", re.IGNORECASE)
    try:
        candidates = [
            page.get_by_role("button", name=pattern),
            page.get_by_role("link", name=pattern),
            page.locator("[aria-label*='Home']"),
            page.locator("[aria-label*='InÃ­cio']"),
            page.locator("[aria-label*='Inicio']"),
            page.locator("[title*='Home']"),
            page.locator("[title*='InÃ­cio']"),
            page.locator("[title*='Inicio']"),
            page.locator("a:has(i.fa-home)"),
            page.locator("button:has(i.fa-home)"),
            page.locator("a:has(.fa-home)"),
            page.locator("button:has(.fa-home)"),
            page.locator("a:has-text('Home')"),
            page.locator("button:has-text('Home')"),
            page.locator("a:has-text('InÃ­cio')"),
            page.locator("button:has-text('InÃ­cio')"),
            page.locator("a:has-text('Inicio')"),
            page.locator("button:has-text('Inicio')"),
        ]
    except AttributeError:
        return False
    return _click_first_visible_safe_portal_control(
        candidates,
        listing_url,
        allow_same_host_root_or_index=True,
    )


def _click_home_minhas_solicitacoes_control(page, listing_url: str) -> bool:
    pattern = re.compile(
        r"minhas\s+solicita[cÃ§][oÃµ]es|consultar\s+solicita[cÃ§][oÃµ]es",
        re.IGNORECASE,
    )
    try:
        candidates = [
            page.get_by_role("link", name=pattern),
            page.get_by_role("button", name=pattern),
            page.locator("a:has-text('Minhas SolicitaÃ§Ãµes')"),
            page.locator("button:has-text('Minhas SolicitaÃ§Ãµes')"),
            page.locator("a:has-text('Minhas Solicitacoes')"),
            page.locator("button:has-text('Minhas Solicitacoes')"),
            page.locator("a:has-text('Consultar SolicitaÃ§Ãµes')"),
            page.locator("button:has-text('Consultar SolicitaÃ§Ãµes')"),
            page.locator("a:has-text('Consultar Solicitacoes')"),
            page.locator("button:has-text('Consultar Solicitacoes')"),
        ]
    except AttributeError:
        return False
    return _click_first_visible_safe_portal_control(candidates, listing_url)


def _click_first_visible_safe_portal_control(
    candidates: list,
    listing_url: str,
    *,
    allow_same_host_root_or_index: bool = False,
) -> bool:
    for locator in candidates:
        item = _first_visible(locator)
        if item is None:
            continue
        if _locator_href_is_unsafe(
            item,
            listing_url,
            allow_same_host_root_or_index=allow_same_host_root_or_index,
        ):
            continue
        try:
            item.click(timeout=10_000)
            try:
                item.page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
            except (AttributeError, PlaywrightError):
                pass
            return True
        except PlaywrightError as exc:
            logger.debug(f"Controle seguro visivel nao clicavel: {exc}")
    return False


def _locator_href_is_unsafe(
    locator,
    listing_url: str,
    *,
    allow_same_host_root_or_index: bool = False,
) -> bool:
    try:
        href = locator.get_attribute("href", timeout=1_000)
    except (AttributeError, PlaywrightError):
        return False
    if not href:
        return False
    normalized = href.strip()
    if not normalized or normalized.startswith("#"):
        return False
    if normalized.lower().startswith("javascript:"):
        return False
    parsed = urlparse(normalized)
    expected_host = urlparse(listing_url or "").netloc.lower()
    if not parsed.scheme and not parsed.netloc:
        if normalized.startswith(("/", ".")):
            base_url = listing_url or f"https://{expected_host or PORTAL_GD_HOST}/"
            normalized = urljoin(base_url, normalized)
            parsed = urlparse(normalized)
        else:
            return False
    if parsed.scheme.lower() != "https":
        return True
    if expected_host and parsed.netloc.lower() != expected_host:
        return True
    if _is_portal_root_or_index_url(normalized, expected_host):
        return not allow_same_host_root_or_index
    return False


def _context_pages_snapshot(page) -> list:
    context = getattr(page, "context", None)
    if context is None:
        return []
    return list(getattr(context, "pages", []) or [])


def _context_pages_changed(page, before: list) -> bool:
    context = getattr(page, "context", None)
    if context is None:
        return False
    return list(getattr(context, "pages", []) or []) != before


def _page_is_closed(page) -> bool:
    try:
        return bool(page.is_closed())
    except PlaywrightError:
        return True


def _is_valid_pdf(path: Path | str | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    return candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".pdf"


def _result_has_valid_pdf_for_processing(result: dict) -> bool:
    return (
        result.get("download_status") in VALID_DOWNLOAD_STATUSES_FOR_PROCESSING
        and _is_valid_pdf(result.get("process_pdf_path"))
    )


def _result_is_metadata_only_for_processing(result: dict) -> bool:
    return (
        result.get("download_status") in METADATA_ONLY_DOWNLOAD_STATUSES_FOR_PROCESSING
        and bool(result.get("selected_for_processing"))
        and bool(
            result.get("completion_date")
            or result.get("completion_date_raw")
            or result.get("completion_date_normalized")
        )
    )


def _refresh_download_totals(summary: dict) -> None:
    results = summary["results"]
    summary["total_processed"] = len(results)
    summary["total_downloaded"] = sum(
        1 for result in results if result.get("download_status") == "downloaded"
    )
    summary["total_existing_reused"] = sum(
        1
        for result in results
        if result.get("download_status") == "existing_pdf_after_skip"
    )
    summary["total_skipped_existing"] = sum(
        1 for result in results if result.get("skipped_download")
    )
    summary["total_skipped_duplicate"] = len(summary.get("duplicates_skipped", []))
    summary["total_skipped_origin_page_unavailable"] = sum(
        1
        for result in results
        if result.get("download_status") == "skipped_origin_page_unavailable"
    )
    summary["total_for_processing"] = sum(
        1 for result in results if _result_has_valid_pdf_for_processing(result)
    )
    summary["total_metadata_only_for_processing"] = sum(
        1 for result in results if _result_is_metadata_only_for_processing(result)
    )
    summary["total_budget_unavailable"] = sum(
        1 for result in results if result.get("download_status") == "budget_unavailable"
    )
    summary["total_sent_to_processing"] = (
        summary["total_for_processing"] + summary["total_metadata_only_for_processing"]
    )
    summary["total_cdp_errors"] = sum(
        1
        for result in results
        if result.get("cdp_error") or result.get("navigation_error")
    )
    summary["total_download_errors"] = sum(
        1 for result in results if result.get("download_error")
    )
    summary["total_errors"] = (
        summary["total_cdp_errors"] + summary["total_download_errors"]
    )


def _page_has_listing_table(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                  const text = document.body?.innerText || "";
                  return /PROTOCOLO/i.test(text)
                    && /STATUS/i.test(text)
                    && /DATA\\s+DE\\s+INGRESSO/i.test(text);
                }
                """
            )
        )
    except PlaywrightError:
        return False


def _wait_listing_table(page) -> None:
    deadline_ms = DETAIL_TIMEOUT_MS
    try:
        page.wait_for_function(
            """
            () => {
              const text = document.body?.innerText || "";
              return /PROTOCOLO/i.test(text)
                && /STATUS/i.test(text)
                && /DATA\\s+DE\\s+INGRESSO/i.test(text);
            }
            """,
            timeout=deadline_ms,
        )
    except PlaywrightError as exc:
        raise RuntimeError("Não foi possível retornar para a tabela de listagem.") from exc


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
                logger.info(f"Clique realizado no primeiro controle visível índice={index}.")
                return True
        except PlaywrightError as exc:
            logger.debug(f"Controle visível não clicável índice={index}: {exc}")
    return False


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


def _detail_text(page) -> str:
    return cdp_detail_helpers.detail_text(
        page,
        playwright_error=PlaywrightError,
        logger=logger,
    )


def _title_candidates(page) -> list[str]:
    return cdp_detail_helpers.title_candidates(
        page,
        playwright_error=PlaywrightError,
    )


def _extract_client_from_detail_text(
    title_text: str, body_text: str, protocol: str | None
) -> str | None:
    return cdp_detail_helpers.extract_client_from_detail_text(
        title_text,
        body_text,
        protocol,
    )


def _clean_client_candidate(value: str, protocol: str | None) -> str | None:
    return cdp_detail_helpers.clean_client_candidate(value, protocol)


def _normalize_search(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.upper()
    return re.sub(r"\s+", " ", text).strip()


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


def _next_budget_path(
    protocol_dir: Path, protocol: str, suggested_filename: str | None
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
