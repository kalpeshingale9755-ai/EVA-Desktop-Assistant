"""Capability specs: what the LLM is ALLOWED to propose.

Core should build this list from its real capability registry, so the LLM can
only ever name capabilities that actually exist. Anything not listed here is
rejected by the Output Interpreter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ArgSpec:
    type: str = "str"                       # "str" | "int" | "bool"
    required: bool = True
    max_len: int = 120                      # for str
    choices: Optional[Tuple[str, ...]] = None
    allow_special: bool = False             # allow & | ; < > ` $ and control chars
    description: str = ""


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    args: Dict[str, ArgSpec] = field(default_factory=dict)
    needs_confirmation: bool = False        # dangerous actions: Core must ask the user


def default_specs() -> Dict[str, CapabilitySpec]:
    """Starter registry. Replace/extend from Core's real capabilities."""
    specs = [
        CapabilitySpec(
            name="open_app",
            description="Open an installed Windows app by its name",
            args={"name": ArgSpec(type="str", max_len=60, description="app name, e.g. notepad")},
        ),
    ]
    return {s.name: s for s in specs}
