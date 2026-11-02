"""A hermes cron job that fails three times in a row gets ONE card (the User 2026-09-30).

Crissa's `needs-crissa-watch` failed at every 10-minute tick from 09-28 05:33 to 09-30 - 211 times, a
`ModuleNotFoundError` in the worker before it ran at all - and nothing said so: the failure lived in
`last_error` of her `cron/jobs.json` and in a log nobody tails. hermes already counts it
(`failure_streak`); this reads that number for every profile's jobs and names the ones at or past
the threshold. The board gate files the card (one per streak: the state file remembers the streak it
filed for and forgets it when the job runs clean again).

Read-only except for the state file. A jobs file that cannot be read is skipped, never an alarm.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

THRESHOLD = int(os.environ.get("KREW_CRON_STREAK_MIN", "3"))


def hermes_home() -> Path:
    return Path(os.environ.get("KREW_HERMES") or KH.hermes_home())


def job_files(home: Path | None = None) -> list[tuple[str, Path]]:
    home = home or hermes_home()
    out = [("default", home / "cron" / "jobs.json")]
    profiles = home / "profiles"
    if profiles.is_dir():
        out += [(p.name, p / "cron" / "jobs.json") for p in sorted(profiles.iterdir()) if p.is_dir()]
    return [(name, path) for name, path in out if path.is_file()]


def _jobs(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = data.get("jobs") if isinstance(data, dict) else data
    return [j for j in jobs or [] if isinstance(j, dict)]


def in_flight(job: dict) -> bool:
    """Dispatched after the last recorded result: the job is running (or was just handed to its worker).
    A job that dies AT dispatch records its failure at or after the dispatch time, so it is never in flight."""
    sent = str((job.get("last_dispatch") or {}).get("dispatched_at") or "")
    return bool(sent) and sent > str(job.get("last_run_at") or "")


def failing(home: Path | None = None, threshold: int | None = None) -> list[dict]:
    """Enabled jobs whose last run failed and whose streak is at or past the threshold."""
    threshold = THRESHOLD if threshold is None else threshold
    rows = []
    for profile, path in job_files(home):
        for j in _jobs(path):
            try:
                streak = int(j.get("failure_streak") or 0)
            except (TypeError, ValueError):
                streak = 0
            if not j.get("enabled", True) or j.get("paused_at") or streak < threshold:
                continue
            if str(j.get("last_status") or "").lower() not in ("error", "failed"):
                continue
            if in_flight(j):
                continue            # a run is going right now: its result is not in `last_status` yet
            rows.append({"profile": profile, "id": str(j.get("id") or ""), "name": str(j.get("name") or j.get("id") or "?"),
                         "streak": streak, "last_run_at": str(j.get("last_run_at") or ""),
                         "last_error": str(j.get("last_error") or "")})
    return rows


def cause_line(error: str, limit: int = 200) -> str:
    """The line of an error that names the cause: a traceback's LAST line, else its first."""
    lines = [x.strip() for x in (error or "").splitlines() if x.strip()]
    if not lines:
        return "no error text recorded"
    line = lines[-1] if any(x.startswith("Traceback") or "Traceback (most recent call last)" in x for x in lines) else lines[0]
    return line[:limit]


def card_for(row: dict) -> tuple[str, str]:
    title = f"cron: {row['profile']}'s job {row['name']} has failed {row['streak']} times in a row"
    body = (f"source: hermes cron job {row['id']} ({row['name']}), profile {row['profile']}\n"
            f"cause: {cause_line(row['last_error'])}\n"
            f"evidence: failure_streak={row['streak']}, last run {row['last_run_at']}, in "
            f"~/.hermes/{'' if row['profile'] == 'default' else 'profiles/' + row['profile'] + '/'}cron/jobs.json `last_error`:\n\n"
            f"{row['last_error'][-1500:]}\n\n"
            "Filed once per streak by the board gate (lib/cron_streak.py); it is not re-filed while this card is "
            "open, and a clean run ends the streak. Fix the cause; do not just re-run the job.")
    return title, body


def due(rows: list[dict], state: dict) -> list[dict]:
    """Rows with no card filed for their current streak. `state` is {profile:job -> {...}}."""
    return [r for r in rows if f"{r['profile']}:{r['id']}" not in state]


def settle(rows: list[dict], state: dict) -> dict:
    """Forget every job that is no longer failing: its next streak is a new card."""
    live = {f"{r['profile']}:{r['id']}" for r in rows}
    return {k: v for k, v in state.items() if k in live}


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def mark(state: dict, row: dict, card: str) -> dict:
    state[f"{row['profile']}:{row['id']}"] = {"card": card, "filed_at": round(time.time()), "streak": row["streak"],
                                              "name": row["name"]}
    return state
