import unittest
from datetime import datetime

from eva.capabilities.clock import ClockCapability, format_date, format_time
from eva.capabilities.text_match import normalize
from eva.common.types import Mode, TaskOutcome
from eva.core.core import EvaCore
from eva.core.registry import CapabilityRegistry
from eva.understanding.request import InputSource, RequestObject


def req(text, conf=None):
    return RequestObject(raw_text=text, clean_text=text, source=InputSource.VOICE,
                         mode=Mode.COMMAND_MODE, confidence=conf)


class NormalizeTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("  Open the Notepad, please! "), "open the notepad please")
        self.assertEqual(normalize("What\u2019s the TIME?"), "whats the time")


class ClockTests(unittest.TestCase):
    NOW = datetime(2026, 10, 24, 15, 5)     # Saturday

    def c(self):
        return ClockCapability(now_fn=lambda: self.NOW)

    def test_formats(self):
        self.assertEqual(format_time(self.NOW), "3:05 PM")
        self.assertEqual(format_time(datetime(2026, 1, 1, 0, 7)), "12:07 AM")
        self.assertEqual(format_time(datetime(2026, 1, 1, 12, 0)), "12:00 PM")
        self.assertEqual(format_date(self.NOW), "Saturday, 24 October 2026")

    def test_time_phrases(self):
        for t in ["what time is it", "What's the time?", "tell me the time", "what is the time",
                  "can you tell me the time please", "current time", "what time is it right now",
                  "what is the current time", "the time now"]:
            self.assertEqual(self.c().match(req(t)), {"time": True, "date": False}, t)

    def test_date_phrases(self):
        for t in ["what's the date", "what is today's date", "today's date", "what day is it",
                  "what day is it today", "tell me the date", "what is today", "current date"]:
            self.assertEqual(self.c().match(req(t)), {"time": False, "date": True}, t)

    def test_both_phrases(self):
        for t in ["what is the date and time", "time and date", "tell me the date and time"]:
            self.assertEqual(self.c().match(req(t)), {"time": True, "date": True}, t)

    def test_must_not_match(self):
        for t in ["what is the time complexity of quicksort", "what time does the shop open",
                  "set the time to 5", "time", "date", "open notepad",
                  "what is the date of the exam", "update the date"]:
            self.assertIsNone(self.c().match(req(t)), t)

    def test_replies(self):
        c = self.c()
        self.assertEqual(c.execute({"time": True, "date": False}).message, "It's 3:05 PM.")
        self.assertEqual(c.execute({"time": False, "date": True}).message,
                         "Today is Saturday, 24 October 2026.")
        self.assertEqual(c.execute({"time": True, "date": True}).message,
                         "It's 3:05 PM on Saturday, 24 October 2026.")


class ClockWithCoreTests(unittest.TestCase):
    def test_time_end_to_end(self):
        reg = CapabilityRegistry()
        reg.register(ClockCapability())
        r = EvaCore(reg).handle(req("what time is it"))
        self.assertEqual((r.outcome, r.capability), (TaskOutcome.SUCCESS, "clock"))
        self.assertRegex(r.reply, r"^It's \d{1,2}:\d{2} (AM|PM)\.$")

    def test_clock_ignores_open_commands(self):
        self.assertIsNone(ClockCapability().match(req("open spotify")))


if __name__ == "__main__":
    unittest.main()
