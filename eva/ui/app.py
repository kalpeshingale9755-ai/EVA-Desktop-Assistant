"""Starts the Qt app: state manager, main window, system tray, and voice input."""
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from eva.common.config import load_config
from eva.common.logging_setup import get_logger
from eva.input.voice_service import VoiceInputService
from eva.input.voice_settings import VoiceSettings
from eva.understanding.request import InputSource
from eva.understanding.understand import understand
from eva.ui.main_window import MainWindow
from eva.ui.state_manager import StateManager
from eva.ui.tray import EvaTray

log = get_logger("eva.app")


def run_app() -> int:
    config = load_config()

    app = QApplication(sys.argv)
    app.setApplicationName(config.app_name)
    app.setQuitOnLastWindowClosed(False)  # EVA keeps running in the tray

    states = StateManager()
    window = MainWindow(config, states)

    # Typed OR spoken text -> Understanding System. In Stage 3, EVA Core will take over here.
    def handle_input(raw_text: str, source: InputSource, confidence: float | None = None) -> None:
        request = understand(raw_text, source, confidence)
        if request is None:
            window.show_understood("Nothing to do.")
            return
        log.info(
            "Request %s | source=%s | mode=%s | confidence=%s | text=%s",
            request.request_id, request.source.value,
            request.mode.value, request.confidence, request.clean_text,
        )
        prefix = "Heard: " if source is InputSource.VOICE else ""
        window.show_understood(f"{prefix}[{request.mode.value}] {request.clean_text}")

    window.text_submitted.connect(lambda text: handle_input(text, InputSource.TEXT))

    # Voice: hotkey -> record -> speech-to-text -> same handler as typed text.
    voice_settings = VoiceSettings()
    voice = VoiceInputService(states, voice_settings)
    voice.text_ready.connect(lambda text, conf: handle_input(text, InputSource.VOICE, conf))
    voice.message.connect(window.show_understood)
    app.aboutToQuit.connect(voice.shutdown)
    if voice.start():
        window.show_understood(f"Voice ready. Press {voice_settings.hotkey.upper()} and speak.")
    else:
        window.show_understood("Voice input is not available. Typing still works. See the log.")

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = EvaTray(window, states, app)
        tray.show()
        window.set_tray_available(True)
    else:
        log.warning("No system tray found. Closing the window will quit EVA.")
        app.setQuitOnLastWindowClosed(True)

    if not (config.start_hidden and tray is not None):
        window.show()

    return app.exec()
