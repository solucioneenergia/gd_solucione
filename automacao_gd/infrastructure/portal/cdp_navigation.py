from urllib.parse import urlparse


PORTAL_GD_HOST = "gdneoenergiapernambuco.neoenergia.com"


def is_insecure_portal_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "http" and parsed.netloc.lower() == PORTAL_GD_HOST


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
    if _is_portal_root_or_index_url(current_url or "", expected_host):
        return True
    return False


def _is_unsafe_authenticated_control_context_url(
    current_url: str | None,
    listing_url: str | None,
) -> bool:
    current = urlparse(current_url or "")
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


def _is_portal_root_or_index_url(url: str, expected_host: str | None = None) -> bool:
    parsed = urlparse(url or "")
    host = (expected_host or PORTAL_GD_HOST).lower()
    if parsed.netloc.lower() != host:
        return False
    path = (parsed.path or "/").rstrip("/").lower()
    return path in {"", "/index.jsf"}


def click_numeric_paginator_with_playwright(
    page,
    *,
    target_page_number: int,
    playwright_error,
    wait_portal_loader_idle,
    click_locator_via_dom,
    wait_after_pagination_click,
) -> dict:
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
                except (playwright_error, AttributeError):
                    continue
                if text != target_text:
                    continue
                if (
                    "ui-state-active" in class_name
                    or "ui-state-disabled" in class_name
                ):
                    continue
                wait_portal_loader_idle(page, timeout_ms=5_000)
                try:
                    link.click(timeout=5_000)
                except (playwright_error, AttributeError) as exc:
                    if not click_locator_via_dom(link):
                        raise exc
                    wait_after_pagination_click(page)
                    return {
                        "clicked": True,
                        "selector": selector,
                        "index": index,
                        "text": text,
                        "class_name": class_name,
                        "stop_reason": "pagination_numeric_page_clicked_dom_fallback",
                    }
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
    except (playwright_error, AttributeError) as exc:
        return {
            "clicked": False,
            "selector": last_selector,
            "text": target_text,
            "stop_reason": f"pagination_numeric_locator_click_error: {exc}",
        }


def finish_listing_page_click_diagnostic(
    *,
    result,
    legacy_not_found_stop_reason: str,
    fallback_not_found_stop_reason: str,
    click_error_stop_reason_prefix: str,
    clicked_log_message: str,
    not_clicked_log_message: str,
    click_error_log_message: str,
    logger,
    error: Exception | None = None,
) -> dict:
    if error is not None:
        logger.warning(f"{click_error_log_message}: {error}")
        return {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": None,
            "class_name": None,
            "stop_reason": f"{click_error_stop_reason_prefix}: {error}",
        }

    if isinstance(result, bool):
        result = {
            "found": result,
            "enabled": result,
            "clicked": result,
            "selector": "legacy-boolean-evaluate",
            "text": None,
            "class_name": None,
            "stop_reason": None if result else legacy_not_found_stop_reason,
        }
    if not isinstance(result, dict):
        result = {
            "found": False,
            "enabled": False,
            "clicked": False,
            "selector": None,
            "text": None,
            "class_name": None,
            "stop_reason": fallback_not_found_stop_reason,
        }
    if result.get("clicked"):
        logger.info(
            clicked_log_message.format(
                selector=result.get("selector"),
                text=result.get("text"),
            )
        )
    else:
        logger.info(not_clicked_log_message.format(result=result))
    return result


def build_numeric_page_navigation_result(target_page_number: int) -> dict:
    target = int(target_page_number or 1)
    return {
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


def mark_numeric_page_navigation_already_on_target(
    result: dict,
    *,
    active_page: int,
    url_after: str | None,
) -> dict:
    result.update(
        {
            "success": True,
            "status": "already_on_target_page",
            "method": "numeric_page_not_needed",
            "active_page_after": active_page,
            "url_after": url_after,
        }
    )
    return result


def resolve_numeric_page_navigation_active_before(
    result: dict,
    active_before: int | None,
    *,
    has_listing_table: bool,
    url_after: str | None,
) -> tuple[int | None, bool, dict]:
    if active_before is not None:
        return active_before, False, result
    if has_listing_table:
        result["active_page_before"] = 1
        result["active_page_assumed"] = True
        return 1, False, result
    result.update(
        {
            "status": "cannot_confirm_active_page",
            "error": "Nao foi possivel detectar a pagina ativa antes da navegacao.",
            "url_after": url_after,
        }
    )
    return None, True, result


def mark_numeric_page_navigation_target_not_found(
    result: dict,
    *,
    target_page_number: int,
    click_result: dict,
    sequential_result: dict | None,
) -> dict:
    sequential = sequential_result or {}
    result["status"] = (
        sequential.get("status")
        or click_result.get("stop_reason")
        or "pagination_numeric_target_not_found"
    )
    result["error"] = (
        sequential.get("error")
        or "Pagina numerica de origem nao encontrada: "
        f"{target_page_number}. Motivo: {click_result.get('stop_reason')}"
    )
    return result


def find_and_click_previous_listing_page(
    page,
    current_page_number: int,
    *,
    click_previous_listing_page_diagnostic,
) -> dict:
    previous_button = click_previous_listing_page_diagnostic(page)
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


def find_and_click_next_listing_page(
    page,
    current_page_number: int,
    *,
    find_and_click_next_numeric_page,
    click_next_listing_page_diagnostic,
) -> dict:
    numeric = find_and_click_next_numeric_page(page, current_page_number)
    if numeric.get("found"):
        return numeric

    next_button = click_next_listing_page_diagnostic(page)
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
