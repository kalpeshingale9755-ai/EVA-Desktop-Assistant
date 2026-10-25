"""Settings for the open-file capability. Change a value here, not inside the code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSettings:
    # ---- where EVA looks ---------------------------------------------------------
    use_default_folders: bool = True        # Documents, Downloads, Desktop (and their OneDrive versions)
    folders: tuple[str, ...] = ()           # EXTRA folders, full paths, e.g. ("D:\\Projects", "E:\\Study")
    max_depth: int = 6                      # how many folder levels deep
    max_files: int = 100_000

    # ---- keeping the list fresh ---------------------------------------------------
    refresh_hours: float = 24.0
    miss_refresh_min_interval_s: float = 300.0   # file not found -> full refresh in background (not more often)
    quick_refresh_min_interval_s: float = 5.0    # file not found -> instant look at the top level of each folder
    data_dir: str | None = None                  # None = %LOCALAPPDATA%\EVA
    cache_file: str = "file_index.json"

    # ---- matching ----------------------------------------------------------------
    min_score: float = 0.85                 # stricter than apps: a wrong file is annoying
    ambiguity_gap: float = 0.03
    max_spoken_words: int = 6
    max_listed: int = 3                     # EVA reads at most this many names when unsure

    # ---- checking that it opened (Word and PDF viewers can be slow to start) -----
    verify_seconds: float = 10.0
    poll_interval_s: float = 0.25

    # ---- safety: ONLY these types can be opened; the rest are never opened by voice ---------
    allowed_extensions: tuple[str, ...] = (
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
        ".xls", ".xlsx", ".csv", ".ods",
        ".ppt", ".pptx", ".odp",
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
        ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg",
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm",
    )
    # Second lock (belt and braces): even if one of these ends up in the list, it is refused.
    blocked_extensions: tuple[str, ...] = (
        ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe",
        ".js", ".jse", ".wsf", ".wsh", ".hta", ".jar", ".reg", ".lnk", ".url", ".pif", ".cpl",
        ".msc", ".dll", ".sys", ".py", ".pyw", ".sh", ".iso", ".docm", ".xlsm", ".pptm",
    )
    skip_dir_names: tuple[str, ...] = (
        "node_modules", "__pycache__", "venv", ".venv", "env", "site-packages", "appdata",
        "$recycle.bin", "system volume information", ".git", ".idea", ".vscode",
    )

    def data_path(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir)
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "EVA"

    def roots(self, home: Path | None = None, env: dict | None = None,
              is_dir=os.path.isdir) -> list[Path]:
        """The folders to search (only the ones that exist, no duplicates)."""
        home = home or Path.home()
        env = os.environ if env is None else env
        candidates: list[Path] = []
        if self.use_default_folders:
            candidates += [home / "Documents", home / "Downloads", home / "Desktop"]
            for var in ("OneDrive", "OneDriveConsumer"):
                base = env.get(var)
                if base:
                    candidates += [Path(base) / "Documents", Path(base) / "Desktop"]
        candidates += [Path(f) for f in self.folders]
        roots, seen = [], set()
        for c in candidates:
            key = os.path.normcase(os.path.abspath(c))
            if key not in seen and is_dir(c):
                seen.add(key)
                roots.append(c)
        return roots
