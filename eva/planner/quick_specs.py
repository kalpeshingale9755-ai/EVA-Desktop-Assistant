"""Capability specs for the Stage 6b quick actions. Merge with the other specs."""
from __future__ import annotations

from typing import Dict

from eva.brain.specs import ArgSpec, CapabilitySpec


def quick_specs() -> Dict[str, CapabilitySpec]:
    steps = ArgSpec(type="int", required=False,
                    description="number of volume steps, each about 2 percent; default 5")
    specs = [
        CapabilitySpec("tell_time", "Say the current time"),
        CapabilitySpec("tell_date", "Say today's date and weekday"),
        CapabilitySpec("battery_status", "Say the battery percentage and whether it is charging"),
        CapabilitySpec("volume_up", "Raise the system volume", {"steps": steps}),
        CapabilitySpec("volume_down", "Lower the system volume", {"steps": steps}),
        CapabilitySpec("volume_mute", "Mute or unmute the system sound (it toggles)"),
        CapabilitySpec("take_screenshot", "Take a screenshot of the whole screen and save it as a file"),
    ]
    return {s.name: s for s in specs}
