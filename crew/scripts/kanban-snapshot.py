#!/usr/bin/env python3
"""Snapshot live kanban health + record krew KPIs.

Museum process: fleet-improve (KPI registry). Live board: hermes kanban stats.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import (  # noqa: E402
    MS_DIR,
    append_jsonl,
    kpi_history_n,
    kpi_insufficient,
    load_jsonl,
    open_ops_action_ids,
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HERMES = Path.home() / ".local" / "bin" / "hermes"
SNAP = MS_DIR / "kanban-snapshots.jsonl"
IMPROVE_DIR = MS_DIR / "improvements"
FLEET_IMPROVE = Path("/usr/local/bin/fleet-improve")


def _run(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(
            args, capture_output=True, text=True, timeout=30, check=False
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def stats_json() -> dict:
    rc, out = _run([str(HERMES), "kanban", "stats", "--json"])
    if rc != 0:
        return {"error": out.strip()[:400]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"error": "not json", "raw": out[:400]}


def diag_text() -> str:
    _rc, out = _run([str(HERMES), "kanban", "diagnostics"])
    return cut((out or "").strip(), 2000, "`hermes kanban diagnostics` gives it whole")


def record_kpi(kpi_id: str, value: float, note: str) -> None:
    if not FLEET_IMPROVE.is_file():
        return
    env = os.environ.copy()
    env["FLEET_IMPROVE_DIR"] = str(IMPROVE_DIR)
    subprocess.run(
        [str(FLEET_IMPROVE), "record", kpi_id, str(value), "--note", note],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _stamp_added_at(kpath: Path) -> None:
    try:
        data = json.loads(kpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    kpis = data.get("kpis") or {}
    changed = False
    for k in kpis.values():
        if not isinstance(k, dict) or k.get("added_at"):
            continue
        hist = k.get("history") or []
        ts = hist[0].get("ts") if hist and isinstance(hist[0], dict) else None
        if ts is None:
            continue
        try:
            k["added_at"] = datetime.fromtimestamp(float(ts)).date().isoformat()
            changed = True
        except (TypeError, ValueError, OSError):
            continue
    if changed:
        kpath.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def ensure_kpis() -> None:
    IMPROVE_DIR.mkdir(parents=True, exist_ok=True)
    kpath = IMPROVE_DIR / "kpis.json"
    if kpath.exists():
        _stamp_added_at(kpath)
        return
    seed = {
        "kpis": {
            "kanban_oldest_ready_sec": {
                "kpi_id": "kanban_oldest_ready_sec",
                "name": "Oldest ready card age",
                "unit": "sec",
                "direction": "down",
                "baseline": None,
                "target": 3600,
                "current": None,
                "added_at": datetime.now().date().isoformat(),
                "how_measured": "hermes kanban stats --json oldest_ready_age_seconds",
                "linked_proposals": [],
            },
            "kanban_blocked": {
                "kpi_id": "kanban_blocked",
                "name": "Blocked cards",
                "unit": "count",
                "direction": "down",
                "baseline": None,
                "target": 0,
                "current": None,
                "added_at": datetime.now().date().isoformat(),
                "how_measured": "stats.by_status.blocked",
                "linked_proposals": [],
            },
            "kanban_open": {
                "kpi_id": "kanban_open",
                "name": "Open cards (not done/archived)",
                "unit": "count",
                "direction": "down",
                "baseline": None,
                "target": None,
                "current": None,
                "added_at": datetime.now().date().isoformat(),
                "how_measured": "sum by_status except done",
                "linked_proposals": [],
            },
        }
    }
    kpath.write_text(json.dumps(seed, indent=2) + "\n")
    (IMPROVE_DIR / "proposals.jsonl").touch()
    (IMPROVE_DIR / "events.jsonl").touch()
    readme = IMPROVE_DIR / "README.md"
    if not readme.exists():
        readme.write_text(
            "Krew KPI store (rails). Museum loop: fleet-improve. "
            "Do not write to ~/agents/network — this dir is SoT.\n"
        )


def _current_board_db() -> Path | None:
    home = Path(os.environ.get("HERMES_HOME") or KH.hermes_home())
    try:
        slug = (home / "kanban" / "current").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    p = home / "kanban" / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def record_flow_kpis(note: str, *, since_h: float = 24.0) -> dict:
    ""


    import sqlite3
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    try:
        import kanban_kpis as kk
        from architect_lib import KPI_SAMPLE_SNAPSHOT, register_kpi
    except ImportError as e:
        return {"error": f"import: {e}"}
    db = _current_board_db()
    if db is None:
        return {"error": "no current board"}
    liveness = None
    lpath = KH.hermes_home() / "kanban-liveness.json"
    try:
        liveness = json.loads(lpath.read_text(encoding="utf-8")) if lpath.is_file() else None
    except (OSError, json.JSONDecodeError):
        liveness = None
    now = time.time()
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            k = kk.compute(con, now - since_h * 3600, now, liveness)
        finally:
            con.close()
    except sqlite3.Error as e:
        return {"error": f"sqlite: {e}"}
    for kid, (name, unit, direction, target) in kk.KPIS.items():
        try:
            register_kpi(kpi_id=kid, name=name, unit=unit, direction=direction,
                         how_measured="lib/kanban_kpis.compute over the current board, last 24 h",
                         sample=KPI_SAMPLE_SNAPSHOT, target=target)
        except (ValueError, OSError) as e:
            print(f"kpi register {kid}: {e}", file=sys.stderr)
    recorded = 0
    for kid, v in k.items():
        if v is None:
            continue
        record_kpi(kid, float(v), note)
        recorded += 1
    return {"kpis": k, "recorded": recorded, "liveness": bool(liveness)}


def main() -> int:
    ensure_kpis()
    st = stats_json()
    by = st.get("by_status") or {}
    blocked = int(by.get("blocked") or 0)
    open_n = sum(
        int(by.get(k) or 0)
        for k in ("triage", "todo", "scheduled", "ready", "running", "blocked")
    )
    oldest = st.get("oldest_ready_age_seconds")
    open_ids = open_ops_action_ids(load_jsonl(IMPROVE_DIR / "actions.jsonl"))
    kpis: dict = {}
    kpath = IMPROVE_DIR / "kpis.json"
    if kpath.is_file():
        try:
            kpis = json.loads(kpath.read_text(encoding="utf-8")).get("kpis") or {}
        except json.JSONDecodeError:
            kpis = {}
    hist_n = kpi_history_n(kpis)
    insuff = kpi_insufficient(hist_n)
    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "unix": time.time(),
        "stats": st,
        "diagnostics": diag_text(),
        "blocked": blocked,
        "open": open_n,
        "oldest_ready_age_seconds": oldest,
        "open_ops_action_ids": open_ids,
        "kpi_history_n": hist_n,
        "kpi_insufficient": insuff,
        "kpi_history_ok": bool(hist_n) and not insuff,
    }
    note = f"snapshot {row['ts']}"
    flow = record_flow_kpis(note) if os.environ.get("KANBAN_FLOW_KPIS", "1") != "0" else {"off": True}
    row["flow"] = flow
    append_jsonl(SNAP, row)
    if oldest is not None:
        record_kpi("kanban_oldest_ready_sec", float(oldest), note)
    record_kpi("kanban_blocked", float(blocked), note)
    record_kpi("kanban_open", float(open_n), note)
    # Ad-hoc KPIs (vLLM speed_probe) are sampled on land, not here.
    print(json.dumps({"blocked": blocked, "open": open_n, "oldest": oldest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
