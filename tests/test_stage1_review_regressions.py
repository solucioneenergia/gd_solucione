from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import automacao_gd.application.processing_service as processing_service
import automacao_gd.domain.equipment_validation as equipment_validation
import automacao_gd.infrastructure.pdf.service as pdf_service
from automacao_gd.domain.equipment import (
    format_inverters_for_excel_v2,
    format_modules_for_excel_v2,
)
from automacao_gd.domain.equipment_cache import serialize_equipment_cache
from automacao_gd.domain.models import GenerationData, InverterEquipment, ModuleEquipment


EXPECTED_EQUIPMENT_RULES_VERSION = "equipment-v2-technical-v7-rules-7"


def _parallel_text(
    *,
    inverter_manufacturers: str = "GROWATT",
    module_manufacturers: str = "LEAPTON",
    inverter_models: str = "MIC 3000TL-X",
    module_models: str = "BIFACIAL 585W N-TYPE",
    inverter_quantity: str = "1",
    module_quantity: str = "5",
) -> str:
    return "\n".join(
        [
            "Fabricante(s) do(s) inversor(es)",
            "Fabricante(s) do(s) módulos(s)",
            inverter_manufacturers,
            module_manufacturers,
            "Modelo(s) do(s) inversor(es)",
            "Modelo(s) do(s) módulos(s)",
            *inverter_models.split("\n"),
            *module_models.split("\n"),
            "Qtd inversores",
            "Pot. total do(s) inversor(es) (kW)",
            "Qtd módulos",
            "Pot. total da(s) placa(s) (kWp)",
            inverter_quantity,
            "3",
            module_quantity,
            "2,92",
            "Tipo de Proteção CC",
            "CONTEUDO DA PROXIMA SECAO",
        ]
    )


def _linear_text(
    *,
    inverter_manufacturers: str = "HUAWEI",
    inverter_models: str = "SUN2000-5KTL",
    inverter_quantity: str | None = "1",
    micro_manufacturers: str | None = None,
    micro_models: str | None = None,
    micro_quantity: str | None = None,
) -> str:
    lines = [
        "Fabricante(s) do(s) módulos(s): LEAPTON",
        "Modelo(s) do(s) módulos(s): LP182-585W",
        "Qtd módulos: 5",
        "Pot. total da(s) placa(s): 2,92 kWp",
        f"Fabricante(s) do(s) inversor(es): {inverter_manufacturers}",
        f"Modelo(s) do(s) inversor(es): {inverter_models}",
    ]
    if inverter_quantity is not None:
        lines.extend(
            [
                f"Qtd inversores: {inverter_quantity}",
                "Pot. total do(s) inversor(es): 5 kW",
            ]
        )
    if micro_manufacturers is not None:
        lines.extend(
            [
                f"Fabricante(s) do(s) microinversor(es): {micro_manufacturers}",
                f"Modelo(s) do(s) microinversor(es): {micro_models}",
            ]
        )
        if micro_quantity is not None:
            lines.extend(
                [
                    f"Qtd microinversores: {micro_quantity}",
                    "Pot. total do(s) microinversor(es): 4 kW",
                ]
            )
    return "\n".join(lines)


def _validation(data: GenerationData):
    module_text = format_modules_for_excel_v2(data).text
    inverter_text = format_inverters_for_excel_v2(data).text
    return equipment_validation.validate_technical_equipment(
        data, module_text, inverter_text
    )


def _positioned_parallel_elements():
    element = pdf_service.PositionedText
    return [
        element("Fabricante(s) do(s) inversor(es)", 10, 10, 145, 25),
        element("Fabricante(s) do(s) módulos(s)", 205, 9, 355, 27),
        element("GROWATT", 13, 38, 74, 49),
        element("LEAP", 211, 37, 252, 48),
        element("TON", 214, 50, 244, 61),
        element("Modelo(s) do(s) inversor(es)", 8, 80, 128, 96),
        element("Modelo(s) do(s) módulos(s)", 198, 78, 370, 98),
        element("MIC 3000TL", 14, 112, 92, 123),
        element("-X", 17, 125, 31, 136),
        element("BIFACIAL 585W", 209, 111, 301, 123),
        element("N-TYPE", 213, 126, 264, 138),
        element("Qtd inversores", 5, 165, 80, 178),
        element("Pot. inversores", 101, 164, 181, 179),
        element("Qtd módulos", 204, 166, 274, 179),
        element("Pot. placas", 303, 163, 375, 180),
        element("1", 18, 197, 23, 208),
        element("3", 113, 198, 118, 209),
        element("5", 218, 196, 223, 207),
        element("2,92", 316, 199, 340, 210),
        element("Tipo de Proteção CC", 8, 230, 145, 244),
        element("NAO INCORPORAR", 12, 250, 100, 262),
    ]


