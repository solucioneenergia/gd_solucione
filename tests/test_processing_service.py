from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import automacao_gd.application.processing_service as processing_service
from automacao_gd.domain.equipment_cache import serialize_equipment_cache
from automacao_gd.domain.models import GenerationData, InverterEquipment, ModuleEquipment
from src.processing_service import (
    _count_archived,
    _critical_simulation_issues,
    _resolve_pdf_paths,
    _skipped_excel_status,
)


def test_resolve_pdf_paths_filters_explicit_list(tmp_path: Path) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_123.pdf"
    relative_pdf = Path("456/Orcamento_de_Conexao_456.pdf")

    result = _resolve_pdf_paths(tmp_path, [pdf, pdf, relative_pdf])

    assert result == [pdf, tmp_path / relative_pdf]


def test_resolve_pdf_paths_scans_downloads_root(tmp_path: Path) -> None:
    wanted = tmp_path / "123" / "Orcamento_de_Conexao_123.pdf"
    ignored = tmp_path / "123" / "outro.pdf"
    wanted.parent.mkdir()
    wanted.touch()
    ignored.touch()

    assert _resolve_pdf_paths(tmp_path, None) == [wanted]


def test_critical_simulation_issues_ignores_can_write_when_excel_disabled() -> None:
    results = [
        {"error": None, "excel_status": {"can_write": False}},
        {"error": "falha", "excel_status": {"can_write": True}},
    ]

    critical = _critical_simulation_issues(results, apply_excel=False)

    assert critical == [results[1]]


def test_critical_simulation_issues_ignores_individual_technical_pending() -> None:
    results = [
        {
            "error": "Extração técnica inconclusiva",
            "technical_review_required": True,
            "technical_validation_status": "pending_review",
            "action": "pending_technical_review",
            "excel_status": {"can_write": False},
        },
        {"error": "falha sistêmica", "excel_status": {"can_write": True}},
    ]

    critical = _critical_simulation_issues(results, apply_excel=True)

    assert critical == [results[1]]


def test_skipped_excel_status_is_success_without_write_permission() -> None:
    status = _skipped_excel_status("123", dry_run=True)

    assert status["success"] is True
    assert status["skipped"] is True
    assert status["can_write"] is False
    assert status["action"] == "skipped_apply_excel_false"


def test_count_archived_ignores_dry_run_and_skipped_status() -> None:
    results = [
        {"archive_status": {"success": True, "skipped": False}},
        {"archive_status": {"success": True, "skipped": True}},
        {"archive_status": {"success": False, "skipped": False}},
    ]

    assert _count_archived(results, dry_run=True, apply_archive=True) == 0
    assert _count_archived(results, dry_run=False, apply_archive=False) == 0
    assert _count_archived(results, dry_run=False, apply_archive=True) == 1


def test_processing_uses_per_model_equipment_quantities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = """
    Fabricante(s) do(s) módulos(s): BYD | TRINA
    Modelo(s) do(s) módulos(s): P6C-30 260 | TSM-NEG21C 695
    Qtd módulos: 100 | 118
    Pot. total da(s) placa(s): 99,31 kWp
    Fabricante(s) do(s) inversor(es): HUAWEI | ABB
    Modelo(s) do(s) inversor(es): SUN2000-30KTL | Aurora Trio-20.0TL-OUTD
    Qtd inversores: 1 | 2
    Pot. total do(s) inversor(es): 70 kW
    """
    pdf_path = tmp_path / "Orcamento_de_Conexao_2606184625.pdf"
    monkeypatch.setattr(processing_service, "extract_pdf_text", lambda path: text)
    monkeypatch.setattr(
        processing_service,
        "extract_protocol_from_pdf_text",
        lambda value: "2606184625",
    )
    monkeypatch.setattr(
        processing_service,
        "extract_client_from_pdf_text",
        lambda value: "CLIENTE TESTE",
    )

    result = processing_service._load_or_extract_technical_data(pdf_path)

    assert result[4] == (
        "100x BYD | P6C-30 260\n"
        "118x TRINA | TSM-NEG21C 695\n"
        "Qtd. total: 218 módulos"
    )
    assert result[5] == (
        "HUAWEI | SUN2000-30KTL\n"
        "ABB | Aurora Trio-20.0TL-OUTD\n"
        "Qtd. total: 3 inversores"
    )


