"""Door-view check + the janitor's watcher (the User 2026-10-06). No camera, no presence, no board, no systemctl."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401  (the janitor's settings, in a temp folder)
sys.path.insert(0, str(ROOT / "janitor"))
import door_view_check as dv  # noqa: E402

AT_DOOR = {"at_door": True, "lease": None, "sweeping": False, "paused": False, "tool_hold": False,
           "door_home_pan": 432000, "firmware": {"frame_on": True, "track_on": False}}
DOOR_PIC = {"ok": True, "best": "door", "door": 285, "desk": 5, "chair": 20}
DESK_PIC = {"ok": True, "best": "desk", "door": 12, "desk": 240, "chair": 30}


def _lines(t0: float, n: int, who: str = "someone", refuse: bool = True) -> list:
    out = []
    for i in range(n):
        ts = t0 + i * 100
        out.append((ts, f"{ts} host python[1]: [door-glance] faces=1 names=['{who}']"))
        if refuse:
            out.append((ts + 1, f"{ts + 1} host python[1]: [face] refuse guest_until_secure {who} src=door_buffer sim=0.7"))
    return out


def test_mode_defaults_to_report():
    assert dv.mode({}) == "report" and dv.mode({"KREW_DOOR_VIEW_CHECK": "act"}) == "act"
    assert dv.mode({"KREW_DOOR_VIEW_CHECK": "off"}) == "off" and dv.mode({"KREW_DOOR_VIEW_CHECK": "x"}) == "report"


def test_the_picture_decides_door_or_not():
    assert not dv.picture_off_door(DOOR_PIC)
    assert dv.picture_off_door(DESK_PIC)
    assert dv.picture_off_door({"ok": True, "best": "door", "door": 60, "desk": 5})       # too few inliers
    assert not dv.picture_off_door({"ok": False})                                         # unreadable: no verdict


def test_this_morning_a_stuck_guest_in_the_door_view():
    t0 = 1_000_000.0
    p = dv.parse(_lines(t0, 6))
    assert dv.stuck_guest(p, t0 + 600) == "someone"
    granted = dv.parse(_lines(t0, 6) + [(t0 + 550, "x [face] AUTH grant someone src=beam_look sim=0.85")])
    assert dv.stuck_guest(granted, t0 + 600) == ""
    assert dv.stuck_guest(dv.parse(_lines(t0, 6, refuse=False)), t0 + 600) == ""
    assert dv.stuck_guest(dv.parse(_lines(t0, 2)), t0 + 600) == ""


def test_check_needs_the_camera_to_claim_the_door():
    busy = dict(AT_DOOR, lease={"owner": "beam_look"})
    assert dv.check(1.0, health=busy, lines=[], pic=DESK_PIC)["state"] == "ok"
    assert dv.check(1.0, health=AT_DOOR, lines=[], pic=DOOR_PIC)["state"] == "ok"
    r = dv.check(1.0, health=AT_DOOR, lines=[], pic=DESK_PIC)
    assert r["state"] == "suspect" and r["off_door"] and "not the door" in r["why"]
    t0 = 1_000_000.0
    r = dv.check(t0 + 600, health=AT_DOOR, lines=_lines(t0, 6), pic=DOOR_PIC)
    assert r["state"] == "suspect" and r["stuck_guest"] == "someone"


def test_rehome_moves_off_the_door_pan_first_and_always_ends_at_the_door():
    posts = []
    out = dv.rehome(AT_DOOR, post=lambda url, body, timeout=0: posts.append((url, body)) or {"ok": True, "pan_actual": 432000},
                    sleep=lambda s: None)
    assert posts[0][0].endswith("/v1/aim") and posts[0][1]["pan"] == 432000 - 3600
    assert posts[1][0].endswith("/v1/return_door") and posts[1][1] == {"force": True} and out["at_door"]

    def flaky(url, body, timeout=0):
        if url.endswith("/v1/aim"):
            raise OSError("nudge failed")
        flaky.n += 1
        return {"ok": flaky.n >= 2, "pan_actual": 432000 if flaky.n >= 2 else 0}
    flaky.n = 0
    out = dv.rehome(AT_DOOR, post=flaky, sleep=lambda s: None)
    assert out["nudge_error"] and out["tries"] == 2 and out["at_door"]       # the nudge failed, it still went home


def _presence(auth=False, secure=False, hold=False):
    return {"user": {"auth": auth}, "visit_lamp_info": {"secure": secure, "hold": hold}}


def test_act_never_asks_for_auth_when_secure_double_secure_or_auth_is_on_and_reports_a_drop():
    posts = []
    post = lambda url, body, timeout=0: posts.append(url) or {"ok": True, "pan_actual": 432000, "auth": False}
    sus = {"stuck_guest": "someone", "off_door": True}
    out = dv.act(sus, AT_DOOR, get=lambda url: _presence(hold=True), post=post, sleep=lambda s: None)
    assert "skipped" in out["auth"] and not any(u.endswith("ensure_user_auth") for u in posts)
    assert out["violations"] == [] and out["rehome"]["at_door"]
    posts.clear()
    out = dv.act(sus, AT_DOOR, get=lambda url: _presence(), post=post, sleep=lambda s: None)
    assert any(u.endswith("ensure_user_auth") for u in posts)
    seq = iter([_presence(auth=True, secure=True), _presence(auth=False, secure=True)])
    out = dv.act({"off_door": True}, AT_DOOR, get=lambda url: next(seq), post=post, sleep=lambda s: None)
    assert out["violations"] == ["auth"]


def test_ask_auth_is_a_face_check_never_a_grant():
    seen = []
    r = dv.ask_auth("someone", post=lambda url, body, timeout=0: seen.append((url, body)) or {"auth": False, "reason": "no_face"})
    assert seen[0][0].endswith("/v1/ensure_user_auth") and seen[0][1]["force_facefind"] is True
    assert seen[0][1]["voice_matched"] is False and r == {"auth": False, "reason": "no_face"}


def _janitor():
    spec = importlib.util.spec_from_file_location("hj", ROOT / "janitor" / "krew-janitor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeDV:
    def __init__(self, mode, result):
        self._mode, self._r, self.logged, self.acts = mode, result, [], []
        self.CAMERA = "http://cam"

    def mode(self):
        return self._mode

    def check(self, now):
        return self._r

    def _get(self, url):
        return AT_DOOR

    def act(self, r, h):
        self.acts.append("act"); return {"rehome": {"at_door": True}, "violations": []}

    def log(self, row):
        self.logged.append(row)


def test_janitor_reports_once_per_episode_and_acts_only_in_act(tmp_path):
    hj = _janitor()
    sus = {"state": "suspect", "why": "the picture is not the door", "off_door": True, "stuck_guest": "someone"}
    cards = []
    card = lambda title, body, key: cards.append((title, key)) or "t_00000001"
    st: dict = {}
    d = FakeDV("report", sus)
    out = hj.door_view_tick(st, 100.0, dv=d, card=card)
    assert out["red"] and out["card"] == "t_00000001" and d.acts == []
    hj.door_view_tick(st, 400.0, dv=d, card=card)
    assert len(cards) == 1                                      # one card per episode (cooldown)
    hj.door_view_tick(st, 700.0, dv=FakeDV("report", {"state": "ok", "why": "fine"}), card=card)
    assert "episode" not in st["door_view"]
    d2 = FakeDV("act", sus)
    hj.door_view_tick({}, 100.0, dv=d2, card=card)
    assert d2.acts == ["act"]
    assert hj.door_view_tick({}, 1.0, dv=FakeDV("off", sus), card=card) == {"mode": "off"}


def test_janitor_watches_its_watcher(tmp_path, monkeypatch):
    hj = _janitor()
    beat = tmp_path / "beat"
    monkeypatch.setattr(hj, "WATCH_BEAT", beat)
    monkeypatch.delenv("KREW_JANITOR_WATCH", raising=False)
    cards = []
    out = hj.watcher_tick({}, 1000.0, card=lambda t, b, k: cards.append(t) or "t_00000002")
    assert not out["ok"] and cards and "never beaten" in out["why"]
    beat.write_text("990")
    assert hj.watcher_tick({}, 1000.0, card=lambda *a: "")["ok"]


def _watch():
    spec = importlib.util.spec_from_file_location("jw", ROOT / "janitor" / "janitor-watch.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_janitor_watch_reads_the_last_tick_and_the_units(tmp_path):
    jw = _watch()
    day = date(2026, 10, 6)
    (tmp_path / "janitor-20261006.jsonl").write_text(json.dumps({"ts": "2026-10-06T09:00:00"}) + "\n")
    t = jw.last_tick(tmp_path, today=day)
    assert t is not None
    ok = {"timer": "active", "result": "success", "service": "inactive"}
    assert jw.verdict(t + 300, t, ok) == []
    assert "min ago" in jw.verdict(t + 3600, t, ok)[0]
    assert any("timer" in w for w in jw.verdict(t + 60, t, dict(ok, timer="inactive")))
    assert any("result" in w for w in jw.verdict(t + 60, t, dict(ok, result="exit-code")))
    assert jw.last_tick(tmp_path / "none", today=day) is None
    assert not jw.act_on({}) and jw.act_on({"KREW_JANITOR_WATCH_ACT": "1"})


def test_no_camera_answering_is_not_a_door_fault(monkeypatch):
    """krew: on a machine without Crissa the camera does not answer; the check says so and stays ok (no card)."""
    monkeypatch.setattr(dv, "CAMERA", "http://127.0.0.1:9")
    r = dv.check(1000.0, lines=[], pic={"ok": False})
    assert r["state"] == "ok" and "not reachable" in r["why"]
