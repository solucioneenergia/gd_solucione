from __future__ import annotations

from types import SimpleNamespace

import pytest

from automacao_gd.application.contracts import OperationResult
from automacao_gd.presentation import cli
from automacao_gd.presentation.operational_output import (
    format_operation_summary,
    sanitize_for_console,
)


def _pipeline_result() -> OperationResult:
    return OperationResult(
        success=True,
        message="Pipeline CDP concluído.",
        payload={
            "dry_run": True,
            "total_pages_read": 1,
            "total_rows": 50,
            "total_completed": 26,
            "total_eligible_after_skip": 25,
            "total_selected": 1,
            "total_downloaded": 1,
            "total_existing_reused": 0,
            "total_protocols_selected_by_global_limit": 1,
            "total_pdfs_analyzed": 1,
            "total_processed_success": 1,
            "total_errors": 0,
            "total_excel_updated": 0,
            "total_archived": 0,
            "total_pending_review": 0,
            "protocol_results": [
                {
                    "protocol": "2606184625",
                    "client_name": "Cliente Teste",
                    "download_status": "existing_pdf_after_skip",
                    "sent_to_processing": True,
                    "processing_result": "success",
                    "excel_action": "update_existing",
                    "archive_state": "dry_run",
                    "generation_data": {
                        "raw_text": "CPF 123.456.789-00 contato pessoa@example.com"
                    },
                }
            ],
            "json_report_path": "data/logs/pipeline_cdp_completo.json",
            "markdown_report_path": "data/logs/pipeline_cdp_completo.md",
        },
    )


def test_pipeline_summary_does_not_print_raw_text_or_protocol_results() -> None:
    output = format_operation_summary("pipeline", _pipeline_result())

    assert "raw_text" not in output
    assert "protocol_results" not in output
    assert "123.456.789-00" not in output
    assert "pessoa@example.com" not in output


def test_pipeline_summary_shows_main_totals() -> None:
    output = format_operation_summary("pipeline", _pipeline_result())

    assert "Páginas lidas: 1" in output
    assert "Linhas lidas: 50" in output
    assert "Solicitações concluídas: 26" in output
    assert "Protocolos selecionados: 1" in output
    assert "PDFs analisados: 1" in output
    assert "PDFs aprovados tecnicamente: 1" in output
    assert "2606184625 | Cliente Teste | PDF reutilizado" in output
    assert "[TELEFONE REMOVIDO] | Cliente Teste" not in output


def test_pipeline_dry_run_summary_labels_completion_updates_as_proposals() -> None:
    result = _pipeline_result()
    result.payload.update(
        {
            "dry_run": True,
            "total_completion_dates_found": 5,
            "completion_dates_proposed": 5,
            "completion_dates_applied": 0,
            "open_values_proposed": 1,
            "open_values_applied": 0,
            "total_completion_no_change": 0,
            "total_completion_pending_review": 0,
        }
    )

    output = format_operation_summary("pipeline", result)

    assert "Datas propostas: 5" in output
    assert "EM ABERTO propostos: 1" in output
    assert "Datas atualizadas: 5" not in output
    assert "EM ABERTO aplicados: 1" not in output


def test_pipeline_real_run_summary_labels_completion_updates_as_applied() -> None:
    result = _pipeline_result()
    result.payload.update(
        {
            "dry_run": False,
            "total_completion_dates_found": 5,
            "completion_dates_proposed": 0,
            "completion_dates_applied": 5,
            "open_values_proposed": 0,
            "open_values_applied": 1,
            "total_completion_no_change": 0,
            "total_completion_pending_review": 0,
        }
    )

    output = format_operation_summary("pipeline", result)

    assert "Datas atualizadas: 5" in output
    assert "EM ABERTO aplicados: 1" in output
    assert "Datas propostas: 5" not in output
    assert "EM ABERTO propostos: 1" not in output


def test_pipeline_summary_shows_reconciliation_totals() -> None:
    result = _pipeline_result()
    result.payload["reconciliation"] = {
        "portal_concluded_unique": 461,
        "workbook_unique_protocols": 631,
        "matched_unique": 450,
        "missing_in_workbook_unique": 11,
        "duplicate_workbook_protocols": 0,
        "wrong_year_sheet": 2,
        "completion_empty": 20,
        "equipment_empty_requires_review": 3,
        "incomplete_records": 4,
        "markdown_report_path": "data/logs/portal_workbook_reconciliation_20260728T120000Z.md",
    }

    output = format_operation_summary("pipeline", result)

    assert "Reconciliação Portal × planilha:" in output
    assert "Concluídos únicos no Portal: 461" in output
    assert "Ausentes na planilha: 11" in output
    assert "portal_workbook_reconciliation_20260728T120000Z.md" in output


