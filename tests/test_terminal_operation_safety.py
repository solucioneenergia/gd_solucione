from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.application import full_pipeline, processing_service
from automacao_gd.application.contracts import OperationResult, OperationStatus
from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.infrastructure.locking import ExecutionLock
from automacao_gd.presentation.cli import _confirmed_pipeline, _confirmed_real_processing
from tests._operational_auth import authorize_synthetic_pdfs


class _CancelledController:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(DRY_RUN=False, MAX_COMPLETED_TO_PROCESS=1)
        self.process_called = False
        self.pipeline_called = False

    def preflight(self, **_: object) -> OperationResult:
        return OperationResult(True, "preflight aprovado", status=OperationStatus.SUCESSO)

    def process_downloads(self, *, dry_run: bool) -> OperationResult:
        self.process_called = True
        return OperationResult(True, "processado", status=OperationStatus.SUCESSO)

    def run_pipeline(self) -> OperationResult:
        self.pipeline_called = True
        return OperationResult(True, "processado", status=OperationStatus.SUCESSO)


@pytest.mark.parametrize("confirmation", ["SIM", "APLICAR OPCAO 4"])
def test_option4_rejects_generic_or_partial_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    confirmation: str,
) -> None:
    controller = _CancelledController()
    controller.settings.downloads_dir_path = tmp_path
    (tmp_path / "Orcamento_de_Conexao_2600000000.pdf").write_bytes(
        b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF"
    )
    answers = iter(
        [
            "Orcamento_de_Conexao_2600000000.pdf|2600000000",
            confirmation,
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    result = _confirmed_real_processing(controller)

    assert result.status is OperationStatus.BLOQUEADO
    assert result.payload["code"] == "STRONG_CONFIRMATION_MISMATCH"
    assert controller.process_called is False


def test_option5_cancellation_never_reuses_successful_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _CancelledController()
    monkeypatch.setattr("builtins.input", lambda _: "SIM")

    result = _confirmed_pipeline(controller)

    assert result.status is OperationStatus.BLOQUEADO
    assert result.payload["code"] == "STRONG_CONFIRMATION_MISMATCH"
    assert controller.pipeline_called is False


def test_full_pipeline_simple_sim_does_not_authorize_real_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        DRY_RUN=False,
        APPLY_EXCEL=True,
        APPLY_ARCHIVE=False,
        MAX_COMPLETED_TO_PROCESS=1,
        planilha_path=Path("planilha-sintetica.xlsx"),
        clientes_root_path=Path("clientes-sinteticos"),
    )
    monkeypatch.setattr("builtins.input", lambda _: "SIM")

    assert full_pipeline.confirm_real_run_if_needed(settings) is False


def test_full_pipeline_real_boundary_requires_confirmation_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        DRY_RUN=False,
        APPLY_EXCEL=True,
        APPLY_ARCHIVE=False,
        MAX_COMPLETED_TO_PROCESS=1,
        option5_execution_lock_path=tmp_path / "operacao-global.lock",
    )
    external_calls: list[str] = []
    monkeypatch.setattr(
        full_pipeline,
        "run_preflight",
        lambda *args, **kwargs: external_calls.append("preflight"),
    )
    monkeypatch.setattr(
        full_pipeline,
        "_run_download_step",
        lambda *args, **kwargs: external_calls.append("download"),
    )

    with pytest.raises(OperationalBlockError) as exc:
        full_pipeline.run_full_cdp_pipeline(settings)

    assert exc.value.code == "STRONG_CONFIRMATION_MISMATCH"
    assert external_calls == []


def test_full_pipeline_dry_run_boundary_requires_confirmation_before_portal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        DRY_RUN=True,
        APPLY_EXCEL=False,
        APPLY_ARCHIVE=False,
        MAX_COMPLETED_TO_PROCESS=1,
        option5_execution_lock_path=tmp_path / "operacao-global.lock",
    )
    external_calls: list[str] = []
    monkeypatch.setattr(
        full_pipeline,
        "run_preflight",
        lambda *args, **kwargs: external_calls.append("preflight"),
    )

    with pytest.raises(OperationalBlockError) as exc:
        full_pipeline.run_full_cdp_pipeline(settings)

    assert exc.value.code == "STRONG_CONFIRMATION_MISMATCH"
    assert external_calls == []


