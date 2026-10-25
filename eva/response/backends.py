"""Voice engines. Each one speaks one text and obeys a stop signal.

EdgeTTSBackend   : main voice (en-IN-NeerjaNeural), needs internet.
Pyttsx3Backend  : offline fallback (Microsoft Zira).
Both import their libraries lazily, so tests run without them.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
from typing import Optional

from .tts_settings import TTSSettings

log = logging.getLogger("eva.response.backends")


class TTSBackend:
    name = "base"

    def speak(self, text: str, stop_event: threading.Event) -> None:
        raise NotImplementedError

    def interrupt(self) -> None:
        """Called from another thread to cut the sound immediately."""


class _MciPlayer:
    """Plays an mp3 on Windows with no extra library (winmm MCI)."""

    def __init__(self) -> None:
        self._alias = f"eva_{id(self)}"

    def _send(self, command: str) -> str:
        import ctypes

        buf = ctypes.create_unicode_buffer(255)
        err = ctypes.windll.winmm.mciSendStringW(command, buf, 254, 0)  # type: ignore[attr-defined]
        if err:
            raise RuntimeError(f"MCI error {err} for: {command}")
        return buf.value

    def play(self, path: str, stop_event: threading.Event, max_seconds: float = 180.0) -> None:
        a = self._alias
        self._send(f'open "{path}" type mpegvideo alias {a}')
        try:
            self._send(f"play {a}")
            started = time.monotonic()
            while not stop_event.is_set():
                if time.monotonic() - started > max_seconds:
                    break
                if self._send(f"status {a} mode") != "playing":
                    break
                time.sleep(0.05)
        finally:
            for cmd in (f"stop {a}", f"close {a}"):
                try:
                    self._send(cmd)
                except Exception:  # noqa: BLE001
                    pass


class EdgeTTSBackend(TTSBackend):
    name = "edge-tts"

    def __init__(self, settings: TTSSettings) -> None:
        self._s = settings

    async def _synthesize(self, text: str, path: str) -> None:
        import edge_tts

        comm = edge_tts.Communicate(
            text,
            self._s.edge_voice,
            rate=self._s.edge_rate,
            volume=self._s.edge_volume,
        )
        await comm.save(path)

    def speak(self, text: str, stop_event: threading.Event) -> None:
        fd, path = tempfile.mkstemp(prefix="eva_tts_", suffix=".mp3")
        os.close(fd)
        try:
            asyncio.run(
                asyncio.wait_for(self._synthesize(text, path), self._s.edge_timeout_s)
            )
            if stop_event.is_set():
                return
            if os.path.getsize(path) == 0:
                raise RuntimeError("edge-tts produced an empty file")
            _MciPlayer().play(path, stop_event)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


class Pyttsx3Backend(TTSBackend):
    name = "pyttsx3"

    def __init__(self, settings: TTSSettings) -> None:
        self._s = settings
        self._engine: Optional[object] = None

    def _pick_voice(self, engine) -> None:
        hint = self._s.fallback_voice_hint.lower()
        for v in engine.getProperty("voices"):
            if hint in (v.name or "").lower():
                engine.setProperty("voice", v.id)
                return
        log.warning("fallback voice containing %r not found, using default", hint)

    def speak(self, text: str, stop_event: threading.Event) -> None:
        if stop_event.is_set():
            return
        import pyttsx3

        engine = pyttsx3.init()  # new engine per sentence avoids the known hang
        self._engine = engine
        try:
            self._pick_voice(engine)
            engine.setProperty("rate", self._s.fallback_rate)
            engine.say(text)
            engine.runAndWait()
        finally:
            self._engine = None
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass

    def interrupt(self) -> None:
        eng = self._engine
        if eng is not None:
            try:
                eng.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
