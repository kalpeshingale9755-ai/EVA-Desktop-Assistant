"""EVA main window: title, orb, state text, text input, and a dev panel."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from eva.common.config import Config
from eva.common.logging_setup import get_logger
from eva.common.types import InterfaceState
from eva.ui.orb import Orb
from eva.ui.state_manager import StateManager
from eva.ui.theme import STATE_TEXT, WINDOW_STYLE, make_state_icon

log = get_logger("eva.window")


class MainWindow(QWidget):
    hidden_to_tray = Signal()
    text_submitted = Signal(str)  # the typed text, sent when you press Enter

    def __init__(self, config: Config, states: StateManager) -> None:
        super().__init__()
        self._states = states
        self._has_tray = False

        self.setWindowTitle(config.app_name)
        self.resize(config.window_width, config.window_height)
        self.setStyleSheet(WINDOW_STYLE)
        self.setWindowIcon(make_state_icon(states.state))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(config.app_name.upper())
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.orb = Orb()
        layout.addWidget(self.orb, stretch=1)

        self.state_label = QLabel()
        self.state_label.setObjectName("state")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.state_label)

        # Text input is the backup way to talk to EVA.
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Type a command and press Enter")
        self.text_input.returnPressed.connect(self._on_text_entered)
        layout.addWidget(self.text_input)

        # Shows how EVA understood the last typed text (useful while testing).
        self.understood_label = QLabel("")
        self.understood_label.setWordWrap(True)
        self.understood_label.setStyleSheet("color: #8A919E; font-size: 12px;")
        layout.addWidget(self.understood_label)

        if config.show_dev_panel:
            layout.addWidget(self._build_dev_panel())

        states.state_changed.connect(self._on_state_changed)
        self._apply_state(states.state)

    def set_tray_available(self, available: bool) -> None:
        """If there is a tray, closing the window hides it. If not, closing quits."""
        self._has_tray = available

    def _build_dev_panel(self) -> QGroupBox:
        box = QGroupBox("Dev: simulate states")
        grid = QGridLayout(box)
        for index, state in enumerate(InterfaceState):
            button = QPushButton(state.value.title())
            button.clicked.connect(lambda _checked=False, s=state: self._states.set_state(s))
            grid.addWidget(button, index // 2, index % 2)
        return box

    def _on_text_entered(self) -> None:
        text = self.text_input.text()
        self.text_input.clear()
        self.text_submitted.emit(text)

    def show_understood(self, message: str) -> None:
        self.understood_label.setText(message)

    def _on_state_changed(self, _old, new) -> None:
        self._apply_state(new)

    def _apply_state(self, state: InterfaceState) -> None:
        self.orb.set_state(state)
        self.state_label.setText(STATE_TEXT[state])
        self.setWindowIcon(make_state_icon(state))

    def closeEvent(self, event) -> None:
        if self._has_tray:
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
        else:
            event.accept()
