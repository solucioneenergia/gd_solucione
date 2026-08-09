from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.application.operational_guard import (
    CONNECT_EXISTING_EDGE_OPERATION,
    DOWNLOAD_CDP_OPERATION,
    INSPECT_PORTAL_OPERATION,
    MIGRATION_OPERATION,
    PROCESS_FIRST_CDP_OPERATION,
    authorize_direct_route,
    authorize_offline_batch,
    build_direct_route_confirmation,
    build_option4_strong_confirmation,
    global_execution_lock_path,
    prepare_offline_batch,
)
from automacao_gd.application import processing_service
from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.infrastructure.locking import ExecutionLock


def _direct_authorization(operation: str, limit: int | None = None):
    confirmation = build_direct_route_confirmation(
        operation,
        requested_limit=limit,
    )
    return authorize_direct_route(
        operation,
        confirmation,
        requested_limit=limit,
    )


def _settings(tmp_path: Path, *, limit: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        logs_dir_path=tmp_path / "logs",
        MAX_COMPLETED_TO_PROCESS=limit,
        downloads_dir_path=tmp_path / "downloads",
        REPROCESS_EXISTING_PDFS=False,
        PROCESS_EXISTING_AFTER_SKIP=False,
    )


def _hold_common_lock(settings: object):
    return ExecutionLock(
        global_execution_lock_path(settings),
        execution_id="SYNTHETIC-HELD-LOCK",
        operation="synthetic-holder",
        requested_batch_limit=1,
        authorization_scope="SYNTHETIC_TEST",
    )


def test_migration_apply_acquires_common_lock_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import migrate_v1_operational_data as script

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    state = source / "data/state/pipeline_cdp_state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    settings = _settings(tmp_path)
    monkeypatch.setattr(script, "get_settings", lambda: settings, raising=False)

    with _hold_common_lock(settings), pytest.raises(OperationalBlockError) as exc:
        script.migrate_operational_data(
            source,
            destination,
            apply=True,
            authorization=_direct_authorization(MIGRATION_OPERATION),
        )

    assert exc.value.code in {
        "GLOBAL_EXECUTION_LOCKED",
        "GLOBAL_EXECUTION_LOCK_REENTRANT",
    }
    assert not (destination / "data/state/pipeline_cdp_state.json").exists()


def test_imported_download_acquires_common_lock_before_portal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import download_completed_budgets_cdp as script

    settings = _settings(tmp_path)
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(
        script,
        "create_portal_automation",
        lambda *_: (_ for _ in ()).throw(AssertionError("portal iniciado")),
    )

    with _hold_common_lock(settings), pytest.raises(OperationalBlockError) as exc:
        script.run_download_completed_budgets_cdp(
            _direct_authorization(DOWNLOAD_CDP_OPERATION, 1)
        )

    assert exc.value.code in {
        "GLOBAL_EXECUTION_LOCKED",
        "GLOBAL_EXECUTION_LOCK_REENTRANT",
    }


@pytest.mark.parametrize(
    ("module_name", "function_name", "operation"),
    [
        ("connect_existing_edge", "_legacy_connect_existing_edge", CONNECT_EXISTING_EDGE_OPERATION),
        ("inspect_portal_table", "_legacy_inspect_portal_table", INSPECT_PORTAL_OPERATION),
        (
            "process_first_solicitation_cdp",
            "_legacy_process_first_solicitation_cdp",
            PROCESS_FIRST_CDP_OPERATION,
        ),
    ],
)
def test_imported_legacy_route_acquires_common_lock_before_effects(
    module_name: str,
    function_name: str,
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__(f"scripts.{module_name}", fromlist=[function_name])
    limit = 1 if operation == PROCESS_FIRST_CDP_OPERATION else None
    settings = _settings(tmp_path)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        module,
        "ensure_directories",
        lambda: (_ for _ in ()).throw(AssertionError("efeito iniciado")),
    )

    with _hold_common_lock(settings), pytest.raises(OperationalBlockError) as exc:
        getattr(module, function_name)(_direct_authorization(operation, limit))

    assert exc.value.code in {
        "GLOBAL_EXECUTION_LOCKED",
        "GLOBAL_EXECUTION_LOCK_REENTRANT",
    }


def test_real_processing_requires_typed_authorization_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        processing_service,
        "ensure_directories",
        lambda: (_ for _ in ()).throw(AssertionError("efeito iniciado")),
    )

    with pytest.raises(OperationalBlockError) as exc:
        processing_service.process_downloaded_pdfs(
            downloads_root=tmp_path,
            workbook_path=tmp_path / "workbook.xlsx",
            clientes_root=tmp_path / "clients",
            dry_run=False,
            pdf_paths=[tmp_path / "Orcamento_de_Conexao_2600000000.pdf"],
            allowed_protocols={"2600000000"},
        )

    assert exc.value.code == "DIRECT_ROUTE_AUTHORIZATION_REQUIRED"


def test_real_processing_with_authorization_is_blocked_by_common_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    batch = prepare_offline_batch(
        tmp_path,
        requested_limit=1,
        selections=[(pdf.name, "2600000000")],
    )
    authorization = authorize_offline_batch(
        batch,
        build_option4_strong_confirmation(1),
    )
    settings = _settings(tmp_path)
    monkeypatch.setattr(processing_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        processing_service,
        "ensure_directories",
        lambda: (_ for _ in ()).throw(AssertionError("efeito iniciado")),
    )

    with _hold_common_lock(settings), pytest.raises(OperationalBlockError) as exc:
        processing_service.process_downloaded_pdfs(
            downloads_root=tmp_path,
            workbook_path=tmp_path / "workbook.xlsx",
            clientes_root=tmp_path / "clients",
            dry_run=False,
            pdf_paths=[pdf],
            allowed_protocols={"2600000000"},
            authorization=authorization,
        )

    assert exc.value.code in {
        "GLOBAL_EXECUTION_LOCKED",
        "GLOBAL_EXECUTION_LOCK_REENTRANT",
    }
