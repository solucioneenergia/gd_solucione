from __future__ import annotations

from automacao_gd.domain.equipment_semantics import (
    CANONICAL_RULES_VERSION,
    TECHNICAL_FORMAT_VERSION,
    CanonicalEquipmentCollection,
    EquipmentSource,
    EquipmentSourceType,
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


def _item(
    equipment_type: str,
    manufacturer: str,
    model: str,
    quantity: int | None,
    *,
    source: EquipmentSourceType = EquipmentSourceType.LINEAR_FIELD_VALUE,
):
    return canonicalize_equipment(
        equipment_type=equipment_type,
        manufacturer=manufacturer,
        model=model,
        quantity=quantity,
        source=EquipmentSource(
            origin_type=source,
            section="generation",
            field=f"{equipment_type}_equipment",
        ),
    )


def _collection(*, modules=(), inverters=(), microinverters=()):
    return CanonicalEquipmentCollection(
        modules=tuple(modules),
        conventional_inverters=tuple(inverters),
        microinverters=tuple(microinverters),
    )


def test_group_a_corporate_manufacturer_equivalence_is_approved() -> None:
    result = semantic_compare_equipment_cells(
        "120x GOKIN GK-1-72HTBD 585W",
        "1x Ginlong (Solis) Solis-25K-5G",
        "120x GOKIN GK-1-72HTBD 585W",
        "1x GINLONG (SOLIS) Solis-25K-5G",
    )

    assert result.status == "approved"
    assert result.equivalent
    assert "EQUIPMENT_ITEM_LOST" not in result.errors


def test_group_a_punctuation_spacing_and_manufacturer_normalization_are_equivalent() -> None:
    result = semantic_compare_equipment_cells(
        "21x LEAPTON LP182-199-M-66-NH",
        "1x GROWATT New Energy Co., Ltd. MIN 10000TL-X",
        "21x LEAPTON LP182-199-M-66-NH",
        "1x GROWATT NEW ENERGY CO., LTD. MIN 10000TL-X",
    )

    assert result.status == "approved"
    assert result.equivalent


def test_group_a_does_not_hide_model_quantity_power_or_equipment_loss() -> None:
    lost_model = compare_canonical_collections(
        _collection(modules=(_item("module", "JA", "JAM66D45 610W", 25),)),
        _collection(modules=(_item("module", "JA", "JAM66D45", 25),)),
    )
    lost_quantity = compare_canonical_collections(
        _collection(modules=(_item("module", "JA", "JAM66D45 610W", 25),)),
        _collection(modules=(_item("module", "JA", "JAM66D45 610W", None),)),
    )
    lost_equipment = compare_canonical_collections(
        _collection(
            modules=(
                _item("module", "JA", "JAM66D45 610W", 25),
                _item("module", "LEAPTON", "LP182-M-72-MH 585W", 10),
            )
        ),
        _collection(modules=(_item("module", "JA", "JAM66D45 610W", 25),)),
    )

    assert "MODEL_TOKEN_LOST" in lost_model.errors
    assert "QUANTITY_LOST" in lost_quantity.errors
    assert "EQUIPMENT_ITEM_LOST" in lost_equipment.errors


def test_group_b_module_unit_w_is_recovered_only_when_total_power_proves_it() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): RONMA
        Modelo(s) do(s) módulos(s): RM182/144TB 585
        Qtd módulos: 18
        Pot. total da(s) placa(s) (kWp): 10,53
        Fabricante(s) do(s) inversor(es): HUAWEI
        Modelo(s) do(s) inversor(es): SUN2000-7,5K
        Qtd inversores: 1
        Pot. total do(s) inversor(es) (kW): 7,5
        """
    )
    collection = data.to_canonical_collection()

    assert collection.modules[0].canonical_model == "RM182/144TB 585W"
    assert collection.modules[0].power_value == "585"
    assert collection.modules[0].power_unit == "W"
    assert format_canonical_collection(collection)[0] == "18x RONMA RM182/144TB 585W"


def test_group_b_preserves_w_wp_kw_and_kwp_without_cross_assignment() -> None:
    assert _item("module", "TEST", "MODEL 585 W", 1).canonical_model == "MODEL 585W"
    assert _item("module", "TEST", "MODEL 585 Wp", 1).power_unit == "Wp"
    assert _item("inverter", "TEST", "INV 7,5 kW", 1).power_unit == "kW"
    assert _item("module", "TEST", "MODEL 585", 1).power_unit is None


def test_group_c_proven_label_origin_removes_label_and_duplicate_manufacturer() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): TSUN
        Modelo(s) do(s) módulos(s): MODULO 615W N-TYPE TSUN BIFACIAL 30MM
        Qtd módulos: 25
        Pot. total da(s) placa(s) (kWp): 15,37
        Fabricante(s) do(s) inversor(es): SAJ
        Modelo(s) do(s) inversor(es): INVERSOR SAJ AFCI MONO 10K-R6 220V 3MPPT
        Qtd inversores: 1
        Pot. total do(s) inversor(es) (kW): 10
        """
    )
    collection = data.to_canonical_collection()
    module_text, inverter_text = format_canonical_collection(collection)
    validation = validate_canonical_equipment(collection, module_text, inverter_text)

    assert module_text == "25x TSUN 615W N-TYPE BIFACIAL 30MM"
    assert inverter_text == "1x SAJ AFCI MONO 10K-R6 220V 3MPPT"
    assert validation.approved


