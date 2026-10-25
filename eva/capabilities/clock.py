"""Tell the time and/or the date. Changes nothing, so there is nothing to verify."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from eva.capabilities.text_match import normalize
from eva.core.capability import Capability, CapabilityResult, Risk
from eva.common.types import TaskOutcome
from eva.understanding.request import RequestObject

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]

_PREFIX = r"^(?:please )*(?:(?:can|could|would) you )?(?:please )?(?:tell me )?"
_SUFFIX = r"(?: (?:please|right now|now|today|thanks))*$"
# The WHOLE sentence must be about the time or date ("what is the time complexity" must not match).
_BOTH = re.compile(_PREFIX + r"(?:(?:what is|whats) )?(?:the |todays )?(?:current )?"
                   r"(?:date and time|time and date)" + _SUFFIX)
_TIME = re.compile(_PREFIX + r"(?:what time is it|(?:what is|whats) the (?:current )?time|"
                   r"the (?:current )?time|current time)" + _SUFFIX)
_DATE = re.compile(_PREFIX + r"(?:what day is it|(?:what is|whats) (?:the date|todays date|today)|"
                   r"the date|todays date|(?:the )?current date)" + _SUFFIX)


def format_time(now: datetime) -> str:
    hour = now.hour % 12 or 12
    return f"{hour}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}"


def format_date(now: datetime) -> str:
    return f"{_DAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]} {now.year}"


class ClockCapability(Capability):
    name = "clock"
    description = "Tell the time and/or the date."
    risk = Risk.LOW
    timeout_s = 3.0

    def __init__(self, now_fn: Callable[[], datetime] = datetime.now) -> None:
        self._now = now_fn

    def match(self, request: RequestObject) -> dict | None:
        text = normalize(request.clean_text)
        if _BOTH.match(text):
            return {"time": True, "date": True}
        if _TIME.match(text):
            return {"time": True, "date": False}
        if _DATE.match(text):
            return {"time": False, "date": True}
        return None

    def execute(self, args: dict) -> CapabilityResult:
        now = self._now()
        if args["time"] and args["date"]:
            message = f"It's {format_time(now)} on {format_date(now)}."
        elif args["time"]:
            message = f"It's {format_time(now)}."
        else:
            message = f"Today is {format_date(now)}."
        return CapabilityResult(TaskOutcome.SUCCESS, message)
