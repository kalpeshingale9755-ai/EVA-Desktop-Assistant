"""Stage 6b tests. Run from EVA:  python -m unittest tests.test_quick -v
Nothing real happens: no keys are pressed, no screen is captured (fakes are used)."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from eva.brain import BrainSettings, default_specs
from eva.brain.gateway import BaseGateway
from eva.capabilities.quick_actions import (DEFAULT_VOLUME_STEPS, MAX_VOLUME_STEPS, VK_VOLUME_DOWN,
                                            VK_VOLUME_MUTE, VK_VOLUME_UP, battery_status, take_screenshot,
                                            tell_date, tell_time, volume_down, volume_mute, volume_up)
from eva.planner.local_quick import parse_quick_command
from eva.planner.plan_interpreter import interpret_plan
from eva.planner.plan_specs import web_specs
from eva.planner.planner import Planner
from eva.planner.quick_specs import quick_specs


class TimeDateTests(unittest.TestCase):
    def test_time(self):
        self.assertEqual(tell_time(datetime(2026, 10, 27, 15, 5)).message, "It is 3:05 PM.")
        self.assertEqual(tell_time(datetime(2026, 10, 27, 0, 30)).message, "It is 12:30 AM.")
        self.assertEqual(tell_time(datetime(2026, 10, 27, 12, 0)).message, "It is 12:00 PM.")

    def test_date(self):
        self.assertEqual(tell_date(datetime(2026, 10, 27)).message, "Today is Tuesday, 27 October 2026.")


class BatteryTests(unittest.TestCase):
    def msg(self, **kw):
        s = {"ac": 1, "flag": 0, "percent": 80, "seconds": 0xFFFFFFFF}
        s.update(kw)
        return battery_status(lambda: s)

    def test_cases(self):
        self.assertEqual(self.msg(ac=1, flag=8).message, "Battery is at 80 percent and charging.")
        self.assertEqual(self.msg(ac=1, flag=0, percent=100).message, "Battery is at 100 percent and plugged in.")
        self.assertEqual(self.msg(ac=0, seconds=7800).message,
                         "Battery is at 80 percent and running on battery, about 2 hours 10 minutes left.")
        self.assertEqual(self.msg(ac=0).message, "Battery is at 80 percent and running on battery.")
        self.assertEqual(self.msg(ac=255).message, "Battery is at 80 percent.")
        self.assertEqual(self.msg(flag=128).message, "This computer has no battery.")
        self.assertEqual(self.msg(percent=255).outcome, "FAILED")

    def test_reader_failure(self):
        def boom():
            raise OSError("no windows")
        r = battery_status(boom)
        self.assertEqual(r.outcome, "FAILED")
        self.assertNotIn("no windows", r.message)


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.sent = []

    def send(self, vk, count):
        self.sent.append((vk, count))

    def test_keys_and_clamping(self):
        volume_up(sender=self.send)
        volume_down(3, sender=self.send)
        volume_mute(sender=self.send)
        volume_up(500, sender=self.send)
        volume_up(0, sender=self.send)
        volume_down(True, sender=self.send)       # a bool is not a number
        volume_up("loud", sender=self.send)
        self.assertEqual(self.sent, [(VK_VOLUME_UP, DEFAULT_VOLUME_STEPS), (VK_VOLUME_DOWN, 3),
                                     (VK_VOLUME_MUTE, 1), (VK_VOLUME_UP, MAX_VOLUME_STEPS),
                                     (VK_VOLUME_UP, 1), (VK_VOLUME_DOWN, DEFAULT_VOLUME_STEPS),
                                     (VK_VOLUME_UP, DEFAULT_VOLUME_STEPS)])

    def test_failure_is_contained(self):
        def boom(vk, count):
            raise OSError("x")
        r = volume_up(sender=boom)
        self.assertEqual(r.outcome, "FAILED")


class ScreenshotTests(unittest.TestCase):
    def test_success(self):
        with tempfile.TemporaryDirectory() as d:
            def cap(path):
                Path(path).write_bytes(b"PNGDATA")
            r = take_screenshot(Path(d) / "shots", capturer=cap, now=datetime(2026, 10, 27, 9, 8, 7))
            self.assertEqual((r.outcome, r.message), ("SUCCESS", "Screenshot saved in shots."))
            self.assertTrue((Path(d) / "shots" / "EVA_screenshot_20261027_090807.png").is_file())

    def test_failures(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(take_screenshot(d, capturer=lambda p: None).outcome, "FAILED")   # no file written

            def empty(path):
                Path(path).write_bytes(b"")
            self.assertEqual(take_screenshot(d, capturer=empty).outcome, "FAILED")           # empty file

            def boom(path):
                raise RuntimeError("powershell missing")
            r = take_screenshot(d, capturer=boom)
            self.assertEqual(r.outcome, "FAILED")
            self.assertNotIn("powershell", r.message)


class LocalQuickParserTests(unittest.TestCase):
    CASES = {
        "what time is it": "tell_time", "What's the time?": "tell_time", "tell me the time": "tell_time",
        "what is today's date": "tell_date", "what day is it": "tell_date", "whats the date": "tell_date",
        "battery": "battery_status", "what's my battery level": "battery_status",
        "how much battery is left": "battery_status", "is my laptop charging": "battery_status",
        "volume up": "volume_up", "increase the volume": "volume_up", "turn up the volume": "volume_up",
        "louder please": "volume_up", "volume down": "volume_down", "lower the volume": "volume_down",
        "mute": "volume_mute", "unmute the sound": "volume_mute", "Eva, mute": "volume_mute",
        "take a screenshot": "take_screenshot", "screenshot": "take_screenshot",
        "grab a snapshot of my screen": "take_screenshot",
    }

    def test_matches(self):
        for text, cap in self.CASES.items():
            m = parse_quick_command(text)
            self.assertIsNotNone(m, msg=text)
            self.assertEqual((m.capability, m.args), (cap, {}), msg=text)

    def test_not_quick(self):
        for t in ["", "open notepad", "set volume to 50", "increase volume by 10 percent",
                  "take a screenshot and open youtube", "what time does the shop open",
                  "what is the date of independence day", "mute the microphone and call",
                  "search for battery prices", "time", "date", "volume"]:
            self.assertIsNone(parse_quick_command(t), msg=t)


class FakeGateway(BaseGateway):
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate(self, system_prompt, messages):
        self.calls.append(system_prompt)
        return json.dumps(self.reply)


class QuickSpecsWithPlannerTests(unittest.TestCase):
    SPECS = {**default_specs(), **web_specs(), **quick_specs()}

    def test_planner_prompt_lists_quick_capabilities(self):
        gw = FakeGateway({"kind": "unknown", "steps": [], "confidence": 0, "reply": "No."})
        Planner(settings=BrainSettings(api_key="x"), gateway=gw, specs=self.SPECS).plan("hello")
        for name in ["tell_time", "battery_status", "volume_up", "take_screenshot"]:
            self.assertIn(name, gw.calls[0])
        self.assertIn("volume_up(steps: int, optional)", gw.calls[0])

    def test_plan_with_quick_steps(self):
        raw = json.dumps({"kind": "plan", "confidence": 0.9, "reply": "Taking a screenshot and checking the time.",
                          "steps": [{"capability": "take_screenshot", "args": {}},
                                    {"capability": "tell_time", "args": None},
                                    {"capability": "volume_down", "args": {"steps": 10}}]})
        kind, reply, steps, needs = interpret_plan(raw, self.SPECS)
        self.assertEqual([s.capability for s in steps], ["take_screenshot", "tell_time", "volume_down"])
        self.assertEqual(steps[2].args, {"steps": 10})

    def test_bad_quick_args_rejected(self):
        for args in [{"steps": "ten"}, {"steps": 2.5}, {"level": 50}, {"steps": True}]:
            raw = json.dumps({"kind": "plan", "confidence": 0.9, "reply": "",
                              "steps": [{"capability": "volume_up", "args": args}]})
            with self.assertRaises(Exception, msg=str(args)):
                interpret_plan(raw, self.SPECS)


if __name__ == "__main__":
    unittest.main()
