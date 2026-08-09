from __future__ import annotations

import json
import multiprocessing
import os
import queue
import time
from pathlib import Path

import pytest

from automacao_gd.infrastructure.locking.execution_lock import (
    ExecutionLock,
    ExecutionLockError,
)


def _hold_lock(lock_path: str, ready, release, result_queue) -> None:
    try:
        with ExecutionLock(
            Path(lock_path),
            execution_id="SYNTH-LOCK-A",
            operation="option5",
            requested_batch_limit=10,
            authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
        ):
            ready.set()
            release.wait(5)
        result_queue.put("released")
    except Exception as exc:  # pragma: no cover - child process diagnostic
        result_queue.put(f"{type(exc).__name__}:{exc}")


def test_global_execution_lock_blocks_second_process_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "option5_execution.lock"
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    result_queue: multiprocessing.Queue[str] = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_hold_lock,
        args=(str(lock_path), ready, release, result_queue),
    )
    process.start()
    try:
        assert ready.wait(5)
        with pytest.raises(ExecutionLockError) as exc:
            with ExecutionLock(
                lock_path,
                execution_id="SYNTH-LOCK-B",
                operation="option5",
                requested_batch_limit=10,
                authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
            ):
                pass
        assert exc.value.code == "GLOBAL_EXECUTION_LOCKED"
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert result_queue.get(timeout=2) == "released"

    with ExecutionLock(
        lock_path,
        execution_id="SYNTH-LOCK-C",
        operation="option5",
        requested_batch_limit=10,
        authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
    ):
        pass


def test_global_execution_lock_writes_sanitized_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / "real_run_execution.lock"

    with ExecutionLock(
        lock_path,
        execution_id="SYNTH-META",
        operation="option5",
        requested_batch_limit=10,
        authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
    ) as lock:
        metadata = json.loads(lock.metadata.to_json())
        assert lock_path.exists()

    assert metadata["schema_version"] == 1
    assert metadata["execution_id"] == "SYNTH-META"
    assert metadata["operation"] == "option5"
    assert metadata["requested_batch_limit"] == 10
    assert metadata["authorization_scope"] == "SYNTHETIC_BATCH10_VALIDATION"
    assert "hostname" not in metadata
    assert "username" not in metadata
    assert "\\" not in json.dumps(metadata)
    assert ":/" not in json.dumps(metadata)
    assert not lock_path.exists()


def test_global_execution_lock_removes_marker_after_exception(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "real_run_execution.lock"

    with pytest.raises(RuntimeError):
        with ExecutionLock(
            lock_path,
            execution_id="SYNTH-EXC",
            operation="option5",
            requested_batch_limit=10,
            authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
        ):
            raise RuntimeError("synthetic failure")

    assert not lock_path.exists()
    with ExecutionLock(
        lock_path,
        execution_id="SYNTH-AFTER",
        operation="option5",
        requested_batch_limit=10,
        authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
    ):
        pass
    assert not lock_path.exists()


def test_global_execution_lock_removes_marker_after_success(tmp_path: Path) -> None:
    lock_path = tmp_path / "real_run_execution.lock"

    with ExecutionLock(
        lock_path,
        execution_id="SYNTH-SUCCESS",
        operation="option4",
        requested_batch_limit=1,
        authorization_scope="SYNTHETIC_OPTION4",
    ):
        assert lock_path.exists()

    assert not lock_path.exists()


def test_global_execution_lock_reclaims_orphan_marker_with_dead_pid(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "real_run_execution.lock"
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "execution_id": "SYNTH-ORPHAN",
                "pid": 999999999,
                "operation": "option4",
                "requested_batch_limit": 1,
                "authorization_scope": "SYNTHETIC_OPTION4",
            }
        ),
        encoding="utf-8",
    )

    with ExecutionLock(
        lock_path,
        execution_id="SYNTH-AFTER-ORPHAN",
        operation="option4",
        requested_batch_limit=1,
        authorization_scope="SYNTHETIC_OPTION4",
    ) as lock:
        metadata = json.loads(lock.metadata.to_json())
        assert metadata["execution_id"] == "SYNTH-AFTER-ORPHAN"

    assert not lock_path.exists()


def test_global_execution_lock_blocks_marker_with_active_pid(tmp_path: Path) -> None:
    lock_path = tmp_path / "real_run_execution.lock"
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "execution_id": "SYNTH-ACTIVE",
                "pid": os.getpid(),
                "operation": "option4",
                "requested_batch_limit": 1,
                "authorization_scope": "SYNTHETIC_OPTION4",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ExecutionLockError) as exc:
        with ExecutionLock(
            lock_path,
            execution_id="SYNTH-BLOCKED",
            operation="option4",
            requested_batch_limit=1,
            authorization_scope="SYNTHETIC_OPTION4",
        ):
            pass

    assert exc.value.code == "GLOBAL_EXECUTION_LOCKED"
    assert json.loads(lock_path.read_text(encoding="utf-8"))["execution_id"] == "SYNTH-ACTIVE"


def test_global_execution_lock_reentrant_acquire_is_blocked(tmp_path: Path) -> None:
    lock_path = tmp_path / "option5_execution.lock"

    with ExecutionLock(
        lock_path,
        execution_id="SYNTH-REENTRANT",
        operation="option5",
        requested_batch_limit=10,
        authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
    ):
        with pytest.raises(ExecutionLockError) as exc:
            with ExecutionLock(
                lock_path,
                execution_id="SYNTH-REENTRANT-2",
                operation="option5",
                requested_batch_limit=10,
                authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
            ):
                pass
        assert exc.value.code == "GLOBAL_EXECUTION_LOCK_REENTRANT"


def test_different_lock_paths_do_not_interfere(tmp_path: Path) -> None:
    with ExecutionLock(
        tmp_path / "a.lock",
        execution_id="SYNTH-A",
        operation="option5",
        requested_batch_limit=10,
        authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
    ):
        with ExecutionLock(
            tmp_path / "b.lock",
            execution_id="SYNTH-B",
            operation="option5",
            requested_batch_limit=10,
            authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
        ):
            time.sleep(0.01)
