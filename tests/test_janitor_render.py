"""janitor: Render leftover detector + auto-restore. No systemd, no kanban."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
loader = SourceFileLoader("crew_janitor", str(ROOT / "janitor" / "krew-janitor.py"))
spec = spec_from_loader(loader.name, loader)
j = module_from_spec(spec)
loader.exec_module(j)


def test_decision_matrix():
    d = j.render_leftover_decision
    assert d("normal", True, ["t_a"], {"t_a": "done"}, False)[0] is False
    assert d("render", True, [], {}, False)[0] is False            # human hold: never
    assert d("render", True, ["t_a"], {"t_a": "running"}, False)[0] is False
    assert d("render", True, ["t_a"], {"t_a": "done"}, False)[0] is True
    assert d("render", True, ["t_a"], {"t_a": "blocked"}, False)[0] is True
    assert d("render", True, ["t_a", "t_b"], {"t_a": "done", "t_b": "ready"}, False)[0] is False
    assert d("render", False, [], {}, False)[0] is True            # flag says render, no hold at all
    assert d("render", True, ["t_a"], {"t_a": "done"}, True)[0] is False   # transition pending


def test_tick_needs_two_ticks_then_launches_once(monkeypatch, tmp_path):
    monkeypatch.setattr(j, "GPU_PENDING_FILE", tmp_path / "none")
    launched = []
    filed = []
    st: dict = {}
    kw = dict(hold=lambda: (True, ["t_a"]), status_fn=lambda c: "done",
              launch=lambda why: launched.append(why) or True, filer=lambda *a: filed.append(a))
    r1 = j.render_leftover_tick(st, 1000.0, "render", **kw)
    assert r1["leftover"] and r1["ticks"] == 1 and r1["action"] is None and not launched
    r2 = j.render_leftover_tick(st, 1300.0, "render", **kw)
    assert r2["action"] == "launched" and launched == ["owners terminal: t_a=done"]
    r3 = j.render_leftover_tick(st, 1600.0, "render", **kw)
    assert r3["action"] == "cooldown" and len(launched) == 1
    r4 = j.render_leftover_tick(st, 1900.0, "normal", **kw)
    assert r4["leftover"] is False and st["render_leftover_ticks"] == 0
    assert not filed


def test_tick_files_card_when_launch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(j, "GPU_PENDING_FILE", tmp_path / "none")
    filed = []
    st = {"render_leftover_ticks": 1}
    r = j.render_leftover_tick(st, 5000.0, "render", hold=lambda: (False, []),
                               status_fn=lambda c: "done", launch=lambda why: False,
                               filer=lambda *a: filed.append(a))
    assert r["action"] == "launch_failed" and len(filed) == 1


def test_the_mode_button_render_and_a_running_init_are_never_leftovers(monkeypatch, tmp_path):
    """2026-09-14: "occupancy" (crew-mode's Mode-button Render) and "init-wizard" (a running Init) are not
    kanban cards — `kanban show` says "no such task" → "missing" → terminal, and the janitor took the machine
    to Standard ~10 min after every Mode-button Render (12:55, before the owner opener test)."""
    d = j.render_leftover_decision
    assert d("render", True, ["occupancy"], {}, False)[0] is False
    assert d("render", True, ["init-wizard"], {}, False)[0] is False
    assert d("render", True, ["occupancy", "t_a"], {"t_a": "done"}, False)[0] is False
    assert d("render", False, [], {}, False, init_live=True)[0] is False       # Init owns Render, hold or not
    assert d("render", True, ["t_a"], {"t_a": "done"}, False, init_live=False)[0] is True
    monkeypatch.setattr(j, "GPU_PENDING_FILE", tmp_path / "none")
    monkeypatch.setattr(j, "init_running", lambda: False)
    asked = []
    r = j.render_leftover_tick({}, 1000.0, "render", hold=lambda: (True, ["occupancy"]),
                               status_fn=lambda c: asked.append(c) or "missing",
                               launch=lambda why: True, filer=lambda *a: None)
    assert r["leftover"] is False and asked == [], "no kanban lookup for a hold that is not a card"
    state = tmp_path / "init-wizard.json"
    state.write_text('{"status": "running"}')
    monkeypatch.setattr(j, "INIT_STATE_FILE", state)
    monkeypatch.undo()
    monkeypatch.setattr(j, "INIT_STATE_FILE", state)
    assert j.init_running() is True