def test_parallel_table_preserves_multiline_module_model() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _parallel_text(module_models="BIFACIAL 585W\nN-TYPE")
    )

    assert data.module_model == "BIFACIAL 585W N-TYPE"
    assert format_modules_for_excel_v2(data).text == (
        "5x LEAPTON BIFACIAL 585W N-TYPE"
    )


def test_parallel_table_preserves_multiline_inverter_model_positionally() -> None:
    data = pdf_service.parse_generation_data_from_positioned_elements(
        _positioned_parallel_elements(), fallback_text=_parallel_text()
    )

    assert data.inverter_model == "MIC 3000TL-X"
    assert data.module_model == "BIFACIAL 585W N-TYPE"
    assert data.module_manufacturer == "LEAPTON"
    assert data.module_source == "parallel_positioned"
    assert "NAO INCORPORAR" not in data.inverter_model


def test_parallel_table_rejects_ambiguous_cardinality() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _parallel_text(
            inverter_models="INV-A\nINV-B",
            module_models="MOD-A\nMOD-B",
        )
    )

    assert "AMBIGUOUS_PARALLEL_CARDINALITY" in {
        violation.code for violation in data.equipment_violations
    }
    assert _validation(data).status == "pending_review"


def test_linear_fallback_rejects_ambiguous_inverter_continuation() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _parallel_text(
            inverter_models="MIC 3000TL\n-X",
            module_models="BIFACIAL 585W N-TYPE",
        )
    )

    assert "AMBIGUOUS_PARALLEL_CARDINALITY" in {
        violation.code for violation in data.equipment_violations
    }
    assert _validation(data).status == "pending_review"


def test_positioned_parallel_table_keeps_quantities_out_of_models() -> None:
    data = pdf_service.parse_generation_data_from_positioned_elements(
        _positioned_parallel_elements(), fallback_text=_parallel_text()
    )

    assert data.inverter_model == "MIC 3000TL-X"
    assert data.module_model == "BIFACIAL 585W N-TYPE"
    assert data.inverter_quantity == 1
    assert data.module_quantity == 5
    assert data.inverter_total_kw == "3"
    assert data.module_total_kwp == "2,92"


def test_parallel_table_supports_multiple_models_without_dropping_values() -> None:
    elements = _positioned_parallel_elements()
    elements = [
        item
        if item.text != "MIC 3000TL"
        else item.__class__("ASW5000-S | ASW6000-S", item.x0, item.y0, item.x1, item.y1)
        for item in elements
        if item.text != "-X"
    ]
    data = pdf_service.parse_generation_data_from_positioned_elements(
        elements, fallback_text=_parallel_text()
    )

    assert "ASW5000-S" in (data.inverter_model or "")
    assert "ASW6000-S" in (data.inverter_model or "")


def test_gokin_with_valid_inverter_is_blocking() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _linear_text(
            inverter_manufacturers="HUAWEI | GOKIN",
            inverter_models="SUN2000-5KTL",
        )
    )
    validation = _validation(data)

    assert validation.status == "pending_review"
    assert "MODULE_BRAND_IN_INVERTER_SECTION" in validation.errors
    assert any(
        violation.blocking and violation.manufacturer == "GOKIN"
        for violation in data.equipment_violations
    )


def test_parallel_cell_with_huawei_and_gokin_is_blocking() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _parallel_text(inverter_manufacturers="HUAWEI | GOKIN")
    )

    validation = _validation(data)

    assert validation.status == "pending_review"
    assert "MODULE_BRAND_IN_INVERTER_SECTION" in validation.errors


def test_gokin_in_microinverter_section_is_blocking() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _linear_text(
            micro_manufacturers="GOKIN",
            micro_models="AMBIGUO",
            micro_quantity="1",
        )
    )

    assert _validation(data).status == "pending_review"
    assert any(
        violation.source_section == "microinverter"
        for violation in data.equipment_violations
    )


def test_gokin_as_module_and_huawei_as_inverter_is_valid() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _linear_text().replace("LEAPTON", "GOKIN")
    )

    assert _validation(data).approved is True
    assert format_modules_for_excel_v2(data).text.startswith("5x GOKIN")


def test_gokin_outside_equipment_sections_is_not_a_false_positive() -> None:
    data = pdf_service.parse_generation_data_from_text(
        _linear_text() + "\nObservação posterior: GOKIN"
    )

    assert _validation(data).approved is True
    assert data.equipment_violations == []