def test_processing_ignores_stale_technical_cache_without_format_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = """
    Fabricante(s) do(s) modulos(s): BYD | TRINA
    Modelo(s) do(s) modulos(s): P6C-30 260 | TSM-NEG21C 695
    Qtd modulos: 218
    Pot. total da(s) placa(s): 99,31 kWp
    Fabricante(s) do(s) inversor(es): HUAWEI | ABB | HUAWEI
    Modelo(s) do(s) inversor(es): SUN2000-30KTL | Aurora Trio-20.0TL-OUTD | SUN2000-20KTL
    Qtd inversores: 3
    Pot. total do(s) inversor(es): 70 kW
    """
    pdf_path = tmp_path / "Orcamento_de_Conexao_2604275348.pdf"
    monkeypatch.setattr(processing_service, "extract_pdf_text", lambda path: text)
    monkeypatch.setattr(
        processing_service,
        "extract_protocol_from_pdf_text",
        lambda value: "2604275348",
    )
    monkeypatch.setattr(
        processing_service,
        "extract_client_from_pdf_text",
        lambda value: "CLIENTE TESTE",
    )

    class StateStore:
        updated_payload = None

        def get_protocol(self, protocol: str) -> dict:
            return {
                "client_name": "CLIENTE CACHE",
                "technical_processing": {
                    "status": "success",
                    "module_excel": "OLD MODULE",
                    "inverter_excel": "OLD INVERTER",
                    "placa_planilha": "218x BYD TRINA P6C-30 260 TSM-NEG21C 695W",
                    "inversor_planilha": "3x HUAWEI ABB HUAWEI SUN2000-30KTL",
                },
            }

        def is_force_reprocess(self, protocol: str) -> bool:
            return False

        def update_section(
            self,
            protocol: str,
            section: str,
            payload: dict,
            last_step: str | None = None,
        ) -> None:
            self.updated_payload = payload

    state_store = StateStore()

    result = processing_service._load_or_extract_technical_data(pdf_path, state_store)

    assert result[4] == (
        "BYD | P6C-30 260\n"
        "TRINA | TSM-NEG21C 695\n"
        "Qtd. total: 218 m\u00f3dulos"
    )
    assert result[5] == (
        "HUAWEI | SUN2000-30KTL\n"
        "ABB | Aurora Trio-20.0TL-OUTD\n"
        "HUAWEI | SUN2000-20KTL\n"
        "Qtd. total: 3 inversores"
    )
    assert state_store.updated_payload is not None
    assert state_store.updated_payload["format_version"] is None
    assert state_store.updated_payload["technical_review_required"] is True
    assert "CANONICAL_STRUCTURE_INCOMPLETE" in state_store.updated_payload[
        "technical_validation_errors"
    ]


def test_processing_ignores_previous_technical_cache_version_for_equipment_fix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = """
    Fabricante(s) do(s) modulos(s): RONMA
    Modelo(s) do(s) modulos(s): RM182/144TB 585W
    Qtd modulos: 14
    Pot. total da(s) placa(s): 8,19 kWp
    Fabricante(s) do(s) inversor(es): Solplanet/Aiswei
    Modelo(s) do(s) inversor(es): ASW6000-S-G2
    Qtd inversores: 1
    Pot. total do(s) inversor(es): 6 kW
    """
    pdf_path = tmp_path / "Orcamento_de_Conexao_2606021741.pdf"
    monkeypatch.setattr(processing_service, "extract_pdf_text", lambda path: text)
    monkeypatch.setattr(
        processing_service,
        "extract_protocol_from_pdf_text",
        lambda value: "2606021741",
    )
    monkeypatch.setattr(
        processing_service,
        "extract_client_from_pdf_text",
        lambda value: "CLIENTE TESTE",
    )

    class StateStore:
        updated_payload = None

        def get_protocol(self, protocol: str) -> dict:
            return {
                "client_name": "CLIENTE CACHE",
                "technical_processing": {
                    "status": "success",
                    "format_version": 3,
                    "placa_planilha": "14x RONMA RM182/144TB 585W",
                    "inversor_planilha": (
                        "SOLPLANET | ASW6000-S-G2\n"
                        "AISWEI | ASW6000-S-G2\n"
                        "Qtd. total: 1 inversor"
                    ),
                },
            }

        def is_force_reprocess(self, protocol: str) -> bool:
            return False

        def update_section(
            self,
            protocol: str,
            section: str,
            payload: dict,
            last_step: str | None = None,
        ) -> None:
            self.updated_payload = payload

    state_store = StateStore()

    result = processing_service._load_or_extract_technical_data(pdf_path, state_store)

    assert result[5] == "1x SOLPLANET ASW6000-S-G2"
    assert state_store.updated_payload is not None
    assert (
        state_store.updated_payload["format_version"]
        == processing_service.TECHNICAL_PROCESSING_FORMAT_VERSION
    )
    assert state_store.updated_payload["technical_validation_status"] == "approved"


def _valid_generation_data(*, with_inverter: bool = True) -> GenerationData:
    return GenerationData(
        module_manufacturer="LEAPTON",
        module_model="BIFACIAL 585W N-TYPE",
        module_quantity=5,
        module_total_quantity=5,
        modules=[
            ModuleEquipment(
                manufacturer="LEAPTON",
                model="BIFACIAL 585W N-TYPE",
                quantity=5,
            )
        ],
        inverter_manufacturer="GROWATT" if with_inverter else None,
        inverter_model="MIC 3000TL-X" if with_inverter else None,
        inverter_quantity=1 if with_inverter else None,
        inverter_total_quantity=1 if with_inverter else None,
        inverters=(
            [
                InverterEquipment(
                    manufacturer="GROWATT",
                    model="MIC 3000TL-X",
                    quantity=1,
                )
            ]
            if with_inverter
            else []
        ),
        module_source="parallel_table",
        inverter_source="parallel_table",
    )


