"""Planner: the single LLM fallback for Core (replaces brain.think from Stage 5).

    planner = Planner(settings=settings, gateway=gateway, specs=specs)
    result = planner.plan("search for python tutorials and open youtube", mode)

plan() is BLOCKING (up to the gateway timeout). Call it from a worker thread.
It never raises and never runs anything. Share ONE gateway object with Brain so
both use the same rate guard:  gateway = make_gateway(settings)
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Deque, Dict, Optional

from eva.brain.brain_settings import BrainSettings
from eva.brain.brain_types import BrainStatus
from eva.brain.gateway import (BaseGateway, GatewayBadResponse, GatewayError, GatewayRateLimited,
                               GatewayTimeout, GatewayUnavailable, make_gateway)
from eva.brain.interpreter import InterpretError
from eva.brain.specs import CapabilitySpec, default_specs

from .plan_interpreter import interpret_plan
from .plan_prompts import build_plan_prompt
from .plan_settings import PlanSettings
from .plan_specs import web_specs
from .plan_types import PlanResult

log = logging.getLogger("eva.planner")

_SAY = {
    BrainStatus.UNAVAILABLE: "My language brain is not available right now.",
    BrainStatus.RATE_LIMITED: "I am a bit busy. Please try again in a minute.",
    BrainStatus.TIMEOUT: "That took too long. Please try again.",
    BrainStatus.REJECTED: "I could not understand that safely. Please say it again.",
    BrainStatus.ERROR: "Something went wrong in my brain. Please try again.",
}


class Planner:
    def __init__(self, settings: Optional[BrainSettings] = None,
                 gateway: Optional[BaseGateway] = None,
                 specs: Optional[Dict[str, CapabilitySpec]] = None,
                 plan_settings: Optional[PlanSettings] = None):
        self.settings = settings or BrainSettings.from_env()
        self.plan_settings = plan_settings or PlanSettings()
        self.specs = specs if specs is not None else {**default_specs(), **web_specs()}
        self._gateway = gateway
        self._history: Deque[tuple] = deque(maxlen=self.settings.history_turns * 2)

    def clear_history(self) -> None:
        self._history.clear()

    def plan(self, text: str, mode: str = "COMMAND_MODE") -> PlanResult:
        text = (text or "").strip()[: self.settings.max_input_chars]
        if not text:
            return self._fail(BrainStatus.REJECTED, "empty input")
        if not self.settings.enabled:
            return self._fail(BrainStatus.UNAVAILABLE, "brain disabled by setting")
        try:
            if self._gateway is None:
                self._gateway = make_gateway(self.settings)
            history = list(self._history) if mode == "CONVERSATION_MODE" else []
            raw = self._gateway.generate(
                build_plan_prompt(self.specs, mode, self.plan_settings.max_steps),
                history + [("user", text)])
            kind, reply, steps, needs_conf = interpret_plan(
                raw, self.specs, self.settings.min_confidence, self.plan_settings.max_steps)
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
        except Exception as exc:  # never let the Planner crash EVA
            log.exception("unexpected planner error")
            return self._fail(BrainStatus.ERROR, f"unexpected: {type(exc).__name__}")

        if mode == "CONVERSATION_MODE" and kind == "chat":
            self._history.append(("user", text))
            self._history.append(("model", reply))
        log.info("plan ok kind=%s steps=%s", kind, [s.capability for s in steps])
        return PlanResult(BrainStatus.OK, kind=kind, reply=reply, steps=steps,
                          needs_confirmation=needs_conf)

    @staticmethod
    def _fail(status: BrainStatus, detail: str) -> PlanResult:
        log.warning("planner %s: %s", status.value, detail)
        return PlanResult(status, kind="unknown", reply=_SAY[status], detail=detail)
