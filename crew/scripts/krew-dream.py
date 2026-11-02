#!/usr/bin/env python3
"""krew-dream — nightly rollup from the house's OWN innate records.

User 2026-07-31: the 3-day rollup belongs inside the Dream cron, built from the
summaries Hermes already keeps. It does — no new logging layer is needed:

  * every profile's ``state.db`` has ``sessions(started_at, ended_at, message_count,
    title, model, profile_name)`` and ``messages(timestamp, role, content)`` — and
    that includes the ear-bridge free-air sessions (``freeair-<epoch>``).
  * ``kanban.db`` has ``task_runs(started_at, ended_at, summary)`` — the real worker
    handoff text (``tasks.result`` is usually NULL).
  * the door's day came from a gold file the reference install no longer keeps (see day()).

Grounded like the museum's crew_dream: REAL activity only. A quiet day writes a
quiet line and no Hindsight fact — never a fabricated saga.

  krew-dream.py --day            → today's rollup
  krew-dream.py --rollup 3       → 3-day rollup (the Dream-cron artifact)
  krew-dream.py --rollup 3 --dry → print, write nothing
Writes: ~/.krew/memory/day/<date>.json and rolling/<n>day.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402
KREW_MEMORY_ROOT = os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory")  # krew: the house kept this on a data disk

HERMES = KH.hermes_home()
OUT = Path((KREW_MEMORY_ROOT + ""))
HINDSIGHT = os.environ.get("HINDSIGHT_URL", "http://127.0.0.1:8888")
BANK = os.environ.get("HINDSIGHT_BANK", "hermes")
STATE = OUT / "dream-state.json"   # high-water marks: never re-retain the same day


def _q(db: Path, sql: str, args=()):
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []


def sessions_since(since: float) -> list[dict]:
    out = []
    for db in [HERMES / "state.db"] + sorted(HERMES.glob("profiles/*/state.db")):
        who = "bridge" if db.parent == HERMES else db.parent.name
        for sid, start, end, n, title, model in _q(
            db,
            "select id, started_at, ended_at, message_count, title, model "
            "from sessions where started_at >= ? order by started_at",
            (since,),
        ):
            out.append({
                "profile": who, "session": sid, "started": start, "ended": end,
                "messages": n, "title": title,
                "kind": "free-air" if str(sid).startswith("freeair") else "typed",
            })
    return out


def turns_since(since: float, limit: int = 400) -> list[dict]:
    out = []
    for db in sorted(HERMES.glob("profiles/*/state.db")):
        for ts, role, content in _q(
            db,
            "select timestamp, role, content from messages "
            "where timestamp >= ? and role in ('user','assistant') order by timestamp",
            (since,),
        ):
            out.append({"ts": ts, "profile": db.parent.name, "role": role,
                        "text": cut(content or "", 300, db.parent.name + " state.db has it whole")})
    out.sort(key=lambda r: r["ts"])
    return out[-limit:]


def cards_since(since: float) -> list[dict]:
    """Runs live in PER-BOARD dbs (kanban/boards/<slug>/kanban.db); the root
    kanban.db is only the default board. Scan them all."""
    out = []
    dbs = [HERMES / "kanban.db"] + sorted(HERMES.glob("kanban/boards/*/kanban.db"))
    for db in dbs:
        for t, st_, en, status, sm in _q(
            db,
            "select task_id, started_at, ended_at, status, summary from task_runs "
            "where started_at >= ? order by started_at",
            (since,),
        ):
            out.append({"board": db.parent.name, "task": t, "started": st_,
                        "ended": en, "status": status,
                        "summary": cut((sm or "").strip(), 400, "task_runs in " + str(db) + " has it whole")})
    return out



def _dedupe(items: list[str]) -> list[str]:
    """60 runs of the same failing goal-loop shouldn't fill the day with one line."""
    seen, out = set(), []
    for x in items:
        k = x[:80]
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def build(days: int) -> dict:
    since = time.time() - days * 86400
    s, c, t = sessions_since(since), cards_since(since), turns_since(since)
    spoken = [x for x in s if x["kind"] == "free-air"]
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "window_days": days,
        "sessions": {"total": len(s), "free_air": len(spoken),
                     "titles": [x["title"] for x in s if x.get("title")][-25:]},
        "cards": {"runs": len(c),
                  "summaries": _dedupe([x["summary"] for x in c if x["summary"]])[-25:]},
        # No "door" section since 2026-09-05: door_since() read the door predictor's per-day gold files;
        # that predictor is gone and the face/voice stamping that replaced it produces no door verdict.
        "turns_sample": t[-60:],
        "quiet": not (s or c),
    }