@pytest.mark.parametrize(
    ("mutation", "placa", "inversor", "expected_code"),
    [
        (lambda data: data, "", "1x GROWATT MIC 3000TL-X", "MODULE_TEXT_EMPTY"),
        (lambda data: data, "5x LEAPTON BIFACIAL 585W N-TYPE", "", "INVERTER_TEXT_EMPTY"),
        (
            lambda data: data.model_copy(
                update={"inverter_model": "MIC 3000TL-X BIFACIAL 585W N-TYPE"}
            ),
            "5x LEAPTON BIFACIAL 585W N-TYPE",
            "1x GROWATT MIC 3000TL-X BIFACIAL 585W N-TYPE",
            "INVERTER_MODULE_CONTAMINATION",
        ),
        (
            lambda data: data.model_copy(
                update={
                    "inverter_manufacturer": "GOKIN",
                    "inverters": [
                        InverterEquipment(
                            manufacturer="GOKIN", model="AMBIGUO", quantity=1
                        )
                    ],
                }
            ),
            "5x LEAPTON BIFACIAL 585W N-TYPE",
            "1x GOKIN AMBIGUO",
            "GOKIN_INVALID_INVERTER",
        ),
        (
            lambda data: data.model_copy(
                update={
                    "module_quantity": None,
                    "module_total_quantity": None,
                    "modules": [
                        ModuleEquipment(
                            manufacturer="LEAPTON", model="BIFACIAL 585W N-TYPE"
                        )
                    ],
                }
            ),
            "LEAPTON BIFACIAL 585W N-TYPE",
            "1x GROWATT MIC 3000TL-X",
            "MODULE_QUANTITY_MISSING",
        ),
        (
            lambda data: data.model_copy(
                update={
                    "inverter_manufacturer": None,
                    "inverter_model": None,
                    "inverter_quantity": None,
                    "inverter_total_quantity": None,
                    "inverters": [],
                    "microinverters": [
                        InverterEquipment(
                            manufacturer="GOKIN",
                            model="AMBIGUO",
                            quantity=1,
                            equipment_type="microinverter",
                        )
                    ],
                    "microinverter_total_quantity": 1,
                }
            ),
            "5x LEAPTON BIFACIAL 585W N-TYPE",
            "1x MICROINVERSOR GOKIN AMBIGUO",
            "GOKIN_INVALID_INVERTER",
        ),
    ],
)
def test_technical_validation_rejects_unsafe_equipment_before_excel(
    mutation,
    placa: str,
    inversor: str,
    expected_code: str,
) -> None:
    validation = processing_service.validate_technical_equipment(
        mutation(_valid_generation_data()),
        placa,
        inversor,
    )

    assert validation.approved is False
    assert expected_code in validation.errors
    assert validation.technical_review_required is True


def test_technical_validation_accepts_microinverter_without_conventional_inverter() -> None:
    data = _valid_generation_data(with_inverter=False).model_copy(
        update={
            "microinverters": [
                InverterEquipment(
                    manufacturer="APSYSTEMS",
                    model="DS3D",
                    quantity=10,
                    equipment_type="microinverter",
                )
            ],
            "microinverter_total_quantity": 10,
        }
    )

    validation = processing_service.validate_technical_equipment(
        data,
        "5x LEAPTON BIFACIAL 585W N-TYPE",
        "10x MICROINVERSOR APSYSTEMS DS3D",
    )

    assert validation.approved is True
    assert validation.errors == []


def test_pending_technical_review_skips_excel_archive_and_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation = SimpleNamespace(
        approved=False,
        status="pending_review",
        errors=["INVERTER_MODULE_CONTAMINATION"],
        warnings=[],
        technical_review_required=True,
        module_source="parallel_table",
        inverter_source="parallel_table",
        user_message=(
            "Extração técnica inconclusiva: os dados de Placa e Inversor "
            "não puderam ser separados com segurança."
        ),
        recommended_action="Confira a seção 3. GERAÇÃO do orçamento de conexão.",
    )
    monkeypatch.setattr(
        processing_service,
        "_load_or_extract_technical_data",
        lambda *args: (
            "2500000001",
            "CLIENTE SINTETICO",
            "modulo",
            "inversor contaminado",
            "5x LEAPTON MODELO",
            "1x GROWATT MODELO BIFACIAL",
            validation,
        ),
    )
    excel_mock = Mock()
    archive_mock = Mock()
    exception_log_mock = Mock()
    metadata_mock = Mock(side_effect=AssertionError("metadata não deve ser lido"))
    monkeypatch.setattr(processing_service, "update_excel_from_pdf_data", excel_mock)
    monkeypatch.setattr(processing_service, "archive_pdf_to_client_folder", archive_mock)
    monkeypatch.setattr(processing_service, "load_portal_metadata", metadata_mock)
    monkeypatch.setattr(processing_service.logger, "exception", exception_log_mock)

    class StateStore:
        completed = False
        updates: list[dict] = []

        def update_protocol(self, protocol, status=None, last_step=None):
            self.updates.append(
                {"protocol": protocol, "status": status, "last_step": last_step}
            )

        def mark_completed(self, protocol):
            self.completed = True

    state_store = StateStore()
    result = processing_service._process_single_pdf(
        tmp_path / "Orcamento_de_Conexao_2500000001.pdf",
        tmp_path / "planilha.xlsx",
        tmp_path / "clientes",
        dry_run=False,
        state_store=state_store,
    )

    excel_mock.assert_not_called()
    archive_mock.assert_not_called()
    metadata_mock.assert_not_called()
    exception_log_mock.assert_not_called()
    assert result["error"].startswith("Extração técnica inconclusiva")
    assert result["action"] == "pending_technical_review"
    assert result["technical_validation_status"] == "pending_review"
    assert result["technical_review_required"] is True
    assert state_store.completed is False
    assert state_store.updates[-1]["status"] == "pending_review"


