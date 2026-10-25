"""Tiny helpers so spoken sentences can be matched reliably."""
import re

_KEEP = re.compile(r"[^a-z0-9 ]")


def normalize(text: str) -> str:
    """'  Open the Notepad, please!  ' -> 'open the notepad please'
    Apostrophes are dropped (what's -> whats), other punctuation becomes nothing."""
    t = (text or "").lower().replace("\u2019", "").replace("'", "")
    t = _KEEP.sub(" ", t)
    return " ".join(t.split())
