"""Pure logic: which RUNNING program did the person mean?  No Windows, no files.

Matching is done on the PROGRAM NAME (notepad.exe), not on window titles, because a title can lie:
a browser tab called "How to use Notepad" must never be closed by "close notepad".
Only Store apps (Calculator, Settings...) are matched by title, because their program is a shared host.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from eva.capabilities.app_matcher import score_name
from eva.capabilities.close_settings import CloseSettings
from eva.capabilities.system_probe import WindowInfo
from eva.capabilities.text_match import normalize

# Spoken name -> program file names (without .exe) for programs whose name is not what people say.
EXE_ALIASES: dict[str, tuple[str, ...]] = {
    "chrome": ("chrome",), "google chrome": ("chrome",),
    "vs code": ("code",), "vscode": ("code",), "visual studio code": ("code",), "code": ("code",),
    "edge": ("msedge",), "microsoft edge": ("msedge",),
    "word": ("winword",), "microsoft word": ("winword",),
    "excel": ("excel",), "microsoft excel": ("excel",),
    "powerpoint": ("powerpnt",), "power point": ("powerpnt",), "microsoft powerpoint": ("powerpnt",),
    "outlook": ("outlook", "olk"), "teams": ("ms-teams", "teams"), "microsoft teams": ("ms-teams", "teams"),
    "paint": ("mspaint",), "task manager": ("taskmgr",), "snipping tool": ("snippingtool",),
    "file explorer": ("explorer",), "explorer": ("explorer",), "files": ("explorer",),
    "my files": ("explorer",), "windows explorer": ("explorer",),
}
FRIENDLY: dict[str, str] = {
    "code": "Visual Studio Code", "chrome": "Chrome", "msedge": "Edge", "winword": "Word",
    "excel": "Excel", "powerpnt": "PowerPoint", "mspaint": "Paint", "explorer": "File Explorer",
    "taskmgr": "Task Manager", "snippingtool": "Snipping Tool", "ms-teams": "Teams",
}
_STORE_HOST = "applicationframehost"


class Kind(str, Enum):
    FOUND = "FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    PROTECTED = "PROTECTED"
    NONE = "NONE"


@dataclass(frozen=True)
class Group:
    """All windows of one program."""
    key: str                      # program file name, or the title for Store apps
    name: str                     # what EVA calls it when she speaks
    windows: tuple[WindowInfo, ...]
    score: float
    protected: bool


@dataclass(frozen=True)
class Decision:
    kind: Kind
    groups: tuple[Group, ...] = ()


def exe_stem(exe: str) -> str:
    """'C:\\...\\Notepad.exe' or 'Notepad.exe' -> 'notepad'"""
    name = (exe or "").replace("/", "\\").rsplit("\\", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _spaced(stem: str) -> str:
    return normalize(stem.replace("+", "plus"))      # "notepad++" must NOT look like "notepad"


def friendly_name(stem: str, title: str = "") -> str:
    if title and stem == _STORE_HOST:
        return title
    return FRIENDLY.get(stem, stem[:1].upper() + stem[1:])


def find_running(query: str, windows: list[WindowInfo], aliases: dict[str, str],
                 settings: CloseSettings, own_pid: int | None = None) -> Decision:
    q = normalize(query)
    forms = [q]
    if q in aliases:
        forms.insert(0, normalize(aliases[q]))
    wanted_stems = {stem for f in forms for stem in EXE_ALIASES.get(f, ())}
    protected = set(settings.protected_exes)

    groups: dict[str, list[WindowInfo]] = {}
    scores: dict[str, float] = {}
    names: dict[str, str] = {}
    for w in windows:
        stem = exe_stem(w.exe)
        if not stem or (own_pid is not None and w.pid == own_pid):
            continue
        if stem == "explorer" and w.cls != "CabinetWClass":
            continue                                   # desktop, taskbar: never
        if stem == _STORE_HOST:
            score = max(score_name(f, w.title) for f in forms)
            key = "store:" + normalize(w.title)
        else:
            score = 1.0 if stem in wanted_stems else max(score_name(f, _spaced(stem)) for f in forms)
            key = stem
        if score < settings.min_score:
            continue
        groups.setdefault(key, []).append(w)
        scores[key] = max(scores.get(key, 0.0), score)
        names[key] = friendly_name(stem, w.title)

    ranked = sorted((Group(k, names[k], tuple(ws), round(scores[k], 3), k in protected)
                     for k, ws in groups.items()),
                    key=lambda g: (-g.score, g.name.lower()))
    if not ranked:
        return Decision(Kind.NONE)
    top = ranked[0]
    if top.protected:
        return Decision(Kind.PROTECTED, (top,))
    close = [g for g in ranked if not g.protected and top.score - g.score < settings.ambiguity_gap]
    if len(close) > 1:
        return Decision(Kind.AMBIGUOUS, tuple(close[:3]))
    return Decision(Kind.FOUND, (top,))


# "close notepad", "please quit the calculator app", "can you exit spotify please"
_CLOSE = re.compile(
    r"^(?:please )?(?:(?:can|could|would) you )?(?:please )?"
    r"(?:close|quit|exit)(?: down)? (?:the |my )?(?P<target>[a-z0-9 ]+?)"
    r"(?: (?:app|application|program|window|windows))?(?: (?:please|thanks|thank you))*$"
)


def extract_close_target(text: str) -> str | None:
    n = normalize(text)
    found = _CLOSE.match(n)
    if found:
        return found.group("target").strip()
    return None
