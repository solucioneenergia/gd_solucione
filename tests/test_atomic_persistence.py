from __future__ import annotations

import os
from pathlib import Path

import pytest

from automacao_gd.infrastructure.persistence import atomic
from automacao_gd.infrastructure.persistence.atomic import atomic_copy_file, atomic_write_json


def test_atomic_copy_uses_writable_descriptor_for_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_bytes(b"conteudo")

    original_fsync = os.fsync
    fsync_called = False

    def validating_fsync(fd: int) -> None:
        nonlocal fsync_called
        fsync_called = True
        os.write(fd, b"")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", validating_fsync)

    result = atomic_copy_file(source, destination)

    assert fsync_called is True
    assert result == destination
    assert destination.exists()
    assert destination.read_bytes() == source.read_bytes()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_json_retries_transient_windows_replace_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"
    original_replace = os.replace
    calls = 0

    def flaky_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(5, "Acesso negado", str(destination))
        original_replace(source, destination)

    monkeypatch.setattr(atomic, "_IS_WINDOWS", True)
    monkeypatch.setattr(atomic.os, "replace", flaky_replace)
    monkeypatch.setattr(atomic, "_WINDOWS_REPLACE_RETRY_DELAYS", (0,))

    atomic_write_json(target, {"version": 1})

    assert calls == 2
    assert target.read_text(encoding="utf-8").strip().startswith("{")
    assert not list(tmp_path.glob("*.tmp"))
