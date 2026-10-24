"""Heavy-render H2 (the User 09-29): krew-mode heavy-enter / heavy-comfy / heavy-exit and the
runner (scripts/heavy-render.py). "Crew always comes back, even on a crash or timeout."

Nothing here touches a unit, the board, the GPU, Comfy or ~/.hermes: krew-mode is loaded with
every live path on tmp and every action stubbed; the runner gets a fake krew-mode and a fake Comfy.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import render_orders as ro  # noqa: E402


def _load(name: str, rel: str):
    # every import-time path in a sandbox, never the passwd tree (tests/krew_mode_sandbox.py)
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import krew_mode_sandbox
    return krew_mode_sandbox.load(name, rel)


KANBAN_CFG = "model:\n  default: crew\nkanban:\n  dispatch_in_gateway: true\n  max_in_progress: 3\nother: 1\n"


@pytest.fixture
def hm(tmp_path, monkeypatch):
    m = _load("krew_mode_h2", "crew/scripts/krew-mode")
    house = tmp_path / "hermes"
    (house / "profiles" / "zoey").mkdir(parents=True)
    (house / "config.yaml").write_text(KANBAN_CFG)
    for attr, val in (("HOUSE", house), ("MODE_FILE", house / "krew-gpu-mode"),
                      ("HEAVY_STATE", house / "heavy-render.json"), ("STATE_FILE", house / "krew-gpu-state.json"),
                      ("PENDING_FILE", house / "krew-gpu-pending"), ("CREW_HOLD", tmp_path / "krew-train-hold"),
                      ("BEAST_OPS_PIN", house / "krew-beast-ops-pin"), ("FLAG_HOME_STATE", house / "flag-home.json")):
        monkeypatch.setattr(m, attr, val)
    (house / "krew-gpu-mode").write_text("normal\n")
    monkeypatch.setenv("KREW_HEAVY_RENDER", "1")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(m, "_render_pending_cards", lambda **k: set())

    def no_subprocess(*a, **k):
        raise AssertionError(f"H2 tests never run a subprocess: {a[:1]}")
    monkeypatch.setattr(m.subprocess, "run", no_subprocess)
    monkeypatch.setattr(m.subprocess, "Popen", no_subprocess)
    return m


# ------------------------------------------------------------- the dispatch pause
def test_the_pause_is_one_marked_line_under_kanban(hm):
    new, ch = hm.set_dispatch_pause(KANBAN_CFG, True)
    assert ch and new.splitlines()[3] == "  dispatch_profiles: []  " + hm.DISPATCH_PAUSE_MARK
    assert hm.set_dispatch_pause(new, True) == (new, False)  # idempotent
    back, ch2 = hm.set_dispatch_pause(new, False)
    assert ch2 and back == KANBAN_CFG  # removed exactly, nothing else touched
    assert hm.set_dispatch_pause(KANBAN_CFG, False) == (KANBAN_CFG, False)


def test_a_dispatch_profiles_line_that_is_not_ours_is_never_edited(hm):
    user = KANBAN_CFG.replace("kanban:\n", "kanban:\n  dispatch_profiles: [bash]\n")
    with pytest.raises(hm.DispatchPauseRefused):
        hm.set_dispatch_pause(user, True)


def test_no_kanban_block_gets_one(hm):
    new, ch = hm.set_dispatch_pause("model:\n  default: crew", True)
    assert ch and new.endswith("kanban:\n  dispatch_profiles: []  " + hm.DISPATCH_PAUSE_MARK + "\n")


def test_hermes_reads_our_line_as_claim_nothing(hm):
    """The line we write parses to an empty dispatch_profiles - what hermes treats as
    'claims nothing' (kanban_db_dispatch._dispatch_profile_allowlist)."""
    yaml = pytest.importorskip("yaml")
    new, _ = hm.set_dispatch_pause(KANBAN_CFG, True)
    assert yaml.safe_load(new)["kanban"]["dispatch_profiles"] == []


def test_apply_dispatch_pause_is_atomic_and_keeps_the_mode(hm):
    cfg = hm.HOUSE / "config.yaml"
    os.chmod(cfg, 0o640)
    assert hm.apply_dispatch_pause(True) == "paused"
    assert hm.DISPATCH_PAUSE_MARK in cfg.read_text() and (cfg.stat().st_mode & 0o777) == 0o640
    assert hm.apply_dispatch_pause(False) == "running" and cfg.read_text() == KANBAN_CFG
    assert not list(cfg.parent.glob(".config.yaml.*"))


def test_heavy_markers_find_every_trace(hm):
    assert hm.heavy_markers() == []
    hm.MODE_FILE.write_text("heavy-render\n")
    hm.HEAVY_STATE.write_text("{}")
    hm.apply_dispatch_pause(True)
    hm.hold_units("crew.service", card=hm.HEAVY_CARD)
    assert hm.heavy_markers() == ["flag", "crew hold", "runner state", "dispatch pause"]


# ------------------------------------------------------------- heavy-enter
@pytest.mark.parametrize("setup,why", [
    (lambda m, mp: mp.setenv("KREW_HEAVY_RENDER", "0"), "is not 1"),
    (lambda m, mp: mp.setenv("HERMES_KANBAN_TASK", "t_x"), "kanban worker"),
    (lambda m, mp: m.MODE_FILE.write_text("render\n"), "in render"),
    (lambda m, mp: m.CREW_HOLD.write_text("ops.service\n"), "GPU hold"),
    (lambda m, mp: mp.setattr(m, "_render_pending_cards", lambda **k: {"t_r"}), "render is pending"),
    (lambda m, mp: m.HEAVY_STATE.write_text("{}"), "left traces"),
])
def test_heavy_enter_refuses_and_changes_nothing(hm, monkeypatch, capsys, setup, why):
    setup(hm, monkeypatch)
    before = (hm.HOUSE / "config.yaml").read_text()
    assert hm.cmd_heavy_enter(123, time.time() + 60) == 3
    assert why in capsys.readouterr().err
    assert (hm.HOUSE / "config.yaml").read_text() == before


def test_our_own_in_flight_marker_does_not_refuse(hm, monkeypatch):
    hm.PENDING_FILE.write_text(f"heavy-render\n{os.getpid()}\n")
    monkeypatch.setattr(hm, "heavy_enabled", lambda: False)  # stop right after the pending check
    assert hm.read_pending() == "heavy-render"
    assert hm.cmd_heavy_enter(1, time.time()) == 3  # refused for the switch, NOT for "in flight"


def test_heavy_enter_order_of_changes(hm, monkeypatch):
    calls = []
    monkeypatch.setattr(hm, "stop_unit", lambda n, **k: calls.append(("stop", n)))
    monkeypatch.setattr(hm, "wait_vram", lambda need, **k: calls.append(("vram", need)) or 46.0)
    monkeypatch.setattr(hm, "write_mode", lambda mode: calls.append(("mode", mode)))
    rc = hm.cmd_heavy_enter(4242, 1_900_000_000.0)
    assert rc == 0
    st = json.loads(hm.HEAVY_STATE.read_text())
    assert st["pid"] == 4242 and st["deadline"] == 1_900_000_000.0 and st["card"] == "heavy-render"
    assert hm.DISPATCH_PAUSE_MARK in (hm.HOUSE / "config.yaml").read_text()
    assert "crew.service" in hm._hold_list() and hm._hold_cards() == ["heavy-render"]
    assert hm.read_state()["parked"] == ["crew"]
    assert calls == [("stop", "crew"), ("vram", hm.HEAVY_MIN_FREE_GIB), ("mode", "heavy-render")]


def test_a_failed_enter_rolls_back_through_heavy_exit(hm, monkeypatch):
    def boom(n, **k):
        raise RuntimeError("stop timed out")
    exits = []
    monkeypatch.setattr(hm, "stop_unit", boom)
    monkeypatch.setattr(hm, "cmd_heavy_exit", lambda **k: exits.append(1) or 0)
    assert hm.cmd_heavy_enter(1, time.time() + 60) == 1
    assert exits == [1]


# ------------------------------------------------------------- heavy-exit and the restore
def test_heavy_exit_does_nothing_on_a_normal_house(hm, monkeypatch):
    monkeypatch.setattr(hm, "cmd_restore", lambda *a, **k: pytest.fail("a normal on a normal house"))
    assert hm.cmd_heavy_exit() == 0


def test_leaving_heavy_wakes_crew_and_always_clears_the_window(hm, monkeypatch):
    hm.MODE_FILE.write_text("heavy-render\n")
    hm.HEAVY_STATE.write_text("{}")
    hm.apply_dispatch_pause(True)
    hm.hold_units("crew.service", card=hm.HEAVY_CARD)
    hm.write_state({"parked": []})  # even a state that forgot crew
    seen = {}

    def inner(mode, dry_run=False):
        seen["hold"] = hm._hold_list()
        seen["parked"] = hm.read_state()["parked"]
        raise RuntimeError("crew did not come back")
    monkeypatch.setattr(hm, "_cmd_restore", inner)
    with pytest.raises(RuntimeError):
        hm.cmd_heavy_exit()
    assert seen == {"hold": [], "parked": ["crew"]}  # hold released, crew on the wake list
    assert hm.DISPATCH_PAUSE_MARK not in (hm.HOUSE / "config.yaml").read_text()  # never paused forever
    assert not hm.HEAVY_STATE.exists()


def test_an_ordinary_normal_is_untouched_by_the_wrapper(hm, monkeypatch):
    monkeypatch.setattr(hm, "_cmd_restore", lambda mode, dry_run=False: 7)
    monkeypatch.setattr(hm, "release_card", lambda c: pytest.fail("no heavy trace, no release"))
    assert hm.cmd_restore("normal") == 7


def test_heavy_exit_bypasses_the_prod_lock_and_every_gate(hm, monkeypatch):
    monkeypatch.setattr(hm, "prod_lock_blocks", lambda cmd, **k: True)
    monkeypatch.setattr(hm, "leave_the_host_gateway", lambda argv: None)
    monkeypatch.setattr(hm, "cmd_heavy_exit", lambda **k: 0)
    assert hm.main(["heavy-exit"]) == 0
    monkeypatch.setattr(hm, "cmd_heavy_enter", lambda *a, **k: pytest.fail("enter must stay locked"))
    assert hm.main(["heavy-enter", "--runner-pid", "1", "--deadline", "1"]) == 3


# ------------------------------------------------------------- Comfy, Zoey, the host
def test_start_comfy_passes_highvram_only_when_asked(hm, monkeypatch):
    argvs = []

    class P:
        pid = 99
    monkeypatch.setattr(hm, "comfy_up", lambda: False)
    monkeypatch.setattr(hm, "remember_comfy_pid", lambda pid: None)
    monkeypatch.setenv("KREW_COMFY_LOG", str(hm.HOUSE / "comfy.log"))
    monkeypatch.setattr(hm.subprocess, "Popen", lambda argv, **k: argvs.append(argv) or P())
    hm.start_comfy(highvram=True)
    hm.start_comfy()
    assert argvs[0][-1] == "--highvram" and "--highvram" not in argvs[1]


def test_zoey_goes_fully_to_ops_and_comes_back_byte_for_byte(hm, monkeypatch):
    zoey = hm.HOUSE / "profiles" / "zoey" / "config.yaml"
    orig = ("model:\n  default: ops\n  provider: custom:ops\n  base_url: http://127.0.0.1:8000/v1\n"
            "auxiliary:\n  vision:\n    provider: custom\n    model: crew\n    base_url: http://127.0.0.1:8005/v1\n"
            "fallback_model:\n  provider: custom\n  model: crew\n  base_url: http://127.0.0.1:8005/v1\n")
    zoey.write_text(orig)
    restarts = []
    monkeypatch.setattr(hm, "_restart_ops_pin_gateways", lambda: restarts.append(1))
    out = hm.apply_ops_occupancy("heavy-render", restart=True)
    assert out.startswith("ops (") and hm.CREW_URL_PIN not in zoey.read_text() and "model: crew" not in zoey.read_text()
    assert restarts == [1]  # her PROFILE restart (host untouched)
    assert hm.apply_ops_occupancy("heavy-render", restart=True) == "ops (already)"
    hm.apply_ops_occupancy("normal", restart=True)
    assert zoey.read_text() == orig


def test_write_mode_never_bounces_the_host_under_heavy_render(hm, monkeypatch):
    calls = []
    for f in ("apply_ops_occupancy", "maybe_sleep_ada_for_exclusive", "maybe_restart_ada_gateway",
              "maybe_restart_zoey_gateway"):
        monkeypatch.setattr(hm, f, lambda *a, _f=f, **k: calls.append(_f))
    monkeypatch.setattr(hm, "maybe_bounce_ada_for_caps", lambda *a, **k: calls.append("BOUNCE"))
    for changed in (True, False):
        monkeypatch.setattr(hm, "write_flag", lambda mode, _c=changed, **k: _c)
        hm.write_mode("heavy-render")
    assert "BOUNCE" not in calls


def test_caps_on_disk_stay_as_they_are_under_heavy_render(hm, monkeypatch):
    got = []
    monkeypatch.setattr(hm, "ada_caps_on_disk", lambda *a, **k: (6, 3))
    monkeypatch.setattr(hm, "apply_ada_dispatch", lambda mode, caps=None, **k: got.append(caps) or "x")
    for f in ("apply_turbo_krew_occupancy", "drop_presence_to_guest", "apply_render_lane",
              "apply_ada_decomposer", "apply_zoey_spawn"):
        monkeypatch.setattr(hm, f, lambda *a, **k: "x")
    hm.write_flag("heavy-render")
    assert got == [(6, 3)]


# ------------------------------------------------------------- the runner
@pytest.fixture
def hr(monkeypatch):
    r = _load("heavy_render_h2", "crew/scripts/heavy-render.py")
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: pytest.fail(f"no subprocess: {a[:1]}"))
    return r


def _o(oid, fam, files, seq):
    return {"id": oid, "family": fam, "models": [{"path": p, "bytes": int(g * 2 ** 30)} for p, g in files],
            "priority": 0, "seq": seq, "created": "2026-09-29T05:00:00", "need_gib": sum(g for _p, g in files)}


def test_sub_batches_fit_resident_or_run_alone_in_normal_vram(hr):
    by = {"a": _o("a", "flux2", [("/dev", 30), ("/enc", 8)], 1),
          "b": _o("b", "flux2", [("/dev", 30), ("/enc", 8)], 2),     # same weights: joins a
          "c": _o("c", "flux2", [("/gguf", 18), ("/enc", 8)], 3),    # union 56+3 > 44: new run
          "d": _o("d", "flux2", [("/max", 50), ("/enc", 8)], 4)}     # alone 58+3 > 44: normal VRAM
    subs = hr.sub_batches([{"family": "flux2", "orders": ["a", "b", "c", "d"]}], by, budget=44)
    assert [(s["orders"], s["highvram"]) for s in subs] == [(["a", "b"], True), (["c"], True), (["d"], False)]
    video = hr.sub_batches([{"family": "ltx23", "orders": ["v"]}],
                           {"v": _o("v", "ltx23", [("/ltx", 39)], 1)}, budget=44)
    assert video[0]["highvram"] is False  # 39 + the 6 GiB video margin does not fit 44


def _orders(tmp_path, n=2, fam_file=("/w", 5)):
    root = tmp_path / "orders"
    for i in range(n):
        o = {"id": f"ro-{i}", "state": "pending", "workflow": "w", "graph": {"1": {"class_type": "X", "inputs": {}}},
             "family": "sdxl", "models": [{"path": fam_file[0], "bytes": int(fam_file[1] * 2 ** 30)}],
             "need_gib": fam_file[1], "priority": 0, "seq": i, "created": "t", "out_dir": str(tmp_path / "out" / f"ro-{i}"),
             "requested_by": "test", "history": []}
        ro._atomic(root / "pending" / f"ro-{i}.json", o)
    return root


def test_plan_touches_nothing_and_says_how_crew_comes_back(hr, tmp_path, monkeypatch, capsys):
    root = _orders(tmp_path)
    monkeypatch.setenv("KREW_HEAVY_RENDER", "0")
    p = hr.make_plan(root=root)
    hr.print_plan(p)
    out = capsys.readouterr().out
    assert "2 of 2 pending" in out and "--highvram" in out
    assert "crew comes back" in out and "`run` would refuse" in out
    assert len(list((root / "pending").glob("*.json"))) == 2


def test_the_unit_always_runs_heavy_exit_after_the_runner(hr):
    argv = hr.unit_argv(time.time() + 600, max_orders=3, unit="heavy-render-1")
    assert argv[:2] == ["systemd-run", "--user"] and "--collect" in argv
    runtime = int(next(a for a in argv if a.startswith("RuntimeMaxSec=")).split("=")[1])
    assert 1150 < runtime <= 1200  # the deadline + 10 minutes
    stop = next(a for a in argv if a.startswith("ExecStopPost="))
    assert stop.endswith(" heavy-exit") and hr.KREW_MODE in stop
    # heavy-exit waits for crew; systemd's 90 s default killed it in the H3 crash drill
    stop_s = int(next(a for a in argv if a.startswith("TimeoutStopSec=")).split("=")[1])
    assert stop_s >= 1800
    assert argv[-5:] == ["inside", "--deadline", argv[-3], "--max-orders", "3"]


def test_run_refuses_while_off_empty_or_busy(hr, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KREW_HEAVY_RENDER", "0")
    assert hr.cmd_run(30, None, False) == 3
    monkeypatch.setenv("KREW_HEAVY_RENDER", "1")
    monkeypatch.setattr(hr, "make_plan", lambda **k: {"orders": 0})
    assert hr.cmd_run(30, None, False) == 0 and "nothing to do" in capsys.readouterr().out
    monkeypatch.setattr(hr, "make_plan", lambda **k: {"orders": 1})
    monkeypatch.setattr(hr, "board_running", lambda: 2)
    assert hr.cmd_run(30, None, False) == 3
    monkeypatch.setattr(hr, "board_running", lambda: -1)  # unreadable board: refused, fail closed
    assert hr.cmd_run(30, None, False) == 3


class FakeComfy:
    def __init__(self, fail_on=None, raise_exc=None):
        self.n, self.fail_on, self.raise_exc, self.posts = 0, fail_on, raise_exc, []

    def post(self, path, body):
        self.posts.append(path)
        if path == "/free":
            return {}
        self.n += 1
        if self.raise_exc and self.n == 1:
            raise self.raise_exc
        return {"prompt_id": f"p{self.n}"}

    def get(self, path):
        pid = path.rsplit("/", 1)[1]
        ok = not (self.fail_on and pid == self.fail_on)
        return {pid: {"status": {"completed": ok, "status_str": "success" if ok else "error"},
                      "outputs": {"9": {"images": [{"filename": f"{pid}.png", "type": "output"}]}}}}

    def raw(self, path):
        return b"PNG"


def _hm_recorder(rc_for=None):
    calls = []

    def hm(*args):
        calls.append(args[0])
        return (rc_for or {}).get(args[0], 0)
    return hm, calls


def test_a_window_renders_every_order_and_leaves_through_heavy_exit(hr, tmp_path):
    root = _orders(tmp_path, 2)
    hm, calls = _hm_recorder()
    comfy = FakeComfy(fail_on="p2")
    assert hr.cmd_inside(time.time() + 600, None, hm=hm, comfy=comfy, root=root) == 0
    assert calls == ["heavy-enter", "heavy-comfy", "heavy-exit"]
    done = ro.orders("done", root=root)
    failed = ro.orders("failed", root=root)
    assert [o["id"] for o in done] == ["ro-0"] and Path(done[0]["outputs"][0]).read_bytes() == b"PNG"
    assert [o["id"] for o in failed] == ["ro-1"] and "status error" in failed[0]["why"]
    assert comfy.posts[-1] == "/free"


def test_a_refused_enter_still_leaves_through_heavy_exit(hr, tmp_path):
    root = _orders(tmp_path, 1)
    hm, calls = _hm_recorder({"heavy-enter": 3})
    assert hr.cmd_inside(time.time() + 600, None, hm=hm, comfy=FakeComfy(), root=root) == 3
    assert calls == ["heavy-enter", "heavy-exit"] and len(ro.orders("pending", root=root)) == 1


def test_a_crash_mid_window_still_brings_crew_back(hr, tmp_path):
    root = _orders(tmp_path, 1)
    calls = []

    def hm(*args):
        calls.append(args[0])
        if args[0] == "heavy-comfy":
            raise RuntimeError("the runner itself broke")
        return 0
    with pytest.raises(RuntimeError):
        hr.cmd_inside(time.time() + 600, None, hm=hm, comfy=FakeComfy(), root=root)
    assert calls[-1] == "heavy-exit"


def test_sigterm_mid_render_puts_the_order_back_and_exits(hr, tmp_path):
    root = _orders(tmp_path, 1)
    hm, calls = _hm_recorder()
    with pytest.raises(SystemExit):
        hr.cmd_inside(time.time() + 600, None, hm=hm, comfy=FakeComfy(raise_exc=SystemExit(143)), root=root)
    assert calls[-1] == "heavy-exit"
    back = ro.orders("pending", root=root)
    assert [o["id"] for o in back] == ["ro-0"] and back[0]["history"][-1]["why"] == "window ended mid-render"


def test_a_passed_deadline_starts_nothing_and_leaves(hr, tmp_path):
    root = _orders(tmp_path, 2)
    hm, calls = _hm_recorder()
    assert hr.cmd_inside(time.time() - 1, None, hm=hm, comfy=FakeComfy(), root=root) == 0
    assert calls == ["heavy-enter", "heavy-exit"] and len(ro.orders("pending", root=root)) == 2


def test_max_orders_caps_the_window(hr, tmp_path):
    root = _orders(tmp_path, 3)
    hm, calls = _hm_recorder()
    hr.cmd_inside(time.time() + 600, 2, hm=hm, comfy=FakeComfy(), root=root)
    assert len(ro.orders("done", root=root)) == 2 and len(ro.orders("pending", root=root)) == 1


def test_run_requeues_a_dead_runners_order_before_counting(hr, monkeypatch, capsys):
    """H3 09-29: the only order was stranded in running/ and `run` answered 'nothing to do'."""
    monkeypatch.setenv("KREW_HEAVY_RENDER", "1")
    calls = []
    monkeypatch.setattr(hr.ro, "requeue_orphans", lambda **k: calls.append(1) or ["ro-x"])
    monkeypatch.setattr(hr, "make_plan", lambda **k: {"orders": 0})
    assert hr.cmd_run(30, None, False) == 0
    assert calls and "ro-x back to pending" in capsys.readouterr().out
