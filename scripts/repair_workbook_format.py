import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.infrastructure.excel.service import repair_workbook_format
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text


JSON_REPORT_NAME = "reparo_formatacao_planilha.json"
MARKDOWN_REPORT_NAME = "reparo_formatacao_planilha.md"


def main() -> int:
    ensure_directories()
    setup_logger()
    settings = get_settings()

    if not settings.DRY_RUN:
        print("Status: BLOQUEADO")
        print("Reparo real direto desativado nesta etapa; execute somente dry-run.")
        return 2

    payload = repair_workbook_format(
        workbook_path=settings.planilha_path,
        dry_run=settings.DRY_RUN,
        template_sheet_name=settings.EXCEL_DEFAULT_TEMPLATE_SHEET,
        mode=settings.WORKBOOK_REPAIR_MODE,
    )
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    json_path, markdown_path = _save_reports(settings.logs_dir_path, payload)
    print(f"Relatorio JSON: {json_path}")
    print(f"Relatorio Markdown: {markdown_path}")
    print(f"Sucesso: {payload['success']}")
    return 0 if payload["success"] else 1


def _save_reports(logs_dir: Path, payload: dict) -> tuple[Path, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    json_path = logs_dir / JSON_REPORT_NAME
    markdown_path = logs_dir / MARKDOWN_REPORT_NAME
    atomic_write_json(json_path, payload, private=True)
    atomic_write_text(markdown_path, _build_markdown(payload), private=True)
    return json_path, markdown_path


def _build_markdown(payload: dict) -> str:
    lines = [
        "# Reparo de Formatacao da Planilha",
        "",
        "## Resumo",
        "",
        f"- DRY_RUN: {payload.get('dry_run')}",
        f"- Modo: {payload.get('mode')}",
        f"- Planilha: {payload.get('workbook_path')}",
        f"- Backup: {payload.get('backup_path') or '-'}",
        f"- Temporario: {payload.get('temp_path') or '-'}",
        f"- Integridade XLSX OK: {payload.get('xlsx_integrity_ok')}",
        f"- Arquivo oficial substituido: {payload.get('official_file_replaced')}",
        f"- Sucesso: {payload.get('success')}",
        "",
        "## Reparos visuais simulados/aplicados",
        "",
        "| Aba | Criaria | Linhas formatadas | #VALUE removidos | Datas formatadas | Protocolos como texto | Logomarca copiada |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sheet in payload.get("sheets", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(sheet.get("sheet")),
                    _md(sheet.get("would_create", False)),
                    _md(sheet.get("formatted_rows", 0)),
                    _md(sheet.get("value_errors_removed", 0)),
                    _md(sheet.get("date_cells_formatted", 0)),
                    _md(sheet.get("protocol_cells_as_text", 0)),
                    _md(sheet.get("logo_copied", False)),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Pendências de dados", ""])
    data_pending = payload.get("data_pending", [])
    if data_pending:
        lines.extend(
            [
                "| Aba | Célula/Linha | Tipo | Descrição | Ação sugerida |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in data_pending:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(item.get("sheet") or item.get("current_sheet")),
                        _md(item.get("cell") or item.get("row")),
                        _md(item.get("type")),
                        _md(item.get("description")),
                        _md(item.get("suggested_action")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- Nenhuma pendência de dados.")

    lines.extend(["", "## Alertas", ""])
    warnings = payload.get("warnings", []) + [
        warning
        for sheet in payload.get("sheets", [])
        for warning in sheet.get("warnings", [])
    ]
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- Nenhum alerta.")

    lines.extend(["", "## Erros técnicos", ""])
    technical_errors = payload.get("technical_errors", [])
    visual_errors = payload.get("visual_errors", [])
    errors = payload.get("errors", [])
    if technical_errors:
        lines.extend(f"- {error}" for error in technical_errors)
    elif visual_errors:
        lines.extend(f"- {error}" for error in visual_errors)
    elif errors:
        lines.extend(f"- {error}" for error in errors)
    else:
        lines.append("- Nenhum erro técnico.")
    return "\n".join(lines) + "\n"


def _md(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    raise SystemExit(main())
