import json
from unittest.mock import patch

import pytest

from ink_nfc_fmsc.cli import main, parse_args, parser


@pytest.mark.parametrize("name", ["send", "flash", "\u4f20\u56fe\u5237\u65b0"])
def test_send_command_and_hidden_compatible_aliases(name):
    args = parse_args([name, "picture.png", "--screen", "huawei-a0", "--port", "COM18", "--yes"])
    assert args.command == "send" and args.image.name == "picture.png"
    assert args.de_hz == 9 and args.passes == "auto"


def test_help_labels_send_clear_and_keeps_refresh():
    help_text = parser().format_help()
    assert "send" in help_text and "\u4f20\u56fe\u5237\u65b0" in help_text
    assert "refresh" in help_text and "\u91cd\u5237" in help_text
    assert "flash" not in help_text
    assert "send-test" in help_text


@pytest.mark.parametrize("color", ["BW", "BwR", "BWRY", "GrAy4", "E6"])
def test_color_values_are_case_insensitive(color):
    args = parse_args(["send-test", "--ic", "jd79668aa", "--resolution", "400X300", "--color", color])
    assert args.color == color.casefold()
    assert args.resolution == (400, 300)
    assert args.keep_driver


def test_combined_screen_filters_accept_mixed_case(capsys):
    assert main(["screens", "--ic", "jD79668aA", "--resolution", "400X300", "--color", "BwRy", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in rows] == ["JD79668AA-400x300-BWRY"]


@pytest.mark.parametrize("flags,expected", [([], 5), (["--de-timeout", "3"], 3)])
def test_de_query_deadline_is_independent_of_total_refresh_deadline(flags, expected):
    from ink_nfc_fmsc.cli import serial_settings
    args = parse_args(["refresh", "--port", "SIM", "--timeout", "150", *flags])
    assert serial_settings(args).de_timeout == expected
    assert args.timeout == 150


@pytest.mark.parametrize("alias,board,passes", [("SSD1683-400x300-BWR", "huawei", 1), ("huawei-a1", "iphone", 2)])
def test_send_test_generates_preview_and_uses_normal_upload(tmp_path, alias, board, passes):
    from PIL import Image
    from ink_nfc_fmsc.device import Device
    from test_device import Clock, FakeReader, FakeSession

    devices = []

    def factory(settings, logger, **kwargs):
        clock = Clock()
        device = Device(settings, logger, reader_factory=FakeReader, session_factory=FakeSession,
                        clock=clock.time, sleep=clock.sleep, **kwargs)
        devices.append(device)
        return device

    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["send-test", "--screen", alias, "--board", board, "--port", "SIM",
                     "--log-dir", str(tmp_path), "--yes"]) == 0
    sent = devices[0].session.sent
    assert any(p[1] == 0xD3 for p in sent)
    assert sum(p[1] == 0xD4 for p in sent) == passes
    with Image.open(devices[0].log.directory / "preview.png") as preview:
        assert len(preview.getcolors()) >= 3
    assert devices[0].reader.closed


def test_send_test_retains_e6_wiring_guard():
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["send-test", "--screen", "osptek-e6-154", "--port", "SIM", "--yes"]) == 1
        hardware.assert_not_called()


@pytest.mark.parametrize("alias", ["SSD1619-128x250-BWR", "huawei-a0", "huawei-a1", "SSD1683-400x300-BWR", "osptek-e6-154"])
def test_send_test_defaults_to_landscape_without_changing_native_geometry(alias):
    from PIL import Image
    from ink_nfc_fmsc.catalog import select_screen
    from ink_nfc_fmsc.cli import prepare
    from ink_nfc_fmsc.images import test_pattern as make_pattern

    screen = select_screen(alias)
    source = make_pattern(screen)
    _, _, width, height = screen.visible
    assert source.width >= source.height
    planes, preview = prepare(parse_args(["send-test", "--screen", alias]), screen)
    expected = source.transpose(Image.Transpose.ROTATE_270) if height > width else source
    assert preview.size == (screen.value["width"], screen.value["height"])
    assert preview.tobytes() == expected.tobytes()
    assert sum(map(len, planes)) == screen.value["raw_bytes"]


