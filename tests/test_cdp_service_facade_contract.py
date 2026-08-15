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
