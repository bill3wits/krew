"""Steward files ops-action cards from Ada's Action items heading."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "scripts"))
from architect_lib import ops_action_id, plan_filings  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "steward_file_cards", ROOT / "crew" / "scripts" / "steward-file-architect-cards.py"
)
assert _spec and _spec.loader
cards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cards)


def test_parse_ops_action_under_action_items():
    text = """# Ops efficiency 2026-08-25

## Tracked changes
- not a card

## Action items
- ops-action: keep-baseline — kanban_blocked — new tool; baseline established
- ops-action: investigate — kanban_open — jumped this week

## Other
- ops-action: should-not-file — ignored after heading
"""
    found = cards.parse_cards(text)
    assert found[0][0] == "ops-action"
    assert "keep-baseline" in found[0][1]
    assert found[1][0] == "ops-action"
    assert "kanban_open" in found[1][1]
    assert all("should-not-file" not in t for _k, t in found)


def test_ops_action_assignee_is_steward():
    assert cards.ASSIGNEE["ops-action"] == "steward"
    assert cards.ASSIGNEE["architect-try"] == "ada"


def test_ops_action_id_is_stable():
    a = ops_action_id("keep-baseline — kanban_blocked — new tool")
    b = ops_action_id("keep-baseline — kanban_blocked — new tool")
    assert a == b
    assert a.startswith("oa_")
    assert len(a) == 15
    other = ops_action_id("investigate — kanban_open — jumped")
    assert other != a


def test_plan_filings_skips_existing_ops_action():
    title = "keep-baseline — kanban_blocked — new tool"
    aid = ops_action_id(title)
    planned = plan_filings(
        [("ops-action", title), ("architect-try", "bench foo")],
        {aid},
    )
    assert planned[0]["skip"] is True
    assert planned[0]["assignee"] == "steward"
    assert planned[1]["skip"] is False
    assert planned[1]["assignee"] == "ada"


# --- 2026-09-12: a try needs a green, and a green needs a queue ---------------

class _Run:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        class R:
            returncode = 0
            stdout = f"Created t_{len(self.calls):08x}  (triage, assignee=ada)\n"
            stderr = ""
        return R()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_board(tmp_path, monkeypatch):
    """The filer looks an idea up on the fleet's live board and logs the night:
    a test reads no live board and writes no house ledger."""
    monkeypatch.setattr(cards, "seen_on_board", lambda key, head="": None)
    monkeypatch.setattr(cards, "SEEN", tmp_path / "seen.jsonl")
    monkeypatch.setattr(cards, "FAILED", [])
    # these tests file a research note at once; the vote hold has its own tests (test_improve_round.py)
    monkeypatch.setattr(cards, "WAIT_FOR_VOTE", False)


def _verb(c):
    """The kanban verb and what follows it, wherever `-p <profile>` puts them."""
    return c[c.index("kanban") + 1:]


def _note(tmp_path: Path) -> Path:
    n = tmp_path / "team-research-2026-09-12.md"
    n.write_text(
        "# note\n\n## Proposed cards\n"
        "- team-improve: NNT 404 six nights — fix the 00:20 collect\n"
        "- architect-watch: vLLM v0.29 head\n"
        "- architect-try: replicate the 16 GB vision recipe\n"
    )
    return n


def test_green_kinds_are_blocked_needs_input_at_filing(tmp_path: Path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(cards.subprocess, "run", run)
    monkeypatch.setattr(cards, "ACTIONS", tmp_path / "actions.jsonl")
    monkeypatch.setattr(cards, "GREEN_GATE", True)
    assert cards.file_from(_note(tmp_path)) == 3
    creates = [_verb(c) for c in run.calls if _verb(c)[0] == "create"]
    blocks = [_verb(c) for c in run.calls if _verb(c)[0] == "block"]
    assert len(creates) == 3
    # reason BEFORE --kind (a trailing nargs=* reason after the flag is a usage error)
    assert all(list(c[-2:]) == ["--kind", "needs_input"] for c in blocks) and len(blocks) == 2
    blocked_ids = {c[1] for c in blocks}
    # the watch card is not gated; the two greens are
    watch_body = next(c for c in creates if c[1].startswith("architect-watch"))
    assert "gate: green" not in watch_body[creates[0].index("--body") + 1]
    green_bodies = [c[c.index("--body") + 1] for c in creates if not c[1].startswith("architect-watch")]
    assert all("gate: green" in b for b in green_bodies)
    assert len(blocked_ids) == 2
    assert all(cards.GREEN_QUESTION == c[2] for c in blocks)


def test_green_gate_off_is_the_old_behaviour(tmp_path: Path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(cards.subprocess, "run", run)
    monkeypatch.setattr(cards, "ACTIONS", tmp_path / "actions.jsonl")
    monkeypatch.setattr(cards, "GREEN_GATE", False)
    cards.file_from(_note(tmp_path))
    assert not [c for c in run.calls if _verb(c)[0] == "block"]
    bodies = [c[c.index("--body") + 1] for c in run.calls if _verb(c)[0] == "create"]
    assert all("greens a try" in b for b in bodies)


def test_created_id_parses_hermes_output():
    assert cards.created_id("Created t_b6fb8a72  (triage, assignee=ada)") == "t_b6fb8a72"
    assert cards.created_id("nothing") == ""


# --- 2026-09-12: NNT — picks debated, agreed items on the top improvements list

def test_nnt_agreed_files_a_green_card_and_records_the_list(tmp_path: Path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(cards.subprocess, "run", run)
    monkeypatch.setattr(cards, "ACTIONS", tmp_path / "actions.jsonl")
    monkeypatch.setattr(cards, "PROPOSALS", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(cards, "GREEN_GATE", True)
    n = tmp_path / "zoey-improvement-ideas-2026-09-13.md"
    n.write_text("# ideas\n\nnnt-agreed: ggml-org/whisper.cpp v1.8 — the ear on CPU int8\n"
                 "- nnt-pass: some/other — not ours\n")
    assert cards.nnt_agreed(n.read_text()) == ["ggml-org/whisper.cpp v1.8 — the ear on CPU int8"]
    assert cards.file_from(n) == 1
    create = [_verb(c) for c in run.calls if _verb(c)[0] == "create"][0]
    assert create[1].startswith("team-improve: ggml-org/whisper.cpp")
    assert [c for c in run.calls if _verb(c)[0] == "block"]          # green-gated
    rows = [json.loads(l) for l in (tmp_path / "proposals.jsonl").read_text().splitlines()]
    assert rows[0]["kind"] == "nnt-agreed" and rows[0]["status"] == "agreed"
    # a second pass does not duplicate the ledger line
    assert cards.record_proposal(rows[0]["title"], n, path=tmp_path / "proposals.jsonl") is False


# --- 2026-09-17: one idea, one card; a gate that cannot close fails closed ----

def test_every_kanban_call_names_a_profile(tmp_path: Path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(cards.subprocess, "run", run)
    monkeypatch.setattr(cards, "ACTIONS", tmp_path / "actions.jsonl")
    monkeypatch.setattr(cards, "SEEN", tmp_path / "seen.jsonl")
    monkeypatch.setattr(cards, "seen_on_board", lambda key, head="": None)
    cards.file_from(_note(tmp_path))
    assert run.calls and all(c[1:4] == ["-p", "ada", "kanban"] for c in run.calls)


def test_an_idea_this_board_has_seen_is_not_filed_again(tmp_path: Path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(cards.subprocess, "run", run)
    monkeypatch.setattr(cards, "ACTIONS", tmp_path / "actions.jsonl")
    monkeypatch.setattr(cards, "SEEN", tmp_path / "seen.jsonl")
    # the prose after the first field changes every night; the key does not
    assert cards.slug_key("team-improve", "NNT 404 six nights — fix the 00:20 collect") == \
           cards.slug_key("team-improve", "NNT 404 six nights — 4 of 5 roles, all >= 2 (sum 12)")
    monkeypatch.setattr(cards, "seen_on_board",
                        lambda key, head="": ("t_0badf00d", "archived") if key.startswith("team-improve:") else None)
    assert cards.file_from(_note(tmp_path)) == 2                     # the parked idea stays parked
    titles = [_verb(c)[1] for c in run.calls if _verb(c)[0] == "create"]
    assert not any(t.startswith("team-improve") for t in titles)
    seen = [json.loads(l) for l in (tmp_path / "seen.jsonl").read_text().splitlines()]
    assert seen[0]["task"] == "t_0badf00d" and seen[0]["status"] == "archived"


def test_a_gate_that_will_not_block_is_archived_and_fails_loud(tmp_path: Path, monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        verb = _verb(list(argv))[0]
        class R:
            returncode = 2 if verb == "block" else 0
            stdout = f"Created t_{len(calls):08x}\n" if verb == "create" else ""
            stderr = "usage: hermes" if verb == "block" else ""
        return R()

    monkeypatch.setattr(cards.subprocess, "run", run)
    monkeypatch.setattr(cards, "ACTIONS", tmp_path / "actions.jsonl")
    monkeypatch.setattr(cards, "SEEN", tmp_path / "seen.jsonl")
    monkeypatch.setattr(cards, "seen_on_board", lambda key, head="": None)
    monkeypatch.setattr(cards, "GREEN_GATE", True)
    monkeypatch.setattr(cards, "FAILED", [])
    cards.file_from(_note(tmp_path))
    archived = [_verb(c)[1] for c in calls if _verb(c)[0] == "archive"]
    blocked = [_verb(c)[1] for c in calls if _verb(c)[0] == "block"]
    assert archived == blocked and len(archived) == 2 and cards.FAILED == archived


def test_the_ledger_outlives_the_board(tmp_path: Path, monkeypatch):
    """gc trims events and a purge takes the row: a filed idea is still one card."""
    run = _Run()
    monkeypatch.setattr(cards.subprocess, "run", run)
    monkeypatch.setattr(cards, "ACTIONS", tmp_path / "actions.jsonl")
    assert cards.file_from(_note(tmp_path)) == 3
    rows = [json.loads(l) for l in (tmp_path / "seen.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == ["filed"] * 3 and all(r["task"].startswith("t_") for r in rows)
    before = len(run.calls)
    assert cards.file_from(_note(tmp_path)) == 0          # the board says nothing (mocked empty); the ledger does
    assert len(run.calls) == before


def test_the_filer_has_an_off_switch(monkeypatch, tmp_path, capsys):
    """Krew fence (the User 09-28): the flag or the env stops every card, nothing else."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "filer_off", Path(__file__).resolve().parents[1] / "crew" / "scripts" / "steward-file-architect-cards.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("KREW_ARCHITECT_CARDS", raising=False)
    assert m.cards_off() == ""
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes" / "architect-cards.off").write_text("fence\n")
    assert m.cards_off().endswith("architect-cards.off")
    monkeypatch.setattr(m, "file_from", lambda note: (_ for _ in ()).throw(AssertionError("filed")))
    assert m.main() == 0
    assert "nothing filed" in capsys.readouterr().out
    (tmp_path / ".hermes" / "architect-cards.off").unlink()
    monkeypatch.setenv("KREW_ARCHITECT_CARDS", "0")
    assert m.cards_off() == "KREW_ARCHITECT_CARDS=0"
