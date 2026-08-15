from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from automacao_gd.application.historical_backfill.audit_service import (
    TechnicalProposal,
    audit_historical_workbook,
)
from automacao_gd.application.historical_backfill.plan import (
    BackfillPlanError,
    build_backfill_plan,
)
from automacao_gd.application.historical_backfill.comparison import row_fingerprint
from automacao_gd.domain.backfill_models import (
    BackfillAction,
    HistoricalAuditResult,
    HistoricalAuditSummary,
    HistoricalEquipmentAuditItem,
)
from automacao_gd.domain.equipment_semantics import (
    CanonicalEquipmentCollection,
    EquipmentSource,
    EquipmentSourceType,
    canonical_collection_from_formatted_cells,
    canonical_collection_to_dict,
    canonicalize_equipment,
    format_canonical_collection,
    semantic_fingerprint,
)


def _workbook(path: Path, row: tuple[str, str, str]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "2025"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append(row)
    wb.save(path)


def _resolver(
    *,
    module_text: str,
    inverter_text: str,
    collection: CanonicalEquipmentCollection,
) -> TechnicalProposal:
    return TechnicalProposal(
        status="approved",
        module_text=module_text,
        inverter_text=inverter_text,
        source_hash="a" * 64,
        pdf_status="valid",
        canonical_collection=collection,
    )


def _collection(
    *,
    module: tuple[str, str, int, EquipmentSourceType],
    inverter: tuple[str, str, int, EquipmentSourceType],
) -> CanonicalEquipmentCollection:
    module_manufacturer, module_model, module_quantity, module_source = module
    inverter_manufacturer, inverter_model, inverter_quantity, inverter_source = inverter
    return CanonicalEquipmentCollection(
        modules=(
            canonicalize_equipment(
                equipment_type="module",
                manufacturer=module_manufacturer,
                model=module_model,
                quantity=module_quantity,
                source=EquipmentSource(module_source),
            ),
        ),
        conventional_inverters=(
            canonicalize_equipment(
                equipment_type="inverter",
                manufacturer=inverter_manufacturer,
                model=inverter_model,
                quantity=inverter_quantity,
                source=EquipmentSource(inverter_source),
            ),
        ),
    )


def _audit_item(
    *,
    current_module: str,
    current_inverter: str,
    proposed_module: str,
    proposed_inverter: str,
    source_collection: CanonicalEquipmentCollection,
) -> HistoricalEquipmentAuditItem:
    current_collection = canonical_collection_from_formatted_cells(
        current_module, current_inverter
    )
    canonical_module, canonical_inverter = format_canonical_collection(
        source_collection
    )
    return HistoricalEquipmentAuditItem(
        workbook_sheet="2025",
        workbook_row=2,
        protocol="2600001011",
        current_module_text=current_module,
        current_inverter_text=current_inverter,
        proposed_module_text=canonical_module,
        proposed_inverter_text=canonical_inverter,
        technical_validation_status="approved",
        action=BackfillAction.UPDATE_EQUIPMENT,
        reasons=("MODULE_DIFFERENT", "INVERTER_DIFFERENT"),
        pdf_status="valid",
        source_hash="b" * 64,
        expected_row_fingerprint=row_fingerprint(
            "2025", 2, "2600001011", current_module, current_inverter
        ),
        semantic_validation_status="approved",
        current_semantic_fingerprint=semantic_fingerprint(
            current_collection.all_items()
        ),
        proposed_semantic_fingerprint=semantic_fingerprint(
            source_collection.all_items()
        ),
        current_canonical_collection=canonical_collection_to_dict(current_collection),
        proposed_canonical_collection=canonical_collection_to_dict(source_collection),
    )


def _plan_from_item(item: HistoricalEquipmentAuditItem) -> dict:
    return build_backfill_plan(
        HistoricalAuditResult(
            workbook_fingerprint="c" * 64,
            items=(item,),
            summary=HistoricalAuditSummary(
                sheets_analyzed=1,
                rows_analyzed=1,
                valid_protocols=1,
                no_change=0,
                total_updates=1,
                pending_review=0,
                pdf_not_found=0,
                protocol_missing=0,
                duplicate_protocols=0,
            ),
            created_at="2026-07-24T12:00:00Z",
        ),
        created_at="2026-07-24T12:00:00Z",
    )


def test_cross_field_module_and_inverter_contamination_is_approved() -> None:
    source = _collection(
        module=("LEAPTON", "LP182-M-72-MH 585W", 12, EquipmentSourceType.TABLE_CELL),
        inverter=("SOLPLANET", "ASW5000-S", 1, EquipmentSourceType.TABLE_CELL),
    )

    payload = _plan_from_item(
        _audit_item(
            current_module="",
            current_inverter=(
                "Solplanet | LEAPTON ASW5000-S LP182-M-72-MH 585W\n"
                "Aiswei | LEAPTON ASW5000-S LP182-M-72-MH 585W\n"
                "Qtd. total: 1 inversor"
            ),
            proposed_module="12x LEAPTON LP182-M-72-MH 585W",
            proposed_inverter="1x SOLPLANET ASW5000-S",
            source_collection=source,
        )
    )

    assert payload["items"][0]["action"] == "UPDATE_EQUIPMENT"
    assert payload["items"][0]["blocking_violations"] == []
    assert "CROSS_FIELD_CONTAMINATION_RESOLVED" in payload["items"][0]["warnings"]


def test_cross_field_comparison_still_blocks_real_lost_inverter() -> None:
    source = _collection(
        module=("LEAPTON", "LP182-M-72-MH 585W", 12, EquipmentSourceType.TABLE_CELL),
        inverter=("SOLPLANET", "ASW5000-S", 1, EquipmentSourceType.TABLE_CELL),
    )

    with pytest.raises(BackfillPlanError, match="regress"):
        _plan_from_item(
            _audit_item(
                current_module="",
                current_inverter=(
                    "1x SOLPLANET ASW5000-S\n"
                    "1x GROWATT MIC 3000TL-X\n"
                    "LEAPTON LP182-M-72-MH 585W"
                ),
                proposed_module="12x LEAPTON LP182-M-72-MH 585W",
                proposed_inverter="1x SOLPLANET ASW5000-S",
                source_collection=source,
            )
        )


@pytest.mark.parametrize(
    ("protocol", "current_module", "current_inverter", "proposed_inverter", "source"),
    [
        (
            "2600001069",
            "12x GOKIN GK-1-72HTBD 585W",
            "1x Ginlong (Solis) SOLIS 1P7,7K-5G",
            "1x GINLONG (SOLIS) SOLIS 1P7,7K-5G",
            _collection(
                module=("GOKIN", "GK-1-72HTBD 585W", 12, EquipmentSourceType.TABLE_CELL),
                inverter=(
                    "GINLONG (SOLIS)",
                    "SOLIS 1P7,7K-5G",
                    1,
                    EquipmentSourceType.TABLE_CELL,
                ),
            ),
        ),
        (
            "2600001082",
            "48x TRINA TSM-NEG19RC620W",
            "1x GUANGZHOU SANJING ELECTRIC CO., LTD (SAJ) SAJ TRIF AFCI 25K-R6-ON GRID",
            "1x GUANGZHOU SANJING ELECTRIC CO., LTD (SAJ) SAJ TRIF AFCI 25K-R6-ON GRID",
            _collection(
                module=("TRINA", "TSM-NEG19RC620W", 48, EquipmentSourceType.TABLE_CELL),
                inverter=(
                    "GUANGZHOU SANJING ELECTRIC CO., LTD (SAJ)",
                    "SAJ TRIF AFCI 25K-R6-ON GRID",
                    1,
                    EquipmentSourceType.TABLE_CELL,
                ),
            ),
        ),
    ],
)
def test_documented_alias_at_model_prefix_is_no_change(
    tmp_path: Path,
    protocol: str,
    current_module: str,
    current_inverter: str,
    proposed_inverter: str,
    source: CanonicalEquipmentCollection,
) -> None:
    workbook = tmp_path / "historico.xlsx"
    _workbook(workbook, (protocol, current_module, current_inverter))

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text=current_module,
            inverter_text=proposed_inverter,
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.UPDATE_EQUIPMENT
    assert "DUPLICATED_MANUFACTURER_IN_MODEL" not in result.items[0].reasons


@pytest.mark.parametrize(
    (
        "protocol",
        "current_module",
        "proposed_module",
        "source_module",
        "expected_reason",
    ),
    [
        (
            "2600001068",
            "6x TSUN TSUN 605W BIFACIAL",
            "6x TSUN 605W BIFACIAL",
            ("TSUN", "605W BIFACIAL", 6, EquipmentSourceType.TABLE_CELL),
            "DUPLICATED_MANUFACTURER_IN_MODEL_REMOVED",
        ),
        (
            "2600001074",
            "7x DMEGC Dmegc 605W (Monocristalino/N- Type) Bifacial",
            "7x DMEGC 605W (Monocristalino/N- Type) Bifacial",
            (
                "DMEGC",
                "605W (Monocristalino/N- Type) Bifacial",
                7,
                EquipmentSourceType.TABLE_CELL,
            ),
            "DUPLICATED_MANUFACTURER_IN_MODEL_REMOVED",
        ),
    ],
)
def test_duplicate_manufacturer_text_cleanup_is_update(
    tmp_path: Path,
    protocol: str,
    current_module: str,
    proposed_module: str,
    source_module: tuple[str, str, int, EquipmentSourceType],
    expected_reason: str,
) -> None:
    source = _collection(
        module=source_module,
        inverter=("GROWATT", "MIC 3000TL-X", 1, EquipmentSourceType.TABLE_CELL),
    )
    workbook = tmp_path / "historico.xlsx"
    _workbook(workbook, (protocol, current_module, "1x GROWATT MIC 3000TL-X"))

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text=proposed_module,
            inverter_text="1x GROWATT MIC 3000TL-X",
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.UPDATE_EQUIPMENT
    assert result.items[0].blocking_violations == ()
    assert expected_reason in result.items[0].warnings


@pytest.mark.parametrize(
    ("protocol", "current_inverter", "proposed_inverter"),
    [
        ("2600001079", "1x SOLPLANET/AISWEI ASW4000-S", "1x SOLPLANET ASW4000-S"),
        ("2600001080", "1x SOLPLANET/AISWEI ASW5000-S", "1x SOLPLANET ASW5000-S"),
    ],
)
def test_solplanet_aiswei_text_cleanup_is_update(
    tmp_path: Path,
    protocol: str,
    current_inverter: str,
    proposed_inverter: str,
) -> None:
    model = proposed_inverter.removeprefix("1x SOLPLANET ")
    source = _collection(
        module=("JINKO", "JKM625N-78HL4-BDV 625W", 8, EquipmentSourceType.TABLE_CELL),
        inverter=("SOLPLANET", model, 1, EquipmentSourceType.TABLE_CELL),
    )
    workbook = tmp_path / "historico.xlsx"
    module_text = "8x JINKO JKM625N-78HL4-BDV 625W"
    _workbook(workbook, (protocol, module_text, current_inverter))

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text=module_text,
            inverter_text=proposed_inverter,
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.UPDATE_EQUIPMENT
    assert result.items[0].blocking_violations == ()
    assert "SOLPLANET_ALIAS_NORMALIZED" in result.items[0].reasons


def test_corporate_capitalization_only_remains_no_change(tmp_path: Path) -> None:
    source = _collection(
        module=("ASTRONERGY", "ASTRO N5 585W", 6, EquipmentSourceType.TABLE_CELL),
        inverter=("GINLONG (SOLIS)", "S6-GR1P3K-M", 1, EquipmentSourceType.TABLE_CELL),
    )
    workbook = tmp_path / "historico.xlsx"
    _workbook(
        workbook,
        (
            "2600001012",
            "6x astronergy ASTRO N5 585W",
            "1x Ginlong (Solis) S6-GR1P3K-M",
        ),
    )

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text="6x ASTRONERGY ASTRO N5 585W",
            inverter_text="1x GINLONG (SOLIS) S6-GR1P3K-M",
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.UPDATE_EQUIPMENT
    assert "CANONICAL_FORMAT_REQUIRED" in result.items[0].warnings


def test_text_difference_without_cleanup_evidence_remains_no_change(
    tmp_path: Path,
) -> None:
    source = _collection(
        module=("JINKO", "JKM625N 625W", 10, EquipmentSourceType.TABLE_CELL),
        inverter=("HUAWEI", "SUN2000-5KTL-L1", 1, EquipmentSourceType.TABLE_CELL),
    )
    workbook = tmp_path / "historico.xlsx"
    _workbook(
        workbook,
        (
            "2600001013",
            "10x Jinko JKM625N 625W",
            "1x Huawei SUN2000-5KTL-L1",
        ),
    )

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text="10x JINKO JKM625N 625W",
            inverter_text="1x HUAWEI SUN2000-5KTL-L1",
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.UPDATE_EQUIPMENT
    assert "CANONICAL_FORMAT_REQUIRED" in result.items[0].warnings


def test_duplicate_warning_without_actual_manufacturer_removal_remains_no_change(
    tmp_path: Path,
) -> None:
    source = _collection(
        module=("LEAPTON", "MONO HALF-CELL 665W", 12, EquipmentSourceType.TABLE_CELL),
        inverter=("SOLIS", "Solis-1P5K -4G", 1, EquipmentSourceType.TABLE_CELL),
    )
    workbook = tmp_path / "historico.xlsx"
    _workbook(
        workbook,
        (
            "2600001088",
            "12x LEAPTON MONO HALF-CELL 665W",
            "1x SOLIS SOLIS-1P5K - 4G",
        ),
    )

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text="12x LEAPTON MONO HALF-CELL 665W",
            inverter_text="1x SOLIS Solis-1P5K -4G",
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.UPDATE_EQUIPMENT
    assert "DUPLICATED_MANUFACTURER_IN_MODEL_REMOVED" in result.items[0].warnings


def test_true_duplicate_manufacturer_in_model_is_valid_update() -> None:
    source = _collection(
        module=(
            "DMEGC",
            "605W (Monocristalino/N-Type) Bifacial",
            23,
            EquipmentSourceType.TABLE_CELL,
        ),
        inverter=("GINLONG (SOLIS)", "Solis S5 10KW", 1, EquipmentSourceType.TABLE_CELL),
    )

    payload = _plan_from_item(
        _audit_item(
            current_module="23x DMEGC Dmegc 605W (Monocristalino/N-Type) Bifacial",
            current_inverter="1x Ginlong (Solis) Solis S5 10KW",
            proposed_module="23x DMEGC 605W (Monocristalino/N-Type) Bifacial",
            proposed_inverter="1x GINLONG (SOLIS) Solis S5 10KW",
            source_collection=source,
        )
    )

    assert payload["items"][0]["action"] == "UPDATE_EQUIPMENT"
    assert payload["items"][0]["blocking_violations"] == []
    assert "DUPLICATED_MANUFACTURER_IN_MODEL_REMOVED" in payload["items"][0]["warnings"]


def test_tsun_structural_label_contamination_with_origin_is_update(
    tmp_path: Path,
) -> None:
    source = _collection(
        module=(
            "TSUN",
            "TSUN MODULO 690W HJT TSUN BIFACIAL 35MM",
            17,
            EquipmentSourceType.TABLE_HEADER_CONTAMINATION,
        ),
        inverter=("SAJ", "R5-5K-S1", 1, EquipmentSourceType.TABLE_CELL),
    )
    workbook = tmp_path / "historico.xlsx"
    _workbook(
        workbook,
        (
            "2600001075",
            "17x TSUN MODULO 690W HJT TSUN BIFACIAL 35MM",
            "1x SAJ R5-5K-S1",
        ),
    )

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text="17x TSUN 690W HJT BIFACIAL 35MM",
            inverter_text="1x SAJ R5-5K-S1",
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.UPDATE_EQUIPMENT
    assert result.items[0].blocking_violations == ()
    assert "PROVEN_STRUCTURAL_CONTAMINATION_REMOVED" in result.items[0].warnings


def test_tsun_structural_label_contamination_without_origin_stays_pending(
    tmp_path: Path,
) -> None:
    source = _collection(
        module=(
            "TSUN",
            "TSUN MODULO 690W HJT TSUN BIFACIAL 35MM",
            17,
            EquipmentSourceType.UNKNOWN,
        ),
        inverter=("SAJ", "R5-5K-S1", 1, EquipmentSourceType.TABLE_CELL),
    )
    workbook = tmp_path / "historico.xlsx"
    _workbook(
        workbook,
        (
            "2600001075",
            "17x TSUN MODULO 690W HJT TSUN BIFACIAL 35MM",
            "1x SAJ R5-5K-S1",
        ),
    )

    result = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: _resolver(
            module_text="17x TSUN 690W HJT BIFACIAL 35MM",
            inverter_text="1x SAJ R5-5K-S1",
            collection=source,
        ),
    )

    assert result.items[0].action is BackfillAction.PENDING_TECHNICAL_REVIEW
