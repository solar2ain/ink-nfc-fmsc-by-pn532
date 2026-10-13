import copy
import hashlib

import pytest

from ink_nfc_fmsc.catalog import ALIASES, SCREEN_BASELINES, Screen, asset_json, screens, select_screen, source_recipes


def test_all_recipes_are_valid_and_sorted():
    catalog = screens()
    assert len(catalog) == 50
    assert sum(s.supported for s in catalog) == 45
    assert len({s.id for s in catalog}) == 50
    assert len(source_recipes()) == 67 and sum(s.supported for s in source_recipes()) == 61
    assert all(select_screen(alias).name == name for alias, name in ALIASES.items())
    assert all(select_screen(alias.upper()).name == name for alias, name in ALIASES.items())


@pytest.mark.parametrize("alias", ["a0", "A1", "ssd1683-bwr", "SSD1683-BWR", "ssd1683-bw",
    "pricetag154", "hema213", "hanshow213", "hanshow420", "hanshow420-drf", "he037-bwry",
    "e6-154", "e6-169", "e6-370"])
def test_ambiguous_short_aliases_are_retired(alias):
    with pytest.raises(ValueError, match="matched 0"):
        select_screen(alias)
    with pytest.raises(ValueError, match="matched 0"):
        select_screen(alias.upper())


def test_aliases_only_contain_reviewed_public_names():
    assert ALIASES == {
        "huawei-a0": "JD79665AA-552x768-BWRY-A0",
        "huawei-a1": "JD79665AA-552x768-BWRY-A1",
        "osptek-e6-154": "IST7513-240x240-E6",
        "osptek-e6-169": "IST7601-400x400-E6",
        "osptek-e6-370": "JD7601-IST7601-480x720-E6",
    }


@pytest.mark.parametrize("alias,name", [
    ("huawei-a0", "JD79665AA-552x768-BWRY-A0"),
    ("huawei-a1", "JD79665AA-552x768-BWRY-A1"),
    ("osptek-e6-154", "IST7513-240x240-E6"),
    ("osptek-e6-169", "IST7601-400x400-E6"),
    ("osptek-e6-370", "JD7601-IST7601-480x720-E6"),
])
def test_explicit_model_aliases_are_preserved(alias, name):
    assert select_screen(alias.upper()).name == name


@pytest.mark.parametrize("screen", screens(), ids=lambda s: s.name)
def test_public_names_are_unique_case_insensitive_and_preserve_exact_recipe(screen):
    for name in (screen.name, screen.name.lower(), screen.name.upper()):
        selected = select_screen(name)
        assert selected.id == screen.id and selected.value == screen.value
    assert "reference" not in screen.name.lower()
    assert len({s.name.casefold() for s in screens()}) == 50


@pytest.mark.parametrize("name,ident", [
    ("SSD1683-400x300-BWR", "panel_generic_33"),
    ("SSD1680-128x250-BW", "panel_generic_1b"),
    ("JD79665AA-552x768-BWRY-A0", "huawei_a0_jd79665"),
    ("JD79665AA-552x768-BWRY-A1", "huawei_a1_jd79665"),
    ("JD79668AA-400x300-BWRY", "hanshow_jd79668_400x300_drf"),
    ("SSD1619-128x250-BWR", "panel_rule_32_1_2"),
    ("UC8151-128x250-BWR", "uc8151_213_bwr"),
    ("IST7513-240x240-E6", "osptek_ht0154a10_e6_strict"),
])
def test_common_public_names_select_registered_variants(name, ident):
    assert select_screen(name).id == ident


@pytest.mark.parametrize("screen", [s for s in source_recipes() if s.id.startswith("panel_")], ids=lambda s: s.id)
def test_panel_developer_ids_are_no_longer_selection_names(screen):
    with pytest.raises(ValueError, match="matched 0"):
        select_screen(screen.id)
    with pytest.raises(ValueError, match="matched 0"):
        select_screen(screen.id.upper())


def test_duplicate_public_name_blocks_ambiguous_catalog(monkeypatch):
    original = copy.deepcopy(select_screen("SSD1683-400x300-BWR").value)
    duplicate = {**original, "id": "other_recipe"}
    monkeypatch.setattr("ink_nfc_fmsc.catalog.asset_json", lambda _name: {"profiles": [original, duplicate]})
    with pytest.raises(ValueError, match="Duplicate screen names"):
        screens()


