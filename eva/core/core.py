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

from eva.common.logging_setup import get_logger
from eva.common.types import InterfaceState, Mode, TaskOutcome
from eva.core.capability import Capability, CapabilityResult
from eva.core.gate import GateDecision, PermissionGate
from eva.core.registry import CapabilityRegistry
from eva.core.settings import CoreSettings
from eva.understanding.request import RequestObject

log = get_logger("eva.core")


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
    ) -> None:
        self.registry = registry if registry is not None else CapabilityRegistry()
        self.settings = settings or CoreSettings()
        self.gate = gate or PermissionGate()
        self._on_state = on_state

    # ---- public ---------------------------------------------------------------
    def handle(self, request: RequestObject) -> CoreResult:
        started = time.perf_counter()
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
            return self._result(request, TaskOutcome.FAILED, "conversation_not_ready",
                                f"I heard: {request.clean_text}. I can't chat yet.")

        # COMMAND_MODE
        min_conf = self.settings.min_command_confidence
        if request.confidence is not None and request.confidence < min_conf:
            return self._result(request, TaskOutcome.FAILED, "low_confidence",
                                "I'm not sure I heard that right. Please say it again.")

        matches = self.registry.find(request)
        if not matches:
            return self._result(request, TaskOutcome.FAILED, "no_capability",
                                "I don't know how to do that yet.")
        if len(matches) > 1:
            names = ", ".join(c.name for c, _ in matches)
            log.warning("Request %s matched several capabilities: %s", request.request_id, names)
            return self._result(request, TaskOutcome.FAILED, "ambiguous",
                                "I'm not sure which action you mean. Please be more specific.")

        capability, args = matches[0]
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
