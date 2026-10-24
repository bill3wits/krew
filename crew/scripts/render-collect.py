#!/usr/bin/env python3
"""Collect finished renders (the other half of render-submit.py).

Runs from krew-hold-sweep.service every 2 min. For each pending job in
~/.hermes/render-jobs.json: ask Comfy /history; when the mp4 exists, copy it
to the take folder as take-N.mp4, cut f0.png / last.png, score both ends
against the plate on the wall crop (pose-fade-join.ncc, the same 736x400
ZFRAME the hub check uses), write take-N.json, comment the numbers on the
card with the f0/last attached, and put a blocked card back to ready so the
worker's next turn is judging only. When no job is pending any more and the
card still stamps the GPU hold, release it and start ops (the way back).
"""
from __future__ import annotations
import pwd
import importlib, json, os, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
# The krew-mode binary. An env override so a test can point it at a stub, the
# same shape as render-submit.py (a87be473): the absolute path here is exactly
# what let a test's release_hold_for escape into the live house — a PATH stub
# cannot intercept a caller that importlib-exec's the real module in-process.
# One rule for every krew caller (crew/lib/krew_mode_cmd.py): KREW_MODE_BIN, else KREW_MODE_PATH, else the
# install's $KREW_HOME/bin/krew-mode link, else the krew-mode shipped beside this script.
sys.path.insert(0, str(ROOT / "lib"))
import krew_mode_cmd  # noqa: E402
KREW_MODE_BIN = krew_mode_cmd.command()
# The real krew-mode script (never the test stub). Used only by the
# `krew-mode normal` restore path below, which MUST always target the real
# binary even when a test points KREW_MODE_BIN at a stub — a stubbed restore
# would silently skip waking ops.
KREW_MODE_PATH = krew_mode_cmd.path()
# ONE jobs file for the house, whatever HERMES_HOME a worker runs under
# (Vera's worker wrote take 3 into profiles/vera/ and the collector never saw it, 2026-09-11 11:59)
# THE HOUSE'S OWN HOME, not the worker's. A kanban worker runs with
# HOME=~/.hermes/profiles/<who>/home, so Path.home() sent Vera's bash-work
# job to a jobs file the collector never reads (2026-09-12). The real
# account home from the passwd entry is the same for every caller.
HOUSE = Path(os.environ.get("KREW_HERMES") or (Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes"))
JOBS = HOUSE / "render-jobs.json"
LEDGER = HOUSE / "krew-gpu-hold.jsonl"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import krew_comfy  # noqa: E402  one Comfy location for krew and the Wall: render.json, else KREW_COMFY_*
COMFY = krew_comfy.get("url")
OUT_DIR = Path(krew_comfy.get("output"))
STALE_H = 5.0
sys.path.insert(0, str(ROOT / "scripts"))



def _start_unit(unit: str) -> subprocess.CompletedProcess:
    """Start a freed unit. Under ONE host gateway (KREW_GATEWAY_TOPOLOGY=host) a per-profile
    gateway name means "unpark that profile" (lib/hermes_topology); everything else, and the
    default `units`, is the systemctl start it always was."""
    try:
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "lib" / "hermes_topology.py"   # krew's own, by path
        if not path.is_file():
            if os.environ.get("KREW_GATEWAY_TOPOLOGY", "").strip().lower() == "host":
                print(f"render-collect: KREW_GATEWAY_TOPOLOGY=host but {path} is missing; {unit} is started "
                      "as a unit", file=sys.stderr)
            raise ImportError(f"no {path}")
        spec = importlib.util.spec_from_file_location("krew_own_hermes_topology", path)
        topo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(topo)
        prof = topo.profile_of_unit(unit) if topo.is_host() else None
        if prof:
            ok = topo.unpark(prof)
            return subprocess.CompletedProcess(["unpark", prof], 0 if ok else 1, "", "" if ok else "unpark failed")
    except subprocess.TimeoutExpired:
        raise
    except Exception:  # noqa: BLE001
        pass
    return subprocess.run(["systemctl", "--user", "start", unit], capture_output=True, text=True, timeout=30)

def log(ev: dict) -> None:
    try:
        ev = dict(ev); ev.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S")); ev.setdefault("by", "render-collect")
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass


def kanban(*args: str) -> str:
    try:
        return subprocess.run(["hermes", "kanban", *args], capture_output=True, text=True, timeout=90).stdout
    except Exception as exc:
        return f"kanban failed: {exc}"


def card_status(card: str) -> str | None:
    import sqlite3
    for db in sorted((HOUSE / "kanban" / "boards").glob("*/kanban.db")):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
            row = con.execute("select status from tasks where id=?", (card,)).fetchone(); con.close()
        except sqlite3.Error:
            continue
        if row:
            return str(row[0] or "")
    return None


def history(pid: str) -> dict | None:
    try:
        h = json.loads(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=8).read())
    except Exception:
        return None
    return h.get(pid)


def output_file(rec: dict) -> Path | None:
    for node in (rec.get("outputs") or {}).values():
        for key in ("videos", "gifs", "images"):
            for it in node.get(key) or []:
                name = it.get("filename")
                if name and name.lower().endswith((".mp4", ".webm", ".mov")):
                    p = OUT_DIR / (it.get("subfolder") or "") / name
                    if p.is_file():
                        return p
    return None


def extract_ends(mp4: Path, f0: Path, last: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-vframes", "1", str(f0)], timeout=120)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.05", "-i", str(mp4), "-vframes", "1", str(last)], timeout=120)


