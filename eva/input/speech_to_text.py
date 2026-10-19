"""Speech-to-text with faster-whisper. The model loads once and is reused."""
import math
import threading
import time
from dataclasses import dataclass

from eva.common.logging_setup import get_logger
from eva.input.voice_settings import VoiceSettings

log = get_logger("eva.stt")

_NO_SPEECH_LIMIT = 0.6   # skip segments the model thinks are not speech


class SttError(Exception):
    """The speech model is missing, could not load, or failed."""


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None   # 0.0 to 1.0, None if unknown


class SpeechToText:
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
        started = time.perf_counter()
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
        log.info("Transcribed in %.1fs | confidence=%s | text=%r",
                 time.perf_counter() - started, confidence, text)
        return Transcript(text=text, confidence=confidence)
