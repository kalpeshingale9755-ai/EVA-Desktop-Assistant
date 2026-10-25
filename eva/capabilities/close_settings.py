"""Settings for the close-app capability. Change a value here, not inside the code."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CloseSettings:
    min_score: float = 0.88          # stricter than opening: closing is the risky direction
    ambiguity_gap: float = 0.03
    max_spoken_words: int = 5

    close_wait_seconds: float = 4.0  # how long EVA waits for the windows to really disappear
    exit_wait_seconds: float = 1.5   # then, how long she waits for the program itself to exit
    poll_interval_s: float = 0.2

    # Programs EVA will never close (file name without .exe). Terminals are here because EVA
    # may be running inside one; the rest keep Windows itself working.
    protected_exes: tuple[str, ...] = (
        "dwm", "csrss", "winlogon", "searchhost", "searchapp", "startmenuexperiencehost",
        "shellexperiencehost", "textinputhost", "lockapp", "sihost", "taskhostw",
        "cmd", "powershell", "pwsh", "windowsterminal", "wt", "conhost", "openconsole",
        "python", "pythonw", "eva",
    )
    # These programs own windows of OTHER apps / the desktop, so "still running" means nothing for them.
    shared_process_exes: tuple[str, ...] = ("explorer", "applicationframehost")

    # "close this", "close everything" ... EVA asks which app you mean instead of guessing.
    vague_words: tuple[str, ...] = (
        "this", "it", "that", "them", "all", "everything", "everything else", "everything open",
        "window", "windows", "all windows", "this window", "that window", "current window",
        "this app", "that app", "the app", "app", "apps", "all apps", "program", "this program",
    )
    # "quit eva" / "exit yourself": EVA does not close herself.
    self_words: tuple[str, ...] = ("eva", "ava", "eve", "evie", "yourself", "you", "yourself eva")
