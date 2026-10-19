"""Simple config. Reads config.json in the project root (created on first run)."""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from eva.common.logging_setup import get_logger

log = get_logger("eva.config")

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT_DIR / "config.json"


@dataclass
class Config:
    app_name: str = "EVA"
    window_width: int = 420
    window_height: int = 620
    start_hidden: bool = False  # True = start in the tray without showing the window
    show_dev_panel: bool = True  # buttons to simulate interface states (testing only)


def load_config() -> Config:
    defaults = asdict(Config())
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
        log.info("Created default config at %s", CONFIG_PATH)
        return Config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read config.json (%s). Using defaults.", exc)
        return Config()
    known = {k: v for k, v in data.items() if k in defaults}
    return Config(**{**defaults, **known})
