"""An env promoted to green and then rolled back twice in a row raises a card to the User;
M&S never demotes it on its own (the User 09-29)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_spec = importlib.util.spec_from_file_location("ms_promo_alert", ROOT / "ms" / "scripts" / "ms-trail-run.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

ROLLED = {"ok": False, "upgraded": [], "changed_n": 0, "failed": ["six"],
          "outcomes": [{"package": "six", "outcome": "rolled-back"}],
          "err": "smoke RED after upgrade — rolled back to pre-freeze pins"}
LANDED = {"ok": True, "upgraded": ["six 1.16.0→1.17.0"], "changed_n": 1, "failed": [], "outcomes": []}


@pytest.fixture
def promoted(monkeypatch, tmp_path):
    monkeypatch.setitem(ms._S.SETTINGS, "envs", {"hs": {"promote": {"after_clean_nights": 7}}})
    monkeypatch.setitem(ms.SMOKE_IMPORT, "hs", {"code": "pass"})
    path = tmp_path / "promotions.json"
    path.write_text(json.dumps({"hs": {"clean_nights": 7, "needed": 7, "promoted_on": "2026-10-07"}}))
    monkeypatch.setattr(ms, "PROMOTIONS_PATH", path)
    monkeypatch.setattr(ms, "PROMOTED_ROLLBACKS_ALERT", 2)
    sent = []
    monkeypatch.setattr(ms, "raise_promotion_alert", lambda name, row, day: sent.append((name, day)) or "stub")
    return sent


def night(applied: dict | None, day: str) -> dict:
    rep = {"envs": {"hs": {"smoke_baseline": True}}, "applied": {"hs": applied} if applied else {}}
    ms.update_promotions(rep, day)
    return rep


def test_two_rollbacks_in_a_row_raise_one_alert_and_never_demote(promoted):
    night(ROLLED, "2026-10-08")
    assert promoted == [] and ms.load_promotions()["hs"]["rollbacks_in_row"] == 1
    night(None, "2026-10-09")                                   # nothing attempted: count stands
    rep = night(ROLLED, "2026-10-10")
    assert promoted == [("hs", "2026-10-10")]
    assert rep["promotion_alerts"][0]["rollbacks_in_row"] == 2 and rep["promotion_alerts"][0]["rolled"] == ["six"]
    night(ROLLED, "2026-10-11")                                 # already alerted for this streak
    assert len(promoted) == 1
    assert ms.env_apply_mode("hs") == "green"                   # never demoted


def test_a_night_that_lands_resets_the_streak(promoted):
    night(ROLLED, "2026-10-08")
    night(LANDED, "2026-10-09")
    night(ROLLED, "2026-10-10")
    assert promoted == [] and ms.load_promotions()["hs"]["rollbacks_in_row"] == 1


def test_a_house_green_env_is_not_watched(monkeypatch, tmp_path):
    monkeypatch.setitem(ms._S.SETTINGS, "envs", {"g": {"apply": "green"}})
    monkeypatch.setattr(ms, "PROMOTIONS_PATH", tmp_path / "p.json")
    rep = {"envs": {"g": {"smoke_baseline": True}}, "applied": {"g": ROLLED}}
    assert ms.update_promotions(rep, "2026-10-08") == {} and "promotion_alerts" not in rep


def test_the_card_is_capability_blocked_for_the_user(monkeypatch):
    calls = []
    def fake_run(argv, **k):
        calls.append(argv)
        return (0, "Created t_abcdef12  (ready)", "") if "create" in argv else (0, "", "")
    monkeypatch.setattr(ms, "run", fake_run)
    monkeypatch.setitem(ms._S.SETTINGS, "kanban", {"enabled": True, "profile": "ada",
                                                    "assignees": {"promotion_alert": "ada"}})
    monkeypatch.setattr(ms, "MS_KANBAN_PROFILE", "ada")
    got = ms.raise_promotion_alert("hs", {"rollbacks_in_row": 2, "promoted_on": "2026-10-07",
                                          "last_rolled": ["six"]}, "2026-10-10")
    assert got == "card t_abcdef12"
    create, block = calls
    assert create[create.index("--assignee") + 1] == "ada"
    assert create[create.index("--idempotency-key") + 1] == "ms-promotion-rollback:hs:2026-10-07"
    assert "rolled back 2 nights in a row" in create[create.index("create") + 1]
    i = block.index("block")
    assert block[i + 1] == "t_abcdef12" and block[-2:] == ["--kind", "capability"]
    assert "the owner decides" in block[i + 2]                  # reason BEFORE --kind (block_args law)


def test_without_a_board_it_notifies(monkeypatch):
    monkeypatch.setitem(ms._S.SETTINGS, "kanban", {"enabled": False})
    sent = []
    monkeypatch.setattr(ms, "notify_user", lambda text: sent.append(text) or "sent")
    got = ms.raise_promotion_alert("hs", {"rollbacks_in_row": 2, "promoted_on": "2026-10-07"}, "2026-10-10")
    assert got == "notify: sent" and "hs rolled back 2 nights in a row" in sent[0]
