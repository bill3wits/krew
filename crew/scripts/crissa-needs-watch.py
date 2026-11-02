#!/usr/bin/env python3
"""Stable monitor for Crissa's needs-crissa cron (no timestamps).

Prints presence + open escalation cards. Unchanged bytes suppress the
agent. Used as `hermes -p crissa cron --monitor-script`.

2026-09-17: unchanged bytes made the Decide queue an EDGE trigger on a LEVEL
condition: 47 of 50 ticks were `no_change (agent run suppressed)` while three
cards sat 20 h, and a run that did nothing (dead engine, a ruling that never
landed) spent the edge for good. While a software-rulable `needs_input` card
is on the queue the output now carries `beat=<n>`, which turns over every
CRISSA_NEEDS_REWAKE_MIN (60; 0 = the old edge-only monitor). Cards print oldest
first: the first line is the ask, the diff is only what woke her.

krew (lifted from the reference install, 2026-10-09): presence is read through the Crissa Wall's player
(CRISSA_PLAYER, /v1/rail/presence/v1/presence), never a presence service's own port; the owner's id and spoken
name come from the environment (KREW_OWNER_ID / KREW_OWNER_NAME) or `owner:` in $KREW_HOME/krew.yaml, never an
identity store; the heartbeat and the list live under $KREW_HOME. Installed to $HERMES_HOME/profiles/crissa/scripts/
by crew/scripts/krew-crew-install.py --crons, which also pins the prompt below into the job (install_cron_prompt).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402


def _krew_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


PLAYER = (os.environ.get("CRISSA_PLAYER") or "http://127.0.0.1:8100").rstrip("/")
# The presence rail, through the Wall's player (the player answers for the services she sits beside; a direct
# address is KREW_PRESENCE_URL, for a machine whose presence service is reachable on its own).
PRESENCE = (os.environ.get("KREW_PRESENCE_URL") or (PLAYER + "/v1/rail/presence")).rstrip("/") + "/v1/presence"
GENERIC_USER_ID, GENERIC_USER_NAME = "user", "the owner"


def _owner_setting() -> dict:
    """`owner:` in $KREW_HOME/krew.yaml ({id, name}); {} without a file or a YAML reader."""
    p = Path(os.path.expanduser(os.environ.get("KREW_CONFIG") or str(_krew_home() / "krew.yaml")))
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        try:
            from yaml import safe_load
        except ImportError:
            from ruamel.yaml import YAML
            safe_load = YAML(typ="safe").load
        d = safe_load(text) or {}
        o = d.get("owner") if isinstance(d, dict) else None
        return o if isinstance(o, dict) else {}
    except Exception:  # noqa: BLE001 - a settings file that does not parse is the generic owner
        return {}


def _the_user() -> tuple[str, str]:
    """(id, spoken name) of the owner: the environment, else krew.yaml `owner:`, else generic. No store is read:
    who the owner is belongs to the machine's settings, never to code or a people record."""
    uid = (os.environ.get("KREW_OWNER_ID") or "").strip().lower()
    name = (os.environ.get("KREW_OWNER_NAME") or "").strip()
    if not (uid and name):
        o = _owner_setting()
        uid = uid or str(o.get("id") or "").strip().lower()
        name = name or str(o.get("name") or "").strip()
    return uid or GENERIC_USER_ID, name or GENERIC_USER_NAME


