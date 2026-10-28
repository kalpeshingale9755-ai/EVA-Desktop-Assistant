import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from eva.capabilities.app_index import BUILTIN_APPS, AppEntry, AppIndex
from eva.capabilities.app_matcher import (DEFAULT_ALIASES, Kind, decide, extract_target,
                                          is_blocked, rank, score_name)
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.defaults import register_default_capabilities
from eva.capabilities.open_app import OpenAppCapability, load_aliases
from eva.capabilities.system_probe import parse_start_apps_json
from eva.common.types import InterfaceState, Mode, TaskOutcome
from eva.core.core import EvaCore
from eva.core.registry import CapabilityRegistry
from eva.understanding.request import InputSource, RequestObject

SAMPLE = [
    {"Name": "Google Chrome", "AppID": "chrome-id"},
    {"Name": "Chrome Remote Desktop", "AppID": "crd-id"},
    {"Name": "Microsoft Word", "AppID": "word-id"},
    {"Name": "WordPad", "AppID": "wordpad-id"},
    {"Name": "Microsoft Edge", "AppID": "edge-id"},
    {"Name": "Microsoft Excel", "AppID": "excel-id"},
    {"Name": "Spotify", "AppID": "spotify-id"},
    {"Name": "Uninstall Spotify", "AppID": "unspot-id"},
    {"Name": "Visual Studio Code", "AppID": "code-id"},
    {"Name": "Notepad", "AppID": "notepad-id"},
    {"Name": "Calculator", "AppID": "calc-id"},
    {"Name": "Command Prompt", "AppID": "cmd-id"},
    {"Name": "Windows PowerShell", "AppID": "ps-id"},
    {"Name": "Windows Terminal", "AppID": "wt-id"},
    {"Name": "Paint", "AppID": "paint-id"},
    {"Name": "Settings", "AppID": "settings-id"},
]


def req(text, conf=None):
    return RequestObject(raw_text=text, clean_text=text, source=InputSource.VOICE,
                         mode=Mode.COMMAND_MODE, confidence=conf)


class FakeProbe:
    """Pretends to be Windows. Nothing real is opened."""

    def __init__(self, apps=None, list_error=None, new_window=("Spotify Premium", "Chrome_WidgetWin_1"),
                 appear_after_calls=0, launch_error=None, fg_after=None):
        self.apps, self.list_error = apps if apps is not None else SAMPLE, list_error
        self.new_window, self.appear_after_calls = new_window, appear_after_calls
        self.launch_error, self.fg_after = launch_error, fg_after
        self.launched_apps, self.launched, self.list_calls, self.scans = [], [], 0, 0
        self._launched_any = False

    def list_start_apps(self):
        self.list_calls += 1
        if self.list_error:
            raise self.list_error
        return list(self.apps)

    def launch_app(self, app_id):
        if self.launch_error:
            raise self.launch_error
        self.launched_apps.append(app_id)
        self._launched_any = True

    def launch(self, argv):
        if self.launch_error:
            raise self.launch_error
        self.launched.append(list(argv))
        self._launched_any = True

    def visible_windows(self):
        wins = {100: ("Old window", "Cls")}
        if self._launched_any and self.new_window:
            self.scans += 1
            if self.scans > self.appear_after_calls:
                wins[200] = self.new_window
        return wins

    def foreground_window(self):
        if self._launched_any and self.fg_after:
            return 300, self.fg_after
        return 100, "Old window"


class Env:
    def __init__(self, test, **probe_kw):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        test.addCleanup(self._cleanup)
        self.settings = AppSettings(data_dir=self.tmp.name, verify_seconds=0.3,
                                    poll_interval_s=0.01)
        self.probe = FakeProbe(**probe_kw)
        self.index = AppIndex(self.probe, self.settings)
        self.index.refresh()
        self.cap = OpenAppCapability(self.index, self.probe, self.settings)

    def _cleanup(self):
        if getattr(self, "index", None) and self.index._thread and self.index._thread.is_alive():
            self.index._thread.join(timeout=2.0)
        self.tmp.cleanup()


