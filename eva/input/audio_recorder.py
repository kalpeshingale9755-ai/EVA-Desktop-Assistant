"""Records from the microphone until the person stops talking."""
import queue
import threading
import time
from dataclasses import dataclass
from enum import Enum

from eva.common.logging_setup import get_logger
from eva.input.voice_logic import EndpointDetector, Verdict
from eva.input.voice_settings import VoiceSettings

log = get_logger("eva.recorder")

_CHUNK_SECONDS = 0.05


class MicError(Exception):
    """The microphone is missing, busy, or not giving any sound."""


class RecordOutcome(str, Enum):
    OK = "OK"
    NO_SPEECH = "NO_SPEECH"
    CANCELLED = "CANCELLED"


@dataclass
class RecordResult:
    outcome: RecordOutcome
    audio: object = None      # numpy float32 array, 16 kHz mono (None unless OK)
    seconds: float = 0.0
    peak_level: float = 0.0


def record_until_silence(settings: VoiceSettings, cancel: threading.Event) -> RecordResult:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise MicError("Missing packages. Run: pip install sounddevice numpy") from exc

    rate = settings.sample_rate
    audio_queue: "queue.Queue" = queue.Queue()

    def on_audio(indata, frames, time_info, status) -> None:  # runs on the audio thread
        audio_queue.put(indata[:, 0].copy())

    detector = EndpointDetector(
        threshold=settings.speech_threshold,
        silence_seconds=settings.silence_seconds,
        max_seconds=settings.max_record_seconds,
        no_speech_timeout=settings.no_speech_timeout,
        min_speech_seconds=settings.min_speech_seconds,
    )

    try:
        stream = sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            blocksize=int(rate * _CHUNK_SECONDS),
            device=settings.mic_device,
            callback=on_audio,
        )
        stream.start()
    except (sd.PortAudioError, ValueError) as exc:
        raise MicError(str(exc)) from exc

    chunks = []
    started = time.monotonic()
    last_audio = started
    try:
        while True:
            if cancel.is_set():
                return RecordResult(RecordOutcome.CANCELLED)
            try:
                chunk = audio_queue.get(timeout=0.2)
            except queue.Empty:
                if time.monotonic() - last_audio > 2.0:
                    raise MicError("The microphone is not sending any audio.")
                continue
            last_audio = time.monotonic()
            chunks.append(chunk)

            level = float(np.sqrt(np.mean(chunk ** 2)))
            verdict = detector.feed(level, len(chunk) / rate)
            if verdict is Verdict.CONTINUE:
                continue

            seconds = time.monotonic() - started
            log.info("Recording ended: %s | %.1fs | loudest level %.4f (threshold %.4f)",
                     verdict.value, seconds, detector.peak, settings.speech_threshold)
            if verdict is Verdict.NO_SPEECH:
                return RecordResult(RecordOutcome.NO_SPEECH, seconds=seconds,
                                    peak_level=detector.peak)
            return RecordResult(RecordOutcome.OK, audio=np.concatenate(chunks),
                                seconds=seconds, peak_level=detector.peak)
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:  # never let cleanup crash EVA
            log.exception("Could not close the microphone cleanly")
