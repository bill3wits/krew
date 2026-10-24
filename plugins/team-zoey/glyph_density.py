#!/usr/bin/env python3
"""
Glyph Density Rendering Tool
=============================

Takes a subject description (e.g. "mountain", "dragon", "forest") and outputs
dense ASCII/glyph art using the character set:  +  *  #  @

Aesthetic: white-on-black terminal, high density, block-fill style.
The rendering uses multi-pass distance-field layering so dense regions
use heavier glyphs (@ > # > * > +) and sparse edges fade out.

Usage:
    python3 glyph_density.py <subject> [--width WIDTH] [--height HEIGHT]

Examples:
    python3 glyph_density.py skull
    python3 glyph_density.py mountain --width 60 --height 30
    python3 glyph_density.py "spiral galaxy"

Self-contained — no external dependencies beyond stdlib.
"""

import argparse
import math
import random
import re
import sys

# Glyph density palette (lightest to heaviest)
GLYPHS = [" ", "+", "*", "#", "@"]
GLYPH_COUNT = len(GLYPHS)

# ── built-in shape generators ────────────────────────────────────────────────
# Each generator returns a 2D grid of float density values (0.0–1.0).

def _grid(w, h, default=0.0):
    return [[default] * w for _ in range(h)]


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _dist(x, y, cx, cy):
    return math.sqrt((x - cx) ** 2 + (y - cy) ** 2)


def generate_circle(width, height, radius_factor=0.38):
    """Solid circle with smooth falloff edges."""
    w, h = width, height
    cx, cy = w // 2, h // 2
    r = min(w, h) * radius_factor
    grid = _grid(w, h)
    for y in range(h):
        for x in range(w):
            d = _dist(x, y, cx, cy) / r
            grid[y][x] = _clamp(1.0 - d * 0.6)
    return grid


def generate_ring(width, height, radius_factor=0.35, thickness=0.22):
    """Hollow ring / donut."""
    w, h = width, height
    cx, cy = w // 2, h // 2
    r = min(w, h) * radius_factor
    grid = _grid(w, h)
    for y in range(h):
        for x in range(w):
            d = _dist(x, y, cx, cy) / r
            diff = abs(d - 1.0)
            grid[y][x] = _clamp(1.0 - diff / thickness)
    return grid


def generate_heart(width, height):
    """Heart shape using rasterized parametric heart."""
    grid = _grid(width, height)
    cx, cy = width // 2, height // 2
    scale = min(width, height) / 3.5
    for y in range(height):
        for x in range(width):
            # Map pixel to math coords centered on heart
            xx = (x - cx) / scale
            yy = (cy - y) / scale  # flip Y so +Y is up
            # Heart inequality: (x^2 + y^2 - 1)^3 <= x^2 * y^3
            lhs = (xx * xx + yy * yy - 1.0) ** 3
            rhs = xx * xx * (yy ** 3)
            inside = lhs - rhs
            if inside < 0:
                # Inside the heart — density depends on distance from boundary
                d = abs(inside) ** 0.5
                grid[y][x] = _clamp(0.5 + d * 0.5)
            else:
                # Outside — density fades quickly
                d = inside ** 0.4
                grid[y][x] = _clamp(max(0.0, 0.6 - d * 1.5))
    return grid


def generate_star(width, height, points=5, inner_ratio=0.4):
    """Regular star polygon."""
    grid = _grid(width, height)
    cx, cy = width // 2, height // 2
    r = min(width, height) * 0.42
    for y in range(height):
        for x in range(width):
            angle = math.atan2(y - cy, x - cx)
            sector = angle / (2 * math.pi) * points
            sector_frac = sector - int(sector)
            outer_angle = sector_frac * 2 * math.pi / points
            ri = r if sector_frac < 0.5 else r * inner_ratio
            ri *= 1.0 / math.cos(outer_angle - math.pi / points)
            d = _dist(x, y, cx, cy)
            grid[y][x] = _clamp(1.0 - abs(d - ri) / (r * 0.25))
    return grid


def generate_mountain(width, height):
    """Mountain peak with snowy cap."""
    grid = _grid(width, height)
    cx = width // 2
    peak_y = int(height * 0.15)
    base_y = int(height * 0.88)
    snow_line = int(height * 0.35)
    hw = width * 0.45
    for y in range(height):
        for x in range(width):
            if y < peak_y or y > base_y:
                continue
            progress = (y - peak_y) / (base_y - peak_y)
            w_at_y = hw * progress
            dx = abs(x - cx) / w_at_y if w_at_y > 0 else 99
            d = max(0, dx - 0.85)
            grid[y][x] = _clamp(1.0 - d * 1.8)
            # Snow cap: lighter near peak
            if y < snow_line and grid[y][x] > 0:
                snow_progress = 1.0 - (y - peak_y) / (snow_line - peak_y)
                grid[y][x] *= (0.3 + 0.7 * (1 - snow_progress * 0.5))
    return grid


