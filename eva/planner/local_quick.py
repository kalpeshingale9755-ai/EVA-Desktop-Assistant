"""Local (no LLM) understanding of the quick actions.

Only short, clear commands match. Anything longer, with numbers, or with a second
request ("take a screenshot and open youtube") returns None and goes to the Planner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class LocalQuickMatch:
    capability: str
    args: Dict[str, Any]
    spoken: str


_W = r"what(?:'s|s| is)"
_RULES: List[Tuple[str, List[str]]] = [
    ("tell_time", [
        rf"^{_W} the (?:current )?time(?: now| right now)?$",
        r"^what time is it(?: now| right now)?$",
        r"^(?:tell me|say|give me) the (?:current )?time$",
        r"^(?:the )?current time$", r"^time now$",
    ]),
    ("tell_date", [
        rf"^{_W} (?:the |today'?s )?date(?: today)?$",
        rf"^{_W} today(?:'?s date)?$",
        r"^what day is (?:it|today)(?: today)?$",
        r"^(?:tell me|say|give me) (?:the |today'?s )?date$",
        r"^today'?s date$",
    ]),
    ("battery_status", [
        rf"^{_W} (?:the |my )?(?:current )?battery(?: level| status| percentage| percent)?$",
        r"^(?:check|show|tell me) (?:the |my )?battery(?: level| status| percentage)?$",
        r"^battery(?: level| status| percentage| percent)?$",
        r"^how much battery (?:is )?(?:left|remaining|do i have)$",
        r"^is (?:my |the )?(?:laptop|battery) charging$",
    ]),
    ("volume_up", [
        r"^volume up$", r"^(?:increase|raise) (?:the )?(?:system )?volume$",
        r"^turn (?:up (?:the )?volume|(?:the )?volume up)$", r"^(?:make it )?louder$",
        r"^(?:a bit |a little )?(?:more )?volume up$",
    ]),
    ("volume_down", [
        r"^volume down$", r"^(?:decrease|lower|reduce) (?:the )?(?:system )?volume$",
        r"^turn (?:down (?:the )?volume|(?:the )?volume down)$", r"^(?:make it )?quieter$",
    ]),
    ("volume_mute", [
        r"^(?:mute|unmute|toggle mute)(?: the)?(?: volume| sound| audio| laptop| system)?$",
    ]),
    ("take_screenshot", [
        r"^(?:take|capture|grab) (?:a |the )?(?:screenshot|screen shot|screen capture)$",
        r"^(?:take|capture|grab) (?:a )?(?:snapshot|picture|photo) of (?:the |my )?screen$",
        r"^(?:screenshot|screen shot)$",
    ]),
]
_COMPILED = [(cap, [re.compile(p) for p in pats]) for cap, pats in _RULES]
_SPOKEN = {
    "tell_time": "Checking the time.", "tell_date": "Checking the date.",
    "battery_status": "Checking the battery.", "volume_up": "Turning the volume up.",
    "volume_down": "Turning the volume down.", "volume_mute": "Toggling mute.",
    "take_screenshot": "Taking a screenshot.",
}


def _clean(text: str) -> str:
    t = " ".join((text or "").lower().replace("’", "'").split()).strip(" .,!?")
    t = re.sub(r"^(?:please|eva|hey eva)[, ]+", "", t)
    t = re.sub(r"[, ]+please$", "", t)
    return t.strip()


def parse_quick_command(text: str) -> Optional[LocalQuickMatch]:
    t = _clean(text)
    if not t or re.search(r"\d", t):
        return None
    for cap, patterns in _COMPILED:
        if any(p.match(t) for p in patterns):
            return LocalQuickMatch(cap, {}, _SPOKEN[cap])
    return None
