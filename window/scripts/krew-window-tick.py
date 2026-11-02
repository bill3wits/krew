#!/usr/bin/env python3
"""krew-window-tick — the house's own work starts when the User's off-hours open.

the User 2026-09-17: "We can all key off that to unify times for future user. Open window
+5mins starts M&S ... like that." The window is ONE global — site.json `training_window`,
read only through lib/krew_hours.py — so a new User's answer to Init's Overnight step
moves every job here. Nothing guesses: no window written = idle, and the log says why.

Tick (krew-window-tick.timer, every 5 min). Inside the window, each job in
config/krew-window-schedule.json whose offset has passed is started ONCE for that
window (`systemctl --user start --no-block`), late starts included (the box was off at
+5 → it starts when the ticker next sees the window), unless too little window is left
(`needs_min`) or a calendar timer still owns it (`legacy_timer` enabled → hands off).

A start is intent, not effect (`--no-block` returns 0 for a unit that then fails): every
later tick reads the unit back and records `ok` / `failed: <result>` / `did not run` on the
job, once, in the state file — ~/.hermes/krew-window-tick.json is the status a report reads
(per job: at, status, checked_at; `next_by` = when tonight's un-fired jobs fall due).

CHAINS (the User 2026-09-17: "Using an already anchored completion is perfectly fine and desired
if dependancy"): a job with `"after": ["<job>", …]` is due once every named job has a FINAL
verdict in this window (ok / failed / did not run) — completion, not a clock.

ONE DAY LABEL (the User: "agree with avoiding midnight crossovers"): inside the window the ticker
keeps $KREW_HOME/krew-day.env = `KREW_DAY=<date the window opened>` (removed outside it; KREW_DAY_ENV names
another file). A unit opts in with `EnvironmentFile=-%h/.krew/krew-day.env` (krew's crew install writes the
install's own krew home there), so a chain that runs past
midnight carries one date. Opt-in, not `systemctl set-environment`: a job whose partner does
not carry the label yet (team-eval before team-research) must not get it by accident.

Kill: ~/.hermes/krew-window-tick.off or KREW_WINDOW_TICK=0; one job: "enabled": false.
`--facts` prints the window and what is due, and starts nothing.

krew: the window is `off_hours: "HH:MM-HH:MM"` in $KREW_HOME/krew.yaml (window/lib/krew_hours.py), the schedule
$KREW_HOME/window-schedule.json (config/window-schedule.example.json), the state, day env and log under $KREW_HOME.
Off: $KREW_HOME/krew-window-tick.off or KREW_WINDOW_TICK=0.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import krew_hours  # noqa: E402
import job_status  # noqa: E402

KH = Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser()
SCHEDULE = Path(os.environ.get("KREW_WINDOW_SCHEDULE", str(KH / "window-schedule.json")))
STATE = Path(os.environ.get("KREW_WINDOW_STATE", str(KH / "krew-window-tick.json")))
OFF = KH / "krew-window-tick.off"
DAY_ENV = Path(os.environ.get("KREW_DAY_ENV", str(KH / "krew-day.env")))
LOG = KH / "logs" / "krew-window-tick.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} krew-window-tick: {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_jobs(path: Path = SCHEDULE) -> list[dict]:
    if not path.is_file():
        log(f"no schedule at {path} (copy config/window-schedule.example.json there): no jobs")
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [j for j in (data.get("jobs") or []) if isinstance(j, dict) and j.get("name") and j.get("unit")]


def position(now: dt.datetime, start: int, end: int) -> tuple[int, int]:
    """(minutes since the window opened, minutes left). Caller checked contains()."""
    m = now.hour * 60 + now.minute
    length = (end - start) % 1440 or 1440
    elapsed = (m - start) % 1440
    return elapsed, length - elapsed


def final(rec: dict | None) -> bool:
    """A job's verdict for this window is in (ok / failed / did not run / already running)."""
    if not rec:
        return False
    st = rec.get("status")
    if st is None:
        return bool(rec.get("note"))              # "already running" counted as tonight's run
    return st != "started"


