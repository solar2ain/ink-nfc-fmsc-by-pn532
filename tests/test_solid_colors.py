from unittest.mock import patch

import pytest
from PIL import Image

from ink_nfc_fmsc.catalog import screens, select_screen
from ink_nfc_fmsc.cli import main, parse_args, prepare
from ink_nfc_fmsc.device import Device
from ink_nfc_fmsc.images import SOLID_COLORS, prepare_image
from ink_nfc_fmsc.protocol import D1
from test_confirmation import stored_device
from test_device import Clock, FakeReader, FakeSession
from test_plans import restore


@pytest.mark.parametrize("name,color,codes", [
    ("SSD1675-152x152-BW", "black", (0x00,)),
    ("SSD1675-152x152-BW", "white", (0xFF,)),
    ("SSD1683-400x300-BWR", "black", (0x00, 0x00)),
    ("SSD1683-400x300-BWR", "white", (0xFF, 0x00)),
    ("SSD1683-400x300-BWR", "red", (0x00, 0xFF)),
    *[(name, color, (code,)) for name in ("huawei-a0", "huawei-a1", "JD79668AA-400x300-BWRY")
      for color, code in (("black", 0x00), ("white", 0x55), ("yellow", 0xAA), ("red", 0xFF))],
    *[("osptek-e6-154", color, (code,)) for color, code in
      (("black", 0x00), ("white", 0x11), ("red", 0x33), ("yellow", 0x22), ("green", 0x66), ("blue", 0x55))],
    *[("SSD1677-800x480-GRAY4", color, codes) for color, codes in
      (("black", (0xFF, 0xFF)), ("dark-gray", (0x00, 0xFF)),
       ("light-gray", (0xFF, 0x00)), ("white", (0x00, 0x00)))],
])
def test_solid_color_uses_exact_native_codes_without_image_processing(name, color, codes):
    screen = select_screen(name)
    with patch("ink_nfc_fmsc.images.compose_image") as compose, patch("ink_nfc_fmsc.images.process_frame") as process:
        planes, preview = prepare_image(screen, fill_color=color.upper())
        compose.assert_not_called()
        process.assert_not_called()
    assert planes == tuple(bytes([code]) * screen.value["plane_bytes"] for code in codes)
    assert preview.size == (screen.value["width"], screen.value["height"])
    assert preview.getcolors() == [(preview.width * preview.height, SOLID_COLORS[color])]


@pytest.mark.parametrize("screen", [s for s in screens() if s.supported], ids=lambda s: s.id)
def test_solid_white_is_identical_to_clear_for_every_supported_recipe(screen):
    clear_planes, clear_preview = prepare_image(screen, white=True)
    planes, preview = prepare_image(screen, fill_color="white")
    assert planes == clear_planes and preview.tobytes() == clear_preview.tobytes()


def test_conflicting_white_and_fill_color_are_rejected():
    with pytest.raises(ValueError, match="not both"):
        prepare_image(select_screen("huawei-a0"), white=True, fill_color="red")


def test_cli_color_name_is_case_insensitive_and_does_not_replace_screen_color_filter():
    args = parse_args(["send-color", "ReD", "--color", "BwR", "--ic", "SSD1683"])
    assert args.fill_color == "red" and args.color == "bwr" and args.keep_driver
    assert args.transport == "d3" and args.de_hz == 9
    planes, preview = prepare(args, select_screen("SSD1683-400x300-BWR"))
    assert len(planes) == 2 and preview.getpixel((0, 0)) == SOLID_COLORS["red"]
    with pytest.raises(SystemExit):
        parse_args(["send-color"])
    with pytest.raises(SystemExit):
        parse_args(["send-color", "purple"])


@pytest.mark.parametrize("name,color", [("SSD1675-152x152-BW", "red"),
    ("SSD1683-400x300-BWR", "yellow"), ("huawei-a0", "blue"), ("osptek-e6-154", "dark-gray")])
