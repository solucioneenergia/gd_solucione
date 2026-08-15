from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from automacao_gd.application.historical_backfill.audit_service import (
    TechnicalProposal,
    audit_historical_workbook,
)
from automacao_gd.application.historical_backfill.apply_service import (
    BackfillApplyError,
    apply_backfill_plan,
    required_apply_confirmation,
)
from automacao_gd.application.historical_backfill.plan import (
    BACKFILL_PLAN_VERSION,
    BackfillPlanError,
    build_backfill_plan,
    calculate_plan_hash,
    validate_backfill_plan,
)
from automacao_gd.domain.backfill_models import BackfillAction, BackfillApplyStatus
from automacao_gd.domain.equipment_semantics import (
    CanonicalEquipmentCollection,
    EquipmentSource,
    EquipmentSourceType,
    canonicalize_equipment,
    format_canonical_collection,
    format_canonical_equipment,
    remove_redundant_manufacturer_prefix,
    semantic_compare_equipment_cells,
)
from automacao_gd.domain.equipment_validation import EQUIPMENT_RULES_VERSION
from automacao_gd.domain.models import GenerationData, InverterEquipment, ModuleEquipment


def _canonical_text(
    manufacturer: str,
    model: str,
    *,
    equipment_type: str = "module",
    source_type: EquipmentSourceType = EquipmentSourceType.TABLE_CELL,
) -> str:
    equipment = canonicalize_equipment(
        equipment_type=equipment_type,
        manufacturer=manufacturer,
        model=model,
        quantity=1,
        source=EquipmentSource(origin_type=source_type),
    )
    return format_canonical_equipment(equipment)


@pytest.mark.parametrize(
    ("manufacturer", "model", "expected"),
    [
        ("LEAPTON", "LEAPTON PANTHER 585W", "1x LEAPTON PANTHER 585W"),
        ("TSUN", "TSUN 600W BIFACIAL", "1x TSUN 600W BIFACIAL"),
        ("SUNGROW", "SUNGROW SG5KW-RS", "1x SUNGROW SG5KW-RS"),
        ("SOLPLANET", "AISWEI ASW6000-S-G2", "1x SOLPLANET ASW6000-S-G2"),
        ("SAJ", "SAJ AFCI 3K-R5", "1x SAJ AFCI 3K-R5"),
        ("JA", "JAM66D45 610W", "1x JA JAM66D45 610W"),
    ],
)
def test_canonical_equipment_removes_only_redundant_manufacturer_tokens(
    manufacturer: str, model: str, expected: str
) -> None:
    assert _canonical_text(manufacturer, model) == expected


def test_redundant_manufacturer_prefix_removes_consecutive_repetitions() -> None:
    assert (
        remove_redundant_manufacturer_prefix(
            "LEAPTON", "LEAPTON LEAPTON LEAPTON PANTHER 585W"
        )
        == "PANTHER 585W"
    )


@pytest.mark.parametrize(
    ("equipment_type", "manufacturer", "model", "required", "forbidden"),
    [
        (
            "module",
            "TSUN",
            "TSUN MODULO 610W N-TYPE TSUN BIFACIAL 30MM",
            {"610W", "N-TYPE", "BIFACIAL", "30MM"},
            {"MODULO"},
        ),
        (
            "inverter",
            "SAJ",
            "SAJ INVERSOR SAJ AFCI MONO 10K-R6",
            {"AFCI", "MONO", "10K-R6"},
            {"INVERSOR"},
        ),
    ],
)
def test_generic_table_labels_are_removed_but_technical_attributes_survive(
    equipment_type: str,
    manufacturer: str,
    model: str,
    required: set[str],
    forbidden: set[str],
) -> None:
    result = _canonical_text(
        manufacturer,
        model,
        equipment_type=equipment_type,
        source_type=EquipmentSourceType.TABLE_HEADER_CONTAMINATION,
    )
    assert required.issubset(set(result.split()))
    assert forbidden.isdisjoint(set(result.split()))


@pytest.mark.parametrize("power", ["610 W", "585 W", "710 W"])
def test_explicit_watt_unit_is_structured_and_formatted_without_space(power: str) -> None:
    equipment = canonicalize_equipment(
        equipment_type="module",
        manufacturer="TEST",
        model=f"MODEL {power}",
        quantity=1,
        source="synthetic_table",
    )
    assert equipment.power_value == power.split()[0]
    assert equipment.power_unit == "W"
    assert power.replace(" ", "") in format_canonical_equipment(equipment)


