"""Brain facade. EVA Core talks to this class only.

    brain = Brain(specs=specs_from_core_registry)
    result = brain.think("open notepad", mode="COMMAND_MODE")

think() is BLOCKING (up to timeout_seconds). Call it from a worker thread,
never from the PySide6 UI thread, or the window will freeze.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Optional

from eva.common.logging_setup import get_logger
from .brain_settings import BrainSettings
from .brain_types import BrainResult, BrainStatus
from .gateway import (BaseGateway, GatewayBadResponse, GatewayRateLimited,
                      GatewayTimeout, GatewayUnavailable, GatewayError, make_gateway)
from .interpreter import InterpretError, interpret
from .prompts import build_system_prompt
from .specs import CapabilitySpec, default_specs

log = get_logger("eva.brain")

# Safe things EVA may say when the Brain fails (spoken to the user).
_SAY = {
    BrainStatus.UNAVAILABLE: "My language brain is not available right now.",
    BrainStatus.RATE_LIMITED: "I am a bit busy. Please try again in a minute.",
    BrainStatus.TIMEOUT: "That took too long. Please try again.",
    BrainStatus.REJECTED: "I could not understand that safely. Please say it again.",
    BrainStatus.ERROR: "Something went wrong in my brain. Please try again.",
}


class Brain:
    def __init__(self, settings: Optional[BrainSettings] = None,
                 gateway: Optional[BaseGateway] = None,
                 specs: Optional[Dict[str, CapabilitySpec]] = None):
        self.settings = settings or BrainSettings.from_env()
        self.specs = specs if specs is not None else default_specs()
        self._gateway = gateway
        self._history: Deque[tuple] = deque(maxlen=self.settings.history_turns * 2)

    # -- public -----------------------------------------------------------
    def clear_history(self) -> None:
        self._history.clear()

    def think(self, text: str, mode: str = "COMMAND_MODE") -> BrainResult:
        text = (text or "").strip()[: self.settings.max_input_chars]
        if not text:
            return BrainResult(BrainStatus.REJECTED, reply=_SAY[BrainStatus.REJECTED], detail="empty input")
        if not self.settings.enabled:
            return self._fail(BrainStatus.UNAVAILABLE, "brain disabled by setting")

        try:
            if self._gateway is None:
                self._gateway = make_gateway(self.settings)
            use_history = mode == "CONVERSATION_MODE"
            messages = (list(self._history) if use_history else []) + [("user", text)]
            raw = self._gateway.generate(build_system_prompt(self.specs, mode), messages)
            kind, reply, proposal = interpret(raw, self.specs, self.settings.min_confidence)
        except GatewayRateLimited as exc:
            return self._fail(BrainStatus.RATE_LIMITED, str(exc))
        except GatewayTimeout as exc:
            return self._fail(BrainStatus.TIMEOUT, str(exc))
        except GatewayUnavailable as exc:
            return self._fail(BrainStatus.UNAVAILABLE, str(exc))
        except GatewayBadResponse as exc:
            return self._fail(BrainStatus.ERROR, str(exc))
        except InterpretError as exc:
            return self._fail(BrainStatus.REJECTED, str(exc))
        except GatewayError as exc:
            return self._fail(BrainStatus.ERROR, str(exc))
        except Exception as exc:  # never let the Brain crash EVA
            log.exception("unexpected brain error")
            return self._fail(BrainStatus.ERROR, f"unexpected: {type(exc).__name__}")

        if mode == "CONVERSATION_MODE" and kind == "chat":
            self._history.append(("user", text))
            self._history.append(("model", reply))

        log.info("brain ok kind=%s capability=%s", kind, proposal.capability if proposal else None)
        return BrainResult(BrainStatus.OK, kind=kind, reply=reply, proposal=proposal)

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _fail(status: BrainStatus, detail: str) -> BrainResult:
        log.warning("brain %s: %s", status.value, detail)
        return BrainResult(status, kind="unknown", reply=_SAY[status], detail=detail)
