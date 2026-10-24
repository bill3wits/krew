"""krew-janitor: hermes venv-generation inventory (report only, never deletes).

t_706a5db8 (board 2026-09-16, reframed report-only by the User 2026-10-01):
hermes venv generations under ~/.hermes/installs are NEVER pruned. Every
`hermes-env-pins.py apply` / nightly trail resync materializes a new
generation. The 15 GiB/day slope did not hold, but the accumulation is real:
15 envs, 3 live installs, ~15.7 GiB total measured this session.

LIVE rule: an env generation is live ONLY when its install's facts.json
packages.venv.environment names it. A generation mapped by a running process
but absent from facts.json is reported STALE with a `mapped: true` annotation
— mapped is report-only, it never promotes a gen to live.

AGE rule: `age_days` is derived from the venv dir mtime — LAST ACTIVITY, not
creation time. A gen still being written reports a young age. The field is
`age_days` deliberately (not `created_days`) so the basis is not misread.

This module only REPORTS: which generations are live, which are not, age,
size. A prune is the User's call. The janitor row is `hermes_venv_gen`
in the tick JSONL row.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# t_706a5db8: ~/.hermes/installs is the agent's home, NOT the profile's
# HERMES_HOME (which is the profile dir under ~/.hermes/profiles/).
HERMES_ROOT = Path.home() / ".hermes"


def _live_generations(root: Path) -> dict[str, dict]:
    """Map install_id -> {gen, path} from every facts.json under root.

    facts.json is the authoritative source of the live gen. An unreadable or
    malformed facts.json claims nothing — that install's envs all go stale.
    """
    live: dict[str, dict] = {}
    for facts in root.glob("*/facts.json"):
        try:
            data = json.loads(facts.read_text())
        except Exception:
            continue
        env = (data.get("packages", {}).get("venv", {}) or {}).get("environment")
        if not env:
            continue
        # environment looks like .../installs/<id>/environments/<gen>/venv
        parts = Path(env).parts
        try:
            idx = parts.index("installs")
            install_id = parts[idx + 1]
            gen_idx = parts.index("environments")
            gen = parts[gen_idx + 1]
        except (ValueError, IndexError):
            continue
        live[install_id] = {"gen": gen, "path": env}
    return live


def _proc_mapped_generations() -> dict[str, set[str]]:
    """install_id -> {gen, ...} from /proc/*/maps. Read-only, no subprocess.

    A gen mapped by a running process is annotated `mapped: true` on its
    stale row. It is never promoted to live — facts.json alone is live.
    """
    mapped: dict[str, set[str]] = {}
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return mapped
    for pid in pids:
        try:
            with open(f"/proc/{pid}/maps") as f:
                for line in f:
                    if "/.hermes/installs/" not in line:
                        continue
                    hit = False
                    for token in line.split():
                        if "/.hermes/installs/" not in token:
                            continue
                        try:
                            parts = token.split("/")
                            i = parts.index("installs")
                            install_id = parts[i + 1]
                            gen_idx = parts.index("environments")
                            gen = parts[gen_idx + 1]
                            mapped.setdefault(install_id, set()).add(gen)
                        except (ValueError, IndexError):
                            pass
                        hit = True
                    if hit:
                        break  # first matching line per pid is enough
        except (OSError, PermissionError):
            continue
    return mapped


def _venv_size_bytes(venv: Path, gen_dir: Path, cache: dict) -> int:
    """Total venv bytes, cached on the gen dir mtime (see report for rationale)."""
    mtime = gen_dir.stat().st_mtime
    key = (str(gen_dir), mtime)
    if key in cache:
        return cache[key]
    size = 0
    if venv.is_dir():
        for f in venv.rglob("*"):
            try:
                if f.is_file():
                    size += f.stat().st_size
            except OSError:
                pass
    cache[key] = size
    return size


def hermes_venv_gen_report(root: Path | None = None, *,
                           size_cache: dict | None = None) -> dict:
    """One-shot report: which envs are live, which are not, age, size.

    `size_cache` (optional, in-process) keys on (gen_dir, mtime) so repeated
    calls within a process only re-walk a gen whose dir mtime changed — the
    common path is a handful of stat() calls. The janitor (one-shot process
    per tick) passes no cache and walks fresh; the 5-min wall cost is real
    (~1.9 s / ~420k stat on the current 15 envs) and is the price of a
    report-only metric that grows with the accumulation it surfaces. A
    durable cross-tick cache belongs in the janitor state; that is a follow-up
    card, not this one — the report is correct and report-only as it stands.

    Returns a dict ready to drop into the janitor tick row:
    {
      "live":    {install_id: {gen, path}},
      "stale":   [ {install_id, gen, path, age_days, size_gib, mapped}, ... ],
      "total_gib": float,
      "live_gib":  float,
      "stale_gib": float,
      "error":   str | None,
    }
    """
    root = root or HERMES_ROOT / "installs"
    if not root.is_dir():
        return {"live": {}, "stale": [], "total_gib": 0.0, "live_gib": 0.0,
                "stale_gib": 0.0, "error": f"no such dir: {root}"}

    live = _live_generations(root)
    proc_mapped = _proc_mapped_generations()
    now = time.time()
    cache: dict = size_cache if size_cache is not None else {}

    stale_rows = []
    live_gib = 0.0
    total_gib = 0.0

    for install_dir in sorted(root.iterdir()):
        if not install_dir.is_dir():
            continue
        install_id = install_dir.name
        envs_dir = install_dir / "environments"
        if not envs_dir.is_dir():
            continue
        for gen_dir in sorted(envs_dir.iterdir()):
            if not gen_dir.is_dir():
                continue
            gen = gen_dir.name
            venv = gen_dir / "venv"
            size_bytes = _venv_size_bytes(venv, gen_dir, cache)
            size_gib = round(size_bytes / 2**30, 3)
            total_gib += size_gib

            # LIVE is facts-only. A mapped-but-not-in-facts gen is stale with
            # mapped:true — never promoted, so the report never under-states
            # what a prune could touch.
            is_live = (live.get(install_id, {}).get("gen") == gen)
            is_mapped = gen in proc_mapped.get(install_id, set())

            if is_live:
                live_gib += size_gib
            else:
                mtime = gen_dir.stat().st_mtime
                age_days = round((now - mtime) / 86400, 1)
                stale_rows.append({
                    "install_id": install_id,
                    "gen": gen,
                    "path": str(gen_dir),
                    "age_days": age_days,
                    "size_gib": size_gib,
                    "mapped": is_mapped,
                })

    stale_gib = round(total_gib - live_gib, 3)
    stale_rows.sort(key=lambda r: r["size_gib"], reverse=True)

    return {
        "live": live,
        "stale": stale_rows,
        "total_gib": round(total_gib, 3),
        "live_gib": round(live_gib, 3),
        "stale_gib": stale_gib,
        "error": None,
    }
