"""Speech-to-text.

Main engine : Google (SpeechRecognition.recognize_google), needs internet.
Backup      : faster-whisper, works offline. The model loads once and is reused.

voice_service.py still uses  SpeechToText(settings).transcribe(audio)  exactly as before.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from eva.common.logging_setup import get_logger
from eva.input.voice_settings import VoiceSettings

log = get_logger("eva.stt")

_NO_SPEECH_LIMIT = 0.6   # skip Whisper segments the model thinks are not speech


class SttError(Exception):
    """A speech engine is missing, could not load, could not be reached, or failed."""


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None   # 0.0 to 1.0, None if unknown


# ---------------------------------------------------------------------------
# Small pure helpers (easy to test)
# ---------------------------------------------------------------------------
def audio_to_pcm16(audio, normalize: bool = True,
                   target_peak: float = 0.8, max_gain: float = 8.0) -> bytes:
    """float32 audio (-1..1) -> 16-bit PCM bytes. Quiet audio is made louder
    (at most 8x) so Google can hear a quiet microphone."""
    import numpy as np

    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    if normalize and a.size:
        peak = float(np.max(np.abs(a)))
        if 0.0 < peak < target_peak:
            a = a * min(target_peak / peak, max_gain)
    a = np.clip(a, -1.0, 1.0)
    return (a * 32767).astype("<i2").tobytes()


def parse_google_result(result) -> Transcript:
    """recognize_google(show_all=True) gives a dict, or an empty list if nothing was understood."""
    if isinstance(result, str):
        return Transcript(result.strip(), None)
    if not result or not isinstance(result, dict):
        return Transcript("", None)
    alternatives = result.get("alternative") or []
    if not alternatives:
        return Transcript("", None)
    best = alternatives[0]
    text = str(best.get("transcript", "")).strip()
    conf = best.get("confidence")
    try:
        conf = round(max(0.0, min(1.0, float(conf))), 2) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    return Transcript(text, conf)


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------
class GoogleEngine:
    name = "google"

    def __init__(self, settings: VoiceSettings, sr_module=None) -> None:
        self._settings = settings
        self._sr = sr_module          # tests can pass a fake module

    def _load_sr(self):
        if self._sr is None:
            try:
                import speech_recognition as sr
            except ImportError as exc:
                raise SttError("SpeechRecognition is not installed. "
                               "Run: pip install SpeechRecognition") from exc
            self._sr = sr
        return self._sr

    def transcribe(self, audio) -> Transcript:
        sr = self._load_sr()
        s = self._settings
        pcm = audio_to_pcm16(audio, normalize=s.google_normalize)
        data = sr.AudioData(pcm, s.sample_rate, 2)       # 2 bytes per sample = 16-bit
        recognizer = sr.Recognizer()
        recognizer.operation_timeout = s.google_timeout_s
        try:
            result = recognizer.recognize_google(data, language=s.google_language,
                                                 show_all=True)
        except sr.UnknownValueError:
            return Transcript("", None)                  # Google heard sound but no words
        except sr.RequestError as exc:
            raise SttError(f"Google speech could not be reached: {exc}") from exc
        except Exception as exc:
            raise SttError(f"Google speech failed: {exc}") from exc
        return parse_google_result(result)


class WhisperEngine:
    name = "whisper"

    def __init__(self, settings: VoiceSettings) -> None:
        self._settings = settings
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """Load the model once. Safe to call many times, from any thread."""
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SttError("faster-whisper is not installed. "
                               "Run: pip install faster-whisper") from exc

            started = time.perf_counter()
            try:
                self._model = WhisperModel(
                    self._settings.model_name,
                    device=self._settings.device,
                    compute_type=self._settings.compute_type,
                )
            except Exception as exc:
                raise SttError(
                    f"Could not load speech model '{self._settings.model_name}'. "
                    f"The first run needs internet to download it. ({exc})"
                ) from exc
            log.info("Speech model '%s' loaded in %.1fs",
                     self._settings.model_name, time.perf_counter() - started)

    def transcribe(self, audio) -> Transcript:
        self.load()
        try:
            segments, _info = self._model.transcribe(
                audio,
                language=self._settings.language,
                beam_size=1,                       # fastest, easy on the laptop
                condition_on_previous_text=False,
                vad_filter=False,
            )
            parts, scores = [], []
            for segment in segments:               # this is where the work really happens
                if segment.no_speech_prob > _NO_SPEECH_LIMIT:
                    continue
                parts.append(segment.text.strip())
                scores.append(math.exp(segment.avg_logprob))
        except Exception as exc:
            raise SttError(f"Transcription failed: {exc}") from exc

        text = " ".join(p for p in parts if p).strip()
        confidence = round(min(1.0, sum(scores) / len(scores)), 2) if scores else None
        return Transcript(text=text, confidence=confidence)


# ---------------------------------------------------------------------------
# The class the rest of EVA uses
# ---------------------------------------------------------------------------
class SpeechToText:
    def __init__(self, settings: VoiceSettings, google=None, whisper=None) -> None:
        self._settings = settings
        self._google = google or GoogleEngine(settings)
        self._whisper = whisper or WhisperEngine(settings)
        self._skip_google_until = 0.0
        self.last_engine: str | None = None

        engine = (settings.stt_engine or "google").lower()
        if engine not in ("google", "whisper"):
            log.warning("Unknown stt_engine %r, using 'google'", settings.stt_engine)
            engine = "google"
        self._main = engine

    @property
    def needs_preload(self) -> bool:
        """Only preload Whisper when it is the main engine (saves CPU and heat)."""
        return self._main == "whisper"

    def load(self) -> None:
        if self._main == "whisper":
            self._whisper.load()

    def transcribe(self, audio) -> Transcript:
        started = time.perf_counter()
        errors: list[str] = []
        for engine in self._engine_order():
            try:
                transcript = engine.transcribe(audio)
            except SttError as exc:
                errors.append(str(exc))
                log.warning("%s speech engine failed: %s", engine.name, exc)
                if engine is self._google:
                    self._skip_google_until = (time.monotonic()
                                               + self._settings.retry_google_after_s)
                continue
            self.last_engine = engine.name
            log.info("Transcribed by %s in %.1fs | confidence=%s | text=%r",
                     engine.name, time.perf_counter() - started,
                     transcript.confidence, transcript.text)
            return transcript
        raise SttError(self._friendly_error(errors))

    # ---- internals ----------------------------------------------------------
    def _engine_order(self) -> list:
        s = self._settings
        if self._main == "whisper":
            return [self._whisper]
        if time.monotonic() < self._skip_google_until:
            # Google failed a moment ago: go straight to the backup
            return [self._whisper] if s.fallback_to_whisper else [self._google]
        return [self._google, self._whisper] if s.fallback_to_whisper else [self._google]

    @staticmethod
    def _friendly_error(errors: list[str]) -> str:
        joined = " | ".join(errors) if errors else "no speech engine is available"
        return f"I couldn't turn your voice into text. {joined}"
