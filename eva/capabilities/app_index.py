"""EVA's list of installed apps (read from the Windows Start menu).

- Loaded from a small file at start-up (fast), refreshed in the background when old.
- Notepad, Calculator and File Explorer are always available as a safety net.
- Only apps from THIS list can ever be opened. The list comes from Windows, not from speech.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

from eva.capabilities.app_settings import AppSettings
from eva.capabilities.text_match import normalize
from eva.common.logging_setup import get_logger

log = get_logger("eva.app_index")


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str = ""                 # Windows Start-menu id (opened through shell:AppsFolder)
    argv: tuple[str, ...] = ()       # or a fixed command, for the built-in safety-net apps


BUILTIN_APPS: tuple[AppEntry, ...] = (
    AppEntry("Notepad", argv=("notepad.exe",)),
    AppEntry("Calculator", argv=("calc.exe",)),
    AppEntry("File Explorer", argv=("explorer.exe",)),
)


class AppIndex:
    def __init__(self, probe, settings: AppSettings | None = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._probe = probe
        self._settings = settings or AppSettings()
        self._now = now_fn
        self._system: list[AppEntry] = []
        self._merged: list[AppEntry] = list(BUILTIN_APPS)
        self._saved_at: float | None = None
        self._last_miss_refresh = 0.0
        self._thread: threading.Thread | None = None

    # ---- reading ----------------------------------------------------------------
    def entries(self) -> list[AppEntry]:
        return self._merged

    @property
    def has_system_apps(self) -> bool:
        return bool(self._system)

    @property
    def is_stale(self) -> bool:
        if self._saved_at is None:
            return True
        return self._now() - self._saved_at > self._settings.refresh_hours * 3600

    # ---- cache file -------------------------------------------------------------
    def _cache_path(self):
        return self._settings.data_path() / self._settings.cache_file

    def load_cache(self) -> bool:
        path = self._cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            apps = [AppEntry(a["name"], a.get("app_id", "")) for a in data["apps"]
                    if a.get("name") and a.get("app_id")]
            saved_at = float(data["saved_at"])
        except FileNotFoundError:
            return False
        except Exception:
            log.exception("App list file is damaged, it will be rebuilt: %s", path)
            return False
        self._set_system(apps, saved_at)
        log.info("App list loaded from file: %d apps", len(apps))
        return True

    def _save_cache(self) -> None:
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"saved_at": self._saved_at,
                       "apps": [{"name": e.name, "app_id": e.app_id} for e in self._system]}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.exception("Could not save the app list file")

    # ---- refreshing -------------------------------------------------------------
    def refresh(self) -> bool:
        """Read the Start menu now (takes about a second). Keeps the old list if it fails."""
        started = time.perf_counter()
        try:
            raw = self._probe.list_start_apps()
        except Exception:
            log.exception("Could not read the Start menu app list")
            return False
        apps, seen = [], set()
        for item in raw or []:
            name, app_id = str(item.get("Name") or "").strip(), str(item.get("AppID") or "").strip()
            key = normalize(name)
            if not name or not app_id or not key or key in seen:
                continue
            seen.add(key)
            apps.append(AppEntry(name, app_id))
        if not apps:
            log.warning("Start menu list was empty, keeping the old list")
            return False
        self._set_system(apps, self._now())
        self._save_cache()
        log.info("App list refreshed: %d apps in %.1fs", len(apps), time.perf_counter() - started)
        return True

    def refresh_in_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.refresh, name="eva-app-index", daemon=True)
        self._thread.start()

    def ensure_fresh(self) -> None:
        if self.is_stale:
            self.refresh_in_background()

    def note_miss(self) -> None:
        """An app was asked for but not found: maybe it was just installed. Refresh (not too often)."""
        now = self._now()
        if now - self._last_miss_refresh >= self._settings.miss_refresh_min_interval_s:
            self._last_miss_refresh = now
            self.refresh_in_background()

    # ---- internal ---------------------------------------------------------------
    def _set_system(self, apps: list[AppEntry], saved_at: float) -> None:
        have = {normalize(a.name) for a in apps}
        merged = list(apps) + [b for b in BUILTIN_APPS if normalize(b.name) not in have]
        self._system, self._saved_at, self._merged = apps, saved_at, merged   # one swap, no lock needed
