"""EVA Speaker: the voice half of the Response Engine.

- speak() never blocks the UI (own worker thread + queue).
- stop() cuts the voice at once (use it when the user presses the hotkey).
- Main voice fails -> fallback voice speaks; main voice is skipped for a while.
- on_start / on_end / on_error are called from the worker thread.
  In PySide6 forward them with Qt signals (see INTEGRATION.md).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Dict, Optional, Sequence

from .backends import EdgeTTSBackend, Pyttsx3Backend, TTSBackend
from .text_prep import prepare_for_speech
from .tts_settings import TTSSettings

log = logging.getLogger("eva.response.speaker")
_SENTINEL = object()


class Speaker:
    def __init__(
        self,
        backends: Sequence[TTSBackend],
        settings: Optional[TTSSettings] = None,
        on_start: Optional[Callable[[], None]] = None,
        on_end: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.settings = settings or TTSSettings()
        self._backends = list(backends)
        self._on_start, self._on_end, self._on_error = on_start, on_end, on_error

        self._q: "queue.Queue[object]" = queue.Queue()
        self._lock = threading.Lock()
        self._gen = 0                       # changes on every stop()
        self._pending = 0                   # items queued or being spoken
        self._current_stop: Optional[threading.Event] = None
        self._current_backend: Optional[TTSBackend] = None
        self._skip_until: Dict[str, float] = {}
        self._thread: Optional[threading.Thread] = None
        self.muted = False
        self.last_backend_used: Optional[str] = None

    # ---------- public ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="eva-speaker", daemon=True)
        self._thread.start()

    def speak(self, text: str, interrupt: bool = False) -> bool:
        """Queue text to be spoken. Returns False if nothing was queued."""
        if not self.settings.enabled or self.muted:
            return False
        clean = prepare_for_speech(text, self.settings.max_spoken_chars)
        if not clean:
            return False
        if interrupt:
            self.stop()
        self.start()
        with self._lock:
            self._pending += 1
            gen = self._gen
        self._q.put((gen, clean))
        return True

    def stop(self) -> None:
        """Stop the current sentence and drop everything waiting."""
        with self._lock:
            self._gen += 1
            ev, backend = self._current_stop, self._current_backend
            drained = 0
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item is not _SENTINEL:
                    drained += 1
                else:
                    self._q.put(_SENTINEL)
                    break
            self._pending -= drained
        if ev is not None:
            ev.set()
        if backend is not None:
            try:
                backend.interrupt()
            except Exception:  # noqa: BLE001
                log.exception("interrupt failed")

    def mute(self, value: bool = True) -> None:
        self.muted = value
        if value:
            self.stop()

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._current_stop is not None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    def wait_idle(self, timeout: float = 30.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.busy:
                return True
            time.sleep(0.02)
        return not self.busy

    def shutdown(self) -> None:
        self.stop()
        self._q.put(_SENTINEL)
        if self._thread:
            self._thread.join(timeout=3)

    # ---------- worker ----------
    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is _SENTINEL:
                return
            gen, text = item  # type: ignore[misc]
            ev = threading.Event()
            with self._lock:
                if gen != self._gen:        # stop() happened after this was queued
                    self._pending -= 1
                    continue
                self._current_stop = ev
            try:
                self._safe_call(self._on_start)
                self._speak_with_fallback(text, ev)
            finally:
                with self._lock:
                    self._current_stop = None
                    self._current_backend = None
                    self._pending -= 1
                    last_one = self._pending <= 0
                if last_one:
                    self._safe_call(self._on_end)

    def _speak_with_fallback(self, text: str, ev: threading.Event) -> None:
        errors = []
        now = time.monotonic()
        for backend in self._backends:
            if self._skip_until.get(backend.name, 0) > now:
                continue
            with self._lock:
                self._current_backend = backend
            try:
                backend.speak(text, ev)
                self.last_backend_used = backend.name
                return
            except Exception as exc:  # noqa: BLE001
                if ev.is_set():             # user stopped it, not a real failure
                    return
                errors.append(f"{backend.name}: {exc}")
                log.warning("voice backend %s failed: %s", backend.name, exc)
                self._skip_until[backend.name] = (
                    time.monotonic() + self.settings.retry_main_after_s
                )
        if errors and self._on_error:
            self._safe_call(self._on_error, "; ".join(errors))
        elif not errors:
            # every backend was in its skip window: try the last one anyway
            try:
                self._backends[-1].speak(text, ev)
                self.last_backend_used = self._backends[-1].name
            except Exception as exc:  # noqa: BLE001
                self._safe_call(self._on_error, str(exc))

    @staticmethod
    def _safe_call(fn, *args) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("speaker callback failed")


def build_default_speaker(
    settings: Optional[TTSSettings] = None,
    on_start=None,
    on_end=None,
    on_error=None,
) -> Speaker:
    s = settings or TTSSettings()
    sp = Speaker(
        [EdgeTTSBackend(s), Pyttsx3Backend(s)],
        s,
        on_start=on_start,
        on_end=on_end,
        on_error=on_error,
    )
    sp.start()
    return sp
