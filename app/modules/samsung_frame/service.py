"""Fail-closed upload journal: verify selection before deleting one owned ID."""
import io
import hashlib
import logging
import time
import json
import re
from pathlib import Path
from PIL import Image
from .storage import save_state
from .transport import RetryableUploadError

_LOG = logging.getLogger(__name__)


def owned_id(value):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"MY_[A-Za-z0-9_-]+", value)):
        raise ValueError(f"Invalid personal artwork ID: {value!r}")
    return value


class FrameService:
    def __init__(self, art, state_path: Path, identity: str, *, activation_timeout=60, poll_interval=1):
        self.last_action = None
        self.presentation = None
        self.art = art
        self.activation_timeout = activation_timeout
        self.poll_interval = poll_interval
        self.path = state_path
        self.state = {"version": 1, "tv": identity, "current": None,
                      "pending": None, "previous": None, "upload_attempt": None,
                      "current_sha256": None, "pending_sha256": None}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.setdefault("current_sha256", None)
            state.setdefault("pending_sha256", None)
            state.setdefault("upload_attempt", None)  # Read existing version-1 journals.
            if set(state) != set(self.state) or state["version"] != 1 or state["tv"] != identity:
                raise ValueError("Artwork journal invalid or belongs to a different TV; refusing upload/deletion")
            for key in ("current", "pending", "previous"):
                owned_id(state[key])
            ids = [state[k] for k in ("current", "pending", "previous") if state[k]]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate IDs in artwork journal")
            attempt = state["upload_attempt"]
            if attempt is not None and (not isinstance(attempt, dict) or set(attempt) != {"sha256"}
                                        or not isinstance(attempt["sha256"], str)
                                        or not re.fullmatch(r"[a-f0-9]{64}", attempt["sha256"])):
                raise ValueError("Invalid upload attempt in artwork journal")
            if state["pending"] and state["previous"]:
                raise ValueError("Conflicting pending and previous IDs in artwork journal")
            for key in ("current_sha256", "pending_sha256"):
                digest = state[key]
                if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                    raise ValueError("Invalid image SHA-256 in artwork journal")
            self.state = state

    def save(self):
        save_state(self.path, self.state)

    def step(self, name, action, *args, **kwargs):
        _LOG.info("Samsung step=%s current=%s pending=%s previous=%s", name,
                  self.state["current"], self.state["pending"], self.state["previous"])
        try:
            return action(*args, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"Samsung step={name} failed: {type(exc).__name__}: {exc}") from exc

    def verify_matte(self, content_id):
        entries = self.art.available()
        item = next((entry for entry in entries if entry.get("content_id") == content_id), None)
        if item is None or item.get("matte_id") != "none":
            raise RuntimeError(f"matte=none verification failed for {content_id}: {item!r}")

    def activate(self, content_id):
        self.step("verify-matte", self.verify_matte, content_id)
        mode = self.step("read-artmode", self.art.get_artmode)
        if mode not in ("on", "off"):
            raise RuntimeError(f"Unknown Art Mode status; refusing selection: {mode!r}")
        # show=False selects the next Art Mode artwork without forcing display.
        # Use it even when mode is on: a user can start watching between the
        # getter and setter. Never send a power/mode command or show=True.
        _LOG.info("Samsung selecting content_id=%s show=False art_mode=%s", content_id, mode)
        self.step("select", self.art.select_image, content_id, show=False)
        deadline = time.monotonic() + self.activation_timeout
        current = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Activation deadline exceeded for {content_id}; active={current!r}; art_mode={mode!r}")
            # Bound each getter as well as the overall polling window.
            old_timeout = self.art.timeout
            self.art.timeout = min(remaining, 5)
            try:
                current = self.step("verify-active", self.art.get_current)
                if current.get("content_id") == content_id:
                    if current.get("matte_id") != "none":
                        raise RuntimeError(f"Active matte=none verification failed: {current!r}")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(f"Activation deadline exceeded before Art Mode confirmation for {content_id}")
                    self.art.timeout = min(remaining, 5)
                    mode = self.step("verify-artmode", self.art.get_artmode)
                    if mode not in ("on", "off"):
                        raise RuntimeError(f"Unknown Art Mode status after selection: {mode!r}")
                    self.presentation = "art" if mode == "on" else "background"
                    _LOG.info("Samsung selection confirmed content_id=%s matte=none presentation=%s",
                              content_id, self.presentation)
                    return
            finally:
                self.art.timeout = old_timeout
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))

    def recover(self):
        if self.state["upload_attempt"]:
            raise RuntimeError("Upload outcome unknown: no content ID was durably recorded. Refusing another upload; inspect TV and journal before manual reconciliation")
        candidate = self.state["pending"] or self.state["current"]
        if not candidate:
            raise ValueError("No managed artwork to recover")
        _LOG.info("Samsung recovering existing content_id=%s; no file read or upload", candidate)
        self.activate(candidate)
        if self.state["pending"]:
            self.state["previous"] = self.state["current"]
            self.state["current"] = candidate
            self.state["current_sha256"] = self.state["pending_sha256"]
            self.state["pending_sha256"] = None
            self.state["pending"] = None
            self.step("commit-activation", self.save)  # No deletion if persistence fails.
        previous = self.state["previous"]
        if previous:
            # Recheck immediately before deleting exactly the previous journal ID.
            active = self.step("recheck-before-delete", self.art.get_current)
            if active.get("content_id") != candidate or active.get("matte_id") != "none":
                raise RuntimeError(f"Active artwork changed before cleanup: {active!r}")
            mode = self.step("recheck-artmode-before-delete", self.art.get_artmode)
            if mode not in ("on", "off"):
                raise RuntimeError(f"Unknown Art Mode status before cleanup: {mode!r}; previous artwork retained")
            entries = self.step("list-before-delete", self.art.available)
            if any(item.get("content_id") == previous for item in entries):
                if self.step("delete-previous", self.art.delete, previous) is not True:
                    raise RuntimeError(f"Deletion not confirmed for previous managed artwork {previous}")
                if any(item.get("content_id") == previous for item in self.step("verify-delete", self.art.available)):
                    raise RuntimeError(f"Previous artwork still present after deletion: {previous}")
            self.state["previous"] = None
            self.step("commit-cleanup", self.save)
        self.last_action = "recovered"
        _LOG.info("Samsung transaction complete current=%s", candidate)
        return candidate

    def upload(self, image_path: Path, *, resume=False, only_changed=False):
        # Never begin another upload while a known transaction needs recovery.
        if resume or self.state["pending"] or self.state["previous"] or self.state["upload_attempt"]:
            return self.recover()
        data = image_path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (3840, 2160):
                raise ValueError("Expected a PNG of exactly 3840 x 2160 pixels")
            image.verify()
        digest = hashlib.sha256(data).hexdigest()
        if only_changed and self.state["current"] and self.state["current_sha256"] == digest:
            self.last_action = "unchanged"
            _LOG.info("Samsung PNG unchanged; skip upload content_id=%s sha256=%s", self.state["current"], digest)
            return self.state["current"]
        # Persist intent before network I/O. Ambiguous failure blocks duplicate retry.
        self.state["upload_attempt"] = {"sha256": digest}
        self.step("record-upload-intent", self.save)
        try:
            content_id = owned_id(self.step("upload", self.art.upload, data,
                                           file_type="png", matte="none", portrait_matte="none"))
        except RuntimeError as exc:
            if isinstance(exc.__cause__, RetryableUploadError):
                self.state["upload_attempt"] = None
                self.step("record-safe-retry", self.save)
                _LOG.info("Samsung upload failed before image transfer; next timer run may retry")
            raise
        if not content_id or content_id in (self.state["current"], self.state["previous"]):
            raise RuntimeError(f"Upload did not return a new personal content ID: {content_id!r}")
        self.state["pending"] = content_id
        self.state["pending_sha256"] = digest
        self.state["upload_attempt"] = None
        self.step("record-pending", self.save)
        result = self.recover()
        self.last_action = "uploaded"
        return result

    def sync(self, image_path: Path):
        """One timer run: recover first or upload changed bytes; never both."""
        return self.upload(image_path, only_changed=True)
