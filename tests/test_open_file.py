import os
import tempfile
import unittest
from pathlib import Path

from eva.capabilities.app_index import AppIndex, AppEntry
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.file_index import FileEntry, FileIndex, scan_folders
from eva.capabilities.file_matcher import (Kind, decide_files, parse_target, rank_files, spoken_labels)
from eva.capabilities.file_settings import FileSettings
from eva.capabilities.open_file import OpenFileCapability
from eva.common.types import InterfaceState, Mode, TaskOutcome
from eva.core.core import EvaCore
from eva.core.registry import CapabilityRegistry
from eva.understanding.request import InputSource, RequestObject


def req(text, conf=None):
    return RequestObject(raw_text=text, clean_text=text, source=InputSource.VOICE,
                         mode=Mode.COMMAND_MODE, confidence=conf)


def touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def build_tree(base: Path, dup_resume=False):
    docs, downloads = base / "Documents", base / "Downloads"
    for f in ["Resume.pdf", "Resume (1).pdf", "Sales Report.xlsx", "Sales Report.docx", "notes.txt",
              "installer.exe", "script.py", ".hidden.pdf", "node_modules/lib.pdf", "photo.JPG",
              "sub/Deep/budget.xlsx", "sub/trip plan.pptx", "Song List.docx"]:
        touch(docs / f)
    touch(downloads / "Invoice 2024.pdf")
    touch(downloads / "setup.msi")
    if dup_resume:
        touch(downloads / "Resume.pdf")
    return docs, downloads


def settings_for(base: Path, **kw):
    docs, downloads = base / "Documents", base / "Downloads"
    return FileSettings(use_default_folders=False, folders=(str(docs), str(downloads)),
                        data_dir=str(base / "data"), verify_seconds=0.3, poll_interval_s=0.01,
                        quick_refresh_min_interval_s=0.0, **kw)


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()


