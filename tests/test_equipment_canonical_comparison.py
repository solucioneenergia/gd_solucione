from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook

from automacao_gd.application.historical_backfill.audit_service import (
    TechnicalProposal,
    audit_historical_workbook,
)
from automacao_gd.application.historical_backfill.plan import build_backfill_plan
from automacao_gd.domain.equipment_cache import (
    load_validated_equipment_cache,
    serialize_equipment_cache,
)
from automacao_gd.domain.equipment_semantics import (
    CanonicalEquipment,
    CanonicalEquipmentCollection,
    EquipmentSource,
    EquipmentSourceType,
    canonical_collection_from_dict,
    canonical_collection_to_dict,
    canonicalize_equipment,
    compare_canonical_collections,
    format_canonical_collection,
    semantic_compare_equipment_cells,
)
from automacao_gd.domain.equipment_validation import (
    EQUIPMENT_RULES_VERSION,
    TECHNICAL_PROCESSING_FORMAT_VERSION,
    validate_canonical_equipment,
)
from automacao_gd.infrastructure.pdf.service import parse_generation_data_from_text


PROVEN_SOURCE = EquipmentSource(
    section="generation", field="equipment", origin_type=EquipmentSourceType.TABLE_CELL
)
UNKNOWN_SOURCE = EquipmentSource(origin_type=EquipmentSourceType.UNKNOWN)


def _item(
    equipment_type: str,
    manufacturer: str,
    model: str,
    quantity: int | None,
    *,
    source: EquipmentSource = PROVEN_SOURCE,
) -> CanonicalEquipment:
    return canonicalize_equipment(
        equipment_type=equipment_type,
        manufacturer=manufacturer,
        model=model,
        quantity=quantity,
        source=source,
    )


def _collection(
    *,
    modules: tuple[CanonicalEquipment, ...] = (),
    inverters: tuple[CanonicalEquipment, ...] = (),
    microinverters: tuple[CanonicalEquipment, ...] = (),
) -> CanonicalEquipmentCollection:
    return CanonicalEquipmentCollection(
        modules=modules,
        conventional_inverters=inverters,
        microinverters=microinverters,
    )


@pytest.mark.parametrize(
    ("current", "proposed"),
    [
        (
            _collection(
                modules=(
                    _item("module", "LEAPTON", "PANTHER 585W", 10),
                    _item("module", "JA", "JAM66D45 610W", 5),
                )
            ),
            _collection(modules=(_item("module", "LEAPTON", "PANTHER 585W", 10),)),
        ),
        (
            _collection(
                inverters=(
                    _item("inverter", "HUAWEI", "SUN2000-5KTL-L1", 1),
                    _item("inverter", "SOLPLANET", "ASW6000-S-G2", 1),
                )
            ),
            _collection(
                inverters=(_item("inverter", "HUAWEI", "SUN2000-5KTL-L1", 1),)
            ),
        ),
        (
            _collection(
                microinverters=(
                    _item("microinverter", "APSystems", "DS3D", 10),
                )
            ),
            _collection(),
        ),
    ],
)
def test_rejects_loss_of_one_equipment_from_complete_multiset(
    current: CanonicalEquipmentCollection,
    proposed: CanonicalEquipmentCollection,
) -> None:
    comparison = compare_canonical_collections(current, proposed)

    assert comparison.status == "pending_review"
    assert "EQUIPMENT_ITEM_LOST" in comparison.errors
    assert "SEMANTIC_REGRESSION" in comparison.errors


def test_rejects_loss_when_total_quantity_is_unchanged() -> None:
    current = _collection(
        modules=(
            _item("module", "ACME", "MODEL-A 500W", 2),
            _item("module", "ACME", "MODEL-B 500W", 3),
        )
    )
    proposed = _collection(modules=(_item("module", "ACME", "MODEL-A 500W", 5),))

    comparison = compare_canonical_collections(current, proposed)

    assert "EQUIPMENT_ITEM_LOST" in comparison.errors


def test_rejects_loss_of_duplicate_identity_with_distinct_quantities() -> None:
    first = _item("module", "ACME", "MODEL 500W", 2)
    second = replace(first, quantity=3)
    comparison = compare_canonical_collections(
        _collection(modules=(first, second)),
        _collection(modules=(replace(first, quantity=5),)),
    )
    assert "EQUIPMENT_ITEM_LOST" in comparison.errors


