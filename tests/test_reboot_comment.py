"""A reboot the User explained is not an UNKNOWN reboot (the User 2026-09-30).

09-30 04:27: the User rebooted after an apt upgrade, the janitor filed "INTENT: UNKNOWN" (t_a55a2b7b) and the
card sat as unexplained. His comment is the intent: before the reboot it stops the card being filed, after it
the janitor records it and archives the card.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))
import reboot_comment as rc  # noqa: E402

NOW = 1_790_756_900.0          # 09-30 04:28:20, the first janitor sample after the reboot
UNKNOWN = "JANITOR: reboot detected 2026-09-30T04:30:03 — INTENT: UNKNOWN"


def _board(tmp_path, tasks=(), comments=()):
    root = tmp_path / "kanban"
    (root / "boards" / "live").mkdir(parents=True)
    (root / "current").write_text("live\n")
    db = root / "boards" / "live" / "kanban.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, "
                "created_at INTEGER)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?)", tasks)
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", comments)
    con.commit()
    con.close()
    return root


def test_the_live_board_is_found_through_current(tmp_path):
    root = _board(tmp_path)
    assert rc.board_db(root) == root / "boards" / "live" / "kanban.db"
    (root / "current").write_text("gone\n")
    assert rc.board_db(root) is None


@pytest.mark.parametrize("author,body,found", [
    ("user", "Rebooting after the apt upgrade, back in 5", True),
    ("dashboard", "reboot now for the kernel", True),
    ("user", "restarting the host for the driver", True),
    ("zoey", "rebooting after the apt upgrade", False),          # his word only
    ("steward", "reboot the house tonight?", False),
    ("user", "restart the gateway when quiet", False),           # a unit is not the host
])
def test_his_word_before_the_reboot(tmp_path, author, body, found):
    root = _board(tmp_path, comments=[("t_x", author, body, int(NOW - 600))])
    con = rc.connect(rc.board_db(root))
    said = rc.stated_before(con, NOW)
    assert (said is not None) is found
    if found:
        assert said["card"] == "t_x" and said["reason"].lower().startswith(body.lower()[:6])


def test_an_old_comment_is_not_this_reboot(tmp_path):
    root = _board(tmp_path, comments=[("t_x", "user", "rebooting after the upgrade", int(NOW - 4 * 3600))])
    assert rc.stated_before(rc.connect(rc.board_db(root)), NOW) is None


def test_his_comment_on_the_unknown_card_explains_it(tmp_path):
    root = _board(tmp_path, tasks=[("t_a55a2b7b", UNKNOWN, "triage", int(NOW)),
                                   ("t_other", "JANITOR RED: dashboard crash loop", "triage", int(NOW))],
                  comments=[("t_a55a2b7b", "krew-janitor", "filed", int(NOW) + 1),
                            ("t_a55a2b7b", "zoey", "looking into it", int(NOW) + 60),
                            ("t_a55a2b7b", "user", "That was me: apt upgrade.", int(NOW) + 3600),
                            ("t_other", "user", "this one too", int(NOW) + 60)])
    cards = rc.explained_cards(rc.connect(rc.board_db(root)))
    assert [c["card"] for c in cards] == ["t_a55a2b7b"]
    assert cards[0]["reason"] == "That was me: apt upgrade." and cards[0]["author"] == "user"


def test_an_unexplained_or_already_resolved_card_is_left(tmp_path):
    root = _board(tmp_path, tasks=[("t_1", UNKNOWN, "triage", int(NOW)), ("t_2", UNKNOWN, "triage", int(NOW)),
                                   ("t_3", UNKNOWN, "archived", int(NOW))],
                  comments=[("t_1", "zoey", "no idea", int(NOW) + 5),
                            ("t_2", "user", "mine", int(NOW) + 5),
                            ("t_2", "krew-janitor", rc.RESOLVED_MARK + " (user): mine", int(NOW) + 6),
                            ("t_3", "user", "mine", int(NOW) + 5)])
    assert rc.explained_cards(rc.connect(rc.board_db(root))) == []


def test_resolve_says_so_archives_and_audits(tmp_path):
    calls, audit = [], tmp_path / "reboot-intent.jsonl"
    card = {"card": "t_a55a2b7b", "author": "user", "ts": int(NOW), "reason": "That was me: apt upgrade."}
    n = rc.resolve([card], lambda *a: (calls.append(a), 0)[1], audit_path=audit)
    assert n == 1 and calls[0][0] == "comment" and rc.RESOLVED_MARK in calls[0][2] and calls[1] == ("archive", "t_a55a2b7b")
    line = json.loads(audit.read_text())
    assert line["verdict"] == "explained-after" and line["source"] == "user-comment" and line["card"] == "t_a55a2b7b"


def test_a_failed_comment_archives_nothing(tmp_path):
    calls = []
    assert rc.resolve([{"card": "t_1", "author": "user", "ts": 1, "reason": "x"}],
                      lambda *a: (calls.append(a), 1)[1], audit_path=tmp_path / "a.jsonl") == 0
    assert [c[0] for c in calls] == ["comment"]


def test_the_kill_switch():
    assert rc.enabled({}) and not rc.enabled({"KREW_JANITOR_REBOOT_COMMENT": "0"})


def _janitor():
    loader = SourceFileLoader("krew_janitor_reboot", str(ROOT / "janitor" / "krew-janitor.py"))
    mod = module_from_spec(spec_from_loader("krew_janitor_reboot", loader))
    loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("stamp,said,expect", [
    ("UNKNOWN", {"card": "t_x", "author": "user", "ts": 1, "reason": "apt upgrade"}, []),
    ("UNKNOWN", None, ["INTENT: UNKNOWN"]),
    ("intentional=kernel", None, ["INTENT: kernel"]),
])
def test_the_janitor_reboot_branch(monkeypatch, tmp_path, stamp, said, expect):
    jan = _janitor()
    filed = []
    monkeypatch.setattr(jan, "file_card", lambda t, b: filed.append(t) or True)
    monkeypatch.setattr(jan, "_reboot_intent", lambda: stamp)
    monkeypatch.setattr(jan, "reboot_stated_by_user", lambda now: said)
    monkeypatch.setattr(jan._reboot_comment(), "AUDIT", tmp_path / "audit.jsonl")
    row = {"ts": "2026-09-30T04:30:03", "boot_id": "ca4a4f7e"}
    jan.reboot_detected(row, NOW)
    assert [t.split(" — ", 1)[1] for t in filed] == expect
    if said:
        assert row["reboot_intent"] == "apt upgrade"
        assert json.loads((tmp_path / "audit.jsonl").read_text())["verdict"] == "stated-before"
    src = (ROOT / "janitor" / "krew-janitor.py").read_text()
    assert "reboot_detected(row, now)" in src and 'row["reboot_cards_explained"] = reboot_comment_tick()' in src


def test_the_janitor_tick_helpers_never_raise(monkeypatch):
    jan = _janitor()
    lib = jan._reboot_comment()
    monkeypatch.setattr(lib, "board_db", lambda root=None: (_ for _ in ()).throw(OSError("no board")))
    assert jan.reboot_stated_by_user(NOW) is None
    assert jan.reboot_comment_tick() == -1
