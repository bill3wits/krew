"""House Daily: the page's own checks, not the routines' word (2026-09-17)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours
import krew_daily as hd  # noqa: E402

NOW = dt.datetime(2026, 9, 18, 1, 50)            # ten minutes before the window closes
SITE = {"off_hours": "20:00-02:00"}   # krew: krew.yaml off_hours


@pytest.fixture(autouse=True)
def _half_full_disk(monkeypatch):
    """The disk row reads the disk the test runs on (data_mount=tmp); a CI runner's is often past the 80 % warn line."""
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(hd.shutil, "disk_usage", lambda p: usage(100 * 2**30, 50 * 2**30, 50 * 2**30))


def _src(tmp: Path, **kw) -> hd.Sources:
    """Every source points into tmp: a test reads nothing of the live house."""
    base = dict(inbox=tmp / "inbox", janitor_dir=tmp / "metrics", battery_dir=tmp / "bat", battery_latest=tmp / "latest.json",
                ms_trail=tmp / "ms.json", improvements=tmp / "imp", keeper=tmp / "keeper.jsonl", dream_log=tmp / "dream.log",
                t0_hits=tmp / "t0.jsonl", kanban_home=tmp / "kanban", tick_state=tmp / "tick.json", schedule=tmp / "sched.json",
                temporaries=tmp / "temps.json", gpu_mode=tmp / "mode", hold=tmp / "hold", reboot_flag=tmp / "reboot", announce_dump=tmp / "announce-dump.jsonl", announce_card=tmp / "announce-card.json", data_mount=tmp, site=SITE,
                unit_show=lambda unit, scope: {}, apt_check=lambda: (0, 0),
                hermes_tree_status=lambda: {"patches": [], "uncovered": [], "beyond": {}},
                # hermetic: without this the adapter shells out to the live `hermes`
                hooks_doctor=lambda profile: (0, ""),
                # hermetic: the Self block's healing reads the live actuator logs otherwise
                self_healing=lambda now: {"text": "no fault detected", "actuators": [], "detected": 0, "cleared": 0},
                # hermetic: the cua check shells out to the live `hermes` + tool tax otherwise
                cua_check=lambda: {"ok": True, "why": [], "doctor": None, "tax": None})
    base.update(kw)
    return hd.Sources(**base)


def _jl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_self_claims_fill_the_row_instead_of_the_icon_column():
    page = hd._render_self({
        "maintaining": {"text": "20 of 21 applies landed"},
        "healing": {"text": "24 of 38 faults cleared"},
        "improving": {"text": "0 of 3 shipped changes measured better"},
    })
    assert page.count('class="row claim"') == 3
    assert ".row.claim{grid-template-columns:minmax(0,1fr)}" in hd.CSS


def test_a_krew_with_no_sources_still_gets_a_page(tmp_path):
    m = hd.build(NOW, _src(tmp_path))
    assert m["covers"]["since"] == "2026-09-17T02:00"              # the day runs close to close
    assert all(sl["status"] in ("unknown", "ok") for sl in m["slips"].values())
    page = hd.render_html(m)
    assert "Krew Daily" in page and "Could not verify" in page
    assert [p.name for p in hd.write(m, tmp_path / "out")][2:] == ["krew-daily.html", "krew-daily.json"]


def test_one_adapter_blowing_up_costs_one_line_not_the_page(tmp_path, monkeypatch):
    def boom(now, since, s):
        raise RuntimeError("no")
    boom.__name__ = "a_keeper"
    monkeypatch.setattr(hd, "ADAPTERS", [boom if f.__name__ == "a_keeper" else f for f in hd.ADAPTERS])
    m = hd.build(NOW, _src(tmp_path))
    assert m["slips"]["keeper"]["status"] == "unknown" and any("keeper" in u for u in m["unverified"])


def test_a_measured_fact_beats_a_newer_carried_one():
    old_probe = hd.fact("health.hindsight", False, NOW - dt.timedelta(hours=2), NOW, status="bad")
    pasted = hd.fact("health.hindsight", True, NOW - dt.timedelta(minutes=5), NOW, source="carried")
    best, conflicts = hd.merge_facts([pasted, old_probe])
    assert best["health.hindsight"]["v"] is False and conflicts[0]["kept"]["source"] == "measured"
    # 09-17: the 07:45 digest pasted "all GREEN" over Steward's 06:12 measured RED


