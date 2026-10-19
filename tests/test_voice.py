"""Run from the EVA folder:  python -m unittest tests.test_voice -v
(No microphone or AI model needed. These test the stop-recording and cleanup rules.)"""
import unittest

from eva.input.voice_logic import EndpointDetector, Verdict, is_junk, to_pynput_combo

STEP = 0.05
LOUD, QUIET = 0.1, 0.001


def make(**overrides) -> EndpointDetector:
    args = dict(threshold=0.012, silence_seconds=1.2, max_seconds=15.0,
                no_speech_timeout=5.0, min_speech_seconds=0.25)
    args.update(overrides)
    return EndpointDetector(**args)


def run(detector: EndpointDetector, levels) -> Verdict:
    """Feed levels one by one; return the first verdict that is not CONTINUE."""
    for level in levels:
        verdict = detector.feed(level, STEP)
        if verdict is not Verdict.CONTINUE:
            return verdict
    return Verdict.CONTINUE


class EndpointTests(unittest.TestCase):
    def test_speech_then_silence_is_done(self):
        d = make()
        verdict = run(d, [LOUD] * 20 + [QUIET] * 40)   # 1 s of speech, then 2 s quiet
        self.assertEqual(verdict, Verdict.DONE)
        self.assertLess(d.elapsed, 2.5)

    def test_pause_shorter_than_limit_does_not_stop(self):
        d = make()
        levels = [LOUD] * 10 + [QUIET] * 10 + [LOUD] * 10   # 0.5 s pause in the middle
        self.assertEqual(run(d, levels), Verdict.CONTINUE)

    def test_nobody_speaks_gives_no_speech(self):
        d = make()
        self.assertEqual(run(d, [QUIET] * 200), Verdict.NO_SPEECH)
        self.assertAlmostEqual(d.elapsed, 5.0, delta=0.1)

    def test_tiny_click_is_not_speech(self):
        d = make()
        verdict = run(d, [LOUD] * 2 + [QUIET] * 200)   # 0.1 s blip only
        self.assertEqual(verdict, Verdict.NO_SPEECH)

    def test_hard_stop_at_max_length(self):
        d = make(max_seconds=3.0)
        verdict = run(d, [LOUD] * 200)                 # talks forever
        self.assertEqual(verdict, Verdict.DONE)
        self.assertAlmostEqual(d.elapsed, 3.0, delta=0.1)

    def test_peak_level_is_tracked(self):
        d = make()
        run(d, [QUIET] * 5 + [0.2] + [QUIET] * 5)
        self.assertEqual(d.peak, 0.2)


class JunkTests(unittest.TestCase):
    def test_junk_is_detected(self):
        for text in ("", "   ", ".", "...", "You.", "um", "Hmm...", None):
            self.assertTrue(is_junk(text), repr(text))

    def test_real_commands_are_kept(self):
        for text in ("open notepad", "Hey EVA open chrome", "what time is it", "thank you"):
            self.assertFalse(is_junk(text), text)

    def test_other_languages_are_not_junk(self):
        self.assertFalse(is_junk("नोटपैड खोलो"))


class HotkeyFormatTests(unittest.TestCase):
    def test_converts_to_library_format(self):
        self.assertEqual(to_pynput_combo("ctrl+alt+e"), "<ctrl>+<alt>+e")
        self.assertEqual(to_pynput_combo("Ctrl + Shift + Space"), "<ctrl>+<shift>+<space>")
        self.assertEqual(to_pynput_combo("control+alt+e"), "<ctrl>+<alt>+e")

    def test_bad_hotkeys_are_rejected(self):
        for combo in ("", "e", "ctrl", "e+r"):
            with self.assertRaises(ValueError, msg=combo):
                to_pynput_combo(combo)


if __name__ == "__main__":
    unittest.main()
