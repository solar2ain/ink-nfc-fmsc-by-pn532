import copy

import pytest

from ink_nfc_fmsc import cli
from ink_nfc_fmsc.catalog import Screen, driver_tags, select_screen
from ink_nfc_fmsc.device import Device
from ink_nfc_fmsc.images import prepare_image, test_pattern as pattern_image
from ink_nfc_fmsc.plans import jobs_for
from test_device import Clock, FakeReader, FakeSession
from test_plans import restore


def test_public_name_and_controller_geometry():
    screen = select_screen("HUAWEI-A1.1")
    assert screen.name == "JD79665AA-552x768-BWRY-A1.1"
    assert select_screen(screen.name.lower()).value == screen.value
    assert (screen.value["width"], screen.value["height"]) == (552, 768)
    assert screen.controller_resolution == (768, 600)
    assert screen.visible == (0, 0, 552, 768)
    assert screen.value["raw_bytes"] == 115200


@pytest.mark.parametrize("color,code", [("black", 0), ("white", 0x55), ("yellow", 0xAA), ("red", 0xFF)])
def test_solid_image_pads_only_hidden_rows_white(color, code):
    screen = select_screen("huawei-a1.1")
    planes, preview = prepare_image(screen, fill_color=color)
    assert planes == (bytes([code]) * 105984 + b"\x55" * 9216,)
    assert preview.size == (552, 768)
    job, = jobs_for(screen, planes)
    assert restore(job.packets, (115200,)) == planes
    assert job.geometry == bytes.fromhex("F0C72006000258")
    assert len({p[5] for p in job.packets}) == 58


def test_physical_rotation_and_mirror_match_a1_before_padding():
    a1, a11 = select_screen("huawei-a1"), select_screen("huawei-a1.1")
    picture = pattern_image(a1)
    old, preview = prepare_image(a1, picture, native_palette=True, rotation="90", mirror_x=True)
    new, padded_preview = prepare_image(a11, picture, native_palette=True, rotation="90", mirror_x=True)
    assert preview.tobytes() == padded_preview.tobytes()
    assert new[0] == old[0] + b"\x55" * 9216


def test_native_window_and_reference_power_are_not_experimentally_overridden():
    screen = select_screen("huawei-a1.1")
    planes, _ = prepare_image(screen, white=True)
    job, = jobs_for(screen, planes)
    tags = driver_tags(screen.flow)
    assert (0xA1, bytes.fromhex("6103000258")) in tags
    assert [v for t, v in tags if t in (0xA1, 0xA2) and v[0] == 0x83] == [bytes.fromhex("83000002FF0000025700")] * 3
    assert (0xA1, b"\x01\x3F") in tags
    assert (0xA1, bytes.fromhex("066F1F1414")) in tags
    assert "default_a1_power" not in screen.value
    assert job.raw_bytes == 115200 and job.half is None


@pytest.mark.parametrize("command", ["prepare", "send-test", "clear", "refresh"])
def test_experimental_power_flags_are_not_exposed(command):
    with pytest.raises(SystemExit):
        cli.parser().parse_args([command, "--screen", "huawei-a1.1", "--a1-power", "mtp"])


@pytest.mark.parametrize("passes,capacity", [("auto", 80000), ("two", 200000), ("first", 200000), ("second", 200000)])
def test_no_unreviewed_split_recipe(passes, capacity):
    screen = select_screen("huawei-a1.1")
    planes, _ = prepare_image(screen, white=True)
    with pytest.raises(ValueError, match="A1.1"):
        jobs_for(screen, planes, passes=passes, capacity=capacity)


@pytest.mark.parametrize("changes", [
    {"controller_height": 601}, {"controller_width": 800}, {"plane_bytes": 105984, "raw_bytes": 105984},
    {"row_mapping": "a0_interleaved"}, {"expected_d1_a0": "F0C72006000228"},
])
def test_inconsistent_geometry_and_mapping_are_rejected(changes):
    value = copy.deepcopy(select_screen("huawei-a1.1").value)
    value.update(changes)
    with pytest.raises(ValueError):
        Screen(value)


def simulated(monkeypatch, *, fault=None, original_geometry=False):
    devices = []

    class Session(FakeSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields[0xC0] = bytes.fromhex("201EA001")
            if not original_geometry:
                self.fields[0xA0] = bytes.fromhex("F0C72006000258")

        def transceive(self, apdu, **options):
            if apdu[1] == fault:
                self.sent.append(apdu)
                return b"\x68\xCA"
            return super().transceive(apdu, **options)

    def factory(settings, logger, **kwargs):
        clock = Clock()
        device = Device(settings, logger, reader_factory=FakeReader, session_factory=Session,
                        clock=clock.time, sleep=clock.sleep, **kwargs)
        devices.append(device)
        return device

    monkeypatch.setattr(cli, "Device", factory)
    return devices


@pytest.mark.parametrize("command", ["clear", "send-test", "send-color", "refresh"])
def test_cli_padded_uploads_and_read_only_stored_refresh(tmp_path, monkeypatch, command):
    devices = simulated(monkeypatch)
    args = [command, *(["red"] if command == "send-color" else []), "--screen", "huawei-a1.1",
            "--port", "SIM", "--yes", "--log-dir", str(tmp_path)]
    assert cli.main(args) == 0
    sent = devices[0].session.sent
    assert sum(p[1] == 0xD4 for p in sent) == 1
    assert not devices[0].reader.rf_on
    if command == "refresh":
        assert not any(p[1] in (0xD2, 0xD3, 0xDA, 0xDB) for p in sent)
    else:
        assert any(p[1] == 0xD3 for p in sent)
        assert devices[0].session.fields[0xA0] == bytes.fromhex("F0C72006000258")


def test_552_row_stored_driver_is_not_used_with_600_row_image(tmp_path, monkeypatch):
    devices = simulated(monkeypatch, original_geometry=True)
    assert cli.main(["clear", "--screen", "huawei-a1.1", "--use-stored-driver", "--port", "SIM",
                     "--yes", "--log-dir", str(tmp_path)]) == 1
    assert not any(p[1] in (0xD2, 0xD3, 0xDB, 0xDA, 0xD4) for p in devices[0].session.sent)


def test_low_voltage_stops_without_replaying_refresh(tmp_path, monkeypatch):
    devices = simulated(monkeypatch, fault=0xDE)
    assert cli.main(["clear", "--screen", "huawei-a1.1", "--port", "SIM", "--yes", "--log-dir", str(tmp_path)]) == 1
    assert sum(p[1] == 0xD4 for p in devices[0].session.sent) == 1
    assert not devices[0].reader.rf_on