def test_a_carried_red_is_measured_again_before_it_is_shown(tmp_path, monkeypatch):
    # krew: the health units come from the services list; the reference install named hindsight in code
    monkeypatch.setitem(hd.HEALTH_UNITS, "hindsight", "hindsight.service")
    tmp_path.joinpath("ms.json").write_text(json.dumps({"ts_local": "2026-09-17T22:34:00", "health": {"hindsight": False, "stamp_ui": False}, "applied": {}}))
    up = _src(tmp_path, unit_show=lambda unit, scope: {"LoadState": "loaded", "ActiveState": "active"})
    m = hd.build(NOW + dt.timedelta(hours=4), up)               # 05:50, the trail is 7 h old
    assert not [b for b in m["broken"] if "hindsight" in b["what"]]
    assert any("hindsight is back" in c["what"] for c in m["changed"])
    down = _src(tmp_path, unit_show=lambda unit, scope: {"LoadState": "loaded", "ActiveState": "failed"})
    m = hd.build(NOW + dt.timedelta(hours=4), down)
    assert [b for b in m["broken"] if "hindsight" in b["what"] and b["status"] == "bad" and "Measured" in b["evidence"]]
    assert not [b for b in m["broken"] if "stamp_ui" in b["what"]]      # idle by design


def test_an_alert_nobody_heard_reaches_the_page(tmp_path):
    t = (NOW - dt.timedelta(hours=3)).timestamp()
    _jl(tmp_path / "keeper.jsonl", [
        {"ts": t, "event": "announced", "text": "Config drift detected.", "skipped": True, "reason": "quiet_hours"},
        {"ts": t + 60, "event": "announced", "text": "Config drift detected.", "skipped": True, "reason": "duplicate"},
        {"ts": t + 90, "event": "lane_down_budget_exhausted", "lane": "cosy"},
        {"ts": NOW.timestamp() - 30, "event": "lane_restarted", "lane": "ops"}])
    sl = hd.a_keeper(NOW, NOW - dt.timedelta(hours=24), _src(tmp_path))
    whats = [b["what"] for b in sl["broken"]]
    assert any("nobody heard" in w and "Config drift" in w for w in whats) and any("restart budget" in w for w in whats)
    assert sl["status"] == "bad" and "1×" in sl["broken"][0]["evidence"]        # the duplicate is not a second miss


def test_the_janitor_is_judged_by_what_it_did(tmp_path):
    rows = [{"ts": (NOW - dt.timedelta(minutes=5 * i)).isoformat(timespec="seconds"),
             "units": {"hermes-ada-gateway.service": {"active": "failed", "red": True, "nrestarts": 3},
                       "zoey-display.service": {"active": "inactive", "red": False, "parked": True}}} for i in range(6)]
    _jl(tmp_path / "metrics" / f"janitor-{NOW:%Y%m%d}.jsonl", list(reversed(rows)))
    sl = hd.a_janitor(NOW, NOW - dt.timedelta(hours=24), _src(tmp_path))
    assert sl["status"] == "bad" and "none restarted or carded" in sl["headline"]
    assert any("is down" in b["what"] for b in sl["broken"])


def _board(tmp: Path, tasks: list[tuple]) -> None:
    d = tmp / "kanban" / "boards" / "house"
    d.mkdir(parents=True)
    (tmp / "kanban" / "current").write_text("house\n")
    con = sqlite3.connect(d / "kanban.db")
    con.execute("CREATE TABLE tasks (id, title, status, block_kind, created_at, completed_at)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id, kind, created_at, payload)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?)", tasks)
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_1','blocked',?)", (NOW.timestamp() - 20 * 3600,))
    con.commit()
    con.close()


def test_software_calls_are_zoeys_and_only_capability_reaches_the_user(tmp_path):
    t = NOW.timestamp() - 3600
    _board(tmp_path, [("t_1", "Green this try?", "blocked", "needs_input", t, None),
                      ("t_2", "apt install libaom3", "blocked", "capability", t, None),
                      ("t_3", "done thing", "done", None, t, t + 60)])
    m = hd.build(NOW, _src(tmp_path))
    assert m["zoey_queue"] == {"n": 1, "oldest_h": 20}
    assert m["needs_you"] == []                                  # "no terminal in this sandbox" is a re-route, not the User's
    assert any("lacks the tool" in b["what"] and "t_2" in b["evidence"] and b["owner"] == "ada" for b in m["broken"])
    con = sqlite3.connect(tmp_path / "kanban" / "boards" / "house" / "kanban.db")
    con.execute("INSERT INTO task_events (task_id, kind, created_at, payload) VALUES ('t_2','blocked',?,?)",
                (t, json.dumps({"reason": "Every sudo in this session is T0-blocked: NEEDS HUMAN — root kernel"})))
    con.commit(); con.close()
    m = hd.build(NOW, _src(tmp_path))
    assert [n["id"] for n in m["needs_you"]] == ["capability-blocked"] and "t_2" in m["needs_you"][0]["evidence"]
    assert all(n["options"][-1] == "Leave it" for n in m["needs_you"])


