from automacao_gd.infrastructure.portal.cdp_service import (
    POINT_OF_CONNECTION_STAGE,
    extract_point_of_connection_completion_from_text,
)


def test_extracts_only_point_of_connection_completion_date() -> None:
    text = """
    Aguardando Documentação
    Concluído em 02/07/2026
    Em Análise Técnica
    Concluído em 08/07/2026
    Aguardando solicitação de vistoria e Conexão
    Concluído em 17/07/2026
    Realizando vistoria e Conexão
    Concluído em 24/07/2026
    Ponto de Conexão Aprovado
    Concluído em 25/07/2026
    Solicitação Concluída
    """

    result = extract_point_of_connection_completion_from_text(text, protocol="2600000001")

    assert result["completion_date_raw"] == "25/07/2026"
    assert result["completion_date_normalized"] == "2026-07-25"
    assert result["completion_source_stage"] == POINT_OF_CONNECTION_STAGE
    assert result["completion_extraction_status"] == "FOUND"


def test_extracts_stage_date_when_steps_are_in_different_order() -> None:
    text = """
    Solicitação Concluída
    Realizando vistoria e Conexão
    Concluído em 24/07/2026
    PONTO   DE
    CONEXÃO    APROVADO
    Concluído em 25/07/2026
    Em Análise Técnica
    Concluído em 08/07/2026
    """

    result = extract_point_of_connection_completion_from_text(text, protocol="2600000002")

    assert result["completion_date_raw"] == "25/07/2026"
    assert result["completion_extraction_status"] == "FOUND"


def test_stage_without_date_returns_not_available() -> None:
    text = """
    Em Análise Técnica
    Concluído em 08/07/2026
    Ponto de Conexão Aprovado
    Solicitação Concluída
    Concluído em 30/07/2026
    """

    result = extract_point_of_connection_completion_from_text(text, protocol="2600000003")

    assert result["completion_date_raw"] is None
    assert result["completion_extraction_status"] == "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE"


def test_missing_stage_does_not_use_global_date() -> None:
    text = """
    Aguardando Documentação
    Concluído em 02/07/2026
    Solicitação Concluída
    Concluído em 30/07/2026
    """

    result = extract_point_of_connection_completion_from_text(text, protocol="2600000004")

    assert result["completion_date_raw"] is None
    assert result["completion_extraction_status"] == "POINT_OF_CONNECTION_STAGE_NOT_FOUND"


def test_ambiguous_stage_dates_are_not_chosen() -> None:
    text = """
    Ponto de Conexão Aprovado
    Concluído em 25/07/2026
    Concluído em 26/07/2026
    Solicitação Concluída
    """

    result = extract_point_of_connection_completion_from_text(text, protocol="2600000005")

    assert result["completion_date_raw"] is None
    assert result["completion_extraction_status"] == "AMBIGUOUS_POINT_OF_CONNECTION_COMPLETION_DATE"
