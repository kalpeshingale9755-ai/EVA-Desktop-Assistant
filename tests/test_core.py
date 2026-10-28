import time
import unittest

from eva.common.types import InterfaceState, Mode, TaskOutcome
from eva.core.capability import Capability, CapabilityResult, Risk
from eva.core.core import EvaCore, run_with_timeout
from eva.core.registry import CapabilityRegistry
from eva.core.settings import CoreSettings
from eva.brain import ArgSpec, Brain, BrainSettings, BrainStatus, CapabilitySpec
from eva.planner import Planner
from tests.test_brain import FakeGateway
from eva.understanding.request import InputSource, RequestObject

S = InterfaceState


def req(text, mode=Mode.COMMAND_MODE, conf=None, source=InputSource.VOICE):
    return RequestObject(raw_text=text, clean_text=text, source=source, mode=mode,
                         confidence=conf)


class Fake(Capability):
    """A test capability that handles any request containing its keyword."""

    def __init__(self, name="fake", keyword="notepad", risk=Risk.LOW, result=None,
                 verify=True, execute_error=None, sleep=0.0, timeout_s=2.0):
        self.name, self.keyword, self.risk, self.timeout_s = name, keyword, risk, timeout_s
        self._result = result or CapabilityResult(TaskOutcome.SUCCESS, "Opened Notepad.")
        self._verify, self._error, self._sleep = verify, execute_error, sleep
        self.executed = 0

    def match(self, request):
        return {"text": request.clean_text} if self.keyword in request.clean_text.lower() else None

    def execute(self, args):
        self.executed += 1
        if self._sleep:
            time.sleep(self._sleep)
        if self._error:
            raise self._error
        return self._result

    def verify(self, args, result):
        if isinstance(self._verify, Exception):
            raise self._verify
        return self._verify


def make_core(*caps, **kw):
    reg = CapabilityRegistry()
    for c in caps:
        reg.register(c)
    states = []
    kw.setdefault("brain", None)
    core = EvaCore(reg, on_state=states.append, **kw)
    return core, states


class ConversationTests(unittest.TestCase):
    def test_conversation_mode_waits_for_brain(self):
        cap = Fake()
        core, states = make_core(cap)
        r = core.handle(req("open notepad please", mode=Mode.CONVERSATION_MODE))
        self.assertEqual(r.reason, "conversation_not_ready")
        self.assertIn("open notepad please", r.reply)
        self.assertEqual(cap.executed, 0)               # never runs capabilities
        self.assertEqual(states, [S.PROCESSING, S.BACKGROUND])


