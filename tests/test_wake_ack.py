import unittest

from eva.input.wake_ack import WakeAckFlow


class Harness:
    """Fake speaker + fake timer. Time only moves when the test says so."""

    def __init__(self, can_speak=True, speak_error=None, settle=200, fallback=10000):
        self.now = 0.0
        self.timers = []          # (due_ms, fn)
        self.events = []
        self.spoken = []
        self.can_speak, self.speak_error = can_speak, speak_error
        self.flow = WakeAckFlow(
            speak=self._speak, schedule=self._schedule,
            start_listening=lambda: self.events.append("LISTEN"),
            on_waiting=lambda: self.events.append("WAKING"),
            ack_text="Yes boss.", settle_ms=settle, fallback_ms=fallback,
            clock=lambda: self.now / 1000.0,
        )

    def _speak(self, text):
        if self.speak_error:
            raise self.speak_error
        self.spoken.append(text)
        return self.can_speak

    def _schedule(self, ms, fn):
        self.timers.append((self.now + ms, fn))

    def advance(self, ms):
        self.now += ms
        due = [t for t in self.timers if t[0] <= self.now]
        self.timers = [t for t in self.timers if t[0] > self.now]
        for _, fn in sorted(due, key=lambda t: t[0]):
            fn()


class WakeAckTests(unittest.TestCase):
    def test_happy_path_waits_for_her_voice_then_settles_then_listens(self):
        h = Harness()
        h.flow.begin()
        self.assertEqual((h.spoken, h.events), (["Yes boss."], ["WAKING"]))
        self.assertTrue(h.flow.active)
        h.advance(1500)                                  # she is still talking: do NOT listen yet
        self.assertEqual(h.events, ["WAKING"])
        h.flow.speech_ended()
        h.advance(199)                                   # settle pause not over
        self.assertEqual(h.events, ["WAKING"])
        h.advance(1)
        self.assertEqual(h.events, ["WAKING", "LISTEN"])
        self.assertFalse(h.flow.active)

    def test_no_arbitrary_sleep_a_long_ack_is_still_waited_for(self):
        h = Harness()
        h.flow.begin()
        h.advance(8000)                                  # longer than any fixed 2 s sleep
        self.assertNotIn("LISTEN", h.events)
        h.flow.speech_ended()
        h.advance(200)
        self.assertIn("LISTEN", h.events)

    def test_cannot_speak_listens_right_away(self):
        h = Harness(can_speak=False)
        h.flow.begin()
        self.assertEqual(h.events, ["WAKING", "LISTEN"])
        self.assertFalse(h.flow.active)

    def test_speak_crash_listens_right_away(self):
        h = Harness(speak_error=RuntimeError("tts broke"))
        h.flow.begin()
        self.assertEqual(h.events, ["WAKING", "LISTEN"])

    def test_safety_timer_when_voice_never_finishes(self):
        h = Harness()
        h.flow.begin()
        h.advance(9999)
        self.assertEqual(h.events, ["WAKING"])
        h.advance(1)
        self.assertEqual(h.events, ["WAKING", "LISTEN"])
        h.flow.speech_ended()                            # late "finished" must not listen twice
        h.advance(500)
        self.assertEqual(h.events.count("LISTEN"), 1)

    def test_cancel_before_voice_ends(self):
        h = Harness()
        h.flow.begin()
        h.flow.cancel()
        h.flow.speech_ended()                            # the stopped voice reports "finished"
        h.advance(20000)
        self.assertEqual(h.events, ["WAKING"])
        self.assertFalse(h.flow.active)

    def test_cancel_during_settle_pause(self):
        h = Harness()
        h.flow.begin()
        h.flow.speech_ended()
        h.advance(100)
        h.flow.cancel()                                  # hotkey pressed during the settle pause
        h.advance(500)
        self.assertEqual(h.events, ["WAKING"])

    def test_other_speech_ending_later_does_not_start_listening(self):
        h = Harness()
        h.flow.begin()
        h.flow.speech_ended()
        h.advance(200)
        h.flow.speech_ended()                            # a later reply finishing
        h.advance(500)
        self.assertEqual(h.events.count("LISTEN"), 1)

    def test_speech_ended_without_wake_word_is_ignored(self):
        h = Harness()
        h.flow.speech_ended()
        h.advance(1000)
        self.assertEqual(h.events, [])

    def test_second_wake_word_restarts_cleanly(self):
        h = Harness()
        h.flow.begin()
        h.flow.begin()                                   # said "Eva" twice quickly
        h.flow.speech_ended()
        h.advance(10000)
        self.assertEqual(h.events.count("LISTEN"), 1)

    def test_a_failing_listen_callback_does_not_crash(self):
        h = Harness()
        h.flow._start = lambda: 1 / 0
        h.flow.begin()
        h.flow.speech_ended()
        h.advance(200)                                   # must not raise


if __name__ == "__main__":
    unittest.main()