def test_rejects_missing_quantity_for_one_matched_item() -> None:
    current = _collection(
        inverters=(
            _item("inverter", "HUAWEI", "SUN2000-5KTL", 1),
            _item("inverter", "SOLPLANET", "ASW6000-S-G2", 1),
        )
    )
    proposed = _collection(
        inverters=(
            _item("inverter", "HUAWEI", "SUN2000-5KTL", None),
            _item("inverter", "SOLPLANET", "ASW6000-S-G2", 1),
        )
    )

    assert "QUANTITY_LOST" in compare_canonical_collections(current, proposed).errors


@pytest.mark.parametrize(("current_quantity", "proposed_quantity"), [(5, 3), (5, 8)])
def test_rejects_unproven_quantity_change(
    current_quantity: int, proposed_quantity: int
) -> None:
    current = _collection(
        modules=(_item("module", "ACME", "MODEL 500W", current_quantity),)
    )
    proposed = _collection(
        modules=(
            _item(
                "module",
                "ACME",
                "MODEL 500W",
                proposed_quantity,
                source=UNKNOWN_SOURCE,
            ),
        )
    )
    assert (
        "QUANTITY_CHANGED_WITHOUT_EVIDENCE"
        in compare_canonical_collections(current, proposed).errors
    )


def test_rejects_quantity_transfer_between_models() -> None:
    current = _collection(
        modules=(
            _item("module", "ACME", "A-500W", 2),
            _item("module", "ACME", "B-500W", 3),
        )
    )
    proposed = _collection(
        modules=(
            _item("module", "ACME", "A-500W", 3, source=UNKNOWN_SOURCE),
            _item("module", "ACME", "B-500W", 2, source=UNKNOWN_SOURCE),
        )
    )
    assert (
        "QUANTITY_CHANGED_WITHOUT_EVIDENCE"
        in compare_canonical_collections(current, proposed).errors
    )


@pytest.mark.parametrize(
    ("current_model", "proposed_model"),
    [
        ("ABC-123-XYZ 585W", "ABC-123 585W"),
        ("SUN2000-5KTL-L1", "SUN2000-5KTL"),
        ("ASW6000-S-G2", "ASW6000-S"),
        ("JKM625N-78HL4-BDV", "JKM625N-78HL4"),
    ],
)
def test_rejects_model_token_loss(current_model: str, proposed_model: str) -> None:
    comparison = compare_canonical_collections(
        _collection(modules=(_item("module", "ACME", current_model, 1),)),
        _collection(
            modules=(
                _item("module", "ACME", proposed_model, 1, source=UNKNOWN_SOURCE),
            )
        ),
    )
    assert "MODEL_TRUNCATED" in comparison.errors
    assert "MODEL_TOKEN_LOST" in comparison.errors


@pytest.mark.parametrize(
    ("current_model", "proposed_model"),
    [
        ("PANEL 585 W", "PANEL 585W"),
        ("ASW 6000-S-G2", "ASW6000-S-G2"),
        ("sun2000-5ktl-l1", "SUN2000-5KTL-L1"),
    ],
)
def test_equivalent_model_spellings_are_not_truncation(
    current_model: str, proposed_model: str
) -> None:
    comparison = compare_canonical_collections(
        _collection(inverters=(_item("inverter", "ACME", current_model, 1),)),
        _collection(inverters=(_item("inverter", "ACME", proposed_model, 1),)),
    )
    assert "MODEL_TRUNCATED" not in comparison.errors
    assert comparison.status == "approved"


def test_preserves_internal_manufacturer_token() -> None:
    item = _item("module", "ACME", "X ACME PRO", 1)
    assert item.canonical_model == "X ACME PRO"


def test_semantic_comparison_does_not_flag_internal_manufacturer_as_duplicate() -> None:
    current = _collection(modules=(_item("module", "ACME", "X ACME PRO", 1),))
    proposed = _collection(modules=(_item("module", "ACME", "X ACME PRO", 1),))
    comparison = compare_canonical_collections(current, proposed)
    assert comparison.status == "approved"
    assert "DUPLICATED_MANUFACTURER_IN_MODEL" not in comparison.errors
    textual = semantic_compare_equipment_cells(
        "1x ACME X ACME PRO", "", "1x ACME X ACME PRO", ""
    )
    assert "DUPLICATED_MANUFACTURER_IN_MODEL" not in textual.errors


def test_preserves_partial_token_match() -> None:
    item = _item("module", "JA", "JAM66D45 610W", 1)
    assert item.canonical_model == "JAM66D45 610W"


