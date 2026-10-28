"""Types for the Planner (Stage 6a). A plan is a PROPOSAL; Core decides."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from eva.brain.brain_types import BrainStatus

_OUTCOME = {BrainStatus.OK: "SUCCESS", BrainStatus.TIMEOUT: "TIMEOUT"}


@dataclass(frozen=True)
class PlanStep:
    capability: str
    args: Dict[str, Any]


@dataclass(frozen=True)
class PlanResult:
    status: BrainStatus
    kind: str = "unknown"                 # "plan" | "chat" | "unknown"
    reply: str = ""                       # always safe to speak
    steps: Tuple[PlanStep, ...] = ()
    needs_confirmation: bool = False      # True if any step is marked dangerous
    detail: str = ""                      # logs only

    @property
    def ok(self) -> bool:
        return self.status == BrainStatus.OK

    @property
    def task_outcome(self) -> str:
        return _OUTCOME.get(self.status, "FAILED")