def test_roll_call_silence_is_never_fine_and_a_park_is_not_an_alarm(tmp_path):
    (tmp_path / "sched.json").write_text(json.dumps({
        "jobs": [{"name": "ms-nightly", "unit": "nightly-tests.service", "offset_min": 5},
                 {"name": "krew-daily", "unit": "krew-daily.service", "before_close_min": 10}],
        "watch": [{"routine": "janitor", "unit": "krew-janitor.service", "every_min": 5, "grace_min": 15},
                  {"routine": "never-wrote", "unit": "ghost.service", "every_min": 60, "grace_min": 10},
                  {"routine": "team-research", "unit": "tr.service", "every_min": 1440, "grace_min": 60, "parked_in": ["beast"]},
                  {"routine": "kanban-loop", "unit": "kl.service", "every_min": 20, "grace_min": 20}]}))
    (tmp_path / "mode").write_text("beast\n")
    (tmp_path / "tick.json").write_text(json.dumps({"fired": {"ms-nightly": {"at": "2026-09-17T20:05:00", "status": "failed: result=exit-code exit=1"}}}))
    fresh = {"LoadState": "loaded", "Result": "success", "ExecMainExitTimestamp": NOW.timestamp() - 120}
    shows = {"krew-janitor.service": fresh, "kl.service": fresh, "ghost.service": {"LoadState": "not-found"}}
    t = NOW.timestamp() - 3600
    _board(tmp_path, [(f"t_{i}", "x", "blocked", None, t - 60 * 3600, None) for i in range(60)])   # untouched for 2.5 days
    m = hd.build(NOW, _src(tmp_path, unit_show=lambda unit, scope: shows.get(unit, {})))
    rc = m["rollcall"]
    exc = {r["routine"]: r for r in rc["exceptions"]}
    assert rc["fine"] == ["krew-daily", "janitor"] and rc["parked"] == ["team-research"]
    assert exc["never-wrote"]["status"] == "unknown" and "not installed" in exc["never-wrote"]["evidence"]
    assert exc["ms-nightly"]["status"] == "bad" and "exit=1" in exc["ms-nightly"]["evidence"]
    assert exc["kanban-loop"]["status"] == "bad" and "ran on time, not working" in exc["kanban-loop"]["evidence"]


def test_the_page_escapes_what_the_krew_wrote(tmp_path):
    t = NOW.timestamp() - 600
    _jl(tmp_path / "keeper.jsonl", [{"ts": t, "event": "announced", "text": "<script>alert(1)</script>", "skipped": True, "reason": "quiet_hours"}])
    page = hd.render_html(hd.build(NOW, _src(tmp_path)))
    assert "<script>alert" not in page and "&lt;script&gt;" in page


# --- the ticker's closing job ---------------------------------------------------------

SPEC = importlib.util.spec_from_file_location("krew_window_tick_daily", ROOT / "window" / "scripts" / "krew-window-tick.py")
wt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wt)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _tick_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the module's import-time paths stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(wt, tmp_path, monkeypatch)


def test_a_closing_job_follows_the_window_not_the_clock():
    job = [{"name": "krew-daily", "unit": "krew-daily.service", "before_close_min": 10}]
    assert wt.due_jobs(job, elapsed=5, left=355, fired={}) == ([], [])             # 20:05: the night's work comes first
    assert wt.due_jobs(job, elapsed=349, left=11, fired={})[0] == []
    assert wt.due_jobs(job, elapsed=350, left=10, fired={})[0] == job              # 01:50 for 20:00-02:00
    assert wt.due_jobs(job, elapsed=470, left=10, fired={})[0] == job              # 01:50 for 18:00-02:00 too
    assert wt.due_jobs(job, elapsed=355, left=5, fired={"krew-daily": {}})[0] == []   # once per window


def test_the_shipped_registry_names_the_page_and_real_routines():
    # krew: the registry krew ships is config/window-schedule.example.json (copied to $KREW_HOME/window-schedule.json)
    jobs, watch = hd.load_registry(hd.Sources(schedule=ROOT / "config" / "window-schedule.example.json"))
    daily = next(j for j in jobs if j["name"] == "krew-daily")
    assert daily["unit"] == "krew-daily.service" and daily["before_close_min"] == 10 and "offset_min" not in daily
    assert (ROOT / "crew" / "units" / "krew-daily.service").is_file()
    names = [w["routine"] for w in watch]
    assert len(names) == len(set(names)) and all(w.get("unit") or w.get("file") for w in watch)
    live_jobs = {j["name"] for j in jobs if j.get("enabled", True)}
    assert not live_jobs & set(names)          # a routine is a window job or a watched one, never both
    assert len(live_jobs | set(names)) >= 15   # rows MOVE between the lists as routines are keyed to the window
    assert all(wt.load_jobs(hd.Sources().schedule))                                  # the ticker still reads its own file


def test_a_card_archived_by_day_close_still_counts_as_finished(tmp_path):
    """the User 2026-09-17: count filed by created_at and finished by completed_at; the
    day-close archives done cards at 36 h, so `done` alone loses history."""
    t = NOW.timestamp() - 3600
    _board(tmp_path, [("t_1", "landed and archived", "archived", None, t - 86400 * 3, t),
                      ("t_2", "done", "done", None, t, t + 60),
                      ("t_3", "open", "todo", None, t, None)])
    facts = {f["k"]: f["v"] for f in hd.a_board(NOW, NOW - dt.timedelta(hours=24), _src(tmp_path))["facts"]}
    assert facts["board.closed"] == 2 and facts["board.opened"] == 2 and facts["board.open"] == 1


