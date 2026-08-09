"""Compatibilidade mínima para testar a ponte sem carregar o Qt.

A aplicação desktop real exige PySide6. O fallback mantém a camada de
apresentação importável em ambientes de CLI e testes sem dependências gráficas.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any


_FORCE_QT_FALLBACK = os.environ.get("AUTOMACAO_GD_QT_FALLBACK") == "1"


if TYPE_CHECKING:
    from PySide6.QtCore import QObject, QThread, Signal, Slot

    QT_AVAILABLE: bool
else:
    if _FORCE_QT_FALLBACK:
        QT_AVAILABLE = False
    else:
        try:
            from PySide6.QtCore import QObject, QThread, Signal, Slot

            QT_AVAILABLE = True
        except ImportError:
            QT_AVAILABLE = False

    if not QT_AVAILABLE:
        QThread = None

        class _BoundSignal:
            def __init__(self) -> None:
                self._callbacks: list[Callable[..., Any]] = []

            def connect(self, callback: Callable[..., Any]) -> None:
                self._callbacks.append(callback)

            def emit(self, *args: Any) -> None:
                for callback in tuple(self._callbacks):
                    callback(*args)

        class Signal:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                self._name = ""

            def __set_name__(self, _owner: type, name: str) -> None:
                self._name = f"__signal_{name}"

            def __get__(self, instance: Any, _owner: type | None = None) -> Any:
                if instance is None:
                    return self
                signal = instance.__dict__.get(self._name)
                if signal is None:
                    signal = _BoundSignal()
                    instance.__dict__[self._name] = signal
                return signal

        class QObject:
            def __init__(self, _parent: Any = None) -> None:
                pass

            def moveToThread(self, _thread: Any) -> None:
                pass

            def deleteLater(self) -> None:
                pass

        def Slot(*_types: Any, **_kwargs: Any):
            def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
                return function

            return decorator


__all__ = ["QObject", "QThread", "QT_AVAILABLE", "Signal", "Slot"]
