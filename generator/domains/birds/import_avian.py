"""Explicit offline import of AvianVisitors illustration/table pairs. Never generates."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from PIL import Image
from .helpers import slugify
from .masks import build_tables


def import_assets(source, destination):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    names = json.loads((source / "model/l18n/labels_en.json").read_text(encoding="utf-8"))
    for labels in sorted((source / "model").glob("*Labels.txt")):
        for line in labels.read_text(encoding="utf-8").splitlines():
            scientific, separator, common = line.partition("_")
            if separator:
                names.setdefault(scientific, common)
    slugs = {slugify(name): name for name in names}
    state_path = source / ".avian/auto-image-generation.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for name, entry in state["species"].items():
            if name != "*":
                slugs[slugify(name)] = name
                names.setdefault(name, entry.get("common_name", name))
    catalogue = {}
    imported = []
    for layer, art, tables in (
        ("bundled", source / "avian/assets/illustrations", source / "avian/frontend"),
        ("local", source / ".avian/illustrations", source / ".avian/frontend"),
        ("cutouts", source / "avian/assets/cutouts", None),
        ("sketches", source / "avian/assets/sketches", None),
    ):
        if not art.exists():
            continue
        if tables is None:
            dims, masks = build_tables(art)
        else:
            dims = json.loads((tables / "dims.json").read_text(encoding="utf-8"))
            masks = json.loads((tables / "masks.json").read_text(encoding="utf-8"))
        target = destination / layer
        target.mkdir(exist_ok=True)
        for image in sorted(art.glob("*.png")):
            slug = image.stem
            base = slug.removesuffix("-2")
            if base not in slugs:
                # Legacy illustrations can outlive their label catalogue entry.
                # The existing binomial filename is reversible; no invented translation.
                scientific = base.replace("-", " ").capitalize()
                slugs[base] = scientific
                names[scientific] = scientific
            if slug not in dims or slug not in masks:
                raise ValueError(f"Missing dimensions/mask for {slug}")
            with Image.open(image) as im:
                im.verify()
            with Image.open(image) as im:
                size = list(im.size)
            data = image.read_bytes()
            output = target / image.name
            if output.exists() and output.read_bytes() != data:
                raise ValueError(f"Refusing to overwrite changed imported asset: {output}")
            if not output.exists():
                shutil.copyfile(image, output)
            sci = slugs[base]
            entry = catalogue.setdefault(sci, {"common_name": names[sci], "assets": {}})
            pose = "flight" if slug.endswith("-2") else "perched"
            asset_id = "photo_cutout" if layer == "cutouts" else "sketch_" + pose if layer == "sketches" else pose
            asset_type = "photo_cutout" if layer == "cutouts" else "legacy_sketch" if layer == "sketches" else "illustration"
            entry["assets"][asset_id] = {"asset_type": asset_type, "pose": pose,"file": f"{layer}/{image.name}", "slug": slug,
                "dimensions": dims[slug], "mask": masks[slug], "pixel_dimensions": size,
                "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
                "source": f"AvianVisitors/{layer}", "content_type": "image/png"}
            imported.append({"file": f"{layer}/{image.name}", "sha256": hashlib.sha256(data).hexdigest()})
        # Preserve exact source tables, including metadata not reconstructed here.
        for table in ("dims.json", "masks.json"):
            if tables is not None:
                shutil.copyfile(tables / table, target / table)
            else:
                (target / table).write_text(json.dumps(dims if table == "dims.json" else masks))
        raw = art / "raw"
        if layer == "local" and raw.exists():
            shutil.copytree(raw, target / "raw", dirs_exist_ok=True)
    refs = source / "avian/assets/references"
    if refs.exists():
        shutil.copytree(refs, destination / "references", dirs_exist_ok=True)
    if state_path.exists():
        shutil.copyfile(state_path, destination / "imported-generation-history.json")
    # Source demo generated this known non-bird using its bird prompt. Preserve
    # the original files/metadata without exposing it as a Backyard bird species.
    archived = {name: catalogue.pop(name) for name in ("Phoca vitulina",) if name in catalogue}
    for name, data in (("catalogue.json", catalogue), ("imported-files.json", imported),
                       ("legacy-nonbirds.json", archived)):
        (destination / name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Imported {len(imported)} PNGs; {len(catalogue)} species; source tables and local raw/history preserved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--destination", type=Path, default=Path(__file__).with_name("assets"))
    args = parser.parse_args()
    import_assets(args.source, args.destination)