def test_a_long_job_still_running_at_close_is_shown_not_waited_for(tmp_path):
    """pose-freelance runs last in the window (the User 2026-09-17); the page is built at close-10 regardless."""
    (tmp_path / "sched.json").write_text(json.dumps({"jobs": [
        {"name": "pose-freelance", "unit": "pf.service", "offset_min": 240},
        {"name": "krew-daily", "unit": "krew-daily.service", "before_close_min": 10}]}))
    (tmp_path / "tick.json").write_text(json.dumps({"fired": {"pose-freelance": {"at": "2026-09-18T00:05:00", "status": "started", "unit": "pf.service"}}}))
    exc = {r["routine"]: r for r in hd.build(NOW, _src(tmp_path))["rollcall"]["exceptions"]}
    assert exc["pose-freelance"]["status"] == "unknown" and "still running" in exc["pose-freelance"]["evidence"]
    assert exc["pose-freelance"]["when"] == "00:05"


def test_before_the_first_window_the_jobs_are_one_waiting_row(tmp_path):
    (tmp_path / "sched.json").write_text(json.dumps({"jobs": [
        {"name": "ms-nightly", "unit": "n.service", "offset_min": 5}, {"name": "dream-night", "unit": "d.service", "before_close_min": 30},
        {"name": "team-eval", "unit": "e.service", "enabled": False}, {"name": "krew-daily", "unit": "krew-daily.service", "before_close_min": 10}]}))
    ticking = lambda unit, scope: {"LoadState": "loaded", "ExecMainExitTimestamp": NOW.timestamp() - 200} if "tick" in unit else {}
    exc = hd.build(NOW, _src(tmp_path, unit_show=ticking))["rollcall"]["exceptions"]
    assert len(exc) == 1 and exc[0]["routine"] == "window jobs (2): ms-nightly, dream-night" and "no off-hours window has opened" in exc[0]["evidence"]
    exc = hd.build(NOW, _src(tmp_path))["rollcall"]["exceptions"]
    assert "not installed or has stopped" in exc[0]["evidence"]


def test_a_temporary_thing_that_outlives_its_date_is_a_row_with_its_results(tmp_path):
    (tmp_path / "probe.log").write_text("2026-09-17T20:30 200\n2026-09-18T00:20 404\n")
    (tmp_path / "temps.json").write_text(json.dumps({"temporaries": [{
        "id": "nnt-feed-probe", "what": "The NNT feed probes", "why": "They decide whether collect can move.",
        "units": ["p-2030.timer", "p-0020.timer"], "until": "2026-09-18", "result_file": str(tmp_path / "probe.log"), "owner": "M&S"}]}))
    loaded = lambda unit, scope: {"LoadState": "loaded"} if unit.startswith("p-") else {}
    on_time = hd.a_mechanical(dt.datetime(2026, 9, 18, 23, 0), NOW, _src(tmp_path, unit_show=loaded))
    assert on_time["broken"] == []                                        # its last planned night is not late
    late = hd.a_mechanical(dt.datetime(2026, 9, 19, 1, 50), NOW, _src(tmp_path, unit_show=loaded))["broken"]
    assert len(late) == 1 and "past 09-18" in late[0]["what"] and "00:20 404" in late[0]["evidence"] and late[0]["owner"] == "M&S"
    gone = hd.a_mechanical(dt.datetime(2026, 9, 19, 1, 50), NOW, _src(tmp_path, unit_show=lambda u, s: {"LoadState": "not-found"}))
    assert gone["broken"] == []                                           # removed = silent


def test_improving_ends_at_the_effect_not_at_a_closed_card(tmp_path):
    imp = tmp_path / "imp"
    _jl(imp / "seen.jsonl", [{"day": "2026-09-17", "key": "team-improve:a", "status": "filed", "task": "t_1"},
                              {"day": "2026-09-17", "key": "team-improve:a", "status": "blocked", "task": "t_1"}])
    rows = [{"item": "review gate", "stage": "landed", "measure": "no KPI named", "kpi_id": "unnamed"},
            {"item": "drain the ready lane", "stage": "on Zoey's queue", "kpi_id": "kanban_oldest_ready_sec"},
            {"item": "headroom", "stage": "withdrawn", "measure": "partial vote"}]
    (imp / "outcomes.json").write_text(json.dumps({"day": "2026-09-17", "rows": rows}))
    import os
    os.utime(imp / "outcomes.json", (NOW.timestamp() - 600,) * 2)
    sl = hd.a_ideas(NOW, NOW - dt.timedelta(hours=24), _src(tmp_path))
    f = {x["k"]: x["v"] for x in sl["facts"]}
    assert [(r["label"], r["n"]) for r in f["ideas.funnel"]] == [("Ideas filed", 1), ("Agreed by the team", 2), ("With Crissa", 1),
                                                                 ("Try in work", 0), ("Landed", 1), ("Measured better", 0)]
    assert sl["status"] == "warn" and f["ideas.value"] == "1 landed, none measured yet" and "cannot be measured (no KPI named)" in f["ideas.note"]
    rows[0]["measure"] = "worse"
    (imp / "outcomes.json").write_text(json.dumps({"rows": rows}))
    os.utime(imp / "outcomes.json", (NOW.timestamp() - 600,) * 2)
    assert hd.a_ideas(NOW, NOW, _src(tmp_path))["status"] == "bad"          # a landing that hurt is not progress


