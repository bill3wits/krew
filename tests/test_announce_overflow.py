"""Announcer lines dropped unspoken: the Daily counts and shows them, more than five in an hour files one card
(the User 2026-09-30). Tmp files and a fake `hermes` only."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours
import announce_overflow as ao  # noqa: E402
import krew_daily as hd  # noqa: E402

NOW = dt.datetime(2026, 9, 30, 11, 0, 0)


def _dump(tmp: Path, rows: list[tuple[str, str, str]]) -> Path:
    """(HH:MM, reason, title) -> a dump file."""
    p = tmp / "announce-dump.jsonl"
    out = []
    for hhmm, reason, title in rows:
        t = dt.datetime.combine(NOW.date(), dt.time(*map(int, hhmm.split(":")))).timestamp()
        out.append({"ts": t - 300, "dump_ts": t, "category": "completion", "title": title,
                    "summary": f"Closed the finding for {title}. " * 3, "reason": reason})
    p.write_text("".join(json.dumps(r) + "\n" for r in out) + "not json\n")
    return p


BURST = [("10:32", "overflow", "fix, five red tests"), ("10:33", "overflow", "steward sweep"),
         ("10:35", "overflow", "M S Hermes security, HIGH"), ("10:35", "overflow", "M S Hermes security, HIGH"),
         ("10:37", "overflow", "M S Hermes security, HIGH"), ("10:38", "overflow", "M S Hermes security, HIGH")]


def test_only_overflow_rows_count_and_a_bad_line_is_skipped(tmp_path):
    p = _dump(tmp_path, [("01:31", "quiet_or_hold", "steward sweep"), *BURST, ("10:40", "mic_busy:mic busy", "x")])
    rows = ao.drops(0, p)
    assert len(rows) == 6 and rows[0]["title"] == "fix, five red tests"
    assert ao.worst_hour(rows)[0] == 6
    assert ao.line_of(rows[0]).startswith("10:32 fix, five red tests - Closed the finding")
    assert ao.drops(0, tmp_path / "missing.jsonl") == []


def test_the_worst_hour_is_a_rolling_hour_not_a_clock_hour(tmp_path):
    p = _dump(tmp_path, [("09:40", "overflow", "a"), ("09:50", "overflow", "b"), ("10:10", "overflow", "c"),
                         ("10:30", "overflow", "d"), ("11:45", "overflow", "e")])
    n, at = ao.worst_hour(ao.drops(0, p))
    assert n == 4 and dt.datetime.fromtimestamp(at).strftime("%H:%M") == "09:40"


class Hermes:
    def __init__(self, rc=0, out="Created t_0a1b2c3d (todo)"):
        self.calls, self.rc, self.out = [], rc, out

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, stdout=self.out, stderr="")


def test_more_than_five_in_an_hour_files_one_card_that_day(tmp_path):
    p, state, h = _dump(tmp_path, BURST), tmp_path / "state.json", Hermes()
    now = NOW.timestamp()
    assert ao.maybe_file_card(now, path=p, state=state, run=h) == "t_0a1b2c3d"
    (cmd,) = h.calls
    assert cmd[1:5] == ["-p", "ada", "kanban", "create"] and "6 lines in an hour" in cmd[5]
    body = cmd[cmd.index("--body") + 1]
    assert body.count("\n- ") == 6 and "10:32 fix, five red tests" in body          # the dropped text is in the card
    assert "source:" in body and "cause:" in body and "evidence:" in body
    assert cmd[cmd.index("--idempotency-key") + 1] == "announcer-overflow:2026-09-30"
    # a minute later, and with more drops: still the one card today
    assert ao.maybe_file_card(now + 60, path=p, state=state, run=h) == "" and len(h.calls) == 1


def test_five_in_an_hour_is_not_a_card_and_old_drops_do_not_count(tmp_path):
    h = Hermes()
    p = _dump(tmp_path, BURST[:5])
    assert ao.maybe_file_card(NOW.timestamp(), path=p, state=tmp_path / "s.json", run=h) == "" and h.calls == []
    p = _dump(tmp_path, BURST)                                   # six, but the check runs two hours later
    assert ao.maybe_file_card(NOW.timestamp() + 7200, path=p, state=tmp_path / "s.json", run=h) == "" and h.calls == []


def test_a_failed_filing_is_retried_and_the_kill_switch_files_nothing(tmp_path, monkeypatch):
    p, state = _dump(tmp_path, BURST), tmp_path / "state.json"
    assert ao.maybe_file_card(NOW.timestamp(), path=p, state=state, run=Hermes(rc=1, out="board locked")) == ""
    assert not state.exists()                                    # nothing recorded: the next minute tries again
    assert ao.maybe_file_card(NOW.timestamp() + 60, path=p, state=state, run=Hermes()) == "t_0a1b2c3d"
    monkeypatch.setattr(ao, "CARD_ON", False)
    h = Hermes()
    assert ao.maybe_file_card(NOW.timestamp(), path=p, state=tmp_path / "other.json", run=h) == "" and h.calls == []


# ---------------------------------------------------------------- the House Daily row
def _slip(tmp_path, rows):
    s = hd.Sources(announce_dump=_dump(tmp_path, rows), announce_card=tmp_path / "card.json", inbox=tmp_path / "inbox")
    return hd.a_announcer(NOW, dt.datetime(2026, 9, 30, 2, 0, 0), s)


def test_the_daily_shows_the_count_and_the_dropped_text(tmp_path):
    sl = _slip(tmp_path, [("01:31", "overflow", "before the day began"), *BURST])
    assert sl["routine"] == "announcer" and sl["status"] == "bad"          # six in one hour is over the line
    (b,) = sl["broken"]
    assert b["what"] == "The announcer dropped 6 lines it never spoke."
    assert "worst hour 6 from" in b["evidence"] and "10:32" in b["evidence"]
    assert "no card on file for it" in b["evidence"]                     # the page never assumes a card exists
    (tmp_path / "card.json").write_text(json.dumps({"day": "2026-09-30", "card": "t_0a1b2c3d"}))
    assert "(card t_0a1b2c3d)" in _slip(tmp_path, BURST)["broken"][0]["evidence"]
    assert b["evidence"].count(" M S Hermes security, HIGH - ") == 4 and "before the day began" not in b["evidence"]
    assert b["evidence"].split("Dropped: ")[1].count(" | ") == 5        # six dropped lines, each with its text
    got = {f["k"]: f["v"] for f in sl["facts"]}
    assert got["announcer.overflow_drops"] == 6 and got["announcer.overflow_worst_hour"] == 6


def test_a_few_drops_are_a_warning_and_none_is_fine(tmp_path):
    sl = _slip(tmp_path, BURST[:2])
    assert sl["status"] == "warn" and "dropped 2 lines" in sl["broken"][0]["what"] and "a card" not in sl["broken"][0]["evidence"]
    sl = _slip(tmp_path, [("09:00", "quiet_or_hold", "held, not dropped")])
    assert sl["status"] == "ok" and sl["broken"] == [] and sl["headline"].endswith("none dropped for a full queue")


def test_a_missing_dump_file_is_unknown_never_fine(tmp_path):
    sl = hd.a_announcer(NOW, NOW - dt.timedelta(hours=9),
                        hd.Sources(announce_dump=tmp_path / "nope.jsonl", announce_card=tmp_path / "card.json",
                                   inbox=tmp_path / "inbox"))
    assert sl["status"] == "unknown" and sl["unverified"]


def test_the_row_is_on_the_page_and_the_drain_checks_each_minute(tmp_path):
    assert hd.a_announcer in hd.ADAPTERS
    src = (ROOT / "crew" / "scripts" / "krew-announce-drain.py").read_text()
    assert "announce_overflow.maybe_file_card(now)" in src and "_overflow_card(overflow)" in src
    assert "now - state.get(\"at\", 0.0) < 60.0" in src
