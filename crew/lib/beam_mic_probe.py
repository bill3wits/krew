"""Is the room's beam mic still there, and does the ear still get a beam? Report only (the User 2026-10-01).

The Seeed reSpeaker XVF3800 was unplugged from 09-30 10:02 to 10-01 02:05 and nothing said so: rail-stt fell back
to another mic without a log line, and the beam angle the wall and the speaker stack read went null. Each tick
reads three things and writes them down:

  source   PipeWire still lists the Seeed input (`pactl list short sources`, an `alsa_input` whose name contains
           BEAM_MIC_SOURCE_MATCH, default `Seeed_Studio_reSpeaker`)
  beam     rail-stt :8190/health doa_loop.last_az and :8099/v1/sound az_deg - a beam is FRESH when either carries
           a number from the last BEAM_STALE_S; `null` when both are null or older (the DOA loop posts at 5 Hz,
           quiet room included, so a live mic never goes null for minutes)
  ear_mic  which mic rail-stt is listening on (`ear.mic`) - evidence of the silent fallback, not a trigger

A FAULT is the source missing or the beam null. A fault that lasts GRACE_S (5 min) opens ONE card per episode,
blocked on Crissa (`needs_input`): replugging a mic is the User's hands, and she is who asks him. When the source is
back and the beam fresh, the probe comments and closes its own card. An unreadable reading (pactl or both HTTP
endpoints down) is `unknown`: written down, never a fault, never clears one.

IT NEVER RESTARTS, RELOADS OR CHANGES ANYTHING. Kill `KREW_BEAM_MIC_PROBE=0` or ~/.hermes/beam-mic-probe.off;
cards only: `KREW_BEAM_MIC_PROBE_CARDS=0`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

HOME = KH.hermes_home()
STATE = HOME / "beam-mic-probe.json"
OFF = HOME / "beam-mic-probe.off"
LOG = HOME / "logs" / "beam-mic-probe.jsonl"
HERMES = os.environ.get("HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
PROFILE = "ada"
AUTHOR = "beam-mic-probe"
# krew: the ear's health is read through the Wall's player (CRISSA_PLAYER, default http://127.0.0.1:8100,
# GET /v1/ear?part=stt wraps the stt health under "stt"); the feed's /v1/sound is on the feed port (8099,
# KREW_PORT_8099 moves it). BEAM_MIC_STT_HEALTH / BEAM_MIC_WALL_SOUND name either directly.
_PLAYER = (os.environ.get("CRISSA_PLAYER") or "http://127.0.0.1:8100").rstrip("/")
STT_HEALTH = os.environ.get("BEAM_MIC_STT_HEALTH", _PLAYER + "/v1/ear?part=stt")
WALL_SOUND = os.environ.get("BEAM_MIC_WALL_SOUND",
                            f"http://{os.environ.get('KREW_LOCAL_HOST') or '127.0.0.1'}:{os.environ.get('KREW_PORT_8099') or 8099}/v1/sound")
# the beam mic's PipeWire source name (a per-machine value; the reference install's reSpeaker is the default)
SOURCE_MATCH = os.environ.get("KREW_BEAM_MIC_SOURCE") or os.environ.get("BEAM_MIC_SOURCE_MATCH", "Seeed_Studio_reSpeaker")
GRACE_S = float(os.environ.get("BEAM_MIC_GRACE_S", "300"))
BEAM_STALE_S = float(os.environ.get("BEAM_MIC_STALE_S", "120"))
ASK = "Room beam mic missing or no beam: replug the Seeed (User's hands) | park?"


def _off(name: str, env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(name, "1")).strip().lower() in ("0", "false", "no", "off")


def enabled(env: dict | None = None) -> bool:
    return not _off("KREW_BEAM_MIC_PROBE", env) and not OFF.exists()


def cards_enabled(env: dict | None = None) -> bool:
    return not _off("KREW_BEAM_MIC_PROBE_CARDS", env)


# ── the readings ────────────────────────────────────────────────────────────

def _get_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            d = json.load(r)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001 - unreadable is a reading of its own
        return None


def sources() -> list[str] | None:
    """PipeWire's input sources, or None when pactl cannot answer (a one-shot list, never `subscribe`)."""
    try:
        p = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=8)
    except Exception:  # noqa: BLE001
        return None
    if p.returncode != 0:
        return None
    return [ln.split("\t")[1] for ln in p.stdout.splitlines() if "\t" in ln]


def beam_fresh(stt: dict | None, wall: dict | None, now: float) -> bool | None:
    """True = an angle from the last BEAM_STALE_S; False = null/stale in every source we could read;
    None = neither endpoint answered."""
    seen = []
    if stt is not None:
        doa = stt.get("doa_loop") or {}
        az, ts = doa.get("last_az"), doa.get("last_pub_ts")
        seen.append(az is not None and ts is not None and now - float(ts) <= BEAM_STALE_S)
    if wall is not None:
        snd = wall.get("sound") or {}
        az, ts = snd.get("az_deg"), snd.get("ts")
        seen.append(az is not None and ts is not None and now - float(ts) <= BEAM_STALE_S)
    if not seen:
        return None
    return any(seen)


