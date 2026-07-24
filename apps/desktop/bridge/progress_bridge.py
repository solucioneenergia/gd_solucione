from __future__ import annotations

from typing import Any

from automacao_gd.application.contracts import ProgressEvent, ProgressTracker
from automacao_gd.presentation.desktop._qt import QObject, Signal
from automacao_gd.presentation.operational_output import sanitize_for_console


class ProgressBridge(QObject):
    progressChanged = Signal(dict)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.tracker = ProgressTracker(self._emit_progress)
        self.last_payload: dict[str, Any] | None = None

    def consume(self, event: ProgressEvent) -> dict[str, Any]:
        normalized = self.tracker.emit(event)
        return self._payload_from_event(normalized)

    def callback(self, event: ProgressEvent) -> None:
        self.consume(event)

    def _emit_progress(self, event: ProgressEvent) -> None:
        payload = self._payload_from_event(event)
        self.last_payload = payload
        self.progressChanged.emit(payload)

    @staticmethod
    def _payload_from_event(event: ProgressEvent) -> dict[str, Any]:
        return {
            "overall_percent": event.overall_percent,
            "stage_percent": event.stage_percent,
            "protocol_percent": event.protocol_percent,
            "stage": sanitize_for_console(event.stage),
            "protocol": sanitize_for_console(event.protocol, field_name="protocol")
            if event.protocol
            else None,
            "message": sanitize_for_console(event.message),
            "current": event.current,
            "total": event.total,
        }
