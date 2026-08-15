from types import SimpleNamespace

import pytest

from automacao_gd.infrastructure.portal import cdp_detail


class _BodyLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    def inner_text(self, timeout: int) -> str:
        assert timeout == 10_000
        return self.text


class _DetailPage:
    def __init__(self, *, body: str, titles: list[str] | None = None) -> None:
        self.body = body
        self.titles = titles or []

    def locator(self, selector: str):
        assert selector == "body"
        return _BodyLocator(self.body)

    def evaluate(self, script: str):
        assert "document.title" in script
        return self.titles


def test_extract_detail_header_reads_protocol_and_client_from_detail_title() -> None:
    page = _DetailPage(
        body="Unidade consumidora\nTitular: Cliente do Corpo",
        titles=["Solicitação 2600001107: CLIENTE SINTETICO LTDA"],
    )

    header = cdp_detail.extract_detail_header(page)

    assert header == {
        "detail_protocol": "2600001107",
        "detail_client_name": "SINTETICO LTDA",
    }


def test_extract_detail_header_falls_back_to_body_client_when_title_has_only_protocol() -> None:
    page = _DetailPage(
        body="Dados gerais\nCliente: CLIENTE SINTETICO LTDA\nEtapa atual",
        titles=["Detalhe do protocolo 2600001106"],
    )

    header = cdp_detail.extract_detail_header(page)

    assert header == {
        "detail_protocol": "2600001106",
        "detail_client_name": "SINTETICO LTDA",
    }


def test_detail_has_completed_status_normalizes_accented_text() -> None:
    page = _DetailPage(body="Histórico\nSolicitação Concluída\nFim")

    assert cdp_detail.detail_has_completed_status(page) is True


def test_click_follow_eye_button_uses_explicit_action_cell_index() -> None:
    clicked = []

    class Locator:
        def __init__(self, name: str) -> None:
            self.name = name

        def locator(self, selector: str):
            return Locator(selector)

        def nth(self, index: int):
            clicked.append(("cell", index))
            return self

    class Row:
        def locator(self, selector: str):
            assert selector == "td"
            return Locator("td")

    def click_first_visible(locator):
        clicked.append(("click", locator.name))
        return "button" in locator.name

    cdp_detail.click_follow_eye_button(
        Row(),
        3,
        click_first_visible=click_first_visible,
        logger=SimpleNamespace(info=lambda message: None),
    )

    assert clicked == [
        ("cell", 3),
        (
            "click",
            "button, a, input[type='button'], input[type='submit'], [role='button']",
        ),
    ]


def test_click_follow_eye_button_raises_when_no_visible_control() -> None:
    class Locator:
        def count(self) -> int:
            return 1

        def locator(self, selector: str):
            return self

        def nth(self, index: int):
            return self

    with pytest.raises(
        RuntimeError,
        match="Não foi encontrado botão/link visível na coluna ACOMPANHAR",
    ):
        cdp_detail.click_follow_eye_button(
            Locator(),
            None,
            click_first_visible=lambda locator: False,
            logger=SimpleNamespace(info=lambda message: None),
        )


def test_wait_detail_loaded_waits_for_protocol_text() -> None:
    events = []

    class TextLocator:
        @property
        def first(self):
            return self

        def wait_for(self, timeout: int) -> None:
            events.append(("wait_text", timeout))

    class Page:
        def wait_for_load_state(self, state: str, timeout: int) -> None:
            events.append(("load", state, timeout))

        def get_by_text(self, value):
            events.append(("text", value))
            return TextLocator()

    cdp_detail.wait_detail_loaded(
        Page(),
        "2600001107",
        detail_timeout_ms=20_000,
        playwright_error=RuntimeError,
        playwright_timeout_error=TimeoutError,
        logger=SimpleNamespace(warning=lambda message: None),
    )

    assert events == [
        ("load", "domcontentloaded", 20_000),
        ("text", "2600001107"),
        ("wait_text", 20_000),
    ]
