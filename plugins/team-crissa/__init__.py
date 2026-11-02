"""TeamCrissa plugin — shared senses + mouth for krew profiles.

VoiceOS touchpoints (no Hermes /voice mode; her ear is the Wall's, read through its player):



  announcer / PA   → Audio8 :8024 (completions · stuck · protocol · staged;
                     title+summary, no task keys; drain queue dumps trailing)
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
try:
    from . import krew_hermes as KH
except ImportError:                 # imported alone (tests, scripts)
    import krew_hermes as KH  # noqa: E402


# --- krew: where things live (settings, never code) -----------------------------------------------------------
# _CREW: krew's crew machinery ($KREW_HOME/crew). The Wall (Crissa) is reached only through its player's routes
# (CRISSA_PLAYER, below), never a Crissa checkout. Local service ports default to Crissa's own and each can be moved:
# KREW_PORT_<default port>=<port> (e.g. KREW_PORT_9119=9120), KREW_LOCAL_HOST for another host.
def _krew_home_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


_CREW = Path(os.path.expanduser(os.environ.get("KREW_CREW_ROOT") or str(_krew_home_dir() / "crew")))
_HERMES_TREE = os.path.expanduser(os.environ.get("HERMES_TREE") or str(KH.hermes_tree()))
_PERSON = (os.environ.get("KREW_PERSON") or "user").strip().lower()      # the household member a brief is for
_USER_NAMES = tuple(n.strip().lower() for n in (os.environ.get("KREW_USER_NAMES") or "you,user").split(",") if n.strip())


def _local(port: int, path: str = "") -> str:
    host = os.environ.get("KREW_LOCAL_HOST") or "127.0.0.1"
    return f"http://{host}:{os.environ.get(f'KREW_PORT_{port}') or port}{path}"


def _cut(text, n: int, more: str = "") -> str:
    """Coach, don't cap: a cut says how much was kept and where the rest is."""
    text = "" if text is None else str(text)
    if len(text) <= n:
        return text
    return f"{text[:n]} [cut at {n} of {len(text)} chars{'; ' + more if more else ''}]"


def _json_env(name: str, default: dict) -> dict:
    try:
        v = json.loads(os.environ.get(name) or "null")
        return v if isinstance(v, dict) else default
    except ValueError:
        return default




# The Wall is Crissa's own page (2026-10-06). krew puts a card beside her through the player only:
# POST /v1/screen (notes, doc, art, a web address, a whole page), GET /v1/screen reads back what is up.
# The named screens below are krew's own pages; a name opens on her card by its address.
WALL_VIEWS = {
    "kanban": _local(9119, "/kanban?profile=crissa"),
    "board": _local(9119, "/kanban?profile=crissa"),
    "sessions": _local(9119, "/sessions?profile=crissa"),
    "logs": _local(9119, "/logs?profile=crissa"),
    "dashboard": _local(9119, "/?profile=crissa"),
    "chat": _local(9119, "/?profile=crissa"),
    "panel": os.environ.get("KREW_PANEL_URL") or _local(8097, "/"),       # Crissa's Control Panel
    "control": os.environ.get("KREW_PANEL_URL") or _local(8097, "/"),
    "hq": os.environ.get("KREW_PANEL_URL") or _local(8097, "/"),
    # her presence feed and registration panel, through the Wall's player (Crissa's /v1/rail)
    "presence": os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/") + "/v1/rail/presence/",
    "enroll": os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/") + "/v1/rail/enroll/",
    "composer": _local(8199, "/"),
    "vllm": _local(8086, "/"),
    "crew": _local(8086, "/"),
    "approvals": _local(8898, "/"),
}
# Living HTML in krew-deliverables (resolved at call time).
_LIVING_VIEWS = {
    "morning": ("team-daily.html",),

    # so the helper reached for "morning" (Scout's team daily) and put up the wrong page.
    "daily": ("krew-daily.html",),
    "krew-daily": ("krew-daily.html",),
    "krew daily": ("krew-daily.html",),
    "efficiency": ("ops-efficiency.html",),
    "ops": ("ops-efficiency.html",),
    "ms": ("ms.html",),
    "architect": ("architect-world.html",),
    "inbox": ("INDEX.html",),
    "deliverables": ("INDEX.html",),
}
_HOME_WORDS = ("home", "face", "her", "scene", "wall")     # home on the Wall = her card comes down



# Legacy env kept for any external readers; announcer no longer calls Qwen.
QWEN_TTS = os.environ.get("QWEN_TTS_URL", _local(8002, "/tts"))
# Her voice is the Wall's player (2026-10-06): krew reaches the Wall (Crissa) only through the player's routes,
# /v1/speak, /v1/announce and /v1/stop, never its files or its voice services. CRISSA_PLAYER moves it.
CRISSA_PLAYER = os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/")
# The services she sits beside - presence feed, camera, registration panel - through the player's rail
# (/v1/rail/<name>/, Crissa 2026-10-06): reads on any path, writes only on the paths the player names.
PRESENCE_BASE = CRISSA_PLAYER + "/v1/rail/presence"
PRESENCE = PRESENCE_BASE + "/v1/presence"
CAMERA = CRISSA_PLAYER + "/v1/rail/camera"

# lock serializes ALL announce audio across processes (workers + gateways).
_ANNOUNCE_LOCK = Path(
    os.environ.get(
        "KREW_ANNOUNCE_LOCK",
        str(KH.hermes_home() / "krew-announce.lock"),
    )
)
# Hook coalescing only (double-complete / multi-worker). Must NOT share
# krew_announce's play-ack file — writing that at enqueue made drain

_ANNOUNCE_HOOK_DEDUP = Path(
    os.environ.get(
        "KREW_ANNOUNCE_HOOK_DEDUP",
        str(Path(os.path.expanduser(os.environ.get("HERMES_HOME") or str(KH.hermes_home()))) / "krew-announce-hook-dedup.json"),
    )
)
_DEDUP_SEC = float(os.environ.get("KREW_ANNOUNCE_DEDUP_SEC", "45"))
# How long after User/Crissa speech to keep announcer paused (seconds).
_SPEECH_PAUSE_SEC = float(os.environ.get("KREW_ANNOUNCE_SPEECH_PAUSE_SEC", "12"))
_TTS_GATE = Path(
    os.environ.get(
        "KREW_TTS_GATE",
        str(KH.hermes_home() / "krew-tts-gate.json"),
    )
).expanduser()

# Set KREW_ANNOUNCE_COMPLETIONS=0 to silence task-done PA.

_ANNOUNCE_COMPLETIONS = os.environ.get(
    "KREW_ANNOUNCE_COMPLETIONS", "1"
).strip().lower() not in ("0", "false", "no", "off")

# Announcer / kanban → Qwen TTS voice keys (clone stems in qwen refs)
QWEN_VOICES = _json_env("KREW_TEAM_QWEN_VOICES", {
    "crissa": "zoe",
    "lila": "lila",
    "announcer": "announcer",
    "ada": "ada",
})


