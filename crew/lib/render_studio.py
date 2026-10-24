"""Render-only Lila studio. No Muse. No Cosy change.

User talks to Lila on crew 27B. Vera looks on the same engine. Ada keeps
dispatching one card; Lila/Vera/Bash stay on crew. Ops (Zoey's composer)
is parked. Zoey and Steward sleep. Speaks while Comfy draws (Voice ON →
Cosy `lila`). Heavy/Normal never enter this module's silo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional
try:
    import krew_comfy  # one Comfy location for krew and the Wall: render.json, else KREW_COMFY_*, else ~/ComfyUI
except ImportError:  # loaded by path without its folder on sys.path: the copy beside this file
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("krew_comfy", Path(__file__).resolve().with_name("krew_comfy.py"))
    krew_comfy = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(krew_comfy)

# Reuse cycle helpers (vision + generate). The house file is this repo's
# render-cycle.py. A mint pack that does not ship it still imports: rc is
# None and a generate call says so. A present file that fails to load still
# raises — only FileNotFoundError is the absent path.
import importlib.util

_CYCLE = Path(os.environ.get(
    "KREW_RENDER_CYCLE",
    str(Path(__file__).resolve().parents[1] / "scripts" / "render-cycle.py"),
))
rc = None
try:
    _spec = importlib.util.spec_from_file_location("render_cycle", _CYCLE)
    if not (_spec and _spec.loader and _CYCLE.is_file()):
        raise FileNotFoundError(str(_CYCLE))
    rc = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(rc)
except Exception as _cycle_err:
    if not isinstance(_cycle_err, FileNotFoundError):
        raise
    rc = None

STATE = Path(os.environ.get("RENDER_STUDIO_STATE", str(Path.home() / ".hermes" / "render-studio.json")))
LISTEN_FLAG = Path(
    os.environ.get("RENDER_STUDIO_LISTEN", str(Path.home() / ".hermes" / "render-studio-listen"))
)
OUT_DIR = Path(krew_comfy.get("output"))
DELIVER_DIR = Path(
    os.environ.get(
        "KREW_RENDER_DELIVER",
        str(Path.home() / "Desktop" / "krew-deliverables" / "krew-renders"),
    )
)
VOICE_LOCK = Path(os.environ.get("KREW_VOICE_LOCK", str(Path.home() / ".hermes" / "krew-voice.json")))
# Lila's line is spoken through the Wall's player (POST /v1/speak, like crew/scripts/morning-hello.py): the reference
# install spawned its own speaker script here, which krew does not carry. No player = silent, never an error.
ZOEYOS_PLAYER = os.environ.get("ZOEYOS_PLAYER", "http://127.0.0.1:8100").rstrip("/")
STT = os.environ.get("KREW_STT_URL", "http://127.0.0.1:8190").rstrip("/")
PRESENCE = os.environ.get("KREW_PRESENCE_URL", "http://127.0.0.1:8191").rstrip("/")
_SCRATCH_ID = re.compile(r"^s\d+$")

_busy = threading.Lock()

LILA_TALK = (
    "You are Lila, house designer, in Render studio. You ride "
    "crew 27B (Vera looks here too) and you CAN see the attached still if there is one. "
    "The still on the wall at engage is wallpaper, not the job. USER this turn is law — "
    "they may replace the brief wholesale. Kanban is not your assignment. "
    "JSON only, no markdown: "
    '{"say":"one or two spoken sentences, no markdown",'
    '"generate":"full image prompt if you want a new still or clip, else empty",'
    '"model":"flux2-turbo|flux2-klein|sdxl|flux2-max|qwen-image-2512|qwen-edit-2511|wan22-i2v|ltx23-nvfp4|ltx25-i2v"}'
    " ENGINES this window (put one in model): "
    "flux2-turbo = fast photoreal still (default); "
    "flux2-klein = cheap/fast draft when they want a sketch or to try a idea; "
    "sdxl = open/legacy still, lower quality, publishable. "
    "Pick for the job. If they name an engine, use it. "
    "Menu also has Max, Qwen, Edit (needs a still), WAN (clip from a still), "
    "LTX (text-to-video, no still), "
    "LTX 2.5 (15 s from the current still; first-frame only; wrap the tail after). "
    " If they are chatting (taste, approve, a question) leave generate empty. "
    "If they want a change or the first picture, put the FULL prompt in generate. "
    "Keep generate photoreal, no people unless they asked, no text, no logo. "
    "SPEC is the current brief only. Do not keep wallpaper objects "
    "(lilies, cups, Friday table) unless USER this turn named them. "
    "If they approve the current still (hang it / that's the one), leave "
    "generate empty and set hang true. "
    "If they call you Crissa, say in a few words that you are Lila and Crissa is "
    "resting while you draw, then answer what they asked."
)
LILA_LOOK = (
    "You are Lila looking at a still you just made. SPEC is the current User brief "
    "— not wallpaper, not an old session. JSON only: "
    '{"on_spec":bool,"taste":"one sentence","revise":"prompt rewrite or empty if good"}'
)
VERA_LOOK = (
    "You are Vera. YOUR REFERENCE is the current User brief. They may have replaced "
    "it this turn — do not resurrect dropped objects. You may tighten wording. JSON only: "
    '{"pass":bool,"stop":bool,"why":"one sentence","revise":"prompt rewrite or empty",'
    '"reference":"tighter brief for next look, or empty to keep yours"}'
)
LILA_WINDOW_SEC = 12.0
_OPEN_RE = re.compile(
    r"(?:\bhey\s+lila\b|\blila\s*[,:]|\bdraw\b|\bpaint\b|\bpicture\b|"
    r"\bmake\s+(?:a\s+|it\b)|\bgenerate\b|\brender\b|"
    r"\bhang\s+it\b)",
    re.I,
)
_ZOEY_VOC_RE = re.compile(r"\bhey\s+zoey\b", re.I)
# RENDER IS NOT A CONVERSATION UNTIL SHE IS ADDRESSED (the owner 2026-09-17: "Lila takes
# them as real and keeps referencing the living room … make render non interactive
# unless she is addressed via text entry / mic button ON or hello lila"). Render
# used to open the ear AND arm the studio mic on entry, so every sentence in the
# room was a turn — her own voice off the speakers included: the panel showed her
# line back as "YOU" and she answered it, about the welcome picture. Now a room turn
# (`ear-session`) reaches her only with the Studio mic ON (a click, never the entry),
# with her name on it, or inside the 12 s follow-up window after one that did; a
# sentence that is her own last line is an echo. Typed is always hers.
# KREW_RENDER_ADDRESS_ONLY=0 = every heard sentence is a turn again.
RENDER_ADDRESS_ONLY = os.environ.get("KREW_RENDER_ADDRESS_ONLY", "1").strip().lower() not in ("0", "false", "no", "off")
RENDER_ZOEY_TO_LILA = os.environ.get("KREW_RENDER_ZOEY_TO_LILA", "1").strip().lower() not in ("0", "false", "no", "off")
# SHE COMES IN TO A BLANK LOG, PAUSED (the owner 2026-09-19: "She is not to come into render
# expecting comms - just ready for them. She should now come in to a blank log and on
# paused."). Entry stamps engaged_at; the tape shows only what happened since; entry
# closes the Studio mic even if a crash or reboot left the flag. KREW_RENDER_ENTRY_BLANK=0.
RENDER_ENTRY_BLANK = os.environ.get("KREW_RENDER_ENTRY_BLANK", "1").strip().lower() not in ("0", "false", "no", "off")
_LILA_VOC_RE = re.compile(
    r"(?:\b(?:hey|hello|hi|hiya|okay|ok|yo)[\s,]+(?:lila|leila|leela|leya|lyla)\b"
    r"|^\s*(?:lila|leila|leela|leya|lyla)\s*[,:.!?-])",
    re.I,
)


def is_lila_vocative(text: str) -> bool:
    return bool(_LILA_VOC_RE.search((text or "").strip())) and not is_zoey_vocative(text)


def room_turn_is_hers(text: str, st: dict, *, now: float | None = None) -> tuple[bool, str]:
    """(accept, why) for a sentence the ear heard in Render."""
    now = time.time() if now is None else now
    if _too_close(text, str(st.get("say") or "")):
        return False, "echo"
    if listen_armed():
        return True, "mic"
    if is_lila_vocative(text):
        return True, "vocative"
    if RENDER_ZOEY_TO_LILA and is_zoey_vocative(text):
        # "HEY ZOEY" IN RENDER IS FOR LILA (the owner 2026-09-19: "she can correct a hey
        # Zoey. Let her do it vs scripted" - "User wont always know Zoey is gone").
        # Zoey is away while Lila hosts; the line goes to Lila and SHE answers it,
        # in her own words - no script drops it and none speaks for her.
        return True, "zoey-vocative"
    if st.get("pending_show") and show_pick(text, pending=True):
        return True, "answer"            # she asked "Wall or Control Panel?" — the reply is hers
    try:
        until = float(st.get("lila_until") or 0)
    except (TypeError, ValueError):
        until = 0.0
    if until and now < until:
        return True, "window"
    return False, "not-addressed"
_ADDITIVE_RE = re.compile(
    r"^\s*(add|also|keep|and|plus|warmer|cooler|closer|pull|move)\b|"
    r"\bkeep this\b|\balso\b",
    re.I,
)

# Fallback labels if recipes.json is missing.
ENGINES = {
    "flux2-turbo": "Turbo",
    "flux2-klein": "Klein",
    "sdxl": "SDXL",
}
DEFAULT_ENGINE = "flux2-turbo"
WF_DIR = Path(krew_comfy.get("workflows"))


def _recipes_file() -> Path:
    env = os.environ.get("KREW_COMFY_RECIPES")
    if env:
        return Path(env)
    live = Path.home() / ".hermes" / "plugins" / "krew-comfy" / "recipes.json"
    # krew's own copy of the plugin (plugins/krew-comfy in the checkout; $KREW_HOME/plugins/krew-comfy when installed)
    pack = Path(__file__).resolve().parents[2] / "plugins" / "krew-comfy" / "recipes.json"
    return live if live.is_file() else pack


def catalog_recipes() -> list:
    try:
        data = json.loads(_recipes_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    recs = data.get("recipes") if isinstance(data, dict) else data
    if not isinstance(recs, list):
        return []
    return [r for r in recs if isinstance(r, dict) and r.get("id")]


def studio_engine_list() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    recs = catalog_recipes()
    if not recs:
        return [
            {"id": k, "label": v, "popup": "", "kind": "t2i", "wired": True}
            for k, v in ENGINES.items()
        ]
    for rec in recs:
        if not rec.get("studio"):
            continue
        wf = rec.get("workflow")
        wired = bool(wf) and (WF_DIR / str(wf)).is_file()
        out.append(
            {
                "id": rec["id"],
                "label": rec.get("label") or rec["id"],
                "popup": rec.get("popup") or rec.get("purpose") or "",
                "kind": rec.get("kind") or "t2i",
                "wired": wired,
            }
        )
    return out


def engine_ids() -> set[str]:
    ids = set(ENGINES)
    for e in studio_engine_list():
        ids.add(e["id"])
    return ids
CUE = (
    "You're in Render. Lila works on her own until you address her: type below, "
    "press Studio mic, or say \"Hello Lila\"."
)
EXAMPLE_PROMPT = (
    "Warm kitchen, long dark wood table, a round loaf of bread on a linen cloth, "
    "a vase of white lilies with a yellow ribbon, a red enamel mug, "
    "morning light from the left window, no people."
)
# Bread table (loaf + linen + lilies + red mug). Homescreen wallpaper in Render.
WELCOME_STILLS = (
    "flux2turbo_00008_.png",
    "flux2turbo_00009_.png",
    "flux2turbo_00007_.png",
)
_HANG_RE = re.compile(
    r"^\s*(hang it|hang this|hang that|that's the one|thats the one|"
    r"ship it|we're done|we are done|freeze this)\s*[.!]?\s*$",
    re.I,
)


def silo_ok() -> tuple[bool, str]:
    if rc._mode() != "render":
        return False, "not Render"
    comfy = rc._load_comfy()
    if not comfy.comfy_up():
        return False, "Comfy down"
    return True, "ok"


def _state() -> dict[str, Any]:
    try:
        d = json.loads(STATE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(d: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=2) + "\n")


def _set_busy(label: str) -> None:
    st = _state()
    st["busy"] = label
    # krew-comfy's studio_busy() lets this process draw through its own seat
    st["busy_pid"] = os.getpid()
    _save(st)


def still_basename(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    p = Path(path).name
    if ".." in p or "/" in p or not p.endswith(".png"):
        return None
    if not (OUT_DIR / p).is_file():
        return None
    return p


def video_basename(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    p = Path(path).name
    if ".." in p or "/" in p:
        return None
    low = p.lower()
    if not (low.endswith(".mp4") or low.endswith(".webm")):
        return None
    if not (OUT_DIR / p).is_file():
        return None
    return p


def still_bytes(name: str) -> Optional[bytes]:
    b = still_basename(name)
    if not b:
        return None
    try:
        return (OUT_DIR / b).read_bytes()
    except OSError:
        return None


def video_bytes(name: str) -> Optional[bytes]:
    b = video_basename(name)
    if not b:
        return None
    try:
        return (OUT_DIR / b).read_bytes()
    except OSError:
        return None


def _voice_on() -> bool:
    try:
        d = json.loads(VOICE_LOCK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(d.get("voice") or "").strip().lower() == "on"


def user_given_name(payload: dict[str, Any] | None = None) -> str:
    """AUTH occupant's given name from presence SoT. Empty if none / scratch."""
    d = payload
    if d is None:
        try:
            with urllib.request.urlopen(f"{PRESENCE}/v1/presence", timeout=2) as r:
                d = json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return ""
    if not isinstance(d, dict):
        return ""
    people = d.get("people")
    if not isinstance(people, list):
        return ""
    for p in people:
        if not isinstance(p, dict):
            continue
        auth = p.get("auth") if isinstance(p.get("auth"), dict) else {}
        level = str(auth.get("level") or "").strip().lower()
        authed = bool(auth.get("authed")) or level in (
            "face",
            "operator",
            "code",
            "voice",
        )
        if not authed:
            continue
        raw = str(
            p.get("name") or p.get("display") or auth.get("person_id") or ""
        ).strip()
        if not raw or _SCRATCH_ID.fullmatch(raw.lower()):
            continue
        first = raw.split()[0]
        if len(first) == 1:
            return first.upper()
        return first[0].upper() + first[1:].lower()
    return ""


