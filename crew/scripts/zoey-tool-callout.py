#!/usr/bin/env python3
"""Speak a short English line when a crew profile starts a tool.

Hermes pre_tool_call hook. Never blocks the tool (timeout 3s). Never says
snake_case names. Mouth is that profile's voice pin (tts.<provider>.voice):
Zoey, Ada/orchestrator, Lila, Vera, Bash, Steward. Callouts queue behind
whoever is already on the speaker. Voice master OFF -> silent.
team_speak / announce -> skip (no loop).

krew (lifted from the reference install, 2026-10-09): the line is spoken through the Crissa Wall's player
(ZOEYOS_PLAYER, POST /v1/announce: the player's own queue waits for quiet, so a callout never cuts her), never a
speech script on this machine; the voice master, the mute, the dedup stamps, the queue and the roll live under
$KREW_HOME (voice.json: {"voice": "on"|"off", "tool_callout": "info"|"on"|"off"}; no file = voice on, info);
the mouth-busy gate file is the player's (ZOEYOS_HOME/tts-gate.json when the Wall writes one). Installed as a
pre_tool_call hook on every crew profile by crew/scripts/krew-crew-install.py --profiles (profile-kit.json).
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _krew_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


def _zoeyos_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("ZOEYOS_HOME") or "~/.zoeyos"))


PLAYER = (os.environ.get("ZOEYOS_PLAYER") or "http://127.0.0.1:8100").rstrip("/")
VOICE_LOCK = Path(os.path.expanduser(os.environ.get("KREW_VOICE_LOCK", str(_krew_home() / "voice.json"))))
MUTE = _krew_home() / "announce-mute"
# Next to the roll: /tmp is not shared across every Hermes helper that
# writes the tool roll (measured 2026-08-21: same phrase
# every 3-9s in the roll while the /tmp stamp was a different namespace).
STAMP = _krew_home() / "zoey-tool-callout.last"
TOOL_ROLL = _krew_home() / "zoey-tool-roll.jsonl"
QUEUE = _krew_home() / "zoey-tool-callout.queue"
WAIT_LOCK = _krew_home() / "zoey-tool-callout.wait.lock"
TTS_GATE = Path(os.path.expanduser(
    os.environ.get("KREW_TTS_GATE", str(_zoeyos_home() / "tts-gate.json"))
))
HERMES_DIR = Path(os.path.expanduser(os.environ.get("KREW_HERMES") or os.environ.get("HERMES_HOME") or "~/.hermes"))
PROFILES = HERMES_DIR / "profiles"
ZOEY_CFG = PROFILES / "zoey" / "config.yaml"
BURST_S = 8.0
PHRASE_S = 12.0
MEMORY_S = 20.0
# After her last syllable: don't clip, then overlap.
TAIL_S = 0.4
# One callout waiting on a long reply; stale after this.
WAIT_MAX = 90.0
# The reviewer's voice family is never Zoey's mouth.
_NOT_ZOEY = frozenset({"zoe", "vera", "reviewer"})
_PROFILE_VOICE = {
    "zoey": "zoey",
    "ada": "ada",
    "orchestrator": "ada",
    "lila": "lila",
    "vera": "vera",
    "bash": "bash",
    "steward": "steward",
}


def _profile() -> str:
    for key in ("HERMES_PROFILE", "HERMES_AGENT_PROFILE"):
        v = str(os.environ.get(key) or "").strip().lower()
        if v in _PROFILE_VOICE:
            return v
    try:
        p = Path.cwd().resolve()
        parts = p.parts
        if "profiles" in parts:
            i = parts.index("profiles")
            if i + 1 < len(parts) and parts[i + 1] in _PROFILE_VOICE:
                return parts[i + 1]
    except Exception:
        pass
    return "zoey"


def _yaml_voice(profile: str) -> str:
    """The profile's voice pin: tts.<provider>.voice for the provider its config names, else tts.voice."""
    cfg = PROFILES / profile / "config.yaml"
    if profile == "zoey" and ZOEY_CFG.is_file():
        cfg = ZOEY_CFG
    try:
        try:
            import yaml
            d = yaml.safe_load(cfg.read_text()) or {}
        except ImportError:
            from ruamel.yaml import YAML
            d = YAML(typ="safe").load(cfg.read_text()) or {}
        tts = d.get("tts") or {}
        prov = str(tts.get("provider") or "").strip()
        block = tts.get(prov) if prov else None
        if isinstance(block, dict) and block.get("voice"):
            return str(block.get("voice")).strip().lower()
        return str(tts.get("voice") or "").strip().lower()
    except Exception:
        return ""


