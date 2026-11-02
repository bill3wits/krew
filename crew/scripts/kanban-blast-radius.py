#!/usr/bin/env python3
"""Which parked cards are holding live work — the edge nobody reported.

2026-09-21. The 09-16 standing board sat five days looking merely quiet: 15 `todo`
cards, every one of them waiting on an open prerequisite, ZERO dispatchable. That is
not a jam — it is the honest consequence of a handful of parked decisions sitting at
the roots of the dependency graph, and the dispatcher correctly refusing to invent
work around them. Nothing was broken: 14 frames completed in the same 18 hours.

No patrol saw it, because every patrol keys on a card's OWN state. `kanban
diagnostics` reports per-card conditions (crashes, phantom refs). steward-sweep is
told to act on cards with "NO native path forward" — and a card waiting on a
prerequisite HAS one, so it correctly leaves it. Crissa's watch reads `blocked
needs_input`, and these are `todo` and `scheduled`, so they never reach her queue.
The card is visible and the park is visible; the EDGE between them is not.

This reports that edge: for each parked or blocked card, how many live cards it
holds. Two extras, both off unless asked:

  --set-priority   writes the count into `priority`, which hermes already sorts by
                   (`ORDER BY priority DESC, created_at ASC`), so the load-bearing
                   parks rise to the top of the column with no new sort anywhere.
                   Safe while parked: the dispatcher's claim queries only ever read
                   `status='ready'`, so this moves the EYE, not the workers.
  --route N        puts the top N onto Crissa's Decide queue as `needs_input`.

ROOTS ONLY for routing. A card whose own prerequisite is still open is not waiting
on Crissa, it is waiting on that card — hermes refuses to promote it and that refusal
is the honest answer (kanban-loop's `ask_crissa`, 2026-09-20). On the 09-16 board that
distinction mattered: of the nine load-bearing cards, two (t_57a06e7c, t_c18db52b)
were themselves waiting, and routing them would have been refused.

Kills: KREW_BLAST_RADIUS=0 (whole tool), KREW_BLAST_PRIORITY=0, KREW_BLAST_ROUTE=0.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

_REAL_HOME = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home())))
KANBAN = KH.kanban_dir()
HERMES = os.environ.get("KREW_HERMES_BIN", str(_REAL_HOME / ".local" / "bin" / "hermes"))
PROFILE = os.environ.get("BLAST_PROFILE", "ada")
OPEN = ("todo", "ready", "running", "blocked", "scheduled", "triage", "review")
PARKED = ("scheduled", "blocked")
ROUTE_MARK = "blast: routed to Crissa"
OLD_ROUTE_MARK = "blast: routed to Crissa"     # the same mark before the rename (2026-10-10), still read
PRIORITY_CAP = int(os.environ.get("KREW_BLAST_PRIORITY_CAP", "9"))


def _on(env: str) -> bool:
    return os.environ.get(env, "1").strip().lower() not in ("0", "false", "no", "off")


def board_db(slug: str | None = None) -> Path | None:
    if not slug:
        try:
            slug = (KANBAN / "current").read_text(encoding="utf-8").strip()
        except OSError:
            return None
    p = KANBAN / "boards" / slug / "kanban.db"
    return p if slug and p.is_file() else None


def read_graph(db: Path) -> tuple[dict, dict, dict, dict, dict]:
    """(status, title, assignee, deps, prereqs). Read-only.

    `task_links` names its columns the other way round from the domain: `parent_id`
    is the PREREQUISITE (the child task that must finish) and `child_id` is the
    DEPENDENT (the frame waiting on it). Verified against a frame whose own comment
    lists its children. Getting this backwards inverts every conclusion.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        status, title, assignee = {}, {}, {}
        for tid, st, ti, asg in con.execute("SELECT id, status, title, assignee FROM tasks"):
            status[tid], title[tid], assignee[tid] = st, ti or "", asg or ""
        deps: dict[str, set] = collections.defaultdict(set)
        prereqs: dict[str, set] = collections.defaultdict(set)
        try:
            rows = con.execute("SELECT parent_id, child_id FROM task_links").fetchall()
        except sqlite3.OperationalError:
            rows = []
        for pre, dep in rows:
            deps[pre].add(dep)
            prereqs[dep].add(pre)
    finally:
        con.close()
    return status, title, assignee, deps, prereqs