USER_ID, USER_NAME = _the_user()
# With no name there is no one to greet by name: "Hey the owner" is not a greeting. A named owner gets the line
# the reference install always had ("starting Hey <name>"); a fresh install says "starting with a short hello".
_GREETING = (f"starting Hey {USER_NAME}" if USER_NAME != GENERIC_USER_NAME else "starting with a short hello")
USER_NAMES = {"user", USER_ID, "you"}
CRON_JOB_NAME = "needs-crissa-watch"
# Zippy prompt: skill_view + hands. Do not name team_* tools; do not paste
# crissa-needs. The index is the one pointer (the owner 2026-09-16). Every card, not the first:
# 2026-09-18 she read nine cards, ruled one, and called the rest "agent work in flight".
CRON_PROMPT = (
    "A card on the Decide queue (blocked needs_input), a Crissa-list ask waiting "
    "on them, or the owner just walked in. One lookup: skill_view crissa-needs (and "
    "crissa-list if it is their list). Then hands with the job in plain words. "
    # 10-01: her watch called terminal 34 times in a week (sqlite on the board, the kanban tail) until the
    # 09-30 belt took the shell away; the reads it did there go through hands, which read a card with its show.
    "Reading a card, its thread or the board is a hands job too (\"read card t_... and its thread\", "
    "\"list the Decide queue\"): you have no terminal and no database, never sqlite or a shell. "
    "Cards are listed oldest first: rule EVERY card in Current output, oldest "
    "first, one team_board answer each, then the next: the diff is only what "
    "woke you, and a beat= change means the queue is still waiting. Finish the "
    "column in THIS turn: do not stop while a listed card is unruled, and end by "
    "saying how many you ruled of how many were listed. "
    # the owner 2026-10-06: "finish the column" pushed a ruling on a verdict nobody had read.
    "A card you couldn't read honestly stays unruled, named, with the reason. That still counts as finishing. "
    "A card on "
    "that list is waiting on YOU, not on an agent: blocked needs_input is never "
    "work in flight. SOFTWARE is your call this turn: hands: team_board answer "
    "with try|park|later <date>|done|archive|your ruling. If the card asks the WRONG question, "
    "comment `frame: <why>` instead of try: kanban-loop cuts the reframe from it. "
    "`done` when the work already landed (its children are done, or the thread shows it "
    "finished): that CLOSES the card, and it is the only word that lets a frame above it "
    "close too. `archive` retires a card nobody should do: it is not `done` and a frame "
    "waiting on it never closes. A park is neither: it is a card you still owe an answer on. "
    "PHYSICAL or a list ask waiting on them: "
    f"hands check presence; if the owner is here speak one or two sentences {_GREETING}; "
    "else chalk and wait. Guest or silent: do nothing. At most one "
    "spoken item per tick. Never file another card. Never bring them software. "
    "Say RULED only after team_board answer came back Landed; quote its reply."
)

REWAKE_MIN = int(os.environ.get("CRISSA_NEEDS_REWAKE_MIN", "60") or 0)
# Titles that wait on a body in the room: presence wakes her for those.
_HANDS_TITLES = tuple(dict.fromkeys(("needs-human", f"needs-{USER_ID}", "needs-user")))


def _beat(now: float | None = None, minutes: int | None = None) -> str:
    """'' when the level trigger is off; else a line that changes once per window."""
    minutes = REWAKE_MIN if minutes is None else minutes
    if minutes <= 0:
        return ""
    now = time.time() if now is None else now
    return f"beat={int(now // (minutes * 60))}"


RULED_QUIET_H = float(os.environ.get("CRISSA_NEEDS_RULED_QUIET_H", "6") or 0)


def _awaiting_lander(con: sqlite3.Connection, task_id: str, now: float | None = None) -> bool:
    """Her words are the newest thing on the card: it waits on whoever lands
    them, not on her. `team_board answer` holds a Park and any green-gate ruling
    as a comment with no unblock, so the card stays `needs_input` until
    kanban-loop lands it; the beat would re-wake her to re-rule it. Quiet for CRISSA_NEEDS_RULED_QUIET_H (6; 0 = never
    quiet), then the beat takes it back: a ruling nobody landed is a jam."""
    if RULED_QUIET_H <= 0:
        return False
    cols = {r[1] for r in con.execute("PRAGMA table_info(task_comments)").fetchall()}
    if "author" not in cols:
        return False
    order = "id" if "id" in cols else "rowid"
    when = "created_at" if "created_at" in cols else "NULL"
    row = con.execute(
        f"SELECT author, {when} FROM task_comments WHERE task_id = ? ORDER BY {order} DESC LIMIT 1",
        (task_id,)).fetchone()
    if not row or str(row[0] or "").strip().lower() != "crissa":
        return False
    if row[1] is None:
        return True
    now = time.time() if now is None else now
    return (now - float(row[1])) < RULED_QUIET_H * 3600


