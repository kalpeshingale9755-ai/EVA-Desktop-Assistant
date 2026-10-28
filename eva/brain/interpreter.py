"""Output Interpreter: the safety wall between the LLM and EVA Core.

The LLM output is untrusted text. This module turns it into a validated result
or refuses it. It never executes anything.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from .brain_types import ActionProposal
from .specs import ArgSpec, CapabilitySpec

MAX_REPLY_CHARS = 600
_SPECIAL = re.compile(r"[&|;<>`$\x00-\x1f\x7f]")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class InterpretError(Exception):
    """The LLM output was invalid or unsafe. Message is for logs, not for speech."""


def parse_json(raw: str) -> Dict[str, Any]:
    text = _FENCE.sub("", (raw or "").strip()).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise InterpretError("no JSON object in LLM output")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise InterpretError(f"bad JSON: {exc.msg}")
    if not isinstance(data, dict):
        raise InterpretError("LLM output is not a JSON object")
    return data


def _check_arg(name: str, value: Any, spec: ArgSpec) -> Any:
    if spec.type == "str":
        if not isinstance(value, str):
            raise InterpretError(f"arg '{name}' must be text")
        value = value.strip()
        if not value and spec.required:
            raise InterpretError(f"arg '{name}' is empty")
        if len(value) > spec.max_len:
            raise InterpretError(f"arg '{name}' too long")
        if not spec.allow_special and _SPECIAL.search(value):
            raise InterpretError(f"arg '{name}' has unsafe characters")
        if spec.choices is not None and value not in spec.choices:
            raise InterpretError(f"arg '{name}' not an allowed choice")
        return value
    if spec.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InterpretError(f"arg '{name}' must be a whole number")
        return value
    if spec.type == "bool":
        if not isinstance(value, bool):
            raise InterpretError(f"arg '{name}' must be true/false")
        return value
    raise InterpretError(f"unknown arg type for '{name}'")


def _check_args(args: Any, spec: CapabilitySpec) -> Dict[str, Any]:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise InterpretError("args is not an object")
    extra = set(args) - set(spec.args)
    if extra:
        raise InterpretError(f"unexpected args: {sorted(extra)}")
    clean: Dict[str, Any] = {}
    for name, aspec in spec.args.items():
        if name not in args or args[name] is None:
            if aspec.required:
                raise InterpretError(f"missing arg '{name}'")
            continue
        clean[name] = _check_arg(name, args[name], aspec)
    return clean


def interpret(raw: str, specs: Dict[str, CapabilitySpec],
              min_confidence: float = 0.6) -> Tuple[str, str, Optional[ActionProposal]]:
    """Return (kind, reply, proposal). Raises InterpretError if anything is wrong."""
    data = parse_json(raw)

    kind = data.get("kind")
    if kind not in ("action", "chat", "unknown"):
        raise InterpretError("invalid kind")

    reply = data.get("reply", "")
    if not isinstance(reply, str):
        raise InterpretError("reply is not text")
    reply = reply.strip()[:MAX_REPLY_CHARS]

    if kind in ("chat", "unknown"):
        # Any capability/args the LLM added here is ignored on purpose.
        if kind == "chat" and not reply:
            raise InterpretError("empty chat reply")
        return kind, reply, None

    # kind == "action"
    cap = data.get("capability")
    if not isinstance(cap, str) or cap not in specs:
        raise InterpretError("capability not in approved list")
    conf = data.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise InterpretError("invalid confidence")
    if conf < min_confidence:
        raise InterpretError("confidence too low")
    spec = specs[cap]
    args = _check_args(data.get("args"), spec)
    proposal = ActionProposal(capability=cap, args=args, confidence=float(conf),
                              needs_confirmation=spec.needs_confirmation)
    return "action", reply, proposal
