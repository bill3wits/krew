"""Does Crissa's hands still have a body, after a hermes update? (2026-10-01)

hermes upstream 5e4a2a3d24 (09-12) made computer_use available only when hermes's own
package manager installed cua-driver, or when HERMES_CUA_DRIVER_CMD names the binary.
The house driver (~/.local/bin/cua-driver -> ~/.cua-driver/packages/current, its own
cua-driver.service, installed 09-02 outside hermes) is neither. So the 09-27 update
made computer_use read "unavailable" and it dropped from every hands kit, silently,
for six days - the last computer_use call was 09-25 20:39, and nobody said anything.

This module reads the two facts that tell the story, the way a session would read them:

  * `hermes -p crissa computer-use doctor` - the session's own view of the driver
  * scripts/krew-tool-tax.py --profile crissa --platform cli --json - the hands'
    kit, whose "unavailable" list must not name computer_use

`check()` returns a dict:

  {
    "ok": bool,
    "why": [..] or [],
    "doctor": {rc, lines} | {"error": why},
    "tax":    {"unavailable": [...], "computer_use": bool} | {"error": why},
  }

`fail_loud(out, by=...)` is the report-only alarm that a bad check owes. It never
throws and never blocks the caller (the update it rides on is already good); it
owes one line on the House Daily and one blocked card so the board carries it too.
A card is created at most once per day (state file) and only when the card
runner says so; either channel failing leaves the other in place, and what
happened to each is in `out["alarm"]`.

Constraints the caller keeps: the house cua daemon stays on X11 (CLAUDE.md, the
User 10-01 - never set CUA_DRIVER_RS_ENABLE_WAYLAND here), and no gateway restart
comes from this path (if one is ever owed, it goes through
lib/restart_window.owe_restart, which a caller may record).

A third fact, OFF by default (KREW_CUA_DOCTOR_GATEWAY=1 arms it, 2026-10-01): since the
09-27 multiplex cutover one host gateway (hermes-gateway.service) serves Crissa, and it reads
HERMES_CUA_DRIVER_CMD from its OWN process environment - a profile's .env is a secret scope
there, never os.environ - so the line in ~/.hermes/profiles/crissa/.env never reached it and the
host logged "check_computer_use_requirements returned False" at its 09:44 start. `run_gateway`
reads /proc/<MainPID>/environ of the unit that answers for Crissa (lib/hermes_topology.unit_for),
says whether the variable is there and resolves to an executable, and counts the live
hc-*.sock sessions hermes's embedded driver opens in the gateway's tmpdir (0 is normal at idle).
The fix is the Environment= drop-in config/systemd/user/hermes-gateway.service.d/cua-driver.conf,
live only after the restart window restarts the gateway.

Kill: KREW_CUA_DOCTOR=0 (check() reports {"ok": True, "off": True} and no
post-update step is wired).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)
from typing import Any, Callable
import krew_hermes as KH  # noqa: E402  where Hermes lives

HERMES_HOME = Path(os.environ.get("HERMES_REAL_HOME") or str(Path.home()))
HERMES_BIN = os.environ.get("KREW_HERMES_BIN", str(HERMES_HOME / ".local" / "bin" / "hermes"))
TAX = Path(__file__).resolve().parent.parent / "scripts" / "krew-tool-tax.py"
PROFILE = "crissa"
PLATFORM = "cli"
STATE = Path(os.environ.get("KREW_CUA_DOCTOR_STATE",
                            str(KH.hermes_home() / "cua-doctor-alarm.json")))


def _off() -> dict:
    return {"ok": True, "off": True, "why": [], "doctor": None, "tax": None}


def _runtime_python() -> str:
    """The interpreter the hermes launcher runs (hermes ships its own since 09-26)."""
    try:
        import hermes_runtime
        if hermes_runtime.home_is_the_install():
            py = hermes_runtime.runtime_python()
            if py:
                return py
    except Exception:  # noqa: BLE001
        pass
    return "/usr/bin/python3"


def run_doctor(*, hermes: str = HERMES_BIN, timeout: int = 60) -> dict:
    """`hermes -p crissa computer-use doctor`, measured. rc != 0 or a crash is the fail."""
    try:
        r = subprocess.run([hermes, "-p", PROFILE, "computer-use", "doctor"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    out = (r.stdout or "") + (r.stderr or "")
    return {"rc": r.returncode, "lines": [l.strip() for l in out.splitlines() if l.strip()][:12]}


def run_tool_tax(*, tax_script: Path = TAX, runner: Callable = None,
                 timeout: int = 240) -> dict:
    """The hands' kit as a session builds it; the `unavailable` list is the fact we need."""
    def _run(argv, **kw):
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    _rx = runner if callable(runner) else _run  # type: ignore[assignment]
    try:
        r = _rx([_runtime_python(), str(tax_script),
                 "--profile", PROFILE, "--platform", PLATFORM, "--json"])
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        return {"error": f"rc={r.returncode} {(r.stderr or 'no output').strip()[:160]}"}
    try:
        # --json emits pretty-printed multi-line JSON (indent=1): the fact is the
        # whole stdout, not the last line (that is just the closing brace).
        rep = json.loads(out)
    except ValueError:
        try:
            rep = json.loads(out.splitlines()[-1])
        except ValueError as e:
            return {"error": f"not json: {str(e)[:120]}"}
    d = rep.get(PROFILE) if isinstance(rep, dict) else None
    if isinstance(d, dict) and d.get("error"):
        return {"error": str(d["error"])[:200]}
    if not isinstance(d, dict):
        return {"error": f"no {PROFILE} profile in the tax"}
    return {"unavailable": [str(n) for n in d.get("unavailable") or []],
            "computer_use": "computer_use" in [r.get("tool") for r in d.get("rows") or []]}


