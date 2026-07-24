import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import logger, setup_logger
from automacao_gd.infrastructure.pdf.service import (
    extract_client_from_pdf_text,
    extract_generation_data,
    extract_pdf_text,
    extract_protocol_from_pdf_text,
)


OUTPUT_PATH = PROJECT_ROOT / "data" / "logs" / "validacao_extracao_pdfs.json"
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


def main() -> None:
    ensure_directories()
    setup_logger()

    pdfs = sorted((PROJECT_ROOT / "data" / "downloads").rglob("*.pdf"))
    results = [_validate_pdf(pdf_path) for pdf_path in pdfs]
    total_missing = sum(1 for item in results if item["missing_fields"])
    failures = [item["protocol"] or item["pdf_path"] for item in results if not item["success"]]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_pdfs": len(results),
        "total_success": sum(1 for item in results if item["success"]),
        "total_with_missing_fields": total_missing,
        "total_failures": len(failures),
        "failed_protocols": failures,
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUTPUT_PATH, payload, private=True)

    for result in results:
        _print_pdf_result(result)

    print(f"Total de PDFs avaliados: {payload['total_pdfs']}")
    print(f"Total extraídos com sucesso: {payload['total_success']}")
    print(f"Total com campos ausentes: {payload['total_with_missing_fields']}")
    print(f"Protocolos com falha: {', '.join(failures) if failures else 'nenhum'}")
    print(f"Relatório salvo em: {OUTPUT_PATH}")


def _validate_pdf(pdf_path: Path) -> dict:
    try:
        text = extract_pdf_text(pdf_path)
        generation_data = extract_generation_data(pdf_path)
        module_excel = generation_data.format_module_for_excel()
        inverter_excel = generation_data.format_inverter_for_excel()
        missing_fields = [
            name
            for name, value in generation_data.model_dump().items()
            if name != "raw_text" and value is None
        ]
        header_issues = _header_issues(module_excel, inverter_excel)
        protocol = extract_protocol_from_pdf_text(text)
        result = {
            "pdf_path": _project_relative_path(pdf_path),
            "protocol": protocol,
            "client": extract_client_from_pdf_text(text),
            "module_excel": module_excel,
            "inverter_excel": inverter_excel,
            "missing_fields": missing_fields,
            "header_issues": header_issues,
            "success": not missing_fields and not header_issues,
            "error": None,
        }
        if not result["success"]:
            logger.warning(f"PDF com pendências de extração: {result}")
        return result
    except Exception as exc:
        logger.exception(f"Falha ao validar PDF {pdf_path}: {exc}")
        return {
            "pdf_path": _project_relative_path(pdf_path),
            "protocol": None,
            "client": None,
            "module_excel": None,
            "inverter_excel": None,
            "missing_fields": [],
            "header_issues": [],
            "success": False,
            "error": str(exc),
        }


def _header_issues(module_excel: str, inverter_excel: str) -> list[str]:
    issues: list[str] = []
    for term in FORBIDDEN_MODULE_TERMS:
        if term.lower() in module_excel.lower():
            issues.append(f"module_excel contem '{term}'")
    for term in FORBIDDEN_INVERTER_TERMS:
        if term.lower() in inverter_excel.lower():
            issues.append(f"inverter_excel contem '{term}'")
    return issues


def _print_pdf_result(result: dict) -> None:
    missing_fields = result["missing_fields"] or []
    header_issues = result["header_issues"] or []

    print("")
    print(f"PDF: {result['pdf_path']}")
    print(f"Protocolo: {result['protocol'] or 'Nao identificado'}")
    print(f"Cliente: {result['client'] or 'Nao identificado'}")
    print(f"Placa: {result['module_excel'] or 'Nao identificado'}")
    print(f"Inversor: {result['inverter_excel'] or 'Nao identificado'}")
    print(
        "Campos ausentes: "
        f"{', '.join(missing_fields) if missing_fields else 'nenhum'}"
    )
    if header_issues:
        print(f"Problemas de cabecalho: {', '.join(header_issues)}")
    if result["error"]:
        print(f"Erro: {result['error']}")


def _project_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
