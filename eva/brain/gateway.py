"""Brain Gateway: the ONLY file that talks to an LLM provider.

To change provider later, add another class here that follows BaseGateway and
register it in make_gateway(). Nothing else in EVA needs to change.
Uses only the Python standard library (no extra pip install).
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Deque, List, Tuple

from eva.common.logging_setup import get_logger
from .brain_settings import BrainSettings

log = get_logger("eva.brain.gateway")

Message = Tuple[str, str]  # (role, text) where role is "user" or "model"


class GatewayError(Exception):
    """Base class. Messages are for logs only (never contain the API key)."""


class GatewayUnavailable(GatewayError):
    """No key, disabled, no internet, bad key, server down."""


class GatewayRateLimited(GatewayError):
    pass


class GatewayTimeout(GatewayError):
    pass


class GatewayBadResponse(GatewayError):
    pass


class _RateGuard:
    """Sliding 60 second window so EVA stops itself before the provider says 429."""

    def __init__(self, max_per_minute: int):
        self.max = max_per_minute
        self._hits: Deque[float] = deque()
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > 60:
                self._hits.popleft()
            if len(self._hits) >= self.max:
                return False
            self._hits.append(now)
            return True


class BaseGateway:
    def generate(self, system_prompt: str, messages: List[Message]) -> str:
        raise NotImplementedError


class GeminiGateway(BaseGateway):
    def __init__(self, settings: BrainSettings, _urlopen=None, _sleep=None, **kw):
        self.s = settings
        self._guard = _RateGuard(settings.max_requests_per_minute)
        self._urlopen = _urlopen or kw.get("urlopen")
        self._sleep = _sleep or kw.get("sleep")

    def generate(self, system_prompt: str, messages: List[Message]) -> str:
        if not self.s.api_key:
            raise GatewayUnavailable("GEMINI_API_KEY is not set")
        if not self._guard.try_acquire():
            raise GatewayRateLimited("local rate guard: too many requests this minute")

        url = f"{self.s.base_url}/models/{self.s.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": r, "parts": [{"text": t}]} for r, t in messages],
            "generationConfig": {
                "temperature": self.s.temperature,
                "maxOutputTokens": self.s.max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.s.api_key}

        urlopen_fn = self._urlopen or urllib.request.urlopen
        sleep_fn = self._sleep or time.sleep
        delays = (0.8, 1.6)

        for attempt in range(1 + len(delays)):
            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST",
            )
            try:
                with urlopen_fn(req, timeout=self.s.timeout_seconds) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return self._extract_text(payload)
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code in (500, 502, 503, 504):
                    if attempt < len(delays):
                        sleep_fn(delays[attempt])
                        continue
                    raise GatewayUnavailable("provider busy (HTTP 503)")
                if code == 429:
                    raise GatewayRateLimited("provider returned 429")
                if code in (400, 401, 403):
                    raise GatewayUnavailable(f"provider rejected request (HTTP {code}); check key/model name")
                if code == 404:
                    raise GatewayUnavailable("model not found; set EVA_LLM_MODEL to a current model")
                raise GatewayUnavailable(f"provider error HTTP {code}")
            except (TimeoutError, socket.timeout):
                raise GatewayTimeout("LLM timed out")
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    raise GatewayTimeout("LLM timed out")
                raise GatewayUnavailable("cannot reach provider (no internet?)")
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise GatewayBadResponse("provider sent unreadable data")

    @staticmethod
    def _extract_text(payload: dict) -> str:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError, TypeError):
            raise GatewayBadResponse("no answer in provider response (blocked or empty)")
        if not text:
            raise GatewayBadResponse("empty answer from provider")
        return text


def make_gateway(settings: BrainSettings) -> BaseGateway:
    if settings.provider == "gemini":
        return GeminiGateway(settings)
    raise GatewayUnavailable(f"unknown provider '{settings.provider}'")
