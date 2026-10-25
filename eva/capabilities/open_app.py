"""Open ANY installed app by name.   "Hey EVA, open spotify"

Flow:  extract the name -> match it against EVA's app list -> launch the app Windows told us about
       -> check that a window really appeared -> "Opened Spotify."

Safety:
  - Only apps from the app list can be opened. The words you say only CHOOSE an entry.
  - Nothing you say is ever run as a command. No paths, links or arguments.
  - Terminals, admin tools and uninstallers are on a blocklist (app_settings.py).
  - Two close matches -> EVA reads both names and does NOT guess.
"""
from __future__ import annotations

import json
import time
from typing import Callable

from eva.capabilities.app_index import AppIndex
from eva.capabilities.app_matcher import DEFAULT_ALIASES, Kind, decide, extract_target, rank
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.text_match import normalize
from eva.common.logging_setup import get_logger
from eva.common.types import TaskOutcome
from eva.core.capability import Capability, CapabilityResult, Risk
from eva.understanding.request import RequestObject

log = get_logger("eva.cap.open_app")

_NO_INDEX = "NO_INDEX"
_EXPLORER_CLASS = "CabinetWClass"


def load_aliases(settings: AppSettings) -> dict[str, str]:
    """Built-in nicknames + your own file (optional). Your file wins."""
    aliases = dict(DEFAULT_ALIASES)
    path = settings.data_path() / settings.aliases_file
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
        aliases.update({normalize(k): normalize(v) for k, v in user.items()})
        log.info("Loaded %d app nicknames from %s", len(user), path)
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not read the nickname file %s, using built-in nicknames", path)
    return aliases


def _join_or(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


class OpenAppCapability(Capability):
    name = "open_app"
    description = "Open any installed app by name."
    risk = Risk.LOW
    timeout_s = 10.0
    verify_timeout_s = 8.0        # must be longer than settings.verify_seconds

    def __init__(self, index: AppIndex, probe, settings: AppSettings | None = None,
                 aliases: dict[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._index, self._probe = index, probe
        self._settings = settings or AppSettings()
        self._aliases = aliases if aliases is not None else load_aliases(self._settings)
        self._sleep, self._clock = sleep, clock

    # ---- Core calls these -------------------------------------------------------
    def match(self, request: RequestObject) -> dict | None:
        target = extract_target(request.clean_text)
        if not target or len(target.split()) > self._settings.max_spoken_words:
            return None
        decision = decide(rank(target, self._index.entries(), self._aliases,
                               self._settings.blocklist), self._settings)
        if decision.kind is Kind.NONE:
            self._index.note_miss()          # maybe it was just installed: refresh in the background
            if not self._index.has_system_apps:
                return {"kind": _NO_INDEX, "query": target}
            return None                      # not an app: other capabilities may want "open ..."
        return {"kind": decision.kind.value, "query": target,
                "apps": [{"name": m.entry.name, "app_id": m.entry.app_id,
                          "argv": list(m.entry.argv)} for m in decision.matches]}

    def execute(self, args: dict) -> CapabilityResult:
        kind = args["kind"]
        if kind == _NO_INDEX:
            return CapabilityResult(TaskOutcome.FAILED,
                                    "I can't read your installed apps right now, so I can only "
                                    "open Notepad, Calculator and File Explorer.")
        apps = args["apps"]
        if kind == Kind.BLOCKED.value:
            return CapabilityResult(TaskOutcome.FAILED, f"I'm not allowed to open {apps[0]['name']}.")
        if kind == Kind.AMBIGUOUS.value:
            names = [a["name"] for a in apps]
            return CapabilityResult(TaskOutcome.FAILED,
                                    f"I found {_join_or(names)}. Please say the full name.")

        app = apps[0]
        before = self._probe.visible_windows()
        before_fg, _ = self._probe.foreground_window()
        try:
            if app["app_id"]:
                self._probe.launch_app(app["app_id"])
            else:
                self._probe.launch(list(app["argv"]))
        except OSError:
            log.exception("Could not start %s", app["name"])
            return CapabilityResult(TaskOutcome.FAILED, f"I couldn't open {app['name']}.")
        log.info("Started %s (heard %r) | windows before=%d", app["name"], args["query"], len(before))
        return CapabilityResult(TaskOutcome.SUCCESS, f"Opened {app['name']}.",
                                {"name": app["name"], "before": sorted(before), "before_fg": before_fg})

    def verify(self, args: dict, result: CapabilityResult) -> bool:
        """Wait for proof. Proof = a NEW window (best: its title has the app's name),
        or the app's window coming to the front."""
        name = result.details["name"]
        before = set(result.details["before"])
        before_fg = result.details["before_fg"]
        words = [w for w in normalize(name).split() if len(w) >= 3]
        is_explorer = "explorer" in normalize(name)
        deadline = self._clock() + self._settings.verify_seconds

        while True:
            new = {h: v for h, v in self._probe.visible_windows().items()
                   if h not in before and (is_explorer or v[1] != _EXPLORER_CLASS)}
            if any(any(w in normalize(title) for w in words) for title, _cls in new.values()):
                log.info("Verified %s | evidence=title", name)
                return True
            if new:
                log.info("Verified %s | evidence=new_window %s", name, [t for t, _ in new.values()])
                return True
            fg_handle, fg_title = self._probe.foreground_window()
            if fg_handle and fg_handle != before_fg and any(w in normalize(fg_title) for w in words):
                log.info("Verified %s | evidence=foreground", name)
                return True
            if self._clock() >= deadline:
                log.warning("Could not verify %s | no new window in %.1fs",
                            name, self._settings.verify_seconds)
                return False
            self._sleep(self._settings.poll_interval_s)
