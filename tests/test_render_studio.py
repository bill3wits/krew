"""Render studio silo — no GPU, no Comfy, no ops."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_live_lila(monkeypatch, tmp_path):
    """Since 19a4fa34 (2026-09-16) a typed/ear/wall lila_turn goes to page_lila,
    which POSTs Lila's LIVE gateway (:8654) through scripts/lila-page.py and
    queues into the live ~/.hermes/render-studio.json. Five tests reached it on
    every run (her gateway logged a memory write about the test line "Zoey, make
    it warmer" at 06:59 on 2026-09-19). No test here may talk to her: the page
    script points at a file that does not exist and the state file is a tmp one
    (a test that sets its own STATE still wins)."""
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs, "LILA_PAGE", tmp_path / "no-live-lila-page.py")
    # ...nor to the live ear: listen_set POSTs :8194 session open/pause. The 07:00 run
    # on 2026-09-19 opened then paused the real session, and the ear dropped the owner's
    # "Hello. This is the owner." at 07:01:26 as zoey_skip/paused. A test that needs the
    # posts replaces this with its own recorder.
    monkeypatch.setattr(rs, "_ear_post", lambda url: None)
    monkeypatch.setattr(rs, "STATE", tmp_path / "render-studio-state.json")
    monkeypatch.setattr(rs, "speak_lila", lambda *a, **k: None)


def test_still_basename_rejects_traversal(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs, "OUT_DIR", tmp_path)
    (tmp_path / "ok.png").write_bytes(b"x")
    assert rs.still_basename("ok.png") == "ok.png"
    assert rs.still_basename(str(tmp_path / "ok.png")) == "ok.png"
    # Path.name is the allowlist — parent dirs cannot escape OUT_DIR.
    assert rs.still_basename("../ok.png") == "ok.png"
    assert rs.still_bytes("../ok.png") == b"x"
    assert rs.still_basename("/etc/passwd") is None
    assert rs.still_basename("ok.jpg") is None
    assert rs.still_basename("") is None
    assert rs.still_bytes("ok.png") == b"x"


def test_lila_turn_refuses_when_not_render(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("heavy\n")
    monkeypatch.setattr(rs, "LANE_BUSY", tmp_path / "lane-busy")
    # 19a4fa34 (2026-09-16 "Render Lila is her gateway"): a typed note with no
    # engine pages her gateway and the silo is the /v1/render-studio route's
    # (test_panel_has_studio_and_silo_copy). The draw turn (an engine) is what
    # still refuses here.
    d = rs.lila_turn("Friday supper", engine="flux2-turbo")
    assert d["ok"] is False
    assert "silo" in d["error"]
    assert "Render" in d["say"]


def test_pick_welcome_is_wallpaper_not_session_still(tmp_path, monkeypatch):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs, "OUT_DIR", tmp_path)
    (tmp_path / "flux2turbo_00008_.png").write_bytes(b"x")
    (tmp_path / "flux2turbo_00010_.png").write_bytes(b"y")
    assert rs.pick_welcome({"still": str(tmp_path / "flux2turbo_00010_.png")}) == "flux2turbo_00008_.png"
    assert rs.pick_welcome(
        {"welcome": "flux2turbo_00008_.png", "still": str(tmp_path / "flux2turbo_00010_.png")}
    ) == "flux2turbo_00008_.png"
    assert rs.pick_welcome({"hung": True, "still": str(tmp_path / "flux2turbo_00010_.png")}) == "flux2turbo_00008_.png"
    assert (
        rs.pick_welcome(
            {
                "wallpaper": "flux2turbo_00010_.png",
                "welcome": "flux2turbo_00008_.png",
                "still": str(tmp_path / "flux2turbo_00010_.png"),
            }
        )
        == "flux2turbo_00010_.png"
    )


def test_crew_feed_includes_kanban_comments(tmp_path, monkeypatch):
    import sqlite3
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    boards = tmp_path / "boards"
    day = boards / "daily-test"
    day.mkdir(parents=True)
    (boards / "current").write_text("daily-test\n")
    db = day / "kanban.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, created_at INTEGER, author TEXT, body TEXT, task_id TEXT)")
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, assignee TEXT, title TEXT)")
    con.execute("INSERT INTO task_comments VALUES (1, 100, 'vera', 'Pass on the still', 't_abc')")
    con.execute("INSERT INTO task_comments VALUES (2, 101, 'bash', 'landed the prompt', 't_abc')")
    con.execute("INSERT INTO tasks VALUES ('t_run1','running','lila','draw the plate')")
    con.commit(); con.close()
    (day / "logs").mkdir()
    (day / "logs" / "t_run1.log").write_text("comfy queued\nprogress 40%\n")
    monkeypatch.setattr(rs, "BOARD_ROOT", boards)
    monkeypatch.setattr(rs, "_state", lambda: {"turns": [{"ts": 90, "user": "warmer light", "say": "I'll warm it."}]})
    feed = rs.crew_feed()
    who = {x["who"] for x in feed}
    assert "You" in who and "Lila" in who and "vera" in who and "bash" in who
    assert any("draw the plate" in (x.get("text") or "") for x in feed)
    assert any("progress 40%" in (x.get("text") or "") for x in feed)


def test_page_lila_queues_when_a_card_is_running(tmp_path, monkeypatch):
    import sys
    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs
    monkeypatch.setattr(rs, "lila_card_busy", lambda: "card t_abc")
    monkeypatch.setattr(rs, "_state", lambda: {})
    saved = {}
    monkeypatch.setattr(rs, "_save", lambda st: saved.update(st))
    out = rs.page_lila("warmer light")
    assert out["queued"] is True
    assert "when she can" in out["say"]
    assert saved.get("inbox")


def test_snapshot_carries_feed(monkeypatch, tmp_path):
    import sys
    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs
    monkeypatch.setattr(rs, "crew_feed", lambda **k: [{"who": "vera", "text": "Pass.", "kind": "kanban", "ts": 1}])
    monkeypatch.setattr(rs, "_state", lambda: {})
    monkeypatch.setattr(rs, "latest_comfy_still", lambda **k: None)
    monkeypatch.setattr(rs, "pick_welcome", lambda st=None: None)
    snap = rs.snapshot()
    assert snap["feed"][0]["who"] == "vera"



def test_orient_line_uses_user_name():
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    assert rs.orient_line("Noah") == (
        "Noah — Lila has the helm."
    )
    assert rs.orient_line("") == "Lila has the helm."
    payload = {
        "people": [
            {
                "name": "noah",
                "auth": {"authed": True, "level": "face", "person_id": "noah"},
            }
        ]
    }
    assert rs.user_given_name(payload) == "Noah"
    assert rs.user_given_name({"people": [{"name": "s1", "auth": {"authed": True}}]}) == ""
    assert rs.user_given_name({"people": [{"name": "noah", "auth": {}}]}) == ""


def test_welcome_pin_refuses_when_not_render(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("heavy\n")
    d = rs.welcome_pin()
    assert d["ok"] is False


def test_welcome_pin_orients_and_never_arms_the_mic(monkeypatch, tmp_path):
    # 2026-09-17: the Studio mic is a click, never the entry — Render is not a
    # conversation until she is addressed (it used to arm listen here).
    import json
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("render\n")
    monkeypatch.setattr(rs, "STATE", tmp_path / "studio.json")
    monkeypatch.setattr(rs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(rs, "LISTEN_FLAG", tmp_path / "listen")
    (tmp_path / "flux2turbo_00008_.png").write_bytes(b"x")
    (tmp_path / "studio.json").write_text(
        json.dumps(
            {
                "turns": [{"user": "Caroline", "say": "old"}],
                "vera": {"why": "computer"},
                "engine": "flux2-klein",
                "spec": "stale",
            }
        )
        + "\n"
    )
    spoken: list[str] = []
    monkeypatch.setattr(rs, "speak_lila", lambda t: spoken.append(t))
    monkeypatch.setattr(rs, "user_given_name", lambda payload=None: "the owner")
    monkeypatch.setattr(rs, "silo_ok", lambda: (True, "ok"))
    d = rs.welcome_pin()
    assert d["ok"] is True
    line = "the owner — Lila has the helm."
    assert d["cue"] == rs.CUE
    assert d["say"] == line
    assert d["user"] == "the owner"
    assert d["listen"] is False and not (tmp_path / "listen").exists()
    assert spoken == []     # the owner 2026-09-14: no scripted announcement of Render mode
    again = rs.welcome_pin()
    assert again.get("already") is True
    assert again.get("listen") is False
    (tmp_path / "listen").write_text("render-studio\n")     # the User pressed Studio mic
    assert rs.welcome_pin().get("listen") is True
    (tmp_path / "listen").unlink()
    assert again.get("talk") is True
    assert spoken == []     # the owner 2026-09-14: no scripted announcement of Render mode
    assert d.get("still") is None
    assert not (tmp_path / "listen").exists()
    st = rs._state()
    assert st.get("orient") is True
    assert st.get("cue") == rs.CUE
    assert st.get("spec") == ""
    assert st.get("card") is None
    assert not st.get("still")
    assert st.get("turns") == []
    assert st.get("engine") == rs.DEFAULT_ENGINE



def test_is_hang_and_not_keep_this_chip():
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    assert rs.is_hang("hang it")
    assert rs.is_hang("That's the one.")
    assert not rs.is_hang("Keep this. Pull the red cup closer to the window.")
    assert not rs.is_hang("add a vase of lilies")


def test_apply_vera_ref_does_not_resurrect_dropped_objects():
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    spec = "a wolf howling under the moon"
    vera = {"reference": "Friday supper, lilies, yellow ribbon, wolf"}
    out = rs.apply_vera_ref("Friday supper, lilies", spec, vera)
    assert "lilies" not in out.lower()
    assert "wolf" in out.lower()
    assert out == spec


def test_resolve_engine_menu_only():
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    assert rs.resolve_engine({}, "not-a-model") == "flux2-turbo"
    assert rs.resolve_engine({}, "sdxl") == "sdxl"   # a studio engine krew's recipes.json carries
    assert rs.resolve_engine({"engine": "sdxl"}, "") == "sdxl"
    assert rs.resolve_engine({"engine": "sdxl"}, "flux2-klein") == "flux2-klein"
    assert rs.resolve_engine({"engine": "flux2-turbo"}, "flux2-klein", "sdxl") == "sdxl"
    assert rs.resolve_engine({}, "flux2-klein") == "flux2-klein"
    talk = rs.LILA_TALK
    assert "flux2-klein" in talk and "sdxl" in talk
    assert "cheap" in talk.lower() or "draft" in talk.lower()
    assert "wan22-i2v" in talk
    assert "ltx25-i2v" in talk
    assert "wallpaper" in talk.lower()
    assert "wallpaper" in talk.lower()
    assert "Never take an object" not in talk


def test_next_spec_replace_vs_additive():
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    seed = "Friday supper, long table, bread, red cup"
    assert rs.next_spec(seed, "a wolf howling under the moon", generated=True) == (
        "a wolf howling under the moon"
    )
    added = rs.next_spec(seed, "add a vase of lilies with a yellow ribbon", generated=True)
    assert "lilies" in added.lower()
    assert "Friday supper" in added
    chat = rs.next_spec(seed, "love it!", generated=False)
    assert chat == seed


def test_listen_set_refuses_when_not_render(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    monkeypatch.setattr(rs, "LISTEN_FLAG", tmp_path / "listen")
    (tmp_path / "mode").write_text("heavy\n")
    d = rs.listen_set(True)
    assert d["ok"] is False
    assert d["on"] is False
    assert not (tmp_path / "listen").exists()
    off = rs.listen_set(False)
    assert off["ok"] is True
    assert off["on"] is False


def test_lila_turn_refuses_normal(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("normal\n")
    monkeypatch.setattr(rs, "LANE_BUSY", tmp_path / "lane-busy")
    d = rs.lila_turn("draw something", engine="flux2-turbo")   # the draw turn (19a4fa34)
    assert d["ok"] is False
    assert "not Render" in d["error"]



def test_catalog_engines_compute_wired(monkeypatch, tmp_path):
    import json
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    rec = {
        "recipes": [
            {
                "id": "flux2-turbo",
                "label": "Turbo",
                "kind": "t2i",
                "studio": True,
                "popup": "fast still",
                "workflow": "flux2-turbo-txt2img.json",
            },
            {
                "id": "ltx23-nvfp4",
                "label": "LTX",
                "kind": "t2v",
                "studio": True,
                "popup": "not wired",
                "workflow": None,
            },
        ]
    }
    recipes = tmp_path / "recipes.json"
    recipes.write_text(json.dumps(rec))
    wf = tmp_path / "wf"
    wf.mkdir()
    (wf / "flux2-turbo-txt2img.json").write_text("{}")
    monkeypatch.setattr(rs, "WF_DIR", wf)
    monkeypatch.setenv("KREW_COMFY_RECIPES", str(recipes))
    engines = rs.studio_engine_list()
    by = {e["id"]: e for e in engines}
    assert by["flux2-turbo"]["wired"] is True
    assert by["ltx23-nvfp4"]["wired"] is False
    assert "fast still" in by["flux2-turbo"]["popup"]


# The reference install's tests of its own render plugin, its profile packs and the Hermes image tool
# (test_cp_engine_row_includes_ltx25 .. test_ltx_flf2v_recipe_and_hub_params) are not here: krew's render plugin
# (plugins/krew-comfy) and its profiles are tested by their own files, and the hermes tool import needs hermes's
# interpreter.


def test_seed_if_empty_is_gone():
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    assert not hasattr(rs, "_seed_if_empty")


def test_welcome_and_snapshot_do_not_reseed_cycle_log(monkeypatch, tmp_path):
    import json
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("render\n")
    monkeypatch.setattr(rs, "STATE", tmp_path / "studio.json")
    monkeypatch.setattr(rs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(rs, "LISTEN_FLAG", tmp_path / "listen")
    (tmp_path / "flux2turbo_00008_.png").write_bytes(b"x")
    log = tmp_path / "cycle.jsonl"
    log.write_text(
        json.dumps({"still": str(tmp_path / "flux2turbo_00008_.png"), "prompt": "Friday supper"})
        + "\n"
    )
    monkeypatch.setattr(rs, "speak_lila", lambda t: None)
    monkeypatch.setattr(rs, "user_given_name", lambda payload=None: "")
    monkeypatch.setattr(rs, "silo_ok", lambda: (True, "ok"))
    rs.welcome_pin()
    st = rs._state()
    assert st.get("spec") == ""
    assert "Friday" not in (st.get("spec") or "")
    snap = rs.snapshot()
    assert snap["spec"] == ""
    assert snap["still"] is None


def test_ear_window_not_per_turn_vocative():
    import sys
    import time

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    now = time.time()
    wolf = "Hey, love. Hey, can you make a picture of a wolf in the evening under a moon howling?"
    assert rs.is_lila_open(wolf)
    assert rs.ear_reaches_lila(wolf, now=now, until=0)
    river = "On the river please now."
    assert not rs.is_lila_open(river)
    assert not rs.ear_reaches_lila(river, now=now, until=0)
    assert rs.ear_reaches_lila(river, now=now, until=now + 12)
    assert not rs.ear_reaches_lila(river, now=now + 13, until=now + 12)
    assert not rs.ear_reaches_lila("Hey Zoey what time is it there now", now=now, until=now + 12)
    assert rs.ear_reaches_lila("Now she'll hang it. Good, hang it.", now=now, until=0)


def test_ear_lila_turn_skips_unaddressed(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("render\n")
    monkeypatch.setattr(rs, "STATE", tmp_path / "studio.json")
    monkeypatch.setattr(rs, "silo_ok", lambda: (True, "ok"))
    rs._save({"spec": "", "lila_until": 0, "say": "Lila has the helm."})
    # 19a4fa34 (09-16) routed ear lines to her gateway before any check; restored
    # 2026-09-19 (_ear_turn_gate): the ear asks first, then she hears it live.
    d = rs.lila_turn("On the river please now.", source="ear")
    assert d.get("skipped") is True
    assert d.get("ok") is False


def test_hang_still_does_not_call_kanban(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("render\n")
    monkeypatch.setattr(rs, "STATE", tmp_path / "studio.json")
    monkeypatch.setattr(rs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(rs, "DELIVER_DIR", tmp_path / "krew-renders")
    (tmp_path / "ok.png").write_bytes(b"x")
    monkeypatch.setattr(rs, "silo_ok", lambda: (True, "ok"))
    monkeypatch.setattr(rs, "speak_lila", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(rs, "_kanban", lambda *a, **k: called.append(a) or {"ok": True})
    monkeypatch.setattr(rs, "ensure_card", lambda st: called.append("ensure") or st)
    rs._save({"still": str(tmp_path / "ok.png"), "spec": "wolf"})
    d = rs.hang_still("hang it")
    assert d["ok"] is True
    assert called == []
    assert (tmp_path / "krew-renders" / "latest.png").is_file()


def test_hang_still_copies_to_krew_renders_and_pins_wallpaper(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("render\n")
    monkeypatch.setattr(rs, "STATE", tmp_path / "studio.json")
    monkeypatch.setattr(rs, "OUT_DIR", tmp_path)
    deliver = tmp_path / "krew-renders"
    monkeypatch.setattr(rs, "DELIVER_DIR", deliver)
    (tmp_path / "wolf.png").write_bytes(b"y")
    monkeypatch.setattr(rs, "silo_ok", lambda: (True, "ok"))
    monkeypatch.setattr(rs, "speak_lila", lambda *a, **k: None)
    rs._save({"still": str(tmp_path / "wolf.png"), "spec": "wolf", "welcome": "flux2turbo_00008_.png"})
    d = rs.hang_still("hang it")
    assert d["ok"] is True
    assert d["hung"] is True
    assert (deliver / "latest.png").is_file()
    assert (deliver / "latest.png").read_bytes() == b"y"
    stamped = [p for p in deliver.glob("wolf_*.png")]
    assert stamped
    st = rs._state()
    assert st.get("wallpaper") == "wolf.png"
    assert st.get("welcome") == "wolf.png"
    snap = rs.snapshot()
    assert snap["welcome"] == "wolf.png"
    assert snap["welcome_url"] == "/render-still/wolf.png"




def _rs(monkeypatch, tmp_path):
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("render\n")
    monkeypatch.setattr(rs, "STATE", tmp_path / "studio.json")
    monkeypatch.setattr(rs, "OUT_DIR", tmp_path)
    monkeypatch.setattr(rs, "LISTEN_FLAG", tmp_path / "listen")
    monkeypatch.setattr(rs, "LANE_BUSY", tmp_path / "lane-busy")
    monkeypatch.setattr(rs, "silo_ok", lambda: (True, "ok"))
    return rs


def test_show_pick_answers_her_ask_or_an_explicit_show(monkeypatch, tmp_path):
    """the owner 2026-09-13: Wall or CP, his pick. "A vase on the wall" is a brief."""
    rs = _rs(monkeypatch, tmp_path)
    assert rs.show_pick("put a vase on the wall") == ""
    assert rs.show_pick("put a vase on the wall", pending=True) == ""
    assert rs.show_pick("The wall, please.") == ""
    assert rs.show_pick("The wall, please.", pending=True) == "wall"
    assert rs.show_pick("Control panel.", pending=True) == "panel"
    assert rs.show_pick("show it on the panel") == "panel"
    assert rs.show_pick("Show the renders on the wall") == "wall"


def test_an_unpicked_render_is_broadcast_on_the_panel_and_nobody_is_asked(monkeypatch, tmp_path):
    """the owner 2026-09-17: "the team's work default to this CP window on every
    iteration. Like their workspace broadcast for viewing." Lila holds the wall;
    the default neither asks, fronts a tab nor pins the panel. A pick still wins."""
    rs = _rs(monkeypatch, tmp_path)
    assert rs.SHOW_DEFAULT == "panel"
    (tmp_path / "a.png").write_bytes(b"x")
    spoken, fronted = [], []
    monkeypatch.setattr(rs, "speak_lila", lambda t, wait_previous=False: spoken.append(t))
    monkeypatch.setattr(rs, "_front_tab", lambda url: fronted.append(url) or "opened")
    monkeypatch.setattr(rs, "_glass", lambda: "wallpaper")
    monkeypatch.setattr(rs, "_open_ear", lambda: None)
    d = rs.present(still="a.png")
    assert d["on"] == "panel" and d["asked"] is False and spoken == [] and fronted == []
    assert rs.snapshot()["show"]["url"] == "/render-still/a.png"
    assert rs.panel_was_pinned() is False
    rs.pick_show("wall")
    assert rs.present(still="a.png")["on"] == "wall"


def test_present_asks_once_shows_on_the_wall_and_remembers_the_pick(monkeypatch, tmp_path):
    rs = _rs(monkeypatch, tmp_path)
    monkeypatch.setattr(rs, "SHOW_DEFAULT", "wall")        # the 09-13 behaviour: KREW_RENDER_SHOW_DEFAULT=wall
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.png").write_bytes(b"y")
    spoken, fronted, opened = [], [], []
    monkeypatch.setattr(rs, "speak_lila", lambda t, wait_previous=False: spoken.append(t))
    monkeypatch.setattr(rs, "_front_tab", lambda url: fronted.append(url) or "opened")
    monkeypatch.setattr(rs, "_glass", lambda: "wallpaper")
    monkeypatch.setattr(rs, "_open_ear", lambda: opened.append(1))
    d = rs.present(still="a.png")
    assert d["on"] == "wall" and d["asked"] is True
    assert spoken == [rs.SHOW_ASK] and opened == [1] and fronted == []
    assert rs.snapshot()["show"]["url"] == "/render-still/a.png"
    d2 = rs.present(still="b.png")                 # unanswered: the wall, no second ask
    assert d2["asked"] is False and d2["on"] == "wall" and spoken == [rs.SHOW_ASK]
    r = rs.lila_turn("Control panel.", source="ear-session")
    assert r["show_on"] == "panel"
    assert fronted == [rs.PANEL_URL]
    assert rs.panel_was_pinned() is True
    rs.leave_render()
    st = rs._state()
    assert st["show_on"] == "panel" and st["panel_pinned"] is False


def test_ear_duplicate_is_one_answer_but_a_bare_name_then_the_ask_is_two(monkeypatch, tmp_path):
    import time

    rs = _rs(monkeypatch, tmp_path)
    # the owner 2026-09-19: "skipping was not intended. Her live during render for speech
    # TO her is." A repeat is one answer; an unaddressed line never reaches her; a
    # "hey Zoey" DOES - "she can correct a hey Zoey. Let her do it vs scripted".
    rs._save({"spec": "", "last_user": "Lila, make it warmer", "last_user_ts": time.time()})
    assert rs.lila_turn("Lila, make it warmer.", source="ear").get("reason") == "duplicate"
    zoey = rs.lila_turn("Hey Zoey, make it warmer.", source="ear")
    assert not zoey.get("skipped") and zoey.get("reason") not in ("not-addressed", "duplicate")
    hers, why = rs.room_turn_is_hers("Hey Zoey, can you make it brighter?", {})
    assert hers and why == "zoey-vocative"
    assert rs.room_turn_is_hers("Can you make it brighter?", {}) == (False, "not-addressed")
    rs._save({"spec": "", "last_user": "Lila.", "last_user_ts": time.time()})
    monkeypatch.setattr(rs, "show_pick", lambda t, pending=False: "wall")    # stop before the model
    monkeypatch.setattr(rs, "pick_show", lambda on: {"ok": True, "picked": on})
    assert rs.lila_turn("Lila, make it warmer", source="ear-session").get("picked") == "wall"


def test_lila_corrects_the_wrong_name_and_the_helm_line_names_no_window():
    import sys

    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_studio as rs

    assert "call you Crissa" in rs.LILA_TALK
    assert "window" not in rs.orient_line("the owner")



def test_render_is_not_a_conversation_until_she_is_addressed(monkeypatch, tmp_path):
    """the owner 2026-09-17: "Lila takes them as real and keeps referencing the living
    room … make render non interactive unless she is addressed via text entry /
    mic button ON or hello lila." A room sentence is hers only with the Studio
    mic ON, her name on it, or inside the follow-up window; her own last line
    coming back off the speakers is an echo, never a turn."""
    rs = _rs(monkeypatch, tmp_path)
    assert rs.RENDER_ADDRESS_ONLY is True
    monkeypatch.setattr(rs, "LISTEN_FLAG", tmp_path / "render-studio-listen")
    st = {"say": "No, that's just the default wallpaper on the wall, not a job for Bash.", "lila_until": 0}
    now = 1000.0
    assert rs.room_turn_is_hers("What is this for? Is this for Bash's office?", st, now=now) == (False, "not-addressed")
    assert rs.room_turn_is_hers("make it a picture of the kitchen", st, now=now) == (False, "not-addressed"), \
        "the loose opener words (draw, picture, render) are not an address"
    assert rs.room_turn_is_hers("No, that's just the default wallpaper on the wall", st, now=now) == (False, "echo")
    for said in ("Hello Lila, warm it up.", "Hey Leila can you try again", "Lila, swap the sofa.", "ok lila go"):
        assert rs.room_turn_is_hers(said, st, now=now) == (True, "vocative"), said
    assert rs.room_turn_is_hers("and a little brighter", {"say": "", "lila_until": now + 5}, now=now) == (True, "window")
    assert rs.room_turn_is_hers("and a little brighter", {"say": "", "lila_until": now - 1}, now=now) == (False, "not-addressed")
    rs.LISTEN_FLAG.write_text("render-studio\n")                      # the Studio mic, by a click
    assert rs.room_turn_is_hers("and a little brighter", st, now=now) == (True, "mic")
    assert rs.room_turn_is_hers("No, that's just the default wallpaper on the wall", st, now=now) == (False, "echo"), \
        "even with the mic on she does not answer herself"
    assert rs.is_lila_vocative("Hey Zoey, tell Lila hello") is False



def test_render_entry_is_a_blank_log_and_paused(monkeypatch, tmp_path):
    """the owner 2026-09-19: "She is not to come into render expecting comms - just ready
    for them. She should now come in to a blank log and on paused." """
    import os
    import sqlite3
    import time

    rs = _rs(monkeypatch, tmp_path)
    posts: list[str] = []
    monkeypatch.setattr(rs, "_ear_post", lambda url: posts.append(url))
    monkeypatch.setattr(rs, "user_given_name", lambda payload=None: "the owner")
    monkeypatch.setattr(rs, "silo_ok", lambda: (True, "ok"))
    rs._save({"turns": [{"user": "old ask", "say": "old answer", "ts": time.time() - 3600}], "orient": False})
    rs.LISTEN_FLAG.write_text("render-studio\n")          # a crash left the Studio mic on
    db = tmp_path / "kanban.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, created_at REAL, author TEXT, body TEXT, task_id TEXT)")
    con.execute("CREATE TABLE tasks (id TEXT, assignee TEXT, title TEXT, status TEXT)")
    con.execute("INSERT INTO task_comments (created_at, author, body, task_id) VALUES (?, 'lila', 'from last Render', 't_old')", (time.time() - 3600,))
    con.execute("INSERT INTO tasks VALUES ('t_run', 'bash', 'a card running since before', 'running')")
    con.commit()
    (tmp_path / "logs").mkdir()
    old_log = tmp_path / "logs" / "t_run.log"
    old_log.write_text("an old line\n")
    os.utime(old_log, (time.time() - 3600, time.time() - 3600))
    monkeypatch.setattr(rs, "_board_db", lambda: db)

    d = rs.welcome_pin()
    assert d["ok"] is True and d["listen"] is False
    assert not rs.LISTEN_FLAG.exists() and rs.EAR_SESSION_PAUSE in posts, "paused on entry"
    assert rs.crew_feed() == [], "a blank log: no old turns, comments or card lines"

    con.execute("INSERT INTO task_comments (created_at, author, body, task_id) VALUES (?, 'vera', 'new this session', 't_new')", (time.time() + 1,))
    con.commit()
    con.close()
    old_log.write_text("an old line\nworking now\n")     # the running card speaks this session
    texts = [r["text"] for r in rs.crew_feed()]
    assert "new this session" in texts and "working now" in texts
    assert "from last Render" not in texts and "old answer" not in texts


# The reference install's Wall pages and feed (display/voiceonly-*.html, voiceonly-home-server.py) are the Crissa pack's; the tests that read them are
# not here: test_example_prompt_is_bread_table, test_wall_render_ground_is_gated, test_panel_has_studio_and_silo_copy, test_snapshot_keeps_welcome_when_session_still, test_server_passes_source, test_lila_log_is_the_entry_default_not_a_latch, test_entering_render_never_arms_the_mic
