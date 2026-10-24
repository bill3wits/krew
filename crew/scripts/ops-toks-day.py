#!/usr/bin/env python3
"""One decode-speed sample per krew day for `ops_tok_s` (and `crew_tok_s` if registered).


and nobody ever did: 0 samples, so an idea tied to it could never be measured. The
engine already measures itself — vLLM logs "Avg generation throughput … Running: N"
every 10 s while it works — so this reads the unit's journal for the krew day (one
off-hours close to the next) and records ONE figure:

    p75 of the intervals with Running: 1, throughput > 0, AND THE OTHER ENGINE IDLE


while crew was idle and 30.9 while crew worked; crew 58.1 alone, 27.9 while ops worked.

under load from kanban right now. no way to account for that?"). So the KPI is the
ALONE figure, and the contended figure, the share of time contended and the engine's
own per-request counters (true decode and prefill speed, MTP acceptance) ride along
as context.

Why p75: an interval a request only half filled reads low, so the median swings with

63.7 / 60.4 / 62.6 — inside the 5 % the outcomes check calls flat. Fewer than
--min-n intervals is not a sample: nothing is recorded and the run says so (rc 3).

  scripts/ops-toks-day.py                  # print today's figure, record nothing
  scripts/ops-toks-day.py --record         # record once per krew day (idempotent)
  scripts/ops-toks-day.py --lane crew --record

Started before the Krew Daily build (krew-daily.service ExecStartPre). State for
the page: <improvements>/ops-toks-day.json. Off: OPS_TOKS_DAY=0.
This is NOT the Control Panel's "Speed check" (4 concurrent 48-token callers, SSE
deltas per second, kept in memory): that measures the queue under load, this the
single-stream decode speed a model or engine change moves.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))
import krew_daily  # noqa: E402  (day_bounds: the krew day runs close to close)
from architect_lib import IMPROVE_DIR  # noqa: E402
from vllm_log import LANES  # noqa: E402

KPI = {"ops": "ops_tok_s", "crew": "crew_tok_s"}
STATE = IMPROVE_DIR / "ops-toks-day.json"
LINE = re.compile(r"^(\S+) .*generation throughput: ([\d.]+) tokens/s, Running: (\d+)")
MIN_N = 60
NEAR_S = 7.0        # the engines log every 10 s on their own clocks: "same moment" = within 7 s
OTHER = {"ops": "crew", "crew": "ops"}
HOW = ("scripts/ops-toks-day.py --record: p75 of the engine's 10 s generation-throughput intervals with Running: 1 "
       "AND the other engine idle (both share one GPU), over the krew day (journal). Not the CP Speed check.")


def intervals(unit: str, since: dt.datetime, until: dt.datetime) -> list[tuple[float, float, int]]:
    """(epoch, generation tok/s, running) per 10 s engine line."""
    p = subprocess.run(["journalctl", "--user", "-u", unit, "--no-pager", "-o", "short-iso",
                        "--since", since.strftime("%Y-%m-%d %H:%M:%S"), "--until", until.strftime("%Y-%m-%d %H:%M:%S")],
                       capture_output=True, text=True, timeout=120, check=False)
    out = []
    for m in map(LINE.search, p.stdout.splitlines()):
        if m:
            try:
                out.append((dt.datetime.fromisoformat(m.group(1)).timestamp(), float(m.group(2)), int(m.group(3))))
            except ValueError:
                continue
    return out


def _pct(v: list[float], p: float):
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def figure(rows: list[tuple[float, float, int]], other: list[tuple[float, float, int]] | None = None,
           *, min_n: int = MIN_N) -> dict:
    """The KPI (`value` = p75 alone, None when too few) and its context. Pure."""
    import bisect
    ots = [t for t, _, _ in (other or [])]
    alone, contended = [], []
    for t, g, r in rows:
        if r != 1 or g <= 0:
            continue
        i = bisect.bisect_left(ots, t)
        near = [other[j] for j in (i - 1, i) if 0 <= j < len(ots) and abs(ots[j] - t) <= NEAR_S] if other else []
        (contended if any(o[2] >= 1 and o[1] > 0 for o in near) else alone).append(g)
    alone.sort(); contended.sort()
    one = sorted(alone + contended)
    busy = [(g, r) for _, g, r in rows if r >= 1]
    multi = sorted(g for g, r in busy if r > 1 and g > 0)
    return {"n": len(alone), "intervals": len(rows), "p50": _pct(alone, .5), "p75": _pct(alone, .75), "p90": _pct(alone, .9),
            "value": _pct(alone, .75) if len(alone) >= min_n else None,
            "contended_n": len(contended), "contended_p75": _pct(contended, .75),
            "contended_pct": round(100 * len(contended) / len(one)) if one else None,
            "all_p75": _pct(one, .75),
            "avg_running": round(sum(r for _, r in busy) / len(busy), 2) if busy else None,
            "max_running": max((r for _, r in busy), default=None),
            "shared_pct": round(100 * sum(1 for _, r in busy if r > 1) / len(busy)) if busy else None,
            "shared_total_p75": multi[min(len(multi) - 1, int(.75 * len(multi)))] if len(multi) >= 20 else None}


def counters(lane: str) -> dict:
    """The engine's own per-request truth since it started: decode and prefill speed,
    MTP acceptance. Not diluted by idle gaps the way a 10 s log average is."""
    import urllib.request
    try:
        raw = urllib.request.urlopen(LANES[lane]["metrics"], timeout=4).read().decode()
    except Exception:  # noqa: BLE001
        return {}
    m: dict[str, float] = {}
    for line in raw.splitlines():
        if line.startswith("#") or 'le="' in line or " " not in line:
            continue
        left, val = line.rsplit(" ", 1)
        try:
            m[left.split("{", 1)[0]] = m.get(left.split("{", 1)[0], 0.0) + float(val)
        except ValueError:
            continue
    g = lambda k: m.get("vllm:" + k, 0.0)   # noqa: E731
    dec, pre, n = g("request_decode_time_seconds_sum"), g("request_prefill_time_seconds_sum"), g("request_decode_time_seconds_count")
    if not dec or not n:
        return {}
    draft = g("spec_decode_num_draft_tokens_total")
    return {"requests": int(n), "decode_tok_s": round(g("generation_tokens_total") / dec, 1),
            "prefill_tok_s": round(g("prompt_tokens_total") / pre) if pre else None,
            "prompt_tok_per_req": round(g("prompt_tokens_total") / n), "output_tok_per_req": round(g("generation_tokens_total") / n),
            "mtp_accept_pct": round(100 * g("spec_decode_num_accepted_tokens_total") / draft) if draft else None,
            "scope": "since the engine started"}


def already(kpi_id: str, day: str, path: Path | None = None) -> bool:
    try:
        row = (json.loads((path or IMPROVE_DIR / "kpis.json").read_text()).get("kpis") or {}).get(kpi_id) or {}
    except (OSError, ValueError):
        return False
    return any(f"day={day}" in str(h.get("note") or "") for h in row.get("history") or [])


def registered(kpi_id: str) -> bool:
    try:
        return kpi_id in (json.loads((IMPROVE_DIR / "kpis.json").read_text()).get("kpis") or {})
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lane", choices=("ops", "crew", "both"), default="both")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--min-n", type=int, default=MIN_N)
    a = ap.parse_args(argv)
    if os.environ.get("OPS_TOKS_DAY", "1").strip().lower() in ("0", "false", "no", "off"):
        print("ops-toks-day: off switch set")
        return 0
    now = dt.datetime.now()
    since, _ = krew_daily.day_bounds(now, None)
    day = since.date().isoformat()
    out, rc = {"day": day, "since": since.isoformat(timespec="minutes"), "at": now.isoformat(timespec="seconds"), "lanes": {}}, 0
    rows = {lane: intervals(LANES[lane]["unit"], since, now) for lane in ("ops", "crew")}
    for lane in (("ops", "crew") if a.lane == "both" else (a.lane,)):
        kid = KPI[lane]
        f = figure(rows[lane], rows[OTHER[lane]], min_n=a.min_n)
        f["engine"] = counters(lane)
        f["kpi"] = kid
        if f["value"] is None:
            f["status"] = f"not a sample: {f['n']} alone single-stream intervals, need {a.min_n}"
            rc = max(rc, 3)
        elif not a.record:
            f["status"] = "measured, not recorded (no --record)"
        elif not registered(kid):
            f["status"] = f"measured, not recorded: {kid} is not a registered KPI"
        elif already(kid, day):
            f["status"] = "already recorded for this install day"
        else:
            note = (f"day={day} p75 of {f['n']} single-stream intervals with the other engine idle (p50 {f['p50']}, p90 {f['p90']}); "
                    f"contended {f['contended_pct']}% of the time at p75 {f['contended_p75']}; "
                    f"load: avg {f['avg_running']} concurrent, max {f['max_running']}, shared {f['shared_pct']}% of busy time"
                    + (f", {f['shared_total_p75']} tok/s total when shared" if f["shared_total_p75"] else ""))
            p = subprocess.run([sys.executable, str(HERE / "krew-kpi.py"), "record", kid, str(f["value"]), "--note", note],
                               capture_output=True, text=True, timeout=60, check=False)
            f["status"] = "recorded" if p.returncode == 0 else f"record failed: {(p.stderr or p.stdout).strip()[:120]}"
            rc = max(rc, 0 if p.returncode == 0 else 2)
        out["lanes"][lane] = f
        e = f["engine"]
        print(f"{lane}: ALONE {f['value']} tok/s (n={f['n']}, p50 {f['p50']}, p90 {f['p90']}) | while the other engine works "
              f"{f['contended_p75']} (n={f['contended_n']}, {f['contended_pct']}% of the time) | avg {f['avg_running']} concurrent, max {f['max_running']}, "
              f"{f['shared_total_p75']} total when shared | engine counters: decode {e.get('decode_tok_s')} tok/s per request, "
              f"prefill {e.get('prefill_tok_s')} tok/s, MTP {e.get('mtp_accept_pct')}% — {f['status']}")
    if a.record or not STATE.is_file():
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=2) + "\n")
        tmp.replace(STATE)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
