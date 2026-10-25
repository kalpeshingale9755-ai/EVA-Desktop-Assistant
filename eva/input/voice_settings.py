"""All voice-input settings in one place. Change a value here, not inside the code."""
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceSettings:
    # Hotkey: press once to start listening, press again to cancel.
    hotkey: str = "ctrl+alt+e"

    # ---- Speech-to-text: which engine is the main one ----------------------
    # "google"  = SpeechRecognition + recognize_google() (needs internet)
    # "whisper" = faster-whisper (offline)
    stt_engine: str = "google"

    # Google settings
    google_language: str = "en-IN"      # Indian English. Use "hi-IN" for Hindi.
    google_timeout_s: float = 8.0       # wait this long, then treat Google as failed
    google_normalize: bool = True       # make quiet recordings louder before sending
    fallback_to_whisper: bool = True    # if Google cannot be reached, use offline Whisper
    retry_google_after_s: float = 30.0  # after a failure, skip Google for this long

    # Whisper settings (offline fallback). CPU + int8 + small model = cool laptop.
    model_name: str = "base.en"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = "en"
    preload_model: bool = True      # preload Whisper at start-up (only if Whisper is the main engine)

    # After you say only the wake word ("Eva"): EVA answers, then listens for the command.
    ack_text: str = "Yes boss."
    ack_settle_ms: int = 200            # pause after her voice ends, so her own voice is not recorded
    ack_fallback_ms: int = 10000        # if her voice never reports "finished", listen anyway after this

    # Microphone.
    sample_rate: int = 16000
    mic_device: int | str | None = None   # None = Windows default microphone

    # When to stop recording.
    silence_seconds: float = 0.8        # quiet for this long after speech -> stop
    max_record_seconds: float = 15.0    # hard stop
    no_speech_timeout: float = 5.0      # nobody spoke -> give up
    speech_threshold: float = 0.005     # loudness that counts as speech (raise if noisy room)
    min_speech_seconds: float = 0.25    # shorter sounds (clicks, coughs) are ignored
