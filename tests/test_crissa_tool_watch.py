"""The front door's tool-call watch (crew/lib/crissa_tool_watch.py; the User 2026-10-02)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import crissa_tool_watch as w  # noqa: E402


def test_failure_is_read_from_shape_not_words():
    assert w.classify('{"success": true, "content": "how to recover from an error, failed calls"}') == ""
    assert w.classify("14 open · done 83 ✓ t_04a76f0a done steward M&S failed overnight") == ""
    assert w.classify('{"error": "Cannot read binary file"}') == "error"
    assert w.classify('{"success": false, "error": "Unknown action \'search\'"}') == "bad_args"
    assert w.classify("Tool 'terminal' does not exist. Available tools: ...") == "error"
    assert w.classify("HANDS DID NOT FINISH. ...") == "did_not_finish"
    assert w.classify("Hands hit the time cap. Ask me to try a smaller piece.") == "did_not_finish"


def rows(n, fails, ts):
    return [{"ts": ts, "tool": "x", "failed": i < fails, "retry": False} for i in range(n)]


def test_jump_needs_volume_factor_and_floor():
    now = 1_000_000.0
    base = rows(100, 8, now - 86400)
    assert w.rates(base + rows(12, 6, now - 60), now)["jump"]          # 50 % vs 8 %
    assert not w.rates(base + rows(5, 5, now - 60), now)["jump"]       # too few calls
    assert not w.rates(base + rows(20, 3, now - 60), now)["jump"]      # 15 % vs 8 %: under the floor


def test_cards_are_report_only_by_default(monkeypatch):
    monkeypatch.delenv("KREW_TOOL_WATCH_CARDS", raising=False)
    assert not w.cards_on()


def test_drill_runs_are_left_out(tmp_path):
    # krew: the asks to leave out are $KREW_HOME/tool-watch-exclude.json ({"asks": [...]}); the house's drill file
    # shape ({"tasks": [{"ask": ...}]}) is read too
    ex = tmp_path / "exclude.json"
    ex.write_text('{"asks": ["show me the board", "%s"]}' % ("x" * 120))
    asks = w.drill_asks(ex)
    assert asks and all(len(a) <= 80 for a in asks)
    ex.write_text('{"tasks": [{"ask": "what is on my list"}]}')
    assert w.drill_asks(ex) == ["what is on my list"]
    assert w.drill_asks(tmp_path / "none.json") == []


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("KREW_TOOL_WATCH", "0")
    assert w.tick() == {"skipped": "off"}
