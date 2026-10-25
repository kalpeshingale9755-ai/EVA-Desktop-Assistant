"""The "Eva" -> "Yes boss." -> listen-for-the-command flow.

Pure Python (no Qt, no microphone), so it is easy to test. VoiceInputService gives it:
  speak(text) -> bool            say something (False = could not speak)
  schedule(ms, fn)               run fn later, on the UI thread
  start_listening()              start recording the command
  on_waiting()                   show the WAKING state

Rules:
  - EVA starts listening when her voice has REALLY finished (speech_ended), not after a guessed sleep.
  - A short settle pause (ack_settle_ms) lets the sound of her voice die out of the microphone.
  - If her voice cannot play or never reports "finished", a safety timer starts listening anyway.
  - Pressing the hotkey cancels everything (cancel()).
"""
from __future__ import annotations

import time
from typing import Callable

from eva.common.logging_setup import get_logger

log = get_logger("eva.wake_ack")


class WakeAckFlow:
    def __init__(self, speak: Callable[[str], bool],
                 schedule: Callable[[int, Callable[[], None]], None],
                 start_listening: Callable[[], None],
                 on_waiting: Callable[[], None] | None = None,
                 ack_text: str = "Yes boss.", settle_ms: int = 200, fallback_ms: int = 10000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._speak, self._schedule, self._start = speak, schedule, start_listening
        self._on_waiting = on_waiting
        self.ack_text, self.settle_ms, self.fallback_ms = ack_text, settle_ms, fallback_ms
        self._clock = clock
        self._token = 0              # changes on cancel; old timers see a different token and do nothing
        self._waiting_for_voice = False
        self._launch_pending = False
        self._began_at = 0.0

    @property
    def active(self) -> bool:
        """True from 'Eva' until listening starts (or is cancelled)."""
        return self._waiting_for_voice or self._launch_pending

    def begin(self) -> None:
        self._token += 1
        token = self._token
        self._began_at = self._clock()
        self._waiting_for_voice, self._launch_pending = True, False
        log.info("Wake word heard: acknowledging with %r", self.ack_text)
        if self._on_waiting:
            self._call(self._on_waiting)
        try:
            spoke = self._speak(self.ack_text)
        except Exception:
            log.exception("Could not speak the acknowledgement")
            spoke = False
        if spoke:
            self._schedule(self.fallback_ms, lambda: self._fallback(token))
        else:
            log.warning("Acknowledgement could not be spoken: listening right away")
            self._waiting_for_voice = False
            self._launch_pending = True
            self._launch(token)

    def speech_ended(self) -> None:
        """Call this every time EVA's voice finishes."""
        if not self._waiting_for_voice:
            return
        token = self._token
        self._waiting_for_voice, self._launch_pending = False, True
        self._schedule(self.settle_ms, lambda: self._launch(token))

    def cancel(self) -> None:
        if self.active:
            log.info("Acknowledgement cancelled")
        self._token += 1
        self._waiting_for_voice = self._launch_pending = False

    # ---- internal ---------------------------------------------------------------
    def _fallback(self, token: int) -> None:
        if token == self._token and self._waiting_for_voice:
            log.warning("EVA's voice never reported 'finished' after %d ms: listening anyway",
                        self.fallback_ms)
            self._waiting_for_voice, self._launch_pending = False, True
            self._launch(token)

    def _launch(self, token: int) -> None:
        if token != self._token or not self._launch_pending:
            return
        self._launch_pending = False
        log.info("Listening for the command | %.0f ms after the wake word",
                 (self._clock() - self._began_at) * 1000)
        self._call(self._start)

    @staticmethod
    def _call(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("Wake flow step failed")
