"""krew-window-tick starts the crew's own jobs off ONE global: krew.yaml off_hours (the house: site.json training_window).

the User 2026-09-17: "We can all key off that to unify times for future user. Open window
+5mins starts M&S ... like that."
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("krew_window_tick", ROOT / "window" / "scripts" / "krew-window-tick.py")
wt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wt)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _tick_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the module's import-time paths stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(wt, tmp_path, monkeypatch)

SITE = {"off_hours": "20:00-02:00"}
JOBS = [{"name": "day-close", "unit": "dc.service", "offset_min": 2, "needs_min": 5},
        {"name": "ms", "unit": "ms.service", "offset_min": 5, "needs_min": 30, "legacy_timer": "ms.timer"}]


def _tick(tmp_path, when, *, site=SITE, legacy=lambda u: False, active=lambda u: False, jobs=JOBS, facts=False):
    calls = []
    r = wt.tick(dt.datetime.fromisoformat(when), site=site, jobs=jobs, state_path=tmp_path / "s.json",
                day_env=tmp_path / "krew-day.env",
                starter=lambda u: (calls.append(u) or (0, "")), legacy=legacy, active=active, facts_only=facts)
    return r, calls


def test_no_window_written_is_idle_and_says_why(tmp_path):
    r, calls = _tick(tmp_path, "2026-09-17T21:00", site={})
    assert r["action"] == "idle" and "off_hours" in r["why"] and not calls
    r, calls = _tick(tmp_path, "2026-09-17T21:00", site={"off_hours": "evening"})
    assert r["action"] == "idle" and not calls


def test_outside_the_window_nothing_starts(tmp_path):
    r, calls = _tick(tmp_path, "2026-09-17T19:59")
    assert r["action"] == "idle" and "outside" in r["why"] and not calls


def test_offsets_fire_once_per_window(tmp_path):
    assert _tick(tmp_path, "2026-09-17T20:00")[1] == []
    assert _tick(tmp_path, "2026-09-17T20:02")[1] == ["dc.service"]
    assert _tick(tmp_path, "2026-09-17T20:05")[1] == ["ms.service"]
    assert _tick(tmp_path, "2026-09-17T20:10")[1] == []            # once
    assert _tick(tmp_path, "2026-09-18T01:30")[1] == []            # same window, past midnight
    assert _tick(tmp_path, "2026-09-18T20:05")[1] == ["dc.service", "ms.service"]   # next window


def test_a_new_users_window_moves_every_job(tmp_path):
    early = {"off_hours": "18:00-02:00"}
    assert _tick(tmp_path, "2026-09-17T18:05", site=early)[1] == ["dc.service", "ms.service"]


def test_late_start_inside_the_window_but_not_with_too_little_left(tmp_path):
    r, calls = _tick(tmp_path, "2026-09-17T23:10")               # box was off at +5
    assert calls == ["dc.service", "ms.service"]
    (tmp_path / "b").mkdir()
    r, calls = _tick(tmp_path / "b", "2026-09-18T01:40")           # 20 min left: M&S needs 30
    assert calls == ["dc.service"] and any("not tonight" in n for n in r["notes"])


def test_a_calendar_timer_that_still_owns_the_job_is_left_alone(tmp_path):
    r, calls = _tick(tmp_path, "2026-09-17T20:06", legacy=lambda u: u == "ms.timer")
    assert calls == ["dc.service"] and any("hands off" in n for n in r["notes"])
    assert "ms" not in r["fired"]                                    # taken up the night the timer goes


def test_running_unit_counts_and_facts_start_nothing(tmp_path):
    r, calls = _tick(tmp_path, "2026-09-17T20:06", active=lambda u: u == "ms.service")
    assert calls == ["dc.service"] and "ms" in r["fired"]
    (tmp_path / "f").mkdir()
    r, calls = _tick(tmp_path / "f", "2026-09-17T20:06", facts=True)
    assert not calls and not (tmp_path / "f" / "s.json").exists() and len(r["started"]) == 2


def test_failed_start_is_retried_and_held_note_logs_once(tmp_path):
    r = wt.tick(dt.datetime.fromisoformat("2026-09-17T20:03"), site=SITE, day_env=None, jobs=JOBS[:1],
                state_path=tmp_path / "s.json", starter=lambda u: (1, "boom"), legacy=lambda u: False, active=lambda u: False)
    assert r["fired"] == [] and r["notes_changed"]
    r = wt.tick(dt.datetime.fromisoformat("2026-09-17T20:08"), site=SITE, day_env=None, jobs=JOBS[:1],
                state_path=tmp_path / "s.json", starter=lambda u: (1, "boom"), legacy=lambda u: False, active=lambda u: False)
    assert not r["notes_changed"]


def test_a_closing_job_keys_off_the_end_of_the_window(tmp_path):
    """House Daily is '10 min before close', not +N after open — a 4 h window
    and an 8 h window both fire it at the same remaining time."""
    jobs = JOBS + [{"name": "daily", "unit": "daily.service", "before_close_min": 10}]
    assert _tick(tmp_path, "2026-09-17T20:10", jobs=jobs)[1] == ["dc.service", "ms.service"]
    assert _tick(tmp_path, "2026-09-18T01:49", jobs=jobs)[1] == []
    assert _tick(tmp_path, "2026-09-18T01:50", jobs=jobs)[1] == ["daily.service"]


def test_shipped_schedule_is_the_law_human_gave():
    # krew: the shipped schedule is config/window-schedule.example.json; its `jobs` are the house's chain on krew's unit names, and the house's
    # own schedule is kept verbatim under `_reference_install` (ref- prefix), which this pins the house's law on.
    data = json.loads((ROOT / "config" / "window-schedule.example.json").read_text())
    mine = {j["name"]: j for j in data["jobs"]}
    assert mine["ms-nightly"]["offset_min"] == 5 and mine["ms-nightly"]["unit"] == "krew-ms-nightly.service"
    assert mine["ms-nightly"]["legacy_timer"] == "krew-ms-nightly.timer"
    jobs = {j["name"]: j for j in data["_reference_install"]["jobs"]}
    assert jobs["ms-nightly"]["offset_min"] == 5 and jobs["ms-nightly"]["unit"] == "nightly-tests.service"
    assert jobs["ms-nightly"]["legacy_timer"] == "nightly-tests.timer"
    assert jobs["kanban-day-close"]["offset_min"] < jobs["ms-nightly"]["offset_min"]
    assert jobs["ref-daily"]["before_close_min"] == 10 and jobs["ref-daily"]["unit"] == "ref-daily.service"
    code = "\n".join(l for l in (ROOT / "window" / "scripts" / "krew-window-tick.py").read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    assert "krew_hours.off_hours" in code and "20:00" not in code.split('"""', 2)[2]   # no second default


