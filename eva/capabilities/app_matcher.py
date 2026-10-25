"""Pure logic: which installed app did the person mean?  No Windows, no files.

Score (0..1) of a spoken name against an app name:
  1.00 same words | 0.97 same letters ("note pad" = "notepad") |
  0.95/0.92 the spoken words sit inside the app name (shorter app names win) |
  fuzzy spelling (x0.95) for small speech-to-text mistakes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

from eva.capabilities.app_index import AppEntry
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.text_match import normalize

# Words people say that differ from the real app name. Add your own in app_aliases.json.
DEFAULT_ALIASES: dict[str, str] = {
    "chrome": "google chrome",
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "visual studio": "visual studio code",
    "calc": "calculator",
    "files": "file explorer",
    "my files": "file explorer",
    "explorer": "file explorer",
    "windows explorer": "file explorer",
    "power point": "powerpoint",
}


class Kind(str, Enum):
    FOUND = "FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    BLOCKED = "BLOCKED"
    NONE = "NONE"


@dataclass(frozen=True)
class Match:
    entry: AppEntry
    score: float
    blocked: bool


@dataclass(frozen=True)
class Decision:
    kind: Kind
    matches: tuple[Match, ...] = ()


def score_name(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    qc, nc = q.replace(" ", ""), n.replace(" ", "")
    if qc == nc:
        return 0.97

    best = 0.0
    qw, nw = q.split(), n.split()
    k = len(qw)
    for i in range(len(nw) - k + 1):
        if nw[i:i + k] == qw:
            base = 0.95 if i == 0 else 0.92
            best = max(best, base - 0.04 * (len(nw) - k))
            break
    if len(qc) >= 3:
        best = max(best, SequenceMatcher(None, qc, nc).ratio() * 0.95)
    return best


def is_blocked(name: str, blocklist: tuple[str, ...]) -> bool:
    n = " " + normalize(name) + " "
    return any(f" {normalize(b)} " in n for b in blocklist)


def rank(query: str, entries: list[AppEntry], aliases: dict[str, str],
         blocklist: tuple[str, ...]) -> list[Match]:
    q = normalize(query)
    queries = [q]
    if q in aliases:
        queries.insert(0, normalize(aliases[q]))
    matches = []
    for entry in entries:
        score = max(score_name(candidate, entry.name) for candidate in queries)
        if score > 0.5:
            matches.append(Match(entry, round(score, 3), is_blocked(entry.name, blocklist)))
    matches.sort(key=lambda m: (-m.score, len(m.entry.name.split()), m.entry.name.lower()))
    return matches


def decide(matches: list[Match], settings: AppSettings) -> Decision:
    good = [m for m in matches if m.score >= settings.min_score]
    if not good:
        return Decision(Kind.NONE)
    top = good[0]
    if top.blocked:
        return Decision(Kind.BLOCKED, (top,))
    close = [m for m in good if not m.blocked and top.score - m.score < settings.ambiguity_gap]
    if len(close) > 1:
        return Decision(Kind.AMBIGUOUS, tuple(close[:3]))
    return Decision(Kind.FOUND, (top,))


# "open notepad", "please open the calculator app", "can you launch spotify please"
_OPEN = re.compile(
    r"^(?:please )?(?:(?:can|could|would) you )?(?:please )?"
    r"(?:open|launch|start)(?: up)? (?:the |my )?(?P<target>[a-z0-9 ]+?)"
    r"(?: (?:app|application|program))?(?: (?:please|thanks|thank you))*$"
)


def extract_target(text: str) -> str | None:
    found = _OPEN.match(normalize(text))
    return found.group("target").strip() if found else None
