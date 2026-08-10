from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.application import full_pipeline, processing_service
from automacao_gd.application.contracts import OperationStatus
from automacao_gd.presentation import cli
from automacao_gd.presentation.cli import _parse_args


def test_eligibility_cache_sanitizes_sensitive_portal_fields(tmp_path: Path) -> None:
    from automacao_gd.application.op5_optimization import (
        load_eligibility_cache,
        write_eligibility_cache,
    )

    cache_path = tmp_path / "op5_portal_eligibility_cache.json"
    captured_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

    write_eligibility_cache(
        cache_path,
        records=[
            {
                "protocol": "2600001048",
                "page_number": 1,
                "row_index": 7,
                "status": "CONCLUIDA",
                "selection_reason": "eligible_new",
                "client_name": "CLIENTE SINTETICO LTDA",
                "uc": "0000000000",
                "address": "RUA SINTETICA",
                "raw_text": "conteudo bruto nao deve persistir",
            }
        ],
        requested_limit=1,
        reconciliation_mode="batch_fast",
        captured_at=captured_at,
    )

    raw = cache_path.read_text(encoding="utf-8")
    assert "CLIENTE SINTETICO LTDA" not in raw
    assert "0000000000" not in raw
    assert "RUA SINTETICA" not in raw
    assert "conteudo bruto" not in raw

    loaded = load_eligibility_cache(
        cache_path,
        requested_limit=1,
        reconciliation_mode="batch_fast",
        now=captured_at + timedelta(minutes=5),
        ttl_minutes=30,
    )

    assert loaded["cache_hit"] is True
    assert loaded["protocols"] == ["2600001048"]
    assert loaded["records"] == [
        {
            "protocol": "2600001048",
            "page_number": 1,
            "row_index": 7,
            "status": "CONCLUIDA",
            "selection_reason": "eligible_new",
        }
    ]


def test_eligibility_cache_invalidates_by_ttl_and_mode(tmp_path: Path) -> None:
    from automacao_gd.application.op5_optimization import (
        load_eligibility_cache,
        write_eligibility_cache,
    )

    cache_path = tmp_path / "op5_portal_eligibility_cache.json"
    captured_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    write_eligibility_cache(
        cache_path,
        records=[{"protocol": "2600001048", "page_number": 1, "row_index": 1}],
        requested_limit=1,
        reconciliation_mode="batch_fast",
        captured_at=captured_at,
    )

    expired = load_eligibility_cache(
        cache_path,
        requested_limit=1,
        reconciliation_mode="batch_fast",
        now=captured_at + timedelta(minutes=31),
        ttl_minutes=30,
    )
    wrong_mode = load_eligibility_cache(
        cache_path,
        requested_limit=1,
        reconciliation_mode="inline_global",
        now=captured_at + timedelta(minutes=1),
        ttl_minutes=30,
    )

    assert expired["cache_hit"] is False
    assert expired["reason"] == "expired"
    assert wrong_mode["cache_hit"] is False
    assert wrong_mode["reason"] == "mode_mismatch"


def test_reconciliation_cache_reuses_only_matching_keys(tmp_path: Path) -> None:
    from automacao_gd.application.op5_optimization import (
        load_reconciliation_cache,
        portal_protocols_hash,
        write_reconciliation_cache,
    )

    cache_path = tmp_path / "portal_workbook_reconciliation_cache.json"
    protocols_hash = portal_protocols_hash(["2600001048", "2600001049"])
    summary = {"decision": "STAGE2_COMPLETE", "matched_unique": 2}

    write_reconciliation_cache(
        cache_path,
        workbook_sha256="a" * 64,
        portal_protocols_hash=protocols_hash,
        reconciliation_mode="audit_global",
        summary=summary,
    )

    hit = load_reconciliation_cache(
        cache_path,
        workbook_sha256="a" * 64,
        portal_protocols_hash=protocols_hash,
        reconciliation_mode="audit_global",
    )
    miss = load_reconciliation_cache(
        cache_path,
        workbook_sha256="b" * 64,
        portal_protocols_hash=protocols_hash,
        reconciliation_mode="audit_global",
    )

    assert hit["cache_hit"] is True
    assert hit["summary"] == summary
    assert miss["cache_hit"] is False
    assert miss["reason"] == "workbook_sha_mismatch"


