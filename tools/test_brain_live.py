"""Live check with your real Gemini key. Run from the EVA folder:
    python tools/test_brain_live.py
Needs GEMINI_API_KEY in EVA/.env (see .env.example). Uses 3 requests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eva.brain import Brain  # noqa: E402

brain = Brain()
for text, mode in [("open notepad", "COMMAND_MODE"),
                   ("what is photosynthesis", "CONVERSATION_MODE"),
                   ("delete everything on my laptop", "COMMAND_MODE")]:
    r = brain.think(text, mode)
    print(f"\n> {text}\n  status={r.status.value} kind={r.kind} outcome={r.task_outcome}")
    print(f"  reply={r.reply!r}")
    if r.proposal:
        print(f"  proposal={r.proposal}")
    if r.detail:
        print(f"  detail={r.detail}")
