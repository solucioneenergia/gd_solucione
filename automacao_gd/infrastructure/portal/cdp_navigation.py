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


def mark_numeric_page_navigation_direct_click_disabled(
    result: dict,
    *,
    target_page_number: int,
    click_result: dict,
) -> dict:
    result["error"] = (
        "Pagina numerica de origem encontrada, mas desabilitada: "
        f"{target_page_number}. Motivo: {click_result.get('stop_reason')}"
    )
    result["status"] = click_result.get(
        "stop_reason", "pagination_numeric_target_disabled"
    )
    return result


def mark_numeric_page_navigation_direct_click_not_performed(
    result: dict,
    *,
    target_page_number: int,
    click_result: dict,
) -> dict:
    result["error"] = (
        "Pagina numerica de origem encontrada, mas nao clicada: "
        f"{target_page_number}. Motivo: {click_result.get('stop_reason')}"
    )
    result["status"] = click_result.get("stop_reason", "pagination_click_failed")
    return result


def mark_numeric_page_navigation_unconfirmed_active(result: dict) -> dict:
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_numeric_page_unconfirmed_active",
            "method": "recovered_listing_by_numeric_page_unconfirmed_active",
            "error": None,
        }
    )
    return result


def mark_numeric_page_navigation_active_mismatch(
    result: dict,
    *,
    target_page_number: int,
    active_after: int | None,
) -> dict:
    result["status"] = "pagination_active_page_mismatch"
    result["error"] = (
        f"Pagina ativa apos clique: {active_after}; esperado: {target_page_number}."
    )
    return result


def mark_numeric_page_navigation_click_no_change(
    result: dict,
    *,
    target_page_number: int,
) -> dict:
    result["status"] = "pagination_click_no_change"
    result["error"] = (
        "Clique na pagina numerica de origem nao alterou a tabela: "
        f"{target_page_number}."
    )
    return result


def mark_numeric_page_navigation_success(result: dict) -> dict:
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_numeric_page",
            "method": "recovered_listing_by_numeric_page",
            "error": None,
        }
    )
    return result


def mark_numeric_page_navigation_click_error(
    result: dict,
    *,
    error: Exception,
    url_after: str | None,
) -> dict:
    result["status"] = "pagination_numeric_click_error"
    result["error"] = str(error)
    result["url_after"] = url_after
    return result


def mark_numeric_page_navigation_recovery_success(
    result: dict,
    *,
    recovery_result: dict,
    method: str,
) -> dict:
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_numeric_page",
            "method": method,
            "active_page_after": recovery_result.get("active_page_after"),
            "signature_after": recovery_result.get("signature_after"),
            "error": None,
        }
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


def recover_minhas_solicitacoes(
    page,
    listing_url: str,
    *,
    has_minhas_solicitacoes_table,
    safe_page_url,
    is_unsafe_navigation_url,
    click_minhas_solicitacoes_navigation,
    wait_minhas_solicitacoes,
    goto_listing_url,
    reload_page,
    playwright_error,
    logger,
    manual_recovery_message=None,
    detail_timeout_ms: int = 20_000,
    allow_active_navigation: bool = True,
) -> dict:
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": None,
        "url_before": safe_page_url(page),
        "url_after": None,
        "error": None,
    }
    last_error: Exception | None = None

    if has_minhas_solicitacoes_table(page):
        result.update(
            {
                "success": True,
                "status": "ok",
                "method": "already_on_listing",
                "url_after": safe_page_url(page),
            }
        )
        return result

    recovery_message = (
        manual_recovery_message
        if manual_recovery_message is not None
        else lambda: "Nao foi possivel retornar para a tabela de listagem."
    )

    if not allow_active_navigation:
        result["error"] = recovery_message()
        result["url_after"] = safe_page_url(page)
        return result

    unsafe_url = is_unsafe_navigation_url(safe_page_url(page), listing_url)
    if unsafe_url:
        result["error"] = recovery_message()
        result["url_after"] = safe_page_url(page)
        return result

    if not unsafe_url and click_minhas_solicitacoes_navigation(page):
        if wait_minhas_solicitacoes(page):
            result.update(
                {
                    "success": True,
                    "status": "recovered_listing_by_menu",
                    "method": "recovered_listing_by_menu",
                    "url_after": safe_page_url(page),
                }
            )
            return result

    try:
        goto_listing_url(page, listing_url)
        if wait_minhas_solicitacoes(page):
            result.update(
                {
                    "success": True,
                    "status": "recovered_listing_by_url",
                    "method": "recovered_listing_by_url",
                    "url_after": safe_page_url(page),
                }
            )
            return result
    except playwright_error as exc:
        last_error = exc
        logger.debug(f"Falha ao navegar para URL salva da listagem: {exc}")

    try:
        reload_page(page)
        if wait_minhas_solicitacoes(page):
            result.update(
                {
                    "success": True,
                    "status": "recovered_listing_by_reload",
                    "method": "recovered_listing_by_reload",
                    "url_after": safe_page_url(page),
                }
            )
            return result
    except playwright_error as exc:
        last_error = exc
        logger.debug(f"Falha ao recarregar listagem: {exc}")

    if not unsafe_url:
        try:
            page.go_back(wait_until="networkidle", timeout=detail_timeout_ms)
            if wait_minhas_solicitacoes(page):
                result.update(
                    {
                        "success": True,
                        "status": "recovered_listing_by_history",
                        "method": "recovered_listing_by_history",
                        "url_after": safe_page_url(page),
                    }
                )
                return result
        except playwright_error as exc:
            last_error = exc
            logger.debug(f"Falha ao voltar pelo historico: {exc}")

    error = "Nao foi possivel retornar para a tabela de listagem."
    if last_error:
        error = f"{error} Ultimo erro: {last_error}"
    result["error"] = error
    result["url_after"] = safe_page_url(page)
    return result