# --- a start is intent; the unit's result is the effect ---------------------------

def test_verdict_reads_the_effect():
    at = 1_000_000.0
    assert wt.verdict({"active": "activating", "started": at}, at) is None                    # still running
    assert wt.verdict({"active": "inactive", "result": "success", "status": "0", "started": at + 2}, at) == "ok"
    assert wt.verdict({"active": "failed", "result": "exit-code", "status": "1", "started": at + 2}, at).startswith("failed")
    assert wt.verdict({"active": "inactive", "result": "success", "status": "0", "started": at - 86400}, at).startswith("did not run")


def test_a_failed_job_is_recorded_once_and_next_by_is_published(tmp_path):
    sp = tmp_path / "s.json"
    kw = dict(site=SITE, jobs=JOBS, state_path=sp, day_env=None, starter=lambda u: (0, ""), legacy=lambda u: False, active=lambda u: False)
    t0 = dt.datetime.fromisoformat("2026-09-17T20:02")
    wt.tick(t0, result=lambda u: {}, **kw)
    st = json.loads(sp.read_text())
    assert st["fired"]["day-close"]["status"] == "started" and st["next_by"] == {"ms": "2026-09-17T20:05"}
    bad = lambda u: {"active": "failed", "result": "exit-code", "status": "1", "started": t0.timestamp() + 1}
    r = wt.tick(dt.datetime.fromisoformat("2026-09-17T20:03"), result=bad, **kw)
    assert r["outcomes"] == ["day-close: failed: result=exit-code exit=1"]
    r = wt.tick(dt.datetime.fromisoformat("2026-09-17T20:04"), result=bad, **kw)
    assert r["outcomes"] == []                                                                  # once
    assert json.loads(sp.read_text())["fired"]["day-close"]["status"].startswith("failed")



