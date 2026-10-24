"""krew-board: paging says how many more and how to get them; shortened fields say so; no machine paths."""
from __future__ import annotations

import json

from conftest import submodule


def B():
    return submodule("krew-board", "board")


def rows(n):
    return [{"id": f"t_{i:08x}", "title": f"card {i}", "status": "ready", "assignee": "bash",
             "created_at": 1_700_000_000 + i} for i in range(n)]


def fake_cli(out_by_cmd):
    calls = []

    def cli(args, *a, **k):
        calls.append(list(args))
        return 0, out_by_cmd(args)
    cli.calls = calls
    return cli


def test_list_pages_and_says_how_to_get_the_rest():
    b = B()
    cli = fake_cli(lambda a: json.dumps(rows(b.PAGE + 7)))
    out = b.team_board({"action": "list"}, cli=cli)
    assert "[7 more not shown: list again with offset=%d" % b.PAGE in out
    out2 = b.team_board({"action": "list", "offset": b.PAGE}, cli=cli)
    assert "more not shown" not in out2 and out2.count("card ") == 7


def test_show_marks_every_shortened_field_and_full_returns_it_whole():
    b = B()
    body = "x" * (b.SHOW_BODY + 50)
    card = {"task": {"id": "t_00000001", "title": "t", "status": "ready", "body": body}, "comments": []}
    cli = fake_cli(lambda a: json.dumps(card))
    short = b.team_board({"action": "show", "id": "t_00000001"}, cli=cli)
    assert "shortened" in short and "full=true" in short
    whole = b.team_board({"action": "show", "id": "t_00000001", "full": True}, cli=cli)
    assert body in whole and "shortened" not in whole


def test_a_read_word_with_an_id_is_show():
    b = B()
    cli = fake_cli(lambda a: json.dumps({"task": {"id": "t_1", "title": "t", "status": "ready", "body": ""}}))
    b.team_board({"action": "read card", "id": "t_1"}, cli=cli)
    assert cli.calls[0][:2] == ["show", "t_1"]


def test_unknown_action_changes_nothing():
    b = B()
    cli = fake_cli(lambda a: "")
    out = b.team_board({"action": "explode"}, cli=cli)
    assert "Nothing changed" in out and cli.calls == []


def test_config_audit_names_no_machine_paths():
    b = B()
    out = b.config_audit(toolsets={"team": {"description": "Board tools.", "tools": ["team_board"]},
                                   "web": {"description": "Web search. More.", "tools": ["web_search"]}}, cfg={"toolsets": ["team"]},
                         config_path="/tmp/x/config.yaml")
    from test_scrub import offences
    assert "/tmp/x/config.yaml" in out
    assert not offences("audit", out.replace("/tmp/x/config.yaml", ""))
