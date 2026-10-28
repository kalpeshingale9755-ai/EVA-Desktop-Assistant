"""Live check of the quick actions on your own laptop. Run from the EVA folder:

    python tools/test_quick_live.py             # time, date, battery, local command matching, screenshot
    python tools/test_quick_live.py --volume    # ALSO presses volume up, volume down and mute (toggle)

The screenshot is saved in Pictures\\EVA Screenshots (or EVA_SCREENSHOT_DIR if you set it).
Uses no Gemini requests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eva.capabilities import quick_actions as q  # noqa: E402
from eva.planner.local_quick import parse_quick_command  # noqa: E402


def show(name, r):
    print(f"{name:16} {r.outcome:8} {r.message!r}" + (f"   [{r.detail}]" if r.detail else ""))


show("tell_time", q.tell_time())
show("tell_date", q.tell_date())
show("battery_status", q.battery_status())
show("take_screenshot", q.take_screenshot())

print("\nLocal command matching (no LLM):")
for text in ["what time is it", "what's the date", "battery level", "volume up", "mute",
             "take a screenshot", "take a screenshot and open youtube"]:
    m = parse_quick_command(text)
    print(f"  {text!r:42} -> {m.capability if m else 'None (goes to the Planner)'}")

if "--volume" in sys.argv:
    print("\nVolume keys (watch the Windows volume popup):")
    show("volume_up", q.volume_up())
    show("volume_down", q.volume_down())
    show("volume_mute", q.volume_mute())
    show("volume_mute", q.volume_mute())   # second press turns sound back on
else:
    print("\n(Volume keys not tested. Add --volume to test them.)")
