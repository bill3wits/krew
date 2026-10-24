"""A crew card may not close while its worker leaves uncommitted changes in a live tree (the User 10-02).

Four crew cards (t_7f5da45c, t_42d6ef2e, t_706a5db8, t_689d1a1e) closed `done` while their workers' edits sat
uncommitted in the live tree, breaking the suite until a session found them (10-02). This is the
check the close should have run; docs/checkpoints/close-clean-20261002 has the look-back.

Attribution, so another agent's work never blocks a card: a path counts as THIS worker's only when it is
dirty in a live repo (modified, added or untracked; ignored files never), it changed after the worker's
session started, and the session's own tool calls name it (a write_file / patch path, or the path text in a
terminal command or code). Everything is read-only: the board, the repos and the profile state.db.

Mode (the switch), read on every call so no restart is needed:
  ~/.hermes/close-clean.mode  =  off (default, also when missing) | report | enforce
  KREW_CLOSE_CLEAN env var overrides the file.
  off      the hook answers {} at once; nothing is read.
  report   the close goes through; the finding is logged (~/.hermes/close-clean.jsonl) and told to the worker.
  enforce  the close is refused with the list: commit the files (pathspec) or revert them, then complete.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)   # never $HOME: kanban-env-clean sandboxes it
MODE_FILE = REAL_HOME / ".hermes" / "close-clean.mode"
LOG = REAL_HOME / ".hermes" / "close-clean.jsonl"
# The house's values (which trees are live, which profiles are crew) live in config, never here (the User 10-02).
CONFIG = Path(os.environ.get("KREW_CLOSE_CLEAN_CONFIG",
                             str(Path(__file__).resolve().parents[1] / "config" / "close-clean.json")))


def config() -> dict:
    """The close-clean config; {} when missing or unreadable (the hook then checks nothing - fails open)."""
    try:
        data = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def live_repos() -> list[Path]:
    home = str(REAL_HOME)
    return [Path(home + r[1:] if r.startswith("~/") else r) for r in config().get("live_repos") or [] if isinstance(r, str)]


def crew_profiles() -> tuple[str, ...]:
    return tuple(str(x).strip().lower() for x in config().get("crew_profiles") or [] if str(x).strip())
MODES = ("off", "report", "enforce")
SLACK_S = 60.0   # a file touched a minute before the session's recorded start still counts


def mode() -> str:
    raw = (os.environ.get("KREW_CLOSE_CLEAN") or "").strip().lower()
    if not raw:
        try:
            raw = MODE_FILE.read_text().strip().lower()
        except OSError:
            raw = "off"
    return raw if raw in MODES else "off"


def dirty(repo: Path) -> list[tuple[str, float]]:
    """(relative path, mtime) of every modified, added or untracked file in `repo`; ignored files never."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-uall", "--no-renames"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for line in out.splitlines():
        if len(line) < 4 or line[:2] == "!!":
            continue
        rel = line[3:].strip().strip('"')
        try:
            mt = (repo / rel).stat().st_mtime
        except OSError:
            mt = time.time()   # deleted in the tree: count it as fresh
        rows.append((rel, mt))
    return rows


_SHELL_WRITE = r"(?:>>?|\btee(?:\s+-a)?)\s*['\"]?"
_CODE_WRITE = r"(?:open\(\s*['\"]|Path\(\s*['\"])"