class MatcherTests(unittest.TestCase):
    def decide(self, query, apps=SAMPLE, **kw):
        entries = [AppEntry(a["Name"], a["AppID"]) for a in apps]
        s = AppSettings(**kw)
        return decide(rank(query, entries, DEFAULT_ALIASES, s.blocklist), s)

    def names(self, d):
        return [m.entry.name for m in d.matches]

    def test_found_cases(self):
        cases = {
            "chrome": "Google Chrome", "google chrome": "Google Chrome",
            "word": "Microsoft Word", "wordpad": "WordPad", "vs code": "Visual Studio Code",
            "vscode": "Visual Studio Code", "note pad": "Notepad", "notepad": "Notepad",
            "spotify": "Spotify", "edge": "Microsoft Edge", "calculator": "Calculator",
            "calculater": "Calculator", "note pat": "Notepad", "paint": "Paint",
            "settings": "Settings",
        }
        for query, expected in cases.items():
            d = self.decide(query)
            self.assertEqual((d.kind, self.names(d)), (Kind.FOUND, [expected]), query)

    def test_word_does_not_pick_wordpad(self):
        self.assertEqual(self.names(self.decide("word")), ["Microsoft Word"])

    def test_not_an_app(self):
        for query in ["youtube", "the door", "spotty", "mic", "a", "cmdd"]:
            self.assertEqual(self.decide(query).kind, Kind.NONE, query)

    def test_blocked(self):
        for query in ["command prompt", "powershell", "windows powershell", "terminal",
                      "windows terminal", "uninstall spotify"]:
            self.assertEqual(self.decide(query).kind, Kind.BLOCKED, query)

    def test_spotify_beats_its_uninstaller(self):
        d = self.decide("spotify")
        self.assertEqual((d.kind, self.names(d)), (Kind.FOUND, ["Spotify"]))

    def test_two_close_apps_are_ambiguous(self):
        d = self.decide("microsoft")
        self.assertEqual(d.kind, Kind.AMBIGUOUS)
        self.assertIn("Microsoft Edge", self.names(d))
        self.assertIn("Microsoft Excel", self.names(d))

    def test_blocklist_uses_whole_words(self):
        bl = AppSettings().blocklist
        self.assertTrue(is_blocked("Windows PowerShell ISE", bl))
        self.assertFalse(is_blocked("Terminalogy Quiz Game", bl))
        self.assertFalse(is_blocked("Spotify", bl))

    def test_extract_target(self):
        self.assertEqual(extract_target("Open Spotify."), "spotify")
        self.assertEqual(extract_target("can you please launch the calculator app please"), "calculator")
        self.assertEqual(extract_target("start my files"), "files")
        for t in ["spotify", "close spotify", "what time is it", "open", ""]:
            self.assertIsNone(extract_target(t), t)

    def test_score_order(self):
        self.assertGreater(score_name("chrome", "Chrome"), score_name("chrome", "Chrome Remote Desktop"))
        self.assertEqual(score_name("note pad", "Notepad"), 0.97)