def as_markdown(d: dict) -> str:
    L = [f"# House rolling summary — {d['window_days']} day(s)",
         f"_generated {d['generated']}_", ""]
    if d["quiet"]:
        L.append("Quiet window — no sessions, no cards. Nothing to retain.")
        return "\n".join(L)
    ss, cc = d["sessions"], d["cards"]
    L += [f"**Conversations:** {ss['total']} sessions ({ss['free_air']} spoken aloud).",
          f"**Cards:** {cc['runs']} runs.", ""]
    if ss["titles"]:
        L += ["## What was discussed"] + [f"- {x}" for x in ss["titles"]] + [""]
    if cc["summaries"]:
        L += ["## What the crew finished"] + [f"- {x}" for x in cc["summaries"]] + [""]
    if d.get("door"):
        L += ["## Door"]
        for x in d["door"]:
            L.append(f"- {x['day']}: {x['add']} in, {x['sub']} out, {x['null']} null"
                     + (f", {x['human_overrides']} the User corrections" if x["human_overrides"] else ""))
    return "\n".join(L)


def retain(d: dict, dry: bool = False) -> dict:
    """Write the window's durable facts into Hindsight.

    Grounded (museum crew_dream rule): a quiet window retains NOTHING — no
    fabricated saga. Idempotent: a per-day high-water mark in dream-state.json
    means a re-run never double-writes, and document_id lets Hindsight itself
    dedupe if we ever do.
    """
    if d.get("quiet"):
        return {"retained": 0, "reason": "quiet window — nothing real to retain"}

    day = datetime.now().strftime("%Y-%m-%d")
    state = {}
    if STATE.is_file():
        try:
            state = json.loads(STATE.read_text())
        except json.JSONDecodeError:
            state = {}
    if state.get("last_retained_day") == day and not dry:
        return {"retained": 0, "reason": f"already retained for {day}"}

    items = []
    for title in d["sessions"]["titles"]:
        items.append({
            "content": f"Conversation: {title}",
            "timestamp": datetime.now().isoformat(),
            "context": "house conversation topic",
            "tags": ["house", "conversation", day],
            "document_id": f"krew-{day}-conv-{abs(hash(title)) % 10**8}",
        })
    for sm in d["cards"]["summaries"]:
        items.append({
            "content": f"Crew work: {sm}",
            "timestamp": datetime.now().isoformat(),
            "context": "kanban card outcome",
            "tags": ["house", "kanban", day],
            "document_id": f"krew-{day}-card-{abs(hash(sm)) % 10**8}",
        })
    for x in d.get("door") or []:
        if not (x["add"] or x["sub"]):
            continue
        items.append({
            "content": (f"Door {x['day']}: {x['add']} entries, {x['sub']} exits, "
                        f"{x['null']} non-crossings"
                        + (f", {x['human_overrides']} corrections from the User"
                           if x["human_overrides"] else "")),
            "timestamp": datetime.now().isoformat(),
            "context": "door activity",
            "tags": ["house", "door", x["day"]],
            "document_id": f"krew-door-{x['day']}",
        })
    if not items:
        return {"retained": 0, "reason": "no speakable facts in window"}
    if dry:
        return {"would_retain": len(items), "sample": items[:2]}

    url = f"{HINDSIGHT}/v1/default/banks/{BANK}/memories"
    body = json.dumps({"items": items, "async": True,
                       "document_tags": ["krew-dream", day]}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = r.read().decode()[:200]
    except (urllib.error.URLError, OSError) as e:
        return {"retained": 0, "error": f"hindsight unreachable: {e}"}

    STATE.parent.mkdir(parents=True, exist_ok=True)
    state["last_retained_day"] = day
    state["last_retained_at"] = time.time()
    state["last_count"] = len(items)
    STATE.write_text(json.dumps(state, indent=1))
    return {"retained": len(items), "bank": BANK, "response": resp}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", action="store_true")
    ap.add_argument("--rollup", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--retain", action="store_true",
                    help="write the window's facts into Hindsight (the Dream step)")
    a = ap.parse_args()
    days = a.rollup or 1
    d = build(days)
    md = as_markdown(d)
    if a.dry:
        print(md)
        if a.retain:
            print("\n---\nhindsight (dry):", json.dumps(retain(d, dry=True), indent=1)[:600])
        return 0
    (OUT / "day").mkdir(parents=True, exist_ok=True)
    (OUT / "rolling").mkdir(parents=True, exist_ok=True)
    if a.day or days == 1:
        (OUT / "day" / f"{datetime.now():%Y-%m-%d}.json").write_text(json.dumps(d, indent=1))
    if a.rollup:
        (OUT / "rolling" / f"{days}day.md").write_text(md)
    print(md)
    if a.retain:
        print("\n---\nhindsight:", json.dumps(retain(d, dry=a.dry)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
