"""Permission gate: decides if a capability may run NOW.

Stage 3 rule (safe by default): only LOW risk runs without asking.
Confirmation by voice/dialog is a later stage, so MEDIUM and HIGH are held back.
"""
from __future__ import annotations

from enum import Enum

from eva.core.capability import Capability, Risk
from eva.understanding.request import RequestObject


class GateDecision(str, Enum):
    ALLOW = "ALLOW"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    DENY = "DENY"


class PermissionGate:
    def check(self, capability: Capability, request: RequestObject) -> GateDecision:
        if capability.risk is Risk.LOW:
            return GateDecision.ALLOW
        return GateDecision.NEEDS_CONFIRMATION
