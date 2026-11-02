"""Crissa's tool-call watch (the User 2026-10-02): a live log of her calls, failures and retries, and ONE
alert card on her list when her failure rate jumps.

Every tick reads her session store (read-only) from a cursor and appends one row per tool reply to
~/.hermes/logs/crissa-tool-watch.jsonl: when, which surface (api_server = her mouth, cron = her watch,
oneshot/cli = her hands), the tool, failed or not and why, and whether it repeated the call before it.
The rate is the last WINDOW minutes against her own baseline (the 7 days before them). A jump is at
least MIN_CALLS calls with a failure rate at least FACTOR x the baseline AND at least FLOOR points over it.
One card per episode; it closes itself when the rate falls back.

The drill's own runs are left out (their asks are in config/krew-crissa-drill.json), so a night of graded
tasks never reads as her day going wrong.

Cards are REPORT-ONLY until KREW_TOOL_WATCH_CARDS=1 (a new alarm is armed report-only, house law):
until then a jump is logged as `would_card`. Kill: KREW_TOOL_WATCH=0 or $KREW_HOME/krew-tool-watch.off.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
KREW_HOME = Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))
HERMES_HOME = Path(os.path.expanduser(os.environ.get("KREW_HERMES") or os.environ.get("HERMES_HOME") or str(KH.hermes_home())))
try:   # krew: whose session store - KREW_TOOL_WATCH_PROFILE, else hands.profile in krew.yaml, else crissa
    import krew_settings  # noqa: E402
    _HANDS = str(krew_settings.load("hands").get("profile") or "")
except Exception:  # noqa: BLE001
    _HANDS = ""
PROFILE = os.environ.get("KREW_TOOL_WATCH_PROFILE", "").strip() or _HANDS or "crissa"
STATE_DB = Path(os.environ.get("KREW_TOOL_WATCH_DB") or HERMES_HOME / "profiles" / PROFILE / "state.db")
DIR = KREW_HOME / "tool-watch"           # krew: its own state folder, never the Hermes home
LOG = DIR / "log.jsonl"
STATE = DIR / "state.json"
OFF_FILE = KREW_HOME / "krew-tool-watch.off"
# the house left its drill's asks out (config/krew-crissa-drill.json); krew reads $KREW_HOME/tool-watch-exclude.json ({"asks": [...]})
DRILL_CFG = KREW_HOME / "tool-watch-exclude.json"

WINDOW_MIN = float(os.environ.get("KREW_TOOL_WATCH_WINDOW_MIN", "60"))
MIN_CALLS = int(os.environ.get("KREW_TOOL_WATCH_MIN_CALLS", "10"))
FACTOR = float(os.environ.get("KREW_TOOL_WATCH_FACTOR", "2.0"))
FLOOR = float(os.environ.get("KREW_TOOL_WATCH_FLOOR", "0.15"))
BASELINE_DAYS = float(os.environ.get("KREW_TOOL_WATCH_BASELINE_DAYS", "7"))

# A failure is read from the reply's SHAPE, never from a word anywhere in it: a skill that explains errors or a
# board line about a failed card is a successful read (the first draft counted 293 skill_view reads as errors).
DID_NOT_FINISH = re.compile(r"HANDS DID NOT FINISH|Hands hit the time cap|hit the time cap", re.I)
BAD_ARGS = re.compile(r"Unknown action|unknown argument|missing (?:a )?required|invalid (?:argument|parameter)|"
                      r"empty ask|unexpected keyword|validation error|is not a valid", re.I)
LEAD_FAIL = re.compile(r"^\s*(?:Tool '[^']*' does not exist|Error\b|ERROR\b|Traceback|hands (?:[A-Za-z]+Error|rc=)|"
                       r"[a-z_]+: (?:refused|not available|unavailable)|refused\b|Hands are already busy)", re.I)
def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_TOOL_WATCH", "1")).strip().lower() not in ("0", "false", "no", "off") \
        and not OFF_FILE.exists()


def cards_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_TOOL_WATCH_CARDS", "0")).strip().lower() in ("1", "true", "yes", "on")


def drill_asks(path: Path | None = None) -> list[str]:
    try:
        d = json.loads(Path(path or DRILL_CFG).read_text())
        rows = d.get("asks") if isinstance(d, dict) and "asks" in d else [t["ask"] for t in d["tasks"]]
        return [str(a).strip()[:80] for a in rows]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def classify(text: str) -> str:
    """'' = ok, else bad_args | did_not_finish | error."""
    t = (text or "").strip()
    if DID_NOT_FINISH.search(t):
        return "did_not_finish"
    if t.startswith("{"):
        try:
            d = json.loads(t)
        except ValueError:
            d = None
        if isinstance(d, dict):
            err = d.get("error")
            if d.get("success") is False or (err not in (None, "", False)):
                return "bad_args" if BAD_ARGS.search(str(err or d)) else "error"
            return ""
    head = t[:200]
    if BAD_ARGS.search(head):
        return "bad_args"
    if LEAD_FAIL.search(head):
        return "error"
    return ""


def read_new(cursor: int, db: Path | None = None, asks: list[str] | None = None) -> tuple[list[dict], int]:
    """Tool replies with id > cursor, oldest first; each with the call it answers."""
    asks = drill_asks() if asks is None else asks
    con = sqlite3.connect(f"file:{db or STATE_DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT m.id, m.session_id, m.tool_call_id, m.tool_name, m.content, m.timestamp, s.source "
        "FROM messages m JOIN sessions s ON s.id=m.session_id WHERE m.role='tool' AND m.id>? ORDER BY m.id LIMIT 5000",
        (cursor,)).fetchall()
    out, top = [], cursor
    drill_sessions: dict[str, bool] = {}
    prev_call: dict[str, str] = {}
    for r in rows:
        top = max(top, r["id"])
        sid = r["session_id"]
        if sid not in drill_sessions:
            first = con.execute("SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
                                (sid,)).fetchone()
            drill_sessions[sid] = bool(first and any(a and a in (first[0] or "") for a in asks))
        if drill_sessions[sid]:
            continue
        args = ""
        call = con.execute("SELECT tool_calls FROM messages WHERE session_id=? AND role='assistant' AND tool_calls LIKE ? "
                           "ORDER BY id DESC LIMIT 1", (sid, f"%{r['tool_call_id']}%")).fetchone()
        if call:
            try:
                for c in json.loads(call[0]):
                    if r["tool_call_id"] in (c.get("id"), c.get("call_id")):
                        args = str((c.get("function") or {}).get("arguments") or "")[:200]
            except ValueError:
                pass
        sig = f"{r['tool_name']}|{args}"
        retry = prev_call.get(sid) == sig
        prev_call[sid] = sig
        why = classify(str(r["content"] or ""))
        out.append({"id": r["id"], "ts": round(float(r["timestamp"]), 1), "session": sid, "surface": r["source"],
                    "tool": r["tool_name"], "failed": bool(why), "why": why, "retry": retry,
                    "said": (str(r["content"] or "")[:160] if why else "")})
    con.close()
    return out, top


def rates(rows: list[dict], now: float) -> dict:
    w0 = now - WINDOW_MIN * 60
    b0 = w0 - BASELINE_DAYS * 86400
    rec = [r for r in rows if r["ts"] >= w0]
    base = [r for r in rows if b0 <= r["ts"] < w0]
    rf = sum(r["failed"] for r in rec) / len(rec) if rec else 0.0
    bf = sum(r["failed"] for r in base) / len(base) if base else 0.0
    jump = len(rec) >= MIN_CALLS and rf >= FACTOR * bf and rf - bf >= FLOOR
    return {"recent_n": len(rec), "recent_fail": round(rf, 3), "base_n": len(base), "base_fail": round(bf, 3),
            "jump": jump, "retries": sum(r["retry"] for r in rec)}


def load_log(since: float) -> list[dict]:
    out = []
    try:
        with LOG.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("ts", 0) >= since and "tool" in r:
                    out.append(r)
    except OSError:
        pass
    return out


def _kanban(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run([os.environ.get("HERMES_BIN") or str(HOME / ".local/bin/hermes"), "-p", PROFILE, "kanban", *args],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def file_card(r: dict, top: list[tuple[str, int]], now: float, kanban=None) -> str:
    k = kanban or _kanban
    title = (f"Crissa tool failures jumped: {r['recent_fail']:.0%} of {r['recent_n']} calls in {int(WINDOW_MIN)} min "
             f"(baseline {r['base_fail']:.0%})")
    body = (f"crissa-tool-watch at {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}: {r}\n"
            f"Most failing tools: {', '.join(f'{t} x{n}' for t, n in top)}\nLog: {LOG}")
    rc, out = k("create", title, "--assignee", PROFILE, "--body", body, "--created-by", "crissa-tool-watch",
                "--idempotency-key", f"crissa-tool-watch:{int(now // 3600)}")
    m = re.search(r"\b(t_[0-9a-f]{8})\b", out)
    if rc != 0 or not m:
        return ""
    tid = m.group(1)
    rc2, _ = k("block", tid, "Her tool failure rate jumped; see the body. Nothing to rule unless it persists.",
               "--kind", "needs_input")
    if rc2 != 0:
        k("archive", tid)
        return ""
    return tid


def tick(now: float | None = None, *, db: Path | None = None, kanban=None) -> dict:
    now = now or time.time()
    if not enabled():
        return {"skipped": "off"}
    st = {}
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        st = {}
    cursor = int(st.get("cursor") or 0)
    if not cursor:   # first run: start a baseline's worth back, not at message 1
        try:
            con = sqlite3.connect(f"file:{db or STATE_DB}?mode=ro", uri=True, timeout=10)
            row = con.execute("SELECT min(id) FROM messages WHERE timestamp >= ?",
                              (now - (BASELINE_DAYS * 86400 + WINDOW_MIN * 60),)).fetchone()
            con.close()
            cursor = int(row[0] or 1) - 1
        except sqlite3.Error:
            cursor = 0
    new, top_id = read_new(cursor, db)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        for r in new:
            fh.write(json.dumps(r) + "\n")
    rows = load_log(now - (BASELINE_DAYS * 86400 + WINDOW_MIN * 60))
    r = rates(rows, now)
    rec = [x for x in rows if x["ts"] >= now - WINDOW_MIN * 60 and x["failed"]]
    counts: dict[str, int] = {}
    for x in rec:
        counts[x["tool"]] = counts.get(x["tool"], 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    ep = st.get("episode")
    action = ""
    if r["jump"] and not ep:
        if cards_on():
            card = file_card(r, top, now, kanban)
            ep = {"since": now, "card": card}
            action = f"card {card or 'FAILED'}"
        else:
            ep = {"since": now, "card": ""}
            action = "would_card"
    elif ep and not r["jump"] and r["recent_fail"] < max(r["base_fail"] * 1.2, r["base_fail"] + 0.05):
        if ep.get("card"):
            (kanban or _kanban)("complete", ep["card"], "--summary",
                                f"Rate back to {r['recent_fail']:.0%} (baseline {r['base_fail']:.0%}).")
        action = "episode closed"
        ep = None
    st.update(cursor=top_id, episode=ep, last=dict(r, ts=now, action=action, top=top))
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, STATE)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tick": round(now, 1), **r, "action": action, "new": len(new)}) + "\n")
    return dict(r, action=action, new=len(new))