def ncc_ends(mp4: Path, plate: Path, f0: Path, last: Path, *, extract: bool = True) -> tuple[float | None, float | None]:
    if extract:
        extract_ends(mp4, f0, last)
    try:
        import cv2
        m = importlib.import_module("pose-fade-join")
        a = cv2.resize(cv2.imread(str(plate)), (736, 400))
        def s(p):
            im = cv2.imread(str(p))
            return round(m.ncc(cv2.resize(im, (736, 400)), a), 3) if im is not None else None
        return s(f0), s(last)
    except Exception:
        return None, None


def release_hold_for(card: str) -> None:
    """No job pending for this card any more: give the GPU back."""
    # krew-mode is driven through KREW_MODE_BIN as a SUBPROCESS (a87be473
    # shape, mirrored from render-submit.py). The old code importlib-exec'd the
    # real module in-process at an absolute path — a test with a PATH stub
    # pointing at a fake krew-mode still ran the live module body here.
    # `release-card` prints the freed units, one per line; it starts nothing,
    # so the collector stays the sole starter of what it freed.
    try:
        r = subprocess.run([*KREW_MODE_BIN, "release-card", card],
                           capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as e:
        log({"event": "release_failed", "card": card,
             "err": str(e)[:200], "why": "render collected"})
        return
    freed = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
    # release-card promises units, one per line (krew-mode cmd_release_card).
    # A misbehaving krew-mode printing prose before the units would otherwise
    # feed "start <prose>" to systemctl — bounded and logged, but drop it here:
    # the 3-unit whitelist below is the safety net, this is the first line.
    freed = [u for u in freed if u.endswith(".service")]
    if r.returncode != 0:
        log({"event": "release_failed", "card": card, "rc": r.returncode,
             "err": (r.stderr or "")[:200], "why": "render collected"})
        return
    log({"event": "released", "card": card, "units": freed, "why": "render collected"})
    for unit in freed:
        if unit in ("ops.service", "crew.service", "hermes-zoey-gateway.service"):
            # The hold release is the way back: start the unit, but NEVER wait
            # unbounded for it. A slow ops start (model reload, VRAM settle)
            # must not wedge the 2-min sweep — the collector's job is to land
            # the render, not to babysit the brain's boot. The unit's own
            # TimeoutStartSec (ops 600 / crew 900) is the systemd backstop;
            # here we cap the collector's wait at 30 s and move on: if the
            # unit is still coming up, the janitor / next sweep will notice.
            try:
                r = _start_unit(unit)
            except subprocess.TimeoutExpired:
                log({"event": "start_slow", "unit": unit,
                     "why": "still starting after 30 s — sweep not blocked, janitor will notice"})
                continue
            log({"event": "started", "unit": unit, "rc": r.returncode})
    # THE WAY BACK: krew-mode render parks ops in the state's parked[] (not
    # always on the hold file), so `krew-mode normal` is what wakes it —
    # detached, like the hold plugin does, so this tick never blocks on ops load
    try:
        mode = (HOUSE / "krew-gpu-mode").read_text().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        mode = ""
    if mode == "render":
        unit = "krew-mode-restore-collect-%d" % int(time.time())
        r = subprocess.run(["systemd-run", "--user", "--collect", "--quiet", "--unit", unit,
                            "--description", "krew-mode normal after render for %s" % card,
                            "python3", KREW_MODE_PATH, "normal"],
                           capture_output=True, text=True, timeout=20)
        log({"event": "restore_launched", "card": card, "unit": unit, "rc": r.returncode})


def main() -> int:
    try:
        jobs = json.loads(JOBS.read_text())
    except (OSError, ValueError):
        jobs = []
    changed = False
    for j in jobs:
        if j.get("status") != "pending":
            continue
        pid = j.get("prompt_id") or ""
        rec = history(pid)
        age_h = (time.time() - time.mktime(time.strptime(j["submitted"], "%Y-%m-%dT%H:%M:%S"))) / 3600.0
        if rec is None or not rec.get("outputs"):
            st = ((rec or {}).get("status") or {})
            if st.get("status_str") == "error":
                msg = "; ".join(str(m) for m in (st.get("messages") or [])[-2:])[:300]
                j["status"] = "error"; j["error"] = msg; changed = True
                kanban("comment", j["card"], f"render-collect: take {j['take']} FAILED on Comfy ({pid[:8]}): {msg}. Fix the prompt or the seed and submit again.")
                log({"event": "render_error", "card": j["card"], "prompt": pid, "msg": msg})
            elif age_h > STALE_H and not j.get("stale_noted"):
                j["stale_noted"] = True; changed = True
                kanban("comment", j["card"], f"render-collect: take {j['take']} ({pid[:8]}) has been queued {age_h:.1f} h with no output — Comfy may have lost it. Check :8188/queue.")
            continue
        src = output_file(rec)
        if src is None:
            continue
        out_dir = Path(j["out_dir"]); out_dir.mkdir(parents=True, exist_ok=True)
        n = j["take"]
        mp4 = out_dir / f"take-{n}.mp4"; f0 = out_dir / f"take-{n}.f0.png"; last = out_dir / f"take-{n}.last.png"
        shutil.copy2(src, mp4)
        s0, s1 = ncc_ends(mp4, Path(j["plate"]), f0, last)
        if j.get("plate_last"):            # a slide: the last frame is scored against the OTHER hub
            _, s1 = ncc_ends(mp4, Path(j["plate_last"]), f0, last, extract=False)
        verdict = "ends on the plate" if (s0 or 0) >= 0.97 and (s1 or 0) >= 0.97 else "OFF the plate — do not install"
        j.update(status="done", mp4=str(mp4), ncc_f0=s0, ncc_last=s1, collected=time.strftime("%Y-%m-%dT%H:%M:%S"))
        changed = True
        (out_dir / f"take-{n}.json").write_text(json.dumps(j, indent=1) + "\n")
        kanban("comment", j["card"], f"render-collect: take {n} landed — {mp4.name} ({j['size']}, {j['length']} frames @ {j['fps']}, {age_h*60:.0f} min). "
               f"NCC vs plate: f0 {s0}, last {s1} → {verdict} (bar 0.97 both ends). "
               f"JUDGING TURN: vision_analyze take-{n}.f0.png, take-{n}.last.png and the mp4; say what the bun, the eyes and the landing do; "
               f"then either `hermes kanban complete` with the verdict or submit take {n+1} with a changed prompt (python3 scripts/render-submit.py --card {j['card']} --name {j['name']} ... ). Three takes max. Never install; never touch inventory.json.")
        for p in (f0, last):
            kanban("attach", j["card"], str(p))
        if card_status(j["card"]) == "blocked":
            kanban("unblock", j["card"], "--reason", f"render take {n} landed; judging turn")
        # THE DELIVERABLE LANDS ITSELF (the User 2026-09-11: "what is the best way,
        # thinking Ada, to enforce deliverables there?" — the mechanism writes
        # ~/Desktop/krew-deliverables/krew-renders/<name>/, not the worker)
        try:
            dl = Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES") or "~/Desktop/krew-deliverables")) / "krew-renders" / j["name"]
            dl.mkdir(parents=True, exist_ok=True)
            for p in (mp4, f0, last):
                shutil.copy2(p, dl / p.name)
            md = dl / f"take-{n}.md"
            md.write_text(f"# {j['name']} — take {n}\n\nCard {j['card']} · {j['size']} · {j['length']} frames @ {j['fps']} · {j['steps']} steps · seed `{Path(j['seed']).name}` · {age_h*60:.0f} min on Comfy\n\n"
                          f"NCC vs plate on the wall crop: f0 **{s0}**, last **{s1}** → {verdict} (bar 0.97).\n\n"
                          f"![f0]({f0.name}) ![last]({last.name})\n\n`{mp4.name}` beside this file. Prompt:\n\n> {j['prompt']}\n")
            subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-report-html.py"), "--file", str(md)], capture_output=True, timeout=60)
            j["deliverable"] = str(md)
        except Exception as exc:
            log({"event": "deliverable_failed", "card": j["card"], "error": repr(exc)[:160]})
        log({"event": "render_landed", "card": j["card"], "take": n, "mp4": str(mp4), "ncc": [s0, s1]})
        print(f"landed {mp4} ncc f0={s0} last={s1}")
    if changed:
        JOBS.write_text(json.dumps(jobs, indent=1) + "\n")
    # the way back: a card with nothing pending gives the GPU back
    # a fresh `preparing` row is a submit mid-park: do not give its GPU back under it
    pending_cards = {j["card"] for j in jobs if j.get("status") in ("pending", "preparing")}
    for card in {j["card"] for j in jobs if j.get("status") in ("done", "error") and not j.get("hold_released")}:
        if card in pending_cards:
            continue
        release_hold_for(card)
        for j in jobs:
            if j["card"] == card:
                j["hold_released"] = True
        JOBS.write_text(json.dumps(jobs, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
