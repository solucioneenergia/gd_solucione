from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.application.contracts import OperationResult, OperationStatus
from automacao_gd.application.operational_guard import prepare_offline_batch
from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.presentation.cli import _confirmed_real_processing


def _synthetic_pdf(root: Path, protocol: str) -> Path:
    path = root / f"Orcamento_de_Conexao_{protocol}.pdf"
    path.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    return path


def test_prepare_offline_batch_rejects_implicit_directory_scan(tmp_path: Path) -> None:
    _synthetic_pdf(tmp_path, "2600000000")

    with pytest.raises(OperationalBlockError) as exc:
        prepare_offline_batch(tmp_path, requested_limit=1)

    assert exc.value.code == "FROZEN_BATCH_SCOPE_VIOLATION"


def test_prepare_offline_batch_freezes_only_explicit_file_and_protocol(
    tmp_path: Path,
) -> None:
    selected = _synthetic_pdf(tmp_path, "2600000000")
    _synthetic_pdf(tmp_path, "2600000001")

    batch = prepare_offline_batch(
        tmp_path,
        requested_limit=1,
        selections=[(selected.name, "2600000000")],
    )

    assert batch.pdf_paths == (selected.resolve(),)
    assert batch.protocols == ("2600000000",)


def test_prepare_offline_batch_rejects_declared_protocol_mismatch(
    tmp_path: Path,
) -> None:
    selected = _synthetic_pdf(tmp_path, "2600000000")

    with pytest.raises(OperationalBlockError) as exc:
        prepare_offline_batch(
            tmp_path,
            requested_limit=1,
            selections=[(selected.name, "2600000001")],
        )

    assert exc.value.code == "FROZEN_BATCH_SCOPE_VIOLATION"


def test_option4_cli_requires_explicit_selection_and_prints_sanitized_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = _synthetic_pdf(tmp_path, "2600000000")

    class Controller:
        settings = SimpleNamespace(
            MAX_COMPLETED_TO_PROCESS=1,
            downloads_dir_path=tmp_path,
        )

        def process_downloads(self, **_: object) -> OperationResult:
            return OperationResult(True, "processado", status=OperationStatus.SUCESSO)

    answers = iter(
        [
            f"{selected.name}|2600000000",
            "APLICAR OPCAO 4 EM 1 PROTOCOLOS",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    result = _confirmed_real_processing(Controller())

    output = capsys.readouterr().out
    assert result.status is OperationStatus.SUCESSO
    assert "PDFs congelados: 1" in output
    assert selected.name not in output
    assert str(tmp_path) not in output
