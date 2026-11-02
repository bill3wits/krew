"""Door-view check: is the camera really looking at the door, and is the User stuck as a guest in front of it?
(the User 2026-10-06)

10-06 04:00-09:35: after a FaceFinder desk look the camera reported pan = door (432000, tilt 0, zoom 100) and idle,
but the picture stayed on the User's seat (head-framing left on; the firmware does not report what it moved). For five
hours every door glance saw the User and presence refused him (`guest_until_secure`, after an occupancy switch at
04:00), and every watcher stayed green because each asks the camera about itself. Nothing tried to get him back.

Two independent signals, asked by the janitor every tick:
  picture   door-view-picture.py (the Wall's; KREW_DOOR_VIEW_PICTURE names it): the live frame matched against the reference stops (door ~285 inliers at
            door, every other stop 5-24, measured 10-06). Off-door = best is not door, or door < PICTURE_MIN.
  glances   rail-presence's own journal: in the last WINDOW_S, door glances that keep seeing the SAME recognised face,
            refused (`[<face tag>] refuse guest_until_secure <id>`, the face engine's own journal tag) with no grant.
Both only count while the camera says it is idle at the door (no lease, not sweeping, not paused) - a look in
progress is not a fault.

Mode KREW_DOOR_VIEW_CHECK = report (default) | act | off:
  report  a janitor RED + one card per episode (blocked on Crissa; she decides and tells the User), a JSONL row.
  act     also: a REAL re-home (one step off the door pan, then return_door force - the same-value pan is what the
          firmware may ignore) and, for a stuck guest, ONE legitimate ensure-auth look (`/v1/ensure_user_auth`, a desk
          FaceFinder that grants only if the face matches). It never sets Secure and never grants auth itself; if the
          look does not grant, the card asks the User to press Secure.
Nothing user-specific lives here: the face id comes from presence's own log line.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

ROOT = Path(__file__).resolve().parent.parent
# krew: the camera and presence are Crissa's, reached through its Wall player (CRISSA_PLAYER, default
# http://127.0.0.1:8100: /v1/rail/camera/..., /v1/rail/presence/...); RAIL_CAMERA_URL / KREW_PRESENCE_URL name them directly.
_PLAYER = (os.environ.get("CRISSA_PLAYER") or "http://127.0.0.1:8100").rstrip("/")
CAMERA = (os.environ.get("RAIL_CAMERA_URL") or _PLAYER + "/v1/rail/camera").rstrip("/")
PRESENCE = (os.environ.get("KREW_PRESENCE_URL") or _PLAYER + "/v1/rail/presence").rstrip("/")
PRESENCE_UNIT = os.environ.get("KREW_PRESENCE_UNIT", "rail-presence")      # whose journal carries the door glances
FACE_TAG = os.environ.get("KREW_PRESENCE_FACE_TAG", "face")               # the face engine's journal tag: `[<tag>] refuse ...`
PICTURE_PY = os.environ.get("KREW_DOOR_VIEW_PY", (os.environ.get("KREW_PYTHON") or "/usr/bin/python3"))
# the picture matcher (door-view-picture.py) is the Wall's own script: name it with KREW_DOOR_VIEW_PICTURE, else the
# picture signal reads "not checked" and only the glances count
PICTURE_SCRIPT = Path(os.path.expanduser(os.environ.get("KREW_DOOR_VIEW_PICTURE") or str(ROOT / "scripts" / "door-view-picture.py")))
LOG = KH.hermes_home() / "door-view-check.jsonl"
WINDOW_S = int(os.environ.get("KREW_DOOR_VIEW_WINDOW_S", "1800"))  # glances arrive ~every 100 s, bursty (10-06)
MIN_GLANCES = 3
SAME_SHARE = 0.75
MIN_REFUSES = 3
PICTURE_MIN = 100          # door inliers at the door: 279-294 on 10-06; every other stop 5-24
PICTURE_RATIO = 3.0        # door must beat the next stop by this much

GLANCE = re.compile(r"\[door-glance\] faces=(\d+) names=\[([^\]]*)\]")
REFUSE = re.compile(r"\[" + re.escape(FACE_TAG) + r"\] refuse (\S+) (\S+)")
GRANT = re.compile(r"\[" + re.escape(FACE_TAG) + r"\] AUTH grant (\S+)")


def mode(env: dict | None = None) -> str:
    v = str((os.environ if env is None else env).get("KREW_DOOR_VIEW_CHECK", "report")).strip().lower()
    return v if v in ("off", "report", "act") else "report"


def _get(url: str, timeout: float = 4.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url: str, body: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def journal(since_s: int = WINDOW_S, run=subprocess.run) -> list[tuple[float, str]]:
    r = run(["journalctl", "--user", "-u", PRESENCE_UNIT, f"--since=-{int(since_s)}s", "-o", "short-unix",
             "--no-pager"], capture_output=True, text=True, timeout=20)
    out = []
    for line in (r.stdout or "").splitlines():
        if "[door-glance]" not in line and f"[{FACE_TAG}]" not in line:
            continue
        try:
            out.append((float(line.split(" ", 1)[0]), line))
        except ValueError:
            continue
    return out


def parse(lines: list[tuple[float, str]]) -> dict:
    g, ref, grants = [], [], []
    for ts, line in lines:
        if m := GLANCE.search(line):
            names = [n.strip().strip("'\"") for n in m.group(2).split(",") if n.strip()]
            g.append((ts, int(m.group(1)), names))
        elif m := REFUSE.search(line):
            ref.append((ts, m.group(1), m.group(2)))
        elif m := GRANT.search(line):
            grants.append((ts, m.group(1)))
    return {"glances": g, "refuses": ref, "grants": grants}


def camera_idle_at_door(h: dict) -> bool:
    return bool(h.get("at_door")) and not h.get("lease") and not h.get("sweeping") and not h.get("paused") \
        and not h.get("tool_hold")


def picture(run=subprocess.run) -> dict:
    if not PICTURE_SCRIPT.is_file():
        return {"ok": False, "error": f"not checked: no picture matcher at {PICTURE_SCRIPT} (KREW_DOOR_VIEW_PICTURE names it)"}
    try:
        r = run([PICTURE_PY, str(PICTURE_SCRIPT)], capture_output=True, text=True, timeout=60)
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}


def picture_off_door(p: dict) -> bool:
    if not p.get("ok"):
        return False
    scores = {k: v for k, v in p.items() if isinstance(v, int) and not isinstance(v, bool)}
    door = scores.get("door", 0)
    rest = max([v for k, v in scores.items() if k != "door"] or [0])
    return door < PICTURE_MIN or door < PICTURE_RATIO * max(rest, 1)


def stuck_guest(parsed: dict, now: float) -> str:
    """The face id that door glances keep seeing and presence keeps refusing, or ''."""
    since = now - WINDOW_S
    gl = [x for x in parsed["glances"] if x[0] >= since]
    if len(gl) < MIN_GLANCES:
        return ""
    counts: dict[str, int] = {}
    for _ts, _n, names in gl:
        for n in set(names):
            counts[n] = counts.get(n, 0) + 1
    if not counts:
        return ""
    who, seen = max(counts.items(), key=lambda kv: kv[1])
    if seen < SAME_SHARE * len(gl):
        return ""
    refused = sum(1 for ts, _why, n in parsed["refuses"] if ts >= since and n == who)
    granted = any(ts >= since and n == who for ts, n in parsed["grants"])
    return who if refused >= MIN_REFUSES and not granted else ""


def check(now: float | None = None, *, health=None, lines=None, pic=None) -> dict:
    now = time.time() if now is None else now
    try:
        h = health if health is not None else _get(f"{CAMERA}/health")
    except (OSError, ValueError) as e:     # krew: no camera answering (Crissa not installed) is not a door fault
        return {"state": "ok", "why": f"camera not reachable at {CAMERA} ({type(e).__name__}): Crissa not installed?"}
    if not camera_idle_at_door(h):
        return {"state": "ok", "why": "camera busy or not claiming the door", "at_door": h.get("at_door")}
    p = pic if pic is not None else picture()
    parsed = parse(lines if lines is not None else journal())
    who = stuck_guest(parsed, now)
    off = picture_off_door(p)
    fw = h.get("firmware") or {}
    ev = {"picture": p, "frame_on": fw.get("frame_on"), "track_on": fw.get("track_on"),
          "glances": len([g for g in parsed["glances"] if g[0] >= now - WINDOW_S]), "stuck_guest": who}
    if off or who:
        why = []
        if off:
            why.append(f"the picture is not the door (best match {p.get('best')}, door {p.get('door')} inliers)")
        if who:
            why.append(f"door glances keep seeing '{who}' and presence keeps refusing them (no grant in "
                       f"{WINDOW_S // 60} min)")
        return {"state": "suspect", "off_door": off, "stuck_guest": who, "why": "; ".join(why), **ev}
    return {"state": "ok", "why": "picture is the door, no stuck guest", **ev}


def rehome(h: dict, post=_post, sleep=time.sleep) -> dict:
    """A real move: one step off the door pan, then return_door force (the same-value pan may be a firmware no-op).
    ALWAYS ends at the door (the User 2026-10-06): return_door runs in `finally`, retried until the readback is the
    door pan, even when the nudge failed. The camera service holds no auth or Secure state - this moves glass only."""
    door = int(h.get("door_home_pan") or 432000)
    step = -3600 if door > 0 else 3600
    out: dict = {"nudge": False, "return_door": False, "pan_actual": None, "tries": 0}
    try:
        a = post(f"{CAMERA}/v1/aim", {"pan": door + step, "tilt": 0, "zoom": 100}, timeout=15)
        out["nudge"] = bool(a.get("ok", True))
        sleep(1.0)
    except Exception as e:  # noqa: BLE001 - the return to the door below still runs
        out["nudge_error"] = f"{type(e).__name__}"
    finally:
        for i in range(3):
            out["tries"] = i + 1
            try:
                b = post(f"{CAMERA}/v1/return_door", {"force": True}, timeout=30)
                out["return_door"], out["pan_actual"] = bool(b.get("ok")), b.get("pan_actual")
                if b.get("ok") and abs(int(b.get("pan_actual") or 0) - door) <= 3600:
                    break
            except Exception as e:  # noqa: BLE001
                out["return_error"] = f"{type(e).__name__}"
            sleep(2.0)
    out["at_door"] = bool(out["return_door"]) and out["pan_actual"] is not None and abs(int(out["pan_actual"]) - door) <= 3600
    return out


GUARDED = ("auth", "secure", "hold")     # User auth, Secure, Double Secure (hold = the sudo-password Secure)


def guard_snapshot(get=_get) -> dict:
    """What act mode must never turn off: User auth, Secure, Double Secure, as presence reports them."""
    d = get(f"{PRESENCE}/v1/presence")
    u = d.get("user") or {}
    v = d.get("visit_lamp_info") or {}
    return {"auth": bool(u.get("auth")), "secure": bool(v.get("secure")), "hold": bool(v.get("hold"))}


def act(r: dict, h: dict, *, get=_get, post=_post, sleep=time.sleep) -> dict:
    """Act mode, guarded (the User 2026-10-06): never turn off Double Secure, Secure or User auth, and always end with the
    camera at the door. Reads the three before and after and reports any that went off as a violation. Asks for an auth
    look only for a stuck guest with none of the three on - presence decides; nothing here grants or sets Secure."""
    try:
        before = guard_snapshot(get)
    except Exception as e:  # noqa: BLE001 - cannot prove the guard: move the camera home only
        before = None
        guard_err = f"{type(e).__name__}"
    out: dict = {"before": before}
    if before is None:
        out["guard_error"] = guard_err
    out["rehome"] = rehome(h, post=post, sleep=sleep)
    who = r.get("stuck_guest")
    if who and before is not None and not any(before.values()):
        out["auth"] = ask_auth(who, post=post)
    elif who:
        out["auth"] = {"skipped": "auth, Secure or Double Secure already on - or not provable"}
    try:
        after = guard_snapshot(get)
        out["after"] = after
        if before is not None:
            out["violations"] = [k for k in GUARDED if before.get(k) and not after.get(k)]
    except Exception as e:  # noqa: BLE001
        out["after_error"] = f"{type(e).__name__}"
    return out


def ask_auth(who: str, post=_post) -> dict:
    """ONE legitimate face check - presence decides; nothing here grants."""
    try:
        r = post(f"{PRESENCE}/v1/ensure_user_auth",
                 {"person_id": who, "voice_matched": False, "reason": "door_view_stuck_guest",
                  "force_facefind": True, "mode": "desk"}, timeout=90)
        return {"auth": bool(r.get("auth")), "reason": str(r.get("reason") or "")[:80]}
    except Exception as e:  # noqa: BLE001
        return {"auth": False, "reason": f"ensure_fail: {type(e).__name__}"}


def log(row: dict, path: Path | None = None) -> None:
    try:
        p = path or LOG
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), **row}) + "\n")
    except OSError:
        pass