def generate_skull(width, height):
    """Stylized skull profile (front view)."""
    grid = _grid(width, height)
    cx, cy = width // 2, height // 2
    head_r = min(width, height) * 0.38
    jaw_w = head_r * 0.9
    jaw_h = head_r * 0.5
    eye_r = head_r * 0.18
    eye_y = cy - head_r * 0.15
    eye_dx = head_r * 0.28
    nose_y = cy + head_r * 0.15
    jaw_y0 = cy + head_r * 0.45
    for y in range(height):
        for x in range(width):
            # Head (circle)
            d_head = _dist(x, y, cx, cy) / head_r
            val = _clamp(1.0 - (d_head - 0.9) * 3) if d_head < 1.0 else 0.0
            # Jaw (rectangle with rounded bottom)
            jaw_top = jaw_y0
            jaw_bot = jaw_y0 + jaw_h
            jaw_l = cx - jaw_w / 2
            jaw_r_pos = cx + jaw_w / 2
            if jaw_top <= y <= jaw_bot and jaw_l <= x <= jaw_r_pos:
                jaw_edge = min(abs(x - jaw_l), abs(x - jaw_r_pos),
                               abs(y - jaw_top), abs(y - jaw_bot)) / (jaw_w * 0.2)
                jaw_val = _clamp(1.0 - jaw_edge * 1.5)
                val = max(val, jaw_val)
            # Eyes (dark holes — set density to 0)
            for ex in [cx - eye_dx, cx + eye_dx]:
                d_eye = _dist(x, y, ex, eye_y) / eye_r
                if d_eye < 0.85:
                    val = 0.0
                elif d_eye < 1.1:
                    val = max(val, _clamp(0.6 - (d_eye - 0.85) * 2))
            # Nose (triangle-ish dark spot)
            d_nose = _dist(x, y, cx, nose_y) / (head_r * 0.12)
            if d_nose < 0.7:
                val = min(val, 0.1)
            # Teeth (vertical gaps in jaw)
            if jaw_top + jaw_h * 0.15 <= y <= jaw_top + jaw_h * 0.5:
                num_teeth = 6
                tooth_w = jaw_w / num_teeth
                tx = ((x - jaw_l) % tooth_w) / tooth_w
                if 0.35 < tx < 0.65:
                    val *= 0.3
            grid[y][x] = val
    return grid


def generate_dragon(width, height):
    """Abstract dragon silhouette (serpentine body + wings)."""
    grid = _grid(width, height)
    cx, cy = width // 2, height // 2
    body_r = min(width, height) * 0.1
    # Coiled body
    for y in range(height):
        for x in range(width):
            angle = (y - cy) * 0.15
            bx = cx + math.sin(angle) * min(w := width, height) * 0.2
            d_body = _dist(x, y, bx, y)
            if d_body < body_r:
                grid[y][x] = _clamp(1.0 - d_body / body_r * 0.6)
            # Wings
            wing_y = cy - body_r * 1.5
            wing_span = min(width, height) * 0.4
            if abs(y - wing_y) < wing_span * 0.5:
                wing_width = wing_span * (1 - abs(y - wing_y) / (wing_span * 0.5))
                dx_w = abs(x - cx) / wing_width
                if dx_w < 1.0:
                    grid[y][x] = max(grid[y][x], _clamp(0.7 - dx_w * 0.5))
            # Head
            head_y = cy - body_r * 3.5
            d_head = _dist(x, y, cx, head_y) / (body_r * 0.8)
            if d_head < 1.0:
                grid[y][x] = max(grid[y][x], _clamp(1.0 - d_head * 0.7))
    return grid


def generate_wave(width, height):
    """Oscillating sine waves with fading amplitude."""
    grid = _grid(width, height)
    for y in range(height):
        for x in range(width):
            val = 0.0
            for i in range(1, 5):
                freq = i * 0.15
                amp = height * 0.15 / i
                phase = x * freq
                wave_y = height // 2 + math.sin(phase) * amp
                d = abs(y - wave_y) / (height * 0.08)
                val = max(val, _clamp((1.0 - d) * (1.0 - i * 0.15)))
            grid[y][x] = val
    return grid


