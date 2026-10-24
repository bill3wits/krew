"""A reboot the User explained is not an UNKNOWN reboot (the User 2026-09-30).

The janitor files `JANITOR: reboot detected <ts> — INTENT: UNKNOWN` whenever the boot id changes and no
pre-reboot stamp (scripts/krew-reboot-stamp.py) was consumed. On 09-30 the User rebooted at 04:27 after an
apt upgrade, said nothing to the stamp script, and the card sat open as "unexplained" all day. His word is
enough, in either order:

* BEFORE the reboot: a comment of his on the live board in the 3 h before the janitor noticed, naming the
  reboot ("rebooting after apt upgrade") -> the janitor files no UNKNOWN card; it logs and audits his reason.
* AFTER: a comment of his on the open UNKNOWN card -> the janitor records it as the intent, says so on the
  card and archives it (a triage card cannot be completed).

His word only: authors user, dashboard (his UI), human, human - never Zoey, the crew or a machine.
Every decision is appended to /data/krew-metrics/reboot-intent.jsonl beside the stamp's own lines.
Kill: KREW_JANITOR_REBOOT_COMMENT=0 (the janitor then behaves as before).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

import krew_window_settings as _KWS
import text_cut
USER_AUTHORS = frozenset(_KWS.load("restart-window").get("owner_authors") or ["user", "dashboard", "human"])
CARD_TITLE_LIKE = "JANITOR: reboot detected%INTENT: UNKNOWN%"
RESOLVED_MARK = "janitor: reboot intent recorded from the User's comment"
REBOOT_WORD = re.compile(r"\bre-?boot(?:s|ed|ing)?\b|\brestart(?:ed|ing)?\s+the\s+(?:host|house|machine|box|computer)\b",
                         re.I)
BEFORE_WINDOW_S = 3 * 3600
AUDIT = Path(os.environ.get("KREW_REBOOT_AUDIT") or os.path.join(
    os.path.expanduser(os.environ.get("KREW_METRICS_ROOT") or str(_KWS.krew_home() / "metrics")), "reboot-intent.jsonl"))
KANBAN_ROOT = Path(os.environ.get("KREW_KANBAN_ROOT", Path.home() / ".hermes" / "kanban"))


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_JANITOR_REBOOT_COMMENT", "1")).strip().lower() not in ("0", "false", "no", "off")


def board_db(root: Path | None = None) -> Path | None:
    """The live board's database: kanban/current names the slug."""
    root = root or KANBAN_ROOT
    try:
        slug = (root / "current").read_text().strip()
    except OSError:
        return None
    p = root / "boards" / slug / "kanban.db"
    return p if slug and p.is_file() else None


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _reason(body: str, card: str = "") -> str:
    where = f"the User's comment on card {card} (hermes kanban show {card})" if card else None
    return text_cut.clip(" ".join(str(body or "").split()), 300, label="reboot-reason", where=where, hard=False)


def stated_before(conn: sqlite3.Connection, now: float, window_s: int = BEFORE_WINDOW_S) -> dict | None:
    """The User's newest comment naming a reboot in the window before the janitor noticed, else None."""
    rows = conn.execute(
        "SELECT task_id, author, body, created_at FROM task_comments WHERE created_at >= ? AND created_at <= ? "
        "ORDER BY created_at DESC", (int(now - window_s), int(now) + 60)).fetchall()
    for task_id, author, body, ts in rows:
        if str(author or "").lower() in USER_AUTHORS and REBOOT_WORD.search(str(body or "")):
            return {"card": task_id, "author": author, "ts": ts, "reason": _reason(body, task_id)}
    return None


def explained_cards(conn: sqlite3.Connection) -> list[dict]:
    """Open UNKNOWN reboot cards that carry a comment of the User's and no janitor resolution yet."""
    out = []
    cards = conn.execute("SELECT id, created_at FROM tasks WHERE title LIKE ? AND status NOT IN ('archived', 'done')",
                         (CARD_TITLE_LIKE,)).fetchall()
    for tid, created in cards:
        comments = conn.execute("SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id",
                                (tid,)).fetchall()
        if any(RESOLVED_MARK in str(b or "") for _a, b, _t in comments):
            continue
        said = [(a, b, t) for a, b, t in comments
                if str(a or "").lower() in USER_AUTHORS and (t or 0) >= (created or 0) and str(b or "").strip()]
        if said:
            a, b, t = said[-1]
            out.append({"card": tid, "author": a, "ts": t, "reason": _reason(b, tid)})
    return out


def audit(record: dict, path: Path | None = None) -> None:
    p = path or AUDIT
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "user-comment", **record}) + "\n")
    except OSError:
        pass


def resolve(cards: list[dict], kanban, *, audit_path: Path | None = None) -> int:
    """Say so on each explained card and archive it. `kanban(*args) -> rc`. Returns the cards resolved."""
    done = 0
    for c in cards:
        note = (f"{RESOLVED_MARK} ({c['author']}): \"{c['reason']}\". The reboot was the User's; no forensic pass "
                "is owed. Archived by the janitor (a triage card cannot be completed). Kill: KREW_JANITOR_REBOOT_COMMENT=0.")
        if kanban("comment", c["card"], note, "--author", "krew-janitor") != 0:
            continue
        if kanban("archive", c["card"]) == 0:
            audit({"verdict": "explained-after", **c}, audit_path)
            done += 1
    return done
