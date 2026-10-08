"""Run from Backyard project root: python -m app.modules.samsung_frame COMMAND."""
import argparse
import ipaddress
import json
import logging
from pathlib import Path
import sys
from .transport import BoundedArt
from samsungtvws.rest import SamsungTVRest
from .config import load_settings, resolve_path
from .image import generate
from .service import FrameService
from .storage import locked, private_parent, write_private


class PrivateArt(BoundedArt):
    """Persist pairing without the upstream token logging or non-atomic writes."""
    def __init__(self, settings, token_path):
        self.private_token_path = token_path
        private_parent(token_path)
        token = (settings.samsung_frame_token.get_secret_value()
                 if settings.samsung_frame_token else
                 token_path.read_text(encoding="utf-8").strip() if token_path.exists() else None)
        super().__init__(host=settings.samsung_frame_host, port=8002,
                         token=token, timeout=settings.samsung_frame_timeout,
                         name="Backyard Samsung Frame")

    def _set_token(self, token):
        write_private(self.private_token_path, token)
        self.token = token


def main():
    parser = argparse.ArgumentParser(description="Manual Samsung Frame Art Mode")
    parser.add_argument("--environment-file", type=Path,
                        help="Existing production EnvironmentFile (e.g. /etc/backyard/backyard.env)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("connect", help="Test Art API connection and pairing")
    commands.add_parser("generate", help="Generate configured samsung-frame.png")
    upload = commands.add_parser("upload", help="Upload and verify; then delete previous own artwork")
    upload.add_argument("--resume", action="store_true", help="Resume the journal, without another upload")
    commands.add_parser("recover", help="Finish existing pending/cleanup transaction without reading or uploading an image")
    commands.add_parser("status", help="Read journal and live TV status (no artwork changes)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    art = None
    try:
        settings = load_settings(args.environment_file)
        image_path = resolve_path(settings.samsung_frame_image_path)
        if args.command == "generate":
            print(generate(image_path))
            return 0
        ipaddress.ip_address(settings.samsung_frame_host)  # Require an explicit TV IP.
        token_path = resolve_path(settings.samsung_frame_token_path)
        state_path = resolve_path(settings.samsung_frame_state_path)
        if len({image_path.resolve(), token_path.resolve(), state_path.resolve()}) != 3:
            raise ValueError("Image, token and state paths must be different")
        # Upstream debug frames can contain tokens; never enable them here.
        for name in ("samsungtvws", "samsungtvws.connection", "samsungtvws.art.art", "websocket"):
            logging.getLogger(name).disabled = True
        with locked(state_path.with_suffix(".lock")):
            art = PrivateArt(settings, token_path)
            art.open()
            # Bind the deletion journal to the actual TV, not just a reusable DHCP address.
            device = SamsungTVRest(settings.samsung_frame_host, port=8002,
                                   timeout=settings.samsung_frame_timeout).rest_device_info().get("device", {})
            identity = device.get("id")
            if not isinstance(identity, str) or not identity:
                raise RuntimeError("TV did not provide device.id; ownership cannot be verified")
            service = FrameService(art, state_path, identity, activation_timeout=settings.samsung_frame_timeout)
            if args.command == "recover":
                print(json.dumps({"activated": service.recover(), "matte": "none", "uploaded": False}))
            elif args.command == "upload":
                print(json.dumps({"activated": service.upload(image_path, resume=args.resume),
                                  "matte": "none"}))
            else:
                print(json.dumps({"connected": True, "host": settings.samsung_frame_host,
                                  "device_id": identity, "model": device.get("modelName"),
                                  "art_api": art.get_api_version(), "art_mode": art.get_artmode(),
                                  "active": art.get_current(), "managed": service.state,
                                  "token_saved": token_path.exists()}, indent=2))
        return 0
    except Exception as exc:
        # Preserve exact library/TV error, redacting authentication if embedded in it.
        message = str(exc)
        if exc.__cause__:
            message += f"; caused by {type(exc.__cause__).__name__}: {exc.__cause__}"
        secrets = [getattr(art, "token", None)]
        if "settings" in locals() and settings.samsung_frame_token:
            secrets.append(settings.samsung_frame_token.get_secret_value())
        for token in secrets:
            if token:
                message = message.replace(token, "[REDACTED]")
        print(f"{args.command}: {type(exc).__name__}: {message}", file=sys.stderr)
        if args.command == "status" and "state_path" in locals():
            try:
                journal = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
                print(json.dumps({"connected": False, "managed": journal}, indent=2))
            except (OSError, ValueError) as error:
                print(f"Journal read failed: {error}", file=sys.stderr)
        if args.command in ("upload", "recover"):
            print("Transaction retained. Inspect status and run recover; never edit owned IDs or re-upload pending artwork.", file=sys.stderr)
        return 1
    finally:
        if art is not None:
            art.close()


if __name__ == "__main__":
    sys.exit(main())
