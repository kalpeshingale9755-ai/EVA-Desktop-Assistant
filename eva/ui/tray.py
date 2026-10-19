"""System tray icon. Shows state, and lets you show/hide/quit EVA."""
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from eva.common.types import InterfaceState
from eva.ui.main_window import MainWindow
from eva.ui.state_manager import StateManager
from eva.ui.theme import STATE_TEXT, make_state_icon


class EvaTray(QSystemTrayIcon):
    def __init__(self, window: MainWindow, states: StateManager, app: QApplication) -> None:
        super().__init__(make_state_icon(states.state))
        self._window = window
        self._app = app

        self._menu = QMenu()
        self._show_action = QAction("Show EVA", self._menu)
        self._hide_action = QAction("Hide EVA", self._menu)
        self._state_action = QAction("", self._menu)
        self._state_action.setEnabled(False)
        self._quit_action = QAction("Quit", self._menu)

        self._show_action.triggered.connect(self._show_window)
        self._hide_action.triggered.connect(self._window.hide)
        self._quit_action.triggered.connect(self._app.quit)

        self._menu.addAction(self._show_action)
        self._menu.addAction(self._hide_action)
        self._menu.addSeparator()
        self._menu.addAction(self._state_action)
        self._menu.addSeparator()
        self._menu.addAction(self._quit_action)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        states.state_changed.connect(self._on_state_changed)
        self._refresh(states.state)

    def _show_window(self) -> None:
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _on_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self._window.isVisible():
                self._window.hide()
            else:
                self._show_window()

    def _on_state_changed(self, _old, new) -> None:
        self._refresh(new)

    def _refresh(self, state: InterfaceState) -> None:
        self.setIcon(make_state_icon(state))
        self.setToolTip(f"EVA - {STATE_TEXT[state]}")
        self._state_action.setText(f"State: {state.value}")
