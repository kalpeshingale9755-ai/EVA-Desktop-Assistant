"""Clean text so it sounds natural when spoken."""
import re

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_LINE_MARKS = re.compile(r"^\s*(?:#{1,6}\s+|>\s+|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_EMPHASIS = re.compile(r"[*_~|]+")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\uFE0F\u200d]"
)
_SPACES = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

TRUNCATED_NOTE = " The rest is on screen."


def prepare_for_speech(text: str, max_chars: int = 600) -> str:
    if not text:
        return ""
    t = _CODE_BLOCK.sub(" code block omitted. ", text)
    t = _INLINE_CODE.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _URL.sub("link", t)
    t = _LINE_MARKS.sub("", t)
    t = _EMPHASIS.sub(" ", t)
    t = _EMOJI.sub("", t)
    t = _SPACES.sub(" ", t).strip()

    if max_chars and len(t) > max_chars:
        cut = t[:max_chars]
        ends = [m.end() for m in _SENTENCE_END.finditer(cut)]
        # cut at the last full sentence if one exists in the second half
        if ends and ends[-1] > max_chars // 2:
            cut = cut[: ends[-1]]
        else:
            cut = cut.rsplit(" ", 1)[0]
        t = cut.strip() + TRUNCATED_NOTE
    return t