def test_group_c_unknown_origin_does_not_remove_internal_manufacturer() -> None:
    item = canonicalize_equipment(
        equipment_type="module",
        manufacturer="ACME",
        model="X ACME PRO",
        quantity=1,
        source=EquipmentSourceType.LINEAR_FIELD_VALUE,
    )

    assert item.canonical_model == "X ACME PRO"
    assert "DUPLICATED_MANUFACTURER_IN_MODEL" not in item.violations


def test_group_c_proven_duplicate_manufacturer_removal_is_not_truncation() -> None:
    current = _collection(
        modules=(
            _item(
                "module",
                "TSUN",
                "610W N-TYPE TSUN BIFACIAL 30MM",
                38,
                source=EquipmentSourceType.FORMATTED_CELL,
            ),
        )
    )
    proposed = _collection(
        modules=(
            _item(
                "module",
                "TSUN",
                "610W N-TYPE BIFACIAL 30MM",
                38,
                source=EquipmentSourceType.FIELD_LABEL_CONTAMINATION,
            ),
        )
    )

    result = compare_canonical_collections(current, proposed)

    assert result.status == "approved"
    assert "MODEL_TOKEN_LOST" not in result.errors


def test_group_d_missing_inverter_quantity_remains_pending_without_inference() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): HANERSUN
        Modelo(s) do(s) módulos(s): HN21RN-66HT
        Qtd módulos: 38
        Pot. total da(s) placa(s) (kWp): 23,18
        Fabricante(s) do(s) inversor(es): Solplanet/Aiswei
        Modelo(s) do(s) inversor(es): ASW15K-LT-G2
        Qtd inversores
        Pot. total do(s) inversor(es) (kW)
        Tipo de Conexão
        Trifásica
        """
    )
    collection = data.to_canonical_collection()
    module_text, inverter_text = format_canonical_collection(collection)
    validation = validate_canonical_equipment(collection, module_text, inverter_text)

    assert collection.conventional_inverters[0].quantity is None
    assert "INVERTER_QUANTITY_MISSING" in validation.errors


def test_multiple_equipment_multiset_remains_protected_from_generic_deduplication() -> None:
    current = _collection(
        modules=(
            _item("module", "ACME", "MODEL-A 500W", 12),
            _item("module", "ACME", "MODEL-B 510W", 8),
        ),
        inverters=(
            _item("inverter", "HUAWEI", "SUN2000-10K", 1),
            _item("inverter", "HUAWEI", "SUN2000-20K", 1),
        ),
    )
    proposed = _collection(
        modules=(_item("module", "ACME", "MODEL-A 500W", 20),),
        inverters=(_item("inverter", "HUAWEI", "SUN2000-10K", 2),),
    )

    result = compare_canonical_collections(current, proposed)

    assert "EQUIPMENT_ITEM_LOST" in result.errors
    assert result.status == "pending_review"


def test_technical_processing_v7_rules7_are_active() -> None:
    assert TECHNICAL_FORMAT_VERSION == 7
    assert TECHNICAL_PROCESSING_FORMAT_VERSION == 7
    assert CANONICAL_RULES_VERSION == "equipment-v2-technical-v7-rules-7"
    assert EQUIPMENT_RULES_VERSION == "equipment-v2-technical-v7-rules-7"
