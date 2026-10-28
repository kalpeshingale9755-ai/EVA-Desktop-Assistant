"""Shared data types for the Brain System (Stage 5).

The Brain never executes anything. It only returns a BrainResult:
  - a spoken reply (chat / unknown), or
  - an ActionProposal that EVA Core may accept or refuse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class BrainStatus(str, Enum):
    OK = "OK"                      # LLM answered and the Interpreter accepted it
    REJECTED = "REJECTED"          # LLM answered, Interpreter refused it (unsafe/invalid)
    UNAVAILABLE = "UNAVAILABLE"    # disabled, no API key, no internet, bad key
    RATE_LIMITED = "RATE_LIMITED"  # free-tier limit (local guard or HTTP 429)
    TIMEOUT = "TIMEOUT"            # LLM did not answer in time
    ERROR = "ERROR"                # anything unexpected


# Maps Brain status to EVA's task outcomes (SUCCESS/FAILED/TIMEOUT/PARTIAL/UNKNOWN)
_OUTCOME = {
    BrainStatus.OK: "SUCCESS",
    BrainStatus.TIMEOUT: "TIMEOUT",
}


@dataclass(frozen=True)
class ActionProposal:
    """A PROPOSAL only. Core decides. The permission gate still applies."""
    capability: str
    args: Dict[str, Any]
    confidence: float
    needs_confirmation: bool = False


@dataclass(frozen=True)
class BrainResult:
    status: BrainStatus
    kind: str = "unknown"                 # "action" | "chat" | "unknown"
    reply: str = ""                       # what EVA can speak (always safe to speak)
    proposal: Optional[ActionProposal] = None
    detail: str = ""                      # technical detail for logs only, never spoken

    @property
    def ok(self) -> bool:
        return self.status == BrainStatus.OK

    @property
    def task_outcome(self) -> str:
        return _OUTCOME.get(self.status, "FAILED")
