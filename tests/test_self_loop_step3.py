"""Self-loop step 3 (the User 09-28): the gate lives in the filer, and landed means reviewed + committed."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours

import idea_ledger  # noqa: E402
import job_status  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


DAY = "2099-01-02"
EVAL = f"""# team-eval-{DAY}

## Proposed cards
- nnt-agreed: tencent/weknora — kpi=ops_tok_s — 3 of 3 roles, all >= 2 (sum 7)
- nnt-agreed: drain the ready lane — kpi=kanban_oldest_ready_sec — 3 of 3 roles (sum 6)
- nnt-agreed: alibaba/open-code-review — kpi=crew_tok_s — 3 of 3 roles (sum 6)
"""
RESEARCH = f"""# team-research-{DAY}

## Proposed cards
- `team-improve: a bullet Scout wrote that nobody voted top (bucket=improve-ops)` — why
"""


class _Hermes:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.n = 0

    def __call__(self, argv, **kw):
        self.calls.append([str(a) for a in argv])

        class R:
            returncode = 0
            stderr = ""
        r = R()
        if "create" in argv:
            self.n += 1
            r.stdout = f"Created t_{self.n:08x} (todo)"
        else:
            r.stdout = ""
        return r

    def created(self) -> list[str]:
        return [c[c.index("create") + 1] for c in self.calls if "create" in c]


@pytest.fixture
def window(tmp_path, monkeypatch):
    fl = _load("filer_step3", "crew/scripts/steward-file-architect-cards.py")
    inbox, imp = tmp_path / "inbox", tmp_path / "imp"
    inbox.mkdir()
    imp.mkdir()
    (inbox / f"team-eval-{DAY}.md").write_text(EVAL)
    (inbox / f"team-research-{DAY}.md").write_text(RESEARCH)
    monkeypatch.setenv("KREW_DAY", DAY)
    monkeypatch.setenv("KREW_JOB_STATUS_DIR", str(tmp_path / "status"))
    for attr, val in (("INBOX", inbox), ("RANK_DIR", imp), ("PROPOSALS", imp / "proposals.jsonl"),
                      ("SEEN", imp / "seen.jsonl"), ("ACTIONS", imp / "actions.jsonl")):
        monkeypatch.setattr(fl, attr, val)
    monkeypatch.setattr(fl, "cards_off", lambda: "")
    monkeypatch.setattr(fl, "seen_on_board", lambda *a, **k: None)
    monkeypatch.setattr(fl, "seen_in_ledger", lambda *a, **k: None)
    monkeypatch.setattr(fl, "open_kpi_card", lambda kid: "")
    h = _Hermes()
    monkeypatch.setattr(fl.subprocess, "run", h)
    return fl, imp, h, tmp_path


def _rank(imp: Path, voters, absent=(), discounted=None):
    (imp / f"rank-{DAY}.json").write_text(json.dumps({"day": DAY, "voters": list(voters), "absent": list(absent),
                                                      "discounted": discounted or {}, "quorum": 3, "rows": []}))


def test_below_quorum_nothing_is_filed_but_the_ledger_is_written(window):
    fl, imp, h, tmp = window
    _rank(imp, ["ada", "bash", "lila"], discounted={"lila": "no scores"})
    assert fl.main(["--window"]) == 0
    assert h.created() == []
    st = job_status.read("improve-file", DAY, d=tmp / "status")
    assert st["status"] == "ok" and "2 real voter(s) of quorum 3" in st["reason"]
    keys = {r["key"] for r in idea_ledger.load(imp / "ideas.jsonl")}
    assert "repo:tencent/weknora" in keys and any(k.startswith("team-improve:a-bullet-scout") for k in keys)


def test_a_named_missing_voice_does_not_block_three_real_voters_and_the_cap_holds(window):
    fl, imp, h, tmp = window
    _rank(imp, ["ada", "bash", "lila", "vera"], absent=["steward"])
    assert fl.main(["--window"]) == 0
    made = h.created()
    assert len(made) == 2, made
    assert made[0].startswith("team-improve: tencent/weknora") and made[1].startswith("team-improve: drain the ready lane")
    assert not any("a bullet Scout wrote" in t for t in made), "Scout's own bullet is ledger-only"
    held = [r for r in idea_ledger.load(imp / "ideas.jsonl") if r["event"] == "held"]
    assert [r["key"] for r in held] == ["repo:alibaba/open-code-review"]
    assert "2 team-improve, cap 2" in job_status.read("improve-file", DAY, d=tmp / "status")["reason"]


def test_a_board_wide_kpi_is_filed_once_as_unmeasured_and_a_second_open_one_is_refused(window, monkeypatch):
    fl, imp, h, tmp = window
    _rank(imp, ["ada", "bash", "lila"])
    bodies = []
    orig = h.__call__
    monkeypatch.setattr(fl.subprocess, "run", lambda argv, **kw: (bodies.append(argv[argv.index("--body") + 1])
                                                                  if "--body" in argv else None) or orig(argv, **kw))
    fl.main(["--window"])
    body = next(b for b in bodies if "kanban_oldest_ready_sec" in b)
    assert "BOARD-WIDE: filed as unmeasured" in body
    h.calls.clear()
    monkeypatch.setattr(fl, "open_kpi_card", lambda kid: "t_open" if kid.startswith("kanban_") else "")
    (imp / "seen.jsonl").unlink()
    fl.main(["--window"])
    assert not any("drain the ready lane" in t for t in h.created())
    assert any(r["event"] == "refused" and "t_open" in r["data"]["why"] for r in idea_ledger.load(imp / "ideas.jsonl"))


def test_the_fence_still_writes_the_status_line_and_files_nothing(window, monkeypatch):
    fl, imp, h, tmp = window
    _rank(imp, ["ada", "bash", "lila"])
    monkeypatch.setattr(fl, "cards_off", lambda: "/x/architect-cards.off")
    assert fl.main(["--window"]) == 0 and h.calls == []
    assert job_status.read("improve-file", DAY, d=tmp / "status")["reason"].startswith("fenced (architect-cards.off)")


def test_there_is_one_filing_path_and_the_ops_run_keeps_its_own():
    calls = []
    for sh in (ROOT / "crew" / "scripts").glob("*.sh"):
        for line in sh.read_text().splitlines():
            if "steward-file-architect-cards.py" in line and not line.lstrip().startswith("#"):
                calls.append((sh.name, line.strip()))
    assert calls == [("steward-architect-run.sh", 'python3 "$ROOT/scripts/steward-file-architect-cards.py" --ops || true')]
    sched = {j["name"]: j for j in json.loads((ROOT / "config/window-schedule.example.json").read_text())["jobs"]}
    assert sched["improve-file"]["after"] == ["team-eval"] and sched["improve-file"]["status_line"] == "improve-file"
    assert sched["improvement-outcomes"]["after"] == ["improve-file"]
    unit = (ROOT / "crew/units/krew-improve-file.service").read_text()
    assert "steward-file-architect-cards.py --window" in unit and "krew-day.env" in unit
    ev = (ROOT / "crew/scripts/team-eval-run.sh").read_text()
    assert "below quorum" in ev and "steward-file-architect-cards" not in ev


# --- landed = reviewed + committed ----------------------------------------------------------

@pytest.fixture
def out(monkeypatch):
    m = _load("outcomes_step3", "crew/scripts/improvement-outcomes.py")
    return m


def _card(**kw):
    c = {"id": "t_try", "title": "try: team-improve: x", "status": "done", "completed_at": 1000.0, "comments": [],
         "body": "", "review": "", "child_ids": ["t_piece"]}
    c.update(kw)
    return c


def test_a_failed_review_never_lands(out):
    assert out.land_verdict(_card(review="REVISE — decisions row"))["stage"] == "closed, not landed (review REVISE)"


def test_review_not_armed_is_named_and_a_commit_is_still_required(out, monkeypatch):
    monkeypatch.setattr(out, "review_armed", lambda: False)
    monkeypatch.setattr(out, "commit_for", lambda c: "")
    v = out.land_verdict(_card())
    assert v == {"stage": "closed, not landed (no commit found)", "review": "review not armed"}
    monkeypatch.setattr(out, "commit_for", lambda c: "abc123456789")
    v = out.land_verdict(_card())
    assert v["stage"] == "landed" and v["review"] == "review not armed" and v["sha"] == "abc123456789"


def test_armed_and_unreviewed_is_not_landed(out, monkeypatch):
    monkeypatch.setattr(out, "review_armed", lambda: True)
    assert out.land_verdict(_card())["stage"] == "closed, not landed (no review yet)"


def test_the_commit_join_takes_a_subject_tag_never_a_prose_mention(out, tmp_path):
    (tmp_path / ".git").mkdir()
    subjects = {
        "log": "aaa111\tSelf-loop step 2: the ledger stays with t_piece's owner\n"
               "bbb222\tlint gate: habit fix must stay wired (t_piece)\n",
    }

    def git(repo, *args):
        if args[0] == "log":
            return subjects["log"]
        if args[0] == "rev-parse":
            return "ccc333ffffffffff" if args[-1].startswith("ccc333") else ""
        return ""
    out._SUBJECTS.clear()
    assert out.commit_for(_card(), [tmp_path], git=git) == "bbb222"
    out._SUBJECTS.clear()
    subjects["log"] = "aaa111\tthe ledger stays with t_piece's owner\n"
    assert out.commit_for(_card(), [tmp_path], git=git) == ""
    out._SUBJECTS.clear()
    assert out.commit_for(_card(comments=[{"body": "done\nsha: ccc333f"}]), [tmp_path], git=git) == "ccc333ffffff"
    out._SUBJECTS.clear()
    subjects["log"] = "ddd444\tt_try: worker ships it\n"
    assert out.commit_for(_card(), [tmp_path], git=git) == "ddd444"


def test_a_board_wide_kpi_is_unmeasured(out):
    row = {"stage": "landed", "landed_at": 0.0}
    r = out.measure(dict(row), {"baseline": 9}, {"kanban_blocked": {"direction": "down"}}, "kanban_blocked", 1e10)
    assert r["measure"] == "KPI kanban_blocked is board-wide: unmeasured"
    assert idea_ledger.kpi_scope("ops_tok_s") == "unit" and idea_ledger.kpi_scope("kanban_blocked") == "board"
