"""EVA Core: the controller. It receives a RequestObject and always returns a CoreResult.

Flow (COMMAND_MODE):
  PROCESSING -> find capability -> permission gate -> EXECUTING -> verify -> outcome -> BACKGROUND
CONVERSATION_MODE waits for the Brain (later stage).

Core has NO Qt and NO microphone. It is plain Python, so it is easy to test.
It never raises: whatever happens, the caller gets a CoreResult with a sentence to say.
"""
from __future__ import annotations

import dataclasses
import threading
import time
from dataclasses import dataclass
from typing import Callable

from eva.brain import ArgSpec, Brain, BrainResult, BrainSettings, CapabilitySpec
from eva.brain.gateway import make_gateway
from eva.common.logging_setup import get_logger
from eva.common.types import InterfaceState, Mode, TaskOutcome
from eva.core.capability import Capability, CapabilityResult
from eva.core.gate import GateDecision, PermissionGate
from eva.core.registry import CapabilityRegistry
from eva.capabilities.quick_actions import (
    battery_status,
    take_screenshot,
    tell_date,
    tell_time,
    volume_down,
    volume_mute,
    volume_up,
)
from eva.core.settings import CoreSettings
from eva.planner import (
    PlanOutcome,
    PlanResult,
    PlanRunner,
    PlanSettings,
    PlanStep,
    Planner,
    parse_web_command,
    web_specs,
)
from eva.planner.local_quick import parse_quick_command
from eva.planner.quick_specs import quick_specs
from eva.understanding.request import InputSource, RequestObject

log = get_logger("eva.core")
_DEFAULT = object()


def default_core_specs() -> dict[str, CapabilitySpec]:
    return {
        "open_app": CapabilitySpec(
            name="open_app",
            description="Open an installed Windows app by its name",
            args={"name": ArgSpec(max_len=60, description="app name, e.g. notepad")},
        )
    }


def build_specs_from_registry(registry: CapabilityRegistry | None) -> dict[str, CapabilitySpec]:
    specs: dict[str, CapabilitySpec] = {}
    if registry and hasattr(registry, "names"):
        try:
            names = set(registry.names())
        except Exception:
            names = set()
    else:
        names = set()
    if "open_app" in names:
        specs["open_app"] = CapabilitySpec(
            name="open_app",
            description="Open an installed Windows app by its name",
            args={"name": ArgSpec(max_len=60, description="app name, e.g. notepad")},
        )
    if "close_app" in names:
        specs["close_app"] = CapabilitySpec(
            name="close_app",
            description="Close a running Windows app by its name",
            args={"name": ArgSpec(max_len=60, description="app name, e.g. notepad")},
        )
    if "open_file" in names:
        specs["open_file"] = CapabilitySpec(
            name="open_file",
            description="Open a file by its name",
            args={"name": ArgSpec(max_len=120, description="file name, e.g. notes.txt")},
        )
    return specs


def build_specs_for_planner(registry: CapabilityRegistry | None) -> dict[str, CapabilitySpec]:
    specs = build_specs_from_registry(registry)
    if not specs:
        specs = default_core_specs()
    return {**specs, **web_specs(), **quick_specs()}


@dataclass(frozen=True)
class CoreResult:
    request_id: str
    outcome: TaskOutcome
    reply: str                    # what EVA shows and says
    reason: str                   # short code, for logs and tests
    capability: str | None = None
    seconds: float = 0.0