def orient_line(name: str = "") -> str:
    """One spoken sentence. Name from SoT; never invent one."""
    # No "in this window": the panel is no longer pinned in Render (the owner
    # 2026-09-13); she asks Wall or Control Panel when the first one lands.
    n = (name or "").strip()
    if n:
        return f"{n} — Lila has the helm."
    return "Lila has the helm."


_last_lila_proc: Optional[subprocess.Popen] = None


def _wait_pid(pid: int, cap: float = 20.0) -> None:
    deadline = time.time() + cap
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.12)


def speak_lila(text: str, *, wait_previous: bool = False) -> None:
    """Cosy `lila` in the room. Voice OFF → silent.

    Say-before-draw must not wait (wait_previous=False). Wrap/hang wait.
    """
    global _last_lila_proc
    line = (text or "").strip()
    if not line or not _voice_on():
        return
    if wait_previous and _last_lila_proc is not None and _last_lila_proc.pid:
        _wait_pid(_last_lila_proc.pid)
    try:
        body = json.dumps({"text": line[:400], "voice": "lila", "by": "krew-render-studio", "queue": True}).encode()
        req = urllib.request.Request(ZOEYOS_PLAYER + "/v1/speak", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            r.read()
    except (OSError, urllib.error.URLError, ValueError):
        return
    _last_lila_proc = None


def pick_welcome(st: dict[str, Any] | None = None) -> Optional[str]:
    """Homescreen wallpaper only. Session still is st['still'], not this.

    Hung still wins (Render wall). Else last welcome. Else bread example.
    """
    st = st or {}
    for key in ("wallpaper", "welcome"):
        w = still_basename(st.get(key))
        if w:
            return w
    for name in WELCOME_STILLS:
        if (OUT_DIR / name).is_file():
            return name
    return None


def _clear_brief(st: dict[str, Any]) -> dict[str, Any]:
    """Engage / new session. Wallpaper may stay; SPEC and cards do not."""
    st["spec"] = ""
    st["vera_ref"] = ""
    st["hung"] = False
    st["card"] = None
    st["card_error"] = None
    st["prompt"] = ""
    st["lila_until"] = 0.0
    st["generated"] = False
    st["still"] = None
    st["video"] = None
    st["turns"] = []
    st["vera"] = {}
    st["lila"] = {}
    st["engine"] = DEFAULT_ENGINE
    st["session"] = uuid.uuid4().hex[:8]
    return st


def welcome_pin() -> dict[str, Any]:
    """Render engage. Wallpaper + helm say. Wallpaper is not SPEC."""
    if rc._mode() != "render":
        return {"ok": False, "error": "not Render"}
    st = _state()
    if st.get("orient"):
        nice = pick_welcome(st)
        armed = {"on": listen_armed()}      # the Studio mic is a click, never the entry (2026-09-17)
        return {
            "ok": True,
            "already": True,
            "still": still_basename(st.get("still")),
            "welcome": nice,
            "cue": CUE,
            "say": st.get("say") or "",
            "listen": bool(isinstance(armed, dict) and armed.get("on")) or listen_armed(),
            "talk": True,
        }
    st = _clear_brief(st)
    st["engaged_at"] = time.time()
    nice = pick_welcome(st)
    name = user_given_name()
    line = orient_line(name)
    if nice:
        st["welcome"] = nice
    st["cue"] = CUE
    st["say"] = line
    st["orient"] = True
    _save(st)
    if RENDER_ENTRY_BLANK:
        listen_set(False)                   # paused on entry, whatever a crash or reboot left
    armed = {"on": listen_armed()}          # the Studio mic is a click, never the entry (2026-09-17)
    # not spoken (the owner 2026-09-14: no scripted announcement of Render mode);
    # the line stays in state for the panel's text
    return {
        "ok": True,
        "still": None,
        "welcome": nice,
        "cue": CUE,
        "say": line,
        "user": name,
        "listen": bool(isinstance(armed, dict) and armed.get("on")),
        "talk": True,
    }


def snapshot() -> dict[str, Any]:
    st = _state()
    still = still_basename(st.get("still"))
    crew_still = latest_comfy_still()
    if crew_still:
        if still:
            try:
                sm = (OUT_DIR / still).stat().st_mtime
                cm = (OUT_DIR / crew_still).stat().st_mtime
                if cm > sm + 1:
                    still = crew_still
            except OSError:
                pass
        elif st.get("generated") or st.get("busy") or st.get("card"):
            still = crew_still
    welcome = pick_welcome(st)
    # Wall JS is immutable: welcome_url || still_url. Keep welcome so the
    # Render homescreen stays bread/hung wallpaper until Hang; the panel
    # prefers still_url for the session still.
    until = 0.0
    try:
        until = float(st.get("lila_until") or 0)
    except (TypeError, ValueError):
        until = 0.0
    out = {
        "ok": True,
        "busy": st.get("busy") or "",
        "still": still,
        "still_url": f"/render-still/{still}" if still else None,
        "welcome": welcome,
        "welcome_url": f"/render-still/{welcome}" if welcome else None,
        "cue": CUE,
        "say": st.get("say") or "",
        "vera": st.get("vera") if isinstance(st.get("vera"), dict) else {},
        "lila": st.get("lila") if isinstance(st.get("lila"), dict) else {},
        "spec": st.get("spec") or "",
        "vera_ref": st.get("vera_ref") or st.get("spec") or "",
        "engine": resolve_engine(st),
        "engines": studio_engine_list(),
        "hung": bool(st.get("hung")),
        "card": st.get("card"),
        "turns": (st.get("turns") or [])[-8:] if isinstance(st.get("turns"), list) else [],
        "feed": crew_feed(),
        "generated": bool(st.get("generated")),
        "listen": listen_armed(),
        "talk": True,
        "inbox": (st.get("inbox") or [])[-8:] if isinstance(st.get("inbox"), list) else [],
        "card_busy": lila_card_busy(),
        "lila_until": until,
        "lila_open": bool(until and time.time() < until),
        "example_prompt": EXAMPLE_PROMPT,
        # the Tk wall paints `show` when on == "wall" (the owner 2026-09-13)
        "show": st.get("show") if isinstance(st.get("show"), dict) else None,
        "show_on": st.get("show_on") or "",
        "video": video_basename(st.get("video")),
        "video_url": (
            f"/render-video/{video_basename(st.get('video'))}"
            if video_basename(st.get("video"))
            else None
        ),
    }
    # THE STAGE SHOWS WORK IN PROGRESS ONLY (the owner 2026-09-18): the wallpaper is
    # not the job. The panel paints Comfy's live preview while it draws, else
    # the newest version this Render, else a blank "Please wait…" — never
    # `welcome_url`. lib/render_wip.py is the source; a miss leaves the keys
    # out and the panel shows the blank note.
    try:
        import render_wip

        out.update(render_wip.wip_payload())
    except Exception as e:  # noqa: BLE001
        out["wip_error"] = f"{type(e).__name__}: {e}"[:120]
    return out


def listen_armed() -> bool:
    return LISTEN_FLAG.is_file()


BOARD_ROOT = Path.home() / ".hermes" / "kanban" / "boards"
_FEED_WHO = frozenset({
    "ada", "bash", "lila", "vera", "steward", "zoey", "grok",
    "kanban-loop", "auto-decomposer", "kanban-board-gate",
})


def _board_db() -> Path | None:
    slug = "daily"
    cur = BOARD_ROOT / "current"
    try:
        if cur.is_file():
            slug = cur.read_text(encoding="utf-8").strip() or slug
    except OSError:
        pass
    p = BOARD_ROOT / slug / "kanban.db"
    return p if p.is_file() else None


def latest_comfy_still(*, newer_than: float = 0.0) -> str | None:
    """Newest png in Comfy out — crew draws here too, not only the studio turn."""
    try:
        pics = list(OUT_DIR.glob("*.png"))
    except OSError:
        return None
    best: tuple[float, str] | None = None
    for p in pics:
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m <= newer_than:
            continue
        if best is None or m > best[0]:
            best = (m, p.name)
    return best[1] if best else None


def crew_feed(*, limit: int = 40) -> list[dict[str, Any]]:
    """One tape: studio turns + kanban comments + running-card log tail."""
    rows: list[dict[str, Any]] = []
    st = _state()
    try:
        since = float(st.get("engaged_at") or 0) if RENDER_ENTRY_BLANK else 0.0
    except (TypeError, ValueError):
        since = 0.0
    for t in (st.get("turns") or [])[-12:]:
        if not isinstance(t, dict):
            continue
        try:
            ts = float(t.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if t.get("user"):
            rows.append({"ts": ts, "who": "You", "kind": "user",
                         "text": str(t.get("user") or "")[:240]})
        say = str(t.get("say") or t.get("lila") or "").strip()
        if say:
            rows.append({"ts": ts + 0.01, "who": "Lila", "kind": "talk", "text": say[:240]})
        vera = t.get("vera") if isinstance(t.get("vera"), dict) else None
        if vera and (vera.get("why") or vera.get("pass") is not None):
            bit = ("Pass. " if vera.get("pass") else "Not yet. ") + str(vera.get("why") or "")
            rows.append({"ts": ts + 0.02, "who": "Vera", "kind": "look", "text": bit.strip()[:240]})
    db = _board_db()
    if db is not None:
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            for r in con.execute(
                "SELECT created_at, author, body, task_id FROM task_comments "
                "WHERE created_at >= ? ORDER BY id DESC LIMIT 30", (since,)
            ):
                who = str(r["author"] or "").strip() or "?"
                if who.lower() not in _FEED_WHO and who.lower() not in {"user", "default"}:
                    continue
                rows.append({
                    "ts": float(r["created_at"] or 0),
                    "who": who,
                    "kind": "kanban",
                    "card": str(r["task_id"] or ""),
                    "text": re.sub(r"\s+", " ", str(r["body"] or "")).strip()[:240],
                })
            running = list(con.execute(
                "SELECT id, assignee, substr(title,1,80) AS title FROM tasks "
                "WHERE status IN ('running','ready') ORDER BY id DESC LIMIT 4"
            ))
            con.close()
            log_dir = db.parent / "logs"
            for r in running:
                lp = log_dir / f"{r['id']}.log"
                if since:
                    try:
                        if lp.stat().st_mtime < since:
                            continue            # no activity this session: not on her tape
                    except OSError:
                        continue
                rows.append({
                    "ts": time.time(),
                    "who": str(r["assignee"] or "crew"),
                    "kind": "card",
                    "card": str(r["id"]),
                    "text": f"working {r['id']} · {r['title'] or ''}".strip()[:240],
                })
                lp = log_dir / f"{r['id']}.log"
                if lp.is_file():
                    try:
                        tail = lp.read_text(encoding="utf-8", errors="replace").splitlines()[-4:]
                    except OSError:
                        tail = []
                    for line in tail:
                        line = line.strip()
                        if line:
                            rows.append({
                                "ts": time.time(),
                                "who": str(r["assignee"] or "log"),
                                "kind": "log",
                                "card": str(r["id"]),
                                "text": line[:240],
                            })
        except Exception:
            pass
    rows.sort(key=lambda x: float(x.get("ts") or 0))
    return rows[-limit:]


# RENDER SHOW (the owner 2026-09-13: "show the renders from Wall OR CP whichever the
# User picks"). Asked once, remembered across Renders in `show_on`; until he
# answers it shows on the wall, so Voice-Only never needs the panel.
SHOW_ASK = "Wall or Control Panel?"
# THE CREW'S WORK LANDS ON THE CONTROL PANEL (the owner 2026-09-17: "the team's work
# default to this CP window on every iteration. Like their workspace broadcast
# for viewing"). In Render Lila holds the wall; the studio on the panel is where
# each iteration shows. So an unpicked render goes to the panel and nobody is
# asked. It is a broadcast, not a summons: the DEFAULT neither fronts the tab nor
# pins the panel (09-16: "Render leaves the User where they are") — an explicit
# "show it on the panel" still does, and "show it on the wall" still re-picks.
# KREW_RENDER_SHOW_DEFAULT=wall is the 09-13 behaviour, ask included.
SHOW_DEFAULT = "wall" if os.environ.get("KREW_RENDER_SHOW_DEFAULT", "panel").strip().lower() == "wall" else "panel"
KREW_URL = (os.environ.get("ZOEYOS_FEED") or "http://127.0.0.1:8099").rstrip("/")   # the Wall's feed (the pack's port table)
PANEL_URL = KREW_URL + "/panel"
DEDUPE_SEC = 10.0
_SHOW_SAY_RE = re.compile(
    r"\bshow\s+(?:it|them|that|this|those|the\s+renders?|renders?|the\s+work)\s+"
    r"(?:on|in)\s+the\s+(wall|(?:control\s+)?panel)\b",
    re.I,
)
_SHOW_BARE_RE = re.compile(
    r"^\s*(?:(?:on|in)\s+)?(?:the\s+)?(wall|(?:control\s+)?panel|cp)\b[\w\s,']{0,16}[.!]?\s*$",
    re.I,
)


def show_pick(text: str, *, pending: bool = False) -> str:
    """'wall' | 'panel' | ''. "Show it on the panel" re-picks any time; a bare
    "The wall, please." only answers her ask — "a vase on the wall" is a brief."""
    t = text or ""
    m = _SHOW_SAY_RE.search(t) or (_SHOW_BARE_RE.search(t) if pending else None)
    if not m:
        return ""
    return "wall" if m.group(1).lower() == "wall" else "panel"


def _glass() -> str:
    try:
        import glass_mode

        return str(glass_mode.read_mode() or "")
    except Exception:  # noqa: BLE001
        return ""


def _front_tab(url: str) -> str:
    try:
        import zos_brave

        if zos_brave.background():
            # a finished render is a tab, not a reason to raise or start ZOS Brave (the User 2026-10-02)
            return zos_brave.tab_only(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        import glass_mode

        return glass_mode.front_tab(url)
    except Exception as e:  # noqa: BLE001
        return f"front failed: {type(e).__name__}"


EAR_SESSION_OPEN = os.environ.get("EAR_BRIDGE_SESSION_OPEN", "http://127.0.0.1:8194/v1/session/open")
EAR_SESSION_PAUSE = os.environ.get("EAR_BRIDGE_SESSION_PAUSE", "http://127.0.0.1:8194/v1/session/pause")
LILA_PAGE = Path(__file__).resolve().parents[1] / "scripts" / "lila-page.py"


def _open_ear() -> None:
    """Her question opens the ear (the PAUSED chip's call), so "the wall"
    needs no wake word; the ear's idle expiry pauses it again."""
    try:
        req = urllib.request.Request(EAR_SESSION_OPEN, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:  # noqa: BLE001
        pass


def present(still: Optional[str] = None, video: Optional[str] = None, *, ask: bool = True) -> dict[str, Any]:
    """Put a finished render in front of the User where they picked. The Tk
    wall paints `show` itself (a silent layer); on the Brave glass, and for the
    panel, a tools tab is fronted — never the kiosk, never full screen."""
    vb = video_basename(video)
    sb = still_basename(still)
    if not (vb or sb):
        return {"ok": False, "error": "nothing to show"}
    st = _state()
    on = str(st.get("show_on") or "")
    asked = False
    picked = on in ("wall", "panel")
    if not picked:
        on = SHOW_DEFAULT
        if on == "wall" and ask and not st.get("show_asked"):
            st["show_asked"] = True
            st["pending_show"] = {"still": sb, "video": vb}
            asked = True
    kind = "video" if vb else "still"
    path = f"/render-video/{vb}" if vb else f"/render-still/{sb}"
    st["show"] = {"on": on, "kind": kind, "name": vb or sb, "url": path, "ts": time.time()}
    if on == "panel" and picked:
        st["panel_pinned"] = True
    _save(st)
    how = ""
    if on == "panel" and picked:
        how = _front_tab(PANEL_URL)
    elif on == "panel":
        how = "broadcast (the panel's studio shows it; nothing fronted)"
    elif _glass() == "brave":
        how = _front_tab(KREW_URL + path)
    if asked:
        speak_lila(SHOW_ASK, wait_previous=True)
        _open_ear()
    return {"ok": True, "on": on, "asked": asked, "show": st["show"], "front": how}


def pick_show(on: str) -> dict[str, Any]:
    """The User answered her ask, or re-picked: remember it, and show the
    waiting render (else the last one) where they want it now."""
    st = _state()
    st["show_on"] = on
    st["show_asked"] = True
    pend = st.get("pending_show") if isinstance(st.get("pending_show"), dict) else None
    last = st.get("show") if isinstance(st.get("show"), dict) else None
    st["pending_show"] = None
    _save(st)
    line = "The wall it is." if on == "wall" else "Control Panel it is."
    speak_lila(line)
    shown = None
    if pend:
        shown = present(pend.get("still"), pend.get("video"), ask=False)
    elif last:
        name = last.get("name")
        shown = present(name if last.get("kind") == "still" else None,
                        name if last.get("kind") == "video" else None, ask=False)
    return {"ok": True, "show_on": on, "say": line, "shown": shown}


def panel_was_pinned() -> bool:
    """Did this Render put the Control Panel up (the User's pick)? krew-mode
    sends the kiosk back to the wall on the way out only then."""
    return bool(_state().get("panel_pinned"))


def leave_render() -> None:
    """Leaving occupancy. Drop helm latch; do not keep the Lila window.
    `show_on` (the User's Wall / Control Panel pick) survives."""
    st = _state()
    st["orient"] = False
    st["panel_pinned"] = False
    st["pending_show"] = None
    _close_window(st)
    _save(st)
    listen_set(False)


def listen_set(on: bool) -> dict[str, Any]:
    """Studio Mic IS Listening/Pause — the wall chip, not a second latch.

    Open the ear so she hears the room. Do not inject into a User-render
    speech loop. A typed/voice note pages her gateway when she is free.
    """
    if on:
        ok, why = silo_ok()
        if not ok:
            return {"ok": False, "on": False, "error": f"silo: {why}"}
        _ear_post(EAR_SESSION_OPEN)
        LISTEN_FLAG.parent.mkdir(parents=True, exist_ok=True)
        LISTEN_FLAG.write_text("render-studio\n")
    else:
        _ear_post(EAR_SESSION_PAUSE)
        try:
            LISTEN_FLAG.unlink()
        except FileNotFoundError:
            pass
    return {"ok": True, "on": listen_armed()}


def _ear_post(url: str) -> None:
    try:
        req = urllib.request.Request(
            url, data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def lila_card_busy() -> str:
    """Running kanban assignee=lila, or the render-lane busy file."""
    doing = lane_busy_what()
    if doing:
        return doing
    db = _board_db()
    if db is None:
        return ""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute(
            "SELECT id, substr(title,1,60) FROM tasks "
            "WHERE status='running' AND lower(assignee)='lila' LIMIT 1"
        ).fetchone()
        con.close()
    except Exception:
        return ""
    if not row:
        return ""
    return f"card {row[0]}"


def page_lila(text: str) -> dict[str, Any]:
    """Same door as zoey-page: her gateway. Queue if she's on a card. No Cosy push."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty", "say": ""}
    busy = lila_card_busy()
    st = _state()
    if busy:
        q = st.get("inbox") if isinstance(st.get("inbox"), list) else []
        q.append({"ts": time.time(), "text": text[:400]})
        st["inbox"] = q[-12:]
        _save(st)
        return {
            "ok": True,
            "queued": True,
            "busy": busy,
            "say": f"She's on {busy} — she'll get this when she can.",
        }
    try:
        spec = importlib.util.spec_from_file_location("lila_page", LILA_PAGE)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        reply = mod.page(text)
    except Exception as e:
        return {"ok": False, "error": str(e)[:120], "say": ""}
    return {"ok": True, "queued": False, "say": reply, "via": "gateway"}


def _too_close(a: str, b: str) -> bool:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def latest_heard(ignore: str = "") -> dict[str, Any]:
    """Newest human ear/listen turn from rail-stt. Never a browser mic."""
    try:
        with urllib.request.urlopen(f"{STT}/v1/turns?n=12", timeout=2) as r:
            turns = json.loads(r.read().decode()).get("turns") or []
    except Exception as e:
        return {"ok": False, "text": "", "error": str(e)[:80]}
    ignore = (ignore or "").strip()
    for t in reversed(turns if isinstance(turns, list) else []):
        if not isinstance(t, dict):
            continue
        # krew: the reference install also skipped turns from its retired vision profile here; krew has no such profile
        st = t.get("stamp") if isinstance(t.get("stamp"), dict) else {}
        if st.get("source") in {"tts_echo_veto"}:
            continue
        if t.get("reason") == "tts_echo_veto":
            continue
        text = (t.get("text") or "").strip()
        if len(text) < 8 or _too_close(text, ignore):
            continue
        return {"ok": True, "text": text, "ts": t.get("ts"), "via": via}
    return {"ok": True, "text": "", "via": ""}


def one_listen(seconds: float = 4.0) -> dict[str, Any]:
    """One-shot :8190/v1/listen when the continuous ear is closed.

    Voice ON already owns the Seeed. Do not fight it — return ear_open.
    """
    ok, why = silo_ok()
    if not ok:
        return {"ok": False, "error": f"silo: {why}", "text": ""}
    try:
        seconds = max(1.0, min(8.0, float(seconds)))
    except (TypeError, ValueError):
        seconds = 4.0
    ear_open = False
    try:
        with urllib.request.urlopen(f"{STT}/health", timeout=1.5) as r:
            health = json.loads(r.read().decode())
        ear_open = bool((health.get("ear") or {}).get("open"))
    except Exception:
        ear_open = False
    if ear_open:
        heard = latest_heard()
        heard["ear_open"] = True
        heard["ok"] = True
        return heard
    req = urllib.request.Request(
        f"{STT}/v1/listen",
        data=json.dumps({"seconds": seconds}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=seconds + 8) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code == 409:
            heard = latest_heard()
            heard["ear_open"] = True
            heard["ok"] = True
            return heard
        return {"ok": False, "error": raw[:160], "text": ""}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160], "text": ""}
    return {
        "ok": True,
        "ear_open": False,
        "text": (d.get("text") or "").strip(),
        "via": "listen",
    }


def amend_spec(spec: str, user_text: str) -> str:
    """Add to the current brief. A new picture request replaces — see next_spec."""
    spec = (spec or "").strip()
    user = (user_text or "").strip()
    if not user:
        return spec
    if not spec:
        return user
    if user.lower() in spec.lower():
        return spec
    return spec + "\nAlso: " + user


def is_additive(text: str) -> bool:
    return bool(_ADDITIVE_RE.search(text or ""))


def next_spec(spec: str, user_text: str, *, generated: bool) -> str:
    """Chat does not touch SPEC. Generate: additive amends, else replace."""
    if not generated:
        return (spec or "").strip()
    user = (user_text or "").strip()
    if is_additive(user) and (spec or "").strip():
        return amend_spec(spec, user)
    return user


def is_hang(text: str) -> bool:
    return bool(_HANG_RE.match(text or ""))


def is_zoey_vocative(text: str) -> bool:
    return bool(_ZOEY_VOC_RE.search(text or ""))


def is_lila_open(text: str) -> bool:
    t = (text or "").strip()
    if not t or is_zoey_vocative(t):
        return False
    if is_hang(t):
        return True
    return bool(_OPEN_RE.search(t))


def ear_reaches_lila(
    text: str,
    *,
    now: float | None = None,
    until: float = 0,
    ignore: str = "",
) -> bool:
    """Ear auto-submit: opening vocative or inside the 12 s window."""
    now = time.time() if now is None else now
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if _too_close(t, ignore):
        return False
    if is_zoey_vocative(t):
        return False
    if is_lila_open(t):
        return True
    try:
        until_f = float(until or 0)
    except (TypeError, ValueError):
        until_f = 0.0
    return bool(until_f and now < until_f)


def _refresh_window(st: dict[str, Any], *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    st["lila_until"] = now + LILA_WINDOW_SEC


def _close_window(st: dict[str, Any]) -> None:
    st["lila_until"] = 0.0


def resolve_engine(st: dict[str, Any], lila_model: str = "", user_engine: str = "") -> str:
    # Panel chip this POST, then Lila's job pick, then sticky state.
    # State-before-Lila (2026-08-21) ignored flux2-klein on a turbo session.
    ids = engine_ids()
    for cand in (user_engine, lila_model, st.get("engine"), DEFAULT_ENGINE):
        name = str(cand or "").strip()
        if name in ids:
            return name
    return DEFAULT_ENGINE


def set_engine(name: str) -> dict[str, Any]:
    ok, why = silo_ok()
    if not ok:
        return {"ok": False, "error": f"silo: {why}"}
    name = str(name or "").strip()
    if name not in engine_ids():
        return {"ok": False, "error": f"engine not in ad-hoc menu: {name}"}
    st = _state()
    st["engine"] = name
    _save(st)
    label = ENGINES.get(name) or name
    for e in studio_engine_list():
        if e["id"] == name:
            label = e.get("label") or name
            if not e.get("wired"):
                return {
                    "ok": False,
                    "error": f"{label} is not wired",
                    "engine": name,
                }
            break
    return {"ok": True, "engine": name, "label": label}


def apply_vera_ref(current: str, spec: str, vera: dict[str, Any] | None) -> str:
    """Vera may tighten wording. Current User SPEC is law — no resurrected objects."""
    spec = (spec or "").strip()
    proposed = str((vera or {}).get("reference") or "").strip()
    if spec:
        return spec
    return proposed or (current or "").strip()


def _hermes_bin() -> Optional[str]:
    return shutil.which("hermes")


def _fleet_board() -> str:
    """The board the fleet works on: the `current` pointer, not today's date.
    2026-09-17: the day board had not rolled, so every render card call went to
    a `daily-<today>` that did not exist and soft-failed unseen."""
    try:
        slug = (Path.home() / ".hermes" / "kanban" / "current").read_text().strip()
    except OSError:
        slug = ""
    return slug or time.strftime("daily-%Y-%m-%d")


def _kanban(args: list[str], timeout: float = 25.0) -> dict[str, Any]:
    """Soft fail. Never spawn a worker — callers use blocked / comment / complete."""
    exe = _hermes_bin()
    if not exe:
        return {"ok": False, "error": "hermes missing"}
    board = os.environ.get("KREW_KANBAN_BOARD") or _fleet_board()
    cmd = [exe, "kanban", "--board", board, *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        return {"ok": False, "error": (err or out)[:240]}
    try:
        return {"ok": True, "data": json.loads(out)}
    except json.JSONDecodeError:
        return {"ok": True, "text": out[:400]}


def _task_id(data: Any) -> str:
    if isinstance(data, dict):
        for k in ("id", "task_id"):
            v = data.get(k)
            if v:
                return str(v)
        t = data.get("task")
        if isinstance(t, dict) and t.get("id"):
            return str(t["id"])
    return ""


def ensure_card(st: dict[str, Any]) -> dict[str, Any]:
    """Blocked Lila card. Crew is parked — Ada must not spawn her."""
    if st.get("card"):
        return st
    session = st.get("session") or uuid.uuid4().hex[:8]
    st["session"] = session
    day = time.strftime("%Y-%m-%d")
    key = f"render-studio-{day}-{session}"
    title = f"Render studio · {day} · {session}"
    body = (
        "Ad-hoc Control Panel studio. User art-directs; Lila and Vera ride crew 27B; "
        "Vera looks. Ops :8000 is parked — DO NOT DISPATCH this card while "
        "krew-gpu-mode=render. Status starts blocked on purpose. Comments "
        "are the session log. Hang-it completes."
    )
    r = _kanban(
        [
            "create",
            title,
            "--assignee",
            "lila",
            "--initial-status",
            "blocked",
            "--created-by",
            "render-studio",  # a machine card, not the User's touch (09-28)
            "--idempotency-key",
            key,
            "--json",
            "--body",
            body,
        ]
    )
    tid = _task_id(r.get("data")) if r.get("ok") else ""
    st["card"] = tid or None
    st["card_error"] = None if tid else (r.get("error") or "no task id")
    _save(st)
    return st


def _comment(st: dict[str, Any], text: str, author: str = "user") -> None:
    tid = st.get("card")
    if not tid:
        return
    _kanban(["comment", str(tid), text[:1500], "--author", author, "--max-len", "1500"])


def deliver_hang(still: Optional[str] = None, video: Optional[str] = None) -> dict[str, str]:
    """Copy the hung still (and clip, if any) into krew-renders."""
    out: dict[str, str] = {}
    DELIVER_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if still:
        src = OUT_DIR / still
        if src.is_file():
            dest = DELIVER_DIR / f"{Path(still).stem}_{stamp}{Path(still).suffix}"
            shutil.copy2(src, dest)
            latest = DELIVER_DIR / f"latest{Path(still).suffix}"
            shutil.copy2(src, latest)
            out["still"] = str(dest)
            out["latest"] = str(latest)
    vid = video_basename(video)
    if vid:
        vsrc = OUT_DIR / vid
        if vsrc.is_file():
            vdest = DELIVER_DIR / f"{Path(vid).stem}_{stamp}{Path(vid).suffix}"
            shutil.copy2(vsrc, vdest)
            out["video"] = str(vdest)
            out.setdefault("latest", str(vdest))
    return out


def hang_still(note: str = "") -> dict[str, Any]:
    """User resolve. Freeze still, copy to krew-renders, pin Render wallpaper."""
    ok, why = silo_ok()
    if not ok:
        return {"ok": False, "error": f"silo: {why}", "say": "We are not in Render."}
    st = _state()
    _close_window(st)
    still = still_basename(st.get("still"))
    video = video_basename(st.get("video"))
    if not still and not video:
        return {"ok": False, "error": "no still", "say": "Nothing to hang yet."}
    delivered = deliver_hang(still, video)
    if still:
        st["wallpaper"] = still
        st["welcome"] = still
    st["hung"] = True
    st["busy"] = ""
    st["say"] = "Hung. On the Render homescreen and in krew-renders."
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    st["hung_ts"] = ts
    st["deliver"] = delivered.get("still") or delivered.get("video") or ""
    ref = st.get("vera_ref") or st.get("spec") or ""
    _save(st)
    path = (OUT_DIR / still) if still else (OUT_DIR / (video or ""))
    speak_lila("Hung. I'd put it on the wall.", wait_previous=True)
    rc._log(
        {
            "ts": ts,
            "studio": True,
            "hung": True,
            "still": str(path) if still else "",
            "video": str(OUT_DIR / video) if video else "",
            "deliver": delivered,
            "vera_ref": ref,
        }
    )
    return {
        "ok": True,
        "hung": True,
        "say": st["say"],
        "still": still,
        "still_url": f"/render-still/{still}" if still else None,
        "welcome": still or pick_welcome(st),
        "welcome_url": f"/render-still/{still}" if still else None,
        "deliver": delivered,
        "vera_ref": ref,
        "card": None,
        "generated": False,
    }


def _append_turn(st: dict[str, Any], row: dict[str, Any]) -> None:
    turns = st.get("turns") if isinstance(st.get("turns"), list) else []
    turns.append(row)
    st["turns"] = turns[-12:]


# A kanban card renders in a different PROCESS, so `_busy` above cannot see it.
# krew-comfy drops this marker while its graph is in flight; we honour it, and
# it honours our `busy` label. Two mouths, one --max-num-seqs 1 seat.
LANE_BUSY = Path(
    os.environ.get("KREW_RENDER_LANE_BUSY", str(Path.home() / ".hermes" / "render-lane-busy"))
)
LANE_BUSY_TTL = float(os.environ.get("KREW_RENDER_LANE_TTL", "900"))


def lane_busy_what() -> str:
    """What a kanban card is doing on the seat, or "" if nothing is.

    Stale-safe: a worker killed mid-graph leaves the file behind, and a wedged
    studio is worse than a double-submit that Comfy will queue anyway.
    """
    try:
        rec = json.loads(LANE_BUSY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(rec, dict):
        return ""
    if time.time() - float(rec.get("ts") or 0) > LANE_BUSY_TTL:
        return ""
    return str(rec.get("what") or "rendering")


# THE EAR ASKS FIRST, THEN SHE HEARS IT LIVE (the owner 2026-09-19: "skipping was not
# intended. Her live during render for speech TO her is."). 19a4fa34 (09-16) sent
# every ear line straight to her gateway, ahead of the two checks the draw loop
# ran since 09-13: was she addressed (ear_reaches_lila - vocative or inside her
# 12 s window), and is this the panel's copy of a line the ear already sent (one
# answer, not two). Same checks, same order, same state; a line that passes still
# goes to her gateway. KREW_RENDER_EAR_GATE=0 is the 09-16 direct route.
RENDER_EAR_GATE = os.environ.get("KREW_RENDER_EAR_GATE", "1").strip().lower() not in ("0", "false", "no", "off")


def _ear_turn_gate(user_text: str) -> dict[str, Any] | None:
    """None = the ear line reaches her; else the skip record (not-addressed / duplicate)."""
    st = _state()
    try:
        until = float(st.get("lila_until") or 0)
    except (TypeError, ValueError):
        until = 0.0
    if RENDER_ADDRESS_ONLY:
        # the owner 09-17 (0bcfb9f8): her name, the Studio mic ON, an answer to her own
        # question, or her 12 s window; and since 09-19 a "hey Zoey", which she
        # corrects herself because the User won't always know Zoey is gone.
        hers, why = room_turn_is_hers(user_text, st)
        if not hers:
            return {"ok": False, "skipped": True, "reason": why, "say": ""}
    elif not ear_reaches_lila(user_text, until=until, ignore=str(st.get("say") or "")):
        return {"ok": False, "skipped": True, "reason": "not-addressed", "say": ""}
    try:
        last_ts = float(st.get("last_user_ts") or 0)
    except (TypeError, ValueError):
        last_ts = 0.0
    a = re.sub(r"[^\w\s]", "", user_text.lower()).strip()
    b = re.sub(r"[^\w\s]", "", str(st.get("last_user") or "").lower()).strip()
    same = bool(a and b and (a == b or (
        (a in b or b in a) and min(len(a), len(b)) >= 0.8 * max(len(a), len(b)))))
    if same and time.time() - last_ts < DEDUPE_SEC:
        return {"ok": False, "skipped": True, "reason": "duplicate", "say": ""}
    st["last_user"] = user_text
    st["last_user_ts"] = time.time()
    _save(st)
    return None


def lila_turn(user_text: str, *, engine: str = "", source: str = "typed") -> dict[str, Any]:
    # Typed / spoken notes go to her gateway (zoey-page twin). Hang/engine
    # still use the studio draw loop. Do not Cosy-push a busy line.
    src = (source or "typed").strip().lower()
    if src in ("typed", "ear", "wall") and not engine:
        t = (user_text or "").strip().lower()
        if t and not t.startswith("hang") and "that's the one" not in t:
            if src == "ear" and RENDER_EAR_GATE:
                skip = _ear_turn_gate(user_text)
                if skip is not None:
                    return skip
            return page_lila(user_text)
    if not _busy.acquire(blocking=False):
        return {
            "ok": False,
            "busy": True,
            "error": "Lila is still drawing",
            "say": "Give me a moment — still on this one.",
        }
    doing = lane_busy_what()
    if doing:
        _busy.release()
        return {
            "ok": False,
            "busy": True,
            "error": f"a kanban card is {doing} on crew",
            "say": "Give me a moment — I'm finishing a card.",
        }
    try:
        return _lila_turn(user_text, engine=engine, source=source)
    except Exception:
        # the panel server answers {"say": "I dropped the charcoal."} but only
        # the panel shows it — in the room a failed turn was silence
        speak_lila("I dropped the charcoal.")
        raise
    finally:
        try:
            st = _state()
            if st.get("busy"):
                st["busy"] = ""
                _save(st)
        except OSError:
            pass
        _busy.release()


def _lila_turn(user_text: str, *, engine: str = "", source: str = "typed") -> dict[str, Any]:
    ok, why = silo_ok()
    if not ok:
        return {"ok": False, "error": f"silo: {why}", "say": "We are not in Render."}
    user_text = (user_text or "").strip()
    if not user_text:
        return {"ok": False, "error": "empty", "say": "Tell me what you want on the wall."}
    st = _state()
    src = (source or "typed").strip().lower()
    if src == "ear":
        until = 0.0
        try:
            until = float(st.get("lila_until") or 0)
        except (TypeError, ValueError):
            until = 0.0
        if not ear_reaches_lila(
            user_text, until=until, ignore=str(st.get("say") or "")
        ):
            return {
                "ok": False,
                "skipped": True,
                "reason": "not-addressed",
                "say": "",
            }
    if src == "ear-session" and RENDER_ADDRESS_ONLY:
        hers, why_not = room_turn_is_hers(user_text, st)
        if not hers:
            return {"ok": False, "skipped": True, "reason": why_not, "say": ""}
    if src in ("ear", "ear-session"):
        # Render turns come from the ear itself now (the owner 2026-09-13), and the
        # locked panel page may still post the same words from its own poll:
        # one answer. "Zoey." then "Zoey, make it warmer" are two turns.
        try:
            last_ts = float(st.get("last_user_ts") or 0)
        except (TypeError, ValueError):
            last_ts = 0.0
        a = re.sub(r"[^\w\s]", "", user_text.lower()).strip()
        b = re.sub(r"[^\w\s]", "", str(st.get("last_user") or "").lower()).strip()
        same = bool(a and b and (a == b or (
            (a in b or b in a) and min(len(a), len(b)) >= 0.8 * max(len(a), len(b)))))
        if same and time.time() - last_ts < DEDUPE_SEC:
            return {"ok": False, "skipped": True, "reason": "duplicate", "say": ""}
    st["last_user"] = user_text
    st["last_user_ts"] = time.time()
    _save(st)
    pick = show_pick(user_text, pending=bool(st.get("pending_show")))
    if pick:
        return pick_show(pick)
    if is_hang(user_text):
        return hang_still(user_text)
    if st.get("hung"):
        keep = ("show_on", "show_asked", "show", "pending_show", "panel_pinned",
                "orient", "last_user", "last_user_ts")
        st = {
            "still": st.get("still"),
            "spec": "",
            "vera_ref": "",
            "engine": st.get("engine") or DEFAULT_ENGINE,
            "session": uuid.uuid4().hex[:8],
            "welcome": st.get("welcome"),
            **{k: st.get(k) for k in keep if k in st},
        }
    last = Path(st["still"]) if st.get("still") and Path(st["still"]).is_file() else None
    spec = (st.get("spec") or "").strip()
    if engine in engine_ids():
        st["engine"] = engine
    _refresh_window(st)
    _save(st)
    user = (
        f"SPEC (User law):\n{spec}\n"
        f"VERA REFERENCE:\n{st.get('vera_ref')}\n"
        f"ENGINE now: {resolve_engine(st)}  menu: flux2-turbo | flux2-klein | sdxl\n"
        f"USER this turn: {user_text}\n"
    )
    _set_busy("listening")
    if last:
        raw = rc._see(last, LILA_TALK, user + "Look at the still. Art-direct or approve.")
    else:
        body = {
            "model": "crew",
            "max_tokens": 220,
            "messages": [
                {"role": "system", "content": LILA_TALK},
                {"role": "user", "content": user + "No still yet. Propose the first generate."},
            ],
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            f"{rc.CREW}/v1/chat/completions",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = (json.loads(r.read().decode())["choices"][0]["message"].get("content") or "").strip()
    plan = rc._json_from(raw)
    say = str(plan.get("say") or raw)[:400]
    if plan.get("hang"):
        return hang_still(user_text)
    gen = str(plan.get("generate") or "").strip()
    model = resolve_engine(st, str(plan.get("model") or ""), engine)
    st["engine"] = model
    st["say"] = say
    _save(st)
    # Speak first so Cosy is in the air while Comfy draws.
    speak_lila(say)
    out: dict[str, Any] = {
        "ok": True,
        "say": say,
        "lila": plan,
        "still": still_basename(st.get("still")),
        "still_url": None,
        "generated": False,
        "spoke": _voice_on(),
    }
    if out["still"]:
        out["still_url"] = f"/render-still/{out['still']}"
    kind = "t2i"
    for e in studio_engine_list():
        if e["id"] == model:
            kind = str(e.get("kind") or "t2i")
            if not e.get("wired"):
                st["busy"] = ""
                _save(st)
                line = f"{e.get('label') or model} is not wired yet."
                speak_lila(line)
                out.update({"ok": False, "error": "not wired", "say": line})
                return out
            break
    if gen:
        spec = next_spec(spec, user_text, generated=True)
        if kind in ("i2v", "t2v"):
            still_src = str(last) if last and last.is_file() else ""
            if kind == "i2v" and not still_src:
                line = "Give me a still first, then we can move it."
                speak_lila(line)
                st["busy"] = ""
                _save(st)
                out.update({"ok": False, "error": "WAN needs a still", "say": line})
                return out
            _set_busy("drawing")
            comfy = rc._load_comfy()
            vid = comfy.HouseComfyVideo().generate(
                prompt=gen, model=model, image_url=still_src or None
            )
            vpath = str((vid or {}).get("video") or "")
            vb = video_basename(vpath)
            st["busy"] = ""
            if not vb:
                err = str((vid or {}).get("error") or "video failed")[:160]
                speak_lila("That clip didn't land.")
                _save(st)
                out.update({"ok": False, "error": err, "say": "That clip didn't land."})
                return out
            st["video"] = str(OUT_DIR / vb)
            st["spec"] = spec
            st["generated"] = True
            st["say"] = (say + " Here's the clip.").strip()[:400]
            _save(st)
            speak_lila("Here's the clip.")
            out.update(
                {
                    "generated": True,
                    "video": vb,
                    "video_url": f"/render-video/{vb}",
                    "say": st["say"],
                }
            )
            out["present"] = present(video=vb)
            return out
        _set_busy("drawing")
        comfy = rc._load_comfy()
        path = rc.generate(comfy, gen, model, "landscape")
        _set_busy("looking")
        lila_j = rc._json_from(
            rc._see(
                path,
                LILA_LOOK,
                f"SPEC:\n{spec}\nUSER this turn:\n{user_text}\nYou just made this. Look.",
                url=rc.CREW,
                model="crew",
            )
        )
        vera_j = rc._json_from(
            rc._see(
                path,
                VERA_LOOK,
                f"YOUR REFERENCE:\n{spec}\nSPEC (User law):\n{spec}\n"
                f"LILA:\n{json.dumps(lila_j, ensure_ascii=False)}",
                url=rc.CREW,
                model="crew",
            )
        )
        st["vera_ref"] = apply_vera_ref(st.get("vera_ref") or spec, spec, vera_j)
        taste = str(lila_j.get("taste") or lila_j.get("note") or "").strip()
        if vera_j.get("pass"):
            wrap = (taste or "I'd hang this.") + " Vera would put it on the wall."
        else:
            wrap = taste or str(vera_j.get("why") or "Another pass.")
        wrap = wrap.strip()[:400]
        speak_lila(wrap, wait_previous=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        st.update(
            {
                "spec": spec,
                "still": str(path),
                "cue": CUE,
                "prompt": gen,
                "model": model,
                "engine": model,
                "ts": ts,
                "say": (say + " " + wrap).strip()[:400],
                "lila": lila_j,
                "vera": vera_j,
                "generated": True,
                "busy": "",
            }
        )
        _append_turn(
            st,
            {
                "ts": ts,
                "user": user_text,
                "say": say,
                "wrap": wrap,
                "still": path.name,
                "vera_pass": bool(vera_j.get("pass")),
            },
        )
        _save(st)
        rc._log(
            {
                "ts": ts,
                "studio": True,
                "still": str(path),
                "lila": lila_j,
                "vera": vera_j,
                "vera_ref": st.get("vera_ref"),
                "user": user_text,
                "engine": model,
                "card": st.get("card"),
            }
        )
        if taste and taste not in say:
            say = (say + " " + taste).strip()
        out.update(
            {
                "say": say[:400],
                "wrap": wrap,
                "still": path.name,
                "still_url": f"/render-still/{path.name}",
                "still_path": str(path),
                "vera": vera_j,
                "lila_look": lila_j,
                "generated": True,
                "vera_ref": st.get("vera_ref"),
                "engine": model,
                "card": st.get("card"),
            }
        )
        out["present"] = present(still=str(path))
    else:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        st["spec"] = spec
        st["say"] = say
        st["generated"] = False
        st["busy"] = ""
        _append_turn(st, {"ts": ts, "user": user_text, "say": say, "still": out.get("still")})
        _save(st)
    out["vera_ref"] = st.get("vera_ref")
    out["engine"] = resolve_engine(st)
    out["card"] = st.get("card")
    out["hung"] = bool(st.get("hung"))
    return out
