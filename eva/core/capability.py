"""What a capability looks like. Stage 4 adds real ones (open notepad, time, ...).

Rules from the approved architecture:
  - The Core decides. A capability only does its one small job.
  - A capability never runs free-form commands. It only does what its code allows.
  - Anything that changes the real world must be able to verify its own result.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from eva.common.types import TaskOutcome
from eva.understanding.request import RequestObject


class Risk(str, Enum):
    LOW = "LOW"        # safe, easy to undo (open notepad, tell the time)
    MEDIUM = "MEDIUM"  # changes something (change volume, create a file)
    HIGH = "HIGH"      # dangerous or hard to undo (delete, send, shut down)


@dataclass(frozen=True)
class CapabilityResult:
    outcome: TaskOutcome
    message: str = ""                       # what EVA says, e.g. "Opened Notepad."
    details: dict = field(default_factory=dict)


class Capability(ABC):
    name: str = ""                # short unique id, e.g. "open_app"
    description: str = ""
    risk: Risk = Risk.LOW
    timeout_s: float = 10.0       # Core stops waiting after this long
    verify_timeout_s: float = 5.0

    @abstractmethod
    def match(self, request: RequestObject) -> dict | None:
        """Return the arguments (a dict, can be empty) if this capability handles the
        request, or None if it does not. Must be fast and must not change anything."""

    @abstractmethod
    def execute(self, args: dict) -> CapabilityResult:
        """Do the job."""

    def verify(self, args: dict, result: CapabilityResult) -> bool:
        """Check that it REALLY happened (for example: is the Notepad process running?).
        Return True/False. Capabilities that change nothing can keep this default."""
        return True
