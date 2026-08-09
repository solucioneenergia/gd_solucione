from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook

from automacao_gd.application.equipment_reformatting import (
    reformat_completed_protocol_if_needed,
    select_completed_protocols_for_equipment_reformat,
)
from automacao_gd.domain.equipment import EQUIPMENT_FORMAT_VERSION
from automacao_gd.infrastructure.state.pipeline_state import PipelineStateStore


PROTOCOL = "2600000001"


def _budget_text(
    *,
    module_manufacturers: str = "BYD | TRINA",
    module_models: str = "P6C-30 260 | TSM-NEG21C 695",
    module_quantity: str = "100 | 118",
    inverter_manufacturers: str | None = "SOLIS | INTELBRAS",
    inverter_models: str | None = "S5-GC25K | EGT 25000 MAX",
    inverter_quantity: str | None = "1 | 2",
    micro_manufacturers: str | None = None,
    micro_models: str | None = None,
    micro_quantity: str | None = None,
) -> str:
    parts = [
        f"Fabricante(s) do(s) módulos(s): {module_manufacturers}",
        f"Modelo(s) do(s) módulos(s): {module_models}",
        f"Qtd módulos: {module_quantity}",
        "Pot. total da(s) placa(s): 99,31 kWp",
    ]
    if inverter_manufacturers is not None or inverter_models is not None:
        parts.extend(
            [
                f"Fabricante(s) do(s) inversor(es): {inverter_manufacturers or ''}",
                f"Modelo(s) do(s) inversor(es): {inverter_models or ''}",
            ]
        )
        if inverter_quantity is not None:
            parts.append(f"Qtd inversores: {inverter_quantity}")
        parts.append("Pot. total do(s) inversor(es): 75 kW")
    if micro_manufacturers is not None or micro_models is not None:
        parts.extend(
            [
                f"Fabricante(s) do(s) micro-inversor(es): {micro_manufacturers or ''}",
                f"Modelo(s) do(s) micro-inversor(es): {micro_models or ''}",
            ]
        )
        if micro_quantity is not None:
            parts.append(f"Qtd micro-inversores: {micro_quantity}")
        parts.append("Pot. total do(s) micro-inversor(es): 20 kW")
    return "\n".join(parts)


def _create_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(
        [
            "Cliente",
            "Protocolo",
            "Data de ingresso",
            "Conclusão",
            "Parecer",
            "Placa",
            "Inversor",
            "Observação",
        ]
    )
    ws.append(
        [
            "Cliente Sintético",
            PROTOCOL,
            datetime(2026, 1, 10),
            "",
            "Sim",
            "218x BYD TRINA P6C-30 260 TSM-NEG21C 695W",
            "3x HUAWEI ABB HUAWEI SUN2000-30KTL",
            "não alterar",
        ]
    )
    ws.auto_filter.ref = "A1:H2"
    ws.column_dimensions["F"].width = 42
    ws.column_dimensions["G"].width = 48
    wb.save(path)
    wb.close()


def _completed_entry(version: int | None = None, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "protocol": PROTOCOL,
        "status": "completed",
        "client_name": "Cliente Sintético",
        "excel": {"row": 2},
    }
    if version is not None:
        entry["equipment_format_version"] = version
    entry.update(extra)
    return entry


def _store_with_entry(tmp_path: Path, entry: dict[str, Any]) -> PipelineStateStore:
    store = PipelineStateStore(path=tmp_path / "state.json", resume=False)
    store.update_protocol(PROTOCOL, status="completed", data=entry)
    return store


def _run_reformat(
    tmp_path: Path,
    *,
    entry: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    dry_run: bool = False,
    state_store: PipelineStateStore | None = None,
    workbook_path: Path | None = None,
    pdf_path: Path | None = None,
    backup_excel: bool = False,
) -> dict[str, Any]:
    workbook_path = workbook_path or tmp_path / "planilha.xlsx"
    if not workbook_path.exists():
        _create_workbook(workbook_path)
    return reformat_completed_protocol_if_needed(
        protocol=PROTOCOL,
        state_entry=entry or _completed_entry(),
        workbook_path=workbook_path,
        state_store=state_store,
        metadata=metadata or {"raw_text": _budget_text()},
        pdf_path=pdf_path,
        dry_run=dry_run,
        backup_excel=backup_excel,
    )


