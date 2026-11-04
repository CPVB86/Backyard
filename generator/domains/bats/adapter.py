"""Species illustrations for confirmed bats, using the shared Generator worker."""
import hashlib
import os
import re
from uuid import uuid4

from generator.domains.birds import openai_images
from generator.domains.birds.render import save_cutout


class Bats:
    required_assets = ("hanging", "flight")

    def configuration_reason(self):
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key or any(not 33 <= ord(c) <= 126 for c in key):
            return "openai_key_missing_or_invalid"
        return None

    def validate_species(self, name):
        if not re.fullmatch(r"[A-Za-z]{2,40}(?: [a-z]{2,40}){1,3}", name):
            raise ValueError("Invalid bat scientific name")

    def identities(self):
        return ()

    def bundled(self, name):
        return {}

    def valid(self, path, metadata):
        try:
            data = path.read_bytes()
            return data.startswith(bytes.fromhex("89504e470d0a1a0a")) and hashlib.sha256(data).hexdigest() == metadata["sha256"]
        except (OSError, KeyError):
            return False

    def generate(self, scientific_name, common_name, pose, directory, options):
        from PIL import Image
        stance = {"hanging": "roosting upside down, wings folded against its body",
                  "flight": "in flight with both membranous wings extended"}[pose]
        prompt = (f"Natural-history field guide illustration of one {scientific_name}, {stance}. "
                  "Show the complete animal with species-accurate ears, muzzle, fur and wing anatomy. "
                  "Keep the feet and wingtips inside the frame. Transparent background. "
                  "No labels, scenery, branches, shadows or other animals.")
        slug = scientific_name.lower().replace(" ", "-") + "-" + pose
        target = directory / (slug + ".png")
        model = os.environ.get("OPENAI_IMAGE_MODEL") or openai_images.DEFAULT_MODEL
        quality = os.environ.get("OPENAI_IMAGE_QUALITY") or "medium"
        if not target.exists():
            data = openai_images.generate_png(os.environ.get("OPENAI_API_KEY", ""), prompt, [], model, quality)
            raw = directory / "raw"
            raw.mkdir(exist_ok=True)
            (raw / f"{slug}-{uuid4().hex}.png").write_bytes(data)
            (directory / (slug + ".prompt.txt")).write_text(prompt, encoding="utf-8")
            save_cutout(data, target)
        with Image.open(target) as image:
            alpha = image.convert("RGBA").getchannel("A")
            if image.format != "PNG" or alpha.getextrema()[0] or not alpha.getbbox():
                raise ValueError("Invalid bat illustration")
            dimensions = list(image.size)
        data = target.read_bytes()
        return {"file": target.name, "slug": slug, "pose": pose, "asset_type": "illustration",
                "pixel_dimensions": dimensions, "content_type": "image/png", "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "model": model, "quality": quality,
                "source": "Backyard Generator/OpenAI"}