def test_a_window_job_is_silent_only_after_its_due_time(tmp_path):
    (tmp_path / "sched.json").write_text(json.dumps({"jobs": [
        {"name": "ms-nightly", "unit": "n.service", "offset_min": 5},
        {"name": "pack-drift-check", "unit": "p.service", "after": ["ms-nightly"]},
        {"name": "dream-night", "unit": "d.service", "before_close_min": 30}]}))
    (tmp_path / "tick.json").write_text(json.dumps({"window_key": "2026-09-17", "window": "20:00-02:00", "fired": {}}))
    noon = hd.build(dt.datetime(2026, 9, 17, 12, 6), _src(tmp_path))["rollcall"]      # seeded by hand before the window
    assert noon["exceptions"] == [] and noon["pending"] == ["ms-nightly 20:05", "pack-drift-check 02:00", "dream-night 01:30"]
    late = hd.build(dt.datetime(2026, 9, 17, 21, 0), _src(tmp_path))["rollcall"]      # 55 min past +5, nothing fired
    assert [r["routine"] for r in late["exceptions"]] == ["ms-nightly"] and "was due 09-17 20:05" in late["exceptions"][0]["evidence"]


def test_work_crossing_the_day_line_is_not_a_jam(tmp_path):
    """the User 2026-09-17: on a standing board "task creation and completions will differ but be accurate"."""
    t = NOW.timestamp()
    tasks = [(f"o_{i}", "opened today", "todo", None, t - 3600, None) for i in range(105)]
    tasks += [(f"c_{i}", "finished today", "done", None, t - 40 * 3600, t - 1800) for i in range(69)]
    tasks += [(f"b_{i}", "blocked this morning", "blocked", None, t - 11 * 3600, None) for i in range(50)]
    _board(tmp_path, tasks)
    sl = hd.a_board(NOW, NOW - dt.timedelta(hours=24), _src(tmp_path))
    assert sl["status"] == "ok" and sl["broken"] == []                  # +105 / -69 with nothing stuck
    con = sqlite3.connect(tmp_path / "kanban" / "boards" / "house" / "kanban.db")
    con.execute("INSERT INTO tasks VALUES ('r_1','waiting for a worker','ready',NULL,?,NULL)", (t - 7 * 3600,))
    con.executemany("INSERT INTO task_events (task_id, kind, created_at) VALUES (?, 'protocol_violation', ?)", [(f"o_{i}", t - 600) for i in range(40)])
    con.commit(); con.close()
    sl = hd.a_board(NOW, NOW - dt.timedelta(hours=24), _src(tmp_path))
    whats = [b["what"] for b in sl["broken"]]
    assert sl["status"] == "bad" and "a ready card has waited 7 h" in whats[0] and "40 workers exited" in whats[1]
    con = sqlite3.connect(tmp_path / "kanban" / "boards" / "house" / "kanban.db")
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('r_1', 'promoted', ?)", (t - 600,))
    con.commit(); con.close()
    sl = hd.a_board(NOW, NOW - dt.timedelta(hours=24), _src(tmp_path))
    assert not [b for b in sl["broken"] if "ready card" in b["what"]]      # an old card promoted ten minutes ago is not waiting 7 h


def test_the_page_says_whether_the_board_clears_on_its_own(tmp_path):
    t = NOW.timestamp()
    _board(tmp_path, [(f"b_{i}", "blocked", "blocked", None, t - 3600, None) for i in range(12)])
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    assert not [c for c in hd.build(NOW, _src(tmp_path))["changed"] if "since the last page" in c["what"]]   # first day: nothing to compare
    (inbox / "krew-daily-2026-09-17.json").write_text(json.dumps({"slips": {"board": {"facts": [
        {"k": "board.blocked", "v": 50}, {"k": "board.stale_blocked", "v": 0}, {"k": "board.wasted_runs", "v": 48}]}}}))
    (inbox / "krew-daily-2026-09-18.json").write_text("{}")                                                   # today's own file is not "the last page"
    line = [c for c in hd.build(NOW, _src(tmp_path))["changed"] if "since the last page" in c["what"]][0]
    assert "(clearing)" in line["what"] and "blocked 50 → 12" in line["what"] and "wasted runs 48 → 0" in line["what"]
    assert line["evidence"] == "krew-daily-2026-09-17.json"