@pytest.mark.parametrize(
    ("conventional_quantity", "micro_quantity", "expected_error"),
    [
        (None, 10, "INVERTER_QUANTITY_MISSING"),
        (1, None, "MICROINVERTER_QUANTITY_MISSING"),
        (0, 10, "INVERTER_QUANTITY_MISSING"),
        (1, -1, "MICROINVERTER_QUANTITY_MISSING"),
        ("não numérica", 10, "INVERTER_QUANTITY_MISSING"),
    ],
)
def test_conventional_and_microinverter_require_independent_quantities(
    conventional_quantity,
    micro_quantity,
    expected_error: str,
) -> None:
    data = GenerationData(
        modules=[ModuleEquipment(manufacturer="LEAPTON", model="LP182", quantity=5)],
        module_total_quantity=5,
        inverters=[InverterEquipment(manufacturer="HUAWEI", model="SUN2000")],
        inverter_total_quantity=(
            conventional_quantity
            if isinstance(conventional_quantity, int)
            else None
        ),
        microinverters=[
            InverterEquipment(
                manufacturer="APSYSTEMS",
                model="DS3D",
                equipment_type="microinverter",
            )
        ],
        microinverter_total_quantity=micro_quantity,
    )
    if not isinstance(conventional_quantity, int):
        data.inverter_total_quantity = conventional_quantity

    validation = _validation(data)

    assert validation.status == "pending_review"
    assert expected_error in validation.errors


def test_conventional_and_microinverter_with_own_quantities_are_valid() -> None:
    data = GenerationData(
        modules=[ModuleEquipment(manufacturer="LEAPTON", model="LP182", quantity=5)],
        module_total_quantity=5,
        inverters=[InverterEquipment(manufacturer="HUAWEI", model="SUN2000", quantity=1)],
        inverter_total_quantity=1,
        microinverters=[
            InverterEquipment(
                manufacturer="APSYSTEMS",
                model="DS3D",
                quantity=10,
                equipment_type="microinverter",
            )
        ],
        microinverter_total_quantity=10,
        module_source="parallel_table",
        inverter_source="parallel_table",
    )

    assert _validation(data).approved is True


def _valid_structured_cache(**overrides) -> dict:
    data = GenerationData(
        modules=[
            ModuleEquipment(
                manufacturer="LEAPTON", model="LP182", quantity=5, source="linear"
            )
        ],
        module_total_quantity=5,
        inverters=[
            InverterEquipment(
                manufacturer="HUAWEI", model="SUN2000", quantity=1, source="linear"
            )
        ],
        inverter_total_quantity=1,
        module_source="linear",
        inverter_source="linear",
    )
    payload = {
        "status": "validated",
        "format_version": 7,
        "equipment_rules_version": EXPECTED_EQUIPMENT_RULES_VERSION,
        "technical_validation_status": "approved",
        "technical_review_required": False,
        "technical_validation_errors": [],
        "technical_validation_warnings": [],
        "module_excel": "LEAPTON LP182 | 5 módulos",
        "inverter_excel": "HUAWEI SUN2000 | 1 inversor",
        "placa_planilha": "5x LEAPTON LP182",
        "inversor_planilha": "1x HUAWEI SUN2000",
        "module_source": "linear",
        "inverter_source": "linear",
        "equipment": serialize_equipment_cache(data),
    }
    payload.update(overrides)
    return payload


class _CacheState:
    def __init__(self, technical: dict) -> None:
        self.technical = technical
        self.updated: list[dict] = []

    def get_protocol(self, protocol: str) -> dict:
        return {
            "client_name": "CLIENTE SINTETICO",
            "technical_processing": self.technical,
            "unrelated": {"preserve": True},
        }

    def is_force_reprocess(self, protocol: str) -> bool:
        return False

    def update_section(self, protocol, section, payload, last_step=None):
        self.updated.append(payload)


