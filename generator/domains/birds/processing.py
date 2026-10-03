"""Original AvianVisitors cream-background processing; optional offline dependencies."""

from pathlib import Path

CREAM_TOL = 11

PLUMAGE = 18

def chroma_cut(src: Path, dst: Path) -> None:
    """Instant cutout for a cream-ground render: everything reachable from
    the border through near-paper pixels is background; everything else is
    bird. Enclosed pale patches (a white belly) are unreachable, so they
    stay opaque - the same property the BiRefNet pipeline's fill step has
    to reconstruct. Edges get a light feather instead of a learned matte.

    The paper tolerance adapts per image: the model renders real grain
    and a slight vignette, so the ground's distance-from-corner-paper
    varies render to render (a fixed tolerance either strands ground or
    eats pale plumage). The border strips are guaranteed paper - their
    99th percentile, widened, clears grain and vignette while staying
    far below the inked outline."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.open(src).convert("RGB")
    arr = np.asarray(im)
    h, w, _ = arr.shape
    corners = np.concatenate([arr[:15, :15].reshape(-1, 3), arr[:15, -15:].reshape(-1, 3),
                              arr[-15:, :15].reshape(-1, 3), arr[-15:, -15:].reshape(-1, 3)])
    paper = np.median(corners, axis=0)
    dist = np.sqrt(((arr.astype(np.int32) - paper) ** 2).sum(2))
    border = np.concatenate([dist[:40, :].ravel(), dist[-40:, :].ravel(),
                             dist[:, :40].ravel(), dist[:, -40:].ravel()])
    tol = float(min(40.0, max(15.0, 2.5 * np.percentile(border, 99))))
    passable = dist < tol

    # Flood the passable region from the border (ImageDraw.floodfill runs
    # at C speed; seeds cover each edge in case a wing splits the margin).
    # The .copy() is load-bearing: floodfill's writes are lost on a
    # numpy-buffer-backed image (observed on Pillow 12.3).
    m = Image.fromarray(np.where(passable, 128, 0).astype(np.uint8)).copy()
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
             (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for s in seeds:
        if m.getpixel(s) == 128:
            ImageDraw.floodfill(m, s, 255)
    exterior = np.asarray(m) == 255
    if exterior.mean() < 0.5:
        raise RuntimeError(f"cutout flood failed (tol {tol:.0f}, "
                           f"exterior {100 * exterior.mean():.0f}%) - raw kept for the upgrade pass")

    solid = ~exterior
    # Opening (erode then dilate) drops stray grain specks the flood
    # couldn't reach without nibbling the bird.
    sm = Image.fromarray(solid.astype(np.uint8) * 255)
    sm = sm.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    solid = np.asarray(sm) > 127
    binary = solid.astype(np.uint8) * 255
    # Feather: soften the silhouette edge, keep the interior fully opaque,
    # never bleed outside the silhouette.
    af = np.asarray(Image.fromarray(binary).filter(ImageFilter.GaussianBlur(0.8))).copy()
    af[~solid] = 0

    rgba = np.dstack([arr, af]).astype(np.uint8)
    fg = af > 40
    ys, xs = np.where(fg)
    if not len(ys):
        raise RuntimeError("cutout produced an empty image (bad ground?)")
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    pad = round(0.03 * max(y1 - y0, x1 - x0))
    y0 = max(0, y0 - pad); x0 = max(0, x0 - pad)
    y1 = min(h, y1 + pad); x1 = min(w, x1 + pad)
    Image.fromarray(rgba[y0:y1, x0:x1], "RGBA").save(dst)

def birefnet_cut(src: Path, dst: Path, sess) -> None:
    """BiRefNet matte + exterior-cream peel + belly fill. A filled region
    whose boundary is mostly not plumage is a between-legs pocket, not a
    belly, and is rejected. Same approach the bundled set was cut with."""
    import numpy as np
    from PIL import Image, ImageFilter
    from rembg import remove
    from scipy import ndimage

    im = Image.open(src).convert("RGB")
    arr = np.asarray(im)
    h, w, _ = arr.shape
    a = np.asarray(remove(im, session=sess))[:, :, 3]
    corners = np.concatenate([arr[:15, :15].reshape(-1, 3), arr[:15, -15:].reshape(-1, 3),
                              arr[-15:, :15].reshape(-1, 3), arr[-15:, -15:].reshape(-1, 3)])
    paper = np.median(corners, axis=0)
    dist = np.sqrt(((arr - paper) ** 2).sum(2))
    passable = (a < 100) | (dist < CREAM_TOL)
    lbl, _n = ndimage.label(passable)
    border = set(lbl[0, :]) | set(lbl[-1, :]) | set(lbl[:, 0]) | set(lbl[:, -1])
    border.discard(0)
    exterior = np.isin(lbl, list(border))
    base = (a >= 100) & ~exterior
    solid = ndimage.binary_fill_holes(base)
    added = solid & ~base
    plumage = (a >= 100) & (dist > PLUMAGE)
    al, an = ndimage.label(added)
    reject = np.zeros_like(solid)
    for i in range(1, an + 1):
        C = al == i
        ring = ndimage.binary_dilation(C, iterations=3) & ~C & ~exterior
        if ring.sum() == 0 or plumage[ring].mean() < 0.30:
            reject |= C
    solid = solid & ~reject
    L, m = ndimage.label(solid)
    if m > 1:
        sizes = ndimage.sum(np.ones_like(L), L, range(1, m + 1))
        solid = (L == int(np.argmax(sizes)) + 1)
    inside = ndimage.binary_erosion(solid, iterations=2)
    af = a.copy(); af[inside] = 255; af[~solid] = 0
    af = np.maximum(np.asarray(Image.fromarray(af).filter(ImageFilter.GaussianBlur(0.4))),
                    (inside * 255).astype(np.uint8))
    af[~solid] = 0
    rgba = np.dstack([arr, af]).astype(np.uint8)
    fg = af > 40
    ys, xs = np.where(fg)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    pad = round(0.03 * max(y1 - y0, x1 - x0))
    y0 = max(0, y0 - pad); x0 = max(0, x0 - pad)
    y1 = min(h, y1 + pad); x1 = min(w, x1 + pad)
    Image.fromarray(rgba[y0:y1, x0:x1], "RGBA").save(dst)
