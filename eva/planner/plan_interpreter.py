"""Plan Interpreter: checks the LLM's plan before Core ever sees it.

Every step must name an approved capability with arguments that match its spec.
Reuses the Stage 5 argument checks (same safety wall, now applied per step).
"""
from __future__ import annotations

from typing import Dict, Tuple

from eva.brain.interpreter import MAX_REPLY_CHARS, InterpretError, _check_args, parse_json
from eva.brain.specs import CapabilitySpec

from .plan_types import PlanStep


def interpret_plan(raw: str, specs: Dict[str, CapabilitySpec], min_confidence: float = 0.6,
                   max_steps: int = 5) -> Tuple[str, str, Tuple[PlanStep, ...], bool]:
    """Return (kind, reply, steps, needs_confirmation). Raises InterpretError."""
    data = parse_json(raw)

    kind = data.get("kind")
    if kind not in ("plan", "chat", "unknown"):
        raise InterpretError("invalid kind")

    reply = data.get("reply", "")
    if not isinstance(reply, str):
        raise InterpretError("reply is not text")
    reply = reply.strip()[:MAX_REPLY_CHARS]

    if kind in ("chat", "unknown"):
        # Any steps the LLM added here are ignored on purpose.
        if kind == "chat" and not reply:
            raise InterpretError("empty chat reply")
        return kind, reply, (), False

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise InterpretError("plan has no steps")
    if len(raw_steps) > max_steps:
        raise InterpretError("too many steps")

    conf = data.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise InterpretError("invalid confidence")
    if conf < min_confidence:
        raise InterpretError("confidence too low")

    steps = []
    needs_confirmation = False
    for i, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            raise InterpretError(f"step {i} is not an object")
        cap = item.get("capability")
        if not isinstance(cap, str) or cap not in specs:
            raise InterpretError(f"step {i}: capability not in approved list")
        args = _check_args(item.get("args"), specs[cap])
        steps.append(PlanStep(capability=cap, args=args))
        needs_confirmation = needs_confirmation or specs[cap].needs_confirmation
    return "plan", reply, tuple(steps), needs_confirmation