def test_workbook_index_cache_reuses_by_sha_and_rebuilds_when_workbook_changes(
    tmp_path: Path,
) -> None:
    from automacao_gd.application.op5_optimization import load_or_build_workbook_index

    workbook = tmp_path / "planilha.xlsx"
    cache_path = tmp_path / "workbook_index_cache.json"
    workbook.write_bytes(b"versao-1")
    calls: list[str] = []

    def builder(path: Path) -> dict:
        calls.append(path.read_bytes().decode("utf-8"))
        return {"protocols": {"2600001048": {"sheet_name": "2026", "row_number": 49}}}

    first = load_or_build_workbook_index(workbook, cache_path, builder=builder)
    second = load_or_build_workbook_index(workbook, cache_path, builder=builder)
    workbook.write_bytes(b"versao-2")
    third = load_or_build_workbook_index(workbook, cache_path, builder=builder)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert third["cache_hit"] is False
    assert calls == ["versao-1", "versao-2"]


def test_parallel_pdf_extraction_preserves_order_and_rejects_excess_workers(
    tmp_path: Path,
) -> None:
    from automacao_gd.application.op5_optimization import run_limited_pdf_tasks

    pdfs = []
    for index in range(4):
        pdf = tmp_path / f"Orcamento_de_Conexao_260000104{index}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        pdfs.append(pdf)

    def task(path: Path) -> dict:
        return {"name": path.name}

    results = run_limited_pdf_tasks(pdfs, worker_count=2, task=task)

    assert [item["name"] for item in results] == [path.name for path in pdfs]
    with pytest.raises(ValueError, match="OP5_PDF_WORKERS"):
        run_limited_pdf_tasks(pdfs, worker_count=5, task=task)


def test_cli_accepts_explicit_option5_commands() -> None:
    plan = _parse_args(["op5-plan", "--limit", "20", "--protocols", "2600001048,2600001049"])
    apply = _parse_args(["op5-apply", "--plan", "data/logs/op5_plan_latest.json"])
    audit = _parse_args(["op5-audit-global"])

    assert plan.command == "op5-plan"
    assert plan.limit == 20
    assert plan.protocols == "2600001048,2600001049"
    assert apply.command == "op5-apply"
    assert apply.plan == Path("data/logs/op5_plan_latest.json")
    assert audit.command == "op5-audit-global"


def test_cli_op5_plan_passes_target_protocols_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    base = SimpleNamespace(
        OPTION5_AUTHORIZED_MAX_PROTOCOLS=60,
        model_copy=lambda update: SimpleNamespace(
            OPTION5_AUTHORIZED_MAX_PROTOCOLS=60,
            **update,
        ),
    )
    monkeypatch.setattr(cli, "get_settings", lambda: base)
    monkeypatch.setattr(
        cli.ApplicationController,
        "run_pipeline",
        lambda self, *, confirmation: captured.append(self.settings)
        or SimpleNamespace(status=OperationStatus.SUCESSO, payload={}),
    )
    monkeypatch.setattr(cli, "print_operation_summary", lambda *_args, **_kwargs: None)

    exit_code = cli._run_op5_plan(2, protocols="2600001048, 2600001049")

    assert exit_code == 0
    assert captured[0].MAX_COMPLETED_TO_PROCESS == 2
    assert captured[0].OP5_TARGET_PROTOCOLS == "2600001048,2600001049"


def test_cli_op5_audit_global_invokes_readonly_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[object] = []
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(
            model_copy=lambda update: SimpleNamespace(**update),
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_op5_audit_global",
        lambda settings: called.append(settings)
        or {
            "status": OperationStatus.SUCESSO.value,
            "operation_message": "Auditoria concluida.",
        },
    )

    exit_code = cli._run_op5_audit_global()

    assert exit_code == 0
    assert called
    assert called[0].OP5_RECONCILIATION_MODE == "audit_global"
    assert called[0].APPLY_EXCEL is False
    assert called[0].APPLY_ARCHIVE is False


