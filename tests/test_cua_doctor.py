"""The computer-use check: the hands keep a body after a hermes update (2026-10-01).

The 09-27 update (upstream 5e4a2a3d24) made computer_use available only when hermes's
own package manager installed cua-driver, or when HERMES_CUA_DRIVER_CMD names it. The
house driver is neither, so computer_use read "unavailable" and dropped from every hands
kit with no error, until this check caught it. Every probe here is stubbed: the doctor
and the tool-tax output are fixtures, never the live house.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import cua_doctor as cd  # noqa: E402

# --- stubbed probes ------------------------------------------------------------

DOCTOR_OK = {"rc": 0, "lines": ["✅ cua-driver 0.31.0", "✅ session active"]}
DOCTOR_NOT_INSTALLED = {"rc": 0, "lines": ["❌ cua-driver: not installed", "⏭ session"]}
DOCTOR_RC = {"rc": 2, "lines": ["boom"]}
TAX_OK = {"unavailable": [], "computer_use": True}
TAX_UNAVAILABLE = {"unavailable": ["computer_use", "image_generate"], "computer_use": False}
TAX_NO_TOOL = {"unavailable": [], "computer_use": False}


class FakeRun:
    """A subprocess.run stand-in: every probe answer comes from the fixture dict."""

    def __init__(self, rc=0, stdout="", stderr=""):
        self.rc, self.stdout, self.stderr = rc, stdout, stderr

    def __call__(self, argv, **kw):
        return types.SimpleNamespace(returncode=self.rc, stdout=self.stdout, stderr=self.stderr)


def _runner(stdout: str, rc: int = 0) -> FakeRun:
    return FakeRun(rc=rc, stdout=stdout)


def _tax_json(unavailable, rows) -> str:
    return json.dumps({"crissa": {"unavailable": unavailable, "rows": rows}})


# --- check(): the two facts, read ------------------------------------------------

def test_ok_is_quiet_and_green():
    out = cd.check(run_doctor_=lambda: DOCTOR_OK, run_tax=lambda: TAX_OK)
    assert out == {"ok": True, "why": [], "doctor": DOCTOR_OK, "tax": TAX_OK}


def test_not_installed_is_the_0927_failure():
    """'cua-driver: not installed' in the doctor is the alarm, whatever the rc."""
    out = cd.check(run_doctor_=lambda: DOCTOR_NOT_INSTALLED, run_tax=lambda: TAX_OK)
    assert out["ok"] is False
    assert any("not installed" in w for w in out["why"])


def test_a_nonzero_doctor_rc_is_the_alarm():
    out = cd.check(run_doctor_=lambda: DOCTOR_RC, run_tax=lambda: TAX_OK)
    assert out["ok"] is False and any("rc=2" in w for w in out["why"])


def test_computer_use_in_unavailable_is_the_alarm():
    out = cd.check(run_doctor_=lambda: DOCTOR_OK, run_tax=lambda: TAX_UNAVAILABLE)
    assert out["ok"] is False
    assert any("unavailable" in w for w in out["why"])


def test_a_kit_with_no_computer_use_at_all_is_the_alarm():
    out = cd.check(run_doctor_=lambda: DOCTOR_OK, run_tax=lambda: TAX_NO_TOOL)
    assert out["ok"] is False
    assert any("no computer_use" in w for w in out["why"])


def test_both_facts_bad_says_both():
    out = cd.check(run_doctor_=lambda: DOCTOR_NOT_INSTALLED, run_tax=lambda: TAX_UNAVAILABLE)
    assert out["ok"] is False and len(out["why"]) == 2


def test_a_probe_that_crashes_says_so_in_why():
    out = cd.check(run_doctor_=lambda: (_ for _ in ()).throw(FileNotFoundError("no hermes")),
                   run_tax=lambda: TAX_OK)
    assert out["ok"] is False and any("could not run" in w for w in out["why"])


def test_the_kill_reports_off_not_green(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_CUA_DOCTOR", "0")

    def boom():
        raise AssertionError("the probes ran with the kill set")

    out = cd.check(run_doctor_=boom, run_tax=boom)
    assert out == {"ok": True, "off": True, "why": [], "doctor": None, "tax": None}


# --- run_tool_tax: the runner shape ----------------------------------------------

def test_run_tool_tax_reads_unavailable_and_rows(monkeypatch):
    rows = [{"tool": "terminal"}, {"tool": "computer_use"}]
    monkeypatch.setattr(cd, "TAX", Path("/nonexistent/krew-tool-tax.py"))
    r = cd.run_tool_tax(runner=_runner(_tax_json(["image_generate"], rows)))
    assert r == {"unavailable": ["image_generate"], "computer_use": True}


def test_run_tool_tax_reports_a_nonzero_rc(monkeypatch):
    monkeypatch.setattr(cd, "TAX", Path("/nonexistent/krew-tool-tax.py"))
    r = cd.run_tool_tax(runner=_runner("", rc=3))
    assert "rc=3" in r["error"]


def test_run_tool_tax_reports_non_json(monkeypatch):
    monkeypatch.setattr(cd, "TAX", Path("/nonexistent/krew-tool-tax.py"))
    r = cd.run_tool_tax(runner=_runner("not json at all"))
    assert r["error"].startswith("not json")


def test_run_tool_tax_reads_pretty_printed_multi_line_json(monkeypatch):
    # 2026-10-01 (t_cb284264): krew-tool-tax --json emits indent=1, so the fact
    # is the whole stdout; parsing only the last line saw a closing brace.
    pretty = json.dumps({"crissa": {"unavailable": [], "rows": [{"tool": "computer_use"}]}},
                        indent=1)
    monkeypatch.setattr(cd, "TAX", Path("/nonexistent/krew-tool-tax.py"))
    r = cd.run_tool_tax(runner=_runner(pretty))
    assert r == {"unavailable": [], "computer_use": True}


def test_run_tool_tax_reports_a_profile_error(monkeypatch):
    monkeypatch.setattr(cd, "TAX", Path("/nonexistent/krew-tool-tax.py"))
    r = cd.run_tool_tax(runner=_runner(json.dumps({"crissa": {"error": "boom"}})))
    assert r["error"] == "boom"


# --- fail_loud: the report-only alarm ---------------------------------------------

def test_fail_loud_files_a_daily_line_and_a_card(monkeypatch, tmp_path):
    seen = []
    out = {"ok": False, "why": ["computer_use unavailable"]}

    def card(title, body, **kw):
        seen.append((title, body))
        return types.SimpleNamespace(id="t_alarm")

    state = tmp_path / "alarm.json"
    alarm = cd.fail_loud(out, by="hermes-tree update", card=card, state=state, now=1000.0)
    assert alarm["channel"] == "Krew Daily" and "computer_use unavailable" in alarm["line"]
    assert alarm["card"] == "t_alarm"
    assert len(seen) == 1 and "HERMES_CUA_DRIVER_CMD" in seen[0][1]
    assert json.loads(state.read_text())["day"]  # the day stamp was recorded


def test_fail_loud_files_at_most_one_card_a_day(monkeypatch, tmp_path):
    seen = []
    state = tmp_path / "alarm.json"
    cd._record_alarm(state, 1000.0)  # the day stamp is already down
    cd.fail_loud({"ok": False, "why": ["x"]}, card=lambda *a, **k: seen.append(1) or
                 types.SimpleNamespace(id="t_alarm"), state=state, now=1001.0)
    assert seen == [] and json.loads(state.read_text())["day"]


def test_fail_loud_keeps_the_daily_line_when_the_card_fails(monkeypatch, tmp_path):
    def card(title, body, **kw):
        raise RuntimeError("no board")

    alarm = cd.fail_loud({"ok": False, "why": ["x"]}, card=card, state=tmp_path / "a.json",
                         now=1000.0)
    assert alarm["channel"] == "Krew Daily" and "line" in alarm
    assert alarm["card"].startswith("failed:")


def test_fail_loud_with_no_card_runner_is_daily_only(monkeypatch, tmp_path):
    alarm = cd.fail_loud({"ok": False, "why": ["x"]}, card=None, state=tmp_path / "a.json",
                         now=1000.0)
    assert alarm["channel"] == "Krew Daily" and alarm["card"] == "off"


def test_fail_loud_never_raises_even_on_a_missing_state_dir(monkeypatch, tmp_path):
    alarm = cd.fail_loud({"ok": False, "why": ["x"]}, card=None,
                         state=tmp_path / "deep" / "missing" / "a.json", now=1000.0)
    assert alarm["channel"] == "Krew Daily"


# --- hermes-tree wiring ------------------------------------------------------------

def _hermes_tree_mod():
    spec = importlib.util.spec_from_file_location("hermes_tree", ROOT / "crew" / "scripts" / "hermes-tree.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_cua_doctor_after_update_is_report_only_and_ok(monkeypatch):
    ht = _hermes_tree_mod()
    ok = {"ok": True, "why": [], "doctor": DOCTOR_OK, "tax": TAX_OK}
    monkeypatch.setattr(cd, "check", lambda: ok)
    assert ht.cua_doctor_after_update() == {"ok": True}


def test_cua_doctor_after_update_fails_loud_but_never_raises(monkeypatch):
    ht = _hermes_tree_mod()
    bad = {"ok": False, "why": ["cua-driver: not installed"]}
    alarms = []
    monkeypatch.setattr(cd, "check", lambda: bad)
    monkeypatch.setattr(cd, "fail_loud", lambda out, **kw: alarms.append(out) or {"channel": "Krew Daily"})
    out = ht.cua_doctor_after_update()
    assert out["ok"] is False and out["alarm"] == {"channel": "Krew Daily"} and alarms[0] is bad


def test_cua_doctor_after_update_off_is_quiet(monkeypatch):
    ht = _hermes_tree_mod()
    with monkeypatch.context() as m:
        m.setenv("KREW_CUA_DOCTOR", "0")
        assert ht.cua_doctor_after_update() == {"ok": True}


# --- the gateway's own environment (KREW_CUA_DOCTOR_GATEWAY, OFF by default) ------------

GW_BEFORE = {"unit": "hermes-gateway.service", "pid": 42, "cmd": None, "resolves": False, "sessions": 0}
GW_AFTER = {"unit": "hermes-gateway.service", "pid": 43, "cmd": "~/.local/bin/cua-driver",
            "resolves": True, "sessions": 0}


def test_gateway_check_is_off_by_default_and_changes_nothing(monkeypatch):
    monkeypatch.delenv("KREW_CUA_DOCTOR_GATEWAY", raising=False)
    called = []
    out = cd.check(run_doctor_=lambda: DOCTOR_OK, run_tax=lambda: TAX_OK,
                   run_gateway_=lambda: called.append(1) or GW_BEFORE)
    assert out == {"ok": True, "why": [], "doctor": DOCTOR_OK, "tax": TAX_OK} and not called


def test_gateway_without_the_variable_is_the_0927_host_failure(monkeypatch):
    """The 10-01 09:44 host start: Crissa's .env had the line, the gateway's process did not."""
    monkeypatch.setenv("KREW_CUA_DOCTOR_GATEWAY", "1")
    out = cd.check(run_doctor_=lambda: DOCTOR_OK, run_tax=lambda: TAX_OK, run_gateway_=lambda: GW_BEFORE)
    assert out["ok"] is False and out["gateway"] == GW_BEFORE
    assert "has no HERMES_CUA_DRIVER_CMD" in out["why"][0]


def test_gateway_with_the_drop_in_is_green(monkeypatch):
    monkeypatch.setenv("KREW_CUA_DOCTOR_GATEWAY", "1")
    out = cd.check(run_doctor_=lambda: DOCTOR_OK, run_tax=lambda: TAX_OK, run_gateway_=lambda: GW_AFTER)
    assert out["ok"] is True and out["why"] == []


def test_gateway_probe_that_raises_is_a_reason_not_a_crash(monkeypatch):
    monkeypatch.setenv("KREW_CUA_DOCTOR_GATEWAY", "1")
    def boom():
        raise PermissionError("environ")
    out = cd.check(run_doctor_=lambda: DOCTOR_OK, run_tax=lambda: TAX_OK, run_gateway_=boom)
    assert out["ok"] is False and "PermissionError" in out["why"][0]


def test_run_gateway_reads_the_process_env_and_counts_sessions():
    env = {"HERMES_CUA_DRIVER_CMD": "~/.local/bin/cua-driver", "TMPDIR": "/t"}
    seen = {}
    gw = cd.run_gateway(unit="hermes-gateway.service", main_pid=lambda u: 7,
                        proc_env=lambda pid: env, which=lambda c: seen.setdefault("c", c),
                        sockets=lambda d: seen.setdefault("d", d) and ["/t/hc-a.sock", "/t/hc-b.sock"])
    assert gw["pid"] == 7 and gw["resolves"] and gw["sessions"] == 2 and seen["d"] == "/t"
    assert not seen["c"].startswith("~")


def test_run_gateway_not_running_says_so():
    gw = cd.run_gateway(unit="hermes-gateway.service", main_pid=lambda u: 0)
    assert "not running" in gw["error"] and cd._gateway_reasons(gw)


def test_run_gateway_missing_variable():
    gw = cd.run_gateway(unit="u", main_pid=lambda u: 9, proc_env=lambda p: {},
                        which=lambda c: None, sockets=lambda d: [])
    assert gw["cmd"] is None and gw["resolves"] is False
