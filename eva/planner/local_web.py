"""Local (no LLM) understanding of simple web commands.

Handles: "search for X", "google X", "look up X", "search X on youtube",
         "go to github.com", "open youtube", "visit youtube dot com".
Returns None for anything else, including multi-step requests like
"search for X and open youtube" (those go to the Planner).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from eva.capabilities.web_actions import MAX_QUERY_CHARS, build_youtube_search_url

# Well-known sites that can be opened by name. (No bare "maps"/"drive": they clash with apps.)
SITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
    "google": "https://www.google.com", "github": "https://github.com",
    "linkedin": "https://www.linkedin.com", "google maps": "https://maps.google.com",
    "google drive": "https://drive.google.com", "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com", "chatgpt": "https://chatgpt.com",
    "whatsapp web": "https://web.whatsapp.com",
}
# Only these endings count as a website. This keeps "open notes.txt" or "open app.py"
# away from the web (those belong to open_file).
_TLDS = {"com", "org", "net", "edu", "gov", "in", "io", "ai", "co", "dev", "app", "me", "info",
         "tv", "uk", "us", "ac", "xyz", "tech", "online", "site", "store", "blog", "news"}

_MULTI = re.compile(r"\b(?:and|then|after that)\s+(?:open|go to|search|visit|google|look up)\b")
_SEARCH = re.compile(r"^(?:search|google|look up)(?:\s+(?:for|up))?\s+(.+)$")
_OPEN = re.compile(r"^(?:open|go to|goto|visit|launch|take me to)\s+(?:the\s+)?(?:website\s+|site\s+)?"
                   r"(.+?)(?:\s+(?:website|web site|site|page))?$")
_DOMAIN = re.compile(r"^(?:https?://)?((?:[a-z0-9-]+\.)+([a-z]{2,24}))(?::\d+)?(?:/\S*)?$")
_ON_YT = re.compile(r"\s+on\s+youtube$")
_ON_OTHER = re.compile(r"\s+(?:on|in|using)\s+(?:google|chrome|the web|the internet|web)$|\s+online$")


@dataclass(frozen=True)
class LocalWebMatch:
    capability: str            # "open_url" or "web_search"
    args: Dict[str, Any]
    spoken: str                # short line EVA can say while it works


def _clean(text: str) -> str:
    t = " ".join((text or "").lower().split()).strip(" .,!?")
    t = re.sub(r"^(?:please|eva)[, ]+", "", t)
    t = re.sub(r"[, ]+please$", "", t)
    return t.strip()


def parse_web_command(text: str) -> Optional[LocalWebMatch]:
    t = _clean(text)
    if not t or _MULTI.search(t):
        return None

    m = _SEARCH.match(t)
    if m:
        q = m.group(1).strip()
        on_yt = bool(_ON_YT.search(q))
        q = _ON_YT.sub("", q) if on_yt else _ON_OTHER.sub("", q)
        q = q.strip()
        if not q or len(q) > MAX_QUERY_CHARS:
            return None
        if on_yt:
            return LocalWebMatch("open_url", {"url": build_youtube_search_url(q)},
                                 f"Searching YouTube for {q}.")
        return LocalWebMatch("web_search", {"query": q}, f"Searching for {q}.")

    m = _OPEN.match(t)
    if m:
        target = re.sub(r"\s+dot\s+", ".", m.group(1).strip())
        if target in SITES:
            return LocalWebMatch("open_url", {"url": SITES[target]}, f"Opening {target}.")
        d = _DOMAIN.match(target)
        if d and d.group(2) in _TLDS:
            return LocalWebMatch("open_url", {"url": target}, f"Opening {d.group(1)}.")
    return None