class ScanTests(Tmp):
    def test_only_safe_types_and_no_junk(self):
        build_tree(self.base)
        found = scan_folders(FileSettings(use_default_folders=False, folders=(str(self.base / "Documents"),)).roots()
                             and [self.base / "Documents"], settings_for(self.base))
        names = {e.display for e in found if not e.is_dir}
        self.assertIn("Resume.pdf", names)
        self.assertIn("photo.jpg", names)                     # extension lower-cased
        for bad in ["installer.exe", "script.py", ".hidden.pdf", "lib.pdf"]:
            self.assertNotIn(bad, names)
        dirs = {e.name for e in found if e.is_dir}
        self.assertNotIn("node_modules", dirs)
        self.assertIn("sub", dirs)

    def test_depth_limit(self):
        build_tree(self.base)
        shallow = scan_folders([self.base / "Documents"], settings_for(self.base, max_depth=2))
        deep = scan_folders([self.base / "Documents"], settings_for(self.base, max_depth=3))
        self.assertNotIn("budget.xlsx", {e.display for e in shallow})
        self.assertIn("budget.xlsx", {e.display for e in deep})

    def test_links_to_other_folders_are_not_followed(self):
        outside = self.base / "outside"
        touch(outside / "secret.pdf")
        (self.base / "Documents").mkdir()
        try:
            os.symlink(outside, self.base / "Documents" / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this system cannot make symlinks")
        found = scan_folders([self.base / "Documents"], settings_for(self.base))
        self.assertNotIn("secret.pdf", {e.display for e in found})

    def test_file_cap(self):
        build_tree(self.base)
        found = scan_folders([self.base / "Documents"], settings_for(self.base, max_files=3))
        self.assertEqual(len([e for e in found if not e.is_dir]), 3)

    def test_roots_only_existing_and_without_duplicates(self):
        (self.base / "Documents").mkdir()
        s = FileSettings(folders=(str(self.base / "Documents"), str(self.base / "nope")))
        roots = s.roots(home=self.base, env={})
        self.assertEqual(roots, [self.base / "Documents"])


class IndexTests(Tmp):
    def test_refresh_save_reload_and_changed_folders(self):
        build_tree(self.base)
        s = settings_for(self.base)
        idx = FileIndex(s)
        self.assertTrue(idx.refresh())
        again = FileIndex(s)
        self.assertTrue(again.load_cache())
        self.assertIn("Resume.pdf", {e.display for e in again.entries()})
        other = FileIndex(settings_for(self.base), roots=[self.base / "Documents"])
        self.assertFalse(other.load_cache())                  # different folders -> rebuild

    def test_damaged_cache_is_ignored(self):
        s = settings_for(self.base)
        (self.base / "data").mkdir()
        (self.base / "data" / s.cache_file).write_text("{broken", encoding="utf-8")
        self.assertFalse(FileIndex(s, roots=[]).load_cache())

    def test_stale(self):
        now = [1000.0]
        idx = FileIndex(settings_for(self.base), roots=[], now_fn=lambda: now[0])
        self.assertTrue(idx.is_stale)
        idx.refresh()
        self.assertFalse(idx.is_stale)
        now[0] += 25 * 3600
        self.assertTrue(idx.is_stale)

    def test_quick_refresh_finds_a_new_download_and_forgets_a_deleted_file(self):
        docs, downloads = build_tree(self.base)
        idx = FileIndex(settings_for(self.base))
        idx.refresh()
        touch(downloads / "Fresh Notes.pdf")
        (downloads / "Invoice 2024.pdf").unlink()
        self.assertTrue(idx.quick_refresh())
        names = {e.display for e in idx.entries()}
        self.assertIn("Fresh Notes.pdf", names)
        self.assertNotIn("Invoice 2024.pdf", names)
        self.assertIn("budget.xlsx", names)                   # deeper files stay
        self.assertIn("Documents", names)                     # root folders stay

    def test_miss_refresh_is_rate_limited(self):
        now = [10_000.0]
        idx = FileIndex(settings_for(self.base), roots=[], now_fn=lambda: now[0])
        calls = []
        idx.refresh_in_background = lambda: calls.append(1)
        idx.note_miss(); idx.note_miss()
        now[0] += 301
        idx.note_miss()
        self.assertEqual(len(calls), 2)


class ParseTests(unittest.TestCase):
    def test_type_words_and_generic_words(self):
        p = parse_target("resume pdf")
        self.assertEqual((p.name, p.exts, p.want_dir, p.explicit), ("resume", frozenset({".pdf"}), False, True))
        p = parse_target("sales excel sheet")
        self.assertEqual(p.name, "sales")
        self.assertIn(".xlsx", p.exts)
        p = parse_target("resume file")
        self.assertEqual((p.name, p.exts, p.explicit), ("resume", None, True))
        p = parse_target("file called resume")
        self.assertEqual((p.name, p.explicit), ("resume", True))

    def test_folders(self):
        p = parse_target("project folder")
        self.assertEqual((p.name, p.want_dir, p.explicit), ("project", True, True))
        p = parse_target("downloads", {"downloads", "documents"})
        self.assertEqual((p.name, p.want_dir), ("downloads", True))
        p = parse_target("downloads", set())
        self.assertFalse(p.want_dir)

    def test_plain_names_are_not_explicit(self):
        p = parse_target("resume")
        self.assertEqual((p.name, p.exts, p.explicit), ("resume", None, False))
        p = parse_target("song list")                          # "list" is not a type word
        self.assertEqual((p.name, p.exts), ("song list", None))

    def test_only_a_type_word_has_no_name(self):
        self.assertIsNone(parse_target("file"))
        self.assertEqual(parse_target("pdf").name, "pdf")      # a single word stays the name

    def test_name_with_type_word_inside_is_kept(self):
        self.assertEqual(parse_target("word count notes").name, "word count notes")


class MatchTests(unittest.TestCase):
    E = [FileEntry("Resume", ".pdf", "/d/Resume.pdf", 5), FileEntry("Resume (1)", ".pdf", "/d/Resume (1).pdf", 9),
         FileEntry("Resume", ".docx", "/d/Resume.docx", 1), FileEntry("Invoice 2024", ".pdf", "/dl/Invoice 2024.pdf"),
         FileEntry("Sales Report", ".xlsx", "/d/Sales Report.xlsx"), FileEntry("Sales Report", ".docx", "/d/Sales Report.docx"),
         FileEntry("Documents", "", "/home/Documents", 0, True), FileEntry("Project X", "", "/d/Project X", 0, True)]
    S = FileSettings()

    def decide(self, text, entries=None, roots=frozenset({"documents"})):
        parsed = parse_target(text, roots)
        return decide_files(rank_files(parsed, entries or self.E), self.S)

    def names(self, d):
        return [m.entry.display for m in d.matches]

    def test_type_word_picks_the_right_file(self):
        d = self.decide("resume pdf")
        self.assertEqual(d.kind, Kind.FOUND)
        self.assertEqual(self.names(d), ["Resume.pdf"])         # the "(1)" copy loses clearly
        self.assertEqual(self.names(self.decide("resume word")), ["Resume.docx"])

    def test_plain_name_with_two_types_is_ambiguous(self):
        d = self.decide("resume")
        self.assertEqual(d.kind, Kind.AMBIGUOUS)
        self.assertEqual(sorted(self.names(d)), ["Resume.docx", "Resume.pdf"])
        self.assertEqual(self.decide("sales report").kind, Kind.AMBIGUOUS)

    def test_words_inside_the_name_and_spelling_mistakes(self):
        self.assertEqual(self.names(self.decide("invoice")), ["Invoice 2024.pdf"])
        self.assertEqual(self.names(self.decide("invoice 2024")), ["Invoice 2024.pdf"])
        self.assertEqual(self.names(self.decide("invoise 2024")), ["Invoice 2024.pdf"])

    def test_unknown_and_wrong_type(self):
        self.assertEqual(self.decide("budget").kind, Kind.NONE)
        self.assertEqual(self.decide("resume excel").kind, Kind.NONE)

    def test_folders_are_found_only_as_folders(self):
        self.assertEqual(self.names(self.decide("project folder")), ["Project X"])
        self.assertEqual(self.names(self.decide("documents")), ["Documents"])
        self.assertEqual(self.decide("project").kind, Kind.NONE)   # files only, unless you say folder

    def test_labels_name_the_folder_for_duplicates(self):
        a = FileEntry("Resume", ".pdf", os.path.join("x", "Documents", "Resume.pdf"))
        b = FileEntry("Resume", ".pdf", os.path.join("x", "Downloads", "Resume.pdf"))
        c = FileEntry("Resume", ".docx", os.path.join("x", "Documents", "Resume.docx"))
        self.assertEqual(spoken_labels([a, b]), ["Resume.pdf in Documents", "Resume.pdf in Downloads"])
        self.assertEqual(spoken_labels([a, c]), ["Resume.pdf", "Resume.docx"])


class FakeProbe:
    def __init__(self, new_window=("Resume.pdf - Microsoft Edge", "Chrome_WidgetWin_1"), appear_after=0,
                 open_error=None, fg_after=None):
        self.new_window, self.appear_after, self.open_error, self.fg_after = new_window, appear_after, open_error, fg_after
        self.opened, self._scans = [], 0

    def open_path(self, path):
        if self.open_error:
            raise self.open_error
        self.opened.append(path)

    def visible_windows(self):
        wins = {100: ("Old", "Cls")}
        if self.opened and self.new_window:
            self._scans += 1
            if self._scans > self.appear_after:
                wins[200] = self.new_window
        return wins

    def foreground_window(self):
        if self.opened and self.fg_after:
            return 300, self.fg_after
        return 100, "Old"


class Env:
    def __init__(self, test, dup=False, apps=(), **probe_kw):
        build_tree(test.base, dup_resume=dup)
        self.settings = settings_for(test.base)
        self.index = FileIndex(self.settings)
        self.index.refresh()
        self.probe = FakeProbe(**probe_kw)
        app_settings = AppSettings(data_dir=str(test.base / "data"))
        self.apps = AppIndex(self.probe, app_settings)
        self.apps._set_system([AppEntry(n, n.lower()) for n in apps], 1.0)
        self.cap = OpenFileCapability(self.index, self.probe, self.apps, app_settings, self.settings)
        test.addCleanup(self._cleanup)

    def _cleanup(self):
        if getattr(self, "index", None) and self.index._thread and self.index._thread.is_alive():
            self.index._thread.join(timeout=2.0)

    def run(self, text):
        args = self.cap.match(req(text))
        return args, self.cap.execute(args)


class CapabilityTests(Tmp):
    def test_open_a_file_by_name_and_type(self):
        env = Env(self)
        args, res = env.run("open my resume pdf")
        self.assertEqual((res.outcome, res.message), (TaskOutcome.SUCCESS, "Opened Resume.pdf."))
        self.assertEqual(env.probe.opened, [str(self.base / "Documents" / "Resume.pdf")])
        self.assertTrue(env.cap.verify(args, res))

    def test_open_a_folder(self):
        env = Env(self, new_window=("Downloads", "CabinetWClass"))
        args, res = env.run("open downloads folder")
        self.assertEqual(res.message, "Opened Downloads.")
        self.assertEqual(env.probe.opened, [str(self.base / "Downloads")])
        self.assertTrue(env.cap.verify(args, res))

    def test_a_deep_file_in_a_subfolder(self):
        env = Env(self)
        _, res = env.run("open the trip plan presentation")
        self.assertEqual(res.message, "Opened trip plan.pptx.")

    def test_two_files_with_the_same_name_are_ambiguous(self):
        env = Env(self, dup=True)
        _, res = env.run("open resume pdf")
        self.assertEqual(res.outcome, TaskOutcome.FAILED)
        self.assertIn("Resume.pdf in Documents", res.message)
        self.assertIn("Resume.pdf in Downloads", res.message)
        self.assertEqual(env.probe.opened, [])

    def test_not_found_is_only_claimed_when_it_is_clearly_a_file(self):
        env = Env(self)
        _, res = env.run("open budget pdf")
        self.assertEqual(res.message, "I couldn't find a file called budget.")
        _, res = env.run("open missing folder")
        self.assertEqual(res.message, "I couldn't find a folder called missing.")
        self.assertIsNone(env.cap.match(req("open youtube")))      # plain unknown word: not ours

    def test_apps_win(self):
        env = Env(self, apps=["Spotify", "Google Chrome"])
        touch(self.base / "Documents" / "Spotify.pdf")
        env.index.refresh()
        self.assertIsNone(env.cap.match(req("open spotify")))
        self.assertIsNone(env.cap.match(req("open chrome")))
        self.assertIsNotNone(env.cap.match(req("open resume")))        # real files are still found

    def test_other_sentences_are_not_ours(self):
        env = Env(self)
        for t in ["close resume", "what time is it", "resume pdf", "open", "open the resume of the person who "
                  "applied yesterday for the job"]:
            self.assertIsNone(env.cap.match(req(t)), t)

    def test_quick_refresh_finds_a_file_downloaded_a_moment_ago(self):
        env = Env(self)
        touch(self.base / "Downloads" / "Boarding Pass.pdf")
        args, res = env.run("open boarding pass")
        self.assertEqual(res.message, "Opened Boarding Pass.pdf.")

    def test_programs_and_scripts_are_never_opened_even_if_the_list_is_wrong(self):
        env = Env(self)
        for name in ["installer.exe", "script.py", "setup.msi"]:
            p = touch(self.base / "Documents" / name)
            item = {"display": name, "name": p.stem, "path": str(p), "ext": p.suffix, "is_dir": False}
            res = env.cap.execute({"kind": "FOUND", "query": p.stem, "want_dir": False, "items": [item], "labels": [name]})
            self.assertEqual(res.outcome, TaskOutcome.FAILED, name)
            self.assertIn("kind of file", res.message)
        self.assertEqual(env.probe.opened, [])

    def test_a_file_outside_your_folders_is_never_opened(self):
        env = Env(self)
        p = touch(self.base / "outside" / "secret.pdf")
        item = {"display": "secret.pdf", "name": "secret", "path": str(p), "ext": ".pdf", "is_dir": False}
        res = env.cap.execute({"kind": "FOUND", "query": "secret", "want_dir": False, "items": [item], "labels": []})
        self.assertIn("own folders", res.message)
        self.assertEqual(env.probe.opened, [])

    def test_a_file_deleted_after_the_list_was_made(self):
        env = Env(self)
        args = env.cap.match(req("open resume pdf"))
        (self.base / "Documents" / "Resume.pdf").unlink()
        res = env.cap.execute(args)
        self.assertEqual(res.message, "That file isn't there any more.")

    def test_open_error(self):
        env = Env(self, open_error=OSError("no handler"))
        _, res = env.run("open resume pdf")
        self.assertEqual((res.outcome, res.message), (TaskOutcome.FAILED, "I couldn't open Resume.pdf."))

    def test_verify_cases(self):
        slow = Env(self, appear_after=5)
        args, res = slow.run("open resume pdf")
        self.assertTrue(slow.cap.verify(args, res))
        other = Env(self, new_window=("Some Popup", "Dialog"))
        args, res = other.run("open resume pdf")
        self.assertTrue(other.cap.verify(args, res))                 # any new window counts, title is better
        front = Env(self, new_window=None, fg_after="resume.pdf - Edge")
        args, res = front.run("open resume pdf")
        self.assertTrue(front.cap.verify(args, res))                 # already-open file brought to front
        none = Env(self, new_window=None)
        args, res = none.run("open resume pdf")
        self.assertFalse(none.cap.verify(args, res))

    def test_a_folder_window_is_not_proof_for_a_file(self):
        env = Env(self, new_window=("Documents", "CabinetWClass"))
        args, res = env.run("open resume pdf")
        self.assertFalse(env.cap.verify(args, res))

    def test_time_budget_fits_core_limit(self):
        self.assertGreater(OpenFileCapability.verify_timeout_s, FileSettings().verify_seconds)


class CoreIntegrationTests(Tmp):
    def test_end_to_end_and_states(self):
        env = Env(self)
        reg = CapabilityRegistry()
        reg.register(env.cap)
        states = []
        r = EvaCore(reg, on_state=states.append).handle(req("open sales report excel", conf=0.9))
        self.assertEqual((r.outcome, r.reason, r.reply), (TaskOutcome.SUCCESS, "ok", "Opened Sales Report.xlsx."))
        self.assertEqual(states, [InterfaceState.PROCESSING, InterfaceState.EXECUTING, InterfaceState.BACKGROUND])

    def test_never_claims_success_when_no_window_appears(self):
        env = Env(self, new_window=None)
        reg = CapabilityRegistry()
        reg.register(env.cap)
        r = EvaCore(reg).handle(req("open resume pdf"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.FAILED, "verified_failed"))
        self.assertNotIn("Opened", r.reply)

    def test_default_registry_has_no_overlap(self):
        from eva.capabilities.defaults import register_default_capabilities
        build_tree(self.base)
        reg = CapabilityRegistry()
        probe = FakeProbe()
        probe.list_start_apps = lambda: [{"Name": "Spotify", "AppID": "sp"}, {"Name": "Notepad", "AppID": "np"}]
        probe.list_windows = lambda: []
        register_default_capabilities(reg, probe=probe, app_settings=AppSettings(data_dir=str(self.base / "data")),
                                      file_settings=settings_for(self.base), refresh_apps=False)
        self.assertEqual(reg.names(), ["open_app", "open_file", "close_app", "open_url", "web_search",
                                       "tell_time", "tell_date", "battery_status",
                                       "volume_up", "volume_down", "volume_mute", "take_screenshot"])
        file_cap = [c for c in reg._capabilities if c.name == "open_file"][0]
        file_cap._index.refresh()
        for phrase, expected in [("open notepad", ["open_app"]), ("close notepad", ["close_app"]),
                                 ("what time is it", ["tell_time"]), ("open resume pdf", ["open_file"]),
                                 ("open downloads folder", ["open_file"])]:
            found = [c.name for c, _ in reg.find(req(phrase))]
            self.assertEqual(found, expected, phrase)
        if file_cap._index._thread and file_cap._index._thread.is_alive():
            file_cap._index._thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
