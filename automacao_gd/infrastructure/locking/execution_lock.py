from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import IO
from pathlib import Path
from types import TracebackType


class ExecutionLockError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_HELD_LOCK_PATHS: set[str] = set()


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
        if handle is not None:
            try:
                _unlock_file(handle)
            finally:
                handle.close()
        self._handle = None
        self.acquired = False
        _HELD_LOCK_PATHS.discard(key)


if os.name == "nt":
    import msvcrt

    def _try_lock_file(handle: IO[str]) -> None:
        handle.seek(0)
        locking = getattr(msvcrt, "locking")
        lock_non_blocking = getattr(msvcrt, "LK_NBLCK")
        try:
            locking(handle.fileno(), lock_non_blocking, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc

    def _unlock_file(handle: IO[str]) -> None:
        handle.seek(0)
        locking = getattr(msvcrt, "locking")
        unlock = getattr(msvcrt, "LK_UNLCK")
        try:
            locking(handle.fileno(), unlock, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock_file(handle: IO[str]) -> None:
        flock = getattr(fcntl, "flock")
        exclusive_lock = getattr(fcntl, "LOCK_EX")
        non_blocking = getattr(fcntl, "LOCK_NB")
        try:
            flock(handle.fileno(), exclusive_lock | non_blocking)
        except BlockingIOError:
            raise

    def _unlock_file(handle: IO[str]) -> None:
        flock = getattr(fcntl, "flock")
        unlock = getattr(fcntl, "LOCK_UN")
        flock(handle.fileno(), unlock)
