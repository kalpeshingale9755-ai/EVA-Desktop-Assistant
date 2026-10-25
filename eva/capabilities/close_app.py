"""Close a running app.   "Eva, close notepad"

Gentle only: EVA asks the app's windows to close (like clicking X). If you have unsaved work the app asks
"Save changes?" by itself, so nothing is lost. EVA NEVER kills a program.

Safety:
  - Matches the running PROGRAM, not window titles ("How to use Notepad" in a browser is never touched).
  - Never closes EVA herself, terminals, Windows' own parts, or the desktop and taskbar.
  - "close this / all / everything" -> EVA asks which app you mean.
  - Says "Closed" only after the windows really disappeared.
"""
from __future__ import annotations

import os
import time
from typing import Callable

from eva.capabilities.app_index import AppIndex
from eva.capabilities.app_matcher import Kind as AppKind, decide as decide_app, rank as rank_apps
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.close_matcher import Kind, extract_close_target, find_running
from eva.capabilities.close_settings import CloseSettings
from eva.capabilities.open_app import load_aliases
from eva.capabilities.text_match import normalize
from eva.common.logging_setup import get_logger
from eva.common.types import TaskOutcome
from eva.core.capability import Capability, CapabilityResult, Risk
from eva.understanding.request import RequestObject

log = get_logger("eva.cap.close_app")


def _plural(n: int, name: str) -> str:
    return f"Closed {name}." if n == 1 else f"Closed {n} {name} windows."


class CloseAppCapability(Capability):
    name = "close_app"
    description = "Close a running app (gently, it can still ask you to save)."
    risk = Risk.LOW           # the app itself protects unsaved work; nothing is force-killed
    timeout_s = 10.0          # must be longer than close_wait + exit_wait
    verify_timeout_s = 3.0

    def __init__(self, probe, index: AppIndex | None = None, app_settings: AppSettings | None = None,
                 settings: CloseSettings | None = None, aliases: dict[str, str] | None = None,
                 own_pid: int | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._probe, self._index = probe, index
        self._app_settings = app_settings or AppSettings()
        self._settings = settings or CloseSettings()
        self._aliases = aliases if aliases is not None else load_aliases(self._app_settings)
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._sleep, self._clock = sleep, clock

    # ---- Core calls these -------------------------------------------------------
    def match(self, request: RequestObject) -> dict | None:
        s = self._settings
        target = extract_close_target(request.clean_text)
        if not target or len(target.split()) > s.max_spoken_words:
            return None
        if target in s.vague_words:
            return {"kind": "VAGUE", "query": target}
        if target in s.self_words:
            return {"kind": "SELF", "query": target}

        decision = find_running(target, self._probe.list_windows(), self._aliases, s, self._own_pid)
        if decision.kind is Kind.PROTECTED:
            return {"kind": "PROTECTED", "query": target, "name": decision.groups[0].name}
        if decision.kind is Kind.AMBIGUOUS:
            return {"kind": "AMBIGUOUS", "query": target, "names": [g.name for g in decision.groups]}
        if decision.kind is Kind.FOUND:
            g = decision.groups[0]
            return {"kind": "FOUND", "query": target, "name": g.name,
                    "windows": [{"hwnd": w.hwnd, "pid": w.pid, "exe": w.exe} for w in g.windows]}

        # Not running. If it is an app EVA knows, say so; otherwise this is not for us.
        if self._index is not None:
            known = decide_app(rank_apps(target, self._index.entries(), self._aliases,
                                         self._app_settings.blocklist), self._app_settings)
            if known.kind is AppKind.FOUND:
                return {"kind": "NOT_RUNNING", "query": target, "name": known.matches[0].entry.name}
        return None

    def execute(self, args: dict) -> CapabilityResult:
        kind = args["kind"]
        if kind == "VAGUE":
            return CapabilityResult(TaskOutcome.FAILED,
                                    "Tell me which app to close, for example: close notepad.")
        if kind == "SELF":
            return CapabilityResult(TaskOutcome.FAILED,
                                    "I can't close myself. Use the tray icon if you want me to quit.")
        if kind == "PROTECTED":
            return CapabilityResult(TaskOutcome.FAILED, f"I'm not allowed to close {args['name']}.")
        if kind == "AMBIGUOUS":
            names = args["names"]
            joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            return CapabilityResult(TaskOutcome.FAILED,
                                    f"I found {joined} open. Please say which one.")
        if kind == "NOT_RUNNING":
            return CapabilityResult(TaskOutcome.FAILED, f"I can't see {args['name']} open right now.")
        return self._close(args)

    def verify(self, args: dict, result: CapabilityResult) -> bool:
        """Double-check: none of the windows we closed exists any more."""
        current = {w.hwnd for w in self._probe.list_windows()}
        return not any(h in current for h in result.details["hwnds"])

    # ---- closing ----------------------------------------------------------------
    def _close(self, args: dict) -> CapabilityResult:
        s, name = self._settings, args["name"]
        hwnds = [w["hwnd"] for w in args["windows"]]
        pids = sorted({w["pid"] for w in args["windows"]})
        shared = any(w["exe"] and w["exe"].lower().removesuffix(".exe") in s.shared_process_exes
                     for w in args["windows"])

        asked = 0
        for hwnd in hwnds:
            try:
                self._probe.close_window(hwnd)
                asked += 1
            except OSError:
                log.exception("Could not ask window %s to close", hwnd)
        if asked == 0:
            return CapabilityResult(TaskOutcome.FAILED, f"I couldn't close {name}.")
        log.info("Asked %d window(s) of %s to close", asked, name)

        started = self._clock()
        deadline = started + s.close_wait_seconds
        while True:
            current = {w.hwnd for w in self._probe.list_windows()}
            remaining = [h for h in hwnds if h in current]
            if not remaining or self._clock() >= deadline:
                break
            self._sleep(s.poll_interval_s)

        closed = len(hwnds) - len(remaining)
        details = {"hwnds": hwnds, "name": name}
        if remaining and closed == 0:
            log.warning("%s did not close in %.1fs (probably asking to save)", name, s.close_wait_seconds)
            return CapabilityResult(TaskOutcome.FAILED,
                                    f"I asked {name} to close, but it's still open. "
                                    "It may be asking you to save.", details)
        if remaining:
            return CapabilityResult(TaskOutcome.PARTIAL,
                                    f"I closed {closed} of {len(hwnds)} {name} windows. "
                                    "The rest may be asking you to save.", details)

        # All windows are gone. Is the program itself still alive (tray apps like Spotify)?
        if not shared and pids:
            exit_deadline = self._clock() + s.exit_wait_seconds
            alive = [p for p in pids if self._probe.is_process_running(p)]
            while alive and self._clock() < exit_deadline:
                self._sleep(s.poll_interval_s)
                alive = [p for p in alive if self._probe.is_process_running(p)]
            if alive:
                log.info("%s window closed, program still running (pids %s)", name, alive)
                return CapabilityResult(TaskOutcome.SUCCESS,
                                        f"Closed {name}'s window, but it's still running in the background.",
                                        details)
        log.info("Closed %s in %.1fs", name, self._clock() - started)
        return CapabilityResult(TaskOutcome.SUCCESS, _plural(len(hwnds), name), details)
