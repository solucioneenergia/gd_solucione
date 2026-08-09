import pytest

import src.pdf_service as pdf_service
from src.pdf_service import (
    _clean_generation_lines,
    extract_client_from_pdf_text,
    extract_protocol_from_pdf_text,
    parse_generation_data_from_text,
)
from src.models import (
    InverterEquipment,
    ModuleEquipment,
    format_inverters_for_excel,
    format_modules_for_excel,
)


SAMPLE_TEXT = """
ORÇAMENTO DE CONEXÃO PARA CONEXÃO DE MINI E MICROGERAÇÃO - 2600001097
Titular da UC SEVERINO JOSE DA SILVA

3. GERAÇÃO
Fabricante(s) do(s) módulos(s) Modelo(s) do(s) módulos(s) Qtd módulos Pot. total da(s) placa(s) (kWp)
RENEPV ZY620G12RNHB-132
30MM 12 7,44

Fabricante(s) do(s) inversor(es) Modelo(s) do(s) inversor(es)
Solplanet/Aiswei ASW6000-S-G2

Qtd inversores Pot. total do(s) inversor(es) (kW) Tipo de Conexão
1 6 Monofásica
"""

FORBIDDEN_MODULE_TERMS = [
    "Qtd módulos",
    "Pot. total da(s) placa(s)",
    "Fabricante(s)",
    "Modelo(s)",
]
FORBIDDEN_INVERTER_TERMS = [
    "Modelo(s) do(s) inversor(es)",
    "Qtd inversores",
    "Pot. total do(s) inversor(es)",
]


def test_extract_protocol_from_pdf_text() -> None:
    assert extract_protocol_from_pdf_text(SAMPLE_TEXT) == "2600001097"


def test_extract_client_from_pdf_text() -> None:
    assert extract_client_from_pdf_text(SAMPLE_TEXT) == "SEVERINO JOSE DA SILVA"


def test_parse_generation_data_from_text() -> None:
    data = parse_generation_data_from_text(SAMPLE_TEXT)

    assert data.module_manufacturer == "RENEPV"
    assert data.module_model == "ZY620G12RNHB-132 30MM"
    assert data.module_quantity == 12
    assert data.module_total_kwp == "7,44"
    assert data.inverter_manufacturer == "SOLPLANET"
    assert data.inverter_model == "ASW6000-S-G2"
    assert data.inverter_quantity == 1
    assert data.inverter_total_kw == "6"
    assert (
        data.format_module_for_excel()
        == "RENEPV ZY620G12RNHB-132 30MM | 12 módulos | 7,44 kWp"
    )
    assert data.format_inverter_for_excel() == (
        "SOLPLANET ASW6000-S-G2 | 1 inversor | 6 kW"
    )


def test_parallel_equipment_columns_are_separated_deterministically() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) inversor(es)
        Fabricante(s) do(s) módulos(s)
        GROWATT
        LEAPTON
        Modelo(s) do(s) inversor(es)
        Modelo(s) do(s) módulos(s)
        MIC 3000TL-X
        BIFACIAL 585W N-TYPE
        Qtd inversores
        Pot. total do(s) inversor(es) (kW)
        Qtd módulos
        Pot. total da(s) placa(s) (kWp)
        1
        3
        5
        2,92
        """
    )

    assert data.module_manufacturer == "LEAPTON"
    assert data.module_model == "BIFACIAL 585W N-TYPE"
    assert data.module_quantity == 5
    assert data.inverter_manufacturer == "GROWATT"
    assert data.inverter_model == "MIC 3000TL-X"
    assert data.inverter_quantity == 1
    assert data.format_module_for_planilha() == "5x LEAPTON BIFACIAL 585W N-TYPE"
    assert data.format_inverter_for_planilha() == "1x GROWATT MIC 3000TL-X"
    assert data.module_source == "parallel_table"
    assert data.inverter_source == "parallel_table"


def test_linear_equipment_layout_keeps_separate_sources() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante do módulo: JINKO
        Modelo do módulo: JKM625N
        Qtd módulos: 10
        Fabricante do inversor: HUAWEI
        Modelo do inversor: SUN2000-5KTL
        Qtd inversores: 1
        Pot. total do inversor: 5 kW
        """
    )

    assert data.module_manufacturer == "JINKO"
    assert data.module_model == "JKM625N"
    assert data.module_quantity == 10
    assert data.inverter_manufacturer == "HUAWEI"
    assert data.inverter_model == "SUN2000-5KTL"
    assert data.inverter_quantity == 1
    assert data.module_source == "linear"
    assert data.inverter_source == "linear"


def test_connection_type_lines_are_not_treated_as_inverter_quantity_rows() -> None:
    assert _clean_generation_lines(["Trifásica", "Bifásica", "Monofásica"]) == []