def blast(root: str, deps: dict, status: dict) -> set:
    """Every OPEN card transitively waiting on `root`."""
    seen: set = set()
    stack = [root]
    while stack:
        for d in deps.get(stack.pop(), ()):
            if d not in seen and status.get(d) in OPEN:
                seen.add(d)
                stack.append(d)
    return seen


def rank(db: Path) -> list[dict]:
    """Parked/blocked cards holding live work, most first. Read-only."""
    status, title, assignee, deps, prereqs = read_graph(db)
    out = []
    for cid, st in status.items():
        if st not in PARKED:
            continue
        held = blast(cid, deps, status)
        if not held:
            continue
        waiting_on = sorted(p for p in prereqs.get(cid, ()) if status.get(p) in OPEN)
        out.append({
            "id": cid, "status": st, "title": title.get(cid, "")[:70],
            "assignee": assignee.get(cid, ""), "blocks": len(held),
            "blocked": sorted(held), "root": not waiting_on, "waiting_on": waiting_on,
        })
    out.sort(key=lambda r: (-r["blocks"], not r["root"], r["id"]))
    return out


def idle_note(db: Path) -> dict:
    """Is the board idle because everything open is waiting? The headline fact."""
    status, _t, _a, _deps, prereqs = read_graph(db)
    todo = [c for c, s in status.items() if s == "todo"]
    free = [c for c in todo
            if not [p for p in prereqs.get(c, ()) if status.get(p) in OPEN]]
    return {"todo": len(todo), "dispatchable": len(free), "free_ids": sorted(free)}


# --- the two write paths, both opt-in ---------------------------------------

def set_priority(db: Path, rows: list[dict], *, dry_run: bool = False) -> int:
    """Write `blocks` into `priority` so the column sorts itself.

    Only on a PARKED card: hermes claims work with `status='ready' AND claim_lock IS
    NULL ORDER BY priority DESC`, so while the card is parked this cannot change what
    any worker picks up. When it is released it is claimed early, which is the point.
    """
    if not _on("KREW_BLAST_PRIORITY"):
        return 0
    sys.path.insert(0, str(KH.hermes_tree()))
    import logging
    logging.disable(logging.CRITICAL)
    from hermes_cli import kanban_db as kb
    n = 0
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        for r in rows:
            want = min(int(r["blocks"]), PRIORITY_CAP)
            cur = con.execute("SELECT priority, status FROM tasks WHERE id = ?", (r["id"],)).fetchone()
            if cur is None or cur["status"] not in PARKED or int(cur["priority"] or 0) == want:
                continue
            if dry_run:
                n += 1
                continue
            with kb.write_txn(con):
                con.execute("UPDATE tasks SET priority = ? WHERE id = ?", (want, r["id"]))
                kb._append_event(con, r["id"], "priority_set",
                                 {"priority": want, "blocks": r["blocks"],
                                  "by": "kanban-blast-radius",
                                  "why": f"holds {r['blocks']} live card(s)"})
            n += 1
    finally:
        con.close()
    return n


