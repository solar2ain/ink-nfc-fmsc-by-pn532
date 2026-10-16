import json
from types import SimpleNamespace

from PIL import Image
import pytest

from ink_nfc_fmsc import cli
from ink_nfc_fmsc.catalog import select_screen
from ink_nfc_fmsc.device import Device
from ink_nfc_fmsc.images import prepare_image
from ink_nfc_fmsc.pn532 import Activation, RfTransportError, parse_ats
from ink_nfc_fmsc.protocol import D1, D8, DE, board_info, stored_scan_compatible


def simulated_device(monkeypatch, screen_name, *, board="huawei", mismatch=None, upload_fault=None):
    screen = select_screen(screen_name)
    devices = []
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    class Reader:
        def __init__(self, *_args, **_kwargs):
            self.rf_on = False

        def open(self):
            pass

        def activate(self, **_kwargs):
            self.rf_on = True
            return Activation(bytes.fromhex("9E56721E"), b"\x04\x00", 0x20,
                              parse_ats(bytes.fromhex("0208")), 0x7A if board == "huawei" else 0x26)

        def close(self):
            self.rf_on = False

    class Session:
        def __init__(self, reader, activation, **_kwargs):
            self.reader = reader
            self.sent = []
            self.authorized = board != "huawei"
            self.busy = False
            self.fields = {0xA0: bytes.fromhex(screen.value["expected_d1_a0"]),
                           0xA1: bytes.fromhex(screen.value["expected_d1_a1"]),
                           0xB1: b"\x02", 0xD1: b"\x01" + b"\x00" * 6,
                           0xC0: bytes.fromhex("201EA001" if board == "iphone" or screen_name == "huawei-a1" else "00000001"),
                           0xC1: activation.uid}
            if mismatch == "geometry":
                self.fields[0xA0] = b"\x00" * 7
            elif mismatch == "scan":
                self.fields[0xA1] = b"\x00" * 7
            elif mismatch == "factory_vertical":
                self.fields[0xA1] = bytes.fromhex("00120030FFFFFF")

        def transceive(self, apdu, **_kwargs):
            assert self.reader.rf_on
            self.sent.append(apdu)
            if apdu == D1:
                return b"".join(bytes((tag, len(data))) + data for tag, data in self.fields.items()) + b"\x90\x00"
            if apdu == D8:
                return b"\x90\x00" if self.authorized else b"\x69\x85"
            if apdu[:2] == b"\x00\x20":
                self.authorized = True
            if apdu[1] in (0xD2, 0xD3) and upload_fault is not None:
                if isinstance(upload_fault, Exception):
                    raise upload_fault
                return upload_fault
            if apdu[1] == 0xD4:
                self.busy = True
                return b"\x01\x90\x00"
            if apdu == DE:
                response = bytes((int(self.busy),)) + b"\x90\x00"
                self.busy = False
                return response
            return b"\x90\x00"

    def factory(settings, logger, **kwargs):
        device = Device(settings, logger, reader_factory=Reader, session_factory=Session,
                        clock=lambda: now[0], sleep=sleep, **kwargs)
        devices.append(device)
        return device

    monkeypatch.setattr(cli, "Device", factory)
    return devices


def command(tmp_path, operation="clear", screen="huawei-a0", *, stored=True, **options):
    args = [operation]
    if operation == "send":
        path = tmp_path / "picture.png"
        image = Image.new("RGB", (8, 8), "white")
        image.putpixel((0, 0), (0, 0, 0))
        image.save(path)
        args.append(str(path))
    elif operation == "send-color":
        args.append("red")
    args += ["--screen", screen, "--port", "SIM", "--yes", "--log-dir", str(tmp_path / "logs")]
    if stored:
        args.append("--use-stored-driver")
    for name, value in options.items():
        args += ["--" + name.replace("_", "-"), str(value)]
    return args


@pytest.mark.parametrize("operation", ["send", "send-test", "send-color", "clear", "refresh"])
@pytest.mark.parametrize("screen", ["huawei-a0", "huawei-a1"])
def test_explicit_screen_can_preserve_stored_driver(operation, screen):
    args = cli.parse_args([operation, *(["image.png"] if operation == "send" else
                                      ["red"] if operation == "send-color" else []),
                           "--screen", screen, "--use-stored-driver"])
    assert args.keep_driver
    assert cli.selected(args).id == select_screen(screen).id