def session_trace(state_db: Path, session_id: str) -> tuple[float, str]:
    """(session start, the text of every WRITE the session made) from the profile's state.db.

    Only writes count (10-02): a write_file / patch path, a shell write into a path, or code that opens a
    path for writing. A path a session merely ran (`pytest tests/x.py`) or quoted is not its edit."""
    con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    try:
        row = con.execute("select started_at from sessions where id = ?", (session_id,)).fetchone()
        start = float(row[0]) if row and row[0] else 0.0
        calls = [r[0] for r in con.execute(
            "select tool_calls from messages where session_id = ? and tool_calls is not null", (session_id,))]
    finally:
        con.close()
    text = []
    for tc in calls:
        try:
            items = json.loads(tc)
        except (ValueError, TypeError):
            continue
        for x in items:
            f = x.get("function") or {}
            name = f.get("name") or ""
            try:
                args = json.loads(f.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            if name in ("write_file", "patch") and isinstance(args, dict):
                text.append(str(args.get("path") or args.get("file_path") or ""))
            elif name in ("terminal", "execute_code") and isinstance(args, dict):
                body = str(args.get("command") or args.get("code") or "")
                for m in re.finditer(_SHELL_WRITE + r"([^\s'\";|&)]+)", body):
                    text.append(m.group(1))
                # cp / mv / install / rsync / sed -i: the last operand is the file written
                for m in re.finditer(r"\b(?:cp|mv|install|rsync|sed\s+-i\S*)(\s[^\n;|&]*)", body):
                    ops = [w for w in m.group(1).split() if not w.startswith("-")]
                    if ops:
                        text.append(ops[-1].strip("'\""))
                # prodgate stage <card> <path>...: the house's reviewed write into the live tree
                for m in re.finditer(r"\bprodgate\s+(?:stage|apply)\s+\S+((?:\s+[^\s;|&]+)+)", body):
                    text.extend(w.strip("'\"") for w in m.group(1).split() if not w.startswith("-"))
                for m in re.finditer(_CODE_WRITE + r"([^'\"]+)['\"][^\n]{0,80}?(?:['\"][wa]|write_text|write_bytes)", body):
                    text.append(m.group(1))
    home = str(REAL_HOME)
    norm = [t.replace("~/", home + "/", 1) if t.startswith("~/") else t.replace("$HOME/", home + "/", 1) for t in text]
    return start, "\n".join(norm)


def named_in(rel: str, repo: Path, text: str) -> bool:
    absolute = str(repo / rel)
    return absolute in text or re.search(r"(?<![\w./-])" + re.escape(rel) + r"(?![\w-])", text) is not None


def worker_dirty(state_db: Path, session_id: str, repos: list[Path] | None = None,
                 dirty_as_of: dict[Path, list[str]] | None = None) -> list[str]:
    """Live-repo paths this session touched that are still uncommitted, as 'repo/rel'.

    `dirty_as_of` replays the check against a recorded uncommitted set ({repo: [rel, ...]}) instead of
    `git status` now - how the 10-02 look-back asks what the close would have refused at the time."""
    start, text = session_trace(state_db, session_id)
    if not text:
        return []
    found = []
    for repo in repos if repos is not None else live_repos():
        rows = dirty(repo) if dirty_as_of is None else [
            (rel, float("inf")) for rel in dirty_as_of.get(repo, [])]   # replay: the writes decide, not mtimes
        for rel, mt in rows:
            if mt >= start - SLACK_S and named_in(rel, repo, text):
                found.append(f"{repo.name}/{rel}")
    return sorted(set(found))


def card_sessions(state_db: Path, card: str) -> list[str]:
    """Every session in this profile whose first instruction is to work `card`."""
    con = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    try:
        sids = [r[0] for r in con.execute(
            "select s.id from sessions s where exists (select 1 from messages m where m.session_id = s.id "
            "and m.role = 'user' and m.content like ?) order by s.started_at", (f"%{card}%",))]
    finally:
        con.close()
    return sids


def refusal(paths: list[str]) -> str:
    shown = ", ".join(paths[:12]) + (f" (+{len(paths) - 12} more)" if len(paths) > 12 else "")
    return ("close-clean: this card's worker leaves uncommitted changes in the live tree: " + shown +
            ". Commit them (git add P && git commit -m '<card> ...' -- P, never bare) or revert them, "
            "then complete the card.")


def log(row: dict[str, Any]) -> None:
    try:
        with LOG.open("a") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **row}) + "\n")
    except OSError:
        pass
