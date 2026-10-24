#!/usr/bin/env python3
"""img2glyph.py — turn a picture into ASCII in the crew's Glyph Density style.

    img2glyph.py photo.jpg                          # the crew's style, 90 cols
    img2glyph.py photo.jpg --ramp braille --cols 70 # most detail
    img2glyph.py photo.jpg --post --label "night watch"

Three different things are going on depending on the ramp, and the difference
matters more than any knob here:

  TONE ramps (glyph, ascii, shade, eighth, cjk)
      One sample per cell, mapped to a glyph by how much ink it carries.
      Resolution = the character grid.

  SUB-CELL ramps (quadrant 2x2, braille 2x4)
      The image is dithered to pure black and white at 2x or 4x the grid, then
      the dots are PACKED into one glyph per cell. Resolution is 2-8x the grid,
      which is the whole reason to use them.

Ink per cell was measured on this box (2026-08-13), not guessed:

    ramp      ink range   resolution  note
    glyph     0.00-0.27   1x1         the crew's style: white figure, dark wash
    ascii     0.00-0.27   1x1         renders in any font
    cjk       0.00-0.30   1x1         full-width texture, NOT denser than ascii
    shade     0.00-1.00   1x1         flat tone, 4 steps
    eighth    0.00-1.00   1x1         best tonal ladder here, 9 linear steps
    quadrant  0.00-1.00   2x2         detail AND full black — best all-round
    braille   0.06-0.25   2x4         most detail available, cannot go dark

The headline finding: CJK is NOT denser than ASCII (darkest CJK 0.299 vs ASCII
M 0.271). Full-width glyphs spread their ink over two cells. If you want weight,
reach for the block ramps; if you want detail, reach for braille or quadrant.

Braille renders ONLY in Unifont. DejaVu Sans Mono and Noto CJK draw .notdef
boxes for it — that is where a wall of tofu comes from. Posting with
--ramp braille sets art_ramp so the page picks the right font.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("needs Pillow:  python3 -m pip install --user Pillow")

SCREEN_URL = os.environ.get("ZOEYOS_PLAYER", "http://127.0.0.1:8100").rstrip("/") + "/v1/screen"   # the Wall's player (Crissa)

# Monotonic by measured ink. The crew's style keeps the low end sparse so the
# dark wash stays dark and the figure is what carries weight.
RAMPS = {
    "glyph":  " ··+*#@",
    "ascii":  " .:;*=x%X&#M",
    "shade":  " ░▒▓█",
    "eighth": " ▁▂▃▄▅▆▇█",
    "cjk":    "　一二三王田噩黂",
}
SUBCELL = {"quadrant": (2, 2), "braille": (2, 4)}

# Cell width as a fraction of the line box, per ramp — what keeps a face from
# coming out stretched. From the same measurement pass.
CELL_ASPECT = {
    "glyph": 0.58, "ascii": 0.58, "shade": 0.58, "eighth": 0.58,
    "quadrant": 0.58, "braille": 0.48, "cjk": 0.98,
}

# 2x2 quadrant blocks indexed by bitmask TL=1 TR=2 BL=4 BR=8.
QUADRANT = [
    " ", "▘", "▝", "▀", "▖", "▌", "▞", "▛",
    "▗", "▚", "▐", "▜", "▄", "▙", "▟", "█",
]
# Braille dot bits by (row, col) in the 2x4 sub-grid.
BRAILLE_BITS = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]


def prepare(path, cols, ramp, gamma, clip, invert):
    """Grayscale, aspect-corrected to the character grid, levels stretched."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        # Flatten onto black: the style lives on a dark ground, and a
        # transparent background otherwise reads as a bright slab.
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("L")

    sx, sy = SUBCELL.get(ramp, (1, 1))
    aspect = CELL_ASPECT.get(ramp, 0.58)
    rows = max(1, round(cols * (im.height / im.width) * aspect))
    im = im.resize((cols * sx, rows * sy), Image.LANCZOS)

    # Auto-levels. A dim photo otherwise collapses into one or two glyphs and
    # reads as banding rather than as a picture.
    if clip > 0:
        lo, hi = percentiles(im, clip)
        if hi > lo:
            im = im.point(lambda v: max(0, min(255, int((v - lo) * 255 / (hi - lo)))))
    if gamma != 1.0:
        inv = 1.0 / gamma
        im = im.point(lambda v: int(255 * ((v / 255) ** inv)))
    if invert:
        im = ImageOps.invert(im)
    return im, rows