def test_removes_repeated_prefix_aliases_and_stops_at_first_non_alias() -> None:
    item = _item("module", "LEAPTON", "LEAPTON LEAPTON PANTHER LEAPTON 585W", 1)
    assert item.canonical_model == "PANTHER LEAPTON 585W"


def test_removes_label_when_source_proves_header_contamination() -> None:
    source = EquipmentSource(
        section="generation",
        field="module_model",
        origin_type=EquipmentSourceType.TABLE_HEADER_CONTAMINATION,
    )
    item = _item("module", "ACME", "MODULO 610W", 1, source=source)
    assert item.canonical_model == "610W"
    assert "GENERIC_LABEL_AMBIGUOUS" not in item.violations


def test_removes_label_when_source_proves_section_contamination() -> None:
    source = EquipmentSource(
        section="generation",
        field="module_model",
        origin_type=EquipmentSourceType.SECTION_LABEL,
    )
    item = _item("module", "ACME", "MODULO 610W", 1, source=source)
    assert item.canonical_model == "610W"


def test_preserves_label_from_catalog_value() -> None:
    source = EquipmentSource(origin_type=EquipmentSourceType.CATALOG_VALUE)
    item = _item("module", "ACME", "MODULO PRO", 1, source=source)
    assert item.canonical_model == "MODULO PRO"
    assert not item.violations


@pytest.mark.parametrize(
    ("equipment_type", "manufacturer", "model", "expected_model", "attributes"),
    [
        (
            "module",
            "TSUN",
            "MODULO 610W N-TYPE TSUN BIFACIAL 30MM",
            "610W N-TYPE BIFACIAL 30MM",
            {"N-TYPE", "BIFACIAL"},
        ),
        (
            "module",
            "TSUN",
            "MODULO 615W N-TYPE TSUN BIFACIAL 30MM",
            "615W N-TYPE BIFACIAL 30MM",
            {"N-TYPE", "BIFACIAL"},
        ),
        (
            "inverter",
            "SAJ",
            "INVERSOR SAJ AFCI MONO 10K-R6 220V 3MPPT",
            "AFCI MONO 10K-R6 220V 3MPPT",
            {"AFCI", "MONO"},
        ),
    ],
)
def test_proven_label_contamination_removes_label_and_duplicate_manufacturer(
    equipment_type: str,
    manufacturer: str,
    model: str,
    expected_model: str,
    attributes: set[str],
) -> None:
    source = EquipmentSource(
        section="generation",
        field=f"{equipment_type}_model",
        origin_type=EquipmentSourceType.FIELD_LABEL_CONTAMINATION,
    )

    item = _item(equipment_type, manufacturer, model, 1, source=source)

    assert item.canonical_model == expected_model
    assert attributes.issubset(item.technical_attributes)
    assert "GENERIC_LABEL_IN_MODEL" not in item.violations
    assert "DUPLICATED_MANUFACTURER_IN_MODEL" not in item.violations


@pytest.mark.parametrize(
    ("equipment_type", "manufacturer", "model"),
    [
        ("module", "TSUN", "MODULO 610W N-TYPE TSUN BIFACIAL 30MM"),
        ("module", "TSUN", "MODULO 615W N-TYPE TSUN BIFACIAL 30MM"),
        ("inverter", "SAJ", "INVERSOR SAJ AFCI MONO 10K-R6 220V 3MPPT"),
    ],
)
def test_unproven_label_origin_is_blocking(
    equipment_type: str, manufacturer: str, model: str
) -> None:
    item = _item(
        equipment_type,
        manufacturer,
        model,
        1,
        source=EquipmentSource(EquipmentSourceType.LINEAR_FIELD_VALUE),
    )

    assert "GENERIC_LABEL_IN_MODEL" in item.violations
    assert "GENERIC_LABEL_ORIGIN_UNPROVEN" in item.violations
    assert "DUPLICATED_MANUFACTURER_IN_MODEL" in item.violations


def test_internal_manufacturer_remains_when_there_is_no_proven_label() -> None:
    item = _item(
        "module",
        "ACME",
        "X ACME PRO",
        1,
        source=EquipmentSource(EquipmentSourceType.LINEAR_FIELD_VALUE),
    )

    assert item.canonical_model == "X ACME PRO"
    assert "DUPLICATED_MANUFACTURER_IN_MODEL" not in item.violations


