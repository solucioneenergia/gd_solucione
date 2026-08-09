from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import IO


class ExecutionLockError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_HELD_LOCK_PATHS: set[str] = set()
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LockMetadata:
    execution_id: str
    operation: str
    requested_batch_limit: int
    authorization_scope: str

    def to_json(self) -> str:
        payload = {
            "schema_version": 1,
            "execution_id": self.execution_id,
            "pid": os.getpid(),
            "operation": self.operation,
            "requested_batch_limit": self.requested_batch_limit,
            "authorization_scope": self.authorization_scope,
            "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ExecutionLock:
    """Cross-process OS lock held by an open file descriptor."""

    def __init__(
        self,
        path: Path,
        *,
        execution_id: str,
        operation: str,
        requested_batch_limit: int,
        authorization_scope: str,
    ) -> None:
        self.path = Path(path)
        self.metadata = _LockMetadata(
            execution_id=execution_id,
            operation=operation,
            requested_batch_limit=requested_batch_limit,
            authorization_scope=authorization_scope,
        )
        self._handle: IO[str] | None = None
        self.acquired = False

    def __enter__(self) -> "ExecutionLock":
        key = str(self.path.resolve(strict=False)).lower()
        if key in _HELD_LOCK_PATHS:
            raise ExecutionLockError(
                "GLOBAL_EXECUTION_LOCK_REENTRANT",
                "A execução operacional já possui o lock global neste processo.",
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8", newline="\n")
        try:
            _try_lock_file(handle)
        except BlockingIOError as exc:
            handle.close()
            raise ExecutionLockError(
                "GLOBAL_EXECUTION_LOCKED",
                "Outra execução operacional já está em andamento.",
            ) from exc
        try:
            _validate_existing_marker_is_reclaimable(handle)
        except ExecutionLockError:
            try:
                _unlock_file(handle)
            finally:
                handle.close()
            raise
        handle.seek(0)
        handle.truncate()
        handle.write(self.metadata.to_json())
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        self.acquired = True
        _HELD_LOCK_PATHS.add(key)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        key = str(self.path.resolve(strict=False)).lower()
        try:
            if handle is not None:
                try:
                    _unlock_file(handle)
                finally:
                    handle.close()
        finally:
            self._handle = None
            self.acquired = False
            _HELD_LOCK_PATHS.discard(key)
            _remove_owned_marker(self.path, self.metadata.execution_id)


def _validate_existing_marker_is_reclaimable(handle: IO[str]) -> None:
    handle.seek(0)
    raw = handle.read().strip()
    if not raw:
        return
    marker = _parse_marker(raw)
    pid = _marker_pid(marker)
    if pid is not None and _pid_is_active(pid):
        raise ExecutionLockError(
            "GLOBAL_EXECUTION_LOCKED",
            "Outra execuÃ§Ã£o operacional jÃ¡ estÃ¡ em andamento.",
        )
    _LOGGER.warning(
        "Lock global orfao recuperado; marcador=%s; pid_status=inactive_or_invalid.",
        Path(handle.name).name,
    )


def _remove_owned_marker(path: Path, execution_id: str) -> None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    marker = _parse_marker(raw)
    if marker.get("execution_id") != execution_id:
        return
    try:
        path.unlink()
    except OSError:
        _LOGGER.warning(
            "Nao foi possivel remover marcador de lock global; marcador=%s.",
            path.name,
        )


def _parse_marker(raw: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _marker_pid(marker: dict[str, object]) -> int | None:
    raw_pid = marker.get("pid")
    if not isinstance(raw_pid, int | str):
        return None
    try:
        pid = int(raw_pid)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_is_active(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_pid_is_active(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_active(pid: int) -> bool:
    try:
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError, ValueError):
        return False


if os.name == "nt":
    import msvcrt

    def _try_lock_file(handle: IO[str]) -> None:
        handle.seek(0)
        try:
            getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc

    def _unlock_file(handle: IO[str]) -> None:
        handle.seek(0)
        try:
            getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock_file(handle: IO[str]) -> None:
        try:
            getattr(fcntl, "flock")(
                handle.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB")
            )
        except BlockingIOError:
            raise

    def _unlock_file(handle: IO[str]) -> None:
        getattr(fcntl, "flock")(handle.fileno(), getattr(fcntl, "LOCK_UN"))