def _row_values(path: Path) -> list[Any]:
    wb = load_workbook(path)
    ws = wb["2026"]
    try:
        return [ws.cell(row=2, column=column).value for column in range(1, 9)]
    finally:
        wb.close()


def test_completed_without_equipment_version_runs_reformat(tmp_path: Path) -> None:
    result = _run_reformat(tmp_path, entry=_completed_entry())

    assert result["action"] == "reformat_existing_excel_row"
    assert result["equipment_format_version"] == EQUIPMENT_FORMAT_VERSION
    assert result["updated_columns"] == ["Placa", "Inversor"]


def test_completed_with_equipment_version_1_runs_reformat(tmp_path: Path) -> None:
    result = _run_reformat(tmp_path, entry=_completed_entry(1))

    assert result["action"] == "reformat_existing_excel_row"
    assert result["previous_equipment_format_version"] == 1
    assert result["reformat_status"] == "success"


def test_completed_with_equipment_version_2_skips_reformat(tmp_path: Path) -> None:
    result = _run_reformat(tmp_path, entry=_completed_entry(2))

    assert result["action"] == "skipped_already_v2"
    assert result["reformat_status"] == "skipped_already_v2"


def test_reformat_uses_existing_metadata_when_available(tmp_path: Path) -> None:
    result = _run_reformat(tmp_path, metadata={"raw_text": _budget_text()})

    assert result["source_used"] == "metadata"
    assert "BYD | P6C-30 260" in result["placa_nova"]


def test_reformat_uses_local_pdf_when_metadata_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_path = tmp_path / "Orcamento_de_Conexao_2600000001.pdf"
    pdf_path.write_bytes(b"%PDF- synthetic")
    monkeypatch.setattr(
        "automacao_gd.application.equipment_reformatting.extract_pdf_text",
        lambda path: _budget_text(
            module_manufacturers="DMEGC/INTELBRAS",
            module_models="EMSB-535M HC/ DMEGC BIFACE",
            module_quantity="100 | 205",
        ),
    )

    result = reformat_completed_protocol_if_needed(
        protocol=PROTOCOL,
        state_entry=_completed_entry(1),
        workbook_path=tmp_path / "planilha.xlsx",
        metadata=None,
        pdf_path=pdf_path,
        dry_run=False,
        backup_excel=False,
    )

    assert result["source_used"] == "local_pdf"
    assert "DMEGC | EMSB-535M HC" in result["placa_nova"]


def test_reformat_without_metadata_or_pdf_goes_to_pending_review(tmp_path: Path) -> None:
    result = reformat_completed_protocol_if_needed(
        protocol=PROTOCOL,
        state_entry=_completed_entry(1),
        workbook_path=tmp_path / "planilha.xlsx",
        metadata=None,
        pdf_path=None,
        dry_run=False,
    )

    assert result["action"] == "pending_manual_review"
    assert result["reformat_status"] == "pending_review"
    assert result["equipment_format_version"] is None


def test_reformat_accepts_canonical_aggregate_quantities_without_per_item_quantities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_excel: dict[str, Any] = {}

    def fake_excel(**kwargs: Any) -> dict[str, Any]:
        captured_excel.update(kwargs)
        return {
            "success": True,
            "worksheet": "2026",
            "row_number": 2,
            "updated_columns": ["Placa", "Inversor"],
        }

    monkeypatch.setattr(
        "automacao_gd.application.equipment_reformatting.update_excel_equipment_columns",
        fake_excel,
    )
    result = reformat_completed_protocol_if_needed(
        protocol=PROTOCOL,
        state_entry=_completed_entry(1),
        workbook_path=tmp_path / "planilha.xlsx",
        metadata={"raw_text": _budget_text(module_quantity="218")},
        dry_run=False,
        backup_excel=False,
    )

    assert result["action"] == "reformat_existing_excel_row"
    assert result["reformat_status"] == "success"
    assert "Qtd. total: 218 módulos" in captured_excel["module_text"]
    assert "Qtd. total: 3 inversores" in captured_excel["inverter_text"]