def test_batch_fast_download_summary_persists_eligibility_cache(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        logs_dir_path=tmp_path,
        OP5_RECONCILIATION_MODE="batch_fast",
        MAX_COMPLETED_TO_PROCESS=1,
        OP5_ELIGIBILITY_CACHE_TTL_MINUTES=30,
    )
    summary = {
        "results": [
            {
                "protocol": "2600001048",
                "page_number": 1,
                "row_index": 2,
                "status": "CONCLUIDA",
                "selection_reason": "eligible_new",
                "client_name": "CLIENTE SINTETICO LTDA",
            }
        ]
    }

    metadata = full_pipeline._persist_eligibility_cache_if_applicable(settings, summary)

    assert metadata["eligibility_cache_hit"] is False
    assert metadata["eligibility_cache_path"].endswith("op5_portal_eligibility_cache.json")
    assert "CLIENTE SINTETICO LTDA" not in (tmp_path / "op5_portal_eligibility_cache.json").read_text(
        encoding="utf-8"
    )


def test_processing_reports_configured_pdf_workers_and_preserves_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdfs = []
    for index in range(2):
        pdf = tmp_path / f"Orcamento_de_Conexao_260000104{index}.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        pdfs.append(pdf)

    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(
            logs_dir_path=tmp_path / "logs",
            BACKUP_EXCEL=False,
            OP5_PDF_WORKERS=2,
        ),
    )
    monkeypatch.setattr(
        processing_service,
        "_process_single_pdf",
        lambda pdf_path, *_args: {
            "success": True,
            "pdf_path": str(pdf_path),
            "protocol": pdf_path.stem.rsplit("_", 1)[-1],
            "client_folder_match_type": None,
            "archive_status": {"skipped": True},
            "excel_status": {"action": "insert_new_chronological"},
        },
    )
    monkeypatch.setattr(
        processing_service,
        "project_dry_run_target_rows",
        lambda results, _workbook: results,
    )

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha.xlsx",
        clientes_root=tmp_path / "clientes",
        dry_run=True,
        pdf_paths=pdfs,
        apply_excel=True,
        apply_archive=False,
    )

    assert payload["pdf_workers"] == 2
    assert [Path(item["pdf_path"]).name for item in payload["results"]] == [
        path.name for path in pdfs
    ]


def test_reconciliation_callback_reuses_cache_for_same_workbook_and_portal_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = tmp_path / "planilha.xlsx"
    workbook.write_bytes(b"planilha-sintetica")
    settings = SimpleNamespace(
        logs_dir_path=tmp_path,
        planilha_path=workbook,
        OP5_RECONCILIATION_MODE="audit_global",
    )
    records = [
        SimpleNamespace(protocol="2600001048"),
        SimpleNamespace(protocol="2600001049"),
    ]
    calls: list[str] = []

    def fake_reconcile(*_args, **_kwargs):
        calls.append("recompute")
        return SimpleNamespace(decision="STAGE2_COMPLETE", portal_summary={})

    monkeypatch.setattr(full_pipeline, "reconcile_portal_workbook", fake_reconcile)
    monkeypatch.setattr(
        full_pipeline,
        "save_reconciliation_reports",
        lambda *_args, **_kwargs: (
            tmp_path / "portal_workbook_reconciliation_global.json",
            tmp_path / "portal_workbook_reconciliation_global.md",
        ),
    )
    monkeypatch.setattr(
        full_pipeline,
        "reconciliation_summary",
        lambda result, report_path: {
            "decision": result.decision,
            "markdown_report_path": report_path,
        },
    )

    callback = full_pipeline._reconciliation_callback(settings)
    first = callback("before_limit", records, [], {"pagination_complete": True})
    second = callback("before_limit", records, [], {"pagination_complete": True})

    assert calls == ["recompute"]
    assert first["reconciliation_cache_hit"] is False
    assert second["reconciliation_cache_hit"] is True


def test_audit_global_portal_context_excludes_model_objects() -> None:
    from automacao_gd.domain.models import PortalSolicitation

    context = full_pipeline._audit_global_portal_context(
        {
            "pages_read": 1,
            "total_rows": 50,
            "total_completed": 1,
            "pagination_stop_reason": "last_page_reached",
            "completed_records": [
                PortalSolicitation(
                    protocol="2600001048",
                    client_name="CLIENTE SINTETICO LTDA",
                    status="CONCLUIDA",
                )
            ],
            "results": [{"protocol": "2600001048"}],
        }
    )

    assert context == {
        "pages_read": 1,
        "total_rows": 50,
        "total_completed": 1,
        "pagination_stop_reason": "last_page_reached",
    }
