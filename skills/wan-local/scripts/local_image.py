#!/usr/bin/env python3
"""Print krew-comfy tool args for Wan-skills modes. Does not call Comfy."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

HERMES_DIR = KH.hermes_home() / "krew-gpu-mode"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("1", "2", "3"), required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--canvas", type=Path, default=None)
    ap.add_argument("--ref", type=Path, nargs="*", default=[])
    ap.add_argument("--n", type=int, default=1)
    args = ap.parse_args()
    mode = HERMES_DIR.read_text().strip() if HERMES_DIR.is_file() else "missing"
    if mode != "render":
        print(json.dumps({"error": f"occupancy is {mode}, need render"}))
        return 2
    if args.mode == "1":
        out = {
            "tool": "image_generate",
            "model": "qwen-image-2512",
            "prompt": args.prompt,
            "aspect_ratio": "landscape",
            "n": args.n,
        }
    else:
        if not args.canvas or not args.canvas.is_file():
            print(json.dumps({"error": "mode 2/3 need --canvas as an existing file"}))
            return 2
        refs = [str(p.resolve()) for p in args.ref if p.is_file()]
        out = {
            "tool": "image_generate",
            "model": "qwen-edit-2511",
            "prompt": args.prompt,
            "image_url": str(args.canvas.resolve()),
            "reference_image_urls": refs,
            "aspect_ratio": "landscape",
            "n": args.n,
            "note": "mode 3 = n separate calls; look after each. Face plates: render-avatars §2.",
        }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
