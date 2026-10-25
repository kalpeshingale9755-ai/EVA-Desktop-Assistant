"""Understanding System: raw text in -> Structured Request Object out.

The rule (simple on purpose, reliability first, no LLM here):
  - Starts with the wake word  -> COMMAND_MODE  (the wake word is removed)
        "EVA, open notepad"  "Hey EVA open notepad"  "ok eva what time is it"
  - Anything else              -> CONVERSATION_MODE
  - Right after "Yes boss" (force_command=True), the next sentence is a command with no wake word needed.
"""
import re

from eva.common.types import Mode
from eva.understanding.request import InputSource, RequestObject

# The wake word. Speech-to-text sometimes hears "Eva" as "Ava", "Eve" or "Evie", so those count too.
# To change it, edit ONLY this line.
WAKE_NAMES = ("eva", "ava", "eve", "evie")

_NAME = "|".join(WAKE_NAMES)
# [optional greeting] + name, at the start. The name must END there: "Eva's laptop", "Evan", "evaluate" do NOT count.
_WAKE_PHRASE = re.compile(
    rf"^(?:(?:hey|hi|hello|ok|okay)[\s,.!\-]+)?(?:{_NAME})(?![\w'\u2019])[\s,.:;!?\-]*",
    re.IGNORECASE,
)


def is_wake_only(raw_text: str) -> bool:
    """True if the person said ONLY the wake word ("Eva", "hey eva"). EVA then answers
    "Yes boss" and listens for the command."""
    if not raw_text or not raw_text.strip():
        return False
    text = " ".join(raw_text.split())
    return bool(_WAKE_PHRASE.match(text)) and not _WAKE_PHRASE.sub("", text).strip()


def understand(
    raw_text: str,
    source: InputSource,
    confidence: float | None = None,
    force_command: bool = False,
) -> RequestObject | None:
    """Return a RequestObject, or None if there is nothing to act on.

    force_command=True is used right after EVA said "Yes boss": whatever you say next is a
    command, with or without the wake word."""
    if not raw_text or not raw_text.strip():
        return None

    text = " ".join(raw_text.split())  # fix spaces, tabs, new lines

    if _WAKE_PHRASE.match(text):
        mode = Mode.COMMAND_MODE
        text = _WAKE_PHRASE.sub("", text).strip()
        if not text:  # the user only said the wake word
            return None
    elif force_command:
        mode = Mode.COMMAND_MODE
    else:
        mode = Mode.CONVERSATION_MODE

    return RequestObject(
        raw_text=raw_text,
        clean_text=text,
        source=source,
        mode=mode,
        confidence=confidence,
    )