def ensure_listing_page(
    page,
    listing_url: str,
    *,
    recover_minhas_solicitacoes,
):
    recovery = recover_minhas_solicitacoes(page, listing_url)
    if recovery["success"]:
        return page
    raise RuntimeError(recovery["error"])


def ensure_minhas_solicitacoes(
    page,
    listing_url: str,
    *,
    recover_minhas_solicitacoes,
) -> bool:
    return bool(recover_minhas_solicitacoes(page, listing_url)["success"])


def return_to_listing(
    page,
    listing_url: str,
    *,
    ensure_minhas_solicitacoes,
    logger,
) -> None:
    logger.info("Garantindo retorno para a listagem 'Minhas Solicitacoes'.")
    if ensure_minhas_solicitacoes(page, listing_url):
        return
    raise RuntimeError("Nao foi possivel retornar para a tabela de listagem.")


def return_to_listing_after_detail(
    detail_page,
    listing_page,
    listing_url: str,
    *,
    page_is_closed,
    recover_minhas_solicitacoes,
    recover_listing_in_new_context_page,
    recover_listing_by_detail_return_control,
    recover_listing_by_authenticated_home_icon,
    playwright_error,
    logger,
    allow_active_navigation: bool = False,
):
    if detail_page is not listing_page:
        if not page_is_closed(listing_page):
            if not page_is_closed(detail_page):
                try:
                    detail_page.close()
                except playwright_error as exc:
                    logger.debug(f"Falha ao fechar aba de detalhe: {exc}")
            recovery = recover_minhas_solicitacoes(
                listing_page,
                listing_url,
                allow_active_navigation=allow_active_navigation,
            )
            if not recovery["success"]:
                fallback_page, fallback_recovery = recover_listing_in_new_context_page(
                    listing_page,
                    listing_url,
                    previous_error=recovery.get("error"),
                    allow_active_navigation=allow_active_navigation,
                )
                if fallback_recovery["success"] or fallback_recovery.get("error"):
                    return fallback_page, fallback_recovery
            return listing_page, recovery
    recovery = recover_minhas_solicitacoes(
        detail_page,
        listing_url,
        allow_active_navigation=allow_active_navigation,
    )
    if not recovery["success"]:
        local_return_recovery = recover_listing_by_detail_return_control(
            detail_page,
            listing_url,
            previous_error=recovery.get("error"),
        )
        if local_return_recovery["success"]:
            return detail_page, local_return_recovery
        home_recovery = recover_listing_by_authenticated_home_icon(
            detail_page,
            listing_url,
            previous_error=recovery.get("error"),
        )
        if home_recovery["success"]:
            return detail_page, home_recovery
        fallback_page, fallback_recovery = recover_listing_in_new_context_page(
            detail_page,
            listing_url,
            previous_error=recovery.get("error"),
            allow_active_navigation=allow_active_navigation,
        )
        if fallback_recovery["success"] or fallback_recovery.get("error"):
            return fallback_page, fallback_recovery
    return detail_page, recovery


