#!~/.hermes/hermes-agent/venv/bin/python
"""kanban-skills-add — append skill pins to a card's `skills` column (skills once per thread).

the User 2026-09-18: "tools once per turn, skills once per thread." hermes stores per-card pins in
`tasks.skills` (JSON list) and re-emits them as `--skills <name>` when the worker spawns
(kanban_db._default_spawn), but no verb writes the column after `create --skill`. This is that
verb, krew side (no tree patch): the worker wrapper calls it at exit with the skills the
session actually loaded (`skill_view`), and the board gate calls it to inherit a link-parent's
pins onto a child / reframe cut / retry.

  scripts/kanban-skills-add.py <task-id> <skill> [<skill> …] [--board <slug>] [--why <text>]
                                [--dry-run] [--json]

Rules: names are validated against the ASSIGNEE's registry (a pin the worker cannot load is
worse than none); duplicates are dropped; the column is written through kanban_db.connect +
write_txn (the invariant path, not a raw sqlite write); one `skills_added` event per call.
Exit 0 = written or nothing to add; 2 = unknown card; 3 = every name refused; 4 = no board.
Kill: KREW_SKILLS_ADD=0 (exit 0, writes nothing).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
import logging  # noqa: E402

logging.disable(logging.CRITICAL)

_REAL_HOME = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home())))
KANBAN = _REAL_HOME / ".hermes" / "kanban"


def _board_db(slug: str | None) -> Path | None:
    if not slug:
        try:
            slug = (KANBAN / "current").read_text().strip()
        except OSError:
            return None
    p = KANBAN / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def _validate_for(profile: str, names: list[str]) -> tuple[list[str], list[str]]:
    """Validate against the ASSIGNEE profile's registry. Returns (ok, refused)."""
    ok: list[str] = []
    refused: list[str] = []
    prof_home = _REAL_HOME / ".hermes" / "profiles" / profile
    old = os.environ.get("HERMES_HOME")
    try:
        if prof_home.is_dir():
            os.environ["HERMES_HOME"] = str(prof_home)
        try:
            from hermes_constants import reset_hermes_home_override, set_hermes_home_override
            set_hermes_home_override(str(prof_home)) if prof_home.is_dir() else None
        except Exception:
            reset_hermes_home_override = None  # type: ignore
        from hermes_cli import kanban_db as kb
        check = getattr(kb, "_validate_skill_names", None)
        if check is None:
            # The house validator (patch 10) is not on the 2026-09-26 tree. Ask the loader the
            # worker itself will use: a name it cannot load is the name that aborts the preload.
            # Without this branch the AttributeError refused EVERY name.
            from agent import skill_commands as _sc

            def check(one):
                if _sc._load_skill_payload(one[0]) is None:
                    raise ValueError(f"unknown skill: {one[0]}")
                return one
        for n in names:
            try:
                got = check([n]) or []
                ok.extend(got if got else [n])
            except Exception:
                refused.append(n)
    finally:
        if old is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old
        try:
            reset_hermes_home_override()  # type: ignore[misc]
        except Exception:
            pass
    return ok, refused


def _connect(kb, db):
    """`connect` moved to hermes_cli.kanban_db_connect in the 2026-09 split; the old path is a
    compat shim past its removal date. An older tree has only the old one."""
    try:
        from hermes_cli.kanban_db_connect import connect
    except ImportError:
        connect = kb.connect
    return connect(db)


