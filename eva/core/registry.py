"""The list of capabilities EVA has."""
from __future__ import annotations

from eva.common.logging_setup import get_logger
from eva.core.capability import Capability
from eva.understanding.request import RequestObject

log = get_logger("eva.registry")


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: list[Capability] = []

    def register(self, capability: Capability) -> None:
        if not capability.name:
            raise ValueError("A capability needs a name.")
        if capability.name in self.names():
            raise ValueError(f"Capability '{capability.name}' is already registered.")
        self._capabilities.append(capability)
        log.info("Capability registered: %s (risk=%s)", capability.name, capability.risk.value)

    def names(self) -> list[str]:
        return [c.name for c in self._capabilities]

    def __len__(self) -> int:
        return len(self._capabilities)

    def find(self, request: RequestObject) -> list[tuple[Capability, dict]]:
        """Every capability that says it can handle this request (with its arguments)."""
        found: list[tuple[Capability, dict]] = []
        for capability in self._capabilities:
            try:
                args = capability.match(request)
            except Exception:
                log.exception("Capability '%s' crashed while matching, ignored", capability.name)
                continue
            if args is not None:
                found.append((capability, args))
        return found
