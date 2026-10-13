"""Registered controller-specific recipes; geometry is never patched by guessing."""
import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

COLORS = {2: "bw", 3: "bwr", 4: "bwry", 6: "e6"}
ALIASES = {
    "huawei-a0": "JD79665AA-552x768-BWRY-A0",
    "huawei-a1": "JD79665AA-552x768-BWRY-A1",
    "osptek-e6-154": "IST7513-240x240-E6", "osptek-e6-169": "IST7601-400x400-E6",
    "osptek-e6-370": "JD7601-IST7601-480x720-E6",
}

# Public names distinguish recipes without changing fingerprinted source IDs or driver data.
NAME_CONTROLLERS = {"uc8151_213_bwr": "UC8151"}
NAME_VARIANTS = {
    "huawei_a0_jd79665": "A0", "huawei_a1_jd79665": "A1",
}

# One reviewed baseline per glass/controller format. First ID is selected; the rest
# remain source evidence, not selectable device presets. Never combine their TLVs.
SCREEN_BASELINES = {
    "JD79661-128x250-BWRY": ("panel_rule_48_0_3", "panel_zkc21b_original"),
    "JD79668AA-400x300-BWRY": ("hanshow_jd79668_400x300_drf", "panel_generic_05",
                               "panel_hs_nebular420", "panel_zkc42b_original"),
    "JD79XXX-128x250-BWR": ("panel_rule_63_0_2", "panel_rule_63_1_2"),
    "JD79XXX-128x250-BWRY": ("panel_hs_nebular213", "panel_rule_63_0_3"),
    "SSD1619-128x250-BWR": ("panel_rule_32_1_2", "panel_generic_0f"),
    "SSD1675-152x152-BWR": ("panel_hs_nowa154", "panel_rule_33_0_2"),
    "SSD1680-128x250-BW": ("panel_generic_1b", "panel_rule_35_1_1"),
    "SSD1680-128x250-BWR": ("panel_generic_1a", "panel_zkc21v_original"),
    "SSD1683-400x300-BW": ("panel_generic_32", "panel_rule_36_1_1"),
    "SSD1683-400x300-BWR": ("panel_generic_33", "panel_zkc42vm_original"),
    "UC8151-104x212-BWR": ("panel_generic_19", "panel_ses_se2213fs093"),
    "UC8151-152x296-BWR": ("panel_generic_18", "panel_ses_se2266fs093"),
    "UC8176-400x300-BWR": ("panel_generic_02", "panel_rule_19_1_2"),
    "UC8276-400x300-BWR": ("panel_nrf_aesl_420_test", "panel_zkc42v_original"),
    "UC8XXX-128x250-BWR": ("panel_rule_16_0_2", "panel_rule_16_1_2"),
}


def asset_json(name):
    return json.loads(files("ink_nfc_fmsc._assets").joinpath(name).read_text(encoding="utf-8"))


def driver_tags(flow):
    tags = []
    offset = 0
    while offset < len(flow):
        if offset + 2 > len(flow):
            raise ValueError("Truncated DB TLV")
        tag, length = flow[offset:offset + 2]
        offset += 2
        if not length or offset + length > len(flow):
            raise ValueError("Invalid DB TLV length")
        tags.append((tag, flow[offset:offset + length]))
        offset += length
    return tags


