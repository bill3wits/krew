"""The window ticker in krew: off_hours from krew.yaml (unset = idle and says why), jobs from
$KREW_HOME/window-schedule.json, each job started once a window, its verdict read back, the M&S nightly's own
status line, and units that the installer writes but never enables. Nothing here calls systemctl."""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))


@pytest.fixture
def kh(tmp_path, monkeypatch):
    home = tmp_path / ".krew"
    home.mkdir()
    monkeypatch.setenv("KREW_HOME", str(home))
    monkeypatch.delenv("KREW_CONFIG", raising=False)
    monkeypatch.setenv("KREW_JOB_STATUS_DIR", str(home / "job-status"))
    return home


def _tick_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("krew_window_tick", ROOT / "window" / "scripts" / "krew-window-tick.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_off_hours_unset_is_idle_and_names_the_setting(kh):
    import krew_hours
    importlib.reload(krew_hours)
    oh = krew_hours.off_hours()
    assert oh["source"] == "unset" and str(kh / "krew.yaml") in oh["why"]
    T = _tick_mod()
    r = T.tick(dt.datetime(2026, 10, 6, 23, 0), jobs=[], state_path=kh / "s.json", day_env=None)
    assert r["action"] == "idle" and "off_hours is not set" in r["why"]


def test_off_hours_is_read_from_krew_yaml(kh):
    (kh / "krew.yaml").write_text('board:\n  author: ""\noff_hours: "22:00-06:00"   # mine\n')
    import krew_hours
    oh = krew_hours.off_hours()
    assert (oh["source"], oh["window"], oh["start"], oh["end"]) == ("init", "22:00-06:00", 1320, 360)
    assert krew_hours.quiet_hours()["source"] == "none"          # quiet hours default to never (2026-10-15)
    (kh / "krew.yaml").write_text("off_hours: 9pm\n")
    assert krew_hours.off_hours()["source"] == "invalid"


def test_a_night_starts_each_job_once_and_reads_the_verdict_back(kh):
    T = _tick_mod()
    site = {"off_hours": "22:00-06:00"}
    jobs = [{"name": "ms-nightly", "unit": "krew-ms-nightly.service", "offset_min": 5, "needs_min": 30,
             "status_line": "ms-nightly"},
            {"name": "after-ms", "unit": "x.service", "after": ["ms-nightly"]}]
    started = []
    kw = dict(site=site, jobs=jobs, state_path=kh / "tick.json", day_env=kh / "krew-day.env",
              starter=lambda u: (started.append(u), (0, ""))[1], legacy=lambda u: False, active=lambda u: False)
    r = T.tick(dt.datetime(2026, 10, 6, 21, 59), **kw)
    assert r["action"] == "idle" and "outside the off-hours" in r["why"]
    r = T.tick(dt.datetime(2026, 10, 6, 22, 3), **kw, result=lambda u, f=0: {})
    assert started == [] and (kh / "krew-day.env").read_text() == "KREW_DAY=2026-10-06\n"
    r = T.tick(dt.datetime(2026, 10, 6, 22, 6), **kw, result=lambda u, f=0: {})
    assert started == ["krew-ms-nightly.service"]
    fired_at = json.loads((kh / "tick.json").read_text())["fired"]["ms-nightly"]["epoch"]
    done = {"active": "inactive", "result": "success", "status": "0", "started": fired_at + 5}
    # exit 0 but no status line: not ok (a silent green)
    r = T.tick(dt.datetime(2026, 10, 6, 22, 11), **kw, result=lambda u, f=0: done)
    assert any("wrote no status line" in o for o in r["outcomes"])
    # the same tick starts the chained job: a failed verdict is final (hold_on_failed is per job)
    assert started == ["krew-ms-nightly.service", "x.service"]
    r = T.tick(dt.datetime(2026, 10, 6, 22, 16), **kw, result=lambda u, f=0: done)
    assert started == ["krew-ms-nightly.service", "x.service"], "each job once a window"


def test_status_line_ok_makes_the_verdict_ok(kh):
    import job_status
    T = _tick_mod()
    jobs = [{"name": "ms-nightly", "unit": "u.service", "status_line": "ms-nightly"}]
    kw = dict(site={"off_hours": "22:00-06:00"}, jobs=jobs, state_path=kh / "t.json", day_env=None,
              starter=lambda u: (0, ""), legacy=lambda u: False, active=lambda u: False)
    T.tick(dt.datetime(2026, 10, 6, 22, 1), **kw, result=lambda u, f=0: {})
    fired_at = json.loads((kh / "t.json").read_text())["fired"]["ms-nightly"]["epoch"]
    p = job_status.write("ms-nightly", "2026-10-06", "ok", "x" * 900)
    rec = json.loads(p.read_text())
    p.write_text(json.dumps({**rec, "ts": fired_at + 10}))   # written after the start (the test's clock is fake)
    assert len(job_status.read("ms-nightly", "2026-10-06")["reason"]) == 900, "the reason is never cut"
    done = {"active": "inactive", "result": "success", "status": "0", "started": fired_at + 1}
    r = T.tick(dt.datetime(2026, 10, 6, 22, 30), **kw, result=lambda u, f=0: done)
    assert r["outcomes"] == ["ms-nightly: ok"]


def test_a_missing_schedule_is_said_not_crashed(kh):
    T = _tick_mod()
    assert T.load_jobs(kh / "nope.json") == []
    assert "no schedule at" in (kh / "logs" / "krew-window-tick.log").read_text()


def test_legacy_timer_owns_the_job(kh):
    T = _tick_mod()
    jobs = [{"name": "ms-nightly", "unit": "krew-ms-nightly.service", "legacy_timer": "krew-ms-nightly.timer"}]
    r = T.tick(dt.datetime(2026, 10, 6, 22, 30), site={"off_hours": "22:00-06:00"}, jobs=jobs,
               state_path=kh / "t.json", day_env=None, starter=lambda u: pytest.fail("started"),
               legacy=lambda u: True, active=lambda u: False, result=lambda u, f=0: {})
    assert any("hands off" in n for n in r["notes"])


def test_schedule_example_names_units_krew_ships():
    d = json.loads((ROOT / "config" / "window-schedule.example.json").read_text())
    units = {p.name for d in ("units", "crew/units") for p in (ROOT / d).iterdir()}   # the chain's crew jobs live in crew/units
    for j in d["jobs"]:
        assert j["unit"] in units and (not j.get("legacy_timer") or j["legacy_timer"] in units)
    assert d["_reference_install"]["jobs"], "the reference schedule is kept for reference"


def test_installer_writes_units_and_enables_none(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("krew_install", ROOT / "scripts" / "krew-install.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    calls = []
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: calls.append(a))
    out = []
    done = m.write_units(tmp_path / "units", tmp_path / ".krew", "/opt/py/bin/python", force=False, dry=False,
                         say=out.append)
    assert set(done) == {p.name for p in (ROOT / "units").glob("krew-*.*")}
    for n in done:
        body = (tmp_path / "units" / n).read_text()
        assert "{" not in body.replace("${", ""), n
    svc = (tmp_path / "units" / "krew-ms-nightly.service").read_text()
    assert f"ExecStart=/opt/py/bin/python {ROOT}/ms/scripts/krew-ms.py --nightly" in svc
    assert calls == [] and any("none enabled" in s for s in out)


def test_nightly_writes_the_status_line(tmp_path):
    pytest.importorskip("packaging")
    home = tmp_path / "home"
    (home / ".krew").mkdir(parents=True)
    proc = tmp_path / "proc" / "net"
    proc.mkdir(parents=True)
    for f in ("tcp", "tcp6"):
        (proc / f).write_text("sl local rem st\n")
    env = {"HOME": str(home), "USER": "t", "PATH": "/nonexistent", "LANG": "C.UTF-8",
           "KREW_HOME": str(home / ".krew"), "KREW_MS_PROC_DIR": str(tmp_path / "proc"),
           "HERMES_BIN": str(tmp_path / "none"), "KREW_DAY": "2026-10-06"}
    r = subprocess.run([sys.executable, str(ROOT / "ms" / "scripts" / "krew-ms.py"), "--nightly"], env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    line = json.loads((home / ".krew" / "job-status" / "ms-nightly-2026-10-06.json").read_text())
    assert line["status"] == "ok"
    assert "--apply-green" not in (home / ".krew" / "ms" / "krew-ms.log").read_text().split("nightly:")[0]


def _crissa_site(tmp_path, monkeypatch, body):
    zh = tmp_path / "zoeyos"
    (zh / "identity").mkdir(parents=True)
    (zh / "identity" / "site.json").write_text(body)
    monkeypatch.setenv("ZOEYOS_HOME", str(zh))


def test_with_no_off_hours_the_window_is_the_one_crissas_setup_saved(kh, tmp_path, monkeypatch):
    """The owner 2026-10-15: one answer, asked in Crissa's Setup; krew.yaml's own value still wins."""
    import krew_hours
    _crissa_site(tmp_path, monkeypatch, '{"training_window": "18:00-02:00"}')
    oh = krew_hours.off_hours()
    assert (oh["source"], oh.get("from"), oh["window"], oh["start"], oh["end"]) == ("init", "crissa", "18:00-02:00", 1080, 120)
    T = _tick_mod()
    r = T.tick(dt.datetime(2026, 10, 6, 23, 0), jobs=[], state_path=kh / "s.json", day_env=None)
    assert "off_hours is not set" not in str(r.get("why") or "")
    (kh / "krew.yaml").write_text('off_hours: "22:00-06:00"\n')
    oh = krew_hours.off_hours()
    assert (oh["window"], oh.get("from")) == ("22:00-06:00", None)


def test_crissas_window_is_ignored_when_bad_missing_or_killed(kh, tmp_path, monkeypatch):
    import krew_hours
    _crissa_site(tmp_path, monkeypatch, '{"training_window": "6pm"}')
    assert krew_hours.off_hours()["source"] == "unset"
    _crissa_site(tmp_path / "b", monkeypatch, "not json")
    assert krew_hours.off_hours()["source"] == "unset"
    _crissa_site(tmp_path / "c", monkeypatch, '{"training_window": "18:00-02:00"}')
    monkeypatch.setenv("KREW_OFF_HOURS_FROM_CRISSA", "0")
    assert krew_hours.off_hours()["source"] == "unset"
    monkeypatch.delenv("KREW_OFF_HOURS_FROM_CRISSA")
    monkeypatch.setenv("ZOEYOS_IDENTITY", str(tmp_path / "nowhere"))
    assert krew_hours.off_hours()["source"] == "unset"