def is_failed(rec: dict | None) -> bool:
    """The verdict is a failure of some kind (exit code, no status line, the job's own failed)."""
    st = (rec or {}).get("status")
    return isinstance(st, str) and st.startswith("failed")


def due_jobs(jobs: list[dict], elapsed: int, left: int, fired: dict,
             status_reader=None, window_key: str | None = None,
             since: dict | None = None) -> tuple[list[dict], list[str]]:
    """(jobs to start now, notes about the ones held). Pure.

    `since` maps a job name to the epoch it fired; the live hold check reads each
    held link's status line from that moment on (a rerun's ok line lands after it)."""
    go, notes = [], []
    by_name = {str(j["name"]): j for j in jobs}
    for j in jobs:
        name = str(j["name"])
        if not j.get("enabled", True) or name in fired:
            continue
        after = [str(x) for x in (j.get("after") or [])]
        if after and not all(final(fired.get(a)) for a in after):
            continue                               # its chain has not completed yet
        if j.get("hold_on_failed"):
            # A FAILED chain link is final (so this row would be due) but is not a success:
            # starting the rest of the chain on top of a failure is the 09-28 team-research
            # night (research wrote no note, team-eval started anyway and skipped). Rows
            # that opt in hold until the failed link's day is rerun (the catch-up timer) or
            # its status line flips to ok/skipped. `after:` evaluation stays in due_jobs —
            # this is the one guard, not a second.
            held_link = None
            for a in after:
                if not is_failed(fired.get(a)):
                    continue
                # the recorded verdict is one shot: a rerun (the 02:25 catch-up) writes a
                # FRESH line that the outcomes loop never re-reads (it stops at "started"),
                # so ask the line itself what the link says NOW — the job's word wins.
                if status_reader and window_key:
                    since_epoch = float((since or {}).get(a)
                                       or (fired.get(a) or {}).get("epoch") or 0)
                    live = str((status_reader(status_key(by_name.get(a)), window_key, since_epoch)
                                or {}).get("status") or "")
                    if live in ("ok", "skipped"):
                        continue                          # the rerun flipped the line — release
                held_link = a
                break
            if held_link:
                notes.append(f"{j.get('name')}: held ({held_link} failed — waits for the rerun)")
                continue
        if j.get("before_close_min") is not None:
            # a closing job (the House Daily) is keyed to the END of the window, so
            # it follows the night's work whatever the window's length is for this User
            if left > int(j["before_close_min"]):
                continue
        elif elapsed < int(j.get("offset_min", 0)):
            continue
        if left < int(j.get("needs_min", 0)):
            notes.append(f"{name}: {left} min left < needs {j.get('needs_min')} — not tonight")
            continue
        go.append(j)
    return go, notes


# Units the schedule marks "scope": "system" (team-research runs as a system service; the
# start is allowed by config/polkit/50-krew-window-system-jobs.rules, the User 2026-09-17
# "please start"). Everything else is the user manager.
SYSTEM_UNITS: set[str] = set()


def _systemctl(unit: str) -> list[str]:
    return ["systemctl", "--no-ask-password"] if unit in SYSTEM_UNITS else ["systemctl", "--user"]


def timer_enabled(unit: str) -> bool:
    p = subprocess.run([*_systemctl(unit), "is-enabled", unit], capture_output=True, text=True, check=False)
    return p.stdout.strip() in ("enabled", "enabled-runtime", "static", "linked")


def unit_active(unit: str) -> bool:
    p = subprocess.run([*_systemctl(unit), "is-active", unit], capture_output=True, text=True, check=False)
    return p.stdout.strip() in ("active", "activating")


def start_unit(unit: str) -> tuple[int, str]:
    p = subprocess.run([*_systemctl(unit), "start", "--no-block", unit], capture_output=True, text=True, check=False)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# systemd's own MESSAGE_IDs (sd-messages.h): the journal keeps what the unit forgets.
