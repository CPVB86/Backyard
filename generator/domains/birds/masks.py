"""Unchanged AvianVisitors silhouette algorithm; see PROVENANCE.md."""
import base64
import json
import re
from pathlib import Path
DIM_MAX = 560
MASK_MAX = 93
ALPHA_ON = 127

def build_tables(illus_dir: Path, only=None):
    """Return (dims, masks) dicts keyed by slug, in sorted order.
    `only` (a set of slugs) restricts the scan for incremental --add runs."""
    from PIL import Image
    dims, masks = {}, {}
    pngs = sorted(p for p in illus_dir.glob("*.png")
                  if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", p.stem))
    if only is not None:
        pngs = [p for p in pngs if p.stem in only]
    for p in pngs:
        slug = p.stem
        im = Image.open(p).convert("RGBA")
        w, h = im.size
        scale = DIM_MAX / max(w, h)
        dims[slug] = [round(w * scale), round(h * scale)]

        ms = MASK_MAX / max(w, h)
        mw, mh = max(1, round(w * ms)), max(1, round(h * ms))
        alpha = im.getchannel("A").resize((mw, mh), Image.LANCZOS)
        px = alpha.load()
        bits = bytearray((mw * mh + 7) // 8)
        for y in range(mh):
            for x in range(mw):
                if px[x, y] > ALPHA_ON:
                    i = y * mw + x
                    bits[i >> 3] |= 1 << (7 - (i & 7))
        masks[slug] = {"w": mw, "h": mh, "bits": base64.b64encode(bytes(bits)).decode()}
    return dims, masks

def dump_perkey(table) -> str:
    """Serialize {key: value} as valid JSON with one key per line, sorted.

    A per-key layout keeps a species-add to a single inserted line, so
    independent regional contributions produce non-overlapping diffs
    instead of rewriting one giant line and colliding on every merge.
    json.loads reads it back exactly as a normal object.
    """
    lines = [f"{json.dumps(k)}:{json.dumps(v, separators=(',', ':'))}"
             for k, v in sorted(table.items())]
    return "{\n" + ",\n".join(lines) + "\n}\n"
