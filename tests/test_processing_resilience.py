from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automacao_gd.application import processing_service
from automacao_gd.application.contracts import OperationStatus
from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.domain.equipment_cache import serialize_equipment_cache
from automacao_gd.domain.equipment_validation import TechnicalValidationResult
from automacao_gd.domain.models import GenerationData, InverterEquipment, ModuleEquipment
from tests._operational_auth import authorize_synthetic_pdfs


def _approved_generation_data() -> GenerationData:
    return GenerationData(
        modules=[
            ModuleEquipment(
                manufacturer="FABRICANTE SINTETICO",
                model="MODULO SINTETICO",
                quantity=5,
                source="parallel_table",
            )
        ],
        module_total_quantity=5,
        inverters=[
            InverterEquipment(
                manufacturer="FABRICANTE SINTETICO",
                model="INVERSOR SINTETICO",
                quantity=1,
                source="parallel_table",
            )
        ],
        inverter_total_quantity=1,
        module_source="parallel_table",
        inverter_source="parallel_table",
    )


def _configure_successful_single_pdf_dependencies(
    tmp_path: Path,
    pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Mock, Mock]:
    validation = TechnicalValidationResult(status="approved")
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(
            downloads_dir_path=tmp_path / "downloads-sinteticos",
            logs_dir_path=tmp_path / "logs-sinteticos",
        ),
    )
    monkeypatch.setattr(
        processing_service,
        "_load_or_extract_technical_data",
        lambda *args: (
            "2600000000",
            "CLIENTE SINTETICO LTDA",
            "MODULO SINTETICO",
            "INVERSOR SINTETICO",
            "5x MODULO SINTETICO",
            "1x INVERSOR SINTETICO",
            validation,
        ),
    )
    monkeypatch.setattr(
        processing_service,
        "load_portal_metadata",
        lambda *args: ({}, None),
    )
    monkeypatch.setattr(
        processing_service,
        "find_client_folder",
        lambda *args: SimpleNamespace(
            match_type="protocol",
            matched_path=str(tmp_path / "cliente-sintetico"),
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
            destination_folder=tmp_path / "cliente-sintetico",
            match_type="protocol",
            reason=None,
            should_create_folder=False,
            fallback_mode=None,
            legacy_gd_ignored=False,
        ),
    )
    excel = Mock(
        return_value={
            "success": True,
            "can_write": True,
            "skipped": False,
            "action": "update_existing",
        }
    )
    archive = Mock(
        return_value=SimpleNamespace(
            success=True,
            error=None,
            match_type="protocol",
            reason=None,
            created_folder=False,
            fallback_mode=None,
            legacy_gd_ignored=False,
            archived_pdf_path=str(tmp_path / "cliente-sintetico" / pdf.name),
            destination_folder=str(tmp_path / "cliente-sintetico"),
        )
    )
    monkeypatch.setattr(processing_service, "update_excel_from_pdf_data", excel)
    monkeypatch.setattr(processing_service, "archive_pdf_to_client_folder", archive)
    return excel, archive


def test_cache_protocol_mismatch_is_reextracted_and_blocks_all_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000001.pdf"
    content = "PROTOCOLO EXTRAIDO 2600000002\nCONTEUDO SINTETICO"
    pdf.write_text(content, encoding="utf-8")
    fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
    data = _approved_generation_data()
    writes: list[str] = []

    class StateStore:
        def get_protocol(self, protocol: str) -> dict:
            return {
                "client_name": "CLIENTE SINTETICO LTDA",
                "technical_processing": {
                    "status": "validated",
                    "format_version": processing_service.TECHNICAL_PROCESSING_FORMAT_VERSION,
                    "equipment_rules_version": processing_service.EQUIPMENT_RULES_VERSION,
                    "technical_validation_status": "approved",
                    "technical_review_required": False,
                    "module_excel": "MODULO SINTETICO",
                    "inverter_excel": "INVERSOR SINTETICO",
                    "placa_planilha": "5x FABRICANTE SINTETICO MODULO SINTETICO",
                    "inversor_planilha": "1x FABRICANTE SINTETICO INVERSOR SINTETICO",
                    "equipment": serialize_equipment_cache(data),
                    "source_protocol": "2600000002",
                    "source_pdf_sha256": fingerprint,
                },
            }

        def is_force_reprocess(self, protocol: str) -> bool:
            return False

        def update_section(self, *args: object, **kwargs: object) -> None:
            writes.append("update_section")

        def update_protocol(self, *args: object, **kwargs: object) -> None:
            writes.append("update_protocol")

        def mark_completed(self, *args: object, **kwargs: object) -> None:
            writes.append("mark_completed")

        def add_error(self, *args: object, **kwargs: object) -> None:
            writes.append("add_error")

    excel = Mock()
    archive = Mock()
    metadata = Mock(side_effect=AssertionError("metadata nao deve ser consultado"))
    monkeypatch.setattr(
        processing_service,
        "extract_pdf_text",
        lambda path: Path(path).read_text(encoding="utf-8"),
    )
    monkeypatch.setattr(
        processing_service,
        "extract_protocol_from_pdf_text",
        lambda text: re.search(r"\d{10}", text).group(0),
    )
    monkeypatch.setattr(processing_service, "update_excel_from_pdf_data", excel)
    monkeypatch.setattr(processing_service, "archive_pdf_to_client_folder", archive)
    monkeypatch.setattr(processing_service, "load_portal_metadata", metadata)

    result = processing_service._process_single_pdf(
        pdf,
        tmp_path / "planilha-sintetica.xlsx",
        tmp_path / "clientes-sinteticos",
        dry_run=False,
        state_store=StateStore(),
    )

    assert result["success"] is False
    assert result["error_code"] == "FROZEN_BATCH_SCOPE_VIOLATION"
    assert result["protocol"] == "2600000002"
    assert result["excel_effect"] == "not_applied"
    assert result["archive_effect"] == "not_applied"
    assert result["state_effect"] == "not_applied"
    assert writes == []
    excel.assert_not_called()
    archive.assert_not_called()
    metadata.assert_not_called()


