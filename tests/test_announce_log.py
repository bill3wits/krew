"""Every announcer line in a daily file the House Daily links to; a roundup when more than 3 wait; the queue is 20
deep (the User 2026-09-30, "I want to hear more of what kanban does, to learn from it"). Tmp files only."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours
import announce_log as al  # noqa: E402


@pytest.fixture
def world(tmp_path, monkeypatch):
    import announce_queue as aq
    monkeypatch.setattr(al, "DIR", tmp_path / "inbox" / "announcer")
    monkeypatch.setattr(aq, "QUEUE", tmp_path / "queue.jsonl")
    monkeypatch.setattr(aq, "DUMP_LOG", tmp_path / "dump.jsonl")
    monkeypatch.setattr(aq, "LOCK", tmp_path / "lock")
    return tmp_path, aq


def _drain(monkeypatch, said):
    spec = importlib.util.spec_from_file_location("krew_announce_drain", ROOT / "crew" / "scripts" / "krew-announce-drain.py")
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)

    def announce(text, **kw):
        said.append((text, kw))
        return {"ok": True}
    monkeypatch.setattr(d, "announce", announce)
    # never the live crew from a test: the plain-sentence rewrite says the old line here
    monkeypatch.setattr(d.announce_rewrite, "plain_line", lambda item, fallback: (fallback, "fallback:test"))
    return d


def test_a_line_is_one_tab_separated_row_a_person_can_read(world):
    tmp, _aq = world
    at = dt.datetime(2026, 9, 30, 13, 7, 52).timestamp()
    al.record("spoken", "Done. Fix\\tfive red tests.\\n", category="completion", reason="zoeyos-player", ts=at)
    al.record("skipped", "Stuck. steward sweep", category="stuck", reason="mic_busy_after_synth:ear", ts=at + 1)
    text = al.path_for(dt.date(2026, 9, 30)).read_text()
    assert text.splitlines()[0] == "13:07:52\tspoken\tcompletion\tzoeyos-player\tDone. Fix\\tfive red tests.\\n"
    rows = al.read(dt.date(2026, 9, 30))
    assert [r["event"] for r in rows] == ["spoken", "skipped"] and rows[1]["reason"] == "mic_busy_after_synth:ear"


def test_the_log_never_raises_and_has_a_kill(world, monkeypatch):
    tmp, _aq = world
    monkeypatch.setattr(al, "DIR", tmp / "a-file")
    (tmp / "a-file").write_text("not a folder")
    al.record("spoken", "x")                                      # unwritable: silently nothing
    monkeypatch.setattr(al, "DIR", tmp / "inbox" / "announcer")
    monkeypatch.setattr(al, "ON", False)
    al.record("spoken", "x")
    assert not (tmp / "inbox" / "announcer").exists()


def test_the_queue_is_20_deep_and_a_dropped_line_is_logged(world):
    tmp, aq = world
    assert aq.MAX_QUEUE == 20
    for i in range(22):
        aq.enqueue(category="completion", title=f"card {i}", summary="done")
    assert aq.depth() == 20
    dropped = [r for r in al.read(dt.date.today()) if r["event"] == "dropped"]
    assert [r["text"] for r in dropped] == ["card 0. done", "card 1. done"] and dropped[0]["reason"] == "overflow"


def test_more_than_three_waiting_is_one_roundup_and_each_line_is_logged(world, monkeypatch):
    tmp, aq = world
    for t, c in [("M S Hermes security, HIGH", "completion")] * 4 + [("fix five red tests", "completion"),
                                                                    ("steward sweep blocked cards", "stuck")]:
        aq.enqueue(category=c, title=t, summary="details")
    said = []
    d = _drain(monkeypatch, said)
    assert d._roundup() is True and aq.depth() == 0
    ((line, kw),) = said
    assert line.startswith("Kanban roundup, 6 updates. Stuck: steward sweep blocked cards. Done: M S Hermes security HIGH, times 4")
    assert "fix five red tests" in line and len(line) <= 160 and kw["wait_mic"] is False
    folded = [r for r in al.read(dt.date.today()) if r["event"] == "summarised"]
    assert len(folded) == 6 and folded[0]["reason"] == "folded into a roundup of 6"


def test_three_or_fewer_waiting_are_said_one_at_a_time(world, monkeypatch):
    tmp, aq = world
    for i in range(3):
        aq.enqueue(category="completion", title=f"card {i}", summary="done")
    said = []
    d = _drain(monkeypatch, said)
    assert d._roundup() is False and said == [] and aq.depth() == 3
    d._say(aq.pop_one())
    assert said[0][0] == "Done. card 0. done"


def test_a_long_roundup_says_how_many_it_left_out(world):
    spec = importlib.util.spec_from_file_location("d2", ROOT / "crew" / "scripts" / "krew-announce-drain.py")
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    items = [{"category": "completion", "title": f"distinct card number {i} with a long title"} for i in range(15)]
    line = d.summary_line(items)
    assert line.startswith("Kanban roundup, 15 updates. Done: ") and len(line) <= 160
    told = line.count("distinct card number")
    assert line.endswith(f"And {15 - told} more.") and told >= 2


def test_the_roundup_has_a_kill(world, monkeypatch):
    tmp, aq = world
    for i in range(6):
        aq.enqueue(category="completion", title=f"card {i}")
    said = []
    d = _drain(monkeypatch, said)
    monkeypatch.setattr(d, "ROUNDUP_ON", False)
    assert d._roundup() is False and aq.depth() == 6


def test_every_announce_call_leaves_a_line(world, monkeypatch):
    import krew_announce as ha
    monkeypatch.setattr(ha, "_announce", lambda text, **kw: {"ok": False, "skipped": True, "reason": "quiet_hours"})
    ha.announce("Done. The board is quiet.", category="completion")
    monkeypatch.setattr(ha, "_announce", lambda text, **kw: {"ok": True, "engine": "zoeyos-player"})   # krew: the player speaks it
    ha.announce("Stuck. steward sweep.", category="stuck")
    monkeypatch.setattr(ha, "_announce", lambda text, **kw: {"ok": False, "error": "short_wav:44"})
    ha.announce("Done. x.", category="completion")
    rows = al.read(dt.date.today())
    assert [(r["event"], r["reason"]) for r in rows] == [("skipped", "quiet_hours"), ("spoken", "zoeyos-player"),
                                                          ("failed", "short_wav:44")]


def test_the_daily_links_the_file_and_counts_it(world, tmp_path):
    import krew_daily as hd
    tmp, _aq = world
    now = dt.datetime.now()
    al.record("spoken", "Done. a.", category="completion")
    al.record("summarised", "Done. b.", category="completion", reason="folded into a roundup of 4")
    s = hd.Sources(inbox=tmp / "inbox", announce_dump=tmp / "dump.jsonl", announce_card=tmp / "card.json")
    (tmp / "dump.jsonl").write_text("")
    sl = hd.a_announcer(now, now - dt.timedelta(hours=8), s)
    assert sl["headline"].startswith("today: 1 spoken, 1 summarised")
    src = (ROOT / "crew" / "lib" / "krew_daily.py").read_text()
    assert '"announcer_log": f"announcer/announcer-{now:%Y-%m-%d}.log"' in src
    assert '<a href="{_e(m["announcer_log"])}">' in src
