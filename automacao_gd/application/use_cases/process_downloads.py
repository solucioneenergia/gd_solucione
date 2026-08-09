from __future__ import annotations

from automacao_gd.application.operational_guard import (
    OfflineOperationAuthorization,
    guard_offline_operation,
)
from automacao_gd.application.processing_service import process_downloaded_pdfs
from automacao_gd.application.preflight import run_preflight
from automacao_gd.infrastructure.config import Settings, get_settings


class ProcessDownloadedPdfsUseCase:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def execute(
        self,
        *,
        dry_run: bool,
        authorization: OfflineOperationAuthorization | None = None,
    ) -> dict:
        if dry_run:
            return self._execute_processing(dry_run=True)

        with guard_offline_operation(self.settings, authorization) as (batch, proof):
            preflight = run_preflight(self.settings, real_run=True)
            if not preflight.ready:
                preflight.raise_if_blocked()
            return process_downloaded_pdfs(
                downloads_root=self.settings.downloads_dir_path,
                workbook_path=self.settings.planilha_path,
                clientes_root=self.settings.clientes_root_path,
                dry_run=False,
                pdf_paths=batch.pdf_paths,
                apply_excel=self.settings.APPLY_EXCEL,
                apply_archive=self.settings.APPLY_ARCHIVE,
                allowed_protocols=set(batch.protocols),
                authorization=authorization,
                lock_proof=proof,
            )

    def _execute_processing(self, *, dry_run: bool) -> dict:
        preflight = run_preflight(self.settings, real_run=not dry_run)
        if not preflight.ready:
            preflight.raise_if_blocked()
        return process_downloaded_pdfs(
            downloads_root=self.settings.downloads_dir_path,
            workbook_path=self.settings.planilha_path,
            clientes_root=self.settings.clientes_root_path,
            dry_run=dry_run,
            apply_excel=self.settings.APPLY_EXCEL,
            apply_archive=self.settings.APPLY_ARCHIVE,
        )
