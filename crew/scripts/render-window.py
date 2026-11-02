#!/usr/bin/env python3
"""render-window — the crew's renders run in ONE planned batch inside the User's off-hours.

the User 2026-09-17: "If we take the trouble to order it, we need to give Ada the resources
to judge them for that window. A batch should qualify and the est time to complete
relative to the kanban action. You pick reasonable times for thresh. I know days like
today and yesterday, they all should be scheduled. AND that window should defer to next
night if kanban not clear."

Before this nothing looked at the clock: a render card dispatched at 14:00 flipped the
house to Render at once (render-submit parks ops), whatever the User was doing.

  DAY (and the window before the render slot)  a render card that turns ready is HELD:
      comment `gate: render window` + block. `render-now` in its body or a Crissa/User
      comment exempts it. A spawn in the ≤2 min before a tick cannot park ops:
      render-submit and krew-comfy refuse a kanban worker outside the window
      (and, at night, until the card is in tonight's released batch).
  SLOT OPENS  inside the window, once the evening chain has a verdict (the window
      ticker's pose-freelance + improvement-outcomes jobs; the User 09-30 kept both after
      t_ab8aefea had dropped improvement-outcomes). A FAILED verdict counts as final: the window ticker's final() treats any status
      other than "started" (ok / failed / did not run) as final, so a chain job that
      FINISHED releases the slot; the hold is on the job not finishing, not on its
      exit code. And while NO pose render is queued, the slot does not wait for the
      verdict at all — pose chains have never run (the 45-min estimate is
      unknown-heavy), so on a pose-free night the batch opens at the first tick, on
      the board-clear + stop-line budget alone. ALL held render cards form tonight's
      batch, oldest first, each with an estimate by recipe.
  ADA JUDGES  one card, `Render window <date>: N renders, ~M min`, with the batch table.
      She has REVIEW_MIN to comment `skip t_x` · `first t_y` · `defer` · `go`; silence
      releases the batch as planned.
  RELEASE  only while the board is CLEAR (non-render running <= CLEAR_RUNNING_MAX,
      non-render ready <= CLEAR_READY_MAX; gate cards on Crissa's queue and parks do not
      count), and only what fits before the stop line (close - STOP_BEFORE_CLOSE_MIN).
  DEFER  not clear by the latest start (stop line - first render's estimate - switch -
      margin) → the whole batch waits for tomorrow's window and goes first then.
  STOP LINE  released cards that have not started by close - STOP_BEFORE_CLOSE_MIN are
      held again: nothing spills into the User's day.

Estimates are card-level minutes (render + worker overhead), from the measured
2026-09-11..17 history: flux2 stills 30 s / qwen-edit 75 s of Comfy time, ltx25-flf2v
submit→collected median 2.8 min p90 4.7, wan22-i2v 19-31 min, render-card runs median
2 min p90 14.4; Standard↔Render switches 1.3-2 min each way. Pose chains have never
run; they get the unknown-heavy estimate until one does.

Reads the board read-only (sqlite); writes only through `hermes -p ada kanban`. State:
~/.hermes/render-window.json. Off: ~/.hermes/render-window.off or KREW_RENDER_WINDOW=0.
The window is the one global (lib/krew_hours.py); unset → this does nothing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT.parent / "window" / "lib"))   # krew: krew_hours lives with the window
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)
import krew_hours  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HOME_H = Path(os.environ.get("KREW_HERMES") or KH.hermes_home())
STATE = Path(os.environ.get("KREW_RENDER_WINDOW_STATE", str(HOME_H / "render-window.json")))
TICK_STATE = Path(os.environ.get("KREW_WINDOW_STATE", str(HOME_H / "krew-window-tick.json")))
OFF = HOME_H / "render-window.off"
HERMES = os.environ.get("KREW_HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
LOG = HOME_H / "logs" / "render-window.log"
MARK = "gate: render window"
AUTHOR = "render-window"

# ── thresholds (the User: "You pick reasonable times for thresh") ──────────────────────
EST_MIN = {"still": 5, "edit": 6, "ltx": 10, "wan": 40, "pose-chain": 45, "unknown": 20}
SWITCH_MIN = 5              # Standard -> Render and back (measured 1.3-2 min each)
MARGIN_MIN = 20             # slack for a retry / a slow first load
REVIEW_MIN = 15             # Ada's look before a silent release
STOP_BEFORE_CLOSE_MIN = 30  # nothing starts in the last half hour; unstarted cards are held again
# A DAYTIME SLOT (the User, 2026-09-22). Render cards could only run 20:00-02:00, which is
# 18 hours a day closed. That gate is what turned ONE logical change — the Comfy v0.36.0 ->
# v0.37.0 swap and smoke — into 28 cards over three days and the single NEEDS HUMAN card on
# the board, and it is why the User pressed Standard by hand at 17:00 on 09-21.
#
# The slot is deliberately narrow: it is the nightly batch's own clear-board test plus the
# facts that say nobody is using the machine. It never runs inside the User's quiet hours, it
# takes ONE card (not a batch — a batch is what the night is for), and it uses the same
# "silence means go" review the night uses, which is the only self-clearing timeout in the
# tree. Kill KREW_RENDER_DAY_SLOT=0.
DAY_SLOT_ON = os.environ.get("KREW_RENDER_DAY_SLOT", "1").strip().lower() not in ("0", "false", "no", "off")
DAY_SLOT_MAX_CARDS = 1
DAY_SLOT_MIN_MIN = int(os.environ.get("KREW_RENDER_DAY_MIN_MIN", "60") or 60)
CLEAR_RUNNING_MAX = 1       # non-render workers running
CLEAR_READY_MAX = 2         # non-render cards ready to dispatch
# The evening chain the slot waits on: BOTH jobs (the User 09-30, landing t_ab8aefea's
# leftover). t_ab8aefea (09-24, archived) had cut this to pose-freelance alone because the
# pair's verdict landed ~280 min into a 360-min window and the slot idled; the User kept
# the pair. What t_ab8aefea also added stays: while no pose render is queued the verdict is
# not waited on at all (_pose_work_queued, in tick()).
CHAIN_JOBS = ("pose-freelance", "improvement-outcomes")   # the evening chain the slot waits on


def _pose_work_queued(cards: list[dict]) -> bool:
    """A pose render is held or dispatchable tonight. While one exists, the slot must have
    the pose-freelance verdict before it opens; while none does, the slot does not wait —
    pose chains have never run, so waiting a 45-min unknown-heavy estimate for nothing
    would recreate exactly the idle-night this slot exists to end."""
    return any(c["render"] and c["recipe"] == "pose-chain" and not c["exempt"]
               and (c["held"] or c["status"] in ("ready", "todo")) for c in cards)


def chain_done(tick_state: dict, key: str, wait_on: tuple[str, ...] = CHAIN_JOBS) -> bool:
    if not wait_on:
        return True
    if tick_state.get("window_key") != key:
        return False
    fired = tick_state.get("fired") or {}
    for job in wait_on:
        rec = fired.get(job)
        if not rec or rec.get("status") in (None, "started"):
            return False
    # A FAILED verdict counts as a verdict: the window ticker's final() already treats any
    # status other than "started" (ok / failed / did not run) as final — the hold is on the
    # job not FINISHING, never on its exit code (documented 2026-09-24, t_ab8aefea).
    return True

# The board gate's classifier misses cards that never name a tool: "Run flux2-turbo hub
# smoke (host hands)" (t_5df2faa8) was not held and its worker parked ops at 13:41 in the
# User's day. A recipe or engine name in the TITLE is enough — being held only moves
# a card to tonight's batch, and Ada can skip it.
HOLD_REASON = "render window: waits for the off-hours render slot"
NOW_RE = re.compile(r"\brender-now\b", re.I)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} render-window: {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ── what is a render card ────────────────────────────────────────────────────────────
def _gate_classifier():
    """The board gate's own classifier (scripts/kanban-board-gate.py), not a second copy."""
    p = ROOT / "scripts" / "kanban-board-gate.py"
    try:
        spec = importlib.util.spec_from_file_location("kanban_board_gate_ro", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m._is_render_card
    except Exception:  # noqa: BLE001
        return None


_GATE = None


def is_render_card(title: str, body: str, skills: str) -> bool:
    """True if this card is a render/mint job the window holds for the off-hours
    slot. Single source of truth: the board gate's own classifier
    (scripts/kanban-board-gate.py::_is_render_card). The former local
    EXTRA_RENDER_RE second copy — a bare comfy/recipe match that also searched
    the body — is gone: it misclassified the t_6698facd code-fix card on a
    prose ComfyUI mention and held it 4.5h (2026-09-22, t_122fe536)."""
    global _GATE
    if _GATE is None:
        _GATE = _gate_classifier() or (lambda *a: False)
    return bool(_GATE(title or "", body or "", skills or ""))


def recipe_of(title: str, body: str) -> str:
    t = f"{title or ''}\n{body or ''}".lower()
    if "wan22" in t or "wan 2.2" in t or "i2v" in t and "ltx" not in t:
        return "wan"
    if "freelance chain" in t or "pose-mint" in t:
        return "pose-chain"
    if "ltx" in t or "flf2v" in t:
        return "ltx"
    if "qwen" in t and "edit" in t:
        return "edit"
    if "flux" in t or "klein" in t or "still" in t or "portrait" in t or "image_generate" in t:
        return "still"
    return "unknown"


# ── the board (read-only) ────────────────────────────────────────────────────────────
def board_db() -> Path | None:
    try:
        slug = (HOME_H / "kanban" / "current").read_text().strip()
    except OSError:
        return None
    p = HOME_H / "kanban" / "boards" / slug / "kanban.db"
    return p if slug and p.is_file() else None


def read_board(db: Path | None = None) -> list[dict]:
    db = db or board_db()
    if not db:
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        rows = []
        for tid, title, body, status, skills, created, block_kind in con.execute(
                "SELECT id, title, body, status, skills, created_at, block_kind FROM tasks "
                "WHERE status IN ('ready','todo','running','blocked','triage')"):
            comments = [{"author": a or "", "body": b or ""} for a, b in con.execute(
                "SELECT author, body FROM task_comments WHERE task_id = ? ORDER BY id", (tid,))]
            last = con.execute("SELECT payload FROM task_events WHERE task_id = ? AND kind = 'blocked' "
                               "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
            try:
                last_block = str((json.loads(last[0]) or {}).get("reason") or "") if last else ""
            except (json.JSONDecodeError, TypeError, AttributeError):
                last_block = ""
            rows.append({"id": tid, "title": title or "", "body": body or "", "status": status or "",
                         "skills": skills or "", "created_at": float(created or 0),
                         "block_kind": block_kind or "", "comments": comments, "last_block": last_block})
        return rows
    finally:
        con.close()


def annotate(cards: list[dict]) -> list[dict]:
    for c in cards:
        c["render"] = is_render_card(c["title"], c["body"], c["skills"])
        c["held"] = any(MARK in x["body"] for x in c["comments"]) and c["status"] == "blocked"
        c["exempt"] = bool(NOW_RE.search(c["body"])) or any(
            NOW_RE.search(x["body"]) and x["author"].lower() in tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(",")) for x in c["comments"])
        c["recipe"] = recipe_of(c["title"], c["body"]) if c["render"] else ""
        c["est_min"] = EST_MIN[c["recipe"]] if c["render"] else 0
    return cards


def misheld(cards: list[dict]) -> list[str]:
    """Cards this window holds that are not render cards (held before the title-only rule, or
    re-titled since). The nightly release only takes render cards, so these would wait for
    ever. Only a card whose LAST block is the window's own: one that was held, released and
    then blocked for another reason (a worker's needs_input) still carries the hold comment."""
    return [c["id"] for c in cards if c["held"] and not c["render"]
            and (c.get("last_block") or "").startswith(HOLD_REASON[:14])]


def board_load(cards: list[dict]) -> dict:
    """Non-render work in flight. Crissa's gate queue (needs_input) and parks are not load."""
    running = sum(1 for c in cards if c["status"] == "running" and not c["render"])
    ready = sum(1 for c in cards if c["status"] == "ready" and not c["render"])
    render_running = sum(1 for c in cards if c["status"] == "running" and c["render"])
    return {"running": running, "ready": ready, "render_running": render_running,
            "clear": running <= CLEAR_RUNNING_MAX and ready <= CLEAR_READY_MAX}

# ── the plan (pure) ──────────────────────────────────────────────────────────────────
def plan_batch(cards: list[dict], left_min: int, skip: set[str] = frozenset(), first: list[str] = ()) -> dict:
    """Tonight's batch: held + dispatchable render cards, oldest first (Ada's `first` ahead),
    cut at what fits before the stop line. The rest carry to tomorrow."""
    pool = [c for c in cards if c["render"] and not c["exempt"] and c["id"] not in skip
            and (c["held"] or c["status"] in ("ready", "todo"))]
    pool.sort(key=lambda c: (0 if c["id"] in first else 1, c["created_at"]))
    budget = left_min - STOP_BEFORE_CLOSE_MIN - SWITCH_MIN - MARGIN_MIN
    fit, carry, used = [], [], 0
    for c in pool:
        if used + c["est_min"] <= budget:
            fit.append(c)
            used += c["est_min"]
        else:
            carry.append(c)
    return {"fit": fit, "carry": carry, "est_min": used, "budget_min": max(budget, 0),
            "latest_start_left": STOP_BEFORE_CLOSE_MIN + SWITCH_MIN + MARGIN_MIN + (fit[0]["est_min"] if fit else 0)}


def ada_rulings(comments: list[dict]) -> dict:
    """Ada's (or Crissa's / the User's) words on the batch card."""
    out = {"skip": set(), "first": [], "defer": False, "go": False}
    for c in comments:
        if c["author"].lower() not in ("ada",) + tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(",")):
            continue
        b = c["body"]
        out["skip"] |= set(re.findall(r"\bskip\s+(t_[0-9a-f]{8})", b, re.I))
        out["first"] += [x for x in re.findall(r"\bfirst\s+(t_[0-9a-f]{8})", b, re.I) if x not in out["first"]]
        if re.search(r"^\s*defer\b", b, re.I | re.M):
            out["defer"] = True
        if re.search(r"^\s*go\b", b, re.I | re.M):
            out["go"] = True
    return out


