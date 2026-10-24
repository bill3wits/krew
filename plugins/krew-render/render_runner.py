#!/usr/bin/env python3
"""The render runner: works the render-order queue against a ComfyUI server. No LLM.

    python3 render_runner.py plan                    what would run, batch by batch
    python3 render_runner.py run [--max-orders N] [--minutes M]

Orders are grouped by model family (one load per batch), highest priority then oldest first. Each order
is claimed (pending -> running), its graph POSTed to `<comfy_url>/prompt`, `/history/<id>` polled until it
completes, and every output file fetched from `/view` into the order's out_dir (-> done). A failure is
recorded whole on the order (-> failed). An order whose runner died goes back to pending at the next run.

Machine specifics are settings, not code (`render:` in $KREW_HOME/krew.yaml): comfy_url, order_timeout_s,
and two optional shell hooks for whatever your machine must do around a run (free a GPU, start ComfyUI):
before_run and after_run. after_run always runs once before_run has started, even on a failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_settings as S          # noqa: E402
import render_orders as ro         # noqa: E402
import krew_comfy                   # noqa: E402  the one Comfy location


def cfg() -> dict:
    st = S.load("render")
    return {"comfy_url": str(st.get("comfy_url") or krew_comfy.get("url")).rstrip("/"),
            "order_timeout_s": float(st.get("order_timeout_s") or 1800),
            "before_run": str(st.get("before_run") or ""), "after_run": str(st.get("after_run") or "")}


class Comfy:
    def __init__(self, base: str):
        self.base = base
        self.client_id = f"krew-{uuid.uuid4().hex[:8]}"

    def post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return json.loads(r.read() or b"{}")

    def raw(self, path: str) -> bytes:
        with urllib.request.urlopen(self.base + path, timeout=300) as r:
            return r.read()


AI_MARK_FAMILIES = ("ltx",)      # an LTX output is marked AI-generated (the owner 2026-10-08; Lightricks' licence asks it)


def ai_generated_mark(o: dict, out_dir: Path) -> dict:
    """For an order whose family needs it: write AI-GENERATED.txt beside the outputs and return the fields the
    done record carries (ai_generated, licence). {} for every other family."""
    fam = str(o.get("family") or "")
    if not fam.startswith(AI_MARK_FAMILIES):
        return {}
    note = (f"AI-generated video (order {o.get('id', '')}, workflow {o.get('workflow', '')}), made with LTX-2 under the "
            "Lightricks LTX community licence (commercial use allowed under $10M annual revenue, outputs labelled "
            "AI-generated, no competing product). Label it as AI-generated wherever it is shown.\n")
    try:
        (out_dir / "AI-GENERATED.txt").write_text(note, encoding="utf-8")
    except OSError:
        pass
    return {"ai_generated": True, "licence": "Lightricks LTX community licence"}


def render_one(o: dict, comfy, *, timeout_s: float, deadline: float | None = None, root=None, poll_s: float = 3.0) -> dict:
    """A claimed order -> done (outputs on disk) or failed (why, whole). Never raises."""
    t0 = time.time()
    try:
        pid = comfy.post("/prompt", {"prompt": o["graph"], "client_id": getattr(comfy, "client_id", "krew")})["prompt_id"]
        limit = t0 + timeout_s if deadline is None else min(t0 + timeout_s, deadline)
        h = None
        while time.time() < limit:
            h = (comfy.get(f"/history/{pid}") or {}).get(pid)
            if h and (h.get("status") or {}).get("completed") is not None:
                break
            time.sleep(poll_s)
        else:
            raise TimeoutError(f"no result in {int(limit - t0)} s")
        if not (h.get("status") or {}).get("completed"):
            raise RuntimeError(f"ComfyUI status {(h.get('status') or {}).get('status_str')}")
        out_dir = Path(o["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for nid in sorted(h.get("outputs") or {}, key=str):
            for kind in ("images", "gifs", "videos", "animated"):
                for it in (h["outputs"][nid].get(kind) or []):
                    if not isinstance(it, dict) or "filename" not in it:
                        continue
                    q = urllib.parse.urlencode({k: it.get(k, "") for k in ("filename", "subfolder", "type")})
                    f = out_dir / f"{nid}-{it['filename']}"
                    f.write_bytes(comfy.raw(f"/view?{q}"))
                    files.append(str(f))
        if not files:
            raise RuntimeError("the prompt finished with no output files")
        mark = ai_generated_mark(o, out_dir)
        return ro.move(o["id"], "done", root=root, outputs=files, seconds=round(time.time() - t0), prompt_id=pid, **mark)
    except Exception as e:  # noqa: BLE001
        return ro.move(o["id"], "failed", root=root, why=f"{type(e).__name__}: {e}", seconds=round(time.time() - t0))


def plan(max_orders: int | None = None, root=None) -> list[dict]:
    batches = ro.plan(root=root)
    if max_orders:
        left, kept = max_orders, []
        for b in batches:
            if left <= 0:
                break
            b = {**b, "orders": b["orders"][:left]}
            left -= len(b["orders"])
            kept.append(b)
        batches = kept
    return batches


def _hook(cmd: str, what: str) -> int:
    if not cmd:
        return 0
    r = subprocess.run(shlex.split(cmd), text=True)
    if r.returncode != 0:
        print(f"{what} hook exited {r.returncode}: {cmd}", file=sys.stderr)
    return r.returncode


def run(max_orders: int | None = None, minutes: float | None = None, *, comfy=None, root=None, c: dict | None = None) -> int:
    c = c or cfg()
    for oid in ro.requeue_orphans(root=root):
        print(f"{oid} back to pending (its runner is gone)")
    batches = plan(max_orders, root=root)
    if not batches:
        print("nothing to do: no pending render orders")
        return 0
    deadline = time.time() + minutes * 60 if minutes else None
    claimed: list[str] = []
    started = False
    rc = 0
    try:
        if _hook(c["before_run"], "before_run") != 0:
            return 3
        started = True
        comfy = comfy or Comfy(c["comfy_url"])
        for b in batches:
            for oid in b["orders"]:
                if deadline and time.time() >= deadline:
                    print("deadline reached: the rest stays pending")
                    return rc
                try:
                    o = ro.move(oid, "running", root=root, runner_pid=os.getpid())
                except ro.OrderError:
                    continue
                claimed.append(oid)
                res = render_one(o, comfy, timeout_s=c["order_timeout_s"], deadline=deadline, root=root)
                claimed.remove(oid)
                print(f"{oid} {res['state']} {res.get('why') or len(res.get('outputs') or [])}")
                rc = rc or (1 if res["state"] == "failed" else 0)
            try:
                comfy.post("/free", {"unload_models": True, "free_memory": True})
            except Exception:  # noqa: BLE001
                pass
        return rc
    finally:
        for oid in claimed:
            try:
                ro.move(oid, "pending", root=root, why="the run ended mid-render")
            except ro.OrderError:
                pass
        if started:
            _hook(c["after_run"], "after_run")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="render_runner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--max-orders", type=int)
    r = sub.add_parser("run")
    r.add_argument("--max-orders", type=int)
    r.add_argument("--minutes", type=float)
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        for b in plan(a.max_orders):
            print(f"{b['family']:14} {len(b['orders']):3} order(s)  {b['need_gib']:6} GiB  {', '.join(b['orders'])}")
        return 0
    return run(a.max_orders, a.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
