from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from app.modules.observations.models import Observation
from app.modules.avian_collage_exporter import renderer


@pytest.fixture
def config(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "existing.sqlite3",
        storage_root=tmp_path / "data", samsung_frame_image_path=tmp_path / "frame/samsung-frame.png",
        samsung_frame_state_path=tmp_path / "frame/artwork.json", samsung_frame_token_path=tmp_path / "frame/token",
        avian_export_chromium_path=os.environ.get("BACKYARD_EXPORT_TEST_CHROMIUM"))
    now = datetime(2026, 10, 8, 9, 45, tzinfo=timezone.utc)
    with TestClient(create_app(settings)) as client:
        with Session(client.app.state.engine) as session:
            for index, (name, status) in enumerate([
                ("Turdus merula", "auto_accepted"), ("Parus major", "human_confirmed"),
                ("Cyanistes caeruleus", "auto_accepted"), ("Corvus corax", "human_rejected")]):
                session.add(Observation(source="test", event_id=str(index), ingest_hash="a"*64,
                    domain="bird", scientific_name=name, common_name=name, start_at=now-timedelta(minutes=1),
                    end_at=now, best_confidence=.9, status=status, decision={}, policy={}, clip={}, evidence_kind="permanent"))
            session.commit()
        yield settings, now


def test_amsterdam_timestamp_winter_and_summer():
    assert renderer.timestamp(datetime(2026,10,8,9,45,tzinfo=timezone.utc)) == "Vogelbezoeken in de tuin op 08-10-2026 - 11:45u"
    assert renderer.timestamp(datetime(2026,1,8,9,45,tzinfo=timezone.utc)).endswith("10:45u")


def test_snapshot_reuses_accepted_avian_data_and_assets(config):
    settings, now = config
    value, assets = renderer.snapshot(settings, now)
    assert len(value["species"]) == 3 and assets
    assert "Corvus corax" not in {item["scientific_name"] for item in value["species"]}
    assert all(path.is_file() for path in assets.values())
    assert value["timestamp"].endswith("11:45u")


def test_missing_database_is_never_created(tmp_path):
    config=Settings(_env_file=None, database_path=tmp_path/"missing.sqlite3")
    with pytest.raises(FileNotFoundError):
        renderer.snapshot(config, datetime.now(timezone.utc))
    assert not config.resolved_database_path.exists()


def test_render_failure_and_invalid_png_preserve_last_output(config):
    settings, now = config
    output=settings.samsung_frame_image_path
    output.parent.mkdir(); output.write_bytes(b"last successful PNG")
    with patch.object(renderer,"render_png",side_effect=RuntimeError("browser failed")):
        with pytest.raises(RuntimeError):
            renderer.export(settings,now=now)
    assert output.read_bytes()==b"last successful PNG"
    data=io.BytesIO(); Image.new("RGB",(800,600)).save(data,format="PNG")
    with pytest.raises(ValueError):
        renderer.publish(output,data.getvalue())
    assert output.read_bytes()==b"last successful PNG"
    assert not list(output.parent.glob(".collage-*.png"))


def test_real_browser_export(config):
    settings,now=config
    if not settings.avian_export_chromium_path:
        pytest.skip("Set BACKYARD_EXPORT_TEST_CHROMIUM for necessary real browser verification")
    output=renderer.export(settings,now=now)
    with Image.open(output) as image:
        assert image.format=="PNG" and image.size==(3840,2160)
        assert len(image.getcolors(maxcolors=10000000))>100
    assert not list(output.parent.glob(".collage-*.png"))


def test_atomic_replace_failure_retains_last_png_and_cleans_temp(tmp_path, monkeypatch):
    output=tmp_path/"samsung-frame.png"; output.write_bytes(b"previous")
    data=io.BytesIO(); Image.new("RGB",(3840,2160)).save(data,format="PNG")
    def fail_replace(*args):
        raise OSError("disk error")
    monkeypatch.setattr(renderer.os,"replace",fail_replace)
    with pytest.raises(OSError):
        renderer.publish(output,data.getvalue())
    assert output.read_bytes()==b"previous"
    assert not list(tmp_path.glob(".collage-*.png"))


def test_lock_rejects_overlapping_export(config):
    settings,now=config
    from app.modules.samsung_frame.storage import locked
    with locked(settings.samsung_frame_state_path.with_suffix(".lock")):
        with pytest.raises(OSError):
            renderer.export(settings,now=now)
    assert not settings.samsung_frame_image_path.exists()


def test_shared_layout_adapts_counts_without_low_resolution_upscaling():
    executable=os.environ.get("BACKYARD_EXPORT_TEST_CHROMIUM")
    if not executable:
        pytest.skip("Set BACKYARD_EXPORT_TEST_CHROMIUM for browser geometry test")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=executable)
        try:
            page=browser.new_page()
            page.add_script_tag(path=str(renderer.STATIC/"collage-layout.js"))
            plans=page.evaluate("""() => [1,7].map(n => {
                const items=Array.from({length:n},(_,i)=>({scientific_name:'test-'+i,common_name:'Vogel '+i,count:1,
                  assets:{perched:{dimensions:[500,500],pixel_dimensions:[500,500],mask:{w:1,h:1,bits:'gA=='}}}}));
                const plan=AvianCollage.arrange(items,3808,2056,{exportMode:true,maxUpscale:1.5});
                return {missing:plan.missing.length,tiles:plan.placed.map(t=>({x:t.x,y:t.y,w:t.fullW,h:t.fullH}))};
            })""")
            for count,plan in zip((1,7),plans):
                assert len(plan['tiles'])==count and plan['missing']==0
                for tile in plan['tiles']:
                    assert 0<=tile['x']<=3808-tile['w'] and 0<=tile['y']<=2056-tile['h']
                    assert tile['w']<=500+1e-8 and tile['h']<=500+1e-8
                    assert abs(tile['w']/tile['h']-1)<.001
        finally:
            browser.close()