def kanban(*args: str, dry_run: bool = False) -> tuple[int, str]:
    if dry_run:
        return 0, "dry-run " + " ".join(args)
    try:
        p = subprocess.run([HERMES, "-p", PROFILE, "kanban", *args],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:                                   # noqa: BLE001
        return 1, str(exc)


def block_kind_of(db: Path, tid: str) -> str:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = con.execute("SELECT block_kind FROM tasks WHERE id = ?", (tid,)).fetchone()
        return str((r[0] if r else "") or "")
    except sqlite3.OperationalError:
        return ""
    finally:
        con.close()


def already_routed(db: Path, tid: str) -> bool:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return any(ROUTE_MARK in (b or "") or OLD_ROUTE_MARK in (b or "") for (b,) in
                   con.execute("SELECT body FROM task_comments WHERE task_id = ?", (tid,)))
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def route(db: Path, rows: list[dict], n: int, *, dry_run: bool = False,
          only: tuple[str, ...] = ()) -> list[str]:
    """Put the top N ROOTS on Crissa's Decide queue, most blocking first.

    `only` names exact cards instead: rank order is the right default, but it is
    not always the right answer — a hand picking two cards should not also sweep up
    whatever happens to tie with them. Named cards still go through every guard.
    """
    if not _on("KREW_BLAST_ROUTE"):
        return []
    done = []
    if only:
        rows = [r for r in rows if r["id"] in set(only)]
        missing = set(only) - {r["id"] for r in rows}
        for m in sorted(missing):
            print(f"blast: {m} is not a park holding live work — not routed")
        n = len(rows)
    for r in rows:
        if len(done) >= n:
            break
        if not r["root"] or already_routed(db, r["id"]):
            continue
        q = (f"This card is holding {r['blocks']} live card(s): "
             f"{', '.join(r['blocked'])}. Nothing else on the board can move until it is "
             "ruled, and nothing is waiting on it in turn. Rule it: "
             "try | park | later <date> | archive.")
        note = (f"{ROUTE_MARK} — holds {r['blocks']} live card(s). "
                "Parked cards are invisible to the Decide queue, so this was routed by "
                "blast radius (kanban-blast-radius).")
        # TRIAGE GUARD (kanban-loop's `ask_crissa`, 2026-09-21): a card that ALREADY carries
        # needs_input is already hers. A second needs_input block on it is a same-cause
        # re-block, and hermes answers that with BLOCK_RECURRENCE_LIMIT -> `triage`, which
        # takes the card OFF her queue entirely. Measured the hard way: routing five cards
        # without this guard sent t_84fa8600 and t_e2d5f2a3 to triage. Comment instead.
        if block_kind_of(db, r["id"]) == "needs_input":
            kanban("comment", r["id"], f"{note}\n\ndecide: {q}",
                   "--author", "kanban-blast-radius", dry_run=dry_run)
            done.append(r["id"])
            continue
        kanban("comment", r["id"], note, "--author", "kanban-blast-radius", dry_run=dry_run)
        # `unblock` is a scheduled card's only exit; hermes blocks only running/ready.
        kanban("unblock", r["id"], "--reason",
               f"routed to Crissa: holds {r['blocks']} live card(s)", dry_run=dry_run)
        rc, out = kanban("block", r["id"], q, "--kind", "needs_input", dry_run=dry_run)
        if rc != 0:
            print(f"blast: route {r['id']} refused ({out.strip()[:120]})")
            continue
        done.append(r["id"])
    return done


def render(rows: list[dict], idle: dict) -> str:
    if not rows:
        return "blast: no parked card is holding live work"
    out = [f"blast: {len(rows)} parked card(s) holding live work "
           f"({idle['dispatchable']} of {idle['todo']} todo cards dispatchable)"]
    for r in rows:
        tag = "ROOT " if r["root"] else "waits"
        out.append(f"  {r['blocks']:>2} {tag} {r['id']}  {r['status']:<9} @{r['assignee']:<8} {r['title'][:46]}")
        if not r["root"]:
            out.append(f"          itself waiting on: {' '.join(r['waiting_on'])}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kanban-blast-radius.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--set-priority", action="store_true", help="sort the column by what each park holds")
    ap.add_argument("--route", type=int, default=0, metavar="N", help="put the top N ROOTS on Crissa's queue")
    ap.add_argument("--only", nargs="+", default=[], metavar="ID",
                    help="route exactly these cards instead of the top N")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if not _on("KREW_BLAST_RADIUS"):
        print("blast: KREW_BLAST_RADIUS=0")
        return 0
    db = board_db(a.board)
    if db is None:
        print("blast: no current board db")
        return 0
    rows, idle = rank(db), idle_note(db)
    if a.set_priority and argv is None:
        # the write goes through hermes's own transaction; see lib/hermes_runtime
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import hermes_runtime
        hermes_runtime.ensure(__file__)
    pri = set_priority(db, rows, dry_run=a.dry_run) if a.set_priority else 0
    routed = (route(db, rows, 0, dry_run=a.dry_run, only=tuple(a.only)) if a.only
              else route(db, rows, a.route, dry_run=a.dry_run) if a.route else [])
    if a.json:
        print(json.dumps({"board": db.parent.name, "idle": idle, "parks": rows,
                          "priority_set": pri, "routed": routed}, indent=1))
    else:
        print(render(rows, idle))
        if pri:
            print(f"blast: priority set on {pri} park(s){' (dry run)' if a.dry_run else ''}")
        if routed:
            print(f"blast: routed to Crissa{' (dry run)' if a.dry_run else ''}: {' '.join(routed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