CUA_ENV = "HERMES_CUA_DRIVER_CMD"


def gateway_check_on() -> bool:
    return os.environ.get("KREW_CUA_DOCTOR_GATEWAY", "0").strip().lower() in ("1", "true", "yes", "on")


def _gateway_unit() -> str:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "krew_own_hermes_topology", Path(__file__).resolve().parent / "hermes_topology.py")   # krew's own, by path
        hermes_topology = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hermes_topology)
        return hermes_topology.unit_for(PROFILE)
    except Exception:  # noqa: BLE001
        return "hermes-gateway.service"


def _main_pid(unit: str) -> int:
    r = subprocess.run(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"],
                       capture_output=True, text=True, timeout=5)
    try:
        return int((r.stdout or "0").strip() or 0)
    except ValueError:
        return 0


def _proc_env(pid: int) -> dict:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    out = {}
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep:
            out[k.decode(errors="replace")] = v.decode(errors="replace")
    return out


def run_gateway(*, unit: str | None = None, main_pid: Callable[[str], int] = None,
                proc_env: Callable[[int], dict] = None, which: Callable = None,
                sockets: Callable[[str], list] = None) -> dict:
    """The gateway's own environment: does the process that answers for Crissa carry the driver?"""
    import glob
    import shutil
    u = unit or _gateway_unit()
    try:
        pid = (main_pid or _main_pid)(u)
        if not pid:
            return {"unit": u, "error": "not running (MainPID 0)"}
        env = (proc_env or _proc_env)(pid)
    except (OSError, subprocess.SubprocessError) as e:
        return {"unit": u, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    cmd = (env.get(CUA_ENV) or "").strip()
    tmp = env.get("TMPDIR") or "/tmp"
    socks = (sockets or (lambda d: glob.glob(os.path.join(d, "hc-*.sock"))))(tmp)
    resolves = bool(cmd) and bool((which or shutil.which)(os.path.expanduser(cmd)))
    return {"unit": u, "pid": pid, "cmd": cmd or None, "resolves": resolves, "sessions": len(socks)}


def _gateway_reasons(gw: dict | None) -> list[str]:
    if gw is None:
        return []
    if "error" in gw:
        return [f"gateway {gw.get('unit')}: {gw['error']}"]
    if not gw.get("cmd"):
        return [f"gateway {gw.get('unit')} (pid {gw.get('pid')}) has no {CUA_ENV} in its environment"]
    if not gw.get("resolves"):
        return [f"gateway {gw.get('unit')}: {CUA_ENV}={gw['cmd']} is not an executable"]
    return []


def _reasons(doctor: dict | None, tax: dict | None) -> list[str]:
    why: list[str] = []
    if doctor is None:
        why.append("the computer-use doctor was not run (KREW_CUA_DOCTOR=0)")
    elif "error" in doctor:
        why.append(f"computer-use doctor could not run: {doctor['error']}")
    elif doctor.get("rc") not in (0, None):
        why.append(f"computer-use doctor failed (rc={doctor['rc']})")
    elif any("not installed" in l for l in doctor.get("lines") or []):
        why.append("computer-use doctor: the driver is not installed (or not named)")
    if tax is None:
        why.append("the tool tax was not run (KREW_CUA_DOCTOR=0)")
    elif "error" in tax:
        why.append(f"tool tax could not run: {tax['error']}")
    elif "computer_use" in (tax.get("unavailable") or []):
        why.append("the hands' kit lists computer_use as unavailable")
    elif not tax.get("computer_use"):
        why.append("the hands' kit resolves no computer_use tool at all")
    return why


def check(run_doctor_: Callable | None = None, run_tax: Callable | None = None,
          run_gateway_: Callable | None = None) -> dict:
    """The two facts (three when KREW_CUA_DOCTOR_GATEWAY=1), read. Never raises: a probe that
    cannot run says so in `why`. With the gateway check off the result is byte-for-byte today's."""
    if os.environ.get("KREW_CUA_DOCTOR", "1").strip().lower() in ("0", "false", "no", "off"):
        return _off()
    doctor: Any = None
    tax: Any = None
    try:
        _rd = run_doctor_ if callable(run_doctor_) else run_doctor
        _rt = run_tax if callable(run_tax) else run_tool_tax
        doctor = _rd()
        tax = _rt()
        why = _reasons(doctor, tax)
        out = {"ok": not why, "why": why, "doctor": doctor, "tax": tax}
        if gateway_check_on():
            try:
                gw = (run_gateway_ if callable(run_gateway_) else run_gateway)()
            except Exception as e:  # noqa: BLE001
                gw = {"unit": "?", "error": f"{type(e).__name__}: {str(e)[:120]}"}
            why = why + _gateway_reasons(gw)
            out.update(ok=not why, why=why, gateway=gw)
        return out
    except Exception as e:  # noqa: BLE001 - report-only by contract
        # A probe that raised mid-check is the alarm for that probe too: the
        # session cannot read a fact that throws. Say so in `why`, keep the
        # other probe's result where it landed.
        where = "the computer-use doctor"
        try:
            import traceback
            last = traceback.format_exc().strip().splitlines()[-1]
        except Exception:  # noqa: BLE001
            last = ""
        if "run_tax" in last or "run_tool_tax" in last:
            where = "the tool tax"
        why = [f"{where} could not run: {type(e).__name__}: {str(e)[:120]}"]
        return {"ok": False, "why": why, "doctor": doctor, "tax": tax}


def _daily_ok(path: Path, now: float) -> bool:
    try:
        row = json.loads(path.read_text())
        return row.get("day") == time.strftime("%Y-%m-%d", time.localtime(now))
    except (OSError, ValueError, TypeError):
        return False


def _record_alarm(path: Path, now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": now, "day": time.strftime("%Y-%m-%d", time.localtime(now))}) + "\n")


def make_card(title: str, body: str, **kw):
    """One blocked card via `hermes kanban create` (the established house pattern)."""
    argv = [
        HERMES_BIN,
        "kanban", "create", title,
        "--body", cut(body, 5500, "`hermes -p crissa computer-use doctor` gives the whole check"),
        "--assignee", "crissa",
        "--initial-status", "blocked",
        "--created-by", "cua-doctor",
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"kanban create rc={r.returncode}: {(r.stderr or r.stdout or '').strip()[:200]}")
    return (r.stdout or "").strip()[:120] or title[:40]


def fail_loud(out: dict, *, by: str = "hermes-tree update",
              card: Callable[..., object] | None = make_card,
              state: Path = STATE, now: float | None = None) -> dict:
    """The report-only alarm a bad check owes: a House Daily line (always) plus one
    blocked card a day. Nothing here throws, and nothing restarts anything - a
    restart owed from here goes through lib/restart_window.owe_restart."""
    now = float(now if now is not None else time.time())
    alarm = {"channel": "Krew Daily",
             "line": f"computer_use check failed after {by}: " + "; ".join(out.get("why") or ["?"])}
    result = {"daily": True, "card": "skipped"}
    try:
        if card is None:
            result["card"] = "off"
        elif _daily_ok(state, now):
            result["card"] = "already-alarmed-today"
        else:
            body = ("\n".join(out.get("why") or ["(no reason recorded)"])
                    + "\n\nThe check: `hermes -p crissa computer-use doctor` and\n"
                      "`scripts/krew-tool-tax.py --profile crissa --platform cli --json` "
                      "(computer_use must not be in `unavailable`). The krew driver stays on X11 "
                      "(CLAUDE.md); the fix is HERMES_CUA_DRIVER_CMD in the environment of the process\n"
                      "that serves Crissa (under the host gateway: the hermes-gateway.service.d/cua-driver.conf\n"
                      "drop-in, not her .env), not a driver change. lib/cua_doctor.fail_loud owes this row and card.")
            c = card(title="computer_use unavailable: the hands lost their body", body=body)
            _record_alarm(state, now)
            result["card"] = getattr(c, "id", None) or str(c)[:40]
    except Exception as e:  # noqa: BLE001 - the card is the SECOND channel; the row stays
        result["card"] = f"failed: {type(e).__name__}: {str(e)[:120]}"
    alarm["card"] = result["card"]
    return alarm
