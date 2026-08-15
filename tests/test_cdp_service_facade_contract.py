import inspect

from automacao_gd.infrastructure.portal import cdp_service
from automacao_gd.infrastructure.portal.cdp_errors import (
    DownloadNotProducedError as InternalDownloadNotProducedError,
)
from automacao_gd.infrastructure.portal.cdp_extractors import (
    POINT_OF_CONNECTION_STAGE as INTERNAL_POINT_OF_CONNECTION_STAGE,
)
from automacao_gd.infrastructure.portal.cdp_extractors import (
    extract_point_of_connection_completion_from_text as internal_completion_extractor,
)
from automacao_gd.infrastructure.portal.cdp_navigation import (
    _is_portal_root_or_index_url as internal_is_portal_root_or_index_url,
)
from automacao_gd.infrastructure.portal.cdp_navigation import (
    _is_unsafe_navigation_url as internal_is_unsafe_navigation_url,
)
from automacao_gd.infrastructure.portal.cdp_navigation import (
    is_insecure_portal_http_url as internal_is_insecure_portal_http_url,
)


def test_cdp_service_keeps_completion_extraction_facade() -> None:
    assert cdp_service.POINT_OF_CONNECTION_STAGE == INTERNAL_POINT_OF_CONNECTION_STAGE
    assert (
        cdp_service.extract_point_of_connection_completion_from_text
        is internal_completion_extractor
    )
    assert inspect.signature(
        cdp_service.extract_point_of_connection_completion_from_text
    ) == inspect.signature(internal_completion_extractor)


def test_cdp_service_keeps_download_error_facade() -> None:
    assert cdp_service.DownloadNotProducedError is InternalDownloadNotProducedError


def test_cdp_service_keeps_navigation_facade() -> None:
    assert cdp_service._is_unsafe_navigation_url is internal_is_unsafe_navigation_url
    assert (
        cdp_service._is_portal_root_or_index_url
        is internal_is_portal_root_or_index_url
    )
    assert cdp_service.is_insecure_portal_http_url is internal_is_insecure_portal_http_url


def test_cdp_service_keeps_numeric_paginator_click_signature() -> None:
    signature = inspect.signature(cdp_service._click_numeric_paginator_with_playwright)

    assert list(signature.parameters) == ["page", "target_page_number"]
    assert (
        signature.parameters["target_page_number"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


def test_cdp_service_keeps_previous_listing_page_signature() -> None:
    signature = inspect.signature(cdp_service.find_and_click_previous_listing_page)

    assert list(signature.parameters) == ["page", "current_page_number"]


def test_cdp_service_keeps_next_listing_page_signature() -> None:
    signature = inspect.signature(cdp_service.find_and_click_next_listing_page)

    assert list(signature.parameters) == ["page", "current_page_number"]


def test_cdp_service_keeps_listing_page_diagnostic_signatures() -> None:
    next_signature = inspect.signature(cdp_service._click_next_listing_page_diagnostic)
    previous_signature = inspect.signature(
        cdp_service._click_previous_listing_page_diagnostic
    )

    assert list(next_signature.parameters) == ["page"]
    assert list(previous_signature.parameters) == ["page"]


def test_cdp_service_keeps_numeric_page_navigation_signature() -> None:
    signature = inspect.signature(cdp_service.navigate_to_numeric_page)

    assert list(signature.parameters) == ["page", "target_page_number"]


def test_cdp_service_keeps_listing_recovery_signatures() -> None:
    ensure_listing_signature = inspect.signature(cdp_service.ensure_listing_page)
    ensure_minhas_signature = inspect.signature(cdp_service.ensure_minhas_solicitacoes)
    return_signature = inspect.signature(cdp_service.return_to_listing)
    recover_signature = inspect.signature(cdp_service._recover_minhas_solicitacoes)
    after_detail_signature = inspect.signature(cdp_service._return_to_listing_after_detail)

    assert list(ensure_listing_signature.parameters) == ["page", "listing_url"]
    assert list(ensure_minhas_signature.parameters) == ["page", "listing_url"]
    assert list(return_signature.parameters) == ["page", "listing_url"]
    assert list(recover_signature.parameters) == [
        "page",
        "listing_url",
        "allow_active_navigation",
    ]
    assert (
        recover_signature.parameters["allow_active_navigation"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
    assert list(after_detail_signature.parameters) == [
        "detail_page",
        "listing_page",
        "listing_url",
        "allow_active_navigation",
    ]
    assert (
        after_detail_signature.parameters["allow_active_navigation"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )
