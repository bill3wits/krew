#!/usr/bin/env python3
"""Photo → wall ASCII. Built so a face is RECOGNISED, not just textured.



Four things decide whether a face survives the trip down to ~60 columns, and a
naive brightness ramp gets all four wrong:

1. **Crop to the face.** A wide beach selfie at 60 columns spends most of its
   glyphs on sand. We find faces and crop to them, so the whole budget goes on
   the part that carries identity.
2. **Local contrast.** A backlit outdoor shot puts the face in the bottom third
   of the histogram. CLAHE on the L channel pulls the features apart without
   blowing out the sky.
3. **Cell aspect.** A terminal cell is about twice as tall as it is wide, so
   `rows = cols * cell * H/W`. Skip this and everyone comes out squashed —
   the single most common reason ASCII portraits look like nobody.
4. **Dither the binary ramps.** Quadrant and braille glyphs are on/off per
   subcell: thresholding gives a harsh stencil, Floyd–Steinberg gives tone.
   Tone is what the eye reads as a face.

Ramp names match `voiceonly-home-server.py` ART_RAMPS exactly, so whatever this
prints can be posted with the same `art_ramp` and render in the right font.

    # look at it first
    photo-ascii.py ~/Downloads/photo.jpg --cols 64
    photo-ascii.py ~/Downloads/photo.jpg --ramp eighth --compare

    # put it on the wall
    photo-ascii.py ~/Downloads/photo.jpg --post --label "fort lauderdale"

Wall geometry: the piece rides the log roll, ART_MAX_LINES 120 / 12000 chars.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

WALL = os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/")   # the Wall's player (Crissa)

# --- ramps -------------------------------------------------------------------
# Tone ramps map luminance straight to a glyph (1 sample per cell).
# Binary ramps pack sub-cell on/off bits, so they get dithered first.
TONE_RAMPS: dict[str, str] = {
    "ascii": " .:-=+*#%@",
    "shade": " ░▒▓█",
    "eighth": " ▁▂▃▄▅▆▇█",
    "cjk": " 一二三十田囲圖黼",
}
# bit weights: TL=1 TR=2 BL=4 BR=8
QUADRANT = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"
# braille dot bits by (col, row) inside the 2x4 cell
BRAILLE_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
BINARY_RAMPS = {"quadrant": (2, 2), "braille": (2, 4)}
ALL_RAMPS = sorted(set(TONE_RAMPS) | set(BINARY_RAMPS))


def _log(msg: str) -> None:
    print(f"[photo-ascii] {msg}", file=sys.stderr)


# --- face crop ---------------------------------------------------------------
def find_faces(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Face boxes, best detector available. Empty list is a valid answer."""
    try:
        import cv2
    except ImportError:
        _log("cv2 absent — no face crop (run with a python that has OpenCV for that)")
        return []

    bgr = np.array(img.convert("RGB"))[:, :, ::-1]


    # krew uses the haar detector below (no face-ID model weights ship with krew)

    try:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        hits = cascade.detectMultiScale(gray, 1.08, 5, minSize=(60, 60))
        out = [(int(x), int(y), int(x + w), int(y + h)) for x, y, w, h in hits]
        _log(f"haar: {len(out)} face(s)")
        return out
    except Exception as e:
        _log(f"haar failed ({e.__class__.__name__})")
        return []


