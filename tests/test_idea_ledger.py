"""Self-loop step 2 (the User 09-28): the idea ledger, and the filer recording before the fence."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours

import idea_ledger as L  # noqa: E402


def _filer():
    spec = importlib.util.spec_from_file_location("filer_il", ROOT / "crew" / "scripts" / "steward-file-architect-cards.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


NOTE = """# team-research-2099-01-02

## NNT repos
- `nnt-pick: Tencent/WeKnora` — https://github.com/Tencent/WeKnora — memory — a wiki (★30601)
- `nnt-pick: alibaba/open-code-review` — https://github.com/alibaba/open-code-review — ops — review
- `team-improve: add a Hermes observability check — verify cron/kanban ledgers (bucket=expand-caps)` — cheap

## Proposed cards
- `team-improve: add a Hermes observability check — verify cron/kanban ledgers (bucket=expand-caps)` — restated
- team-improve: none this cycle
- idea: drain-ready-lane | drain the ready lane before the next tick
- nnt-agreed: tencent/weknora — kpi=kanban_blocked — 3 of 3 roles
"""


def test_the_key_is_the_filers_idempotency_key_byte_for_byte():
    fl = _filer()
    for kind, t in (("team-improve", "add a Hermes observability check — verify cron/kanban (x)"),
                    ("architect-watch", "vLLM 0.30 — re-baseline"), ("team-improve", "Kit")):
        assert L.slug_key(kind, t) == fl.slug_key(kind, t)


def test_repo_keys_are_lowercase_and_only_from_a_repo_field():
    assert L.repo_key("Tencent/WeKnora") == "repo:tencent/weknora"
    assert L.repo_key("https://github.com/Tencent/WeKnora") == "repo:tencent/weknora"
    assert L.repo_key("verify cron/kanban ledgers") == "", "a house change that mentions a/b is not a repo"


def test_a_note_parses_once_per_key_and_skips_none():
    ev = L.parse_note(NOTE)
    keys = [(e["key"], e["event"]) for e in ev]
    assert ("repo:tencent/weknora", "seen") in keys and ("repo:alibaba/open-code-review", "seen") in keys
    assert keys.count(("team-improve:add-a-hermes-observability-check", "seen")) == 1, "the restatement is one row"
    assert ("idea:drain-ready-lane", "seen") in keys
    assert ("repo:tencent/weknora", "agreed") in keys
    assert not any("none" in k for k, _ in keys)
    assert next(e for e in ev if e["key"] == "idea:drain-ready-lane")["text"] == "drain the ready lane before the next tick"


def test_record_is_idempotent_across_the_archive_move(tmp_path):
    led = tmp_path / "ideas.jsonl"
    a = tmp_path / "inbox" / "team-research-2099-01-02.md"
    b = tmp_path / "inbox" / "krew-reports" / "2099-01-02" / "team-research-2099-01-02.md"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(NOTE)
    n1 = L.record_note(a, "2099-01-02", path=led)
    assert n1 == len(L.parse_note(NOTE))
    assert L.record_note(b, "2099-01-02", path=led) == 0, "the archived copy is the same note"


def test_rebuild_reads_the_archive_and_rank_and_folds_nights(tmp_path, monkeypatch):
    inbox, imp = tmp_path / "inbox", tmp_path / "imp"
    monkeypatch.setattr(L, "INBOX", inbox)
    monkeypatch.setattr(L, "KREW_REPORTS", inbox / "krew-reports")
    monkeypatch.setattr(L, "IMPROVE_DIR", imp)
    imp.mkdir(parents=True)
    for i, day in enumerate(("2099-01-01", "2099-01-02", "2099-01-03")):
        p = (inbox / "krew-reports" / day if i < 2 else inbox) / f"team-research-{day}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("- `nnt-pick: Tencent/WeKnora` — url — memory\n")
        (imp / f"rank-{day}.json").write_text(json.dumps({"rows": [
            {"item": "Tencent/WeKnora`", "scores": {"ada": 1, "bash": 0, "lila": 1}, "sum": 2, "top": False, "rank": 3}]}))
    (imp / "seen.jsonl").write_text(json.dumps({"day": "2099-01-02", "key": "team-improve:x", "task": "t_1",
                                                "status": "filed", "note": "n"}) + "\n")
    led = tmp_path / "ideas.jsonl"
    n = L.rebuild("2099-01-01", "2099-01-03", path=led)
    assert n["notes"] == 3 and n["ranks"] == 3, "the two archived nights are read too"
    assert L.rebuild("2099-01-01", "2099-01-03", path=led)["events"] == 0
    st = L.fold(L.load(led))
    w = st["repo:tencent/weknora"]
    assert w["nights"] == 3 and w["state"] == "debated(3)" and w["best_sum"] == 2
    assert st["team-improve:x"]["state"] == "filed"
    a, b = L.views(L.load(led), tmp_path)
    assert json.loads(a.read_text().splitlines()[0])["task"] == "t_1"


def test_the_kill_switch_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_IDEA_LEDGER", "0")
    assert L.record([{"key": "k", "event": "seen"}], "2099-01-02", "s", path=tmp_path / "x.jsonl") == 0
    assert not (tmp_path / "x.jsonl").exists()


def test_fenced_filer_records_the_ledger_and_the_proposal_and_creates_nothing(tmp_path, monkeypatch):
    fl = _filer()
    inbox, imp = tmp_path / "inbox", tmp_path / "imp"
    inbox.mkdir()
    imp.mkdir()
    (inbox / "team-research-2099-01-02.md").write_text(NOTE)
    (imp / "rank-2099-01-02.json").write_text(json.dumps({"rows": [
        {"item": "alibaba/open-code-review`", "scores": {"ada": 2}, "sum": 2, "top": False, "rank": 1}]}))
    monkeypatch.setenv("KREW_DAY", "2099-01-02")
    monkeypatch.setenv("KREW_ARCHITECT_CARDS", "0")
    monkeypatch.setattr(fl, "INBOX", inbox)
    monkeypatch.setattr(fl, "RANK_DIR", imp)
    monkeypatch.setattr(fl, "PROPOSALS", imp / "proposals.jsonl")
    monkeypatch.setattr(fl, "SEEN", imp / "seen.jsonl")
    monkeypatch.setattr(L, "LEDGER", tmp_path / "must-not-be-used.jsonl")
    calls = []
    monkeypatch.setattr(fl.subprocess, "run", lambda argv, **kw: calls.append(argv) or (_ for _ in ()).throw(
        AssertionError(f"fenced filer ran {argv}")))
    assert fl.main() == 0
    assert calls == [], "under the fence nothing calls hermes (no kanban create, no block)"
    rows = L.load(imp / "ideas.jsonl")
    assert {(r["key"], r["event"]) for r in rows} >= {
        ("repo:tencent/weknora", "seen"), ("repo:tencent/weknora", "agreed"),
        ("repo:alibaba/open-code-review", "debated"), ("idea:drain-ready-lane", "seen")}
    agreed = next(r for r in rows if r["event"] == "agreed")
    assert agreed["data"]["proposal"]["kind"] == "nnt-agreed", "the proposals view can be rebuilt from the ledger"
    props = [json.loads(l) for l in (imp / "proposals.jsonl").read_text().splitlines()]
    assert len(props) == 1 and props[0]["title"].startswith("tencent/weknora")
    assert not (imp / "seen.jsonl").exists(), "nothing was filed, so the filer's seen ledger is untouched"
    fl.main()
    assert len(L.load(imp / "ideas.jsonl")) == len(rows), "a second run the same night records nothing new"


def test_the_filer_writes_the_ledger_beside_its_own_ledgers(tmp_path, monkeypatch):
    """The 09-28 leak: the older filer tests redirect SEEN / PROPOSALS, so the ledger follows them."""
    fl = _filer()
    monkeypatch.setattr(fl, "SEEN", tmp_path / "seen.jsonl")
    assert fl.ledger_path() == tmp_path / "ideas.jsonl"
    assert fl.ledger_path(tmp_path / "x" / "proposals.jsonl") == tmp_path / "x" / "ideas.jsonl"
