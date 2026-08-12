import json
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.dates import parse_date
from automacao_gd.infrastructure.excel.service import protocol_row_has_required_values
from automacao_gd.infrastructure.files.file_service import sanitize_filename
from automacao_gd.infrastructure.logging import logger
from automacao_gd.infrastructure.metadata.service import save_download_metadata
from automacao_gd.domain.models import PortalSolicitation
from automacao_gd.infrastructure.pdf.service import extract_generation_data


DOWNLOAD_TIMEOUT_MS = 30_000
BUDGET_BUTTON_TIMEOUT_MS = 10_000
DETAIL_TIMEOUT_MS = 20_000
LISTING_RECOVERY_ATTEMPTS = 3
VALID_DOWNLOAD_STATUSES_FOR_PROCESSING = {"downloaded", "existing_pdf_after_skip"}
POINT_OF_CONNECTION_STAGE = "PONTO_DE_CONEXAO_APROVADO"
POINT_OF_CONNECTION_STAGE_LABEL = "Ponto de Conexão Aprovado"
POINT_OF_CONNECTION_NO_DATE_STATUSES = {
    "POINT_OF_CONNECTION_COMPLETED_WITHOUT_DATE",
    "POINT_OF_CONNECTION_STAGE_COMPLETED_WITHOUT_DATE",
    "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE",
}
_COMPLETION_DATE_RE = re.compile(
    r"Conclu[ií]do\s+em\s+(\d{2}/\d{2}/\d{4})",
    flags=re.IGNORECASE,
)
_KNOWN_TIMELINE_STAGES = {
    "AGUARDANDO DOCUMENTACAO",
    "EM ANALISE TECNICA",
    "AGUARDANDO SOLICITACAO DE VISTORIA E CONEXAO",
    "REALIZANDO VISTORIA E CONEXAO",
    "PONTO DE CONEXAO APROVADO",
    "SOLICITACAO CONCLUIDA",
}
OPERATIONAL_ORIGIN_NAVIGATION_STATUSES = {
    "pagination_numeric_target_not_found",
    "pagination_numeric_target_disabled",
    "pagination_click_failed",
    "pagination_active_page_mismatch",
    "cannot_confirm_active_page",
}
INCOMPLETE_PAGINATION_STOP_REASONS = {
    "safety_cap_reached_with_next_page",
    "pagination_click_no_change",
    "pagination_repeated_signature",
    "pagination_loop_detected",
    "pagination_duplicate_page_content",
    "cannot_confirm_active_page",
}
PORTAL_GD_HOST = "gdneoenergiapernambuco.neoenergia.com"


class DownloadNotProducedError(RuntimeError):
    """Erro operacional quando o portal não entrega o PDF após o clique."""


def _log_origin_navigation_failure(message: str, status: str | None) -> None:
    if status in OPERATIONAL_ORIGIN_NAVIGATION_STATUSES:
        logger.warning(message)
        return
    logger.error(message)


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


def is_insecure_portal_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "http" and parsed.netloc.lower() == PORTAL_GD_HOST


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
) -> dict:
    pagination_enabled = bool(getattr(settings, "ENABLE_PORTAL_PAGINATION", False))
    max_pages = int(getattr(settings, "MAX_PORTAL_PAGES", 0) or 0)
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
    return len(selection["selected_records"]) >= limit


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
    if pipeline_state is None:
        return _workbook_should_skip_completed(protocol, settings)
    if hasattr(pipeline_state, "should_skip_completed"):
        return bool(pipeline_state.should_skip_completed(protocol, settings)) or (
            _workbook_should_skip_completed(protocol, settings)
        )
    entry = _state_entry(pipeline_state, protocol)
    return bool(entry and entry.get("status") == "completed") or (
        _workbook_should_skip_completed(protocol, settings)
    )


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
    validation = protocol_row_has_required_values(Path(workbook_path), protocol)
    return bool(validation.get("success") and validation.get("complete"))


