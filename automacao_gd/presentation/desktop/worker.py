from __future__ import annotations

import threading
from collections.abc import Callable

from automacao_gd.presentation.desktop._qt import QObject, Signal, Slot


class OperationCancelled(RuntimeError):
    """Cancelamento cooperativo solicitado antes da execução da operação."""


class OperationWorker(QObject):
    resultReady = Signal(object)
    errorOccurred = Signal(str)
    progressChanged = Signal(int)
    statusChanged = Signal(str)
    finished = Signal()

    def __init__(
        self,
        operation: Callable[[], object],
        *,
        initial_status: str,
        cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.operation = operation
        self.initial_status = initial_status
        self.cancel_event = cancel_event or threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                raise OperationCancelled("Operação cancelada antes de iniciar.")
            self.statusChanged.emit(self.initial_status)
            self.progressChanged.emit(10)
            result = self.operation()
            self.progressChanged.emit(90)
            self.resultReady.emit(result)
        except Exception as exc:
            self.errorOccurred.emit(str(exc))
        finally:
            self.finished.emit()