def test_ambiguous_selection_reports_public_names_not_developer_ids():
    with pytest.raises(ValueError, match="matched 2") as error:
        select_screen(ic="JD79665AA", width=552, height=768, color="bwry")
    assert "JD79665AA-552x768-BWRY-A0" in str(error.value)
    assert "A1" in str(error.value) and "reference" not in str(error.value).lower()
    assert select_screen("UC8151-128x250-BWR", ic="uc8151").id == "uc8151_213_bwr"


def test_selection_is_controller_geometry_and_color_not_an_arbitrary_patch():
    assert select_screen("SSD1683-400x300-BWR", ic="SSD1683", width=400, height=300, color="bwr").id == "panel_generic_33"
    assert select_screen(ic="IST7513", width=240, height=240, color="e6").id == "osptek_ht0154a10_e6_strict"
    assert select_screen(ic="SSD1683", width=400, height=300, color="bwr").name == "SSD1683-400x300-BWR"
    with pytest.raises(ValueError, match="matched 0"):
        select_screen(ic="SSD1683", width=800, height=300, color="bwr")
    assert select_screen(ic="JD", width=400, height=300).name == "JD79668AA-400x300-BWRY"
    with pytest.raises(ValueError):
        select_screen("huawei-a0", width=400)


@pytest.mark.parametrize("name", ["SSD1680-128x250-BW-ZKC21V", "SSD1683-400x300-BWR-ZKC42VM",
    "JD79668AA-400x300-BWRY-HANSHOW-DRF", "SSD1619-128x250-BWR-HEMA",
    "SSD1675-152x152-BWR-NOWA", "UC8151-104x212-BWR-SES-SE2213FS093"])
def test_device_specific_screen_presets_are_retired(name):
    with pytest.raises(ValueError, match="matched 0"):
        select_screen(name)


def test_only_huawei_has_variants_and_reviewed_baselines_stay_intact():
    original = {s.id: s for s in source_recipes()}
    for screen in screens():
        suffix = screen.name.removeprefix(f"{screen.controller_name}-{screen.value['width']}x{screen.value['height']}-{screen.color.upper()}")
        assert not suffix or suffix in ("-A0", "-A1") and screen.id.startswith("huawei_")
        assert screen.value == original[screen.id].value
    for name, ids in SCREEN_BASELINES.items():
        assert select_screen(name).id == ids[0]
    for alias, canonical in ALIASES.items():
        assert select_screen(canonical).flow == select_screen(alias).flow


def test_missing_screen_baseline_cannot_silently_choose_device_alternative(monkeypatch):
    alternative = next(s.value for s in source_recipes() if s.id == "panel_zkc42vm_original")
    monkeypatch.setattr("ink_nfc_fmsc.catalog.asset_json", lambda _name: {"profiles": [alternative]})
    with pytest.raises(ValueError, match="missing reviewed baseline"):
        screens()


def resign(p):
    p["driver_sha256"] = hashlib.sha256(b"".join(bytes.fromhex(a)[5:] for a in p["db_apdus"])).hexdigest()
    return p


@pytest.mark.parametrize("change", [
    {"width": 384}, {"raw_bytes": 1}, {"row_mapping": "unknown"}, {"driver_sha256": "00"},
    {"expected_d1_a0": "F0122000800098"}, {"db_clear": "F0DB030000"},
    {"da_scan_apdu": "F0DA010103990030"}, {"d4_apdu": "F0D4850000"},
])
def test_corrupt_recipes_fail_locally(change):
    p = copy.deepcopy(select_screen("SSD1675-152x152-BW").value)
    p.update(change)
    with pytest.raises(ValueError):
        Screen(p)


def test_no_otp_mtp_programming_or_unsafe_e6_wiring():
    p = copy.deepcopy(select_screen("huawei-a0").value)
    p["db_apdus"][0] = p["db_apdus"][0].replace("A102000B", "A102900B")
    with pytest.raises(ValueError, match="OTP/MTP"):
        Screen(resign(p))
    p = copy.deepcopy(select_screen("osptek-e6-154").value)
    p["requires_e6_wiring_confirmation"] = False
    with pytest.raises(ValueError, match="wiring"):
        Screen(p)


def test_e6_370_is_not_phone_capacity_disabled_but_preserves_unqualified_state():
    screen = select_screen("osptek-e6-370")
    assert screen.supported and screen.value["raw_bytes"] == 172800
    assert screen.value["fmsc_byte_stream_verified"] is False
    assert "path" not in asset_json("drivers.json")["source_identity"]