# her presence feed, through the Wall's player (Crissa's /v1/rail/presence/); KREW_PRESENCE_URL moves it
CRISSA_PLAYER = os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/")
PRESENCE_URL = os.environ.get("KREW_PRESENCE_URL") or (CRISSA_PLAYER + "/v1/rail/presence/v1/presence")
GPU_MODE_FILE = Path(os.environ.get("KREW_GPU_MODE_FILE") or (HOME_H / "krew-gpu-mode"))
GPU_HOLD_FILE = Path(os.environ.get("KREW_TRAIN_HOLD", "/tmp/krew-train-hold"))
PRESENCE_MAX_AGE_S = float(os.environ.get("KREW_PRESENCE_MAX_AGE_S", "30") or 30)
USER_NAMES = {n.strip().lower() for n in (os.environ.get("KREW_USER_NAMES") or "you,user").split(",") if n.strip()}


def day_slot_ok(*, now: dt.datetime | None = None) -> tuple[bool, str]:
    """Is the house idle enough to render in the User's day? (ok, why not).

    Every fact here fails to NO. In particular presence: `crissa-needs-watch` treats an
    unreadable presence service as "absent" because it is deciding whether to SPEAK, and
    silence is the safe side there. Here the polarity is opposite — an unreadable service
    must mean "someone may be here", or a dead probe silently licenses renders all day.
    """
    if not DAY_SLOT_ON:
        return False, "KREW_RENDER_DAY_SLOT=0"
    try:
        mode = (GPU_MODE_FILE.read_text().splitlines() or [""])[0].strip().lower()
    except OSError:
        return False, "cannot read the occupancy flag"
    if mode != "normal":
        return False, f"occupancy is {mode or '?'}, not Standard"
    try:
        if GPU_HOLD_FILE.is_file() and GPU_HOLD_FILE.read_text().strip():
            return False, "a GPU hold is live"
    except OSError:
        return False, "cannot read the GPU hold"
    # Quiet hours are the User's DOWN hours; this slot is for his UP hours, and the night
    # batch owns the rest. Being outside the window already implies this, but the window and
    # quiet hours are separately configurable (krew_hours.quiet_hours), so ask.
    try:
        if krew_hours.contains(now or dt.datetime.now(), krew_hours.quiet_hours()["window"]):
            return False, "inside the User's quiet hours — the night batch owns this"
    except (KeyError, TypeError, ValueError):
        pass
    try:
        with urllib.request.urlopen(PRESENCE_URL, timeout=1.5) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:          # the player answered: the presence feed is missing or down
        try:
            why = (json.loads(e.read() or b"{}") or {}).get("why") or e.reason
        except ValueError:
            why = e.reason
        return False, f"presence through the player said {e.code}: {why}"
    except Exception as e:                       # noqa: BLE001 — any failure is "someone may be here"
        if isinstance(getattr(e, "reason", e), ConnectionRefusedError) or isinstance(e, ConnectionRefusedError):
            return False, f"Crissa not installed (no player at {CRISSA_PLAYER}) — no presence feed"
        return False, f"presence unreadable ({type(e).__name__})"
    # POSITIVE evidence of an empty room from a WORKING camera. An empty `people` list is
    # not evidence on its own: a closed, blind or stale camera also reports nobody, and a
    # broken probe must never silently license renders all day.
    if not body.get("camera_open") or body.get("camera_blind"):
        return False, "the camera is closed or blind — cannot see the room"
    try:
        age = float(body.get("frame_age_sec"))
    except (TypeError, ValueError):
        return False, "presence has no frame age"
    if age > PRESENCE_MAX_AGE_S:
        return False, f"the newest frame is {age:.0f}s old"
    if int(body.get("headcount") or 0) > 0:
        return False, f"{body.get('headcount')} person(s) in the room"
    for person in body.get("people") or []:
        if not isinstance(person, dict):
            continue
        for k in ("name", "person_id", "display_name", "id"):
            if str(person.get(k) or "").strip().lower() in USER_NAMES:
                return False, "the User is in the room"
    return True, f"Standard, no hold, camera sees an empty room ({age:.1f}s old)"


