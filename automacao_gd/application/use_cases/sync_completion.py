from __future__ import annotations

from automacao_gd.application.completion_sync_service import run_completion_sync
from automacao_gd.infrastructure.config import Settings


class SyncCompletionStatusUseCase:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def execute(self) -> dict:
        return run_completion_sync(self.settings)
