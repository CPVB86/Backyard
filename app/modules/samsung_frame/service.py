"""Fail-closed upload journal: activate and verify before deleting one owned ID."""
import io
import json
import re
from pathlib import Path
from PIL import Image
from .storage import save_state


def owned_id(value):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"MY_[A-Za-z0-9_-]+", value)):
        raise ValueError(f"Invalid personal artwork ID: {value!r}")
    return value


class FrameService:
    def __init__(self, art, state_path: Path, identity: str):
        self.art = art
        self.path = state_path
        self.state = {"version": 1, "tv": identity, "current": None,
                      "pending": None, "previous": None}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if set(state) != set(self.state) or state["version"] != 1 or state["tv"] != identity:
                raise ValueError("Artwork journal invalid or belongs to a different TV; refusing upload/deletion")
            for key in ("current", "pending", "previous"):
                owned_id(state[key])
            ids = [state[k] for k in ("current", "pending", "previous") if state[k]]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate IDs in artwork journal")
            self.state = state

    def save(self):
        save_state(self.path, self.state)

    def verify_matte(self, content_id):
        entries = self.art.available()
        item = next((entry for entry in entries if entry.get("content_id") == content_id), None)
        if item is None or item.get("matte_id") != "none":
            raise RuntimeError(f"matte=none verification failed for {content_id}: {item!r}")

    def activate(self, content_id):
        self.verify_matte(content_id)
        self.art.set_artmode(True)
        self.art.select_image(content_id, show=True)
        current = self.art.get_current()
        if current.get("content_id") != content_id:
            raise RuntimeError(f"Selection verification failed: {current!r}")
        if current.get("matte_id") != "none":
            raise RuntimeError(f"Active matte=none verification failed: {current!r}")
        if self.art.get_artmode() != "on":
            raise RuntimeError("Art Mode did not report 'on' after selection")

    def upload(self, image_path: Path, *, resume=False):
        if resume:
            if not (self.state["pending"] or self.state["previous"]):
                raise ValueError("No interrupted upload to resume")
        else:
            if self.state["pending"] or self.state["previous"]:
                raise ValueError("Interrupted upload in journal; inspect status, then use upload --resume")
            # Read once: a later producer may atomically replace the image while uploading.
            data = image_path.read_bytes()
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or image.size != (3840, 2160):
                    raise ValueError("Expected a PNG of exactly 3840 x 2160 pixels")
                image.verify()
            content_id = owned_id(self.art.upload(data, file_type="png", matte="none", portrait_matte="none"))
            if not content_id or content_id in (self.state["current"], self.state["previous"]):
                raise RuntimeError(f"Upload did not return a new personal content ID: {content_id!r}")
            self.state["pending"] = content_id
            self.save()  # Persist ownership before any activation or cleanup.
        candidate = self.state["pending"] or self.state["current"]
        self.activate(candidate)
        if self.state["pending"]:
            self.state["previous"] = self.state["current"]
            self.state["current"] = candidate
            self.state["pending"] = None
            self.save()  # If this fails, no deletion occurs.
        previous = self.state["previous"]
        if previous:
            # Only the exact previous journal ID can ever be deleted.
            if any(item.get("content_id") == previous for item in self.art.available()):
                if self.art.delete(previous) is not True:
                    raise RuntimeError(f"Deletion not confirmed for previous managed artwork {previous}")
            self.state["previous"] = None
            self.save()
        return candidate