def test_reformat_blocks_excel_when_canonical_quantities_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    excel_called = False

    def forbidden_excel(**kwargs: Any) -> dict[str, Any]:
        nonlocal excel_called
        excel_called = True
        raise AssertionError("Excel não pode ser chamado")

    monkeypatch.setattr(
        "automacao_gd.application.equipment_reformatting.update_excel_equipment_columns",
        forbidden_excel,
    )
    raw_text = "\n".join(
        [
            "Fabricante(s) do(s) módulos(s): BYD | TRINA",
            "Modelo(s) do(s) módulos(s): P6C-30 260 | TSM-NEG21C 695",
            "Pot. total da(s) placa(s): 99,31 kWp",
            "Fabricante(s) do(s) inversor(es): SOLIS | INTELBRAS",
            "Modelo(s) do(s) inversor(es): S5-GC25K | EGT 25000 MAX",
            "Pot. total do(s) inversor(es): 75 kW",
        ]
    )

    result = reformat_completed_protocol_if_needed(
        protocol=PROTOCOL,
        state_entry=_completed_entry(1),
        workbook_path=tmp_path / "planilha.xlsx",
        metadata={"raw_text": raw_text},
        dry_run=False,
        backup_excel=False,
    )

    assert result["action"] == "pending_manual_review"
    assert result["reformat_status"] == "pending_review"
    assert "MODULE_QUANTITY_MISSING" in result["warning"]
    assert "INVERTER_QUANTITY_MISSING" in result["warning"]
    assert not excel_called


def test_reformat_updates_only_module_and_inverter_columns(tmp_path: Path) -> None:
    workbook_path = tmp_path / "planilha.xlsx"
    _create_workbook(workbook_path)
    before = _row_values(workbook_path)

    _run_reformat(tmp_path, workbook_path=workbook_path)

    after = _row_values(workbook_path)
    assert after[:5] == before[:5]
    assert after[7] == before[7]
    assert after[5] != before[5]
    assert after[6] != before[6]


def test_reformat_applies_wrap_text_vertical_top_and_row_height(tmp_path: Path) -> None:
    workbook_path = tmp_path / "planilha.xlsx"
    _create_workbook(workbook_path)

    _run_reformat(tmp_path, workbook_path=workbook_path)

    wb = load_workbook(workbook_path)
    ws = wb["2026"]
    try:
        assert ws["F2"].alignment.wrap_text is True
        assert ws["G2"].alignment.wrap_text is True
        assert ws["F2"].alignment.vertical == "top"
        assert ws["G2"].alignment.vertical == "top"
        assert ws.row_dimensions[2].height >= 45
    finally:
        wb.close()


def test_reformat_does_not_call_download_or_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"download": 0, "archive": 0}

    def download_spy(*args: Any, **kwargs: Any) -> None:
        calls["download"] += 1

    def archive_spy(*args: Any, **kwargs: Any) -> None:
        calls["archive"] += 1

    monkeypatch.setattr(
        "automacao_gd.infrastructure.portal.cdp_service.download_connection_budget",
        download_spy,
    )
    monkeypatch.setattr(
        "automacao_gd.infrastructure.files.client_folder_service.archive_pdf_to_client_folder",
        archive_spy,
    )

    result = _run_reformat(tmp_path)

    assert result["action"] == "reformat_existing_excel_row"
    assert calls == {"download": 0, "archive": 0}


def test_reformat_does_not_create_versioned_pdf_copies(tmp_path: Path) -> None:
    _run_reformat(tmp_path)

    created_names = [path.name for path in tmp_path.rglob("*")]
    assert not any("_v2" in name or "_v3" in name for name in created_names)


def test_reformat_never_uses_old_excel_cell_text_as_equipment_source(tmp_path: Path) -> None:
    result = reformat_completed_protocol_if_needed(
        protocol=PROTOCOL,
        state_entry=_completed_entry(1),
        workbook_path=tmp_path / "planilha.xlsx",
        metadata=None,
        pdf_path=None,
        old_excel_cells={
            "Placa": "218x BYD TRINA P6C-30 260 TSM-NEG21C 695W",
            "Inversor": "3x HUAWEI ABB HUAWEI SUN2000-30KTL",
        },
        dry_run=False,
    )

    assert result["action"] == "pending_manual_review"
    assert result["source_used"] == "none"