def decide(state: dict, cards: list[dict], *, in_window: bool, key: str, left_min: int,
           chain: bool, now: float, ada_comments: list[dict] | None = None,
           day_ok: bool = False) -> dict:
    """{"hold": [ids], "action": idle|file-review|release|defer|stop-line, ...}. Pure."""
    hold = [c["id"] for c in cards if c["render"] and not c["exempt"] and c["status"] == "ready"
            and not (state.get("window_key") == key and c["id"] in (state.get("released") or []))]
    if not in_window and not day_ok:
        return {"action": "idle", "hold": hold, "why": "outside the User's off-hours — render cards wait"}
    if not in_window:
        # The daytime slot. Same machinery, one card, and every gate the night applies plus
        # the ones that say the house is idle (computed by the caller, `day_ok`).
        load = board_load(cards)
        if not load["clear"]:
            return {"action": "idle", "hold": hold, "load": load,
                    "why": f"day slot: board not clear ({load['running']} running, {load['ready']} ready)"}
        plan = plan_batch(cards, left_min, skip=rulings["skip"] if (rulings := ada_rulings(ada_comments or [])) else [],
                          first=rulings["first"])
        plan = {**plan, "fit": plan["fit"][:DAY_SLOT_MAX_CARDS],
                "carry": plan["fit"][DAY_SLOT_MAX_CARDS:] + plan["carry"]}
        if not plan["fit"]:
            return {"action": "idle", "hold": hold, "why": "day slot: no render work queued", "done": True}
        phase = state.get("phase") if state.get("window_key") == key else None
        if phase in ("released", "deferred", "done"):
            return {"action": "idle", "hold": [] if phase == "released" else hold,
                    "why": f"day slot: already {phase}"}
        if phase is None:
            return {"action": "file-review", "hold": hold, "plan": plan, "load": load, "day": True}
        if rulings["defer"]:
            return {"action": "defer", "hold": hold, "why": "day slot: Ada deferred", "plan": plan}
        if not (rulings["go"] or now >= float(state.get("review_until") or 0)):
            return {"action": "idle", "hold": hold, "why": "day slot: Ada is reviewing"}
        return {"action": "release", "hold": [], "plan": plan, "load": load, "day": True}
    phase = state.get("phase") if state.get("window_key") == key else None
    load = board_load(cards)
    if phase == "released":
        if left_min <= STOP_BEFORE_CLOSE_MIN:
            back = [c["id"] for c in cards if c["id"] in (state.get("released") or [])
                    and c["status"] in ("ready", "todo")]
            return {"action": "stop-line", "hold": back, "why": "stop line: unstarted renders held for tomorrow"}
        return {"action": "idle", "hold": [], "why": "batch released"}
    if phase in ("deferred", "done"):
        return {"action": "idle", "hold": hold, "why": f"batch {phase} for this window"}
    if not chain:
        # the caller waits on the chain only while a pose render is queued
        # (_pose_work_queued, t_ab8aefea); a failed verdict already counts
        # (krew-window-tick.py::final).
        return {"action": "idle", "hold": hold, "why": "the evening chain has not finished"}
    rul = ada_rulings(ada_comments or [])
    plan = plan_batch(cards, left_min, skip=rul["skip"], first=rul["first"])
    if not plan["fit"] and not plan["carry"]:
        return {"action": "idle", "hold": hold, "why": "no render work queued", "done": True}
    if phase is None:
        return {"action": "file-review", "hold": hold, "plan": plan, "load": load}
    # phase == "review"
    if rul["defer"]:
        return {"action": "defer", "hold": hold, "why": "Ada deferred the batch", "plan": plan}
    if not plan["fit"]:
        return {"action": "defer", "hold": hold, "why": "nothing fits before the stop line", "plan": plan}
    review_over = rul["go"] or now >= float(state.get("review_until") or 0)
    if not review_over:
        return {"action": "idle", "hold": hold, "why": "Ada is reviewing the batch"}
    if load["clear"]:
        return {"action": "release", "hold": [], "plan": plan, "load": load}
    if left_min <= plan["latest_start_left"]:
        return {"action": "defer", "hold": hold, "plan": plan, "load": load,
                "why": f"board not clear by the latest start ({load['running']} running, {load['ready']} ready)"}
    return {"action": "idle", "hold": hold, "load": load,
            "why": f"waiting for a clear board ({load['running']} running, {load['ready']} ready)"}

