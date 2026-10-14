from unittest.mock import patch
from types import SimpleNamespace

import pytest

from ink_nfc_fmsc.catalog import select_screen
from ink_nfc_fmsc.cli import confirm_operation, main, parse_args, stored_screen
from ink_nfc_fmsc.console import Console
from ink_nfc_fmsc.device import Device
from ink_nfc_fmsc.protocol import D1
from test_device import Clock, FakeReader, FakeSession


@pytest.mark.parametrize("operation", ["send", "send-test", "send-color", "clear", "refresh"])
def test_yes_only_skips_the_prompt(operation, capsys):
    argv = [operation, "--screen", "huawei-a0", "--yes"]
    if operation == "send":
        argv.insert(1, "picture.png")
    elif operation == "send-color":
        argv.insert(1, "red")
    with patch("builtins.input") as prompt:
        assert confirm_operation(parse_args(argv), select_screen("huawei-a0"), Console())
        prompt.assert_not_called()
    warning = capsys.readouterr().err
    assert ("无法还原原厂驱动" in warning) == (operation != "refresh")


@pytest.mark.parametrize("answer,accepted", [("y", True), ("YES", True), ("", False), ("n", False)])
def test_interactive_confirmation_is_default(answer, accepted, capsys):
    args = parse_args(["clear", "--screen", "huawei-a0"])
    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=answer):
        assert confirm_operation(args, select_screen("huawei-a0"), Console()) is accepted
    assert "覆盖驱动板的原厂屏幕配置" in capsys.readouterr().err


def test_decline_never_connects_to_hardware():
    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n"), \
         patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["clear", "--screen", "huawei-a0", "--port", "SIM"]) == 0
        hardware.assert_not_called()


def test_keep_driver_still_requires_refresh_confirmation_without_overwrite_warning(capsys):
    args = parse_args(["clear"])
    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="y") as prompt:
        assert confirm_operation(args, select_screen("huawei-a0"), Console())
        prompt.assert_called_once()
    assert "原厂" not in capsys.readouterr().err


@pytest.mark.parametrize("operation", ["send", "send-test", "send-color", "clear", "refresh"])
def test_no_screen_preserves_driver_and_removed_switch_is_not_public(operation):
    argv = [operation] + (["image.png"] if operation == "send" else [])
    if operation == "send-color":
        argv.insert(1, "red")
    assert parse_args(argv).keep_driver
    assert parse_args(argv + ["--screen", "huawei-a0"]).keep_driver == (operation == "refresh")
    with pytest.raises(SystemExit):
        parse_args(argv + ["--keep-driver"])


def stored_device(tmp_path, screen_name):
    screen = select_screen(screen_name)
    devices = []

    class ConfiguredSession(FakeSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields[0xA0] = bytes.fromhex(screen.value["expected_d1_a0"])
            self.fields[0xA1] = bytes.fromhex(screen.value["expected_d1_a1"])

    def factory(settings, logger, **kwargs):
        clock = Clock()
        result = Device(settings, logger, reader_factory=FakeReader, session_factory=ConfiguredSession,
                        clock=clock.time, sleep=clock.sleep, **kwargs)
        devices.append(result)
        return result

    return factory, devices


@pytest.mark.parametrize("command", ["clear", "send-test"])
def test_implicit_screen_upload_does_not_write_db_da(tmp_path, command):
    factory, devices = stored_device(tmp_path, "SSD1683-400x300-BWR")
    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main([command, "--ic", "SSD1683", "--port", "SIM", "--yes", "--log-dir", str(tmp_path)]) == 0
    sent = devices[0].session.sent
    assert any(p[:2] == b"\xf0\xd3" for p in sent)
    assert any(p[:2] == b"\xf0\xd4" for p in sent)
    assert not any(p[:2] in (b"\xf0\xdb", b"\xf0\xda") for p in sent)
    assert devices[0].reader.closed


def test_implicit_ambiguous_config_stops_before_authentication_or_writes(tmp_path, capsys):
    factory, devices = stored_device(tmp_path, "huawei-a0")
    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["clear", "--port", "SIM", "--yes", "--log-dir", str(tmp_path)]) == 1
    assert devices[0].session.sent == [D1]
    assert "无法唯一确定" in capsys.readouterr().err


