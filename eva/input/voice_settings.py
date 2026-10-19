"""All voice-input settings in one place. Change a value here, not inside the code."""
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceSettings:
    # Hotkey: press once to start listening, press again to cancel.
    hotkey: str = "ctrl+alt+e"

    # Speech-to-text (faster-whisper). CPU + int8 + small model = cool laptop.
    model_name: str = "base.en"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = "en"
    preload_model: bool = True      # load the model in the background at start-up

    # Microphone.
    sample_rate: int = 16000
    mic_device: int | str | None = None   # None = Windows default microphone

    # When to stop recording.
    silence_seconds: float = 1.2        # quiet for this long after speech -> stop
    max_record_seconds: float = 15.0    # hard stop
    no_speech_timeout: float = 5.0      # nobody spoke -> give up
    speech_threshold: float = 0.004     # loudness that counts as speech (raise if noisy room)
    min_speech_seconds: float = 0.25    # shorter sounds (clicks, coughs) are ignored
