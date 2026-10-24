"""The wall-tab probe reports a dead page and never touches it (the User 2026-09-30).

No browser and no board here: the one question (`ask`), the tab list and the kanban CLI are stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path

import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import wall_tab_probe as wp  # noqa: E402

# krew: the glass mode and the browser's tab list are read inside wall_tab_probe (the house used the Wall's own
# lib/glass_mode and lib/show_glass); the same two names, so the rules below are stubbed as the house stubbed them
glass_mode = wp.glass_mode
show_glass = wp.show_glass
TAB = {"id": "284CB33F0FBB", "type": "page", "url": "http://127.0.0.1:8099/", "webSocketDebuggerUrl": "ws://page"}


@pytest.fixture()
def house(tmp_path, monkeypatch):
    calls = []
    flag = tmp_path / "krew-glass-mode"
    flag.write_text("brave\n")            # Wallpaper OFF: Brave is the wall, the only mode it probes
    monkeypatch.setattr(glass_mode, "PATH", flag)
    monkeypatch.setattr(glass_mode, "is_active", lambda unit: False)
    monkeypatch.setattr(glass_mode, "is_enabled", lambda unit: False)
    # The suite's venv python is websockets-less (the unit's interpreter is not); the
    # probe's up-front gate would read no-browser in this interpreter, so the fixture
    # asserts the question CAN be asked. The one no-module case gets its own test.
    monkeypatch.setattr(wp, "_has_websockets", lambda: True)
    monkeypatch.setattr(show_glass, "GLASS_FLAG", flag)
    monkeypatch.setattr(show_glass, "_get", lambda path: [TAB])
    monkeypatch.setattr(wp, "STATE", tmp_path / "wall-tab-probe.json")
    monkeypatch.setattr(wp, "OFF", tmp_path / "wall-tab-probe.off")
    monkeypatch.delenv("KREW_WALL_TAB_PROBE", raising=False)
    monkeypatch.delenv("KREW_WALL_TAB_PROBE_CARDS", raising=False)
    answer = {"answered": True, "crashed": False}
    monkeypatch.setattr(wp, "ask", lambda ws_url, wait=wp.ANSWER_S: dict(answer))

    def kanban(*args, timeout=60):
        calls.append(args)
        return (0, "Created t_0badc0de") if args[0] == "create" else (0, "")

    monkeypatch.setattr(wp, "kanban", kanban)
    return type("H", (), {"calls": calls, "answer": answer, "flag": flag, "tmp": tmp_path})


# ── the reading ─────────────────────────────────────────────────────────────

def test_a_page_that_answers_is_ok(house):
    r = wp.probe()
    assert r["verdict"] == "ok" and r["tab"] == "284CB33F" and r["href"].endswith(":8099/")


def test_a_crashed_renderer_is_dead_and_says_why(house):
    house.answer.update(answered=False, crashed=True)
    r = wp.probe()
    assert r["verdict"] == "dead" and r["crashed"] and "Inspector.targetCrashed" in r["why"]


def test_a_silent_page_is_dead_without_a_crash(house):
    house.answer.update(answered=False, crashed=False)
    r = wp.probe()
    assert r["verdict"] == "dead" and not r["crashed"] and "did not answer" in r["why"]


def test_an_attach_that_fails_counts_as_no_answer_never_an_exception(house, monkeypatch):
    monkeypatch.setattr(wp, "ask", lambda ws_url, wait=wp.ANSWER_S: (_ for _ in ()).throw(OSError("refused")))
    r = wp.probe()
    assert r["verdict"] == "dead" and "OSError" in r["why"]


def test_a_closed_browser_and_a_missing_tab_are_by_design_not_faults(house, monkeypatch):
    monkeypatch.setattr(show_glass, "_get", lambda path: (_ for _ in ()).throw(OSError("no CDP")))
    assert wp.probe()["verdict"] == "no-browser"
    monkeypatch.setattr(show_glass, "_get", lambda path: [{"id": "X", "type": "page", "url": "devtools://x"}])
    assert wp.probe()["verdict"] == "no-tab"
    for v in ("no-browser", "no-tab", "parked", "ok"):
        assert v in wp.NOT_A_FAULT


def test_a_module_less_interpreter_is_probe_side_not_a_dead_page(house, monkeypatch):
    # t_d97747d2: the unit runs plain /usr/bin/python3; without `websockets` the question
    # can never be asked — so the missing module reads no-browser (probe-side), never
    # dead, and ask() is not even reached. A no-browser reading takes no strike and
    # leaves an open episode untouched.
    monkeypatch.setattr(wp, "_has_websockets", lambda: False)
    ask_calls = []
    monkeypatch.setattr(wp, "ask", lambda ws_url, wait=wp.ANSWER_S: ask_calls.append(ws_url))
    r = wp.probe()
    assert r["verdict"] == "no-browser" and "probe-side" in r["why"] and ask_calls == []
    st, act = wp.step({}, r, 100.0)
    assert act == "" and st["strikes"] == 0
    open_ep = {"dead_since": 1.0, "strikes": 2, "episode": {"card": "t_26f85ef6"}}
    st2, act2 = wp.step(open_ep, r, 200.0)
    assert act2 == "" and st2["episode"]["card"] == "t_26f85ef6"


def test_wallpaper_on_files_nothing_about_the_wall_tab(house, monkeypatch):
    """Wallpaper ON (the User 2026-10-02): the wallpaper is the glass, so a missing or dead wall tab is
    expected - the page is not even asked, and no card is ever filed."""
    monkeypatch.setattr(wp, "ask", lambda *a, **k: pytest.fail("asked the page under Wallpaper ON"))
    house.flag.write_text("wallpaper from-brave\n")      # the first word is the mode, as the CP reads it
    for _ in range(4):
        out = wp.tick()
        assert out["verdict"] == "parked" and out["act"] == ""
    assert house.calls == []


def test_zoeyos_off_files_nothing(house, monkeypatch):
    monkeypatch.setattr(wp, "ask", lambda *a, **k: pytest.fail("asked the page with ZoeyOS off"))
    house.flag.write_text("off\n")
    assert wp.tick()["verdict"] == "parked" and house.calls == []


def test_the_mode_is_read_the_control_panels_way(house, monkeypatch):
    """No flag file: the mode reads `off` (krew reads the mode file alone: a missing flag never asks a page)."""
    monkeypatch.setattr(wp, "ask", lambda *a, **k: pytest.fail("asked the page under Wallpaper ON"))
    house.flag.unlink()
    assert wp.probe()["verdict"] == "parked"
    src = (ROOT / "crew" / "lib" / "wall_tab_probe.py").read_text()
    assert "glass_mode.read_mode()" in src and "show_glass.glass_mode()" not in src


def test_wallpaper_off_files_one_card_to_zoey_never_a_worker(house):
    house.answer.update(answered=False, crashed=True)
    assert wp.tick()["act"] == "file"
    create = house.calls[0]
    assert create[create.index("--assignee") + 1] == "zoey"
    assert "ada" not in create and wp.PROFILE == "zoey"


def test_the_card_tells_no_one_to_run_show_me_home(house):
    house.answer.update(answered=False, crashed=True)
    wp.tick()
    body = house.calls[0][house.calls[0].index("--body") + 1]
    for text in (body, wp.ASK, wp.card_title({})):
        assert "show_me" not in text and "reload" not in text.replace("reloaded nothing", "")


def test_a_dead_tools_tab_is_never_her_wall(house, monkeypatch):
    tools = [{"id": "K1", "type": "page", "url": "http://127.0.0.1:8099/krew", "webSocketDebuggerUrl": "ws://k"},
             {"id": "P1", "type": "page", "url": "http://127.0.0.1:8099/panel", "webSocketDebuggerUrl": "ws://p"}]
    monkeypatch.setattr(show_glass, "_get", lambda path: tools)
    house.answer.update(answered=False, crashed=True)
    for _ in range(3):
        out = wp.tick()
        assert out["verdict"] == "no-tab" and out["act"] == ""
    assert house.calls == []
    monkeypatch.setattr(show_glass, "_get", lambda path: tools + [dict(TAB, url="http://<lan-host>:8099/")])
    assert wp.probe()["verdict"] == "dead"            # her wall page itself, by port + root path


def test_both_kill_switches(house, monkeypatch):
    monkeypatch.setenv("KREW_WALL_TAB_PROBE", "0")
    assert wp.tick() == {"verdict": "off"} and not wp.STATE.exists()
    monkeypatch.delenv("KREW_WALL_TAB_PROBE")
    wp.OFF.write_text("")
    assert wp.tick() == {"verdict": "off"}


# ── the count and the card ──────────────────────────────────────────────────

def test_one_missed_answer_is_a_busy_page_two_is_a_card():
    dead = {"verdict": "dead", "crashed": False}
    st, act = wp.step({}, dead, 100.0)
    assert act == "" and st["strikes"] == 1 and st["dead_since"] == 100.0
    st, act = wp.step(st, dead, 220.0)
    assert act == "file" and st["strikes"] == 2 and st["dead_since"] == 100.0


def test_a_crash_is_certain_and_files_at_once():
    st, act = wp.step({}, {"verdict": "dead", "crashed": True}, 100.0)
    assert act == "file"


def test_an_answer_between_two_misses_clears_the_count():
    dead, ok = {"verdict": "dead", "crashed": False}, {"verdict": "ok"}
    st, _ = wp.step({}, dead, 1.0)
    st, act = wp.step(st, ok, 2.0)
    assert act == "" and st["strikes"] == 0 and "dead_since" not in st
    st, act = wp.step(st, dead, 3.0)
    assert act == "" and st["strikes"] == 1


def test_one_card_per_episode_blocked_on_zoey_then_closed_by_the_probe_when_it_answers(house):
    house.answer.update(answered=False, crashed=True)
    out = wp.tick()
    assert out["act"] == "file" and out["card"] == "t_0badc0de"
    create, block = house.calls
    assert create[0] == "create" and create[create.index("--assignee") + 1] == "zoey"
    assert create[create.index("--created-by") + 1] == "wall-tab-probe" and "--triage" not in create
    assert create[create.index("--idempotency-key") + 1].startswith("wall-tab-dead:")
    body = create[create.index("--body") + 1]
    assert "cause: its renderer crashed" in body and "evidence: tab 284CB33F" in body and "reloaded nothing" in body
    assert block[:3] == ("block", "t_0badc0de", wp.ASK) and block[3:] == ("--kind", "needs_input")   # reason FIRST
    for _ in range(3):                                   # still dead: the same episode, no second card
        assert wp.tick()["act"] == ""
    assert len(house.calls) == 2
    house.answer.update(answered=True, crashed=False)
    out = wp.tick()
    assert out["act"] == "recovered" and out["card"] == "t_0badc0de"
    comment, complete = house.calls[-2:]
    assert comment[0] == "comment" and "answers again" in comment[2] and comment[-2:] == ("--author", "wall-tab-probe")
    # the probe closes its own report: a recovered fault must not sit in Zoey's Decide queue
    assert complete[:2] == ("complete", "t_0badc0de") and complete[2] == "--summary" and "recovered" in complete[3]
    assert "episode" not in json.loads(wp.STATE.read_text())
    assert wp.tick()["act"] == "" and len(house.calls) == 4


def test_a_card_that_cannot_be_blocked_is_archived_not_left_for_a_worker(house, monkeypatch):
    def kanban(*args, timeout=60):
        house.calls.append(args)
        return {"create": (0, "Created t_0badc0de"), "block": (2, "usage")}.get(args[0], (0, ""))

    monkeypatch.setattr(wp, "kanban", kanban)
    house.answer.update(answered=False, crashed=True)
    out = wp.tick()
    assert out["card"] == "NOT FILED" and [c[0] for c in house.calls] == ["create", "block", "archive"]
    assert wp.tick()["act"] == ""                        # the episode is open: it does not refile every 2 min


def test_cards_off_still_reports(house, monkeypatch):
    monkeypatch.setenv("KREW_WALL_TAB_PROBE_CARDS", "0")
    house.answer.update(answered=False, crashed=True)
    out = wp.tick()
    assert out["verdict"] == "dead" and out["card"] == "cards off" and house.calls == []


def test_a_not_a_fault_reading_never_files_or_closes(house):
    house.flag.write_text("wallpaper\n")
    assert wp.tick()["act"] == "" and house.calls == []
    assert json.loads(wp.STATE.read_text())["last"]["verdict"] == "parked"


# ── report only ─────────────────────────────────────────────────────────────

def test_the_probe_cannot_reload_navigate_raise_or_restart():
    for rel in ("crew/lib/wall_tab_probe.py", "crew/scripts/krew-wall-tab-probe.py"):
        code = (ROOT / rel).read_text(encoding="utf-8").split('"""', 2)[2]      # the code, not the story
        for verb in ("Page.navigate", "Page.reload", "Page.bringToFront", "setWindowBounds", "json/new",
                     "json/activate", "json/close", "systemctl", "shell_activate", "set_state", "_put_away",
                     "show_glass.forward", "show_glass.home", "show_glass.cdp"):
            assert verb not in code, f"{rel} carries {verb}"
    sent = (ROOT / "crew" / "lib" / "wall_tab_probe.py").read_text().count('{"id":')
    assert sent == 2                                     # Inspector.enable and the one Runtime.evaluate


def test_the_unit_is_a_oneshot_that_never_fails_on_a_dead_page():
    svc = (ROOT / "crew/units/krew-wall-tab-probe.service").read_text()
    tmr = (ROOT / "crew/units/krew-wall-tab-probe.timer").read_text()
    assert "Type=oneshot" in svc and "krew-wall-tab-probe.py tick" in svc and "Restart=" not in svc
    assert "OnUnitActiveSec=2min" in tmr and "WantedBy=timers.target" in tmr
    assert "return 0" in (ROOT / "crew/scripts/krew-wall-tab-probe.py").read_text()


def test_the_daily_roll_call_expects_the_probe():
    # krew: the reference install's roll-call rows (config/window-schedule.example.json `_reference_install.watch`)
    watch = json.loads((ROOT / "config/window-schedule.example.json").read_text())["_reference_install"]["watch"]
    row = next(w for w in watch if w["routine"] == "wall-tab-probe")
    assert row["unit"] == "wall-tab-probe.service" and row["every_min"] == 2
