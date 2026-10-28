"""Web capabilities (Stage 6a): open_url and web_search.

Both only LAUNCH the browser. EVA cannot confirm that the page finished loading,
so SUCCESS means "browser was started with a safe address" and nothing more.
Core should keep wording honest ("Opening ...", not "Opened ...").

No shell is used: Chrome is started with an argument list, never a command string.
"""
from __future__ import annotations

import os
import re
import subprocess
import webbrowser
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import quote_plus, urlparse

from .url_safety import UnsafeUrl, normalize_url

MAX_QUERY_CHARS = 200
SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q=",
    "bing": "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class WebActionResult:
    outcome: str          # "SUCCESS" or "FAILED"
    message: str          # safe to speak
    detail: str = ""      # for logs only
    url: str = ""


def _chrome_path() -> Optional[str]:
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.isfile(p):
                return p
    return None


def open_in_browser(url: str, prefer_chrome: bool = True) -> bool:
    """Start the browser. Returns True if it was started (not that the page loaded)."""
    try:
        if prefer_chrome:
            chrome = _chrome_path()
            if chrome:
                subprocess.Popen([chrome, url], shell=False)
                return True
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


def build_youtube_search_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(query)


def _clean_query(query: str) -> str:
    if not isinstance(query, str):
        raise UnsafeUrl("search words must be text")
    q = " ".join(query.split())
    if not q:
        raise UnsafeUrl("empty search")
    if len(q) > MAX_QUERY_CHARS:
        raise UnsafeUrl("search too long")
    if _CONTROL.search(q):
        raise UnsafeUrl("search has control characters")
    return q


def open_url(url: str, opener: Optional[Callable[[str], bool]] = None) -> WebActionResult:
    opener = opener or open_in_browser
    try:
        safe = normalize_url(url)
    except UnsafeUrl as exc:
        return WebActionResult("FAILED", "I cannot open that address safely.", detail=str(exc))
    host = urlparse(safe).hostname or "the website"
    if opener(safe):
        return WebActionResult("SUCCESS", f"Opening {host}.", url=safe)
    return WebActionResult("FAILED", "I could not start the browser.", detail="opener returned False", url=safe)


def web_search(query: str, engine: Optional[str] = None,
               opener: Optional[Callable[[str], bool]] = None) -> WebActionResult:
    opener = opener or open_in_browser
    try:
        q = _clean_query(query)
        name = (engine or os.environ.get("EVA_SEARCH_ENGINE", "google")).strip().lower()
        base = SEARCH_ENGINES.get(name, SEARCH_ENGINES["google"])
        safe = normalize_url(base + quote_plus(q))
    except UnsafeUrl as exc:
        return WebActionResult("FAILED", "I cannot search for that.", detail=str(exc))
    spoken = q if len(q) <= 60 else q[:57] + "..."
    if opener(safe):
        return WebActionResult("SUCCESS", f"Searching for {spoken}.", url=safe)
    return WebActionResult("FAILED", "I could not start the browser.", detail="opener returned False", url=safe)


from eva.common.types import TaskOutcome
from eva.core.capability import Capability, CapabilityResult, Risk
from eva.understanding.request import RequestObject


class OpenUrlCapability(Capability):
    name = "open_url"
    description = "Open a website in the browser. Needs the full https address"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, opener: Optional[Callable[[str], bool]] = None) -> None:
        self._opener = opener

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_web import parse_web_command
        m = parse_web_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        res = open_url(args.get("url", ""), opener=self._opener)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail, "url": res.url})


class WebSearchCapability(Capability):
    name = "web_search"
    description = "Search the web in the browser for some words"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, engine: Optional[str] = None, opener: Optional[Callable[[str], bool]] = None) -> None:
        self._engine = engine
        self._opener = opener

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_web import parse_web_command
        m = parse_web_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        res = web_search(args.get("query", ""), engine=self._engine, opener=self._opener)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail, "url": res.url})
