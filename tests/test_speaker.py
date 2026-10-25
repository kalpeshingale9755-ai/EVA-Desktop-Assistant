import threading
import time
import unittest

from eva.response.speaker import Speaker
from eva.response.backends import TTSBackend
from eva.response.text_prep import prepare_for_speech, TRUNCATED_NOTE
from eva.response.tts_settings import TTSSettings


class FakeBackend(TTSBackend):
    def __init__(self, name="fake", fail=False, duration=0.0):
        self.name, self.fail, self.duration = name, fail, duration
        self.spoken = []

    def speak(self, text, stop_event):
        if self.fail:
            raise RuntimeError("boom")
        self.spoken.append(text)
        stop_event.wait(self.duration)   # returns early if stopped


class TextPrepTests(unittest.TestCase):
    def test_markdown_removed(self):
        out = prepare_for_speech("## Title\n- **bold** item\n[site](https://x.com) `code`")
        self.assertEqual(out, "Title bold item site code")

    def test_code_block_and_url(self):
        out = prepare_for_speech("Run this:\n```py\nprint(1)\n```\nSee https://a.b/c")
        self.assertIn("code block omitted", out)
        self.assertNotIn("print", out)
        self.assertNotIn("http", out)

    def test_emoji_removed(self):
        self.assertEqual(prepare_for_speech("Done \U0001F600"), "Done")

    def test_truncates_at_sentence(self):
        text = "First sentence here. " + "word " * 200
        out = prepare_for_speech(text, 60)
        self.assertTrue(out.endswith(TRUNCATED_NOTE))
        self.assertTrue(out.startswith("First sentence here."))

    def test_empty(self):
        self.assertEqual(prepare_for_speech("   "), "")


class SpeakerTests(unittest.TestCase):
    def make(self, backends, **cb):
        sp = Speaker(backends, TTSSettings(retry_main_after_s=60), **cb)
        sp.start()
        self.addCleanup(sp.shutdown)
        return sp

    def test_speaks_in_order(self):
        b = FakeBackend()
        sp = self.make([b])
        sp.speak("one"); sp.speak("two"); sp.speak("three")
        self.assertTrue(sp.wait_idle(3))
        self.assertEqual(b.spoken, ["one", "two", "three"])

    def test_fallback_used_when_main_fails(self):
        main, fb = FakeBackend("main", fail=True), FakeBackend("fb")
        sp = self.make([main, fb])
        sp.speak("hello")
        sp.wait_idle(3)
        self.assertEqual(fb.spoken, ["hello"])
        self.assertEqual(sp.last_backend_used, "fb")

    def test_main_skipped_after_failure(self):
        calls = []

        class Counting(FakeBackend):
            def speak(self, text, ev):
                calls.append(text)
                raise RuntimeError("offline")

        fb = FakeBackend("fb")
        sp = self.make([Counting("main"), fb])
        sp.speak("a"); sp.wait_idle(3)
        sp.speak("b"); sp.wait_idle(3)
        self.assertEqual(calls, ["a"])          # main tried only once
        self.assertEqual(fb.spoken, ["a", "b"])

    def test_error_callback_when_all_fail(self):
        errs = []
        sp = self.make([FakeBackend("m", fail=True), FakeBackend("f", fail=True)],
                       on_error=errs.append)
        sp.speak("hi"); sp.wait_idle(3)
        self.assertEqual(len(errs), 1)

    def test_stop_interrupts_and_clears_queue(self):
        b = FakeBackend(duration=5)
        sp = self.make([b])
        sp.speak("long one"); sp.speak("queued")
        time.sleep(0.2)
        self.assertTrue(sp.is_speaking)
        t = time.monotonic()
        sp.stop()
        self.assertTrue(sp.wait_idle(2))
        self.assertLess(time.monotonic() - t, 1.5)
        self.assertEqual(b.spoken, ["long one"])   # "queued" never spoken

    def test_speak_after_stop_still_works(self):
        b = FakeBackend(duration=5)
        sp = self.make([b])
        sp.speak("first"); time.sleep(0.2)
        sp.speak("second", interrupt=True)
        time.sleep(0.2)
        sp.stop()
        sp.speak("third")
        sp.wait_idle(3)
        self.assertIn("third", b.spoken)

    def test_callbacks_start_and_end_once_for_queue(self):
        ev = []
        sp = self.make([FakeBackend(duration=0.05)],
                       on_start=lambda: ev.append("s"), on_end=lambda: ev.append("e"))
        sp.speak("a"); sp.speak("b")
        sp.wait_idle(3); time.sleep(0.1)
        self.assertEqual(ev.count("e"), 1)
        self.assertEqual(ev[-1], "e")

    def test_mute_blocks_speech(self):
        b = FakeBackend()
        sp = self.make([b])
        sp.mute(True)
        self.assertFalse(sp.speak("silent"))
        sp.mute(False)
        self.assertTrue(sp.speak("loud"))
        sp.wait_idle(3)
        self.assertEqual(b.spoken, ["loud"])


if __name__ == "__main__":
    unittest.main()
