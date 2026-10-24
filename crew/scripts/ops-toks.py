#!/usr/bin/env python3
"""Ops/crew tok/s for Crissa's report. Bash runs this. Crissa does not.

Live number is Control Panel Crew tab :8086 — the same Engine line as
journalctl (Avg generation throughput). Idle windows (Running: 0) are not
a keep-sample.

  python3 scripts/ops-toks.py                  # ops + crew JSON
  python3 scripts/ops-toks.py --lane ops
  python3 scripts/ops-toks.py --lane ops --record
  python3 scripts/ops-toks.py --lane ops --probe   # quiet-lane 400 tok; hitch

--probe is bench/nvfp4-speed/speed_probe.py. Use it for a keep A/B, not
for the morning brief.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from vllm_log import snapshot  # noqa: E402

ROOT = HERE.parent
LOG_URL = "http://127.0.0.1:8086/v1/log"
KPI = {"ops": "ops_tok_s", "crew": "crew_tok_s"}


def from_8086(lane: str, n: int = 80) -> dict:
    url = f"{LOG_URL}?lane={lane}&n={n}"
    try:
        raw = urllib.request.urlopen(url, timeout=5).read().decode()
        rec = json.loads(raw)
        rec["source"] = url
        return rec
    except Exception as e:  # noqa: BLE001
        rec = snapshot(lane, n)
        rec["source"] = rec.get("log_path") or "journalctl"
        rec["error"] = rec.get("error") or f"8086-down:{type(e).__name__}"
        return rec


def pick_gen(rec: dict) -> tuple[float | None, str]:
    eng = rec.get("engine") or {}
    gen = eng.get("gen_tok_s")
    running = eng.get("running")
    if gen is None:
        return None, "no Engine throughput line yet"
    if running == 0:
        return float(gen), "idle (Running: 0) — last interval, not a keep-sample"
    return float(gen), f"running={running}"


def record_kpi(lane: str, value: float, note: str) -> int:
    kid = KPI.get(lane) or f"{lane}_tok_s"
    p = subprocess.run(
        [
            sys.executable,
            str(HERE / "krew-kpi.py"),
            "record",
            kid,
            str(value),
            "--note",
            note,
        ],
        cwd=str(ROOT),
        timeout=30,
        check=False,
    )
    return p.returncode


def run_probe(lane: str) -> dict:
    probe = ROOT / "bench" / "nvfp4-speed" / "speed_probe.py"
    out = Path("/tmp") / f"{lane}-speed.json"
    p = subprocess.run(
        [
            sys.executable,
            str(probe),
            "--lane",
            lane,
            "--fresh",
            "1",
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        timeout=600,
        check=False,
    )
    rec: dict = {"probe_rc": p.returncode, "out": str(out)}
    if out.is_file():
        try:
            rec["probe"] = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rec["probe_error"] = "not json"
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lane", choices=("ops", "crew", "both"), default="both")
    ap.add_argument("--record", action="store_true", help="write gen tok/s to krew-kpi")
    ap.add_argument(
        "--idle-ok",
        action="store_true",
        help="allow --record when Running: 0 (default: refuse)",
    )
    ap.add_argument(
        "--probe",
        action="store_true",
        help="quiet-lane speed_probe (hitches the brain; keep A/B only)",
    )
    args = ap.parse_args()
    lanes = ["ops", "crew"] if args.lane == "both" else [args.lane]
    report = []
    rc = 0
    for lane in lanes:
        rec = from_8086(lane)
        gen, note = pick_gen(rec)
        row = {
            "lane": lane,
            "gen_tok_s": gen,
            "prompt_tok_s": (rec.get("engine") or {}).get("prompt_tok_s"),
            "running": (rec.get("engine") or {}).get("running"),
            "note": note,
            "source": rec.get("source"),
        }
        if args.probe:
            if lane != args.lane and args.lane == "both":
                row["probe"] = "skip-both (pass --lane ops or crew)"
            else:
                row["probe"] = run_probe(lane)
                mean = None
                body = row["probe"].get("probe")
                if isinstance(body, dict):
                    results = body.get("results") or []
                    hit = next(
                        (x for x in results if isinstance(x, dict) and x.get("lane") == lane),
                        results[0] if results else None,
                    )
                    if isinstance(hit, dict):
                        mean = hit.get("fresh_tok_per_s_mean")
                if mean is not None:
                    row["probe_tok_s"] = mean
                    if args.record:
                        record_kpi(lane, float(mean), f"speed_probe {lane}")
        elif args.record:
            if gen is None:
                print(f"{lane}: no Engine line — not recorded", file=sys.stderr)
                rc = 2
            elif (rec.get("engine") or {}).get("running") == 0 and not args.idle_ok:
                print(
                    f"{lane}: idle gen={gen} tok/s — not recorded "
                    "(pass --idle-ok or --probe)",
                    file=sys.stderr,
                )
                rc = 2
            else:
                record_kpi(lane, float(gen), f"8086 {note}")
        report.append(row)
        print(
            f"{lane}: gen={gen} tok/s  prompt={row['prompt_tok_s']}  "
            f"{note}  src={row['source']}",
            flush=True,
        )
    print(json.dumps(report, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
