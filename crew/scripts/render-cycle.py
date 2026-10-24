#!/usr/bin/env python3
"""Render-only generate → look → iterate. No brain swaps.

Silo: refuses unless ~/.hermes/krew-gpu-mode is render and Comfy is up.
Does not start/stop ops, crew, or Cosy. Muse stays parked. Ada/Zoey
sleep; this script talks HTTP to crew :8005 (the kanban 27B). Ops is parked.

Loop:
  1. krew-comfy still
  2. Lila (crew :8005) sees the PNG
  3. Vera (crew :8005) pass/fail, stop or revise
  4. re-roll only if Vera says iterate

Bash does not render. Creative (Lila) sits with Vera on the pixels.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

HERMES = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
MODE_FILE = Path(os.environ.get("KREW_GPU_MODE_FILE", str(HERMES / "krew-gpu-mode")))
OPS = os.environ.get("KREW_OPS_URL", "http://127.0.0.1:8000").rstrip("/")
CREW = os.environ.get("KREW_CREW_URL", "http://127.0.0.1:8005").rstrip("/")
PLUGIN = HERMES / "plugins" / "krew-comfy" / "__init__.py"
if not PLUGIN.is_file():   # krew's own copy of the plugin (plugins/krew-comfy beside crew/)
    _own = Path(__file__).resolve().parents[2] / "plugins" / "krew-comfy" / "__init__.py"
    PLUGIN = _own if _own.is_file() else PLUGIN
LOG = Path(os.environ.get("RENDER_CYCLE_LOG", "/tmp/render-cycle.jsonl"))

LILA = (
    "You are Lila, house designer, looking at a still that was just generated. "
    "Spec is below. One JSON object only, no markdown: "
    '{"on_spec":bool,"taste":"one sentence","revise":"prompt rewrite or empty if good"}'
)
VERA = (
    "You are Vera, house reviewer. Same spec, same pixels. Lila's note is below. "
    "Would we put this on the wall for family Friday? JSON only: "
    '{"pass":bool,"stop":bool,"why":"one sentence","revise":"prompt rewrite or empty"}'
)


def _mode() -> str:
    try:
        return MODE_FILE.read_text().strip().splitlines()[0].strip().lower()
    except OSError:
        return ""


def _must_render() -> None:
    if _mode() != "render":
        sys.exit("silo: krew-gpu-mode is not render — refuse (do not touch Standard/Beast)")


def _load_comfy():
    spec = spec_from_file_location("krew_comfy_cycle", PLUGIN)
    assert spec and spec.loader
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _b64_png(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def _engine(url: str | None, model: str | None) -> tuple[str, str]:
    if url:
        base = url.rstrip("/")
        served = model or ("crew" if base.endswith(":8005") else "ops")
        return base, served
    return CREW, model or "crew"


def _post_chat(base: str, body: dict, timeout: int = 120) -> str:
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return (d["choices"][0]["message"].get("content") or "").strip()


def _see(
    path: Path,
    system: str,
    user: str,
    max_tokens: int = 180,
    *,
    url: str | None = None,
    model: str | None = None,
) -> str:
    """Vision look. Lila and Vera → crew :8005."""
    base, served = _engine(url, model)
    body = {
        "model": served,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {"type": "image_url", "image_url": {"url": _b64_png(path)}},
                ],
            },
        ],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        return _post_chat(base, body)
    except (urllib.error.URLError, TimeoutError, OSError):
        if base.rstrip("/") == CREW.rstrip("/"):
            raise
        body["model"] = "crew"
        return _post_chat(CREW, body)


def _json_from(text: str) -> dict[str, Any]:
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e <= s:
        return {"raw": text}
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return {"raw": text}


def _log(row: dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def generate(comfy, prompt: str, model: str, aspect: str) -> Path:
    img = comfy.HouseComfyImage()
    r = img.generate(prompt=prompt, aspect_ratio=aspect, model=model)
    if not r.get("success"):
        raise RuntimeError(r.get("error") or r)
    p = Path(r["image"])
    if not p.is_file():
        raise FileNotFoundError(p)
    return p


def _vram_ok() -> str:
    """t_724afc97 (2026-09-20): the pre-loop VRAM guard.

    The loop's per-round GPU consumers live OUTSIDE this process: the Comfy
    server (:8188) holds the generation model, and each round's Lila/Vera look
    is a 27B crew vision call. There are no per-round textures or assets to
    lazy-load in the loop itself (the PNG is streamed to the judge and
    dropped), so the low-VRAM optimization for THIS pipeline is the guard:
    the loop must not start a round its judge cannot fit.

    Reads free VRAM via gpu_budget (nvidia-smi first, Comfy /system_stats
    second, the LOWER of the two). Fails fast with a clear message when less
    than the floor is free (the card's own number, ~6 GB) — a 6.5 GB-free
    machine runs the loop without OOMing mid-round, and anything under the
    floor is refused HERE, not discovered as a dead GPU half-way through.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gpu_budget

    g = gpu_budget.query_vram()
    if g.free_mib is None:
        # Fail-closed: no reading is not enough VRAM. The per-round 27B
        # vision judge would OOM if we ran blind — that is the OOM this
        # guard exists to prevent.
        return (
            "VRAM could not be measured (nvidia-smi and Comfy /system_stats "
            "both answered nothing). Refusing to start the render loop blind — "
            "the per-round 27B vision judge would OOM. Restore the "
            "GPU/driver and retry."
        )
    # The loop's floor is an ABSOLUTE number — the card's own "if <6GB is
    # available" / "~6.5GB free". gpu_budget's 10%-of-total frac (9.6 GB on
    # this 96 GB card) was designed for render-submit's pre-park precheck, a
    # different question; borrowing it here would refuse a 6.5 GB-free loop
    # the card says must run. So the loop pins its own floor (env-overridable)
    # and reads the VRAM through gpu_budget for the source-of-truth merge.
    floor_mib = float(os.environ.get("KREW_RENDER_LOOP_MIN_FREE_MIB", "6096"))
    if g.free_mib < floor_mib:
        return (
            f"only {g.free_mib:.0f} MiB ({g.free_mib / 1024.0:.1f} GiB) "
            f"free VRAM; the render loop needs >= {floor_mib:.0f} MiB "
            f"for the per-round 27B vision judge (plus the Comfy model). "
            f"Refusing to start — it would OOM mid-round. Free VRAM "
            f"(park ops, or stop a resident engine) and retry."
        )
    return ""


def cycle(
    spec: str,
    *,
    model: str = "flux2-turbo",
    aspect: str = "landscape",
    max_rounds: int = 3,
) -> Path:
    _must_render()
    comfy = _load_comfy()
    if not comfy.comfy_up():
        sys.exit("silo: Comfy :8188 down — Render first")
    # t_724afc97: the pre-loop VRAM guard — fails fast with a clear message
    # below the floor, BEFORE the first round's 27B vision judge OOMs.
    # KREW_RENDER_SKIP_VRAM_GUARD=1 skips it (test seam).
    if not os.environ.get("KREW_RENDER_SKIP_VRAM_GUARD", "").strip():
        _v = _vram_ok()
        if _v:
            print(_v, file=sys.stderr)
            sys.exit(3)
    prompt = spec
    last = Path()
    for rnd in range(1, max_rounds + 1):
        print(f"round {rnd}/{max_rounds} model={model}")
        print(f"  hand: {prompt[:160]}")
        last = generate(comfy, prompt, model, aspect)
        print(f"  still: {last}")
        lila_raw = _see(
            last,
            LILA,
            f"SPEC:\n{spec}\n\nThis is round {rnd}. Look.",
            url=CREW,
            model="crew",
        )
        lila = _json_from(lila_raw)
        print(f"  lila: {json.dumps(lila, ensure_ascii=False)[:240]}")
        vera_raw = _see(
            last,
            VERA,
            f"SPEC:\n{spec}\nLILA:\n{json.dumps(lila, ensure_ascii=False)}\nRound {rnd}/{max_rounds}.",
            url=CREW,
            model="crew",
        )
        vera = _json_from(vera_raw)
        print(f"  vera: {json.dumps(vera, ensure_ascii=False)[:240]}")
        _log(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "round": rnd,
                "model": model,
                "prompt": prompt,
                "still": str(last),
                "lila": lila,
                "vera": vera,
            }
        )
        if vera.get("pass") or vera.get("stop") or rnd == max_rounds:
            print(f"done pass={vera.get('pass')} stop={vera.get('stop')} {last}")
            return last
        nxt = (vera.get("revise") or lila.get("revise") or "").strip()
        if not nxt:
            print("no revise text — stop")
            return last
        prompt = nxt
    return last


def main() -> int:
    p = argparse.ArgumentParser(description="Render-only look/iterate cycle")
    p.add_argument("spec", help="what the still should be")
    p.add_argument("--model", default="flux2-turbo")
    p.add_argument("--aspect", default="landscape")
    p.add_argument("--max-rounds", type=int, default=3)
    args = p.parse_args()
    cycle(args.spec, model=args.model, aspect=args.aspect, max_rounds=args.max_rounds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
