import pytest
import hashlib
from PIL import Image

from ink_nfc_fmsc.catalog import Screen, screens, select_screen, source_recipes
from ink_nfc_fmsc.images import pack_indices, palette, prepare_image, test_pattern as make_test_pattern
from ink_nfc_fmsc.image_processing import IMAGE_ALGORITHMS, PROCESSING_PRESETS, ImageProcessOptions


@pytest.mark.parametrize("screen", [s for s in screens() if s.supported], ids=lambda s: s.id)
def test_white_and_pattern_packing_sizes(screen):
    p = screen.value
    planes, preview = prepare_image(screen, white=True)
    assert preview.size == (p["width"], p["height"])
    assert len(planes) == p["planes"]
    assert all(len(plane) == p["plane_bytes"] for plane in planes)
    count = len(palette(screen))
    pattern = bytes(i % count for i in range(p["width"] * p["height"]))
    assert sum(map(len, pack_indices(pattern, screen))) == p["raw_bytes"]


def test_known_white_polarities_and_native_e6_codes():
    assert prepare_image(select_screen("huawei-a0"), white=True)[0][0] == b"\x55" * 105984
    assert prepare_image(select_screen("osptek-e6-154"), white=True)[0][0] == b"\x11" * 28800
    assert prepare_image(select_screen("SSD1683-400x300-BWR"), white=True)[0] == (b"\xff" * 15000, b"\x00" * 15000)
    s = select_screen("osptek-e6-154")
    indices = bytes([0, 1, 3, 2, 5, 4]) * 9600
    assert pack_indices(indices, s)[0] == bytes.fromhex("012356") * 9600


@pytest.mark.parametrize("screen", [s for s in screens() if s.supported], ids=lambda s: s.id)
def test_test_pattern_fits_visible_area_and_uses_native_palette(screen):
    pattern = make_test_pattern(screen)
    _, _, width, height = screen.visible
    assert pattern.size == (max(width, height), min(width, height))
    assert {color for _, color in pattern.getcolors()} == set(palette(screen))
    assert pattern.getpixel((0, 0)) == (0, 0, 0)


@pytest.mark.parametrize("alias", ["SSD1675-152x152-BW", "SSD1683-400x300-BWR", "JD79668AA-400x300-BWRY", "osptek-e6-154", "SSD1677-800x480-GRAY4"])
def test_generated_pattern_encodes_each_color_family(alias):
    screen = select_screen(alias)
    planes, preview = prepare_image(screen, make_test_pattern(screen), native_palette=True)
    assert sum(map(len, planes)) == screen.value["raw_bytes"]
    assert {color for _, color in preview.getcolors()} == set(palette(screen))


def test_in_memory_pattern_matches_png_input_and_mirror(tmp_path):
    screen = select_screen("SSD1675-152x152-BW")
    pattern = make_test_pattern(screen)
    path = tmp_path / "pattern.png"
    pattern.save(path)
    options = dict(rotation="180", mirror_x=True, options=ImageProcessOptions(algorithm="none"))
    memory, memory_preview = prepare_image(screen, pattern, **options)
    disk, disk_preview = prepare_image(screen, path, **options)
    assert memory == disk and memory_preview.tobytes() == disk_preview.tobytes()
    assert pattern.getpixel((0, 0)) == (0, 0, 0)