def test_extracted_protocol_mismatch_blocks_before_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000001.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    logs = tmp_path / "logs"
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=logs, BACKUP_EXCEL=False),
    )
    monkeypatch.setattr(
        processing_service,
        "_real_run_preflight_result",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        processing_service,
        "_process_single_pdf",
        lambda *args, **kwargs: {
            "pdf_path": str(pdf),
            "protocol": "2600000002",
            "success": True,
        },
    )
    monkeypatch.setattr(processing_service, "_critical_simulation_issues", lambda *_: [])
    monkeypatch.setattr(processing_service, "_has_processable_protocols", lambda *_: True)

    def fail_if_applied(**_: object) -> list[dict]:
        raise AssertionError("aplicacao real nao pode iniciar fora do lote congelado")

    monkeypatch.setattr(
        processing_service,
        "_apply_processable_subset_from_simulation",
        fail_if_applied,
    )

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha-sintetica.xlsx",
        clientes_root=tmp_path / "clientes-sinteticos",
        dry_run=False,
        pdf_paths=[pdf],
        allowed_protocols={"2600000001"},
        authorization=authorize_synthetic_pdfs(tmp_path, [pdf]),
    )

    assert payload["status"] == OperationStatus.BLOQUEADO.value
    assert payload["real_run_block_code"] == "FROZEN_BATCH_SCOPE_VIOLATION"
    assert payload["total_excel_updated"] == 0
    assert payload["total_archived"] == 0


def test_option4_batch_requires_authorized_limit(tmp_path: Path) -> None:
    from automacao_gd.application.operational_guard import prepare_offline_batch

    with pytest.raises(OperationalBlockError) as exc:
        prepare_offline_batch(tmp_path, requested_limit=0)

    assert exc.value.code == "BATCH_LIMIT_NOT_AUTHORIZED"


def test_option4_freezes_at_most_five_explicit_pdfs(tmp_path: Path) -> None:
    from automacao_gd.application.operational_guard import prepare_offline_batch

    for offset in range(6):
        protocol = f"{2600000000 + offset}"
        (tmp_path / f"Orcamento_de_Conexao_{protocol}.pdf").write_bytes(
            b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF"
        )

    batch = prepare_offline_batch(
        tmp_path,
        requested_limit=5,
        selections=[
            (
                f"Orcamento_de_Conexao_{2600000000 + offset}.pdf",
                f"{2600000000 + offset}",
            )
            for offset in range(5)
        ],
    )

    assert len(batch.items) == 5
    assert len(batch.pdf_paths) == 5
    assert batch.protocols == tuple(f"{2600000000 + offset}" for offset in range(5))
    assert len(batch.digest) == 64


def test_option4_fingerprint_change_invalidates_frozen_batch(tmp_path: Path) -> None:
    from automacao_gd.application.operational_guard import (
        prepare_offline_batch,
        validate_frozen_batch,
    )

    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nVERSAO A\n%%EOF")
    batch = prepare_offline_batch(
        tmp_path,
        requested_limit=1,
        selections=[(pdf.name, "2600000000")],
    )
    pdf.write_bytes(b"%PDF-1.4\nVERSAO B\n%%EOF")

    with pytest.raises(OperationalBlockError) as exc:
        validate_frozen_batch(batch)

    assert exc.value.code == "FROZEN_BATCH_SCOPE_VIOLATION"


@pytest.mark.parametrize("confirmation", ["SIM", "APLICAR OPCAO 4 EM 1"])
def test_option4_authorization_rejects_non_exact_phrase(
    tmp_path: Path,
    confirmation: str,
) -> None:
    from automacao_gd.application.operational_guard import (
        authorize_offline_batch,
        prepare_offline_batch,
    )

    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF"
    )
    batch = prepare_offline_batch(
        tmp_path,
        requested_limit=1,
        selections=[(pdf.name, "2600000000")],
    )

    with pytest.raises(OperationalBlockError) as exc:
        authorize_offline_batch(batch, confirmation)

    assert exc.value.code == "STRONG_CONFIRMATION_MISMATCH"


