"""Stage 6a planner tests. Run from EVA:  python -m unittest tests.test_planner -v
No internet or key needed (fake gateway)."""
import json
import threading
import unittest

from eva.brain import ArgSpec, BrainSettings, BrainStatus, CapabilitySpec, default_specs
from eva.brain.gateway import BaseGateway, GatewayRateLimited, GatewayTimeout, GatewayUnavailable
from eva.brain.interpreter import InterpretError
from eva.planner import (PlanResult, PlanRunner, PlanSettings, PlanStep, Planner, describe_plan,
                         interpret_plan, web_specs)

SPECS = {**default_specs(), **web_specs()}


def plan_json(steps, **kw):
    d = {"kind": "plan", "steps": steps, "confidence": 0.9, "reply": "Doing it."}
    d.update(kw)
    return json.dumps(d)


SEARCH = {"capability": "web_search", "args": {"query": "python tutorials"}}
OPEN_YT = {"capability": "open_url", "args": {"url": "https://www.youtube.com"}}


class FakeGateway(BaseGateway):
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


class InterpretPlanTests(unittest.TestCase):
    def test_valid_two_step_plan(self):
        kind, reply, steps, conf = interpret_plan(plan_json([SEARCH, OPEN_YT]), SPECS)
        self.assertEqual(kind, "plan")
        self.assertEqual([s.capability for s in steps], ["web_search", "open_url"])
        self.assertFalse(conf)

    def test_rejections(self):
        bad = [
            plan_json([]),
            plan_json([{"capability": "delete_files", "args": {}}]),
            plan_json([SEARCH, {"capability": "open_app", "args": {"name": "a & b"}}]),
            plan_json([{"capability": "open_url", "args": {"url": "x", "extra": 1}}]),
            plan_json([SEARCH] * 6),
            plan_json([SEARCH], confidence=0.1),
            plan_json([SEARCH], confidence="high"),
            plan_json("not a list"),
            plan_json([["web_search"]]),
            '{"kind":"action"}', "nonsense", "",
        ]
        for raw in bad:
            with self.assertRaises(InterpretError, msg=raw[:60]):
                interpret_plan(raw, SPECS)

    def test_chat_and_unknown_ignore_steps(self):
        raw = json.dumps({"kind": "chat", "steps": [SEARCH], "reply": "Hello.", "confidence": 1})
        kind, reply, steps, _ = interpret_plan(raw, SPECS)
        self.assertEqual((kind, steps), ("chat", ()))

    def test_confirmation_flag_comes_from_spec(self):
        specs = {"close_app": CapabilitySpec("close_app", "Close an app",
                 {"name": ArgSpec(max_len=60)}, needs_confirmation=True)}
        raw = plan_json([{"capability": "close_app", "args": {"name": "chrome"}}],
                        needs_confirmation=False)
        self.assertTrue(interpret_plan(raw, specs)[3])


class PlannerTests(unittest.TestCase):
    def make(self, reply=None, error=None, **kw):
        gw = FakeGateway(reply, error)
        return Planner(settings=BrainSettings(api_key="x", **kw), gateway=gw, specs=SPECS), gw

    def test_plan_flow(self):
        p, _ = self.make(json.loads(plan_json([SEARCH, OPEN_YT])))
        r = p.plan("search for python tutorials and open youtube")
        self.assertTrue(r.ok)
        self.assertEqual((r.kind, len(r.steps), r.task_outcome), ("plan", 2, "SUCCESS"))

    def test_bad_plan_is_rejected_with_safe_reply(self):
        p, _ = self.make(json.loads(plan_json([{"capability": "format_disk", "args": {}}])))
        r = p.plan("wipe it")
        self.assertEqual((r.status, r.steps, r.task_outcome), (BrainStatus.REJECTED, (), "FAILED"))
        self.assertTrue(r.reply)

    def test_errors_map_to_statuses(self):
        cases = [(GatewayRateLimited("x"), BrainStatus.RATE_LIMITED, "FAILED"),
                 (GatewayTimeout("x"), BrainStatus.TIMEOUT, "TIMEOUT"),
                 (GatewayUnavailable("x"), BrainStatus.UNAVAILABLE, "FAILED"),
                 (RuntimeError("boom"), BrainStatus.ERROR, "FAILED")]
        for err, status, outcome in cases:
            r = self.make(error=err)[0].plan("hi")
            self.assertEqual((r.status, r.task_outcome), (status, outcome))

    def test_disabled_and_empty(self):
        self.assertEqual(Planner(settings=BrainSettings(enabled=False), gateway=FakeGateway({}),
                                 specs=SPECS).plan("hi").status, BrainStatus.UNAVAILABLE)
        self.assertEqual(self.make({})[0].plan("   ").status, BrainStatus.REJECTED)

    def test_prompt_has_capabilities_and_limits(self):
        p, gw = self.make({"kind": "unknown", "steps": [], "confidence": 0, "reply": "No."})
        p.plan("x")
        prompt = gw.calls[0][0]
        self.assertIn("open_url", prompt)
        self.assertIn("web_search", prompt)
        self.assertIn("cannot see web pages", prompt)
        self.assertNotIn("close_app", prompt)

    def test_history_only_in_conversation_mode(self):
        chat = {"kind": "chat", "steps": [], "confidence": 1, "reply": "Sure."}
        p, gw = self.make(chat)
        p.plan("hello", "CONVERSATION_MODE")
        p.plan("and now?", "CONVERSATION_MODE")
        self.assertEqual(len(gw.calls[1][1]), 3)
        p.plan("open notepad", "COMMAND_MODE")
        self.assertEqual(len(gw.calls[2][1]), 1)


