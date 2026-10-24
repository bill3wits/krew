"""Control Panel unit plumbing: restart with a post-check, journals by scope,
and who may restart a system unit.

the User 2026-09-17, after the CP audit: "please add correct post-checks and wire
to logs … I would like to see the krew searches logs … give panel permission
if User FF verifies or in User mode. Grey out if not."

What the audit found, and what each piece here answers:

- Restart said "restarted" for anything. `_systemctl` swallowed the return
  code, so a polkit refusal (every system unit: hindsight, searxng,
  approvals-api) toasted success. `restart_job` queues the restart
  (`--no-block`, so a slow vLLM load cannot hold the request), then checks the
  EFFECT: a new InvocationID, active, still the same invocation after a
  settle (hindsight exits inside 2 s and would pass a one-shot look), and the
  unit's port answering.
- A system unit's tile read "-- No entries --": the journal was always
  `journalctl --user`. `read_journal` asks each scope for its own units, and
  appends the tail of a unit's StandardOutput/StandardError FILE when it
  writes one (hindsight's traceback lives only there).
- The Searxng tile opened the Searxng page. Its tape is now the krew's own
  searches, then Searxng's own journal. Reading those searches means reading the
  agent framework's own databases, so that reader stays with the install and never
  ships with the wall (2026-10-01).
- System units need a polkit rule (config/polkit/50-krew-panel-restart.rules)
  and the panel only uses it while the house says User: FaceFinder AUTH or
  Secure (sage — "Green = User mode", docs/session-notes-2026-09-05). The page
  greys the key from the same `restart_gate`.

Kill switch: KREW_CP_RESTART_POSTCHECK=0 → the old synchronous restart
(still honest about the return code).
"""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

POSTCHECK = os.environ.get("KREW_RESTART_POSTCHECK", "1") != "0"
SETTLE_S = float(os.environ.get("KREW_RESTART_SETTLE_S", "4"))
# How long a restarted unit may take to be active, and then to answer its
# port. The brains load for minutes; everything else is seconds.
ACTIVE_WAIT_S = 30.0
import krew_window_settings as _KWS
import text_cut
_RW = _KWS.load("restart-window")
PORT_WAIT_S = {str(k): float(v) for k, v in (_RW.get("port_wait_s") or {}).items()}
PORT_WAIT_DEFAULT_S = 45.0
# Units whose port is an HTTP health we can read; the rest are a TCP connect.
HEALTH_PATH = {str(k): str(v) for k, v in (_RW.get("health_path") or {}).items()}
# Restarting these blinks the wall. The page asks first.
WALL_UNITS = frozenset(_RW.get("wall_units") or [])
# The panel restarting itself cannot report on itself: it answers first.
SELF_UNIT = str(_RW.get("self_unit") or "")



def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def _sysctl(scope: str) -> list[str]:
    return ["systemctl"] + (["--user"] if scope == "user" else []) + ["--no-ask-password"]


# A unit keeps its own kind (the User 2026-10-01): "<name>.timer" became
# "<name>.timer.service", read not-found in the user scope, fell to SYSTEM, and the
# restart window's executor got "Access denied" for every .timer / .path row it owed.
UNIT_SUFFIXES = (".service", ".timer", ".path", ".socket", ".target", ".mount", ".scope", ".slice")


def unit_file(unit: str) -> str:
    """The systemd name: a bare name is a .service, a name with a unit suffix keeps it."""
    return unit if unit.endswith(UNIT_SUFFIXES) else unit + ".service"


def unit_props(unit: str, scope: str) -> dict:
    name = unit_file(unit)
    rc, out, _ = _run(_sysctl(scope) + [
        "show", name, "-p", "InvocationID", "-p", "ActiveState", "-p", "SubState",
        "-p", "Result", "-p", "NRestarts", "-p", "ExecMainStatus",
        "-p", "StandardOutput", "-p", "StandardError", "-p", "LoadState"])
    return dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)


# ── who may restart ───────────────────────────────────────────────────────
def user_signal(house: dict | None) -> tuple[bool, str]:
    """The house says User: FaceFinder AUTH, or Secure (sage = User mode)."""
    h = house or {}
    auth = h.get("auth") or {}
    if auth.get("user_auth") is True:
        return True, "User AUTH (" + str(auth.get("user_auth_tier") or "FaceFinder") + ")"
    pres = h.get("presence") or {}
    lamp = h.get("visit_lamp") or pres.get("visit_lamp")
    if lamp == "sage":
        return True, "Secure — User mode"
    return False, "needs User AUTH (FaceFinder) or Secure"


def restart_gate(house: dict | None) -> dict:
    """What the page reads to grey a system unit's Restart key."""
    ok, why = user_signal(house)
    return {"system_ok": ok, "why": why}