def test_pipeline_summary_shows_limited_operational_protocol_details() -> None:
    result = _pipeline_result()
    result.payload["protocol_results"] = [
        {
            "protocol": f"26000000{index}",
            "client_name": f"Cliente {index}",
            "download_status": "existing_pdf_after_skip",
            "sent_to_processing": True,
            "processing_result": "success",
            "excel_action": "update_existing",
            "archive_state": "dry_run",
            "archive_destination_folder": f"clientes/Cliente {index}",
        }
        for index in range(1, 7)
    ]

    output = format_operation_summary("pipeline", result)

    assert "Protocolos:" in output
    assert (
        "260000001 | Cliente 1 | PDF reutilizado | "
        "Excel: simulação/update_existing | Arquivo: simulação/clientes/Cliente 1"
    ) in output
    assert "260000005" in output
    assert "260000006" not in output
    assert (
        "Simulação: nenhuma alteração real foi feita em planilha ou pastas de clientes."
        in output
    )


def test_pipeline_summary_shows_actionable_protocol_errors() -> None:
    result = _pipeline_result()
    result.payload.update(
        {
            "total_errors": 1,
            "protocol_results": [
                {
                    "protocol": "2504225786",
                    "client_name": "Cliente Erro",
                    "download_status": "downloaded",
                    "sent_to_processing": True,
                    "processing_result": "error",
                    "excel_action": "update_existing",
                    "archive_state": "not_archived",
                    "error": (
                        "A planilha nÃ£o pÃ´de ser salva. O arquivo pode estar aberto, "
                        "sem permissÃ£o de escrita ou bloqueado pela rede/sincronizaÃ§Ã£o."
                    ),
                }
            ],
        }
    )

    output = format_operation_summary("pipeline", result)

    assert "Erros operacionais:" in output
    assert "2504225786 | Cliente Erro" in output
    assert "Problema: A planilha nÃ£o pÃ´de ser salva" in output
    assert "AÃ§Ã£o recomendada: Feche a planilha" in output
    assert "Traceback" not in output


def test_sanitize_for_console_removes_nested_raw_text() -> None:
    sanitized = sanitize_for_console(
        {"outer": {"raw_text": "segredo", "safe": "valor"}}
    )

    assert sanitized == {"outer": {"safe": "valor"}}


def test_sanitize_for_console_masks_email_cpf_and_cnpj() -> None:
    sanitized = sanitize_for_console(
        "CPF 123.456.789-00 CNPJ 12.345.678/0001-90 e-mail pessoa@example.com"
    )

    assert "123.456.789-00" not in sanitized
    assert "12.345.678/0001-90" not in sanitized
    assert "pessoa@example.com" not in sanitized
    assert sanitized.count("[CPF/CNPJ REMOVIDO]") == 2
    assert "[E-MAIL REMOVIDO]" in sanitized


@pytest.mark.parametrize("field_name", ["protocol", "protocolo"])
def test_sanitize_for_console_preserves_protocol_fields(field_name: str) -> None:
    sanitized = sanitize_for_console({field_name: "2606184625"})

    assert sanitized[field_name] == "2606184625"


@pytest.mark.parametrize("field_name", ["telefone", "phone"])
def test_sanitize_for_console_masks_phone_fields(field_name: str) -> None:
    sanitized = sanitize_for_console({field_name: "81999999999"})

    assert sanitized[field_name] == "[TELEFONE REMOVIDO]"


def test_pipeline_summary_masks_sensitive_labeled_text_without_masking_protocol() -> None:
    result = _pipeline_result()
    result.payload["run_error"] = (
        "CPF 123.456.789-00; e-mail pessoa@example.com; "
        "telefone: 81999999999; protocolo: 2606184625"
    )

    output = format_operation_summary("pipeline", result)

    assert "123.456.789-00" not in output
    assert "pessoa@example.com" not in output
    assert "81999999999" not in output
    assert "protocolo: 2606184625" in output


@pytest.mark.parametrize("argv, expected_verbose", [([], False), (["--verbose"], True)])
def test_cli_never_prints_complete_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected_verbose: bool,
) -> None:
    configured: list[bool] = []
    answers = iter(["5", "0"])

    class FakeController:
        settings = SimpleNamespace(DRY_RUN=True)

        def run_pipeline(self) -> OperationResult:
            return _pipeline_result()

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(
        cli,
        "setup_logger",
        lambda *, verbose=False: configured.append(verbose),
    )
    monkeypatch.setattr(cli, "ApplicationController", FakeController)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    cli.main(argv)

    output = capsys.readouterr().out
    assert configured == [expected_verbose]
    assert "raw_text" not in output
    assert "protocol_results" not in output
    assert "generation_data" not in output
    assert "pessoa@example.com" not in output
    assert "123.456.789-00" not in output