def generate_spiral(width, height):
    """Archimedean spiral."""
    grid = _grid(width, height)
    cx, cy = width // 2, height // 2
    max_r = min(width, height) * 0.45
    for y in range(height):
        for x in range(width):
            d = _dist(x, y, cx, cy)
            angle = math.atan2(y - cy, x - cx)
            spiral_r = max_r * (angle + 2 * math.pi) / (6 * math.pi)
            diff = abs(d - spiral_r) / (max_r * 0.12)
            grid[y][x] = _clamp(1.0 - diff)
    return grid


def generate_diamond(width, height):
    """Diamond / rotated square with inner glow."""
    grid = _grid(width, height)
    cx, cy = width // 2, height // 2
    r = min(width, height) * 0.42
    for y in range(height):
        for x in range(width):
            d = (abs(x - cx) + abs(y - cy)) / r
            grid[y][x] = _clamp(1.0 - d * 0.7)
    return grid


def generate_grid_fill(width, height, pattern="noise"):
    """Fill patterns: noise, checkerboard, stripes."""
    grid = _grid(width, height)
    for y in range(height):
        for x in range(width):
            if pattern == "noise":
                grid[y][x] = random.random() * 0.9 + 0.1
            elif pattern == "checkerboard":
                grid[y][x] = 0.9 if (x + y) % 2 == 0 else 0.3
            elif pattern == "stripes":
                grid[y][x] = 0.9 if y % 3 == 0 else 0.2
            else:
                grid[y][x] = random.random()
    return grid


# ── subject → generator mapping ──────────────────────────────────────────────

SHAPES = {
    "circle": generate_circle,
    "ring": generate_ring,
    "heart": generate_heart,
    "star": generate_star,
    "mountain": generate_mountain,
    "skull": generate_skull,
    "dragon": generate_dragon,
    "wave": generate_wave,
    "spiral": generate_spiral,
    "diamond": generate_diamond,
    "noise": lambda w, h: generate_grid_fill(w, h, "noise"),
    "checkerboard": lambda w, h: generate_grid_fill(w, h, "checkerboard"),
    "stripes": lambda w, h: generate_grid_fill(w, h, "stripes"),
}

# Aliases
ALIASES = {
    "round": "circle", "sphere": "circle", "orb": "circle",
    "donut": "ring", "torus": "ring", "halo": "ring",
    "love": "heart", "loveletter": "heart",
    "starburst": "star", "star5": "star",
    "peak": "mountain", "hill": "mountain", "alps": "mountain",
    "skullface": "skull", "death": "skull", "cranium": "skull",
    "wyvern": "dragon", "serpent": "dragon", "beast": "dragon",
    "ocean": "wave", "ripple": "wave", "sine": "wave",
    "coil": "spiral", "whirl": "spiral", "galaxy": "spiral",
    "gem": "diamond", "rhombus": "diamond",
}


def _match_subject(subject: str) -> tuple:
    """Return (generator_fn, width_mult, height_mult)."""
    s = subject.strip().lower()
    # Direct match
    if s in SHAPES:
        return SHAPES[s], 1.0, 1.0
    # Alias
    if s in ALIASES:
        return SHAPES[ALIASES[s]], 1.0, 1.0
    # Substring match
    for key, fn in SHAPES.items():
        if key in s:
            return fn, 1.0, 1.0
    for key, shape_name in ALIASES.items():
        if key in s:
            return SHAPES[shape_name], 1.0, 1.0
    # Default: spiral
    return SHAPES["spiral"], 1.0, 1.0


# ── density → glyph rendering ────────────────────────────────────────────────

def density_to_glyph(density: float, palette: list = GLYPHS) -> str:
    """Map density [0,1] to a glyph index."""
    idx = int(density * (GLYPH_COUNT - 1) + 0.5)
    return palette[min(idx, GLYPH_COUNT - 1)]


def render_grid(grid, width, height, palette=None):
    """Convert density grid to glyph string."""
    if palette is None:
        palette = GLYPHS
    pcount = len(palette)
    lines = []
    for row in grid[:height]:
        line = ""
        for x in range(width):
            d = row[x] if x < len(row) else 0.0
            idx = int(d * (pcount - 1) + 0.5)
            line += palette[min(idx, pcount - 1)]
        # Strip trailing spaces for clean terminal output
        line = line.rstrip()
        lines.append(line)
    return "\n".join(lines)


