"""The only place that touches Windows: start things, read the app list, look at windows.

Capabilities talk to a "probe", so tests can use a fake one and nothing real is opened.
All Windows-only code is inside functions, so this file also imports on other systems.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import NamedTuple

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


class WindowInfo(NamedTuple):
    hwnd: int
    title: str
    cls: str        # window class, e.g. "CabinetWClass" = a File Explorer window
    pid: int        # id of the program that owns the window
    exe: str        # file name of that program, e.g. "notepad.exe" ("" if Windows does not tell us)


def parse_start_apps_json(text: str) -> list[dict]:
    """PowerShell prints ONE object (not a list) when there is a single app. Handle both."""
    text = (text or "").lstrip("\ufeff").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)]


class WindowsProbe:
    # ---- starting things ---------------------------------------------------------
    def launch(self, argv: list[str]) -> None:
        """Start a program and do not wait for it. argv is built by EVA's code, never from speech."""
        flags = (_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)

    def launch_app(self, app_id: str) -> None:
        """Open a Start-menu app (classic or Store) by the id Windows gave us."""
        self.launch(["explorer.exe", "shell:AppsFolder\\" + app_id])

    def open_path(self, path: str) -> None:
        """Open a file or folder with the program Windows chooses for it. The path always comes from
        EVA's own file list (and is re-checked), never straight from speech."""
        import os
        if not hasattr(os, "startfile"):
            raise OSError("Opening files needs Windows")
        os.startfile(path)  # type: ignore[attr-defined]

    # ---- the app list ------------------------------------------------------------
    def list_start_apps(self) -> list[dict]:
        script = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                  "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
        flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=30, check=True, creationflags=flags).stdout
        return parse_start_apps_json(out)

    # ---- looking at windows ------------------------------------------------------
    def _scan(self, with_program: bool) -> list[WindowInfo]:
        """Real, visible, titled top-level windows. with_program=True also finds each window's program."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        dwm = ctypes.WinDLL("dwmapi")
        kernel32 = ctypes.WinDLL("kernel32")
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        found: list[WindowInfo] = []
        exe_of: dict[int, str] = {}

        def program_name(pid: int) -> str:
            if pid in exe_of:
                return exe_of[pid]
            name = ""
            handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
                kernel32.CloseHandle(handle)
            exe_of[pid] = name
            return name

        def callback(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                if user32.GetWindowLongW(hwnd, -20) & 0x80:      # tool window (tray popups etc.)
                    return True
                cloaked = ctypes.c_int(0)                         # hidden Store-app frames
                if (dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked),
                                              ctypes.sizeof(cloaked)) == 0 and cloaked.value):
                    return True
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                pid, exe = 0, ""
                if with_program:
                    pid_value = wintypes.DWORD(0)
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
                    pid = int(pid_value.value)
                    exe = program_name(pid)
                found.append(WindowInfo(int(hwnd), title.value, cls.value, pid, exe))
            except Exception:
                pass            # one odd window must never stop the scan
            return True

        user32.EnumWindows(enum_proc(callback), 0)
        return found

    def visible_windows(self) -> dict[int, tuple[str, str]]:
        """{window handle: (title, window class)} for real, visible, titled top-level windows."""
        return {w.hwnd: (w.title, w.cls) for w in self._scan(with_program=False)}

    def list_windows(self) -> list[WindowInfo]:
        """Like visible_windows, but also says which program owns each window."""
        return self._scan(with_program=True)

    def foreground_window(self) -> tuple[int, str]:
        """(handle, title) of the window the user is looking at."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0, ""
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        return int(hwnd), title.value

    # ---- closing -----------------------------------------------------------------
    def close_window(self, hwnd: int) -> None:
        """Politely ask a window to close (same as clicking its X). The app may ask "Save changes?".
        This NEVER kills a program."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        if not user32.PostMessageW(hwnd, 0x0010, 0, 0):          # WM_CLOSE
            raise OSError(f"Could not send the close request to window {hwnd}")

    def is_process_running(self, pid: int) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD(0)
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
