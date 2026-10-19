"""Holds the current interface state and tells the UI when it changes.

Right now the dev buttons change the state. Later (Stage 3) EVA Core will do it.
"""
from PySide6.QtCore import QObject, Signal

from eva.common.logging_setup import get_logger
from eva.common.types import InterfaceState

log = get_logger("eva.state")


class StateManager(QObject):
    state_changed = Signal(object, object)  # (old_state, new_state)

    def __init__(self) -> None:
        super().__init__()
        self._state = InterfaceState.BACKGROUND

    @property
    def state(self) -> InterfaceState:
        return self._state

    def set_state(self, new_state: InterfaceState) -> None:
        if new_state == self._state:
            return
        old = self._state
        self._state = new_state
        log.info("State: %s -> %s", old.value, new_state.value)
        self.state_changed.emit(old, new_state)
