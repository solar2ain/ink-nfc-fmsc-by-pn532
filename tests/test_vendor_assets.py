import hashlib
import json
from pathlib import Path

from PIL import Image


def test_vendor_images_match_manifest():
    directory = Path(__file__).parents[1] / "assets" / "gooddisplay"
    manifest = json.loads((directory.parent / "gooddisplay-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["images"]) == 30
    assert manifest["directory"] == "gooddisplay"
    assert all(entry["kind"] == "sample" for entry in manifest["images"])
    assert {p.name for p in directory.iterdir() if p.suffix in {".bmp", ".png"}} == {
        entry["file"] for entry in manifest["images"]}
    assert manifest["source_page"].startswith("https://www.good-display.com/")
    sources = {entry["id"] for entry in manifest["sources"]}
    assert sources == {"nfc-d3-pro"}
    pixels = set()
    for entry in manifest["images"]:
        path = directory / entry["file"]
        assert path.resolve().is_relative_to(directory.resolve())
        assert path.parent == directory and path.suffix in {".bmp", ".png"}
        assert entry["source_id"] in sources
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        with Image.open(path) as image:
            image.load()
            assert image.size == (entry["width"], entry["height"])
            fingerprint = hashlib.sha256(str(image.size).encode() + image.convert("RGB").tobytes()).hexdigest()
            assert fingerprint == entry["pixel_sha256"]
            assert fingerprint not in pixels
            pixels.add(fingerprint)
        assert "also_in" not in entry


def test_user_documentation_has_consistent_bilingual_navigation():
    root = Path(__file__).parents[1]
    chinese = [root / "README.md", *(root / "docs").glob("*.md"), *(root / "assets").rglob("*.md")]
    chinese = [path for path in chinese if not path.name.endswith(".en.md")]
    assert chinese
    for path in chinese:
        english = path.with_name(path.stem + ".en.md")
        assert english.exists()
        navigation = f"[中文]({path.name}) | [English]({english.name})"
        assert path.read_text(encoding="utf-8").splitlines()[2] == navigation
        assert english.read_text(encoding="utf-8").splitlines()[2] == navigation
