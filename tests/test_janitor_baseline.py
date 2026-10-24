"""krew-baseline: what did not come back (the owner 09-30). Synthetic snapshots only - no GPU, no pactl, no units."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))
import krew_baseline as hb  # noqa: E402


def snap(**over):
    s = {"schema": hb.SCHEMA, "label": "swap", "ts": "2026-09-30T05:30:00", "unknown": {}, "probes": {
        "vram": {"used_mib": 89761, "total_mib": 97887, "by_unit": {"ops.service": 38740, "crew.service": 38392}},
        "memory": {"mem_available_mib": 49792, "swap_used_mib": 0, "swap_cached_mib": 0, "swap_by_unit": {}},
        "audio": {"clients": 13, "clients_by_name": {"pacat": 2, "pactl": 2}, "sink_inputs": 1, "pactl_subscribe": 1},
        "holds": {"hold_lines": [], "hold_file": False, "gpu_mode": "normal", "glass_mode": "wallpaper", "board": "daily-2026-09-16",
                  "off_switches": ["crew-kit-reapply.off"]},
        "units": {"enabled": ["crew.service", "ops.service"], "active": ["crew.service", "ops.service"],
                  "failed": [], "scopes": 12},
        "ports": {"listening": ["8000", "8005", "8100"]}}}
    for path, value in over.items():
        probe, key = path.split("__")
        s["probes"][probe][key] = value
    return s


def items(result):
    return {o["item"] for o in result["not_returned"]}


def test_the_same_machine_is_clean():
    r = hb.diff(snap(), snap())
    assert r["clean"] and r["not_returned"] == [] and r["unknown"] == []
    assert "everything measured came back" in hb.render(r)


def test_the_0930_swap_is_named_with_who_was_pushed_out():
    after = snap(memory__swap_used_mib=25061,
                 memory__swap_by_unit={"voice.service": 6602, "crew.service": 3820, "gnome.service": 90})
    r = hb.diff(snap(), after)
    assert {"swap", "swap:voice.service", "swap:crew.service"} <= items(r)
    assert "swap:gnome.service" not in items(r)  # under the per-unit tolerance
    assert "voice.service has 6602 MiB more of its memory in swap" in hb.render(r)


def test_the_clip_reel_left_enabled_is_named():
    after = snap(units__enabled=["clip-reel.service", "crew.service", "ops.service"])
    assert "enabled:clip-reel.service" in items(hb.diff(snap(), after))


@pytest.mark.parametrize("change, item", [
    ({"holds__hold_lines": ["# unit ops.service card=lane-canary", "ops.service"]}, "hold:ops.service"),
    ({"holds__hold_file": True}, "hold_file"),
    ({"holds__gpu_mode": "render"}, "gpu_mode"),
    ({"holds__board": "probe-board"}, "board"),
    ({"holds__off_switches": ["crew-kit-reapply.off", "restart-window.off"]}, "off_switch:restart-window.off"),
    ({"audio__pactl_subscribe": 3}, "pactl_subscribe"),
    ({"audio__clients": 44}, "audio_clients"),
    ({"vram__used_mib": 92000}, "vram"),
    ({"units__active": ["crew.service"]}, "active:-ops.service"),
    ({"units__failed": ["rail-stt.service"]}, "failed:rail-stt.service"),
    ({"ports__listening": ["8000", "8001", "8005", "8100"]}, "port:8001"),
    ({"memory__mem_available_mib": 40000}, "mem_headroom"),
])
def test_each_kind_of_residue_is_found(change, item):
    assert item in items(hb.diff(snap(), snap(**change)))


@pytest.mark.parametrize("change", [
    {"vram__used_mib": 89761 + 300}, {"audio__clients": 15}, {"memory__swap_used_mib": 200},
    {"memory__mem_available_mib": 49792 - 3000}, {"vram__used_mib": 60000}, {"audio__clients": 5},
    {"ports__listening": ["8000", "8005", "8100", "46193"]},   # a kernel-picked port in an older snapshot
])
def test_breathing_and_improvements_are_not_findings(change):
    assert hb.diff(snap(), snap(**change))["not_returned"] == []


def test_a_probe_that_could_not_be_read_is_unknown_never_clean():
    after = snap()
    del after["probes"]["audio"]
    after["unknown"]["audio"] = "TimeoutExpired: pactl"
    r = hb.diff(snap(), after)
    assert r["unknown"] == ["audio"] and not r["clean"] and r["not_returned"] == []
    assert "UNKNOWN" in hb.render(r)


def test_one_dead_probe_does_not_blind_the_rest():
    def boom():
        raise RuntimeError("no GPU here")
    s = hb.snapshot("x", probes={"vram": boom, "holds": lambda: {"hold_lines": []}})
    assert "vram" in s["unknown"] and s["probes"]["holds"] == {"hold_lines": []}


def test_kernel_picked_ports_are_not_residue(monkeypatch):
    out = "LISTEN 0 128 127.0.0.1:8000 0.0.0.0:*\nLISTEN 0 64 0.0.0.0:46193 0.0.0.0:*\nLISTEN 0 5 [::]:8100 [::]:*\n"
    run = lambda cmd, **kw: __import__("subprocess").CompletedProcess(cmd, 0, stdout=out, stderr="")  # noqa: E731
    assert hb.probe_ports(run=run) == {"listening": ["8000", "8100"]}


# ---------------------------------------------------------------- findings, the ms_silent_skips way
def test_findings_open_once_update_and_close(tmp_path):
    f = tmp_path / "findings.json"
    f.write_text(json.dumps({"schema": "ms_findings/v1", "findings": [{"key": "other", "status": "open"}]}))
    dirty = hb.diff(snap(), snap(units__enabled=["clip-reel.service", "crew.service", "ops.service"],
                                 memory__swap_used_mib=25061))
    # three things stayed: the swap, the headroom it cost, the unit left enabled
    assert [c["action"] for c in hb.record("lane-swap", dirty, findings_path=f)] == ["opened"] * 3
    assert [c["action"] for c in hb.record("lane-swap", dirty, findings_path=f)] == ["updated"] * 3
    rows = json.loads(f.read_text())["findings"]
    mine = [r for r in rows if r["key"].startswith("not-returned:lane-swap:")]
    assert len(rows) == 4 and len(mine) == 3 and all(r["times"] == 2 and r["source"] == "krew_baseline" for r in mine)
    # the swap came back, the unit is still enabled: one closes, one stays
    half = hb.diff(snap(), snap(units__enabled=["clip-reel.service", "crew.service", "ops.service"]))
    assert sorted(c["action"] for c in hb.record("lane-swap", half, findings_path=f)) == ["closed", "closed", "updated"]
    rows = {r["key"]: r for r in json.loads(f.read_text())["findings"]}
    assert rows["not-returned:lane-swap:swap"]["status"] == "closed"
    assert rows["not-returned:lane-swap:enabled:clip-reel.service"]["status"] == "open"
    assert rows["other"] == {"key": "other", "status": "open"}  # nobody else's row is touched


def test_an_unknown_probe_closes_nothing(tmp_path):
    f = tmp_path / "findings.json"
    hb.record("render", hb.diff(snap(), snap(audio__pactl_subscribe=4)), findings_path=f)
    blind = snap()
    del blind["probes"]["audio"]
    assert hb.record("render", hb.diff(snap(), blind), findings_path=f) == []
    assert json.loads(f.read_text())["findings"][0]["status"] == "open"


def test_another_labels_rows_are_not_closed_by_this_one(tmp_path):
    f = tmp_path / "findings.json"
    hb.record("render", hb.diff(snap(), snap(holds__gpu_mode="render")), findings_path=f)
    hb.record("canary", hb.diff(snap(), snap()), findings_path=f)
    assert json.loads(f.read_text())["findings"][0]["status"] == "open"


# ---------------------------------------------------------------- the hooks are OFF until enabled
@pytest.fixture(autouse=True)
def _no_live_arm_file(tmp_path, monkeypatch):
    """No test reads the machine's own arm file: the hooks here are armed by the test or not at all."""
    monkeypatch.setattr(hb, "ARM_FILE", tmp_path / "no-arm-file.json")