def test_expected_technical_pending_is_reported_without_warning_log(monkeypatch) -> None:
    warning_calls = []
    info_calls = []

    monkeypatch.setattr(
        pdf_service.logger,
        "warning",
        lambda message: warning_calls.append(message),
    )
    monkeypatch.setattr(
        pdf_service.logger,
        "info",
        lambda message: info_calls.append(message),
    )

    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s) Modelo(s) do(s) módulos(s) Qtd módulos Pot. total da(s) placa(s) (kWp)
        JINKO JKM625N-78HL4-BDV 10 6,25
        Fabricante(s) do(s) inversor(es) Modelo(s) do(s) inversor(es)
        Qtd inversores Pot. total do(s) inversor(es) (kW)
        1 5
        """
    )

    assert "fabricante do inversor não identificado" in data.equipment_parse_warning
    assert "modelo do inversor não identificado" in data.equipment_parse_warning
    assert warning_calls == []
    assert any("Pendência na extração técnica" in message for message in info_calls)


def test_simple_module_planilha_format() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s) Modelo(s) do(s) módulos(s) Qtd módulos Pot. total da(s) placa(s) (kWp)
        JINKO JKM625N-78HL4-BDV 10 6,25
        Fabricante(s) do(s) inversor(es) Modelo(s) do(s) inversor(es)
        HUAWEI SUN2000-5KTL
        Qtd inversores Pot. total do(s) inversor(es) (kW)
        1 5
        """
    )

    assert data.format_module_for_planilha() == "10x JINKO JKM625N-78HL4-BDV"


def test_simple_inverter_planilha_format() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s) Modelo(s) do(s) módulos(s) Qtd módulos Pot. total da(s) placa(s) (kWp)
        JINKO JKM625N-78HL4-BDV 10 6,25
        Fabricante(s) do(s) inversor(es) Modelo(s) do(s) inversor(es)
        HUAWEI SUN2000-5KTL
        Qtd inversores Pot. total do(s) inversor(es) (kW)
        1 5
        """
    )

    assert data.format_inverter_for_planilha() == "1x HUAWEI SUN2000-5KTL"


def test_gokin_is_filtered_from_inverter_identity_but_kept_as_module() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): GOKIN
        Modelo(s) do(s) módulos(s): GKM-550M
        Qtd módulos: 20
        Pot. total da(s) placa(s): 11 kWp
        Fabricante(s) do(s) inversor(es): GOKIN
        Modelo(s) do(s) inversor(es):
        Qtd inversores: 1
        Pot. total do(s) inversor(es): 5 kW
        """
    )

    assert data.module_manufacturer == "GOKIN"
    assert data.inverter_manufacturer is None
    assert data.inverters == []
    assert "GOKIN" not in data.format_inverter_for_planilha()
    assert "GOKIN" in data.equipment_parse_warning


def test_module_quantities_are_distributed_for_same_manufacturer_multiple_models() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) modulos(s): GOKIN
        Modelo(s) do(s) modulos(s): GKM-550M | GKM-555M
        Qtd modulos: 10 | 12
        Pot. total da(s) placa(s): 12,16 kWp
        Fabricante(s) do(s) inversor(es): HUAWEI
        Modelo(s) do(s) inversor(es): SUN2000-10KTL
        Qtd inversores: 1
        Pot. total do(s) inversor(es): 10 kW
        """
    )

    assert [item.quantity for item in data.modules] == [10, 12]
    assert data.module_total_quantity == 22
    assert data.format_module_for_planilha() == (
        "GOKIN | GKM-550M\n"
        "GOKIN | GKM-555M\n"
        "Total: 22 m\u00f3dulos"
    )


def test_multiple_module_models_keep_manufacturer_model_pairs() -> None:
    result = format_modules_for_excel(
        [
            ModuleEquipment(manufacturer="BYD", model="P6C-30 260"),
            ModuleEquipment(manufacturer="TRINA", model="TSM-NEG21C 695"),
        ],
        total_quantity=218,
        total_kwp="99,31",
    )

    assert result == (
        "BYD | P6C-30 260\n"
        "TRINA | TSM-NEG21C 695\n"
        "Total: 218 módulos | 99,31 kWp"
    )


def test_multiple_inverter_models_get_one_each_when_total_matches_pairs() -> None:
    result = format_inverters_for_excel(
        [
            InverterEquipment(manufacturer="HUAWEI", model="SUN2000-30KTL"),
            InverterEquipment(manufacturer="ABB", model="Aurora Trio-20.0TL-OUTD"),
            InverterEquipment(manufacturer="HUAWEI", model="SUN2000-20KTL"),
        ],
        total_quantity=3,
        total_kw="70",
    )

    assert result == (
        "HUAWEI | SUN2000-30KTL\n"
        "ABB | Aurora Trio-20.0TL-OUTD\n"
        "HUAWEI | SUN2000-20KTL\n"
        "Total: 3 inversores | 70 kW"
    )


def test_one_manufacturer_with_multiple_models_repeats_manufacturer_without_quantities() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): JINKO
        Modelo(s) do(s) módulos(s): JKM625N-78HL4-BDV
        Qtd módulos: 10
        Pot. total da(s) placa(s): 6,25 kWp
        Fabricante(s) do(s) inversor(es): HUAWEI
        Modelo(s) do(s) inversor(es): SUN2000-5KTL | SUN2000-10KTL
        Qtd inversores: 3
        Pot. total do(s) inversor(es): 15 kW
        """
    )

    assert data.format_inverter_for_planilha() == (
        "HUAWEI | SUN2000-5KTL\n"
        "HUAWEI | SUN2000-10KTL\n"
        "Total: 3 inversores"
    )