def test_real_offline_use_case_requires_authorization_before_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automacao_gd.application.use_cases import process_downloads as module

    settings = SimpleNamespace(
        downloads_dir_path=tmp_path,
        planilha_path=tmp_path / "planilha-sintetica.xlsx",
        clientes_root_path=tmp_path / "clientes-sinteticos",
        APPLY_EXCEL=True,
        APPLY_ARCHIVE=True,
        option5_execution_lock_path=tmp_path / "operacao-global.lock",
    )
    preflight_called = False

    def unexpected_preflight(*args: object, **kwargs: object) -> object:
        nonlocal preflight_called
        preflight_called = True
        raise AssertionError("preflight nao deve anteceder autorizacao")

    monkeypatch.setattr(module, "run_preflight", unexpected_preflight)

    with pytest.raises(OperationalBlockError) as exc:
        module.ProcessDownloadedPdfsUseCase(settings).execute(dry_run=False)

    assert exc.value.code == "BATCH_LIMIT_NOT_AUTHORIZED"
    assert preflight_called is False


def test_real_offline_use_case_rejects_concurrent_global_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automacao_gd.application.operational_guard import (
        authorize_offline_batch,
        prepare_offline_batch,
    )
    from automacao_gd.application.use_cases import process_downloads as module

    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    lock_path = tmp_path / "operacao-global.lock"
    settings = SimpleNamespace(
        downloads_dir_path=tmp_path,
        planilha_path=tmp_path / "planilha-sintetica.xlsx",
        clientes_root_path=tmp_path / "clientes-sinteticos",
        APPLY_EXCEL=True,
        APPLY_ARCHIVE=True,
        option5_execution_lock_path=lock_path,
    )
    batch = prepare_offline_batch(
        tmp_path,
        requested_limit=1,
        selections=[(pdf.name, "2600000000")],
    )
    authorization = authorize_offline_batch(
        batch,
        "APLICAR OPCAO 4 EM 1 PROTOCOLOS",
    )
    monkeypatch.setattr(
        module,
        "run_preflight",
        lambda *args, **kwargs: SimpleNamespace(ready=True),
    )

    with ExecutionLock(
        lock_path,
        execution_id="execucao-sintetica",
        operation="option5",
        requested_batch_limit=1,
        authorization_scope="CONTROLLED_PRODUCTION_V2_0_1",
    ):
        with pytest.raises(OperationalBlockError) as exc:
            module.ProcessDownloadedPdfsUseCase(settings).execute(
                dry_run=False,
                authorization=authorization,
            )

    assert exc.value.code in {
        "GLOBAL_EXECUTION_LOCKED",
        "GLOBAL_EXECUTION_LOCK_REENTRANT",
    }


def test_option4_cli_blocks_missing_authorized_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _CancelledController()
    controller.settings.MAX_COMPLETED_TO_PROCESS = 0
    controller.settings.downloads_dir_path = tmp_path
    monkeypatch.setattr("builtins.input", lambda _: "APLICAR OPCAO 4 EM 0 PROTOCOLOS")

    result = _confirmed_real_processing(controller)

    assert result.status is OperationStatus.BLOQUEADO
    assert result.payload["code"] == "BATCH_LIMIT_NOT_AUTHORIZED"
    assert controller.process_called is False