def test_the_hooks_do_nothing_by_default(tmp_path, monkeypatch):
    for k in ("KREW_BASELINE", "KREW_BASELINE_HOOKS", "KREW_BASELINE_FINDINGS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(hb, "snapshot", lambda *a, **k: pytest.fail("a probe ran with the hooks off"))
    assert hb.start("render", state=tmp_path) is None and hb.end("render", state=tmp_path) is None
    assert [p for p in tmp_path.iterdir() if p.name != 'krew-home'] == []   # conftest's KREW_HOME


def test_start_and_end_report_without_touching_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_BASELINE_HOOKS", "1")
    monkeypatch.delenv("KREW_BASELINE_FINDINGS", raising=False)
    monkeypatch.setenv("KREW_BASELINE_FINDINGS_PATH", str(tmp_path / "findings.json"))
    seq = iter([snap(), snap(holds__gpu_mode="render")])
    monkeypatch.setattr(hb, "snapshot", lambda label="", probes=None: copy.deepcopy(next(seq)))
    assert hb.start("render", state=tmp_path).name == "render.before.json"
    r = hb.end("render", state=tmp_path)
    assert items(r) == {"gpu_mode"} and "findings" not in r
    assert json.loads((tmp_path / "render.report.json").read_text())["not_returned"][0]["item"] == "gpu_mode"
    assert not (tmp_path / "findings.json").exists()


def test_findings_are_written_only_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_BASELINE_HOOKS", "1")
    monkeypatch.setenv("KREW_BASELINE_FINDINGS", "1")
    monkeypatch.setenv("KREW_BASELINE_FINDINGS_PATH", str(tmp_path / "findings.json"))
    seq = iter([snap(), snap(holds__gpu_mode="render")])
    monkeypatch.setattr(hb, "snapshot", lambda label="", probes=None: copy.deepcopy(next(seq)))
    hb.start("render", state=tmp_path)
    assert hb.end("render", state=tmp_path)["findings"] == [{"key": "not-returned:render:gpu_mode", "action": "opened"}]
    assert json.loads((tmp_path / "findings.json").read_text())["findings"][0]["status"] == "open"


def test_an_instrument_never_stops_what_it_watches(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_BASELINE_HOOKS", "1")
    monkeypatch.setattr(hb, "snapshot", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert hb.start("render", state=tmp_path) is None and hb.end("render", state=tmp_path) is None


def test_the_kill_switch_beats_the_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_BASELINE", "0")
    monkeypatch.setenv("KREW_BASELINE_HOOKS", "1")
    monkeypatch.setenv("KREW_BASELINE_FINDINGS", "1")
    assert not hb.hooks_on() and not hb.findings_on() and hb.start("x", state=tmp_path) is None


# ---------------------------------------------------------------- the call sites
def test_end_later_spawns_nothing_with_the_hooks_off(monkeypatch):
    monkeypatch.delenv("KREW_BASELINE_HOOKS", raising=False)
    monkeypatch.setattr(hb.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned with the hooks off"))
    assert hb.end_later("render", 1.0) is False


def test_end_later_is_a_detached_child_running_the_cli(monkeypatch):
    monkeypatch.setenv("KREW_BASELINE_HOOKS", "1")
    seen = {}
    monkeypatch.setattr(hb.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, kw=kw))
    assert hb.end_later("render", 420.0) is True
    assert seen["cmd"][1].endswith("scripts/krew-baseline.py") and seen["cmd"][2:] == ["end", "render", "--settle", "420.0"]
    assert seen["kw"]["start_new_session"] is True






def test_clearing_swap_is_not_a_loss_of_memory():
    """09-30 08:30: swapoff brought 25 GB back into RAM; available fell 20 GB and nothing was lost."""
    before = snap(memory__mem_available_mib=64799, memory__swap_used_mib=25029)
    after = snap(memory__mem_available_mib=44864, memory__swap_used_mib=73)
    assert hb.diff(before, after)["clean"]
    # the reverse - the same RAM free but 25 GB newly parked in swap - is both a swap and a headroom finding
    assert {"swap", "mem_headroom"} <= items(hb.diff(snap(), snap(memory__swap_used_mib=25029)))


# ---------------------------------------------------------------- the lane caps (drafts)


def test_an_empty_hold_file_left_behind_is_named():
    """09-30: the lane swaps' sed removed the hold LINES and left the file, which did not exist at boot."""
    r = hb.diff(snap(), snap(holds__hold_file=True))
    assert items(r) == {"hold_file"} and "it is empty: a leftover" in hb.render(r)
    assert hb.diff(snap(holds__hold_file=True), snap(holds__hold_file=True))["clean"]
    # a snapshot from before the probe existed is not evidence either way
    old = snap()
    del old["probes"]["holds"]["hold_file"]
    assert hb.diff(old, snap(holds__hold_file=True))["clean"]


def test_one_file_arms_every_call_site_and_env_overrides_it(tmp_path, monkeypatch):
    for k in ("KREW_BASELINE", "KREW_BASELINE_HOOKS", "KREW_BASELINE_FINDINGS"):
        monkeypatch.delenv(k, raising=False)
    arm = tmp_path / "krew-baseline.json"
    monkeypatch.setattr(hb, "ARM_FILE", arm)
    assert not hb.hooks_on() and not hb.findings_on()            # no file
    arm.write_text("{not json")
    assert not hb.hooks_on()                                     # unreadable = off, never on
    arm.write_text(json.dumps({"hooks": True, "findings": False}))
    assert hb.hooks_on() and not hb.findings_on()
    arm.write_text(json.dumps({"hooks": True, "findings": True}))
    assert hb.hooks_on() and hb.findings_on()
    monkeypatch.setenv("KREW_BASELINE_HOOKS", "0")              # one process opts out
    assert not hb.hooks_on() and hb.findings_on()
    monkeypatch.setenv("KREW_BASELINE", "0")                    # the kill beats everything
    assert not hb.findings_on()


# ---------------------------------------------------------------- expected during a hold or a lane swap
def _during(s, why):
    s["context"] = {"active": True, "why": [why]}
    return s


def test_a_difference_measured_during_a_hold_or_swap_is_expected_not_a_finding(tmp_path):
    """09-30: two pytest 'VRAM' findings opened and closed because the test runs straddled the 0.30 lane swaps."""
    after = _during(snap(vram__used_mib=92000, units__active=["crew.service", "ops-lane-ab-triton-attn.service"]),
                    "a lane swap unit is running: ops-lane-ab-triton-attn.service")
    r = hb.diff(snap(), after)
    assert r["not_returned"] == [] and {o["item"] for o in r["expected"]} >= {"vram", "active:-ops.service"}
    assert not r["clean"] and r["during"] == ["after: a lane swap unit is running: ops-lane-ab-triton-attn.service"]
    text = hb.render(r)
    assert "expected, not a finding" in text and "did not come back" not in text
    f = tmp_path / "findings.json"
    assert hb.record("pytest", r, findings_path=f) == [] and not f.exists()


def test_a_reading_during_a_swap_closes_nothing(tmp_path):
    f = tmp_path / "findings.json"
    hb.record("pytest", hb.diff(snap(), snap(audio__pactl_subscribe=4)), findings_path=f)
    quiet_but_held = hb.diff(snap(), _during(snap(), "a GPU hold is active"))
    assert hb.record("pytest", quiet_but_held, findings_path=f) == []
    assert json.loads(f.read_text())["findings"][0]["status"] == "open"


def test_the_context_is_read_from_the_probes(tmp_path):
    held = snap(holds__hold_lines=["# unit ops.service card=lane-ab", "ops.service"])
    assert hb.context_of(held, state=tmp_path) == {"active": True, "why": ["a GPU hold is active"]}
    swapping = snap(units__active=["crew.service", "ms-first-start-vllm-0271-cu132-0-31-0.service"])
    assert "a lane swap unit is running" in hb.context_of(swapping, state=tmp_path)["why"][0]
    assert hb.context_of(snap(), state=tmp_path) == {"active": False, "why": []}


def test_a_swap_in_flight_excuses_others_but_never_itself(tmp_path):
    (tmp_path / "lane-swap-ab.before.json").write_text("{}")
    (tmp_path / "pytest.before.json").write_text("{}")           # not swap-like: excuses nobody
    assert hb.in_flight("pytest", tmp_path) == ["lane-swap-ab"]
    assert hb.in_flight("lane-swap-ab", tmp_path) == []            # a swap's own residue is a real finding
    (tmp_path / "lane-swap-ab.after.json").write_text("{}")
    import os, time as _t
    os.utime(tmp_path / "lane-swap-ab.after.json", (_t.time() + 5, _t.time() + 5))
    assert hb.in_flight("pytest", tmp_path) == []                  # ended
    # a start whose end never came stops excusing anything after six hours
    (tmp_path / "render.before.json").write_text("{}")
    assert hb.in_flight("pytest", tmp_path, now=_t.time() + 7 * 3600) == []


def test_an_old_snapshot_without_a_context_behaves_as_before():
    old = snap()
    assert "context" not in old
    assert items(hb.diff(old, snap(holds__gpu_mode="render"))) == {"gpu_mode"}