def test_implicit_decline_only_reads_d1(tmp_path):
    factory, devices = stored_device(tmp_path, "SSD1675-152x152-BW")
    with patch("ink_nfc_fmsc.cli.Device", factory), patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="n"):
        assert main(["clear", "--port", "SIM", "--log-dir", str(tmp_path)]) == 0
    assert devices[0].session.sent == [D1]
    assert devices[0].reader.closed


def test_implicit_noninteractive_requires_yes_and_never_writes(tmp_path):
    factory, devices = stored_device(tmp_path, "SSD1675-152x152-BW")
    with patch("ink_nfc_fmsc.cli.Device", factory), patch("sys.stdin.isatty", return_value=False):
        assert main(["clear", "--port", "SIM", "--log-dir", str(tmp_path)]) == 1
    assert devices[0].session.sent == [D1]


def test_factory_vertical_scan_and_unknown_config_fail_closed():
    fields = {0xC0: bytes.fromhex("00000001"), 0xA0: bytes.fromhex("F0C72006000228"),
              0xA1: bytes.fromhex("00120030FFFFFF")}
    with pytest.raises(ValueError, match="无法唯一确定"):
        stored_screen(parse_args(["clear"]), fields)
    with pytest.raises(ValueError, match="没有匹配型号"):
        stored_screen(parse_args(["clear"]), {**fields, 0xA0: b"\x00" * 7})
    assert stored_screen(parse_args(["clear", "--data-format", "linear"]), fields).id == "huawei_a0_jd79665"


def test_implicit_e6_requires_wiring_before_authentication(tmp_path):
    factory, devices = stored_device(tmp_path, "osptek-e6-154")
    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["clear", "--port", "SIM", "--yes", "--log-dir", str(tmp_path)]) == 1
    assert devices[0].session.sent == [D1]


def test_implicit_refresh_only_uses_existing_slot(tmp_path):
    factory, devices = stored_device(tmp_path, "SSD1675-152x152-BW")
    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["refresh", "--port", "SIM", "--yes", "--log-dir", str(tmp_path)]) == 0
    sent = devices[0].session.sent
    assert any(p[:2] == b"\xf0\xd4" for p in sent)
    assert not any(p[:2] in (b"\xf0\xdb", b"\xf0\xda", b"\xf0\xd2", b"\xf0\xd3") for p in sent)


def test_custom_profile_requires_replacement_confirmation():
    assert not parse_args(["clear", "--profile-file", "profile.json"]).keep_driver
    assert parse_args(["refresh", "--profile-file", "profile.json"]).keep_driver


@pytest.mark.parametrize("operation", ["send", "send-test", "send-color", "clear", "refresh"])
@pytest.mark.parametrize("port_count", [0, 4])
@pytest.mark.parametrize("skip_prompt", [False, True])
def test_missing_port_stops_before_confirmation(operation, port_count, skip_prompt, capsys):
    argv = [operation] + (["picture.png"] if operation == "send" else [])
    if operation == "send-color":
        argv.insert(1, "red")
    argv += ["--screen", "huawei-a0"] + (["--yes"] if skip_prompt else [])
    ports = [SimpleNamespace(device=f"COM{i}") for i in range(port_count)]
    with patch("ink_nfc_fmsc.cli.list_ports.comports", return_value=ports) as enumerate_ports, \
         patch("ink_nfc_fmsc.cli.confirm_operation") as confirmation, \
         patch("ink_nfc_fmsc.cli.prepare") as image, \
         patch("ink_nfc_fmsc.cli.RunLog") as log, patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(argv) == 1
        enumerate_ports.assert_called_once()
        confirmation.assert_not_called()
        image.assert_not_called()
        log.assert_not_called()
        hardware.assert_not_called()
    error = capsys.readouterr().err
    assert f"found {port_count} ports" in error
    assert "风险确认" not in error


def test_single_auto_port_is_checked_once_before_confirmation():
    with patch("ink_nfc_fmsc.cli.list_ports.comports", return_value=[SimpleNamespace(device="com18")]) as ports, \
         patch("ink_nfc_fmsc.cli.confirm_operation", return_value=False) as confirmation, \
         patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["clear", "--screen", "huawei-a0"]) == 0
        ports.assert_called_once()
        confirmation.assert_called_once()
        hardware.assert_not_called()


def test_invalid_serial_settings_stop_before_confirmation():
    with patch("ink_nfc_fmsc.cli.confirm_operation") as confirmation, \
         patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["clear", "--screen", "huawei-a0", "--port", "SIM", "--precharge-ms", "-1"]) == 1
        confirmation.assert_not_called()
        hardware.assert_not_called()