def crop_to_faces(
    img: Image.Image,
    boxes: list[tuple[int, int, int, int]],
    *,
    pad: float = 0.18,
) -> Image.Image:
    """Union of the face boxes plus headroom, clamped to the frame.

    Padding scales with the MEDIAN FACE, not the union. Measured on a two-face
    selfie: padding off the union added 500px of sand on every side, because
    the union of two faces is most of the frame already. Off the median face it
    keeps hair and chin at any number of faces.
    """
    if not boxes:
        return img
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    sides = sorted(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
    m = int(sides[len(sides) // 2] * pad)
    W, H = img.size
    return img.crop(
        (max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m))
    )


def trim_border(img: Image.Image, *, max_frac: float = 0.08) -> Image.Image:
    """Shave a scanned print's paper border off the edges.

    A scan of a 2004 photograph carries a white paper edge. It is the
    brightest thing in the frame, so it survives every tone stage and lands on
    the wall as a solid bar over the faces. Detect it the way it actually
    presents — an edge line that is near-uniform (tiny variance) and much
    brighter or darker than the picture's median — and stop at max_frac so a
    genuinely pale sky is never eaten.
    """
    a = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    H, W = a.shape
    mid = float(np.median(a))

    def _is_border(line: np.ndarray) -> bool:
        return float(line.std()) < 0.06 and abs(float(line.mean()) - mid) > 0.22

    top, bottom, left, right = 0, H, 0, W
    while top < int(H * max_frac) and _is_border(a[top, left:right]):
        top += 1
    while bottom > H - int(H * max_frac) and _is_border(a[bottom - 1, left:right]):
        bottom -= 1
    while left < int(W * max_frac) and _is_border(a[top:bottom, left]):
        left += 1
    while right > W - int(W * max_frac) and _is_border(a[top:bottom, right - 1]):
        right -= 1
    if (top, left) == (0, 0) and (bottom, right) == (H, W):
        return img
    _log(f"border trimmed: t{top} b{H - bottom} l{left} r{W - right}")
    return img.crop((left, top, right, bottom))


# --- tone --------------------------------------------------------------------
def to_luma(img: Image.Image, *, clahe: bool = True, gamma: float = 1.0,
            eq: float = 0.55) -> np.ndarray:
    """Greyscale 0..1, local contrast applied. This is where faces are won."""
    if clahe:
        try:
            import cv2

            lab = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2LAB)
            # clip 2.5 measured too hot on a face: it lifts skin texture into
            # the same band as the features and the portrait goes chalky. 1.8
            # separates brow/nose/mouth and leaves cheeks smooth.
            c = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
            lab[:, :, 0] = c.apply(lab[:, :, 0])
            rgb = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
            img = Image.fromarray(rgb)
        except Exception:
            pass  # plain greyscale still works, just flatter
    a = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    lo, hi = np.percentile(a, 1.0), np.percentile(a, 99.0)
    if hi > lo:
        a = np.clip((a - lo) / (hi - lo), 0.0, 1.0)

    # Partial histogram equalisation. A linear stretch only fixes the ENDS of
    # the range: a bright low-contrast scan (the 2004 wedding print) still
    # piles the whole face into the top third of the ramp and comes out as a
    # flat slab. Rank-equalising spreads pixels evenly across the glyph levels
    # whatever the exposure was; blending it back with the linear version keeps
    # the result photographic instead of posterised.
    if 0.0 < eq <= 1.0:
        flat = a.ravel()
        order = np.argsort(flat, kind="stable")
        ranks = np.empty_like(order, dtype=np.float32)
        ranks[order] = np.linspace(0.0, 1.0, flat.size, dtype=np.float32)
        a = (1.0 - eq) * a + eq * ranks.reshape(a.shape)

    if gamma and gamma != 1.0:
        a = np.power(a, gamma)
    return a


def resample(a: np.ndarray, w: int, h: int, *, soften: float = 0.0) -> np.ndarray:
    """Downscale to the sample grid, optionally pre-blurred.

    Dithered ramps turn leftover high-frequency detail (hair, skin grain) into
    speckle that competes with the features. A blur tied to the scale factor
    removes what the grid cannot carry anyway.
    """
    im = Image.fromarray((a * 255).astype(np.uint8))
    if soften > 0:
        from PIL import ImageFilter

        r = max(0.0, soften * (a.shape[1] / max(w, 1)) / 4.0)
        if r > 0.3:
            im = im.filter(ImageFilter.GaussianBlur(radius=r))
    return np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float32) / 255.0


def dither(a: np.ndarray) -> np.ndarray:
    """Floyd–Steinberg to 0/1. Tone out of on-off glyphs."""
    a = a.copy()
    h, w = a.shape
    for y in range(h):
        for x in range(w):
            old = a[y, x]
            new = 1.0 if old >= 0.5 else 0.0
            a[y, x] = new
            err = old - new
            if x + 1 < w:
                a[y, x + 1] += err * 7 / 16
            if y + 1 < h:
                if x:
                    a[y + 1, x - 1] += err * 3 / 16
                a[y + 1, x] += err * 5 / 16
                if x + 1 < w:
                    a[y + 1, x + 1] += err * 1 / 16
    return a


