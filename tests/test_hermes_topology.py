"""lib/hermes_topology + lib/hermes_endpoint: one place that knows where a gateway lives.

A missing ``krew-topology.env`` is the shared host gateway. `units` is what the call
sites did before the 2026-09-27 cutover, and it applies only when the env or the file
names it. `host` is the one-gateway topology.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))


# hermes's own default API port and the retired per-profile unit's (the scrub keeps the literal addresses
# out of krew's files)
HOST_PORT = 8642
CRISSA_UNIT_PORT = 8652


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode = out, rc


@pytest.fixture
def mods(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    (root / "profiles" / "crissa").mkdir(parents=True)
    (root / "profiles" / "ada").mkdir(parents=True)
    monkeypatch.setenv("KREW_HERMES_ROOT", str(root))
    monkeypatch.setenv("KREW_HERMES_BIN", "/opt/hermes")
    monkeypatch.delenv("KREW_GATEWAY_TOPOLOGY", raising=False)
    monkeypatch.delenv("KREW_HERMES_HOST_PORT", raising=False)
    # krew: crew/lib is on sys.path for this file only (the tests package places window/ and ms/ libs)
    hermes_topology = importlib.import_module("hermes_topology")
    topo = importlib.reload(hermes_topology)
    hermes_endpoint = importlib.import_module("hermes_endpoint")
    end = importlib.reload(hermes_endpoint)
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "systemctl" and argv[2] == "is-active":
            return topo._answers.get(tuple(argv[:4]), R("inactive\n", 3))
        return R("", 0)

    topo._answers = {}
    monkeypatch.setattr(topo.subprocess, "run", run)
    yield topo, end, root, calls
    # Put the modules back as the house has them (09-30): reloaded under KREW_HERMES_ROOT=tmp
    # they kept TOPOLOGY_FILE on a dead tmp dir, so every later test in the run read the
    # topology as `units` (test_svc_summary expected :8652 against the host's :8642).
    monkeypatch.undo()
    importlib.reload(hermes_topology)
    importlib.reload(hermes_endpoint)


def _active(topo, unit):
    topo._answers[("systemctl", "--user", "is-active", unit)] = R("active\n")


def _host_record(root, served, pid=None, state="running"):
    (root / "gateway_state.json").write_text(json.dumps(
        {"gateway_state": state, "pid": os.getpid() if pid is None else pid, "served_profiles": served}))


def test_a_missing_topology_file_is_the_host_and_an_unknown_word_is_units(mods, monkeypatch):
    """The written file (krew-install init writes `host`): the dispatcher is hermes-gateway and its config is the
    host file, never profiles/ada/config.yaml. A typo stays on the per-profile units.

    krew: NO file is `units` here (the reference install read it as host after its cutover); init writes host."""
    topo, _, root, _ = mods
    assert not (root / "krew-topology.env").exists()
    assert topo.topology() == "units"
    (root / "krew-topology.env").write_text("KREW_GATEWAY_TOPOLOGY=host\n")
    assert topo.topology() == "host" and topo.is_host()
    assert topo.dispatcher_unit() == "hermes-gateway.service"
    assert topo.dispatcher_config() == root / "config.yaml"
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "cluster")
    assert topo.topology() == "units" and not topo.is_host()
    assert topo.dispatcher_config() == root / "profiles" / "ada" / "config.yaml"
    monkeypatch.delenv("KREW_GATEWAY_TOPOLOGY")
    (root / "krew-topology.env").write_text("# cutover\nKREW_GATEWAY_TOPOLOGY=host\n")
    assert topo.topology() == "host"
    (root / "krew-topology.env").write_text("# blank\n")
    assert topo.topology() == "units"                    # krew: a file that never sets the key is units too
    (root / "krew-topology.env").write_text("KREW_GATEWAY_TOPOLOGY=hosts\n")
    assert topo.topology() == "units" and topo.dispatcher_unit() == "hermes-ada-gateway.service"
    (root / "krew-topology.env").write_text("KREW_GATEWAY_TOPOLOGY=units\n")
    assert topo.topology() == "units"
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")          # the process env wins
    assert topo.topology() == "host"


def test_units_answers_what_the_call_sites_always_did(mods, monkeypatch):
    topo, end, _, calls = mods
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")
    assert topo.unit_for("crissa") == "hermes-crissa-gateway.service"
    assert topo.dispatcher_unit() == "hermes-ada-gateway.service"
    assert topo.gateway_units() == [f"hermes-{p}-gateway.service" for p in ("ada", "crissa", "lila", "steward")]
    assert not topo.profile_up("crissa")
    _active(topo, "hermes-crissa-gateway.service")
    assert topo.profile_up("crissa")
    assert topo.restart_profile("crissa") and calls[-1] == ["systemctl", "--user", "restart", "hermes-crissa-gateway.service"]
    assert topo.park("crissa") and calls[-1][2:] == ["stop", "hermes-crissa-gateway.service"]
    assert topo.unpark("crissa") and calls[-1][2:] == ["start", "hermes-crissa-gateway.service"]
    assert topo.restart_host() and calls[-1] == ["systemctl", "--user", "restart", "--no-block", "hermes-ada-gateway.service"]
    assert end.url("crissa") == "http://127.0.0.1:%d/v1/chat/completions" % CRISSA_UNIT_PORT
    assert end.health_url("ada") == "http://127.0.0.1:8653/health"
    assert [end.port(p) for p in ("steward", "crissa", "ada", "lila")] == [8651, 8652, 8653, 8654]
    with pytest.raises(KeyError):
        end.url("bash")                                            # never had a gateway of its own


@pytest.mark.parametrize("state", ["activating", "deactivating", "failed", "inactive", "reloading"])
def test_only_active_is_up(mods, state, monkeypatch):
    """09-26: a crash-looping unit is `activating`; counting it as up suppressed gate_crissa_down."""
    topo, _, _, _ = mods
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")
    topo._answers[("systemctl", "--user", "is-active", "hermes-crissa-gateway.service")] = R(state + "\n", 3)
    assert not topo.profile_up("crissa")


def test_systemd_that_cannot_be_asked_returns_the_callers_own_default(mods, monkeypatch):
    topo, _, _, _ = mods
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")

    def boom(*a, **k):
        raise OSError("no bus")

    monkeypatch.setattr(topo.subprocess, "run", boom)
    assert topo.profile_up("crissa") is False and topo.profile_up("crissa", unknown=True) is True


def test_host_is_up_only_when_the_live_host_serves_the_profile(mods, monkeypatch):
    topo, _, root, _ = mods
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    assert topo.unit_for("crissa") == topo.dispatcher_unit() == "hermes-gateway.service"
    assert topo.gateway_units() == ["hermes-gateway.service"]
    _host_record(root, ["default", "ada", "crissa"])
    assert not topo.profile_up("crissa")                             # the unit is not active
    _active(topo, "hermes-gateway.service")
    assert topo.profile_up("crissa") and topo.profile_up("ada") and not topo.profile_up("lila")
    (root / "profiles" / "crissa" / "gateway.parked").write_text("")
    assert not topo.profile_up("crissa") and topo.profile_up("ada") and topo.parked("crissa")
    (root / "profiles" / "crissa" / "gateway.parked").unlink()
    _host_record(root, ["default", "ada", "crissa"], pid=2 ** 22 + 12345)   # the record outlived its process
    assert not topo.profile_up("crissa") and topo.host_record() == {}
    _host_record(root, ["default", "crissa"], state="stopped")
    assert not topo.profile_up("crissa")
    (root / "gateway_state.json").write_text("not json")
    assert not topo.profile_up("crissa")


def test_host_acts_through_hermes_per_profile_and_restarts_one_unit(mods, monkeypatch):
    topo, _, _, calls = mods
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    assert topo.restart_profile("crissa") and calls[-1] == ["/opt/hermes", "-p", "crissa", "gateway", "restart"]
    assert topo.park("crissa") and calls[-1][-1] == "stop"
    assert topo.unpark("crissa") and calls[-1][-1] == "start"
    assert topo.restart_host(block=True) and calls[-1] == ["systemctl", "--user", "restart", "hermes-gateway.service"]


def test_old_unit_names_stay_readable_after_the_cutover(mods):
    """A hold written before the cutover names a unit; an unknown name is a permanent latch."""
    topo, _, _, _ = mods
    assert topo.profile_of_unit("hermes-crissa-gateway.service") == "crissa"
    assert topo.profile_of_unit("hermes-ada-gateway") == "ada"
    assert topo.profile_of_unit("hermes-gateway.service") is None
    assert topo.profile_of_unit("ops.service") is None and topo.profile_of_unit("hermes-dashboard.service") is None


def test_host_endpoints_carry_the_profile_prefix_and_the_profiles_own_key(mods, monkeypatch):
    topo, end, root, _ = mods
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    assert end.host_port() == HOST_PORT
    assert end.url("crissa") == "http://127.0.0.1:%d/p/crissa/v1/chat/completions" % HOST_PORT
    assert end.url("bash", "v1/models") == "http://127.0.0.1:%d/p/bash/v1/models" % HOST_PORT
    assert end.url("default") == "http://127.0.0.1:%d/v1/chat/completions" % HOST_PORT
    assert end.health_url("crissa") == "http://127.0.0.1:%d/health" % HOST_PORT
    (root / ".env").write_text("API_SERVER_ENABLED=true\nAPI_SERVER_PORT=8660\nAPI_SERVER_KEY=host-key\n")
    assert end.host_port() == 8660 and end.port("lila") == 8660
    monkeypatch.setenv("KREW_HERMES_HOST_PORT", "8700")
    assert end.url("ada", "/health") == "http://127.0.0.1:8700/p/ada/health"
    (root / "profiles" / "crissa" / ".env").write_text("X=1\nexport API_SERVER_KEY='crissa-key'\n")
    assert end.key("crissa") == "crissa-key" and end.key("default") == "host-key" and end.key("ada") == ""


def test_describe_is_one_read(mods, monkeypatch):
    topo, _, root, _ = mods
    (root / "krew-topology.env").write_text("KREW_GATEWAY_TOPOLOGY=host\n")   # krew: what init writes
    d = topo.describe()
    assert d["topology"] == "host" and d["dispatcher_unit"] == "hermes-gateway.service"
    assert d["dispatcher_config"] == str(root / "config.yaml")
    assert set(d["up"]) == {"ada", "crissa", "lila", "steward"} and d["served"] == []
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")
    d = topo.describe()
    assert d["topology"] == "units" and "served" not in d
    assert d["dispatcher_config"].endswith("profiles/ada/config.yaml")
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    _host_record(root, ["default", "ada"])
    _active(topo, "hermes-gateway.service")
    d = topo.describe()
    assert d["served"] == ["default", "ada"] and d["up"]["ada"] and not d["up"]["crissa"]


def test_the_shells_door(mods, monkeypatch, capsys):
    topo, _, root, calls = mods
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "units")
    assert topo.main(["unit", "crissa"]) == 0 and capsys.readouterr().out.strip() == "hermes-crissa-gateway.service"
    assert topo.main(["up", "crissa"]) == 1
    _active(topo, "hermes-crissa-gateway.service")
    assert topo.main(["up", "crissa"]) == 0
    assert topo.main(["restart", "ada"]) == 0 and calls[-1][-1] == "hermes-ada-gateway.service"
    assert topo.main(["restart"]) == 2 and topo.main(["explode", "ada"]) == 2
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    assert topo.main(["restart", "crissa"]) == 0 and calls[-1] == ["/opt/hermes", "-p", "crissa", "gateway", "restart"]
    assert topo.main(["restart-host"]) == 0 and calls[-1][-1] == "hermes-gateway.service"
    assert topo.main(["describe"]) == 0 and '"topology": "host"' in capsys.readouterr().out


def test_the_shell_scripts_ask_the_topology_and_name_no_gateway_unit():
    for name in ("krew-kit-reapply.sh", "sync-packs.sh"):
        text = (ROOT / "crew" / "scripts" / name).read_text()
        live = [l for l in text.splitlines() if "systemctl" in l and "gateway" in l and not l.lstrip().startswith("#")
                and "by hand" not in l]
        assert not live, (name, live)
        assert "lib/hermes_topology.py" in text


def test_last_after_mods_the_modules_point_at_the_krew_again():
    """Kept LAST in the file: runs after every `mods` test has torn down."""
    import hermes_topology
    assert str(Path(hermes_topology.TOPOLOGY_FILE)).startswith(str(Path.home()))