def test_option4_cli_passes_frozen_explicit_batch_to_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automacao_gd.application.operational_guard import OfflineOperationAuthorization

    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")

    class Controller:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                MAX_COMPLETED_TO_PROCESS=1,
                downloads_dir_path=tmp_path,
            )
            self.authorization: OfflineOperationAuthorization | None = None

        def process_downloads(
            self,
            *,
            dry_run: bool,
            authorization: OfflineOperationAuthorization | None = None,
        ) -> OperationResult:
            assert dry_run is False
            self.authorization = authorization
            return OperationResult(True, "processado", status=OperationStatus.SUCESSO)

    controller = Controller()
    answers = iter(
        [
            f"{pdf.name}|2600000000",
            "APLICAR OPCAO 4 EM 1 PROTOCOLOS",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    result = _confirmed_real_processing(controller)

    assert result.status is OperationStatus.SUCESSO
    assert controller.authorization is not None
    assert controller.authorization.batch.pdf_paths == (pdf.resolve(),)
    assert controller.authorization.batch.protocols == ("2600000000",)


def test_real_offline_use_case_passes_only_frozen_scope_to_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automacao_gd.application.operational_guard import (
        authorize_offline_batch,
        prepare_offline_batch,
    )
    from automacao_gd.application.use_cases import process_downloads as module

    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    settings = SimpleNamespace(
        downloads_dir_path=tmp_path,
        planilha_path=tmp_path / "planilha-sintetica.xlsx",
        clientes_root_path=tmp_path / "clientes-sinteticos",
        APPLY_EXCEL=True,
        APPLY_ARCHIVE=True,
        option5_execution_lock_path=tmp_path / "operacao-global.lock",
    )
    authorization = authorize_offline_batch(
        prepare_offline_batch(
            tmp_path,
            requested_limit=1,
            selections=[(pdf.name, "2600000000")],
        ),
        "APLICAR OPCAO 4 EM 1 PROTOCOLOS",
    )
    monkeypatch.setattr(
        module,
        "run_preflight",
        lambda *args, **kwargs: SimpleNamespace(ready=True),
    )
    captured: dict[str, object] = {}

    def fake_processing(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {"status": OperationStatus.SUCESSO.value}

    monkeypatch.setattr(module, "process_downloaded_pdfs", fake_processing)

    payload = module.ProcessDownloadedPdfsUseCase(settings).execute(
        dry_run=False,
        authorization=authorization,
    )

    assert payload["status"] == OperationStatus.SUCESSO.value
    assert captured["pdf_paths"] == (pdf.resolve(),)
    assert captured["allowed_protocols"] == {"2600000000"}


def test_full_pipeline_main_returns_nonzero_when_confirmation_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace()
    monkeypatch.setattr(full_pipeline, "ensure_directories", lambda: None)
    monkeypatch.setattr(full_pipeline, "setup_logger", lambda: None)
    monkeypatch.setattr(full_pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(full_pipeline, "confirm_real_run_if_needed", lambda _: False)

    assert full_pipeline.main() == 2


def test_direct_offline_script_fails_closed_in_real_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import process_downloaded_pdfs as script

    monkeypatch.setattr(script, "ensure_directories", lambda: None)
    monkeypatch.setattr(script, "setup_logger", lambda: None)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace(DRY_RUN=False))
    monkeypatch.setattr(
        script,
        "process_downloaded_pdfs",
        lambda **_: (_ for _ in ()).throw(AssertionError("escrita real iniciada")),
    )

    assert script.main() == 2


def test_direct_download_script_fails_closed_before_portal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import download_completed_budgets_cdp as script

    monkeypatch.setattr(script, "ensure_directories", lambda: None)
    monkeypatch.setattr(script, "setup_logger", lambda: None)
    monkeypatch.setattr(
        script,
        "run_download_completed_budgets_cdp",
        lambda: (_ for _ in ()).throw(AssertionError("Portal iniciado")),
    )

    assert script.main() == 2


def test_first_solicitation_download_script_fails_closed_before_portal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import process_first_solicitation_cdp as script

    monkeypatch.setattr(script, "ensure_directories", lambda: None)
    monkeypatch.setattr(script, "setup_logger", lambda: None)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        script,
        "create_portal_automation",
        lambda *_: (_ for _ in ()).throw(AssertionError("Portal iniciado")),
    )

    assert script.main() == 2


def test_direct_repair_script_fails_closed_in_real_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import repair_workbook_format as script

    monkeypatch.setattr(script, "ensure_directories", lambda: None)
    monkeypatch.setattr(script, "setup_logger", lambda: None)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace(DRY_RUN=False))
    monkeypatch.setattr(
        script,
        "repair_workbook_format",
        lambda **_: (_ for _ in ()).throw(AssertionError("workbook alterado")),
    )

    assert script.main() == 2


def test_direct_migration_script_fails_closed_in_apply_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import migrate_v1_operational_data as script

    v1_root = tmp_path / "v1-sintetica"
    v2_root = tmp_path / "v2-sintetica"
    v1_root.mkdir()
    v2_root.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        [
            "migrate_v1_operational_data.py",
            "--v1-root",
            str(v1_root),
            "--v2-root",
            str(v2_root),
            "--apply",
        ],
    )
    monkeypatch.setattr(
        script,
        "migrate_operational_data",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("migração real iniciada")
        ),
    )

    assert script.main() == 2