def plan_of(*steps, reply="All done.", needs=False):
    return PlanResult(BrainStatus.OK, "plan", reply, tuple(PlanStep(c, a) for c, a in steps), needs)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.ran, self.slept = [], []

    def ok_step(self, step):
        self.ran.append(step.capability)
        return "SUCCESS", f"ok {step.capability}"

    def runner(self, fn=None, confirm=None):
        return PlanRunner(fn or self.ok_step, confirm=confirm, settings=PlanSettings(step_delay_seconds=0.5),
                          sleep=self.slept.append)

    def test_all_steps_succeed(self):
        out = self.runner().run(plan_of(("web_search", {"query": "a"}), ("open_url", {"url": "u"})))
        self.assertEqual((out.outcome, out.completed, out.message), ("SUCCESS", 2, "All done."))
        self.assertEqual(self.ran, ["web_search", "open_url"])
        self.assertEqual(self.slept, [0.5])           # one pause, between the two steps

    def test_stops_at_first_failure_partial(self):
        def fn(step):
            self.ran.append(step.capability)
            return ("FAILED", "No browser.") if step.capability == "open_url" else ("SUCCESS", "ok")
        out = self.runner(fn).run(plan_of(("web_search", {}), ("open_url", {}), ("web_search", {})))
        self.assertEqual((out.outcome, self.ran), ("PARTIAL", ["web_search", "open_url"]))
        self.assertIn("step 2 of 3", out.message)

    def test_first_step_fails_is_failed_and_timeout_is_kept(self):
        out = self.runner(lambda s: ("FAILED", "x")).run(plan_of(("open_url", {}), ("open_url", {})))
        self.assertEqual(out.outcome, "FAILED")
        out = self.runner(lambda s: ("TIMEOUT", "slow")).run(plan_of(("open_url", {})))
        self.assertEqual(out.outcome, "TIMEOUT")

    def test_crashing_step_is_contained(self):
        def boom(step):
            raise RuntimeError("bad capability")
        out = self.runner(boom).run(plan_of(("open_url", {})))
        self.assertEqual(out.outcome, "FAILED")

    def test_dangerous_plan_needs_yes(self):
        plan = plan_of(("close_app", {"name": "chrome"}), needs=True)
        self.assertEqual(self.runner().run(plan).outcome, "FAILED")            # no confirm callback
        self.assertEqual(self.runner(confirm=lambda q: False).run(plan).outcome, "FAILED")
        self.assertEqual(self.ran, [])
        asked = []
        out = self.runner(confirm=lambda q: asked.append(q) or True).run(plan)
        self.assertEqual((out.outcome, self.ran), ("SUCCESS", ["close_app"]))
        self.assertIn("close_app", asked[0])

    def test_cancel_stops_before_next_step(self):
        cancel = threading.Event()

        def fn(step):
            self.ran.append(step.capability)
            cancel.set()
            return "SUCCESS", "ok"
        out = self.runner(fn).run(plan_of(("web_search", {}), ("open_url", {})), cancel=cancel)
        self.assertEqual((out.outcome, self.ran), ("PARTIAL", ["web_search"]))

    def test_non_plan_is_not_run(self):
        chat = PlanResult(BrainStatus.OK, "chat", "Hello.")
        out = self.runner().run(chat)
        self.assertEqual((out.outcome, self.ran), ("UNKNOWN", []))

    def test_describe_plan(self):
        self.assertIn("web_search(query=cats)", describe_plan(plan_of(("web_search", {"query": "cats"}))))


if __name__ == "__main__":
    unittest.main()