# A keep ruling holds until something new arrives (the owner 2026-10-02): at 08:10 the hourly beat re-listed
# a card she had parked at 06:08 on a stated condition (a shot and a snapshot first). Nothing had
# arrived but echoes of her own ruling - the dispatcher's SCHEDULED line, kanban-loop's park-sticky, a sweep
# note that said the park holds - and `_awaiting_lander` read the echo as someone else speaking. Re-shown the
# card, she re-ruled it `try` and a worker reloaded the wall. Now a card whose newest ruling keeps it as it is
# (park / later / wait / hands) is not listed until new information lands after that ruling: a comment from
# anyone but the rulers' echoes, or an attachment. Kill: CRISSA_NEEDS_HOLD_RULING=0.
HOLD_RULING = os.environ.get("CRISSA_NEEDS_HOLD_RULING", "1").strip().lower() not in ("0", "false", "no", "off")
RULERS = ("crissa", "user", "dashboard")
KEEP_VERDICTS = ("park", "later", "wait", "hands")
# Lines that land or restate a ruling, never information: the dispatcher's echo, the loop's and gate's
# landings, a patrol's sweep note.
ECHO_AUTHORS = ("kanban-loop", "kanban-board-gate")
ECHO_PREFIXES = ("scheduled:", "blocked:", "unblock:", "park-sticky:", "done-ruled:", "gate:", "sweep t_")


def _verdict(body: str) -> str:
    b = re.sub(r"^\s*(?:UNBLOCK|BLOCKED|SCHEDULED)\s*:\s*", "", body or "", flags=re.I)
    m = re.match(r"\W*([A-Za-z]+)", b)
    return m.group(1).lower() if m else ""


def _ruling_holds(con: sqlite3.Connection, task_id: str) -> bool:
    """Her newest ruling keeps the card as it is, and nothing new has landed since."""
    if not HOLD_RULING:
        return False
    cols = {r[1] for r in con.execute("PRAGMA table_info(task_comments)").fetchall()}
    if not {"author", "created_at"} <= cols:
        return False
    order = "id" if "id" in cols else "rowid"
    rows = con.execute(f"SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY {order}",
                       (task_id,)).fetchall()
    ruled_at, verdict = None, ""
    for author, body, ts in rows:
        a = str(author or "").strip().lower()
        if a in RULERS and not str(body or "").lstrip().lower().startswith(("blocked:",)):
            v = _verdict(str(body or ""))
            if v:
                ruled_at, verdict = ts, v
    if ruled_at is None or verdict not in KEEP_VERDICTS:
        return False
    for author, body, ts in rows:
        if ts is None or float(ts) <= float(ruled_at):
            continue
        a = str(author or "").strip().lower()
        b = str(body or "").lstrip().lower()
        if a in RULERS or a in ECHO_AUTHORS or b.startswith(ECHO_PREFIXES):
            continue
        return False                      # something new: let her see it
    try:
        if con.execute("SELECT 1 FROM task_attachments WHERE task_id = ? AND created_at > ? LIMIT 1",
                       (task_id, ruled_at)).fetchone():
            return False
    except sqlite3.Error:
        pass
    return True


def _software_rulable(title: str, block_kind: str) -> bool:
    return (block_kind or "").lower() == "needs_input" and not _norm(title).startswith(_HANDS_TITLES)


