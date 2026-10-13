"""Readable terminal messages; redirected output and saved logs stay ANSI-free."""
import os
import re
import sys
from datetime import datetime

from colorama import just_fix_windows_console


LEVELS = {"info": "INFO", "success": "SUCCESS", "warning": "WARN", "error": "ERROR"}
COLORS = {"INFO": "34", "SUCCESS": "32", "WARN": "33", "ERROR": "31"}
PREFIX = re.compile(r"^(\[\d{2}:\d{2}:\d{2}\]) (\[(INFO|SUCCESS|WARN|ERROR)\])(.*)$")


def configure_output():
    # Windows pipes may default to a locale encoding that cannot represent Chinese.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


def windows_console_api():
    if os.name != "nt" or not sys.stdin.isatty():
        return None
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetStdHandle.argtypes = [wintypes.DWORD]
    api.GetStdHandle.restype = wintypes.HANDLE
    api.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetConsoleMode.restype = wintypes.BOOL
    api.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.SetConsoleMode.restype = wintypes.BOOL
    return api


def format_message(message, *, level="info", stage=None, detail=False, now=None):
    timestamp = (now or datetime.now()).strftime("%H:%M:%S")
    phase = f"[{stage}] " if stage else ""
    indent = "  " if detail else ""
    return f"[{timestamp}] [{LEVELS[level]}] {indent}{phase}{message}"


class Console:
    def __init__(self):
        just_fix_windows_console()
        self._input_mode = None

    def suspend_selection(self):
        if self._input_mode is not None:
            return
        try:
            import ctypes
            from ctypes import wintypes

            api = windows_console_api()
            if api is None:
                return
            handle = api.GetStdHandle(-10)
            mode = wintypes.DWORD()
            if not api.GetConsoleMode(handle, ctypes.byref(mode)) or not mode.value & 0x0040:
                return
            # Legacy Quick Edit can block WriteConsoleW and stop NFC polling.
            if api.SetConsoleMode(handle, (mode.value | 0x0080) & ~0x0040):
                self._input_mode = api, handle, mode.value
        except (OSError, AttributeError):
            pass

    def restore_selection(self):
        saved, self._input_mode = self._input_mode, None
        if saved is not None:
            api, handle, mode = saved
            try:
                api.SetConsoleMode(handle, mode | 0x0080)
                if not mode & 0x0080:
                    api.SetConsoleMode(handle, mode)
            except (OSError, AttributeError):
                pass

    def __call__(self, line, *, file=None, emphasis=False):
        stream = file if file is not None else sys.stdout
        match = PREFIX.match(line)
        if "NO_COLOR" not in os.environ and stream.isatty():
            if emphasis:
                line = f"\x1b[1;31m{line}\x1b[0m"
            elif match:
                timestamp, label, level, rest = match.groups()
                line = f"\x1b[90m{timestamp}\x1b[0m \x1b[{COLORS[level]}m{label}\x1b[0m{rest}"
        print(line, file=stream, flush=True)
