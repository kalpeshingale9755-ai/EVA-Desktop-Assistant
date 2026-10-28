"""Quick actions (Stage 6b): time, date, battery, volume, screenshot.

No extra pip packages. Windows features use ctypes and PowerShell (started with an
argument list, never through a shell string). Every function returns a
QuickActionResult and never raises.

Honesty notes (keep the spoken wording consistent with these):
- Volume keys are SENT to Windows. EVA cannot read the volume level back, so it says
  "Turning the volume up", not "Volume is now 60 percent". The mute key is a toggle.
- A screenshot is only reported as saved after the file really exists.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]

VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF
DEFAULT_VOLUME_STEPS = 5      # each key press is about 2 percent in Windows
MAX_VOLUME_STEPS = 25


@dataclass(frozen=True)
class QuickActionResult:
    outcome: str          # "SUCCESS" or "FAILED"
    message: str          # safe to speak
    detail: str = ""      # for logs only


# ---------------------------------------------------------------- time and date
def tell_time(now: Optional[datetime] = None) -> QuickActionResult:
    now = now or datetime.now()
    hour12 = now.hour % 12 or 12
    suffix = "AM" if now.hour < 12 else "PM"
    return QuickActionResult("SUCCESS", f"It is {hour12}:{now.minute:02d} {suffix}.")


def tell_date(now: Optional[datetime] = None) -> QuickActionResult:
    now = now or datetime.now()
    return QuickActionResult(
        "SUCCESS", f"Today is {_DAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]} {now.year}.")


# ---------------------------------------------------------------------- battery
def _read_power_status() -> Dict[str, int]:
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        raise OSError("GetSystemPowerStatus failed")
    return {"ac": status.ACLineStatus & 0xFF, "flag": status.BatteryFlag & 0xFF,
            "percent": status.BatteryLifePercent & 0xFF, "seconds": status.BatteryLifeTime & 0xFFFFFFFF}


def battery_status(reader: Optional[Callable[[], Dict[str, int]]] = None) -> QuickActionResult:
    try:
        s = (reader or _read_power_status)()
    except Exception as exc:
        return QuickActionResult("FAILED", "I cannot read the battery on this computer.",
                                 detail=type(exc).__name__)
    if s["flag"] == 128:
        return QuickActionResult("SUCCESS", "This computer has no battery.")
    if s["percent"] > 100:
        return QuickActionResult("FAILED", "I cannot read the battery level.", detail="unknown percent")

    text = f"Battery is at {s['percent']} percent"
    if s["ac"] == 1:
        text += " and charging." if s["flag"] & 8 else " and plugged in."
    elif s["ac"] == 0:
        text += " and running on battery"
        secs = s.get("seconds", 0xFFFFFFFF)
        if secs not in (0xFFFFFFFF, 0) and secs > 0:
            hours, minutes = secs // 3600, (secs % 3600) // 60
            parts = []
            if hours:
                parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes:
                parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            if parts:
                text += f", about {' '.join(parts)} left"
        text += "."
    else:
        text += "."
    return QuickActionResult("SUCCESS", text)


# ----------------------------------------------------------------------- volume
def _send_media_key(vk: int, count: int) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    for _ in range(count):
        user32.keybd_event(vk, 0, 0, 0)   # key down
        user32.keybd_event(vk, 0, 2, 0)   # key up


def _clamp_steps(steps) -> int:
    if isinstance(steps, bool) or not isinstance(steps, int):
        return DEFAULT_VOLUME_STEPS
    return max(1, min(steps, MAX_VOLUME_STEPS))


def _volume(vk: int, count: int, spoken: str, sender) -> QuickActionResult:
    try:
        (sender or _send_media_key)(vk, count)
    except Exception as exc:
        return QuickActionResult("FAILED", "I cannot change the volume on this computer.",
                                 detail=type(exc).__name__)
    return QuickActionResult("SUCCESS", spoken)


def volume_up(steps: Optional[int] = None, sender: Optional[Callable[[int, int], None]] = None):
    return _volume(VK_VOLUME_UP, _clamp_steps(steps), "Turning the volume up.", sender)


def volume_down(steps: Optional[int] = None, sender: Optional[Callable[[int, int], None]] = None):
    return _volume(VK_VOLUME_DOWN, _clamp_steps(steps), "Turning the volume down.", sender)


def volume_mute(sender: Optional[Callable[[int, int], None]] = None):
    return _volume(VK_VOLUME_MUTE, 1, "Toggling mute.", sender)


# ------------------------------------------------------------------- screenshot
_SHOT_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "Add-Type -TypeDefinition 'using System.Runtime.InteropServices; "
    "public class EvaDpi { [DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware(); }';"
    "[void][EvaDpi]::SetProcessDPIAware();"
    "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
    "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
    "$g=[System.Drawing.Graphics]::FromImage($bmp);"
    "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
    "$bmp.Save($env:EVA_SHOT_PATH,[System.Drawing.Imaging.ImageFormat]::Png);"
    "$g.Dispose();$bmp.Dispose()"
)


def _capture_with_powershell(path: Path) -> None:
    env = dict(os.environ, EVA_SHOT_PATH=str(path))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SHOT_SCRIPT],
                   env=env, timeout=20, check=True, capture_output=True, creationflags=flags)


def _screenshot_dir() -> Path:
    custom = os.environ.get("EVA_SCREENSHOT_DIR", "").strip()
    return Path(custom) if custom else Path.home() / "Pictures" / "EVA Screenshots"


def take_screenshot(directory: Optional[Path] = None,
                    capturer: Optional[Callable[[Path], None]] = None,
                    now: Optional[datetime] = None) -> QuickActionResult:
    folder = Path(directory) if directory else _screenshot_dir()
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = folder / f"EVA_screenshot_{stamp}.png"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (capturer or _capture_with_powershell)(path)
    except subprocess.TimeoutExpired:
        return QuickActionResult("FAILED", "The screenshot took too long.", detail="timeout")
    except Exception as exc:
        return QuickActionResult("FAILED", "I could not take the screenshot.", detail=type(exc).__name__)
    try:
        ok = path.is_file() and path.stat().st_size > 0
    except OSError:
        ok = False
    if not ok:
        return QuickActionResult("FAILED", "I could not save the screenshot.", detail="file missing")
    return QuickActionResult("SUCCESS", f"Screenshot saved in {folder.name}.", detail=str(path))


from eva.common.types import TaskOutcome
from eva.core.capability import Capability, CapabilityResult, Risk
from eva.core.registry import CapabilityRegistry
from eva.understanding.request import RequestObject


class TellTimeCapability(Capability):
    name = "tell_time"
    description = "Say the current time"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._now_fn = now_fn

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_quick import parse_quick_command
        m = parse_quick_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        now = self._now_fn() if self._now_fn else None
        res = tell_time(now=now)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail})


class TellDateCapability(Capability):
    name = "tell_date"
    description = "Say today's date and weekday"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._now_fn = now_fn

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_quick import parse_quick_command
        m = parse_quick_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        now = self._now_fn() if self._now_fn else None
        res = tell_date(now=now)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail})


class BatteryStatusCapability(Capability):
    name = "battery_status"
    description = "Say the battery percentage and whether it is charging"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, reader: Optional[Callable[[], Dict[str, int]]] = None) -> None:
        self._reader = reader

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_quick import parse_quick_command
        m = parse_quick_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        res = battery_status(reader=self._reader)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail})


class VolumeUpCapability(Capability):
    name = "volume_up"
    description = "Raise the system volume"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, sender: Optional[Callable[[int, int], None]] = None) -> None:
        self._sender = sender

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_quick import parse_quick_command
        m = parse_quick_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        steps = args.get("steps") if isinstance(args, dict) else None
        res = volume_up(steps=steps, sender=self._sender)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail})


class VolumeDownCapability(Capability):
    name = "volume_down"
    description = "Lower the system volume"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, sender: Optional[Callable[[int, int], None]] = None) -> None:
        self._sender = sender

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_quick import parse_quick_command
        m = parse_quick_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        steps = args.get("steps") if isinstance(args, dict) else None
        res = volume_down(steps=steps, sender=self._sender)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail})


class VolumeMuteCapability(Capability):
    name = "volume_mute"
    description = "Mute or unmute the system sound (it toggles)"
    risk = Risk.LOW
    timeout_s = 10.0

    def __init__(self, sender: Optional[Callable[[int, int], None]] = None) -> None:
        self._sender = sender

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_quick import parse_quick_command
        m = parse_quick_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        res = volume_mute(sender=self._sender)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail})


class TakeScreenshotCapability(Capability):
    name = "take_screenshot"
    description = "Take a screenshot of the whole screen and save it as a file"
    risk = Risk.LOW
    timeout_s = 25.0

    def __init__(self, directory: Optional[Path] = None,
                 capturer: Optional[Callable[[Path], None]] = None,
                 now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._directory = directory
        self._capturer = capturer
        self._now_fn = now_fn

    def match(self, request: RequestObject) -> dict | None:
        from eva.planner.local_quick import parse_quick_command
        m = parse_quick_command(request.clean_text)
        if m and m.capability == self.name:
            return m.args
        return None

    def execute(self, args: dict) -> CapabilityResult:
        now = self._now_fn() if self._now_fn else None
        res = take_screenshot(directory=self._directory, capturer=self._capturer, now=now)
        outcome = TaskOutcome.SUCCESS if res.outcome == "SUCCESS" else TaskOutcome.FAILED
        return CapabilityResult(outcome, res.message, {"detail": res.detail})


def register_quick_capabilities(registry: CapabilityRegistry) -> None:
    registry.register(TellTimeCapability())
    registry.register(TellDateCapability())
    registry.register(BatteryStatusCapability())
    registry.register(VolumeUpCapability())
    registry.register(VolumeDownCapability())
    registry.register(VolumeMuteCapability())
    registry.register(TakeScreenshotCapability())