_MSG_STARTING = "7d4958e842da4a758f6c1cdc7b36dcc5"
_MSG_FINISHED = "39f53479d3a045ac8e11786248231fbf"     # "Finished X." / "Started X."
_MSG_FAILED = "be02cf6855d2428ba40df7e9d022f03d"       # "Failed to start X."
_MSG_EXITED = "98e322203f7a4ed290d09fe03c09fe15"       # "X: Main process exited, ... status=N"


def _journalctl(unit: str) -> list[str]:
    return ["journalctl"] + ([] if unit in SYSTEM_UNITS else ["--user"]) + ["-u", unit, "-o", "json", "--no-pager"]


def journal_run(unit: str, since: float, lines: str | None = None) -> dict:
    """The unit's last run as the journal saw it, from `since` (epoch) on.

    2026-09-18: `systemctl show` clears ExecMainStartTimestamp the moment a
    Type=oneshot unit goes back to inactive, so a job that FINISHED read as
    `started=0` → "did not run" — 7 of 8 window jobs on the first night, while
    the journal showed every one of them Starting … Finished. The journal's
    systemd lines (MESSAGE_ID, not the wording) are the record that survives.
    Returns {} when the journal has nothing since `since`."""
    if lines is None:
        p = subprocess.run([*_journalctl(unit), "--since", f"@{int(max(since - 60, 0))}"],
                           capture_output=True, text=True, check=False)
        lines = p.stdout if p.returncode == 0 else ""
    out: dict = {}
    for raw in (lines or "").splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        mid = str(d.get("MESSAGE_ID") or "")
        try:
            ts = float(d.get("__REALTIME_TIMESTAMP") or 0) / 1e6
        except (TypeError, ValueError):
            ts = 0.0
        if mid == _MSG_STARTING:
            out = {"started": ts, "active": "activating", "result": "", "status": ""}
        elif mid == _MSG_EXITED and out:
            out["status"] = str(d.get("EXIT_STATUS") or "")
        elif mid == _MSG_FINISHED and out:
            out.update(active="inactive", result="success", status=out.get("status") or "0", ended=ts)
        elif mid == _MSG_FAILED and out:
            out.update(active="failed", result=str(d.get("JOB_RESULT") or "failed"),
                       status=out.get("status") or "?", ended=ts)
    return out


def unit_result(unit: str, fired_at: float = 0.0) -> dict:
    """ActiveState / Result / ExecMainStatus / start time (epoch) of the unit's last run.

    The unit's properties while it is active or failed; the journal once a
    finished oneshot has forgotten its own start (see journal_run)."""
    p = subprocess.run([*_systemctl(unit), "show", unit, "-p", "ActiveState", "-p", "Result",
                        "-p", "ExecMainStatus", "-p", "ExecMainStartTimestamp"],
                       capture_output=True, text=True, check=False)
    d = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
    started = 0.0
    raw = d.get("ExecMainStartTimestamp", "").strip()
    if raw and raw != "n/a":
        q = subprocess.run(["date", "-d", raw, "+%s"], capture_output=True, text=True, check=False)
        started = float(q.stdout.strip() or 0) if q.returncode == 0 else 0.0
    res = {"active": d.get("ActiveState", ""), "result": d.get("Result", ""),
           "status": d.get("ExecMainStatus", ""), "started": started}
    if res["active"] not in ("active", "activating", "deactivating") and started < fired_at - 120 and fired_at:
        j = journal_run(unit, fired_at)
        if j:
            res.update(j)
    return res


def verdict(res: dict, fired_at: float) -> str | None:
    """None while the run is still going; else what actually happened. Pure."""
    if res.get("active") in ("active", "activating", "deactivating"):
        return None
    if float(res.get("started") or 0) < fired_at - 120:
        return "did not run (no start recorded after the ticker asked)"
    if res.get("result") == "success" and str(res.get("status") or "0") == "0":
        return "ok"
    return f"failed: result={res.get('result') or '?'} exit={res.get('status') or '?'}"