def test_pending_technical_review_does_not_interrupt_remaining_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdfs = [
        tmp_path / "Orcamento_de_Conexao_2500000010.pdf",
        tmp_path / "Orcamento_de_Conexao_2500000011.pdf",
    ]
    calls: list[Path] = []

    def fake_process(pdf_path, *args, **kwargs):
        calls.append(pdf_path)
        pending = pdf_path == pdfs[0]
        return {
            **processing_service._empty_result(pdf_path),
            "success": not pending,
            "protocol": "2500000010" if pending else "2500000011",
            "action": "pending_technical_review" if pending else "updated",
            "technical_review_required": pending,
            "technical_validation_status": "pending_review" if pending else "approved",
            "error": "Extração técnica inconclusiva" if pending else None,
            "excel_status": {
                "success": not pending,
                "can_write": not pending,
                "skipped": pending,
            },
            "archive_status": {
                "success": not pending,
                "skipped": pending,
                "simulated": True,
            },
        }

    monkeypatch.setattr(processing_service, "_process_single_pdf", fake_process)
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=tmp_path / "logs"),
    )
    monkeypatch.setattr(processing_service, "atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing_service, "atomic_write_text", lambda *args, **kwargs: None)

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha.xlsx",
        clientes_root=tmp_path / "clientes",
        dry_run=True,
        pdf_paths=pdfs,
        apply_excel=False,
        apply_archive=False,
    )

    assert calls == pdfs
    assert payload["status"] == "PARCIAL"
    assert payload["total_technical_pending_review"] == 1
    assert payload["pending_pdf_paths"] == [str(pdfs[0])]


def test_real_run_technical_pending_does_not_block_safe_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdfs = [
        tmp_path / "Orcamento_de_Conexao_2500000100.pdf",
        tmp_path / "Orcamento_de_Conexao_2500000101.pdf",
    ]
    calls: list[tuple[Path, bool]] = []

    def fake_process(pdf_path, *_args, dry_run=True, **_kwargs):
        actual_dry_run = _args[2] if len(_args) >= 3 else dry_run
        calls.append((pdf_path, actual_dry_run))
        pending = pdf_path == pdfs[1]
        result = {
            **processing_service._empty_result(pdf_path),
            "success": not pending,
            "protocol": "2500000101" if pending else "2500000100",
            "action": "pending_technical_review" if pending else "update_existing",
            "technical_review_required": pending,
            "technical_validation_status": "pending_review" if pending else "approved",
            "error": "Extração técnica inconclusiva" if pending else None,
            "excel_status": {
                "success": not pending,
                "can_write": not pending,
                "skipped": pending,
                "action": "pending_technical_review" if pending else "update_existing",
            },
            "archive_status": {
                "success": not pending,
                "skipped": pending,
                "simulated": actual_dry_run,
            },
        }
        return result

    monkeypatch.setattr(processing_service, "_process_single_pdf", fake_process)
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "_real_run_preflight_result",
        lambda *args, **kwargs: {"success": True, "error": None, "code": None},
    )
    monkeypatch.setattr(processing_service, "create_workbook_backup", lambda path: tmp_path / "backup.xlsx")
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=tmp_path / "logs", BACKUP_EXCEL=True),
    )
    monkeypatch.setattr(processing_service, "atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing_service, "atomic_write_text", lambda *args, **kwargs: None)

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha.xlsx",
        clientes_root=tmp_path / "clientes",
        dry_run=False,
        pdf_paths=pdfs,
        apply_excel=True,
        apply_archive=True,
    )

    assert payload["blocked_real_run"] is False
    assert payload["status"] == "PARCIAL"
    assert payload["total_safe_protocols"] == 1
    assert payload["total_technical_pending_review"] == 1
    assert payload["total_excel_updated"] == 1
    assert payload["total_archived"] == 1
    assert payload["pending_pdf_paths"] == [str(pdfs[1])]
    assert calls == [(pdfs[0], True), (pdfs[1], True), (pdfs[0], False)]


