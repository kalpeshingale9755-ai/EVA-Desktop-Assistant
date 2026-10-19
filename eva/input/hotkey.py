"""Global hotkey (works even when the EVA window is not in front)."""
from PySide6.QtCore import QObject, Signal

from eva.common.logging_setup import get_logger
from eva.input.voice_logic import to_pynput_combo

log = get_logger("eva.hotkey")


class HotkeyListener(QObject):
    pressed = Signal()

    def __init__(self, combo: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._combo = combo
        self._listener = None

    def start(self) -> bool:
        """Returns False (and logs why) if the hotkey could not be registered."""
        try:
            from pynput import keyboard
        except ImportError:
            log.error("pynput is not installed. Run: pip install pynput")
            return False
        try:
            combo = to_pynput_combo(self._combo)
            self._listener = keyboard.GlobalHotKeys({combo: lambda: self.pressed.emit()})
            self._listener.start()
        except Exception:
            log.exception("Could not register the hotkey '%s'", self._combo)
            self._listener = None
            return False
        log.info("Hotkey ready: %s", self._combo)
        return True

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                log.exception("Could not stop the hotkey listener")
            self._listener = None
