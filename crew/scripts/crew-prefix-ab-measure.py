#!/usr/bin/env python3
"""Crew prefix-cache A/B — one row per call, appended to a JSONL, plus a
summary table for the lock-in doc.

the User 2026-09-18 "prefix cache test approved": crew :8005 restarted 16:59:34
with enable_prefix_caching=True, speculative_config=None (MTP off). This
script is the measurement, never the knob: it reads crew /metrics, the board's
task_runs and the gate log, and writes ~/.hermes/logs/crew-prefix-ab.jsonl.

  scripts/crew-prefix-ab-measure.py            # one sample + a summary since the restart
  scripts/crew-prefix-ab-measure.py --since '2026-09-18 16:59'
  scripts/crew-prefix-ab-measure.py --summary  # table only, from the JSONL

TWO CLOCKS, on purpose and now labelled: the ENGINE columns (hit ratio, prefill, prompt
tokens, requests) are cumulative since the engine process started and reset on a crew
restart; salad_ticks and the run counts are since `--since`. Rows either side of a restart
are not one series — group them by `engine_started` before comparing anything.

Columns: prefix hit ratio (hits/queries since engine start), prefill s per
request, prompt tokens per request, runs / crashed / timed_out / completed in
the window, salad ticks in the gate log, gen tokens per prompt token.
Judge with docs/KANBAN-GRAND-SCHEME-20260918.md §9 rows 1–3 and 10; the
salad relapse remedy is the gate's (one restart-window card), not this file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HOME = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home())))
METRICS = os.environ.get("KREW_CREW_METRICS", "http://127.0.0.1:8005/metrics")
OUT = Path(os.environ.get("KREW_PREFIX_AB_LOG", str(KH.hermes_home() / "logs/crew-prefix-ab.jsonl")))
GATE_LOG = Path(os.environ.get("KREW_BOARD_GATE_LOG", str(KH.hermes_home() / "logs/kanban-board-gate.log")))
KANBAN = KH.kanban_dir()

WANT = ("prompt_tokens_total", "generation_tokens_total", "prefix_cache_queries_total",
        "prefix_cache_hits_total", "request_prefill_time_seconds_sum",
        "request_prefill_time_seconds_count", "num_requests_running", "num_requests_waiting")


def metrics() -> dict:
    out: dict = {}
    try:
        with urllib.request.urlopen(METRICS, timeout=4) as r:
            for ln in r.read().decode(errors="replace").splitlines():
                m = re.match(r"^vllm:([a-z_]+)\{[^}]*model_name=\"crew\"[^}]*\}\s+([0-9.eE+-]+)", ln)
                if m and m.group(1) in WANT:
                    out[m.group(1)] = float(m.group(2))
                    continue
                # Not a vllm: metric and carries no labels — prometheus' own per-process
                # stamp. It is what tells two rows apart across a crew restart.
                ps = re.match(r"^process_start_time_seconds\s+([0-9.eE+-]+)", ln)
                if ps:
                    out["process_start_time_seconds"] = float(ps.group(1))
    except Exception as e:  # engine down is a row, not a crash
        out["error"] = str(e)[:120]
    return out


def board_db() -> Path | None:
    cur = KANBAN / "current"
    try:
        slug = cur.read_text().strip()
    except OSError:
        return None
    p = KANBAN / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def runs_since(since_ts: int) -> dict:
    db = board_db()
    if not db:
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = conn.execute(
        "select count(*), sum(outcome='completed'), sum(outcome='crashed'), sum(outcome='timed_out'), "
        "sum(outcome='blocked'), round(avg(ended_at-started_at)) from task_runs where started_at>=?",
        (since_ts,)).fetchone()
    return {"runs": row[0] or 0, "completed": row[1] or 0, "crashed": row[2] or 0,
            "timed_out": row[3] or 0, "blocked": row[4] or 0, "avg_s": row[5]}


def salad_ticks(since: datetime) -> int:
    n = 0
    try:
        for ln in GATE_LOG.read_text(errors="replace").splitlines()[-4000:]:
            m = re.search(r"salad=(\d+)", ln)
            if not m or int(m.group(1)) == 0:
                continue
            ts = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", ln)
            if ts and datetime.fromisoformat(ts.group(1).replace(" ", "T")) >= since:
                n += 1
    except OSError:
        pass
    return n


def sample(since: datetime) -> dict:
    m = metrics()
    q, h = m.get("prefix_cache_queries_total", 0.0), m.get("prefix_cache_hits_total", 0.0)
    n = m.get("request_prefill_time_seconds_count", 0.0) or 0.0
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "since": since.isoformat(timespec="minutes"),
        "prefix_hit_ratio": round(h / q, 3) if q else None,
        "prefill_s_per_req": round(m.get("request_prefill_time_seconds_sum", 0.0) / n, 2) if n else None,
        "prompt_tok_per_req": round(m.get("prompt_tokens_total", 0.0) / n) if n else None,
        "gen_per_prompt": round(m.get("generation_tokens_total", 0.0) / max(1.0, m.get("prompt_tokens_total", 1.0)), 4),
        "requests": int(n),
        "running": m.get("num_requests_running"), "waiting": m.get("num_requests_waiting"),
        "salad_ticks": salad_ticks(since),
        # WHICH ENGINE LIFE (2026-09-22). This row carries TWO clocks: the engine columns
        # above are cumulative since ENGINE START (vLLM's counters are per-process and reset
        # on a restart), while salad_ticks and the run counts below are since `--since`. So
        # two rows either side of a crew restart are NOT one series, and reading the file as
        # one made the 09-19 rows (hit ~0.89, a different process) look like a decline into
        # today's 0.78. crew restarted 2026-09-20 22:36 and nothing in the file said so.
        # `process_start_time_seconds` is prometheus' own per-process stamp, so a reader can
        # group rows by engine life without trusting the label.
        "engine_started": (datetime.fromtimestamp(m["process_start_time_seconds"]).isoformat(timespec="seconds")
                           if m.get("process_start_time_seconds") else None),
        "engine_error": m.get("error"),
    }
    row.update(runs_since(int(since.timestamp())))
    return row


def summary(rows: list[dict]) -> str:
    if not rows:
        return "no samples"
    hdr = "| ts | hit ratio | prefill s/req | prompt tok/req | requests | runs | done | crashed | timed out | salad ticks |"
    lines = [hdr, "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['ts'][11:16]} | {r.get('prefix_hit_ratio')} | {r.get('prefill_s_per_req')} | "
                     f"{r.get('prompt_tok_per_req')} | {r.get('requests')} | {r.get('runs', '')} | {r.get('completed', '')} | "
                     f"{r.get('crashed', '')} | {r.get('timed_out', '')} | {r.get('salad_ticks')} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-09-18 16:59", help="window start (local), default = the cache-ON restart")
    ap.add_argument("--summary", action="store_true", help="print the table from the JSONL, take no sample")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    since = datetime.fromisoformat(args.since.replace(" ", "T"))
    rows: list[dict] = []
    if OUT.is_file():
        rows = [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()]
    if not args.summary:
        row = sample(since)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        rows.append(row)
        if args.json:
            print(json.dumps(row, indent=1))
            return 0
    print(summary(rows[-24:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