def _callout_voice(profile: str | None = None) -> str:
    """Voice pin for this worker. Zoey never borrows the reviewer's."""
    who = (profile or _profile()).strip().lower() or "zoey"
    if who == "zoey":
        pin = (os.environ.get("ZOEY_CALLOUT_VOICE") or "").strip().lower()
        pin = pin or _yaml_voice("zoey")
        if not pin or pin in _NOT_ZOEY:
            pin = "zoey"
        return pin
    pin = _yaml_voice(who) or _PROFILE_VOICE.get(who, "")
    return pin or _PROFILE_VOICE.get(who, "zoey")


def _zoey_voice() -> str:
    """Back-compat: Zoey's pin."""
    return _callout_voice("zoey")

# English only. Keys are prefixes (web_ -> any web_*). Board tools are SKIP:
# kanban workers were a loop of "Checking the board." / "Unblocking a card."
SAY = (
    ("team_presence", "Looking at the room."),
    ("team_ear", "Checking the ear."),
    ("team_look", "Taking a look."),
    ("team_compose", "A beat."),
    ("web_", "Searching the web."),
    ("search_web", "Searching the web."),
    ("vision", "Looking at a picture."),
    ("memory", "Checking memory."),
    ("hindsight", "Checking memory."),
    ("read_file", "Reading a file."),
    ("search_files", "Searching files."),
    ("browser", "Opening the browser."),
)
SKIP = {
    "team_speak",
    "team_announce",
    "team_display",
    "show_me",
    "team_time",
    "tool_search",
    "tool_describe",
    "tool_call",
    "terminal",
    "execute_code",
    "delegate_task",
    "hands",
    "kanban",
    "team_board",
}


def _voice_lock() -> dict:
    try:
        return json.loads(VOICE_LOCK.read_text())
    except Exception:
        return {}


def _voice_on() -> bool:
    if MUTE.is_file():
        return False
    raw = str(_voice_lock().get("voice") or "on").strip().lower()
    return raw in (
        "on",
        "1",
        "true",
        "yes",
    )


def _callout_info() -> bool:
    """Info-only spoken tool lines. Default on when voice is on.

    voice.json ``tool_callout``: info | on | off
    """
    raw = str(_voice_lock().get("tool_callout") or "info").strip().lower()
    if raw in ("0", "false", "no", "off", "silent"):
        return False
    return raw in ("info", "on", "1", "true", "yes", "")


def _phrase(name: str) -> str | None:
    n = (name or "").strip()
    if not n:
        return None
    low = n.lower()
    if low in SKIP or low.startswith("hands") or low.startswith("kanban"):
        return None
    for key, line in SAY:
        if low == key or low.startswith(key):
            return line
    return None


def _norm(s: str) -> str:
    return " ".join((s or "").strip().split()).casefold()


def _parse_stamp(raw: str) -> tuple[float, str, str]:
    """ts, tool name, phrase. Old format was ``ts\\tline``."""
    parts = (raw or "").strip().split("\t")
    ts = 0.0
    name = ""
    line = ""
    if parts:
        try:
            ts = float(parts[0])
        except ValueError:
            ts = 0.0
    if len(parts) >= 3:
        name = parts[1].strip().lower()
        line = _norm(parts[2])
    elif len(parts) == 2:
        line = _norm(parts[1])
    return ts, name, line