def percentiles(im, clip):
    h = im.histogram()
    total = sum(h)
    lo_target, hi_target = total * clip / 100.0, total * (1 - clip / 100.0)
    acc, lo, hi = 0, 0, 255
    for v, n in enumerate(h):
        acc += n
        if acc >= lo_target:
            lo = v
            break
    acc = 0
    for v, n in enumerate(h):
        acc += n
        if acc >= hi_target:
            hi = v
            break
    return lo, hi


def render_tone(im, cols, rows, ramp, ground):
    """One sample per cell, mapped by ink."""
    glyphs = RAMPS[ramp]
    px = im.load()
    out = []
    for y in range(rows):
        line = []
        for x in range(cols):
            v = px[x, y] / 255.0
            idx = min(len(glyphs) - 1, int(v * len(glyphs)))
            ch = glyphs[idx]
            # The dark wash is not empty. A sparse lattice of dots in the
            # shadows is what makes the figure read as lit rather than cut out.
            if ground and idx == 0 and (x + y) % ground == 0:
                ch = "·"
            line.append(ch)
        out.append("".join(line).rstrip())
    return "\n".join(out)


def render_subcell(im, cols, rows, ramp):
    """Dither to pure black/white at sub-cell resolution, then pack the dots."""
    sx, sy = SUBCELL[ramp]
    bw = im.convert("1")            # Floyd-Steinberg, and it is the good part
    px = bw.load()
    out = []
    for y in range(rows):
        line = []
        for x in range(cols):
            bits = 0
            for dy in range(sy):
                for dx in range(sx):
                    if px[x * sx + dx, y * sy + dy]:
                        if ramp == "braille":
                            bits |= BRAILLE_BITS[dy][dx]
                        else:
                            bits |= 1 << (dy * sx + dx)
            line.append(chr(0x2800 + bits) if ramp == "braille" else QUADRANT[bits])
        out.append("".join(line).rstrip())
    return "\n".join(out)


def post(art, ramp, label, hold):
    body = {"art": art, "art_ramp": ramp, "art_hold": hold}
    if label:
        body["art_label"] = label
    req = urllib.request.Request(SCREEN_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--ramp", default="glyph",
                    choices=list(RAMPS) + list(SUBCELL))
    ap.add_argument("--cols", type=int, default=90,
                    help="character columns (default 90). The roll scales the "
                         "piece to the column, so this is detail, not size.")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help=">1 lifts midtones, <1 deepens them")
    ap.add_argument("--clip", type=float, default=2.0,
                    help="percent clipped off each end for auto-levels; 0 = off")
    ap.add_argument("--invert", action="store_true",
                    help="for a dark subject on a light ground")
    ap.add_argument("--ground", type=int, default=0, metavar="N",
                    help="dot lattice in the shadows, 1 dot per N cells "
                         "(the crew's style: 2). Tone ramps only.")
    ap.add_argument("--out", help="write here instead of stdout")
    ap.add_argument("--post", action="store_true", help="send it to the wall roll")
    ap.add_argument("--label", default="", help="kicker shown above the piece")
    ap.add_argument("--hold", type=float, default=90,
                    help="seconds to hold it still. Any spoken line ends the "
                         "hold early — that is correct, the room outranks the art.")
    a = ap.parse_args()

    im, rows = prepare(a.image, a.cols, a.ramp, a.gamma, a.clip, a.invert)
    if a.ramp in SUBCELL:
        art = render_subcell(im, a.cols, rows, a.ramp)
    else:
        art = render_tone(im, a.cols, rows, a.ramp, a.ground)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(art + "\n")
        print(f"wrote {a.out}  ({a.cols}x{rows} cells, ramp={a.ramp})", file=sys.stderr)
    elif not a.post:
        print(art)

    if a.post:
        r = post(art, a.ramp, a.label, a.hold)
        if r.get("ok"):
            print(f"posted to the roll ({a.cols}x{rows}, ramp={a.ramp}, "
                  f"rev={r['screen'].get('rev')})", file=sys.stderr)
        else:
            sys.exit(f"post refused: {r.get('error')}")


if __name__ == "__main__":
    main()
