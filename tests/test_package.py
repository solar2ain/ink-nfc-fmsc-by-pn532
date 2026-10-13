import hashlib
from importlib.resources import files

from ink_nfc_fmsc.catalog import asset_json, screens, select_screen, source_recipes
from ink_nfc_fmsc.image_processing import ImageProcessOptions, process_frame
from ink_nfc_fmsc.image_encoding import RgbaFrame


def test_installed_resources_are_self_contained_and_match_manifest():
    assets = files("ink_nfc_fmsc._assets")
    manifest = asset_json("provenance.json")
    for name, expected in manifest["bundled"].items():
        assert hashlib.sha256(assets.joinpath(name).read_bytes()).hexdigest() == expected
    assert len(screens()) == 50 and len(source_recipes()) == 67
    assert select_screen("huawei-a1").value["controller"] == "JD79665AA"
    assert select_screen("jd79665aa-552X768-bwry-a1").name == "JD79665AA-552x768-BWRY-A1"


def test_installed_mini_racer_runs_without_node_or_panel_checkout():
    frame = RgbaFrame(8, 8, bytes([127, 127, 127, 255]) * 64)
    processed = process_frame(frame, 1, ImageProcessOptions())
    assert (processed.width, processed.height, len(processed.data)) == (8, 8, 256)