def test_send_and_refresh_route_to_distinct_operations(tmp_path):
    from PIL import Image
    from ink_nfc_fmsc.device import Device
    from test_device import Clock, FakeReader, FakeSession
    from ink_nfc_fmsc.protocol import D1, D8, DE
    image = tmp_path / "picture.png"
    Image.new("RGB", (16, 16), "white").save(image)
    instances = []
    clock = Clock()

    def factory(settings, logger, **kwargs):
        device = Device(settings, logger, reader_factory=FakeReader, session_factory=FakeSession,
                        clock=clock.time, sleep=clock.sleep, **kwargs)
        instances.append(device)
        return device

    common = ["--screen", "huawei-a0", "--board", "huawei", "--port", "SIM", "--log-dir", str(tmp_path / "logs"), "--yes"]
    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["send", str(image), *common]) == 0
        assert main(["refresh", *common]) == 0
    assert any(p[1] == 0xD3 for p in instances[0].session.sent)
    assert any(p[1] == 0xD4 for p in instances[0].session.sent)
    assert not any(p[1] in (0xDB, 0xDA, 0xD2, 0xD3) for p in instances[1].session.sent)
    assert any(p[1] == 0xD4 for p in instances[1].session.sent)


def test_default_de_is_9hz_and_product_interface_has_no_blind_debug_commands():
    args = parse_args(["clear", "--screen", "huawei-a0", "--port", "COM18", "--yes"])
    assert args.de_hz == 9 and args.reader == args.board == "auto"
    assert args.transport == "d3" and args.compression == "lzo"
    assert args.packet_delay_ms == args.pre_refresh_ms == 0
    with pytest.raises(SystemExit):
        parser().parse_args(["blind-scan"])


@pytest.mark.parametrize("operation", ["send", "send-test", "clear", "prepare"])
def test_compressed_default_and_explicit_uncompressed_option(operation):
    argv = [operation, "--screen", "SSD1683-400x300-BWR"]
    if operation == "send":
        argv.insert(1, "picture.png")
    elif operation == "prepare":
        argv += ["--white", "--output", "output/preview"]
    args = parse_args(argv)
    assert args.transport == "d3" and args.compression == "lzo"
    assert parse_args([*argv, "--no-compress"]).transport == "d2"
    assert parse_args([*argv, "--transport", "d2"]).transport == "d2"
    assert parse_args([*argv, "--transport", "auto"]).transport == "auto"
    with pytest.raises(SystemExit):
        parse_args([*argv, "--no-compress", "--transport", "d3"])
    with pytest.raises(SystemExit):
        parse_args([*argv, "--transport", "d3", "--no-compress"])


def test_prepare_default_compresses_ssd1683_and_no_compress_preserves_bytes(tmp_path):
    from test_plans import restore

    outputs = {}
    common = ["prepare", "--white", "--screen", "SSD1683-400x300-BWR"]
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        for name, flags in (("compressed", []), ("raw", ["--no-compress"])):
            path = tmp_path / name
            assert main([*common, "--output", str(path), *flags]) == 0
            outputs[name] = json.loads((path / "plan.json").read_text())["jobs"][0]
        hardware.assert_not_called()
    compressed, raw = outputs["compressed"], outputs["raw"]
    packed = [bytes.fromhex(p) for p in compressed["image_packets"]]
    uncompressed = [bytes.fromhex(p) for p in raw["image_packets"]]
    assert all(p[1] == 0xD3 for p in packed)
    assert all(p[1] == 0xD2 for p in uncompressed)
    assert len(packed) == 16 and len(uncompressed) == 120
    planes = tuple(b"".join(p[5:] for p in uncompressed if p[2] == slot) for slot in (0, 1))
    assert restore(packed, (15000, 15000)) == planes
    assert compressed["config"] == raw["config"] and compressed["d4"] == raw["d4"]


