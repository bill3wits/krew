"""The janitor's one watch list + the Control Panel's Janitor tile (the owner 2026-10-06: "We cannot
operate like this" - hidden off switches, a unit failing on no list). lib/krew_watch.py,
THE unit registry (lib/krew_registry.py) + config/unit-notes.json (words only), scripts/krew-janitor.py
watch_tick, /v1/janitor on the panel. The hand list config/crew-watch.json is gone (2026-10-06)."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))
import krew_watch as hw  # noqa: E402

CFG = {"notes": {}, "gaps": []}   # the crew's own notes file is its data, not krew's
NOTES = CFG["notes"]
import krew_registry as hr  # noqa: E402


def _janitor():
    spec = importlib.util.spec_from_file_location("crew_janitor_watch_t", ROOT / "janitor" / "krew-janitor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def runner_for(table: dict, journal: str = ""):
    """systemctl show / journalctl stub: table[unit] = {prop: value}."""
    def run(cmd, **_kw):
        if cmd[0] == "journalctl":
            return SimpleNamespace(stdout=journal, returncode=0)
        unit = next(c for c in cmd if c.endswith((".service", ".timer", ".path", ".socket")))
        kv = table.get(unit, {})
        return SimpleNamespace(stdout="".join(f"{k}={v}\n" for k, v in kv.items()), returncode=0)
    return run


def up(**kw):
    return {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "Result": "success", **kw}


# ---- the list ---------------------------------------------------------------------------------------
def test_every_note_says_what_it_is_and_who_leans_on_it():
    for u, e in NOTES.items():
        assert u.endswith((".service", ".timer", ".path", ".socket"))
        assert e.get("label") and e.get("what"), u
        assert e.get("scope", "user") in ("user", "system")
        assert e.get("expect", "running") in hw.EXPECTS
        if e.get("off_file"):
            assert e["off_file"].startswith("~/.hermes/") and e["off_file"].endswith(".off")




def test_the_watch_list_is_the_registry_minus_the_janitors_own_units():
    j = _janitor()
    builtin = {u for u, _ in j.UNITS}
    reg = {"units": [{"unit": u, "scope": "user", "role": "service", "expect": "running"}
                     for u in ("crew-conductor.service", "crew-pose-map.service", "krew-crissa-new.service")]}
    es = {e["unit"]: e for e in hr.watch_entries(reg, CFG, skip=builtin)}
    assert "crew-conductor.service" not in es, "the janitor watches it by unit state already"
    assert {"crew-pose-map.service", "krew-crissa-new.service"} <= set(es)
    assert es["krew-crissa-new.service"]["feeds"] == "not written down yet", "no note: still watched, and says so"
    cfg = hw.load(reg=reg, skip=frozenset(builtin))
    assert {e["unit"] for e in cfg["watch"]} == set(es) and cfg["gaps"] == CFG["gaps"]




def test_a_disabled_timer_is_listed_as_off_by_choice_not_red():
    r = hw.check({"unit": "k.timer", "expect": "off"}, runner=runner_for({"k.timer": up(ActiveState="inactive",
                                                                                        SubState="dead")}))
    assert r["level"] == "ok" and "off by choice" in r["note"]


def test_an_enabled_path_unit_that_is_not_watching_is_red():
    r = hw.check({"unit": "k.path", "expect": "path"}, runner=runner_for({"k.path": up(ActiveState="inactive",
                                                                                      SubState="dead")}))
    assert r["red"] and r["why"] == "path inactive/dead"


# ---- verdicts ---------------------------------------------------------------------------------------
def test_a_failed_service_is_red_and_says_why():
    r = hw.check({"unit": "x.service"}, runner=runner_for({"x.service": up(ActiveState="failed", SubState="failed",
                                                                            Result="exit-code")}))
    assert r["red"] and r["level"] == "bad" and "failed" in r["why"]


def test_a_running_service_is_green_and_an_exited_oneshot_is_green_when_expected():
    assert hw.check({"unit": "x.service"}, runner=runner_for({"x.service": up()}))["level"] == "ok"
    r = hw.check({"unit": "x.service", "expect": "exited"}, runner=runner_for({"x.service": up(SubState="exited")}))
    assert r["level"] == "ok"
    r = hw.check({"unit": "x.service"}, runner=runner_for({"x.service": up(ActiveState="inactive", SubState="dead")}))
    assert r["red"] and r["why"] == "inactive/dead"


def test_a_missing_unit_is_red():
    r = hw.check({"unit": "gone.service"}, runner=runner_for({"gone.service": {"LoadState": "not-found"}}))
    assert r["red"] and "not-found" in r["why"]


def test_a_timer_is_red_when_its_last_run_failed():
    t = {"x.timer": up(SubState="waiting", Unit="x.service"),
         "x.service": {"ActiveState": "failed", "Result": "exit-code"}}
    r = hw.check({"unit": "x.timer"}, runner=runner_for(t))
    assert r["red"] and "x.service" in r["why"]
    t["x.service"] = {"ActiveState": "inactive", "Result": "success"}
    assert hw.check({"unit": "x.timer"}, runner=runner_for(t))["level"] == "ok"


def test_a_pause_switch_is_amber_and_named_never_hidden(tmp_path):
    off = tmp_path / "x.off"
    off.write_text("testing the tile\n")
    r = hw.check({"unit": "x.service", "off_file": str(off)}, runner=runner_for({"x.service": up()}))
    assert r["level"] == "paused" and not r["red"] and "testing the tile" in r["why"]


def test_a_gpu_tenant_parked_by_the_mode_is_not_red():
    r = hw.check({"unit": "voice.service", "expect": "gpu"},
                 runner=runner_for({"voice.service": up(ActiveState="inactive", SubState="dead")}),
                 parked=lambda u: True)
    assert r["level"] == "parked" and not r["red"]


def test_a_failed_build_in_the_journal_turns_the_watcher_red():
    e = {"unit": "w.service", "log_check": {"name": "build", "pattern": r"build (rc=-?\d+|TIMEOUT)", "ok": "build rc=0"}}
    good = "crew-places-watch: build rc=1 in 1s\nhouse-places-watch: build rc=0 in 1s\n"
    assert hw.check(e, runner=runner_for({"w.service": up()}, good))["level"] == "ok"
    bad = "crew-places-watch: build rc=0 in 1s\nhouse-places-watch: build TIMEOUT\n"
    r = hw.check(e, runner=runner_for({"w.service": up()}, bad))
    assert r["red"] and "TIMEOUT" in r["why"]


# ---- pause / resume ----------------------------------------------------------------------------------
def test_pause_and_resume_only_touch_a_declared_switch(tmp_path):
    off = tmp_path / "w.off"
    cfg = {"watch": [{"unit": "w.service", "off_file": str(off)}, {"unit": "n.service"}]}
    assert hw.set_pause("w.service", True, cfg=cfg)["ok"] and off.exists()
    assert "Control Panel" in off.read_text()
    assert hw.set_pause("w.service", False, cfg=cfg)["ok"] and not off.exists()
    assert not hw.set_pause("n.service", True, cfg=cfg)["ok"]
    assert not hw.set_pause("/etc/passwd", True, cfg=cfg)["ok"]


# ---- gaps ---------------------------------------------------------------------------------------------
def test_gaps_close_on_their_done_condition_and_stay_closed(tmp_path):
    (tmp_path / "plug").mkdir()
    orders = tmp_path / "orders"
    (orders / "done").mkdir(parents=True)
    cfg = {"gaps": [
        {"id": "g1", "title": "tool", "done_when": {"kind": "grep", "paths": ["plug"], "glob": "*.py",
                                                     "pattern": r"name=\"render_order\""}},
        {"id": "g2", "title": "clip", "done_when": {"kind": "order", "card": "t_x", "workflow": "qwen-edit",
                                                     "after": "2026-10-06T02:30:00"}},
    ]}
    st = {g["id"]: g["status"] for g in hw.gaps(cfg, root=tmp_path, orders=orders)}
    assert st == {"g1": "open", "g2": "open"}
    (tmp_path / "plug" / "a.py").write_text('ctx.register_tool(name="render_order")')
    (orders / "done" / "old.json").write_text(json.dumps({"card": "t_x", "workflow": "qwen-edit-2511",
                                                          "created": "2026-10-06T01:00:00"}))
    out = {g["id"]: g for g in hw.gaps(cfg, root=tmp_path, orders=orders, now="2026-10-06T03:00:00")}
    assert out["g1"]["status"] == "closed" and out["g1"]["closed_at"] == "2026-10-06T03:00:00"
    assert out["g2"]["status"] == "open", "an order from before the gap opened does not close it"
    later = hw.gaps(cfg, {"gaps": list(out.values())}, root=tmp_path, orders=orders, now="2026-10-07T00:00:00")
    assert later[0]["closed_at"] == "2026-10-06T03:00:00"




# ---- the janitor and the tile -------------------------------------------------------------------------
def test_watch_tick_cards_a_red_unit_once_and_the_snapshot_names_it(tmp_path, monkeypatch):
    j = _janitor()
    monkeypatch.setenv("KREW_JANITOR_WATCH", "1")
    snap = tmp_path / "snap.json"
    fake = SimpleNamespace(
        on=lambda: True,
        check_all=lambda parked=None, skip=frozenset(): [{"unit": "z.service", "scope": "user", "label": "Z", "red": True,
                                        "level": "bad", "why": "failed (exit-code)", "state": "failed/failed"}],
        write_snapshot=lambda s: hw.write_snapshot(s, snap), read_snapshot=lambda: None, gaps=lambda prev=None: [])
    monkeypatch.setattr(j, "journal_tail", lambda *a, **k: "")
    filed = []
    state = {"cards": {}}
    row = {"ts": "2026-10-06T02:50:00", "probes": {}, "gpu_mode": "normal"}
    j.watch_tick(state, 1_000_000.0, "normal", False, {}, row, hw=fake, card=lambda t, b: filed.append(t) or True)
    j.watch_tick(state, 1_000_300.0, "normal", False, {}, row, hw=fake, card=lambda t, b: filed.append(t) or True)
    assert filed == ["JANITOR RED: z.service failed (exit-code)"], "one card per unit per cooldown"
    s = json.loads(snap.read_text())
    assert s["level"] == "bad" and s["reds"][0]["name"] == "z.service" and "z.service" in s["headline"]


def test_the_panel_calls_a_silent_janitor_stale(tmp_path):
    p = tmp_path / "snap.json"
    hw.write_snapshot({"epoch": 1000.0, "level": "ok", "headline": "fine"}, p)
    v = hw.panel_view(path=p, cfg={"watch": []}, now=1000.0 + hw.STALE_S + 60)
    assert v["stale"] and v["level"] == "bad" and "not reported" in v["headline"]
    assert hw.panel_view(path=p, cfg={"watch": []}, now=1100.0)["level"] == "ok"
    assert hw.panel_view(path=tmp_path / "none.json", cfg={"watch": []})["ok"] is False






