"""Settings for the open-app capability. Change a value here, not inside the code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    # ---- matching -----------------------------------------------------------
    min_score: float = 0.80          # below this, "that is not an app I know"
    ambiguity_gap: float = 0.03      # two apps this close in score -> EVA asks for the full name
    max_spoken_words: int = 5        # "open <up to 5 words>"

    # ---- the list of installed apps ------------------------------------------
    refresh_hours: float = 24.0              # re-read the Start menu at least this often
    miss_refresh_min_interval_s: float = 300.0   # app not found -> refresh, but not more often than this
    data_dir: str | None = None              # None = %LOCALAPPDATA%\EVA
    cache_file: str = "app_index.json"
    aliases_file: str = "app_aliases.json"   # optional, you edit it: {"vs code": "Visual Studio Code"}

    # ---- checking that it opened ---------------------------------------------
    verify_seconds: float = 6.0
    poll_interval_s: float = 0.25

    # ---- safety: EVA will never open these (matched on whole words in the app name) ----
    blocklist: tuple[str, ...] = (
        "command prompt", "cmd", "powershell", "windows terminal", "terminal",
        "registry editor", "regedit", "task scheduler", "group policy",
        "local security policy", "computer management", "component services",
        "uninstall", "uninstaller",
    )

    def data_path(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir)
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EVA"
