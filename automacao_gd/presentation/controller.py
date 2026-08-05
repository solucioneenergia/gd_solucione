from __future__ import annotations

from collections.abc import Callable

from automacao_gd.application.contracts import OperationResult, OperationStatus
from automacao_gd.application.preflight import run_preflight
from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.infrastructure.logging import logger


class ApplicationController:
    """Fachada única para CLI e Tkinter, sem dependência de widgets."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def preflight(self, *, real_run: bool = False, require_cdp: bool = False) -> OperationResult:
        report = run_preflight(self.settings, real_run=real_run, require_cdp=require_cdp)
        return OperationResult(
            success=report.ready,
            message="Ambiente pronto." if report.ready else "Ambiente não está pronto.",
            payload=report.to_dict(),
            status=(OperationStatus.SUCESSO if report.ready else OperationStatus.BLOQUEADO),
        )

    def inspect_portal(self, confirm_login: Callable[[str], bool] | None = None) -> OperationResult:
        from automacao_gd.application.use_cases.inspect_portal import InspectPortalUseCase

        return self._execute(
            "Inspeção do portal concluída.",
            lambda: InspectPortalUseCase(self.settings).execute(confirm_login),
        )

    def process_downloads(self, *, dry_run: bool) -> OperationResult:
        from automacao_gd.application.use_cases.process_downloads import (
            ProcessDownloadedPdfsUseCase,
        )

        return self._execute(
            "Processamento offline concluído.",
            lambda: ProcessDownloadedPdfsUseCase(self.settings).execute(dry_run=dry_run),
        )

    def run_pipeline(self) -> OperationResult:
        from automacao_gd.application.full_pipeline import run_full_cdp_pipeline

        return self._execute(
            "Pipeline CDP concluído.",
            lambda: run_full_cdp_pipeline(self.settings),
        )

    def sync_completion_status(self) -> OperationResult:
        from automacao_gd.application.use_cases.sync_completion import (
            SyncCompletionStatusUseCase,
        )

        return self._execute(
            "SincronizaÃ§Ã£o de conclusÃ£o concluÃ­da.",
            lambda: SyncCompletionStatusUseCase(self.settings).execute(),
        )

    @staticmethod
    def _execute(message: str, operation: Callable[[], dict]) -> OperationResult:
        try:
            payload = operation()
            raw_status = payload.get("status", OperationStatus.SUCESSO.value)
            try:
                status = OperationStatus(raw_status)
            except ValueError:
                status = OperationStatus.FALHOU
            return OperationResult(
                status is OperationStatus.SUCESSO,
                str(payload.get("operation_message") or message),
                payload,
                status=status,
            )
        except OperationalBlockError as exc:
            logger.warning(
                "Operação bloqueada com segurança: "
                f"code={exc.code}; stage={exc.stage}; cause={exc.technical_cause or '-'}"
            )
            return OperationResult(
                False,
                exc.user_message,
                {
                    "code": exc.code,
                    "stage": exc.stage,
                    "technical_cause": exc.technical_cause,
                    "total_pages_read": 0,
                    "total_downloaded": 0,
                    "total_processed_success": 0,
                    "total_excel_updated": 0,
                },
                status=OperationStatus.BLOQUEADO,
            )
        except Exception as exc:
            logger.exception(f"Falha na operação: {exc}")
            return OperationResult(
                False,
                "Ocorreu uma falha inesperada. Consulte o log técnico.",
                {"error": str(exc)},
                status=OperationStatus.FALHOU,
            )
