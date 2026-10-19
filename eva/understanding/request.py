"""The Structured Request Object: the clean, standard form of anything the user says or types."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from eva.common.types import Mode


class InputSource(str, Enum):
    TEXT = "TEXT"
    VOICE = "VOICE"


@dataclass(frozen=True)
class RequestObject:
    raw_text: str                  # exactly what came in
    clean_text: str                # tidied up, wake phrase removed
    source: InputSource            # TEXT or VOICE
    mode: Mode                     # COMMAND_MODE if it started with "Hey EVA", else CONVERSATION_MODE
    confidence: float | None = None  # speech-to-text confidence (None for typed text)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: datetime = field(default_factory=datetime.now)