def test_unsupported_color_stops_before_confirmation_or_hardware(name, color, capsys):
    with patch("ink_nfc_fmsc.cli.confirm_operation") as confirm, patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["send-color", color, "--screen", name, "--port", "SIM", "--confirm-e6-wiring"]) == 1
        confirm.assert_not_called()
        hardware.assert_not_called()
    assert "不支持单色" in capsys.readouterr().err


@pytest.mark.parametrize("name,color,board,passes", [
    ("SSD1683-400x300-BWR", "red", "huawei", 1),
    ("huawei-a1", "yellow", "iphone", 2),
    ("osptek-e6-154", "green", "huawei", 1),
])
def test_explicit_solid_refresh_uses_normal_upload_and_capacity_rules(tmp_path, name, color, board, passes):
    devices = []

    def factory(settings, logger, **kwargs):
        clock = Clock()
        device = Device(settings, logger, reader_factory=FakeReader, session_factory=FakeSession,
                        clock=clock.time, sleep=clock.sleep, **kwargs)
        devices.append(device)
        return device

    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["send-color", color, "--screen", name, "--board", board, "--port", "SIM",
                     "--confirm-e6-wiring", "--log-dir", str(tmp_path), "--yes"]) == 0
    device = devices[0]
    sent = device.session.sent
    assert any(p[1] == 0xD3 for p in sent)
    assert any(p[1] == 0xDB for p in sent) and any(p[1] == 0xDA for p in sent)
    assert sum(p[1] == 0xD4 for p in sent) == passes
    assert device.log.summary["fill_color"] == color
    with Image.open(device.log.directory / "preview.png") as preview:
        assert preview.getcolors() == [(preview.width * preview.height, SOLID_COLORS[color])]
    assert device.reader.closed


@pytest.mark.parametrize("uncompressed", [False, True])
def test_stored_driver_solid_refresh_preserves_configuration_and_exact_payload(tmp_path, uncompressed):
    name = "SSD1683-400x300-BWR"
    factory, devices = stored_device(tmp_path, name)
    flags = ["--no-compress"] if uncompressed else []
    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["send-color", "red", "--ic", "SSD1683", "--port", "SIM", "--yes",
                     "--log-dir", str(tmp_path), *flags]) == 0
    device = devices[0]
    sent = device.session.sent
    assert not any(p[1] in (0xDB, 0xDA) for p in sent)
    assert sum(p[1] == 0xD4 for p in sent) == 1
    expected, _ = prepare_image(select_screen(name), fill_color="red")
    if uncompressed:
        packets = [p for p in sent if p[1] == 0xD2]
        actual = tuple(b"".join(p[5:] for p in packets if p[2] == slot) for slot in (0, 1))
    else:
        actual = restore([p for p in sent if p[1] == 0xD3], tuple(map(len, expected)))
    assert actual == expected and device.reader.closed


def test_stored_driver_unsupported_color_only_reads_identity(tmp_path):
    factory, devices = stored_device(tmp_path, "SSD1683-400x300-BWR")
    with patch("ink_nfc_fmsc.cli.Device", factory), patch("ink_nfc_fmsc.cli.confirm_operation") as confirm:
        assert main(["send-color", "green", "--ic", "SSD1683", "--port", "SIM", "--yes",
                     "--log-dir", str(tmp_path)]) == 1
        confirm.assert_not_called()
    assert devices[0].session.sent == [D1] and devices[0].reader.closed


def test_solid_refresh_requires_confirmation_and_preserves_e6_wiring_guard():
    with patch("ink_nfc_fmsc.cli.Device") as hardware, patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="n"):
        assert main(["send-color", "red", "--screen", "huawei-a0", "--port", "SIM"]) == 0
        assert main(["send-color", "blue", "--screen", "osptek-e6-154", "--port", "SIM", "--yes"]) == 1
        hardware.assert_not_called()
