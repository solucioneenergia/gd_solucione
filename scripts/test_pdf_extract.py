import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.infrastructure.pdf.service import (
    extract_client_from_pdf_text,
    extract_generation_data,
    extract_pdf_text,
    extract_protocol_from_pdf_text,
)


def extract_pdf_summary(pdf_path: Path) -> dict:
    text = extract_pdf_text(pdf_path)
    protocol = extract_protocol_from_pdf_text(text)
    client = extract_client_from_pdf_text(text)
    data = extract_generation_data(pdf_path)
    return {
        "protocol": protocol,
        "client": client,
        "module": data.format_module_for_excel(),
        "inverter": data.format_inverter_for_excel(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Testa extração do PDF do orçamento.")
    parser.add_argument("pdf_path", type=Path)
    args = parser.parse_args()

    ensure_directories()
    setup_logger()

    summary = extract_pdf_summary(args.pdf_path)
    print(f"Protocolo: {summary['protocol'] or '-'}")
    print(f"Cliente: {summary['client'] or '-'}")
    print(f"Placa: {summary['module'] or '-'}")
    print(f"Inversor: {summary['inverter'] or '-'}")


if __name__ == "__main__":
    main()