def run_with_timeout(fn: Callable[[], object], timeout: float):
    """Run fn in a helper thread. Raises TimeoutError if it takes too long.
    (A stuck thread cannot be killed in Python, so it is a daemon: it never blocks exit.)"""
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised below in the caller thread
            box["error"] = exc

    thread = threading.Thread(target=target, name="eva-capability", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"no answer after {timeout:.1f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class EvaCore:
    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        settings: CoreSettings | None = None,
        gate: PermissionGate | None = None,
        on_state: Callable[[InterfaceState], None] | None = None,
        brain: Brain | None | object = _DEFAULT,
        planner: Planner | None | object = _DEFAULT,
        confirm: Callable[[str], bool] | None = None,
        cancel_event: threading.Event | None = None,
        plan_settings: PlanSettings | None = None,
    ) -> None:
        self.registry = registry if registry is not None else CapabilityRegistry()
        self.settings = settings or CoreSettings()
        self.gate = gate or PermissionGate()
        self._on_state = on_state
        self._confirm = confirm
        self._cancel_event = cancel_event
        self._plan_settings = plan_settings or PlanSettings()

        if planner is not _DEFAULT:
            self.planner: Planner | None = planner
            self.brain: Brain | None = brain if brain is not _DEFAULT else None
        elif brain is not _DEFAULT:
            if brain is None:
                self.brain = None
                self.planner = None
            elif hasattr(brain, "plan"):
                self.planner = brain
                self.brain = None
            else:
                self.brain = brain
                self.planner = None
        else:
            b_settings = BrainSettings.from_env()
            specs = build_specs_for_planner(self.registry)
            gateway = make_gateway(b_settings)
            self.planner = Planner(settings=b_settings, gateway=gateway, specs=specs,
                                   plan_settings=self._plan_settings)
            self.brain = None

    def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    # ---- public ---------------------------------------------------------------
    def handle(self, request: RequestObject, cancel: threading.Event | None = None) -> CoreResult:
        started = time.perf_counter()
        if cancel is not None:
            self._cancel_event = cancel
        elif self._cancel_event is None:
            self._cancel_event = threading.Event()
        try:
            result = self._handle(request)
            self._set_state(InterfaceState.BACKGROUND)
        except Exception:
            log.exception("Core crashed on request %s", request.request_id)
            self._set_state(InterfaceState.ERROR)
            result = CoreResult(request.request_id, TaskOutcome.FAILED,
                                "Something went wrong inside EVA. I wrote it in the log.",
                                "internal_error")
        result = dataclasses.replace(result, seconds=round(time.perf_counter() - started, 2))
        log.info("Core result | id=%s | %s | reason=%s | capability=%s | %.2fs | reply=%r",
                 result.request_id, result.outcome.value, result.reason,
                 result.capability, result.seconds, result.reply)
        return result

    # ---- steps ----------------------------------------------------------------
    def _handle(self, request: RequestObject) -> CoreResult:
        self._set_state(InterfaceState.PROCESSING)
        log.info("Core received %s | mode=%s | source=%s | confidence=%s | text=%r",
                 request.request_id, request.mode.value, request.source.value,
                 request.confidence, request.clean_text)

        if request.mode is Mode.CONVERSATION_MODE:
            if self.planner is None and self.brain is None:
                return self._result(request, TaskOutcome.FAILED, "conversation_not_ready",
                                    f"I heard: {request.clean_text}. I can't chat yet.")
            return self._handle_with_llm(request, "CONVERSATION_MODE")

        # COMMAND_MODE
        min_conf = self.settings.min_command_confidence
        if request.confidence is not None and request.confidence < min_conf:
            return self._result(request, TaskOutcome.FAILED, "low_confidence",
                                "I'm not sure I heard that right. Please say it again.")

        # Local understanding, quick commands first:
        quick_match = parse_quick_command(request.clean_text)
        if quick_match:
            cap = self.registry.get(quick_match.capability)
            if cap is not None:
                quick_req = RequestObject(
                    raw_text=request.raw_text,
                    clean_text=request.clean_text,
                    source=request.source,
                    mode=Mode.COMMAND_MODE,
                    confidence=request.confidence,
                    request_id=request.request_id,
                )
                return self._run_capability(quick_req, cap, quick_match.args)
            elif any(k in self.registry.names() for k in ("tell_time", "tell_date", "battery_status", "volume_up", "volume_down", "volume_mute", "take_screenshot")):
                return self._result(request, TaskOutcome.FAILED, "no_capability",
                                    "I don't know how to do that yet.")

        # Local understanding, web commands next:
        # Before LLM fallback and BEFORE open_app lookup, check web commands
        # so "open youtube" or "search for ..." do not become app searches.
        web_match = parse_web_command(request.clean_text)
        if web_match:
            cap = self.registry.get(web_match.capability)
            if cap is not None:
                web_req = RequestObject(
                    raw_text=request.raw_text,
                    clean_text=request.clean_text,
                    source=request.source,
                    mode=Mode.COMMAND_MODE,
                    confidence=request.confidence,
                    request_id=request.request_id,
                )
                return self._run_capability(web_req, cap, web_match.args)
            elif "open_app" in self.registry.names() and not any(k in self.registry.names() for k in ("open_url", "web_search")):
                return self._result(request, TaskOutcome.FAILED, "no_capability",
                                    "I don't know how to do that yet.")

        matches = self.registry.find(request)
        if len(matches) > 1:
            names = ", ".join(c.name for c, _ in matches)
            log.warning("Request %s matched several capabilities: %s", request.request_id, names)
            return self._result(request, TaskOutcome.FAILED, "ambiguous",
                                "I'm not sure which action you mean. Please be more specific.")

        if matches:
            capability, args = matches[0]
            return self._run_capability(request, capability, args)

        # Local understanding did not match: LLM fallback
        if self.planner is None and self.brain is None:
            return self._result(request, TaskOutcome.FAILED, "no_capability",
                                "I don't know how to do that yet.")

        return self._handle_with_llm(request, "COMMAND_MODE")

    def _run_capability(self, request: RequestObject, capability: Capability, args: dict) -> CoreResult:
        decision = self.gate.check(capability, request)
        if decision is not GateDecision.ALLOW:
            reason = decision.value.lower()
            return self._result(
                request, TaskOutcome.FAILED, reason,
                "That needs your confirmation, and I can't ask for it yet, so I didn't do it."
                if decision is GateDecision.NEEDS_CONFIRMATION
                else "I'm not allowed to do that.",
                capability)

        return self._execute(request, capability, args)

    def _handle_with_llm(self, request: RequestObject, mode: str) -> CoreResult:
        if self.planner is not None:
            return self._handle_with_planner(request, mode)
        if self.brain is not None:
            return self._handle_with_brain(request, mode)
        return self._result(request, TaskOutcome.FAILED, "no_brain",
                            "My language brain is not available right now.")

    def _handle_with_planner(self, request: RequestObject, mode: str) -> CoreResult:
        assert self.planner is not None
        specs = build_specs_for_planner(self.registry)
        if self.planner.specs:
            specs = {**self.planner.specs, **specs}
        if specs:
            self.planner.specs = specs

        result: PlanResult = self.planner.plan(request.clean_text, mode=mode)
        log.info("Planner result | status=%s | kind=%s | outcome=%s | reply=%r",
                 result.status.value, result.kind, result.task_outcome, result.reply)
        if result.detail:
            log.info("Planner technical detail: %s", result.detail)

        if result.kind == "plan" and result.ok:
            runner = PlanRunner(run_step=self._run_plan_step, confirm=self._confirm,
                                settings=self._plan_settings)
            self._set_state(InterfaceState.EXECUTING)
            outcome: PlanOutcome = runner.run(result, cancel=self._cancel_event)
            self._set_state(InterfaceState.RESPONDING)
            log.info("Plan finished | outcome=%s | message=%r", outcome.outcome, outcome.message)
            for s_res in outcome.results:
                log.info("  step %s -> %s: %s", s_res.step.capability, s_res.outcome, s_res.message)

            task_outcome = getattr(TaskOutcome, outcome.outcome, TaskOutcome.FAILED)
            reason = "ok" if outcome.outcome == "SUCCESS" else f"plan_{outcome.outcome.lower()}"
            return CoreResult(
                request_id=request.request_id,
                outcome=task_outcome,
                reply=outcome.message,
                reason=reason,
                capability=None,
            )

        outcome = getattr(TaskOutcome, result.task_outcome, TaskOutcome.FAILED)
        if mode == "COMMAND_MODE":
            reason = "no_capability"
        else:
            reason = "chat" if (result.ok and result.kind == "chat") else f"planner_{result.status.value.lower()}"
        return self._result(request, outcome, reason, result.reply)

    def _run_plan_step(self, step: PlanStep) -> tuple[str, str]:
        capability = self.registry.get(step.capability)
        if capability is None:
            log.warning("Plan step requested unknown capability '%s'", step.capability)
            return "FAILED", f"I don't know how to {step.capability}."

        step_args = step.args or {}
        if step.capability == "open_app":
            name = step_args.get("name", "")
            clean_text = f"open {name}"
        elif step.capability == "close_app":
            name = step_args.get("name", "")
            clean_text = f"close {name}"
        elif step.capability == "open_file":
            name = step_args.get("name", "")
            clean_text = f"open {name}"
        elif step.capability == "open_url":
            url = step_args.get("url", "")
            clean_text = f"open {url}"
        elif step.capability == "web_search":
            query = step_args.get("query", "")
            clean_text = f"search for {query}"
        else:
            clean_text = f"{step.capability}"

        step_request = RequestObject(
            raw_text=clean_text,
            clean_text=clean_text,
            source=InputSource.VOICE,
            mode=Mode.COMMAND_MODE,
        )

        decision = self.gate.check(capability, step_request)
        if decision is GateDecision.DENY or (decision is GateDecision.NEEDS_CONFIRMATION and not self._confirm):
            msg = ("That needs your confirmation, and I can't ask for it yet, so I didn't do it."
                   if decision is GateDecision.NEEDS_CONFIRMATION else "I'm not allowed to do that.")
            return "FAILED", msg

        if step.capability in ("volume_up", "volume_down", "volume_mute", "tell_time", "tell_date", "battery_status", "take_screenshot"):
            args = step_args
        else:
            args = capability.match(step_request)
            if args is None:
                if step.capability == "open_app":
                    return "FAILED", f"I couldn't find an app named {step_args.get('name', '')}."
                if step.capability == "open_file":
                    return "FAILED", f"I couldn't find a file named {step_args.get('name', '')}."
                args = step_args

        core_result = self._execute(step_request, capability, args)
        return core_result.outcome.value, core_result.reply

    def _handle_with_brain(self, request: RequestObject, mode: str) -> CoreResult:
        if self.brain is None:
            return self._result(request, TaskOutcome.FAILED, "no_brain",
                                "My language brain is not available right now.")

        specs = build_specs_from_registry(self.registry)
        if specs:
            self.brain.specs = specs

        result = self.brain.think(request.clean_text, mode=mode)
        log.info("Brain result | status=%s | kind=%s | outcome=%s | reply=%r",
                 result.status.value, result.kind, result.task_outcome, result.reply)
        if result.detail:
            log.info("Brain technical detail: %s", result.detail)

        if result.kind == "action" and result.ok and result.proposal:
            return self._handle_brain_action(request, result)

        outcome = getattr(TaskOutcome, result.task_outcome, TaskOutcome.FAILED)
        if mode == "COMMAND_MODE":
            reason = "no_capability"
        else:
            reason = "chat" if (result.ok and result.kind == "chat") else f"brain_{result.status.value.lower()}"
        return self._result(request, outcome, reason, result.reply)

    def _handle_brain_action(self, request: RequestObject, brain_result: BrainResult) -> CoreResult:
        proposal = brain_result.proposal
        assert proposal is not None

        capability = self.registry.get(proposal.capability)
        if capability is None:
            log.warning("Brain proposed capability '%s' which is not in registry", proposal.capability)
            return self._result(request, TaskOutcome.FAILED, "unknown_capability",
                                brain_result.reply or "I don't know how to do that yet.")

        target_name = proposal.args.get("name") or request.clean_text
        if proposal.capability == "open_app":
            clean_text = f"open {target_name}"
        elif proposal.capability == "close_app":
            clean_text = f"close {target_name}"
        else:
            clean_text = request.clean_text

        prop_request = RequestObject(
            raw_text=request.raw_text,
            clean_text=clean_text,
            source=request.source,
            mode=Mode.COMMAND_MODE,
            confidence=proposal.confidence,
            request_id=request.request_id,
        )

        decision = self.gate.check(capability, prop_request)
        if proposal.needs_confirmation or decision is GateDecision.NEEDS_CONFIRMATION:
            return self._result(
                prop_request, TaskOutcome.FAILED, "needs_confirmation",
                "That needs your confirmation, and I can't ask for it yet, so I didn't do it.",
                capability)
        if decision is not GateDecision.ALLOW:
            return self._result(
                prop_request, TaskOutcome.FAILED, decision.value.lower(),
                "I'm not allowed to do that.",
                capability)

        args = capability.match(prop_request)
        if args is None:
            if proposal.capability == "open_app":
                return self._result(prop_request, TaskOutcome.FAILED, "app_not_found",
                                    f"I couldn't find an app named {target_name}.", capability)
            args = proposal.args

        return self._execute(prop_request, capability, args)

    def _execute(self, request: RequestObject, capability: Capability, args: dict) -> CoreResult:
        self._set_state(InterfaceState.EXECUTING)
        log.info("Executing '%s' with %s", capability.name, args)

        try:
            result = run_with_timeout(lambda: capability.execute(args), capability.timeout_s)
        except TimeoutError:
            return self._result(request, TaskOutcome.TIMEOUT, "timeout",
                                "That took too long, so I stopped waiting.", capability)
        except Exception:
            log.exception("Capability '%s' raised an error", capability.name)
            return self._result(request, TaskOutcome.FAILED, "capability_error",
                                "Something went wrong while I tried to do that.", capability)

        if not isinstance(result, CapabilityResult):
            log.error("Capability '%s' returned %r instead of a CapabilityResult",
                      capability.name, type(result).__name__)
            return self._result(request, TaskOutcome.FAILED, "capability_error",
                                "Something went wrong while I tried to do that.", capability)

        if result.outcome is TaskOutcome.SUCCESS:
            return self._verify(request, capability, args, result)

        defaults = {
            TaskOutcome.PARTIAL: "I only finished part of that.",
            TaskOutcome.FAILED: "I couldn't do that.",
            TaskOutcome.TIMEOUT: "That took too long, so I stopped waiting.",
            TaskOutcome.UNKNOWN: "I'm not sure if that worked.",
        }
        return self._result(request, result.outcome,
                            f"capability_{result.outcome.value.lower()}",
                            result.message or defaults[result.outcome], capability)

    def _verify(self, request: RequestObject, capability: Capability, args: dict,
                result: CapabilityResult) -> CoreResult:
        """The capability SAYS it worked. Check that it REALLY did."""
        try:
            verified = run_with_timeout(lambda: capability.verify(args, result),
                                        capability.verify_timeout_s)
        except Exception:
            log.exception("Verification of '%s' failed to run", capability.name)
            return self._result(request, TaskOutcome.UNKNOWN, "verify_error",
                                "I did it, but I couldn't check that it worked.", capability)
        if verified is not True:
            return self._result(request, TaskOutcome.FAILED, "verified_failed",
                                "I tried, but I couldn't confirm it worked.", capability)
        return self._result(request, TaskOutcome.SUCCESS, "ok",
                            result.message or "Done.", capability)

    # ---- helpers --------------------------------------------------------------
    @staticmethod
    def _result(request: RequestObject, outcome: TaskOutcome, reason: str, reply: str,
                capability: Capability | None = None) -> CoreResult:
        return CoreResult(request.request_id, outcome, reply, reason,
                          capability.name if capability else None)

    def _set_state(self, state: InterfaceState) -> None:
        if self._on_state is None:
            return
        try:
            self._on_state(state)
        except Exception:
            log.exception("State callback failed")