@pytest.mark.parametrize("model", ["RM182/144TB 585W", "144 CELLS", "30MM", "182M", "72H"])
def test_power_normalization_neither_damages_models_nor_invents_w(model: str) -> None:
    result = canonicalize_equipment(
        equipment_type="module",
        manufacturer="RONMA",
        model=model,
        source="synthetic_table",
    )
    assert result.canonical_model == model
    assert (result.power_unit == "W") is model.endswith("585W")


@pytest.mark.parametrize(
    ("current", "proposed", "error"),
    [
        ("25x JA JAM66D45 610W", "25x JA JAM66D45 610", "POWER_UNIT_LOST"),
        (
            "58x LEAPTON PANTHER 585W",
            "58x LEAPTON LEAPTON PANTHER 585W",
            "DUPLICATED_MANUFACTURER_IN_MODEL",
        ),
        (
            "1x SUNGROW SG5KW-RS",
            "1x SUNGROW SUNGROW SG5KW-RS",
            "DUPLICATED_MANUFACTURER_IN_MODEL",
        ),
    ],
)
def test_semantic_non_degradation_blocks_worse_proposals(
    current: str, proposed: str, error: str
) -> None:
    result = semantic_compare_equipment_cells(current, "", proposed, "")
    assert result.status == "pending_review"
    assert error in result.errors


def test_cosmetic_case_separator_and_spacing_change_is_no_change() -> None:
    result = semantic_compare_equipment_cells(
        "5x leapton   panther 585 w",
        "1x Solplanet ASW5000-S",
        "5x LEAPTON | PANTHER 585W",
        "1x SOLPLANET ASW5000-S",
    )
    assert result.status == "approved"
    assert result.equivalent


@pytest.mark.parametrize(
    ("protocol", "current_module", "current_inverter", "proposed_module", "proposed_inverter"),
    [
        (
            "2600001077",
            "5x TSUN 600W BIFACIAL",
            "1x SUNGROW SG5KW-RS",
            "5x TSUN TSUN 600W BIFACIAL",
            "1x SUNGROW SUNGROW SG5KW-RS",
        ),
        (
            "2600001078",
            "58x LEAPTON PANTHER 585W",
            "1x SAJ 10K-R6",
            "58x LEAPTON LEAPTON PANTHER 585W",
            "1x SAJ 10K-R6",
        ),
        (
            "2600001094",
            "18x RONMA RM182/144TB 585W",
            "1x SAJ 10K-R6",
            "18x RONMA RM182/144TB 585",
            "1x SAJ 10K-R6",
        ),
    ],
)
def test_known_protocol_fixtures_never_become_degrading_updates(
    tmp_path: Path,
    protocol: str,
    current_module: str,
    current_inverter: str,
    proposed_module: str,
    proposed_inverter: str,
) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append((protocol, current_module, current_inverter))
    wb.save(workbook)
    proposal = TechnicalProposal(
        status="approved",
        module_text=proposed_module,
        inverter_text=proposed_inverter,
        source_hash="a" * 64,
        pdf_status="valid",
    )

    audited = audit_historical_workbook(
        workbook, technical_resolver=lambda _: proposal
    )

    assert audited.items[0].action is BackfillAction.PENDING_TECHNICAL_REVIEW
    assert audited.summary.total_updates == 0


def test_plan_v3_quality_gate_and_rules5_are_mandatory(tmp_path: Path) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append(("2600001046", "OLD MODULE", "OLD INVERTER"))
    wb.save(workbook)
    proposal = TechnicalProposal(
        status="approved",
        module_text="5x LEAPTON PANTHER 585W",
        inverter_text="1x SUNGROW SG5KW-RS",
        source_hash="b" * 64,
        pdf_status="valid",
    )
    audited = audit_historical_workbook(workbook, technical_resolver=lambda _: proposal)
    payload = build_backfill_plan(audited, created_at="2026-07-22T10:00:00Z")

    assert BACKFILL_PLAN_VERSION == 3
    assert EQUIPMENT_RULES_VERSION.endswith("rules-7")
    assert payload["plan_status"] == "VALID"
    assert payload["quality_summary"]["blocking_violations"] == 0
    assert validate_backfill_plan(payload) is None

    old = dict(payload)
    old["equipment_rules_version"] = "equipment-v2-technical-v4-rules-1"
    old["plan_hash"] = calculate_plan_hash(old)
    with pytest.raises(BackfillPlanError, match="regras incompatível"):
        validate_backfill_plan(old)