CHAIN = [{"name": "ms", "unit": "ms.service", "offset_min": 5, "needs_min": 30},
         {"name": "drift", "unit": "drift.service", "after": ["ms"], "needs_min": 5},
         {"name": "freelance", "unit": "fl.service", "after": ["drift"], "needs_min": 60}]


def test_after_follows_completion_not_a_clock(tmp_path):
    state = tmp_path / "s.json"
    runs = {}

    def result(unit):
        return runs.get(unit, {"active": "active", "result": "", "status": "", "started": 0})

    def t(when):
        calls = []
        wt.tick(dt.datetime.fromisoformat(when), site=SITE, jobs=CHAIN, state_path=state, day_env=None,
                starter=lambda u: (calls.append(u) or (0, "")), legacy=lambda u: False,
                active=lambda u: False, result=result)
        return calls

    assert t("2026-09-17T20:05") == ["ms.service"]
    assert t("2026-09-17T20:10") == []                       # ms still running
    runs["ms.service"] = {"active": "inactive", "result": "exit-code", "status": "1",
                          "started": dt.datetime.fromisoformat("2026-09-17T20:05").timestamp()}
    # the tick that records ms's verdict starts its dependent (a failed dependency is final too)
    assert t("2026-09-17T20:15") == ["drift.service"]
    assert t("2026-09-17T20:20") == []                       # drift still running
    runs["drift.service"] = {"active": "inactive", "result": "success", "status": "0",
                             "started": dt.datetime.fromisoformat("2026-09-17T20:15").timestamp()}
    assert t("2026-09-17T20:25") == ["fl.service"]
    assert json.loads(state.read_text())["fired"]["ms"]["status"].startswith("failed")


# --- self-loop 09-28 (t_969679a2): a FAILED link is final but must not start the
# chain on top of the failure. 09-28 20:35: research failed (no note) and team-eval
# started anyway, then skipped. hold_on_failed rows wait for the rerun instead. -------

HOLD = [{"name": "research", "unit": "research.service", "offset_min": 0, "needs_min": 5, "status_line": "research"},
        {"name": "eval", "unit": "eval.service", "after": ["research"], "needs_min": 5,
         "hold_on_failed": True, "status_line": "eval"},
        {"name": "file", "unit": "file.service", "after": ["eval"], "needs_min": 5,
         "hold_on_failed": True},
        {"name": "outcomes", "unit": "outcomes.service", "after": ["file"], "needs_min": 5},
        {"name": "logs", "unit": "logs.service", "after": ["eval"], "needs_min": 5}]


def test_failed_chain_link_is_final_but_holds_the_opted_in_rows():
    fired = {"research": {"status": "failed: no note written: team-research-2026-09-28.md",
                          "unit": "research.service"}}
    go, notes = wt.due_jobs(HOLD, 35, 325, fired)
    assert go == []
    assert any("eval" in n and "held" in n for n in notes), "team-eval holds on the failed research"
    # the hold is logged once, not every tick (the notes_changed discipline)
    assert wt.due_jobs(HOLD, 40, 320, fired)[1] == notes
    # a SKIPPED link is final-and-continues (test_self_loop_step1: a skip is final) — no hold
    assert [g["name"] for g in wt.due_jobs(HOLD, 40, 320,
                                           {"research": {"status": "skipped: occupancy=beast",
                                                         "unit": "research.service"}})[0]] == ["eval"]
    # ok link: the chain runs
    assert [g["name"] for g in wt.due_jobs(HOLD, 40, 320, {"research": {"status": "ok",
                                                                        "unit": "research.service"}})[0]] == ["eval"]


