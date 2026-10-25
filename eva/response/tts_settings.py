"""EVA voice OUTPUT settings. Change values here, not inside speaker.py."""
from dataclasses import dataclass


@dataclass
class TTSSettings:
    enabled: bool = True

    # Main voice (online, edge-tts)
    edge_voice: str = "en-IN-NeerjaNeural"
    edge_rate: str = "+0%"        # example: "+10%" faster, "-10%" slower
    edge_volume: str = "+0%"
    edge_timeout_s: float = 8.0   # if synthesis takes longer, use fallback

    # Fallback voice (offline, pyttsx3 / Windows SAPI5)
    fallback_voice_hint: str = "zira"   # matched inside the voice name
    fallback_rate: int = 175

    # If the main voice fails (for example no internet), skip it for this
    # many seconds so EVA does not wait 8 seconds on every sentence.
    retry_main_after_s: float = 60.0

    # Long answers are not read fully. The rest stays on screen.
    max_spoken_chars: int = 600
