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
