"""Ties voice input together: hotkey -> record -> speech-to-text -> text_ready signal.

Heavy work runs in a background thread, so the window never freezes.
Phases: idle -> listening -> processing -> idle. Every path ends back at idle.
"""
import threading
import time

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal

from eva.common.logging_setup import get_logger
from eva.common.types import InterfaceState
from eva.input.audio_recorder import MicError, RecordOutcome, record_until_silence
from eva.input.hotkey import HotkeyListener
from eva.input.speech_to_text import SpeechToText, SttError
from eva.input.voice_logic import is_junk
from eva.input.voice_settings import VoiceSettings
from eva.ui.state_manager import StateManager

log = get_logger("eva.voice")

_HOTKEY_DEBOUNCE = 0.3     # seconds: ignore a double-press that fast
_ERROR_SHOWN_FOR_MS = 2000


class _VoiceJob(QThread):
    """One voice command: record, then transcribe. Runs off the main thread."""
    recorded = Signal()
    no_speech = Signal()
    cancelled = Signal()
    nothing_heard = Signal()
    heard = Signal(str, object)     # text, confidence (float or None)
    failed = Signal(str)

    def __init__(self, settings: VoiceSettings, stt: SpeechToText,
                 cancel: threading.Event, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._stt = stt
        self._cancel = cancel

    def run(self) -> None:
        try:
            result = record_until_silence(self._settings, self._cancel)
            if result.outcome is RecordOutcome.CANCELLED:
                self.cancelled.emit()
                return
            if result.outcome is RecordOutcome.NO_SPEECH:
                self.no_speech.emit()
                return

            self.recorded.emit()
            transcript = self._stt.transcribe(result.audio)
            if is_junk(transcript.text):
                log.info("Ignored junk transcript: %r", transcript.text)
                self.nothing_heard.emit()
                return
            self.heard.emit(transcript.text, transcript.confidence)
        except (MicError, SttError) as exc:
            log.error("Voice input failed: %s", exc)
            self.failed.emit(str(exc))
        except Exception as exc:
            log.exception("Unexpected voice input error")
            self.failed.emit(f"Unexpected voice error: {exc}")


class VoiceInputService(QObject):
    text_ready = Signal(str, object)   # what you said, confidence (float or None)
    message = Signal(str)              # short status for the UI ("I didn't catch that.")

    def __init__(self, states: StateManager, settings: VoiceSettings,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._states = states
        self._settings = settings
        self._stt = SpeechToText(settings)
        self._hotkey = HotkeyListener(settings.hotkey, self)
        self._hotkey.pressed.connect(self._on_hotkey, Qt.ConnectionType.QueuedConnection)

        self._phase = "idle"           # idle | listening | processing
        self._cancel = threading.Event()
        self._jobs: set[_VoiceJob] = set()   # keeps running threads alive until they finish
        self._last_press = 0.0

    # ---- start / stop ----------------------------------------------------
    def start(self) -> bool:
        """Start the hotkey. Returns False if voice input cannot work."""
        ok = self._hotkey.start()
        if ok and self._settings.preload_model:
            threading.Thread(target=self._preload, name="eva-stt-preload", daemon=True).start()
        return ok

    def shutdown(self) -> None:
        self._hotkey.stop()
        self._cancel.set()
        for job in list(self._jobs):
            job.wait(3000)

    def _preload(self) -> None:
        try:
            self._stt.load()
        except SttError as exc:
            log.error("Speech model preload failed: %s", exc)   # shown again on first use

    # ---- hotkey ----------------------------------------------------------
    def _on_hotkey(self) -> None:
        now = time.monotonic()
        if now - self._last_press < _HOTKEY_DEBOUNCE:
            return
        self._last_press = now

        if self._phase == "idle":
            self._begin()
        elif self._phase == "listening":
            log.info("Hotkey pressed again: cancelling")
            self._cancel.set()
        else:
            log.info("Hotkey ignored: still processing the last command")

    def _begin(self) -> None:
        self._phase = "listening"
        self._cancel = threading.Event()
        self._states.set_state(InterfaceState.LISTENING)

        job = _VoiceJob(self._settings, self._stt, self._cancel)
        job.recorded.connect(self._on_recorded)
        job.no_speech.connect(lambda: self._finish("I didn't hear anything."))
        job.cancelled.connect(lambda: self._finish("Listening cancelled."))
        job.nothing_heard.connect(lambda: self._finish("I didn't catch that."))
        job.heard.connect(self._on_heard)
        job.failed.connect(self._on_failed)
        job.finished.connect(lambda j=job: self._jobs.discard(j))
        self._jobs.add(job)
        log.info("Listening...")
        job.start()

    # ---- results (all run on the main thread) -------------------------------
    def _on_recorded(self) -> None:
        self._phase = "processing"
        self._states.set_state(InterfaceState.PROCESSING)

    def _on_heard(self, text: str, confidence) -> None:
        self._finish(None)
        self.text_ready.emit(text, confidence)

    def _finish(self, status: str | None) -> None:
        self._phase = "idle"
        self._states.set_state(InterfaceState.BACKGROUND)
        if status:
            self.message.emit(status)

    def _on_failed(self, error: str) -> None:
        self._phase = "idle"
        self._states.set_state(InterfaceState.ERROR)
        self.message.emit(error)
        QTimer.singleShot(_ERROR_SHOWN_FOR_MS, self._recover_from_error)

    def _recover_from_error(self) -> None:
        if self._phase == "idle" and self._states.state == InterfaceState.ERROR:
            self._states.set_state(InterfaceState.BACKGROUND)