def test_rules4_plan_is_rejected_after_rules5_activation(tmp_path: Path) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append(("2600001084", "38x TSUN 610W N-TYPE", "1x SAJ 25K-R6"))
    wb.save(workbook)
    audited = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: TechnicalProposal(
            status="approved",
            module_text="38x TSUN 610W N-TYPE",
            inverter_text="1x SAJ 25K-R6",
            source_hash="a" * 64,
            pdf_status="valid",
        ),
    )
    payload = build_backfill_plan(audited, created_at="2026-07-22T10:00:00Z")
    payload["equipment_rules_version"] = "equipment-v2-technical-v5-rules-4"
    payload["plan_hash"] = calculate_plan_hash(payload)

    with pytest.raises(BackfillPlanError, match="regras incompatível"):
        validate_backfill_plan(payload)


def test_rules5_audit_blocks_unproven_known_unsafe_protocols(tmp_path: Path) -> None:
    workbook = tmp_path / "unsafe_rules3_rows.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append(
        (
            "2600001084",
            "38x TSUN 610W N-TYPE TSUN BIFACIAL 30MM",
            "1x SAJ 25K-R6-ON GRID",
        )
    )
    ws.append(
        (
            "2600001085",
            "25x TSUN 615W N-TYPE TSUN BIFACIAL 30MM",
            "1x SAJ AFCI MONO 10K-R6 220V 3MPPT",
        )
    )
    wb.save(workbook)

    linear = EquipmentSource(EquipmentSourceType.LINEAR_FIELD_VALUE)
    table = EquipmentSource(EquipmentSourceType.TABLE_CELL)
    proposals: dict[str, CanonicalEquipmentCollection] = {
        "2600001084": CanonicalEquipmentCollection(
            modules=(
                canonicalize_equipment(
                    equipment_type="module",
                    manufacturer="TSUN",
                    model="MODULO 610W N-TYPE TSUN BIFACIAL 30MM",
                    quantity=38,
                    source=linear,
                ),
            ),
            conventional_inverters=(
                canonicalize_equipment(
                    equipment_type="inverter",
                    manufacturer="SAJ",
                    model="25K-R6-ON GRID",
                    quantity=1,
                    source=table,
                ),
            ),
        ),
        "2600001085": CanonicalEquipmentCollection(
            modules=(
                canonicalize_equipment(
                    equipment_type="module",
                    manufacturer="TSUN",
                    model="MODULO 615W N-TYPE TSUN BIFACIAL 30MM",
                    quantity=25,
                    source=linear,
                ),
            ),
            conventional_inverters=(
                canonicalize_equipment(
                    equipment_type="inverter",
                    manufacturer="SAJ",
                    model="INVERSOR SAJ AFCI MONO 10K-R6 220V 3MPPT",
                    quantity=1,
                    source=linear,
                ),
            ),
        ),
    }

    def resolver(protocol: str) -> TechnicalProposal:
        collection = proposals[protocol]
        module_text, inverter_text = format_canonical_collection(collection)
        return TechnicalProposal(
            status="approved",
            module_text=module_text,
            inverter_text=inverter_text,
            source_hash="a" * 64,
            pdf_status="valid",
            canonical_collection=collection,
        )

    audited = audit_historical_workbook(workbook, technical_resolver=resolver)

    assert {item.protocol for item in audited.items} == {
        "2600001084",
        "2600001085",
    }
    assert all(
        item.action is BackfillAction.PENDING_TECHNICAL_REVIEW
        for item in audited.items
    )
    assert all("GENERIC_LABEL_IN_MODEL" in item.reasons for item in audited.items)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proposed_module_text", "5x LEAPTON LEAPTON PANTHER 585W"),
        ("proposed_module_text", "5x LEAPTON MODULO PANTHER 585W"),
        ("proposed_module_text", "5x LEAPTON PANTHER 585"),
        ("semantic_validation_status", "pending_review"),
        ("blocking_violations", ["SEMANTIC_REGRESSION"]),
    ],
)
def test_plan_quality_gate_recomputes_and_rejects_invalid_update(
    tmp_path: Path, field: str, value: object
) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append(
        (
            "2600001047",
            "5x LEAPTON LEAPTON PANTHER 585W",
            "1x SUNGROW SG5KW-RS",
        )
    )
    wb.save(workbook)
    audited = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: TechnicalProposal(
            status="approved",
            module_text=(
                "5x LEAPTON | PANTHER 585W\n"
                "2x JA | JAM66D45 610W\n"
                "Qtd. total: 7 módulos"
            ),
            inverter_text="1x SUNGROW SG5KW-RS",
            source_hash="c" * 64,
            pdf_status="valid",
        ),
    )
    payload = build_backfill_plan(audited, created_at="2026-07-22T10:00:00Z")
    payload["items"][0][field] = value
    payload["plan_hash"] = calculate_plan_hash(payload)

    with pytest.raises(BackfillPlanError, match="quality gate"):
        validate_backfill_plan(payload)