# --- render ------------------------------------------------------------------
def render(
    img: Image.Image,
    *,
    ramp: str = "quadrant",
    cols: int = 64,
    cell: float = 0.5,
    invert: bool = False,
    clahe: bool = True,
    gamma: float = 1.0,
    eq: float = 0.55,
) -> str:
    """One image → one block of text in the named ramp."""
    if ramp not in ALL_RAMPS:
        raise ValueError(f"ramp must be one of {ALL_RAMPS}")
    W, H = img.size
    rows = max(1, int(round(cols * cell * H / W)))  # the aspect correction
    a = to_luma(img, clahe=clahe, gamma=gamma, eq=eq)
    if invert:
        a = 1.0 - a

    if ramp in BINARY_RAMPS:
        sx, sy = BINARY_RAMPS[ramp]
        bits = dither(resample(a, cols * sx, rows * sy, soften=1.0))
        lines = []
        for r in range(rows):
            line = []
            for c in range(cols):
                blk = bits[r * sy:(r + 1) * sy, c * sx:(c + 1) * sx]
                if ramp == "quadrant":
                    v = (
                        int(blk[0, 0]) * 1
                        + int(blk[0, 1]) * 2
                        + int(blk[1, 0]) * 4
                        + int(blk[1, 1]) * 8
                    )
                    line.append(QUADRANT[v])
                else:
                    v = 0
                    for cc in range(2):
                        for rr in range(4):
                            if blk[rr, cc]:
                                v |= BRAILLE_BITS[cc][rr]
                    line.append(chr(0x2800 + v))
            lines.append("".join(line).rstrip())
        return "\n".join(lines)

    glyphs = TONE_RAMPS[ramp]
    g = resample(a, cols, rows)
    idx = np.clip((g * (len(glyphs) - 1)).round().astype(int), 0, len(glyphs) - 1)
    return "\n".join("".join(glyphs[i] for i in row).rstrip() for row in idx)


def preview_png(art: str, out: Path, ramp: str = "shade", scale: int = 14) -> Path:
    """Draw the piece the way the wall draws it: ember on dark, real cell ratio.

    Judging ASCII in a terminal is not the same test — the terminal's cell
    ratio, font and colour are all different from the glass. Render it, look at
    it, then decide. (Eye-check the clip, don't trust the schema.)
    """
    from PIL import ImageDraw, ImageFont

    # Font MUST follow the ramp, the same way the page's .ramp-* classes do.
    # Braille only exists in Unifont: previewing it in DejaVu draws a wall of
    # .notdef tofu and you "discover" a rendering bug that is really a font
    # bug. (Cost me one render to relearn what ART_RAMPS already documents.)
    dejavu = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    unifont = "/usr/share/fonts/opentype/unifont/unifont.otf"
    faces = [unifont, dejavu] if ramp == "braille" else [dejavu, unifont]
    font = None
    for f in faces:
        if Path(f).is_file():
            try:
                font = ImageFont.truetype(f, scale)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()
    lines = art.split("\n")
    cw = int(round(font.getlength("█"))) or scale // 2
    ch = int(round(scale * 1.04))  # the page's line-height
    W = max(1, max(len(l) for l in lines)) * cw + 2 * cw
    H = len(lines) * ch + 2 * ch
    im = Image.new("RGB", (W, H), (14, 17, 23))          # --ground
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((cw, ch + i * ch), line, font=font, fill=(217, 160, 91))  # --ember
    im.save(out)
    return out