# ── restart + post-check ──────────────────────────────────────────────────
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _port_answers(unit: str, port: int | None, timeout: float = 1.5) -> bool:
    if not port:
        return True
    rows = _shared_rows()
    if rows:
        port = rows.port(unit, port)
    path = HEALTH_PATH.get(unit)
    if path:
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
                return 200 <= r.status < 300
        except Exception:  # noqa: BLE001
            return False
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _last_lines(unit: str, scope: str, n: int = 4) -> list[str]:
    return [ln for ln in read_journal([(unit, scope)], n=n, files=True).get("lines", [])
            if ln.strip()][-n:]


def postcheck(unit: str, scope: str, port: int | None, before_inv: str,
              clock=time.monotonic, sleep=time.sleep) -> dict:
    """Did the restart take? New invocation → active → same invocation after a
    settle → port answering. Returns {ok, state, note}."""
    t0 = clock()
    p: dict = {}
    while clock() - t0 < ACTIVE_WAIT_S:
        p = unit_props(unit, scope)
        inv, act = p.get("InvocationID", ""), p.get("ActiveState", "")
        if inv and inv != before_inv and act == "active":
            break
        if inv and inv != before_inv and act == "failed":
            break
        sleep(0.5)
    inv, act = p.get("InvocationID", ""), p.get("ActiveState", "")
    if not inv or inv == before_inv:
        return {"ok": False, "state": "no-restart",
                "note": f"{unit}: systemd never started a new run ({act or 'unknown'})"}
    if act != "active":
        return {"ok": False, "state": act or "down",
                "note": f"{unit} {act}/{p.get('SubState', '')} · {p.get('Result', '')} "
                        f"(exit {p.get('ExecMainStatus', '?')})",
                "tail": _last_lines(unit, scope)}
    sleep(SETTLE_S)
    q = unit_props(unit, scope)
    if q.get("InvocationID") != inv or q.get("ActiveState") != "active":
        return {"ok": False, "state": "crashed",
                "note": f"{unit} came up and fell over within {SETTLE_S:.0f}s "
                        f"({q.get('ActiveState', '')} · {q.get('Result', '')}, "
                        f"exit {q.get('ExecMainStatus', '?')})",
                "tail": _last_lines(unit, scope)}
    if port:
        wait = PORT_WAIT_S.get(unit, PORT_WAIT_DEFAULT_S)
        t1 = clock()
        while not _port_answers(unit, port):
            r = unit_props(unit, scope)
            if r.get("InvocationID") != inv or r.get("ActiveState") != "active":
                return {"ok": False, "state": "crashed",
                        "note": f"{unit} fell over before :{port} answered "
                                f"({r.get('Result', '')}, exit {r.get('ExecMainStatus', '?')})",
                        "tail": _last_lines(unit, scope)}
            if clock() - t1 > wait:
                return {"ok": False, "state": "no-port",
                        "note": f"{unit} is running but :{port} did not answer in {wait:.0f}s"}
            sleep(1.0)
        return {"ok": True, "state": "up", "note": f"{unit} restarted · :{port} answering"}
    return {"ok": True, "state": "up", "note": f"{unit} restarted · running"}


def _shared_rows():
    """The install's own rule for rows that are served INSIDE one shared unit (lib/panel_gateways),
    or None: a pack has no such rows and every row is its own unit."""
    try:
        import panel_gateways
    except ImportError:
        return None
    return panel_gateways


def start_restart(unit: str, scope: str, port: int | None) -> dict:
    """Queue the restart and return a job the page polls. A refusal (polkit,
    bad unit) is reported now, with systemctl's own words."""
    rows = _shared_rows()
    plan = rows.restart_plan(unit, port) if rows else None
    if plan and plan.get("unit"):
        # the row IS the shared unit: it takes the ordinary road below, post-check and all
        unit, port = plan["unit"], plan.get("port", port)
    elif plan:
        # the row is one tenant of the shared unit: only it restarts. The call takes seconds, so
        # it runs off the request and the job says how it went.
        job = uuid.uuid4().hex[:12]
        rec = {"state": "running", "job_id": job, "unit": unit}
        with _LOCK:
            _JOBS[job] = rec

        def _tenant() -> None:
            try:
                ok, note = plan["run"]()
                res = {"ok": bool(ok), "note": text_cut.clip(str(note), 200, label="restart-note", hard=False)}
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "note": text_cut.clip(f"{type(e).__name__}: {e}", 200, label="restart-note", hard=False)}
            with _LOCK:
                rec.update(res)
                rec["state"] = "done"
                rec["ts"] = time.strftime("%H:%M:%S")

        threading.Thread(target=_tenant, name=f"cp-restart-{unit}", daemon=True).start()
        return {"ok": True, "job_id": job, "note": plan.get("note") or f"Restarting {unit}…"}
    before = unit_props(unit, scope).get("InvocationID", "")
    if unit == SELF_UNIT:
        # answer first; the restart lands after the reply is on the wire
        threading.Timer(0.8, lambda: _run(_sysctl(scope) + ["restart", unit_file(unit)])).start()
        return {"ok": True, "self": True, "note": "Control Panel restarting — the page reconnects"}
    if not POSTCHECK:
        rc, out, err = _run(_sysctl(scope) + ["restart", unit_file(unit)], timeout=60)
        return {"ok": rc == 0, "note": f"{unit} restarted" if rc == 0 else text_cut.clip(err or out or f"rc {rc}", 200, label="restart-note", hard=False)}
    rc, out, err = _run(_sysctl(scope) + ["restart", "--no-block", unit_file(unit)])
    if rc != 0:
        return {"ok": False, "error": text_cut.clip(err or out or f"systemctl rc {rc}", 200, label="restart-note", hard=False)}
    job = uuid.uuid4().hex[:12]
    rec = {"state": "running", "job_id": job, "unit": unit}
    with _LOCK:
        _JOBS[job] = rec
        for old in list(_JOBS)[:-12]:
            _JOBS.pop(old, None)

    def _go() -> None:
        try:
            res = postcheck(unit, scope, port, before)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "state": "error", "note": text_cut.clip(f"{type(e).__name__}: {e}", 200, label="restart-note", hard=False)}
        with _LOCK:
            rec.update(res)
            rec["state"] = "done"
            rec["ts"] = time.strftime("%H:%M:%S")

    threading.Thread(target=_go, name=f"cp-restart-{unit}", daemon=True).start()
    return {"ok": True, "job_id": job, "note": f"Restarting {unit}…"}