class CommandTests(unittest.TestCase):
    def test_no_capability(self):
        core, states = make_core()
        r = core.handle(req("do a backflip"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.FAILED, "no_capability"))
        self.assertEqual(r.reply, "I don't know how to do that yet.")
        self.assertEqual(states, [S.PROCESSING, S.BACKGROUND])

    def test_success_flow_and_states(self):
        cap = Fake()
        core, states = make_core(cap)
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reason, r.capability), (TaskOutcome.SUCCESS, "ok", "fake"))
        self.assertEqual(r.reply, "Opened Notepad.")
        self.assertEqual(states, [S.PROCESSING, S.EXECUTING, S.BACKGROUND])
        self.assertEqual(cap.executed, 1)

    def test_verification_false_means_failed(self):
        core, _ = make_core(Fake(verify=False))
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.FAILED, "verified_failed"))
        self.assertNotIn("Opened", r.reply)             # EVA must not claim success

    def test_verification_crash_means_unknown(self):
        core, _ = make_core(Fake(verify=RuntimeError("x")))
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.UNKNOWN, "verify_error"))

    def test_execute_error_means_failed(self):
        core, states = make_core(Fake(execute_error=RuntimeError("boom")))
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.FAILED, "capability_error"))
        self.assertEqual(states[-1], S.BACKGROUND)

    def test_wrong_return_type_is_an_error(self):
        cap = Fake()
        cap.execute = lambda args: "oops"
        core, _ = make_core(cap)
        self.assertEqual(core.handle(req("open notepad")).reason, "capability_error")

    def test_timeout(self):
        core, _ = make_core(Fake(sleep=1.0, timeout_s=0.05))
        t = time.monotonic()
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.TIMEOUT, "timeout"))
        self.assertLess(time.monotonic() - t, 0.8)

    def test_partial_and_failed_pass_through(self):
        core, _ = make_core(Fake(result=CapabilityResult(TaskOutcome.PARTIAL, "Did half.")))
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reply), (TaskOutcome.PARTIAL, "Did half."))
        core, _ = make_core(Fake(result=CapabilityResult(TaskOutcome.FAILED)))
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reply), (TaskOutcome.FAILED, "I couldn't do that."))

    def test_two_matches_is_ambiguous(self):
        a, b = Fake("a"), Fake("b")
        core, _ = make_core(a, b)
        r = core.handle(req("open notepad"))
        self.assertEqual(r.reason, "ambiguous")
        self.assertEqual((a.executed, b.executed), (0, 0))

    def test_medium_and_high_risk_need_confirmation(self):
        for risk in (Risk.MEDIUM, Risk.HIGH):
            cap = Fake(risk=risk)
            core, _ = make_core(cap)
            r = core.handle(req("open notepad"))
            self.assertEqual((r.outcome, r.reason), (TaskOutcome.FAILED, "needs_confirmation"))
            self.assertEqual(cap.executed, 0)

    def test_low_voice_confidence_is_not_executed(self):
        cap = Fake()
        core, _ = make_core(cap)
        r = core.handle(req("open notepad", conf=0.2))
        self.assertEqual(r.reason, "low_confidence")
        self.assertEqual(cap.executed, 0)

    def test_good_and_missing_confidence_are_executed(self):
        cap = Fake()
        core, _ = make_core(cap)
        self.assertEqual(core.handle(req("open notepad", conf=0.9)).reason, "ok")
        self.assertEqual(core.handle(req("open notepad", conf=None,
                                         source=InputSource.TEXT)).reason, "ok")
        self.assertEqual(cap.executed, 2)

    def test_confidence_limit_is_a_setting(self):
        core, _ = make_core(Fake(), settings=CoreSettings(min_command_confidence=0.95))
        self.assertEqual(core.handle(req("open notepad", conf=0.9)).reason, "low_confidence")


class SafetyTests(unittest.TestCase):
    def test_matcher_crash_is_ignored(self):
        bad = Fake("bad")
        bad.match = lambda request: 1 / 0
        good = Fake("good")
        core, _ = make_core(bad, good)
        r = core.handle(req("open notepad"))
        self.assertEqual((r.reason, r.capability), ("ok", "good"))

    def test_internal_crash_never_raises(self):
        class BrokenRegistry:
            def find(self, request):
                raise RuntimeError("registry broke")
        states = []
        core = EvaCore(BrokenRegistry(), on_state=states.append)
        r = core.handle(req("open notepad"))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.FAILED, "internal_error"))
        self.assertEqual(states[-1], S.ERROR)

    def test_state_callback_crash_is_ignored(self):
        def bad(_state):
            raise RuntimeError("ui gone")
        reg = CapabilityRegistry()
        reg.register(Fake())
        core = EvaCore(reg, on_state=bad)
        self.assertEqual(core.handle(req("open notepad")).reason, "ok")

    def test_duplicate_and_unnamed_registration(self):
        reg = CapabilityRegistry()
        reg.register(Fake("x"))
        with self.assertRaises(ValueError):
            reg.register(Fake("x"))
        with self.assertRaises(ValueError):
            reg.register(Fake(""))
        self.assertEqual(len(reg), 1)

    def test_empty_args_dict_still_counts_as_a_match(self):
        cap = Fake()
        cap.match = lambda request: {}
        core, _ = make_core(cap)
        self.assertEqual(core.handle(req("anything")).reason, "ok")

    def test_result_has_request_id_and_time(self):
        core, _ = make_core(Fake())
        request = req("open notepad")
        r = core.handle(request)
        self.assertEqual(r.request_id, request.request_id)
        self.assertGreaterEqual(r.seconds, 0.0)

    def test_run_with_timeout_helper(self):
        self.assertEqual(run_with_timeout(lambda: 5, 1), 5)
        with self.assertRaises(ValueError):
            run_with_timeout(lambda: (_ for _ in ()).throw(ValueError("x")), 1)
        with self.assertRaises(TimeoutError):
            run_with_timeout(lambda: time.sleep(1), 0.05)