@pytest.mark.parametrize("screen", ["huawei-a0", "huawei-a1"])
@pytest.mark.parametrize("operation", ["send", "send-test", "send-color", "clear"])
def test_actual_apdu_flow_preserves_driver_and_records_selected_format(tmp_path, monkeypatch, capsys, screen, operation):
    devices = simulated_device(monkeypatch, screen)
    args = command(tmp_path, operation, screen)
    assert cli.main(args) == 0
    dev = devices[0]
    sent = dev.session.sent
    assert not any(apdu[1] in (0xDA, 0xDB, 0xDC) for apdu in sent)
    assert sum(apdu[1] == 0xD3 for apdu in sent) >= 53
    assert sum(apdu[1] == 0xD4 for apdu in sent) == 1
    assert DE in sent and not dev.reader.rf_on
    assert dev.log.summary["use_stored_driver"] is True
    assert dev.log.summary["screen"] == select_screen(screen).name
    plan = json.loads((dev.log.details / "plan.json").read_text())
    assert plan["recipe_id"] == select_screen(screen).id
    assert all(job["config"] == [] for job in plan["jobs"])
    expected, _ = cli.prepare(cli.parse_args(args), select_screen(screen))
    assert (dev.log.details / "plane-0.bin").read_bytes() == expected[0]
    assert "将覆盖" not in capsys.readouterr().err


@pytest.mark.parametrize("operation", ["send", "send-test", "send-color", "clear"])
def test_standard_wake_board_can_use_stored_ssd_driver(tmp_path, monkeypatch, operation):
    devices = simulated_device(monkeypatch, "SSD1683-400x300-BWR", board="iphone")
    assert cli.main(command(tmp_path, operation, "SSD1683-400x300-BWR")) == 0
    assert not any(apdu[1] in (0x20, 0xDA, 0xDB) for apdu in devices[0].session.sent)


@pytest.mark.parametrize("screen", ["huawei-a0", "huawei-a1"])
def test_explicit_screen_without_flag_still_installs_driver(tmp_path, monkeypatch, screen):
    devices = simulated_device(monkeypatch, screen)
    assert cli.main(command(tmp_path, screen=screen, stored=False)) == 0
    sent = devices[0].session.sent
    assert any(apdu[1] == 0xDA for apdu in sent) and any(apdu[1] == 0xDB for apdu in sent)


@pytest.mark.parametrize("mismatch", ["geometry", "scan"])
@pytest.mark.parametrize("screen", ["huawei-a0", "huawei-a1"])
def test_mismatched_stored_configuration_stops_before_image_or_refresh(tmp_path, monkeypatch, mismatch, screen):
    devices = simulated_device(monkeypatch, screen, mismatch=mismatch)
    assert cli.main(command(tmp_path, screen=screen)) == 1
    assert not any(apdu[1] in (0xD2, 0xD3, 0xD4, 0xDA, 0xDB) for apdu in devices[0].session.sent)


@pytest.mark.parametrize("screen", ["huawei-a0", "huawei-a1"])
def test_recorded_factory_vertical_mode_is_preserved(tmp_path, monkeypatch, screen):
    devices = simulated_device(monkeypatch, screen, mismatch="factory_vertical")
    assert cli.main(command(tmp_path, screen=screen)) == 0
    assert devices[0].session.fields[0xA1] == bytes.fromhex("00120030FFFFFF")
    assert not any(apdu[1] in (0xDA, 0xDB) for apdu in devices[0].session.sent)
    assert devices[0].log.summary["factory_scan_preserved"]


@pytest.mark.parametrize("screen", ["huawei-a0", "huawei-a1"])
def test_insufficient_capacity_never_falls_back_to_config_writes(tmp_path, monkeypatch, capsys, screen):
    devices = simulated_device(monkeypatch, screen, board="iphone")
    assert cli.main(command(tmp_path, screen=screen)) == 1
    assert devices[0].session.sent == [D1]
    assert "使用板内驱动不能自动分段" in capsys.readouterr().err


@pytest.mark.parametrize("passes", ["two", "first", "second"])
def test_explicit_segments_are_rejected_before_connect(tmp_path, monkeypatch, passes):
    devices = simulated_device(monkeypatch, "huawei-a0")
    assert cli.main(command(tmp_path, passes=passes)) == 1
    assert devices == []


@pytest.mark.parametrize("fault", [b"\x68\xCA", RfTransportError(1, "RF_TIMEOUT")])
def test_upload_error_does_not_retry_or_write_driver(tmp_path, monkeypatch, fault):
    devices = simulated_device(monkeypatch, "huawei-a0", upload_fault=fault)
    assert cli.main(command(tmp_path)) == 1
    sent = devices[0].session.sent
    assert sum(apdu[1] == 0xD3 for apdu in sent) == 1
    assert not any(apdu[1] in (0xD2, 0xD4, 0xDA, 0xDB) for apdu in sent)


