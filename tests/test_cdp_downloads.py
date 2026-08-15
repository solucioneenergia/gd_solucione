from pathlib import Path

import pytest

from automacao_gd.infrastructure.portal import cdp_downloads
from automacao_gd.infrastructure.portal.cdp_errors import DownloadNotProducedError


def test_describe_no_download_reports_new_page_opened_instead_of_download() -> None:
    class Page:
        url = "https://portal/detalhe"

        class Context:
            pages = []

        context = Context()

    before_page = Page()
    opened_page = Page()
    opened_page.url = "https://portal/orcamento.pdf"
    page = Page()
    page.context.pages = [before_page, opened_page]

    message = cdp_downloads.describe_no_download(
        page,
        [before_page],
        download_timeout_ms=5_000,
    )

    assert message == (
        "O Orçamento de Conexão abriu em nova aba em vez de baixar. "
        "Abas novas: https://portal/orcamento.pdf"
    )


def test_describe_no_download_reports_current_pdf_url() -> None:
    class Page:
        url = "https://portal/download/orcamento.PDF"

        class Context:
            pages = []

        context = Context()

    page = Page()
    page.context.pages = [page]

    message = cdp_downloads.describe_no_download(
        page,
        [page],
        download_timeout_ms=5_000,
    )

    assert message == (
        "O Orçamento de Conexão parece ter aberto na aba atual em vez de baixar. "
        "URL atual: https://portal/download/orcamento.PDF"
    )


def test_describe_no_download_reports_configured_timeout_seconds() -> None:
    class Page:
        url = "https://portal/detalhe"

        class Context:
            pages = []

        context = Context()

    page = Page()
    page.context.pages = [page]

    message = cdp_downloads.describe_no_download(
        page,
        [page],
        download_timeout_ms=5_000,
    )

    assert message == (
        "Clique em 'Orçamento de Conexão' não gerou download dentro do timeout de 5s."
    )


def test_next_budget_path_preserves_pdf_suffix_and_versions_existing_file(
    tmp_path: Path,
) -> None:
    protocol_dir = tmp_path / "2600000001"
    protocol_dir.mkdir()
    first_path = protocol_dir / "Orcamento_de_Conexao_2600000001.pdf"
    first_path.write_bytes(b"%PDF-1.4\n")

    destination = cdp_downloads.next_budget_path(
        protocol_dir,
        "2600000001",
        "arquivo-original.PDF",
    )

    assert destination == protocol_dir / "Orcamento_de_Conexao_2600000001_v2.pdf"


def test_find_existing_connection_budget_pdf_returns_newest_protocol_pdf(
    tmp_path: Path,
) -> None:
    protocol_dir = tmp_path / "2600000001"
    protocol_dir.mkdir()
    older = protocol_dir / "Orcamento_de_Conexao_2600000001.pdf"
    newer = protocol_dir / "Orcamento_de_Conexao_2600000001_v2.pdf"
    ignored = protocol_dir / "Outro_Arquivo.pdf"
    older.write_bytes(b"%PDF-1.4\n")
    newer.write_bytes(b"%PDF-1.4\n")
    ignored.write_bytes(b"%PDF-1.4\n")

    older.touch()
    newer.touch()

    assert (
        cdp_downloads.find_existing_connection_budget_pdf(
            "2600000001",
            tmp_path,
        )
        == newer
    )


def test_download_connection_budget_raises_unavailable_when_click_produces_no_download(
    tmp_path: Path,
) -> None:
    class DownloadContext:
        value = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            raise TimeoutError("timeout")

    class Page:
        url = "https://portal/detalhe"

        class Context:
            pages = []

        context = Context()

        def expect_download(self, timeout: int):
            assert timeout == 5_000
            return DownloadContext()

    class Target:
        def click(self, **kwargs) -> None:
            assert kwargs == {"timeout": 10_000, "no_wait_after": True}

    page = Page()
    page.context.pages = [page]

    with pytest.raises(
        DownloadNotProducedError,
        match="não gerou download dentro do timeout de 5s",
    ):
        cdp_downloads.download_connection_budget(
            page,
            "2600000001",
            downloads_root=tmp_path,
            target=Target(),
            playwright_timeout_error=TimeoutError,
        )
