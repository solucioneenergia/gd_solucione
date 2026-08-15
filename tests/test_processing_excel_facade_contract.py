from pathlib import Path

from automacao_gd.application import processing_results
from automacao_gd.application import processing_service
from automacao_gd.infrastructure.excel import excel_update_helpers
from automacao_gd.infrastructure.excel import service as excel_service


def test_processing_service_reexports_processing_result_helpers() -> None:
    assert processing_service._skipped_excel_status is processing_results.skipped_excel_status
    assert processing_service._empty_result is processing_results.empty_result
    assert processing_service._blocked_result is processing_results.blocked_result
    assert processing_service._excel_effect is processing_results.excel_effect
    assert processing_service._archive_effect is processing_results.archive_effect
    assert processing_service._processing_metrics is processing_results.processing_metrics
    assert processing_service._classify_processing_result is (
        processing_results.classify_processing_result
    )
    assert processing_service._compact_excel_status is processing_results.compact_excel_status
    assert processing_service._state_excel_payload is processing_results.state_excel_payload
    assert processing_service._state_archive_payload is processing_results.state_archive_payload
    assert processing_service._count_excel_updates is processing_results.count_excel_updates
    assert processing_service._count_archived is processing_results.count_archived
    assert processing_service._join_errors is processing_results.join_errors
    assert processing_service._protocol_from_filename is processing_results.protocol_from_filename


def test_excel_service_reexports_excel_payload_helpers() -> None:
    assert excel_service._base_excel_result is excel_update_helpers.base_excel_result
    assert excel_service._status_payload is excel_update_helpers.status_payload
    assert excel_service._payload_value is excel_update_helpers.payload_value
    assert excel_service._set_payload_value is excel_update_helpers.set_payload_value
    assert excel_service._sheet_name_for_entry_date is (
        excel_update_helpers.sheet_name_for_entry_date
    )
    assert excel_service._sheet_name_for_year is excel_update_helpers.sheet_name_for_year
    assert excel_service._completion_value_matches is excel_update_helpers.completion_value_matches
    assert excel_service._is_open_completion_value is excel_update_helpers.is_open_completion_value
    assert excel_service._equipment_text_matches is excel_update_helpers.equipment_text_matches
    assert excel_service._normalize_equipment_text is excel_update_helpers.normalize_equipment_text
    assert excel_service._normalize_cell is excel_update_helpers.normalize_cell
    assert excel_service._has_text is excel_update_helpers.has_text


def test_extracted_helpers_preserve_representative_existing_behavior() -> None:
    pdf_path = Path("Orcamento_de_Conexao_2600000001.pdf")

    empty = processing_results.empty_result(pdf_path)
    assert empty["pdf_path"] == str(pdf_path)
    assert empty["excel_status"]["can_write"] is False
    assert processing_results.protocol_from_filename(pdf_path) == "2600000001"

    result = excel_update_helpers.base_excel_result("2600000001", dry_run=True)
    assert result["protocol"] == "2600000001"
    assert result["dry_run"] is True
    assert excel_update_helpers.sheet_name_for_year(2021) == "2021"
    assert excel_update_helpers.sheet_name_for_year(2022) == "2022 - 2023"
    assert excel_update_helpers.normalize_cell(" a  b ") == "ab"
    assert excel_update_helpers.equipment_text_matches("A  B\nC", "A B\nC")