def test_a0_linear_format_is_available_with_explicit_screen_and_stored_driver(tmp_path, monkeypatch):
    devices = simulated_device(monkeypatch, "huawei-a0")
    args = command(tmp_path, data_format="linear")
    parsed = cli.parse_args(args)
    assert cli.native_screen(parsed).value["row_mapping"] == "linear"
    assert cli.main(args) == 0
    assert not any(apdu[1] in (0xDA, 0xDB) for apdu in devices[0].session.sent)


def test_cancelled_confirmation_does_not_open_hardware(tmp_path, monkeypatch):
    devices = simulated_device(monkeypatch, "huawei-a0")
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    args = command(tmp_path)
    args.remove("--yes")
    assert cli.main(args) == 0
    assert devices == []


def test_default_stored_inference_and_format_safety_are_unchanged():
    assert cli.parse_args(["clear"]).keep_driver
    assert not cli.parse_args(["clear", "--screen", "huawei-a0"]).keep_driver
    args = cli.parse_args(["clear", "--screen", "huawei-a1", "--use-stored-driver", "--data-format", "linear"])
    with pytest.raises(ValueError, match="only applies to Huawei A0"):
        cli.native_screen(args)


def test_uncompressed_upload_also_never_writes_driver(tmp_path, monkeypatch):
    devices = simulated_device(monkeypatch, "SSD1683-400x300-BWR")
    assert cli.main(command(tmp_path, screen="SSD1683-400x300-BWR", transport="d2")) == 0
    sent = devices[0].session.sent
    assert any(apdu[1] == 0xD2 for apdu in sent)
    assert not any(apdu[1] in (0xD3, 0xDA, 0xDB) for apdu in sent)


def test_e6_stored_driver_does_not_skip_wiring_confirmation(tmp_path, monkeypatch):
    devices = simulated_device(monkeypatch, "osptek-e6-154")
    assert cli.main(command(tmp_path, screen="osptek-e6-154")) == 1
    assert devices == []


def test_existing_slot_refresh_accepts_flag_without_image_or_driver_writes(tmp_path, monkeypatch):
    devices = simulated_device(monkeypatch, "huawei-a1")
    assert cli.main(command(tmp_path, operation="refresh", screen="huawei-a1")) == 0
    sent = devices[0].session.sent
    assert sum(apdu[1] == 0xD4 for apdu in sent) == 1
    assert not any(apdu[1] in (0xD2, 0xD3, 0xDA, 0xDB) for apdu in sent)


def factory_a1_fields():
    # Synthetic D1 retaining the recorded factory geometry and scan layout.
    raw = bytes.fromhex("A007F0C72006000228A10700120030FFFFFFB10102B20114B30100"
                        "C004201EA001C10401020304D107012000000000009000")
    return board_info(raw, bytes.fromhex("01020304"))


def test_factory_a1_metadata_is_allowed_without_guessing_the_panel():
    fields = factory_a1_fields()
    screen = select_screen("huawei-a1")
    assert stored_scan_compatible(fields, bytes.fromhex(screen.value["expected_d1_a0"]),
                                  bytes.fromhex(screen.value["expected_d1_a1"]))
    args = cli.parse_args(["clear", "--use-stored-driver"])
    with pytest.raises(ValueError, match="无法唯一确定"):
        cli.stored_screen(args, fields)


@pytest.mark.parametrize("tag,value", [
    (0xC0, "00000002"), (0xA0, "F0C72003000228"),
    (0xA1, "02120030FFFFFF"), (0xA1, "00130030FFFFFF"),
    (0xA1, "00120031FFFFFF"), (0xA1, "00120030FFFF00"),
])
def test_factory_scan_exception_never_ignores_other_configuration_bytes(tag, value):
    fields = factory_a1_fields()
    fields[tag] = bytes.fromhex(value)
    assert not stored_scan_compatible(fields, bytes.fromhex("F0C72006000228"),
                                      bytes.fromhex("01120030FFFFFF"))


def test_configuration_failure_prints_original_and_expected_bytes(tmp_path, monkeypatch):
    devices = simulated_device(monkeypatch, "huawei-a1", mismatch="scan")
    assert cli.main(command(tmp_path, screen="huawei-a1")) == 1
    text = devices[0].log.text_path.read_text(encoding="utf-8")
    assert "A1=00000000000000" in text and "期望 01120030FFFFFF" in text


def test_factory_scan_is_not_accepted_when_installing_a_new_driver(tmp_path, monkeypatch):
    devices = simulated_device(monkeypatch, "huawei-a1", mismatch="factory_vertical")
    assert cli.main(command(tmp_path, screen="huawei-a1", stored=False)) == 1
    assert not any(apdu[1] in (0xD2, 0xD3, 0xD4) for apdu in devices[0].session.sent)
