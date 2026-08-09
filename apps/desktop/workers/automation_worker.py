from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from typing import Any

from automacao_gd.application.contracts import (
    OperationError,
    ProgressCallback,
    map_exception_to_operation_error,
)
from automacao_gd.presentation.desktop._qt import QObject, Signal, Slot


class AutomationWorker(QObject):
    started = Signal()
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(object)
    cancelled = Signal(str)
    log = Signal(str)

    def __init__(
        self,
        operation: Callable[..., Any],
        *,
        stage: str = "desktop_operation",
        protocol: str | None = None,
        run_async: bool = True,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.operation = operation
        self.stage = stage
        self.protocol = protocol
        self.run_async = run_async
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self.last_result: Any = None
        self.last_error: OperationError | None = None

    def start(self) -> None:
        if self.run_async:
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()
        else:
            self.run()

    @Slot()
    def run(self) -> None:
        self.started.emit()
        try:
            self._raise_if_cancelled()
            kwargs: dict[str, Any] = {}
            if _accepts_parameter(self.operation, "progress_callback"):
                kwargs["progress_callback"] = self._emit_progress
            if _accepts_parameter(self.operation, "cancel_event"):
                kwargs["cancel_event"] = self._cancel_event
            result = self.operation(**kwargs)
            self._raise_if_cancelled()
            self.last_result = result
            self.finished.emit(result)
        except OperationCancelled:
            self.cancelled.emit("Operação cancelada.")
        except Exception as exc:
            self.last_error = map_exception_to_operation_error(
                exc,
                stage=self.stage,
                protocol=self.protocol,
            )
            self.failed.emit(self.last_error)

    def stop(self) -> None:
        self._cancel_event.set()
        self.log.emit("Parada solicitada; a operação será interrompida quando o caso de uso permitir.")

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def _emit_progress(self, event: Any) -> None:
        self._raise_if_cancelled()
        self.progress.emit(event)

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise OperationCancelled


class OperationCancelled(RuntimeError):
    """Cancelamento cooperativo observado em uma fronteira segura de etapa."""


def operation_error_payload(error: OperationError | dict[str, Any]) -> dict[str, Any]:
    return error.to_dict() if isinstance(error, OperationError) else dict(error)


def _accepts_parameter(function: Callable[..., Any], parameter_name: str) -> bool:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return False
    return parameter_name in signature.parameters
