"""EVA Core settings. Change a value here, not inside the code."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CoreSettings:
    # A spoken command with a lower speech-to-text confidence than this is NOT run.
    # EVA asks you to say it again. Typed text has no confidence, so it always passes.
    min_command_confidence: float = 0.4
