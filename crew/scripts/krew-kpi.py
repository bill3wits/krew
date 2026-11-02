#!/usr/bin/env python3
"""Krew KPI registry — the ops-efficiency charts read this.

Vera's unlock step (keep) names a kpi_id. Register it here so Sunday Ada
can keep/stop-tracking. Record samples ad hoc (vLLM speed_probe) or via
kanban-snapshot for cheap board gauges.

  python3 scripts/krew-kpi.py register --id ops_tok_s --name 'Ops decode tok/s' \\
      --unit tok/s --direction up --sample adhoc \\
      --how 'bench/nvfp4-speed/speed_probe.py --lane ops --out /tmp/ops-speed.json'
  python3 scripts/krew-kpi.py record ops_tok_s 132.4 --note 'after land'
  python3 scripts/krew-kpi.py list
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import (  # noqa: E402
    IMPROVE_DIR,
    KPI_SAMPLE_ADHOC,
    load_kpis,
    register_kpi,
)

FLEET_IMPROVE = Path("/usr/local/bin/fleet-improve")


def _record_value(kpi_id: str, value: float, note: str) -> int:
    """Prefer fleet-improve (writes history + current). Fallback: append here."""
    if FLEET_IMPROVE.is_file():
        env = os.environ.copy()
        env["FLEET_IMPROVE_DIR"] = str(IMPROVE_DIR)
        p = subprocess.run(
            [
                str(FLEET_IMPROVE),
                "record",
                kpi_id,
                str(value),
                "--note",
                note,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if p.returncode == 0:
            return 0
        print(p.stderr or p.stdout, file=sys.stderr)
        return p.returncode
    path = IMPROVE_DIR / "kpis.json"
    data = load_kpis(path)
    row = (data.get("kpis") or {}).get(kpi_id)
    if not isinstance(row, dict):
        print(f"ERROR: unknown kpi {kpi_id!r} — register first", file=sys.stderr)
        return 2
    import time

    hist = list(row.get("history") or [])
    old = row.get("current")
    hist.append(
        {
            "ts": time.time(),
            "value": float(value),
            "note": note,
            "old": old,
        }
    )
    row["history"] = hist
    row["current"] = float(value)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rg = sub.add_parser("register", help="add a KPI (no sample yet)")
    rg.add_argument("--id", required=True)
    rg.add_argument("--name", default="")
    rg.add_argument("--unit", default="")
    rg.add_argument("--direction", choices=("up", "down"), required=True)
    rg.add_argument("--how", required=True, help="how_measured (command or probe)")
    rg.add_argument(
        "--sample",
        choices=(KPI_SAMPLE_ADHOC, "snapshot"),
        default=KPI_SAMPLE_ADHOC,
    )
    rg.add_argument("--target", default=None)
    rec = sub.add_parser("record", help="append one measured value")
    rec.add_argument("kpi_id")
    rec.add_argument("value", type=float)
    rec.add_argument("--note", default="")
    sub.add_parser("list", help="print registered KPIs")
    args = ap.parse_args()
    if args.cmd == "register":
        target = args.target
        if target not in (None, ""):
            try:
                target = float(target)
            except ValueError:
                pass
        row = register_kpi(
            kpi_id=args.id,
            name=args.name or args.id,
            unit=args.unit,
            direction=args.direction,
            how_measured=args.how,
            sample=args.sample,
            target=target,
        )
        print(json.dumps(row, indent=2))
        return 0
    if args.cmd == "record":
        return _record_value(args.kpi_id, args.value, args.note)
    data = load_kpis()
    for kid, k in (data.get("kpis") or {}).items():
        if not isinstance(k, dict):
            continue
        n = len(k.get("history") or [])
        print(
            f"{kid:28} n={n:<4} sample={k.get('sample') or 'snapshot':8} "
            f"current={k.get('current')}  {k.get('how_measured') or ''}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
