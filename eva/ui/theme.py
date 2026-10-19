"""Colors, texts and icons for each interface state."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from eva.common.types import InterfaceState

STATE_COLORS = {
    InterfaceState.BACKGROUND: "#5C6370",
    InterfaceState.WAKING: "#E5C07B",
    InterfaceState.LISTENING: "#61AFEF",
    InterfaceState.PROCESSING: "#C678DD",
    InterfaceState.EXECUTING: "#D19A66",
    InterfaceState.RESPONDING: "#98C379",
    InterfaceState.ERROR: "#E06C75",
}

STATE_TEXT = {
    InterfaceState.BACKGROUND: "Idle, waiting",
    InterfaceState.WAKING: "Waking up...",
    InterfaceState.LISTENING: "Listening...",
    InterfaceState.PROCESSING: "Thinking...",
    InterfaceState.EXECUTING: "Doing it...",
    InterfaceState.RESPONDING: "Responding...",
    InterfaceState.ERROR: "Something went wrong",
}

WINDOW_STYLE = """
QWidget { background-color: #14161A; color: #E6E6E6; font-family: 'Segoe UI'; }
QLabel#title { font-size: 22px; font-weight: 600; letter-spacing: 6px; }
QLabel#state { font-size: 16px; }
QLineEdit { background-color: #1E2127; border: 1px solid #2C313A;
            border-radius: 8px; padding: 8px 10px; }
QLineEdit:focus { border: 1px solid #61AFEF; }
QGroupBox { border: 1px solid #2C313A; border-radius: 8px; margin-top: 10px;
            padding-top: 10px; color: #8A919E; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton { background-color: #1E2127; border: 1px solid #2C313A;
              border-radius: 6px; padding: 6px; }
QPushButton:hover { background-color: #272C34; }
QMenu { background-color: #1E2127; color: #E6E6E6; border: 1px solid #2C313A; }
QMenu::item:selected { background-color: #272C34; }
"""


def make_state_icon(state: InterfaceState) -> QIcon:
    """Draw a simple colored circle icon for the tray / window."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(STATE_COLORS[state]))
    painter.drawEllipse(8, 8, 48, 48)
    painter.end()
    return QIcon(pixmap)
