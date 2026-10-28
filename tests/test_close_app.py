import tempfile
import unittest

from eva.capabilities.app_index import AppIndex
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.close_app import CloseAppCapability
from eva.capabilities.close_matcher import (Kind, exe_stem, extract_close_target, find_running)
from eva.capabilities.close_settings import CloseSettings
from eva.capabilities.open_app import load_aliases
from eva.capabilities.system_probe import WindowInfo
from eva.common.types import InterfaceState, Mode, TaskOutcome
from eva.core.core import EvaCore
from eva.core.registry import CapabilityRegistry
from eva.understanding.request import InputSource, RequestObject

OWN = 4242


def W(hwnd, title, exe, pid=None, cls="X"):
    return WindowInfo(hwnd, title, cls, pid if pid is not None else hwnd, exe)


def req(text, conf=None):
    return RequestObject(raw_text=text, clean_text=text, source=InputSource.VOICE,
                         mode=Mode.COMMAND_MODE, confidence=conf)


DESKTOP = [
    W(1, "Program Manager", "explorer.exe", 900, "Progman"),
    W(2, "Untitled - Notepad", "notepad.exe", 10),
    W(3, "notes.txt - Notepad", "notepad.exe", 11),
    W(4, "How to use Notepad - Google Chrome", "chrome.exe", 20),
    W(5, "Inbox - Gmail - Google Chrome", "chrome.exe", 20),
    W(6, "Spotify Premium", "Spotify.exe", 30),
    W(7, "main.py - Visual Studio Code", "Code.exe", 40),
    W(8, "Calculator", "ApplicationFrameHost.exe", 50, "ApplicationFrameWindow"),
    W(9, "Settings", "ApplicationFrameHost.exe", 50, "ApplicationFrameWindow"),
    W(10, "Documents", "explorer.exe", 900, "CabinetWClass"),
    W(11, "Windows PowerShell", "powershell.exe", 60),
    W(12, "EVA", "python.exe", OWN),
    W(13, "Notepad++ - todo", "notepad++.exe", 70),
    W(14, "Document1 - Word", "WINWORD.EXE", 80),
]
ALIASES = load_aliases(AppSettings(data_dir="nowhere-does-not-exist"))
S = CloseSettings()


def find(query, windows=DESKTOP):
    return find_running(query, windows, ALIASES, S, own_pid=OWN)


class MatcherTests(unittest.TestCase):
    def hwnds(self, d):
        return sorted(w.hwnd for g in d.groups for w in g.windows)

    def test_exe_stem(self):
        self.assertEqual(exe_stem("C:\\Windows\\System32\\Notepad.EXE"), "notepad")
        self.assertEqual(exe_stem("notepad++.exe"), "notepad++")
        self.assertEqual(exe_stem(""), "")

    def test_notepad_closes_both_notepads_only(self):
        d = find("notepad")
        self.assertEqual((d.kind, self.hwnds(d)), (Kind.FOUND, [2, 3]))      # not Chrome, not Notepad++
        self.assertEqual(d.groups[0].name, "Notepad")

    def test_a_browser_tab_named_notepad_is_never_matched(self):
        windows = [W(4, "How to use Notepad - Google Chrome", "chrome.exe", 20)]
        self.assertEqual(find("notepad", windows).kind, Kind.NONE)

    def test_nicknames_and_program_names(self):
        cases = {"chrome": [4, 5], "google chrome": [4, 5], "spotify": [6], "vs code": [7],
                 "visual studio code": [7], "word": [14], "note pad": [2, 3], "notepad plus plus": [13]}
        for query, expected in cases.items():
            d = find(query)
            self.assertEqual(self.hwnds(d), expected, query)

    def test_store_apps_match_by_title(self):
        self.assertEqual(self.hwnds(find("calculator")), [8])
        self.assertEqual(self.hwnds(find("settings")), [9])
        self.assertEqual(find("calculator").groups[0].name, "Calculator")

    def test_explorer_only_real_folder_windows(self):
        d = find("file explorer")
        self.assertEqual(self.hwnds(d), [10])                                 # never the desktop (Progman)
        self.assertEqual(find("explorer", [DESKTOP[0]]).kind, Kind.NONE)

    def test_protected_programs(self):
        self.assertEqual(find("powershell").kind, Kind.PROTECTED)
        self.assertEqual(find("python", [W(12, "x", "python.exe", 1)]).kind, Kind.PROTECTED)

    def test_eva_own_window_is_skipped(self):
        self.assertEqual(find("python").kind, Kind.NONE)                      # python.exe pid == OWN

    def test_not_running(self):
        for q in ["youtube", "whatsapp", "the door"]:
            self.assertEqual(find(q).kind, Kind.NONE, q)

    def test_two_different_programs_are_ambiguous(self):
        windows = [W(1, "a", "foo-bar.exe", 1), W(2, "b", "foo-baz.exe", 2)]
        d = find("foo", windows)
        self.assertEqual(d.kind, Kind.AMBIGUOUS)
        self.assertEqual(len(d.groups), 2)

    def test_program_without_known_name_is_skipped(self):
        self.assertEqual(find("notepad", [W(1, "Untitled - Notepad", "", 1)]).kind, Kind.NONE)

    def test_extract_target(self):
        self.assertEqual(extract_close_target("Close Notepad."), "notepad")
        self.assertEqual(extract_close_target("can you please quit the spotify app please"), "spotify")
        self.assertEqual(extract_close_target("exit my calculator"), "calculator")
        self.assertEqual(extract_close_target("close the notepad window"), "notepad")
        self.assertEqual(extract_close_target("close all windows"), "all")
        for t in ["open notepad", "notepad", "kill notepad", "closed notepad", "close", ""]:
            self.assertIsNone(extract_close_target(t), t)