def test_catalog_json_and_help(capsys):
    assert main(["screens", "--ic", "SSD1683", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 2
    assert {row["id"] for row in data} == {"SSD1683-400x300-BW", "SSD1683-400x300-BWR"}
    assert all(row["label"] == row["id"] for row in data)
    assert main(["algorithms", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["algorithms"]) == 16


def test_screen_listing_displays_public_names_and_filters_case_insensitively(capsys):
    assert main(["screens", "--ic", "SSD1683"]) == 0
    output = capsys.readouterr().out
    assert "SSD1683-400x300-BWR" in output and "ZKC42VM" not in output
    assert "reference" not in output.lower() and "ssd1683-bwr" not in output.lower()
    assert main(["screens", "--screen", "jd79665aa-552X768-bwry-a0", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["id"] == "JD79665AA-552x768-BWRY-A0"


def test_screen_table_has_headers_aliases_and_blank_cells(capsys):
    from ink_nfc_fmsc.catalog import select_screen
    assert main(["screens"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [title.strip() for title in lines[0].split("|")] == ["屏幕", "别名", "状态"]
    rows = {cells[0].strip(): [cell.strip() for cell in cells[1:]]
            for line in lines[2:-1] if len(cells := line.split("|")) == 3}
    assert len(rows) == 45
    assert rows["SSD1680-128x250-BW"] == ["", "可用"]
    assert rows["SSD1683-400x300-BWR"] == ["", "可用"]
    assert rows["JD79665AA-552x768-BWRY-A0"] == ["huawei-a0", "可用"]
    assert rows["JD79665AA-552x768-BWRY-A1"] == ["huawei-a1", "可用"]
    assert rows["IST7513-240x240-E6"][0] == "osptek-e6-154"
    assert rows["IST7601-400x400-E6"][0] == "osptek-e6-169"
    assert rows["JD7601-IST7601-480x720-E6"][0] == "osptek-e6-370"
    public_aliases = {"huawei-a0", "huawei-a1", "osptek-e6-154", "osptek-e6-169", "osptek-e6-370"}
    assert {alias for aliases, _ in rows.values() for alias in aliases.split(", ") if alias} == public_aliases
    assert all(status == "可用" for _, status in rows.values())
    for name, (aliases, _) in rows.items():
        for alias in aliases.split(", ") if aliases else ():
            assert select_screen(alias.upper()).name == name
    assert main(["screens", "--ic", "UC8159"]) == 0
    empty = capsys.readouterr().out
    assert "屏幕" in empty and "别名" in empty and "共 0 个型号" in empty


@pytest.mark.parametrize("flags", [["--json"], ["--json", "--details"]])
def test_json_catalog_includes_aliases_and_empty_lists(capsys, flags):
    assert main(["screens", *flags]) == 0
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["SSD1680-128x250-BW"]["aliases"] == []
    assert rows["JD79665AA-552x768-BWRY-A1"]["aliases"] == ["huawei-a1"]
    assert rows["SSD1683-400x300-BWR"]["aliases"] == []
    assert rows["SSD1683-400x300-BW"]["aliases"] == []
    assert rows["JD79668AA-400x300-BWRY"]["aliases"] == []
    assert {alias for row in rows.values() for alias in row["aliases"]} == {
        "huawei-a0", "huawei-a1", "osptek-e6-154", "osptek-e6-169", "osptek-e6-370"}


@pytest.mark.parametrize("flags", [[], ["--json"], ["--json", "--details"]])
def test_screen_lists_hide_unsupported_recipes_in_every_output_format(capsys, flags):
    from ink_nfc_fmsc.catalog import screens
    allowed = {s.name for s in screens() if s.supported}
    hidden = {s.name for s in screens() if not s.supported}
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["screens", *flags]) == 0
        hardware.assert_not_called()
    output = capsys.readouterr().out
    assert len(allowed) == 45 and "暂不可用" not in output
    assert not any(name in output for name in hidden)
    if "--json" in flags:
        rows = json.loads(output)
        assert {row["id"] for row in rows} == allowed and all(row["supported"] for row in rows)
    else:
        assert all(name in output for name in allowed) and "共 45 个型号" in output


def test_unsupported_screen_filters_are_empty_and_explicit_selection_keeps_reason(capsys):
    from ink_nfc_fmsc.catalog import select_screen
    assert main(["screens", "--ic", "UC8159", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    screen = select_screen("UC8159-640x384-BW")
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["screens", "--screen", screen.name, "--json"]) == 1
        assert main(["clear", "--screen", screen.name, "--port", "SIM", "--yes"]) == 1
        hardware.assert_not_called()
    output = capsys.readouterr()
    assert screen.value["unsupported_reason"] in output.err and not output.out


def test_device_suffix_is_rejected_before_hardware_and_baseline_export_is_whole(tmp_path, capsys):
    from ink_nfc_fmsc.catalog import select_screen
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["clear", "--screen", "SSD1680-128x250-BW-ZKC21V", "--port", "SIM", "--yes"]) == 1
        hardware.assert_not_called()
    capsys.readouterr()
    assert main(["screens", "--screen", "JD79668AA-400x300-BWRY", "--json", "--details"]) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["recipe_id"] == "hanshow_jd79668_400x300_drf"
    path = tmp_path / "screen.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    exported = select_screen(profile_file=path)
    assert exported.flow == select_screen("JD79668AA-400x300-BWRY").flow and exported.value["reverse_rows"]


def test_prepare_public_name_has_user_facing_name_in_plan(tmp_path):
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["prepare", "--white", "--screen", "ssd1683-400X300-bwr", "--output", str(tmp_path)]) == 0
        hardware.assert_not_called()
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["screen"] == "SSD1683-400x300-BWR"
    assert plan["recipe_id"] == "panel_generic_33"


@pytest.mark.parametrize("variant", ["A0", "A1"])
def test_detailed_export_round_trip_preserves_native_half_recipe(tmp_path, capsys, variant):
    from ink_nfc_fmsc.catalog import select_screen
    from ink_nfc_fmsc.images import prepare_image
    from ink_nfc_fmsc.plans import jobs_for
    name = f"JD79665AA-552x768-BWRY-{variant}"
    assert main(["screens", "--screen", name, "--json", "--details"]) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["id"] == name and row["recipe_id"] == f"huawei_{variant.lower()}_jd79665"
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    restored = select_screen(profile_file=path)
    original = select_screen(name)
    assert restored.id == original.id and restored.name == name and restored.flow == original.flow
    planes, _ = prepare_image(restored, white=True)
    assert jobs_for(restored, planes, capacity=80000) == jobs_for(original, planes, capacity=80000)


def test_retired_panel_name_does_not_open_hardware():
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["clear", "--screen", "panel_generic_33", "--port", "SIM", "--yes"]) == 1
        hardware.assert_not_called()


@pytest.mark.parametrize("alias", ["a0", "A1", "SSD1683-BWR", "SSD1683-BW", "e6-154", "e6-169", "e6-370",
    "pricetag154", "hema213", "hanshow213", "hanshow420", "hanshow420-drf", "he037-bwry"])
def test_retired_short_alias_never_opens_hardware(alias):
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["clear", "--screen", alias, "--port", "SIM", "--yes"]) == 1
        hardware.assert_not_called()


@pytest.mark.parametrize("command", ["probe", "clear", "refresh", "send"])
@pytest.mark.parametrize("port", ["com18", "Com18", "COM18"])
def test_com_port_case_is_normalized_before_connecting(command, port):
    from ink_nfc_fmsc.cli import serial_settings
    argv = [command, "--port", port]
    if command == "send":
        argv.insert(1, "picture.png")
    assert serial_settings(parse_args(argv)).port == "COM18"


@pytest.mark.parametrize("port", ["/dev/ttyUSB0", "/dev/cu.usbserial-AbC", "SIM"])
def test_non_com_port_paths_preserve_case(port):
    from ink_nfc_fmsc.cli import serial_settings
    assert serial_settings(parse_args(["probe", "--port", port])).port == port


def test_prepare_two_pass_writes_artifacts_without_serial(tmp_path, capsys):
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["prepare", "--white", "--screen", "huawei-a0", "--board", "iphone", "--output", str(tmp_path)]) == 0
        hardware.assert_not_called()
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert [j["raw_bytes"] for j in plan["jobs"]] == [52992, 52992]
    assert (tmp_path / "plane-0.bin").stat().st_size == 105984
    assert (tmp_path / "preview.png").exists()


@pytest.mark.parametrize("argv", [
    ["clear", "--screen", "huawei-a0"],
    ["clear", "--screen", "osptek-e6-154", "--yes"],
    ["clear", "--screen", "uc8151_213_bwr", "--resolution", "400x300", "--yes"],
])
def test_missing_confirmation_or_invalid_recipe_never_opens_port(argv):
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(argv) == 1
        hardware.assert_not_called()


def test_prepare_iphone_e6_370_rejects_capacity_without_hardware(tmp_path):
    with patch("ink_nfc_fmsc.cli.Device") as hardware:
        assert main(["prepare", "--white", "--screen", "osptek-e6-370", "--board", "iphone", "--output", str(tmp_path)]) == 1
        hardware.assert_not_called()


def test_read_only_probe_is_only_identity_and_d8(tmp_path, capsys):
    from ink_nfc_fmsc.device import Device
    from test_device import Clock, FakeReader, FakeSession
    instances = []
    clock = Clock()

    def factory(settings, logger, **kwargs):
        device = Device(settings, logger, reader_factory=FakeReader, session_factory=FakeSession,
                        clock=clock.time, sleep=clock.sleep, **kwargs)
        instances.append(device)
        return device

    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(["probe", "--port", "SIM", "--log-dir", str(tmp_path), "--json"]) == 0
    from ink_nfc_fmsc.protocol import D1, D8
    assert instances[0].session.sent == [D1, D8]
    result = json.loads(capsys.readouterr().out)
    assert result["d8_sw"] == "6985" and result["capacity_measured"] is False
    assert result["board_name"] == "华为Nova墨水屏手机壳NFC驱动板"
