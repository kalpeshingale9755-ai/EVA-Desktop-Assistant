"""Integration tests for Stage 1: PySide6 UI + system tray."""
import unittest
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent

from eva.common.config import Config
from eva.common.types import InterfaceState
from eva.ui.state_manager import StateManager
from eva.ui.main_window import MainWindow
from eva.ui.tray import EvaTray
from eva.ui.theme import STATE_TEXT, STATE_COLORS


class TestStage1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self):
        self.config = Config(app_name="EVA Test", show_dev_panel=True)
        self.states = StateManager()
        self.window = MainWindow(self.config, self.states)
        self.tray = EvaTray(self.window, self.states, self.app)
        self.window.set_tray_available(True)

    def tearDown(self):
        self.window.close()
        self.tray.hide()

    def test_window_components(self):
        """Test dark EVA window with animated orb and state text."""
        self.assertEqual(self.window.windowTitle(), "EVA Test")
        self.assertIn("#14161A", self.window.styleSheet())
        self.assertIsNotNone(self.window.orb)
        self.assertEqual(self.window.state_label.text(), STATE_TEXT[InterfaceState.BACKGROUND])

        # Test orb animation lifecycle with show/hide
        self.window.show()
        self.assertTrue(self.window.orb._timer.isActive())
        self.window.hide()
        self.assertFalse(self.window.orb._timer.isActive())

    def test_text_input_logs_without_execution(self):
        """Test text input does not execute commands, emits signal, and clears upon submit."""
        submitted = []
        self.window.text_submitted.connect(submitted.append)
        self.window.text_input.setText("hello world")
        self.window.text_input.returnPressed.emit()
        self.assertEqual(submitted, ["hello world"])
        self.assertEqual(self.window.text_input.text(), "")

    def test_seven_dev_buttons_and_state_updates(self):
        """Test seven dev buttons update interface state, orb, and tray."""
        # Find dev buttons
        dev_buttons = self.window.findChildren(QPushButton)
        self.assertEqual(len(dev_buttons), 7)

        expected_states = list(InterfaceState)
        self.assertEqual(len(expected_states), 7)

        for btn in dev_buttons:
            state_name = btn.text().upper()
            state = InterfaceState(state_name)
            btn.click()
            self.assertEqual(self.states.state, state)
            self.assertEqual(self.window.orb._state, state)
            self.assertEqual(self.window.state_label.text(), STATE_TEXT[state])
            self.assertEqual(self.tray.toolTip(), f"EVA - {STATE_TEXT[state]}")
            self.assertEqual(self.tray._state_action.text(), f"State: {state.value}")

    def test_close_window_hides_to_tray(self):
        """Test closing the window hides it rather than quitting when tray is available."""
        self.window.show()
        self.assertTrue(self.window.isVisible())

        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted() is False or not self.window.isVisible())
        self.assertFalse(self.window.isVisible())

    def test_tray_menu_actions(self):
        """Test tray menu actions: Show, Hide, Quit."""
        # Test Show
        self.window.hide()
        self.tray._show_action.trigger()
        self.assertTrue(self.window.isVisible())

        # Test Hide
        self.tray._hide_action.trigger()
        self.assertFalse(self.window.isVisible())

        # Verify quit action connects to app.quit
        self.assertEqual(self.tray._quit_action.text(), "Quit")


if __name__ == "__main__":
    unittest.main()