def test_the_hold_waits_the_whole_window_until_the_rerun_flips_the_line(tmp_path):
    """The failed link stays failed all window: the opted-in rows never start (logged once),
    the second-order hold reaches logs (after: eval), and the first flip to ok releases the chain."""
    state = tmp_path / "s.json"
    lines = {"research": {"status": "failed", "reason": "no note written: team-research-2026-09-28.md"}}
    kw = dict(site=SITE, jobs=HOLD, state_path=state, day_env=None, starter=lambda u: (0, ""),
              legacy=lambda u: False, active=lambda u: False,
              status_reader=lambda k, day, since: lines.get(k))
    started: dict[str, float] = {}

    def result(unit, *args):
        # a finished unit forgets its start; the journal keeps it (the unit's own word:
        # systemd says success, the status line says failed)
        return {"active": "inactive", "result": "success", "status": "0",
                "started": started.get(unit, 0.0)}

    kw["result"] = result
    t0 = dt.datetime(2026, 9, 28, 20, 30)
    r = wt.tick(t0, **kw)
    assert r["started"] == ["research"]
    started["research.service"] = t0.timestamp() + 1
    # the 20:35 tick records research's OWN word (the unit succeeded; the status line says failed)
    r = wt.tick(t0 + dt.timedelta(minutes=5), **kw)
    assert json.loads(state.read_text())["fired"]["research"]["status"].startswith("failed")
    held = [n for n in r["notes"] if "held" in n]
    assert held, "the first held note lands on the verdict tick"
    for i, (minute, expected) in enumerate([(35, []), (40, []), (50, [])]):
        r = wt.tick(t0 + dt.timedelta(minutes=minute), **kw)
        assert r["started"] == expected and not any(n == "file" or n == "logs" or n == "outcomes" for n in r["started"])
        # the held note stays logged (same text), not re-emitted every 5 min
        assert [n for n in r["notes"] if "held" in n] == held and not r["notes_changed"]
    lines["research"] = {"status": "ok", "reason": "rerun (02:25 catch-up) wrote the note"}
    r = wt.tick(t0 + dt.timedelta(minutes=55), **kw)
    assert r["started"] == ["eval"], "the flip to ok releases the chain"
    r = wt.tick(t0 + dt.timedelta(minutes=60), **kw)
    assert r["started"] == ["file", "logs"]
    # a skip is final too (covered by the pure-due_jobs test): the chain would continue


def test_outcomes_and_the_unflagged_rows_run_while_the_chain_is_held():
    """improvement-outcomes (no flag) and the side branch keep their 09-28 behavior:
    only the opted-in rows wait. If file never starts (the chain held all night),
    outcomes would hang — so the release path is what matters, and it is the flip."""
    fired = {"research": {"status": "failed: no note written", "unit": "research.service"}}
    go, _ = wt.due_jobs(HOLD, 45, 315, fired)
    assert go == []                                    # everything that waits does
    # but with the flag off (as improvement-outcomes ships), a final link releases the next row
    # the MOMENT the dep reaches final: file held while eval runs, outcomes starts when file
    # lands its own verdict (09-28 20:45: file skipped -> outcomes ran at 20:50).
    plain = [{"name": "file", "unit": "file.service", "after": ["eval"], "needs_min": 5},
             {"name": "outcomes", "unit": "outcomes.service", "after": ["file"], "needs_min": 5}]
    go, _ = wt.due_jobs(plain, 45, 315, {"eval": {"status": "started", "unit": "eval.service"},
                                         "file": {"status": "started", "unit": "file.service"}})
    assert go == []
    go, _ = wt.due_jobs(plain, 50, 310, {"eval": {"status": "skipped: no note to score"},
                                         "file": {"status": "skipped: no quorum", "unit": "file.service"}})
    assert [g["name"] for g in go] == ["outcomes"], "no flag: a final (even skipped) link releases"