def test_apply_revalidates_quality_gate_before_opening_workbook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook = tmp_path / "synthetic.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append(
        (
            "2600001048",
            "5x LEAPTON LEAPTON PANTHER 585W",
            "1x SUNGROW SG5KW-RS",
        )
    )
    wb.save(workbook)
    audited = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: TechnicalProposal(
            status="approved",
            module_text=(
                "5x LEAPTON | PANTHER 585W\n"
                "2x JA | JAM66D45 610W\n"
                "Qtd. total: 7 módulos"
            ),
            inverter_text="1x SUNGROW SG5KW-RS",
            source_hash="d" * 64,
            pdf_status="valid",
        ),
    )
    payload = build_backfill_plan(audited, created_at="2026-07-22T10:00:00Z")
    payload["items"][0]["proposed_module_text"] = (
        "5x LEAPTON LEAPTON PANTHER 585W"
    )
    payload["plan_hash"] = calculate_plan_hash(payload)
    opened = False

    def forbidden_open(*args: object, **kwargs: object) -> None:
        nonlocal opened
        opened = True
        raise AssertionError("workbook must remain unopened")

    monkeypatch.setattr(
        "automacao_gd.application.historical_backfill.apply_service.load_workbook",
        forbidden_open,
    )

    with pytest.raises(BackfillApplyError) as caught:
        apply_backfill_plan(
            workbook,
            payload,
            settings=SimpleNamespace(
                APP_ENV="production",
                DRY_RUN=False,
                APPLY_EXCEL=True,
                BACKUP_EXCEL=True,
            ),
            confirmation=required_apply_confirmation(payload),
        )
    assert caught.value.result is not None
    assert caught.value.result.status is BackfillApplyStatus.ABORTED_PRE_FLIGHT
    assert caught.value.result.error_code == "PLAN_VALIDATION_FAILED"
    assert isinstance(caught.value.__cause__, BackfillPlanError)
    assert "quality gate" in str(caught.value.__cause__)
    assert not opened


