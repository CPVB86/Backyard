"""Read-only snapshot, shared browser renderer, and atomic PNG publication."""
from datetime import datetime, timezone
import io
import logging
import mimetypes
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import urlsplit, unquote
from zoneinfo import ZoneInfo
from PIL import Image
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine
from app.core.config import ROOT
from app.modules.avian_visitors.service import recent, lifelist
from .settings import export_hours
from app.modules.samsung_frame.config import resolve_path
from app.modules.samsung_frame.storage import locked
from generator.store import AssetStore

MODULE = Path(__file__).parent
STATIC = ROOT / "app/modules/avian_visitors/static"
LOG = logging.getLogger(__name__)
SIZE = (3840, 2160)


def timestamp(now):
    local = now.astimezone(ZoneInfo("Europe/Amsterdam"))
    return local.strftime("Vogelbezoeken in de tuin op %d-%m-%Y - %H:%Mu")


def snapshot(settings, now):
    database = settings.resolved_database_path.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Existing Backyard database missing: {database}")
    # Refuse to create, migrate, seed, or write to the production database.
    engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(
        database.as_uri() + "?mode=ro", uri=True, timeout=5))
    store = AssetStore(settings.resolved_storage_root / "generator")
    try:
        hours = export_hours(settings)
        value = (lifelist(engine, store, "nl", now=now) if hours is None
                 else recent(engine, store, hours, "nl", now=now))
    finally:
        engine.dispose()
    if not value["species"]:
        raise ValueError("No accepted bird observations in export window; keeping previous PNG")
    assets = {}
    for species in value["species"]:
        lookup = store.lookup("bird", species["scientific_name"])["assets"]
        for metadata in species["assets"].values():
            asset_id = metadata["url"].rsplit("/", 1)[-1]
            path = lookup[asset_id]["path"]
            with Image.open(path) as image:
                metadata["pixel_dimensions"] = list(image.size)
            assets[metadata["url"]] = path
    return {"species": jsonable_encoder(value["species"]), "timestamp": timestamp(now),
            "max_upscale": settings.avian_export_max_upscale}, assets


def render_png(value, assets, settings):
    from playwright.sync_api import sync_playwright
    files = {"/": MODULE / "export.html", "/export.js": MODULE / "export.js"}
    def serve(route):
        parts = urlsplit(route.request.url)
        if parts.netloc != "avian-export.invalid":
            route.abort(); return
        path = unquote(parts.path)
        file = assets.get(path) or files.get(path)
        if path.startswith("/static/"):
            candidate = (STATIC / path[len("/static/"):]).resolve()
            if candidate.is_relative_to(STATIC.resolve()):
                file = candidate
        if file is None or not file.is_file():
            route.abort(); return
        route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(str(file))[0] or "application/octet-stream")
    with sync_playwright() as playwright:
        executable = settings.avian_export_chromium_path
        browser = playwright.chromium.launch(headless=True,
            executable_path=str(executable) if executable else None)
        try:
            page = browser.new_page(viewport={"width": SIZE[0], "height": SIZE[1]},
                                    device_scale_factor=1, locale="nl-NL", timezone_id="Europe/Amsterdam")
            page.set_default_timeout(settings.avian_export_timeout * 1000)
            page.route("**/*", serve)  # Only whitelisted local assets; no external network/API.
            page.goto("http://avian-export.invalid/", wait_until="load")
            page.evaluate("value => { window.renderExport(value).then(result => {window.exportResult=result;}).catch(error => {window.exportError=String(error);}); }", value)
            page.wait_for_function("window.exportResult || window.exportError")
            failure = page.evaluate("window.exportError || null")
            if failure:
                raise RuntimeError(failure)
            stats = page.evaluate("window.exportResult")
            LOG.info("Rendered species=%s", stats["species"])
            return page.screenshot(type="png", animations="disabled")
        finally:
            browser.close()


def publish(output, data):
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "PNG" or image.size != SIZE:
            raise ValueError("Renderer did not return a 3840 x 2160 PNG")
        image.verify()
    fd, temporary = tempfile.mkstemp(prefix=".collage-", suffix=".png", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data); file.flush(); os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def export(settings, *, now=None):
    now = now or datetime.now(timezone.utc)
    output = resolve_path(settings.samsung_frame_image_path)
    # The same lock as Samsung CLI prevents upload/render overlap.
    state = resolve_path(settings.samsung_frame_state_path)
    if output.resolve() in {state.resolve(), resolve_path(settings.samsung_frame_token_path).resolve()}:
        raise ValueError("Image path must differ from Samsung token and artwork journal")
    with locked(state.with_suffix(".lock")):
        output.parent.mkdir(parents=True, exist_ok=True)
        # Only our unpublished temporary files, e.g. after a killed render.
        for abandoned in output.parent.glob(".collage-*.png"):
            abandoned.unlink()
        value, assets = snapshot(settings, now)
        data = render_png(value, assets, settings)
        publish(output, data)
    return output
