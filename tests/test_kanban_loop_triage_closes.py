"""A ruler's `done` closes a TRIAGE card (the User 2026-10-04; KREW_LOOP_TRIAGE_CLOSES=1, OFF by default).

t_619b4198 sat in triage resolved for two days; t_b28650c7 is a post-launch card created straight into triage.
hermes completes only running|ready|blocked|review and blocks nothing in triage; the CLI's one triage exit is an
LLM `specify`; auto_decompose is fenced off since 09-28. So run_done_rulings read a ruler's done on a triage
card and close_card refused it ("status triage does not complete"). The fix: specify (no field changed) +
complete in one process (scripts/kanban-triage-close.py). Only a ruler's done reaches it - frames and green
gates never close a triage card, and every other status keeps today's close."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_loop_tc", ROOT / "crew" / "scripts" / "kanban-loop.py")
kl = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_loop_tc"] = kl
SPEC.loader.exec_module(kl)
TSPEC = importlib.util.spec_from_file_location("kanban_triage_close", ROOT / "crew" / "scripts" / "kanban-triage-close.py")
tc = importlib.util.module_from_spec(TSPEC)
TSPEC.loader.exec_module(tc)


@pytest.fixture(autouse=True)
def _quiet(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "LOG", tmp_path / "kanban-loop.log")


def _card(status="triage", comments=(), cid="t_1", **kw):
    return kl.Card(cid, "a title", "body", status, "", "", 0, "user",
                   comments=list(comments), comment_at=[100 + i for i in range(len(comments))], **kw)


class Cli:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kw):
        self.calls.append(tuple(str(a) for a in args))
        return 0, ""


def test_off_a_triage_card_is_refused_as_today(monkeypatch):
    monkeypatch.delenv("KREW_LOOP_TRIAGE_CLOSES", raising=False)
    monkeypatch.setattr(kl, "close_triage_card", lambda *a: pytest.fail("the triage close ran with the switch off"))
    cli = Cli()
    rc, out = kl.close_card(cli, _card(), "why", triage_ok=True)
    assert rc == 1 and out == "status triage does not complete" and cli.calls == []


def test_on_a_rulers_done_closes_a_triage_card_in_one_step(monkeypatch):
    monkeypatch.setenv("KREW_LOOP_TRIAGE_CLOSES", "1")
    seen = []
    monkeypatch.setattr(kl, "close_triage_card", lambda cid, why: seen.append((cid, why)) or (0, '{"ok": true}'))
    cli = Cli()
    rc, _ = kl.close_card(cli, _card(), "user ruled done", triage_ok=True)
    assert rc == 0 and seen == [("t_1", "user ruled done")]
    assert cli.calls == []                                               # never unblock / promote / complete CLI


def test_run_done_rulings_passes_triage_ok_and_others_do_not():
    src = (ROOT / "crew" / "scripts" / "kanban-loop.py").read_text()
    body = src.split("def run_done_rulings(", 1)[1].split("\ndef ", 1)[0]
    assert "triage_ok=True" in body
    rest = src.replace(body, "")
    assert rest.count("triage_ok=True") == 0                              # frames / green gates never close triage


@pytest.mark.parametrize("status", ["blocked", "ready", "review", "running"])
def test_every_other_status_keeps_todays_close(monkeypatch, status):
    monkeypatch.setenv("KREW_LOOP_TRIAGE_CLOSES", "1")
    monkeypatch.setattr(kl, "close_triage_card", lambda *a: pytest.fail("triage close on a non-triage card"))
    cli = Cli()
    kl.close_card(cli, _card(status=status), "why", triage_ok=True)
    assert cli.calls == [("complete", "t_1", "--result", "why")]


def test_on_a_frame_or_gate_never_closes_a_triage_card(monkeypatch):
    monkeypatch.setenv("KREW_LOOP_TRIAGE_CLOSES", "1")
    monkeypatch.setattr(kl, "close_triage_card", lambda *a: pytest.fail("a non-ruler close reached triage"))
    rc, out = kl.close_card(Cli(), _card(), "children done")
    assert rc == 1 and "does not complete" in out


# --- the helper ----------------------------------------------------------------------------------

class KB:
    def __init__(self, conn, specify=True, complete=True):
        self.conn, self._s, self._c, self.calls = conn, specify, complete, []

    def specify_triage_task(self, conn, tid, author=None):
        self.calls.append(("specify", tid, author))
        if self._s:
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        return self._s

    def complete_task(self, conn, tid, result=None, summary=None):
        self.calls.append(("complete", tid, result))
        if self._c:
            conn.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
        return self._c


def _db(status="triage"):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    c.execute("INSERT INTO tasks VALUES ('t_1', ?)", (status,))
    return c


def test_helper_specifies_then_completes_with_the_result():
    conn = _db()
    kb = KB(conn)
    out = tc.close_triage(kb, conn, "t_1", "verified green")
    assert out == {"ok": True, "card": "t_1", "status": "done"}
    assert kb.calls == [("specify", "t_1", "kanban-loop"), ("complete", "t_1", "verified green")]


@pytest.mark.parametrize("status", ["todo", "ready", "blocked", "done", "archived"])
def test_helper_refuses_a_card_that_is_not_in_triage(status):
    conn = _db(status)
    kb = KB(conn)
    out = tc.close_triage(kb, conn, "t_1", "x")
    assert out["ok"] is False and "not a triage card" in out["error"] and kb.calls == []


def test_helper_reports_a_refused_complete():
    conn = _db()
    out = tc.close_triage(KB(conn, complete=False), conn, "t_1", "x")
    assert out["ok"] is False and "complete refused" in out["error"]
