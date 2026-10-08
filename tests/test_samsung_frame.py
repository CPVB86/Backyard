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
    art.timeout = 60
    art.upload.return_value = "MY_new"
    art.available.return_value = [{"content_id": "MY_new", "matte_id": "none"},
                                  {"content_id": "MY_old", "matte_id": "none"},
                                  {"content_id": "SAM_other", "matte_id": "none"}]
    art.get_current.return_value = {"content_id": "MY_new", "matte_id": "none"}
    art.get_artmode.return_value = "on"
    def delete_previous(content_id):
        art.available.return_value = [entry for entry in art.available.return_value if entry["content_id"] != content_id]
        return True
    art.delete.side_effect = delete_previous
    image = tmp_path / "samsung-frame.png"
    Image.new("RGB", (3840, 2160)).save(image)
    return FrameService(art, path, "tv-1", activation_timeout=0.02, poll_interval=0.001), art, image


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
    with pytest.raises(RuntimeError):
        resumed.upload(image)  # Automatically attempts only recovery, never another upload.
    art.select_image.side_effect = None
    assert resumed.upload(image, resume=True) == "MY_new"
    assert art.upload.call_count == 1
    art.delete.assert_called_once_with("MY_old")


def test_delete_failure_preserves_journal(tmp_path):
    service, art, image = setup_upload(tmp_path)
    art.delete.side_effect = None
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


def test_delayed_activation_skips_redundant_artmode_setter(tmp_path):
    service, art, image = setup_upload(tmp_path)
    art.get_current.side_effect = [
        {"content_id": "MY_old", "matte_id": "none"},
        {"content_id": "MY_new", "matte_id": "none"},
        {"content_id": "MY_new", "matte_id": "none"}]
    assert service.upload(image) == "MY_new"
    art.set_artmode.assert_not_called()
    art.select_image.assert_called_once_with("MY_new", show=True)
    art.delete.assert_called_once_with("MY_old")


def test_pending_upload_recovers_without_image_file_or_new_upload(tmp_path):
    service, art, image = setup_upload(tmp_path)
    service.state["pending"] = "MY_new"
    service.save()
    missing = tmp_path / "not-generated.png"
    assert service.upload(missing) == "MY_new"
    assert not missing.exists()
    art.upload.assert_not_called()
    assert service.state["pending"] is None and service.state["previous"] is None
    art.delete.assert_called_once_with("MY_old")


def test_ambiguous_upload_blocks_duplicate_retry(tmp_path):
    service, art, image = setup_upload(tmp_path)
    art.upload.side_effect = RuntimeError("upload timeout")
    with pytest.raises(RuntimeError, match="step=upload"):
        service.upload(image)
    resumed = FrameService(art, service.path, "tv-1")
    with pytest.raises(RuntimeError, match="Upload outcome unknown"):
        resumed.upload(image)
    assert art.upload.call_count == 1
    art.delete.assert_not_called()


def test_artwork_changes_before_delete_preserves_previous(tmp_path):
    service, art, image = setup_upload(tmp_path)
    art.get_current.side_effect = [
        {"content_id": "MY_new", "matte_id": "none"},
        {"content_id": "MY_someone_else", "matte_id": "none"}]
    with pytest.raises(RuntimeError, match="changed before cleanup"):
        service.upload(image)
    art.delete.assert_not_called()
    assert FrameService(art, service.path, "tv-1").state["previous"] == "MY_old"


def test_setter_transport_does_not_wait_for_matching_ack():
    from app.modules.samsung_frame.transport import BoundedArt
    art = BoundedArt("192.0.2.1", timeout=1)
    art._send_art_request = Mock()
    art.select_image("MY_F0038")
    art.set_artmode(True)
    requests = art._send_art_request.call_args_list
    assert requests[0].args[0] == {"request": "select_image", "content_id": "MY_F0038", "show": True}
    assert all(request.kwargs["wait_for_event"] is None for request in requests)


def test_unrelated_frames_cannot_extend_request_deadline(monkeypatch):
    from app.modules.samsung_frame.transport import BoundedArt
    from samsungtvws.art import SamsungTVArt
    from samsungtvws.exceptions import ConnectionFailure
    art = BoundedArt("192.0.2.1", timeout=1)
    art.connection = Mock()
    art.connection.gettimeout.return_value = 60
    monkeypatch.setattr(SamsungTVArt, "_recv_frame", lambda self: ("other", {}))
    monkeypatch.setattr("app.modules.samsung_frame.transport.time.monotonic", Mock(side_effect=[0, 0.5, 2]))
    with pytest.raises(ConnectionFailure, match="deadline exceeded"):
        art._wait_for_d2d(request_uuid="unanswered", wait_for_sub_event=None)
    assert art._request_deadline is None
    art.connection.settimeout.assert_any_call(60)


def test_async_setter_error_preserves_tv_error_code(monkeypatch):
    import json
    from app.modules.samsung_frame.transport import BoundedArt
    from samsungtvws.art import SamsungTVArt
    from samsungtvws.exceptions import ResponseError
    art = BoundedArt("192.0.2.1", timeout=1)
    art.connection = Mock()
    art._setter_requests["setter-id"] = "select_image"
    frame = {"data": json.dumps({"request_id": "setter-id", "event": "error", "error_code": -1})}
    monkeypatch.setattr(SamsungTVArt, "_recv_frame", lambda self: ("d2d_service_message", frame))
    with pytest.raises(ResponseError, match="select_image.*error number -1"):
        art._recv_frame()


