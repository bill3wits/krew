"""What a hermes session's prompt weighed, turn by turn, read from its own records.

The house asked "why is every exchange heavy" and the answer was different per surface: on
Zoey's mouth it was memory (a recall block added to every user turn and kept in history), in
her hands it was the tool list and what a run read. A tool count could not show either, so the
yardstick is the prompt itself: characters per part and the prompt tokens the engine logged.

Read-only. The board and session stores are opened `mode=ro` (never `immutable=1`: it ignores
the WAL and serves a stale snapshot of a live store).

  system      the session's system prompt (sessions.system_prompt, else system_prompts by hash)
  recall      the `<memory-context>` block the memory provider appended to THAT user turn
  carried     every earlier turn's recall, still in history
  results     tool results returned inside the turn
  history     everything sent before the turn began (user turns as sent, replies, tool results)
  first/peak  prompt tokens of the turn's first and largest model call, from agent.log
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from pathlib import Path

HERMES = Path.home() / ".hermes"
RECALL_RE = re.compile(r"<memory-context>.*?</memory-context>", re.S)
CALL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \w+ \[(?P<sid>[^\]]+)\] agent\.conversation_loop: "
    r"API call #(?P<n>\d+): model=(?P<model>\S+) .*?\bin=(?P<tin>\d+) out=(?P<tout>\d+) .*?latency=(?P<lat>[\d.]+)s")
# hermes's session source -> the platform list that session's kit is resolved from
SOURCE_PLATFORM = {"api_server": "api_server", "cron": "cron"}


def profile_home(profile: str, root: Path | None = None) -> Path:
    root = root or HERMES
    return root if profile == "default" else root / "profiles" / profile


def open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def log_calls(home: Path, wanted: set[str] | None = None) -> dict[str, list[dict]]:
    """Every logged model call per session id, oldest first (agent.log and its rotations)."""
    logs = sorted((home / "logs").glob("agent.log*"), key=lambda p: p.stat().st_mtime)
    out: dict[str, list[dict]] = {}
    for path in logs:
        try:
            fh = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if "API call #" not in line:
                    continue
                m = CALL_RE.match(line)
                if not m or (wanted is not None and m["sid"] not in wanted):
                    continue
                out.setdefault(m["sid"], []).append({
                    "ts": m["ts"], "n": int(m["n"]), "model": m["model"],
                    "in": int(m["tin"]), "out": int(m["tout"]), "latency": float(m["lat"])})
    return out


def split_turn_calls(calls: list[dict]) -> list[list[dict]]:
    """The call counter restarts at #1 on every turn; that restart is the turn boundary."""
    turns: list[list[dict]] = []
    for c in calls:
        if c["n"] == 1 or not turns:
            turns.append([])
        turns[-1].append(c)
    return turns


def _system_chars(con: sqlite3.Connection, prompt: str | None, prompt_hash: str | None) -> int | None:
    if prompt:
        return len(prompt)
    if prompt_hash:
        try:
            row = con.execute("select length(prompt) from system_prompts where hash=?", (prompt_hash,)).fetchone()
        except sqlite3.Error:
            row = None
        if row and row[0]:
            return int(row[0])
    return None


def weigh_session(con: sqlite3.Connection, sid: str, calls: list[dict] | None = None,
                  tools_chars: int | None = None) -> dict | None:
    """One session as a list of turns. `tools_chars` is the kit's tool array when the caller sized it."""
    row = con.execute(
        "select source, system_prompt, system_prompt_hash, started_at, input_tokens, api_call_count, end_reason "
        "from sessions where id=?", (sid,)).fetchone()
    if row is None:
        return None
    source, prompt, prompt_hash, started, in_tok, n_calls, end_reason = row
    system = _system_chars(con, prompt, prompt_hash)
    turns: list[dict] = []
    history = carried = 0
    cur: dict | None = None
    for role, content, api, tool_calls, ts in con.execute(
            "select role, content, api_content, tool_calls, timestamp from messages where session_id=? order by id",
            (sid,)):
        content, api = content or "", api or ""
        if role == "user":
            sent = api or content
            m = RECALL_RE.search(sent)
            recall = len(m.group(0)) if m else 0
            cur = {"turn": len(turns) + 1, "at": ts, "said": len(content), "sent": len(sent),
                   "recall": recall, "bullets": m.group(0).count("\n- ") if m else 0,
                   "carried": carried, "history": history, "results": 0, "reply": 0, "tool_calls": 0,
                   "request": (system or 0) + (tools_chars or 0) + history + len(sent)}
            turns.append(cur)
            carried += recall
            history += len(sent)
            continue
        size = len(content) + (len(tool_calls) if tool_calls else 0)
        history += size
        if cur is None:
            continue
        if role == "tool":
            cur["results"] += len(content)
        elif role == "assistant":
            cur["reply"] += len(content)
            if tool_calls:
                try:
                    cur["tool_calls"] += len(json.loads(tool_calls))
                except (ValueError, TypeError):
                    cur["tool_calls"] += 1
    for turn, tc in zip(turns, split_turn_calls(calls or [])):
        turn.update({"calls": len(tc), "first_tokens": tc[0]["in"], "peak_tokens": max(c["in"] for c in tc),
                     "model": tc[0]["model"], "seconds": round(sum(c["latency"] for c in tc), 1)})
    return {"session": sid, "source": source, "started": started, "system": system, "tools": tools_chars,
            "input_tokens": in_tok, "api_calls": n_calls, "end_reason": end_reason, "turns": turns}


def sessions_between(con: sqlite3.Connection, since: float | None = None, until: float | None = None,
                     source: str | None = None) -> list[str]:
    q, args = "select id from sessions where 1=1", []
    if since is not None:
        q, args = q + " and started_at>=?", args + [since]
    if until is not None:
        q, args = q + " and started_at<?", args + [until]
    if source:
        q, args = q + " and source=?", args + [source]
    return [r[0] for r in con.execute(q + " order by started_at", args)]


def _median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def summarize(sessions: list[dict]) -> dict:
    """Medians per source: what a first call costs, how far a session climbs, how much is recall."""
    by: dict[str, list[dict]] = {}
    for s in sessions:
        by.setdefault(s["source"] or "?", []).append(s)
    out = {}
    for source, group in sorted(by.items()):
        turns = [t for s in group for t in s["turns"]]
        later = [t for s in group for t in s["turns"][1:]]
        out[source] = {
            "sessions": len(group), "turns": len(turns),
            "system": _median([s["system"] for s in group]),
            "tools": _median([s["tools"] for s in group]),
            "first_tokens": _median([s["turns"][0].get("first_tokens") for s in group if s["turns"]]),
            "peak_tokens": _median([max((t.get("peak_tokens") or 0) for t in s["turns"]) or None
                                    for s in group if s["turns"]]),
            "recall_per_later_turn": _median([t["recall"] for t in later]),
            "turns_with_recall": sum(1 for t in turns if t["recall"]),
            "results_per_turn": _median([t["results"] for t in turns]),
        }
    return out