class BrainIntegrationTests(unittest.TestCase):
    def test_conversation_mode_uses_brain_chat(self):
        gateway = FakeGateway(reply={"kind": "chat", "capability": None, "args": {}, "confidence": 1.0,
                                     "reply": "I am EVA, nice to meet you."})
        brain = Brain(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(brain=brain)
        r = core.handle(req("who are you?", mode=Mode.CONVERSATION_MODE))
        self.assertEqual((r.outcome, r.reason, r.reply),
                         (TaskOutcome.SUCCESS, "chat", "I am EVA, nice to meet you."))

    def test_local_command_does_not_call_brain(self):
        gateway = FakeGateway(reply={"kind": "chat", "capability": None, "args": {}, "confidence": 1.0, "reply": "test"})
        brain = Brain(settings=BrainSettings(api_key="k"), gateway=gateway)
        reg = CapabilityRegistry()
        cap = Fake("fake", keyword="notepad")
        reg.register(cap)
        core = EvaCore(registry=reg, brain=brain)
        r = core.handle(req("open notepad"))
        self.assertEqual(r.reason, "ok")
        self.assertEqual(len(gateway.calls), 0)

    def test_unmatched_command_uses_brain_action(self):
        gateway = FakeGateway(reply={"kind": "action", "capability": "fake", "args": {"name": "notepad"},
                                     "confidence": 0.9, "reply": "Opening notepad."})
        brain = Brain(settings=BrainSettings(api_key="k"), gateway=gateway,
                      specs={"fake": CapabilitySpec("fake", "fake cap", {"name": ArgSpec()})})
        reg = CapabilityRegistry()
        cap = Fake("fake", keyword="notepad")
        reg.register(cap)
        core = EvaCore(registry=reg, brain=brain)
        r = core.handle(req("launch the writing app"))
        self.assertEqual((r.outcome, r.capability), (TaskOutcome.SUCCESS, "fake"))
        self.assertEqual(cap.executed, 1)

    def test_brain_action_permission_gate_checked(self):
        gateway = FakeGateway(reply={"kind": "action", "capability": "fake", "args": {"name": "notepad"},
                                     "confidence": 0.9, "reply": "Opening notepad."})
        brain = Brain(settings=BrainSettings(api_key="k"), gateway=gateway,
                      specs={"fake": CapabilitySpec("fake", "fake cap", {"name": ArgSpec()})})
        reg = CapabilityRegistry()
        cap = Fake("fake", keyword="notepad", risk=Risk.HIGH)
        reg.register(cap)
        core = EvaCore(registry=reg, brain=brain)
        r = core.handle(req("launch the writing app"))
        self.assertEqual(r.reason, "needs_confirmation")
        self.assertEqual(cap.executed, 0)

    def test_brain_error_returns_safe_reply(self):
        from eva.brain.gateway import GatewayTimeout
        gateway = FakeGateway(error=GatewayTimeout("timed out"))
        brain = Brain(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(brain=brain)
        r = core.handle(req("tell me a long story", mode=Mode.CONVERSATION_MODE))
        self.assertEqual((r.outcome, r.reason), (TaskOutcome.TIMEOUT, "brain_timeout"))
        self.assertEqual(r.reply, "That took too long. Please try again.")


class PlannerIntegrationTests(unittest.TestCase):
    def test_local_web_command_open_url_handled_without_planner(self):
        opened = []
        reg = CapabilityRegistry()
        from eva.capabilities.web_actions import OpenUrlCapability, WebSearchCapability
        reg.register(OpenUrlCapability(opener=lambda u: opened.append(u) or True))
        gateway = FakeGateway()
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(registry=reg, planner=planner)

        r = core.handle(req("open youtube"))
        self.assertEqual(r.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r.capability, "open_url")
        self.assertEqual(opened, ["https://www.youtube.com"])
        self.assertEqual(len(gateway.calls), 0)

    def test_local_web_command_web_search_handled_without_planner(self):
        opened = []
        reg = CapabilityRegistry()
        from eva.capabilities.web_actions import OpenUrlCapability, WebSearchCapability
        reg.register(WebSearchCapability(opener=lambda u: opened.append(u) or True))
        gateway = FakeGateway()
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(registry=reg, planner=planner)

        r = core.handle(req("search for python tutorials"))
        self.assertEqual(r.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r.capability, "web_search")
        self.assertTrue(opened[0].startswith("https://www.google.com/search?q=python"))
        self.assertEqual(len(gateway.calls), 0)

    def test_multistep_command_uses_planner_and_plan_runner(self):
        import json
        opened = []
        reg = CapabilityRegistry()
        from eva.capabilities.web_actions import OpenUrlCapability, WebSearchCapability
        reg.register(OpenUrlCapability(opener=lambda u: opened.append(u) or True))
        reg.register(WebSearchCapability(opener=lambda u: opened.append(u) or True))

        plan = {
            "kind": "plan",
            "steps": [
                {"capability": "web_search", "args": {"query": "python tutorials"}},
                {"capability": "open_url", "args": {"url": "https://www.youtube.com"}},
            ],
            "confidence": 0.95,
            "reply": "Searching for python tutorials and opening YouTube.",
        }
        gateway = FakeGateway(reply=json.dumps(plan))
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway)
        states = []
        core = EvaCore(registry=reg, planner=planner, on_state=states.append)

        r = core.handle(req("search for python tutorials and open youtube"))
        self.assertEqual(r.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r.reason, "ok")
        self.assertEqual(len(opened), 2)
        self.assertTrue(opened[0].startswith("https://www.google.com/search?q=python"))
        self.assertEqual(opened[1], "https://www.youtube.com")
        self.assertIn(InterfaceState.EXECUTING, states)
        self.assertIn(InterfaceState.RESPONDING, states)

    def test_planner_plan_with_cancel(self):
        import json, threading
        opened = []
        reg = CapabilityRegistry()
        from eva.capabilities.web_actions import OpenUrlCapability, WebSearchCapability
        cancel_ev = threading.Event()

        def opener(u):
            opened.append(u)
            cancel_ev.set()  # cancel right after first step
            return True

        reg.register(WebSearchCapability(opener=opener))
        reg.register(OpenUrlCapability(opener=opener))

        plan = {
            "kind": "plan",
            "steps": [
                {"capability": "web_search", "args": {"query": "python"}},
                {"capability": "open_url", "args": {"url": "https://www.youtube.com"}},
            ],
            "confidence": 0.95,
            "reply": "Doing both.",
        }
        gateway = FakeGateway(reply=json.dumps(plan))
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(registry=reg, planner=planner)

        r = core.handle(req("search python and open youtube"), cancel=cancel_ev)
        self.assertEqual(r.outcome, TaskOutcome.PARTIAL)
        self.assertIn("Stopped", r.reply)
        self.assertEqual(len(opened), 1)

    def test_planner_risky_plan_refused_without_confirm(self):
        import json
        reg = CapabilityRegistry()
        cap = Fake("danger", keyword="execute_danger", risk=Risk.HIGH)
        reg.register(cap)

        from eva.brain import CapabilitySpec
        specs = {"danger": CapabilitySpec("danger", "risky action", needs_confirmation=True)}
        plan = {
            "kind": "plan",
            "steps": [{"capability": "danger", "args": {}}],
            "confidence": 0.95,
            "reply": "Doing risky action.",
        }
        gateway = FakeGateway(reply=json.dumps(plan))
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway, specs=specs)
        core = EvaCore(registry=reg, planner=planner)  # confirm is None

        r = core.handle(req("do something else"))
        self.assertEqual(r.outcome, TaskOutcome.FAILED)
        self.assertIn("will not do that", r.reply)
        self.assertEqual(cap.executed, 0)

    def test_planner_risky_plan_allowed_with_confirm(self):
        import json
        reg = CapabilityRegistry()
        cap = Fake("danger", keyword="execute_danger", risk=Risk.HIGH)
        reg.register(cap)

        from eva.brain import CapabilitySpec
        specs = {"danger": CapabilitySpec("danger", "risky action", needs_confirmation=True)}
        plan = {
            "kind": "plan",
            "steps": [{"capability": "danger", "args": {}}],
            "confidence": 0.95,
            "reply": "Doing risky action.",
        }
        gateway = FakeGateway(reply=json.dumps(plan))
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway, specs=specs)
        core = EvaCore(registry=reg, planner=planner, confirm=lambda q: True)

        r = core.handle(req("do something else"))
        self.assertEqual(r.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(cap.executed, 1)

    def test_planner_chat_in_conversation_mode(self):
        import json
        gateway = FakeGateway(reply=json.dumps({
            "kind": "chat",
            "steps": [],
            "confidence": 0.9,
            "reply": "The capital of France is Paris.",
        }))
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(planner=planner)

        r = core.handle(req("what is the capital of France", mode=Mode.CONVERSATION_MODE))
        self.assertEqual(r.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r.reason, "chat")
        self.assertEqual(r.reply, "The capital of France is Paris.")

    def test_local_quick_commands_handled_without_planner(self):
        from datetime import datetime
        from eva.capabilities.quick_actions import (
            BatteryStatusCapability,
            TellDateCapability,
            TellTimeCapability,
            VolumeUpCapability,
        )
        sent = []
        reg = CapabilityRegistry()
        reg.register(TellTimeCapability(now_fn=lambda: datetime(2026, 10, 27, 15, 5)))
        reg.register(TellDateCapability(now_fn=lambda: datetime(2026, 10, 27, 15, 5)))
        reg.register(BatteryStatusCapability(reader=lambda: {"ac": 1, "flag": 8, "percent": 80, "seconds": 0xFFFFFFFF}))
        reg.register(VolumeUpCapability(sender=lambda vk, count: sent.append((vk, count))))

        gateway = FakeGateway()
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(registry=reg, planner=planner)

        r_time = core.handle(req("what time is it"))
        self.assertEqual(r_time.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r_time.capability, "tell_time")
        self.assertEqual(r_time.reply, "It is 3:05 PM.")

        r_date = core.handle(req("what's the date"))
        self.assertEqual(r_date.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r_date.capability, "tell_date")
        self.assertIn("Tuesday", r_date.reply)

        r_bat = core.handle(req("battery level"))
        self.assertEqual(r_bat.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r_bat.capability, "battery_status")
        self.assertIn("80 percent", r_bat.reply)

        r_vol = core.handle(req("volume up"))
        self.assertEqual(r_vol.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(r_vol.capability, "volume_up")
        self.assertEqual(len(sent), 1)

        self.assertEqual(len(gateway.calls), 0)

    def test_multistep_quick_command_uses_planner_and_passes_steps(self):
        import json
        from datetime import datetime
        from eva.capabilities.quick_actions import TellTimeCapability, VolumeDownCapability
        sent = []
        reg = CapabilityRegistry()
        reg.register(TellTimeCapability(now_fn=lambda: datetime(2026, 10, 27, 15, 5)))
        reg.register(VolumeDownCapability(sender=lambda vk, count: sent.append((vk, count))))

        plan = {
            "kind": "plan",
            "steps": [
                {"capability": "tell_time", "args": {}},
                {"capability": "volume_down", "args": {"steps": 10}},
            ],
            "confidence": 0.95,
            "reply": "Told time and lowered volume.",
        }
        gateway = FakeGateway(reply=json.dumps(plan))
        planner = Planner(settings=BrainSettings(api_key="k"), gateway=gateway)
        core = EvaCore(registry=reg, planner=planner)

        r = core.handle(req("what time is it and volume down"))
        self.assertEqual(r.outcome, TaskOutcome.SUCCESS)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], 10)
        self.assertEqual(len(gateway.calls), 1)



if __name__ == "__main__":
    unittest.main()
