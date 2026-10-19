"""The glowing orb. Its color and pulse change with the interface state."""
import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

from eva.common.types import InterfaceState
from eva.ui.theme import STATE_COLORS

# (pulse speed, pulse size) for each state
PULSE = {
    InterfaceState.BACKGROUND: (0.04, 0.03),
    InterfaceState.WAKING: (0.20, 0.10),
    InterfaceState.LISTENING: (0.12, 0.12),
    InterfaceState.PROCESSING: (0.30, 0.08),
    InterfaceState.EXECUTING: (0.25, 0.06),
    InterfaceState.RESPONDING: (0.15, 0.14),
    InterfaceState.ERROR: (0.50, 0.05),
}


class Orb(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(240, 240)
        self._state = InterfaceState.BACKGROUND
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # about 30 frames per second
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: InterfaceState) -> None:
        self._state = state
        self.update()

    # Only animate while the window is visible (saves battery in the tray).
    def showEvent(self, event) -> None:
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        speed, _ = PULSE[self._state]
        self._phase += speed
        self.update()

    def paintEvent(self, event) -> None:
        _, size = PULSE[self._state]
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        base = min(self.width(), self.height()) * 0.26
        radius = base * (1.0 + size * pulse)
        center = QPointF(self.width() / 2, self.height() / 2)

        color = QColor(STATE_COLORS[self._state])
        glow_color = QColor(color)
        glow_color.setAlpha(110)
        clear = QColor(color)
        clear.setAlpha(0)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        glow_radius = radius * 1.9
        gradient = QRadialGradient(center, glow_radius)
        gradient.setColorAt(0.0, glow_color)
        gradient.setColorAt(1.0, clear)
        painter.setBrush(gradient)
        painter.drawEllipse(center, glow_radius, glow_radius)

        painter.setBrush(color)
        painter.drawEllipse(center, radius, radius)
        painter.end()
