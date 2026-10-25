"""Check EVA's app list and matching WITHOUT opening anything.

    python -m eva.capabilities.check_apps             -> how many apps EVA can see
    python -m eva.capabilities.check_apps spotify     -> what EVA would do for "open spotify"
"""
from __future__ import annotations

import sys

from eva.capabilities.app_index import AppIndex
from eva.capabilities.app_matcher import decide, rank
from eva.capabilities.app_settings import AppSettings
from eva.capabilities.open_app import load_aliases
from eva.capabilities.system_probe import WindowsProbe


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    settings = AppSettings()
    index = AppIndex(WindowsProbe(), settings)
    ok = index.refresh()
    entries = index.entries()
    print(f"Read from Windows: {'yes' if ok else 'NO (only the 3 built-in apps are available)'}")
    print(f"Apps EVA can see: {len(entries)}   (list file: {settings.data_path() / settings.cache_file})")

    query = " ".join(sys.argv[1:]).strip()
    if not query:
        print("First 25 apps:", ", ".join(e.name for e in entries[:25]))
        return 0 if ok else 1

    matches = rank(query, entries, load_aliases(settings), settings.blocklist)
    decision = decide(matches, settings)
    print(f'\nFor "open {query}":')
    for m in matches[:5]:
        print(f"  {m.score:.2f}  {m.entry.name}{'   [BLOCKED]' if m.blocked else ''}")
    print(f"EVA would: {decision.kind.value}", [m.entry.name for m in decision.matches])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
