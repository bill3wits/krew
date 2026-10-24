"""t_7f5da45c: the janitor watches the GPU tenants by unit state, report-only, and
treats a repeating auth failure in a unit's journal as a red row — not startup churn.

The two units that matter most after the 09-27 cutover (ops, crew) were seen ONLY
through a port probe, so an AUTH retry loop on ops read as ordinary churn in the
journal tail while the unit-state row stayed green. Own mocks only; no systemd,
no journal, no network.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


_SHARED = ("hermes_topology", "hermes_endpoint")


def _is_shared(name: str) -> bool:
    return any(name == p or name.startswith(p + ".") for p in _SHARED)


@pytest.fixture(autouse=True)
def _restore_shared_modules():
    """sys.modules after each test is exactly what it was before (10-02, the leak).

    The janitor and what it imports (hermes_topology, hermes_endpoint and the machine modules that
    read the topology at import) are loaded here under a tmp KREW_HERMES_ROOT and a `units`
    topology. Left in sys.modules they outlive the test, and a later test that imports them gets
    this test's tmp topology (measured: tests/test_svc_summary.py passed alone, failed after this
    file). So every module this test added is dropped and every one it replaced is put back."""
    before = dict(sys.modules)
    yield
    for k in [k for k in sys.modules if k not in before]:
        del sys.modules[k]
    for k, v in before.items():
        if sys.modules.get(k) is not v:
            sys.modules[k] = v


def _load(name: str):
    for m in [k for k in sys.modules if _is_shared(k)]:
        del sys.modules[m]   # load fresh against this test's env; the fixture restores them
    spec = importlib.util.spec_from_file_location(name, ROOT / "janitor" / "krew-janitor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    (root / "profiles" / "zoey").mkdir(parents=True)
    monkeypatch.setenv("KREW_HERMES_ROOT", str(root))
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")
    monkeypatch.delenv("KREW_HERMES_HOST_PORT", raising=False)
    return root


def _fake_journal(monkeypatch, mod, text: str):
    """Patch the journal read: journalctl calls return `text`, everything else 4242."""
    def run(argv, **k):
        return subprocess.CompletedProcess(
            argv, 0, text + "\n" if text else "", "")
    monkeypatch.setattr(subprocess, "run", run)


def _active_unit(monkeypatch, mod, unit: str, manager: str):
    """unit_state for `unit` reads `active`/`running` with zero restarts.

    sysctl makes separate subprocess calls (is-active, then show -p NRestarts
    -p SubState); answer each verb with its own output.
    """
    def run(argv, **k):
        argv = [str(a) for a in argv]
        if "is-active" in argv:
            out = "active\n"
        elif "show" in argv:
            out = "NRestarts=0\nSubState=running\n"
        else:
            out = ""
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(subprocess, "run", run)
    return mod.unit_state(unit, manager)


def test_ops_and_crew_are_watched_units_and_report_only(home):
    j = _load("janitor_gpu")
    units = [u for u, _ in j.UNITS]
    assert "ops.service" in units and "crew.service" in units
    assert j.is_report_only_unit("ops.service") and j.is_report_only_unit("crew.service")
    # GPU tenants never restart: the refusal reason is the report-only one, not the
    # old "restart window" or a blank restart-in-order.
    assert j.restart_refusal(
        "ops.service", "failed", False, 10_000).startswith("report-only")
    assert j.restart_refusal(
        "crew.service", "activating", False, 10_000).startswith("report-only")
    # A non-tenant unit keeps its own refusal path (this is not a blanket
    # no-restart): a healthy since_last + no hold + failed => "" (in order).
    assert j.restart_refusal("voiceonly-home.service", "failed", False, 10_000, hold=False) == ""


def test_auth_repeating_flags_three_failures_in_window(home, monkeypatch):
    j = _load("janitor_gpu_auth")
    text = (
        "2026-10-01 04:00:01 ERROR: 401 Unauthorized from model endpoint\n"
        "2026-10-01 04:05:02 ERROR: 401 Unauthorized from model endpoint\n"
        "2026-10-01 04:10:03 ERROR: 401 Unauthorized from model endpoint\n"
    )
    _fake_journal(monkeypatch, j, text)
    res = j.auth_repeating("ops.service", "user")
    assert res, "three auth failures in window must be a repeating failure"
    assert res["count"] >= 3
    assert any("401 Unauthorized" in s for s in res["signatures"])


def test_auth_repeating_stays_green_below_threshold(home, monkeypatch):
    j = _load("janitor_gpu_green")
    # One-off auth line at boot: below threshold, so a startup blip is NOT a red row.
    _fake_journal(monkeypatch, j, "ERROR: 401 Unauthorized from model endpoint")
    assert j.auth_repeating("ops.service", "user") == {}


def test_auth_repeating_is_zero_when_journal_empty(home, monkeypatch):
    j = _load("janitor_gpu_empty")
    _fake_journal(monkeypatch, j, "")
    assert j.auth_repeating("ops.service", "user") == {}


def test_an_auth_repeating_active_unit_is_a_red_row(home, monkeypatch):
    """The core of the card: the unit reads `active`, but a repeating auth failure in
    its journal must still show as red — churn is not a green unit."""
    j = _load("janitor_gpu_red")
    st = _active_unit(monkeypatch, j, "ops.service", "user")
    assert st["active"] == "active"
    text = (
        "ERROR: 401 Unauthorized from model endpoint\n"
        "ERROR: 401 Unauthorized from model endpoint\n"
        "ERROR: 401 Unauthorized from model endpoint\n"
    )
    # unit_state already consumed its own fake; now the journal read must return the
    # auth failures.
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 0, text + "\n", ""))
    auth = j.auth_repeating("ops.service", "user")
    red = st["active"] != "active" or bool(auth)
    assert red is True
    assert auth and auth["count"] >= 3
