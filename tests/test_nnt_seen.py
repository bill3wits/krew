"""nnt-seen + repo dedupe (t_68837b13): a repo the pick log named inside the
8-day window is seen, ranked, but not re-debated (spec
nnt-picklog-dedupe-spec-2026-09-27.md)."""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SC = ROOT / "crew" / "scripts"
sys_path_saved = None
import sys  # noqa: E402
sys.path.insert(0, str(SC))
spec = importlib.util.spec_from_file_location("nnt_seen", SC / "nnt-seen.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

RANK_SPEC = importlib.util.spec_from_file_location("team_eval_rank", SC / "team-eval-rank.py")
rank = importlib.util.module_from_spec(RANK_SPEC)
RANK_SPEC.loader.exec_module(rank)

# One shared, test-controlled copy of nnt-seen for the RANKER. The rank hook's
# _nnt_seen() mints a fresh module object per call, so monkeypatching a
# returned instance never sticks; instead hold ONE instance and repoint the
# factory at it.
RANK_SEEN_COPY = rank._nnt_seen()
rank._nnt_seen = lambda: RANK_SEEN_COPY


@pytest.fixture(autouse=True)
def _no_live_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(rank, "PROFILES", tmp_path / "no-profiles")


@pytest.fixture(autouse=True)
def _no_live_ledger(tmp_path, monkeypatch):
    """The RANKER loads its OWN nnt-seen copy (a separate module object) and its
    constants point at the live paths. A test slug that reached the rank hook
    would land in the LIVE ledger / seen store, so rebind that copy's paths too."""
    copy = RANK_SEEN_COPY
    monkeypatch.setattr(copy, "INBOX", tmp_path / "live-inbox")
    monkeypatch.setattr(copy, "COLLECT_ROOT", tmp_path / "live-collect")
    monkeypatch.setattr(copy, "IMPROVE_DIR", tmp_path / "live-improve")
    monkeypatch.setattr(copy, "SEEN_PATH", tmp_path / "live-collect" / "nnt-seen.json")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """Isolate both hooks from the live inbox / ledger / seen store."""
    inbox = tmp_path / "inbox"; inbox.mkdir()
    collect = tmp_path / "collect"
    improve = tmp_path / "improve"
    seen = collect / "nnt-seen.json"
    ledger = improve / "proposals.jsonl"
    monkeypatch.setattr(mod, "INBOX", inbox)
    monkeypatch.setattr(mod, "COLLECT_ROOT", collect)
    monkeypatch.setattr(mod, "IMPROVE_DIR", improve)
    monkeypatch.setattr(mod, "SEEN_PATH", seen)
    yield tmp_path


# --- pick-log parsing -------------------------------------------------------

NOTE = """# research
## NNT picks — for the team
- `nnt-pick: Alibaba/Open-Code-Review` — https://github.com/alibaba/open-code-review — ops — why
- `nnt-pick: davila7/claude-code-templates` — https://github.com/davila7/claude-code-templates — ops — hyphens
- `nnt-pick: The Next New Thing` — "Amazing" (2026-09-15, https://youtu.be/x) — brains
- team-improve: some-slug — bucket=improve-ops — not a pick
"""


def test_pick_slugs_take_the_first_field_case_insensitive():
    slugs = mod.picks_in(NOTE)
    assert "alibaba/open-code-review" in slugs          # case dropped at the join
    assert "davila7/claude-code-templates" in slugs     # hyphens inside the slug survive
    assert not any("next new" in s for s in slugs)      # a prose title is not a slug
    assert not any(s.startswith("some-slug") for s in slugs)  # team-improve is not a pick


def test_pick_log_window_reads_notes_and_ledger_in_window(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"; inbox.mkdir()
    improve = tmp_path / "improve"; improve.mkdir()
    seen = tmp_path / "collect" / "nnt-seen.json"
    monkeypatch.setattr(mod, "INBOX", inbox)
    monkeypatch.setattr(mod, "IMPROVE_DIR", improve)
    monkeypatch.setattr(mod, "SEEN_PATH", seen)
    today = date.today()
    (inbox / f"team-research-{today.isoformat()}.md").write_text(
        "- nnt-pick: a/b — https://github.com/a/b — ops — x\n")
    old = (today - timedelta(days=10)).isoformat()      # outside the 8-day window
    (inbox / f"team-research-{old}.md").write_text("- nnt-pick: c/d — u — ops — x\n")
    (improve / "proposals.jsonl").write_text(
        json.dumps({"ts": (today - timedelta(days=2)).isoformat() + "T20:00:00", "kind": "nnt-agreed",
                    "title": "e/f — kpi=ops_tok_s — 3 of 4 roles, all >= 2 (sum 7) — x", "status": "agreed"}) + "\n"
        + json.dumps({"ts": old + "T20:00:00", "kind": "nnt-agreed", "title": "g/h — kpi=ops_tok_s", "status": "withdrawn"}) + "\n"
        + json.dumps({"ts": (today - timedelta(days=1)).isoformat() + "T20:00:00", "kind": "team-improve",
                      "title": "prose, no repo", "status": "agreed"}) + "\n")
    log = mod.pick_log_window(8)
    assert log["a/b"] == today.isoformat()
    assert log["e/f"] == (today - timedelta(days=2)).isoformat()   # the agree counts
    assert "c/d" not in log and "g/h" not in log                    # outside the window
    assert not any("prose" in k for k in log)                       # a prose ledger line names no repo


# --- the seen store ----------------------------------------------------------

def test_mark_seen_is_idempotent_and_prunes(tmp_path, monkeypatch):
    seen = tmp_path / "nnt-seen.json"
    assert mod.seen_map(path=seen) == {}                               # absent = empty, no crash
    today = date.today()
    assert mod.mark_seen(today.isoformat(), ["a/b", "a/b"], path=seen) == 1     # dedup within the call
    assert mod.mark_seen(today.isoformat(), ["a/b"], path=seen) == 0            # same day again = no-op
    old = (today - timedelta(days=9)).isoformat()
    assert mod.mark_seen(old, ["x/y"], path=seen) == 1
    assert "x/y" not in mod.seen_map(path=seen)                       # pruned: outside the window
    data = json.loads(seen.read_text())
    assert set(data) == {"a/b"} and data["a/b"][today.isoformat()] == today.isoformat()


def test_seen_store_survives_an_inbox_prune(tmp_path, monkeypatch):
    """A slug whose note left the inbox is still seen (the store, not the note, is the record)."""
    inbox = tmp_path / "inbox"; inbox.mkdir()
    seen = tmp_path / "nnt-seen.json"
    monkeypatch.setattr(mod, "INBOX", inbox)
    monkeypatch.setattr(mod, "SEEN_PATH", seen)
    monkeypatch.setattr(mod, "ledger_rows", lambda window=8, before="": [])   # not the live agreed ledger
    today = date.today()
    mod.mark_seen(today.isoformat(), ["a/b"], path=seen)
    assert mod.seen_map(path=seen) == {"a/b": today.isoformat()}
    # the note that named it is renamed away; the store still says seen
    (inbox / f"team-research-{today.isoformat()}.md").write_text("")
    assert mod.pick_log_window(8) == {}                                # nothing in the inbox
    assert "a/b" in mod.seen_map(path=seen)                           # but the store remembers
    # corrupt store = empty, never a crash
    seen.write_text("{not json")
    assert mod.seen_map(path=seen) == {}


def test_kill_switch_turns_every_hook_off(tmp_path, monkeypatch):
    seen = tmp_path / "nnt-seen.json"
    monkeypatch.setattr(mod, "SEEN_PATH", seen)
    monkeypatch.setenv("NNT_SEEN_REPOS", "0")
    assert not mod.enabled()
    assert mod.mark_seen(date.today().isoformat(), ["a/b"], path=seen) == 0
    assert not seen.exists()
    monkeypatch.setenv("NNT_SEEN_REPOS", "1")
    assert mod.enabled()


# --- the ranker hook ----------------------------------------------------------

RESEARCH = """# research
## NNT picks — for the team
- nnt-pick: a/b — https://youtu.be/a — ops — CPU path
- nnt-pick: c/d — https://youtu.be/b — render — tool
- nnt-pick: The Next New Thing — "Amazing" (2026-09-15, https://youtu.be/x) — brains
## bash (host)
- team-improve: unit restart wrapper bucket=improve-ops
"""


def _note(scores):
    return "# eval\nlede: x\n\n## Scores\n" + "".join(f"score: {k} = {v} — {w}\n" for k, v, w in scores)


def _rank_main(argv):
    """The RANKER's main (mod is nnt-seen, which has no main)."""
    return rank.main(argv)


def test_repo_slug_key_and_prose_items_are_untouched():
    assert rank.repo_slug_key("a/b") == "a/b"
    assert rank.repo_slug_key("`a/b` — ops — x") == "a/b"
    assert rank.repo_slug_key("team-improve: unit-restart") == ""
    assert rank.repo_slug_key("The Next New Thing — \"Amazing\"") == ""
    assert rank.repo_slug_key("unit restart wrapper bucket=improve-ops") == ""


def test_seen_repo_is_ranked_but_not_refiled(tmp_path, monkeypatch):
    """Seen (in the pick log within the window) => top=True keeps the scores visible,
    but the item is excluded from the nnt-agreed re-filing and marked in the table."""
    inbox = tmp_path / "inbox"; inbox.mkdir()
    today = date.today()
    # a/b was picked last night, c/d is new
    (inbox / f"team-research-{today.isoformat()}.md").write_text(RESEARCH)
    (inbox / f"team-research-{(today - timedelta(days=1)).isoformat()}.md").write_text(
        "- nnt-pick: a/b — https://github.com/a/b — ops — picked yesterday\n")
    seen = tmp_path / "nnt-seen.json"
    monkeypatch.setattr(mod, "INBOX", inbox)
    monkeypatch.setattr(mod, "SEEN_PATH", seen)
    # the rank hook reads the SAME inbox picks AND marks the store the test
    # asserts on (its nnt-seen copy defaults to the live store paths)
    copy = rank._nnt_seen()
    monkeypatch.setattr(copy, "INBOX", inbox)
    monkeypatch.setattr(copy, "SEEN_PATH", seen)
    for r in ("ada", "bash", "lila", "steward"):
        (inbox / f"{r}-improvement-ideas-{today.isoformat()}.md").write_text(
            _note([("a/b", 3, "real"), ("c/d", 3, "real"), ("The Next New Thing", 2, "ok"),
                   ("unit restart wrapper bucket=improve-ops", 3, "mine")]))
    assert _rank_main(["--day", today.isoformat(), "--inbox", str(inbox), "--out-dir", str(tmp_path / "imp")]) == 0
    md = (inbox / f"team-eval-{today.isoformat()}.md").read_text()
    assert "nnt-agreed: c/d" in md                       # the NEW repo is filed
    assert "nnt-agreed: a/b" not in md                   # the seen one is not re-filed
    assert "a/b (seen)" in md                           # but the scores are still visible
    j = json.loads((tmp_path / "imp" / f"rank-{today.isoformat()}.json").read_text())
    rows = {r["item"]: r for r in j["rows"]}
    assert rows["a/b"]["seen"] is True and rows["a/b"]["top"] is True and rows["a/b"]["scores"] == {"ada": 3, "bash": 3, "lila": 3, "steward": 3}
    assert rows["c/d"].get("seen") is not True       # the NEW repo is not demoted
    assert "seen" not in rows["The Next New Thing"]       # prose titles are never demoted
    assert "seen" not in rows["unit restart wrapper bucket=improve-ops"]
    # and tonight's pick is in the store for tomorrow
    store = json.loads(seen.read_text()) if seen.exists() else {}
    assert store.get("c/d", {}).get(today.isoformat()) == today.isoformat()


def test_seen_kill_switch_restores_the_old_ranker(tmp_path, monkeypatch):
    monkeypatch.setenv("NNT_SEEN_REPOS", "0")
    monkeypatch.setattr(rank, "SEEN", False)             # module constant read at import time
    inbox = tmp_path / "inbox"; inbox.mkdir()
    today = date.today()
    (inbox / f"team-research-{today.isoformat()}.md").write_text(RESEARCH)
    for r in ("ada", "bash", "lila", "steward"):
        (inbox / f"{r}-improvement-ideas-{today.isoformat()}.md").write_text(
            _note([("a/b", 3, "real"), ("c/d", 3, "real"), ("The Next New Thing", 2, "ok"),
                   ("unit restart wrapper bucket=improve-ops", 3, "mine")]))
    assert _rank_main(["--day", today.isoformat(), "--inbox", str(inbox), "--out-dir", str(tmp_path / "imp")]) == 0
    md = (inbox / f"team-eval-{today.isoformat()}.md").read_text()
    assert "nnt-agreed: a/b" in md and " (seen)" not in md     # the old ranker re-files everything top


def test_seen_store_absent_is_the_old_behaviour(tmp_path, monkeypatch):
    """Fresh house: no seen store, no prior notes => nothing seen, nothing crashes."""
    inbox = tmp_path / "inbox"; inbox.mkdir()
    today = date.today()
    (inbox / f"team-research-{today.isoformat()}.md").write_text(RESEARCH)
    for r in ("ada", "bash", "lila", "steward"):
        (inbox / f"{r}-improvement-ideas-{today.isoformat()}.md").write_text(
            _note([("a/b", 3, "real"), ("c/d", 3, "real"), ("The Next New Thing", 2, "ok"),
                   ("unit restart wrapper bucket=improve-ops", 3, "mine")]))
    # point the store at an empty dir: nothing has ever been seen
    monkeypatch.setattr(mod, "INBOX", inbox)
    monkeypatch.setattr(mod, "SEEN_PATH", tmp_path / "empty" / "nnt-seen.json")
    (tmp_path / "empty").mkdir(exist_ok=True)
    assert _rank_main(["--day", today.isoformat(), "--inbox", str(inbox), "--out-dir", str(tmp_path / "imp")]) == 0
    md = (inbox / f"team-eval-{today.isoformat()}.md").read_text()
    assert "nnt-agreed: a/b" in md and "nnt-agreed: c/d" in md  # a first night files every top repo
