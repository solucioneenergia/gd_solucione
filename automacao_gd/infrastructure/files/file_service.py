import re
from pathlib import Path

from automacao_gd.infrastructure.config import get_settings


INVALID_FILENAME_CHARS = r'<>:"/\|?*'


def ensure_directories() -> None:
    settings = get_settings()
    directories = [
        settings.downloads_dir_path,
        settings.logs_dir_path,
        settings.resolve_path(Path("data/samples")),
        settings.resolve_path(Path("data/temp")),
        settings.resolve_path(Path("data/state")),
        settings.resolve_path(Path("data/cache")),
        settings.auth_state_path.parent,
        settings.browser_profile_dir_path,
        settings.resolve_path(Path("data/edge_cdp_profile")),
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def sanitize_filename(text: str) -> str:
    sanitized = re.sub(f"[{re.escape(INVALID_FILENAME_CHARS)}]", "_", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized.rstrip(". ")
    return sanitized or "arquivo"
