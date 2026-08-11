from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.application import full_pipeline, processing_service
from automacao_gd.application.contracts import OperationStatus
from automacao_gd.infrastructure.portal import cdp_service as cdp_portal_service
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
    archive_plan = _parse_args(
        ["op5-archive-plan", "--plan", "data/logs/op5_plan_latest.json"]
    )
    retention_audit = _parse_args(["op5-retention-audit"])
    retention_apply = _parse_args(
        ["op5-retention-apply", "--plan", "data/logs/op5_retention_plan_latest.json"]
    )
    workbook_audit = _parse_args(["workbook-format-audit"])
    workbook_apply = _parse_args(
        ["workbook-format-apply", "--plan", "data/logs/workbook_format_plan_latest.json"]
    )

    assert plan.command == "op5-plan"
    assert plan.limit == 20
    assert plan.protocols == "2600001048,2600001049"
    assert apply.command == "op5-apply"
    assert apply.plan == Path("data/logs/op5_plan_latest.json")
    assert audit.command == "op5-audit-global"
    assert archive_plan.command == "op5-archive-plan"
    assert archive_plan.plan == Path("data/logs/op5_plan_latest.json")
    assert retention_audit.command == "op5-retention-audit"
    assert retention_apply.command == "op5-retention-apply"
    assert retention_apply.plan == Path("data/logs/op5_retention_plan_latest.json")
    assert workbook_audit.command == "workbook-format-audit"
    assert workbook_apply.command == "workbook-format-apply"
    assert workbook_apply.plan == Path("data/logs/workbook_format_plan_latest.json")


def test_op5_retention_audit_plans_only_master_valid_completed_downloads(
    tmp_path: Path,
) -> None:
    from automacao_gd.application.op5_retention import audit_download_retention

    downloads = tmp_path / "downloads"
    logs = tmp_path / "logs"
    state = tmp_path / "state" / "op5_completed_index.json"
    workbook = tmp_path / "planilha-sintetica.xlsx"
    archived = tmp_path / "clientes" / "Orcamento_de_Conexao_2600001048.pdf"
    pdf = downloads / "2600001048" / "Orcamento_de_Conexao_2600001048.pdf"
    blocked_pdf = downloads / "2600001049" / "Orcamento_de_Conexao_2600001049.pdf"
    pdf.parent.mkdir(parents=True)
    blocked_pdf.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    logs.mkdir()
    workbook.write_bytes(b"WORKBOOK SINTETICO")
    pdf.write_bytes(b"%PDF-1.4\nPDF SINTETICO\n")
    blocked_pdf.write_bytes(b"%PDF-1.4\nSEM INDICE MESTRE\n")
    archived.write_bytes(pdf.read_bytes())
    _write_completed_index(
        state,
        protocol="2600001048",
        download_pdf=pdf,
        archived_pdf=archived,
        workbook=workbook,
    )

    plan = audit_download_retention(
        downloads_root=downloads,
        master_index_path=state,
        workbook_path=workbook,
        logs_dir=logs,
        now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )

    assert plan["dry_run"] is True
    assert plan["total_delete_candidates"] == 1
    assert plan["total_blocked"] == 1
    assert pdf.exists()
    assert blocked_pdf.exists()
    candidates = {item["protocol"]: item for item in plan["items"]}
    assert candidates["2600001048"]["action"] == "delete_local_pdf"
    assert candidates["2600001048"]["download_pdf_sha256"] == hashlib.sha256(
        pdf.read_bytes()
    ).hexdigest()
    assert candidates["2600001049"]["action"] == "keep"
    assert "master_index_missing_or_expired" in candidates["2600001049"]["reasons"]
    assert Path(plan["plan_path"]).exists()


