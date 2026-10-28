"""Stage 5 tests. Run from the EVA folder:  python -m unittest tests.test_brain -v
No internet or API key needed (a fake gateway is used)."""
import io
import json
import unittest
import urllib.error

from eva.brain import Brain, BrainSettings, BrainStatus, ArgSpec, CapabilitySpec, default_specs
from eva.brain.gateway import (BaseGateway, GatewayRateLimited, GatewayTimeout,
                               GatewayUnavailable, GeminiGateway, _RateGuard)
from eva.brain.interpreter import InterpretError, interpret


class FakeUrlopen:
    def __init__(self, failures=0, code=503, reply=None):
        self.failures = failures
        self.code = code
        self.calls = 0
        self.reply = reply or {"candidates": [{"content": {"parts": [{"text": '{"kind":"chat","reply":"hi"}'}]}}]}

    def __call__(self, req, timeout=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise urllib.error.HTTPError(
                getattr(req, "full_url", "http://fake"),
                self.code,
                "Service Unavailable",
                {},
                io.BytesIO(b""),
            )
        return io.BytesIO(json.dumps(self.reply).encode("utf-8"))


class FakeGateway(BaseGateway):
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def generate(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if self.error:
            raise self.error
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def brain_with(reply=None, error=None, **kw):
    s = BrainSettings(api_key="x", **kw)
    return Brain(settings=s, gateway=FakeGateway(reply, error)), s


class InterpreterTests(unittest.TestCase):
    specs = default_specs()

    def ok(self, d):
        return interpret(json.dumps(d), self.specs)

    def test_valid_action(self):
        kind, reply, p = self.ok({"kind": "action", "capability": "open_app",
                                  "args": {"name": "notepad"}, "confidence": 0.9, "reply": "Opening Notepad."})
        self.assertEqual((kind, p.capability, p.args), ("action", "open_app", {"name": "notepad"}))

    def test_code_fence_is_ok(self):
        raw = '```json\n{"kind":"chat","capability":null,"args":{},"confidence":1,"reply":"Hello."}\n```'
        self.assertEqual(interpret(raw, self.specs)[0], "chat")

    def test_unknown_capability_rejected(self):
        with self.assertRaises(InterpretError):
            self.ok({"kind": "action", "capability": "delete_files", "args": {}, "confidence": 1, "reply": ""})

    def test_extra_arg_rejected(self):
        with self.assertRaises(InterpretError):
            self.ok({"kind": "action", "capability": "open_app",
                     "args": {"name": "notepad", "cmd": "del *"}, "confidence": 1, "reply": ""})

    def test_shell_characters_rejected(self):
        for bad in ["notepad & calc", "a;b", "x|y", "`id`", "$HOME", "a\nb"]:
            with self.assertRaises(InterpretError, msg=bad):
                self.ok({"kind": "action", "capability": "open_app",
                         "args": {"name": bad}, "confidence": 1, "reply": ""})

    def test_low_confidence_rejected(self):
        with self.assertRaises(InterpretError):
            self.ok({"kind": "action", "capability": "open_app",
                     "args": {"name": "notepad"}, "confidence": 0.2, "reply": ""})

    def test_chat_ignores_capability(self):
        _, _, p = self.ok({"kind": "chat", "capability": "open_app", "args": {"name": "x"},
                           "confidence": 1, "reply": "Hi."})
        self.assertIsNone(p)

    def test_garbage_rejected(self):
        for raw in ["", "hello", "[1,2]", '{"kind":"run"}', '{"kind":"chat","reply":""}']:
            with self.assertRaises(InterpretError, msg=raw):
                interpret(raw, self.specs)

    def test_confirmation_flag_comes_from_spec_not_llm(self):
        specs = {"close_app": CapabilitySpec("close_app", "Close an app",
                 {"name": ArgSpec(max_len=60)}, needs_confirmation=True)}
        _, _, p = interpret(json.dumps({"kind": "action", "capability": "close_app",
                            "args": {"name": "chrome"}, "confidence": 0.9, "reply": "",
                            "needs_confirmation": False}), specs)
        self.assertTrue(p.needs_confirmation)


class BrainTests(unittest.TestCase):
    def test_action_flow(self):
        b, _ = brain_with({"kind": "action", "capability": "open_app", "args": {"name": "calculator"},
                           "confidence": 0.95, "reply": "Opening Calculator."})
        r = b.think("open calculator")
        self.assertTrue(r.ok)
        self.assertEqual(r.proposal.args["name"], "calculator")
        self.assertEqual(r.task_outcome, "SUCCESS")

    def test_rejected_output_gets_safe_reply(self):
        b, _ = brain_with({"kind": "action", "capability": "format_disk", "args": {}, "confidence": 1, "reply": "ok"})
        r = b.think("do something bad")
        self.assertEqual(r.status, BrainStatus.REJECTED)
        self.assertIsNone(r.proposal)
        self.assertEqual(r.task_outcome, "FAILED")
        self.assertTrue(r.reply)

    def test_error_mapping(self):
        cases = [(GatewayRateLimited("x"), BrainStatus.RATE_LIMITED, "FAILED"),
                 (GatewayTimeout("x"), BrainStatus.TIMEOUT, "TIMEOUT"),
                 (GatewayUnavailable("x"), BrainStatus.UNAVAILABLE, "FAILED"),
                 (RuntimeError("boom"), BrainStatus.ERROR, "FAILED")]
        for err, status, outcome in cases:
            r = brain_with(error=err)[0].think("hi")
            self.assertEqual((r.status, r.task_outcome), (status, outcome))

    def test_disabled_and_no_key(self):
        r = Brain(settings=BrainSettings(enabled=False), gateway=FakeGateway({})).think("hi")
        self.assertEqual(r.status, BrainStatus.UNAVAILABLE)
        r = Brain(settings=BrainSettings(api_key="")).think("hi")  # real gateway, no key
        self.assertEqual(r.status, BrainStatus.UNAVAILABLE)

    def test_input_is_trimmed(self):
        b, s = brain_with({"kind": "chat", "capability": None, "args": {}, "confidence": 1, "reply": "ok"})
        b.think("a" * 5000)
        self.assertEqual(len(b._gateway.calls[0][1][-1][1]), s.max_input_chars)

    def test_history_only_in_conversation_mode(self):
        chat = {"kind": "chat", "capability": None, "args": {}, "confidence": 1, "reply": "Sure."}
        b, _ = brain_with(chat)
        b.think("hello", mode="CONVERSATION_MODE")
        b.think("and now?", mode="CONVERSATION_MODE")
        self.assertEqual(len(b._gateway.calls[1][1]), 3)   # user, model, user
        b.think("open notepad", mode="COMMAND_MODE")
        self.assertEqual(len(b._gateway.calls[2][1]), 1)   # no history in command mode

    def test_prompt_lists_only_given_capabilities(self):
        b, _ = brain_with({"kind": "unknown", "capability": None, "args": {}, "confidence": 0, "reply": "No."})
        b.think("x")
        self.assertIn("open_app", b._gateway.calls[0][0])
        self.assertNotIn("close_app", b._gateway.calls[0][0])


class GatewayTests(unittest.TestCase):
    def test_rate_guard(self):
        g = _RateGuard(2)
        self.assertEqual([g.try_acquire() for _ in range(3)], [True, True, False])

    def test_local_guard_stops_before_network(self):
        gw = GeminiGateway(BrainSettings(api_key="k", max_requests_per_minute=0))
        with self.assertRaises(GatewayRateLimited):
            gw.generate("sys", [("user", "hi")])

    def test_extract_text(self):
        ok = {"candidates": [{"content": {"parts": [{"text": '{"a":1}'}]}}]}
        self.assertEqual(GeminiGateway._extract_text(ok), '{"a":1}')
        with self.assertRaises(Exception):
            GeminiGateway._extract_text({"promptFeedback": {"blockReason": "SAFETY"}})

    def test_retry_twice_503_then_succeeds(self):
        fake = FakeUrlopen(failures=2, code=503)
        sleeps = []
        gw = GeminiGateway(BrainSettings(api_key="k", max_requests_per_minute=1),
                           _urlopen=fake, _sleep=sleeps.append)
        res = gw.generate("sys", [("user", "hi")])
        self.assertEqual(res, '{"kind":"chat","reply":"hi"}')
        self.assertEqual(fake.calls, 3)
        self.assertEqual(sleeps, [0.8, 1.6])

    def test_retry_three_times_503_fails(self):
        fake = FakeUrlopen(failures=3, code=503)
        sleeps = []
        gw = GeminiGateway(BrainSettings(api_key="k", max_requests_per_minute=1),
                           _urlopen=fake, _sleep=sleeps.append)
        with self.assertRaises(GatewayUnavailable) as ctx:
            gw.generate("sys", [("user", "hi")])
        self.assertEqual(str(ctx.exception), "provider busy (HTTP 503)")
        self.assertEqual(fake.calls, 3)
        self.assertEqual(sleeps, [0.8, 1.6])

    def test_no_retry_on_client_and_rate_errors(self):
        for code, expected_exc in [(400, GatewayUnavailable), (401, GatewayUnavailable),
                                   (403, GatewayUnavailable), (404, GatewayUnavailable),
                                   (429, GatewayRateLimited)]:
            fake = FakeUrlopen(failures=1, code=code)
            sleeps = []
            gw = GeminiGateway(BrainSettings(api_key="k"), _urlopen=fake, _sleep=sleeps.append)
            with self.assertRaises(expected_exc):
                gw.generate("sys", [("user", "hi")])
            self.assertEqual(fake.calls, 1, f"Should not retry for HTTP {code}")
            self.assertEqual(sleeps, [], f"Should not sleep for HTTP {code}")

    def test_retry_on_all_server_error_codes(self):
        for code in (500, 502, 504):
            fake = FakeUrlopen(failures=1, code=code)
            sleeps = []
            gw = GeminiGateway(BrainSettings(api_key="k"), _urlopen=fake, _sleep=sleeps.append)
            res = gw.generate("sys", [("user", "hi")])
            self.assertEqual(res, '{"kind":"chat","reply":"hi"}')
            self.assertEqual(fake.calls, 2, f"Should retry on HTTP {code}")
            self.assertEqual(sleeps, [0.8])


if __name__ == "__main__":
    unittest.main()