def test_a_driver_update_is_never_a_daytime_apt_upgrade(tmp_path):
    sl = hd.a_mechanical(NOW, NOW, _src(tmp_path, apt_check=lambda: (23, 12, ["libheif1", "libsqlite3-0"] + [f"libnvidia-x{i}-595" for i in range(10)])))
    ask = sl["needs_user"][0]
    assert "10 of them are the NVIDIA driver" in ask["evidence"] and "other 2 are safe any time" in ask["evidence"]
    assert ask["ask"].startswith("12 OS security")
    odd = hd.a_mechanical(NOW, NOW, _src(tmp_path, apt_check=lambda: (23, 12, ["libsqlite3-0"] * 12 + ["libnvidia-a-595"] * 16)))["needs_user"][0]
    assert odd["ask"].startswith("28 OS security") and "other 12 are safe" in odd["evidence"]      # never "the other -4"
    assert ask["options"][0].startswith("The safe ones now") and ask["options"][-1] == "Leave it"
    only_gpu = hd.a_mechanical(NOW, NOW, _src(tmp_path, apt_check=lambda: (27, 0, [f"libnvidia-x{i}-595" for i in range(16)])))["needs_user"]
    assert only_gpu and only_gpu[0]["ask"].startswith("16 OS security") and "Nothing else is pending as security" in only_gpu[0]["evidence"]   # apt-check said 0
    assert only_gpu[0]["options"] == ["Install it right before tonight's reboot", "Leave it"]
    plain = hd.a_mechanical(NOW, NOW, _src(tmp_path, apt_check=lambda: (3, 1, ["libsqlite3-0"])))["needs_user"][0]
    assert plain["options"] == ["I'll run it", "Leave it"]


def test_a_kpi_nobody_samples_is_said_out_loud_and_a_slow_brain_is_a_row(tmp_path):
    imp = tmp_path / "imp"
    imp.mkdir()
    (imp / "kpis.json").write_text(json.dumps({"kpis": {"ops_tok_s": {"history": []}}}))
    sl = hd.a_engines(NOW, NOW, _src(tmp_path))
    assert sl["status"] == "ok" and "never recorded a sample" in sl["unverified"][0]          # 0 samples from 09-09 to 09-17
    t = NOW.timestamp()
    week = [{"ts": t - 86400 * (8 - i), "value": v} for i, v in enumerate([63.7, 60.4, 62.6, 61.9, 63.0])]
    (imp / "kpis.json").write_text(json.dumps({"kpis": {"ops_tok_s": {"history": week + [{"ts": t - 600, "value": 61.0}]}}}))
    (imp / "ops-toks-day.json").write_text(json.dumps({"lanes": {"ops": {"avg_running": 1.58, "max_running": 8, "contended_p75": 58.0, "contended_pct": 20}}}))
    sl = hd.a_engines(NOW, NOW, _src(tmp_path))
    assert sl["broken"] == []                                                                  # 3 % is the noise floor
    assert sl["headline"] == "ops 61 tok/s alone, 58 while the other brain works (20 % of the time)"
    (imp / "ops-toks-day.json").write_text(json.dumps({"lanes": {"ops": {"contended_p75": 31.0, "contended_pct": 61}}}))
    sl = hd.a_engines(NOW, NOW, _src(tmp_path))                                                # 09-17: kanban on crew halved ops
    assert "61 % of its working time sharing the GPU" in sl["broken"][0]["what"] and sl["broken"][0]["owner"] == "ada"
    (imp / "ops-toks-day.json").unlink()
    (imp / "kpis.json").write_text(json.dumps({"kpis": {"ops_tok_s": {"history": week + [{"ts": t - 600, "value": 48.0}]}}}))
    sl = hd.a_engines(NOW, NOW, _src(tmp_path))
    assert sl["status"] == "warn" and "23 % slower" in sl["broken"][0]["what"]
    (imp / "kpis.json").write_text(json.dumps({"kpis": {"ops_tok_s": {"history": [{"ts": t - 86400 * 4, "value": 62.0}]}}}))
    assert "last sampled 4 d ago" in hd.a_engines(NOW, NOW, _src(tmp_path))["unverified"][0]


def test_a_job_its_own_timer_still_owns_is_judged_by_its_unit(tmp_path):
    (tmp_path / "sched.json").write_text(json.dumps({"jobs": [
        {"name": "architect-logs", "unit": "logs.service", "scope": "system", "after": ["team-eval"], "legacy_timer": "logs.timer"}]}))
    (tmp_path / "tick.json").write_text(json.dumps({"window_key": "2026-09-16", "window": "20:00-02:00", "fired": {}}))
    ran = {"logs.timer": {"LoadState": "loaded", "UnitFileState": "enabled"},
           "logs.service": {"LoadState": "loaded", "Result": "success", "ExecMainExitTimestamp": NOW.timestamp() - 3600 * 19}}
    rc = hd.build(NOW, _src(tmp_path, unit_show=lambda u, sc: ran.get(u, {})))["rollcall"]
    assert rc["fine"] == ["architect-logs"] and rc["exceptions"] == []                         # it ran at 06:10 by its timer: not silent
    ran["logs.timer"]["UnitFileState"] = "disabled"                                            # handed to the ticker, which never fired it
    rc = hd.build(NOW, _src(tmp_path, unit_show=lambda u, sc: ran.get(u, {})))["rollcall"]
    assert [r["routine"] for r in rc["exceptions"]] == ["architect-logs"]