def _get(url: str, timeout: float = 4.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


ENROLL_PANEL = CRISSA_PLAYER + "/v1/rail/enroll"    # the registration panel, through the player's rail


def _post_json(url: str, body: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _rail_says(code: int, body, reason, what: str) -> str:
    """A refusal through the player's rail, in words: an older Crissa, or the service's / the player's why."""
    body = body if isinstance(body, dict) else {}
    if code == 404 and body.get("error") == "no such route":
        return f"this Crissa is older than /v1/rail (player at {CRISSA_PLAYER}) — update Crissa to reach the {what}"
    return f"the {what} through the player said {code}: {body.get('why') or body.get('error') or reason}"


def _rail_why(e: Exception, what: str) -> str:
    """Why a call through the player's rail failed, in words (2026-10-06): never a bare exception name."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            body = {}
        return _rail_says(e.code, body, e.reason, what)
    if isinstance(getattr(e, "reason", e), TimeoutError):
        return f"the {what} through the player at {CRISSA_PLAYER} did not answer in time"
    if isinstance(e, OSError):
        return f"Crissa not installed (no player at {CRISSA_PLAYER}) — no {what}"
    return f"{what}: {e.__class__.__name__}: {e}"


CONDUCTOR = CRISSA_PLAYER      # the Wall's player is her conductor (/v1/cue, /v1/state)


def _post_cue(body: dict, timeout: float = 4.0) -> dict:
    """POST :8100/v1/cue. 409 is a real answer (taken: false + why), not a
    transport failure — urllib raises HTTPError for it, so we read the body.

    SIGN THE ROW. There is one queue and three hands on it — this toolset, the
    Composer at :8100 and the wall — so every row carries who put it there and
    the Composer marks hers in her own colour. Unsigned, `by` defaults to

    attribution: it is wrong attribution on a shared list.
    """
    body = {"by": "crissa", **body}
    req = urllib.request.Request(
        CONDUCTOR.rstrip("/") + "/v1/cue",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp is not None else ""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and data:
            return data
        return {"taken": False, "why": "conductor %s" % e.code}


def compose_pose(key: str, ttl: float = 12.0, affect: str = "",
                 by: str = "crissa") -> str:
    """Insert a clip into the running Conductor. Returns what will happen,
    never a bare ok — a drop must say so in the same sentence.

    `affect` is body language from the caller's ## <name>affect SOUL block,
    capped at 600 chars, carried every turn so Composer is not welded Crissa.
    """
    key = str(key or "").strip()
    if not key:
        return "need a clip — emote-woe, emote-surprise, rest, listen-user"
    try:
        ttl_f = float(ttl)
    except (TypeError, ValueError):
        ttl_f = 12.0
    payload: dict = {"key": key, "ttl": ttl_f, "by": str(by or "crissa")}
    aff = (affect or "").strip()[:600]
    if aff:
        payload["affect"] = aff
    try:
        body = _post_cue(payload)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return "Crissa not installed (no player at %s: %s) — I can't move right now" % (CRISSA_PLAYER, type(e).__name__)
    if not isinstance(body, dict):
        return "conductor answered without a body — I can't tell if it landed"
    if body.get("taken"):
        when = str(body.get("when") or "").strip()
        if when == "now":
            return "cued %s — now" % key
        if when:
            return "cued %s — %s" % (key, when)
        return "cued %s" % key
    why = str(body.get("why") or "").strip()
    return why or ("%s was not taken" % key)


def _player_get(path: str, timeout: float = 6.0) -> tuple[int, dict]:
    """GET from the Wall's player. (0, {"error": ...}) when no player answers (Crissa not installed or not running)."""
    try:
        with urllib.request.urlopen(CRISSA_PLAYER + path, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
            return r.status, out if isinstance(out, dict) else {}
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            out = {}
        return e.code, out if isinstance(out, dict) else {}
    except (OSError, ValueError) as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def _no_wall() -> str:
    return f"Crissa not installed (no player at {CRISSA_PLAYER}) — nothing changed on the Wall"


def _old_wall() -> str:
    return (f"this Crissa is older than the Wall's card (no /v1/screen at {CRISSA_PLAYER}) — update Crissa; "
            "nothing changed on the Wall")


def _screen(body: dict, timeout: float = 10.0) -> tuple[bool, dict, str]:
    """A card beside her on the Wall through the player (POST /v1/screen), then the glass read back (GET /v1/screen).
    (landed, card, why): `card` is what the Wall shows now; `why` says plainly why nothing changed."""
    code, out = _player_post("/v1/screen", {"by": "krew", **body}, timeout=timeout)
    if code == 0:
        return False, {}, _no_wall()
    if code == 404:
        return False, {}, _old_wall()
    if code != 200 or not out.get("ok"):
        return False, {}, f"the player refused the card ({code}: {out.get('error') or out}) — nothing changed on the Wall"
    code, back = _player_get("/v1/screen")
    card = (back.get("screen") if code == 200 else None) or out.get("screen") or {}
    return True, card, ""


def _card_says(card: dict) -> str:
    """One honest sentence about what the Wall's card shows now, from the read-back, never from the post."""
    if not card or not card.get("up"):
        return "Her card is down — she is all that is on the Wall."
    lines = lambda k: len(str(card.get(k) or "").splitlines())
    bits = []
    if card.get("url"):
        bits.append(f"the page {card['url']} in a frame (a site that refuses to be framed shows blank there)")
    if card.get("html"):
        bits.append("a page" + (f" '{card['title']}'" if card.get("title") else "") + " (shown whole, no scripts)")
    if card.get("doc"):
        bits.append(f"a sheet '{card.get('doc_title') or 'Notes'}' ({lines('doc')} lines)")
    if card.get("notes"):
        bits.append(f"a note '{card.get('notes_title') or 'Notes'}' ({lines('notes')} lines)")
    if card.get("art"):
        bits.append(f"art{' ' + repr(card['art_label']) if card.get('art_label') else ''} ({lines('art')} lines)")
    if not bits and card.get("title"):
        bits.append(f"the title '{card['title']}'")
    return "The Wall shows beside her: " + "; ".join(bits) + "."


def _clear_crissa_popups() -> str:
    """Belt when the chat identity ends: a card krew put up and left longer than KREW_CARD_HOLD_S (default 600)
    comes down through the player. A newer card stays (a hands run is a oneshot whose exit fires this), and a card
    someone else put up is not krew's to take down. Nothing else on the Wall is touched."""
    code, out = _player_get("/v1/screen", timeout=4.0)
    if code == 0:
        return _no_wall()
    card = out.get("screen") or {}
    if code != 200 or not card.get("up"):
        return "no card up"
    try:
        hold = float(os.environ.get("KREW_CARD_HOLD_S") or 600)
    except ValueError:
        hold = 600.0
    if card.get("by") != "krew" or time.time() - float(card.get("at") or 0) < hold:
        return "card left up"
    code, out = _player_post("/v1/screen", {"clear": "all", "by": "krew"}, timeout=5.0)
    return "card down" if code == 200 and out.get("ok") else f"card not taken down ({code})"


# ── show_me: one verb, four landings ──────────────────────────────────────

# she doesn't use it well … display findings in beautiful HTML." The old
# schema had sixteen properties and an action= vocabulary the model had to
# guess at. Now the model says WHAT it wants on the glass and the tool picks
# the landing: a named krew screen, a handed URL/file, a note for the
# corner, or a REPORT (markdown → her HTML template → the glass). The old
# {"action":…,"name":…} shape still works — every skill example keeps its
# meaning.
_NOTE_MAX = 240          # longer than a note, or shaped like markdown → a report
_SCREEN_ALIASES = {
    "board": "kanban", "the board": "kanban", "work": "kanban", "cards": "kanban",
    "control panel": "panel", "cp": "panel", "control": "panel",
    "her": "home", "face": "home", "scene": "home", "wall": "home",
    "clip doctor": "composer", "doctor": "composer",
    "approvals": "approvals",
    "crissa's list": "approvals", "crissas list": "approvals", "her list": "approvals",
    "todo": "approvals", "to-do": "approvals", "to dos": "approvals", "todos": "approvals",
}


def _display_intent(p: dict) -> dict:
    """Read either shape into one intent.

    New: what= a screen name | 'report' | 'note' | 'home' | 'clear' | 'tabs' |
    'shot' | a URL | a file path, with text= / title= / hold=.
    Old: action= view|home|chalk|notes|clear|tab|tabs|activate|close|shot|
    status|resting|hybrid|voice-only … with name= / url= / text= / target=.
    Returns {"landing", "name", "dest", "text", "title", "target", "hold", "moment", "art"}.
    landing ∈ view · home · report · note · clear · tabs · tab · activate ·
    close · shot · status · resting · mode · help.
    """
    g = lambda *keys: next((str(p[k]).strip() for k in keys if p.get(k) not in (None, "")), "")
    what = g("what", "screen", "view", "name", "page").lower()
    action = g("action", "op").lower()
    url = g("url", "href")
    text = g("text", "body", "markdown", "html")
    title = g("title", "caption")
    target = g("target")
    hold = g("hold", "ttl", "notes_ttl", "doc_ttl")
    moment = g("moment").lower()
    art = str(p.get("art") or "")
    out = {"name": "", "dest": "", "text": text, "title": title, "target": target,
           "hold": hold, "moment": moment, "art": art}

    def _screen(key: str) -> str:
        key = _SCREEN_ALIASES.get(key, key)
        if key in WALL_VIEWS or key in _LIVING_VIEWS or key in _HOME_WORDS:
            return key
        return ""

    # explicit old-shape actions first
    if action in ("home", "scene", "face"):
        return dict(out, landing="home")
    if action in ("clear",):
        return dict(out, landing="clear")
    if action in ("tabs",):
        return dict(out, landing="tabs")
    if action in ("tab", "newtab"):
        return dict(out, landing="tab", name=what, dest=url)
    if action in ("activate", "close"):
        return dict(out, landing=action, target=target or what or url)
    if action in ("shot", "screenshot"):
        return dict(out, landing="shot", target=target)
    if action in ("status", "ensure", "start", "pin", "collapse", "expand"):
        return dict(out, landing="status", target=action)
    if action in ("hybrid", "voice-only", "voice_only", "voiceos"):
        return dict(out, landing="mode", target=action)
    if action in ("resting", "rest"):
        return dict(out, landing="resting")
    if action in ("report", "findings", "brief", "summary_page"):
        return dict(out, landing="report")
    if action in ("chalk", "board", "write", "notes", "note", "summary"):
        return dict(out, landing="note")
    if action in ("view", "show", "open", "navigate", "stage", "wall",
                  "panel", "control", "hq", "control-panel", "control_panel"):
        if action in ("panel", "control", "hq", "control-panel", "control_panel") and not what:
            return dict(out, landing="view", name="panel", dest=WALL_VIEWS["panel"])
        if url.startswith(("http://", "https://")):
            return dict(out, landing="view", dest=url)
        key = _screen(what)
        if key:
            dest = WALL_VIEWS.get(key) or _living_html(*(_LIVING_VIEWS.get(key) or ()))
            return dict(out, landing="view", name=key, dest=dest)
        return dict(out, landing="help", name=what or url)

    # the new shape: what=
    if what in ("home", "face", "her", "scene", "wall", "off"):
        return dict(out, landing="home")
    if what in ("clear", "clean"):
        return dict(out, landing="clear")
    if what in ("tabs",):
        return dict(out, landing="tabs")
    if what in ("shot", "screenshot", "glass"):
        return dict(out, landing="shot", target=target)
    if what in ("status",):
        return dict(out, landing="status", target="status")
    if what in ("report", "findings", "brief", "page", "document", "doc"):
        return dict(out, landing="report")
    if what in ("note", "chalk", "notes", "headline"):
        return dict(out, landing="note")
    if what in ("resting", "rest") or (moment and not what and not text):
        return dict(out, landing="resting")
    key = _screen(what)
    if key:
        dest = WALL_VIEWS.get(key) or _living_html(*(_LIVING_VIEWS.get(key) or ()))
        return dict(out, landing="view", name=key, dest=dest)
    for cand in (what, url):
        if cand.startswith(("http://", "https://")):
            return dict(out, landing="view", dest=cand)
        if cand.startswith(("~", "/", "file://")):
            path = cand[7:] if cand.startswith("file://") else cand
            return dict(out, landing="tab", dest="file://" + os.path.expanduser(path))
    if text:
        longish = len(text) > _NOTE_MAX or "\n" in text or text.lstrip().startswith(("#", "|", "- ", "* "))
        return dict(out, landing="report" if longish else "note")
    if art:
        return dict(out, landing="resting")
    return dict(out, landing="help", name=what or url)


DELIVERABLES = Path(os.environ.get("KREW_DELIVERABLES", str(Path.home() / "Desktop" / "krew-deliverables")))


# ── team_board: the board in one tool ────────────────────────────────────
# Crissa carried the whole `kanban` toolset — 14 tools, 23 KB on every turn —

# What her doctrine needs is six verbs: list, show, create (assignee ada,
# triage), comment, unblock, complete (crissa-needs, crissa-big-push, SOUL).
# One tool, ~0.7 KB, over the `hermes kanban` CLI (the shared SQLite board).
_BOARD_ACTIONS = ("list", "show", "create", "comment", "unblock", "complete", "answer")
_BOARD_STATUS = ("archived", "blocked", "done", "ready", "review", "running", "scheduled", "todo", "triage")
_BOARD_MARK = {"done": "✓", "blocked": "⊘", "running": "▶", "review": "◔", "ready": "○",
               "todo": "○", "triage": "?", "scheduled": "◷", "archived": "·"}

# Crissa's list on :8898 (CP Security tile). Not the crew board.
# ask = waits on him (yes/no). task = steps she (or the crew) owns.
_LIST_ACTIONS = ("list", "show", "ask", "task", "note", "done")
_LIST_WHO = ("ada", "crissa", "bash", "lila", "steward", "vera")


def _login_home() -> Path:
    """Login-user home. Hermes cron rewrites HOME to the profile home."""
    override = (os.environ.get("KREW_REAL_HOME") or "").strip()
    if override:
        return Path(override)
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        return Path.home()


# Tests monkeypatch this name. Default is the krew list, not Path.home().
_LIST_ROOT = Path(os.path.expanduser(os.environ.get("KREW_TEAM_LIST_ROOT") or str(_krew_home_dir() / "team" / "list")))


def _board_tally(rows: list[dict]) -> str:
    """Stable per-status counts so a list call is the number, not a hunt."""
    from collections import Counter
    c = Counter(str(r.get("status") or "?") for r in rows)
    parts = []
    for st in _BOARD_STATUS:
        n = c.pop(st, 0)
        if n:
            parts.append(f"{st} {n}")
    for st, n in sorted(c.items()):
        parts.append(f"{st} {n}")
    return " · ".join(parts)


_DONE = frozenset({"done", "complete", "completed"})
_ARCHIVED = frozenset({"archived"})
# Hermes dispatch always forwards these. They are never a board filter.
_RUNTIME_KW = frozenset({
    "task_id", "session_id", "user_task", "enabled_tools", "parent_agent",
    "callback", "tool_call_id", "turn_id", "api_request_id", "scope",
})
_HOUR_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _board_ts(t: dict) -> float:
    for k in ("completed_at", "created_at", "started_at"):
        v = t.get(k)
        if v in (None, "", 0, "0"):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        try:
            return float(s)
        except ValueError:
            pass
        try:
            return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
    return 0.0


def _board_hhmm(t: dict) -> str:
    ts = _board_ts(t)
    if not ts:
        return ""
    return time.strftime("%H:%M", time.localtime(ts))


def _parse_window(v) -> float | None:
    """Minutes/hours from whatever the child sent: 2, 2h, 120, 'last two hours'."""
    if isinstance(v, (int, float)):
        if v <= 0:
            return None
        return float(v) * 3600 if v <= 48 else float(v) * 60
    s = str(v or "").strip().lower()
    if not s:
        return None
    m = re.search(r"(\d+)\s*h", s)
    if m:
        return int(m.group(1)) * 3600
    m = re.search(r"(\d+)\s*m(?:in)?", s)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(one|two|three|four|five|six)\s+hour", s)
    if m:
        return _HOUR_WORDS[m.group(1)] * 3600
    if s.isdigit():
        n = int(s)
        return n * 3600 if n <= 48 else n * 60
    m = re.search(r"(\d+)\s*hour", s)
    if m:
        return int(m.group(1)) * 3600
    return None


def _plain_ask(p: dict) -> str:
    """Scout/list job in plain words. look= was the 08:32 empty-ask shape."""
    return str(
        p.get("ask") or p.get("text") or p.get("look")
        or p.get("query") or p.get("q") or p.get("job") or ""
    ).strip()


def _drop_runtime(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in _RUNTIME_KW}


def _window_seconds(p: dict) -> float | None:
    for k in ("since", "hours", "last", "window", "within"):
        sec = _parse_window(p.get(k))
        if sec:
            return sec
    skip = {"ask", "text", "job", "action", "status", "assignee", "id", "task",
            "body", "title"} | _RUNTIME_KW
    for k, v in p.items():
        if k in skip:
            continue
        sec = _parse_window(v)
        if sec:
            return sec
    blob = " ".join(str(p.get(k) or "") for k in ("ask", "text", "job", "q", "query"))
    return _parse_window(blob)


def _board_head(rows: list[dict], st: str = "", window_s: float | None = None) -> str:
    n = len(rows)
    if st:
        head = f"{n} card{'s' if n != 1 else ''} · {st}"
    else:
        done = sum(1 for r in rows if str(r.get("status") or "") in _DONE)
        arch = sum(1 for r in rows if str(r.get("status") or "") in _ARCHIVED)
        open_n = n - done - arch
        head = f"{open_n} open · done {done}"
        if arch:
            head += f" · archived {arch}"
    if window_s:
        if window_s % 3600 == 0:
            w = f"{int(window_s // 3600)}h"
        elif window_s % 60 == 0:
            w = f"{int(window_s // 60)}m"
        else:
            w = f"{int(window_s)}s"
        head += f" · last {w}"
    return head


def _board_line(t: dict) -> str:
    """One row: mark id status assignee title HH:MM."""
    st = str(t.get("status") or "?")
    line = "%s %s %-8s %-8s %s" % (_BOARD_MARK.get(st, "·"), str(t.get("id") or "?"),
                                   st, str(t.get("assignee") or "-"), str(t.get("title") or "")[:90])
    hh = _board_hhmm(t)
    return f"{line} {hh}" if hh else line



# look verdict stopped at "...wedge feet, matte black," right before FAIL, and the block reason (where she
# wrote "verdict is FAIL") was never shown: `kanban show --json` carries it only in `events`. A helper
# asked for "only pass or fail" chose "pass" and the card was closed on it. Never cut silently.
_SHOW_COMMENT_MAX = 4000
_SHOW_BODY_MAX = 1500
_SHOW_COMMENTS = 5


def _clip_marked(text: str, limit: int) -> str:
    """The whole text, or `limit` chars and an explicit marker - never a silent cut."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f" [truncated, {len(text) - limit} more chars]"


def _board_block_reason(t: dict) -> tuple[str, str, bool]:
    """(reason, kind, from_history). The task row when it carries one, else the latest
    `blocked` event in the card's history (where the board actually keeps it)."""
    blocked = t.get("blocked") if isinstance(t.get("blocked"), dict) else {}
    reason = t.get("blocked_reason") or t.get("block_reason") or blocked.get("reason") or ""
    kind = t.get("blocked_kind") or blocked.get("kind") or ""
    if reason:
        return str(reason), str(kind or ""), False
    for ev in reversed(t.get("events") or []):
        if isinstance(ev, dict) and ev.get("kind") == "blocked":
            pl = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            if pl.get("reason"):
                return str(pl["reason"]), str(pl.get("kind") or kind or ""), True
    return "", str(kind or ""), False


def _board_show_text(t: dict) -> str:
    """A card: title · status · assignee · the block reason (the question, on a needs_input card;
    the last one from its history on a card that has moved on) · the body · the last comments, whole.
    Anything left out says so."""
    lines = [f"{t.get('id')} · {t.get('status')} · {t.get('assignee') or '-'} · {str(t.get('title') or '')[:120]}"]
    reason, kind, hist = _board_block_reason(t)
    if reason:
        label = "blocked" if (str(t.get("status") or "") == "blocked" or not hist) else "last blocked"
        lines.append(f"{label}{(' ' + kind) if kind else ''}: {_clip_marked(reason, _SHOW_COMMENT_MAX)}")
    body = str(t.get("body") or "").strip()
    if body:
        lines.append("body: " + _clip_marked(body, _SHOW_BODY_MAX).replace("\n", " "))
    comments = [c for c in (t.get("comments") or []) if isinstance(c, dict)]
    if len(comments) > _SHOW_COMMENTS:
        lines.append(f"[{len(comments) - _SHOW_COMMENTS} earlier comments not shown; the last {_SHOW_COMMENTS} follow]")
    for c in comments[-_SHOW_COMMENTS:]:
        lines.append(f"— {c.get('author') or '?'}: "
                     f"{_clip_marked(c.get('body') or c.get('text') or '', _SHOW_COMMENT_MAX)}")
    return "\n".join(lines)


def _list_dir(which: str) -> Path:
    return Path(_LIST_ROOT) / which


def _list_parse_steps(raw: str) -> list[str]:
    steps = []
    for line in (raw or "").splitlines():
        s = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", line).strip()
        if s:
            steps.append(s)
    return steps


def _list_read_dir(which: str) -> list[dict]:
    d = _list_dir(which)
    items = []
    if not d.is_dir():
        return items
    for fpath in sorted(d.glob("*.json")):
        try:
            item = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(item, dict):
            item["_file"] = fpath.name
            item["_status"] = which
            items.append(item)
    return items


def _list_find(item_id: str) -> tuple[dict | None, Path | None]:
    want = str(item_id or "").strip()
    if not want:
        return None, None
    for which in ("pending", "done", "decided"):
        for item in _list_read_dir(which):
            if str(item.get("id") or "") == want or str(item.get("_file") or "") == want:
                return item, _list_dir(which) / item["_file"]
    return None, None


def _list_write(item: dict, which: str = "pending") -> Path:
    d = _list_dir(which)
    d.mkdir(parents=True, exist_ok=True)
    item_id = str(item.get("id") or "").strip()
    fpath = d / f"{item_id}.json"
    blob = {k: v for k, v in item.items() if not str(k).startswith("_")}
    fpath.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    return fpath



# Until the cutover each profile's gateway is its own process, so `os.environ` IS that profile's.

# served profile is a context-local scope. Read in the plugin review of 09-27: a `team_board`
# unblock from a crew turn would have been authored `default`, which the loop counts as a hand's
# ruling. Everything below is inert until hermes itself says multiplexing is on.
def _multiplex_on() -> bool:
    try:
        from agent.secret_scope import is_multiplex_active
        return bool(is_multiplex_active())
    except Exception:  # noqa: BLE001 - an older hermes has no such thing: one profile per process
        return False


def _served_home() -> Path:
    """The home of the profile this turn is FOR: the bound scope under one host gateway, else the
    process's own HERMES_HOME (what this file always read)."""
    if _multiplex_on():
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    return Path(os.environ.get("HERMES_HOME") or "")


def _child_env():
    """The env a child that acts FOR the served profile is born with. None = inherit, which is
    today's behaviour byte for byte and the answer whenever multiplexing is off. Under one host
    gateway: that profile's HERMES_HOME and its own .env, never the host's. No scope bound there
    means the child would act as `default`, so it RAISES and the caller's verb does not land."""
    if not _multiplex_on():
        return None
    from hermes_constants import get_hermes_home_override
    if get_hermes_home_override() is None:
        raise RuntimeError("team-crissa: no profile scope is bound under the host gateway; "
                           "refusing to act as the default profile")
    from tools.environments.local import served_profile_child_env
    return served_profile_child_env(inherit_credentials=True)


def _scoped(name: str, default: str = "") -> str:
    """A per-profile setting that lives in the profile's .env (VOICE_SINK, KREW_SPEAK_TEMPO)."""
    if _multiplex_on():
        try:
            from agent.secret_scope import current_secret_scope
            v = (current_secret_scope() or {}).get(name)
            if v is not None:
                return str(v)
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get(name, default)


def _list_who() -> str:
    home = _served_home()
    name = home.name.lower()
    if name in _LIST_WHO:
        return name
    return "crissa"


def _list_drop(kind: str, title: str, body: str = "", steps: list[str] | None = None,
               card_id: str = "") -> dict:
    title = (title or "").strip()[:160]
    kind = "task" if kind == "task" else "ask"
    item_id = f"{kind}-{_slug(title)}-{int(time.time())}"
    item = {
        "id": item_id,
        "kind": kind,
        "title": title,
        "body": (body or ""),
        "steps": list(steps or []),
        "notes": [],
        "options": ["yes", "no"] if kind == "ask" else [],
        "source": _list_who(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    cid = str(card_id or "").strip()
    if cid:
        item["card_id"] = cid
    _list_write(item, "pending")
    return item


def _list_line(item: dict) -> str:
    kind = str(item.get("kind") or "item")
    wait = "wait on him" if kind == "ask" else "her work"
    n = len(item.get("steps") or [])
    extra = f"  {n} step{'s' if n != 1 else ''}" if n else ""
    cid = str(item.get("card_id") or "").strip()
    if cid:
        extra += f"  {cid}"
    return f"{kind:4}  {wait:12}  {str(item.get('title') or '')[:80]}{extra}"


def _list_show_text(item: dict) -> str:
    kind = str(item.get("kind") or "item")
    wait = "wait on him" if kind == "ask" else "her work"
    lines = [f"{item.get('id')} · {kind} · {wait} · {str(item.get('title') or '')}"]
    body = str(item.get("body") or "").strip()
    if body:
        lines.append(body)
    for i, step in enumerate(item.get("steps") or [], 1):
        lines.append(f"{i}. {step}")
    for n in (item.get("notes") or []):
        if isinstance(n, dict):
            lines.append(f"— {n.get('ts') or '?'}: {str(n.get('text') or '')}")
        else:
            lines.append(f"— {str(n)}")
    cid = str(item.get("card_id") or "").strip()
    if cid:
        lines.append(f"card {cid} — land the answer on the card to clear this")
    return "\n".join(lines)


def _needs_board_db() -> Path | None:
    root = KH.kanban_dir(passwd=True)
    current = root / "current"
    slug = "daily"
    if current.is_file():
        slug = current.read_text(encoding="utf-8").strip() or slug
    p = root / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def _is_need_card(kind: str, title: str, assignee: str = "") -> bool:
    k = (kind or "").strip().lower()
    t = (title or "").strip().lower()
    a = (assignee or "").strip().lower()
    if k == "needs_input":
        return True
    if t.startswith("needs human") or t.startswith("needs crissa") or t.startswith("needs-crissa"):
        return True
    if "needs-crissa" in t or "needs crissa:" in t:
        return True
    if a == "crissa" and t.startswith("needs"):
        return True
    return False


def _list_upsert_need(card_id: str, title: str, reason: str = "") -> dict | None:
    cid = str(card_id or "").strip()
    if not cid:
        return None
    title = (title or cid).strip()[:160]
    reason = (reason or "").strip()
    for item in _list_read_dir("pending"):
        if str(item.get("card_id") or "") == cid:
            changed = False
            if title and item.get("title") != title:
                item["title"] = title
                changed = True
            if reason and item.get("body") != reason:
                item["body"] = reason
                changed = True
            if changed:
                _list_write(item, "pending")
            return item
    return _list_drop("ask", title, body=reason, card_id=cid)


def _list_is_users(item: dict) -> bool:
    ""








    if os.environ.get("KREW_CRISSA_LIST_FOREIGN", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    src = str(item.get("source") or "").strip().lower()
    return bool(src) and src not in _LIST_WHO


def _list_clear_card(card_id: str, note: str = "") -> int:
    cid = str(card_id or "").strip()
    if not cid:
        return 0
    n = 0
    for item in _list_read_dir("pending"):
        if str(item.get("card_id") or "") != cid:
            continue
        if _list_is_users(item):
            continue
        item["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if note:
            notes = list(item.get("notes") or [])
            notes.append({"ts": item["done_at"], "text": note})
            item["notes"] = notes
        fname = str(item.get("_file") or "")
        _list_write(item, "done")
        if fname:
            try:
                (_list_dir("pending") / fname).unlink()
            except FileNotFoundError:
                pass
        n += 1
    return n



# block_kind='needs_input' — an unanswered question, explicitly hers — while sitting

# standing board that was NINE cards against two she could see, some of them 33 h old,
# including five dashboard probes nothing would ever retire. A park is not an answer:
# the question is still open, it is just parked somewhere her queue never looked.
# Kill KREW_CRISSA_SEES_PARKED=0 for the old blocked-only queue.
def _need_status_sql() -> str:
    if os.environ.get("KREW_CRISSA_SEES_PARKED", "1").strip().lower() in ("0", "false", "no", "off"):
        return "status = 'blocked'"
    return "(status = 'blocked' OR (status = 'scheduled' AND block_kind = 'needs_input'))"


def _list_live_need_ids() -> set[str] | None:
    """Current Decide-queue card ids, or None if the board cannot be read."""
    db = _needs_board_db()
    if db is None:
        return None
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            f"SELECT id, title, assignee, block_kind FROM tasks WHERE {_need_status_sql()}"
        ).fetchall()
        con.close()
    except Exception:
        return None
    live: set[str] = set()
    for tid, title, assignee, kind in rows:
        if _is_need_card(str(kind or ""), str(title or ""), str(assignee or "")):
            live.add(str(tid))
    return live


def _list_prune_stale() -> int:
    """Pending asks whose card left the Decide queue (or never had one) leave the glass.


    never pruned, so the CP tile lied.
    """
    live = _list_live_need_ids()
    if live is None:
        return 0
    n = 0
    for item in _list_read_dir("pending"):
        cid = str(item.get("card_id") or "").strip()
        if cid in live:
            continue
        if _list_is_users(item):
            continue
        note = "pruned: left the Decide queue" if cid else "pruned: no live Decide card"
        item["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        notes = list(item.get("notes") or [])
        notes.append({"ts": item["done_at"], "text": note})
        item["notes"] = notes
        fname = str(item.get("_file") or "")
        _list_write(item, "done")
        if fname:
            try:
                (_list_dir("pending") / fname).unlink()
            except FileNotFoundError:
                pass
        n += 1
    return n


def _list_sync_needs() -> int:
    """Need Crissa / needs_input cards appear on Crissa's list. Prune + upsert."""
    pruned = _list_prune_stale()
    live = _list_live_need_ids()
    if live is None:
        return pruned
    db = _needs_board_db()
    if db is None:
        return pruned
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            f"SELECT id, title, assignee, block_kind FROM tasks WHERE {_need_status_sql()}"
        ).fetchall()
        con.close()
    except Exception:
        return pruned
    n = pruned
    for tid, title, assignee, kind in rows:
        if not _is_need_card(str(kind or ""), str(title or ""), str(assignee or "")):
            continue
        _list_upsert_need(str(tid), str(title or tid), "")
        n += 1
    return n


def team_list(*args, **kwargs):
    """Crissa's list on CP. action list|show|ask|task|note|done."""
    p: dict = {}
    if args and isinstance(args[0], dict):
        p.update(args[0])
    p.update({k: v for k, v in kwargs.items() if k != "task_id"})
    action = str(p.get("action") or "list").strip().lower()
    if action not in _LIST_ACTIONS:
        return "team_list action is one of: " + ", ".join(_LIST_ACTIONS) + ". Nothing changed."
    tid = str(p.get("id") or "").strip()
    title = str(p.get("title") or "").strip()
    text = str(p.get("text") or p.get("body") or "").strip()
    steps = _list_parse_steps(str(p.get("steps") or ""))
    if not steps and action == "task":
        steps = _list_parse_steps(text)
    if action == "list":
        _list_sync_needs()
        rows = _list_read_dir("pending")
        if not rows:
            return "The list is empty."
        head = f"{len(rows)} pending"
        return head + "\n" + "\n".join(_list_line(r) for r in rows)
    if action == "show":
        item, _path = _list_find(tid)
        if not item:
            return "No item " + (tid or "(need id=)") + "."
        return _list_show_text(item)
    if action in ("ask", "task"):
        if not title:
            return action + " needs title=. Nothing filed."
        card = str(p.get("card_id") or (tid if action == "ask" and tid.startswith("t_") else ""))
        item = _list_drop(action, title, body=text if action == "ask" else "",
                          steps=steps if action == "task" else [],
                          card_id=card)
        if action == "ask":
            return f"On Crissa's list (wait on him): {title[:80]}."
        n = len(item.get("steps") or [])
        return f"On Crissa's list: {title[:80]}" + (f" ({n} steps)" if n else "") + "."
    if action == "note":
        item, path = _list_find(tid)
        if not item or path is None:
            return "note needs a live id=. Nothing changed."
        if not text:
            return "note needs text=. Nothing changed."
        notes = list(item.get("notes") or [])
        notes.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text})
        item["notes"] = notes
        which = str(item.get("_status") or "pending")
        _list_write(item, which)
        return f"Noted on {item.get('id')}."
    if action == "done":
        item, path = _list_find(tid)
        if not item or path is None:
            return "done needs a live id=. Nothing changed."
        cid = str(item.get("card_id") or "").strip()
        if cid:
            return f"Land the answer on the card: team_board answer id={cid} text=…"
        if str(item.get("kind") or "") == "ask":
            return "That one waits on him. He marks it on Crissa's list."
        if str(item.get("_status") or "") != "pending":
            return f"{item.get('id')} is already {item.get('_status')}."
        item["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if text:
            notes = list(item.get("notes") or [])
            notes.append({"ts": item["done_at"], "text": text})
            item["notes"] = notes
        _list_write(item, "done")
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return f"Done: {str(item.get('title') or item.get('id'))[:80]}."
    return "Nothing changed."


def _hermes_bin() -> str:
    import shutil as _sh
    return _sh.which("hermes") or os.path.expanduser("~/.local/bin/hermes")


# A BOARD WRITE IS SIGNED BY WHOEVER MAKES IT (the User 2026-10-11: "kanban sign in as themselves"). team_board signed
# every create, comment and answer as "crissa", and ada, bash, lila, steward and vera all load this plugin, so any crew
# worker could post a ruling as Crissa; T0 never sees this tool (it gates terminal/code/file tools). The author is now
# the profile the call runs FOR, asked at call time the way hermes's own kanban tools ask it
# (hermes_cli.profiles.current_profile_name): crissa signs crissa, the User's own default profile (his CLI and the
# dashboard) signs user as every session does, and any other profile signs its own name, which no ruler set counts.
# Nothing that cannot be named ever signs crissa. Kill: KREW_BOARD_SIGN_AS_SELF=0 (the old fixed "crissa").
BOARD_RULER_PROFILES = {"crissa": "crissa", "default": "user"}


def _board_sign_as_self() -> bool:
    return os.environ.get("KREW_BOARD_SIGN_AS_SELF", "1").strip().lower() not in ("0", "false", "no", "off")


def _board_profile(ctx=None) -> str:
    """The profile this board call runs for, or '' when nothing names one."""
    name = ""
    try:
        from hermes_cli.profiles import current_profile_name
        name = str(current_profile_name(None) or "")
    except Exception:  # noqa: BLE001
        name = ""
    if not name and ctx is not None:
        try:
            name = str(getattr(ctx, "profile_name", "") or "")
        except Exception:  # noqa: BLE001
            name = ""
    if not name:
        name = os.environ.get("HERMES_PROFILE_NAME") or os.environ.get("HERMES_PROFILE") or ""
    return name.strip().lower()


def _board_author(ctx=None) -> str:
    """Who a team_board write is signed as: crissa, user (the User's default profile), or the caller's own name."""
    if not _board_sign_as_self():
        return "crissa"
    name = _board_profile(ctx)
    return BOARD_RULER_PROFILES.get(name, name or "unknown")


def _board_may_rule(ctx=None) -> bool:
    """Only Crissa and the User land the User's words (team_board answer): it comments, unblocks and may close."""
    return (not _board_sign_as_self()) or _board_profile(ctx) in BOARD_RULER_PROFILES


def _kanban_cli(args: list, timeout: float = 25.0) -> tuple[int, str]:
    try:
        r = subprocess.run([_hermes_bin(), "kanban", *args], capture_output=True, text=True, timeout=timeout,
                           env=_child_env())
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return 1, f"{e.__class__.__name__}: {str(e)[:120]}"


def _answer_holds(text: str, body: str = "") -> bool:
    """True when her ruling must NOT unblock the card.

    `answer` was always comment + unblock, so every ruling handed the card


    do-not-implement card to a worker. kanban-loop consumes both from the
    comment — Park schedules the card, a gate ruling cuts the try / archives.
    Kill CRISSA_ANSWER_HOLD=0 (every answer unblocks again)."""
    import re as _re
    if os.environ.get("CRISSA_ANSWER_HOLD", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    t = _re.sub(r"^unblock\b[\s:,.;!-]*", "", (text or "").strip(), flags=_re.I)
    first = _re.split(r"[\s:,.;!—-]+", t.lower(), 1)[0] if t else ""
    if first in ("park", "parked", "drop", "archive"):
        return True

    # unblocked here, claimed and re-blocked untyped within a minute; nothing read the date.
    # The answer handler re-blocks it `dependency` with the date; kanban-loop is the alarm.
    if first == "later":
        return True
    # A restart-window card is ruled in words too: krew-restart-window reads

    return bool(_re.search(r"^gate: (green|restart-window)\s*$", body or "", _re.M))


def _done_closes_on() -> bool:
    ""
    return os.environ.get("KREW_CRISSA_DONE_CLOSES", "").strip().lower() in ("1", "on", "true", "yes")


def _is_done_ruling(text: str) -> bool:
    """True only when the ruling's FIRST word is `done` (a leading UNBLOCK: tolerated) - the same one spelling
    kanban-loop's verdict_of reads. 'complete the smoke first', 'donezo' or a done mid-sentence is not it."""
    import re as _re
    t = _re.sub(r"^unblock\b[\s:,.;!-]*", "", (text or "").strip(), flags=_re.I)
    first = _re.split(r"[\s:,.;!—-]+", t.lower(), 1)[0] if t else ""
    return first == "done"


def _answer_done(tid: str, text: str, cli, clear) -> str:
    ""





    rc, out = cli(["complete", tid, "--result", (text or "")])
    n = clear(tid, note=text)
    tail = "" if n else " (no list row)"
    if rc == 0:
        return f"Landed on {tid}: done - closed in the same step (no unblock, no worker) and cleared Crissa's list." + tail
    return (f"Landed on {tid}: done held for kanban-loop ({out[:80]}) - no unblock, the card stays off the "
            "dispatcher - and cleared Crissa's list." + tail)


def _later_date(text: str) -> str:
    """'YYYY-MM-DD' from a `later <date>` ruling (a leading UNBLOCK: tolerated), else ''."""
    import re as _re
    t = _re.sub(r"^unblock\b[\s:,.;!-]*", "", (text or "").strip(), flags=_re.I)
    m = _re.match(r"^\s*later\b[\s:,-]*(\d{4}-\d{2}-\d{2})", t, _re.I)
    return m.group(1) if m else ""


def _card_body(tid: str) -> str:
    rc, out = _kanban_cli(["show", tid, "--json"])
    if rc != 0 or "{" not in out:
        return ""
    try:
        data = json.loads(out[out.index("{"):])
    except Exception:  # noqa: BLE001
        return ""
    t = data.get("task") if isinstance(data.get("task"), dict) else data
    return str((t or {}).get("body") or "") if isinstance(t, dict) else ""


def _xspaces_drill_beat() -> dict | None:
    """The X Spaces lap's current beat while a lap runs (lib/xspaces_drill writes ~/.hermes/xspaces-drill.active),
    else None. A stale file (its runner gone) is ignored."""
    if os.environ.get("KREW_XSPACES_DRILL_TAG", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        d = json.loads((KH.hermes_home() / "xspaces-drill.active").read_text())
        os.kill(int(d["pid"]), 0)
        return d if d.get("beat") and d.get("day") else None
    except Exception:
        return None



# image-2512, then wan22, no flux" away, and Lila's default is flux2-turbo, which is not for publishing. A render card
# whose words say commercial / marketing / hero / ad gets the licence line in code. Off: KREW_COMMERCIAL_RENDER_LINE=0.
COMMERCIAL_RENDER_LINE = ("Engines (commercial use): still = qwen-image-2512 (Apache 2.0), clip = wan22-i2v from that still, "
                          "or ltx23-* (Lightricks LTX community licence: commercial under $10M annual revenue, outputs labelled AI-generated, "
                          "no competing product). Never flux2-*, ltx25-* or Qwen-Image 2.1 - not licensed for publishing "
                          "(config/krew-only-assets.json).")


def _commercial_render_line(title: str, text: str) -> str:
    if os.environ.get("KREW_COMMERCIAL_RENDER_LINE", "1").strip().lower() in ("0", "false", "no", "off"):
        return text
    words = f"{title} {text}"
    if (re.search(r"\b(?:commercial|marketing|hero|ads?|launch|promo)\b", words, re.I)
            and re.search(r"\b(?:render|clip|still|picture|image|video|photo|shot)\b", words, re.I)
            and COMMERCIAL_RENDER_LINE not in (text or "")):
        return ((text or "").rstrip() + "\n\n" + COMMERCIAL_RENDER_LINE).strip()
    return text


def team_outbox(*args, **kwargs):
    """X post / Google Ads draft into ~/Desktop/krew-deliverables/ready-to-post (lib/marketing_outbox). Never posts."""
    p: dict = dict(args[0]) if args and isinstance(args[0], dict) else {}
    p.update({k: v for k, v in kwargs.items() if k != "task_id"})
    try:
        import importlib.util as _ilu_mo
        _spec = _ilu_mo.spec_from_file_location("krew_marketing_outbox", Path(__file__).resolve().parent / "marketing_outbox.py")
        mo = _ilu_mo.module_from_spec(_spec)
        _spec.loader.exec_module(mo)
    except Exception as e:
        return f"The outbox is not available ({e.__class__.__name__}). Nothing written."
    if not mo.enabled():
        return "The marketing outbox is switched off. Nothing written."
    kind = str(p.get("kind") or "x").strip().lower().replace("-", "_").replace(" ", "_")
    if kind in ("x", "tweet", "post", "space", "spaces", "thread"):
        path, line = mo.x_post(str(p.get("text") or ""), str(p.get("title") or ""))
    elif kind in ("google_ads", "ads", "google", "ad", "search_ad"):
        path, line = mo.google_ads(str(p.get("title") or p.get("campaign") or "Crissa"), p.get("headlines"),
                                   p.get("descriptions"), final_url=str(p.get("url") or ""), keywords=p.get("keywords"))
    else:
        return "kind= x or google_ads. Nothing written."
    return line + (f"\nconfirmed: saved {path}" if path else "")


def _slug(title: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", (title or "report").lower()).strip("-")
    return (s[:48] or "report")


def _render_report(md: str, title: str) -> tuple[Path, Path]:
    """Her markdown → <slug>.md + <slug>.html in krew-deliverables, rendered
    with scripts/krew-report-html.py --kind crissa (her template). Returns
    (md_path, html_path). The markdown is the record; the HTML is what he
    reads (skill krew-deliverable); plugin krew-crystal wants both."""
    import importlib.util as _ilu
    import datetime as _dt
    DELIVERABLES.mkdir(parents=True, exist_ok=True)
    day = _dt.date.today().isoformat()
    base = f"crissa-{day}-{_slug(title)}"
    md_path = DELIVERABLES / f"{base}.md"
    n = 2
    while md_path.exists() and md_path.read_text(encoding="utf-8") != md:
        md_path = DELIVERABLES / f"{base}-{n}.md"
        n += 1
    if title and not md.lstrip().startswith("# "):
        md = f"# {title}\n\n{md}"
    md_path.write_text(md, encoding="utf-8")
    html_path = md_path.with_suffix(".html")
    renderer = _CREW / "scripts" / "krew-report-html.py"
    spec = _ilu.spec_from_file_location("krew_report_html", renderer)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    html_path.write_text(mod.render_markdown(md, title=title, author="Crissa"), encoding="utf-8")
    return md_path, html_path


def _living_html(*names: str) -> str:
    inbox = Path.home() / "Desktop" / "krew-deliverables"
    for n in names:
        p = inbox / n
        if p.is_file():
            return "file://" + str(p)
    return ""


def _post_sheet(*, title: str, body: str, headline: str = "") -> tuple[bool, str]:
    """A sheet of words on the Wall's card through the player (doc + doc_title), read back whole."""
    title = (title or "Notes").strip()
    body = body or title
    ok, card, why = _screen({"clear": "all", "doc": body, "doc_title": title})
    if not ok:
        return False, why
    if card.get("doc") != body:
        return False, "the player took it, but the Wall does not show this sheet — " + _card_says(card)
    return True, f"sheet '{title}' on the Wall ({len(body.splitlines())} lines)"


def _show_beside_her(*, dest: str = "", title: str = "", text: str = "") -> str:
    """A page or report beside her on the Wall's card, through the player. A local HTML file goes up whole (framed,
    no scripts), a local text file as a sheet, a web address in a frame, anything else as a sheet of words."""
    title = (title or "page").strip()
    body: dict = {"clear": "all", "title": title}
    path = dest[7:] if dest.startswith("file://") else ""
    if path:
        try:
            raw = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"NOTHING CHANGED on the Wall: could not read {path} ({type(e).__name__}). Do not say it is up."
        if path.lower().endswith((".html", ".htm")):
            body["html"] = raw
        else:
            body["doc"], body["doc_title"] = raw, title
    elif dest.startswith(("http://", "https://")):
        body["url"] = dest
    else:
        body["doc"], body["doc_title"] = (text or title or dest), title
    ok, card, why = _screen(body)
    if not ok:
        return f"NOTHING CHANGED on the Wall ({why}). Do not say it is up."
    if not any(card.get(k) == body[k] for k in ("html", "url", "doc") if k in body):
        return f"NOTHING CHANGED on the Wall as asked — {_card_says(card)} Do not say it is up."
    return _card_says(card) + " She is still on the Wall. Say the lede; show_me home when the topic moves."


def _start_registration(name: str, mode: str) -> str:
    """Kick a face-only or voice-only enroll on the registration panel, through the player's rail.

    Same flow as the panel buttons: camera lease→aim→verify cadence, cues project to the room screen, camera hands
    back to the door after. When the person already has records the panel asks first (needs_confirm). Replacing
    them is done from the Control Panel only, never by voice: a spoken yes cannot prove who is speaking. This tool
    never asks the panel to replace.
    """
    name = (name or "").strip()
    if not name:
        return "Need a name — who am I registering?"
    try:
        r = _post_json(f"{ENROLL_PANEL}/start", {"display": name, "mode": mode, "create": True})
    except urllib.error.HTTPError as e:
        try:
            r = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            r = {}
        if not (isinstance(r, dict) and r.get("needs_confirm")):
            return f"Could not start: {_rail_says(e.code, r, e.reason, 'registration panel')}"
    except Exception as e:
        return f"Could not start: {_rail_why(e, 'registration panel')}"
    if r.get("needs_confirm"):
        who = r.get("display") or name
        return (f"NEEDS CONFIRMATION — nothing started yet: {who} already has records "
                f"(face={r.get('face')} voice={r.get('voice')}). Replacing someone's face or voice records is done "
                f"from the Control Panel's registration page, not by voice (a spoken yes cannot prove who is "
                f"speaking): tell {who} to replace them there.")
    if not r.get("ok"):
        return f"Could not start: {r.get('error', 'unknown')}"
    what = "face capture" if mode == "face" else "voice takes"
    return (f"Started {what} for {r.get('display') or name} "
            f"(id {r.get('person_id')}). Coach is on the screen — "
            "follow the spoken cues; the camera returns to the door after.")


def _default_sink() -> str:
    """ESS dongle analog when present — never the Seeed jack."""
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True, text=True, timeout=3,
        )
        sinks = [ln.split()[1] for ln in (r.stdout or "").splitlines() if len(ln.split()) >= 2]
    except Exception:
        sinks = []
    dongle = next(
        (s for s in sinks if "ESS_Technology_AUDIO_DONGLE" in s and "analog-stereo" in s),
        None,
    ) or next(
        (s for s in sinks if "ESS_Technology_AUDIO_DONGLE" in s and s.startswith("alsa_output.")),
        None,
    )
    wanted = os.environ.get("VOICE_SINK", "").strip()
    if wanted in ("", "echocancel_sink") and dongle:
        return dongle
    if wanted and "Seeed_Studio_reSpeaker_XVF3800" not in wanted:
        return wanted
    r = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True)
    return dongle or (r.stdout or "").strip() or "echocancel_sink"


@contextmanager
def _announce_lock(timeout: float = 180.0):
    """Cross-process exclusive lock — one announcer on the speaker at a time."""
    _ANNOUNCE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_ANNOUNCE_LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.time() + max(5.0, timeout)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                os.close(fd)
                raise TimeoutError("krew announce lock busy")
            time.sleep(0.15)
    try:
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _dedup_skip(key: str) -> bool:
    """True if this task was already enqueued very recently (double-hook / multi-worker).

    Writes hook-dedup only. Never the play-ack file krew_announce reads.
    """
    if not key:
        return False
    now = time.time()
    try:
        data = (
            json.loads(_ANNOUNCE_HOOK_DEDUP.read_text())
            if _ANNOUNCE_HOOK_DEDUP.is_file()
            else {}
        )
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # prune
    data = {k: float(v) for k, v in data.items() if now - float(v) < _DEDUP_SEC * 3}
    last = float(data.get(key) or 0)
    if last and (now - last) < _DEDUP_SEC:
        return True
    data[key] = now
    try:
        _ANNOUNCE_HOOK_DEDUP.write_text(json.dumps(data))
    except OSError:
        pass
    return False


_LEAD_MS = int(os.environ.get("KREW_SPEAK_LEAD_MS", "400"))


def _pad_wav_lead(path: str, ms: int = 0) -> None:
    ""

    ms = ms or _LEAD_MS
    if ms <= 0:
        return
    try:
        import wave

        with wave.open(path, "rb") as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
        pad = b"\x00" * (params.sampwidth * params.nchannels *
                         int(params.framerate * ms / 1000.0))
        with wave.open(path, "wb") as w:
            w.setparams(params)
            w.writeframes(pad + frames)
    except Exception:
        pass  # unpadded beats unplayed


def _paplay_wav(path: str, sink: str | None = None) -> None:
    sink = sink or _default_sink()
    _pad_wav_lead(path)
    subprocess.run(["pactl", "set-sink-mute", sink, "0"], capture_output=True)
    env = os.environ.copy()
    env["PULSE_SINK"] = sink
    r = subprocess.run(
        ["paplay", f"--device={sink}", path],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "paplay fail")[:200])


def _cut_at_sentence(text: str, limit: int = 800) -> str:
    """Never slice mid-sentence — a hard [:800] went out as partial messages

    and say where the rest lives."""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    head = t[:limit]
    for mark in (". ", "! ", "? "):
        i = head.rfind(mark)
        if i > limit // 3:
            return head[: i + 1] + " More on the board."
    return head.rsplit(" ", 1)[0] + "… More on the board."


def _for_speech(text: str) -> str:
    """Plain words for her voice. The player makes them speakable by its own rules; krew only trims."""
    return " ".join((text or "").split())


def _player_post(path: str, body: dict, timeout: float = 10.0) -> tuple[int, dict]:
    """POST to the Wall's player. (0, {"error": ...}) when no player answers (Crissa not installed or not running)."""
    req = urllib.request.Request(CRISSA_PLAYER + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
            return r.status, out if isinstance(out, dict) else {}
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            out = {}
        return e.code, out if isinstance(out, dict) else {}
    except (OSError, ValueError) as e:
        return 0, {"error": str(e)}


def _no_player() -> str:
    return f"Crissa not installed (no player at {CRISSA_PLAYER}) — said nothing"


def _announce_player(text: str, *, category: str = "completion", dedup_key: str = "") -> str:
    """The krew PA through the Wall's /v1/announce. The player queues the line behind her answers, holds it while
    a conversation is open or someone is talking, and keeps its own quiet hours, roundup and overflow rules.
    KREW_ANNOUNCE_SPEECH_PAUSE_SEC: how long after someone spoke it holds. KREW_ANNOUNCE_MIC_WAIT_S, when set:
    how long a line may wait before the player drops it (logged); unset, nothing is dropped for waiting. The
    lock keeps one hand-off at a time across processes; the hook dedup (KREW_ANNOUNCE_DEDUP_SEC) runs before."""
    text = _for_speech(text)
    if not text:
        return "announce skipped — nothing speakable after sanitize"
    body = {"text": _cut_at_sentence(text), "kind": category, "by": "krew", "speech_pause_s": _SPEECH_PAUSE_SEC}
    wait = os.environ.get("KREW_ANNOUNCE_MIC_WAIT_S", "").strip()
    if wait:
        try:
            body["max_wait_s"] = float(wait)
        except ValueError:
            pass
    if dedup_key:
        body["dedup_key"] = dedup_key
    with _announce_lock(timeout=15.0):
        code, out = _player_post("/v1/announce", body)
    if code == 0:
        return _no_player()
    if code in (200, 202) and out.get("queued"):
        return f"announce queued on the Wall ({category}; {out.get('depth')} waiting)"
    if code == 404:
        return "announce unavailable — this Wall has no /v1/announce (update Crissa)"
    return f"announce skipped — {out.get('reason') or out.get('error') or code}"


def _coerce_tool_args(text, voice="", *args, **kwargs):
    """Hermes may pass tool args as: kwargs, a single positional dict, or dict bound to `text`."""
    if isinstance(text, dict):
        d = text
        text = d.get("text", "") or ""
        voice = d.get("voice", voice) or voice
    elif args and isinstance(args[0], dict):
        d = args[0]
        text = d.get("text", text) or text
        voice = d.get("voice", voice) or voice
    # also merge kwargs overrides
    if kwargs.get("text") is not None and not isinstance(kwargs.get("text"), dict):
        text = kwargs.get("text") or text
    if kwargs.get("voice"):
        voice = kwargs.get("voice")
    return str(text or ""), str(voice or "")


def _speak_player(text: str, *, queue: bool = False, voice: str = "") -> str:
    """Her character voice through the Wall's /v1/speak: her mouth moves with it. queue waits for what she is
    saying instead of stopping it."""
    text = _for_speech(text)
    if not text:
        return "speak skipped — empty text"
    body = {"text": text, "by": "krew"}
    if queue:
        body["queue"] = True
    if voice:
        body["voice"] = voice
    code, out = _player_post("/v1/speak", body)
    if code == 0:
        return _no_player()
    if code in (200, 202):
        return "spoke on the Wall" + (" (queued)" if queue else "")
    return f"voice unavailable — {out.get('error') or code}"



# hermes builds a tool definition as {"type": "function", "function": {**schema, "name": name}} and its
# plugin guide says the model-facing description belongs in schema["description"]: the `description=`
# keyword is registry metadata and is never copied back. Every krew tool passed a bare JSON-schema
# object as `schema`, so the model was sent {"type": "object", "properties": ..., "name": "hands"} -
# no description at all and no `parameters`. It picked tools by name (team_compose for a card,

def canonical_schema(name, description, schema):
    """{"name", "description", "parameters"} - the shape every built-in tool registers."""
    import os as _os
    if _os.environ.get("KREW_PLUGIN_CANON_SCHEMA", "1").strip().lower() in ("0", "false", "no", "off"):
        return schema
    if isinstance(schema, dict) and "parameters" in schema:
        return schema
    params = schema if isinstance(schema, dict) and schema else {"type": "object", "properties": {}}
    return {"name": name, "description": str(description or "").strip(), "parameters": params}


KREW_BOARD_OWNS = frozenset({"team_board", "team_time", "team_config_audit"})


def _switch(name: str, default: bool = True) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return default if not v else v not in ("0", "false", "no", "off")


class _CanonCtx:
    """The plugin context, with every tool registered in the canonical schema shape."""

    def __init__(self, ctx):
        self._ctx = ctx

    def __getattr__(self, key):
        return getattr(self._ctx, key)

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        if name in KREW_BOARD_OWNS and _switch("KREW_TEAM_BOARD_FROM_KREW_BOARD"):
            return None                     # krew-board registers this one (enable krew-board beside team-crissa)
        return self._ctx.register_tool(name=name, toolset=toolset,
                                       schema=canonical_schema(name, kw.get("description"), schema),
                                       handler=handler, **kw)


_KANBAN_SHOW_SLIM: dict = {}
_SAID_MISSING: set = set()


def _say_missing_once(what: str, why: str) -> None:
    """Name a feature that is off because something it needs is not installed: once per process, on stderr."""
    if what not in _SAID_MISSING:
        _SAID_MISSING.add(what)
        print(f"[team-crissa] {what} is off: {why}", file=sys.stderr, flush=True)


def _register_parallel_board_reads() -> bool:
    ""

    if os.environ.get("KREW_PARALLEL_BOARD_READS", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    try:
        from agent.tool_dispatch_helpers import register_plugin_parallel_safe
    except Exception:  # noqa: BLE001 - hermes without patch 200
        _say_missing_once("KREW_PARALLEL_BOARD_READS", "this Hermes has no agent.tool_dispatch_helpers.register_plugin_parallel_safe")
        return False
    try:
        import importlib.util
        path = Path(__file__).resolve().parent / "board_parallel.py"
        if not path.is_file():
            _say_missing_once("KREW_PARALLEL_BOARD_READS", f"{path} is missing")
            return False
        spec = importlib.util.spec_from_file_location("krew_board_parallel", str(path))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        register_plugin_parallel_safe("team_board", mod.team_board_read_only)
        return True
    except Exception as e:  # noqa: BLE001
        _say_missing_once("KREW_PARALLEL_BOARD_READS", f"board_parallel did not load: {type(e).__name__}: {e}")
        return False


def register(ctx):
    _register_parallel_board_reads()
    ctx = _CanonCtx(ctx)
    profile = getattr(ctx, "profile_name", None) or "crissa"

    def team_speak(text: str = "", voice: str = "", *args, **kwargs):
        """Her character voice, through the Wall's player (/v1/speak)."""
        text, voice = _coerce_tool_args(text, voice, *args, **kwargs)
        d = args[0] if args and isinstance(args[0], dict) else {}
        queue = bool(kwargs.get("queue") or d.get("queue"))
        try:
            return _speak_player(text, queue=queue, voice=voice)
        except Exception as e:
            return f"voice unavailable ({e.__class__.__name__}) — said nothing"

    def team_announce(text: str = "", *args, **kwargs):
        """Krew PA through the Wall's player (/v1/announce): queued, never cuts her. Completions/stuck/protocol."""
        _d = text if isinstance(text, dict) else (args[0] if args and isinstance(args[0], dict) else {})
        text, _voice = _coerce_tool_args(text, "", *args, **kwargs)
        text = (text or "").strip()
        if not text or text in ("None", "null") or "Done, None" in text or text.endswith(", None"):
            return "announce skipped — empty/None"
        text = " ".join(text.replace("None", " ").split())
        if not text:
            return "announce skipped — empty text"
        # Optional category from kwargs / dict (default completion for kanban tools)
        cat = "completion"
        if args and isinstance(args[0], dict) and args[0].get("category"):
            cat = str(args[0].get("category"))
        cat = str(kwargs.get("category") or cat)
        cat = str(_d.get("category") or cat)
        key = str(kwargs.get("dedup_key") or _d.get("dedup_key") or "")
        try:
            return _announce_player(text, category=cat, dedup_key=key)
        except Exception as e:
            return f"announcer unavailable ({e.__class__.__name__}: {e})"

    def team_presence(*args, **kwargs):
        try:
            p = _get(PRESENCE)
            names = [s.get("name") or "unknown" for s in p.get("people") or []]
            return (
                f"headcount {p.get('headcount')} ({p.get('count_trust')}): "
                f"{', '.join(names) or 'nobody'}"
            )
        except Exception as e:
            return f"presence unavailable: {_rail_why(e, 'presence feed')}"

    def team_observe_presence(
        action: str = "",
        note: str = "",
        apply: bool = False,
        *args,
        **kwargs,
    ):
        """EA pause package: roster + count + frame hooks for ops judgment.

        Law: voice/face scores soft; count only via explicit action + apply=true
        into POST /v1/vl_delta. Never invent headcount from speech alone.
        Docs: ASSISTANT-OWNS-PRESENCE + TREATY-VOICE-EMB-P0.
        """
        if isinstance(action, dict):
            d = action
            action = d.get("action") or ""
            note = d.get("note") or note
            apply = bool(d.get("apply") or apply)
        action = str(kwargs.get("action") or action or "").strip().lower()
        note = str(kwargs.get("note") or note or "").strip()
        apply = bool(kwargs.get("apply") if "apply" in kwargs else apply)
        # normalize action aliases
        if action in ("+1", "enter", "in"):
            action = "add"
        if action in ("-1", "leave", "exit", "out"):
            action = "sub"
        if action in ("0", "none", "pass"):
            action = "null"

        out: dict = {"schema": "observe_presence/v1", "law": "count≠id≠auth≠voice"}
        try:
            p = _get(PRESENCE)
        except Exception as e:
            return f"observe_presence: presence down: {_rail_why(e, 'presence feed')}"

        people = p.get("people") or []
        roster = []
        for s in people:
            if not isinstance(s, dict):
                continue
            roster.append(
                {
                    "name": s.get("name"),
                    "certain": s.get("certain"),
                    "id_status": s.get("id_status"),
                    "auth": (s.get("auth") or {}).get("level")
                    if isinstance(s.get("auth"), dict)
                    else s.get("auth"),
                }
            )
        out["presence"] = {
            "headcount": p.get("headcount"),
            "count_trust": p.get("count_trust"),
            "camera_mode": p.get("camera_mode"),
            "people": roster,
            "last_event_kind": (p.get("last_event") or {}).get("kind"),
            "ts_local": p.get("ts_local"),
        }
        # Frame hooks (URLs for ops/Inspector; do not bulk base64 into chat)
        frames = {
            "snapshot": f"{CAMERA}/snapshot",
            "health": f"{CAMERA}/health",
            "stream": "the live stream is not forwarded by the player; use snapshot",
        }
        stills = []
        for s in people:
            if not isinstance(s, dict):
                continue
            es = s.get("enter_still") or {}
            for u in es.get("urls") or []:
                stills.append(f"{PRESENCE_BASE}{u}" if str(u).startswith("/") else u)
        frames["enter_stills"] = stills
        out["frames"] = frames
        out["hint"] = (
            "Judgment is yours (ops). Use frames + roster. "
            "To apply count: action=add|sub|null and apply=true. "
            "Voice emb is soft ID only (P0 log); never invent bodies from speech."
        )

        if action in ("add", "sub", "null") and apply:
            # Map to vl_delta
            entered = 1 if action == "add" else 0
            left = 1 if action == "sub" else 0
            body = {
                "entered": entered,
                "left": left,
                "source": "ops_observe_presence",
                "note": note or f"ea observe {action}",
                "force": False,
            }
            try:
                data = json.dumps(body).encode()
                req = urllib.request.Request(
                    f"{PRESENCE_BASE}/v1/vl_delta",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as r:
                    resp = json.loads(r.read().decode())
                out["applied"] = {"action": action, "ok": True, "response": resp}
            except Exception as e:
                out["applied"] = {
                    "action": action,
                    "ok": False,
                    "error": _rail_why(e, "presence feed"),
                }
        elif action in ("add", "sub", "null") and not apply:
            out["applied"] = {
                "action": action,
                "ok": False,
                "error": "pass apply=true to write SoT (preview only)",
            }

        return json.dumps(out, ensure_ascii=False)

    def team_look(what: str = "hear", peek: bool = True, *args, **kwargs):
        """Camera look: Inspector hear (ID a beam) or room_check (body vs SoT).

        After a clip lands (mp4), look with Hermes video_analyze
        (auxiliary.video) — not vision_analyze. Skill crissa-look.
        """
        if isinstance(what, dict):
            d = what
            what = str(d.get("what") or d.get("op") or "hear")
            if "peek" in d:
                peek = bool(d.get("peek"))
        what = str(kwargs.get("what") or kwargs.get("op") or what or "hear").strip().lower()
        if "peek" in kwargs:
            peek = bool(kwargs.get("peek"))
        op = "room_check" if what in ("room", "room_check", "bodies", "count") else "hear"
        body: dict = {"op": op}
        if op == "hear":
            body["peek"] = bool(peek)
        else:
            body["peek_regulars"] = bool(peek)
        try:
            try:
                pause_req = urllib.request.Request(
                    f"{CAMERA}/v1/pause",
                    data=json.dumps(
                        {"owner": "crissa_her_look", "ttl_sec": 45, "reason": op}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(pause_req, timeout=3).read()
                paused = "camera paused"
            except Exception as e:
                paused = "camera not paused: " + _rail_why(e, "camera")
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{PRESENCE_BASE}/v1/inspector",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as r:
                resp = json.loads(r.read().decode())
        except Exception as e:
            return f"look failed: {_rail_why(e, 'presence feed')} ({paused})"
        if op == "hear":
            s = resp.get("suggest") or {}
            return (
                f"hear {s.get('person_id') or 'unknown'} "
                f"({s.get('suggest_level')}) beam={s.get('beam_legal')} "
                f"peeked_faces={(resp.get('peeked') or {}).get('n_faces')}"
                + ("" if paused == "camera paused" else f" ({paused})")
            )
        return (
            f"room_check sot={resp.get('sot_headcount')} "
            f"seen_sum={resp.get('faces_seen_sum')} drift={resp.get('drift')} "
            f"roster={resp.get('sot_roster')}"
            + ("" if paused == "camera paused" else f" ({paused})")
        )

    def show_me(*args, **kwargs):
        ""







        p: dict = {}
        if args and isinstance(args[0], dict):
            p.update(args[0])
        elif args and isinstance(args[0], str):
            p["what"] = args[0]
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        it = _display_intent(p)
        L = it["landing"]
        if L == "view":
            return team_display({"action": "view", "name": it["name"], "url": it["dest"]})
        if L == "home":
            return team_display({"action": "home"})
        if L == "clear":
            return team_display({"action": "clear", "target": it["target"] or "all"})
        if L in ("note", "report") and os.environ.get("HERMES_KANBAN_TASK") and (
                os.environ.get("HERMES_PROFILE") or "").strip().lower() != "crissa":


            # A worker's report is its card's (kanban_comment / kanban_complete --result);
            # a child's is its parent's return text. Only Crissa shows a page beside her.
            return ("NOTHING CHANGED on the wall — a kanban worker's report never goes beside her. "
                    f"Put it on task {os.environ.get('HERMES_KANBAN_TASK')} (kanban_comment / "
                    "kanban_complete --result) or, as a delegated child, return it to your parent.")
        if L == "note":
            return team_display({"action": "chalk", "text": it["text"], "title": it["title"],
                                 "notes_ttl": it["hold"]})
        if L == "resting":
            return team_display({"action": "resting", "art": it["art"], "moment": it["moment"],
                                 "text": it["text"]})
        if L == "tab":
            dest = it["dest"]
            if dest.startswith("file://"):
                return _show_beside_her(
                    dest=dest,
                    title=it["title"] or it["name"] or "page",
                    text=it["text"] or it["title"] or dest,
                )
            return team_display({"action": "tab", "url": dest, "name": it["name"]})
        if L in ("tabs", "shot"):
            return team_display({"action": L, "target": it["target"]})
        if L in ("activate", "close"):
            return team_display({"action": L, "target": it["target"]})
        if L in ("status", "mode"):
            return team_display({"action": it["target"] or "status"})
        if L == "report":
            text = it["text"]
            if not text:
                return ('report needs text= — markdown in crissa-report\'s shape: '
                        '"# Title\\nlede: …\\nkpi: a = 1; b = 2\\n## What I found\\n- …". '
                        "Nothing changed on the wall.")
            title = it["title"] or next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "Report")
            try:
                md_path, html_path = _render_report(text, title)
            except Exception as e:
                return (f"Could not write the report ({e.__class__.__name__}: {str(e)[:120]}). "
                        "Nothing changed on the wall — say the finding out loud.")
            dest = "file://" + str(html_path)
            line = _show_beside_her(dest=dest, title=title, text=_cut(text, 8000, "the page itself has the rest"))
            if line.startswith("NOTHING CHANGED"):
                return line + f" The report is saved at {html_path} (and {md_path.name})."
            return (f"{line} Saved {md_path.name} + {html_path.name} in {DELIVERABLES}.")
        return ("show_me needs what= — a screen (" + ", ".join(sorted(k for k in WALL_VIEWS if k not in ("face", "her", "scene", "board", "control", "hq", "chat")))
                + "), 'report' + text=, 'note' + text=, a URL, a file path, 'home', 'clear' or 'status'. "
                + (f"I don't know a screen called {it['name']!r}. " if it.get("name") else "")
                + "Nothing changed on the wall.")

    def team_config_audit(*args, **kwargs):
        """What am I configured with, what does Hermes offer, what am I missing?


        configs relative to the hermes docs — what they have vs what is
        available — to base suggested edits on"). Every crew profile carries the
        `team` toolset, so putting it here reaches all of them at once.
        """
        # Chat/social integrations — real, but noise for a krew crew. Folded
        # into a count so the interesting gaps are actually readable.
        CHATTY = ("discord", "slack", "telegram", "whatsapp", "signal", "sms",
                  "email", "matrix", "mattermost", "feishu", "dingtalk", "wecom",
                  "weixin", "qqbot", "yuanbao", "bluebubbles", "spotify",
                  "x_search", "webhook")
        try:
            import sys as _sys
            if _HERMES_TREE not in _sys.path:
                _sys.path.insert(0, _HERMES_TREE)
            from toolsets import TOOLSETS
            from hermes_cli.config import get_config_path
            import yaml as _yaml
        except Exception as e:
            return f"cannot read the toolset catalogue ({e.__class__.__name__}: {str(e)[:120]})"
        try:
            # HERMES_HOME is what actually selects the profile inside a gateway.
            path = Path(get_config_path())
            cfg = _yaml.safe_load(path.read_text()) or {}
        except Exception as e:
            return f"cannot read my own config ({e.__class__.__name__}: {str(e)[:120]})"

        profile = path.parent.name if path.parent.parent.name == "profiles" else "default (BRIDGE)"
        have = list(cfg.get("toolsets") or [])
        plat = cfg.get("platform_toolsets") or {}
        deleg = cfg.get("delegation") or {}

        def _is_chatty(n):
            return n.startswith("hermes-") or any(c in n for c in CHATTY)

        missing = [n for n in sorted(TOOLSETS) if n not in have and not _is_chatty(n)]
        chatty_missing = [n for n in sorted(TOOLSETS) if n not in have and _is_chatty(n)]

        out = [f"profile: {profile}   ({path})", ""]
        out.append(f"YOU HAVE ({len(have)}): " + ", ".join(sorted(have)))
        # A grant that is missing from the platform you actually run on is a
        # silent no-op — the single most common config mistake here.
        gaps = {p: [t for t in have if t not in (v or [])] for p, v in plat.items()}
        gaps = {p: g for p, g in gaps.items() if g}
        if gaps:
            out.append("")
            out.append("WARNING — granted at top level but MISSING on these platforms "
                       "(so they do nothing there):")
            for p, g in gaps.items():
                out.append(f"  {p}: missing {', '.join(g)}")
        out.append("")
        out.append(f"AVAILABLE TO YOU, NOT GRANTED ({len(missing)}):")
        for n in missing:
            desc = (TOOLSETS[n].get("description") or "").split(".")[0][:88]
            out.append(f"  {n:16} — {desc}")
        if chatty_missing:
            out.append(f"  (+{len(chatty_missing)} chat/social integrations not listed)")
        out.append("")
        if deleg:
            out.append(f"SUBAGENTS: max_concurrent_children={deleg.get('max_concurrent_children')} "
                       f"max_spawn_depth={deleg.get('max_spawn_depth')} "
                       f"orchestrator_enabled={deleg.get('orchestrator_enabled')} "
                       f"child_model={deleg.get('model') or 'inherit'} "
                       f"child_url={deleg.get('base_url') or 'inherit'}"
                       + ("" if "delegation" in have else "  ← policy set but toolset NOT granted"))
        else:
            out.append("SUBAGENTS: no delegation block — Hermes defaults apply"
                       + ("" if "delegation" in have else " (and the toolset is not granted)"))
        out.append("")
        out.append("TO PROPOSE A CHANGE: edit the PACK — "
                   "your profile's config.yaml (krew: config/config.example.yaml shows the shape) — "
                   "never the live profile alone; packs are the deployment target and the next "
                   "`scripts/sync-packs.sh deploy` overwrites live-only edits. Say WHY the "
                   "toolset serves your role, then let the user decide.")
        return "\n".join(out)[:6000]

    _BOARD_READ_WORDS = frozenset({
        "card", "thread", "card_thread", "read", "read card", "detail", "details", "view", "get", "get_card",
        "open", "body", "comments", "history", "info", "inspect", "look"})

    def team_board(*args, **kwargs):
        """The board (kanban) in one tool. action list|show|create|comment|unblock|complete."""
        p: dict = {}
        if args and isinstance(args[0], dict):
            p.update(args[0])
        p.update({k: v for k, v in kwargs.items() if k not in _RUNTIME_KW})
        for k in _RUNTIME_KW:
            p.pop(k, None)
        action = str(p.get("action") or "list").strip().lower()
        tid = str(p.get("id") or p.get("task") or p.get("card") or p.get("card_id") or "").strip()
        text = str(p.get("text") or p.get("body") or "").strip()

        # card / thread / detail / read / get / view / body / comments / open / history ~230 times and every
        # one was refused ("action is one of ...") - a step of a four-step run each. Reading a card is
        # `show`; a read word with a card id IS show. `file` with a title is create. Nothing else is guessed.
        if action not in _BOARD_ACTIONS:
            if action in _BOARD_READ_WORDS and tid:
                action = "show"
            elif action in ("file", "new", "add") and str(p.get("title") or "").strip():
                action = "create"
        if action not in _BOARD_ACTIONS:
            return "team_board action is one of: " + ", ".join(_BOARD_ACTIONS) + ". Nothing changed."
        if action == "list":
            args_ = ["list", "--json"]
            st = str(p.get("status") or "").strip().lower()
            if st in _BOARD_STATUS:
                args_ += ["--status", st]
            who = str(p.get("assignee") or "").strip()
            if who:
                args_ += ["--assignee", who]
            rc, out = _kanban_cli(args_)
            if rc != 0:
                return f"The board did not answer ({out[:160]})."
            try:
                rows = json.loads(out[out.index("["):]) if "[" in out else []
            except Exception:
                return f"The board answered oddly ({out[:160]})."
            if not rows:
                return "The board is empty" + (f" for status {st}" if st else "") + "."
            rows = [r for r in rows if isinstance(r, dict)]
            window_s = _window_seconds(p)
            if window_s:
                floor = time.time() - window_s
                rows = [r for r in rows if _board_ts(r) >= floor]
            rows.sort(key=_board_ts, reverse=True)
            if not rows:
                return "Nothing in that window" + (f" for status {st}" if st else "") + "."
            head = _board_head(rows, st=st if st in _BOARD_STATUS else "", window_s=window_s)
            return head + "\n" + "\n".join(_board_line(r) for r in rows)
        if action == "show":
            if not tid:
                return "show needs id= (t_…). Nothing changed."
            rc, out = _kanban_cli(["show", tid, "--json"])
            if rc != 0:
                return f"No card {tid} ({out[:120]})."
            try:
                data = json.loads(out[out.index("{"):]) if "{" in out else {}
            except Exception:
                return out[:600]
            t = data.get("task") if isinstance(data.get("task"), dict) else data
            if isinstance(data.get("comments"), list) and isinstance(t, dict) and "comments" not in t:
                t = dict(t, comments=data["comments"])
            if isinstance(data.get("events"), list) and isinstance(t, dict) and "events" not in t:
                t = dict(t, events=data["events"])   # the block reason lives in the history
            return _board_show_text(t if isinstance(t, dict) else data)
        if action == "create":
            title = str(p.get("title") or "").strip()
            if not title:
                return "create needs title=. Nothing filed."
            who = str(p.get("assignee") or "ada").strip()
            drill = _xspaces_drill_beat()
            if drill:

                # keyed on its beat and day, so lap two reads lap one's card instead of filing a twin, and the Daily
                # and KPIs can tell drill cards from krew work. Off: KREW_XSPACES_DRILL_TAG=0.
                if not title.lower().startswith("[drill]"):
                    title = f"[drill] {title}"
            text = _commercial_render_line(title, text)
            args_ = ["create", title, "--assignee", who, "--created-by", _board_author(ctx)]
            if drill:
                args_ += ["--idempotency-key", f"xspaces:{drill['beat']}:{drill['day']}"]
            if text:
                args_ += ["--body", text]
            if p.get("triage") is None or p.get("triage") in (True, "true", "1", 1):
                args_.append("--triage")
            rc, out = _kanban_cli(args_)
            if rc != 0:
                return f"Not filed ({out[:160]})."
            import re as _re
            m = _re.search(r"\bt_[0-9a-f]{6,}\b", out)
            return f"Filed for {who}: {title[:80]}" + (f" ({m.group(0)})" if m else "") + "."
        if action == "comment":
            if not tid or not text:
                return "comment needs id= and text=. Nothing changed."
            rc, out = _kanban_cli(["comment", tid, text, "--author", _board_author(ctx)])
            return f"Commented on {tid}." if rc == 0 else f"Comment did not land ({out[:120]})."
        if action == "unblock":
            if not tid:
                return "unblock needs id=. Nothing changed."
            args_ = ["unblock", tid] + (["--reason", text] if text else [])
            rc, out = _kanban_cli(args_)
            if rc != 0:
                return f"Unblock did not land ({out[:120]})."
            _list_clear_card(tid, note=text or "unblocked")
            return f"Unblocked {tid}."
        if action == "complete":
            if not tid:
                return "complete needs id=. Nothing changed."
            args_ = ["complete", tid] + (["--result", text] if text else [])
            rc, out = _kanban_cli(args_)
            if rc != 0:
                return f"Complete did not land ({out[:120]})."
            _list_clear_card(tid, note=text or "completed")
            return f"Completed {tid}."
        if action == "answer":
            # Land his answer on the card: comment + unblock + clear Crissa's list.
            if not tid or not text:
                return "answer needs id= and text= (his words). Nothing changed."
            if not _board_may_rule(ctx):
                # his words land only through Crissa or the User's own profile (the User 2026-10-11)
                return ("answer lands the User's words and is Crissa's alone. Nothing changed. "
                        "Use action=comment to post your own note on the card.")
            rc, out = _kanban_cli(["comment", tid, text, "--author", _board_author(ctx)])
            if rc != 0:
                return f"Comment did not land ({out[:120]}). List still open."
            if _done_closes_on() and _is_done_ruling(text):

                return _answer_done(tid, text, _kanban_cli, _list_clear_card)
            if _answer_holds(text, _card_body(tid)):
                # Park, or any ruling on a green gate: the words are the ruling,
                # kanban-loop lands it. An unblock here respawns the work.
                n = _list_clear_card(tid, note=text)
                d = _later_date(text)
                if d:

                    # unblock followed by a `dependency` block: between the two the card was live, and
                    # hermes re-kinds, specifies and decomposes a block it thinks is recurring — 3 of 38
                    # dated parks held, and a deferred proposal was built the same morning. `scheduled`
                    # is not dispatchable and `unblock` is its only exit; kanban-loop run_later reads the
                    # date from this ruling and returns the card to the Decide queue that day.
                    # Kill: KREW_CRISSA_LATER_SCHEDULES=0 (the old two-step).
                    if os.environ.get("KREW_CRISSA_LATER_SCHEDULES", "1").strip().lower() in ("0", "false", "no", "off"):
                        rc_u, out_u = _kanban_cli(["unblock", tid, "--reason", f"later {d}: re-blocking as a dated park"])
                        rc_b, out_b = _kanban_cli(["block", tid, f"later {d}: kanban-loop puts it back on the Decide queue then",
                                                   "--kind", "dependency"])
                        where = "unblocked (a parent is still open)" if rc_u == 0 else "still blocked"
                    else:
                        rc_b, out_b = _kanban_cli(["schedule", tid,
                                                   f"later {d}: parked by Crissa's ruling; kanban-loop puts it back on the Decide queue then"])
                        where = "where it was"
                    if rc_b != 0:
                        _kanban_cli(["comment", tid, f"later {d}: dated park did not land ({out_b[:80]}); card is {where}",
                                     "--author", _board_author(ctx)])
                        return (f"Landed on {tid}: later {d} noted, but the dated park did not land "
                                f"({out_b[:80]}); the card is {where}. Cleared Crissa's list.")
                    return (f"Landed on {tid}: parked until {d} (scheduled; kanban-loop returns it to the "
                            "Decide queue that day) and cleared Crissa's list." + ("" if n else " (no list row)"))
                return (f"Landed on {tid}: ruling held for kanban-loop (no unblock — the card stays "
                        "off the dispatcher) and cleared Crissa's list." + ("" if n else " (no list row)"))
            rc2, out2 = _kanban_cli(["unblock", tid, "--reason", text])
            if rc2 != 0:
                return f"Commented on {tid} but unblock did not land ({out2[:120]}). List still open."
            n = _list_clear_card(tid, note=text)
            return f"Landed on {tid} and cleared Crissa's list." + ("" if n else " (no list row)")
        return "Nothing changed."

    def team_ear_health(*args, **kwargs):
        """Her ear's health, asked of the Wall's player (GET /v1/ear); krew never calls the voice module itself."""
        code, out = _player_get("/v1/ear", timeout=4.0)
        if code == 0:
            return f"Crissa not installed (no player at {CRISSA_PLAYER}) — no ear to check"
        if code == 404:
            return f"this Crissa is older than /v1/ear (player at {CRISSA_PLAYER}) — update Crissa to read her ear"
        if not out.get("ok"):
            return f"her ear is not answering: {out.get('why') or out.get('error') or code}"
        h = out.get("stt") or {}
        return (f"ear ok={h.get('ok')} engine={h.get('engine')} parakeet={h.get('parakeet')} "
                f"voice_bank={list((h.get('voice_bank') or {}).keys()) or 'empty'}")

    def team_display(action: str = "status", url: str = "", name: str = "", *args, **kwargs):
        """The Wall through its player (2026-10-06). The Wall is Crissa's own page; krew puts one card beside her
        (POST /v1/screen): a named screen or a web address in a frame, a local page whole (no scripts), a note, art.
        Home takes the card down. Every answer is read back off GET /v1/screen; no player: Crissa not installed."""
        p: dict = {}
        if isinstance(action, dict):
            p.update(action)
            action = ""
        if args and isinstance(args[0], dict):
            p.update(args[0])
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        action = str(p.get("action") or p.get("op") or action or "status").strip().lower()
        url = str(p.get("url") or p.get("href") or url or "").strip()
        name = str(p.get("name") or p.get("view") or name or "").strip()
        text = str(p.get("text") or "").strip()
        html = str(p.get("html") or "").strip()
        art = str(p.get("art") or "").strip("\n") if p.get("art") else ""
        title = str(p.get("title") or "").strip()
        target = str(p.get("target") or "").strip()
        moment = str(p.get("moment") or "").strip().lower()
        key = (name or (target if action in ("tab", "newtab") else "")).strip().lower()
        names = ", ".join(sorted(set(WALL_VIEWS) | set(_LIVING_VIEWS) | set(_HOME_WORDS)))
        if action in ("home", "scene", "face") or (key in _HOME_WORDS and not url and action in (
                "view", "show", "open", "navigate", "stage", "wall", "tab", "newtab")):
            ok, card, why = _screen({"clear": "all"})
            return ("Home: " + _card_says(card)) if ok else why
        if action in ("view", "show", "open", "navigate", "stage", "wall", "tab", "newtab",
                      "panel", "control", "hq", "control-panel", "control_panel"):
            dest = ""
            if url.startswith(("http://", "https://", "file://")):
                dest = url
            elif url.startswith(("~", "/")):
                dest = "file://" + os.path.expanduser(url)
            elif key in WALL_VIEWS:
                dest = WALL_VIEWS[key]
            elif key in _LIVING_VIEWS:
                dest = _living_html(*_LIVING_VIEWS[key])
                if not dest:
                    return f"No living HTML for {key!r} in krew-deliverables. Nothing changed on the Wall."
            elif action in ("panel", "control", "hq", "control-panel", "control_panel"):
                dest = WALL_VIEWS["panel"]
            if not dest:
                if key or url:
                    return (f"I don't know a screen called {(key or url)!r}. Names: {names} — or url= a full "
                            "http:// address. Nothing changed on the Wall.")
                return f"Which screen? name= one of: {names} — or url= a full http:// address. Nothing changed on the Wall."
            return _show_beside_her(dest=dest, title=title or key or dest, text=text or title or key)
        if action in ("chalk", "board", "write", "notes", "note", "summary", "resting", "rest"):
            resting = action in ("resting", "rest")
            if not (text or html or art or (moment and resting)):
                return ('chalk needs text= — e.g. '
                        '{"action":"chalk","title":"Backup","text":"12 files · 3.1 GB · done"}')
            body: dict = {}
            line = text or html or ("" if art else moment)
            if line:
                body["notes"] = line
                body["notes_title"] = title or ("Resting" if resting else "Notes")
            if art:
                body["art"] = art
                body["art_label"] = title or moment
            ok, card, why = _screen(body)
            if not ok:
                return f"NOTHING CHANGED on the Wall ({why}). Say it out loud instead."
            if (line and card.get("notes") != line) or (art and card.get("art") != art):
                return "the player took it, but the Wall does not show it — " + _card_says(card) + " Do not say it is up."
            return _card_says(card) + " Tell them it is up beside her."
        if action in ("clear", "close"):
            t = (target or name or "").strip().lower()
            fields = {"notes": ["notes", "notes_title"], "note": ["notes", "notes_title"],
                      "doc": ["doc", "doc_title"], "sheet": ["doc", "doc_title"], "art": ["art", "art_label"],
                      "words": ["notes", "notes_title", "doc", "doc_title"]}
            if t in ("", "all", "screen", "page", "stage", "card"):
                body = {"clear": "all"}
            elif action == "clear" and t in fields:
                body = {"clear": fields[t]}
            elif action == "close":
                return ("On Crissa there are no tabs to close: the Wall shows one card beside her, and close with no "
                        "target takes it down. Nothing changed on the Wall.")
            else:
                return f"clear target= all, notes, doc, art or words — {t!r} is not one. Nothing changed on the Wall."
            ok, card, why = _screen(body)
            return ("Cleared. " + _card_says(card)) if ok else why
        if action in ("status", "tabs", "mode"):
            code, out = _player_get("/v1/screen")
            if code == 0:
                return f"Crissa not installed (no player at {CRISSA_PLAYER}) — there is no Wall to read"
            if code == 404:
                return _old_wall()
            return _card_says(out.get("screen") or {})
        return (f"{action!r} is not something the Wall does on Crissa: the Wall is the player's own page and krew "
                "puts one card beside her through the player. Use view (name= or url=), home, chalk, clear or "
                f"status. Names: {names}. Nothing changed on the Wall.")

    def team_time(*args, **kwargs):
        """Wall-clock. Only when asked or the reply needs minutes. Not a greeting."""
        import datetime as _dt

        now = _dt.datetime.now().astimezone()
        return now.strftime("%-I:%M %p on %A, %B %-d, %Y (%Z)")

    def team_express(*args, **kwargs):
        """Words on the room screen: a note on the Wall's card through the player (team_display chalk); kind scene
        or clear takes the card down (team_display home)."""
        payload = {}
        if args and isinstance(args[0], dict):
            payload = dict(args[0])
        payload.update({k: v for k, v in kwargs.items() if k != "task_id"})
        kind = payload.get("kind")
        text = str(payload.get("text") or payload.get("title") or "").strip()
        if kind in ("scene", "clear"):
            return team_display({"action": "home"})
        if text:
            return team_display({"action": "chalk", "text": text, "title": str(payload.get("title") or "Note")})
        return ("Nothing to put up: give text= for words on the Wall, or use team_display view name= for a page. "
                "Nothing changed on the Wall.")

    _BRIEF_CARDS = ("weather", "headline", "markets", "on_this_day", "house",
                    "calendar", "commute", "word_of_the_day")

    def team_brief_edit(*args, **kwargs):
        """Edit someone's morning brief durably (add/remove/move/set/show)."""
        p = {}
        if args and isinstance(args[0], dict):
            p = dict(args[0])
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        person = str(p.get("person") or _PERSON).strip().lower()
        action = str(p.get("action") or "show").strip().lower()
        path = Path(os.path.expanduser(os.environ.get("KREW_PEOPLE_DIR") or str(_krew_home_dir() / "people"))) / person / "brief.json"
        try:
            brief = json.loads(path.read_text()) if path.exists() else {}
        except Exception:
            brief = {}
        brief.setdefault("wake_hour", 7)
        brief.setdefault("greeting", f"Good morning, *{person.title()}*.")
        cards = brief.setdefault("cards", ["weather", "headline", "house"])
        card = str(p.get("card") or "").strip().lower().replace(" ", "_")
        try:
            if action == "add" and card:
                if card not in _BRIEF_CARDS:
                    return f"unknown card {card}; menu: {', '.join(_BRIEF_CARDS)}"
                if card not in cards:
                    cards.append(card)
            elif action == "remove" and card:
                cards[:] = [c for c in cards if c != card]
            elif action == "move" and card in cards:
                cards.remove(card)
                cards.insert(max(0, int(p.get("position", 1)) - 1), card)
            elif action == "set":
                if p.get("greeting"):
                    brief["greeting"] = str(p["greeting"])[:120]
                if p.get("wake_hour") is not None:
                    brief["wake_hour"] = max(4, min(11, int(p["wake_hour"])))
            elif action != "show":
                return "action must be add|remove|move|set|show"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(brief, indent=1))
            return (f"{person}'s brief: {', '.join(cards)} · wake {brief['wake_hour']}"
                    f" · greeting \"{brief['greeting']}\"")
        except Exception as e:
            return f"brief edit failed ({e.__class__.__name__})"

    ctx.register_tool(
        "team_brief_edit",
        toolset="team",
        description=(
            "Edit a morning brief. action add|remove|move|set|show; "
            "card weather|headline|markets|on_this_day|house|calendar|"
            "commute|word_of_the_day."
        ),
        schema={
            "type": "object",
            "properties": {
                "person": {"type": "string"},
                "action": {"type": "string"},
                "card": {"type": "string"},
                "position": {"type": "integer"},
                "greeting": {"type": "string"},
                "wake_hour": {"type": "integer"},
            },
            "required": ["action"],
        },
        handler=team_brief_edit,
    )

    def team_morning_brief(*args, **kwargs):
        """Run the Morning Hello (coffee scene + spoken brief) for a person."""
        p = args[0] if args and isinstance(args[0], dict) else {}
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        person = str(p.get("person") or _PERSON).strip().lower()
        try:
            subprocess.Popen(
                ["python3",
                 str(_CREW / "scripts/morning-hello.py"),
                 "--person", person])
            return f"morning brief starting for {person}"
        except Exception as e:
            return f"brief unavailable ({e.__class__.__name__})"

    ctx.register_tool(
        "team_morning_brief",
        toolset="team",
        description="Play Morning Hello (coffee scene + spoken brief) for a person.",
        schema={"type": "object",
                "properties": {"person": {"type": "string"}}},
        handler=team_morning_brief,
    )
    ctx.register_tool(
        "team_express",
        toolset="team",
        description=(
            "Room screen: kind card|choices|art|state|scene|clear. "
            "Optional title, body, options, mood, float."
        ),
        schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "text": {"type": "string"},
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "kv": {"type": "object"},
                "label": {"type": "string"},
                "value": {"type": "string"},
                "mood": {"type": "string"},
                "float": {"type": "string"},
                "verb": {"type": "string"},
                "target_id": {"type": "string"},
                "pin": {"type": "boolean"},
            },
            "required": ["kind"],
        },
        handler=team_express,
    )

    def team_compose(*args, **kwargs):
        """Insert a clip into the running Conductor at the next seam."""
        p = {}
        if args and isinstance(args[0], dict):
            p = dict(args[0])
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        key = str(p.get("key") or p.get("clip") or "").strip()
        who = str(p.get("by") or getattr(ctx, "profile_name", None) or "crissa")
        return compose_pose(key, p.get("ttl", 12.0),
                            affect=str(p.get("affect") or ""), by=who)

    ctx.register_tool(
        "team_compose",
        toolset="team",
        description=(
            "Insert a body clip at the next seam. Pass affect= the whole "
            "## crissaaffect block (Voice+Body, under 600). Never treat a "
            "refusal as done."
        ),
        schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "clip": {"type": "string"},
                "ttl": {"type": "number"},
                "affect": {"type": "string"},
            },
            "required": ["key"],
        },
        handler=team_compose,
    )
    def team_body(*args, **kwargs):
        """Where her body is on the wall, from the Conductor (:8100/v1/state):
        the hub, the clip, why, on a call or not, on the air or not, and the

        log so she can speak about where she is sitting."""
        try:
            with urllib.request.urlopen(CONDUCTOR.rstrip("/") + "/v1/state", timeout=3) as r:
                d = json.load(r)
        except Exception as e:
            return "Crissa not installed (no player at %s: %s) — I can't see the wall right now" % (CRISSA_PLAYER, type(e).__name__)
        hub = d.get("hub") or "nowhere"
        now = d.get("now") or "nothing"
        why = d.get("why") or ""
        comms = bool(d.get("comms"))
        air = bool(d.get("on_air"))
        br = d.get("brain") or {}
        recent = ", ".join(br.get("recent") or [])[:200]
        offer = ", ".join(d.get("comms_offer") or [])
        line = "sitting on %s, playing %s (%s)" % (hub, now, why)
        line += "; on a call" + (", can move to: %s" % offer if offer else "") if comms else "; not on a call"
        line += "; speaking" if air else "; quiet"
        if recent:
            line += "; last moves: " + recent
        return line

    ctx.register_tool(
        "team_body",
        toolset="team",
        description=(
            "Where your body is on the wall right now: hub, clip, on a call or not, "
            "speaking or quiet, your last moves. Read it before talking about where you are sitting."
        ),
        schema={"type": "object", "properties": {}},
        handler=team_body,
    )
    ctx.register_tool(
        "team_time",
        toolset="team",
        description="Current local time and date. System prompt has the date only.",
        schema={"type": "object", "properties": {}},
        handler=team_time,
    )
    ctx.register_tool(
        "team_speak",
        toolset="team",
        description="Speak in-room as this profile, through the Wall's player.",
        schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "voice": {"type": "string"},
            },
            "required": ["text"],
        },
        handler=team_speak,
    )
    if str(profile).strip().lower() != "crissa":

        def team_page(text: str = "", *args, **kwargs):
            ""
            text, _v = _coerce_tool_args(text, "", *args, **kwargs)
            text = (text or "").strip()
            if not text:
                return "team_page: empty text"
            script = _CREW / "scripts/crissa-page.py"
            if not script.is_file():
                return "crissa-page.py missing"
            try:
                r = subprocess.run(
                    ["python3", str(script), text],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                return out[:500] or f"team_page rc={r.returncode}"
            except Exception as e:
                return f"team_page {type(e).__name__}: {e}"

        ctx.register_tool(
            "team_page",
            toolset="team",
            description=(
                "Send Crissa a message on her gateway, the same door the user uses. "
                "One or two sentences. Not a kanban worker spawn."
            ),
            schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=team_page,
        )
    if str(profile).strip().lower() == "crissa":

        def team_scout(*args, **kwargs):
            """One-off Scout look-up. Not a kanban spawn. Not delegate_task."""
            p = args[0] if args and isinstance(args[0], dict) else {}
            p.update({k: v for k, v in kwargs.items() if k not in _RUNTIME_KW})
            for k in _RUNTIME_KW:
                p.pop(k, None)
            ask = _plain_ask(p)
            if not ask:
                return "team_scout: empty ask. Pass the question in plain words — ask, look, or text."
            script = _CREW / "scripts/scout-one-off.py"
            if not script.is_file():
                return "scout-one-off.py missing"
            try:
                r = subprocess.run(
                    ["python3", str(script), ask],
                    capture_output=True,
                    text=True,
                    timeout=200,
                )
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                return (out if out else f"team_scout rc={r.returncode}")
            except subprocess.TimeoutExpired:
                return (
                    "Scout is still looking. The stub note is under "
                    "krew-deliverables/scratch — put it on the wall when it lands."
                )
            except Exception as e:
                return f"team_scout {type(e).__name__}: {e}"

        ctx.register_tool(
            "team_scout",
            toolset="team",
            description=(
                "One-off Scout research note (files, the web, past sessions) to "
                "krew-deliverables/scratch. Not a kanban card. Scout has no shell and no board: "
                "a live probe of a service or a card's thread is not Scout's job."
            ),
            schema={
                "type": "object",
                "properties": {
                    "ask": {"type": "string",
                            "description": "the question, in plain words"},
                },
                "required": ["ask"],
            },
            handler=team_scout,
        )
    ctx.register_tool(
        "team_announce",
        toolset="team",
        description="Kanban/krew PA via Audio8. Completions, stuck, protocol, staged — title only.",
        schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=team_announce,
    )
    ctx.register_tool(
        "team_presence",
        toolset="team",
        description="Read-only room summary from presence SoT.",
        schema={"type": "object", "properties": {}},
        handler=team_presence,
    )
    ctx.register_tool(
        "team_observe_presence",
        toolset="team",
        description=(
            "Door/presence package. Optional action=add|sub|null apply=true "
            "→ vl_delta. Voice is soft ID, never headcount."
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "add|sub|null"},
                "note": {"type": "string", "description": "reason"},
                "apply": {"type": "boolean", "description": "POST /v1/vl_delta"},
            },
        },
        handler=team_observe_presence,
    )
    ctx.register_tool(
        "team_look",
        toolset="team",
        description=(
            "Room camera. what=hear|room_check. A glance does not drop AUTH. "
            "Skill crissa-look."
        ),
        schema={
            "type": "object",
            "properties": {
                "what": {"type": "string", "description": "hear|room_check"},
                "peek": {"type": "boolean", "description": "glance (default true)"},
            },
        },
        handler=team_look,
    )
    def team_needs_sheet(action: str = "status", *args, **kwargs):
        """The Decide-with-User queue on the 8.5x11 document sheet."""
        if isinstance(action, dict):
            action = str(action.get("action") or "status")
        act = str(action or "status").strip().lower().replace("-", "_")
        if act not in ("show", "show_consented", "clear", "status"):
            return ("action must be show, show_consented, clear, or status. "
                    "show puts his questions on the sheet, clear takes it down.")
        code, _glass = _player_get("/v1/screen", timeout=4.0)
        if code == 0:
            return f"Crissa not installed (no player at {CRISSA_PLAYER}) — the sheet has no Wall to go on"
        if code == 404:
            return _old_wall()
        script = Path(__file__).resolve().parent / "crissa-decide-list.py"
        if not script.is_file():
            return f"the sheet tool is missing at {script} — nothing on the glass"
        try:
            r = subprocess.run([sys.executable, str(script), act],
                               capture_output=True, text=True, timeout=120)
        except Exception as e:
            return f"could not run the sheet tool ({type(e).__name__}) — assume nothing is up"
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        # The script already reports the GLASS, not the POST. Pass it through
        # verbatim: a cheerful string here becomes Crissa telling him it is up
        # when it is not.
        return out or f"sheet tool said nothing (exit {r.returncode})"

    ctx.register_tool(
        "team_needs_sheet",
        toolset="team",
        description=(
            "Decide-with-User 8.5x11 sheet. action show|show_consented|"
            "clear|status. Skill crissa-needs."
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "show|show_consented|clear|status"},
            },
        },
        handler=team_needs_sheet,
    )
    ctx.register_tool(
        "team_board",
        toolset="team",
        description=(
            "The board in one tool: list, show id=, create title=, comment, unblock, complete, "
            "answer id= text= (lands his words, unblocks, clears Crissa's list). Skills crissa-needs, crissa-big-push."
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_BOARD_ACTIONS)},
                "id": {"type": "string", "description": "t_… for show/comment/unblock/complete/answer"},
                "title": {"type": "string", "description": "create: the card"},
                "text": {"type": "string", "description": "create body · comment · answer · unblock reason · complete result"},
                "assignee": {"type": "string", "description": "create/list filter; default ada"},
                "status": {"type": "string", "description": "list filter: blocked|todo|running|review|done|triage"},
                "triage": {"type": "boolean", "description": "create: park in triage (default true)"},
            },
            "required": ["action"],
        },
        handler=team_board,
    )
    ctx.register_tool(
        "team_list",
        toolset="team",
        description=(
            "Crissa's list on CP. list; show id=; ask title= text= (waits on him yes/no); "
            "task title= steps= (work with steps); note id= text=; done id= (tasks only). Skill crissa-list."
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_LIST_ACTIONS)},
                "id": {"type": "string", "description": "show/note/done"},
                "title": {"type": "string", "description": "ask/task headline"},
                "text": {"type": "string", "description": "ask body · note · done result"},
                "steps": {"type": "string", "description": "task: one step per line"},
            },
            "required": ["action"],
        },
        handler=team_list,
    )
    ctx.register_tool(
        "team_ear_health",
        toolset="team",
        description="Her ear's health, read through the Wall's player.",
        schema={"type": "object", "properties": {}},
        handler=team_ear_health,
    )
    ctx.register_tool(
        "show_me",
        toolset="team",
        description=(
            "show me / put up / display / report → on YOUR glass this turn. "
            "what= a screen name, 'report' (+text= markdown), 'note', a URL or file, 'home'. Skills crissa-her-wall, crissa-report."
        ),
        schema={
            "type": "object",
            "properties": {
                "what": {"type": "string",
                         "description": "kanban|panel|approvals|composer|dashboard|sessions|logs|presence|enroll|home | report | note | clear | tabs | shot | http(s) URL | /file"},
                "text": {"type": "string", "description": "report: markdown (# title, lede:, kpi:, ## sections, table). note: ≤240 chars"},
                "title": {"type": "string", "description": "report/note title"},
                "hold": {"type": "string", "description": "note: seconds before it fades"},
            },
            "required": ["what"],
        },
        handler=show_me,
    )
    ctx.register_tool(
        "team_outbox",
        toolset="team",
        description=(
            "Post to X / our Space / Google Ads = a draft he posts: kind=x text=, or kind=google_ads title= "
            "headlines= descriptions= keywords=. Never posts."
        ),
        schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["x", "google_ads"]},
                "text": {"type": "string", "description": "x: the post, <=280 (a --- line splits a thread)"},
                "title": {"type": "string", "description": "x: a short name; google_ads: the campaign"},
                "headlines": {"type": "array", "items": {"type": "string"}, "description": "3-15, <=30 chars each"},
                "descriptions": {"type": "array", "items": {"type": "string"}, "description": "2-4, <=90 chars each"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "url": {"type": "string", "description": "google_ads final URL (default https://crissa.ai)"},
            },
            "required": ["kind"],
        },
        handler=team_outbox,
    )
    ctx.register_tool(
        "team_config_audit",
        toolset="team",
        description="Audit this profile's granted toolsets and gaps.",
        schema={"type": "object", "properties": {}},
        handler=team_config_audit,
    )

    def team_register_face(name: str = "", *args, **kwargs):
        if isinstance(name, dict):
            name = name.get("name") or ""
        return _start_registration(str(kwargs.get("name") or name or ""), "face")

    def team_register_voice(name: str = "", *args, **kwargs):
        if isinstance(name, dict):
            name = name.get("name") or ""
        return _start_registration(str(kwargs.get("name") or name or ""), "voice")

    ctx.register_tool(
        "team_register_face",
        toolset="team",
        description="Register/retake a face on the registration panel (through the Wall's player). name= who.",
        schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=team_register_face,
    )
    ctx.register_tool(
        "team_register_voice",
        toolset="team",
        description="Register/retake a voice (2 takes) on the registration panel (through the Wall's player). name= who.",
        schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=team_register_voice,
    )

    _PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    _PHOTO_DIRS = (Path.home() / "Downloads", Path.home() / "Pictures")
    _ASCII_TOOL = Path(__file__).resolve().parent / "photo-ascii.py"

    # and crops to them. Plain python3 still renders — just the whole frame.
    _ASCII_PY = os.environ.get("KREW_ASCII_PYTHON") or sys.executable

    def _newest_photo() -> Path | None:
        best, best_mt = None, -1.0
        for d in _PHOTO_DIRS:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in _PHOTO_EXT:
                    try:
                        mt = f.stat().st_mtime
                    except OSError:
                        continue
                    if mt > best_mt:
                        best, best_mt = f, mt
        return best

    def _resolve_photo(name: str) -> Path | None:
        """'', 'latest', a bare filename, or a full path — all land somewhere."""
        s = (name or "").strip().strip('"').strip("'")
        if not s or s.lower() in ("latest", "last", "newest", "the last one",
                                  "that one", "it"):
            return _newest_photo()
        p = Path(s).expanduser()
        if p.is_file():
            return p
        for d in _PHOTO_DIRS:  # she will be handed a bare filename by voice
            c = d / p.name
            if c.is_file():
                return c
        return None

    def team_wall_photo(*args, **kwargs):
        """Render a photo as ASCII art and put it on the room screen."""
        p = {}
        if args and isinstance(args[0], dict):
            p = dict(args[0])
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})

        img = _resolve_photo(str(p.get("photo") or p.get("path") or p.get("file") or ""))
        if img is None:
            return ("No photo found — put one in ~/Downloads, or give me the "
                    "filename. The wall did not change.")

        ramp = str(p.get("ramp") or "quadrant").strip().lower()
        if ramp not in ("quadrant", "shade", "ascii", "eighth", "braille", "cjk"):
            ramp = "quadrant"
        try:
            cols = max(24, min(110, int(p.get("cols") or 72)))
        except (TypeError, ValueError):
            cols = 72
        label = str(p.get("label") or "").strip()[:48]
        face_off = str(p.get("face") or "").strip().lower() in ("off", "no", "false", "whole")

        cmd = [
            _ASCII_PY if Path(_ASCII_PY).exists() else "python3",
            str(_ASCII_TOOL), str(img),
            "--ramp", ramp, "--cols", str(cols), "--post", "--json",
        ]
        if face_off:
            cmd += ["--face", "off"]
        if label:
            cmd += ["--label", label]
        if p.get("hold") is not None:
            cmd += ["--hold", str(p.get("hold"))]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except Exception as e:
            return f"Could not render it ({e.__class__.__name__}) — the wall did not change."
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip().splitlines()
            return f"Could not render it: {err[-1][:160] if err else 'unknown error'}"
        try:
            out = json.loads((r.stdout or "").strip().splitlines()[-1])
        except Exception:
            return "Rendered, but I could not read the result — check the wall yourself."

        # Report the GLASS. `glass` is read back off /v1/screen by the tool, so
        # a cheerful string here can never outrun what is actually up there.
        glass = out.get("glass") or ""
        if not glass or "did NOT land" in glass or "DIFFERENT piece" in glass:
            return f"It did not land on the wall ({glass or 'no confirmation'})."
        faces = out.get("faces") or 0
        who = (f"{faces} face{'s' if faces != 1 else ''} found, cropped to them"
               if faces and not face_off else "whole frame")
        return (f"{img.name} is on the wall — {who}, {out.get('lines')} lines "
                f"in {ramp}" + (f", labelled '{label}'" if label else "") + ".")

    ctx.register_tool(
        "team_wall_photo",
        toolset="team",
        description=(
            "ASCII a photo onto the wall. photo= path or latest in Downloads. "
            "Reports the glass; if it did not land, say so."
        ),
        schema={
            "type": "object",
            "properties": {
                "photo": {"type": "string"},
                "label": {"type": "string"},
                "ramp": {"type": "string"},
                "cols": {"type": "integer"},
                "face": {"type": "string"},
                "hold": {"type": "number"},
            },
        },
        handler=team_wall_photo,
    )

    # ---- CPU renderers -------------------------------------------------
    # These two draw with the processor, not the card, so unlike krew-comfy
    # they work in every occupancy — Render, Standard, even All-OS-Off. They
    # print ASCII to stdout and post nothing themselves, so the posting and
    # the read-back live here.
    _GLYPH_TOOL = Path(__file__).resolve().parent / "glyph_density.py"
    _BANNER_TOOL = Path(__file__).resolve().parent / "render_banner.py"

    def _art_to_glass(art: str, label: str, hold=None) -> tuple[bool, str]:
        """The piece on the Wall's card through the player, then read back: report the glass, never the post."""
        ok, card, why = _screen({"art": art, "art_label": label or ""})
        if not ok:
            return False, why + " — piece did NOT land"
        live = card.get("art") or ""
        if not live:
            return False, "the player took it, but the Wall shows no art — piece did NOT land"
        if live != art:
            return False, "the player took it, but the Wall is showing a DIFFERENT piece"
        return True, f"on the Wall: {len(art.splitlines())} lines, label {card.get('art_label')!r}"

    def _run_renderer(cmd: list, timeout: float = 60.0):
        """Run a CPU renderer with the interpreter this agent is already using.

        sys.executable is the Hermes venv, which is where pyfiglet actually
        lives — system python3 has it nowhere, so render_banner.py exits 2
        there. Never guess the interpreter.
        """
        try:
            r = subprocess.run([sys.executable] + [str(c) for c in cmd],
                               capture_output=True, text=True, timeout=timeout)
        except Exception as e:
            return None, f"could not run it ({e.__class__.__name__})"
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip().splitlines()
            return None, (err[-1][:200] if err else f"exit {r.returncode}")
        art = (r.stdout or "").rstrip("\n")
        if not art.strip():
            return None, "the renderer drew nothing"
        return art, ""

    def team_glyph_art(*args, **kwargs):
        """Procedural glyph-density art, CPU only."""
        p = {}
        if args and isinstance(args[0], dict):
            p = dict(args[0])
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})

        subject = str(p.get("subject") or "spiral").strip()[:60]
        try:
            width = max(20, min(110, int(p.get("width") or 60)))
        except (TypeError, ValueError):
            width = 60
        try:
            height = max(8, min(60, int(p.get("height") or 30)))
        except (TypeError, ValueError):
            height = 30
        palette = str(p.get("palette") or "full").strip().lower()
        if palette not in ("full", "dense", "light", "minimal"):
            palette = "full"

        cmd = [_GLYPH_TOOL, subject, "--width", width, "--height", height,
               "--palette", palette]
        if p.get("seed") is not None:
            cmd += ["--seed", int(p["seed"])]
        if p.get("invert"):
            cmd += ["--invert"]

        art, err = _run_renderer(cmd)
        if art is None:
            return f"Could not draw it: {err}"
        if not p.get("post", True):
            return art

        label = str(p.get("label") or subject).strip()[:48]
        ok, glass = _art_to_glass(art, label, p.get("hold"))
        if not ok:
            return f"Drew it, but it did not land on the wall ({glass})."
        return f"{subject} is on the wall — {glass}."

    def team_banner(*args, **kwargs):
        """A short word as figlet art, sized for the herbox."""
        p = {}
        if args and isinstance(args[0], dict):
            p = dict(args[0])
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})

        text = str(p.get("text") or "").strip()[:24]
        if not text:
            return "Give me a word to set — text= is required."
        try:
            width = max(12, min(60, int(p.get("width") or 34)))
        except (TypeError, ValueError):
            width = 34

        if p.get("list"):
            art, err = _run_renderer([_BANNER_TOOL, "--list"])
            return art if art is not None else f"Could not list fonts: {err}"

        cmd = [_BANNER_TOOL, text, "--width", width]
        if p.get("category"):
            cmd += ["--category", str(p["category"]).strip().lower()]
        elif p.get("font"):
            cmd += ["--font", str(p["font"]).strip().lower()]

        art, err = _run_renderer(cmd)
        if art is None:
            return f"Could not set it: {err}"
        widest = max((len(l) for l in art.splitlines()), default=0)
        if not p.get("post", True):
            return art

        ok, glass = _art_to_glass(art, str(p.get("label") or "").strip()[:48],
                                  p.get("hold"))
        warn = ("  (wider than the herbox 36 — trim it or pick a quiet font)"
                if widest > 36 else "")
        if not ok:
            return f"Set it, but it did not land on the wall ({glass}).{warn}"
        return f"'{text}' is on the wall — {glass}.{warn}"

    ctx.register_tool(
        "team_glyph_art",
        toolset="team",
        description=(
            "CPU glyph art onto the wall. subject= shape or mood. Not for "
            "photos (team_wall_photo) or words (team_banner)."
        ),
        schema={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "palette": {"type": "string",
                            "enum": ["full", "dense", "light", "minimal"]},
                "seed": {"type": "integer"},
                "invert": {"type": "boolean"},
                "label": {"type": "string"},
                "post": {"type": "boolean"},
                "hold": {"type": "number"},
            },
        },
        handler=team_glyph_art,
    )

    ctx.register_tool(
        "team_banner",
        toolset="team",
        description=(
            "CPU figlet word onto the wall. text= a short word. category= "
            "quiet|slant|script|block|cyber|play|display."
        ),
        schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "category": {"type": "string",
                             "enum": ["quiet", "slant", "script", "block",
                                      "cyber", "play", "display"]},
                "font": {"type": "string"},
                "width": {"type": "integer"},
                "label": {"type": "string"},
                "list": {"type": "boolean"},
                "post": {"type": "boolean"},
                "hold": {"type": "number"},
            },
        },
        handler=team_banner,
    )

    ctx.register_tool(
        "team_display",
        toolset="team",
        description=(
            "Same as show_me, old shape: action view|home|chalk|clear|status|resting "
            "+ name= / url= / text= / title= / target=. Skill crissa-her-wall. Never invent a URL."
        ),
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "view|home|chalk|clear|status|resting"},
                "name": {"type": "string",
                         "description": "kanban|panel|composer|dashboard|sessions|logs|presence|enroll|home"},
                "url": {"type": "string", "description": "http(s) only if handed"},
                "text": {"type": "string", "description": "chalk prose or report markdown"},
                "title": {"type": "string", "description": "caption"},
                "target": {"type": "string", "description": "clear: all|notes|doc|art|words"},
                "art": {"type": "string", "description": "monospace art for resting"},
                "moment": {"type": "string", "description": "resting preset, e.g. coffee"},
            },
            "required": ["action"],
        },
        handler=team_display,
    )

    def _card_title(tid: str, payload: dict) -> str:
        raw = ""
        for key in ("title", "task_title", "name"):
            v = payload.get(key)
            if isinstance(v, str) and v.strip() and v.strip().lower() != "none":
                raw = v.strip()
                break
        if not raw and tid:
            try:
                r = subprocess.run(
                    ["hermes", "kanban", "show", tid],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                out = r.stdout or ""
                m = re.search(r"^Task\s+\S+:\s+(.+)$", out, re.M)
                if m:
                    raw = m.group(1).strip()
            except Exception:
                raw = ""
        return (raw or "").strip()

    def _announce_card(kind: str, prefix: str, *args, **kwargs):
        if os.environ.get("KREW_ANNOUNCE_OWNER", "plugin").strip().lower() == "office":
            return
        payload = args[0] if args and isinstance(args[0], dict) else kwargs
        tid = str(
            payload.get("task_id")
            or payload.get("id")
            or os.environ.get("HERMES_KANBAN_TASK")
            or ""
        )
        if tid and _dedup_skip(f"{kind}:" + tid):
            return
        title = _card_title(tid, payload)
        if not title:
            return
        summary = ""
        for key in ("summary", "result", "message"):
            v = payload.get(key)
            if isinstance(v, str) and v.strip() and v.strip().lower() != "none":
                summary = v.strip()
                break
        # the Wall's player queues it (/v1/announce): krew never reaches into the Wall's files (2026-10-06)
        try:
            return team_announce(
                f"{prefix} {title}. {summary}".strip(),
                category=kind,
                dedup_key=(f"{kind}:" + tid) if tid else "",
            )
        except Exception:
            return

    def _on_complete(*args, **kwargs):
        if not _ANNOUNCE_COMPLETIONS:
            return "announce skipped — completions off"
        return _announce_card("completion", "Done.", *args, **kwargs)

    def _on_block(*args, **kwargs):
        payload = args[0] if args and isinstance(args[0], dict) else kwargs
        tid = str(payload.get("task_id") or payload.get("id") or "")
        reason = str(payload.get("reason") or payload.get("kind") or "")
        title = str(payload.get("title") or "")
        assignee = str(payload.get("assignee") or "")
        kind = str(payload.get("block_kind") or payload.get("kind") or "")
        if tid:
            # Prefer board row: hook payload has reason, not always kind/title.
            db = _needs_board_db()
            if db is not None:
                try:
                    import sqlite3
                    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                    row = con.execute(
                        "SELECT title, assignee, block_kind FROM tasks WHERE id = ?",
                        (tid,),
                    ).fetchone()
                    con.close()
                    if row:
                        title = title or str(row[0] or "")
                        assignee = assignee or str(row[1] or "")
                        kind = kind or str(row[2] or "")
                except Exception:
                    pass
            if _is_need_card(kind, title, assignee):
                _list_upsert_need(tid, title or tid, reason)
                return None
        low = reason.lower()
        if "protocol" in low:
            return _announce_card("protocol", "Protocol.", *args, **kwargs)
        if re.search(r"review[-_ ]required|prodgate|staged", low):
            return _announce_card("stage", "Staged.", *args, **kwargs)
        return _announce_card("stuck", "Stuck.", *args, **kwargs)

    def _on_post_tool(tool_name: str = "", args=None, **kw):
        name = str(tool_name or kw.get("name") or "")
        payload = args if isinstance(args, dict) else (kw.get("args") or kw)
        if not isinstance(payload, dict):
            payload = {}
        if "request_review" in name:
            return _announce_card("stage", "Staged.", payload)
        if any(s in name for s in ("kanban_unblock", "kanban_complete")):
            tid = str(payload.get("task_id") or payload.get("id") or "")
            if tid:
                _list_clear_card(tid, note=name)
        return None

    try:
        ctx.register_hook("kanban_task_completed", _on_complete)
    except Exception:
        pass
    try:
        ctx.register_hook("kanban_task_blocked", _on_block)
    except Exception:
        pass
    try:
        ctx.register_hook("post_tool_call", _on_post_tool)
    except Exception:
        pass

    def _on_session_done(**kwargs):
        # on_session_end fires every turn. Finalize/reset are the chat identity.
        who = (
            getattr(ctx, "profile_name", None)
            or os.environ.get("HERMES_PROFILE")
            or ""
        ).strip().lower()
        if who != "crissa":
            return
        _clear_crissa_popups()

    for _hook in ("on_session_finalize", "on_session_reset"):
        try:
            ctx.register_hook(_hook, _on_session_done)
        except Exception:
            pass


    # The slimming is krew lib/kanban_show_slim.py (what it folds and why is written there); this
    # hook hands it the result before it enters the worker's context. It lives here because team-crissa is
    # the one plugin every crew profile loads (Ada carries the full kanban toolset, not krew-kanban-lean).
    # Anything that is not the expected shape comes back None and the result is left exactly as it was.
    # Kill: KREW_KANBAN_SHOW_SLIM=0.
    def _slim_kanban_show(tool_name=None, result=None, **_kw):
        if tool_name != "kanban_show" or not isinstance(result, str):
            return None
        if os.environ.get("KREW_KANBAN_SHOW_SLIM", "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        try:
            mod = _KANBAN_SHOW_SLIM.get("mod")
            if mod is False:
                return None
            if mod is None:
                import importlib.util
                path = Path(__file__).resolve().parent / "kanban_show_slim.py"
                if not path.is_file():
                    _KANBAN_SHOW_SLIM["mod"] = False
                    _say_missing_once("kanban_show slimming", f"{path} is missing (KREW_KANBAN_SHOW_SLIM=0 says it on purpose)")
                    return None
                spec = importlib.util.spec_from_file_location("krew_kanban_show_slim", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _KANBAN_SHOW_SLIM["mod"] = mod
            return mod.slim(result, os.environ.get("HERMES_KANBAN_TASK") or None)
        except Exception as e:  # noqa: BLE001 - never cost a worker its card
            _say_missing_once("kanban_show slimming", f"{type(e).__name__}: {e}")
            return None

    if not _switch("KREW_TEAM_BOARD_FROM_KREW_BOARD"):     # else krew-board's slimming (marked, readable) is the one
        try:
            ctx.register_hook("transform_tool_result", _slim_kanban_show)
        except Exception:
            pass
