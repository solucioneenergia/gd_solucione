"""Persistência local segura e resistente a interrupções.

Os arquivos são escritos em um temporário no mesmo diretório e substituídos com
``os.replace``. Isso evita JSONs parcialmente gravados e reduz o risco de
corromper checkpoints quando a aplicação é encerrada durante uma escrita.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any


_WINDOWS_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)
_IS_WINDOWS = os.name == "nt"


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    private: bool = True,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            _set_private_permissions(temp_path)
        _replace_with_retry(temp_path, target)
        if private:
            _set_private_permissions(target)
        return target
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    private: bool = True,
    indent: int = 2,
) -> Path:
    return atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=indent),
        private=private,
    )


def atomic_copy_file(source: Path, destination: Path, *, private: bool = True) -> Path:
    source = Path(source)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as temp_handle:
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, temp_handle)
                temp_handle.flush()
                os.fsync(temp_handle.fileno())
        shutil.copystat(source, temp_path)
        if private:
            _set_private_permissions(temp_path)
        _replace_with_retry(temp_path, target)
        if private:
            _set_private_permissions(target)
        return target
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _set_private_permissions(path: Path) -> None:
    """Aplica 0600 quando suportado; no Windows, ``chmod`` é best-effort."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _replace_with_retry(source: Path, target: Path) -> None:
    """Replace a file while tolerating short Windows file locks."""
    try:
        os.replace(source, target)
        return
    except PermissionError:
        if not _IS_WINDOWS:
            raise

    for delay in _WINDOWS_REPLACE_RETRY_DELAYS:
        time.sleep(delay)
        try:
            os.replace(source, target)
            return
        except PermissionError:
            continue
    os.replace(source, target)