def test_a_baseline_battery_night_does_not_claim_ninety_new_failures(tmp_path):
    bat = tmp_path / "bat"
    bat.mkdir()
    (bat / "battery-nightly-2026-09-17.json").write_text(json.dumps({
        "ts": (NOW - dt.timedelta(hours=3)).isoformat(), "worst": "FAIL", "results": [],
        "nightly": {"worst": "FAIL", "baseline": True, "new_n": 90, "known_red_n": 14, "checks_failing": ["check:infra/krew-ready"],
                    "repos": {"krew": {"failing": 60}, "rail-presence": {"failing": 30}, "rail-voice": {"failing": 0}}}}))
    sl = hd.a_battery(NOW, NOW, _src(tmp_path))
    assert sl["status"] == "bad" and "90 failing tests, 14 of them known red" in sl["headline"] and "NEW" not in sl["headline"]
    assert "Baseline run" in sl["broken"][0]["evidence"] and "krew-ready" in sl["broken"][0]["evidence"]


def test_an_edit_beyond_the_krew_patches_on_the_hermes_tree_is_a_row(tmp_path):
    """2026-09-17 22:36: a worker edited kanban_db.py in place under a patch."""
    st = {"patches": [{"name": "70-x", "state": "applied", "files": ["hermes_cli/kanban_db.py"]}],
          "uncovered": [], "beyond": {"hermes_cli/kanban_db.py": "102 line(s) differ from HEAD + patches"}}
    sl = hd.a_hermes_tree(NOW, NOW, _src(tmp_path, hermes_tree_status=lambda: st))
    assert sl["status"] == "warn" and sl["broken"][0]["files"] == ["hermes_cli/kanban_db.py"]
    assert "beyond the house patches" in sl["broken"][0]["what"] and "102 line" in sl["broken"][0]["evidence"]
    clean = hd.a_hermes_tree(NOW, NOW, _src(tmp_path, hermes_tree_status=lambda: {"patches": st["patches"], "uncovered": [], "beyond": {}}))
    assert clean["status"] == "ok" and clean["broken"] == []
    stale = hd.a_hermes_tree(NOW, NOW, _src(tmp_path, hermes_tree_status=lambda: {"patches": [{"name": "70-x", "state": "stale", "files": []}], "uncovered": [], "beyond": {}}))
    assert stale["status"] == "bad"
    blown = hd.a_hermes_tree(NOW, NOW, _src(tmp_path, hermes_tree_status=lambda: (_ for _ in ()).throw(RuntimeError("no git"))))
    assert blown["status"] == "unknown"


def test_a_finished_oneshots_last_run_comes_from_the_journal():
    ids = {"s": "7d4958e842da4a758f6c1cdc7b36dcc5", "f": "39f53479d3a045ac8e11786248231fbf", "x": "98e322203f7a4ed290d09fe03c09fe15", "F": "be02cf6855d2428ba40df7e9d022f03d"}
    ok = "\n".join(json.dumps(r) for r in [
        {"MESSAGE_ID": ids["s"], "__REALTIME_TIMESTAMP": "1789689900000000"},
        {"MESSAGE_ID": ids["f"], "__REALTIME_TIMESTAMP": "1789689960000000"}])
    d = hd._journal_last_run("dc.service", "user", lines=ok)
    assert d["ExecMainStartTimestamp"] == 1789689900.0 and d["ExecMainExitTimestamp"] == 1789689960.0 and d["Result"] == "success"
    bad = "\n".join(json.dumps(r) for r in [
        {"MESSAGE_ID": ids["s"], "__REALTIME_TIMESTAMP": "1789689900000000"},
        {"MESSAGE_ID": ids["x"], "__REALTIME_TIMESTAMP": "1789689950000000", "EXIT_STATUS": "5"},
        {"MESSAGE_ID": ids["F"], "__REALTIME_TIMESTAMP": "1789689960000000", "JOB_RESULT": "failed"}])
    d = hd._journal_last_run("dn.service", "user", lines=bad)
    assert d["Result"] == "failed" and d["ExecMainStatus"] == "5"
    assert hd._journal_last_run("none.service", "user", lines="") == {}


