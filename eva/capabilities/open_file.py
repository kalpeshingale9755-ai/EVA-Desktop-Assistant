"""Open a file or folder by name.   "Eva, open my resume pdf"   "Eva, open downloads folder"

Safety:
  - Only files from EVA's own list (Documents, Downloads, Desktop and folders you add in file_settings.py).
  - Only SAFE TYPES (documents, sheets, slides, images, audio, video). Never programs, scripts or shortcuts.
  - The path is checked again just before opening: it must still exist and still sit inside your folders.
  - Two close matches -> EVA reads the names and does NOT guess.
  - Apps win: "open chrome" is the open-app capability's job, not this one's.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

from eva.capabilities.app_index import AppIndex
from eva.capabilities.app_matcher import Kind as AppKind, decide as decide_app, extract_target, rank as rank_apps
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.file_index import FileEntry, FileIndex
from eva.capabilities.file_matcher import (Kind, ParsedTarget, decide_files, parse_target, rank_files,
                                            spoken_labels)
from eva.capabilities.file_settings import FileSettings
from eva.capabilities.open_app import load_aliases
from eva.capabilities.text_match import normalize
from eva.common.logging_setup import get_logger
from eva.common.types import TaskOutcome
from eva.core.capability import Capability, CapabilityResult, Risk
from eva.understanding.request import RequestObject

log = get_logger("eva.cap.open_file")
_EXPLORER_CLASS = "CabinetWClass"


class OpenFileCapability(Capability):
    name = "open_file"
    description = "Open a file or folder from your own folders by name."
    risk = Risk.LOW
    timeout_s = 10.0
    verify_timeout_s = 12.0        # must be longer than settings.verify_seconds

    def __init__(self, index: FileIndex, probe, app_index: AppIndex | None = None,
                 app_settings: AppSettings | None = None, settings: FileSettings | None = None,
                 aliases: dict[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._index, self._probe, self._apps = index, probe, app_index
        self._app_settings = app_settings or AppSettings()
        self._settings = settings or index.settings
        self._aliases = aliases if aliases is not None else load_aliases(self._app_settings)
        self._sleep, self._clock = sleep, clock

    # ---- Core calls these -------------------------------------------------------
    def match(self, request: RequestObject) -> dict | None:
        target = extract_target(request.clean_text)
        if not target or len(target.split()) > self._settings.max_spoken_words:
            return None
        if self._is_an_app(target):
            return None                                   # the open-app capability handles it
        parsed = parse_target(target, self._index.root_names())
        if parsed is None:
            return None

        decision = self._decide(parsed)
        if decision.kind is Kind.NONE and self._index.quick_refresh():
            decision = self._decide(parsed)               # maybe it was downloaded a moment ago
        if decision.kind is Kind.NONE:
            self._index.note_miss()
            if not parsed.explicit:
                return None                               # not clearly a file: not ours
            return {"kind": "NOT_FOUND", "query": parsed.name, "want_dir": parsed.want_dir}

        entries = [m.entry for m in decision.matches]
        return {"kind": decision.kind.value, "query": parsed.name, "want_dir": parsed.want_dir,
                "items": [{"display": e.display, "name": e.name, "path": e.path,
                           "ext": e.ext, "is_dir": e.is_dir} for e in entries],
                "labels": spoken_labels(entries)}

    def execute(self, args: dict) -> CapabilityResult:
        kind = args["kind"]
        if kind == "NOT_FOUND":
            what = "folder" if args["want_dir"] else "file"
            return CapabilityResult(TaskOutcome.FAILED, f"I couldn't find a {what} called {args['query']}.")
        if kind == "AMBIGUOUS":
            labels = args["labels"]
            joined = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]
            return CapabilityResult(TaskOutcome.FAILED, f"I found {joined}. Please be more specific.")

        item = args["items"][0]
        problem = self._problem(item)
        if problem:
            log.warning("Refused to open %s: %s", item["path"], problem)
            return CapabilityResult(TaskOutcome.FAILED, problem)

        before = self._probe.visible_windows()
        before_fg, _ = self._probe.foreground_window()
        try:
            self._probe.open_path(item["path"])
        except OSError:
            log.exception("Could not open %s", item["path"])
            return CapabilityResult(TaskOutcome.FAILED, f"I couldn't open {item['display']}.")
        log.info("Opened %s | windows before=%d", item["path"], len(before))
        return CapabilityResult(TaskOutcome.SUCCESS, f"Opened {item['display']}.",
                                {"name": item["name"], "is_dir": item["is_dir"],
                                 "before": sorted(before), "before_fg": before_fg})

    def verify(self, args: dict, result: CapabilityResult) -> bool:
        """Wait for proof: a new window (best: its title has the file's name), or that window in front."""
        words = [w for w in normalize(result.details["name"]).split() if len(w) >= 3] \
            or [normalize(result.details["name"])]
        before = set(result.details["before"])
        before_fg = result.details["before_fg"]
        is_dir = result.details["is_dir"]
        deadline = self._clock() + self._settings.verify_seconds

        while True:
            new = {h: v for h, v in self._probe.visible_windows().items()
                   if h not in before and (is_dir or v[1] != _EXPLORER_CLASS)}
            if any(any(w in normalize(title) for w in words) for title, _cls in new.values()):
                log.info("Verified %s | evidence=title", result.details["name"])
                return True
            if new:
                log.info("Verified %s | evidence=new_window %s", result.details["name"],
                         [t for t, _ in new.values()])
                return True
            fg_handle, fg_title = self._probe.foreground_window()
            if fg_handle and fg_handle != before_fg and any(w in normalize(fg_title) for w in words):
                log.info("Verified %s | evidence=foreground", result.details["name"])
                return True
            if self._clock() >= deadline:
                log.warning("Could not verify %s | no new window in %.1fs",
                            result.details["name"], self._settings.verify_seconds)
                return False
            self._sleep(self._settings.poll_interval_s)

    # ---- helpers ----------------------------------------------------------------
    def _decide(self, parsed: ParsedTarget):
        return decide_files(rank_files(parsed, self._index.entries()), self._settings)

    def _is_an_app(self, target: str) -> bool:
        if self._apps is None:
            return False
        known = decide_app(rank_apps(target, self._apps.entries(), self._aliases,
                                     self._app_settings.blocklist), self._app_settings)
        return known.kind is not AppKind.NONE

    def _problem(self, item: dict) -> str | None:
        """Last check before anything is opened. Returns what to say if it must NOT be opened."""
        path = Path(item["path"])
        try:
            resolved = path.resolve()
        except OSError:
            return "I can't open that."
        roots = [r.resolve() for r in self._index.roots]
        if not any(resolved == r or r in resolved.parents for r in roots):
            return "I can only open files in your own folders."
        if item["is_dir"]:
            return None if path.is_dir() else "That folder isn't there any more."
        ext = resolved.suffix.lower()
        s = self._settings
        if ext in {e.lower() for e in s.blocked_extensions} or ext not in {e.lower() for e in s.allowed_extensions}:
            return "I don't open that kind of file by voice."
        if not path.is_file():
            return "That file isn't there any more."
        return None
