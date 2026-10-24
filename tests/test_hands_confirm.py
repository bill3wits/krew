"""She says it was filed or done only when a tool reply says so (the User 2026-09-30)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import hands_confirm as hc

ROOT = Path(__file__).resolve().parents[1]


def _db(tmp_path, calls):
    """A session store with one run: [(tool, args, reply)]."""
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript(
        "create table sessions (id text, source text, started_at real);"
        "create table messages (id integer primary key, session_id text, role text, content text,"
        " tool_calls text, tool_call_id text, tool_name text, timestamp real);")
    con.execute("insert into sessions values ('run1','oneshot',100.0)")
    con.execute("insert into messages (session_id, role, content) values ('run1','user','job')")
    for i, (tool, args, reply) in enumerate(calls):
        tc = json.dumps([{"id": f"c{i}", "function": {"name": tool, "arguments": json.dumps(args)}}])
        con.execute("insert into messages (session_id, role, content, tool_calls) values ('run1','assistant','',?)", (tc,))
        con.execute("insert into messages (session_id, role, content, tool_call_id, tool_name) values ('run1','tool',?,?,?)",
                    (reply, f"c{i}", tool))
    con.commit()
    con.close()
    return db


WRONG_TOOL = [("team_compose", {"brief": "file a card"}, "need a clip — emote-woe, emote-surprise, rest, listen-user"),
              ("team_scout", {}, "team_scout: empty ask. Pass the question in plain words — ask, look, or text.")]
FILED = [("team_board", {"action": "create", "title": "fix the panel"}, "Filed for ada: fix the panel (t_5433e4ef).")]
SHOWN = [("show_me", {"what": "kanban"}, "The wall is now showing Hermes Agent - Dashboard"),
         ("show_me", {"what": "shot"}, "/home/x/shot.png")]
NOT_SHOWN = [("show_me", {"what": "kanban"}, "NOTHING CHANGED on the wall (TimeoutExpired). Tell User you could not.")]


def test_only_the_reply_of_the_tool_that_does_it_is_proof(tmp_path):
    assert hc.confirmed(hc.run_facts(_db(tmp_path / "a", WRONG_TOOL) if (tmp_path / "a").mkdir() is None else None, "run1")) == []
    eff = hc.confirmed(hc.run_facts(_db(tmp_path / "b", FILED) if (tmp_path / "b").mkdir() is None else None, "run1"))
    assert eff == [{"kind": "filed", "tool": "team_board", "who": "ada", "id": "t_5433e4ef"}]
    assert hc.confirmed_lines(eff) == ["confirmed: filed t_5433e4ef ada"]
    (tmp_path / "c").mkdir(); (tmp_path / "d").mkdir()
    assert [e["kind"] for e in hc.confirmed(hc.run_facts(_db(tmp_path / "c", SHOWN), "run1"))] == ["shown"]
    assert hc.confirmed(hc.run_facts(_db(tmp_path / "d", NOT_SHOWN), "run1")) == []
    assert hc.run_facts(tmp_path / "no-such.db", "run1") == []


def test_what_counts_as_saying_it_happened():
    said = {"Card's on the board.": ["filed"], "Filed it as a research card with Ada.": ["filed"],
            "I've filed it as a card for Ada to fix the panel fetch.": ["filed"],
            "Kanban's up on the screen.": ["shown"], "The ruling landed on t_abc12345.": ["ruled"],
            "Filed that one too.": ["filed"]}
    for text, kinds in said.items():
        assert hc.claims(text) == kinds, text
    for text in ("I can file a card to fix that config. Want me to?", "There are 5 open cards.",
                 "Ops on 8000 is up and answering.", "I'll put it up again.", "Ask me for the board, I put it up.",
                 "I can't confirm what's on the screen right now.", "The fix card is with Ada."):
        assert hc.claims(text) == [], text


def test_a_claim_without_its_proof_is_unconfirmed_and_its_sentence_is_dropped():
    text = "Board's data is fine. I've filed it as a card for Ada. Want me to chase it?"
    assert hc.unconfirmed(text, []) == ["filed"]
    assert hc.unconfirmed(text, [{"kind": "shown"}]) == ["filed"], "a screen on the glass does not prove a card"
    assert hc.unconfirmed(text, [{"kind": "filed", "id": "t_5433e4ef"}]) == []
    assert hc.strip_claims(text, ["filed"]) == "Board's data is fine. Want me to chase it?"
    assert "no tool reply shows a card was filed" in hc.not_confirmed_line(["filed"])


def test_the_mouth_reads_confirmed_lines_from_the_replies_it_was_handed():
    eff = hc.effects_in(["The board is up.\nconfirmed: shown kanban", "x\nconfirmed: filed t_5433e4ef ada"])
    assert [e["kind"] for e in eff] == ["shown", "filed"] and eff[1]["id"] == "t_5433e4ef"
    assert hc.effects_in(["confirmed: nothing"]) == [{"kind": "nothing", "id": ""}]
    assert hc.unconfirmed("Filed it as a card for Ada.", hc.effects_in(["confirmed: nothing"])) == ["filed"]
    # her own words quoting a confirmed: line are not proof
    assert hc.claims("confirmed: filed t_5433e4ef") == []


# --- the hands script builds its result from the tool replies -----------------------------------
def _hands():
    spec = importlib.util.spec_from_file_location("zoey_hands_confirm", ROOT / "crew" / "scripts" / "zoey-hands.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_run_that_stops_short_hands_back_no_findings(tmp_path):
    """09-30 07:56: wrong tool, step cap, summary "Card's on the board" - she said it was filed."""
    zh = _hands()
    out = zh.report("Card's on the board. Filed it for Ada as a research card.\nsession_id: run1", 1, db=_db(tmp_path, WRONG_TOOL))
    assert out.startswith("HANDS DID NOT FINISH") and out.endswith("confirmed: nothing")
    assert "Card's on the board" not in out and "Filed it" not in out and "session_id" not in out
    assert len(out) < 900


def test_a_failed_run_still_names_what_did_land(tmp_path):
    zh = _hands()
    out = zh.report("ran out of steps\nsession_id: run1", 1, db=_db(tmp_path, FILED))
    assert out.splitlines()[-1] == "confirmed: filed t_5433e4ef ada"


def test_a_finished_run_that_claims_a_card_no_reply_shows(tmp_path):
    zh = _hands()
    out = zh.report("Board's data is fine. Filed it for Ada as a research card.\n\nsession_id: run1", 0, db=_db(tmp_path, WRONG_TOOL))
    assert "Filed it" not in out and "Board's data is fine." in out
    assert "confirmed: nothing" in out and out.splitlines()[-1].startswith("NOT CONFIRMED: no tool reply shows a card was filed")


def test_a_finished_run_with_its_proof_is_handed_over_as_it_is(tmp_path):
    zh = _hands()
    out = zh.report("Filed it for Ada.\nsession_id: run1", 0, db=_db(tmp_path, FILED))
    assert out == "Filed it for Ada.\nconfirmed: filed t_5433e4ef ada"
    (tmp_path / "s").mkdir()
    shown = zh.report("The kanban board is on the glass now.\nsession_id: run1", 0, db=_db(tmp_path / "s", SHOWN))
    assert shown == "The kanban board is on the glass now.\nconfirmed: shown kanban"


def test_the_launcher_warning_is_not_part_of_the_answer(tmp_path):
    """The CLI validates -t before its plugin thread declares skill_read and prints a warning in front."""
    zh = _hands()
    out = zh.report("Warning: Unknown toolsets: skill_read\nFiled it for Ada.\nsession_id: run1", 0, db=_db(tmp_path, FILED))
    assert out == "Filed it for Ada.\nconfirmed: filed t_5433e4ef ada"
    (tmp_path / "o").mkdir()
    other = zh.report("Warning: Unknown toolsets: browsr\nDone.\nsession_id: run1", 0, db=_db(tmp_path / "o", FILED))
    assert "Unknown toolsets: browsr" in other, "only the known race is dropped; a real typo is still seen"


def test_a_run_whose_replies_cannot_be_read_confirms_nothing(tmp_path):
    zh = _hands()
    out = zh.report("Filed it for Ada.", 0, db=tmp_path / "missing.db")       # no session_id line at all
    assert "Filed it" not in out and "NOT CONFIRMED" in out
    assert zh.report("Five cards are open.", 0, db=tmp_path / "missing.db") == "Five cards are open.\nconfirmed: nothing"


def test_the_kill_switch_is_the_old_result(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HANDS_CONFIRM", "0")
    zh = _hands()
    assert zh.report("all done\nsession_id: run1", 0) == "all done\nsession_id: run1"
    assert zh.report("got halfway", 1).endswith(zh.FAILED)


# --- the report-only check of what she said ------------------------------------------------------
def test_the_claim_check_reports_and_changes_nothing(tmp_path):
    spec = importlib.util.spec_from_file_location("zoey_claim_check", ROOT / "crew" / "scripts" / "zoey-claim-check.py")
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    turn = {"session": "s", "at": 100.0, "said": "Filed it as a research card with Ada.", "replies": ["HANDS DID NOT FINISH.\nconfirmed: nothing"]}
    row = cc.check_turn(turn, tmp_path / "missing.db")
    assert row["unconfirmed"] == ["filed"] and row["would_say"] == "I could not confirm that."
    ok = cc.check_turn({**turn, "replies": ["Filed.\nconfirmed: filed t_5433e4ef ada"]}, tmp_path / "missing.db")
    assert ok["unconfirmed"] == [] and ok["would_say"] is None
    assert cc.check_turn({**turn, "said": "Five cards are open."}, tmp_path / "missing.db") is None
    src = (ROOT / "crew" / "scripts" / "zoey-claim-check.py").read_text()
    assert "mode=ro" in src and "REPORT ONLY" in src


# --- a file write is an effect, a refused one is not (the User 2026-10-02) ---------------------------
# The two replies of the 03:44 probe, verbatim shapes from her store: T0 refused one, the other wrote 2 bytes.
REFUSED = ('{"error": "T0 blocked: Zoey: a file write into a blocked path (~/.krew/envs/zoey-probe.txt (a blocked folder)) '
           '(route: zoey-kit). Zoey\'s hands have no shell and no code runner (the User 2026-10-01): read a card ..."}')
WROTE = ('{"bytes_written": 2, "dirs_created": true, "verified": true, "lint": {"status": "skipped"}, '
         '"resolved_path": "/home/z/scratch/zoey-probe-ok.txt", "files_modified": ["/home/z/scratch/zoey-probe-ok.txt"]}')
PROBE_0344 = [("write_file", {"path": "~/.krew/envs/zoey-probe.txt", "content": "ok"}, REFUSED),
              ("write_file", {"path": "/home/z/scratch/zoey-probe-ok.txt", "content": "ok"}, WROTE)]
LEAN_0343 = [("team_time", {}, "3:43 AM on Friday, October 2, 2026 (EDT)")]
SAID_0343 = ("All three steps done. Step 1: file written, confirmed ok. Step 2: file written, confirmed ok. "
             "Step 3: I have no shell or code-runner tool in this hands set, so I could not run echo hello.")


def test_one_real_write_and_one_refused_write(tmp_path):
    facts = hc.run_facts(_db(tmp_path, PROBE_0344), "run1")
    assert hc.confirmed(facts) == [{"kind": "written", "tool": "write_file", "what": "/home/z/scratch/zoey-probe-ok.txt"}]
    assert hc.not_done_lines(hc.not_done(facts)) == [
        "not done: write ~/.krew/envs/zoey-probe.txt - Zoey: a file write into a blocked path "
        "(~/.krew/envs/zoey-probe.txt (a blocked folder))"]
    zh = _hands()
    (tmp_path / "r").mkdir()
    out = zh.report("One write blocked, one landed.\nsession_id: run1", 0, db=_db(tmp_path / "r", PROBE_0344))
    assert out.splitlines()[-2:] == ["confirmed: written /home/z/scratch/zoey-probe-ok.txt",
                                     hc.not_done_lines(hc.not_done(facts))[0]]


def test_every_write_reply_shape_reads_right():
    def one(reply, tool="write_file"):
        return hc.write_outcome({"tool": tool, "args": {"path": "/p/a.txt"}, "reply": reply})[:2]
    assert one('{"success": true, "diff": "x", "files_modified": ["/p/a.txt"]}', "patch") == ("written", "/p/a.txt")
    assert one('{"success": false, "error": "Failed to read file: /p/a.txt"}', "patch")[0] == "refused"
    assert one('{"bytes_written": 0, "dirs_created": false, "error": "Write denied"}')[0] == "refused"
    assert one("[write_file] wrote to /p/a.txt (3 lines)") == ("written", "/p/a.txt")
    assert one("[patch] replace in /p/a.txt (1,929 chars result)", "patch") == ("written", "/p/a.txt")
    assert one('{"error": "Refusing to overwri')[0] == "refused"
    assert one("") == ("refused", "/p/a.txt")
    assert hc.write_outcome({"tool": "team_time", "reply": "{}"}) is None


def test_the_claim_truth_switch_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("KREW_HANDS_CLAIM_TRUTH", raising=False)
    zh = _hands()
    monkeypatch.setattr(zh, "CLAIM_TRUTH_FILE", tmp_path / "missing.on")
    assert zh.claim_truth() is False
    out = zh.report(SAID_0343 + "\nsession_id: run1", 0, db=_db(tmp_path, LEAN_0343))
    assert "file written, confirmed ok" in out and out.splitlines()[-1] == "confirmed: nothing"
    (tmp_path / "on").write_text("")
    monkeypatch.setattr(zh, "CLAIM_TRUTH_FILE", tmp_path / "on")
    assert zh.claim_truth() is True
    monkeypatch.setenv("KREW_HANDS_CLAIM_TRUTH", "0")
    assert zh.claim_truth() is False, "the env beats the file"


def test_on_nothing_confirmed_she_says_what_she_tried_not_success(tmp_path, monkeypatch):
    """03:43: no file tool, only team_time, and the hands said every step was done."""
    monkeypatch.setenv("KREW_HANDS_CLAIM_TRUTH", "1")
    zh = _hands()
    out = zh.report(SAID_0343 + "\nsession_id: run1", 0, db=_db(tmp_path, LEAN_0343))
    assert "steps done" not in out and "file written" not in out and "confirmed ok" not in out
    assert "could not run echo hello" in out, "a sentence that says what did NOT happen stays"
    assert "confirmed: nothing" in out
    last = out.splitlines()[-1]
    assert last.startswith("NOT DONE:") and "Tried: team_time" in last and "no write tool was called" in last


def test_on_a_true_write_report_is_handed_over_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HANDS_CLAIM_TRUTH", "1")
    zh = _hands()
    said = "The write to ~/.krew/envs was blocked. The second write landed fine in zoey-probe-ok.txt."
    out = zh.report(said + "\nsession_id: run1", 0, db=_db(tmp_path, PROBE_0344))
    assert out.startswith(said) and "NOT DONE" not in out


def test_on_a_write_claim_naming_the_refused_file_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HANDS_CLAIM_TRUTH", "1")
    zh = _hands()
    out = zh.report("Both files written. The file zoey-probe.txt was saved too.\nsession_id: run1", 0,
                    db=_db(tmp_path, PROBE_0344))
    assert "zoey-probe.txt was saved" not in out and out.splitlines()[-1].startswith("NOT DONE:")
    assert "write_file ~/.krew/envs/zoey-probe.txt (refused)" in out.splitlines()[-1]


# --- a count is a claim too (t_03357f8d) ------------------------------------------------------------
def test_a_count_claim_must_match_the_confirmed_writes(tmp_path, monkeypatch):
    """03:44's replies (one written, one refused): 'Both files written' is a claim of two."""
    monkeypatch.setenv("KREW_HANDS_CLAIM_TRUTH", "1")
    zh = _hands()
    out = zh.report("Both files written.\nsession_id: run1", 0, db=_db(tmp_path, PROBE_0344))
    assert "Both files written" not in out and out.splitlines()[-1].startswith("NOT DONE:")
    (tmp_path / "r").mkdir()
    facts = hc.run_facts(_db(tmp_path / "r", PROBE_0344), "run1")
    eff = hc.confirmed(facts)
    for said in ("Wrote all the files.", "Two files written.", "Each write landed.", "2 files written."):
        assert hc.false_success(said, eff, facts) == [said], said
    for said in ("One file written.", "The write landed: bytes_written 2.", "1 file written."):
        assert hc.false_success(said, eff, facts) == [], said


