from __future__ import annotations

from pathlib import Path

from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.infrastructure.files.cleanup import cleanup_temp_files


class CleanupTemporaryFilesUseCase:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def execute(self, *, root: Path | None = None, dry_run: bool = True) -> dict:
        target_root = Path(root) if root is not None else self.settings.resolve_path(Path("data/temp"))
        return cleanup_temp_files(target_root, dry_run=dry_run).to_serializable_dict()
