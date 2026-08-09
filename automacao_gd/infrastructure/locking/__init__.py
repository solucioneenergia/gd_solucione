"""Shared execution locks for operational workflows."""

from automacao_gd.infrastructure.locking.execution_lock import (
    ExecutionLock,
    ExecutionLockError,
)

__all__ = ["ExecutionLock", "ExecutionLockError"]