@pytest.mark.parametrize(
    "invalid_cache",
    [
        _valid_structured_cache(equipment_rules_version=None),
        _valid_structured_cache(equipment_rules_version="rules-old"),
        _valid_structured_cache(status="pending_review"),
        _valid_structured_cache(inversor_planilha="1x GOKIN AMBIGUO"),
        _valid_structured_cache(
            equipment={
                "modules": [
                    {
                        "equipment_type": "module",
                        "manufacturer": "LEAPTON",
                        "model": "LP182",
                        "quantity": 5,
                        "source": "linear",
                    }
                ],
                "inverters": [
                    {
                        "equipment_type": "inverter",
                        "manufacturer": "HUAWEI",
                        "model": "SUN2000",
                        "quantity": None,
                        "source": "linear",
                    }
                ],
                "microinverters": [],
                "violations": [],
                "warnings": [],
            }
        ),
    ],
)
def test_invalid_v4_cache_is_reextracted(
    invalid_cache: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction = Mock(return_value=_linear_text())
    monkeypatch.setattr(processing_service, "extract_pdf_text", extraction)
    monkeypatch.setattr(
        processing_service, "extract_protocol_from_pdf_text", lambda text: "2600001047"
    )
    monkeypatch.setattr(
        processing_service, "extract_client_from_pdf_text", lambda text: "CLIENTE SINTETICO"
    )
    state = _CacheState(invalid_cache)

    result = processing_service._load_or_extract_technical_data(
        tmp_path / "Orcamento_de_Conexao_2600001047.pdf", state
    )

    extraction.assert_called_once()
    assert result[6].approved is True
    assert state.updated[-1]["status"] == "validated"


def test_v6_cache_is_semantically_revalidated_without_pdf_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction = Mock(side_effect=AssertionError("cache semântico válido deve ser reutilizado"))
    monkeypatch.setattr(processing_service, "extract_pdf_text", extraction)
    pdf_path = tmp_path / "Orcamento_de_Conexao_2600001048.pdf"
    pdf_bytes = b"%PDF-1.4\nSYNTHETIC TEST PDF\n%%EOF\n"
    pdf_path.write_bytes(pdf_bytes)
    cache = _valid_structured_cache(
        source_protocol="2600001048",
        source_pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
    )

    result = processing_service._load_or_extract_technical_data(
        pdf_path,
        _CacheState(cache),
    )

    extraction.assert_not_called()
    assert result[6].approved is True
    assert result[4] == "5x LEAPTON LP182"


class _OrderedState:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.protocol_status: str | None = None
        self.completed = False

    def get_protocol(self, protocol: str):
        return None

    def is_force_reprocess(self, protocol: str) -> bool:
        return False

    def update_section(self, protocol, section, payload, last_step=None):
        if section == "technical_processing":
            self.events.append(payload["status"])

    def update_protocol(self, protocol, status=None, last_step=None, data=None):
        self.protocol_status = status

    def add_error(self, protocol, step, message):
        self.events.append("unexpected_add_error")

    def mark_completed(self, protocol):
        self.completed = True


def _patch_operational_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    events: list[str],
    *,
    excel_success: bool,
) -> Mock:
    monkeypatch.setattr(processing_service, "extract_pdf_text", lambda path: _linear_text())
    monkeypatch.setattr(
        processing_service, "extract_protocol_from_pdf_text", lambda text: "2600001049"
    )
    monkeypatch.setattr(
        processing_service, "extract_client_from_pdf_text", lambda text: "CLIENTE SINTETICO"
    )
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(
            downloads_dir_path=tmp_path,
            logs_dir_path=tmp_path,
        ),
    )
    monkeypatch.setattr(
        processing_service, "load_portal_metadata", lambda *args: ({}, "none")
    )
    monkeypatch.setattr(
        processing_service,
        "find_client_folder",
        lambda *args: SimpleNamespace(
            match_type="protocol",
            matched_path=str(tmp_path / "cliente"),
            confidence=1.0,
            cache_hit=False,
            cache_key=None,
            reason=None,
            found_by="protocol",
            protocol_search_hit=True,
            search_elapsed_seconds=0.0,
        ),
    )
    monkeypatch.setattr(
        processing_service,
        "resolve_archive_destination_folder",
        lambda **kwargs: SimpleNamespace(
            destination_folder=tmp_path / "cliente",
            match_type="protocol",
            reason=None,
            should_create_folder=False,
            fallback_mode=None,
            legacy_gd_ignored=False,
        ),
    )

    def update_excel(**kwargs):
        events.append("excel")
        return {
            "success": excel_success,
            "can_write": excel_success,
            "action": "updated" if excel_success else "failed",
            "error": None if excel_success else "falha sintética do Excel",
        }

    monkeypatch.setattr(processing_service, "update_excel_from_pdf_data", update_excel)
    archive = Mock(
        side_effect=lambda *args, **kwargs: (
            events.append("archive")
            or SimpleNamespace(
                success=True,
                error=None,
                match_type="protocol",
                reason=None,
                created_folder=False,
                fallback_mode=None,
                legacy_gd_ignored=False,
                archived_pdf_path=str(tmp_path / "arquivado.pdf"),
                destination_folder=str(tmp_path / "cliente"),
            )
        )
    )
    monkeypatch.setattr(processing_service, "archive_pdf_to_client_folder", archive)
    return archive


