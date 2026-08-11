import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from src.models import PortalSolicitation
from src.pipeline_state_service import PipelineStateStore
from automacao_gd.domain.equipment_cache import serialize_equipment_cache
from automacao_gd.domain.equipment_validation import EQUIPMENT_RULES_VERSION
from automacao_gd.domain.models import GenerationData, InverterEquipment, ModuleEquipment


def _settings(tmp_path: Path, skip_completed: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        SKIP_ALREADY_COMPLETED=skip_completed,
        APPLY_EXCEL=False,
        APPLY_ARCHIVE=False,
        downloads_dir_path=tmp_path / "downloads",
        planilha_path=tmp_path / "planilha.xlsx",
    )


def _write_workbook_with_protocol(
    path: Path,
    protocol: str,
    *,
    completion: str | None,
) -> None:
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
        ]
    )
    ws.append(
        [
            "CLIENTE SINTETICO LTDA",
            protocol,
            "16/01/2026",
            completion,
            "Sim",
            "10x FAB MOD",
            "1x FAB INV",
        ]
    )
    wb.save(path)
    wb.close()


def _completed_artifacts(tmp_path: Path, protocol: str) -> dict:
    protocol_dir = tmp_path / "downloads" / protocol
    protocol_dir.mkdir(parents=True)
    metadata_path = protocol_dir / "metadata.json"
    pdf_path = protocol_dir / f"Orcamento_de_Conexao_{protocol}.pdf"
    folder_path = tmp_path / "clientes" / "Cliente Teste"
    metadata_path.write_text("{}", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4\n")
    folder_path.mkdir(parents=True)
    generation = GenerationData(
        modules=[
            ModuleEquipment(
                manufacturer="FAB", model="MOD", quantity=10, source="linear"
            )
        ],
        module_total_quantity=10,
        inverters=[
            InverterEquipment(
                manufacturer="FAB", model="INV", quantity=1, source="linear"
            )
        ],
        inverter_total_quantity=1,
        module_source="linear",
        inverter_source="linear",
    )

    return {
        "metadata": {"exists": True, "path": str(metadata_path)},
        "download": {
            "status": "existing_pdf_after_skip",
            "pdf_exists": True,
            "pdf_path": str(pdf_path),
        },
        "technical_processing": {
            "status": "success",
            "format_version": 7,
            "equipment_rules_version": EQUIPMENT_RULES_VERSION,
            "technical_validation_status": "approved",
            "technical_review_required": False,
            "module_excel": "FAB MOD | 10 módulos",
            "inverter_excel": "FAB INV | 1 inversor",
            "placa_planilha": "10x FAB MOD",
            "inversor_planilha": "1x FAB INV",
            "module_source": "synthetic",
            "inverter_source": "synthetic",
            "equipment": serialize_equipment_cache(generation),
            "source_protocol": protocol,
            "source_pdf_sha256": hashlib.sha256(b"%PDF-1.4\n").hexdigest(),
        },
        "client_folder": {
            "status": "found",
            "matched_path": str(folder_path),
        },
    }


def test_completed_protocol_is_skipped_when_artifacts_are_valid(tmp_path: Path) -> None:
    protocol = "2601"
    store = PipelineStateStore(path=tmp_path / "state.json", resume=False)
    store.update_protocol(
        protocol,
        status="completed",
        last_step="completed",
        data=_completed_artifacts(tmp_path, protocol),
    )

    assert store.should_skip_completed(protocol, _settings(tmp_path)) is True


def test_completed_protocol_is_not_skipped_when_workbook_completion_is_blank(
    tmp_path: Path,
) -> None:
    protocol = "2600001048"
    _write_workbook_with_protocol(tmp_path / "planilha.xlsx", protocol, completion=None)
    settings = _settings(tmp_path)
    settings.APPLY_EXCEL = True

    store = PipelineStateStore(path=tmp_path / "state.json", resume=False)
    store.update_protocol(
        protocol,
        status="completed",
        last_step="completed",
        data=_completed_artifacts(tmp_path, protocol),
    )

    assert store.should_skip_completed(protocol, settings) is False


def test_completed_protocol_is_not_skipped_when_force_reprocess(tmp_path: Path) -> None:
    protocol = "2601"
    store = PipelineStateStore(
        path=tmp_path / "state.json",
        resume=False,
        force_reprocess_protocols={protocol},
    )
    store.update_protocol(
        protocol,
        status="completed",
        last_step="completed",
        data=_completed_artifacts(tmp_path, protocol),
    )

    assert store.should_skip_completed(protocol, _settings(tmp_path)) is False


def test_completed_protocol_is_reprocessed_when_pdf_fingerprint_changes(
    tmp_path: Path,
) -> None:
    protocol = "2601"
    artifacts = _completed_artifacts(tmp_path, protocol)
    pdf_path = Path(artifacts["download"]["pdf_path"])
    store = PipelineStateStore(path=tmp_path / "state.json", resume=False)
    store.update_protocol(
        protocol,
        status="completed",
        last_step="completed",
        data=artifacts,
    )
    pdf_path.write_bytes(b"%PDF-1.4\nCONTEUDO ALTERADO\n")

    assert store.should_skip_completed(protocol, _settings(tmp_path)) is False


def test_completed_protocol_with_v3_technical_cache_is_reprocessed(tmp_path: Path) -> None:
    protocol = "2601"
    artifacts = _completed_artifacts(tmp_path, protocol)
    artifacts["technical_processing"]["format_version"] = 3
    store = PipelineStateStore(path=tmp_path / "state.json", resume=False)
    store.update_protocol(
        protocol,
        status="completed",
        last_step="completed",
        data=artifacts,
    )

    assert store.should_skip_completed(protocol, _settings(tmp_path)) is False


def test_completed_protocol_with_semantically_invalid_v4_cache_is_reprocessed(
    tmp_path: Path,
) -> None:
    protocol = "2601"
    artifacts = _completed_artifacts(tmp_path, protocol)
    technical = artifacts["technical_processing"]
    technical["inverter_excel"] = "GOKIN AMBIGUO | 1 inversor"
    technical["inversor_planilha"] = "1x GOKIN AMBIGUO"
    technical["equipment"]["conventional_inverters"][0].update(
        {
            "canonical_manufacturer": "GOKIN",
            "raw_manufacturer": "GOKIN",
            "canonical_model": "AMBIGUO",
            "raw_model": "AMBIGUO",
        }
    )
    store = PipelineStateStore(path=tmp_path / "state.json", resume=False)
    store.update_protocol(
        protocol,
        status="completed",
        last_step="completed",
        data=artifacts,
    )

    assert store.should_skip_completed(protocol, _settings(tmp_path)) is False


def test_state_is_persisted_after_each_step(tmp_path: Path) -> None:
    protocol = "2601"
    state_path = tmp_path / "state.json"
    store = PipelineStateStore(path=state_path, resume=False)

    store.mark_selected(
        PortalSolicitation(protocol=protocol, client_name="CLIENTE SINTETICO LTDA", status="CONCLUIDA")
    )
    selected_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert selected_state["protocols"][protocol]["last_step"] == "selected"

    store.update_section(
        protocol,
        "download",
        {"status": "downloaded", "pdf_exists": True, "pdf_path": "x.pdf"},
        last_step="pdf_downloaded",
    )
    download_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert download_state["protocols"][protocol]["download"]["status"] == "downloaded"

    store.add_error(protocol, "processing", "falha")
    error_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert error_state["protocols"][protocol]["status"] == "failed"
    assert error_state["protocols"][protocol]["errors"][0]["step"] == "processing"