def add(task_id: str, names: list[str], *, board: str | None = None, why: str = "",
        dry_run: bool = False) -> dict:
    if os.environ.get("KREW_SKILLS_ADD", "1") == "0":
        return {"ok": True, "killed": True, "added": []}
    db = _board_db(board)
    if not db:
        return {"ok": False, "rc": 4, "error": "no board"}
    from hermes_cli import kanban_db as kb
    conn = _connect(kb, db)
    try:
        row = conn.execute("SELECT id, assignee, skills, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "rc": 2, "error": f"unknown card {task_id}"}
        have: list[str] = []
        try:
            have = list(json.loads(row["skills"]) or []) if row["skills"] else []
        except (TypeError, ValueError):
            have = []
        want = [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip()) if n not in have]
        if not want:
            return {"ok": True, "added": [], "skills": have, "note": "nothing new"}
        ok, refused = _validate_for(row["assignee"] or "", want)
        ok = [n for n in dict.fromkeys(ok) if n not in have]
        if not ok:
            return {"ok": False, "rc": 3, "error": "every name refused", "refused": refused, "skills": have}
        new = have + ok
        if dry_run:
            return {"ok": True, "dry_run": True, "added": ok, "refused": refused, "skills": new}
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET skills = ? WHERE id = ?", (json.dumps(new), task_id))
            kb._append_event(conn, task_id, "skills_added",
                             {"added": ok, "refused": refused, "why": why[:200], "by": "kanban-skills-add"})
        return {"ok": True, "added": ok, "refused": refused, "skills": new}
    finally:
        conn.close()


def remove(task_id: str, names: list[str], *, board: str | None = None, why: str = "",
           dry_run: bool = False) -> dict:
    """Drop pins from a card's `skills` column — the missing half of this verb (2026-09-20).

    A pin the assignee's registry cannot resolve is not a mis-configuration the worker can
    survive: hermes raises `Unknown skill(s)` at preload, so the worker dies at
    `Initializing agent...` with rc=1 BEFORE any tool call, and the breaker counts it as a
    crash (t_996a6f12 `bash-code` x6, t_47c4bebd `bash-map` on a steward card x22 — Ada's
    board-hygiene step on t_a90f3c83, which she could not do: no verb writes this column
    downward). `add` validates; there is nothing to validate on the way out, so a name that
    is on the card comes off whether or not it resolves — that is the point.
    """
    if os.environ.get("KREW_SKILLS_ADD", "1") == "0":
        return {"ok": True, "killed": True, "removed": []}
    db = _board_db(board)
    if not db:
        return {"ok": False, "rc": 4, "error": "no board"}
    from hermes_cli import kanban_db as kb
    conn = _connect(kb, db)
    try:
        row = conn.execute("SELECT id, assignee, skills, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "rc": 2, "error": f"unknown card {task_id}"}
        have: list[str] = []
        try:
            have = list(json.loads(row["skills"]) or []) if row["skills"] else []
        except (TypeError, ValueError):
            have = []
        drop = [n for n in dict.fromkeys(n.strip() for n in names if n and n.strip()) if n in have]
        if not drop:
            return {"ok": True, "removed": [], "skills": have, "note": "nothing to remove"}
        new = [n for n in have if n not in drop]
        if dry_run:
            return {"ok": True, "dry_run": True, "removed": drop, "skills": new}
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET skills = ? WHERE id = ?", (json.dumps(new), task_id))
            kb._append_event(conn, task_id, "skills_removed",
                             {"removed": drop, "why": why[:200], "by": "kanban-skills-add"})
        return {"ok": True, "removed": drop, "skills": new}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    # hermes ships its own Python since 2026-09-26; under /usr/bin/python3 its board module does
    # not import. Re-run once under hermes's interpreter (lib/hermes_runtime, kill KREW_HERMES_REEXEC=0).
    if argv is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import hermes_runtime
        hermes_runtime.ensure(__file__)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task_id")
    ap.add_argument("skills", nargs="+")
    ap.add_argument("--board", default=None)
    ap.add_argument("--why", default="")
    ap.add_argument("--remove", action="store_true",
                    help="drop these pins instead of adding them (an unresolvable pin is a "
                         "guaranteed rc=1 preload abort; nothing validates on the way out)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    fn = remove if a.remove else add
    r = fn(a.task_id, a.skills, board=a.board, why=a.why, dry_run=a.dry_run)
    if a.json:
        print(json.dumps(r))
    else:
        if r.get("ok"):
            sign = f"-{r.get('removed', [])}" if a.remove else f"+{r.get('added', [])}"
            print(f"{a.task_id}: {sign} -> {r.get('skills', [])}" + (" (dry-run)" if r.get("dry_run") else ""))
        else:
            print(f"{a.task_id}: {r.get('error')}", file=sys.stderr)
    return 0 if r.get("ok") else int(r.get("rc", 1))


if __name__ == "__main__":
    raise SystemExit(main())