def test_marks_unknown_label_origin_as_pending() -> None:
    item = _item("inverter", "ACME", "INVERSOR MAX", 1, source=UNKNOWN_SOURCE)
    assert item.canonical_model == "INVERSOR MAX"
    assert "GENERIC_LABEL_AMBIGUOUS" in item.violations


def test_v5_cache_roundtrip_is_lossless_and_preserves_source() -> None:
    source = EquipmentSource(
        section="generation",
        field="module_model",
        origin_type=EquipmentSourceType.TABLE_CELL,
    )
    module = replace(
        _item("module", "Leapton", "PANTHER 585W N-TYPE", 10, source=source),
        violations=("SYNTHETIC_VIOLATION",),
        warnings=("SYNTHETIC_WARNING",),
    )
    collection = _collection(modules=(module,))
    payload = canonical_collection_to_dict(collection)

    assert canonical_collection_from_dict(payload) == collection
    assert payload["modules"][0]["power_value"] == "585"
    assert payload["modules"][0]["power_unit"] == "W"
    assert payload["modules"][0]["source"]["origin_type"] == "table_cell"


def test_v6_cache_is_reextracted() -> None:
    assert TECHNICAL_PROCESSING_FORMAT_VERSION == 7
    assert EQUIPMENT_RULES_VERSION == "equipment-v2-technical-v7-rules-7"
    assert (
        load_validated_equipment_cache(
            {
                "status": "validated",
                "format_version": 6,
                "equipment_rules_version": "equipment-v2-technical-v6-rules-5",
                "equipment": {},
            }
        )
        is None
    )


def test_incomplete_v7_cache_is_rejected() -> None:
    assert (
        load_validated_equipment_cache(
            {
                "status": "validated",
                "format_version": 7,
                "equipment_rules_version": EQUIPMENT_RULES_VERSION,
                "equipment": {"modules": [{}]},
            }
        )
        is None
    )


def test_v7_cache_rejects_unknown_source_even_when_text_is_complete() -> None:
    collection = _collection(
        modules=(
            _item("module", "ACME", "MODEL 500W", 5, source=UNKNOWN_SOURCE),
        ),
        inverters=(
            _item("inverter", "ACME", "INV-5K", 1, source=PROVEN_SOURCE),
        ),
    )
    module_text, inverter_text = format_canonical_collection(collection)
    technical = {
        "status": "validated",
        "format_version": 7,
        "equipment_rules_version": EQUIPMENT_RULES_VERSION,
        "module_excel": module_text,
        "inverter_excel": inverter_text,
        "placa_planilha": module_text,
        "inversor_planilha": inverter_text,
        "equipment": canonical_collection_to_dict(collection),
    }
    assert load_validated_equipment_cache(technical) is None


def test_v7_cache_rejects_aggregate_quantity_for_multiple_unquantified_items() -> None:
    collection = CanonicalEquipmentCollection(
        modules=(
            _item("module", "ACME", "MODEL-A 500W", None),
            _item("module", "ACME", "MODEL-B 510W", None),
        ),
        conventional_inverters=(
            _item("inverter", "ACME", "INV-5K", 1),
        ),
        module_total_quantity=5,
        inverter_total_quantity=1,
    )
    module_text, inverter_text = format_canonical_collection(collection)
    technical = {
        "status": "validated",
        "format_version": 7,
        "equipment_rules_version": EQUIPMENT_RULES_VERSION,
        "module_excel": module_text,
        "inverter_excel": inverter_text,
        "placa_planilha": module_text,
        "inversor_planilha": inverter_text,
        "equipment": canonical_collection_to_dict(collection),
    }
    assert load_validated_equipment_cache(technical) is None


def test_rejects_unpaired_current_identity_even_without_quantity() -> None:
    current = _collection(
        modules=(_item("module", "ACME", "MODEL-A 500W", None),)
    )
    proposed = _collection(
        modules=(_item("module", "ACME", "MODEL-B 500W", 5),)
    )
    comparison = compare_canonical_collections(current, proposed)
    assert "EQUIPMENT_ITEM_LOST" in comparison.errors