def test_a_chained_job_is_not_started_with_too_little_window(tmp_path):
    fired = {"ms": {"status": "ok"}, "drift": {"status": "ok"}}
    go, notes = wt.due_jobs(CHAIN, 330, 40, fired)
    assert go == [] and any("freelance" in n and "not tonight" in n for n in notes)


def test_the_day_label_is_the_window_open_date_and_gone_outside(tmp_path):
    env = tmp_path / "krew-day.env"
    wt.tick(dt.datetime.fromisoformat("2026-09-18T01:30"), site=SITE, jobs=[], state_path=tmp_path / "s.json",
            day_env=env, starter=lambda u: (0, ""), legacy=lambda u: False, active=lambda u: False)
    assert env.read_text() == "KREW_DAY=2026-09-17\n", "past midnight the chain keeps the window's date"
    wt.tick(dt.datetime.fromisoformat("2026-09-18T10:00"), site=SITE, jobs=[], state_path=tmp_path / "s.json",
            day_env=env, starter=lambda u: (0, ""), legacy=lambda u: False, active=lambda u: False)
    assert not env.exists()


def test_the_live_schedule_chains_and_labels():
    # krew: the reference install's schedule (its `house` prefix written `ref`). The house's polkit rule, unit
    # files and drop-ins it also read are the reference box's, not shipped: those lines are cut.
    data = json.loads((ROOT / "config" / "window-schedule.example.json").read_text())["_reference_install"]
    jobs = {j["name"]: j for j in data["jobs"]}
    assert jobs["pack-drift-check"]["after"] == ["ms-nightly"]
    assert jobs["pose-freelance"]["after"] == ["pack-drift-check"]
    assert jobs["dream-night"]["before_close_min"] > jobs["morning-brief"]["before_close_min"] > jobs["ref-daily"]["before_close_min"]
    assert jobs["team-research"]["scope"] == "system" and jobs["team-research"]["after"] == ["pack-drift-check", "architect-collect-evening", "outcomes-lessons"]
    # self-loop 09-28: ONE evening collect ahead of research and logs. It must carry NO
    # legacy_timer (its 06:50 timer stays enabled, and an enabled legacy timer makes the
    # ticker hand the row off, so the evening collect would never start).
    ce = jobs["architect-collect-evening"]
    assert ce["unit"] == "ref-architect-collect.service" and ce["scope"] == "system"
    assert "legacy_timer" not in ce and ce["enabled"] is True and ce["after"] == ["pack-drift-check"]
    assert jobs["architect-logs"]["after"] == ["team-eval", "architect-collect-evening"]
    assert {n: jobs[n].get("status_line") for n in ("architect-collect-evening", "team-research", "team-eval",
                                                     "architect-logs", "architect-world", "architect-stack")} == {
        "architect-collect-evening": "collect", "team-research": "research", "team-eval": "team-eval",
        "architect-logs": "logs", "architect-world": "world", "architect-stack": "stack"}
    assert jobs["team-eval"]["enabled"] is True and jobs["team-eval"]["after"] == ["team-research"]
    assert "KREW_DAY" in (ROOT / "crew" / "scripts" / "steward-architect-run.sh").read_text()
    watched = {w.get("routine") for w in data["watch"]}
    enabled = {n for n, j in jobs.items() if j.get("enabled", True)}
    assert not watched & enabled, "a window job reports through the ticker, not the watch list"