class FakeProbe:
    """Pretends to be Windows. Closing a window removes it after `polls_to_close` checks."""

    def __init__(self, windows=None, polls_to_close=0, stubborn=(), alive_pids=(), close_error=False,
                 exits_after_polls=0):
        self.windows = list(DESKTOP if windows is None else windows)
        self.polls_to_close, self.stubborn = polls_to_close, set(stubborn)
        self.alive_pids, self.close_error, self.exits_after_polls = set(alive_pids), close_error, exits_after_polls
        self.closed_requests, self._pending, self._polls, self._proc_polls = [], {}, 0, 0

    def list_windows(self):
        if self._pending:
            self._polls += 1
            if self._polls > self.polls_to_close:
                gone = {h for h in self._pending if h not in self.stubborn}
                self.windows = [w for w in self.windows if w.hwnd not in gone]
        return list(self.windows)

    def close_window(self, hwnd):
        if self.close_error:
            raise OSError("no")
        self.closed_requests.append(hwnd)
        self._pending[hwnd] = True

    def is_process_running(self, pid):
        self._proc_polls += 1
        return pid in self.alive_pids and self._proc_polls <= self.exits_after_polls

    # needed only because the app list may be refreshed
    def list_start_apps(self):
        return [{"Name": "Spotify", "AppID": "sp"}, {"Name": "WhatsApp", "AppID": "wa"}]


def make(test, probe=None, **kw):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    app_settings = AppSettings(data_dir=tmp.name)
    probe = probe or FakeProbe()
    index = AppIndex(probe, app_settings)
    index.refresh()
    cap = CloseAppCapability(probe, index, app_settings,
                             CloseSettings(close_wait_seconds=0.5, exit_wait_seconds=0.3, poll_interval_s=0.01),
                             own_pid=OWN, **kw)
    return cap, probe


