"""krew-mode under ONE host gateway (KREW_GATEWAY_TOPOLOGY=host), at its one door to systemd.

The file keeps the per-profile unit names (the hold file's vocabulary); `_systemctl_user`
translates them. Under the default `units` the door is untouched. Own mocks only: nothing here
may reach a live unit or a live `hermes`.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox  # noqa: E402
hm = krew_mode_sandbox.load("krew_mode_topology_under_test", "crew/scripts/krew-mode")   # never the passwd tree
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import hermes_topology as real_topo  # noqa: E402


class FakeTopo:
    HOST_UNIT = "hermes-gateway.service"
    profile_of_unit = staticmethod(real_topo.profile_of_unit)

    def __init__(self):
        self.calls, self.up = [], {"zoey": True, "ada": True, "lila": False}

    def profile_up(self, p):
        return self.up.get(p, False)

    def park(self, p):
        self.calls.append(("park", p)); return True

    def unpark(self, p):
        self.calls.append(("unpark", p)); return True

    def restart_profile(self, p):
        self.calls.append(("restart_profile", p)); return True

    def restart_host(self, block=False):
        self.calls.append(("restart_host", block)); return True


@pytest.fixture
def door(monkeypatch):
    ran = []

    def run(argv, **k):
        ran.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0, "Sun 2026-09-27 04:06:09 EDT\n", "")

    monkeypatch.setattr(hm.subprocess, "run", run)
    topo = FakeTopo()
    monkeypatch.setattr(hm, "_gateway_topology", lambda: topo)
    return topo, ran


def test_under_units_the_door_is_the_old_door(monkeypatch):
    ran = []
    monkeypatch.setattr(hm, "_gateway_topology", lambda: None)
    monkeypatch.setattr(hm.subprocess, "run",
                        lambda argv, **k: ran.append(list(argv)) or subprocess.CompletedProcess(argv, 0, "active\n", ""))
    hm._systemctl_user(["restart", "hermes-zoey-gateway.service"])
    hm._systemctl_user(["stop", "hermes-ada-gateway.service"], timeout=9)
    assert hm._unit_active("hermes-zoey-gateway.service")
    assert ran == [["systemctl", "--user", "restart", "hermes-zoey-gateway.service"],
                   ["systemctl", "--user", "stop", "hermes-ada-gateway.service"],
                   ["systemctl", "--user", "is-active", "hermes-zoey-gateway.service"]]


def test_a_profile_is_parked_and_unparked_and_no_unit_is_touched(door):
    topo, ran = door
    assert hm._systemctl_user(["stop", "hermes-zoey-gateway.service"], timeout=5).returncode == 0
    assert hm._systemctl_user(["reset-failed", "hermes-zoey-gateway.service"]).returncode == 0
    assert hm._systemctl_user(["start", "hermes-lila-gateway.service"]).returncode == 0
    assert topo.calls == [("park", "zoey"), ("unpark", "lila")] and ran == []


def test_the_helpers_this_file_calls_everywhere_go_through_the_door(door):
    topo, ran = door
    hm.stop_unit("hermes-zoey-gateway")
    hm.start_unit("hermes-lila-gateway")
    assert topo.calls == [("park", "zoey"), ("unpark", "lila")] and ran == []


def test_her_brain_flip_restarts_her_alone_and_the_caps_bounce_restarts_the_host(door):
    topo, ran = door
    hm._systemctl_user(["restart", "hermes-zoey-gateway.service"])
    hm._systemctl_user(["restart", "hermes-ada-gateway.service"])
    hm._systemctl_user(["restart", "--no-block", "hermes-ada-gateway.service"])
    assert topo.calls == [("restart_profile", "zoey"), ("restart_host", True), ("restart_host", False)]
    assert ran == []


def test_up_means_served_and_activating_cannot_happen(door):
    topo, ran = door
    assert hm._unit_active("hermes-zoey-gateway.service") is True
    assert hm._unit_active("hermes-lila-gateway.service") is False
    topo.up["zoey"] = False                                  # parked, the host perfectly healthy
    assert hm._unit_active("hermes-zoey-gateway.service") is False
    assert ran == []


def test_render_caps_restart_names_the_host_unit_itself(door, monkeypatch, tmp_path):
    """Enter and the stale-caps way back restart hermes-gateway.service directly.

    The masked hermes-ada-gateway name stays hold-file vocabulary. A restart of
    that name would be translated to restart_host; the caps bounce does not go
    that way, so a reader cannot miss which process reloads the caps.
    """
    topo, ran = door
    monkeypatch.setattr(hm, "lane_brain", lambda mode=None: "crew")
    monkeypatch.setattr(hm, "_hold_list", lambda: [])
    hm.maybe_restart_ada_gateway("render")
    hm.maybe_restart_ada_gateway("normal")
    monkeypatch.setattr(hm, "ada_caps_stale", lambda: (True, "test"))
    monkeypatch.setattr(hm, "_ada_gateway_start", lambda: ("Sun 2026-09-27 04:06:09 EDT", 1.0))
    monkeypatch.setattr(hm, "ADA_CAPS_BOUNCE_ON", True)
    monkeypatch.setattr(hm, "ADA_CAPS_BOUNCED", tmp_path / "bounced")
    monkeypatch.setattr(hm, "ADA_CAPS_PENDING", tmp_path / "pending")
    monkeypatch.setattr(hm, "_hold_log", lambda rec: None)
    assert hm.maybe_bounce_ada_for_caps("normal", by="leave") is True
    assert topo.calls == []
    assert ran == [
        ["systemctl", "--user", "restart", "hermes-gateway.service"],
        ["systemctl", "--user", "restart", "hermes-gateway.service"],
    ]


def test_the_caps_clock_is_read_from_the_host_unit(door):
    topo, ran = door
    text, epoch = hm._ada_gateway_start()
    assert ran == [["systemctl", "--user", "show", "hermes-gateway.service", "-p", "ActiveEnterTimestamp", "--value"]]
    assert text.startswith("Sun 2026-09-27") and epoch > 0


def test_everything_that_is_not_a_profile_gateway_is_left_alone(door):
    topo, ran = door
    for unit in ("ops.service", "crew.service", "hermes-dashboard.service", "hermes-gateway.service"):
        hm._systemctl_user(["restart", unit])
    assert topo.calls == [] and [r[-1] for r in ran] == ["ops.service", "crew.service",
                                                        "hermes-dashboard.service", "hermes-gateway.service"]


def test_a_failed_park_is_a_failed_stop(door, capsys):
    topo, _ = door
    topo.park = lambda p: False
    assert hm._systemctl_user(["stop", "hermes-zoey-gateway.service"]).returncode == 1
    topo.unpark = lambda p: False
    with pytest.raises(RuntimeError):
        hm.start_unit("hermes-zoey-gateway")


def test_the_dispatcher_config_follows_the_topology(monkeypatch, tmp_path):
    monkeypatch.setattr(hm, "HOUSE", tmp_path)
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")
    assert hm._dispatcher_config() == tmp_path / "profiles" / "ada" / "config.yaml"
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    assert hm._dispatcher_config() == tmp_path / "config.yaml"
    assert real_topo.dispatcher_config().name == "config.yaml" and real_topo.dispatcher_config().parent.name == ".hermes"


def test_a_mode_switch_leaves_the_host_gateway_before_it_may_restart_it(door, monkeypatch):
    """Entering Render restarts the dispatcher, which IS the host under one gateway: a switch run
    from a crew turn would be killed with its parent half way through."""
    topo, _ = door
    monkeypatch.delenv("KREW_MODE_DETACH", raising=False)
    monkeypatch.delenv("KREW_MODE_DETACHED", raising=False)
    ran = []
    assert hm.leave_the_host_gateway(["render", "turbo"], leaf="hermes-gateway.service",
                                     execvp=lambda f, a: ran.append((f, a))) is True
    (f, a), = ran
    assert f == "systemd-run" and a[:4] == ["systemd-run", "--user", "--scope", "--collect"]
    assert a[-2:] == ["render", "turbo"] and a[-3].endswith("crew/scripts/krew-mode")
    # the re-executed process never detaches again
    assert hm.leave_the_host_gateway(["render"], leaf="hermes-gateway.service",
                                     execvp=lambda f, a: ran.append((f, a))) is False and len(ran) == 1


@pytest.mark.parametrize("leaf", ["krew-hold-sweep.service", "hermes-worker-kanban-t_1-run-2.scope",
                                   "vte-spawn-1.scope", "hermes-ada-gateway.service", ""])
def test_anywhere_else_it_stays_where_it_is(door, monkeypatch, leaf):
    monkeypatch.delenv("KREW_MODE_DETACHED", raising=False)
    assert hm.leave_the_host_gateway(["render"], leaf=leaf, execvp=lambda f, a: pytest.fail("detached")) is False


def test_under_units_and_with_the_kill_switch_nothing_detaches(monkeypatch):
    monkeypatch.delenv("KREW_MODE_DETACHED", raising=False)
    monkeypatch.setattr(hm, "_gateway_topology", lambda: None)
    assert hm.leave_the_host_gateway(["render"], leaf="hermes-gateway.service",
                                     execvp=lambda f, a: pytest.fail("detached")) is False
    monkeypatch.setattr(hm, "_gateway_topology", lambda: FakeTopo())
    monkeypatch.setenv("KREW_MODE_DETACH", "0")
    assert hm.leave_the_host_gateway(["render"], leaf="hermes-gateway.service",
                                     execvp=lambda f, a: pytest.fail("detached")) is False
