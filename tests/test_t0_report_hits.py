"""The report-only day's reader lists one rule's hits and nothing else (the User 2026-09-30)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("t0_report_hits", ROOT / "crew" / "scripts" / "t0-report-hits.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def _log(tmp_path, rows):
    p = tmp_path / "hits.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    return p


def test_lists_only_the_route_since_the_time(tmp_path):
    log = _log(tmp_path, [
        {"ts": "2026-09-30T14:00:00", "route": "window", "reason": "unit stop", "profile": "bash", "mode": "report"},
        {"ts": "2026-09-30T15:00:00", "route": "window", "reason": "unit stop", "profile": "bash", "mode": "report",
         "tool": "terminal", "snippet": "python3 crew-shot.py | x"},
        {"ts": "2026-09-30T15:05:00", "route": "zoey", "reason": "other", "profile": "bash", "mode": "enforce"},
    ])
    out = tmp_path / "day.md"
    rc = m.main(["--route", "window", "--since", "2026-09-30 14:10", "--log", str(log), "--out", str(out)])
    text = out.read_text()
    assert rc == 1 and "1 hit(s)" in text and "crew-shot.py \\| x" in text and "other" not in text


def test_no_hits_and_no_log(tmp_path):
    log = _log(tmp_path, [])
    assert m.main(["--route", "window", "--since", "2026-09-30 14:10", "--log", str(log)]) == 0
    assert m.main(["--route", "window", "--since", "2026-09-30 14:10", "--log", str(tmp_path / "missing")]) == 2


def test_the_review_timer_is_a_one_shot_that_writes_the_checkpoint():
    # krew: the reference install fired a dated one-shot the day after arming; krew's unit reads the day from
    # KREW_T0_REVIEW_SINCE (a drop-in) on a weekly Persistent timer, and writes under $KREW_HOME/docs
    svc = (ROOT / "crew/units/krew-t0-unit-control-review.service").read_text()
    assert "--route window" in svc and "--since-env KREW_T0_REVIEW_SINCE" in svc and "t0-unit-control-review.md" in svc
    timer = (ROOT / "crew/units/krew-t0-unit-control-review.timer").read_text()
    assert "OnCalendar=" in timer and "Persistent=true" in timer


def test_no_since_is_nothing_to_review(tmp_path, monkeypatch):
    log = _log(tmp_path, [{"ts": "2026-09-30T15:00:00", "route": "window", "reason": "unit stop"}])
    monkeypatch.delenv("KREW_T0_REVIEW_SINCE", raising=False)
    assert m.main(["--route", "window", "--since-env", "KREW_T0_REVIEW_SINCE", "--log", str(log)]) == 0
    monkeypatch.setenv("KREW_T0_REVIEW_SINCE", "2026-09-30 14:10")
    assert m.main(["--route", "window", "--since-env", "KREW_T0_REVIEW_SINCE", "--log", str(log)]) == 1


def test_safe_root_refusals_fold_into_the_zoey_review(tmp_path):
    """10-01: the allow list stays until enforce, so its refusals belong in the same review."""
    import sqlite3
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (role TEXT, tool_name TEXT, content TEXT, timestamp REAL)")
    import datetime as dt
    old, new = dt.datetime(2026, 10, 1, 1).timestamp(), dt.datetime(2026, 10, 1, 3).timestamp()
    deny = '{"error": "Write denied: \'~/Desktop/x.txt\' is outside HERMES_WRITE_SAFE_ROOT (/a)"}'
    con.executemany("INSERT INTO messages VALUES (?,?,?,?)", [
        ("tool", "write_file", deny, old), ("tool", "write_file", deny, new), ("tool", "read_file", "fine", new)])
    con.commit(); con.close()
    out = tmp_path / "day.md"
    rc = m.main(["--route", "zoey-kit", "--since", "2026-10-01 02:15", "--log", str(_log(tmp_path, [])),
                 "--out", str(out), "--safe-root-db", str(db)])
    text = out.read_text()
    assert rc == 1 and "1 refusal(s)" in text and "Desktop/x.txt" in text
    svc = (ROOT / "crew/units/krew-t0-zoey-kit-review.service").read_text()
    assert "--safe-root-db %h/.hermes/profiles/zoey/state.db" in svc