def probe(now: float | None = None, *, srcs=..., stt=..., wall=...) -> dict:
    """One reading. The keyword arguments are for tests (... = read the live house)."""
    now = time.time() if now is None else now
    if not enabled():
        return {"verdict": "off"}
    srcs = sources() if srcs is ... else srcs
    stt = _get_json(STT_HEALTH) if stt is ... else stt
    if isinstance(stt, dict) and "doa_loop" not in stt and isinstance(stt.get("stt"), dict):
        stt = stt["stt"]              # the player's /v1/ear?part=stt wraps the ear's own health
    wall = _get_json(WALL_SOUND) if wall is ... else wall
    source = None if srcs is None else any(s.startswith("alsa_input.") and SOURCE_MATCH in s for s in srcs)
    beam = beam_fresh(stt, wall, now)
    ear = ((stt or {}).get("ear") or {}).get("mic")
    faults = []
    if source is False:
        faults.append("source missing")
    if beam is False:
        faults.append("beam null")
    if faults:
        verdict = "fault"
    elif source is None and beam is None:
        verdict = "unknown"
    elif source is True and beam is True:
        verdict = "ok"
    else:
        verdict = "unknown"                      # one side unreadable, the other fine: not proof of either
    return {"verdict": verdict, "faults": faults, "source": source, "beam": beam, "ear_mic": ear,
            "seeed_inputs": [s for s in (srcs or []) if SOURCE_MATCH in s and s.startswith("alsa_input.")]}


# ── the episode ─────────────────────────────────────────────────────────────

def step(state: dict, reading: dict, now: float) -> tuple[dict, str]:
    """(new state, act) where act is '' | 'file' | 'recovered'. A fault must last GRACE_S before a card."""
    st = dict(state or {})
    st["last"] = dict(reading, at=now)
    st["beat"] = now
    ep = st.get("episode")
    v = reading.get("verdict")
    if v == "fault":
        st.setdefault("fault_since", now)
        st["faults_seen"] = sorted(set(st.get("faults_seen") or []) | set(reading.get("faults") or []))
        if not ep and now - float(st["fault_since"]) >= GRACE_S:
            return st, "file"
        return st, ""
    if v == "ok":
        st.pop("fault_since", None)
        st.pop("faults_seen", None)
        if ep:
            return st, "recovered"
    return st, ""                                 # unknown / off: keep whatever was running


def card_title(reading: dict, state: dict) -> str:
    what = " and ".join(state.get("faults_seen") or reading.get("faults") or ["fault"])
    return f"Room beam mic: {what} for 5+ min (rail-stt fell back silently)"


def card_body(reading: dict, state: dict) -> str:
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(state.get("fault_since") or time.time())))
    return "\n".join([
        "The room's beam mic (Seeed reSpeaker XVF3800) is missing or the ear gets no beam angle.",
        "When it went on 09-30 10:02 nothing logged it for 16 h: rail-stt fell back to another mic and the beam",
        "the wall and the speaker stack read went null. This probe reports only; it changed nothing.",
        "",
        "source: beam-mic-probe (lib/beam_mic_probe.py), every minute",
        f"cause: {', '.join(state.get('faults_seen') or reading.get('faults') or [])} since {since}",
        f"evidence: Seeed input listed by PipeWire: {reading.get('source')} {reading.get('seeed_inputs') or ''}; "
        f"beam angle fresh: {reading.get('beam')}; rail-stt listening on: {reading.get('ear_mic')}",
        "",
        "Physical: the User replugs the Seeed USB mic (NEEDS HUMAN). Crissa: ask him once; `park` if he unplugged",
        "it on purpose. The probe closes this card itself when the source is back and the beam is fresh.",
    ])


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
    """One card, blocked on Crissa. '' when it could not be filed AND blocked."""
    key = f"beam-mic-fault:{int(float(state.get('fault_since') or time.time()))}"
    rc, out = kanban("create", card_title(reading, state), "--assignee", PROFILE,
                     "--body", card_body(reading, state)[:3500], "--created-by", AUTHOR, "--idempotency-key", key)
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
    mins = max(0, int((now - float(ep.get("since") or now)) // 60))
    said = ("the Seeed source is back and the beam is fresh at " + time.strftime("%H:%M:%S", time.localtime(now))
            + f", after about {mins} min. Was: {', '.join(ep.get('faults') or [])}.")
    kanban("comment", ep["card"], "beam-mic-probe: " + said, "--author", AUTHOR)
    kanban("complete", ep["card"], "--summary", "Reported, then recovered: " + said)


def _log(row: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def tick() -> dict:
    """Probe once, keep the episode, file or close its card. Returns what happened."""
    now = time.time()
    reading = probe(now)
    if reading.get("verdict") == "off":
        return {"verdict": "off"}
    st, act = step(load_state(), reading, now)
    out = dict(reading, act=act, fault_since=st.get("fault_since"))
    if act == "file":
        tid = file_card(reading, st) if cards_enabled() else ""
        st["episode"] = {"since": st.get("fault_since", now), "card": tid, "faults": st.get("faults_seen")}
        out["card"] = tid or ("cards off" if not cards_enabled() else "NOT FILED")
    elif act == "recovered":
        ep = st.pop("episode", {}) or {}
        if ep.get("card"):
            close_card(ep, now)
        out["card"] = ep.get("card") or ""
    save_state(st)
    _log(dict(out, ts=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))))
    return out
