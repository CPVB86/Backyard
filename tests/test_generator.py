from io import BytesIO
from pathlib import Path
import hashlib
import json
from unittest.mock import Mock

import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from generator.store import AssetStore, GenerationConflict, UnsupportedDomain, public_status, species_key
from generator.domains.birds import adapter, openai_images
from generator.domains.birds.adapter import Birds, ASSETS, catalogue
from generator.domains.birds.render import prepare_prompt, save_cutout
from generator.domains.birds.masks import build_tables


SCI = "Turdus migratorius"
AUTH = {"Authorization": "Bearer backyard-test-token"}


def png(transparent=True):
    image = Image.new("RGBA", (100, 100), (255, 255, 255, 0 if transparent else 255))
    ImageDraw.Draw(image).ellipse((25, 20, 75, 80), fill=(30, 50, 60, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_existing_both_poses_never_generate(tmp_path, monkeypatch):
    paid = Mock(side_effect=AssertionError("Must not generate existing assets"))
    monkeypatch.setattr(openai_images, "generate_png", paid)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = AssetStore(tmp_path).ensure("bird", SCI, "American Robin")
    assert result["status"] == "ready"
    assert result["missing_assets"] == []
    assert {"perched", "flight"} <= result["assets"].keys()
    paid.assert_not_called()
    assert not (tmp_path / "generation.lock").exists()


def test_only_missing_pose_generated_and_persisted(tmp_path, monkeypatch):
    original = Birds.bundled
    monkeypatch.setattr(Birds, "bundled", lambda self, sci: {k:v for k,v in original(self,sci).items() if k != "flight"})
    paid = Mock(return_value=png())
    monkeypatch.setattr(openai_images, "generate_png", paid)
    store = AssetStore(tmp_path)
    result = store.ensure("bird", SCI, "American Robin")
    assert result["status"] == "ready"
    paid.assert_called_once()
    assert "in flight with wings spread" in paid.call_args.args[1]
    flight = result["assets"]["flight"]
    assert flight["file"] == "turdus-migratorius-2.png"
    dims, masks = build_tables(store.directory("bird", SCI), only={"turdus-migratorius-2"})
    assert flight["dimensions"] == dims["turdus-migratorius-2"]
    assert flight["mask"] == masks["turdus-migratorius-2"]
    assert len(list((store.directory("bird", SCI)/"raw").glob("*.png"))) == 1
    AssetStore(tmp_path).ensure("bird", SCI, "American Robin")
    assert paid.call_count == 1


def test_invalid_generation_preserves_raw_and_blocks_automatic_retry(tmp_path, monkeypatch):
    paid = Mock(return_value=png(False))
    monkeypatch.setattr(openai_images, "generate_png", paid)
    store = AssetStore(tmp_path)
    with pytest.raises(ValueError, match="transparent"):
        store.ensure("bird", "Testus example", "Example")
    assert store.lookup("bird", "Testus example")["attempts"]["perched"]["status"] == "failed"
    assert len(list((store.directory("bird", "Testus example")/"raw").glob("*.png"))) == 1
    with pytest.raises(GenerationConflict):
        store.ensure("bird", "Testus example", "Example")
    assert paid.call_count == 1
    paid.return_value = png()
    assert store.ensure("bird", "Testus example", "Example", retry_failed=True)["status"] == "ready"
    assert paid.call_count == 3


def test_generation_lock_prevents_parallel_calls(tmp_path):
    store = AssetStore(tmp_path)
    with store.generation_lock():
        with pytest.raises(GenerationConflict):
            store.ensure("bird", "Testus example", "Example")
    assert not (tmp_path / "generation.lock").exists()


def test_bat_adapter_uses_own_asset_types_and_independent_storage(tmp_path):
    class Bats:
        required_assets = ("roost_diagram",)
        def identities(self): return ()
        def bundled(self, name): return {}
        def validate_species(self, name): pass
        def valid(self, path, metadata): return path.read_bytes() == b"bat diagram"
        def generate(self, name, common, asset_id, directory, options):
            assert asset_id == "roost_diagram"
            (directory / "diagram.txt").write_bytes(b"bat diagram")
            return {"file":"diagram.txt", "content_type":"text/plain"}
    store = AssetStore(tmp_path, {"bird":Birds(), "bat":Bats()})
    result = store.ensure("bat", SCI, "Example")
    assert result["status"] == "ready"
    assert set(result["assets"]) == {"roost_diagram"}
    assert store.directory("bird", SCI) != store.directory("bat", SCI)
    assert "roost_diagram" not in store.lookup("bird", SCI)["assets"]
    assert store.resolve_key("bat", species_key(SCI))["domain"] == "bat"


def test_unconfigured_bat_does_not_invoke_birds(tmp_path):
    store = AssetStore(tmp_path)
    assert store.lookup("bat", "Pipistrellus pipistrellus")["status"] == "not_configured"
    with pytest.raises(UnsupportedDomain):
        store.ensure("bat", "Pipistrellus pipistrellus", "Bat")
    assert store.resolve_key("bat", species_key(SCI)) is None


def test_all_imported_files_and_metadata_are_intact():
    imported = json.loads((ASSETS/"imported-files.json").read_text())
    assert len(imported) == 923
    for item in imported:
        assert hashlib.sha256((ASSETS/item["file"]).read_bytes()).hexdigest() == item["sha256"]
    birds = Birds()
    types = set()
    for sci, entry in catalogue().items():
        assets = birds.bundled(sci)
        assert set(assets) == set(entry["assets"])
        types.update(asset["asset_type"] for asset in assets.values())
    assert types == {"illustration", "photo_cutout", "legacy_sketch"}
    assert len(list((ASSETS/"local/raw").glob("*.png"))) == 24


def test_legacy_assets_are_not_counted_as_illustration_poses(tmp_path, monkeypatch):
    original = Birds.bundled
    monkeypatch.setattr(Birds, "bundled", lambda self,sci: {k:v for k,v in original(self,sci).items() if k not in self.required_assets})
    result = AssetStore(tmp_path).lookup("bird", SCI)
    assert result["missing_assets"] == ["perched", "flight"]
    assert result["status"] != "ready"


def test_transparency_validation_and_crop(tmp_path):
    path = tmp_path/"bird.png"
    save_cutout(png(), path)
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.size == (67,77)
        assert image.getchannel("A").getbbox() == (8,8,59,69)
    with pytest.raises(ValueError): save_cutout(png(False), tmp_path/"opaque.png")


def test_prompt_keeps_reference_roles_and_species_notes():
    notes = json.loads(Path(adapter.__file__).with_name("species-notes.json").read_text())
    sci = next(name for name in notes if not name.startswith("_"))
    prompt = prepare_prompt(sci, "Example", 2, [("ANATOMY", Path("a.png")), ("STYLE only", Path("b.png"))])
    assert sci in prompt and notes[sci] in prompt
    assert "in flight with wings spread" in prompt
    assert "Image 1: ANATOMY" in prompt and "Image 2: STYLE only" in prompt
    assert "fully transparent background" in prompt
    assert "{sci_name}" not in prompt and "{pose}" not in prompt


def test_generator_api_auth_metadata_assets_and_generation(tmp_path, monkeypatch):
    paid = Mock(side_effect=AssertionError("No paid call"))
    monkeypatch.setattr(openai_images, "generate_png", paid)
    app = create_app(Settings(_env_file=None, database_path=tmp_path/"db.sqlite", storage_root=tmp_path))
    with TestClient(app) as client:
        key = species_key(SCI)
        for path in ["/api/generator", "/api/generator/bird/species?scientific_name=Turdus%20migratorius", f"/api/generator/bird/assets/{key}/perched"]:
            assert client.get(path).status_code == 401
        payload = {"scientific_name":SCI, "common_name":"American Robin"}
        assert client.post("/api/generator/bird/generate", json=payload).status_code == 401
        assert client.get("/api/generator", headers=AUTH).json()["bird"]["required_assets"] == ["perched", "flight"]
        response = client.get("/api/generator/bird/species", params={"scientific_name":SCI}, headers=AUTH)
        assert response.status_code == 200
        value = response.json()
        assert value["status"] == "ready"
        for asset in value["assets"].values():
            assert "path" not in asset and "file" not in asset
            image = client.get(asset["url"], headers=AUTH)
            assert image.status_code == 200
            assert image.content.startswith(b"\x89PNG")
            assert image.headers["cache-control"] == "private, max-age=3600"
        assert client.post("/api/generator/bird/generate", json=payload, headers=AUTH).status_code == 200
        assert client.post("/api/generator/bat/generate", json=payload, headers=AUTH).status_code == 501
        assert client.get(f"/api/generator/bat/assets/{key}/perched", headers=AUTH).status_code == 404
        assert client.get("/api/generator/bird/assets/invalid/perched", headers=AUTH).status_code == 422
        assert client.get(f"/api/generator/bird/assets/{key}/missing", headers=AUTH).status_code == 404
    paid.assert_not_called()


def test_manifest_cannot_escape_species_storage(tmp_path):
    store = AssetStore(tmp_path)
    directory = store.directory("bird", "Testus example")
    directory.mkdir(parents=True)
    (directory/"manifest.json").write_text(json.dumps({"domain":"bird", "scientific_name":"Testus example", "assets":{"perched":{"file":"../../outside.png"}}}))
    with pytest.raises(ValueError, match="escapes"):
        store.lookup("bird", "Testus example")


def test_legacy_nonbird_preserved_but_not_served_as_bird(tmp_path):
    archived = json.loads((ASSETS/"legacy-nonbirds.json").read_text())
    assert "Phoca vitulina" in archived
    assert (ASSETS/"local/phoca-vitulina.png").is_file()
    store = AssetStore(tmp_path)
    assert store.lookup("bird", "Phoca vitulina")["assets"] == {}
    with pytest.raises(ValueError, match="non-bird"):
        store.ensure("bird", "Phoca vitulina", "Harbour Seal")