def restart_report(job: str) -> dict:
    with _LOCK:
        rec = dict(_JOBS.get(job or "") or {})
    return rec or {"state": "done", "ok": False, "note": "no such restart (the panel restarted?)"}


# ── journals by scope, plus a unit's own log files ────────────────────────
def _output_files(unit: str, scope: str) -> list[Path]:
    """StandardOutput=append:/path shows in `systemctl show` as the bare word
    `append`, so read the unit text for the path."""
    rc, out, _ = _run(_sysctl(scope) + ["cat", unit_file(unit)])
    files: list[Path] = []
    for ln in out.splitlines():
        ln = ln.strip()
        for key in ("StandardOutput=", "StandardError="):
            if ln.startswith(key):
                val = ln[len(key):]
                for pre in ("append:", "file:", "truncate:"):
                    if val.startswith(pre):
                        p = Path(val[len(pre):])
                        if p not in files:
                            files.append(p)
    return files


_FILES_CACHE: dict[tuple[str, str], tuple[float, list[Path]]] = {}


def output_files(unit: str, scope: str) -> list[Path]:
    key = (unit, scope)
    hit = _FILES_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    files = _output_files(unit, scope)
    _FILES_CACHE[key] = (time.monotonic(), files)
    return files


def _tail(path: Path, n: int, width: int = 8192) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - 64_000)
            fh.seek(max(0, start - 1))
            raw = fh.read()
    except OSError:
        return []
    rows = (raw[1:] if start else raw).decode("utf-8", "replace").splitlines()
    if start and rows and not raw.startswith(b"\n"):
        rows[0] = f"[line start cut, full line: {path}] " + rows[0]
    return [ln if len(ln) <= width else ln[:width] + f" [line cut at {width} of {len(ln)} chars, full line: {path}]"
            for ln in rows if ln.strip()][-n:]


def read_journal(units: list[tuple[str, str]], n: int = 100, files: bool = True,
                 since: float | None = None) -> dict:
    """[(unit, scope)] → one journalctl per scope, merged by time, then each
    unit's output files when it has them (single-unit view only).
    `since` (epoch) reads only lines after that moment — Render's tape starts
    clean at engage (the User 2026-09-18: "clear logs on Render mode")."""
    by_scope: dict[str, list[str]] = {}
    for u, scope in units:
        by_scope.setdefault(scope or "user", []).append(unit_file(u))
    lines: list[str] = []
    errors: list[str] = []
    for scope, svc in by_scope.items():
        cmd = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(n)]
        if since:
            cmd += [f"--since=@{int(since)}"]
        if scope == "user":
            cmd.insert(1, "--user")
        for s in svc:
            cmd += ["-u", s]
        rc, out, err = _run(cmd)
        if rc != 0 and err:
            errors.append(f"{scope}: " + text_cut.clip(err, 120, label="journal-error", hard=False))
        lines += [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("-- No entries")]
    if len(by_scope) > 1:
        lines.sort(key=lambda ln: ln[:25])
    lines = lines[-n:]
    if files and len(units) == 1 and not since:   # a since-read is clean: no old file tails
        u, scope = units[0]
        for p in output_files(u, scope or "user"):
            tail = _tail(p, 40)
            lines.append(f"── {p} ──")
            lines += tail or ["(empty or unreadable)"]
    out = {"ok": not errors or bool(lines), "lines": lines}
    if errors and not lines:
        out["error"] = "; ".join(errors)
    return out


# krew: the house's search_lines() (the panel's Searxng tape: the agents' own web searches, read from the agent
# framework's databases by the reference install's own reader, absent-ok) is not here: that tape is the Crissa
# panel's, and its reader stays with the reference install.