def test_a_count_within_the_confirmed_writes_stands(tmp_path):
    two = [("write_file", {"path": "/p/a.txt"}, WROTE.replace("zoey-probe-ok", "a")),
           ("write_file", {"path": "/p/b.txt"}, WROTE.replace("zoey-probe-ok", "b"))]
    facts = hc.run_facts(_db(tmp_path, two), "run1")
    eff = hc.confirmed(facts)
    assert len(eff) == 2
    assert hc.false_success("Both files written.", eff, facts) == []
    assert hc.false_success("All files written.", eff, facts) == []
    assert hc.false_success("Three files written.", eff, facts) == ["Three files written."]


# --- a plain read that worked is not NOT DONE (the User 2026-10-02) ---------------------------------
def test_a_read_that_reports_the_board_is_not_a_false_success(tmp_path, monkeypatch):
    """The drill's board count: one team_board list, and 'the rest are done' is the done column."""
    monkeypatch.setenv("KREW_HANDS_CLAIM_TRUTH", "1")
    zh = _hands()
    said = "Three open. The first line says 3 open, and the rest are done."
    read = [("team_board", {"action": "list"}, "3 open · done 83 ○ t_9dcb6185 ready steward sweep")]
    out = zh.report(said + "\nsession_id: run1", 0, db=_db(tmp_path, read))
    assert out.startswith(said) and "NOT DONE" not in out
    for ok in ("The board shows 14 cards are done.", "Four cards are closed.", "The list says it is finished."):
        assert hc.false_success(ok, [], []) == [], ok
    for bad in ("All three steps done.", "Done.", "It worked.", "The file was written."):
        assert hc.false_success(bad, [], []) == [bad], bad



def test_rather_than_is_not_a_claim():
    """10-03 spot check: 'so it's just waiting rather than being worked' cost a good read its answer."""
    said = "It's parked because steward re-verified it, so it's just waiting rather than being worked."
    assert hc.false_success(said, [], []) == []
    assert hc.false_success("It worked.", [], []) == ["It worked."]


def test_a_read_job_cannot_falsely_claim_an_act(tmp_path, monkeypatch):
    """10-03 rehearsal: 'with forty-one done' on a board count became NOT DONE and she said the board was empty."""
    said = "Nine cards are open on the board right now, with forty-one done."
    assert hc.false_success(said, [], [], ask="How many cards are open on the board right now?") == []
    assert hc.false_success("All three steps done.", [], [], ask="Write the two files and confirm.") == ["All three steps done."]
    assert hc.false_success("All three steps done.", [], []) == ["All three steps done."]       # no ask = old rule
    monkeypatch.setenv("KREW_HANDS_CLAIM_TRUTH", "1")
    zh = _hands()
    read = [("team_board", {"action": "list"}, "9 open · done 41")]
    out = zh.report(said + "\nsession_id: run1", 0, db=_db(tmp_path, read), ask="How many cards are open?")
    assert out.startswith(said) and "NOT DONE" not in out