def test_hooks_doctor_becomes_a_row_and_never_a_bad(tmp_path):
    """The audit is hermes' own and nothing was running it. It is a WARN, not a bad: a stale
    consent record does not stop the hook — the first live run found 3 issues on all six
    profiles, every one "script modified since approval", while the hooks kept working."""
    seen = []
    m = hd.build(NOW, _src(tmp_path, hooks_doctor=lambda p: seen.append(p) or (
        (3, "⚠ script modified since approval (tier0_gate.py)") if p in ("zoey", "ada") else (0, ""))))
    sl = m["slips"]["hooks"]
    assert sl["status"] == "warn" and "6 issue(s) over 2 of 6 profiles" in sl["headline"]
    row = sl["broken"][0]
    assert row["status"] == "warn" and row["owner"] == "you"
    assert "tier0_gate" in row["evidence"] and "ada 3" in row["evidence"] and "zoey 3" in row["evidence"]
    # and it says why a re-approval is not a mid-flight action
    assert "fail_closed" in row["evidence"] and "NEW worker" in row["evidence"]
    assert seen == list(hd.HOOK_CREW)


def test_clean_hooks_are_one_quiet_line(tmp_path):
    m = hd.build(NOW, _src(tmp_path, hooks_doctor=lambda p: (0, "")))
    sl = m["slips"]["hooks"]
    assert sl["status"] == "ok" and sl["headline"] == "6 profiles clean" and not sl["broken"]


def test_an_unrunnable_doctor_is_unverified_not_healthy(tmp_path):
    """Silence is never fine: if the probe cannot run, the page says so rather than green."""
    m = hd.build(NOW, _src(tmp_path, hooks_doctor=lambda p: (-1, "FileNotFoundError: hermes")))
    sl = m["slips"]["hooks"]
    assert sl["status"] == "unknown" and len(sl["unverified"]) == len(hd.HOOK_CREW)


def test_the_hooks_probe_has_a_kill(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_DAILY_HOOKS", "0")
    def boom(p):
        raise AssertionError("probe ran with the kill set")
    assert hd.a_hooks(NOW, NOW, _src(tmp_path, hooks_doctor=boom))["status"] == "skipped"


def test_the_reader_gate_row_says_whether_it_is_actually_armed(tmp_path):
    """It hardcoded "report-only until KREW_LIVENESS_ACT=1" and so told the User the gate was
    inert for the whole morning of 2026-09-22, hours after it was armed. The `act` flag is in
    the same file the row is built from — and is already read one line above for readers.act."""
    t = NOW.timestamp()
    _board(tmp_path, [("t_late", "todo", None, None, t - 3600, None)])
    live = {"at": t, "counts": {"zoey": 1}, "overdue": ["t_late"],
            "none": [], "due": [], "needs_user": [], "rows": []}
    for act, expect in ((True, "landing wakes"), (False, "report-only")):
        (tmp_path / "kanban-liveness.json").write_text(json.dumps({**live, "act": act}))
        sl = hd.a_board_flow(NOW, NOW, _src(tmp_path))
        rows = [b for b in sl["broken"] if "reader's deadline" in b["what"]]
        assert rows, f"no reader-gate row at act={act}"
        assert expect in rows[0]["evidence"], (act, rows[0]["evidence"])


def _canary_rows(tmp: Path, rows: list[dict]) -> None:
    _jl(tmp / "metrics" / f"janitor-{NOW:%Y%m%d}.jsonl", rows)


def test_code_canary_row_is_unknown_without_samples_and_ok_on_clean_ones(tmp_path):
    since = NOW - dt.timedelta(hours=24)
    assert hd.a_code_canary(NOW, since, _src(tmp_path))["status"] == "unknown"   # silence is never fine
    _canary_rows(tmp_path, [{"ts": "2026-09-18T01:40:00", "code_canary": {"ok": True, "trees": 19}}])
    assert hd.a_code_canary(NOW, since, _src(tmp_path))["status"] == "ok"


def test_code_canary_row_names_the_drop_and_how_to_accept_or_repair_it(tmp_path):
    since = NOW - dt.timedelta(hours=24)
    drop = {"ok": False, "trees": 19, "suspects": ["t_95882010"],
            "drops": [{"tree": "hermes-agent/venv", "was": 6568, "high": 6568, "now": 0}]}
    _canary_rows(tmp_path, [{"ts": "2026-09-18T01:00:00", "code_canary": {"ok": True, "trees": 19}},
                            {"ts": "2026-09-18T01:25:00", "code_canary": drop}])
    row = hd.a_code_canary(NOW, since, _src(tmp_path))
    assert row["status"] == "bad", "a drop still standing at the close is bad"
    ev = json.dumps(row)
    assert "hermes-agent/venv" in ev and "t_95882010" in ev
    assert "rebaseline --tree" in ev and "restore-repair-20260923" in ev
    _canary_rows(tmp_path, [{"ts": "2026-09-18T01:25:00", "code_canary": drop},
                            {"ts": "2026-09-18T01:45:00", "code_canary": {"ok": True, "trees": 19}}])
    assert hd.a_code_canary(NOW, since, _src(tmp_path))["status"] == "warn", "repaired today: a row, not an alarm"
