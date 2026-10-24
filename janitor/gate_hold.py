"""After a hermes update, a gate whose tests fail runs report-only for 24 h (the User 09-28).

Every hermes update so far broke a house gate under a green update: 09-26 (the board gate's
`worker_started_at` became a fingerprint and every tick raised for two hours) and the `crashed`
status nobody writes. The patch queue's own tests only cover the patches. So `hermes-tree
update`, while the house timers are still stopped, runs each gate's suite (`check`), and a gate
whose suite fails is held REPORT-ONLY (its `--dry-run`) for 24 h through this file; the gate
asks `report_only(<gate>)` at start. A passing suite clears its hold.

The decomposer has no report mode: its hold is recorded and printed, and the fix is by hand
(`kanban.auto_decompose: false`).

Kills: KREW_GATE_UPDATE_HOLD=0 (gates ignore the file), KREW_POST_UPDATE_TESTS=0 (no check).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import sys as _sys
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))
import krew_settings  # noqa: E402


def _cfg(section: str) -> dict:
    """One section of $KREW_HOME/janitor.yaml, or of $KREW_JANITOR_CONFIG (config/janitor.example.yaml lists every key)."""
    return krew_settings.load(section, path=os.environ.get("KREW_JANITOR_CONFIG") or krew_settings.krew_home() / "janitor.yaml")


def _p(v) -> Path:
    return Path(os.path.expanduser(str(v)))

HOLD_NAME = "gate-report-only.json"
HOLD_H = float(os.environ.get("KREW_GATE_UPDATE_HOLD_H", "24") or 24)

# gate -> its suites (repo-relative). A suite that is missing is skipped, never a failure.
SUITES: dict[str, tuple[str, ...]] = {
    "kanban-board-gate": ("tests/test_kanban_board_gate.py", "tests/test_kanban_board_gate_zoey_down.py",
                          "tests/test_kanban_cause.py", "tests/test_gate_strikes.py",
                          "tests/test_gate_janitor_topology.py"),
    "kanban-loop": ("tests/test_kanban_loop.py", "tests/test_kanban_loop_archive_verb.py",
                    "tests/test_kanban_loop_done_verb.py", "tests/test_kanban_loop_ruler_reframe.py"),
    "decomposer": ("tests/test_ada_decomposer_key.py",),
}
# krew: `gate_hold.suites` in $KREW_HOME/janitor.yaml replaces the table above when it is set; `gate_hold.repo` is
# where those paths are read from (the reference install's values are the defaults).
if _cfg("gate_hold").get("suites"):
    SUITES = {str(k): tuple(v or ()) for k, v in (_cfg("gate_hold").get("suites") or {}).items()}
SUITE_ROOT = _p(_cfg("gate_hold").get("repo") or ".")


def hold_path() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / HOLD_NAME


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def report_only(gate: str, *, now: float | None = None, path: Path | None = None) -> str:
    """The reason `gate` must run report-only now, or '' when it may act."""
    if os.environ.get("KREW_GATE_UPDATE_HOLD", "1").strip().lower() in ("0", "off", "false", "no"):
        return ""
    e = _read(path or hold_path()).get(gate)
    now = time.time() if now is None else now
    if isinstance(e, dict) and float(e.get("until") or 0) > now:
        return str(e.get("why") or "held after a hermes update")
    return ""


def _clean_env() -> dict:
    # never the live board from a test (CLAUDE.md 09-27: a worker's shell carries HERMES_KANBAN_DB)
    return {k: v for k, v in os.environ.items() if not k.startswith("HERMES_KANBAN")}


def check(repo: Path, *, label: str = "", now: float | None = None, path: Path | None = None,
          run=subprocess.run, suites: dict[str, tuple[str, ...]] | None = None) -> dict:
    """Run each gate's suites; hold a failing gate report-only for HOLD_H hours, clear a passing
    one. Returns {gate: "pass" | "FAIL (held until HH:MM)" | "no suites"}."""
    if os.environ.get("KREW_POST_UPDATE_TESTS", "1").strip().lower() in ("0", "off", "false", "no"):
        return {}
    now = time.time() if now is None else now
    path = path or hold_path()
    holds = {k: v for k, v in _read(path).items()
             if isinstance(v, dict) and float(v.get("until") or 0) > now}
    out: dict[str, str] = {}
    for gate, files in (suites or SUITES).items():
        present = [f for f in files if (repo / f).is_file()]
        if not present:
            out[gate] = "no suites"
            continue
        try:
            r = run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *present],
                    cwd=str(repo), capture_output=True, text=True, timeout=900, env=_clean_env())
            rc, tail = r.returncode, ((r.stdout or "") + (r.stderr or ""))[-400:]
        except (OSError, subprocess.SubprocessError) as exc:
            rc, tail = 1, f"{type(exc).__name__}: {exc}"[:400]
        if rc == 0:
            holds.pop(gate, None)
            out[gate] = "pass"
            continue
        until = now + HOLD_H * 3600
        holds[gate] = {"until": until, "since": now, "tail": tail,
                       "why": f"its tests failed after hermes update {label}".strip()}
        out[gate] = f"FAIL (held report-only until {time.strftime('%m-%d %H:%M', time.localtime(until))})"
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(holds, indent=1) + "\n")
        tmp.replace(path)
    except OSError as exc:
        out["_write"] = f"hold file not written: {exc}"
    return out