# ── I/O ──────────────────────────────────────────────────────────────────────────────
def kanban(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run([HERMES, "-p", "ada", "kanban", *args], capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2) + "\n")
    tmp.replace(STATE)


def batch_body(key: str, plan: dict, load: dict, left_min: int) -> str:
    day = "DAY" if key.endswith("-day") else "NIGHT"
    lines = [
        f"Render window {key} ({day} slot) — the crew's renders, batched in the User's off-hours.",
        "Ada: judge this batch. Comment with any of (one comment is enough; silence releases the plan "
        f"in {REVIEW_MIN} min):",
        "- `skip t_xxxxxxxx` — leave that render out tonight (it stays held)",
        "- `first t_xxxxxxxx` — run that one first",
        "- `defer` — the whole batch waits for tomorrow's window",
        "- `go` — release now (the board still has to be clear)",
        "",
        f"Board now: {load['running']} non-render running, {load['ready']} non-render ready "
        f"(clear = <= {CLEAR_RUNNING_MAX} running and <= {CLEAR_READY_MAX} ready). "
        f"Window left: {left_min} min; stop line {STOP_BEFORE_CLOSE_MIN} min before close.",
        f"Plan: {len(plan['fit'])} render(s), ~{plan['est_min']} min + {SWITCH_MIN} min mode switch + "
        f"{MARGIN_MIN} min margin (budget {plan['budget_min']} min). Carry to tomorrow: {len(plan['carry'])}.",
        "",
        "| # | card | recipe | est | waiting | title |", "|---|---|---|---|---|---|",
    ]
    now = time.time()
    for i, c in enumerate(plan["fit"] + plan["carry"], 1):
        tag = "" if c in plan["fit"] else " (carry)"
        lines.append(f"| {i} | {c['id']} | {c['recipe']} | {c['est_min']} min{tag} | "
                     f"{(now - c['created_at']) / 3600:.0f} h | {c['title'][:80]} |")
    lines += ["", "Judge by: is it still wanted (a smoke that failed 3+ times is a reframe, not a render); "
              "is it a duplicate; does a cheaper still answer it; does it fit. Renders are not free: "
              "Render mode parks ops and Crissa for the batch.",
              "scripts/render-window.py · skill ada-orchestrate § Render window"]
    return "\n".join(lines)


