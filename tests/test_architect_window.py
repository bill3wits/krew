"""The architect notes ride the User's off-hours as a SIDE BRANCH of the window chain.

the User 2026-09-17: logs / world / stack ran at 06:10-10:45, inside his day. The ticker's `after`
needs a FINAL verdict, and a row still owned by its calendar timer never gets one — so nothing
may be chained behind an architect row, or one un-disabled timer stalls the improve chain.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHED = json.loads((ROOT / "config" / "window-schedule.example.json").read_text())
JOBS = {j["name"]: j for j in SCHED["jobs"]}
ARCH = [n for n in JOBS if n.startswith("architect-")]


def test_rows_are_there_and_collect_waits_for_the_probes():
    assert {"architect-logs", "architect-world", "architect-stack", "architect-collect"} <= set(ARCH)
    assert JOBS["architect-collect"]["enabled"] is False
    for n in ("architect-logs", "architect-world", "architect-stack"):
        j = JOBS[n]
        # krew: user units (the reference ran them as system units); a legacy_timer only where krew ships the timer
        assert j["enabled"] is True and j.get("scope", "user") == "user"
        t = j.get("legacy_timer")
        assert not t or (ROOT / "crew" / "units" / t).is_file()


def test_nothing_is_chained_behind_an_architect_row_except_the_branch_itself():
    for name, j in JOBS.items():
        # a row still owned by its calendar timer never gets a verdict, so nothing may wait on
        # it; the self-loop's evening collect (09-28) is ticker-owned (no legacy_timer) and
        # always gets one, so research and logs may follow it
        behind = [a for a in (j.get("after") or []) if a.startswith("architect-")
                  and JOBS.get(a, {}).get("legacy_timer")]
        assert not behind or name.startswith("architect-"), f"{name} would stall behind {behind}"
    assert "legacy_timer" not in JOBS["architect-collect-evening"]
    assert JOBS["architect-logs"]["after"] == ["team-eval", "architect-collect-evening"]  # steward is on ops under Turbo
    assert JOBS["architect-world"]["after"] == ["architect-logs"]
    assert JOBS["architect-stack"]["after"] == ["architect-world"]
    assert "watch" in SCHED and JOBS.get("krew-daily")                   # the House Daily's registry survives


def test_no_window_job_needs_a_system_unit():
    # krew: the reference started its system-scope jobs through a polkit rule (start only); krew ships every window
    # job as a user unit, so there is no rule to keep in step with the schedule
    assert not [j["name"] for j in SCHED["jobs"] if j.get("scope") == "system"]


def test_units_carry_the_day_label_and_window_runs_wait_for_the_lock():
    for job in ("logs", "world", "stack", "collect"):
        unit = (ROOT / "crew" / "units" / f"krew-architect-{job}.service").read_text()
        assert "EnvironmentFile=-%h/.krew/krew-day.env" in unit          # the file the window ticker writes
    run = "\n".join(l for l in (ROOT / "crew" / "scripts" / "steward-architect-run.sh").read_text().splitlines()
                    if not l.lstrip().startswith("#"))
    assert run.index("WINDOW_RUN=${KREW_DAY:+1}") < run.index("TODAY=${KREW_DAY:-")   # read BEFORE the default
    assert '[ "$JOB" = research ] || [ -n "${WINDOW_RUN:-}" ]' in run


def test_the_day_label_is_one_file_the_ticker_writes_and_every_unit_reads(tmp_path):
    """krew-window-tick writes $KREW_HOME/krew-day.env; every crew unit that carries the label reads that file (the
    crew install maps %h/.krew to the install's krew home), and krew's own units read {krew_home}/krew-day.env."""
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location("kwt_day", ROOT / "window" / "scripts" / "krew-window-tick.py")
    m = importlib.util.module_from_spec(spec)
    old = {k: os.environ.get(k) for k in ("KREW_HOME", "KREW_DAY_ENV")}
    os.environ["KREW_HOME"] = str(tmp_path / "kh")
    os.environ.pop("KREW_DAY_ENV", None)
    try:
        spec.loader.exec_module(m)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    assert m.DAY_ENV == tmp_path / "kh" / "krew-day.env"
    readers = [u for u in (ROOT / "crew" / "units").glob("*.service") if "krew-day.env" in u.read_text()]
    assert len(readers) >= 10
    for u in readers:
        assert "EnvironmentFile=-%h/.krew/krew-day.env" in u.read_text() and ".hermes/krew-day.env" not in u.read_text(), u.name
    assert "EnvironmentFile=-{krew_home}/krew-day.env" in (ROOT / "units" / "krew-ms-nightly.service").read_text()
    spec = importlib.util.spec_from_file_location("kci_day", ROOT / "crew" / "scripts" / "krew-crew-install.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    from pathlib import Path as _P
    text = ci.unit_text(ROOT / "crew" / "units" / "krew-daily.service", _P.home() / "elsewhere-krew")
    assert "EnvironmentFile=-%h/elsewhere-krew/krew-day.env" in text
