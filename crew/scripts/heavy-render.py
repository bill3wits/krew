#!/usr/bin/env python3
"""heavy-render — empty the render orders with crew off the card (the User 09-29, H2).

  heavy-render.py plan                   what a window would do; touches nothing (works while OFF)
  heavy-render.py run [--minutes 90] [--max-orders N] [--wait]
                                         start a window (KREW_HEAVY_RENDER=1): a transient unit
  heavy-render.py inside --deadline E    the window itself (the unit runs this; never by hand)

A plain runner, no LLM. The window: `krew-mode heavy-enter` (crew stopped + held, spawns paused,
Crissa's crew slots on ops, flag heavy-render) -> for each sub-batch `krew-mode heavy-comfy
[--highvram]` -> each order claimed, submitted, collected -> `krew-mode heavy-exit`.

Crew ALWAYS comes back - four independent nets:
  1. this process's `finally` runs heavy-exit (normal end, error, SIGTERM);
  2. the unit's ExecStopPost runs heavy-exit (crash, SIGKILL, RuntimeMaxSec);
  3. the 2-min hold sweep sends a heavy-render flag home once the runner's pid is gone or its
     deadline + grace has passed (krew-mode flag_home_due, H0);
  4. a reboot leaves the flag with no runner -> net 3 on the first sweep.
heavy-exit is idempotent: with no heavy-render trace it does nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
import render_orders as ro  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

KREW_MODE = os.environ.get("KREW_MODE_BIN", str(REPO / "scripts" / "krew-mode"))
KREW_MODE_PY = os.environ.get("KREW_MODE_PY", "/usr/bin/python3")
COMFY = os.environ.get("KREW_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
# measured 09-29: crew off, ops + Cosy + presence up -> ~45.8 GiB free; keep a little back
BUDGET_GIB = float(os.environ.get("KREW_HEAVY_BUDGET_GIB", "44"))
VIDEO_FAMILIES = ("ltx23", "ltx25", "wan22")
MARGIN_GIB = {"video": float(os.environ.get("KREW_HEAVY_MARGIN_VIDEO_GIB", "6")),
              "image": float(os.environ.get("KREW_HEAVY_MARGIN_IMAGE_GIB", "3"))}
EST_MIN = {"video": 2.0, "image": 1.0}      # per order incl. load; a plan's rough clock
ORDER_TIMEOUT_S = float(os.environ.get("KREW_HEAVY_ORDER_TIMEOUT_S", "1800"))
EXIT_MARGIN_S = 600.0                          # RuntimeMaxSec = deadline + this
# the unit's ExecStopPost (heavy-exit) waits for crew (900 s + one retry); systemd's default
# 90 s stop timeout killed it mid-wait in the H3 crash drill (09-29) and net 3 had to finish
EXIT_STOP_S = int(os.environ.get("KREW_HEAVY_EXIT_STOP_S", "2400"))


def enabled() -> bool:
    return os.environ.get("KREW_HEAVY_RENDER", "0").strip().lower() in ("1", "on", "true", "yes")


def _kind(family: str) -> str:
    return "video" if family in VIDEO_FAMILIES else "image"


# ---------------------------------------------------------------- the plan
def sub_batches(batches: list[dict], by_id: dict[str, dict], *, budget: float = BUDGET_GIB) -> list[dict]:
    """Split each family batch into runs of orders whose UNION of model files (+ a working
    margin) fits resident in `budget`: those run --highvram. An order that does not fit even
    alone runs on its own in normal VRAM (ComfyUI loads what fits and swaps)."""
    out = []
    for b in batches:
        margin = MARGIN_GIB[_kind(b["family"])]
        cur: dict | None = None
        for oid in b["orders"]:
            o = by_id[oid]
            files = {m["path"]: int(m["bytes"]) for m in o.get("models") or []}
            alone = sum(files.values()) / 2 ** 30 + margin
            if alone > budget:
                out.append({"family": b["family"], "orders": [oid], "highvram": False,
                            "need_gib": round(alone - margin, 2)})
                cur = None
                continue
            if cur is not None:
                union = {**cur["_files"], **files}
                if sum(union.values()) / 2 ** 30 + margin <= budget:
                    cur["orders"].append(oid)
                    cur["_files"] = union
                    cur["need_gib"] = round(sum(union.values()) / 2 ** 30, 2)
                    continue
            cur = {"family": b["family"], "orders": [oid], "highvram": True, "_files": files,
                   "need_gib": round(sum(files.values()) / 2 ** 30, 2)}
            out.append(cur)
    for sb in out:
        sb.pop("_files", None)
    return out


def make_plan(*, max_orders: int | None = None, minutes: float = 90.0, root: Path | None = None) -> dict:
    pending = ro.orders("pending", root=root)
    by_id = {o["id"]: o for o in pending}
    subs = sub_batches(ro.plan(pending), by_id)
    if max_orders:
        left, kept = max_orders, []
        for sb in subs:
            if left <= 0:
                break
            sb = {**sb, "orders": sb["orders"][:left]}
            left -= len(sb["orders"])
            kept.append(sb)
        subs = kept
    est = sum(EST_MIN[_kind(sb["family"])] * len(sb["orders"]) + 0.5 for sb in subs) + 5.0  # + enter/exit
    return {"sub_batches": subs, "orders": sum(len(sb["orders"]) for sb in subs), "pending": len(pending),
            "est_min": round(est, 1), "minutes": minutes, "budget_gib": BUDGET_GIB,
            "stops": ["crew.service (held, card heavy-render)", "kanban spawns (dispatch_profiles: [])"],
            "moves": ["Crissa's crew-pinned slots -> ops", "Vera/Steward (TurboKrew) -> ops"]}


def print_plan(p: dict) -> None:
    print(f"heavy-render plan: {p['orders']} of {p['pending']} pending order(s), ~{p['est_min']} min "
          f"(window {p['minutes']:.0f} min), budget {p['budget_gib']} GiB with crew off")
    for sb in p["sub_batches"]:
        print(f"  {sb['family']:12} {len(sb['orders']):3} order(s)  {sb['need_gib']:6} GiB  "
              f"{'--highvram' if sb['highvram'] else 'normal VRAM (does not fit resident)'}")
    print("  stops: " + "; ".join(p["stops"]))
    print("  moves: " + "; ".join(p["moves"]))
    print("  crew comes back: finally -> heavy-exit; unit ExecStopPost -> heavy-exit; the hold sweep; a reboot")
    if not enabled():
        print("  (KREW_HEAVY_RENDER is not 1: `run` would refuse)")


# ---------------------------------------------------------------- run: launch the unit
def krew_mode(*args: str, runner=subprocess.run) -> int:
    r = runner([KREW_MODE_PY, KREW_MODE, *args], text=True)
    return r.returncode


def board_running(db: Path | None = None) -> int:
    """Running cards on the live board (read-only, mode=ro - never immutable)."""
    import sqlite3
    cur = KH.kanban_dir() / "current"
    try:
        name = cur.read_text().strip() if cur.is_file() else ""
        path = db or (KH.kanban_dir() / "boards" / name / "kanban.db")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        n = con.execute("select count(*) from tasks where status='running'").fetchone()[0]
        con.close()
        return int(n)
    except Exception:
        return -1  # unreadable: the runner refuses (fail closed)


def unit_argv(deadline: float, *, max_orders: int | None, unit: str) -> list[str]:
    runtime = int(deadline - time.time() + EXIT_MARGIN_S)
    exit_cmd = f"{KREW_MODE_PY} {KREW_MODE} heavy-exit"
    inside = [sys.executable, str(Path(__file__).resolve()), "inside", "--deadline", str(deadline)]
    if max_orders:
        inside += ["--max-orders", str(max_orders)]
    return ["systemd-run", "--user", "--collect", "--unit", unit,
            "--description", "heavy-render window (crew off, Comfy high-VRAM)",
            "-p", f"RuntimeMaxSec={max(runtime, 60)}", "-p", f"ExecStopPost={exit_cmd}",
            "-p", f"TimeoutStopSec={EXIT_STOP_S}",
            "--setenv=KREW_HEAVY_RENDER=1", *inside]


def cmd_run(minutes: float, max_orders: int | None, wait: bool, *, runner=subprocess.run) -> int:
    if not enabled():
        print("refused: KREW_HEAVY_RENDER is not 1 (the heavy-render window is off)", file=sys.stderr)
        return 3
    # a dead runner's order counts as waiting (H3 09-29: the only order sat in running/ and
    # `run` said "nothing to do", so it could never be picked up again)
    for oid in ro.requeue_orphans():
        print(f"{oid} back to pending (its runner is gone)")
    p = make_plan(max_orders=max_orders, minutes=minutes)
    if not p["orders"]:
        print("nothing to do: no pending render orders")
        return 0
    running = board_running()
    if running != 0:
        print(f"refused: the board has {running if running > 0 else 'an unreadable number of'} running card(s) - "
              "a window starts on a quiet board", file=sys.stderr)
        return 3
    print_plan(p)
    deadline = time.time() + minutes * 60
    unit = f"heavy-render-{int(time.time())}"
    r = runner(unit_argv(deadline, max_orders=max_orders, unit=unit), capture_output=True, text=True)
    if r.returncode != 0:
        print(f"refused: systemd-run failed: {(r.stderr or '')[-300:]}", file=sys.stderr)
        return 1
    print(f"started {unit} (deadline {time.strftime('%H:%M', time.localtime(deadline))}); "
          f"journalctl --user -u {unit} -f")
    if wait:
        while runner(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0:
            time.sleep(10)
        print(f"{unit} finished")
    return 0


# ---------------------------------------------------------------- inside: the window
class Comfy:
    def __init__(self, base: str = COMFY):
        self.base = base

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


def render_one(o: dict, comfy, *, deadline: float, root: Path | None = None) -> dict:
    """Claimed order -> done (outputs on disk) or failed (why). Never raises."""
    import render_wip
    t0 = time.time()
    try:
        pid = comfy.post("/prompt", render_wip.prompt_body(o["graph"]))["prompt_id"]
        limit = min(t0 + ORDER_TIMEOUT_S, deadline + EXIT_MARGIN_S / 2)
        h = None
        while time.time() < limit:
            h = (comfy.get(f"/history/{pid}") or {}).get(pid)
            if h and (h.get("status") or {}).get("completed") is not None:
                break
            time.sleep(3)
        else:
            raise TimeoutError(f"no result in {int(limit - t0)} s")
        if not (h.get("status") or {}).get("completed"):
            raise RuntimeError(f"Comfy status {(h.get('status') or {}).get('status_str')}")
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
        return ro.move(o["id"], "done", root=root, outputs=files, seconds=round(time.time() - t0),
                       prompt_id=pid)
    except Exception as e:  # noqa: BLE001
        return ro.move(o["id"], "failed", root=root, why=f"{type(e).__name__}: {e}"[:400],
                       seconds=round(time.time() - t0))


def cmd_inside(deadline: float, max_orders: int | None, *, hm=krew_mode, comfy=None,
               root: Path | None = None) -> int:
    """The window. Every path out goes through heavy-exit (net 1)."""
    def _term(_sig, _frm):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, _term)
    claimed: list[str] = []
    rc = 0
    try:
        if hm("heavy-enter", "--runner-pid", str(os.getpid()), "--deadline", str(deadline)) != 0:
            print("heavy-enter refused or failed - leaving", file=sys.stderr)
            return 3
        for oid in ro.requeue_orphans(root=root):
            print(f"{oid} back to pending (its runner is gone)")
        comfy = comfy or Comfy()
        p = make_plan(max_orders=max_orders, root=root)
        for sb in p["sub_batches"]:
            if time.time() >= deadline:
                print("deadline reached - the rest stays pending")
                break
            if hm("heavy-comfy", *(["--highvram"] if sb["highvram"] else [])) != 0:
                print(f"Comfy did not come up for {sb['family']} - leaving", file=sys.stderr)
                rc = 1
                break
            for oid in sb["orders"]:
                if time.time() >= deadline:
                    break
                try:
                    o = ro.move(oid, "running", root=root, runner_pid=os.getpid())
                except ro.OrderError:
                    continue  # cancelled or claimed meanwhile
                claimed.append(oid)
                res = render_one(o, comfy, deadline=deadline, root=root)
                claimed.remove(oid)
                print(f"{oid} {res['state']} {res.get('why') or len(res.get('outputs') or [])}")
            try:
                comfy.post("/free", {"unload_models": True, "free_memory": True})
            except Exception:
                pass
        return rc
    finally:
        for oid in claimed:  # interrupted mid-order: back in the list for the next window
            try:
                ro.move(oid, "pending", root=root, why="window ended mid-render")
            except ro.OrderError:
                pass
        hm("heavy-exit")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="heavy-render", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--max-orders", type=int)
    pl.add_argument("--minutes", type=float, default=90.0)
    r = sub.add_parser("run")
    r.add_argument("--minutes", type=float, default=90.0)
    r.add_argument("--max-orders", type=int)
    r.add_argument("--wait", action="store_true")
    i = sub.add_parser("inside")
    i.add_argument("--deadline", type=float, required=True)
    i.add_argument("--max-orders", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        print_plan(make_plan(max_orders=a.max_orders, minutes=a.minutes))
        return 0
    if a.cmd == "run":
        return cmd_run(a.minutes, a.max_orders, a.wait)
    return cmd_inside(a.deadline, a.max_orders)


if __name__ == "__main__":
    raise SystemExit(main())