def test_mark_completed_failure_marks_partial_traceable_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    validation = TechnicalValidationResult(status="approved")
    monkeypatch.setattr(
        processing_service,
        "_load_or_extract_technical_data",
        lambda *args: (
            "2600000000",
            "CLIENTE SINTETICO LTDA",
            "MODULO SINTETICO",
            "INVERSOR SINTETICO",
            "5x MODULO SINTETICO",
            "1x INVERSOR SINTETICO",
            validation,
        ),
    )
    monkeypatch.setattr(processing_service, "load_portal_metadata", lambda *args: ({}, None))
    monkeypatch.setattr(
        processing_service,
        "find_client_folder",
        lambda *args: SimpleNamespace(
            match_type="protocol",
            matched_path=str(tmp_path / "cliente-sintetico"),
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
            destination_folder=tmp_path / "cliente-sintetico",
            match_type="protocol",
            reason=None,
            should_create_folder=False,
            fallback_mode=None,
            legacy_gd_ignored=False,
        ),
    )
    monkeypatch.setattr(
        processing_service,
        "update_excel_from_pdf_data",
        lambda **kwargs: {
            "success": True,
            "can_write": True,
            "skipped": False,
            "action": "update_existing",
        },
    )
    monkeypatch.setattr(
        processing_service,
        "archive_pdf_to_client_folder",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            error=None,
            match_type="protocol",
            reason=None,
            created_folder=False,
            fallback_mode=None,
            legacy_gd_ignored=False,
            archived_pdf_path=str(tmp_path / "cliente-sintetico" / pdf.name),
            destination_folder=str(tmp_path / "cliente-sintetico"),
        ),
    )

    class StateStore:
        errors: list[tuple[str, str]] = []

        def update_section(self, *args: object, **kwargs: object) -> None:
            return None

        def mark_completed(self, protocol: str) -> None:
            raise OSError("falha sintetica ao concluir state")

        def add_error(self, protocol: str, step: str, message: str) -> None:
            self.errors.append((protocol, step))

        def get_protocol(self, protocol: str) -> None:
            return None

    state = StateStore()
    result = processing_service._process_single_pdf(
        pdf,
        tmp_path / "planilha-sintetica.xlsx",
        tmp_path / "clientes-sinteticos",
        dry_run=False,
        state_store=state,
    )

    assert result["success"] is False
    assert result["status"] == OperationStatus.PARCIAL.value
    assert result["error_code"] == "STATE_PERSISTENCE_FAILED"
    assert result["excel_effect"] == "applied"
    assert result["archive_effect"] == "archived"
    assert result["state_effect"] == "failed"
    assert result["manual_action_required"] is True
    assert state.errors == [("2600000000", "processing")]


