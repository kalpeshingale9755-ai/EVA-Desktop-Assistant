"""Pure logic for voice input. No microphone, no Qt, no AI model: easy to test."""
import re
from enum import Enum


class Verdict(str, Enum):
    CONTINUE = "CONTINUE"    # keep recording
    DONE = "DONE"            # the person finished speaking
    NO_SPEECH = "NO_SPEECH"  # nobody spoke, give up


class EndpointDetector:
    """Decides when to stop recording. Feed it one loudness number per small audio chunk."""

    def __init__(
        self,
        threshold: float,
        silence_seconds: float,
        max_seconds: float,
        no_speech_timeout: float,
        min_speech_seconds: float,
    ) -> None:
        self.threshold = threshold
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.no_speech_timeout = no_speech_timeout
        self.min_speech_seconds = min_speech_seconds

        self.elapsed = 0.0
        self.speech_seconds = 0.0
        self.silence_run = 0.0
        self.in_speech = False
        self.peak = 0.0   # loudest chunk seen (useful for tuning the threshold)

    def feed(self, level: float, seconds: float) -> Verdict:
        self.elapsed += seconds
        self.peak = max(self.peak, level)

        if level >= self.threshold:
            self.speech_seconds += seconds
            self.silence_run = 0.0
            if self.speech_seconds >= self.min_speech_seconds:
                self.in_speech = True
        elif self.in_speech:
            self.silence_run += seconds
            if self.silence_run >= self.silence_seconds - 1e-9:
                return Verdict.DONE

        if self.elapsed >= self.max_seconds - 1e-9:
            return Verdict.DONE if self.in_speech else Verdict.NO_SPEECH
        if not self.in_speech and self.elapsed >= self.no_speech_timeout - 1e-9:
            return Verdict.NO_SPEECH
        return Verdict.CONTINUE


# Sounds that speech models "hear" in silence or noise. Not a real command.
_JUNK_WORDS = {"you", "uh", "um", "hmm", "mm", "ah", "oh", "huh", "the", "so"}


def is_junk(text: str | None) -> bool:
    """True if the transcript is empty or just noise like '.' or 'you'."""
    cleaned = re.sub(r"[^\w\s]|_", "", (text or "").lower()).strip()
    return cleaned == "" or cleaned in _JUNK_WORDS


_MODIFIERS = {"ctrl", "alt", "shift", "cmd"}
_ALIASES = {"control": "ctrl", "win": "cmd", "windows": "cmd", "escape": "esc"}


def to_pynput_combo(combo: str) -> str:
    """'ctrl+alt+e' -> '<ctrl>+<alt>+e' (the format the hotkey library wants)."""
    parts = [_ALIASES.get(p.strip().lower(), p.strip().lower())
             for p in combo.split("+") if p.strip()]
    if len(parts) < 2 or not any(p in _MODIFIERS for p in parts):
        raise ValueError(f"Hotkey '{combo}' needs a modifier and a key, like ctrl+alt+e")
    return "+".join(f"<{p}>" if len(p) > 1 else p for p in parts)