def recover_listing_in_new_context_page(
    page,
    listing_url: str,
    *,
    page_is_closed,
    safe_page_url,
    recover_minhas_solicitacoes,
    manual_recovery_message,
    playwright_error,
    logger,
    previous_error: str | None = None,
    allow_active_navigation: bool = True,
) -> tuple[object, dict]:
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": "existing_context_listing_only",
        "url_before": safe_page_url(page),
        "url_after": None,
        "error": previous_error or "Nao foi possivel retornar para a tabela de listagem.",
    }
    try:
        context = getattr(page, "context", None)
        if context is None:
            return page, result
        for candidate in list(getattr(context, "pages", []) or []):
            if candidate is page or page_is_closed(candidate):
                continue
            recovery = recover_minhas_solicitacoes(
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
        result["error"] = manual_recovery_message()
        return page, result
    except playwright_error as exc:
        result["error"] = str(exc)
        result["url_after"] = safe_page_url(page)
        logger.debug(f"Falha ao recuperar listagem em nova aba: {exc}")
        return page, result


def recover_listing_by_detail_return_control(
    page,
    listing_url: str,
    *,
    page_is_closed,
    safe_page_url,
    is_unsafe_authenticated_control_context_url,
    click_detail_return_to_listing,
    wait_minhas_solicitacoes,
    listing_has_rows,
    manual_recovery_message,
    playwright_error,
    previous_error: str | None = None,
) -> dict:
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": "recovered_listing_by_detail_return_control",
        "url_before": safe_page_url(page),
        "url_after": None,
        "error": previous_error or manual_recovery_message(),
    }
    if page_is_closed(page):
        result["url_after"] = safe_page_url(page)
        return result
    if is_unsafe_authenticated_control_context_url(safe_page_url(page), listing_url):
        result["error"] = manual_recovery_message()
        result["url_after"] = safe_page_url(page)
        return result
    if not click_detail_return_to_listing(page):
        result["url_after"] = safe_page_url(page)
        return result
    try:
        page.wait_for_timeout(500)
    except playwright_error:
        pass
    if wait_minhas_solicitacoes(page) and listing_has_rows(page):
        result.update(
            {
                "success": True,
                "status": "recovered_listing_by_detail_return_control",
                "url_after": safe_page_url(page),
                "error": None,
            }
        )
        return result
    result["error"] = manual_recovery_message()
    result["url_after"] = safe_page_url(page)
    return result


def successful_home_listing_recovery(page, result: dict, *, safe_page_url) -> dict:
    result.update(
        {
            "success": True,
            "status": "recovered_listing_by_authenticated_home_icon",
            "url_after": safe_page_url(page),
            "error": None,
        }
    )
    return result


def recover_listing_by_authenticated_home_icon(
    page,
    listing_url: str,
    *,
    page_is_closed,
    page_looks_access_denied,
    is_unsafe_authenticated_control_context_url,
    safe_page_url,
    context_pages_snapshot,
    click_authenticated_home_icon,
    context_pages_changed,
    wait_minhas_solicitacoes,
    listing_has_rows,
    click_home_minhas_solicitacoes_control,
    is_insecure_portal_http_url,
    manual_recovery_message,
    playwright_error,
    previous_error: str | None = None,
) -> dict:
    result = {
        "success": False,
        "status": "failed_return_to_listing",
        "method": "recovered_listing_by_authenticated_home_icon",
        "url_before": safe_page_url(page),
        "url_after": None,
        "error": previous_error or manual_recovery_message(),
    }
    if page_is_closed(page) or page_looks_access_denied(page):
        result["error"] = manual_recovery_message()
        result["url_after"] = safe_page_url(page)
        return result
    if is_unsafe_authenticated_control_context_url(safe_page_url(page), listing_url):
        result["error"] = manual_recovery_message()
        result["url_after"] = safe_page_url(page)
        return result

    pages_before = context_pages_snapshot(page)
    if not click_authenticated_home_icon(page, listing_url):
        result["url_after"] = safe_page_url(page)
        return result
    if context_pages_changed(page, pages_before):
        result["error"] = manual_recovery_message()
        result["url_after"] = safe_page_url(page)
        return result

    try:
        page.wait_for_timeout(500)
    except playwright_error:
        pass

    if page_looks_access_denied(page) or is_insecure_portal_http_url(safe_page_url(page)):
        result["error"] = manual_recovery_message()
        result["url_after"] = safe_page_url(page)
        return result
    if wait_minhas_solicitacoes(page) and listing_has_rows(page):
        return successful_home_listing_recovery(page, result, safe_page_url=safe_page_url)
    if click_home_minhas_solicitacoes_control(page, listing_url):
        try:
            page.wait_for_timeout(500)
        except playwright_error:
            pass
        if (
            not page_looks_access_denied(page)
            and not is_insecure_portal_http_url(safe_page_url(page))
            and wait_minhas_solicitacoes(page)
            and listing_has_rows(page)
        ):
            return successful_home_listing_recovery(
                page,
                result,
                safe_page_url=safe_page_url,
            )

    result["error"] = manual_recovery_message()
    result["url_after"] = safe_page_url(page)
    return result