def test_commit_activation_failure_keeps_old_and_durable_pending(tmp_path):
    service, art, image = setup_upload(tmp_path)
    save = service.save
    calls = 0
    def fail_activation_commit():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("journal disk full")
        save()
    service.save = fail_activation_commit
    with pytest.raises(RuntimeError, match="commit-activation.*disk full"):
        service.upload(image)
    art.delete.assert_not_called()
    journal = FrameService(art, service.path, "tv-1").state
    assert journal["current"] == "MY_old" and journal["pending"] == "MY_new"


def test_sync_skips_identical_png_after_restart(tmp_path):
    service, art, image = setup_upload(tmp_path)
    assert service.sync(image)=="MY_new"
    resumed=FrameService(art,service.path,"tv-1")
    assert resumed.sync(image)=="MY_new" and resumed.last_action=="unchanged"
    assert art.upload.call_count==1 and art.select_image.call_count==1
    art.delete.assert_called_once_with("MY_old")


def test_sync_changed_png_replaces_only_previous_managed_id(tmp_path):
    service,art,image=setup_upload(tmp_path)
    service.sync(image)
    Image.new("RGB",(3840,2160),"green").save(image)
    art.upload.return_value="MY_newer"
    art.available.return_value.append({"content_id":"MY_newer","matte_id":"none"})
    art.get_current.return_value={"content_id":"MY_newer","matte_id":"none"}
    resumed=FrameService(art,service.path,"tv-1")
    assert resumed.sync(image)=="MY_newer"
    assert art.upload.call_count==2
    assert [call.args[0] for call in art.delete.call_args_list]==["MY_old","MY_new"]
    assert resumed.last_action=="uploaded"


def test_sync_recovers_pending_hash_without_uploading_latest_file(tmp_path):
    import hashlib
    service,art,image=setup_upload(tmp_path)
    digest=hashlib.sha256(image.read_bytes()).hexdigest()
    service.state.update(pending="MY_new",pending_sha256=digest)
    service.save()
    Image.new("RGB",(3840,2160),"green").save(image)
    resumed=FrameService(art,service.path,"tv-1")
    assert resumed.sync(image)=="MY_new" and resumed.last_action=="recovered"
    art.upload.assert_not_called()
    assert resumed.state["current_sha256"]==digest


def test_pre_transfer_failure_is_safely_retryable_on_next_run(tmp_path):
    from app.modules.samsung_frame.transport import RetryableUploadError
    service,art,image=setup_upload(tmp_path)
    art.upload.side_effect=RetryableUploadError("No image bytes sent: connection refused")
    with pytest.raises(RuntimeError,match="connection refused"):
        service.sync(image)
    resumed=FrameService(art,service.path,"tv-1")
    assert resumed.state["upload_attempt"] is None and resumed.state["current"]=="MY_old"
    art.delete.assert_not_called()
    art.upload.side_effect=None
    assert resumed.sync(image)=="MY_new"
    assert art.upload.call_count==2


@pytest.mark.parametrize("failed_write,retryable", [(1,True),(3,False)])
def test_transport_distinguishes_header_failure_from_partial_image(monkeypatch,failed_write,retryable):
    from app.modules.samsung_frame.transport import BoundedArt,RetryableUploadError
    from samsungtvws.art import SamsungTVArt
    art=BoundedArt("192.0.2.1",timeout=1)
    art.get_api_version=Mock(return_value="5.0.1.0")
    art._send_art_request=Mock(return_value={"conn_info":{"ip":"192.0.2.1","port":1234,"key":"test"}})
    socket=Mock()
    socket.sendall.side_effect=[None]*(failed_write-1)+[OSError("connection dropped")]
    monkeypatch.setattr(SamsungTVArt,"_open_d2d_socket",lambda self,info:socket)
    with pytest.raises(RetryableUploadError if retryable else OSError,match="connection dropped"):
        art.upload(b"image bytes",matte="none",portrait_matte="none")
    assert art._upload_started is (not retryable)


def test_export_and_upload_units_use_project_config_and_shifted_persistent_timers():
    units=ROOT/"deploy/systemd"
    for name,module in [("backyard-collage-export","app.modules.avian_collage_exporter"),
                        ("backyard-samsung-frame","app.modules.samsung_frame sync")]:
        service=(units/(name+".service")).read_text()
        timer=(units/(name+".timer")).read_text()
        assert "WorkingDirectory=/home/cpvb86/Backyard" in service
        assert "/home/cpvb86/Backyard/.venv/bin/python -m "+module in service
        assert "EnvironmentFile=" not in service and "/etc/backyard" not in service
        assert "Persistent=true" in timer and "WantedBy=timers.target" in timer
    assert "*:00,15,30,45:00 Europe/Amsterdam" in (units/"backyard-collage-export.timer").read_text()
    assert "*:02,17,32,47:00 Europe/Amsterdam" in (units/"backyard-samsung-frame.timer").read_text()
