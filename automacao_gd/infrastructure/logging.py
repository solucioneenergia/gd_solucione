from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

from loguru import logger

from automacao_gd.infrastructure.config import get_settings
from automacao_gd.infrastructure.log_privacy import sanitize_log_text


def setup_logger(logs_dir: Path | None = None, *, verbose: bool = False) -> None:
    settings = get_settings()
    target_dir = logs_dir or settings.logs_dir_path
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
    logger.add(sys.stderr, level=console_level, format=_console_format)
    logger.add(
        target_dir / "app.log",
        level="DEBUG",
        rotation="5 MB",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        format=_file_format,
    )


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
