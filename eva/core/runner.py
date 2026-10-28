"""Runs EVA Core off the main thread, so a slow capability never freezes the window.

Requests are handled one at a time, in the order they arrive.
Signals reach the UI thread safely (Qt queues them).
"""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, Signal

from eva.common.logging_setup import get_logger
from eva.core.core import CoreResult, EvaCore
from eva.core.registry import CapabilityRegistry
from eva.core.settings import CoreSettings
from eva.understanding.request import RequestObject

log = get_logger("eva.core.runner")
_STOP = object()


class CoreRunner(QObject):
    state_changed = Signal(object)         # InterfaceState
    result_ready = Signal(object, object)  # RequestObject, CoreResult

    def __init__(self, registry: CapabilityRegistry | None = None,
                 settings: CoreSettings | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.core = EvaCore(registry, settings, on_state=self.state_changed.emit)
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="eva-core", daemon=True)
        self._thread.start()

    @property
    def registry(self) -> CapabilityRegistry:
        return self.core.registry

    def submit(self, request: RequestObject) -> None:
        self._queue.put(request)

    def cancel(self) -> None:
        self.core.cancel()

    def shutdown(self) -> None:
        self._queue.put(_STOP)
        self._thread.join(timeout=3)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            request: RequestObject = item  # type: ignore[assignment]
            result: CoreResult = self.core.handle(request)   # never raises
            self.result_ready.emit(request, result)