class CapabilityTests(unittest.TestCase):
    def run_(self, cap, text):
        args = cap.match(req(text))
        return args, cap.execute(args)

    def test_close_notepad_closes_only_its_windows(self):
        cap, probe = make(self)
        args, res = self.run_(cap, "close notepad")
        self.assertEqual(sorted(probe.closed_requests), [2, 3])
        self.assertEqual((res.outcome, res.message), (TaskOutcome.SUCCESS, "Closed 2 Notepad windows."))
        self.assertTrue(cap.verify(args, res))

    def test_single_window_message(self):
        cap, _ = make(self)
        _, res = self.run_(cap, "quit vs code")
        self.assertEqual(res.message, "Closed Visual Studio Code.")

    def test_waits_for_slow_close(self):
        cap, _ = make(self, FakeProbe(polls_to_close=5))
        _, res = self.run_(cap, "close notepad")
        self.assertEqual(res.outcome, TaskOutcome.SUCCESS)

    def test_app_asking_to_save_is_reported_honestly(self):
        cap, probe = make(self, FakeProbe(stubborn=[2, 3]))
        _, res = self.run_(cap, "close notepad")
        self.assertEqual(res.outcome, TaskOutcome.FAILED)
        self.assertIn("still open", res.message)
        self.assertIn("save", res.message)

    def test_some_windows_close_some_dont(self):
        cap, _ = make(self, FakeProbe(stubborn=[3]))
        _, res = self.run_(cap, "close notepad")
        self.assertEqual(res.outcome, TaskOutcome.PARTIAL)
        self.assertIn("1 of 2", res.message)

    def test_tray_app_window_closes_but_program_lives(self):
        cap, _ = make(self, FakeProbe(alive_pids=[30], exits_after_polls=10_000))
        _, res = self.run_(cap, "close spotify")
        self.assertEqual(res.outcome, TaskOutcome.SUCCESS)
        self.assertIn("still running in the background", res.message)

    def test_program_that_exits_a_moment_later_is_plain_closed(self):
        cap, _ = make(self, FakeProbe(alive_pids=[30], exits_after_polls=2))
        _, res = self.run_(cap, "close spotify")
        self.assertEqual(res.message, "Closed Spotify.")

    def test_explorer_and_store_apps_skip_the_background_check(self):
        cap, _ = make(self, FakeProbe(alive_pids=[900, 50], exits_after_polls=10_000))
        self.assertEqual(self.run_(cap, "close file explorer")[1].message, "Closed File Explorer.")
        self.assertEqual(self.run_(cap, "close calculator")[1].message, "Closed Calculator.")

    def test_never_closes_other_windows(self):
        cap, probe = make(self)
        self.run_(cap, "close chrome")
        self.assertEqual(sorted(probe.closed_requests), [4, 5])

    def test_protected_never_gets_a_close_request(self):
        cap, probe = make(self)
        _, res = self.run_(cap, "close powershell")
        self.assertIn("not allowed", res.message)
        self.assertEqual(probe.closed_requests, [])

    def test_vague_and_self_requests(self):
        cap, probe = make(self)
        for t in ["close this", "close all", "close everything", "close this window", "close it"]:
            _, res = self.run_(cap, t)
            self.assertIn("which app", res.message, t)
        for t in ["quit eva", "exit yourself", "close ava"]:
            _, res = self.run_(cap, t)
            self.assertIn("can't close myself", res.message, t)
        self.assertEqual(probe.closed_requests, [])

    def test_known_app_not_running(self):
        cap, probe = make(self)
        _, res = self.run_(cap, "close whatsapp")
        self.assertEqual((res.outcome, res.message), (TaskOutcome.FAILED, "I can't see WhatsApp open right now."))
        self.assertEqual(probe.closed_requests, [])

    def test_unknown_thing_is_not_ours(self):
        cap, _ = make(self)
        for t in ["close the door", "close youtube", "open notepad", "what time is it"]:
            self.assertIsNone(cap.match(req(t)), t)

    def test_ambiguous(self):
        windows = [W(1, "a", "foo-bar.exe", 1), W(2, "b", "foo-baz.exe", 2)]
        cap, probe = make(self, FakeProbe(windows))
        _, res = self.run_(cap, "close foo")
        self.assertIn("Please say which one", res.message)
        self.assertEqual(probe.closed_requests, [])

    def test_close_request_failing_is_an_error_not_a_lie(self):
        cap, _ = make(self, FakeProbe(close_error=True))
        _, res = self.run_(cap, "close notepad")
        self.assertEqual((res.outcome, res.message), (TaskOutcome.FAILED, "I couldn't close Notepad."))

    def test_verify_catches_a_window_that_is_still_there(self):
        cap, probe = make(self)
        args, res = self.run_(cap, "close notepad")
        self.assertTrue(cap.verify(args, res))
        probe._pending.clear()
        probe.windows.append(W(2, "Untitled - Notepad", "notepad.exe", 10))   # came back
        self.assertFalse(cap.verify(args, res))

    def test_time_budget_fits_core_limit(self):
        s = CloseSettings()
        self.assertGreater(CloseAppCapability.timeout_s, s.close_wait_seconds + s.exit_wait_seconds)


class CoreIntegrationTests(unittest.TestCase):
    def core(self, probe=None):
        cap, probe = make(self, probe)
        reg = CapabilityRegistry()
        reg.register(cap)
        states = []
        return EvaCore(reg, on_state=states.append), probe, states

    def test_close_notepad_end_to_end(self):
        core, probe, states = self.core()
        r = core.handle(req("close notepad", conf=0.9))
        self.assertEqual((r.outcome, r.reason, r.reply),
                         (TaskOutcome.SUCCESS, "ok", "Closed 2 Notepad windows."))
        self.assertEqual(states, [InterfaceState.PROCESSING, InterfaceState.EXECUTING,
                                  InterfaceState.BACKGROUND])

    def test_reply_never_says_closed_when_it_did_not(self):
        core, _, _ = self.core(FakeProbe(stubborn=[2, 3]))
        r = core.handle(req("close notepad"))
        self.assertNotIn("Closed", r.reply)
        self.assertEqual(r.outcome, TaskOutcome.FAILED)

    def test_low_confidence_close_is_not_executed(self):
        core, probe, _ = self.core()
        r = core.handle(req("close notepad", conf=0.2))
        self.assertEqual(r.reason, "low_confidence")
        self.assertEqual(probe.closed_requests, [])

    def test_unknown_stays_unknown(self):
        core, _, _ = self.core()
        self.assertEqual(core.handle(req("close the door")).reason, "no_capability")

    def test_open_and_close_do_not_overlap_in_the_default_registry(self):
        from eva.capabilities.defaults import register_default_capabilities
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        reg = CapabilityRegistry()
        register_default_capabilities(reg, probe=FakeProbe(), app_settings=AppSettings(data_dir=tmp.name),
                                      refresh_apps=False)
        self.assertEqual(reg.names(), ["open_app", "open_file", "close_app", "open_url", "web_search",
                                       "tell_time", "tell_date", "battery_status",
                                       "volume_up", "volume_down", "volume_mute", "take_screenshot"])
        for phrase, expected in [("close notepad", "close_app"), ("close spotify", "close_app"),
                                 ("close this", "close_app"), ("what time is it", "tell_time")]:
            found = [c.name for c, _ in reg.find(req(phrase))]
            self.assertEqual(found, [expected], phrase)


if __name__ == "__main__":
    unittest.main()