def test_synthetic_workbook_and_pdfs_cover_semantic_audit_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocols = [f"25000002{index:02d}" for index in range(10)]
    workbook = tmp_path / "synthetic_audit.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    current_cells = [
        ("", "1x TEST INV-5K"),
        ("", "1x TEST INV-5K"),
        ("", "1x TEST INV-5K"),
        ("", "1x TEST INV-5K"),
        ("5x TEST MODEL 500W", "1x TEST INV-5K"),
        ("5x TEST MODEL 500W", ""),
        (
            "2x TEST MODEL-A 500W",
            "1x TEST INV-5K",
        ),
        (
            "2x ALPHA A-500W",
            "1x TEST INV-5K",
        ),
        (
            "5x TEST MODEL 500W",
            "",
        ),
        ("5x TEST MODEL 500W", "1x TEST INV-5K"),
    ]
    for protocol, (current_module, current_inverter) in zip(
        protocols, current_cells
    ):
        ws.append((protocol, current_module, current_inverter))
    wb.save(workbook)
    before = workbook.read_bytes()

    downloads = tmp_path / "pdfs"
    downloads.mkdir()
    for protocol in protocols[:-1]:
        (downloads / f"synthetic_{protocol}.pdf").write_bytes(
            f"%PDF-synthetic-{protocol}".encode()
        )
    ambiguous = protocols[-1]
    (downloads / f"synthetic_a_{ambiguous}.pdf").write_bytes(b"%PDF-a")
    (downloads / f"synthetic_b_{ambiguous}.pdf").write_bytes(b"%PDF-b")

    standard_inverter = InverterEquipment(
        manufacturer="TEST", model="INV-5K", quantity=1, source="synthetic_table"
    )
    proposals = {
        protocols[0]: GenerationData(
            modules=[ModuleEquipment(manufacturer="LEAPTON", model="LEAPTON PANTHER 585W", quantity=5)],
            module_total_quantity=5,
            inverters=[standard_inverter],
            inverter_total_quantity=1,
        ),
            protocols[1]: GenerationData(
            modules=[ModuleEquipment(manufacturer="TSUN", model="MODULO 610W N-TYPE TSUN BIFACIAL 30MM", quantity=5, source="table_header_contamination")],
            module_total_quantity=5,
            inverters=[standard_inverter],
            inverter_total_quantity=1,
        ),
        protocols[2]: GenerationData(
            modules=[ModuleEquipment(manufacturer="RONMA", model="RM182/144TB 585 W", quantity=5)],
            module_total_quantity=5,
            inverters=[standard_inverter],
            inverter_total_quantity=1,
        ),
        protocols[3]: GenerationData(
            modules=[ModuleEquipment(manufacturer="TEST", model="MODEL 585", quantity=5)],
            module_total_quantity=5,
            inverters=[standard_inverter],
            inverter_total_quantity=1,
        ),
        protocols[4]: GenerationData(
            modules=[ModuleEquipment(manufacturer="TEST", model="MODEL 500W", quantity=5)],
            module_total_quantity=5,
            inverters=[standard_inverter],
            inverter_total_quantity=1,
        ),
        protocols[5]: GenerationData(
            modules=[ModuleEquipment(manufacturer="TEST", model="MODEL 500W", quantity=5)],
            module_total_quantity=5,
            inverters=[InverterEquipment(manufacturer="AISWEI", model="AISWEI ASW5000-S", quantity=1)],
            inverter_total_quantity=1,
        ),
        protocols[6]: GenerationData(
            modules=[
                ModuleEquipment(manufacturer="TEST", model="MODEL-A 500W", quantity=2),
                ModuleEquipment(manufacturer="TEST", model="MODEL-B 510W", quantity=3),
            ],
            module_total_quantity=5,
            inverters=[standard_inverter],
            inverter_total_quantity=1,
        ),
        protocols[7]: GenerationData(
            modules=[
                ModuleEquipment(manufacturer="ALPHA", model="A-500W", quantity=2),
                ModuleEquipment(manufacturer="BETA", model="B-510W", quantity=3),
            ],
            module_total_quantity=5,
            inverters=[standard_inverter],
            inverter_total_quantity=1,
        ),
        protocols[8]: GenerationData(
            modules=[ModuleEquipment(manufacturer="TEST", model="MODEL 500W", quantity=5)],
            module_total_quantity=5,
            microinverters=[
                InverterEquipment(
                    manufacturer="HOYMILES",
                    model="HMS-2000",
                    quantity=2,
                    equipment_type="microinverter",
                )
            ],
            microinverter_total_quantity=2,
        ),
    }
    proposals = {
        protocol: data.model_copy(
            update={
                "module_source": "parallel_table",
                "inverter_source": "parallel_table",
            }
        )
        for protocol, data in proposals.items()
    }

    monkeypatch.setattr(
        "automacao_gd.application.historical_backfill.audit_service.validate_pdf_input",
        lambda _: None,
    )
    monkeypatch.setattr(
        "automacao_gd.application.historical_backfill.audit_service.extract_pdf_text",
        lambda path: next(value for value in protocols if value in path.name),
    )
    monkeypatch.setattr(
        "automacao_gd.application.historical_backfill.audit_service.extract_protocol_from_pdf_text",
        lambda text: text,
    )
    monkeypatch.setattr(
        "automacao_gd.application.historical_backfill.audit_service.extract_generation_data",
        lambda _path, *, text: proposals[text],
    )

    audited = audit_historical_workbook(workbook, downloads_root=downloads)

    assert workbook.read_bytes() == before
    assert audited.items[-1].action is BackfillAction.PENDING_TECHNICAL_REVIEW
    assert audited.items[4].action is BackfillAction.UPDATE_EQUIPMENT
    update_text = "\n".join(
        f"{item.proposed_module_text}\n{item.proposed_inverter_text}"
        for item in audited.items
        if item.action is BackfillAction.UPDATE_EQUIPMENT
    )
    assert "LEAPTON LEAPTON" not in update_text
    assert "TSUN TSUN" not in update_text
    assert " MODULO " not in f" {update_text} "
    assert "585W" in update_text
    assert "SOLPLANET | ASW5000-S" in update_text
    assert "MODEL-A 500W" in update_text and "MODEL-B 510W" in update_text
    assert "APSYSTEMS | DS3D" in update_text
