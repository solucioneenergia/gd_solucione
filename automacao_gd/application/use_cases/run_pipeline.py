from __future__ import annotations

from automacao_gd.application.full_pipeline import run_full_cdp_pipeline
from automacao_gd.application.preflight import run_preflight
from automacao_gd.infrastructure.config import Settings, get_settings


class RunFullPipelineUseCase:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def execute(self) -> dict:
        preflight = run_preflight(
            self.settings,
            real_run=not self.settings.DRY_RUN,
            require_cdp=True,
        )
        if not preflight.ready:
            preflight.raise_if_blocked()
        return run_full_cdp_pipeline(self.settings)
