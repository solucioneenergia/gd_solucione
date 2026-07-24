import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text
from automacao_gd.infrastructure.excel.service import validate_workbook_format


JSON_REPORT_NAME = "validacao_formatacao_planilha.json"
MARKDOWN_REPORT_NAME = "validacao_formatacao_planilha.md"


def main() -> None:
    ensure_directories()
    setup_logger()
    settings = get_settings()
    payload = validate_workbook_format(
        workbook_path=settings.planilha_path,
        strict=settings.STRICT_WORKBOOK_VALIDATION,
    )
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    json_path, markdown_path = _save_reports(settings.logs_dir_path, payload)
    print(f"Relatorio JSON: {json_path}")
    print(f"Relatorio Markdown: {markdown_path}")
    print(f"Sucesso: {payload['success']}")


def _save_reports(logs_dir: Path, payload: dict) -> tuple[Path, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    json_path = logs_dir / JSON_REPORT_NAME
    markdown_path = logs_dir / MARKDOWN_REPORT_NAME
    atomic_write_json(json_path, payload, private=True)
    atomic_write_text(markdown_path, _build_markdown(payload), private=True)
    return json_path, markdown_path


def _build_markdown(payload: dict) -> str:
    lines = [
        "# Validacao de Formatacao da Planilha",
        "",
        "## Resumo",
        "",
        f"- Planilha: {payload.get('workbook_path')}",
        f"- Validação estrita: {payload.get('strict')}",
        f"- Sucesso: {payload.get('success')}",
        "",
        "## Reparos visuais necessários",
        "",
        "| Aba | Erros visuais | Alertas |",
        "| --- | --- | --- |",
    ]
    for sheet in payload.get("sheets", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(sheet.get("sheet")),
                    _md("; ".join(sheet.get("visual_errors", [])) or "-"),
                    _md("; ".join(sheet.get("warnings", [])) or "-"),
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
    if payload.get("warnings"):
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    else:
        lines.append("- Nenhum alerta.")

    lines.extend(["", "## Erros técnicos", ""])
    technical_errors = payload.get("technical_errors", [])
    if technical_errors:
        lines.extend(f"- {error}" for error in technical_errors)
    else:
        lines.append("- Nenhum erro técnico.")
    return "\n".join(lines) + "\n"


def _md(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