def test_real_run_only_pending_protocols_blocks_without_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdfs = [
        tmp_path / "Orcamento_de_Conexao_2500000110.pdf",
        tmp_path / "Orcamento_de_Conexao_2500000111.pdf",
    ]

    def fake_process(pdf_path, *_args, dry_run=True, **_kwargs):
        actual_dry_run = _args[2] if len(_args) >= 3 else dry_run
        return {
            **processing_service._empty_result(pdf_path),
            "success": False,
            "protocol": pdf_path.stem.rsplit("_", 1)[-1],
            "action": "pending_technical_review",
            "technical_review_required": True,
            "technical_validation_status": "pending_review",
            "error": "Extração técnica inconclusiva",
            "excel_status": {"success": False, "can_write": False, "skipped": True},
            "archive_status": {"success": False, "skipped": True, "simulated": actual_dry_run},
        }

    monkeypatch.setattr(processing_service, "_process_single_pdf", fake_process)
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "_real_run_preflight_result",
        lambda *args, **kwargs: {"success": True, "error": None, "code": None},
    )
    monkeypatch.setattr(
        processing_service,
        "create_workbook_backup",
        lambda path: pytest.fail("backup não deve ser criado sem protocolos seguros"),
    )
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=tmp_path / "logs", BACKUP_EXCEL=True),
    )
    monkeypatch.setattr(processing_service, "atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing_service, "atomic_write_text", lambda *args, **kwargs: None)

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha.xlsx",
        clientes_root=tmp_path / "clientes",
        dry_run=False,
        pdf_paths=pdfs,
        apply_excel=True,
        apply_archive=True,
    )

    assert payload["status"] == "BLOQUEADO"
    assert payload["real_run_block_code"] == "NO_SAFE_PROTOCOLS_TO_APPLY"
    assert payload["total_safe_protocols"] == 0
    assert payload["total_technical_pending_review"] == 2
    assert payload["total_excel_updated"] == 0


def test_real_run_systemic_preflight_error_blocks_entire_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2500000120.pdf"
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "_real_run_preflight_result",
        lambda *args, **kwargs: {
            "success": False,
            "error": "A planilha está aberta ou bloqueada pelo Excel.",
            "code": "WORKBOOK_LOCKED",
            "stage": "pré-voo",
        },
    )
    monkeypatch.setattr(
        processing_service,
        "_process_single_pdf",
        lambda *args, **kwargs: pytest.fail("PDF não deve ser processado com erro sistêmico"),
    )
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=tmp_path / "logs", BACKUP_EXCEL=True),
    )
    monkeypatch.setattr(processing_service, "atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing_service, "atomic_write_text", lambda *args, **kwargs: None)

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha.xlsx",
        clientes_root=tmp_path / "clientes",
        dry_run=False,
        pdf_paths=[pdf],
        apply_excel=True,
        apply_archive=True,
    )

    assert payload["status"] == "BLOQUEADO"
    assert payload["real_run_block_code"] == "WORKBOOK_LOCKED"
    assert payload["total_excel_updated"] == 0


def test_real_run_systemic_failure_rolls_back_safe_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workbook = tmp_path / "planilha.xlsx"
    backup = tmp_path / "backup.xlsx"
    workbook.write_text("ORIGINAL", encoding="utf-8")
    backup.write_text("ORIGINAL", encoding="utf-8")
    pdfs = [
        tmp_path / "Orcamento_de_Conexao_2500000130.pdf",
        tmp_path / "Orcamento_de_Conexao_2500000131.pdf",
    ]

    def fake_process(pdf_path, *_args, dry_run=True, **_kwargs):
        actual_dry_run = _args[2] if len(_args) >= 3 else dry_run
        protocol = pdf_path.stem.rsplit("_", 1)[-1]
        if actual_dry_run:
            return {
                **processing_service._empty_result(pdf_path),
                "success": True,
                "protocol": protocol,
                "action": "update_existing",
                "technical_review_required": False,
                "technical_validation_status": "approved",
                "excel_status": {"success": True, "can_write": True, "skipped": False},
                "archive_status": {"success": True, "skipped": True, "simulated": True},
            }
        if protocol == "2500000130":
            workbook.write_text("PARTIAL", encoding="utf-8")
            return {
                **processing_service._empty_result(pdf_path),
                "success": True,
                "protocol": protocol,
                "action": "update_existing",
                "technical_review_required": False,
                "technical_validation_status": "approved",
                "excel_status": {"success": True, "can_write": True, "skipped": False},
                "archive_status": {"success": True, "skipped": True, "simulated": False},
            }
        return {
            **processing_service._empty_result(pdf_path),
            "success": False,
            "protocol": protocol,
            "action": "update_existing",
            "technical_review_required": False,
            "technical_validation_status": "approved",
            "error": "A planilha está aberta ou bloqueada pelo Excel.",
            "excel_status": {
                "success": False,
                "can_write": False,
                "skipped": False,
                "error": "A planilha está aberta ou bloqueada pelo Excel.",
            },
            "archive_status": {"success": False, "skipped": True, "simulated": False},
        }

    monkeypatch.setattr(processing_service, "_process_single_pdf", fake_process)
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "_real_run_preflight_result",
        lambda *args, **kwargs: {"success": True, "error": None, "code": None},
    )
    monkeypatch.setattr(processing_service, "create_workbook_backup", lambda path: backup)
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=tmp_path / "logs", BACKUP_EXCEL=True),
    )
    monkeypatch.setattr(processing_service, "atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing_service, "atomic_write_text", lambda *args, **kwargs: None)

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=workbook,
        clientes_root=tmp_path / "clientes",
        dry_run=False,
        pdf_paths=pdfs,
        apply_excel=True,
        apply_archive=True,
    )

    assert workbook.read_text(encoding="utf-8") == "ORIGINAL"
    assert payload["status"] == "FALHOU"
    assert payload["rollback_executed"] is True
    assert payload["total_excel_updated"] == 0


