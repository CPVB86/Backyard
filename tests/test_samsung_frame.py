from pathlib import Path
from unittest.mock import Mock
import pytest
from app.modules.samsung_frame.image import generate
from app.modules.samsung_frame.service import FrameService
from app.modules.samsung_frame.storage import save_state
from app.core.config import Settings, ROOT
from app.modules.samsung_frame.config import load_settings, resolve_path
from PIL import Image


def setup_upload(tmp_path):
    path = tmp_path / "artwork.json"
    save_state(path, dict(version=1, tv="tv-1", current="MY_old", pending=None, previous=None))
    art = Mock()
    art.upload.return_value = "MY_new"
    art.available.return_value = [{"content_id": "MY_new", "matte_id": "none"},
                                  {"content_id": "MY_old", "matte_id": "none"},
                                  {"content_id": "SAM_other", "matte_id": "none"}]
    art.get_current.return_value = {"content_id": "MY_new", "matte_id": "none"}
    art.get_artmode.return_value = "on"
    art.delete.return_value = True
    image = tmp_path / "samsung-frame.png"
    Image.new("RGB", (3840, 2160)).save(image)
    return FrameService(art, path, "tv-1"), art, image


def test_generate_and_config(tmp_path):
    path = generate(tmp_path / "samsung-frame.png")
    with Image.open(path) as image:
        assert image.size == (3840, 2160) and image.format == "PNG"
    assert Settings(_env_file=None).samsung_frame_host == ""
    assert resolve_path(Path("data/samsung_frame/samsung-frame.png")) == ROOT / "data/samsung_frame/samsung-frame.png"


def test_success_only_deletes_previous_after_verified_selection(tmp_path):
    service, art, image = setup_upload(tmp_path)
    assert service.upload(image) == "MY_new"
    assert art.upload.call_args.kwargs == dict(file_type="png", matte="none", portrait_matte="none")
    art.delete.assert_called_once_with("MY_old")
    calls = [call[0] for call in art.mock_calls]
    assert calls.index("select_image") < calls.index("get_current") < calls.index("delete")
    assert FrameService(art, service.path, "tv-1").state["current"] == "MY_new"


@pytest.mark.parametrize("failure", ["upload", "matte", "select", "current", "mode", "persist"])
def test_failure_never_deletes_old(tmp_path, failure):
    service, art, image = setup_upload(tmp_path)
    if failure == "upload":
        art.upload.side_effect = RuntimeError("TV error -1")
    elif failure == "matte":
        art.available.return_value[0]["matte_id"] = "shadowbox_polar"
    elif failure == "select":
        art.select_image.side_effect = RuntimeError("TV selection rejected")
    elif failure == "current":
        art.get_current.return_value["content_id"] = "MY_old"
    elif failure == "mode":
        art.get_artmode.return_value = "off"
    elif failure == "persist":
        service.save = Mock(side_effect=OSError("disk full"))
    with pytest.raises((RuntimeError, OSError)):
        service.upload(image)
    art.delete.assert_not_called()


def test_resume_and_tv_identity(tmp_path):
    service, art, image = setup_upload(tmp_path)
    art.select_image.side_effect = RuntimeError("timeout")
    with pytest.raises(RuntimeError):
        service.upload(image)
    with pytest.raises(ValueError):
        FrameService(art, service.path, "another-tv")
    resumed = FrameService(art, service.path, "tv-1")
    with pytest.raises(ValueError):
        resumed.upload(image)
    art.select_image.side_effect = None
    assert resumed.upload(image, resume=True) == "MY_new"
    assert art.upload.call_count == 1
    art.delete.assert_called_once_with("MY_old")


def test_delete_failure_preserves_journal(tmp_path):
    service, art, image = setup_upload(tmp_path)
    art.delete.return_value = False
    with pytest.raises(RuntimeError):
        service.upload(image)
    resumed = FrameService(art, service.path, "tv-1")
    assert resumed.state["current"] == "MY_new"
    assert resumed.state["previous"] == "MY_old"


def test_samsung_id_rejected(tmp_path):
    service, art, image = setup_upload(tmp_path)
    art.upload.return_value = "SAM_owned_by_samsung"
    with pytest.raises(ValueError):
        service.upload(image)
    art.delete.assert_not_called()


def test_production_environment_preserved(tmp_path, monkeypatch):
    environment = tmp_path / "service.env"
    environment.write_text(
        "BACKYARD_SAMSUNG_FRAME_HOST=192.0.2.10\n"
        "BACKYARD_DATABASE_PATH=data/existing.sqlite3\n"
        "BACKYARD_STORAGE_ROOT=data/existing-audio\n"
        "BACKYARD_MONITOR_DEVICE=unchanged\nUVICORN_PORT=8010\n",
        encoding="utf-8")
    settings = load_settings(environment)
    assert settings.samsung_frame_host == "192.0.2.10"
    assert settings.resolved_database_path == ROOT / "data/existing.sqlite3"
    assert settings.resolved_storage_root == (ROOT / "data/existing-audio").resolve()
    monkeypatch.setenv("BACKYARD_SAMSUNG_FRAME_HOST", "192.0.2.11")
    assert load_settings(environment).samsung_frame_host == "192.0.2.11"
    assert environment.read_text().endswith("UVICORN_PORT=8010\n")
