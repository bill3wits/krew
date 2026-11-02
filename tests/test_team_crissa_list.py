"""The owner's items on Crissa's list are not the team's to close (reference 2026-09-24, lifted 2026-10-10).

Crissa answered `go` on a restart-window card and the plugin's `_list_clear_card` swept every pending item carrying
that card id, including the owner's own YES/NO question filed by the restart window. `_list_prune_stale` has the
same sweep for a card that leaves the Decide queue. Kill: KREW_CRISSA_LIST_FOREIGN=0. Reads plugins/team-crissa
with its list root redirected to tmp: no real home is touched."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from conftest import load_plugin


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "crissa"))
    monkeypatch.setenv("KREW_TEAM_LIST_ROOT", str(tmp_path / "approvals"))
    m = load_plugin("team-crissa")
    monkeypatch.setattr(m, "_LIST_ROOT", tmp_path / "approvals")
    return m


def _pend(m, item: dict) -> Path:
    d = m._LIST_ROOT / "pending"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{item['id']}.json"
    p.write_text(json.dumps(item) + "\n")
    return p


def _item(iid, source, card_id="t_2cf4b3c7", kind="ask"):
    return {"id": iid, "kind": kind, "title": iid, "body": "", "steps": [], "notes": [],
            "options": ["yes", "no"] if kind == "ask" else [], "source": source,
            "card_id": card_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def test_her_answer_on_the_card_clears_her_own_item_and_leaves_the_owners(plugin):
    ours = _pend(plugin, _item("ask-restart-window-26-restart-s-owed-1", "crissa"))
    owners = _pend(plugin, _item("ask-restart-window-reboot-tonight-1", "restart-window"))
    assert plugin._list_clear_card("t_2cf4b3c7", note="go") == 1
    assert not ours.exists() and (plugin._LIST_ROOT / "done" / ours.name).exists()
    assert owners.exists(), "the owner's YES/NO question stays on the list"
    assert not (plugin._LIST_ROOT / "done" / owners.name).exists()


def test_an_owners_item_survives_the_stale_prune(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_list_live_need_ids", lambda: set())   # her card left the queue
    owners = _pend(plugin, _item("ask-restart-window-reboot-tonight-2", "restart-window"))
    ours = _pend(plugin, _item("ask-something-3", "ada"))
    assert plugin._list_prune_stale() == 1
    assert owners.exists() and not ours.exists()


def test_only_a_foreign_source_is_the_owners(plugin):
    assert plugin._list_is_users({"source": "restart-window"})
    assert plugin._list_is_users({"source": "code-canary"})
    for who in ("crissa", "ada", "steward", "vera", "lila", "bash"):
        assert not plugin._list_is_users({"source": who})
    assert not plugin._list_is_users({"source": ""}), "no source = the old shape, ours"


def test_the_kill_switch_restores_the_old_sweep(plugin, monkeypatch):
    monkeypatch.setenv("KREW_CRISSA_LIST_FOREIGN", "0")
    owners = _pend(plugin, _item("ask-restart-window-reboot-tonight-4", "restart-window"))
    assert plugin._list_clear_card("t_2cf4b3c7", note="go") == 1
    assert not owners.exists()
