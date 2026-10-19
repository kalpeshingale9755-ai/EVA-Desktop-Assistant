"""Understanding System: raw text in -> Structured Request Object out.

The rule (simple on purpose, reliability first, no LLM here):
  - Starts with "Hey EVA"  -> COMMAND_MODE  (the wake phrase is removed)
  - Anything else          -> CONVERSATION_MODE
"""
import re

from eva.common.types import Mode
from eva.understanding.request import InputSource, RequestObject

# "Hey EVA", "hey, eva", "Hey! Eva" at the start of the sentence
_WAKE_PHRASE = re.compile(r"^hey[\s,.!\-]+eva\b[\s,.:;!?\-]*", re.IGNORECASE)


def understand(
    raw_text: str,
    source: InputSource,
    confidence: float | None = None,
) -> RequestObject | None:
    """Return a RequestObject, or None if there is nothing to act on."""
    if not raw_text or not raw_text.strip():
        return None

    text = " ".join(raw_text.split())  # fix spaces, tabs, new lines

    if _WAKE_PHRASE.match(text):
        mode = Mode.COMMAND_MODE
        text = _WAKE_PHRASE.sub("", text).strip()
        if not text:  # the user only said "Hey EVA"
            return None
    else:
        mode = Mode.CONVERSATION_MODE

    return RequestObject(
        raw_text=raw_text,
        clean_text=text,
        source=source,
        mode=mode,
        confidence=confidence,
    )