def test_a_system_job_goes_to_the_system_manager(tmp_path, monkeypatch):
    calls = []

    class P:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr(wt.subprocess, "run", lambda argv, **kw: calls.append(argv) or P())
    jobs = [{"name": "research", "unit": "krew-team-research.service", "scope": "system", "offset_min": 0},
            {"name": "eval", "unit": "krew-team-eval.service", "offset_min": 0}]
    wt.tick(dt.datetime.fromisoformat("2026-09-17T20:30"), site=SITE, jobs=jobs, state_path=tmp_path / "s.json",
            day_env=None, legacy=lambda u: False, active=lambda u: False)
    starts = [c for c in calls if "start" in c]
    assert ["systemctl", "--no-ask-password", "start", "--no-block", "krew-team-research.service"] in starts
    assert ["systemctl", "--user", "start", "--no-block", "krew-team-eval.service"] in starts
    assert json.loads((tmp_path / "s.json").read_text())["fired"]["research"]["scope"] == "system"


# --- 2026-09-18: a finished oneshot forgets its own start; the journal does not ----

def _journal_lines(unit: str, t0: int, *, end: str = "finished", exit_status: str = "") -> str:
    ids = {"starting": "7d4958e842da4a758f6c1cdc7b36dcc5", "finished": "39f53479d3a045ac8e11786248231fbf",
           "failed": "be02cf6855d2428ba40df7e9d022f03d", "exited": "98e322203f7a4ed290d09fe03c09fe15"}
    rows = [{"MESSAGE_ID": ids["starting"], "__REALTIME_TIMESTAMP": str(t0 * 1_000_000), "MESSAGE": f"Starting {unit}..."}]
    if exit_status:
        rows.append({"MESSAGE_ID": ids["exited"], "__REALTIME_TIMESTAMP": str((t0 + 30) * 1_000_000),
                     "EXIT_STATUS": exit_status, "MESSAGE": f"{unit}: Main process exited"})
    rows.append({"MESSAGE_ID": ids[end], "__REALTIME_TIMESTAMP": str((t0 + 31) * 1_000_000), "JOB_RESULT": "failed" if end == "failed" else "done",
                 "MESSAGE": f"Finished {unit}." if end == "finished" else f"Failed to start {unit}."})
    return "".join(json.dumps(r) + "\n" for r in rows)


def test_a_finished_oneshot_is_read_from_the_journal(monkeypatch):
    """7 of 8 window jobs on 2026-09-17 read "did not run" — they had run and finished;
    `systemctl show` had cleared ExecMainStartTimestamp the moment they went inactive."""
    fired = 1_789_689_900.0                     # 20:05
    calls = []

    class P:
        def __init__(self, out):
            self.returncode, self.stdout, self.stderr = 0, out, ""

    def run(argv, **kw):
        calls.append(argv)
        if argv[0] == "systemctl":            # what a finished oneshot answers: nothing
            return P("ActiveState=inactive\nResult=success\nExecMainStatus=0\nExecMainStartTimestamp=\n")
        if argv[0] == "journalctl":
            assert "--user" in argv and f"@{int(fired) - 60}" in argv[-1]
            return P(_journal_lines("dc.service", int(fired)))
        raise AssertionError(argv)
    monkeypatch.setattr(wt.subprocess, "run", run)
    res = wt.unit_result("dc.service", fired)
    assert res["started"] == fired and res["result"] == "success" and res["status"] == "0"
    assert wt.verdict(res, fired) == "ok"


def test_the_journal_tells_a_failed_run_and_nothing_from_a_run_that_never_started():
    t0 = 1_789_689_900
    j = wt.journal_run("ms.service", t0, lines=_journal_lines("ms.service", t0, end="failed", exit_status="1"))
    assert j["result"] == "failed" and j["status"] == "1" and j["started"] == t0
    assert wt.verdict({"active": "inactive", **j}, t0).startswith("failed")
    assert wt.journal_run("x.service", t0, lines="") == {}
    assert wt.verdict({"active": "inactive", "result": "success", "status": "0", "started": 0}, t0).startswith("did not run")
    # a system-scope unit asks the system journal
    wt.SYSTEM_UNITS.add("sys.service")
    try:
        assert "--user" not in wt._journalctl("sys.service") and "--user" in wt._journalctl("dc.service")
    finally:
        wt.SYSTEM_UNITS.discard("sys.service")