def test_processing_metrics_distinguish_analyzed_safe_pending_and_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = tmp_path / "Orcamento_de_Conexao_2500000140.pdf"
    pending = tmp_path / "Orcamento_de_Conexao_2500000141.pdf"

    def fake_process(pdf_path, *_args, dry_run=True, **_kwargs):
        actual_dry_run = _args[2] if len(_args) >= 3 else dry_run
        is_pending = pdf_path == pending
        return {
            **processing_service._empty_result(pdf_path),
            "success": not is_pending,
            "protocol": pdf_path.stem.rsplit("_", 1)[-1],
            "action": "pending_technical_review" if is_pending else "update_existing",
            "technical_review_required": is_pending,
            "technical_validation_status": "pending_review" if is_pending else "approved",
            "error": "Extração técnica inconclusiva" if is_pending else None,
            "excel_status": {"success": not is_pending, "can_write": not is_pending, "skipped": is_pending},
            "archive_status": {"success": not is_pending, "skipped": is_pending, "simulated": actual_dry_run},
        }

    monkeypatch.setattr(processing_service, "_process_single_pdf", fake_process)
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "_real_run_preflight_result",
        lambda *args, **kwargs: {"success": True, "error": None, "code": None},
    )
    monkeypatch.setattr(processing_service, "create_workbook_backup", lambda path: tmp_path / "backup.xlsx")
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=tmp_path / "logs", BACKUP_EXCEL=True),
    )
    monkeypatch.setattr(processing_service, "atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing_service, "atomic_write_text", lambda *args, **kwargs: None)

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha.xlsx",
        clientes_root=tmp_path / "clientes",
        dry_run=False,
        pdf_paths=[safe, pending],
        apply_excel=True,
        apply_archive=True,
    )

    assert payload["total_pdfs_analyzed"] == 2
    assert payload["total_technically_approved"] == 1
    assert payload["total_safe_protocols"] == 1
    assert payload["total_pending_protocols"] == 1
    assert payload["total_updates_planned"] == 1
    assert payload["total_updates_applied"] == 1