def tick(*, facts_only: bool = False) -> dict:
    oh = krew_hours.off_hours()
    if oh["source"] != "init":
        return {"action": "idle", "why": oh.get("why") or "off-hours unset"}
    now_dt = dt.datetime.now()
    window = oh["window"]
    inside = krew_hours.contains(now_dt, window)
    key = krew_hours.started_on(now_dt, window)
    left = 0
    if inside:
        m = now_dt.hour * 60 + now_dt.minute
        length = (oh["end"] - oh["start"]) % 1440 or 1440
        left = length - (m - oh["start"]) % 1440
    state = read_json(STATE)
    if state.get("window_key") != key and inside:
        state = {"window_key": key}
    cards = annotate(read_board())
    ada_card = state.get("ada_card")
    ada_comments = []
    if ada_card:
        ada_comments = next((c["comments"] for c in cards if c["id"] == ada_card), None)
        if ada_comments is None:          # the card is done/archived (not in the open rows)
            rc, out = kanban("show", ada_card, "--json")
            try:
                ada_comments = [{"author": x.get("author") or "", "body": x.get("body") or ""}
                                for x in (json.loads(out).get("comments") or [])] if rc == 0 else []
            except json.JSONDecodeError:
                ada_comments = []
    day_ok, day_why = (False, "in the window") if inside else day_slot_ok(now=now_dt)
    if not inside and day_ok:
        # The day slot gets its own window key and its own budget: minutes until the
        # off-hours window OPENS, so the night batch is never eaten into. One card only.
        key = f"{key}-day"
        if state.get("window_key") != key:
            state = {"window_key": key}
        m = now_dt.hour * 60 + now_dt.minute
        left = (oh["start"] - m) % 1440
        if left < DAY_SLOT_MIN_MIN:
            day_ok, day_why = False, f"only {left} min before the night window opens"
    # t_ab8aefea (09-24): the chain verdict is waited on only while a pose render is queued;
    # on a pose-free night the slot opens on the board-clear + stop-line budget alone.
    chain = (chain_done(read_json(TICK_STATE), key) if _pose_work_queued(cards) else True)
    d = decide(state, cards, in_window=inside, key=key, left_min=left,
               chain=chain, now=time.time(), ada_comments=ada_comments,
               day_ok=day_ok)
    if not inside and not day_ok and d.get("action") == "idle":
        d["why"] = f"{d['why']} (day slot: {day_why})"
    if facts_only:
        slim = {k: v for k, v in d.items() if k != "plan"}
        if d.get("plan"):
            slim["plan"] = {"fit": [c["id"] for c in d["plan"]["fit"]], "carry": [c["id"] for c in d["plan"]["carry"]],
                            "est_min": d["plan"]["est_min"], "budget_min": d["plan"]["budget_min"]}
        slim["render_cards"] = [(c["id"], c["status"], c["recipe"], c["title"][:60]) for c in cards if c["render"]]
        slim.update(window=window, in_window=inside, left_min=left, key=key, phase=state.get("phase"),
                    misheld=misheld(cards), day_ok=day_ok, day_why=day_why)
        return slim
    for tid in misheld(cards):
        kanban("comment", tid, "render window: released — not a render card (it only mentions one); "
                               "held by the body match retired 2026-09-17.", "--author", AUTHOR)
        rc, out = kanban("unblock", tid, "--reason", "render window: not a render card")
        log(f"mis-held release {tid}: rc={rc} {out.strip()[:80] if rc else ''}")
    for tid in d.get("hold") or []:
        rc, _ = kanban("comment", tid, f"{MARK}: held for tonight's render slot in the User's off-hours "
                                        "(scripts/render-window.py). `render-now` in a Crissa/User comment exempts it.",
                       "--author", AUTHOR)
        rc2, out2 = kanban("block", tid, HOLD_REASON)
        log(f"hold {tid}: comment rc={rc} block rc={rc2} {out2.strip()[:80] if rc2 else ''}")
    act = d["action"]
    if act == "file-review":
        plan, load = d["plan"], d["load"]
        title = f"Render window {key}: {len(plan['fit'])} render(s), ~{plan['est_min'] + SWITCH_MIN} min"
        rc, out = kanban("create", title, "--assignee", "ada", "--skill", "ada-map", "--body", cut(batch_body(key, plan, load, left), 5500, "the held render cards on the board are the whole batch"),
                         "--created-by", AUTHOR, "--idempotency-key", f"render-window:{key}",
                         "--skill", "ada-orchestrate")
        m = re.search(r"\b(t_[0-9a-f]{8})\b", out)
        state.update(phase="review", ada_card=m.group(1) if m else "", review_until=time.time() + REVIEW_MIN * 60,
                     planned=[c["id"] for c in plan["fit"]])
        log(f"review card {state['ada_card'] or 'FAILED ' + out[:120]}: {title}")
    elif act == "release":
        plan = d["plan"]
        # t_fc0b585c: `released` was judged on the PRE-unblock snapshot, so a
        # held card (status=blocked) whose unblock failed was dropped with no log
        # line (window 2026-09-20). Unblock each fit card and keep its rc, then
        # re-read the board (read-only, same annotate() path) and judge each card
        # by its CURRENT status. A card still blocked — or absent from the fresh
        # read (completed/archived mid-loop) — goes to `dropped`: logged with its
        # id and rc, written into state, and named on the review card.
        released, dropped, rcs = [], [], {}
        for c in plan["fit"]:
            rc, out = kanban("unblock", c["id"], "--reason", "render window: released for tonight's slot")
            rcs[c["id"]] = (rc, (out or "").strip())
        fresh = {c["id"]: c for c in annotate(read_board())}
        for c in plan["fit"]:
            rc, out = rcs[c["id"]]
            cur_status = (fresh.get(c["id"]) or {}).get("status", "")
            if cur_status in ("ready", "todo", "running") or rc == 0:
                released.append(c["id"])
            else:
                dropped.append(c["id"])
                log(f"release DROP {c['id']}: still {cur_status or 'absent'}, unblock rc={rc} {out[:120]}")
        state.update(phase="released", released=released, released_at=time.time())
        if dropped:
            state["dropped"] = dropped
        else:
            # Review REVISE on t_fc0b585c: a clean night must not carry the
            # previous night's drops into the next review summary.
            state.pop("dropped", None)
        if state.get("ada_card"):
            kanban("comment", state["ada_card"], "released: " + ", ".join(released)
                   + (f"; carried to tomorrow: {', '.join(c['id'] for c in plan['carry'])}" if plan["carry"] else "")
                   + (f"; NOT released (unblock failed): {', '.join(dropped)}" if dropped else ""),
                   "--author", AUTHOR)
        log(f"released {released}" + (f" dropped {dropped}" if dropped else ""))
    elif act == "defer":
        state.update(phase="deferred", deferred_why=d.get("why"))
        if state.get("ada_card"):
            kanban("comment", state["ada_card"], f"deferred to tomorrow's window: {d.get('why')}", "--author", AUTHOR)
            kanban("complete", state["ada_card"], "--result", f"render batch deferred: {d.get('why')}")
        log(f"deferred: {d.get('why')}")
    elif act == "stop-line":
        if state.get("ada_card"):
            kanban("comment", state["ada_card"], f"stop line: held again for tomorrow: {', '.join(d['hold']) or 'none'}",
                   "--author", AUTHOR)
        state["phase"] = "done"
        log(f"stop line: re-held {d['hold']}")
    elif d.get("done") and inside:
        state["phase"] = "done"
    if inside or act != "idle":
        write_state(state)
    return {k: v for k, v in d.items() if k not in ("plan",)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("tick", "facts"))
    a = ap.parse_args(argv)
    if a.cmd == "tick" and (OFF.is_file() or os.environ.get("KREW_RENDER_WINDOW", "1") == "0"):
        log("off switch set; nothing done")
        return 0
    r = tick(facts_only=a.cmd == "facts")
    if a.cmd == "facts":
        print(json.dumps(r, indent=2, default=str))
    elif r.get("action") != "idle" or r.get("hold"):
        log(f"{r.get('action')}: {r.get('why', '')} hold={r.get('hold')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