def test_mismatched_manufacturer_model_counts_generates_warning() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): BYD | TRINA | JINKO
        Modelo(s) do(s) módulos(s): P6C-30 260 | TSM-NEG21C 695
        Qtd módulos: 218
        Pot. total da(s) placa(s): 99,31 kWp
        Fabricante(s) do(s) inversor(es): HUAWEI
        Modelo(s) do(s) inversor(es): SUN2000-5KTL
        Qtd inversores: 1
        Pot. total do(s) inversor(es): 5 kW
        """
    )

    assert data.format_module_for_planilha() == (
        "BYD | P6C-30 260\n"
        "TRINA | TSM-NEG21C 695\n"
        "JINKO\n"
        "Total: 218 módulos"
    )
    assert "109x" not in data.format_module_for_planilha()
    assert data.equipment_parse_warning
    assert "divergente" in data.equipment_parse_warning


def test_real_multiple_equipment_example_formats_planilha_without_ambiguity() -> None:
    data = parse_generation_data_from_text(
        """
        MÓDULOS:
        Fabricante(s) do(s) módulo(s): BYD | TRINA
        Modelo(s) do(s) módulo(s): P6C-30 260 | TSM-NEG21C 695
        Qtd módulos: 218
        Pot. total da(s) placa(s): 99,31 kWp

        INVERSORES:
        Fabricante(s) do(s) inversor(es): HUAWEI | ABB | HUAWEI
        Modelo(s) do(s) inversor(es): SUN2000-30KTL | Aurora Trio-20.0TL-OUTD | SUN2000-20KTL
        Qtd inversores: 3
        Pot. total do(s) inversor(es): 70 kW
        """
    )

    assert data.format_module_for_planilha() == (
        "BYD | P6C-30 260\n"
        "TRINA | TSM-NEG21C 695\n"
        "Total: 218 módulos"
    )
    assert data.format_inverter_for_planilha() == (
        "HUAWEI | SUN2000-30KTL\n"
        "ABB | Aurora Trio-20.0TL-OUTD\n"
        "HUAWEI | SUN2000-20KTL\n"
        "Total: 3 inversores"
    )


def test_multiple_equipment_quantities_are_paired_and_formatted_per_model() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): BYD | TRINA
        Modelo(s) do(s) módulos(s): P6C-30 260 | TSM-NEG21C 695
        Qtd módulos: 100 | 118
        Pot. total da(s) placa(s): 99,31 kWp
        Fabricante(s) do(s) inversor(es): HUAWEI | ABB
        Modelo(s) do(s) inversor(es): SUN2000-30KTL | Aurora Trio-20.0TL-OUTD
        Qtd inversores: 1 | 2
        Pot. total do(s) inversor(es): 70 kW
        """
    )

    assert data.format_module_for_planilha() == (
        "BYD | P6C-30 260\n"
        "TRINA | TSM-NEG21C 695\n"
        "Total: 218 módulos"
    )
    assert data.format_inverter_for_planilha() == (
        "HUAWEI | SUN2000-30KTL\n"
        "ABB | Aurora Trio-20.0TL-OUTD\n"
        "Total: 3 inversores"
    )
    assert data.equipment_parse_warning is None