class IndexTests(unittest.TestCase):
    def test_parse_powershell_json(self):
        self.assertEqual(parse_start_apps_json('\ufeff{"Name":"A","AppID":"1"}'),
                         [{"Name": "A", "AppID": "1"}])
        self.assertEqual(len(parse_start_apps_json('[{"Name":"A","AppID":"1"},{"Name":"B","AppID":"2"}]')), 2)
        self.assertEqual(parse_start_apps_json("  "), [])

    def test_refresh_save_and_reload(self):
        env = Env(self)
        self.assertTrue(env.index.has_system_apps)
        again = AppIndex(FakeProbe(), env.settings)
        self.assertTrue(again.load_cache())
        self.assertIn("Spotify", [e.name for e in again.entries()])

    def test_builtins_are_a_safety_net_and_do_not_duplicate(self):
        env = Env(self, apps=[{"Name": "Notepad", "AppID": "np"}, {"Name": "Spotify", "AppID": "sp"}])
        names = [e.name for e in env.index.entries()]
        self.assertEqual(names.count("Notepad"), 1)
        self.assertEqual(next(e for e in env.index.entries() if e.name == "Notepad").app_id, "np")
        self.assertIn("Calculator", names)
        self.assertIn("File Explorer", names)

    def test_failed_refresh_keeps_old_list(self):
        env = Env(self)
        env.probe.list_error = RuntimeError("powershell broke")
        self.assertFalse(env.index.refresh())
        self.assertTrue(env.index.has_system_apps)

    def test_empty_refresh_keeps_old_list(self):
        env = Env(self)
        env.probe.apps = []
        self.assertFalse(env.index.refresh())
        self.assertIn("Spotify", [e.name for e in env.index.entries()])

    def test_damaged_cache_is_ignored(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = AppSettings(data_dir=tmp.name)
        (Path(tmp.name) / s.cache_file).write_text("{not json", encoding="utf-8")
        idx = AppIndex(FakeProbe(), s)
        self.assertFalse(idx.load_cache())
        self.assertEqual([e.name for e in idx.entries()], [b.name for b in BUILTIN_APPS])

    def test_stale_after_refresh_hours(self):
        now = [1000.0]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        idx = AppIndex(FakeProbe(), AppSettings(data_dir=tmp.name, refresh_hours=1), now_fn=lambda: now[0])
        self.assertTrue(idx.is_stale)
        idx.refresh()
        self.assertFalse(idx.is_stale)
        now[0] += 3601
        self.assertTrue(idx.is_stale)

    def test_miss_refresh_is_rate_limited(self):
        now = [10_000.0]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        probe = FakeProbe()
        idx = AppIndex(probe, AppSettings(data_dir=tmp.name, miss_refresh_min_interval_s=300),
                       now_fn=lambda: now[0])
        idx.note_miss(); idx._thread.join(2)
        idx.note_miss()
        self.assertEqual(probe.list_calls, 1)
        now[0] += 301
        idx.note_miss(); idx._thread.join(2)
        self.assertEqual(probe.list_calls, 2)

    def test_user_nickname_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = AppSettings(data_dir=tmp.name)
        (Path(tmp.name) / s.aliases_file).write_text(json.dumps({"Music": "Spotify"}), encoding="utf-8")
        aliases = load_aliases(s)
        self.assertEqual(aliases["music"], "spotify")
        self.assertEqual(aliases["chrome"], "google chrome")           # built-ins kept
        (Path(tmp.name) / s.aliases_file).write_text("oops", encoding="utf-8")
        self.assertEqual(load_aliases(s)["chrome"], "google chrome")   # bad file ignored


class CapabilityTests(unittest.TestCase):
    def test_match_found(self):
        env = Env(self)
        args = env.cap.match(req("open spotify"))
        self.assertEqual((args["kind"], args["apps"][0]["name"]), ("FOUND", "Spotify"))

    def test_match_returns_none_for_non_apps_and_other_sentences(self):
        env = Env(self)
        for t in ["open youtube", "what time is it", "open the door", "close spotify"]:
            self.assertIsNone(env.cap.match(req(t)), t)

    def test_match_ignores_long_sentences(self):
        env = Env(self)
        self.assertIsNone(env.cap.match(req("open spotify and then play my favourite songs")))

    def test_launch_uses_the_app_id_from_the_list(self):
        env = Env(self)
        res = env.cap.execute(env.cap.match(req("open spotify")))
        self.assertEqual(env.probe.launched_apps, ["spotify-id"])
        self.assertEqual((res.outcome, res.message), (TaskOutcome.SUCCESS, "Opened Spotify."))

    def test_builtin_uses_fixed_command(self):
        env = Env(self, apps=[{"Name": "Spotify", "AppID": "sp"}])
        env.cap.execute(env.cap.match(req("open calculator")))
        self.assertEqual(env.probe.launched, [["calc.exe"]])
        self.assertEqual(env.probe.launched_apps, [])

    def test_nothing_you_say_reaches_the_launcher(self):
        env = Env(self)
        for t in ["open notepad; del *", "open notepad && shutdown /s", "open c:\\windows\\system32\\cmd.exe",
                  "open http://evil.example", "open ../../secret"]:
            args = env.cap.match(req(t))
            self.assertIsNone(args, t)
        self.assertEqual(env.probe.launched + env.probe.launched_apps, [])

    def test_blocked_never_launches(self):
        env = Env(self)
        res = env.cap.execute(env.cap.match(req("open command prompt")))
        self.assertEqual(res.outcome, TaskOutcome.FAILED)
        self.assertIn("not allowed", res.message)
        self.assertEqual(env.probe.launched + env.probe.launched_apps, [])

    def test_ambiguous_lists_names_and_never_launches(self):
        env = Env(self)
        res = env.cap.execute(env.cap.match(req("open microsoft")))
        self.assertEqual(res.outcome, TaskOutcome.FAILED)
        self.assertIn("Microsoft Edge", res.message)
        self.assertIn("Microsoft Excel", res.message)
        self.assertIn("full name", res.message)
        self.assertEqual(env.probe.launched_apps, [])

    def test_launch_error(self):
        env = Env(self, launch_error=FileNotFoundError("x"))
        res = env.cap.execute(env.cap.match(req("open spotify")))
        self.assertEqual((res.outcome, res.message), (TaskOutcome.FAILED, "I couldn't open Spotify."))

    def test_no_app_list_gives_a_clear_message(self):
        env = Env(self, list_error=RuntimeError("no powershell"))
        env.index = AppIndex(env.probe, env.settings)          # never loaded anything
        env.cap = OpenAppCapability(env.index, env.probe, env.settings)
        args = env.cap.match(req("open spotify"))
        self.assertEqual(args["kind"], "NO_INDEX")
        self.assertIn("Notepad", env.cap.execute(args).message)
        self.assertIsNotNone(env.cap.match(req("open notepad")))       # safety net still works

    def verify(self, env, text):
        args = env.cap.match(req(text))
        res = env.cap.execute(args)
        return env.cap.verify(args, res)

    def test_verify_by_window_title(self):
        env = Env(self, new_window=("Spotify Premium", "Chrome_WidgetWin_1"))
        self.assertTrue(self.verify(env, "open spotify"))

    def test_verify_by_any_new_window(self):
        env = Env(self, new_window=("Now playing: Song", "Chrome_WidgetWin_1"))
        self.assertTrue(self.verify(env, "open spotify"))

    def test_verify_waits_for_slow_app(self):
        env = Env(self, appear_after_calls=5)
        self.assertTrue(self.verify(env, "open spotify"))

    def test_verify_by_foreground_when_app_was_already_open(self):
        env = Env(self, new_window=None, fg_after="Spotify Premium")
        self.assertTrue(self.verify(env, "open spotify"))

    def test_verify_fails_when_nothing_happens(self):
        env = Env(self, new_window=None)
        self.assertFalse(self.verify(env, "open spotify"))

    def test_explorer_window_is_not_proof_for_other_apps(self):
        env = Env(self, new_window=("Documents", "CabinetWClass"))
        self.assertFalse(self.verify(env, "open spotify"))          # bad id would open a folder
        self.assertTrue(self.verify(Env(self, new_window=("Home", "CabinetWClass")),
                                    "open file explorer"))

    def test_verification_time_fits_in_core_limit(self):
        self.assertGreater(OpenAppCapability.verify_timeout_s, AppSettings().verify_seconds)


class CoreIntegrationTests(unittest.TestCase):
    def core(self, **probe_kw):
        env = Env(self, **probe_kw)
        reg = CapabilityRegistry()
        reg.register(env.cap)
        states = []
        return EvaCore(reg, on_state=states.append), env, states

    def test_open_spotify_end_to_end(self):
        core, env, states = self.core()
        r = core.handle(req("open spotify", conf=0.9))
        self.assertEqual((r.outcome, r.reason, r.reply), (TaskOutcome.SUCCESS, "ok", "Opened Spotify."))
        self.assertEqual(states, [InterfaceState.PROCESSING, InterfaceState.EXECUTING,
                                  InterfaceState.BACKGROUND])

    def test_never_claims_success_without_a_window(self):
        core, _, _ = self.core(new_window=None)
        r = core.handle(req("open spotify"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.FAILED, "verified_failed"))
        self.assertNotIn("Opened", r.reply)

    def test_blocked_and_ambiguous_replies(self):
        core, env, _ = self.core()
        self.assertIn("not allowed", core.handle(req("open powershell")).reply)
        self.assertIn("full name", core.handle(req("open microsoft")).reply)
        self.assertEqual(env.probe.launched_apps, [])

    def test_unknown_stays_unknown(self):
        core, _, _ = self.core()
        self.assertEqual(core.handle(req("open youtube")).reason, "no_capability")

    def test_defaults_register_all_capabilities_without_touching_windows(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        reg = CapabilityRegistry()
        index = register_default_capabilities(reg, probe=FakeProbe(),
                                              app_settings=AppSettings(data_dir=tmp.name),
                                              refresh_apps=False)
        self.assertEqual(reg.names(), ["open_app", "open_file", "close_app", "open_url", "web_search",
                                       "tell_time", "tell_date", "battery_status",
                                       "volume_up", "volume_down", "volume_mute", "take_screenshot"])
        self.assertFalse(index.has_system_apps)          # nothing was read, no thread started


if __name__ == "__main__":
    unittest.main()