def test_op5_retention_apply_deletes_only_revalidated_candidates(tmp_path: Path) -> None:
    from automacao_gd.application.op5_retention import (
        apply_download_retention_plan,
        audit_download_retention,
    )

    downloads = tmp_path / "downloads"
    logs = tmp_path / "logs"
    state = tmp_path / "state" / "op5_completed_index.json"
    workbook = tmp_path / "planilha-sintetica.xlsx"
    archived = tmp_path / "clientes" / "Orcamento_de_Conexao_2600001048.pdf"
    pdf = downloads / "2600001048" / "Orcamento_de_Conexao_2600001048.pdf"
    blocked_pdf = downloads / "2600001049" / "Orcamento_de_Conexao_2600001049.pdf"
    pdf.parent.mkdir(parents=True)
    blocked_pdf.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    logs.mkdir()
    workbook.write_bytes(b"WORKBOOK SINTETICO")
    pdf.write_bytes(b"%PDF-1.4\nPDF SINTETICO\n")
    blocked_pdf.write_bytes(b"%PDF-1.4\nSEM INDICE MESTRE\n")
    archived.write_bytes(pdf.read_bytes())
    _write_completed_index(
        state,
        protocol="2600001048",
        download_pdf=pdf,
        archived_pdf=archived,
        workbook=workbook,
    )
    plan = audit_download_retention(
        downloads_root=downloads,
        master_index_path=state,
        workbook_path=workbook,
        logs_dir=logs,
        now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )

    result = apply_download_retention_plan(
        Path(plan["plan_path"]),
        downloads_root=downloads,
        master_index_path=state,
        workbook_path=workbook,
        logs_dir=logs,
        now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )

    assert result["success"] is True
    assert result["deleted_count"] == 1
    assert not pdf.exists()
    assert blocked_pdf.exists()
    assert archived.exists()


def test_op5_retention_apply_blocks_when_workbook_changed(tmp_path: Path) -> None:
    from automacao_gd.application.op5_retention import (
        apply_download_retention_plan,
        audit_download_retention,
    )

    downloads = tmp_path / "downloads"
    logs = tmp_path / "logs"
    state = tmp_path / "state" / "op5_completed_index.json"
    workbook = tmp_path / "planilha-sintetica.xlsx"
    archived = tmp_path / "clientes" / "Orcamento_de_Conexao_2600001048.pdf"
    pdf = downloads / "2600001048" / "Orcamento_de_Conexao_2600001048.pdf"
    pdf.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    logs.mkdir()
    workbook.write_bytes(b"WORKBOOK SINTETICO V1")
    pdf.write_bytes(b"%PDF-1.4\nPDF SINTETICO\n")
    archived.write_bytes(pdf.read_bytes())
    _write_completed_index(
        state,
        protocol="2600001048",
        download_pdf=pdf,
        archived_pdf=archived,
        workbook=workbook,
    )
    plan = audit_download_retention(
        downloads_root=downloads,
        master_index_path=state,
        workbook_path=workbook,
        logs_dir=logs,
        now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )
    workbook.write_bytes(b"WORKBOOK SINTETICO V2")

    result = apply_download_retention_plan(
        Path(plan["plan_path"]),
        downloads_root=downloads,
        master_index_path=state,
        workbook_path=workbook,
        logs_dir=logs,
        now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )

    assert result["success"] is False
    assert result["deleted_count"] == 0
    assert pdf.exists()
    assert result["error_code"] == "OP5_RETENTION_VALIDATION_FAILED"


def test_workbook_format_apply_blocks_stale_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automacao_gd.application import workbook_format_flow

    logs = tmp_path / "logs"
    logs.mkdir()
    workbook = tmp_path / "planilha-sintetica.xlsx"
    workbook.write_bytes(b"WORKBOOK SINTETICO V1")
    monkeypatch.setattr(
        workbook_format_flow,
        "repair_workbook_format",
        lambda **kwargs: {"success": True, "dry_run": kwargs["dry_run"], "sheets": []},
    )
    plan = workbook_format_flow.audit_workbook_format(workbook, logs)
    workbook.write_bytes(b"WORKBOOK SINTETICO V2")

    result = workbook_format_flow.apply_workbook_format_plan(
        Path(plan["plan_path"]),
        workbook_path=workbook,
        logs_dir=logs,
    )

    assert result["success"] is False
    assert result["error_code"] == "WORKBOOK_FORMAT_WORKBOOK_CHANGED"


