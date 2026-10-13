import hashlib
import json
from pathlib import Path

from ink_nfc_fmsc.catalog import source_recipes


def test_panel_contracts_remain_unchanged():
    expected = json.loads((Path(__file__).parent / "fixtures/panel-contracts.json").read_text())
    metadata = {"id", "label", "source", "qualification", "source_qualification", "hardware_evidence_source",
                "recipe_source_file", "recipe_label", "recipe_origin", "recipe_source", "reference", "notes", "translation_notes"}
    actual = {}
    for screen in source_recipes():
        payload = {key: item for key, item in screen.value.items() if key not in metadata}
        actual[screen.id] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert actual == expected


def test_bundled_metadata_does_not_contain_checkout_paths():
    from ink_nfc_fmsc.catalog import asset_json
    for name in ("drivers.json", "two-pass.json", "provenance.json"):
        text = json.dumps(asset_json(name))
        assert "Projects" not in text and ".codex" not in text
    assert "private_sources" not in asset_json("provenance.json")