def install_cron_prompt(jobs_path: Path) -> bool:
    """Rewrite needs-crissa-watch's prompt; clear skill-body preload. True if wrote."""
    data = json.loads(jobs_path.read_text(encoding="utf-8"))
    jobs = data.get("jobs") if isinstance(data, dict) else data
    changed = False
    for j in jobs or []:
        if str(j.get("name") or "") != CRON_JOB_NAME:
            continue
        if j.get("prompt") != CRON_PROMPT or j.get("skills") or j.get("skill"):
            j["prompt"] = CRON_PROMPT
            j["skills"] = []
            j["skill"] = None
            changed = True
    if changed:
        jobs_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


def _login_home() -> Path:
    """Login-user home. Hermes cron rewrites HOME to the profile home
    (`$HERMES_HOME/profiles/crissa/home`); Path.home() then misses the board.
    Measured 2026-09-15: watch printed cards=none every tick since 09-10.
    KREW_REAL_HOME wins (same pin as krew-pin-check).
    """
    override = (os.environ.get("KREW_REAL_HOME") or "").strip()
    if override:
        return Path(override)
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        return Path.home()


def _kanban_root() -> Path:
    base = (os.environ.get("KREW_HERMES") or "").strip()
    return (Path(os.path.expanduser(base)) if base else KH.hermes_home(passwd=True)) / "kanban"


def _list_root() -> Path:
    """Crissa's list (team-crissa's team_list): $KREW_HOME/team/list, or KREW_TEAM_LIST_ROOT."""
    return Path(os.path.expanduser(os.environ.get("KREW_TEAM_LIST_ROOT") or str(_krew_home() / "team" / "list")))


def _board_db() -> Path | None:
    root = _kanban_root()
    current = root / "current"
    slug = "daily"
    if current.is_file():
        slug = current.read_text(encoding="utf-8").strip() or slug
    p = root / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def _user_present() -> bool:
    # The presence rail is the one source of who is in the room; a mode file's guest policy is for when the
    # owner is away, not live stance.
    try:
        with urllib.request.urlopen(PRESENCE, timeout=1.5) as r:
            body = json.loads(r.read().decode())
    except Exception:
        return False
    for p in body.get("people") or []:
        if not isinstance(p, dict):
            continue
        for key in ("name", "person_id", "display_name", "id"):
            v = str(p.get(key) or "").strip().lower()
            if v in USER_NAMES:
                return True
    return False


def _norm(title: str) -> str:
    """'NEEDS CRISSA: ...' and 'needs_crissa' read the same as 'needs-crissa'."""
    return re.sub(r"[\s_]+", "-", (title or "").strip().lower())


def _is_escalation(title: str, assignee: str, block_kind: str = "") -> bool:
    # The documented queue (crissa-needs 1.3, Ada SOUL): a card BLOCKED
    # `needs_input` is the item, whatever its title says. Measured 09-12:
    # 10 of 13 escalations in 14 days were needs_input-only and one was
    # spelled "NEEDS CRISSA:" with a space; this monitor saw two.
    if (block_kind or "").lower() == "needs_input":
        return True
    t = _norm(title)
    a = (assignee or "").lower()
    if "needs-crissa" in t or t.startswith(_HANDS_TITLES):
        return True
    if a in {"crissa", USER_ID} and (f"needs-{USER_ID}" in t or "thread-churn" in t):
        return True
    return False