def test_rejects_current_item_with_unsafe_canonical_structure() -> None:
    current = _collection(
        modules=(
            _item(
                "module",
                "ACME",
                "MODULO MODEL-A 500W",
                2,
                source=EquipmentSource(EquipmentSourceType.FORMATTED_CELL),
            ),
        )
    )
    proposed = _collection(
        modules=(_item("module", "ACME", "MODEL-A 500W", 2),)
    )

    comparison = compare_canonical_collections(current, proposed)

    assert comparison.status == "pending_review"
    assert "CURRENT_CANONICAL_STRUCTURE_INCOMPLETE" in comparison.errors
    assert "EQUIPMENT_ITEM_LOST" in comparison.errors


def test_v7_cache_rejects_canonical_model_inconsistent_with_raw_model() -> None:
    collection = _collection(
        modules=(_item("module", "ACME", "MODEL-A-PLUS 500W", 2),),
        inverters=(_item("inverter", "ACME", "INV-5K", 1),),
    )
    module_text, inverter_text = format_canonical_collection(collection)
    equipment = canonical_collection_to_dict(collection)
    equipment["modules"][0]["canonical_model"] = "MODEL-A 500W"
    equipment["modules"][0]["power_value"] = "500"
    technical = {
        "status": "validated",
        "format_version": 7,
        "equipment_rules_version": EQUIPMENT_RULES_VERSION,
        "module_excel": module_text,
        "inverter_excel": inverter_text,
        "placa_planilha": "2x ACME MODEL-A 500W",
        "inversor_planilha": inverter_text,
        "equipment": equipment,
    }

    assert load_validated_equipment_cache(technical) is None


def test_serialize_equipment_cache_persists_full_canonical_collection() -> None:
    collection = _collection(
        modules=(_item("module", "ACME", "MODEL 585W N-TYPE", 3),),
        microinverters=(
            _item("microinverter", "APSystems", "DS3D", 3),
        ),
    )
    assert serialize_equipment_cache(collection) == canonical_collection_to_dict(collection)


def test_source_survives_parser_to_plan_and_complete_flow_is_lossless(
    tmp_path: Path,
) -> None:
    parsed = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) modulos(s): LEAPTON
        Modelo(s) do(s) modulos(s): PANTHER 585W N-TYPE
        Qtd modulos: 5
        Fabricante(s) do(s) inversor(es): GROWATT
        Modelo(s) do(s) inversor(es): MIC 3000TL-X
        Qtd inversores: 1
        """
    )
    assert parsed.canonical_equipment is not None
    collection = parsed.to_canonical_collection()
    module_text, inverter_text = format_canonical_collection(collection)
    validation = validate_canonical_equipment(collection, module_text, inverter_text)
    assert validation.approved

    technical = {
        "status": "validated",
        "format_version": 7,
        "equipment_rules_version": EQUIPMENT_RULES_VERSION,
        "module_excel": parsed.format_module_for_excel(),
        "inverter_excel": parsed.format_inverter_for_excel(),
        "placa_planilha": module_text,
        "inversor_planilha": inverter_text,
        "equipment": serialize_equipment_cache(collection),
    }
    cached = load_validated_equipment_cache(technical)
    assert cached is not None
    assert cached.collection == collection
    assert compare_canonical_collections(collection, cached.collection).equivalent

    module = cached.collection.modules[0]
    assert module.canonical_manufacturer == "LEAPTON"
    assert module.raw_manufacturer == "LEAPTON"
    assert module.canonical_model == "PANTHER 585W N-TYPE"
    assert module.raw_model == "PANTHER 585W N-TYPE"
    assert module.quantity == 5
    assert module.power_value == "585"
    assert module.power_unit == "W"
    assert "N-TYPE" in module.technical_attributes
    assert module.source.origin_type is EquipmentSourceType.LINEAR_FIELD_VALUE
    assert module.violations == ()
    assert module.warnings == ()
    assert module.equipment_type == "module"

    workbook = tmp_path / "synthetic.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(("Protocolo", "Placa", "Inversor"))
    ws.append(("2600001061", "ANTIGA", "ANTIGO"))
    wb.save(workbook)
    audited = audit_historical_workbook(
        workbook,
        technical_resolver=lambda _: TechnicalProposal(
            status="approved",
            module_text=module_text,
            inverter_text=inverter_text,
            source_hash="a" * 64,
            pdf_status="valid",
            canonical_collection=cached.collection,
        ),
    )
    plan = build_backfill_plan(audited, created_at="2026-07-22T10:00:00Z")
    persisted = canonical_collection_from_dict(
        plan["items"][0]["proposed_canonical_collection"]
    )
    assert persisted == collection
    assert format_canonical_collection(persisted) == (module_text, inverter_text)
