"""Silent skips become findings (the User 09-30), proved on the real history:
the vLLM canary's 09-17..09-29 nights and the 09-29 battery whose pytest ran in 7 ms."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)

import ms_silent_skips as S

FIX = Path(__file__).parent / "fixtures" / "silent_skips"
ENV = "vllm-0271-cu132"


def _canary():
    return json.loads((FIX / "canary-20260917-29.json").read_text())["nights"]


def _run_canary(tmp_path, nights=None):
    fp = tmp_path / "findings.json"
    hist = _canary()
    log = {}
    for day in sorted(hist):
        if nights and day not in nights:
            continue
        obs = S.observe_ms_report(hist[day], source=f"ms-trail-{day}.json")
        log[day] = S.apply(day, obs, findings_path=fp)
    return fp, log


def _open(fp):
    return [r for r in json.loads(fp.read_text())["findings"] if r.get("status") == "open"]


def test_the_history_is_what_the_reports_say():
    hist = _canary()
    skips = [d for d, r in hist.items() if r["applied"]]
    assert len(skips) == 12 and "2026-09-26" not in skips      # 09-26: ledger unreadable, no canary
    assert hist["2026-09-26"]["ledger_unreadable"]
    for d in skips:
        assert hist[d]["applied"][ENV]["canary"].startswith("UNVERIFIED")


def test_two_nights_are_silent_the_third_files_one_finding(tmp_path):
    fp, log = _run_canary(tmp_path)
    assert log["2026-09-17"] == [] and log["2026-09-18"] == []
    assert [c.action for c in log["2026-09-19"]] == ["opened"]
    rows = _open(fp)
    assert len(rows) == 1
    r = rows[0]
    assert r["key"] == f"silent-skip:canary:{ENV}" and r["check"] == "canary" and r["env"] == ENV
    assert r["opened_on"] == "2026-09-19"
    assert r["numbers"]["2026-09-17"] == "free 3387 MiB < needed 7109 MiB"


def test_later_nights_update_never_duplicate(tmp_path):
    fp, log = _run_canary(tmp_path)
    assert all([c.action for c in log[d]] == ["updated"] for d in log if d > "2026-09-19" and log[d])
    rows = [r for r in json.loads(fp.read_text())["findings"] if r.get("key", "").startswith("silent-skip:")]
    assert len(rows) == 1
    r = rows[0]
    assert len(r["nights"]) == 12 and r["nights"][-1] == "2026-09-29"
    assert r["no_record"] == ["2026-09-26"]                   # neutral: neither counted nor broke it
    assert r["numbers"]["2026-09-29"] == "free 7043 MiB < needed 7109 MiB"
    assert r["numbers"]["2026-09-21"] == "free 3275 MiB < needed 7109 MiB"
    assert "12 nights in a row" in r["title"]
    assert "free 7043 MiB < needed 7109 MiB" in r["finding"]


def test_rerunning_a_night_changes_nothing(tmp_path):
    fp, _ = _run_canary(tmp_path)
    before = fp.read_text()
    obs = S.observe_ms_report(_canary()["2026-09-29"])
    assert S.apply("2026-09-29", obs, findings_path=fp) == []
    assert fp.read_text() == before


def test_the_first_real_run_closes_it(tmp_path):
    fp, _ = _run_canary(tmp_path)
    ran = {"applied": {ENV: {"canary": "PASS 0.5B pong 1.2 s"}}}
    ch = S.apply("2026-09-30", S.observe_ms_report(ran), findings_path=fp)
    assert [c.action for c in ch] == ["closed"]
    assert _open(fp) == []
    r = json.loads(fp.read_text())["findings"][-1]
    assert r["status"] == "closed" and r["closed_on"] == "2026-09-30"
    # and the streak starts from zero: two skips afterwards file nothing
    skip = _canary()["2026-09-29"]
    assert S.apply("2026-10-01", S.observe_ms_report(skip), findings_path=fp) == []
    assert S.apply("2026-10-02", S.observe_ms_report(skip), findings_path=fp) == []
    assert _open(fp) == []


def test_other_findings_are_left_alone(tmp_path):
    fp = tmp_path / "findings.json"
    fp.write_text(json.dumps({"schema": "ms_findings/v1", "findings": [{"title": "hand", "finding": "x"}]}))
    hist = _canary()
    for d in ("2026-09-17", "2026-09-18", "2026-09-19"):
        S.apply(d, S.observe_ms_report(hist[d]), findings_path=fp)
    rows = json.loads(fp.read_text())["findings"]
    assert rows[0] == {"title": "hand", "finding": "x"} and len(rows) == 2


def _battery(name):
    return json.loads((FIX / name).read_text())


def test_tonights_7ms_battery_is_a_skip_on_every_repo():
    b = _battery("battery-20260930T000855Z.json")
    obs = S.observe_battery(b)
    assert {o.env for o in obs} == {"app-rails", "rail-presence", "rail-stt", "rail-voice"}
    assert all(not o.ran for o in obs)
    hr = next(o for o in obs if o.env == "app-rails")
    assert hr.numbers.startswith("finished in 7 ms, 0 tests ran")
    assert S.battery_night(b) == "2026-09-29"                 # 00:08Z is the evening before


def test_a_real_battery_run_is_not_a_skip_even_under_a_second():
    obs = {o.env: o for o in S.observe_battery(_battery("battery-20260929T001648Z.json"))}
    assert all(o.ran for o in obs.values())
    # rail-stt: "2 failed, 173 passed, 18 skipped in 0.59s", 741 ms - real tests, a real answer
    assert obs["rail-stt"].ran


def test_battery_three_nights_files_one_per_repo_and_a_real_night_closes(tmp_path):
    fp = tmp_path / "findings.json"
    seven = _battery("battery-20260930T000855Z.json")
    real = _battery("battery-20260929T001648Z.json")
    assert S.apply("2026-09-28", S.observe_battery(real), findings_path=fp) == []
    for d in ("2026-09-29", "2026-09-30"):
        assert S.apply(d, S.observe_battery(seven), findings_path=fp) == []
    ch = S.apply("2026-10-01", S.observe_battery(seven), findings_path=fp)
    assert sorted(c.key for c in ch if c.action == "opened") == [
        "silent-skip:battery:app-rails", "silent-skip:battery:rail-presence",
        "silent-skip:battery:rail-stt", "silent-skip:battery:rail-voice"]
    ch = S.apply("2026-10-02", S.observe_battery(real), findings_path=fp)
    assert sorted(c.action for c in ch) == ["closed"] * 4
    assert _open(fp) == []


def test_zero_tests_counts_as_a_skip_whatever_the_time():
    b = {"results": [{"group": "pytest", "name": "r", "status": "PASS",
                      "detail": "no tests ran in 4.10s", "ms": 4100}]}
    (o,) = S.observe_battery(b)
    assert not o.ran and "0 tests ran" in o.numbers


def test_roots_smoke_survey_observers():
    rep = {"roots": {"mode": "report"}, "envs": {
        "voxtral-cpu-trial": {"smoke_baseline": True, "measured": True,
                              "roots": {"error": "no roots declared for this env (config/ms-env-roots.json)"}},
        "old": {"smoke_baseline": None, "measured": False, "roots": {"dists": 3}}}}
    got = {(o.check, o.env): o for o in S.observe_ms_report(rep)}
    assert not got[("roots", "voxtral-cpu-trial")].ran
    assert "no roots declared" in got[("roots", "voxtral-cpu-trial")].numbers
    assert got[("smoke", "voxtral-cpu-trial")].ran and got[("survey", "voxtral-cpu-trial")].ran
    assert not got[("smoke", "old")].ran and not got[("survey", "old")].ran
    assert got[("roots", "old")].ran


def test_writers_use_ms_findings_and_the_kill_switch(tmp_path, monkeypatch):
    fp = tmp_path / "f.json"
    monkeypatch.setenv("MS_FINDINGS", str(fp))
    assert S.default_findings_path() == fp
    seven = _battery("battery-20260930T000855Z.json")
    S.record_battery_night(seven, source="x")
    assert (tmp_path / "silent-skips.json").is_file()
    monkeypatch.setattr(S, "ENABLED", False)
    assert S.record_battery_night(seven) == [] and S.record_ms_night({}, "2026-09-30") == []


def test_both_nightlies_call_it():
    root = Path(__file__).resolve().parents[1]
    ms = (root / "ms" / "scripts" / "ms-trail-run.py").read_text()
    assert "_sk.record_ms_night(" in ms and "findings_path=FINDINGS_PATH" in ms
    i = ms.index("_sk.record_ms_night(")
    assert "if not adhoc:" in ms[i - 400:i]                   # a night, never an ad-hoc run
    # the second nightly (the reference install's battery script) stays with its install
