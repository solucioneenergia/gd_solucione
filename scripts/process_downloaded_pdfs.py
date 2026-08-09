import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger
from automacao_gd.application.processing_service import process_downloaded_pdfs


def main() -> int:
    ensure_directories()
    setup_logger()
    settings = get_settings()
    if not settings.DRY_RUN:
        print("Status: BLOQUEADO")
        print("Execução real direta desativada; use a opção 4 do CLI.")
        return 2

    payload = process_downloaded_pdfs(
        downloads_root=settings.downloads_dir_path,
        workbook_path=settings.planilha_path,
        clientes_root=settings.clientes_root_path,
        dry_run=settings.DRY_RUN,
        apply_excel=settings.APPLY_EXCEL,
        apply_archive=settings.APPLY_ARCHIVE,
    )

    print("")
    print("Resumo do processamento offline")
    print(f"DRY_RUN: {payload['dry_run']}")
    print(f"Total de PDFs avaliados: {payload['total_pdfs']}")
    print(f"Total processados com sucesso: {payload['total_success']}")
    print(f"Total com erro: {payload['total_errors']}")
    print(f"Total com pendência de pasta: {payload['total_pending_review']}")
    print(f"Atualizações reais na planilha: {payload['total_excel_updated']}")
    print(f"PDFs arquivados: {payload['total_archived']}")
    print(f"Relatório JSON: {payload['json_report_path']}")
    print(f"Relatório Markdown: {payload['markdown_report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
