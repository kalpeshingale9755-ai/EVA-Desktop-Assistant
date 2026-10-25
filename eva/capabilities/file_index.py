"""EVA's list of your files (names only; she never reads inside them).

Only the folders in file_settings are scanned, only safe file types are listed, and the list is saved to a
small file. A background refresh keeps it fresh; a quick look at the top level of each folder catches a file
you downloaded a minute ago.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from eva.capabilities.file_settings import FileSettings
from eva.common.logging_setup import get_logger

log = get_logger("eva.file_index")
_REPARSE_POINT = 0x400        # Windows junctions and links: never followed


@dataclass(frozen=True)
class FileEntry:
    name: str                 # file name without the type ("Resume"), or the folder name
    ext: str = ""             # ".pdf" (empty for folders)
    path: str = ""
    mtime: float = 0.0
    is_dir: bool = False

    @property
    def display(self) -> str:
        return self.name + self.ext


def _is_reparse(entry: os.DirEntry) -> bool:
    try:
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return False


def scan_folders(roots: list[Path], settings: FileSettings, depth_limit: int | None = None) -> list[FileEntry]:
    """Walk the folders (no recursion, no links, no hidden or junk folders)."""
    allowed = {e.lower() for e in settings.allowed_extensions} - {e.lower() for e in settings.blocked_extensions}
    skip = {s.lower() for s in settings.skip_dir_names}
    limit = settings.max_depth if depth_limit is None else min(depth_limit, settings.max_depth)
    found: list[FileEntry] = []
    files = 0
    for root in roots:
        stack = [(str(root), 1)]
        while stack:
            folder, depth = stack.pop()
            try:
                with os.scandir(folder) as it:
                    children = list(it)
            except OSError:
                continue
            for child in children:
                name = child.name
                if name.startswith((".", "$", "~")):
                    continue
                try:
                    if child.is_dir(follow_symlinks=False):
                        if child.is_symlink() or _is_reparse(child) or name.lower() in skip:
                            continue
                        found.append(FileEntry(name, "", child.path, 0.0, True))
                        if depth < limit:
                            stack.append((child.path, depth + 1))
                    elif child.is_file(follow_symlinks=False):
                        stem, ext = os.path.splitext(name)
                        if ext.lower() not in allowed or not stem:
                            continue
                        found.append(FileEntry(stem, ext.lower(), child.path, child.stat().st_mtime))
                        files += 1
                        if files >= settings.max_files:
                            log.warning("File list is full (%d files): the rest is ignored", files)
                            return found
                except OSError:
                    continue
    return found


class FileIndex:
    def __init__(self, settings: FileSettings | None = None, roots: list[Path] | None = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        self.settings = settings or FileSettings()
        self.roots: list[Path] = roots if roots is not None else self.settings.roots()
        self._now = now_fn
        self._entries: list[FileEntry] = self._root_entries()
        self._saved_at: float | None = None
        self._last_miss = 0.0
        self._last_quick = 0.0
        self._thread: threading.Thread | None = None

    # ---- reading ----------------------------------------------------------------
    def entries(self) -> list[FileEntry]:
        return self._entries

    def root_names(self) -> set[str]:
        return {r.name.lower() for r in self.roots}

    @property
    def is_stale(self) -> bool:
        return (self._saved_at is None
                or self._now() - self._saved_at > self.settings.refresh_hours * 3600)

    def _root_entries(self) -> list[FileEntry]:
        return [FileEntry(r.name, "", str(r), 0.0, True) for r in self.roots]

    # ---- cache file -------------------------------------------------------------
    def _cache_path(self) -> Path:
        return self.settings.data_path() / self.settings.cache_file

    def load_cache(self) -> bool:
        path = self._cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["roots"] != [str(r) for r in self.roots]:
                log.info("Folders to search changed: the file list will be rebuilt")
                return False
            entries = [FileEntry(e["n"], e.get("e", ""), e["p"], e.get("m", 0.0), bool(e.get("d")))
                       for e in data["entries"]]
            saved_at = float(data["saved_at"])
        except FileNotFoundError:
            return False
        except Exception:
            log.exception("File list file is damaged, it will be rebuilt: %s", path)
            return False
        self._entries, self._saved_at = entries, saved_at
        log.info("File list loaded from file: %d items", len(entries))
        return True

    def _save_cache(self) -> None:
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"saved_at": self._saved_at, "roots": [str(r) for r in self.roots],
                       "entries": [{"n": e.name, "e": e.ext, "p": e.path, "m": e.mtime, "d": e.is_dir}
                                   for e in self._entries]}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.exception("Could not save the file list")

    # ---- refreshing -------------------------------------------------------------
    def refresh(self) -> bool:
        started = time.perf_counter()
        try:
            found = scan_folders(self.roots, self.settings)
        except Exception:
            log.exception("Could not scan the folders")
            return False
        self._entries = self._root_entries() + found
        self._saved_at = self._now()
        self._save_cache()
        log.info("File list refreshed: %d items in %.1fs", len(self._entries), time.perf_counter() - started)
        return True

    def refresh_in_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.refresh, name="eva-file-index", daemon=True)
        self._thread.start()

    def ensure_fresh(self) -> None:
        if self.is_stale:
            self.refresh_in_background()

    def quick_refresh(self) -> bool:
        """Look at the TOP level of each folder right now (fast). Catches a file downloaded a moment ago."""
        now = self._now()
        if now - self._last_quick < self.settings.quick_refresh_min_interval_s:
            return False
        self._last_quick = now
        try:
            fresh = scan_folders(self.roots, self.settings, depth_limit=1)
        except Exception:
            log.exception("Quick look at the folders failed")
            return False
        top = {os.path.normcase(str(r)) for r in self.roots}
        kept = [e for e in self._entries
                if os.path.normcase(os.path.dirname(e.path)) not in top]   # everything deeper stays
        self._entries = kept + fresh
        return True

    def note_miss(self) -> None:
        """Something was asked for and not found: do a full refresh in the background (not too often)."""
        now = self._now()
        if now - self._last_miss >= self.settings.miss_refresh_min_interval_s:
            self._last_miss = now
            self.refresh_in_background()