def _gate() -> dict:
    try:
        g = json.loads(TTS_GATE.read_text(encoding="utf-8"))
        return g if isinstance(g, dict) else {}
    except Exception:
        return {}


def _on_air(line: str) -> bool:
    """The voice is already saying this exact line (Hermes TTS or a live player line)."""
    g = _gate()
    text = str(g.get("last_reply") or "").strip()
    if _norm(text) != _norm(line):
        return False
    if g.get("speaking"):
        return True
    try:
        age = time.time() - float(g.get("updated_ts") or 0)
    except (TypeError, ValueError):
        return False
    return 0 <= age < BURST_S


def _mouth_busy() -> bool:
    """Her mouth is open: any line, not just this callout.

    Gate speaking=true plus a short tail. Callouts must wait; they must not
    start a second line over her. (The player's announce lane waits for quiet
    on its own as well; this is the belt in front of it.)
    """
    g = _gate()
    if str(g.get("notice") or "").strip().lower() == "interrupted":
        return False
    if g.get("speaking"):
        return True
    try:
        age = time.time() - float(g.get("updated_ts") or 0)
    except (TypeError, ValueError):
        age = 1e9
    if 0 <= age < TAIL_S:
        return True
    return _speaker_running()


def _speaker_running() -> bool:
    """A speech helper of this machine's own is synthesizing (not this waiter). The Wall's player speaks for
    krew, in its own process, so there is none to find here: False."""
    return False


