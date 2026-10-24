#!/usr/bin/env python3
"""Render a short word as herbox-safe figlet art.

  render_banner.py hush --font small
  render_banner.py zoey --category quiet
  render_banner.py dawn --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "categories.json"


def load_cat() -> dict:
    return json.loads(CAT.read_text()) if CAT.is_file() else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("text", nargs="?", default="zoey")
    ap.add_argument("--font", "-f", default=None)
    ap.add_argument("--category", "-c", default=None, help="quiet|slant|script|block|cyber|play|display")
    ap.add_argument("--width", "-w", type=int, default=34)
    ap.add_argument("--list", action="store_true", help="list categories / fonts")
    args = ap.parse_args()

    cat = load_cat()
    fig = cat.get("figlet_herbox") or {}

    if args.list:
        for k, fonts in fig.items():
            print(f"{k}: {', '.join(fonts)}")
        return 0

    try:
        import pyfiglet
    except ImportError:

        # every banner failed). The krew keeps its own copy outside any venv: ~/.hermes/krew-pylib (pure Python).
        sys.path.append(str(Path.home() / ".hermes" / "krew-pylib"))
        try:
            import pyfiglet
        except ImportError:
            print("pyfiglet missing: uv pip install --target ~/.hermes/krew-pylib pyfiglet", file=sys.stderr)
            return 2

    font = args.font
    if args.category:
        fonts = fig.get(args.category) or []
        if not fonts:
            print(f"unknown category {args.category!r}", file=sys.stderr)
            return 2
        font = fonts[0]
    font = font or "small"

    f = pyfiglet.Figlet(font=font, width=args.width)
    out = f.renderText(args.text).rstrip("\n")
    lines = out.split("\n")
    mw = max((len(l) for l in lines), default=0)
    print(out)
    print(f"\n# font={font} cols={mw} lines={len(lines)} category={args.category or '-'}", file=sys.stderr)
    if mw > 36:
        print("# WARN: wider than herbox 36 — trim or use scene float", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
