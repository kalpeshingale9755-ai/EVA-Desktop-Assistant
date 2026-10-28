"""Plan Runner: runs an approved plan one step at a time.

Core supplies `run_step(step) -> (outcome, spoken_message)`. That callback MUST send
the step through the normal Core flow (permission gate, execution, verification),
exactly like a step typed or spoken by the user. The Runner adds no powers of its own.

Rules: stop at the first step that does not return SUCCESS; never skip the
confirmation for plans that contain a dangerous step; honour a cancel flag.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .plan_settings import PlanSettings
from .plan_types import PlanResult, PlanStep

log = logging.getLogger("eva.planner.runner")

RunStep = Callable[[PlanStep], Tuple[str, str]]
Confirm = Callable[[str], bool]


@dataclass(frozen=True)
class StepResult:
    step: PlanStep
    outcome: str
    message: str


@dataclass(frozen=True)
class PlanOutcome:
    outcome: str                                   # SUCCESS | PARTIAL | FAILED | TIMEOUT | UNKNOWN
    message: str                                   # safe to speak
    results: Tuple[StepResult, ...] = field(default_factory=tuple)

    @property
    def completed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "SUCCESS")


def describe_plan(plan: PlanResult) -> str:
    """Plain text of what the plan will do (for the confirmation question and logs)."""
    parts = []
    for i, s in enumerate(plan.steps, start=1):
        args = ", ".join(f"{k}={v}" for k, v in s.args.items())
        parts.append(f"{i}. {s.capability}({args})")
    return "; ".join(parts)


class PlanRunner:
    def __init__(self, run_step: RunStep, confirm: Optional[Confirm] = None,
                 settings: Optional[PlanSettings] = None, sleep: Callable[[float], None] = time.sleep):
        self._run_step = run_step
        self._confirm = confirm
        self._settings = settings or PlanSettings()
        self._sleep = sleep

    def run(self, plan: PlanResult, cancel: Optional[threading.Event] = None) -> PlanOutcome:
        if plan.kind != "plan" or not plan.steps:
            return PlanOutcome("UNKNOWN", plan.reply or "There is nothing to do.")

        if plan.needs_confirmation:
            question = f"This plan has a risky step: {describe_plan(plan)}. Should I go ahead?"
            if self._confirm is None or not self._confirm(question):
                return PlanOutcome("FAILED", "Okay, I will not do that.")

        results: List[StepResult] = []
        total = len(plan.steps)
        for i, step in enumerate(plan.steps, start=1):
            if cancel is not None and cancel.is_set():
                return self._stopped(results, total, "Stopped as you asked.")
            if i > 1:
                self._sleep(self._settings.step_delay_seconds)
                if cancel is not None and cancel.is_set():
                    return self._stopped(results, total, "Stopped as you asked.")
            try:
                outcome, message = self._run_step(step)
            except Exception as exc:  # a broken capability must not break the plan runner
                log.exception("step %d crashed", i)
                outcome, message = "FAILED", "That step failed."
                log.warning("step %d (%s) raised %s", i, step.capability, type(exc).__name__)
            results.append(StepResult(step, outcome, message))
            log.info("plan step %d/%d %s -> %s", i, total, step.capability, outcome)
            if outcome != "SUCCESS":
                done = sum(1 for r in results if r.outcome == "SUCCESS")
                overall = "PARTIAL" if done > 0 else (outcome if outcome in ("TIMEOUT", "UNKNOWN") else "FAILED")
                spoken = f"I stopped at step {i} of {total}. {message}".strip()
                return PlanOutcome(overall, spoken, tuple(results))
        return PlanOutcome("SUCCESS", plan.reply or "Done.", tuple(results))

    @staticmethod
    def _stopped(results: List[StepResult], total: int, message: str) -> PlanOutcome:
        done = sum(1 for r in results if r.outcome == "SUCCESS")
        return PlanOutcome("PARTIAL" if done else "FAILED", message, tuple(results))
