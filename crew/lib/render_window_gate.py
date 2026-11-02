"""Clock check that closes the ≤2 min gap before render-window's next tick.

A kanban worker (`HERMES_KANBAN_TASK` set) must not start a render outside the
User's off-hours. `render-submit` parks ops; krew-comfy `generate` /
`krew_gpu_mode(render)` would draw or enter Render. The 2-min timer holds
READY cards, but a card can spawn once before that tick.

Kill: `KREW_RENDER_WINDOW=0` or `~/.hermes/render-window.off` (passwd home,
not a worker's sandboxed HOME). Unset `training_window` → no-op (same as
render-window). Exempt: `KREW_RENDER_NOW=1`, or `render-now` in the card
body, or a Crissa/User/the User comment containing `render-now`. A demo lap may
also write `demo-render-now.json` (`on`, `parents`, `cards`, `pid`) so the
cards that lap created, and the children decomposed from those parents, skip
the window. The file is removed when the lap ends; a dead pid reads as off.
Every other card keeps today's rule. Studio / Control Panel / a User CLI
without `HERMES_KANBAN_TASK` is not gated.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pwd
import re
import sqlite3
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

try:
    import krew_hours
except ModuleNotFoundError as e:
    # A public mint has no Init clock. A broken krew_hours still raises:
    # only this module's own absence reads as "window off".
    if getattr(e, "name", None) != "krew_hours":
        raise
    krew_hours = None  # type: ignore[assignment]


def _off_hours() -> dict:
    """The house clock when it shipped, else a window that is not set."""
    if krew_hours is None:
        return {"window": None, "start": None, "end": None, "source": "unset",
                "why": "krew_hours is not in this pack — the render window is off"}
    return krew_hours.off_hours()

NOW_RE = re.compile(r"\brender-now\b", re.I)
RULERS = tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(","))
MARK = "gate: render window"
HERMES_DIR = ".hermes"
KANBAN_CURRENT = Path("kanban") / "current"
DEMO_RENDER_FILE = "demo-render-now.json"


def _passwd_home() -> Path:
    """The passwd entry's home for the current uid — the OS-level identity home.

    Worker sandboxes (Hermes profile shells) repoint $HOME to a profile dir;
    the passwd entry is stable across contexts and is where the host's
    services (render-window, kanban) actually write their state. Isolated
    behind this helper so tests can patch the OS lookup."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _anchor_home(home_env: Path, passwd_home: Path) -> Path:
    """Which home owns the real ~/.hermes state.

    The render-window state file and the kanban board live under the passwd
    home's .hermes — that is where the host service processes write. A worker
    sandbox repoints $HOME (env home) to a profile dir whose .hermes lacks
    kanban state; reading there makes a released card look un-released and a
    running-off-switch look absent. Prefer the env home when it carries real
    kanban state (the normal case: $HOME == passwd home, or a test that
    builds full state under KREW_HERMES); otherwise fall back to the passwd
    home. (t_73d9e1dc: bash worker's $HOME is the profile sandbox and the
    gate missed every released batch.)"""
    if (home_env / HERMES_DIR / KANBAN_CURRENT).is_file():
        return home_env
    return passwd_home


def hermes_home() -> Path:
    env = os.environ.get("KREW_HERMES")
    if env:
        return Path(env)
    if any((os.environ.get(k) or "").strip() for k in ("KREW_HERMES_ROOT", "HERMES_HOME")):
        return KH.hermes_home()             # a named root (a krew beside another Hermes): never the anchor's guess
    return _anchor_home(Path(os.environ.get("HOME", "")), _passwd_home()) / HERMES_DIR


def worker_card() -> str:
    return (os.environ.get("HERMES_KANBAN_TASK") or "").strip()


def enabled() -> bool:
    if os.environ.get("KREW_RENDER_WINDOW", "1") == "0":
        return False
    if (hermes_home() / "render-window.off").is_file():
        return False
    return _off_hours()["source"] == "init"