def _list_reviewables() -> list[str]:
    """Crissa-sourced list asks waiting on the owner. Not the crew's 50 team-improve cards."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return []
    pending = _list_root() / "pending"
    if not pending.is_dir():
        return []
    out: list[str] = []
    for p in sorted(pending.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(d, dict):
            continue
        if str(d.get("source") or "").strip().lower() != "crissa":
            continue
        notes = d.get("notes") or []
        if any("spoken to user" in str(n).lower() for n in notes):
            continue
        tid = str(d.get("id") or p.stem)
        title = str(d.get("title") or tid).replace("\n", " ").strip()
        out.append(f"list={tid}\t{title}")
    return out


def _spoken(conn: sqlite3.Connection, task_id: str) -> bool:
    rows = conn.execute(
        "SELECT body FROM task_comments WHERE task_id = ?", (task_id,)
    ).fetchall()
    for (body,) in rows:
        b = (body or "").lower()
        if "spoken to user" in b:
            return True
    return False


# Her Decide watch is a hermes cron INSIDE her gateway, not a systemd unit, so the Daily's roll-call, which
# reads units and files, had no way to see it at all. The one patrol nothing watched, and the single point of
# contact for the whole board: if it stopped, silence looked exactly like a quiet queue. One heartbeat file per
# tick makes it a roll-call row like every other routine. Written on EVERY path including the idle one, because
# "no cards" is the answer we most need to trust.
def _passwd_home() -> Path:
    """The REAL home, from passwd, not `Path.home()`.

    Her gateway runs with HOME set to her profile, so `Path.home()` here resolved to
    the profile's home and the beat landed there while the Daily's roll-call
    watched the real copy: current file, stale watcher, and the page would have
    called her watch dead every morning while she was working (measured 2026-09-22).
    """
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:                      # noqa: BLE001 - a heartbeat never fails the watch
        return Path.home()


def _beat_file() -> Path:
    env = (os.environ.get("CRISSA_WATCH_BEAT") or "").strip()
    if env:
        return Path(os.path.expanduser(env))
    kh = (os.environ.get("KREW_HOME") or "").strip()
    return (Path(os.path.expanduser(kh)) if kh else _passwd_home() / ".krew") / "crissa-decide-watch.beat"


BEAT_FILE = _beat_file()


def _heartbeat(n_cards: int) -> None:
    """Never fail the watch for a heartbeat: a read-only home is not her problem.

    The file's MTIME is the measurement; that is what the roll-call reads. No timestamp
    goes in the payload: run from inside her gateway on 2026-09-22 this wrote `at:
    31101.0` where the host's clock read 1790055996, and an unexplained number beside a
    real one is how a later reader gets misled.
    """
    try:
        BEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        BEAT_FILE.write_text(json.dumps({"cards": n_cards}) + "\n", encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    present = "1" if _user_present() else "0"
    db = _board_db()
    cards: list[str] = []
    rulable = False
    if db is not None:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)").fetchall()}
        bk = "block_kind" if "block_kind" in cols else "NULL"
        age = "created_at, id" if "created_at" in cols else "id"
        # A parked question still wakes her (2026-09-21): a card can carry
        # needs_input while sitting in `scheduled`, and a park is not an answer.
        # Guarded on the column existing, since `bk` degrades to NULL without it.
        parked = ("" if bk == "NULL" or os.environ.get("KREW_CRISSA_SEES_PARKED", "1")
                  .strip().lower() in ("0", "false", "no", "off")
                  else f" OR (status = 'scheduled' AND {bk} = 'needs_input')")
        rows = con.execute(
            f"SELECT id, assignee, title, status, {bk} FROM tasks "
            f"WHERE (status IN ('ready', 'blocked'){parked}) ORDER BY {age}"
        ).fetchall()
        for tid, assignee, title, status, block_kind in rows:
            if not _is_escalation(title or "", assignee or "", str(block_kind or "")):
                continue
            if _spoken(con, tid):
                continue
            if _ruling_holds(con, tid):
                continue
            rulable = rulable or (_software_rulable(title or "", str(block_kind or ""))
                                  and not _awaiting_lander(con, tid))
            print_title = (title or "").replace("\n", " ").strip()
            cards.append(f"{tid}\t{status}\t{assignee}\t{print_title}")
    lists = _list_reviewables()
    _heartbeat(len(cards) + len(lists))
    if not cards and not lists:
        # Idle output is stable: do not wake on presence flips.
        print("cards=none")
        return 0
    print("present=" + present)
    beat = _beat() if (cards and rulable) else ""
    if beat:
        print(beat)
    if cards:
        print("\n".join(cards))
    if lists:
        print("\n".join(lists))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