def _state_entry(pipeline_state, protocol: str) -> dict | None:
    if pipeline_state is None:
        return None
    if hasattr(pipeline_state, "get_protocol"):
        entry = pipeline_state.get_protocol(protocol)
        return entry if isinstance(entry, dict) else None
    if isinstance(pipeline_state, dict):
        protocols = pipeline_state.get("protocols", pipeline_state)
        entry = protocols.get(protocol) if isinstance(protocols, dict) else None
        return entry if isinstance(entry, dict) else None
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

        is_forced = request.protocol in force_reprocess_protocols or (
            hasattr(pipeline_state, "is_force_reprocess")
            and pipeline_state.is_force_reprocess(request.protocol)
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
    if action_cell_index is None:
        cells = row_locator.locator("td")
        action_cell_index = max(cells.count() - 1, 0)

    action_cell = row_locator.locator("td").nth(action_cell_index)
    logger.info(
        f"Clicando no primeiro botão/link visível da coluna ACOMPANHAR "
        f"(cell_index={action_cell_index})."
    )

    primary = action_cell.locator(
        "button, a, input[type='button'], input[type='submit'], [role='button']"
    )
    if _click_first_visible(primary):
        return

    fallback = action_cell.locator("[onclick], span, i, svg, img")
    if _click_first_visible(fallback):
        return

    raise RuntimeError(
        "Não foi encontrado botão/link visível na coluna ACOMPANHAR da linha."
    )


def extract_detail_header(page) -> dict[str, str | None]:
    text = _detail_text(page)
    title_text = "\n".join(_title_candidates(page))
    combined = f"{title_text}\n{text}"

    detail_match = re.search(
        r"Solicita[cç][aã]o\s+(\d{6,})\s*:?\s*([^\n\r]+)?",
        combined,
        flags=re.IGNORECASE,
    )
    if detail_match:
        protocol = detail_match.group(1)
        client_name = _clean_client_candidate(detail_match.group(2) or "", protocol)
        return {"detail_protocol": protocol, "detail_client_name": client_name}

    protocol_match = re.search(r"\b\d{6,}\b", combined)
    protocol = protocol_match.group(0) if protocol_match else None
    client_name = _extract_client_from_detail_text(title_text, text, protocol)
    return {"detail_protocol": protocol, "detail_client_name": client_name}


def extract_completion_date(page) -> str | None:
    extraction = extract_point_of_connection_completion(page)
    return extraction["completion_date_raw"]


def extract_point_of_connection_completion(page, protocol: str | None = None) -> dict:
    return extract_point_of_connection_completion_from_text(
        _detail_text(page),
        protocol=protocol,
        source_selector="body:text_block",
    )


def extract_point_of_connection_completion_from_text(
    text: str,
    *,
    protocol: str | None = None,
    source_selector: str = "text_block",
) -> dict:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    base = {
        "protocol": protocol,
        "completion_date_raw": None,
        "completion_date_normalized": None,
        "completion_source_stage": POINT_OF_CONNECTION_STAGE,
        "completion_source_selector": source_selector,
    }
    stages = _find_stage_ranges(lines, "PONTO DE CONEXAO APROVADO")
    if not stages:
        return {
            **base,
            "completion_extraction_status": "POINT_OF_CONNECTION_STAGE_NOT_FOUND",
        }
    if len(stages) > 1:
        return {
            **base,
            "completion_extraction_status": "AMBIGUOUS_POINT_OF_CONNECTION_STAGE",
        }

    start, end = stages[0]
    next_stage = _next_timeline_stage_index(lines, end + 1)
    block_lines = lines[start : next_stage if next_stage is not None else len(lines)]
    dates = _COMPLETION_DATE_RE.findall("\n".join(block_lines))
    if not dates:
        return {
            **base,
            "completion_extraction_status": "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE",
        }
    unique_dates = list(dict.fromkeys(dates))
    if len(unique_dates) != 1:
        return {
            **base,
            "completion_extraction_status": "AMBIGUOUS_POINT_OF_CONNECTION_COMPLETION_DATE",
        }
    parsed = parse_date(unique_dates[0])
    if parsed is None:
        return {
            **base,
            "completion_extraction_status": "INVALID_POINT_OF_CONNECTION_COMPLETION_DATE",
        }
    return {
        **base,
        "completion_date_raw": unique_dates[0],
        "completion_date_normalized": parsed.isoformat(),
        "completion_extraction_status": "FOUND",
    }


def _find_stage_ranges(lines: list[str], target_normalized: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start in range(len(lines)):
        for end in range(start, min(start + 3, len(lines))):
            if _normalize_search(" ".join(lines[start : end + 1])) == target_normalized:
                ranges.append((start, end))
                break
    return ranges


def _next_timeline_stage_index(lines: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(lines)):
        for end in range(index, min(index + 3, len(lines))):
            if _normalize_search(" ".join(lines[index : end + 1])) in _KNOWN_TIMELINE_STAGES:
                return index
    return None


def detail_has_completed_status(page) -> bool:
    return "SOLICITACAO CONCLUIDA" in _normalize_search(_detail_text(page))


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
    logger.info("Garantindo retorno para a listagem 'Minhas Solicitacoes'.")
    if ensure_minhas_solicitacoes(page, listing_url):
        return
    raise RuntimeError("Nao foi possivel retornar para a tabela de listagem.")

    logger.info("Retornando para a listagem 'Minhas Solicitações'.")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
    except PlaywrightError as exc:
        logger.warning(f"Falha ao voltar pelo histórico: {exc}")

    if _page_has_listing_table(page):
        logger.info("Listagem recarregada pelo histórico.")
        return

    logger.info(f"Navegando novamente para a URL da listagem: {listing_url}")
    page.goto(listing_url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
    _wait_listing_table(page)


def wait_detail_loaded(page, protocol: str | None = None) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning("Timeout aguardando DOM da tela de detalhe.")

    if protocol:
        try:
            page.get_by_text(protocol).first.wait_for(timeout=DETAIL_TIMEOUT_MS)
        except PlaywrightError:
            logger.warning(
                f"Protocolo {protocol} não apareceu na tela de detalhe dentro do timeout."
            )
    else:
        try:
            page.get_by_text(re.compile(r"Solicita[cç][aã]o", re.IGNORECASE)).first.wait_for(
                timeout=DETAIL_TIMEOUT_MS
            )
        except PlaywrightError:
            logger.warning("Texto 'Solicitação' não apareceu na tela de detalhe.")


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


def _metadata_has_completion_value(metadata_path: Path) -> bool:
    metadata = _load_existing_download_metadata(Path(metadata_path))
    return bool(
        metadata.get("completion_date")
        or metadata.get("completion_date_raw")
        or metadata.get("completion_date_normalized")
        or metadata.get("completion_extraction_status")
        in POINT_OF_CONNECTION_NO_DATE_STATUSES
    )


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

    if settings.ENABLE_PORTAL_PAGINATION:
        reset_result = ensure_listing_starts_on_page_one(page)
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

    batch_fast_mode = _is_batch_fast_mode(settings)
    active_reconciliation_callback = None if batch_fast_mode else reconciliation_callback
    if batch_fast_mode:
        collection = _collect_completed_listing_rows_across_pages(
            page,
            settings,
            state_store=state_store,
            max_completed=limit,
            skip_already_completed=skip_already_completed,
        )
    else:
        collection = _collect_completed_listing_rows_across_pages(page, settings)
    if collection["pages_read"] == 0 or collection["total_rows"] == 0:
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
        if hasattr(state_store, "mark_discovered_many"):
            state_store.mark_discovered_many(completed_records)
        else:
            for record in completed_records:
                state_store.mark_discovered(record)
        logger.info("Registro de protocolos descobertos concluído.")
    selection = select_eligible_completed_requests(
        completed_requests=completed_records,
        pipeline_state=state_store,
        max_completed_to_process=limit,
        skip_already_completed=skip_already_completed,
        force_reprocess_protocols=settings.force_reprocess_protocols,
        settings=settings,
    )
    selected_records = selection["selected_records"]
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
        try:
            previous_entry = state_store.get_protocol(protocol) if state_store else None
            if previous_entry:
                result["previous_state"] = previous_entry.get("status")
                result["previous_last_step"] = previous_entry.get("last_step")
            if state_store:
                state_store.mark_selected(record)
                if (
                    skip_already_completed
                    and state_store.should_skip_completed(protocol, settings)
                ):
                    result["download_status"] = "skipped_already_completed"
                    result["skip_reason"] = "skipped_already_completed"
                    result["previous_state"] = "completed"
                    result["previous_last_step"] = (
                        state_store.get_protocol(protocol) or {}
                    ).get("last_step")
                    logger.info(
                        f"Protocolo {protocol} pulado: ja concluido no state."
                    )
                    continue
                state_store.update_protocol(
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
                and not should_open_detail_for_budget(
                    protocol,
                    downloads_root,
                    reprocess_existing_pdfs,
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
                state_store.update_section(
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
                    state_store.update_section(
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
                        state_store.add_error(
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
                    result["download_status"] = "download_error"
                    result["download_error"] = (
                        "Botao/link 'Orcamento de Conexao' nao encontrado."
                    )
                    result["error"] = (
                        "Botao/link 'Orcamento de Conexao' nao encontrado."
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
                            state_store.update_section(
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
                            state_store.add_error(
                                protocol, "download", result["download_error"]
                            )
        except DownloadNotProducedError as exc:
            logger.error(f"Erro ao processar protocolo {protocol}: {exc}")
            result["download_status"] = (
                result.get("download_status")
                if result.get("process_pdf_path")
                else "cdp_error"
            )
            result["cdp_error"] = str(exc)
            if state_store:
                state_store.add_error(protocol, "cdp", result["cdp_error"])
        except Exception as exc:
            logger.exception(f"Erro ao processar protocolo {protocol}: {exc}")
            result["download_status"] = (
                result.get("download_status")
                if result.get("process_pdf_path")
                else "cdp_error"
            )
            result["cdp_error"] = str(exc)
            if state_store:
                state_store.add_error(protocol, "cdp", result["cdp_error"])
        finally:
            if detail_page is not None:
                try:
                    page, listing_recovery = _return_to_listing_after_detail(
                        detail_page, listing_page, listing_url
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
                    summary["run_error"] = "failed_return_to_listing"
                    summary["aborted"] = True
                    summary["abort_reason"] = "failed_return_to_listing"
                    abort_requested = True
                    result["retorno_listagem_status"] = "failed_return_to_listing"
                    result["metodo_retorno_listagem"] = (
                        result.get("metodo_retorno_listagem") or "failed"
                    )
                    result["navigation_error"] = message
                    if not _result_has_valid_pdf_for_processing(result):
                        result["cdp_error"] = result.get("cdp_error") or message
                        if not result.get("download_status"):
                            result["download_status"] = "cdp_error"
                        if state_store:
                            state_store.add_error(protocol, "navigation", message)

            if result.get("download_status") == "pending":
                result["download_status"] = "cdp_error" if result.get("cdp_error") else "skipped"
            result["error"] = result.get("download_error") or result.get("cdp_error")
            if not _result_has_valid_pdf_for_processing(result):
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
        "total_for_processing": 0,
        "total_sent_to_processing": 0,
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

    if state_store:
        state_store.update_section(
            protocol,
            "metadata",
            {"exists": True, "path": str(metadata_path)},
            last_step="metadata_saved",
        )

    if _is_valid_pdf(existing_pdf):
        if state_store:
            state_store.update_section(
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
            state_store.add_error(protocol, "download", result["download_error"])

    return result


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


def ensure_listing_starts_on_page_one(page) -> dict:
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
    active_after = get_active_numeric_page(page)
    result["active_page_after"] = active_after
    if navigation["success"] and active_after == 1:
        result.update({"success": True, "status": "reset_to_first_page"})
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
            result["error"] = "Listagem visivel sem linhas para confirmar pagina inicial."
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


def navigate_to_numeric_page(page, target_page_number: int) -> dict:
    target = int(target_page_number or 1)
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": "numeric_page_navigation",
        "target_page_number": target,
        "active_page_before": None,
        "active_page_after": None,
        "signature_before": None,
        "signature_after": None,
        "click_result": None,
        "url_after": None,
        "error": None,
    }
    active_before = get_active_numeric_page(page)
    result["active_page_before"] = active_before
    if active_before == target:
        result.update(
            {
                "success": True,
                "status": "already_on_target_page",
                "method": "numeric_page_not_needed",
                "active_page_after": active_before,
                "url_after": _safe_page_url(page),
            }
        )
        return result
    if active_before is None:
        result.update(
            {
                "status": "cannot_confirm_active_page",
                "error": "Nao foi possivel detectar a pagina ativa antes da navegacao.",
                "url_after": _safe_page_url(page),
            }
        )
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
                sequential = _navigate_to_numeric_page_sequentially(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                )
                result["sequential_navigation"] = sequential
                result["url_after"] = sequential.get("url_after")
                if sequential.get("success"):
                    result.update(
                        {
                            "success": True,
                            "status": "recovered_listing_by_numeric_page",
                            "method": "recovered_listing_by_sequential_numeric_page",
                            "active_page_after": sequential.get("active_page_after"),
                            "signature_after": sequential.get("signature_after"),
                            "error": None,
                        }
                    )
                    return result
                result["status"] = (
                    sequential.get("status")
                    or click_result.get("stop_reason")
                    or "pagination_numeric_target_not_found"
                )
                result["error"] = (
                    sequential.get("error")
                    or "Pagina numerica de origem nao encontrada: "
                    f"{target}. Motivo: {click_result.get('stop_reason')}"
                )
                return result
            if target < active_before:
                sequential = _navigate_to_numeric_page_backwards_sequentially(
                    page,
                    target_page_number=target,
                    active_page_before=active_before,
                    signature_before=signature_before,
                )
                result["sequential_navigation"] = sequential
                result["url_after"] = sequential.get("url_after")
                if sequential.get("success"):
                    result.update(
                        {
                            "success": True,
                            "status": "recovered_listing_by_numeric_page",
                            "method": "recovered_listing_by_reverse_sequential_numeric_page",
                            "active_page_after": sequential.get("active_page_after"),
                            "signature_after": sequential.get("signature_after"),
                            "error": None,
                        }
                    )
                    return result
                result["status"] = (
                    sequential.get("status")
                    or click_result.get("stop_reason")
                    or "pagination_numeric_target_not_found"
                )
                result["error"] = (
                    sequential.get("error")
                    or "Pagina numerica de origem nao encontrada: "
                    f"{target}. Motivo: {click_result.get('stop_reason')}"
                )
                return result
            result["error"] = (
                "Pagina numerica de origem nao encontrada: "
                f"{target}. Motivo: {click_result.get('stop_reason')}"
            )
            result["status"] = click_result.get(
                "stop_reason", "pagination_numeric_target_not_found"
            )
            return result
        if not click_result.get("enabled"):
            result["error"] = (
                "Pagina numerica de origem encontrada, mas desabilitada: "
                f"{target}. Motivo: {click_result.get('stop_reason')}"
            )
            result["status"] = click_result.get(
                "stop_reason", "pagination_numeric_target_disabled"
            )
            return result
        if not click_result.get("clicked"):
            result["error"] = (
                "Pagina numerica de origem encontrada, mas nao clicada: "
                f"{target}. Motivo: {click_result.get('stop_reason')}"
            )
            result["status"] = click_result.get(
                "stop_reason", "pagination_click_failed"
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
                result.update(
                    {
                        "success": True,
                        "status": "recovered_listing_by_numeric_page_unconfirmed_active",
                        "method": "recovered_listing_by_numeric_page_unconfirmed_active",
                        "error": None,
                    }
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
                    result.update(
                        {
                            "success": True,
                            "status": "recovered_listing_by_numeric_page",
                            "method": "recovered_listing_by_sequential_numeric_page",
                            "active_page_after": sequential.get("active_page_after"),
                            "signature_after": sequential.get("signature_after"),
                            "error": None,
                        }
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
                    result.update(
                        {
                            "success": True,
                            "status": "recovered_listing_by_numeric_page",
                            "method": "recovered_listing_by_reverse_sequential_numeric_page",
                            "active_page_after": sequential.get("active_page_after"),
                            "signature_after": sequential.get("signature_after"),
                            "error": None,
                        }
                    )
                    return result
            result["status"] = "pagination_active_page_mismatch"
            result["error"] = (
                f"Pagina ativa apos clique: {active_after}; esperado: {target}."
            )
            return result
        if signature_after == signature_before:
            result["status"] = "pagination_click_no_change"
            result["error"] = (
                "Clique na pagina numerica de origem nao alterou a tabela: "
                f"{target}."
            )
            return result

        result.update(
            {
                "success": True,
                "status": "recovered_listing_by_numeric_page",
                "method": "recovered_listing_by_numeric_page",
                "error": None,
            }
        )
        return result
    except PlaywrightError as exc:
        result["status"] = "pagination_numeric_click_error"
        result["error"] = str(exc)
        result["url_after"] = _safe_page_url(page)
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
            result["status"] = (
                click_result.get("stop_reason")
                or "pagination_numeric_target_not_found"
            )
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
    target_text = str(target_page_number)
    selectors = (
        ".ui-paginator a.ui-paginator-page",
        "[class*='paginator'] a",
        "[class*='paginator'] button",
        "[class*='paginator'] [role='button']",
        "[class*='paginator'] span",
        "[class*='pagination'] a",
        "[class*='pagination'] button",
        "[class*='pagination'] [role='button']",
        "[class*='pagination'] span",
    )
    last_selector = selectors[0]
    try:
        for selector in selectors:
            last_selector = selector
            links = page.locator(selector)
            count = links.count()
            for index in range(count):
                link = links.nth(index)
                try:
                    text = (link.inner_text(timeout=1_000) or "").strip()
                    class_name = str(
                        link.get_attribute("class", timeout=1_000) or ""
                    )
                except (PlaywrightError, AttributeError):
                    continue
                if text != target_text:
                    continue
                if (
                    "ui-state-active" in class_name
                    or "ui-state-disabled" in class_name
                ):
                    continue
                link.click(timeout=DETAIL_TIMEOUT_MS)
                return {
                    "clicked": True,
                    "selector": selector,
                    "index": index,
                    "text": text,
                    "class_name": class_name,
                    "stop_reason": "pagination_numeric_page_clicked",
                }
        return {
            "clicked": False,
            "selector": last_selector,
            "text": target_text,
            "stop_reason": "pagination_numeric_locator_target_not_found",
        }
    except (PlaywrightError, AttributeError) as exc:
        return {
            "clicked": False,
            "selector": last_selector,
            "text": target_text,
            "stop_reason": f"pagination_numeric_locator_click_error: {exc}",
        }


def find_and_click_next_listing_page(page, current_page_number: int) -> dict:
    numeric = find_and_click_next_numeric_page(page, current_page_number)
    if numeric.get("found"):
        return numeric

    next_button = _click_next_listing_page_diagnostic(page)
    numeric_links = list(numeric.get("numeric_page_links_found") or [])
    result = {
        **next_button,
        "mode": "next_button",
        "current_page_number": current_page_number,
        "target_page_number": current_page_number + 1,
        "numeric_page_links_found": numeric_links,
        "numeric_page_links_count": len(numeric_links),
        "numeric_probe": numeric,
    }
    if next_button.get("clicked"):
        result["found"] = True
        result["enabled"] = True
        result["next_page_available"] = True
        result["stop_reason"] = "pagination_next_clicked"
        return result
    if not next_button.get("found") or not next_button.get("enabled"):
        result["found"] = False
        result["enabled"] = False
        result["next_page_available"] = False
        result["stop_reason"] = "last_page_reached"
        return result
    return result


def find_and_click_previous_listing_page(page, current_page_number: int) -> dict:
    previous_button = _click_previous_listing_page_diagnostic(page)
    result = {
        **previous_button,
        "mode": "previous_button",
        "current_page_number": current_page_number,
        "target_page_number": max(1, int(current_page_number or 1) - 1),
    }
    if previous_button.get("clicked"):
        result["found"] = True
        result["enabled"] = True
        result["previous_page_available"] = True
        result["stop_reason"] = "pagination_previous_clicked"
        return result
    if not previous_button.get("found") or not previous_button.get("enabled"):
        result["found"] = False
        result["enabled"] = False
        result["previous_page_available"] = False
        result["stop_reason"] = (
            previous_button.get("stop_reason") or "first_page_reached"
        )
        return result
    return result


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
        if isinstance(result, bool):
            result = {
                "found": result,
                "enabled": result,
                "clicked": result,
                "selector": "legacy-boolean-evaluate",
                "text": None,
                "class_name": None,
                "stop_reason": None if result else "pagination_next_not_found",
            }
        if not isinstance(result, dict):
            result = {
                "found": False,
                "enabled": False,
                "clicked": False,
                "selector": None,
                "text": None,
                "class_name": None,
                "stop_reason": "pagination_next_not_found",
            }
        if result.get("clicked"):
            logger.info(
                "Avancando para a proxima pagina da listagem "
                f"(selector={result.get('selector')}, text={result.get('text')})."
            )
        else:
            logger.info(f"Proxima pagina nao clicada: {result}")
        return result
    except (PlaywrightError, AttributeError) as exc:
        logger.warning(f"Falha ao clicar na proxima pagina: {exc}")
        return {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": None,
            "class_name": None,
            "stop_reason": f"pagination_click_error: {exc}",
        }


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
        if isinstance(result, bool):
            result = {
                "found": result,
                "enabled": result,
                "clicked": result,
                "selector": "legacy-boolean-evaluate",
                "text": None,
                "class_name": None,
                "stop_reason": None if result else "pagination_previous_not_found",
            }
        if not isinstance(result, dict):
            result = {
                "found": False,
                "enabled": False,
                "clicked": False,
                "selector": None,
                "text": None,
                "class_name": None,
                "stop_reason": "pagination_previous_not_found",
            }
        if result.get("clicked"):
            logger.info(
                "Retornando para a pagina anterior da listagem "
                f"(selector={result.get('selector')}, text={result.get('text')})."
            )
        else:
            logger.info(f"Pagina anterior nao clicada: {result}")
        return result
    except (PlaywrightError, AttributeError) as exc:
        logger.warning(f"Falha ao clicar na pagina anterior: {exc}")
        return {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": None,
            "class_name": None,
            "stop_reason": f"pagination_previous_click_error: {exc}",
        }


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
    recovery = _recover_minhas_solicitacoes(page, listing_url)
    if recovery["success"]:
        return page
    raise RuntimeError(recovery["error"])


def ensure_minhas_solicitacoes(page, listing_url: str) -> bool:
    return bool(_recover_minhas_solicitacoes(page, listing_url)["success"])


def _ensure_listing_page(page, listing_url: str):
    return ensure_listing_page(page, listing_url)


def _return_to_listing_after_detail(detail_page, listing_page, listing_url: str):
    if detail_page is not listing_page:
        if not _page_is_closed(listing_page):
            if not _page_is_closed(detail_page):
                try:
                    detail_page.close()
                except PlaywrightError as exc:
                    logger.debug(f"Falha ao fechar aba de detalhe: {exc}")
            recovery = _recover_minhas_solicitacoes(
                listing_page,
                listing_url,
                allow_active_navigation=False,
            )
            if not recovery["success"]:
                fallback_page, fallback_recovery = _recover_listing_in_new_context_page(
                    listing_page,
                    listing_url,
                    previous_error=recovery.get("error"),
                    allow_active_navigation=False,
                )
                if fallback_recovery["success"] or fallback_recovery.get("error"):
                    return fallback_page, fallback_recovery
            return listing_page, recovery
    recovery = _recover_minhas_solicitacoes(
        detail_page,
        listing_url,
        allow_active_navigation=False,
    )
    if not recovery["success"]:
        local_return_recovery = _recover_listing_by_detail_return_control(
            detail_page,
            listing_url,
            previous_error=recovery.get("error"),
        )
        if local_return_recovery["success"]:
            return detail_page, local_return_recovery
        fallback_page, fallback_recovery = _recover_listing_in_new_context_page(
            detail_page,
            listing_url,
            previous_error=recovery.get("error"),
            allow_active_navigation=False,
        )
        if fallback_recovery["success"] or fallback_recovery.get("error"):
            return fallback_page, fallback_recovery
    return detail_page, recovery


def _recover_listing_in_new_context_page(
    page,
    listing_url: str,
    *,
    previous_error: str | None = None,
    allow_active_navigation: bool = True,
) -> tuple[object, dict]:
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": "existing_context_listing_only",
        "url_before": _safe_page_url(page),
        "url_after": None,
        "error": previous_error or "Nao foi possivel retornar para a tabela de listagem.",
    }
    try:
        context = getattr(page, "context", None)
        if context is None:
            return page, result
        for candidate in list(getattr(context, "pages", []) or []):
            if candidate is page or _page_is_closed(candidate):
                continue
            recovery = _recover_minhas_solicitacoes(
                candidate,
                listing_url,
                allow_active_navigation=allow_active_navigation,
            )
            if recovery["success"]:
                recovery.update(
                    {
                        "status": "recovered_listing_by_existing_context_page",
                        "method": "recovered_listing_by_existing_context_page",
                    }
                )
                return candidate, recovery
        result["error"] = manual_cdp_listing_recovery_message()
        return page, result
    except PlaywrightError as exc:
        result["error"] = str(exc)
        result["url_after"] = _safe_page_url(page)
        logger.debug(f"Falha ao recuperar listagem em nova aba: {exc}")
        return page, result


def _recover_listing_by_detail_return_control(
    page,
    listing_url: str,
    *,
    previous_error: str | None = None,
) -> dict:
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": "recovered_listing_by_detail_return_control",
        "url_before": _safe_page_url(page),
        "url_after": None,
        "error": previous_error or manual_cdp_listing_recovery_message(),
    }
    if _page_is_closed(page):
        result["url_after"] = _safe_page_url(page)
        return result
    if _is_unsafe_navigation_url(_safe_page_url(page), listing_url):
        result["error"] = manual_cdp_listing_recovery_message()
        result["url_after"] = _safe_page_url(page)
        return result
    if not _click_detail_return_to_listing(page):
        result["url_after"] = _safe_page_url(page)
        return result
    try:
        page.wait_for_timeout(500)
    except PlaywrightError:
        pass
    if _wait_minhas_solicitacoes(page) and _listing_has_rows(page):
        result.update(
            {
                "success": True,
                "status": "recovered_listing_by_detail_return_control",
                "url_after": _safe_page_url(page),
                "error": None,
            }
        )
        return result
    result["error"] = manual_cdp_listing_recovery_message()
    result["url_after"] = _safe_page_url(page)
    return result


def _recover_minhas_solicitacoes(
    page,
    listing_url: str,
    *,
    allow_active_navigation: bool = True,
) -> dict:
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": None,
        "url_before": _safe_page_url(page),
        "url_after": None,
        "error": None,
    }
    last_error: Exception | None = None

    if _has_minhas_solicitacoes_table(page):
        result.update(
            {
                "success": True,
                "status": "ok",
                "method": "already_on_listing",
                "url_after": _safe_page_url(page),
            }
        )
        return result

    if not allow_active_navigation:
        result["error"] = manual_cdp_listing_recovery_message()
        result["url_after"] = _safe_page_url(page)
        return result

    unsafe_url = _is_unsafe_navigation_url(_safe_page_url(page), listing_url)
    if unsafe_url:
        result["error"] = manual_cdp_listing_recovery_message()
        result["url_after"] = _safe_page_url(page)
        return result

    if not unsafe_url and _click_minhas_solicitacoes_navigation(page):
        if _wait_minhas_solicitacoes(page):
            result.update(
                {
                    "success": True,
                    "status": "recovered_listing_by_menu",
                    "method": "recovered_listing_by_menu",
                    "url_after": _safe_page_url(page),
                }
            )
            return result

    try:
        _goto_listing_url(page, listing_url)
        if _wait_minhas_solicitacoes(page):
            result.update(
                {
                    "success": True,
                    "status": "recovered_listing_by_url",
                    "method": "recovered_listing_by_url",
                    "url_after": _safe_page_url(page),
                }
            )
            return result
    except PlaywrightError as exc:
        last_error = exc
        logger.debug(f"Falha ao navegar para URL salva da listagem: {exc}")

    try:
        _reload_page(page)
        if _wait_minhas_solicitacoes(page):
            result.update(
                {
                    "success": True,
                    "status": "recovered_listing_by_reload",
                    "method": "recovered_listing_by_reload",
                    "url_after": _safe_page_url(page),
                }
            )
            return result
    except PlaywrightError as exc:
        last_error = exc
        logger.debug(f"Falha ao recarregar listagem: {exc}")

    if not unsafe_url:
        try:
            page.go_back(wait_until="networkidle", timeout=DETAIL_TIMEOUT_MS)
            if _wait_minhas_solicitacoes(page):
                result.update(
                    {
                        "success": True,
                        "status": "recovered_listing_by_history",
                        "method": "recovered_listing_by_history",
                        "url_after": _safe_page_url(page),
                    }
                )
                return result
        except PlaywrightError as exc:
            last_error = exc
            logger.debug(f"Falha ao voltar pelo historico: {exc}")

    error = "Nao foi possivel retornar para a tabela de listagem."
    if last_error:
        error = f"{error} Ultimo erro: {last_error}"
    result["error"] = error
    result["url_after"] = _safe_page_url(page)
    return result


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


def _wait_minhas_solicitacoes(page, attempts: int = 3) -> bool:
    for _ in range(attempts):
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


def _is_unsafe_navigation_url(current_url: str | None, listing_url: str | None) -> bool:
    current = urlparse(current_url or "")
    listing = urlparse(listing_url or "")
    if is_insecure_portal_http_url(listing_url or ""):
        return True
    if listing.scheme and listing.scheme.lower() != "https":
        return True
    if current.scheme.lower() in {"about", "edge", "chrome"}:
        return True
    if not current.netloc:
        return True
    if is_insecure_portal_http_url(current_url or ""):
        return True
    expected_host = urlparse(listing_url or "").netloc.lower()
    if expected_host and current.netloc.lower() != expected_host:
        return True
    return False


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
    summary["total_for_processing"] = sum(
        1 for result in results if _result_has_valid_pdf_for_processing(result)
    )
    summary["total_sent_to_processing"] = summary["total_for_processing"]
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
    try:
        return page.locator("body").inner_text(timeout=10_000)
    except PlaywrightError as exc:
        logger.warning(f"Não foi possível ler o texto do detalhe: {exc}")
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
    except PlaywrightError:
        return []


def _extract_client_from_detail_text(
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
                client = _clean_client_candidate(match.group(1), protocol)
                if client:
                    return client
    return None


def _clean_client_candidate(value: str, protocol: str | None) -> str | None:
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