@dataclass(frozen=True)
class Screen:
    value: dict

    def __post_init__(self):
        p = self.value
        width, height, colors, planes = p["width"], p["height"], p["color"], p["planes"]
        packing = p["packing"]
        if (not 8 <= width <= 2048 or not 8 <= height <= 2048
                or colors not in (2, 3, 4, 6) or planes not in (1, 2)
                or p["family"] not in ("UC", "SSD", "JD", "IST")):
            raise ValueError(f"Invalid screen metadata: {p['id']}")
        if packing not in ("bw", "bwr", "bwr_inverted", "gray4_planes", "huawei_2bpp",
                           "indexed_2bpp", "indexed_4bpp", "e6_planes", "uc8159_4bpp"):
            raise ValueError("Unknown pixel packing")
        fixed_colors = {"bw": 2, "bwr": 3, "bwr_inverted": 3, "gray4_planes": 4,
                        "huawei_2bpp": 4, "indexed_4bpp": 6, "e6_planes": 6}
        if (packing in fixed_colors and colors != fixed_colors[packing]
                or packing == "indexed_2bpp" and colors not in (2, 3, 4)
                or packing == "uc8159_4bpp" and colors not in (2, 3)
                or packing == "gray4_planes" and p["family"] != "SSD"):
            raise ValueError("Controller/palette/pixel packing mismatch")
        expected_planes = 2 if packing in ("bwr", "bwr_inverted", "gray4_planes", "e6_planes") else 1
        stride = ((width + 3) // 4 if packing in ("huawei_2bpp", "indexed_2bpp", "e6_planes") else
                  (width + 1) // 2 if packing in ("indexed_4bpp", "uc8159_4bpp") else (width + 7) // 8)
        if planes != expected_planes or p["plane_bytes"] != stride * height or p["raw_bytes"] != stride * height * planes:
            raise ValueError("Pixel packing / raw size mismatch")
        if not 0 <= p.get("invert_planes", 0) < (1 << planes):
            raise ValueError("Invalid plane inversion mask")
        x, y, w, h = self.visible
        if x < 0 or y < 0 or w < 1 or h < 1 or x + w > width or y + h > height:
            raise ValueError("Visible window is out of bounds")
        if not 1 <= p["minimum_observation_seconds"] < p["refresh_timeout_seconds"] <= 240:
            raise ValueError("Invalid refresh observation deadlines")
        if not self.supported:
            if not p.get("unsupported_reason"):
                raise ValueError("Disabled recipe must state why")
            return
        if packing in ("e6_planes", "uc8159_4bpp"):
            raise ValueError("Unadapted packing cannot be enabled")
        if packing == "huawei_2bpp" and (width, height, colors, p.get("reverse_rows", False), p["family"]) != (552, 768, 4, False, "JD"):
            raise ValueError("Huawei native layout contract mismatch")
        if packing == "huawei_2bpp" and p.get("invert_planes", 0):
            raise ValueError("Huawei native packed codes cannot be plane-inverted")
        mapping = p.get("row_mapping", "linear")
        if mapping != "linear" and not (packing == "huawei_2bpp" and mapping == "a0_interleaved"):
            raise ValueError("Unknown native row mapping")
        if packing == "indexed_4bpp" and (colors != 6 or width % 2 or p["family"] != "IST"
                or p.get("pixel_codes") != [0, 1, 2, 3, 5, 6]
                or not p.get("requires_e6_wiring_confirmation")):
            raise ValueError("Native E6 code/wiring contract mismatch")
        if packing == "indexed_2bpp" and (width % 4 or p["family"] != "JD"):
            raise ValueError("Indexed 2-bpp geometry/controller mismatch")
        if p["transport"] not in ("d2", "d3") or p["transport"] == "d2" and p["plane_bytes"] > 64000:
            raise ValueError("Invalid image transport/packet count")
        if p["db_clear"] != "F0DB020000" or p["d4_apdu"] != "F0D4858000":
            raise ValueError("Only reviewed slot-0 async D4 stored-driver flows are supported")
        chunks = p["db_apdus"]
        if not 1 <= len(chunks) <= 9:
            raise ValueError("Invalid DB packet count")
        for i, text in enumerate(chunks):
            apdu = bytes.fromhex(text)
            if (not 6 <= len(apdu) <= 255 or apdu[:5] != bytes((0xF0, 0xDB, int(i < len(chunks) - 1), 0, len(apdu) - 5))):
                raise ValueError("Invalid DB framing")
        flow = self.flow
        if len(flow) > 2048 or hashlib.sha256(flow).hexdigest() != p["driver_sha256"]:
            raise ValueError("DB fingerprint/size mismatch")
        tags = driver_tags(flow)
        if tags[0][0] != 0xA0 or len(tags[0][1]) != 6:
            raise ValueError("DB must begin with geometry")
        geometry = tags[0][1]
        expected_x = height * 2 if packing == "huawei_2bpp" else width * 4 if packing == "indexed_4bpp" else width * 2 if packing == "indexed_2bpp" else width
        if int.from_bytes(geometry[2:4], "big") != expected_x or int.from_bytes(geometry[4:6], "big") != (width if packing == "huawei_2bpp" else height):
            raise ValueError("DB geometry is inconsistent; changing only image dimensions is unsafe")
        if bytes.fromhex(p["expected_d1_a0"]) != b"\xf0" + geometry or bytes.fromhex(p["da_apdu"]) != b"\xf0\xda\x00\x00\x03\xf0" + geometry[:2]:
            raise ValueError("DB/DA/D1 geometry mismatch")
        scan = bytes.fromhex(p["da_scan_apdu"])
        if not 6 <= len(scan) <= 11 or scan[:5] != bytes((0xF0, 0xDA, 1, 1, len(scan) - 5)) or bytes.fromhex(p["expected_d1_a1"]) != b"\x01" + scan[5:].ljust(6, b"\xff"):
            raise ValueError("DA/D1 scan mismatch")
        if len(tags) > 300 or sum(t == 0xA2 for t, _ in tags) > 30:
            raise ValueError("DB TLV count exceeded")
        ram = []
        for index, (tag, data) in enumerate(tags):
            if not 0xA0 <= tag <= 0xAA or tag == 0xA9 or tag == 0xA0 and index:
                raise ValueError("Unreviewed DB tag or duplicate geometry")
            if tag == 0xA4 and (len(data) != 1 or data[0] not in (0, 2, 3, 8, 12)):
                raise ValueError("Invalid BUSY/RST operation")
            if tag == 0xA5 and (len(data) != 2 or int.from_bytes(data, "big") > 200):
                raise ValueError("Unreviewed driver delay")
            if tag == 0xA8 and (len(data) != 1 or not 1 <= data[0] <= 9):
                raise ValueError("Invalid SPI read length")
            if tag == 0xA3:
                ram.append(data)
            forbidden = {"JD": (0x90, 0x91, 0xA2), "UC": (0xA0, 0xA1), "SSD": (0x2A, 0x30, 0x36, 0x39)}
            if tag in (0xA1, 0xA2) and data[0] in forbidden.get(p["family"], ()):
                raise ValueError("OTP/MTP programming is not supported")
        if len(ram) != 1 or len(ram[0]) != planes or len(set(ram[0])) != planes or not any(t == 0xA2 for t, _ in tags):
            raise ValueError("Missing/ambiguous RAM or refresh operations")

    @property
    def id(self):
        return self.value["id"]

    @property
    def controller_name(self):
        controller = NAME_CONTROLLERS.get(self.id, self.value["controller"])
        return re.sub(r"[^A-Za-z0-9]+", "-", controller).strip("-").upper()

    @property
    def name(self):
        p = self.value
        base = f"{self.controller_name}-{p['width']}x{p['height']}-{self.color.upper()}"
        variant = NAME_VARIANTS.get(self.id)
        return f"{base}-{variant}" if variant else base

    @property
    def aliases(self):
        return tuple(sorted(alias for alias, name in ALIASES.items()
                            if name.casefold() == self.name.casefold()))

    def matches_controller(self, name):
        p = self.value
        return name.casefold() in (self.controller_name.casefold(), p["controller"].casefold(),
               p["family"].casefold(), *self.controller_name.casefold().split("-"),
               str(p.get("reference", {}).get("config", {}).get("driver", "")))

    @property
    def supported(self):
        return self.value.get("supported", False)

    @property
    def flow(self):
        return b"".join(bytes.fromhex(s)[5:] for s in self.value.get("db_apdus", []))

    @property
    def color(self):
        return "gray4" if self.value["packing"] == "gray4_planes" else COLORS[self.value["color"]]

    @property
    def visible(self):
        p = self.value
        return p.get("visible_x", 0), p.get("visible_y", 0), p.get("visible_width", p["width"]), p.get("visible_height", p["height"])

    def require_supported(self):
        if not self.supported:
            raise ValueError(self.value["unsupported_reason"])


def source_recipes():
    """Reviewed evidence recipes, including retired presets; not a CLI catalog."""
    values = [Screen(p) for p in asset_json("drivers.json")["profiles"]]
    if len({s.id for s in values}) != len(values):
        raise ValueError("Duplicate source recipe IDs")
    return values


def screens():
    groups = {}
    for screen in source_recipes():
        groups.setdefault(screen.name, []).append(screen)
    values = []
    for name, members in groups.items():
        baseline = SCREEN_BASELINES.get(name)
        if baseline:
            by_id = {s.id: s for s in members}
            if baseline[0] not in by_id or not set(by_id) <= set(baseline):
                raise ValueError(f"Duplicate screen names or missing reviewed baseline: {name}")
            values.append(by_id[baseline[0]])
        elif len(members) == 1:
            values.append(members[0])
        else:
            raise ValueError(f"Duplicate screen names require a reviewed screen baseline: {name}")
    return sorted(values, key=lambda s: (s.value["family"], s.controller_name,
                  s.value["width"] * s.value["height"], s.value["width"], s.value["height"], s.name))


def select_screen(name=None, *, ic=None, width=None, height=None, color=None, profile_file=None):
    if profile_file:
        if name:
            raise ValueError("Choose --screen OR --profile-file")
        value = json.loads(Path(profile_file).read_text(encoding="utf-8"))
        if "recipe_id" in value:
            if not isinstance(value["recipe_id"], str) or not value["recipe_id"]:
                raise ValueError("Invalid exported recipe_id")
            value = {**value, "id": value["recipe_id"]}
        candidates = [Screen(value)]
    else:
        candidates = screens()
    if name:
        key = name.casefold()
        key = ALIASES.get(key, key).casefold()
        candidates = [s for s in candidates if key == s.name.casefold()]
    if ic:
        candidates = [s for s in candidates if s.matches_controller(ic)]
    candidates = [s for s in candidates if (width is None or s.value["width"] == width)
                  and (height is None or s.value["height"] == height) and (color is None or s.color == color)]
    if len(candidates) != 1:
        ids = ", ".join(s.name for s in candidates[:12])
        raise ValueError(f"Selection matched {len(candidates)} recipes; specify --screen. Candidates: {ids or 'none'}")
    return candidates[0]
