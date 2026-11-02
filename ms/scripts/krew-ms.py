#!/usr/bin/env python3
"""krew-ms: M&S for this machine. Discovery first (the map), then the M&S report.

    python3 ms/scripts/krew-ms.py                  # map + report (report only unless upgrades: true)
    python3 ms/scripts/krew-ms.py --discover-only  # the map only: units, endpoints, envs, GPUs, models, Hermes
    python3 ms/scripts/krew-ms.py --survey         # map + the fast drift check
    python3 ms/scripts/krew-ms.py --where          # where settings, reports and the ledger live
    python3 ms/scripts/krew-ms.py --nightly        # what krew-ms-nightly.service runs: map + report (+ --apply-green
                                                   # when upgrades: true), then the job's status line for the ticker
Every other flag goes to ms-trail-run.py unchanged (`--help` lists them).

Settings: $KREW_HOME/ms.yaml (default ~/.krew/ms.yaml; start from config/ms.example.yaml). With none, M&S is empty
and learns the machine by discovery: every env discovery finds and ms.yaml does not name is inventoried report-only.
`upgrades: false` (the default) refuses every flag that changes an env and says so on screen and in the log.
Reports, manifests and the decisions ledger live in the state dir (default $KREW_HOME/ms).

v2 (preview, not today's behaviour): discovery is read-only and kept as built, but the reference install does not
use it; it runs the trail on ms.yaml's own lists (`--no-discover`). Upgrades beyond what the reference install does
tonight (the green apply) are v2 too: the Stage D rebuild (env_rebuild) is not lifted.
"""
from __future__ import annotations

import pathlib

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / "lib"
TRAIL = HERE / "ms-trail-run.py"


