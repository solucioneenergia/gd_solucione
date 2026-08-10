from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from automacao_gd.application.contracts import (
    OperationError,
    ProgressEvent,
    ProgressTracker,
    map_exception_to_operation_error,
    protocol_progress_stage_keys,
)
from automacao_gd.presentation.operational_output import (
    format_operation_error,
    format_progress_event,
)


def test_progress_event_accepts_valid_percentages() -> None:
    event = ProgressEvent(
        overall_percent=35,
        stage_percent=80,
        protocol_percent=40,
        stage="protocol_processing",
        protocol="2600000001",
        message="Processando protocolo.",
        current=1,
        total=3,
    )

    assert event.overall_percent == 35
    assert event.stage_percent == 80
    assert event.protocol_percent == 40
    assert event.to_dict()["protocol"] == "2600000001"


def test_progress_event_normalizes_invalid_percentages() -> None:
    event = ProgressEvent(
        overall_percent=-10,
        stage_percent=150,
        protocol_percent=999,
        stage="preflight",
    )

    assert event.overall_percent == 0
    assert event.stage_percent == 100
    assert event.protocol_percent == 100


def test_progress_tracker_emits_zero_at_start_and_100_on_success() -> None:
    emitted: list[ProgressEvent] = []
    tracker = ProgressTracker(emitted.append)

    tracker.start()
    tracker.finish_success()

    assert emitted[0].overall_percent == 0
    assert emitted[-1].overall_percent == 100


def test_progress_tracker_prevents_overall_regression() -> None:
    tracker = ProgressTracker()
    tracker.advance(stage="portal_read", overall_percent=40, stage_percent=10, message="A")
    event = tracker.advance(
        stage="portal_read",
        overall_percent=20,
        stage_percent=20,
        message="B",
    )

    assert event.overall_percent == 40
    assert [item.overall_percent for item in tracker.events] == [40, 40]


def test_progress_tracker_failure_does_not_force_100() -> None:
    tracker = ProgressTracker()
    tracker.advance(stage="protocol_processing", overall_percent=67, stage_percent=50, message="Em execução")
    event = tracker.finish_failed("Falha parcial.")

    assert event.overall_percent == 67
    assert event.overall_percent < 100


def test_protocol_progress_stage_key_order() -> None:
    assert protocol_progress_stage_keys() == [
        "locate_protocol",
        "open_detail",
        "download_or_reuse_pdf",
        "parse_pdf",
        "resolve_client_folder",
        "update_excel",
        "archive_pdf",
        "save_checkpoint",
    ]


def test_operation_error_is_json_serializable() -> None:
    error = OperationError(
        run_id="run-1",
        protocol="2600000001",
        stage="excel_update",
        code="EXCEL_LOCKED",
        user_message="A planilha está aberta.",
        technical_cause="Permission denied",
        suggested_action="Feche a planilha e execute novamente.",
    )

    encoded = json.dumps(error.to_dict(), ensure_ascii=False)

    assert "EXCEL_LOCKED" in encoded
    assert "2600000001" in encoded


@pytest.mark.parametrize(
    ("exception", "stage", "expected_code", "expected_action"),
    [
        (
            PermissionError("arquivo bloqueado"),
            "excel_update",
            "EXCEL_LOCKED",
            "Feche a planilha e execute novamente.",
        ),
        (
            RuntimeError("pasta ambígua para cliente"),
            "archive",
            "CLIENT_FOLDER_AMBIGUOUS",
            "Conferir a pasta correta do cliente.",
        ),
        (
            ValueError("Arquivo não possui assinatura PDF válida"),
            "pdf_validation",
            "PDF_INVALID_SIGNATURE",
            "Verificar se o arquivo é um orçamento de conexão válido.",
        ),
        (
            TimeoutError("download timeout"),
            "download",
            "DOWNLOAD_TIMEOUT",
            "Executar novamente ou conferir instabilidade do portal.",
        ),
        (
            ConnectionError("falha CDP ao conectar Edge"),
            "cdp_connection",
            "CDP_CONNECTION_FAILED",
            "Abrir o Edge com a porta CDP e refazer a conexão.",
        ),
        (
            RuntimeError("erro inesperado"),
            "unknown",
            "UNKNOWN_ERROR",
            "Consultar log técnico.",
        ),
    ],
)
def test_map_exception_to_operation_error(
    exception: BaseException,
    stage: str,
    expected_code: str,
    expected_action: str,
) -> None:
    error = map_exception_to_operation_error(
        exception,
        stage=stage,
        protocol="2600000001",
        run_id="run-1",
    )

    assert error.code == expected_code
    assert error.suggested_action == expected_action


def test_operational_error_output_hides_traceback_and_shows_suggested_action() -> None:
    error = OperationError(
        run_id="run-1",
        protocol="2600000001",
        stage="excel_update",
        code="EXCEL_LOCKED",
        user_message="A planilha está aberta ou bloqueada.",
        technical_cause="Traceback (most recent call last): segredo técnico",
        action_taken="A linha não foi atualizada.",
        suggested_action="Feche a planilha e execute novamente.",
        traceback_ref="logs/tecnico.json#1",
    )

    output = format_operation_error(error)

    assert "Traceback" not in output
    assert "segredo técnico" not in output
    assert "Feche a planilha e execute novamente." in output
    assert "2600000001" in output


