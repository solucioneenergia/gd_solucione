from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from loguru import logger

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.log_privacy import sanitize_log_text


def setup_logger(logs_dir: Path | None = None, *, verbose: bool = False) -> None:
    settings = get_settings()
    target_dir = _resolve_logs_dir(logs_dir, settings)
    target_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    configured_level = settings.LOG_LEVEL
    console_level = (
        "DEBUG"
        if configured_level == "DEBUG"
        else "INFO"
        if verbose or configured_level == "INFO"
        else configured_level
    )
    console_sink = _console_sink()
    if console_sink is not None:
        logger.add(console_sink, level=console_level, format=_console_format)
    logger.add(
        target_dir / "app.log",
        level="DEBUG",
        encoding="utf-8",
        enqueue=True,
        format=_file_format,
    )


def _resolve_logs_dir(logs_dir: Path | None, settings: Any) -> Path:
    if logs_dir is not None:
        return Path(logs_dir)
    configured_logs_dir = getattr(settings, "LOGS_DIR", None)
    configured_logs_path = getattr(settings, "logs_dir_path", None)
    if _is_frozen_default_logs_dir(configured_logs_dir):
        return _local_app_logs_dir()
    if configured_logs_path is not None:
        return Path(configured_logs_path)
    return _local_app_logs_dir()


def _is_frozen_default_logs_dir(configured_logs_dir: Any) -> bool:
    if not getattr(sys, "frozen", False):
        return False
    if configured_logs_dir is None:
        return True
    try:
        path = Path(configured_logs_dir)
    except TypeError:
        return True
    return not path.is_absolute() and path.as_posix().casefold() == "data/logs"


def _local_app_logs_dir() -> Path:
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("APPDATA")
        or tempfile.gettempdir()
    )
    return Path(base) / "AutomacaoGDNeoenergia" / "logs"


def _console_sink() -> Any | None:
    for stream in (sys.stderr, getattr(sys, "__stderr__", None)):
        if stream is not None:
            return stream
    return None


def _console_format(record: Any) -> str:
    _prepare_safe_record(record)
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<level>{extra[safe_message]}</level>\n{extra[safe_exception]}"
    )


def _file_format(record: Any) -> str:
    _prepare_safe_record(record)
    return (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} | {extra[safe_message]}\n{extra[safe_exception]}"
    )


def _prepare_safe_record(record: Any) -> None:
    record["extra"]["safe_message"] = sanitize_log_text(record["message"])
    exception = record.get("exception")
    if exception:
        formatted = "".join(traceback.format_exception(*exception))
        record["extra"]["safe_exception"] = sanitize_log_text(formatted)
    else:
        record["extra"]["safe_exception"] = ""


__all__ = ["logger", "setup_logger"]