def test_success_is_persisted_only_after_excel_and_before_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    archive = _patch_operational_dependencies(
        monkeypatch, tmp_path, events, excel_success=True
    )
    state = _OrderedState(events)

    result = processing_service._process_single_pdf(
        tmp_path / "Orcamento_de_Conexao_2600001049.pdf",
        tmp_path / "planilha.xlsx",
        tmp_path / "clientes",
        dry_run=False,
        state_store=state,
    )

    assert result["success"] is True
    assert events.index("validated") < events.index("excel")
    assert events.index("excel") < events.index("success")
    assert events.index("success") < events.index("archive")
    archive.assert_called_once()
    assert state.completed is True


def test_excel_failure_does_not_persist_success_or_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    archive = _patch_operational_dependencies(
        monkeypatch, tmp_path, events, excel_success=False
    )
    state = _OrderedState(events)

    result = processing_service._process_single_pdf(
        tmp_path / "Orcamento_de_Conexao_2600001049.pdf",
        tmp_path / "planilha.xlsx",
        tmp_path / "clientes",
        dry_run=False,
        state_store=state,
    )

    assert result["success"] is False
    assert "success" not in events
    archive.assert_not_called()
    assert state.protocol_status == "operational_pending"
    assert state.completed is False


@pytest.mark.parametrize(
    ("protocol", "data", "expected_error"),
    [
        (
            "2600001064",
            GenerationData(
                modules=[ModuleEquipment(manufacturer="MODULO", model="M1", quantity=5)],
                module_total_quantity=5,
            ),
            "INVERTER_IDENTITY_MISSING",
        ),
        (
            "2600001070",
            GenerationData(
                modules=[ModuleEquipment(manufacturer="MODULO", model=None, quantity=5)],
                module_total_quantity=5,
                inverters=[
                    InverterEquipment(manufacturer="INVERSOR", model="I1", quantity=1)
                ],
                inverter_total_quantity=1,
            ),
            "MODULE_PAIRING_UNSAFE",
        ),
        (
            "2600001073",
            GenerationData(
                modules=[ModuleEquipment(manufacturer="MODULO", model="M1", quantity=5)],
                module_total_quantity=5,
                inverters=[
                    InverterEquipment(manufacturer="INVERSOR", model="I1")
                ],
            ),
            "INVERTER_QUANTITY_MISSING",
        ),
    ],
)
def test_anonymized_pending_protocol_fixtures(
    protocol: str,
    data: GenerationData,
    expected_error: str,
) -> None:
    validation = _validation(data)

    assert protocol.isdigit()
    assert validation.status == "pending_review"
    assert expected_error in validation.errors


def test_public_report_removes_all_paths() -> None:
    internal = {
        "status": "PARCIAL",
        "dry_run": False,
        "started_at": "2026-07-21T10:00:00",
        "finished_at": "2026-07-21T10:01:00",
        "downloads_root": r"C:\empresa\downloads",
        "workbook_path": r"Z:\empresa\planilha.xlsx",
        "clientes_root": r"\\SERVIDOR\SINTETICO\Pessoa Exemplo",
        "pending_pdf_paths": ["/CAMINHO/SINTETICO"],
        "results": [
            {
                "protocol": "2600001050",
                "action": "pending_technical_review",
                "pdf_path": r"C:\empresa\documento.pdf",
                "error": r"falhou em Z:\empresa\arquivo.xlsx",
                "technical_validation_status": "pending_review",
                "technical_review_required": True,
                "technical_validation_errors": ["INVERTER_QUANTITY_MISSING"],
                "technical_validation_warnings": [],
                "module_source": "linear",
                "inverter_source": "linear",
                "recommended_action": "Reprocesse o protocolo.",
                "excel_status": {
                    "success": False,
                    "error": r"\\SERVIDOR\SINTETICO\planilha.xlsx",
                },
                "archive_status": {
                    "success": False,
                    "nested": {"path": "/CAMINHO/SINTETICO"},
                },
            }
        ],
    }

    public = processing_service._privacy_safe_report_payload(internal)
    serialized = repr(public)

    assert public["results"][0]["protocol"] == "2600001050"
    for forbidden in ("C:\\", "Z:\\", "\\\\servidor\\", "/home/", ".pdf", ".xlsx"):
        assert forbidden not in serialized
    assert "downloads_root" not in public
    assert "pending_pdf_paths" not in public


def test_unit_suite_does_not_access_operational_pdfs() -> None:
    source = Path(__file__).with_name("test_pdf_service.py").read_text(encoding="utf-8")

    assert "data\" / \"downloads" not in source
    assert "REAL_PDF_EXPECTATIONS" not in source
    assert "test_real_downloaded_pdf_extraction" not in source
