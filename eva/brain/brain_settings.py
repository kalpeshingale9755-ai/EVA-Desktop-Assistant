"""Brain settings. Values can be overridden with environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# EVA/eva/brain/brain_settings.py  ->  parents[2] == EVA (project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Optional[Path] = None) -> None:
    """Tiny .env loader (no extra package). Never overrides real env variables."""
    path = path or (PROJECT_ROOT / ".env")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class BrainSettings:
    enabled: bool = True
    provider: str = "gemini"
    # Model names change over time. Check Google AI Studio for a current free Flash/Flash-Lite
    # model and set EVA_LLM_MODEL in .env if this one stops working.
    model: str = "gemini-2.5-flash-lite"
    api_key: str = ""
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 10.0
    temperature: float = 0.2
    max_output_tokens: int = 300
    max_input_chars: int = 500            # user text is cut to this (privacy + cost)
    max_requests_per_minute: int = 8      # local guard to stay inside free-tier limits
    min_confidence: float = 0.6           # below this, action proposals are rejected
    history_turns: int = 4                # short memory for CONVERSATION_MODE only

    @classmethod
    def from_env(cls) -> "BrainSettings":
        load_env_file()
        s = cls()
        s.enabled = _flag("EVA_LLM_ENABLED", s.enabled)
        s.provider = os.environ.get("EVA_LLM_PROVIDER", s.provider).strip().lower()
        s.model = os.environ.get("EVA_LLM_MODEL", s.model).strip()
        s.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        return s