def _write_completed_index(
    index_path: Path,
    *,
    protocol: str,
    download_pdf: Path,
    archived_pdf: Path,
    workbook: Path,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "op5-completed-index-v1",
                "protocols": {
                    protocol: {
                        "status": "completed",
                        "download_pdf_path": str(download_pdf),
                        "download_pdf_sha256": hashlib.sha256(
                            download_pdf.read_bytes()
                        ).hexdigest(),
                        "archived_pdf_path": str(archived_pdf),
                        "archived_pdf_sha256": hashlib.sha256(
                            archived_pdf.read_bytes()
                        ).hexdigest(),
                        "workbook_sheet": "2026",
                        "workbook_row": 42,
                        "workbook_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
                        "technical_extractor_version": "technical-processing-format-v6",
                        "equipment_rules_version": "equipment-rules-v3",
                        "updated_at": now.isoformat(timespec="seconds"),
                        "expires_at": (now + timedelta(days=14)).isoformat(
                            timespec="seconds"
                        ),
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_op5_archive_plan_reuses_frozen_scope_without_cdp_and_refreshes_workbook_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs = tmp_path / "logs"
    downloads = tmp_path / "downloads"
    workbook = tmp_path / "planilha.xlsx"
    clientes = tmp_path / "clientes"
    logs.mkdir()
    downloads.mkdir()
    clientes.mkdir()
    workbook.write_bytes(b"planilha-atual-apos-excel")
    protocols = ("2600001048", "2600001049")
    pdfs = []
    for protocol in protocols:
        pdf = downloads / protocol / f"Orcamento_de_Conexao_{protocol}.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(f"%PDF-1.4 sintético {protocol}".encode("utf-8"))
        pdfs.append(pdf)
    artifacts = [
        {
            "protocol": protocol,
            "path": str(pdf.resolve(strict=True)),
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        }
        for protocol, pdf in zip(protocols, pdfs, strict=True)
    ]
    digest = hashlib.sha256(
        "\n".join(f"{item['protocol']}:{item['sha256']}" for item in artifacts).encode(
            "utf-8"
        )
    ).hexdigest()
    frozen_batch = {
        "requested_limit": 2,
        "authorized_limit": 60,
        "authorization_scope": full_pipeline.CONTROLLED_PRODUCTION_UP_TO_60_AUTHORIZATION_SCOPE,
        "protocols": list(protocols),
        "unique_before_limit": 2,
        "dropped_by_limit": 0,
        "duplicate_protocols_in_frozen_batch": 0,
        "protocols_added_after_freeze": 0,
    }
    source_plan = {
        "schema_version": 1,
        "dry_run": True,
        "status": OperationStatus.SUCESSO.value,
        "requested_batch_limit": 2,
        "authorized_batch_limit": 60,
        "authorization_scope": full_pipeline.CONTROLLED_PRODUCTION_UP_TO_60_AUTHORIZATION_SCOPE,
        "workbook_sha256": "0" * 64,
        "workbook_path": str(workbook),
        "apply_excel": True,
        "apply_archive": False,
        "total_errors": 0,
        "download": {
            "run_error": None,
            "total_selected": 2,
            "total_completed": 2,
            "selected_protocols": [{"protocol": protocol} for protocol in protocols],
            "results": [
                {
                    "protocol": protocol,
                    "download_status": "existing_pdf_after_skip",
                    "process_pdf_path": str(pdf),
                    "selected_by_global_limit": True,
                    "selected_for_processing": True,
                }
                for protocol, pdf in zip(protocols, pdfs, strict=True)
            ],
            "frozen_batch_created": True,
            "frozen_batch": frozen_batch,
            "frozen_pdf_scope": {"digest": digest, "artifacts": artifacts},
        },
    }
    source_plan_path = logs / "op5_source_plan.json"
    source_plan_path.write_text(json.dumps(source_plan), encoding="utf-8")
    settings = SimpleNamespace(
        DRY_RUN=True,
        APPLY_EXCEL=True,
        APPLY_ARCHIVE=True,
        MAX_COMPLETED_TO_PROCESS=2,
        OPTION5_AUTHORIZED_MAX_PROTOCOLS=60,
        OP5_RECONCILIATION_MODE="archive_only_local",
        ENABLE_PORTAL_PAGINATION=True,
        MAX_PORTAL_PAGES=15,
        REPROCESS_EXISTING_PDFS=False,
        PROCESS_EXISTING_AFTER_SKIP=True,
        RESUME_PIPELINE=True,
        SKIP_ALREADY_COMPLETED=True,
        CACHE_CLIENT_FOLDER_LOOKUP=True,
        force_reprocess_protocols=set(),
        RESET_PIPELINE_STATE=False,
        CDP_ENDPOINT="http://127.0.0.1:9222",
        downloads_dir_path=downloads,
        logs_dir_path=logs,
        planilha_path=workbook,
        clientes_root_path=clientes,
    )
    captured: list[dict] = []

    def forbidden_download(*_args, **_kwargs):
        raise AssertionError("op5-archive-plan nao pode chamar CDP/download")

    def fake_processing(**kwargs):
        captured.append(kwargs)
        return {
            "dry_run": True,
            "apply_excel": True,
            "apply_archive": True,
            "total_pdfs": 2,
            "total_pdfs_analyzed": 2,
            "total_technically_approved": 2,
            "total_safe_protocols": 2,
            "total_success": 2,
            "total_errors": 0,
            "total_updates_planned": 0,
            "total_updates_applied": 0,
            "total_excel_updated": 0,
            "total_archived": 0,
            "total_pending_review": 0,
            "results": [
                {
                    "success": True,
                    "protocol": protocol,
                    "pdf_path": str(pdf),
                    "excel_status": {"success": True, "action": "skipped_excel_already_updated"},
                    "archive_status": {
                        "success": True,
                        "skipped": True,
                        "simulated": True,
                        "action": "simulation_only",
                    },
                    "archive_match_type": "entrada_date_folder",
                }
                for protocol, pdf in zip(protocols, pdfs, strict=True)
            ],
        }

    monkeypatch.setattr(full_pipeline, "_run_download_step", forbidden_download)
    monkeypatch.setattr(full_pipeline, "process_downloaded_pdfs", fake_processing)

    result = full_pipeline.run_op5_archive_plan(settings, source_plan_path=source_plan_path)

    assert result.status is OperationStatus.SUCESSO
    assert captured
    assert captured[0]["dry_run"] is True
    assert captured[0]["apply_archive"] is True
    assert captured[0]["allowed_protocols"] == set(protocols)
    plan = json.loads((logs / full_pipeline.OP5_PLAN_JSON_REPORT_NAME).read_text(encoding="utf-8"))
    assert plan["apply_archive"] is True
    assert plan["archive_only_local"] is True
    assert plan["workbook_sha256"] == hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert plan["download"]["archive_only_local"] is True
    assert plan["download"]["cdp_selection_skipped"] is True
    assert plan["download"]["dry_run_plan_source"] == str(source_plan_path)


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


def test_cli_op5_apply_returns_numeric_exit_code_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        model_copy=lambda update: SimpleNamespace(**update),
    )
    result = SimpleNamespace(status=OperationStatus.SUCESSO, payload={})
    printed: list[tuple[str, object]] = []

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "_confirmed_pipeline", lambda _controller: result)
    monkeypatch.setattr(
        cli,
        "print_operation_summary",
        lambda label, operation_result: printed.append((label, operation_result)),
    )

    exit_code = cli._run_op5_apply(tmp_path / "op5-plan.json")

    assert exit_code == 0
    assert printed == [("pipeline", result)]


def test_pipeline_download_step_passes_effective_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[object] = []
    settings = SimpleNamespace(
        CDP_ENDPOINT="http://127.0.0.1:9222",
        PORTAL_GD_URL="https://portal.example/sintetico",
        downloads_dir_path=tmp_path / "downloads",
        MAX_COMPLETED_TO_PROCESS=2,
        REPROCESS_EXISTING_PDFS=False,
        PROCESS_EXISTING_AFTER_SKIP=True,
        SKIP_ALREADY_COMPLETED=True,
        OP5_TARGET_PROTOCOLS="2600001048,2600001049",
        op5_target_protocols={"2600001048", "2600001049"},
    )

    class FakePlaywright:
        def stop(self) -> None:
            pass

    class FakePlaywrightFactory:
        def start(self) -> FakePlaywright:
            return FakePlaywright()

    monkeypatch.setattr(
        full_pipeline,
        "sync_playwright",
        lambda: FakePlaywrightFactory(),
    )
    monkeypatch.setattr(full_pipeline, "connect_to_existing_edge", lambda *_args: object())
    monkeypatch.setattr(full_pipeline, "find_portal_page_from_cdp", lambda *_args: object())
    monkeypatch.setattr(full_pipeline, "_reconciliation_callback", lambda _settings: None)

    def fake_download(**kwargs):
        captured.append(kwargs.get("settings"))
        return {"run_error": None, "status": OperationStatus.SUCESSO.value}

    monkeypatch.setattr(
        full_pipeline,
        "download_completed_budgets_from_current_page",
        fake_download,
    )

    result = full_pipeline._run_download_step(settings, state_store=object())

    assert result["run_error"] is None
    assert captured == [settings]


def test_pipeline_download_step_reports_manual_cdp_opening_when_listing_tab_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        CDP_ENDPOINT="http://127.0.0.1:9222",
        PORTAL_GD_URL="https://gdneoenergiapernambuco.neoenergia.com/",
        downloads_dir_path=tmp_path / "downloads",
        MAX_COMPLETED_TO_PROCESS=60,
        ENABLE_PORTAL_PAGINATION=True,
        MAX_PORTAL_PAGES=15,
        REPROCESS_EXISTING_PDFS=False,
        PROCESS_EXISTING_AFTER_SKIP=True,
        SKIP_ALREADY_COMPLETED=True,
    )

    class FakePlaywright:
        def stop(self) -> None:
            pass

    class FakePlaywrightFactory:
        def start(self) -> FakePlaywright:
            return FakePlaywright()

    monkeypatch.setattr(full_pipeline, "sync_playwright", lambda: FakePlaywrightFactory())
    monkeypatch.setattr(full_pipeline, "connect_to_existing_edge", lambda *_args: object())
    monkeypatch.setattr(full_pipeline, "find_portal_page_from_cdp", lambda *_args: None)

    result = full_pipeline._run_download_step(settings, state_store=object())

    assert result["total_errors"] == 1
    assert "PowerShell" in result["run_error"]
    assert "login manual" in result["run_error"]