# --- the job's own word (self-loop, the User 09-28) --------------------------------------
# A row with `"status_line": "<key>"` (or true = its name) promises that its shell writes
# ~/.hermes/job-status/<key>-<window day>.json (lib/job_status.py). systemd's success is then
# only half the verdict: the line says ok / failed / skipped, and NO line after an exit 0 is
# `failed` (09-27: world and logs exited 0 having done nothing). Read in the SAME tick as the
# verdict and before due_jobs, so a dependent never starts on a silent green.
# Kill: KREW_JOB_STATUS_LINE=0 (systemd's word alone, as before).

def status_line_on() -> bool:
    return os.environ.get("KREW_JOB_STATUS_LINE", "1").strip().lower() not in ("0", "false", "no", "off")


def status_key(job: dict | None) -> str | None:
    v = (job or {}).get("status_line")
    if v is True:
        return str(job["name"])
    return str(v) if v else None


def status_verdict(v: str, key: str | None, line: dict | None) -> str:
    """Fold the job's status line into systemd's verdict. Pure.

    A unit that failed stays failed (its exit code is already the stronger word)."""
    if not key or v != "ok":
        return v
    if line is None:
        return f"failed: exited 0 but wrote no status line ({key}) - did not say it did its work"
    st = str(line.get("status") or "")
    why = str(line.get("reason") or "").strip()
    if st == "ok":
        return "ok"
    if st == "skipped":
        return f"skipped: {why or 'no reason given'}"
    return f"failed: {why or 'its status line says failed'}"


def write_day_label(label: str | None, path: Path = DAY_ENV) -> None:
    """KREW_DAY for units that opt in; gone outside the window (no stale label)."""
    try:
        if not label:
            path.unlink(missing_ok=True)
            return
        body = f"KREW_DAY={label}\n"
        if path.is_file() and path.read_text() == body:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(body)
        tmp.replace(path)
    except OSError:
        pass


