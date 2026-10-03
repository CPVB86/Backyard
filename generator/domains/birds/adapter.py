"""Bird-specific poses, prompts, reference roles, validation and silhouette data."""
import base64
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re

from . import helpers, masks, openai_images
from .render import prepare_prompt, save_cutout

ASSETS = Path(__file__).with_name("assets")


@lru_cache(maxsize=1)
def catalogue():
    return json.loads((ASSETS / "catalogue.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1024)
def valid_plate(path, modified_ns, size, sha256):
    try:
        data = path.read_bytes()
        return data.startswith(b"\x89PNG\r\n\x1a\n") and hashlib.sha256(data).hexdigest() == sha256
    except OSError:
        return False


class Birds:
    required_assets = ("perched", "flight")

    def identities(self):
        return catalogue().keys()

    def validate_species(self, scientific_name):
        archived = json.loads((ASSETS / "legacy-nonbirds.json").read_text(encoding="utf-8"))
        if scientific_name in archived:
            raise ValueError("Archived non-bird demo asset is not a bird species")
        if not re.fullmatch(r"[A-Za-z]{2,40}(?: [a-z]{2,40}){1,3}", scientific_name):
            raise ValueError("Invalid bird scientific name")

    def valid(self, path, metadata):
        # AvianVisitors local_catalog completeness/range checks, plus content hash.
        try:
            dims, mask = metadata["dimensions"], metadata["mask"]
            if not (isinstance(dims, list) and len(dims) == 2 and all(type(v) is int and 0 < v <= 560 for v in dims)):
                return False
            w, h = mask["w"], mask["h"]
            if not all(type(v) is int and 0 < v <= 93 for v in (w,h)):
                return False
            if len(base64.b64decode(mask["bits"], validate=True)) != (w*h+7)//8:
                return False
            info = path.stat()
            return valid_plate(path, info.st_mtime_ns, info.st_size, metadata["sha256"])
        except (OSError, KeyError, TypeError, ValueError):
            return False

    def bundled(self, scientific_name):
        poses = catalogue().get(scientific_name, {}).get("assets", {})
        return {pose: dict(metadata, path=ASSETS / metadata["file"])
                for pose, metadata in poses.items() if self.valid(ASSETS / metadata["file"], metadata)}

    def generate(self, scientific_name, common_name, pose, directory, options):
        self.validate_species(scientific_name)
        number = self.required_assets.index(pose) + 1
        slug = helpers.slugify(scientific_name) + ("-2" if number == 2 else "")
        target = directory / (slug + ".png")
        references = []
        for key, role in (("reference", "ANATOMY of the target species"),
                          ("anti_reference", "NEGATIVE lookalike; do NOT copy markings")):
            if options.get(key):
                references.append((role, Path(options[key])))
        style = options.get("style_reference") or ASSETS / "bundled" / ("turdus-migratorius" + ("-2" if number == 2 else "") + ".png")
        references.append(("STYLE only", Path(style)))
        for _, path in references:
            if not path.is_file():
                raise ValueError("Reference image does not exist")
        prompt = prepare_prompt(scientific_name, common_name, number, references)
        model = os.environ.get("OPENAI_IMAGE_MODEL") or openai_images.DEFAULT_MODEL
        quality = os.environ.get("OPENAI_IMAGE_QUALITY") or "medium"
        # Recover already-published PNG after a crash without another paid call.
        if not target.exists():
            data = openai_images.generate_png(os.environ.get("OPENAI_API_KEY", ""), prompt,
                                             [path for _, path in references], model, quality)
            raw = directory / "raw"
            raw.mkdir(exist_ok=True)
            # Preserve earlier raw responses on an explicitly requested retry.
            import uuid
            (raw / (slug + "-" + uuid.uuid4().hex + ".png")).write_bytes(data)
            (directory / (slug + ".prompt.txt")).write_text(prompt, encoding="utf-8")
            save_cutout(data, target)
        dims, silhouettes = masks.build_tables(directory, only={slug})
        from PIL import Image
        with Image.open(target) as image:
            pixel_dimensions = list(image.size)
            if image.convert("RGBA").getchannel("A").getextrema()[0] > 0 or not image.convert("RGBA").getchannel("A").getbbox():
                raise ValueError("Existing output has no transparent background or visible subject")
        data = target.read_bytes()
        return {"asset_type":"illustration", "pose":pose, "file": target.name, "slug":slug, "dimensions":dims[slug], "mask":silhouettes[slug],
                "pixel_dimensions":pixel_dimensions, "sha256":hashlib.sha256(data).hexdigest(),
                "size_bytes":len(data), "content_type":"image/png", "source":"Backyard Generator/OpenAI",
                "model":model, "quality":quality, "prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
                "references":[{"role":role,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()} for role,path in references]}