def test_state_and_secondary_error_persistence_fail_after_archive_returns_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    monkeypatch.setattr(
        processing_service,
        "_load_or_extract_technical_data",
        lambda *args: (
            "2600000000",
            "CLIENTE SINTETICO LTDA",
            "MODULO SINTETICO",
            "INVERSOR SINTETICO",
            "5x MODULO SINTETICO",
            "1x INVERSOR SINTETICO",
            TechnicalValidationResult(status="approved"),
        ),
    )
    monkeypatch.setattr(processing_service, "load_portal_metadata", lambda *args: ({}, None))
    monkeypatch.setattr(
        processing_service,
        "find_client_folder",
        lambda *args: SimpleNamespace(
            match_type="protocol",
            matched_path=str(tmp_path / "cliente-sintetico"),
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
            destination_folder=tmp_path / "cliente-sintetico",
            match_type="protocol",
            reason=None,
            should_create_folder=False,
            fallback_mode=None,
            legacy_gd_ignored=False,
        ),
    )
    monkeypatch.setattr(
        processing_service,
        "update_excel_from_pdf_data",
        lambda **kwargs: {
            "success": True,
            "can_write": True,
            "skipped": False,
            "action": "update_existing",
        },
    )
    monkeypatch.setattr(
        processing_service,
        "archive_pdf_to_client_folder",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            error=None,
            match_type="protocol",
            reason=None,
            created_folder=False,
            fallback_mode=None,
            legacy_gd_ignored=False,
            archived_pdf_path=str(tmp_path / "cliente-sintetico" / pdf.name),
            destination_folder=str(tmp_path / "cliente-sintetico"),
        ),
    )

    class PersistentlyFailingState:
        update_calls = 0

        def update_section(self, *args: object, **kwargs: object) -> None:
            self.update_calls += 1
            if self.update_calls == 5:
                raise OSError("CAMINHO_PRIVADO token=SEGREDO_SINTETICO")

        def add_error(self, *args: object, **kwargs: object) -> None:
            raise OSError("CAMINHO_PRIVADO token=SEGREDO_SINTETICO")

        def get_protocol(self, protocol: str) -> None:
            return None

    result = processing_service._process_single_pdf(
        pdf,
        tmp_path / "planilha-sintetica.xlsx",
        tmp_path / "clientes-sinteticos",
        dry_run=False,
        state_store=PersistentlyFailingState(),
    )

    assert result["success"] is False
    assert result["status"] == OperationStatus.PARCIAL.value
    assert result["error_code"] == "STATE_PERSISTENCE_FAILED"
    assert result["excel_effect"] == "applied"
    assert result["archive_effect"] == "archived"
    assert result["state_effect"] == "failed"
    assert result["manual_action_required"] is True
    assert "CAMINHO_PRIVADO" not in result["error"]
    assert "SEGREDO_SINTETICO" not in result["error"]


def test_state_failure_immediately_after_excel_preserves_excel_effect_and_skips_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    _excel, archive = _configure_successful_single_pdf_dependencies(
        tmp_path, pdf, monkeypatch
    )

    class StateStore:
        errors: list[tuple[str, str]] = []

        def update_section(
            self, protocol: str, section: str, *args: object, **kwargs: object
        ) -> None:
            if section == "excel":
                raise OSError("falha sintetica apos excel")

        def add_error(self, protocol: str, step: str, message: str) -> None:
            self.errors.append((protocol, step))

        def get_protocol(self, protocol: str) -> None:
            return None

    state = StateStore()
    result = processing_service._process_single_pdf(
        pdf,
        tmp_path / "planilha-sintetica.xlsx",
        tmp_path / "clientes-sinteticos",
        dry_run=False,
        state_store=state,
    )

    assert result["success"] is False
    assert result["status"] == OperationStatus.PARCIAL.value
    assert result["error_code"] == "STATE_PERSISTENCE_FAILED"
    assert result["excel_effect"] == "applied"
    assert result["archive_effect"] == "not_applied"
    assert result["state_effect"] == "failed"
    assert result["manual_action_required"] is True
    assert state.errors == [("2600000000", "processing")]
    archive.assert_not_called()


def test_apply_archive_false_skips_client_folder_lookup_and_allows_excel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    excel, archive = _configure_successful_single_pdf_dependencies(
        tmp_path,
        pdf,
        monkeypatch,
    )
    monkeypatch.setattr(
        processing_service,
        "find_client_folder",
        Mock(side_effect=AssertionError("CLIENTES_ROOT nao deve ser varrido")),
    )
    monkeypatch.setattr(
        processing_service,
        "resolve_archive_destination_folder",
        Mock(side_effect=AssertionError("destino de arquivo nao deve ser resolvido")),
    )

    result = processing_service._process_single_pdf(
        pdf,
        tmp_path / "planilha-sintetica.xlsx",
        Path("Y:/"),
        dry_run=False,
        apply_excel=True,
        apply_archive=False,
    )

    assert result["success"] is True
    assert result["excel_effect"] == "applied"
    assert result["archive_effect"] == "not_applied"
    assert result["archive_reason"] == "APPLY_ARCHIVE=false"
    assert result["archive_status"]["reason"] == "APPLY_ARCHIVE=false"
    assert result["client_folder_match_type"] is None
    assert result["client_folder_search_elapsed_seconds"] is None
    excel.assert_called_once()
    archive.assert_not_called()