def in_off_hours(now: dt.datetime | None = None) -> bool:
    oh = _off_hours()
    if oh["source"] != "init" or not oh.get("window"):
        return False
    return krew_hours.contains(now or dt.datetime.now(), oh["window"])


def _board_db() -> Path | None:
    home = hermes_home()
    try:
        slug = (home / "kanban" / "current").read_text().strip()
    except OSError:
        return None
    p = home / "kanban" / "boards" / slug / "kanban.db"
    return p if slug and p.is_file() else None


def demo_render_path() -> Path:
    return hermes_home() / DEMO_RENDER_FILE


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _demo_lists() -> tuple[set[str], set[str]] | None:
    """Parents and cards while the lap's override is on. None when it is off."""
    try:
        data = json.loads(demo_render_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("on") is not True:
        return None
    pid = data.get("pid")
    if type(pid) is int and not _pid_alive(pid):
        return None
    def _ids(key: str) -> list[str]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if isinstance(item, str) and item.startswith("t_")]
    cards = _ids("cards")
    parents = _ids("parents") or list(cards)
    return set(parents), set(cards)


def _body_or_ruler_exempt(tid: str) -> bool:
    db = _board_db()
    if not db:
        return False
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        row = con.execute("SELECT body FROM tasks WHERE id = ?", (tid,)).fetchone()
        if row and NOW_RE.search(row[0] or ""):
            return True
        for author, body in con.execute(
            "SELECT author, body FROM task_comments WHERE task_id = ? ORDER BY id",
            (tid,),
        ):
            if NOW_RE.search(body or "") and (author or "").lower() in RULERS:
                return True
    except sqlite3.Error:
        return False
    finally:
        con.close()
    return False


def _decomposed_from(tid: str, parents: set[str]) -> bool:
    if not parents:
        return False
    db = _board_db()
    if not db:
        return False
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        rows = con.execute(
            "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'created'",
            (tid,),
        ).fetchall()
    except sqlite3.Error:
        return False
    finally:
        con.close()
    for (payload,) in rows:
        try:
            event = json.loads(payload or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict) and event.get("from_decompose_of") in parents:
            return True
    return False


def card_exempt(card_id: str | None) -> bool:
    if os.environ.get("KREW_RENDER_NOW", "") == "1":
        return True
    tid = (card_id or worker_card() or "").strip()
    if not tid:
        return False
    if _body_or_ruler_exempt(tid):
        return True
    listed = _demo_lists()
    if listed is None:
        return False
    parents, cards = listed
    if tid in cards or tid in parents:
        return True
    return _decomposed_from(tid, parents)


def in_released_batch(card_id: str | None, now: dt.datetime | None = None) -> bool:
    """True only for a card Ada's window released *this* night (not yesterday's list)."""
    tid = (card_id or worker_card() or "").strip()
    if not tid:
        return False
    oh = _off_hours()
    if oh["source"] != "init" or not oh.get("window"):
        return False
    key = krew_hours.started_on(now or dt.datetime.now(), oh["window"])
    try:
        st = json.loads((hermes_home() / "render-window.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(st, dict) or st.get("window_key") != key:
        return False
    return tid in (st.get("released") or [])


def refuse_reason(card_id: str | None = None, *, now: dt.datetime | None = None) -> str:
    """Empty string = allowed. Non-empty = print and stop (do not park ops)."""
    if not worker_card():
        return ""
    if not enabled():
        return ""
    if card_exempt(card_id):
        return ""
    if not in_off_hours(now):
        return (
            "render window: waits for the User's off-hours (site.json training_window). "
            "`render-now` in a Crissa/User comment exempts it."
        )
    if in_released_batch(card_id, now):
        return ""
    return (
        "render window: this card is not in tonight's released batch — "
        "waits for Ada's slot (or `render-now` from Crissa/the User)."
    )
