import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.excel.service import project_dry_run_target_rows, update_excel_from_pdf_data
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text
from automacao_gd.infrastructure.metadata.service import load_portal_metadata
from automacao_gd.infrastructure.pdf.service import (
    extract_client_from_pdf_text,
    extract_generation_data,
    extract_pdf_text,
    extract_protocol_from_pdf_text,
)


JSON_REPORT = "validacao_mapeamento_planilha.json"
MARKDOWN_REPORT = "validacao_mapeamento_planilha.md"


def main() -> None:
    ensure_directories()
    setup_logger()
    settings = get_settings()

    started_at = datetime.now().isoformat(timespec="seconds")
    results = [_validate_pdf(pdf_path, settings) for pdf_path in _downloaded_pdfs(settings)]
    results = project_dry_run_target_rows(results, settings.planilha_path)
    payload = {
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "workbook_path": str(settings.planilha_path),
        "downloads_root": str(settings.downloads_dir_path),
        "total_pdfs": len(results),
        "total_with_entry_date": sum(1 for item in results if item.get("entry_date")),
        "total_blocked": sum(1 for item in results if str(item.get("action", "")).startswith("blocked")),
        "total_warnings": sum(1 for item in results if item.get("warning")),
        "results": results,
    }

    json_path = settings.logs_dir_path / JSON_REPORT
    markdown_path = settings.logs_dir_path / MARKDOWN_REPORT
    atomic_write_json(json_path, payload, private=True)
    atomic_write_text(markdown_path, _markdown(payload), private=True)

    print(f"Total de PDFs avaliados: {payload['total_pdfs']}")
    print(f"Total com data de ingresso: {payload['total_with_entry_date']}")
    print(f"Total bloqueados: {payload['total_blocked']}")
    print(f"Total com alerta: {payload['total_warnings']}")
    print(f"Relatório JSON: {json_path}")
    print(f"Relatório Markdown: {markdown_path}")


def _downloaded_pdfs(settings) -> list[Path]:
    return sorted(settings.downloads_dir_path.rglob("Orcamento_de_Conexao_*.pdf"))


def _validate_pdf(pdf_path: Path, settings) -> dict:
    text = extract_pdf_text(pdf_path)
    protocol = extract_protocol_from_pdf_text(text) or _protocol_from_filename(pdf_path)
    client_name = extract_client_from_pdf_text(text) or "CLIENTE_NAO_IDENTIFICADO"
    metadata, metadata_source = load_portal_metadata(
        settings.downloads_dir_path, settings.logs_dir_path, protocol
    )
    generation_data = extract_generation_data(pdf_path)
    placa_planilha = generation_data.format_module_for_planilha()
    inversor_planilha = generation_data.format_inverter_for_planilha()
    entry_date = metadata.get("entry_date") if metadata else None
    completion_date = metadata.get("completion_date") if metadata else None

    excel_status = update_excel_from_pdf_data(
        workbook_path=settings.planilha_path,
        protocol=protocol,
        client_name=client_name,
        entry_date=entry_date,
        completion_date=completion_date,
        module_text=placa_planilha,
        inverter_text=inversor_planilha,
        dry_run=True,
        apply_changes=False,
    )

    return {
        "protocol": protocol,
        "client_name": client_name,
        "pdf_path": _project_relative_path(pdf_path),
        "metadata_source": metadata_source,
        "entry_date": entry_date,
        "completion_date": completion_date,
        "target_sheet": excel_status.get("target_sheet"),
        "source_sheet": excel_status.get("source_sheet"),
        "source_row": excel_status.get("source_row"),
        "target_row": excel_status.get("target_row"),
        "existing_row": excel_status.get("existing_row"),
        "new_row": excel_status.get("new_row"),
        "moved_from": excel_status.get("moved_from"),
        "moved_to": excel_status.get("moved_to"),
        "row_number": excel_status.get("row_number"),
        "action": excel_status.get("action"),
        "placa_planilha": placa_planilha,
        "inversor_planilha": inversor_planilha,
        "warning": excel_status.get("warning"),
        "error": excel_status.get("error"),
    }


def _markdown(payload: dict) -> str:
    lines = [
        "# Validação de Mapeamento da Planilha",
        "",
        f"- Planilha: {payload['workbook_path']}",
        f"- PDFs avaliados: {payload['total_pdfs']}",
        f"- Com data de ingresso: {payload['total_with_entry_date']}",
        f"- Bloqueados: {payload['total_blocked']}",
        f"- Alertas: {payload['total_warnings']}",
        "",
        "| Protocolo | Cliente | Data ingresso | Aba origem | Linha origem | Aba destino | Linha destino | Ação | Placa | Inversor | Erro/Alerta |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in payload["results"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item["protocol"]),
                    _md(item["client_name"]),
                    _md(item["entry_date"]),
                    _md(item.get("source_sheet")),
                    _md(item.get("source_row")),
                    _md(item["target_sheet"]),
                    _md(item.get("target_row") or item.get("row_number")),
                    _md(item["action"]),
                    _md(item["placa_planilha"]),
                    _md(item["inversor_planilha"]),
                    _md(item["error"] or item["warning"] or "-"),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _protocol_from_filename(pdf_path: Path) -> str | None:
    import re

    match = re.search(r"Orcamento_de_Conexao_(\d+)", pdf_path.stem)
    return match.group(1) if match else None


def _project_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
