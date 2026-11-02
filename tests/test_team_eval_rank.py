"""team-eval-rank: roles' scores add up; every-role >= 2 goes to the top;
a faked voice does not count (the User 2026-09-12)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("team_eval_rank", ROOT / "crew" / "scripts" / "team-eval-rank.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _seen_store_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the ranker loads nnt-seen.py fresh each call, whose store path is fixed at import under the
    home; hand it one copy whose paths are in tmp (tests/krew_home_guard.py)."""
    try:
        seen = mod._nnt_seen()
    except FileNotFoundError:
        return
    keep_in_tmp(seen, tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_nnt_seen", lambda: seen)


@pytest.fixture(autouse=True)
def _no_live_profiles(tmp_path, monkeypatch):
    """The ranker also reads a role's profile-home note: a test reads no live profile."""
    monkeypatch.setattr(mod, "PROFILES", tmp_path / "no-profiles")

RESEARCH = """# research
## NNT picks — for the team
- nnt-pick: ggml-org/whisper.cpp — https://youtu.be/a — ears — CPU int8 path
- nnt-pick: foo/ltx-tool — https://youtu.be/b — render — prompt tool
- nnt-pick: bar/board-bot — https://youtu.be/c — board — auto triage
## bash (host)
- team-improve: unit restart wrapper bucket=improve-ops
"""


def _note(scores):
    return "# eval\nlede: x\n\n## Scores\n" + "".join(f"score: {k} = {v} — {w}\n" for k, v, w in scores)


def test_scores_parse_and_items_match_verbatim():
    items = mod.items_from_research(RESEARCH)
    assert set(items) == {"ggml-org/whisper.cpp", "foo/ltx-tool", "bar/board-bot", "unit restart wrapper bucket=improve-ops"}
    v = mod.scores_from_note(_note([("ggml-org/whisper.cpp", 3, "ear card t_1"), ("Foo/LTX-Tool", 0, "")]))
    assert v["ggml-org/whisper.cpp"] == (3, "ear card t_1") and v["foo/ltx-tool"] == (0, "")


def test_every_role_at_two_goes_to_the_top():
    items = mod.items_from_research(RESEARCH)
    votes = {
        "ada":     mod.scores_from_note(_note([("ggml-org/whisper.cpp", 2, "a"), ("foo/ltx-tool", 3, "b"), ("bar/board-bot", 3, "c"), ("unit restart wrapper bucket=improve-ops", 1, "")])),
        "bash":    mod.scores_from_note(_note([("ggml-org/whisper.cpp", 2, "a"), ("foo/ltx-tool", 0, ""), ("bar/board-bot", 1, ""), ("unit restart wrapper bucket=improve-ops", 3, "mine")])),
        "steward": mod.scores_from_note(_note([("ggml-org/whisper.cpp", 3, "ear"), ("foo/ltx-tool", 1, ""), ("bar/board-bot", 2, "x"), ("unit restart wrapper bucket=improve-ops", 2, "y")])),
    }
    rows = mod.rank(items, votes)
    assert rows[0]["item"] == "ggml-org/whisper.cpp" and rows[0]["top"] is True and rows[0]["rank"] == 1
    assert all(r["top"] is False for r in rows[1:])
    # breadth/sum decide the rest: board-bot sum 6 vs ltx 4 vs wrapper 6 — wrapper min 1 vs board-bot min 1: tie → alphabetical
    assert [r["item"] for r in rows[1:3]] == ["bar/board-bot", "unit restart wrapper bucket=improve-ops"]


def test_faked_voices_are_discounted():
    n = 4
    assert "identical" in mod.faked({"a": (3, "x"), "b": (3, "y"), "c": (3, "z"), "d": (3, "w")}, n)
    assert "no reason" in mod.faked({"a": (3, ""), "b": (1, ""), "c": (0, ""), "d": (2, "")}, n)
    assert "of 4 items" in mod.faked({"a": (2, "x")}, n)
    assert mod.faked({"a": (2, "x"), "b": (0, ""), "c": (3, "pain t_9"), "d": (1, "")}, n) == ""
    items = mod.items_from_research(RESEARCH)
    faker = mod.scores_from_note(_note([(k, 3, "") for k in items.values()]))
    honest = mod.scores_from_note(_note([("ggml-org/whisper.cpp", 2, "a"), ("foo/ltx-tool", 0, ""), ("bar/board-bot", 1, ""), ("unit restart wrapper bucket=improve-ops", 2, "y")]))
    rows = mod.rank(items, {"ada": faker, "bash": honest})
    assert all("ada" not in r["scores"] for r in rows)


def test_main_writes_note_and_json_with_agreed_lines(tmp_path):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    (inbox / "team-research-2026-09-13.md").write_text(RESEARCH)
    for r in ("ada", "bash", "lila"):
        (inbox / f"{r}-improvement-ideas-2026-09-13.md").write_text(
            _note([("ggml-org/whisper.cpp", 2, f"{r} gain"), ("foo/ltx-tool", 1, ""), ("bar/board-bot", 0, ""), ("unit restart wrapper bucket=improve-ops", 2, "y")]))
    (inbox / "vera-improvement-ideas-2026-09-13.md").write_text(_note([(k, 3, "") for k in ("ggml-org/whisper.cpp", "foo/ltx-tool", "bar/board-bot", "unit restart wrapper bucket=improve-ops")]))
    assert mod.main(["--day", "2026-09-13", "--inbox", str(inbox), "--out-dir", str(tmp_path / "imp")]) == 0
    md = (inbox / "team-eval-2026-09-13.md").read_text()
    assert "nnt-agreed: ggml-org/whisper.cpp" in md and "nnt-agreed: unit restart wrapper" in md
    assert "Voices not counted" in md and "vera:" in md
    j = json.loads((tmp_path / "imp" / "rank-2026-09-13.json").read_text())
    assert j["voters"] == ["ada", "bash", "lila"] and "vera" in j["discounted"] and j["rows"][0]["top"] is True


# --- 2026-09-17: the night the ranker saw none of the idea every role scored 3 ---

BACKTICKED = """# research
## NNT picks — for the team
- nnt-pick: The Next New Thing — "Amazing: FREE AI Agent" (2026-09-15, https://youtu.be/x) — brains
## Proposed cards
- `team-improve: kanban-worker-no-terminal-call — bucket=improve-ops — catch rc=0 without a terminal call.` (measured)
- `team-improve: vllm-v0.29-0-watch — bucket=expand-caps — file only if a decision is open.`
"""


def test_backticked_improve_lines_are_items():
    items = mod.items_from_research(BACKTICKED)
    assert set(items) == {"the next new thing", "kanban-worker-no-terminal-call", "vllm-v0.29-0-watch"}


def test_roles_that_rename_the_item_still_count():
    items = mod.items_from_research(BACKTICKED)
    votes = {
        "ada":     mod.scores_from_note(_note([("team-improve: kanban-worker-no-terminal-call", 3, "t_1"), ("The Next New Thing — \"Amazing\"", 1, ""), ("team-improve: vllm-v0.29-0-watch", 1, "")])),
        "bash":    mod.scores_from_note(_note([("kanban-worker-no-terminal-call (team-improve, proposed card)", 3, "t_2"), ("NNT: The Next New Thing — \"Amazing\"", 1, ""), ("vllm-v0.29-0-watch", 0, "")])),
        "vera":    mod.scores_from_note(_note([("kanban-worker-no-terminal-call", 3, "t_3"), ("The Next New Thing", 0, ""), ("something of her own", 2, "x")])),
        "lila":    mod.scores_from_note(_note([("Kanban last snapshot 2026-09-17", 3, "same pain, other name"), ("The Next New Thing", 1, ""), ("Clippy", 2, "x")])),
    }
    rows = {r["item"]: r for r in mod.rank(items, votes)}
    top = rows["kanban-worker-no-terminal-call"]
    assert top["scores"] == {"ada": 3, "bash": 3, "vera": 3} and top["top"] is True   # lila's other name is an abstention
    assert rows["The Next New Thing"]["breadth"] == 4 and rows["The Next New Thing"]["top"] is False
    # a slug is a whole token, never a substring of another word
    assert mod.match_item("not-kanban-worker-no-terminal-callers", items) == "not-kanban-worker-no-terminal-callers"


def test_two_voters_are_not_every_role(monkeypatch):
    items = mod.items_from_research(BACKTICKED)
    two = {
        "ada":  mod.scores_from_note(_note([("kanban-worker-no-terminal-call", 3, "a"), ("The Next New Thing", 1, ""), ("vllm-v0.29-0-watch", 0, "")])),
        "vera": mod.scores_from_note(_note([("kanban-worker-no-terminal-call", 2, "b"), ("The Next New Thing", 0, ""), ("vllm-v0.29-0-watch", 1, "")])),
    }
    assert all(r["top"] is False for r in mod.rank(items, two))          # 09-16: 2 of 5 made a TOP card
    monkeypatch.setattr(mod, "QUORUM", 0)                                  # kill switch = the old rule
    assert mod.rank(items, two)[0]["top"] is True
    monkeypatch.setattr(mod, "QUORUM", 3)
    monkeypatch.setattr(mod, "FUZZY", False)
    assert mod.match_item("team-improve: vllm-v0.29-0-watch", items) == "team-improve: vllm-v0.29-0-watch"


def test_absent_roles_are_named(tmp_path):
    (tmp_path / "team-research-2026-09-17.md").write_text(BACKTICKED)
    (tmp_path / "ada-improvement-ideas-2026-09-17.md").write_text(_note([("kanban-worker-no-terminal-call", 3, "a"), ("The Next New Thing", 1, ""), ("vllm-v0.29-0-watch", 0, "")]))
    assert mod.main(["--day", "2026-09-17", "--inbox", str(tmp_path), "--out-dir", str(tmp_path / "imp")]) == 0
    md = (tmp_path / "team-eval-2026-09-17.md").read_text()
    assert "No note from: bash, lila, vera, steward." in md and "nnt-agreed" not in md
    assert json.loads((tmp_path / "imp" / "rank-2026-09-17.json").read_text())["absent"] == ["bash", "lila", "vera", "steward"]