def read_state(path: Path = STATE) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def tick(now: dt.datetime | None = None, *, facts_only: bool = False, site: dict | None = None,
         jobs: list[dict] | None = None, state_path: Path = STATE,
         starter=start_unit, legacy=timer_enabled, active=unit_active, result=unit_result,
         day_env: Path | None = DAY_ENV, status_reader=None) -> dict:
    now = now or dt.datetime.now()
    oh = krew_hours.off_hours(site)
    if oh["source"] != "init":
        if not facts_only and day_env:
            write_day_label(None, day_env)
        return {"action": "idle", "why": oh.get("why") or "off-hours unset"}
    window = oh["window"]
    if not krew_hours.contains(now, window):
        if not facts_only and day_env:
            write_day_label(None, day_env)
        return {"action": "idle", "why": f"outside the off-hours ({window})", "window": window}
    key = krew_hours.started_on(now, window)
    if not facts_only and day_env:
        write_day_label(key, day_env)
    state = read_state(state_path)
    if state.get("window_key") != key:
        state = {"window_key": key, "window": window, "fired": {}}
    elapsed, left = position(now, oh["start"], oh["end"])
    jobs = load_jobs() if jobs is None else jobs
    by_name = {str(j["name"]): j for j in jobs}
    if status_reader is None:
        status_reader = lambda k, day, since: job_status.read(k, day, since)  # noqa: E731
    SYSTEM_UNITS.clear()
    for j in jobs:
        if j.get("scope") == "system":
            SYSTEM_UNITS.add(str(j["unit"]))
            if j.get("legacy_timer"):
                SYSTEM_UNITS.add(str(j["legacy_timer"]))
    for rec in state["fired"].values():            # a verdict read after the schedule changed
        if rec.get("scope") == "system" and rec.get("unit"):
            SYSTEM_UNITS.add(str(rec["unit"]))
    outcomes = []
    for name, rec in state["fired"].items():          # effect, not intent
        if rec.get("status") not in (None, "started") or not rec.get("unit"):
            continue
        fired_at = float(rec.get("epoch") or 0)
        try:
            res = result(rec["unit"], fired_at)
        except TypeError:                      # an injected one-argument reader
            res = result(rec["unit"])
        v = verdict(res, fired_at)
        skey = rec.get("status_line") or status_key(by_name.get(name))
        if v is not None and skey and status_line_on():
            v = status_verdict(v, skey, status_reader(skey, key, fired_at))
        if v is not None and not facts_only:
            rec["status"], rec["checked_at"] = v, now.isoformat(timespec="seconds")
            outcomes.append(f"{name}: {v}")
    go, notes = due_jobs(jobs, elapsed, left, state["fired"],                         # after every verdict above
                         status_reader if (status_line_on() and status_reader) else None, key,
                         since={n: float((r.get("epoch") or 0)) for n, r in state["fired"].items()})
    started = []
    for j in go:
        name, unit = str(j["name"]), str(j["unit"])
        lt = j.get("legacy_timer")
        if lt and legacy(str(lt)):
            notes.append(f"{name}: {lt} is still enabled — it owns the job, hands off")
            continue
        if facts_only:
            started.append(f"{name} (would start {unit})")
            continue
        if active(unit):
            state["fired"][name] = {"at": now.isoformat(timespec="seconds"), "note": "already running"}
            notes.append(f"{name}: {unit} already running — counted as tonight's run")
            continue
        rc, out = starter(unit)
        if rc == 0:
            state["fired"][name] = {"at": now.isoformat(timespec="seconds"), "epoch": now.timestamp(),
                                    "unit": unit, "elapsed_min": elapsed, "status": "started",
                                    **({"status_line": status_key(j)} if status_key(j) else {}),
                                    **({"scope": "system"} if j.get("scope") == "system" else {})}
            started.append(name)
        else:
            notes.append(f"{name}: start failed ({out.strip()}) — retried next tick")
    changed = notes != state.get("notes")      # a held job is logged once, not every 5 min
    state["notes"] = notes
    opened = now - dt.timedelta(minutes=elapsed)
    length = elapsed + left
    state["next_by"] = {
        str(j["name"]): ("after " + ", ".join(str(x) for x in j["after"]) if j.get("after") else
                         (opened + dt.timedelta(minutes=(length - int(j["before_close_min"]))
                                                if j.get("before_close_min") is not None
                                                else int(j.get("offset_min", 0)))).isoformat(timespec="minutes"))
        for j in jobs if j.get("enabled", True) and str(j["name"]) not in state["fired"]}
    if not facts_only:
        write_state(state, state_path)
    return {"action": "started" if started else "idle", "window": window, "window_key": key,
            "notes_changed": changed, "outcomes": outcomes,
            "elapsed_min": elapsed, "left_min": left, "started": started, "notes": notes,
            "fired": sorted(state["fired"])}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    unknown = [a for a in argv if a not in ("--facts",)]
    if unknown:
        print(__doc__)
        print("usage: krew-window-tick.py [--facts]")
        return 0 if set(unknown) <= {"-h", "--help"} else 2
    facts = "--facts" in argv
    if not facts and (OFF.is_file() or os.environ.get("KREW_WINDOW_TICK", "1").strip().lower() in ("0", "false", "no", "off")):
        log("off switch set; nothing done")
        return 0
    r = tick(facts_only=facts)
    if facts:
        print(json.dumps(r, indent=2))
        return 0
    for o in r.get("outcomes") or []:
        log(("" if o.endswith(": ok") or ": skipped" in o else "FAILED — ") + o)
    if r.get("started") or (r.get("notes") and r.get("notes_changed")):
        log(f"{r.get('window')} +{r.get('elapsed_min')}m: started {r.get('started') or 'nothing'}"
            + ("; " + "; ".join(r["notes"]) if r.get("notes") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