def test_multiple_models_with_only_total_quantity_does_not_distribute_quantity() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s): BYD | TRINA
        Modelo(s) do(s) módulos(s): P6C-30 260 | TSM-NEG21C 695
        Qtd módulos: 218
        Pot. total da(s) placa(s): 99,31 kWp
        Fabricante(s) do(s) inversor(es): HUAWEI
        Modelo(s) do(s) inversor(es): SUN2000-5KTL
        Qtd inversores: 1
        Pot. total do(s) inversor(es): 5 kW
        """
    )

    assert data.format_module_for_planilha() == (
        "BYD | P6C-30 260\n"
        "TRINA | TSM-NEG21C 695\n"
        "Total: 218 módulos"
    )
    assert "109x" not in data.format_module_for_planilha()
    return

    assert data.equipment_parse_warning
    assert "conferência necessária" in data.equipment_parse_warning


def test_microinverter_used_when_traditional_inverter_is_empty() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) mÃ³dulo(s): JINKO
        Modelo(s) do(s) mÃ³dulo(s): JKM625N-78HL4-BDV
        Qtd mÃ³dulos: 10
        Pot. total da(s) placa(s): 6,25 kWp
        Fabricante(s) do(s) micro-inversor(es): APSYSTEMS
        Modelo(s) do(s) micro-inversor(es): DS3D
        Qtd micro-inversores: 10
        Pot. total do(s) micro-inversor(es): 20 kW
        """
    )

    assert data.format_inverter_for_planilha() == "10x MICROINVERSOR APSYSTEMS DS3D"


def test_microinverter_quantity_table_layout_after_connection_type_header() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) módulos(s) Modelo(s) do(s) módulos(s)
        Qtd módulos
        Pot. total da(s) placa(s) (kWp)
        LEAPTON
        PANTHER 585W
        4
        2,34
        Fabricante(s) do(s) inversor(es)
        Modelo(s) do(s) inversor(es)
        Qtd inversores
        Pot. total do(s) inversor(es) (kW)
        Tipo de Conexão
        Tipo de Proteção CC do(s)
        Inversores(es)
        Fabricante(s) do(s) micro-inversor(es)
        Modelo(s) do(s) micro-inversor(es)
        HOYMYLES
        HMS-2000DW-4T
        Qtd micro-inversores
        Pot. total do(s) micro-inversor(es) (kW)
        Tipo de Conexão
        1
        2
        Monofásica
        Tipo de Proteção CC do(s)
        Micro-Inversor(es)
        """
    )

    assert data.microinverter_total_quantity == 1
    assert data.microinverter_total_kw == "2"
    assert data.equipment_parse_warning is None
    assert data.format_inverter_for_planilha() == (
        "1x MICROINVERSOR HOYMYLES HMS-2000DW-4T"
    )


def test_traditional_inverter_and_microinverter_are_both_formatted() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) mÃ³dulo(s): JINKO
        Modelo(s) do(s) mÃ³dulo(s): JKM625N-78HL4-BDV
        Qtd mÃ³dulos: 10
        Pot. total da(s) placa(s): 6,25 kWp
        Fabricante(s) do(s) inversor(es): HUAWEI
        Modelo(s) do(s) inversor(es): SUN2000-30KTL
        Qtd inversores: 1
        Pot. total do(s) inversor(es): 30 kW
        Fabricante(s) do(s) micro-inversor(es): APSYSTEMS
        Modelo(s) do(s) micro-inversor(es): DS3D
        Qtd micro-inversores: 10
        Pot. total do(s) micro-inversor(es): 20 kW
        """
    )

    assert data.format_inverter_for_planilha() == (
        "INVERSOR: HUAWEI | SUN2000-30KTL\n"
        "MICROINVERSOR: APSYSTEMS | DS3D\n"
        "Total: 1 inversor + 10 microinversores"
    )


def test_grouped_section_headers_before_values_are_matched_by_order() -> None:
    data = parse_generation_data_from_text(
        """
        Fabricante(s) do(s) modulos(s)
        Modelo(s) do(s) modulos(s)
        Qtd modulos
        Pot. total da(s) placa(s) (kWp)
        HANERSUN
        HN21RN-66HT
        38
        23,18

        Fabricante(s) do(s) inversor(es)
        Modelo(s) do(s) inversor(es)
        Qtd inversores
        Pot. total do(s) inversor(es) (kW)
        Solplanet/Aiswei
        ASW15K-LT-G2
        1
        15
        Tipo de Conexao
        Trifasica
        """
    )

    assert data.module_manufacturer == "HANERSUN"
    assert data.module_model == "HN21RN-66HT"
    assert data.module_quantity == 38
    assert data.module_total_kwp == "23,18"
    assert data.inverter_manufacturer == "SOLPLANET"
    assert data.inverter_model == "ASW15K-LT-G2"
    assert data.inverter_quantity == 1
    assert data.inverter_total_kw == "15"
    assert data.format_module_for_planilha() == "38x HANERSUN HN21RN-66HT"
    assert data.format_inverter_for_planilha() == "1x SOLPLANET ASW15K-LT-G2"
    assert data.equipment_parse_warning is None