def test_state_failure_after_archive_preserves_applied_effects_without_false_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000000.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    _excel, archive = _configure_successful_single_pdf_dependencies(
        tmp_path, pdf, monkeypatch
    )

    class StateStore:
        errors: list[tuple[str, str]] = []

        def update_section(
            self, protocol: str, section: str, *args: object, **kwargs: object
        ) -> None:
            if section == "archive":
                raise OSError("falha sintetica apos archive")

        def add_error(self, protocol: str, step: str, message: str) -> None:
            self.errors.append((protocol, step))

        def get_protocol(self, protocol: str) -> None:
            return None

    state = StateStore()
    result = processing_service._process_single_pdf(
        pdf,
        tmp_path / "planilha-sintetica.xlsx",
        tmp_path / "clientes-sinteticos",
        dry_run=False,
        state_store=state,
    )

    assert result["success"] is False
    assert result["status"] == OperationStatus.PARCIAL.value
    assert result["error_code"] == "STATE_PERSISTENCE_FAILED"
    assert result["excel_effect"] == "applied"
    assert result["archive_effect"] == "archived"
    assert result["state_effect"] == "failed"
    assert result["manual_action_required"] is True
    assert state.errors == [("2600000000", "processing")]
    archive.assert_called_once()


def test_scope_violation_persists_sanitized_private_and_shareable_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "Orcamento_de_Conexao_2600000002.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
    logs = tmp_path / "logs"
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=logs),
    )

    with pytest.raises(OperationalBlockError) as exc:
        processing_service.process_downloaded_pdfs(
            downloads_root=tmp_path,
            workbook_path=tmp_path / "planilha-sintetica.xlsx",
            clientes_root=tmp_path / "clientes-sinteticos",
            dry_run=False,
            pdf_paths=[pdf],
            allowed_protocols={"2600000001"},
            authorization=authorize_synthetic_pdfs(tmp_path, [pdf]),
        )

    assert exc.value.code == "DIRECT_ROUTE_SCOPE_MISMATCH"


def test_real_processing_service_rejects_missing_explicit_scope_before_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setattr(processing_service, "ensure_directories", lambda: None)
    monkeypatch.setattr(processing_service, "clear_folder_cache", lambda: None)
    monkeypatch.setattr(
        processing_service,
        "get_settings",
        lambda: SimpleNamespace(logs_dir_path=logs),
    )
    monkeypatch.setattr(
        processing_service,
        "_resolve_pdf_paths",
        lambda *_: (_ for _ in ()).throw(AssertionError("busca aberta iniciada")),
    )

    with pytest.raises(OperationalBlockError) as exc:
        processing_service.process_downloaded_pdfs(
            downloads_root=tmp_path,
            workbook_path=tmp_path / "planilha-sintetica.xlsx",
            clientes_root=tmp_path / "clientes-sinteticos",
            dry_run=False,
        )

    assert exc.value.code == "DIRECT_ROUTE_AUTHORIZATION_REQUIRED"


def test_report_failure_is_recorded_in_state_without_changing_applied_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_updates: list[tuple[str, str, str | None]] = []

    class StateStore:
        def update_section(
            self,
            protocol: str,
            section: str,
            data: dict,
            *,
            last_step: str | None = None,
            status: str | None = None,
        ) -> None:
            state_updates.append((protocol, section, status))

    payload = {
        "status": OperationStatus.SUCESSO.value,
        "operation_message": "concluido",
        "dry_run": False,
        "apply_excel": True,
        "apply_archive": True,
        "total_pdfs": 1,
        "total_success": 1,
        "total_errors": 0,
        "results": [
            {
                "protocol": "2600000000",
                "success": True,
                "excel_effect": "applied",
                "archive_effect": "archived",
                "state_effect": "persisted",
                "manual_action_required": False,
            }
        ],
    }
    monkeypatch.setattr(
        processing_service,
        "atomic_write_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("falha sintetica")),
    )

    result = processing_service._persist_processing_reports(
        tmp_path,
        payload,
        state_store=StateStore(),
    )

    item = result["results"][0]
    assert result["status"] == OperationStatus.PARCIAL.value
    assert result["report_effect"] == "failed"
    assert item["success"] is False
    assert item["excel_effect"] == "applied"
    assert item["archive_effect"] == "archived"
    assert item["state_effect"] == "persisted"
    assert item["report_effect"] == "failed"
    assert item["manual_action_required"] is True
    assert state_updates == [("2600000000", "report", "operational_pending")]
