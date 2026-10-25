"""Pure logic: which file or folder did the person mean?  No Windows, no disk.

"open my resume pdf" -> name "resume", type PDF -> best matching file name, or a list if two are close.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from eva.capabilities.app_matcher import score_name
from eva.capabilities.file_index import FileEntry
from eva.capabilities.file_settings import FileSettings
from eva.capabilities.text_match import normalize

_DOCS = frozenset({".doc", ".docx", ".odt", ".rtf"})
_SHEETS = frozenset({".xls", ".xlsx", ".csv", ".ods"})
_SLIDES = frozenset({".ppt", ".pptx", ".odp"})
_IMAGES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"})
_AUDIO = frozenset({".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"})
_VIDEO = frozenset({".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"})

# A type word at the END of what you say narrows the search: "resume pdf", "sales excel sheet"
EXT_WORDS: dict[str, frozenset[str]] = {
    "pdf": frozenset({".pdf"}), "word": _DOCS, "excel": _SHEETS, "spreadsheet": _SHEETS, "sheet": _SHEETS,
    "powerpoint": _SLIDES, "presentation": _SLIDES, "slides": _SLIDES, "ppt": _SLIDES,
    "text": frozenset({".txt"}), "image": _IMAGES, "photo": _IMAGES, "picture": _IMAGES, "pic": _IMAGES,
    "music": _AUDIO, "song": _AUDIO, "audio": _AUDIO, "video": _VIDEO, "movie": _VIDEO,
}
GENERIC_WORDS = frozenset({"file", "document", "doc"})          # "resume file", "resume document"
FOLDER_WORDS = frozenset({"folder", "directory"})
LEAD_WORDS = frozenset({"file", "document", "folder", "directory", "called", "named", "the", "my"})


@dataclass(frozen=True)
class ParsedTarget:
    name: str
    exts: frozenset[str] | None
    want_dir: bool
    explicit: bool           # the person said file / folder / a type word: so it is surely not an app


def parse_target(target: str, root_names: set[str] | frozenset[str] = frozenset()) -> ParsedTarget | None:
    tokens = normalize(target).split()
    want_dir = explicit = False
    exts: frozenset[str] | None = None

    while tokens and tokens[0] in LEAD_WORDS:
        word = tokens.pop(0)
        if word in FOLDER_WORDS:
            want_dir = explicit = True
        elif word in GENERIC_WORDS:
            explicit = True
    while len(tokens) > 1:
        last = tokens[-1]
        if last in FOLDER_WORDS:
            want_dir = explicit = True
        elif last in GENERIC_WORDS:
            explicit = True
        elif last in EXT_WORDS:
            exts = EXT_WORDS[last] if exts is None else exts | EXT_WORDS[last]
            explicit = True
        else:
            break
        tokens.pop()

    name = " ".join(tokens)
    if not name:
        return None
    if name in root_names and exts is None:           # "open downloads" = the Downloads folder
        want_dir, explicit = True, True
    return ParsedTarget(name, exts, want_dir, explicit)


class Kind(str, Enum):
    FOUND = "FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


@dataclass(frozen=True)
class Match:
    entry: FileEntry
    score: float


@dataclass(frozen=True)
class Decision:
    kind: Kind
    matches: tuple[Match, ...] = ()


def rank_files(parsed: ParsedTarget, entries: list[FileEntry]) -> list[Match]:
    matches = []
    for e in entries:
        if e.is_dir != parsed.want_dir:
            continue
        if parsed.exts is not None and e.ext not in parsed.exts:
            continue
        score = score_name(parsed.name, e.name)
        if score > 0.5:
            matches.append(Match(e, round(score, 3)))
    matches.sort(key=lambda m: (-m.score, len(m.entry.name), -m.entry.mtime, m.entry.path))
    return matches


def decide_files(matches: list[Match], settings: FileSettings) -> Decision:
    good = [m for m in matches if m.score >= settings.min_score]
    if not good:
        return Decision(Kind.NONE)
    top = good[0]
    close = [m for m in good if top.score - m.score < settings.ambiguity_gap]
    if len(close) > 1:
        return Decision(Kind.AMBIGUOUS, tuple(close[:settings.max_listed]))
    return Decision(Kind.FOUND, (top,))


def spoken_labels(entries: list[FileEntry]) -> list[str]:
    """['Resume.pdf', 'Resume.docx'] - and when two have the same name, say which folder."""
    names = [e.display for e in entries]
    out = []
    for e, n in zip(entries, names):
        if names.count(n) > 1:
            parent = os.path.basename(os.path.dirname(e.path))
            out.append(f"{n} in {parent}")
        else:
            out.append(n)
    return out