def multi_pass_render(grid, width, height, passes=3, jitter=0.05, palette=None):
    """
    Multi-pass rendering: overlay several slightly-shifted passes to create
    moiré density effects. Denser regions accumulate heavier glyphs.
    """
    combined = _grid(width, height)
    for p in range(passes):
        offset_x = int(jitter * width * (p - passes // 2))
        offset_y = int(jitter * height * (p - passes // 2))
        for y in range(height):
            for x in range(width):
                sx = min(width - 1, max(0, x + offset_x))
                sy = min(height - 1, max(0, y + offset_y))
                combined[y][x] += grid[sy][sx] / passes
    # Normalize back to [0, 1]
    for y in range(height):
        for x in range(width):
            combined[y][x] = _clamp(combined[y][x])
    return render_grid(combined, width, height, palette)


# ── text-to-shape (keyword extraction from freeform descriptions) ────────────

def parse_subject(subject: str) -> list:
    """
    Parse a freeform subject description into a list of shape names.
    Supports comma-separated and 'with'/'and' connectors.
    """
    s = subject.lower()
    found = []
    all_keys = set(SHAPES.keys()) | set(ALIASES.keys())
    for key in sorted(all_keys, key=len, reverse=True):
        if key in s:
            shape = ALIASES.get(key, key)
            found.append(shape)
            s = s.replace(key, " ", 1)
    if not found:
        # Fallback: use hash of subject string to pick a shape
        h = hash(subject)
        shape_names = list(SHAPES.keys())
        found.append(shape_names[abs(h) % len(shape_names)])
    return found


def blend_grids(grids_and_weights: list) -> list:
    """Blend multiple grids (same dimensions) with weights."""
    if not grids_and_weights:
        return []
    h = len(grids_and_weights[0][0])
    w = len(grids_and_weights[0][0][0]) if h > 0 else 0
    total_weight = sum(wt for _, wt in grids_and_weights)
    result = _grid(w, h)
    for grid, wt in grids_and_weights:
        for y in range(h):
            for x in range(w):
                result[y][x] += (grid[y][x] if x < len(grid[y]) else 0.0) * (wt / total_weight)
    return result


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Glyph Density Rendering Tool — dense ASCII art from subject descriptions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s skull
  %(prog)s heart --width 50 --height 40
  %(prog)s "mountain with spiral"
  %(prog)s dragon --passes 5
  %(prog)s noise --palette light     # uses only + * (sparse)
  %(prog)s skull --palette dense      # uses only @ # (heavy)

Built-in shapes: circle, ring, heart, star, mountain, skull, dragon,
                 wave, spiral, diamond, noise, checkerboard, stripes

Subject descriptions are parsed for keywords: "a dragon flying over mountains"
will blend dragon + mountain shapes.
        """
    )
    parser.add_argument("subject", nargs="?", default="spiral",
                        help="Subject description or shape name (default: spiral)")
    parser.add_argument("--width", "-w", type=int, default=60,
                        help="Output width in characters (default: 60)")
    parser.add_argument("--height", "-H", type=int, default=30,
                        help="Output height in lines (default: 30)")
    parser.add_argument("--passes", "-p", type=int, default=3,
                        help="Multi-pass rendering layers (default: 3)")
    parser.add_argument("--jitter", type=float, default=0.05,
                        help="Jitter amount for multi-pass offset (default: 0.05)")
    parser.add_argument("--palette", choices=["full", "dense", "light", "minimal"],
                        default="full",
                        help="Glyph palette: full (all), dense (@#), light (+*), minimal (one char)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducible output")
    parser.add_argument("--invert", action="store_true",
                        help="Invert density (bright becomes dark)")

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    # Palette selection
    PALETTES = {
        "full": [" ", "+", "*", "#", "@"],
        "dense": [" ", " ", "#", "#", "@"],
        "light": [" ", "+", "+", "*", "*"],
        "minimal": [" ", "@", "@", "@", "@"],
    }
    palette = PALETTES[args.palette]

    # Parse subject into shape(s)
    shapes = parse_subject(args.subject)
    grids = []
    for shape_name in shapes:
        fn, _, _ = _match_subject(shape_name)
        g = fn(args.width, args.height)
        grids.append(g)

    # Blend if multiple shapes
    if len(grids) > 1:
        grid = blend_grids(list(zip(grids, [1.0] * len(grids))))
    else:
        grid = grids[0]

    # Invert if requested
    if args.invert:
        for y in range(args.height):
            for x in range(args.width):
                if x < len(grid[y]):
                    grid[y][x] = 1.0 - grid[y][x]

    # Render
    output = multi_pass_render(grid, args.width, args.height,
                               passes=args.passes, jitter=args.jitter,
                               palette=palette)

    print(output)


if __name__ == "__main__":
    main()