def _with_queue(fn):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(QUEUE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            raw += chunk
        lines = [ln for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
        out = fn(lines)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        if out:
            os.write(fd, ("\n".join(out) + "\n").encode())
        return out
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _enqueue(line: str, voice: str = "") -> bool:
    """Append if not already queued. Returns True if the line is on the queue."""
    phrase = (line or "").strip()
    if not phrase:
        return False
    pin = (voice or _callout_voice()).strip().lower() or "zoey"
    want = f"{pin}\t{phrase}"
    got = {"ok": False}

    def add(lines: list[str]) -> list[str]:
        norms = {_norm(x) for x in lines}
        if _norm(want) in norms:
            got["ok"] = True
            return lines
        got["ok"] = True
        return lines + [want]

    try:
        _with_queue(add)
    except OSError:
        return False
    return bool(got["ok"])


def _pop() -> str:
    held: list[str] = [""]

    def take(lines: list[str]) -> list[str]:
        if not lines:
            return []
        held[0] = lines[0]
        return lines[1:]

    try:
        _with_queue(take)
    except OSError:
        return ""
    return held[0]


def _clear_queue() -> None:
    try:
        _with_queue(lambda _lines: [])
    except OSError:
        pass


def _interrupted() -> bool:
    return str(_gate().get("notice") or "").strip().lower() == "interrupted"


def _ensure_waiter() -> None:
    """One drain process. Hook stays non-blocking (Hermes timeout 3s)."""
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--drain"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _speak_now(item: str) -> None:
    """One callout to the Wall's player: POST /v1/announce {text, voice, kind: callout}. The player queues it
    behind whatever is on the air and waits for quiet; a player that is not there is silence, never an error."""
    if not item:
        return
    voice, line = "zoey", item
    if "\t" in item:
        voice, line = item.split("\t", 1)
        voice = (voice or "zoey").strip().lower() or "zoey"
        line = line.strip()
    if not line:
        return
    try:
        import urllib.request
        body = json.dumps({"text": line, "voice": voice, "kind": "callout", "by": "zoey-tool-callout",
                           "dedup_key": "callout:" + _norm(line), "max_wait_s": WAIT_MAX}).encode()
        req = urllib.request.Request(PLAYER + "/v1/announce", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=120).read()
    except Exception:
        pass


def drain() -> int:
    """Play queued callouts only when her mouth is free. Exclusive waiter."""
    WAIT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(WAIT_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return 0
    try:
        idle = 0
        while True:
            if _interrupted():
                _clear_queue()
                return 0
            line = _pop()
            if not line:
                idle += 1
                if idle > 6:
                    return 0
                time.sleep(0.25)
                continue
            idle = 0
            t0 = time.time()
            while _mouth_busy() or _speaker_running():
                if _interrupted():
                    _clear_queue()
                    return 0
                if time.time() - t0 > WAIT_MAX:
                    break
                time.sleep(0.12)
            if _interrupted():
                _clear_queue()
                return 0
            if _mouth_busy() or _speaker_running():
                continue
            _speak_now(line)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
    return 0


def _claim(name: str, line: str) -> bool:
    """True if this process owns the mouth. First writer wins (flock).

    Same phrase / same tool: PHRASE_S (MEMORY_S for memory). Any callout
    in BURST_S stays silent: one line, not one per retry.
    """
    name_n = (name or "").strip().lower()
    line_n = _norm(line)
    now = time.time()
    window = MEMORY_S if name_n.startswith(("memory", "hindsight")) else PHRASE_S
    try:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(STAMP, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            raw += chunk
        last_ts, last_name, last_line = _parse_stamp(raw.decode("utf-8", "replace"))
        age = now - last_ts if last_ts else 1e9
        same_phrase = bool(last_line and last_line == line_n)
        same_tool = bool(last_name and last_name == name_n)
        if last_ts and (
            (same_phrase and age < window)
            or (same_tool and age < window)
            or age < BURST_S
        ):
            return False
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{now:.3f}\t{name_n}\t{line}\n".encode())
        return True
    except OSError:
        return True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


def _recent(name: str, line: str = "") -> bool:
    """Same spoken line (or same tool) within a short window stays silent."""
    name_n = (name or "").strip().lower()
    line_n = _norm(line)
    window = MEMORY_S if name_n.startswith(("memory", "hindsight")) else PHRASE_S
    try:
        last_ts, last_name, last_line = _parse_stamp(STAMP.read_text(encoding="utf-8"))
        age = time.time() - last_ts if last_ts else 1e9
        if not last_ts:
            return False
        if age < BURST_S:
            return True
        if last_line and last_line == line_n and age < window:
            return True
        if last_name and last_name == name_n and age < window:
            return True
        if last_line and last_line in (name_n, line_n) and age < window:
            return True
    except Exception:
        pass
    return False


def _ft_desk() -> None:
    """First tool call while a call is up: fade to heads-down work (the player's /v1/ft-desk)."""
    if _profile() != "zoey":
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            PLAYER + "/v1/ft-desk",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=0.8).read()
    except Exception:
        pass


def main() -> int:
    if os.environ.get("KREW_HANDS_NESTED", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    _ft_desk()
    name = str(payload.get("tool_name") or payload.get("name") or "")
    line = _phrase(name)
    if not line or not _voice_on() or not _callout_info():
        return 0
    if _on_air(line) or not _claim(name, line):
        return 0
    who = _profile()
    voice = _callout_voice(who)
    kind = "kanban" if name.lower().startswith("kanban") else "tool"
    try:
        TOOL_ROLL.parent.mkdir(parents=True, exist_ok=True)
        with TOOL_ROLL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.time(), "text": line, "tool": name, "kind": kind,
                "who": who, "voice": voice,
            }, separators=(",", ":")) + "\n")
        rows = TOOL_ROLL.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(rows) > 40:
            TOOL_ROLL.write_text("\n".join(rows[-40:]) + "\n", encoding="utf-8")
    except OSError:
        pass
    # Queue behind whoever is already on the speaker. Never a second line over her.
    _enqueue(line, voice)
    _ensure_waiter()
    return 0


if __name__ == "__main__":
    if "--drain" in sys.argv:
        raise SystemExit(drain())
    raise SystemExit(main())
