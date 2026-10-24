"""The board gate and the janitor under ONE host gateway (KREW_GATEWAY_TOPOLOGY=host).

Under the default `units` both ask what they always asked. Own mocks only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: str):
    for m in [k for k in sys.modules if k.startswith("hermes_topology") or k.startswith("hermes_endpoint")]:
        del sys.modules[m]
    spec = importlib.util.spec_from_file_location(name, ROOT / "crew" / "scripts" / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    (root / "profiles" / "zoey").mkdir(parents=True)
    monkeypatch.setenv("KREW_HERMES_ROOT", str(root))
    monkeypatch.delenv("KREW_HERMES_HOST_PORT", raising=False)
    return root


def _fake_systemd(monkeypatch, mod, active=()):
    asked = []

    def run(argv, **k):
        asked.append([str(a) for a in argv])
        if "is-active" in argv:
            up = argv[-1] in active
            return subprocess.CompletedProcess(argv, 0 if up else 3, "active\n" if up else "inactive\n", "")
        return subprocess.CompletedProcess(argv, 0, "4242\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    return asked


def test_the_gate_under_units_asks_the_units(home, monkeypatch):
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")
    monkeypatch.setenv("KREW_DISPATCHER_UNIT", "hermes-ada-gateway.service")   # krew: the units shape names Ada's own
    g = _load("gate_units", "kanban-board-gate.py")
    asked = _fake_systemd(monkeypatch, g, active=("hermes-zoey-gateway.service",))
    assert g._unit_active("hermes-zoey-gateway.service") and not g._unit_active("hermes-ada-gateway.service")
    g._gateway_start()
    assert g._gateway_mainpids() == [4242] * 4
    assert asked[0][-1] == "hermes-zoey-gateway.service" and asked[2][3] == "hermes-ada-gateway.service"
    assert [a[3] for a in asked[3:]] == [f"hermes-{u}-gateway" for u in ("ada", "zoey", "lila", "steward")]


def test_the_gate_under_host_means_served(home, monkeypatch):
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    g = _load("gate_host", "kanban-board-gate.py")
    asked = _fake_systemd(monkeypatch, g, active=("hermes-gateway.service",))
    (home / "gateway_state.json").write_text(json.dumps(
        {"gateway_state": "running", "pid": os.getpid(), "served_profiles": ["default", "ada", "zoey"]}))
    assert g._unit_active("hermes-zoey-gateway.service") and g._unit_active("hermes-ada-gateway.service")
    (home / "profiles" / "zoey" / "gateway.parked").write_text("")
    assert not g._unit_active("hermes-zoey-gateway.service")          # gate_zoey_down may act
    assert not g._unit_active("crew.service")                          # any other unit: the old question
    g._gateway_start()
    assert asked[-1][3] == "hermes-gateway.service"
    assert g._gateway_mainpids() == [4242] and asked[-1][3] == "hermes-gateway.service"

# The janitor half of the house test lives in tests/test_janitor_topology.py (krew's janitor reads services.json).
