"""An announcer line is one plain sentence: what happened and who did it (the User 2026-09-30: "Announcer lines read
like raw card titles ('Staged. land, five red tests findings'). Rewrite them into one plain sentence on what
happened and who did it, announcer only").

The drain hands `plain_line(item, fallback)` the queued item (category, card title, summary, dedup key). The card id
in the dedup key opens the card on the live board READ-ONLY (assignee, result); crew rewrites it into one sentence;
the sentence is checked (one line, no card ids, not too long) and anything that fails the check - or a crew that is
down or slow - says the old line instead. Never raises. Kill: ANNOUNCE_PLAIN=0.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.request
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

ON = os.environ.get("ANNOUNCE_PLAIN", "1").strip().lower() not in ("0", "false", "no", "off")
URL = os.environ.get("ANNOUNCE_PLAIN_URL", "http://127.0.0.1:8005/v1/chat/completions")
MODEL = os.environ.get("ANNOUNCE_PLAIN_MODEL", "crew")
TIMEOUT_S = float(os.environ.get("ANNOUNCE_PLAIN_TIMEOUT_S", "8"))
MAX_CHARS = 200
KANBAN_HOME = Path(os.environ.get("KREW_KANBAN_HOME", str(KH.kanban_dir())))
NAMES = {"ada": "Ada", "bash": "Bash", "steward": "Steward", "lila": "Lila", "vera": "Vera", "scout": "Scout",
         "crissa": "Crissa", "judge": "Judge", "default": "you", "dashboard": "you", "user": "you"}

SYSTEM = (
    "You write ONE sentence for a house announcer to say out loud about a kanban card. Say what happened and who "
    "did it, plainly, the way a colleague would tell you in passing. Use the worker's name. Past tense for finished "
    "work, present tense for stuck work, 'is starting' for staged work. No card ids, no version numbers, no file "
    "paths, no abbreviations (say 'maintenance' for M&S), no quotes, no lists, no preamble. At most 22 words. "
    "Credit the worker named in 'worker' - never the one under 'closed by (do not credit)', who only closed the card. "
    "If the worker is unknown, say what happened without naming anyone. "
    "House words: 'land' in a title means apply or merge a finished fix; 'Chief-of-Staff' is Crissa; 'M&S' and "
    "'M S' mean maintenance and security; a 'sweep' is a review pass over the board; 'stuck-card' means a blocked "
    "card; 'root land card' is the card that applied the fix. Say what the work was about in plain words. "
    "Reply with the sentence only."
)
VERB = {"completion": "finished", "stuck": "stuck", "stage": "staged", "protocol": "protocol problem", "test": "test"}


# CREDIT WHOEVER DID THE WORK (the User 2026-09-30: "credit whoever did the work (the card's run history or its
# completing worker), not the assignee who closed the card"). Measured on the board: every completed run's profile
# equals its card's assignee (1955 of 1955), so the closing card alone always names the closer - on 09-30 "Ada
# landed the Hermes security fix" for a fix Steward landed on t_9d3ed1dc. The work is on the card's DONE
# prerequisites (task_links: parent = prerequisite); their completing runs name who did it. With none, the card's
# own completing run is the credit.
DONE = ("done", "archived")


def _completer(con: sqlite3.Connection, tid: str) -> str:
    """The profile of the card's last COMPLETED run; else its last run's; else its assignee."""
    row = con.execute("SELECT profile FROM task_runs WHERE task_id=? AND outcome='completed' ORDER BY id DESC LIMIT 1",
                      (tid,)).fetchone() or con.execute(
        "SELECT profile FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone() or con.execute(
        "SELECT assignee FROM tasks WHERE id=?", (tid,)).fetchone()
    return str((row or [""])[0] or "")


def credit(con: sqlite3.Connection, tid: str, result: str) -> tuple[list[str], str]:
    """(who did the work, who closed the card) as profile names."""
    closer = _completer(con, tid)
    # Prerequisites only. A card the result merely CITES is not the work: on 09-30 four of 73 finished cards moved
    # credit by a citation alone, and all four were wrong - a steward sweep that listed the cards it looked at was
    # announced as "Bash finished a board sweep". The close cards that motivated this all carry the link.
    prereq = [r[0] for r in con.execute("SELECT parent_id FROM task_links WHERE child_id=?", (tid,))]
    doers: list[str] = []
    for cid in dict.fromkeys(prereq):
        st = con.execute("SELECT status FROM tasks WHERE id=?", (cid,)).fetchone()
        if not st or st[0] not in DONE:
            continue
        who = _completer(con, cid)
        if who and who not in doers:
            doers.append(who)
    return (doers or ([closer] if closer else [])), closer


def card_of(item: dict, kanban_home: Path | None = None) -> dict:
    """The card behind a queued line, read-only: {id, assignee, title, result, did, closed_by}; {} when none."""
    m = re.search(r"\bt_[0-9a-f]{8}\b", str(item.get("dedup_key") or ""))
    if not m:
        return {}
    home = kanban_home or KANBAN_HOME
    try:
        slug = (home / "current").read_text().strip()
        db = home / "boards" / slug / "kanban.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)   # never ?immutable=1 (09-22)
        try:
            row = con.execute("SELECT id, assignee, title, coalesce(result, '') FROM tasks WHERE id=?",
                              (m.group(0),)).fetchone()
            did, closed_by = credit(con, row[0], row[3]) if row else ([], "")
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return {"id": m.group(0)}
    if not row:
        return {"id": m.group(0)}
    return {"id": row[0], "assignee": row[1] or "", "title": row[2] or "", "result": (row[3] or "")[:700],
            "did": did, "closed_by": closed_by}


def _ask(payload: dict, post=None) -> str:
    if post is not None:
        return post(payload)
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        d = json.load(r)
    return str(((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def good(sentence: str) -> str:
    """The sentence if it can be said as it is, else ''."""
    s = " ".join((sentence or "").replace("\n", " ").split()).strip().strip('"“”\'')
    s = re.sub(r"</?think>", "", s).strip()
    if not (12 <= len(s) <= MAX_CHARS) or re.search(r"\bt_[0-9a-f]{6,}\b", s):
        return ""
    if s.count(". ") > 1 or not s.endswith((".", "!", "?")):
        return ""
    return s


def plain_line(item: dict, fallback: str, *, post=None, kanban_home: Path | None = None) -> tuple[str, str]:
    """(what to say, how it was made: 'plain' | 'fallback:<why>')."""
    if not ON:
        return fallback, "fallback:off"
    card = card_of(item, kanban_home)

    def name(p: str) -> str:
        return NAMES.get(str(p or "").strip().lower(), str(p or "").title())
    finished = str(item.get("category") or "") == "completion"
    # finished work credits who did it; stuck / staged work is the current worker's (the assignee)
    doers = [name(p) for p in (card.get("did") or [])] if finished else []
    if not doers and card.get("assignee"):
        doers = [name(card["assignee"])]
    facts = {
        "event": VERB.get(str(item.get("category") or ""), str(item.get("category") or "")),
        "card title": card.get("title") or item.get("title") or "",
        "worker": " and ".join(doers) or "unknown",
        "what the worker reported": (card.get("result") or item.get("summary") or "")[:600],
    }
    closer = name(card.get("closed_by") or "")
    if finished and closer and closer not in doers:
        facts["closed by (do not credit)"] = closer
    payload = {"model": MODEL, "max_tokens": 80, "temperature": 0.2,
               "chat_template_kwargs": {"enable_thinking": False},
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]}
    try:
        said = good(_ask(payload, post))
    except Exception as e:  # noqa: BLE001 - crew down or slow: the old line, never silence
        return fallback, f"fallback:{type(e).__name__}"
    return (said, "plain") if said else (fallback, "fallback:unsayable")
