#!/usr/bin/env python3
"""Count tool calls per profile — the measurement before any tool is cut.

WHY. Tools are the heaviest thing in a turn: the schemas ride in the prefix on

lila carries 31 tools (~25 KB/turn) against a 7.4 KB SOUL, ada 27, zoey 28 —
1.3x to 4.8x the SOUL. Two cuts look obvious (ada holds 14 separate `kanban_*`
tools where bash/steward/vera use one `kanban_lean`; `skills_list` and
`skill_manage` ride on all six and are named in no SOUL) — but "looks unused" is
not evidence, and cutting a tool a worker silently depends on breaks it mid-card.
This writes the evidence. Revisit with `--report` after a few days.

HOW. A `pre_tool_call` hook, because the alternative is editing the live hermes
tree, which shows up as BEYOND PATCHES. It appends one short line and exits:
no JSON parse of the whole payload, no imports beyond the three it needs, no
network, no lock. It NEVER blocks a tool — every path returns 0, and the hook
spec carries no `fail_closed`, so hermes fails it open.

    ~/.hermes/tool-counts/<YYYY-MM-DD>.tsv     ts<TAB>profile<TAB>tool

Read it back:
    scripts/krew-tool-count.py --report [--days 7]

Kill: `KREW_TOOL_COUNT=0` (the hook becomes a no-op; leave the spec in place).


"A few days" is the wrong clock — VOLUME arrives tonight, COVERAGE does not.

    1,571 calls in 2.2 h (12:51-15:02) = ~720/h. Seven days would be ~120k rows to
    settle two questions. Tomorrow morning is already more sample than either needs.

But today is a Render day and the mix is skewed by it, so read the two cuts apart:

  * `skills_list` / `skill_manage` — **0 calls in 1,571**, across all six profiles.
    That is the cut the file was opened for and it is nearly evidenced already; one
    ordinary day on top and it is safe to make. Watch for the confound: a tool nobody
    calls may still be one the SOUL never told them they had (the `frame:`/`done`/
    `render-now` lesson from the same day) — check that no SOUL or prompt names them
    before reading zero as "unwanted" rather than "unadvertised".

  * ada's 14 separate `kanban_*` tools — **do not read today's file for this one.**
    ada has 16 calls and 8 distinct tools because her gateway was parked by the
    occupancy hold from ~13:5x (Render, the Comfy v0.37.0 smoke). Across every
    profile only 8 distinct `kanban_*` names have fired at all, so 6 of her 14 are
    "unseen" for a reason that has nothing to do with whether she needs them. This
    cut needs one full NON-Render day of ada dispatching. bash's 871 calls are the
    same skew pointing the other way (the smoke ran in his lane all afternoon).

So: gate the report on a representative day per profile, not on a date. The cheapest
check is `cut -f2 <day>.tsv | sort | uniq -c` — if ada is in the tens, that day does
not count for her.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("KREW_REAL_HOME") or Path.home())
DIR = Path(os.environ.get("KREW_TOOL_COUNT_DIR") or (HOME / ".hermes" / "tool-counts"))
KNOWN = ("zoey", "ada", "bash", "steward", "lila", "vera", "scout", "judge", "default")


def on() -> bool:
    return os.environ.get("KREW_TOOL_COUNT", "1").strip().lower() not in ("0", "false", "no", "off")


def profile() -> str:
    """Same resolution order zoey-tool-callout uses: env first, then the worker's
    sandboxed HOME/cwd under ~/.hermes/profiles/<name>/."""
    for key in ("HERMES_PROFILE", "HERMES_AGENT_PROFILE"):
        v = str(os.environ.get(key) or "").strip().lower()
        if v in KNOWN:
            return v

    # argv, but its HERMES_HOME is the served profile's home.
    for probe in (os.environ.get("HERMES_HOME") or "", os.environ.get("HOME") or "", os.getcwd()):
        parts = Path(probe).parts if probe else ()
        if "profiles" in parts:
            i = parts.index("profiles")
            if i + 1 < len(parts) and parts[i + 1] in KNOWN:
                return parts[i + 1]


    # The hook is spawned BY that worker, and its argv says `hermes -p <profile>`.
    return from_parent_argv() or "unknown"


def from_parent_argv(pid: int | None = None, hops: int = 4,
                     proc: Path = Path("/proc")) -> str:
    """Walk up the process tree for `-p <profile>` / `--profile <profile>`."""
    pid = os.getppid() if pid is None else pid
    for _ in range(hops):
        if pid <= 1:
            break
        try:
            argv = (proc / str(pid) / "cmdline").read_bytes().split(b"\0")
        except OSError:
            break
        args = [a.decode("utf-8", "replace") for a in argv if a]
        for i, a in enumerate(args):
            if a in ("-p", "--profile") and i + 1 < len(args):
                who = args[i + 1].strip().lower()
                if who in KNOWN:
                    return who
        try:
            stat = (proc / str(pid) / "stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return ""


def tool_name(blob: str) -> str:
    """Pull tool_name out without json.loads: the payload can be large (a patch
    body, a file write) and this runs before every single tool call."""
    for key in ('"tool_name"', '"name"'):
        i = blob.find(key)
        if i == -1:
            continue
        j = blob.find('"', blob.find(":", i + len(key)) + 1)
        k = blob.find('"', j + 1)
        if 0 < j < k:
            got = blob[j + 1:k].strip()
            if got and len(got) < 80:
                return got
    return ""


def record(name: str, who: str, *, now: float | None = None, directory: Path | None = None) -> bool:
    if not name:
        return False
    d = directory or DIR
    now = time.time() if now is None else now
    try:
        d.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        line = f"{int(now)}\t{who}\t{name}\n"
        # O_APPEND: concurrent workers interleave whole lines without a lock.
        with open(d / f"{day}.tsv", "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except OSError:
        return False        # a counter must never be the reason a tool did not run


def report(days: int = 7, directory: Path | None = None) -> dict:
    from collections import Counter
    d = directory or DIR
    per: dict[str, Counter] = {}
    cutoff = time.time() - days * 86400
    if not d.is_dir():
        return {"days": days, "calls": 0, "profiles": {}}
    for f in sorted(d.glob("*.tsv")):
        try:
            rows = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for row in rows:
            bits = row.split("\t")
            if len(bits) != 3:
                continue
            try:
                ts = int(bits[0])
            except ValueError:
                continue
            if ts < cutoff:
                continue
            per.setdefault(bits[1], Counter())[bits[2]] += 1
    return {"days": days, "calls": sum(sum(c.values()) for c in per.values()),
            "profiles": {p: dict(c.most_common()) for p, c in sorted(per.items())}}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--report" in argv:
        import json
        import krew_yaml as yaml
        days = 7
        if "--days" in argv:
            try:
                days = int(argv[argv.index("--days") + 1])
            except (IndexError, ValueError):
                pass
        rep = report(days)
        if "--json" in argv:
            print(json.dumps(rep, indent=1))
            return 0
        print(f"{rep['calls']} tool calls in {days} day(s)\n")
        sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
        try:
            import toolsets
        except Exception:
            toolsets = None
        for prof, counts in rep["profiles"].items():
            carried = set()
            if toolsets:
                try:
                    cfg = yaml.safe_load(
                        (HOME / ".hermes" / "profiles" / prof / "config.yaml").read_text()) or {}
                    for t in (cfg.get("toolsets") or []):
                        carried |= set(toolsets.get_toolset_info(t).get("resolved_tools") or [])
                except Exception:
                    carried = set()
            used = set(counts)
            print(f"== {prof}: {sum(counts.values())} calls, {len(used)} of {len(carried) or '?'} tools used")
            for name, n in list(counts.items())[:8]:
                print(f"     {n:6}  {name}")
            never = sorted(carried - used)
            if never:
                print(f"     never called ({len(never)}): {', '.join(never)}")
        return 0

    if not on():
        return 0
    try:
        blob = sys.stdin.read(4096)          # the name is near the front; never the whole body
    except Exception:
        return 0
    try:
        record(tool_name(blob), profile())
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