def post_to_wall(art: str, ramp: str, label: str | None, hold: float | None) -> str:
    # POST /v1/screen, not /v1/action: the latter is a fixed allowlist of panel
    # verbs and 400s on anything else. Screen fields have their own endpoint.
    body: dict = {"art": art, "art_ramp": ramp}
    if label:
        body["art_label"] = label
    if hold is not None:
        body["art_hold"] = hold
    req = urllib.request.Request(
        f"{WALL}/v1/screen",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # A dead wall must read as "did NOT land", not as a crash a caller has to
    # interpret — rendering succeeded, the posting is what failed, and the

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            json.loads(r.read().decode())
        # Report the GLASS, not the POST: read the screen back and prove the
        # piece is the one that landed (display-tools-must-report-the-glass).
        with urllib.request.urlopen(f"{WALL}/v1/screen", timeout=6) as r:
            scr = json.loads(r.read().decode())
        scr = scr.get("screen") or scr      # the player answers {"ok", "screen": {...}}
    except urllib.error.HTTPError as e:
        return f"the player at {WALL} said {e.code} — piece did NOT land"
    except Exception as e:
        return f"Crissa not installed (no player at {WALL}: {e.__class__.__name__}) — piece did NOT land"
    live = scr.get("art") or ""
    if not live:
        return "posted but the screen shows no art — piece did NOT land"
    if live.split("\n")[0] != art.split("\n")[0]:
        return "posted but the screen is showing a DIFFERENT piece"
    return (
        f"on the glass: {len(art.splitlines())} lines, ramp "
        f"{ramp}, label {scr.get('art_label')!r}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("image", type=Path)
    p.add_argument("--ramp", default="quadrant", choices=ALL_RAMPS)
    p.add_argument("--cols", type=int, default=64)
    p.add_argument("--cell", type=float, default=0.5,
                   help="char width/height; 0.5 = twice as tall as wide")
    p.add_argument("--face", default="auto", choices=("auto", "off"))
    p.add_argument("--pad", type=float, default=0.55, help="headroom around faces")
    p.add_argument("--crop", default=None, help="manual x0,y0,x1,y1 (overrides --face)")
    p.add_argument("--gamma", type=float, default=1.0, help=">1 darkens midtones")
    p.add_argument("--eq", type=float, default=0.55,
                   help="0=exposure as shot, 1=full histogram equalisation")
    p.add_argument("--no-clahe", action="store_true")
    p.add_argument("--no-trim", action="store_true",
                   help="keep the scanned paper border")
    p.add_argument("--invert", action="store_true", help="for light-on-dark output")
    p.add_argument("--compare", action="store_true", help="print every ramp")
    p.add_argument("--post", action="store_true")
    p.add_argument("--label", default=None)
    p.add_argument("--hold", type=float, default=None, help="seconds to hold on the roll")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="print a result object instead of the art (for tools)")
    p.add_argument("--preview", type=Path, default=None, help="write a PNG of the piece")
    a = p.parse_args()

    if not a.image.is_file():
        _log(f"no such image: {a.image}")
        return 2
    img = Image.open(a.image)
    try:  # honour phone rotation metadata, or everyone is sideways
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    _log(f"loaded {img.size[0]}x{img.size[1]}")
    if not a.no_trim:
        img = trim_border(img)

    faces_found = 0
    if a.crop:
        x0, y0, x1, y1 = (int(v) for v in a.crop.split(","))
        img = img.crop((x0, y0, x1, y1))
        _log(f"manual crop -> {img.size[0]}x{img.size[1]}")
    elif a.face == "auto":
        boxes = find_faces(img)
        faces_found = len(boxes)
        if boxes:
            img = crop_to_faces(img, boxes, pad=a.pad)
            _log(f"face crop -> {img.size[0]}x{img.size[1]}")
        else:
            _log("no faces found — rendering the whole frame")

    kw = dict(cols=a.cols, cell=a.cell, invert=a.invert,
              clahe=not a.no_clahe, gamma=a.gamma, eq=a.eq)
    if a.compare:
        for r in ALL_RAMPS:
            print(f"\n=== {r} ({a.cols} cols) " + "=" * 30)
            print(render(img, ramp=r, **kw))
        return 0

    art = render(img, ramp=a.ramp, **kw)
    if a.preview:
        preview_png(art, a.preview, ramp=a.ramp)
        _log(f"preview -> {a.preview}")
    glass = post_to_wall(art, a.ramp, a.label, a.hold) if a.post else None
    if a.json_out:
        # For callers (Crissa's tool): the result, not the picture. `glass` is
        # read back off /v1/screen, so it reports what is ON the wall — never
        # just that a POST was accepted.
        print(json.dumps({
            "ok": True,
            "lines": len(art.splitlines()),
            "chars": len(art),
            "ramp": a.ramp,
            "cols": a.cols,
            "faces": faces_found,
            "label": a.label,
            "posted": bool(a.post),
            "glass": glass,
        }))
        return 0
    print(art)
    if glass:
        _log(glass)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