def _log(state: Path, line: str, *, screen: bool = True) -> None:
    if screen:
        print(line, flush=True)
    try:
        state.mkdir(parents=True, exist_ok=True)
        with (state / "krew-ms.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
    except OSError as e:
        print(f"(could not write {state / 'krew-ms.log'}: {e})", flush=True)


def _python_with_packaging(hermes_bin: str) -> str:
    """M&S classifies versions with `packaging`. If this python lacks it, the Hermes env's python has it."""
    try:
        import packaging  # noqa: F401
        return ""
    except ImportError:
        pass
    import ms_discover as D
    py = D._shebang_python(hermes_bin) if Path(hermes_bin).is_file() else ""
    # a venv's python is a symlink to the same binary as this one: compare the paths, not their targets
    if py and Path(py).exists() and os.path.abspath(py) != os.path.abspath(sys.executable):
        import subprocess
        if subprocess.run([py, "-c", "import packaging"], capture_output=True).returncode == 0:
            return py
    return ""


def main(argv: list[str]) -> int:
    off = pathlib.Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew")) / "krew-ms.off"
    if off.is_file() and "--where" not in (argv or []):
        print(f"krew-ms: off by its switch ({off}); remove the file to run it")
        return 0
    if ("-h" in argv or "--help" in argv) and not os.environ.get("KREW_MS_REEXEC"):
        print((__doc__ or "").strip() + "\n\nms-trail-run.py's own flags:\n")
        sys.stdout.flush()
    discover_only = "--discover-only" in argv
    no_discover = "--no-discover" in argv
    nightly = "--nightly" in argv
    rest = [a for a in argv if a not in ("--discover-only", "--no-discover", "--nightly")]
    if discover_only and no_discover:
        print("--discover-only and --no-discover together: nothing to do")
        return 2
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    import ms_settings as S
    import ms_discover as D

    state = S.state_dir()
    ctx = D.Ctx()
    os.environ.setdefault("HERMES_BIN", D.hermes_bin(ctx))
    if not discover_only and not os.environ.get("KREW_MS_REEXEC"):
        py = _python_with_packaging(os.environ["HERMES_BIN"])
        if py:
            _log(state, f"{sys.executable} has no `packaging`: running M&S with the Hermes env's python {py}")
            os.environ["KREW_MS_REEXEC"] = "1"
            os.execv(py, [py, str(Path(__file__).resolve()), *argv])
    if not S.upgrades_on():
        line = S.UPGRADES_OFF_LINE.format(S.settings_file())
        if discover_only:
            _log(state, line)
        else:                                  # the trail prints it on screen; the log keeps it too
            _log(state, line, screen=False)
    if nightly:
        _log(state, "nightly: steps of the reference install's nightly that krew does not run here: the empty-room "
                    "camera reference capture (Crissa), the reference install's own test battery (not shipped), the docs-index night "
                    "(krew docs lane)")
        if S.upgrades_on() and "--apply-green" not in rest:
            rest.append("--apply-green")
    quick = any(a in rest for a in ("--where", "--help", "-h", "--record-decision", "--record-finding",
                                    "--annotate", "--register-env", "--restore-plan"))
    if not no_discover and not quick:
        cfg = D.settings(S)
        reg = D.refresh(state, ctx, cfg, by="krew-ms")
        print(D.render(reg).split("\n## ")[0].rstrip(), flush=True)
        _log(state, f"map: {len(reg['items'])} items, {sum(1 for c in reg['changes'] if c['by'] == 'krew-ms' and c['ts'] == reg['updated'])} "
                    f"new/removed now, {len(reg['not_scanned'])} not scanned, {len(reg['dropped'])} dropped -> "
                    f"{D.map_dir(state) / 'map.md'}")
        if discover_only:
            return 0
        configured = S.SETTINGS.setdefault("envs", {})
        known_py = {str(Path(S.expand(r.get("python"))).resolve()) for r in configured.values()
                    if isinstance(r, dict) and r.get("python")}
        added = []
        for it in reg["items"]:
            if it["kind"] != "env" or it["id"] in configured or it["id"] in (S.get("retired_envs") or []):
                continue
            if str(Path(it["python"]).resolve()) in known_py:
                continue
            configured[it["id"]] = {"python": it["python"], "role": f"discovered: {it['how']}", "apply": "report"}
            added.append(it["id"])
        if added:
            _log(state, "envs from discovery (report only, not in ms.yaml): " + ", ".join(added))
    elif discover_only:
        print("--discover-only does not combine with " + " ".join(a for a in rest if a.startswith("-")))
        return 2

    spec = importlib.util.spec_from_file_location("ms_trail_run", TRAIL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ms_trail_run"] = mod
    old_argv = sys.argv
    sys.argv = [str(TRAIL), *rest]
    try:
        spec.loader.exec_module(mod)
        try:
            rc = mod.main()
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old_argv
    try:   # the ledger exists from the first run (its own writer, its own format: ms_decisions/v1)
        if not quick and hasattr(mod, "DECISIONS_PATH") and not Path(mod.DECISIONS_PATH).exists():
            mod.save_decisions(mod._load_decisions())
            _log(state, f"ledger started: {mod.DECISIONS_PATH} (no decisions yet)")
    except Exception as e:  # noqa: BLE001
        _log(state, f"ledger not started: {e.__class__.__name__}: {e}")
    dropped = dict(getattr(mod, "DROPPED", {}) or {})
    for item, why in dropped.items():
        _log(state, f"M&S dropped {item}: {why}")
    if not quick:
        try:
            p = D.map_dir(state) / "map.json"
            reg = json.loads(p.read_text(encoding="utf-8"))
            reg["trail_dropped"] = [{"item": k, "why": v} for k, v in dropped.items()]
            p.write_text(json.dumps(reg, indent=1) + "\n", encoding="utf-8")
        except (OSError, ValueError):
            pass
    rc = int(rc or 0)
    if nightly:
        _status_line(state, rc)
    return rc


def _status_line(state: Path, rc: int) -> None:
    """The job's own word for krew-window-tick (status_line "ms-nightly"): ok, or failed with the reason."""
    win = HERE.parent.parent / "window" / "lib"
    if str(win) not in sys.path:
        sys.path.insert(0, str(win))
    try:
        import job_status
    except ImportError as e:
        _log(state, f"status line not written: {e}")
        return
    day = os.environ.get("KREW_DAY") or time.strftime("%Y-%m-%d")
    rep = state / "reports" / "ms-trail-LATEST.md"
    if rc == 0:
        p = job_status.write("ms-nightly", day, "ok", "", str(rep))
    else:
        p = job_status.write("ms-nightly", day, "failed", f"M&S trail exit {rc}; see {state / 'krew-ms.log'}", str(rep))
    _log(state, f"status line: {p}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
