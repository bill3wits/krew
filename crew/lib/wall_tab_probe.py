"""Does Crissa's wall page still answer while Brave IS the wall? Report only (the User 2026-09-30, 10-02).

09-30: her display tab's renderer died and nobody knew until a display call stood silent for 30 s. Every check
that existed passed (the unit, CDP's tab list, :8099); only a call the page itself must answer tells a live page
from a dead one, so this asks the page one thing - `Runtime.evaluate("1")`.

THE MODE, READ THE CONTROL PANEL'S WAY (`glass_mode.read_mode`, the User 2026-10-02):
  Wallpaper ON   the wallpaper is the glass; a missing or dead wall tab is expected. Nothing is probed or filed.
  Crissa off     nothing stays open by design. Nothing is probed or filed.
  Wallpaper OFF  (mode `brave`) Brave is the wall: the page is asked, and a dead one is reported.

  ok          the page answered
  dead        the tab says its renderer crashed (Inspector.targetCrashed), or it did not answer
              in ANSWER_S on STRIKES ticks running
  no-browser  CDP is not up
  no-tab      the browser has no wall page (her wall page is its port AND its root path - krew and the
              Control Panel share the port, and a dead tools tab is never reported as her wall)
  parked      Wallpaper on, or Crissa off

IT NEVER RELOADS, NAVIGATES, RAISES OR RESTARTS ANYTHING, and its card asks no one to. On `dead` it files ONE
card per episode, assigned to Crissa (kanban.never_dispatch: no worker ever takes it) and blocked on her
(`needs_input`), for her or the User. When the page answers again the probe comments and closes its own card.
Under Wallpaper ON its old card went to ada saying "show_me home reloads it": Crissa ruled `try`, a worker
reloaded the wall, and the reloaded renderer died again - the reload loop it now cannot start.

Kill `KREW_WALL_TAB_PROBE=0` or `~/.hermes/wall-tab-probe.off`; cards only: `KREW_WALL_TAB_PROBE_CARDS=0`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path

import urllib.request
from types import SimpleNamespace
import krew_hermes as KH  # noqa: E402  where Hermes lives

# krew: the house read the glass mode, the tab list and its state home through the Wall's own lib/glass_mode,
# lib/show_glass and lib/crissa_home. krew reads the mode file's first word (KREW_GLASS_MODE_FILE; unreadable = off),
# the browser's CDP list (KREW_CDP_URL) and $KREW_HOME, under the same two names so the rules below read as written.
KREW_HOME = Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))
_CDP = (os.environ.get("KREW_CDP_URL") or "http://127.0.0.1:9222").rstrip("/")


def _read_mode() -> str:
    try:
        return (glass_mode.PATH.read_text(encoding="utf-8").split() or ["off"])[0]
    except OSError:
        return "off"


def _cdp_get(path: str):
    with urllib.request.urlopen(_CDP + path, timeout=3) as r:    # noqa: S310 - a local URL
        return json.loads(r.read().decode("utf-8") or "null")


glass_mode = SimpleNamespace(PATH=Path(os.path.expanduser(os.environ.get("KREW_GLASS_MODE_FILE") or str(KH.hermes_home() / "krew-glass-mode"))),
                             read_mode=_read_mode, is_active=lambda unit: False, is_enabled=lambda unit: False)
show_glass = SimpleNamespace(HOME_PORT=int(os.environ.get("KREW_WALL_TAB_PORT") or 8099), _get=_cdp_get, GLASS_FLAG=None)

STATE = KREW_HOME / "wall-tab-probe.json"
OFF = KREW_HOME / "wall-tab-probe.off"
HERMES = os.environ.get("HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
# THE CARD IS CRISSA'S OR THE USER'S, NEVER A WORKER'S (the User 2026-10-02). It was filed to ada with
# "show_me home reloads it", Crissa ruled `try`, an ada worker reloaded the wall, and the reloaded renderer died
# again: the reload loop (t_33b03def, t_2a7b4b61, t_f77e1dab). Crissa is in kanban.never_dispatch, so a card
# assigned to her is never handed to a worker; it waits blocked in her Decide queue, and she or the User rules.
PROFILE = "crissa"
ASSIGNEE = "crissa"
AUTHOR = "wall-tab-probe"
ANSWER_S = 3.0
STRIKES = 2
ASK = "Wall tab dead while Brave is the wall (Wallpaper off): look, or park"
NOT_A_FAULT = ("ok", "no-browser", "no-tab", "parked", "off")


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    if str(e.get("KREW_WALL_TAB_PROBE", "1")).strip().lower() in ("0", "false", "no", "off"):
        return False
    return not OFF.exists()


def cards_enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_WALL_TAB_PROBE_CARDS", "1")).strip().lower() not in ("0", "false", "no", "off")


# ── the one question ────────────────────────────────────────────────────────

async def _ask(ws_url: str, wait: float) -> dict:
    """{answered, crashed}: one evaluate; a crashed target announces itself on attach."""
    import websockets
    crashed = False
    async with websockets.connect(ws_url, max_size=2_000_000, open_timeout=wait) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Inspector.enable", "params": {}}))
        await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                                  "params": {"expression": "1", "returnByValue": True}}))
        end = time.monotonic() + wait
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return {"answered": False, "crashed": crashed}
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=left))
            except asyncio.TimeoutError:
                return {"answered": False, "crashed": crashed}
            if msg.get("method") == "Inspector.targetCrashed":
                return {"answered": False, "crashed": True}       # certain: no need to wait out the answer
            elif msg.get("id") == 2:
                return {"answered": "result" in msg, "crashed": crashed}


def ask(ws_url: str, wait: float = ANSWER_S) -> dict:
    return asyncio.run(_ask(ws_url, wait))


def _has_websockets() -> bool:
    try:
        import websockets  # noqa: F401
        return True
    except Exception:
        return False


WALL_PORT = int(os.environ.get("KREW_WALL_TAB_PORT", show_glass.HOME_PORT))
WALL_PATH = os.environ.get("KREW_WALL_TAB_PATH", "/")


def _is_wall(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url or "")
        return u.port == WALL_PORT and (u.path or "/") == WALL_PATH
    except ValueError:
        return False


def _her_tab(pages: list[dict]) -> dict | None:
    """The page this probe judges: her wall page itself - its port AND its root path (the User 2026-10-02).
    Krew and the Control Panel share the port; a dead tools tab is not her wall, so it never gets a
    "wall tab is dead" card (show_glass.pick_tab took any :8099 page, and t_f77e1dab was filed that way)."""
    return next((t for t in pages if _is_wall(t.get("url") or "")), None)


def probe() -> dict:
    """One reading. Never raises; never writes to the browser.

    `websockets` is probed up front: without it the question can never be asked, and a
    module-less interpreter must NOT read as a dead page (2026-10-02: its missing module
    filed a false-dead card, t_77bfb092, against a browser whose tab list was answering).
    """
    if not enabled():
        return {"verdict": "off"}
    if not _has_websockets():
        return {"verdict": "no-browser",
                "why": "probe-side: no websockets module (OS package python3-websockets — "
                       "never a venv/uv, DEPS-PINNED 2026-10-02; then re-tick)"}
    mode = glass_mode.read_mode()          # the Control Panel's own reader (the User 2026-10-02)
    if mode != "brave":
        # Wallpaper ON: the wallpaper is the glass, so a missing or dead wall tab is expected - nothing to file.
        # Crissa off: nothing stays open by design.
        return {"verdict": "parked", "why": f"glass is {mode}: a missing or dead wall tab is expected"}
    try:
        pages = [t for t in (show_glass._get("/json/list") or []) if t.get("type") == "page"]
    except Exception as e:  # noqa: BLE001 - a closed browser is not this probe's fault to report
        return {"verdict": "no-browser", "why": f"{type(e).__name__}"}
    tab = _her_tab(pages)
    if not tab or not tab.get("webSocketDebuggerUrl"):
        return {"verdict": "no-tab", "why": f"{len(pages)} page(s), none of hers"}
    t0 = time.monotonic()
    try:
        got = ask(tab["webSocketDebuggerUrl"])
    except Exception as e:  # noqa: BLE001 - could not even attach: count it as no answer
        got = {"answered": False, "crashed": False, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    ms = int((time.monotonic() - t0) * 1000)
    base = {"tab": str(tab.get("id") or "")[:8], "href": tab.get("url") or "", "ms": ms}
    if got.get("answered") and not got.get("crashed"):
        return dict(base, verdict="ok")
    why = ("its renderer crashed (Inspector.targetCrashed)" if got.get("crashed")
           else got.get("error") or f"the page did not answer Runtime.evaluate in {ANSWER_S:.0f} s")
    return dict(base, verdict="dead", why=why, crashed=bool(got.get("crashed")))


# ── what to do with a reading (pure) ────────────────────────────────────────

def step(state: dict, reading: dict, now: float) -> tuple[dict, str]:
    """(new state, act) where act is '' | 'file' | 'recovered'."""
    st = dict(state or {})
    st["last"] = dict(reading, at=now)
    st["beat"] = now
    ep = st.get("episode")
    if reading.get("verdict") == "dead":
        st["strikes"] = int(st.get("strikes") or 0) + 1
        st.setdefault("dead_since", now)
        if not ep and (reading.get("crashed") or st["strikes"] >= STRIKES):
            return st, "file"
        return st, ""
    st["strikes"] = 0
    st.pop("dead_since", None)
    if ep and reading.get("verdict") == "ok":
        return st, "recovered"
    return st, ""


def card_title(reading: dict) -> str:
    return "Wall tab is dead while Brave is the wall: her page does not answer"


def card_body(reading: dict, state: dict) -> str:
    since = time.strftime("%H:%M:%S", time.localtime(float(state.get("dead_since") or time.time())))
    return "\n".join([
        "Wallpaper is off, so Crissa's Brave is the wall. Its tab list answers, but her wall page does not.",
        "This probe reports only; it reloaded nothing.",
        "",
        "source: wall-tab-probe (lib/wall_tab_probe.py), glass mode brave (Wallpaper off)",
        f"cause: {reading.get('why')}",
        f"evidence: tab {reading.get('tab')} at {reading.get('href')}; dead since {since}; "
        f"{int(state.get('strikes') or 0)} probe(s) without an answer; last wait {reading.get('ms')} ms",
        "",
        "For Crissa or the User: look at why the page died before anything brings it back. Brave's own",
        "reason for the end is in ~/.hermes/zos-brave-target-ends.jsonl. `park` if the wall is meant to be",
        "down. This card is never a worker's: bringing the page back has brought the same death back before.",
    ])


# ── state + the board ───────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        got = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE)


def kanban(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run([HERMES, "-p", PROFILE, "kanban", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def file_card(reading: dict, state: dict) -> str:
    """One card per episode, assigned to Crissa (never dispatched) and blocked on her. '' when it could not be
    filed AND blocked."""
    key = f"wall-tab-dead:{int(float(state.get('dead_since') or time.time()))}"
    rc, out = kanban("create", card_title(reading), "--assignee", ASSIGNEE, "--body", card_body(reading, state)[:3500],
                     "--created-by", AUTHOR, "--idempotency-key", key)
    m = re.search(r"\b(t_[0-9a-f]{8})\b", out)
    if rc != 0 or not m:
        return ""
    tid = m.group(1)
    rc2, _ = kanban("block", tid, ASK, "--kind", "needs_input")     # reason FIRST (hermes is Python 3.11)
    if rc2 != 0:
        kanban("archive", tid)                                       # never leave it where a worker takes it
        return ""
    return tid


def close_card(ep: dict, now: float) -> None:
    """The page answers again: the question on the card is moot, so the probe closes its own report
    (a blocked card would sit in Crissa's Decide queue asking about a fault that is gone). hermes wants
    a summary as evidence; if the close is refused the comment alone still tells her."""
    mins = max(0, int((now - float(ep.get("since") or now)) // 60))
    said = ("the page answers again at " + time.strftime("%H:%M:%S", time.localtime(now))
            + f" after about {mins} min dead (not reloaded by this probe). Was: {ep.get('why')}.")
    kanban("comment", ep["card"], "wall-tab-probe: " + said, "--author", AUTHOR)
    kanban("complete", ep["card"], "--summary", "Reported, then recovered: " + said)


def tick() -> dict:
    """Probe once, keep the count, file or close the episode. Returns what happened."""
    reading = probe()
    if reading.get("verdict") == "off":
        return {"verdict": "off"}
    now = time.time()
    st, act = step(load_state(), reading, now)
    out = dict(reading, strikes=st.get("strikes", 0), act=act)
    if act == "file":
        tid = file_card(reading, st) if cards_enabled() else ""
        st["episode"] = {"since": st.get("dead_since", now), "card": tid, "why": reading.get("why")}
        out["card"] = tid or ("cards off" if not cards_enabled() else "NOT FILED")
    elif act == "recovered":
        ep = st.pop("episode", {}) or {}
        if ep.get("card"):
            close_card(ep, now)
        out["card"] = ep.get("card") or ""
    save_state(st)
    return out