def test_download_step_aborts_before_pagination_when_portal_turns_access_denied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        downloads_dir_path=tmp_path / "downloads",
        MAX_COMPLETED_TO_PROCESS=60,
        ENABLE_PORTAL_PAGINATION=True,
        MAX_PORTAL_PAGES=15,
        DRY_RUN=True,
        REPROCESS_EXISTING_PDFS=False,
        PROCESS_EXISTING_AFTER_SKIP=True,
        SKIP_ALREADY_COMPLETED=True,
        op5_target_protocols=set(),
    )
    page = SimpleNamespace(url="https://gdneoenergiapernambuco.neoenergia.com/index.jsf")

    monkeypatch.setattr(cdp_portal_service, "_page_looks_access_denied", lambda page: True)
    monkeypatch.setattr(
        cdp_portal_service,
        "ensure_listing_starts_on_page_one",
        lambda page: (_ for _ in ()).throw(
            AssertionError("pagination reset must not run after Access Denied")
        ),
    )

    summary = cdp_portal_service.download_completed_budgets_from_current_page(
        page,
        downloads_root=tmp_path / "downloads",
        settings=settings,
    )

    assert summary["aborted"] is True
    assert summary["total_errors"] == 1
    assert "Access Denied" in summary["run_error"]
    assert "PowerShell" in summary["run_error"]


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