def test_real_run_reuses_pending_simulation_result_without_second_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = tmp_path / "Orcamento_de_Conexao_2500000150.pdf"
    pending = tmp_path / "Orcamento_de_Conexao_2500000151.pdf"
    calls: list[tuple[str, bool]] = []

    def fake_process(pdf_path, *_args, dry_run=True, **_kwargs):
        actual_dry_run = _args[2] if len(_args) >= 3 else dry_run
        protocol = pdf_path.stem.rsplit("_", 1)[-1]
        calls.append((protocol, bool(actual_dry_run)))
        is_pending = pdf_path == pending
        return {
            **processing_service._empty_result(pdf_path),
            "success": not is_pending,
            "protocol": protocol,
            "action": "pending_technical_review" if is_pending else "update_existing",
            "technical_review_required": is_pending,
            "technical_validation_status": "pending_review" if is_pending else "approved",
            "error": "Extração técnica inconclusiva" if is_pending else None,
            "excel_status": {
                "success": not is_pending,
                "can_write": not is_pending,
                "skipped": is_pending,
            },
            "archive_status": {
                "success": not is_pending,
                "skipped": is_pending,
                "simulated": actual_dry_run,
            },
        }

    monkeypatch.setattr(processing_service, "_process_single_pdf", fake_process)
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "_real_run_preflight_result",
        lambda *args, **kwargs: {"success": True, "error": None, "code": None},
    )
    monkeypatch.setattr(
        processing_service,
        "create_workbook_backup",
        lambda path: tmp_path / "backup.xlsx",
    )
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=tmp_path / "logs", BACKUP_EXCEL=True),
    )
    monkeypatch.setattr(processing_service, "atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(processing_service, "atomic_write_text", lambda *args, **kwargs: None)

    payload = processing_service.process_downloaded_pdfs(
        downloads_root=tmp_path,
        workbook_path=tmp_path / "planilha.xlsx",
        clientes_root=tmp_path / "clientes",
        dry_run=False,
        pdf_paths=[safe, pending],
        apply_excel=True,
        apply_archive=True,
    )

    assert calls == [
        ("2500000150", True),
        ("2500000151", True),
        ("2500000150", False),
    ]
    assert payload["total_pdfs_analyzed"] == 2
    assert payload["total_pending_protocols"] == 1
    assert payload["total_updates_applied"] == 1


def test_file_report_redacts_client_identity_and_folder_paths() -> None:
    payload = {
        "results": [
            {
                "protocol": "2500000012",
                "client_name": "PESSOA SINTETICA",
                "matched_path": r"Z:\Clientes\PESSOA SINTETICA",
                "target_folder": r"Z:\Clientes\PESSOA SINTETICA\GD",
                "client_folder_cache_key": "pessoa sintetica",
                "technical_validation_status": "approved",
            }
        ]
    }

    report = processing_service._privacy_safe_report_payload(payload)

    assert "client_name" not in report["results"][0]
    assert "matched_path" not in report["results"][0]
    assert "target_folder" not in report["results"][0]
    assert "client_folder_cache_key" not in report["results"][0]
    assert report["results"][0]["technical_validation_status"] == "approved"


def test_valid_technical_result_reaches_excel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation = SimpleNamespace(
        approved=True,
        status="approved",
        errors=[],
        warnings=[],
        technical_review_required=False,
        module_source="parallel_table",
        inverter_source="parallel_table",
    )
    monkeypatch.setattr(
        processing_service,
        "_load_or_extract_technical_data",
        lambda *args: (
            "2500000002",
            "CLIENTE SINTETICO",
            "modulo",
            "inversor",
            "5x LEAPTON MODELO",
            "1x GROWATT MODELO",
            validation,
        ),
    )
    monkeypatch.setattr(processing_service, "load_portal_metadata", lambda *args: ({}, "none"))
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
    excel_mock = Mock(
        return_value={
            "success": True,
            "can_write": True,
            "action": "updated",
            "row_found": True,
            "row_number": 2,
        }
    )
    monkeypatch.setattr(processing_service, "update_excel_from_pdf_data", excel_mock)

    result = processing_service._process_single_pdf(
        tmp_path / "Orcamento_de_Conexao_2500000002.pdf",
        tmp_path / "planilha.xlsx",
        tmp_path / "clientes",
        dry_run=True,
        apply_archive=False,
    )

    excel_mock.assert_called_once()
    assert result["success"] is True
    assert result["technical_validation_status"] == "approved"


def test_point_of_connection_no_date_metadata_marks_completion_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation = SimpleNamespace(
        approved=True,
        status="approved",
        errors=[],
        warnings=[],
        technical_review_required=False,
        module_source="parallel_table",
        inverter_source="parallel_table",
    )
    monkeypatch.setattr(
        processing_service,
        "_load_or_extract_technical_data",
        lambda *args: (
            "2500000190",
            "CLIENTE SINTETICO",
            "modulo",
            "inversor",
            "5x LEAPTON MODELO",
            "1x GROWATT MODELO",
            validation,
        ),
    )
    monkeypatch.setattr(
        processing_service,
        "load_portal_metadata",
        lambda *args: (
            {
                "entry_date": "2026-07-01",
                "status": "Solicitação Concluída",
                "completion_date": None,
                "completion_date_raw": None,
                "completion_source_stage": "PONTO_DE_CONEXAO_APROVADO",
                "completion_extraction_status": "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE",
            },
            "metadata.json",
        ),
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
    excel_mock = Mock(
        return_value={
            "success": True,
            "can_write": True,
            "action": "update_existing",
            "completion_action": "MARKED_AS_OPEN",
            "row_found": True,
            "row_number": 2,
        }
    )
    monkeypatch.setattr(processing_service, "update_excel_from_pdf_data", excel_mock)

    result = processing_service._process_single_pdf(
        tmp_path / "Orcamento_de_Conexao_2500000190.pdf",
        tmp_path / "planilha.xlsx",
        tmp_path / "clientes",
        dry_run=True,
        apply_archive=False,
    )

    assert excel_mock.call_args.kwargs["completion_date"] == "EM ABERTO"
    assert result["completion_reason"] == "POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE"
    assert result["completion_action"] == "MARKED_AS_OPEN"


def test_valid_v6_cache_is_reused_without_pdf_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction_mock = Mock(side_effect=AssertionError("cache v6 deveria ser reutilizado"))
    monkeypatch.setattr(processing_service, "extract_pdf_text", extraction_mock)
    data = GenerationData(
        modules=[
            ModuleEquipment(
                manufacturer="LEAPTON",
                model="LP182",
                quantity=5,
                source="parallel_table",
            )
        ],
        module_total_quantity=5,
        inverters=[
            InverterEquipment(
                manufacturer="GROWATT",
                model="MIC3000",
                quantity=1,
                source="parallel_table",
            )
        ],
        inverter_total_quantity=1,
        module_source="parallel_table",
        inverter_source="parallel_table",
    )

    class StateStore:
        def get_protocol(self, protocol):
            return {
                "client_name": "CLIENTE CACHE",
                "technical_processing": {
                    "status": "validated",
                    "format_version": 7,
                    "equipment_rules_version": (
                        processing_service.EQUIPMENT_RULES_VERSION
                    ),
                    "technical_validation_status": "approved",
                    "technical_review_required": False,
                        "module_excel": "LEAPTON LP182 | 5 módulos",
                    "inverter_excel": "GROWATT MIC3000 | 1 inversor",
                        "placa_planilha": "5x LEAPTON LP182",
                    "inversor_planilha": "1x GROWATT MIC3000",
                    "technical_validation_errors": [],
                    "technical_validation_warnings": [],
                    "module_source": "parallel_table",
                    "inverter_source": "parallel_table",
                    "equipment": serialize_equipment_cache(data),
                },
            }

        def is_force_reprocess(self, protocol):
            return False

    result = processing_service._load_or_extract_technical_data(
        tmp_path / "Orcamento_de_Conexao_2500000003.pdf", StateStore()
    )

    extraction_mock.assert_not_called()
    assert result[6].approved is True


@pytest.mark.parametrize(
    "unsafe_cache",
    [
        {
            "status": "pending_review",
            "format_version": 4,
            "technical_validation_status": "pending_review",
            "technical_review_required": True,
            "placa_planilha": "5x LEAPTON MODELO",
            "inversor_planilha": "1x GOKIN AMBIGUO",
        },
        {
            "status": "success",
            "format_version": 4,
            "technical_validation_status": "approved",
            "technical_review_required": False,
            "placa_planilha": "",
            "inversor_planilha": "1x GROWATT MODELO",
        },
        {
            "status": "success",
            "format_version": 3,
            "placa_planilha": "",
            "inversor_planilha": "1x GROWATT MODELO",
        },
    ],
)
def test_invalid_or_pending_v4_cache_is_reextracted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_cache: dict,
) -> None:
    text = """
    Fabricante(s) do(s) modulos(s): LEAPTON
    Modelo(s) do(s) modulos(s): LP182-585W
    Qtd modulos: 5
    Pot. total da(s) placa(s): 2,92 kWp
    Fabricante(s) do(s) inversor(es): GROWATT
    Modelo(s) do(s) inversor(es): MIC 3000TL-X
    Qtd inversores: 1
    Pot. total do(s) inversor(es): 3 kW
    """
    extraction_mock = Mock(return_value=text)
    monkeypatch.setattr(processing_service, "extract_pdf_text", extraction_mock)
    monkeypatch.setattr(
        processing_service,
        "extract_protocol_from_pdf_text",
        lambda value: "2500000004",
    )
    monkeypatch.setattr(
        processing_service,
        "extract_client_from_pdf_text",
        lambda value: "CLIENTE SINTETICO",
    )

    class StateStore:
        updated_payload = None

        def get_protocol(self, protocol):
            return {"technical_processing": unsafe_cache}

        def is_force_reprocess(self, protocol):
            return False

        def update_section(self, protocol, section, payload, last_step=None):
            self.updated_payload = payload

    state_store = StateStore()
    result = processing_service._load_or_extract_technical_data(
        tmp_path / "Orcamento_de_Conexao_2500000004.pdf", state_store
    )

    extraction_mock.assert_called_once()
    assert result[4] == "5x LEAPTON LP182-585W"
    assert result[5] == "1x GROWATT MIC 3000TL-X"
    assert result[6].approved is True
    assert (
        state_store.updated_payload["format_version"]
        == processing_service.TECHNICAL_PROCESSING_FORMAT_VERSION
    )


def test_pending_validation_does_not_write_reusable_v4_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = """
    Fabricante(s) do(s) modulos(s): LEAPTON
    Modelo(s) do(s) modulos(s): LP182-585W
    Qtd modulos: 5
    Pot. total da(s) placa(s): 2,92 kWp
    Fabricante(s) do(s) inversor(es): GROWATT
    Modelo(s) do(s) inversor(es): MIC 3000TL-X
    """
    monkeypatch.setattr(processing_service, "extract_pdf_text", lambda path: text)
    monkeypatch.setattr(
        processing_service,
        "extract_protocol_from_pdf_text",
        lambda value: "2500000005",
    )
    monkeypatch.setattr(
        processing_service,
        "extract_client_from_pdf_text",
        lambda value: "CLIENTE SINTETICO",
    )

    class StateStore:
        updated_payload = None

        def get_protocol(self, protocol):
            return None

        def is_force_reprocess(self, protocol):
            return False

        def update_section(self, protocol, section, payload, last_step=None):
            self.updated_payload = payload

    state_store = StateStore()
    result = processing_service._load_or_extract_technical_data(
        tmp_path / "Orcamento_de_Conexao_2500000005.pdf", state_store
    )

    assert result[6].approved is False
    assert "INVERTER_QUANTITY_MISSING" in result[6].errors
    assert state_store.updated_payload["status"] == "pending_review"
    assert state_store.updated_payload["format_version"] is None
