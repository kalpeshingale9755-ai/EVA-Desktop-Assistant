"""Starts the Qt app: state manager, main window, system tray, and voice input."""
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from eva.common.config import load_config
from eva.common.logging_setup import get_logger
from eva.capabilities.defaults import register_default_capabilities
from eva.common.types import InterfaceState
from eva.core.runner import CoreRunner
from eva.input.voice_service import VoiceInputService
from eva.input.voice_settings import VoiceSettings
from eva.response.speaker import build_default_speaker
from eva.understanding.request import InputSource
from eva.understanding.understand import is_wake_only, understand
from eva.ui.main_window import MainWindow
from eva.ui.state_manager import StateManager
from eva.ui.tray import EvaTray

log = get_logger("eva.app")


class SpeechBridge(QObject):
    speech_started = Signal()
    speech_ended = Signal()
    speech_error = Signal(str)


def run_app() -> int:
    config = load_config()

    app = QApplication(sys.argv)
    app.setApplicationName(config.app_name)
    app.setQuitOnLastWindowClosed(False)  # EVA keeps running in the tray

    states = StateManager()
    window = MainWindow(config, states)

    # Voice output: bridge forwards speaker thread callbacks to UI thread
    bridge = SpeechBridge(app)
    bridge.speech_started.connect(lambda: states.set_state(InterfaceState.RESPONDING))
    bridge.speech_error.connect(lambda msg: log.error("Voice output error: %s", msg))

    speaker = build_default_speaker(
        on_start=bridge.speech_started.emit,
        on_end=bridge.speech_ended.emit,
        on_error=bridge.speech_error.emit,
    )
    app.aboutToQuit.connect(speaker.shutdown)

    # EVA Core: decides what to do and returns an outcome. It runs on its own thread.
    core_runner = CoreRunner(parent=app)
    register_default_capabilities(core_runner.registry)
    core_runner.state_changed.connect(lambda state: states.set_state(state))
    app.aboutToQuit.connect(core_runner.shutdown)

    def on_core_result(request, result) -> None:
        window.show_understood(result.reply)
        if request.source is InputSource.VOICE:
            speaker.speak(result.reply)
        if result.reason == "internal_error":   # let the red ERROR state fade away
            QTimer.singleShot(
                2000,
                lambda: states.set_state(InterfaceState.BACKGROUND)
                if states.state == InterfaceState.ERROR
                else None,
            )

    core_runner.result_ready.connect(on_core_result)

    # Typed OR spoken text -> Understanding System -> EVA Core.
    def handle_input(raw_text: str, source: InputSource, confidence: float | None = None,
                     force_command: bool = False) -> None:
        clean_norm = " ".join((raw_text or "").lower().split()).strip(" .,!?")
        if clean_norm in ("stop", "cancel", "eva stop", "eva cancel", "hey eva stop", "hey eva cancel"):
            core_runner.cancel()
        request = understand(raw_text, source, confidence, force_command=force_command)
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
        core_runner.submit(request)

    window.text_submitted.connect(lambda text: handle_input(text, InputSource.TEXT))

    # Voice: hotkey -> record -> speech-to-text -> same handler as typed text.
    voice_settings = VoiceSettings()
    voice = VoiceInputService(states, voice_settings, speaker=speaker)

    def on_voice_text(text: str, conf: float | None = None) -> None:
        if is_wake_only(text):
            voice.wake_ack()      # "Yes boss." then EVA listens for the command by herself
            return
        handle_input(text, InputSource.VOICE, conf)   # EVA Core replies and speaks

    def on_command_text(text: str, conf: float | None = None) -> None:
        handle_input(text, InputSource.VOICE, conf, force_command=True)   # no wake word needed now

    voice.text_ready.connect(on_voice_text)
    voice.command_ready.connect(on_command_text)

    def on_speech_ended() -> None:
        voice.notify_speech_ended()        # may start the command listening after "Yes boss."
        if states.state == InterfaceState.RESPONDING and not voice.ack_active:
            states.set_state(InterfaceState.BACKGROUND)

    bridge.speech_ended.connect(on_speech_ended)
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
