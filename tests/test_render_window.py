"""The render window (the User 2026-09-17): renders batch into the User's off-hours, Ada
judges the batch, it runs only on a clear board and defers to the next night if not."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_window", ROOT / "crew" / "scripts" / "render-window.py")
rw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw)

KEY = "2026-09-17"
HOUR = 3600.0


def card(tid, status="ready", *, render=True, recipe="ltx", age_h=10, held=False, comments=(), body=""):
    c = {"id": tid, "title": f"{recipe} {tid}", "body": body, "status": status, "skills": "",
         "created_at": 1_000_000 - age_h * HOUR, "block_kind": "",
         "comments": [{"author": a, "body": b} for a, b in comments]}
    if held:
        c["status"] = "blocked"
        c["comments"].append({"author": "render-window", "body": rw.MARK + ": held"})
    rw.annotate([c])
    c["render"] = render
    c["recipe"] = recipe if render else ""
    c["est_min"] = rw.EST_MIN[recipe] if render else 0
    return c


def test_render_cards_are_recognised_including_the_gates_misses():
    # "Execute Comfy v0.36.0 swap" is NOT a mint title any more: 2026-09-23 (t_af53c50d)
    # the comfy title name needs a MINT verb in the title ("swap" is not one), and the
    # body match was retired 2026-09-17 (t_b5edfa11). The same card names its engine
    # in the title and the gate still catches it (pinned below: via krew-mode render).
    assert not rw.is_render_card("Execute Comfy v0.36.0 swap", "run krew-mode render turbo", "")
    assert rw.is_render_card("Render freelance chain: sat back", "pose-mint-flf.py", "")
    assert rw.is_render_card("x", "python3 scripts/render-submit.py --recipe ltx25-flf2v", "")
    assert not rw.is_render_card("drain the ready lane", "kanban only", "")
    assert rw.recipe_of("wan22-i2v take", "") == "wan" and rw.recipe_of("ltx25-flf2v", "") == "ltx"
    assert rw.recipe_of("Render freelance chain: x", "") == "pose-chain"


def test_a_card_that_only_mentions_a_render_is_not_a_render_card():
    # 2026-09-17: the steward sweep's own card and a pack-drift card were held for the night.
    sweep = "t_2db327e1  blocked   steward   Smoke test flux2-turbo hub\nt_23727de9  blocked   ada"
    assert not rw.is_render_card("steward sweep: blocked cards + stuck-card diagnostics", sweep, "")
    assert not rw.is_render_card("pack-drift: stand down residual crissa/config.yaml DRIFT",
                                 "re-run prints NO DRIFT line (krew-comfy already cleared)", "")
    # the miss the extra pattern was written for names its engine in the title: still held
    assert rw.is_render_card("Run flux2-turbo hub smoke (host hands)", "", "")
    assert rw.is_render_card("Execute Comfy v0.36.0 swap via krew-mode render turbo", "", "")


def test_a_misheld_card_is_released_only_when_the_windows_block_is_its_last():
    mis = card("t_mis", render=False, held=True)
    mis["last_block"] = rw.HOLD_REASON
    moved_on = card("t_inp", render=False, held=True)          # held, released, then needs_input
    moved_on["last_block"] = "needs_input: the measured delta contradicts the card"
    real = card("t_ren", held=True)
    real["last_block"] = rw.HOLD_REASON
    never = card("t_oth", "blocked", render=False)
    never["last_block"] = rw.HOLD_REASON                        # no hold comment: not ours
    assert rw.misheld([mis, moved_on, real, never]) == ["t_mis"]


def test_by_day_a_ready_render_card_is_held_and_render_now_is_exempt():
    cards = [card("t_a"), card("t_b", body="render-now: the User wants it"), card("t_c", render=False)]
    d = rw.decide({}, cards, in_window=False, key=KEY, left_min=0, chain=False, now=0)
    assert d["action"] == "idle" and d["hold"] == ["t_a"]


def test_the_slot_waits_for_the_evening_chain_then_ada_gets_the_batch():
    cards = [card("t_old", held=True, age_h=30), card("t_new", age_h=2)]
    assert rw.decide({}, cards, in_window=True, key=KEY, left_min=300, chain=False, now=0)["why"].startswith("the evening")
    d = rw.decide({}, cards, in_window=True, key=KEY, left_min=300, chain=True, now=0)
    assert d["action"] == "file-review"
    assert [c["id"] for c in d["plan"]["fit"]] == ["t_old", "t_new"], "every queued render, oldest first"


def test_the_plan_fits_the_window_and_carries_the_rest():
    cards = [card(f"t_{i}", held=True, recipe="wan", age_h=10 - i) for i in range(5)]   # 40 min each
    p = rw.plan_batch(cards, left_min=180)          # budget 180 - 30 - 5 - 20 = 125 → three wans
    assert len(p["fit"]) == 3 and len(p["carry"]) == 2 and p["est_min"] == 120


def test_ada_can_skip_reorder_defer_or_go():
    cards = [card("t_aaaaaaa1", held=True, age_h=20), card("t_aaaaaaa2", held=True, age_h=10)]
    st = {"window_key": KEY, "phase": "review", "review_until": 10_000}
    words = [{"author": "ada", "body": "skip t_aaaaaaa1\nfirst t_aaaaaaa2\ngo"}]
    d = rw.decide(st, cards, in_window=True, key=KEY, left_min=300, chain=True, now=0, ada_comments=words)
    assert d["action"] == "release" and [c["id"] for c in d["plan"]["fit"]] == ["t_aaaaaaa2"]
    d = rw.decide(st, cards, in_window=True, key=KEY, left_min=300, chain=True, now=0,
                  ada_comments=[{"author": "ada", "body": "defer — board is the priority tonight"}])
    assert d["action"] == "defer"
    d = rw.decide(st, cards, in_window=True, key=KEY, left_min=300, chain=True, now=0,
                  ada_comments=[{"author": "bash", "body": "go"}])
    assert d["action"] == "idle" and "reviewing" in d["why"], "only Ada / Crissa / the User rule the batch"


def test_silence_releases_only_on_a_clear_board_and_defers_past_the_latest_start():
    batch = [card("t_r", held=True)]
    busy = batch + [card("t_w1", "running", render=False), card("t_w2", "running", render=False)]
    st = {"window_key": KEY, "phase": "review", "review_until": 5}
    assert rw.decide(st, batch, in_window=True, key=KEY, left_min=200, chain=True, now=10)["action"] == "release"
    wait = rw.decide(st, busy, in_window=True, key=KEY, left_min=200, chain=True, now=10)
    assert wait["action"] == "idle" and "clear board" in wait["why"]
    late = rw.decide(st, busy, in_window=True, key=KEY, left_min=65, chain=True, now=10)   # one ltx still fits; latest start reached
    assert late["action"] == "defer" and "not clear" in late["why"]


def test_crissas_gate_queue_and_parks_are_not_board_load():
    cards = [card("t_g", "blocked", render=False), card("t_p", "blocked", render=False),
             card("t_w", "running", render=False), card("t_x", "ready", render=False), card("t_y", "ready", render=False)]
    assert rw.board_load(cards)["clear"] is True
    cards.append(card("t_z", "ready", render=False))
    assert rw.board_load(cards)["clear"] is False


def test_the_stop_line_holds_unstarted_renders_and_a_new_window_starts_fresh():
    cards = [card("t_s", "ready"), card("t_run", "running")]
    st = {"window_key": KEY, "phase": "released", "released": ["t_s", "t_run"]}
    d = rw.decide(st, cards, in_window=True, key=KEY, left_min=25, chain=True, now=0)
    assert d["action"] == "stop-line" and d["hold"] == ["t_s"]
    assert rw.decide(st, cards, in_window=True, key=KEY, left_min=200, chain=True, now=0)["hold"] == [], \
        "a released card is not held again inside its own slot"
    nxt = rw.decide(st, cards, in_window=False, key="2026-09-18", left_min=0, chain=False, now=0)
    assert nxt["hold"] == ["t_s"]


def test_chain_done_reads_the_window_tickers_verdicts():
    st = {"window_key": KEY, "fired": {"pose-freelance": {"status": "ok"}, "improvement-outcomes": {"status": "started"}}}
    assert rw.chain_done(st, KEY) is False
    st["fired"]["improvement-outcomes"]["status"] = "failed: result=exit-code exit=1"
    assert rw.chain_done(st, KEY) is True and rw.chain_done(st, "2026-09-18") is False
    assert rw.chain_done({"window_key": KEY, "fired": {}}, KEY) is False  # nothing fired


# --- the daytime slot (the User, 2026-09-22) ---------------------------------------------------
# Render cards could only run 20:00-02:00 — 18 hours a day closed. That gate turned ONE change
# (Comfy v0.36.0 -> v0.37.0: swap + smoke) into 28 cards over three days and the single
# NEEDS HUMAN card on the board, and it is why the User pressed Standard by hand at 17:00.

def test_outside_the_window_still_waits_when_the_krew_is_not_idle():
    cards = [card("t_r1")]
    d = rw.decide({}, cards, in_window=False, key=KEY, left_min=0, chain=False, now=0, day_ok=False)
    assert d["action"] == "idle" and "off-hours" in d["why"]


def test_the_day_slot_files_a_review_then_releases_on_silence():
    """Same 'silence means go' review the night uses — the only self-clearing timeout in
    the tree. It is copied, not reinvented."""
    cards = [card("t_r1")]
    d = rw.decide({}, cards, in_window=False, key=KEY, left_min=600, chain=False, now=0, day_ok=True)
    assert d["action"] == "file-review" and d.get("day") is True
    st = {"window_key": KEY, "phase": "review", "review_until": 100.0}
    assert rw.decide(st, cards, in_window=False, key=KEY, left_min=600, chain=False,
                     now=50, day_ok=True)["action"] == "idle"          # still reviewing
    late = rw.decide(st, cards, in_window=False, key=KEY, left_min=600, chain=False,
                     now=200, day_ok=True)
    assert late["action"] == "release" and late.get("day") is True


def test_the_day_slot_takes_one_card_and_carries_the_rest():
    """A batch is what the night is for. The day takes one."""
    cards = [card("t_r1"), card("t_r2"), card("t_r3")]
    st = {"window_key": KEY, "phase": "review", "review_until": 0.0}
    d = rw.decide(st, cards, in_window=False, key=KEY, left_min=600, chain=False, now=10, day_ok=True)
    assert d["action"] == "release"
    assert len(d["plan"]["fit"]) == rw.DAY_SLOT_MAX_CARDS == 1
    assert len(d["plan"]["carry"]) == 2


def test_the_day_slot_waits_for_a_clear_board_like_the_night_does():
    cards = [card("t_r1"), card("t_busy", render=False, status="running"),
             card("t_b2", render=False, status="running")]
    d = rw.decide({}, cards, in_window=False, key=KEY, left_min=600, chain=False, now=0, day_ok=True)
    assert d["action"] == "idle" and "board not clear" in d["why"]


def test_the_day_slot_does_not_run_twice_in_one_day():
    cards = [card("t_r1")]
    st = {"window_key": KEY, "phase": "released"}
    d = rw.decide(st, cards, in_window=False, key=KEY, left_min=600, chain=False, now=0, day_ok=True)
    assert d["action"] == "idle" and "already released" in d["why"]


def test_the_batch_body_says_which_slot_it_is_for():
    """The review card names its slot (2026-09-24, t_ef35157e): Ada read a review whose
    plan was one card against an explicit 600-min budget and the body read "batched in the
    User's off-hours" — night's language on a day slot. The key is the same fact the state
    file carries; a day key ends in -day, and the body must say so."""
    load = {"running": 0, "ready": 0, "clear": True}
    day = rw.batch_body("2026-09-24-day", {"fit": [card("t_r1")], "carry": [], "est_min": 10,
                                           "budget_min": 600, "latest_start_left": 100},
                        load, left_min=600)
    assert "(DAY slot)" in day.splitlines()[0]
    night = rw.batch_body("2026-09-24", {"fit": [card("t_r1")], "carry": [], "est_min": 10,
                                         "budget_min": 360, "latest_start_left": 100},
                          load, left_min=360)
    assert "(NIGHT slot)" in night.splitlines()[0]


def test_the_day_slot_needs_positive_evidence_of_an_empty_room(monkeypatch, tmp_path):
    """Every fact fails to NO. An empty `people` list is not evidence on its own — a closed,
    blind or stale camera also reports nobody, and a broken probe must never silently
    license renders all day. (crissa-needs-watch has the OPPOSITE polarity on purpose: it is
    deciding whether to speak, and silence is the safe side there.)"""
    mode = tmp_path / "gpu-mode"; mode.write_text("normal\n")
    monkeypatch.setattr(rw, "GPU_MODE_FILE", mode)
    monkeypatch.setattr(rw, "GPU_HOLD_FILE", tmp_path / "no-hold")
    monkeypatch.setattr(rw, "DAY_SLOT_ON", True)
    monkeypatch.setattr(rw.krew_hours, "quiet_hours", lambda: {"window": "20:00-02:00"})
    import datetime as _dt
    noon = _dt.datetime(2026, 9, 22, 12, 0)

    def presence(body):
        class _R:
            def read(self): return __import__("json").dumps(body).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(rw.urllib.request, "urlopen", lambda *a, **k: _R())

    good = {"camera_open": True, "camera_blind": False, "frame_age_sec": 0.1,
            "headcount": 0, "people": []}
    presence(good)
    assert rw.day_slot_ok(now=noon)[0] is True
    for bad, expect in (({**good, "camera_open": False}, "closed or blind"),
                        ({**good, "camera_blind": True}, "closed or blind"),
                        ({**good, "frame_age_sec": 9999}, "old"),
                        ({**good, "frame_age_sec": None}, "no frame age"),
                        ({**good, "headcount": 1}, "person(s) in the room"),
                        ({**good, "people": [{"name": "user"}]}, "the User is in the room")):
        presence(bad)
        ok, why = rw.day_slot_ok(now=noon)
        assert ok is False and expect in why, (bad, why)
    # an unreachable service is "someone may be here", never "go"
    monkeypatch.setattr(rw.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    assert rw.day_slot_ok(now=noon) == (False, "presence unreadable (OSError)")


def test_the_day_slot_refuses_render_mode_a_hold_and_quiet_hours(monkeypatch, tmp_path):
    import datetime as _dt
    mode = tmp_path / "gpu-mode"; hold = tmp_path / "hold"
    monkeypatch.setattr(rw, "GPU_MODE_FILE", mode)
    monkeypatch.setattr(rw, "GPU_HOLD_FILE", hold)
    monkeypatch.setattr(rw, "DAY_SLOT_ON", True)
    monkeypatch.setattr(rw.krew_hours, "quiet_hours", lambda: {"window": "20:00-02:00"})
    noon = _dt.datetime(2026, 9, 22, 12, 0)
    mode.write_text("render\n")
    assert "not Standard" in rw.day_slot_ok(now=noon)[1]
    mode.write_text("normal\n")
    hold.write_text("# unit ops.service card=t_x\n")
    assert "GPU hold is live" in rw.day_slot_ok(now=noon)[1]
    hold.unlink()
    assert "quiet hours" in rw.day_slot_ok(now=_dt.datetime(2026, 9, 22, 22, 0))[1]
    monkeypatch.setattr(rw, "DAY_SLOT_ON", False)
    assert rw.day_slot_ok(now=noon) == (False, "KREW_RENDER_DAY_SLOT=0")