def test_state_registers_equipment_format_version_after_success(tmp_path: Path) -> None:
    store = _store_with_entry(tmp_path, _completed_entry(1))

    result = _run_reformat(tmp_path, entry=store.get_protocol(PROTOCOL), state_store=store)

    entry = store.get_protocol(PROTOCOL)
    assert result["success"] is True
    assert entry["equipment_format_version"] == EQUIPMENT_FORMAT_VERSION
    assert entry["last_action"] == "reformat_existing_excel_row"
    assert entry["reformat_status"] == "success"
    assert entry["source_used"] == "metadata"


def test_state_does_not_register_equipment_version_when_reformat_fails(tmp_path: Path) -> None:
    store = _store_with_entry(tmp_path, _completed_entry(1))
    missing_workbook = tmp_path / "ausente.xlsx"

    result = reformat_completed_protocol_if_needed(
        protocol=PROTOCOL,
        state_entry=store.get_protocol(PROTOCOL),
        workbook_path=missing_workbook,
        state_store=store,
        metadata={"raw_text": _budget_text()},
        dry_run=False,
    )

    entry = store.get_protocol(PROTOCOL)
    assert result["success"] is False
    assert entry.get("equipment_format_version") == 1
    assert entry["reformat_status"] == "failed"


def test_dry_run_reports_expected_change_without_saving_workbook_or_state(
    tmp_path: Path,
) -> None:
    workbook_path = tmp_path / "planilha.xlsx"
    _create_workbook(workbook_path)
    before_bytes = workbook_path.read_bytes()
    store = _store_with_entry(tmp_path, _completed_entry(1))
    before_entry = deepcopy(store.get_protocol(PROTOCOL))

    result = _run_reformat(
        tmp_path,
        entry=store.get_protocol(PROTOCOL),
        state_store=store,
        workbook_path=workbook_path,
        dry_run=True,
    )

    assert result["reformat_status"] == "planned"
    assert result["placa_nova"]
    assert workbook_path.read_bytes() == before_bytes
    assert store.get_protocol(PROTOCOL) == before_entry


def test_apply_saves_synthetic_workbook_and_temporary_state(tmp_path: Path) -> None:
    workbook_path = tmp_path / "planilha.xlsx"
    _create_workbook(workbook_path)
    store = _store_with_entry(tmp_path, _completed_entry(1))

    _run_reformat(
        tmp_path,
        entry=store.get_protocol(PROTOCOL),
        state_store=store,
        workbook_path=workbook_path,
        dry_run=False,
    )

    values = _row_values(workbook_path)
    assert values[5] == (
        "100x BYD | P6C-30 260\n"
        "118x TRINA | TSM-NEG21C 695\n"
        "Qtd. total: 218 módulos"
    )
    assert store.get_protocol(PROTOCOL)["equipment_format_version"] == EQUIPMENT_FORMAT_VERSION


def test_second_execution_after_success_returns_skipped_already_v2(tmp_path: Path) -> None:
    store = _store_with_entry(tmp_path, _completed_entry(1))
    _run_reformat(tmp_path, entry=store.get_protocol(PROTOCOL), state_store=store)

    result = _run_reformat(tmp_path, entry=store.get_protocol(PROTOCOL), state_store=store)

    assert result["action"] == "skipped_already_v2"


def test_selection_identifies_only_completed_protocols_below_v2() -> None:
    state = {
        "protocols": {
            "1": {"status": "completed"},
            "2": {"status": "completed", "equipment_format_version": 1},
            "3": {"status": "completed", "equipment_format_version": 2},
            "4": {"status": "in_progress", "equipment_format_version": 1},
        }
    }

    assert select_completed_protocols_for_equipment_reformat(state) == ["1", "2"]


def test_pipeline_state_next_step_skips_completed_v2(tmp_path: Path) -> None:
    store = _store_with_entry(tmp_path, _completed_entry(EQUIPMENT_FORMAT_VERSION))

    assert (
        store.next_step_for_protocol(
            PROTOCOL,
            current_fingerprint="abc",
            equipment_format_version=EQUIPMENT_FORMAT_VERSION,
        )
        == "completed"
    )


def test_pipeline_state_next_step_reformats_old_equipment_version(tmp_path: Path) -> None:
    store = _store_with_entry(tmp_path, _completed_entry(1))

    assert (
        store.next_step_for_protocol(
            PROTOCOL,
            current_fingerprint="abc",
            equipment_format_version=EQUIPMENT_FORMAT_VERSION,
        )
        == "reformat_existing_excel_row"
    )
