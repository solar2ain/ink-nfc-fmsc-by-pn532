import io
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from datetime import datetime
from unittest.mock import patch

import pytest

from ink_nfc_fmsc.console import Console, format_message
from ink_nfc_fmsc.device import RunLog


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_timestamp_level_stage_and_detail_hierarchy():
    line = format_message("D4=9000", level="success", stage="刷新", detail=True,
                          now=datetime(2026, 10, 13, 18, 2, 3))
    assert line == "[18:02:03] [SUCCESS]   [刷新] D4=9000"
    assert "[INFO] [连接]" in format_message("连接 COM18", stage="连接")


@pytest.mark.parametrize("level,code", [("info", "34"), ("success", "32"),
                                      ("warning", "33"), ("error", "31")])
def test_terminal_colors_and_preserved_status_code(monkeypatch, level, code):
    monkeypatch.delenv("NO_COLOR", raising=False)
    console = Console()
    stream = Terminal()
    console(format_message("SW=68CA", level=level, stage="刷新"), file=stream)
    assert f"\x1b[{code}m" in stream.getvalue()
    assert "SW=68CA" in stream.getvalue() and "[刷新]" in stream.getvalue()


def test_redirection_and_no_color_use_plain_text(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    console = Console()
    line = format_message("RF=01 (RF_TIMEOUT)", level="error")
    stream = io.StringIO()
    console(line, file=stream)
    assert stream.getvalue() == line + "\n"
    monkeypatch.setenv("NO_COLOR", "1")
    terminal = Terminal()
    console(line, file=terminal)
    assert terminal.getvalue() == line + "\n"


def test_saved_log_keeps_levels_without_ansi(tmp_path):
    log = RunLog(tmp_path)
    line = log.message("DE=01，SW=9000", level="info", stage="刷新", detail=True)
    assert log.text_path.read_text(encoding="utf-8") == line + "\n"
    assert "[INFO]   [刷新]" in line and "\x1b" not in line


def test_risk_emphasis_uses_red_and_respects_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    console = Console()
    terminal = Terminal()
    console("覆盖原厂配置，无法还原", file=terminal, emphasis=True)
    assert terminal.getvalue().startswith("\x1b[1;31m")
    monkeypatch.setenv("NO_COLOR", "1")
    terminal = Terminal()
    console("覆盖原厂配置，无法还原", file=terminal, emphasis=True)
    assert "\x1b" not in terminal.getvalue()


class ConsoleApi:
    def __init__(self, mode=0x01F7, readable=True, writable=True):
        self.mode = mode
        self.readable = readable
        self.writable = writable
        self.changes = []

    def GetStdHandle(self, identifier):
        assert identifier == -10
        return 123

    def GetConsoleMode(self, handle, pointer):
        assert handle == 123
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = self.mode
        return self.readable

    def SetConsoleMode(self, handle, mode):
        assert handle == 123
        self.changes.append(mode)
        if self.writable:
            self.mode = mode
        return self.writable


@pytest.mark.parametrize("original", [0x01F7, 0x0047])
def test_quick_edit_is_temporarily_disabled_and_restored(monkeypatch, original):
    api = ConsoleApi(mode=original)
    monkeypatch.setattr("ink_nfc_fmsc.console.windows_console_api", lambda: api)
    console = Console()
    console.suspend_selection()
    assert api.mode == (original | 0x0080) & ~0x0040
    assert api.mode & 1  # Ctrl+C processing remains enabled.
    console.suspend_selection()
    assert len(api.changes) == 1
    console.restore_selection()
    assert api.mode == original
    before = list(api.changes)
    console.restore_selection()
    assert api.changes == before


@pytest.mark.parametrize("api", [None, ConsoleApi(readable=False), ConsoleApi(writable=False), ConsoleApi(mode=0x0187)])
def test_unavailable_or_disabled_quick_edit_is_safe(monkeypatch, api):
    monkeypatch.setattr("ink_nfc_fmsc.console.windows_console_api", lambda: api)
    console = Console()
    console.suspend_selection()
    assert console._input_mode is None
    console.restore_selection()


@pytest.mark.parametrize("outcome", ["cancel", "error", "interrupt"])
def test_cli_restores_console_mode_on_all_exit_paths(monkeypatch, outcome):
    from ink_nfc_fmsc.cli import main

    api = ConsoleApi()
    original = api.mode
    monkeypatch.setattr("ink_nfc_fmsc.console.windows_console_api", lambda: api)
    if outcome == "error":
        argv = ["clear", "--screen", "huawei-a0", "--port", "SIM", "--precharge-ms", "-1"]
        with patch("ink_nfc_fmsc.cli.Device") as hardware:
            assert main(argv) == 1
            hardware.assert_not_called()
    else:
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n", side_effect=KeyboardInterrupt() if outcome == "interrupt" else None), \
             patch("ink_nfc_fmsc.cli.Device") as hardware:
            assert main(["clear", "--screen", "huawei-a0", "--port", "SIM"]) == (130 if outcome == "interrupt" else 0)
            hardware.assert_not_called()
    assert api.mode == original


def test_redirected_input_does_not_load_console_api(monkeypatch):
    from ink_nfc_fmsc.console import windows_console_api

    monkeypatch.setattr("sys.stdin", io.StringIO())
    assert windows_console_api() is None


@pytest.mark.parametrize("arguments,expected", [
    (["screens", "--ic", "JD"], 0),
    (["--help"], 0),
    (["clear", "--screen", "unregistered-panel", "--port", "SIM", "--yes"], 1),
])
def test_cli_uses_utf8_in_non_chinese_redirected_environment(arguments, expected):
    environment = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run([sys.executable, "-m", "ink_nfc_fmsc", *arguments],
                            env=environment, capture_output=True, encoding="utf-8", timeout=15)
    assert result.returncode == expected
    assert "codec can't encode" not in result.stderr
    assert "屏幕" in result.stdout or "传图" in result.stdout or "未完成" in result.stderr