def test_operational_error_output_sanitizes_sensitive_data() -> None:
    error = OperationError(
        run_id="run-1",
        protocol="2600000001",
        stage="archive",
        code="CLIENT_FOLDER_AMBIGUOUS",
        user_message="Contato telefone: 81999999999 e email pessoa@example.com",
        action_taken="Ação interrompida.",
        suggested_action="Conferir CPF 000.000.000-00 manualmente.",
    )

    output = format_operation_error(error)

    assert "81999999999" not in output
    assert "pessoa@example.com" not in output
    assert "000.000.000-00" not in output
    assert "[TELEFONE REMOVIDO]" in output
    assert "[E-MAIL REMOVIDO]" in output
    assert "[CPF/CNPJ REMOVIDO]" in output


def test_format_progress_event_is_compact_and_preserves_protocol() -> None:
    output = format_progress_event(
        ProgressEvent(
            overall_percent=30,
            stage_percent=100,
            protocol_percent=10,
            stage="protocol_processing",
            protocol="2600001104",
            message="Baixando orçamento.",
        )
    )

    assert "30%" in output
    assert "2600001104" in output
    assert "protocol_processing" in output


def test_pipeline_accepts_progress_callback_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from automacao_gd.application import full_pipeline

    _patch_pipeline_for_offline_progress_test(monkeypatch, tmp_path)

    payload = full_pipeline.run_full_cdp_pipeline(
        _synthetic_settings(tmp_path),
        progress_callback=None,
        confirmation=full_pipeline.build_option5_strong_confirmation(1),
    )

    assert payload["total_errors"] == 0


def test_pipeline_accepts_progress_callback_and_emits_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automacao_gd.application import full_pipeline

    _patch_pipeline_for_offline_progress_test(monkeypatch, tmp_path)
    events: list[ProgressEvent] = []

    full_pipeline.run_full_cdp_pipeline(
        _synthetic_settings(tmp_path),
        progress_callback=events.append,
        confirmation=full_pipeline.build_option5_strong_confirmation(1),
    )

    assert events[0].overall_percent == 0
    assert events[-1].overall_percent == 100
    assert [event.overall_percent for event in events] == sorted(
        event.overall_percent for event in events
    )
    assert "report_generation" in {event.stage for event in events}


def _synthetic_settings(tmp_path: Path) -> SimpleNamespace:
    workbook_path = tmp_path / "planilha.xlsx"
    workbook_path.write_bytes(b"synthetic workbook bytes for OP5 plan hash")
    return SimpleNamespace(
        CDP_MODE=True,
        DRY_RUN=True,
        APPLY_EXCEL=False,
        APPLY_ARCHIVE=False,
        ENABLE_PORTAL_PAGINATION=False,
        MAX_PORTAL_PAGES=1,
        MAX_COMPLETED_TO_PROCESS=1,
        REPROCESS_EXISTING_PDFS=False,
        PROCESS_EXISTING_AFTER_SKIP=False,
        CACHE_CLIENT_FOLDER_LOOKUP=False,
        SKIP_ALREADY_COMPLETED=True,
        RESUME_PIPELINE=False,
        RESET_PIPELINE_STATE=False,
        force_reprocess_protocols=set(),
        CDP_ENDPOINT="http://127.0.0.1:9222",
        pipeline_state_path=tmp_path / "state.json",
        logs_dir_path=tmp_path / "logs",
        downloads_dir_path=tmp_path / "downloads",
        planilha_path=workbook_path,
        clientes_root_path=tmp_path / "clientes",
    )


def _patch_pipeline_for_offline_progress_test(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from automacao_gd.application import full_pipeline

    class FakePreflight:
        ready = True
        blocking_errors: list[str] = []

    class FakeStateStore:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.started = False

        def start_run(self, config: dict[str, Any]) -> str:
            self.started = True
            return "run-sintetico"

    monkeypatch.setattr(full_pipeline, "run_preflight", lambda *args, **kwargs: FakePreflight())
    monkeypatch.setattr(full_pipeline, "PipelineStateStore", FakeStateStore)
    monkeypatch.setattr(
        full_pipeline,
        "_run_download_step",
        lambda settings, state_store: {
            "total_selected": 0,
            "total_completed": 0,
            "results": [],
        },
    )
    monkeypatch.setattr(
        full_pipeline,
        "_save_download_summary",
        lambda logs_dir, summary: Path(tmp_path / "downloads.json"),
    )
    monkeypatch.setattr(full_pipeline, "_pdf_paths_for_processing", lambda summary: [])
    monkeypatch.setattr(
        full_pipeline,
        "process_downloaded_pdfs",
        lambda **kwargs: {
            "total_success": 0,
            "total_pdfs": 0,
            "results": [],
            "total_errors": 0,
            "total_excel_updated": 0,
            "total_archived": 0,
            "total_pending_review": 0,
        },
    )
    monkeypatch.setattr(
        full_pipeline,
        "_save_pipeline_reports",
        lambda logs_dir, payload: (
            Path(tmp_path / "pipeline.json"),
            Path(tmp_path / "pipeline.md"),
        ),
    )