def test_a0_mapping_is_bijection_and_happens_before_split():
    from ink_nfc_fmsc.plans import split_half
    s = select_screen("huawei-a0")
    linear = Screen({**s.value, "row_mapping": "linear"})
    width, height = s.value["width"], s.value["height"]
    indices = bytes((x // 7 + y // 31) % 4 for y in range(height) for x in range(width))
    raw = pack_indices(indices, linear)[0]
    mapped = pack_indices(indices, s)[0]
    for row in range(552):
        target = 2 * row if row < 276 else 1103 - 2 * row
        assert mapped[target * 192:(target + 1) * 192] == raw[row * 192:(row + 1) * 192]
    assert split_half(mapped, 0) != mapped[:52992]


@pytest.fixture
def photo(tmp_path):
    image = Image.new("RGB", (16, 20))
    image.putdata([(x * 17, y * 12, (x + y) * 7) for y in range(20) for x in range(16)])
    path = tmp_path / "image.png"
    image.save(path)
    return path


@pytest.mark.parametrize("algorithm", IMAGE_ALGORITHMS, ids=lambda a: a.name)
@pytest.mark.parametrize("alias", ["SSD1675-152x152-BW", "JD79668AA-400x300-BWRY", "osptek-e6-154", "SSD1677-800x480-GRAY4"])
def test_real_bundled_panel_algorithms_produce_native_colors(photo, algorithm, alias):
    s = select_screen(alias)
    # Use actual recipe dimensions: this also exercises coverage/composition code.
    planes, preview = prepare_image(s, photo, options=ImageProcessOptions(algorithm=algorithm.name))
    assert sum(map(len, planes)) == s.value["raw_bytes"]
    pixels = preview.get_flattened_data() if hasattr(preview, "get_flattened_data") else preview.getdata()
    assert set(pixels) <= set(palette(s))


@pytest.mark.parametrize("preset", PROCESSING_PRESETS)
def test_presets_and_mirror_rotation(photo, preset):
    s = select_screen("SSD1675-152x152-BW")
    _, preview = prepare_image(s, photo, rotation=90, mirror_x=True, fit="stretch",
                              options=ImageProcessOptions(preset=preset, heavy_ink=True))
    assert preview.size == (152, 152)


def test_none_matches_panel_api_exactly(photo):
    from ink_nfc_fmsc.image_processing import compose_image, process_frame
    s = select_screen("SSD1675-152x152-BW")
    composition = compose_image(photo, 152, 152)
    expected = process_frame(composition.frame, 1, ImageProcessOptions(algorithm="none"), coverage=composition.coverage)
    _, preview = prepare_image(s, photo, options=ImageProcessOptions(algorithm="none"))
    assert preview.tobytes() == Image.frombytes("RGBA", (152, 152), expected.data).convert("RGB").tobytes()


# Native PriceTag/Huawei recipes deliberately change row mapping versus the generic
# screen reference snapshot; use their independent legacy-flow vectors below instead.
@pytest.mark.parametrize("screen", [s for s in source_recipes() if s.supported and s.id.startswith("panel_") and s.value.get("reference", {}).get("golden")], ids=lambda s: s.id)
def test_native_plane_hashes_match_existing_panel_golden_vectors(screen):
    p = screen.value
    reference = p["reference"]["golden"]
    colors = palette(screen)
    white = len(colors) - 1 if screen.color == "gray4" else 1
    indices = bytearray([white]) * (p["width"] * p["height"])
    x, y, _, _ = screen.visible
    for i, color in enumerate(reference["colors"]):
        rgb = tuple(bytes.fromhex(color))
        indices[y * p["width"] + x + i] = colors.index(rgb)
    raw = pack_indices(bytes(indices), screen)
    for plane, actual in enumerate(raw):
        # Golden is controller-native; FMSC A3 polarity translation is separate.
        if p.get("invert_planes", 0) & (1 << plane):
            actual = bytes(v ^ 255 for v in actual)
        assert len(actual) == reference["planes"][plane]["bytes"]
        assert hashlib.sha256(actual).hexdigest() == reference["planes"][plane]["sha256"]


@pytest.mark.parametrize("alias,expected", [
    ("SSD1675-152x152-BW", "41c4c3b2ff1ac1fed95cb7fc83b014aa7c66550e4fd6f8b0df6f0f5509e0c8ea"),
    ("huawei-a0", "14cb3400233708e14da42ed78cf507d36ceb928b010715ebc03e9ee3a5671b9a"),
    ("huawei-a1", "02d68c3e874f5ecf05bf63b89b3bc50fc6a42bca64b81b45bff27cf3e921c51a"),
])
def test_native_mapping_matches_legacy_panel_program_bytes(alias, expected):
    # Captured from reference pack_panel() before extraction, not this implementation.
    s = select_screen(alias)
    width, height, count = s.value["width"], s.value["height"], len(palette(s))
    indices = bytes((x // 7 + y // 31) % count for y in range(height) for x in range(width))
    assert hashlib.sha256(b"".join(pack_indices(indices, s))).hexdigest() == expected
