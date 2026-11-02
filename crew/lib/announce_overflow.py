"""Announcer lines the house dropped without speaking them (the User 2026-09-30).

The PA queue holds KREW_ANNOUNCE_QUEUE_MAX (20; was 3 until 09-30) lines; one more pushes the oldest out to
`krew-announce-dump.jsonl` with reason `overflow` and it is never spoken. Nobody was told: 61 lines on 09-27,
6 in six minutes on 09-30 (a run of security completions). Two readers of the same dump file:

  the House Daily   `drops()` - how many since the day began, and the text of each (lib/krew_daily.a_announcer)
  the drain         `maybe_file_card()` - more than PER_HOUR (5) in the last hour files ONE card that day

Reads only, except the card: kill KREW_ANNOUNCE_OVERFLOW_CARD=0.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable
import krew_hermes as KH  # noqa: E402  where Hermes lives

DUMP = Path(os.environ.get("KREW_ANNOUNCE_DUMP", str(KH.hermes_home() / "krew-announce-dump.jsonl")))
STATE = Path(os.environ.get("KREW_ANNOUNCE_OVERFLOW_STATE",
                            str(KH.hermes_home() / "krew-announce-overflow-card.json")))
PER_HOUR = int(os.environ.get("KREW_ANNOUNCE_OVERFLOW_PER_HOUR", "5"))
CARD_ON = os.environ.get("KREW_ANNOUNCE_OVERFLOW_CARD", "1").strip().lower() not in ("0", "false", "no", "off")
HERMES = os.environ.get("KREW_HERMES_BIN") or next(
    (str(c) for c in (Path.home() / ".local" / "bin" / "hermes", Path("/usr/local/bin/hermes")) if c.exists()), "hermes")
AUTHOR = "krew-announce"


def drops(since_ts: float, path: Path | None = None, until_ts: float | None = None) -> list[dict]:
    """Every line dumped for `overflow` in [since_ts, until_ts], oldest first: {ts, title, summary, category}."""
    out = []
    try:
        lines = (path or DUMP).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("reason") != "overflow":
            continue
        ts = float(d.get("dump_ts") or d.get("ts") or 0)
        if ts >= since_ts and (until_ts is None or ts <= until_ts):
            out.append({"ts": ts, "title": str(d.get("title") or ""), "summary": str(d.get("summary") or ""),
                        "category": str(d.get("category") or "")})
    out.sort(key=lambda r: r["ts"])
    return out


def worst_hour(rows: list[dict]) -> tuple[int, float]:
    """(the most drops inside any 60 minutes, when that hour began)."""
    best, at = 0, 0.0
    ts = [r["ts"] for r in rows]
    j = 0
    for i, t in enumerate(ts):
        while ts[j] < t - 3600:
            j += 1
        if i - j + 1 > best:
            best, at = i - j + 1, ts[j]
    return best, at


def line_of(row: dict, limit: int = 140) -> str:
    text = " - ".join(x for x in (row["title"].strip(), " ".join(row["summary"].split())) if x)
    return f"{dt.datetime.fromtimestamp(row['ts']):%H:%M} {text[:limit]}" + ("…" if len(text) > limit else "")


def maybe_file_card(now: float, *, path: Path | None = None, state: Path | None = None,
                    run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """File ONE card the first time more than PER_HOUR lines drop inside an hour on a day. Returns the card id,
    or "" (nothing to file, already filed today, switched off, or the filing failed - which is retried)."""
    if not CARD_ON:
        return ""
    recent = drops(now - 3600, path, now)
    if len(recent) <= PER_HOUR:
        return ""
    day = f"{dt.datetime.fromtimestamp(now):%Y-%m-%d}"
    sp = state or STATE
    try:
        if json.loads(sp.read_text()).get("day") == day:
            return ""
    except (OSError, ValueError, AttributeError):
        pass
    title = f"Announcer dropped {len(recent)} lines in an hour without speaking them"
    body = "\n".join([
        f"The PA queue holds {os.environ.get('KREW_ANNOUNCE_QUEUE_MAX', '20')} lines; more than that and the oldest is "
        f"dumped unspoken (reason `overflow`, {sp.parent / 'krew-announce-dump.jsonl'}). {len(recent)} were dropped "
        f"in the last hour, the threshold is {PER_HOUR}.",
        "", "Dropped, oldest first:", *[f"- {line_of(r, 300)}" for r in recent], "",
        "source: lib/announce_overflow.py (krew-announce-drain)",
        "cause: more lines were queued than the announcer can say (each line is ~10 s of speech, one at a time).",
        "evidence: the rows above. One card a day; the House Daily counts every drop.",
    ])
    r = run([HERMES, "-p", "ada", "kanban", "create", title, "--assignee", "ada", "--body", body[:5500],
             "--created-by", AUTHOR, "--idempotency-key", f"announcer-overflow:{day}"],
            capture_output=True, text=True, timeout=60)
    m = re.search(r"\b(t_[0-9a-f]{8})\b", (r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0 or not m:
        return ""
    tmp = sp.with_name(sp.name + ".tmp")
    sp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps({"day": day, "card": m.group(1), "drops": len(recent), "ts": now}) + "\n")
    os.replace(tmp, sp)
    return m.group(1)
