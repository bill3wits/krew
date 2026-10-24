"""The improvement round, end to end (2026-09-19): Scout's lines wait for the team vote,
a missing voice is found in its profile home, the Ops note's numbered heading files,
a try carries its KPI + baseline, every try is followed, and the morning pages rank it."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m          # a module-level @dataclass looks its module up
    spec.loader.exec_module(m)
    return m


filer = _load("steward_file_cards_round", "crew/scripts/steward-file-architect-cards.py")
rank = _load("team_eval_rank_round", "crew/scripts/team-eval-rank.py")
out = _load("improvement_outcomes_round", "crew/scripts/improvement-outcomes.py")
kl = _load("kanban_loop_round", "crew/scripts/kanban-loop.py")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _loop_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the module's import-time paths stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(kl, tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _seen_store_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the ranker loads nnt-seen.py fresh each call, whose store path is fixed at import under the
    home; hand it one copy whose paths are in tmp (tests/krew_home_guard.py)."""
    try:
        seen = rank._nnt_seen()
    except FileNotFoundError:
        return
    keep_in_tmp(seen, tmp_path, monkeypatch)
    monkeypatch.setattr(rank, "_nnt_seen", lambda: seen)
tdaily = _load("zoey_team_daily_round", "crew/scripts/zoey-team-daily.py")


@pytest.fixture(autouse=True)
def _landed_by_rule(monkeypatch):
    """These tests measure a landed try. Self-loop step 3's landing rule (a review word + a
    commit joined by rule) and its board-wide KPI rule have their own tests
    (tests/test_self_loop_step3.py); here every closed try has its commit."""
    monkeypatch.setattr(out, "commit_for", lambda card, *a, **k: "abc1234")
    monkeypatch.setattr(out, "review_armed", lambda: False)
    monkeypatch.setattr(out, "BOARD_WIDE_UNMEASURED", False)


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


def _verb(c):
    return c[c.index("kanban") + 1:]


@pytest.fixture
def filer_env(tmp_path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(filer.subprocess, "run", run)
    monkeypatch.setattr(filer, "seen_on_board", lambda key, head="": None)
    monkeypatch.setattr(filer, "SEEN", tmp_path / "seen.jsonl")
    monkeypatch.setattr(filer, "ACTIONS", tmp_path / "actions.jsonl")
    monkeypatch.setattr(filer, "FAILED", [])
    monkeypatch.setattr(filer, "GREEN_GATE", True)
    monkeypatch.setattr(filer, "WAIT_FOR_VOTE", True)
    monkeypatch.setattr(filer, "RANK_DIR", tmp_path)
    monkeypatch.setattr(filer, "today", lambda: dt.date(2026, 9, 18))
    return run


RESEARCH = ("# research\n\n## Proposed cards\n"
            "- team-improve: re-baseline vLLM at v0.29.0 (release-notes diff) bucket=improve-existing\n"
            "- architect-watch: LMCache head\n")


# --- 3a: Ada's numbered heading --------------------------------------------------------

def test_a_numbered_action_items_heading_files_its_ops_actions():
    text = ("# Ops efficiency — 2026-09-13\n\n## 2. Tracked changes\n- not a card\n\n"
            "## 3. Action items\n\n- `ops-action: keep-baseline — kanban_blocked — steady`\n"
            "- `ops-action: keep-baseline — kanban_open — steady`\n\n## 4. Next\n- ops-action: no\n")
    found = filer.parse_cards(text)
    assert [k for k, _ in found] == ["ops-action", "ops-action"]
    assert "kanban_blocked" in found[0][1]


# --- 1: Scout's lines wait for the vote --------------------------------------------------

def test_scouts_team_improve_waits_for_the_vote(tmp_path, filer_env):
    note = tmp_path / "team-research-2026-09-18.md"
    note.write_text(RESEARCH)
    assert filer.file_from(note) == 1                     # the watch card files; the improve is held
    creates = [_verb(c) for c in filer_env.calls if _verb(c)[0] == "create"]
    assert [c[1].split(":")[0] for c in creates] == ["architect-watch"]
    assert not (tmp_path / "seen.jsonl").exists() or "team-improve" not in (tmp_path / "seen.jsonl").read_text()


def test_after_the_vote_the_card_and_the_question_carry_rank_scores_and_kpi(tmp_path, filer_env):
    (tmp_path / "rank-2026-09-18.json").write_text(json.dumps({"rows": [
        {"item": "`mksglu/context-mode`", "rank": 1, "sum": 6, "scores": {"ada": 2}, "kpi": "ops_tok_s", "top": False},
        {"item": "re-baseline vLLM at v0.29.0 (release-notes diff) bucket=improve-existing", "rank": 5, "sum": 4,
         "scores": {"ada": 0, "bash": 2, "lila": 0, "steward": 2}, "kpi": "ops_tok_s", "top": False}]}))
    note = tmp_path / "team-research-2026-09-18.md"
    note.write_text(RESEARCH)
    assert filer.file_from(note) == 2
    creates = [_verb(c) for c in filer_env.calls if _verb(c)[0] == "create"]
    imp = next(c for c in creates if c[1].startswith("team-improve"))
    assert "kpi=ops_tok_s" in imp[1] and "team vote: rank 5 of 2, sum 4 (ada 0, bash 2, lila 0, steward 2)" in imp[1]
    # the idea's key is its first field — the vote suffix does not make a new idea
    key = imp[imp.index("--idempotency-key") + 1]
    assert key == filer.slug_key("team-improve", "re-baseline vLLM at v0.29.0 (release-notes diff) bucket=improve-existing")
    block = next(_verb(c) for c in filer_env.calls if _verb(c)[0] == "block")
    assert block[2].startswith(filer.GREEN_QUESTION) and "rank 5" in block[2]
    assert list(block[-2:]) == ["--kind", "needs_input"]


def test_an_unscored_line_still_files_and_says_so(tmp_path, filer_env):
    (tmp_path / "rank-2026-09-18.json").write_text(json.dumps({"rows": [{"item": "other", "rank": 1}]}))
    note = tmp_path / "team-research-2026-09-18.md"
    note.write_text(RESEARCH)
    filer.file_from(note)
    imp = next(_verb(c) for c in filer_env.calls if _verb(c)[0] == "create" and _verb(c)[1].startswith("team-improve"))
    assert "team vote: not scored" in imp[1]


def test_the_hold_is_only_for_the_research_note(tmp_path, filer_env):
    note = tmp_path / "architect-stack-2026-09-18.md"
    note.write_text("# stack\n\n## Proposed cards\n- team-improve: pin the sm120 wheel\n")
    assert filer.file_from(note) == 1


def test_kill_switch_files_at_once(tmp_path, filer_env, monkeypatch):
    monkeypatch.setattr(filer, "WAIT_FOR_VOTE", False)
    note = tmp_path / "team-research-2026-09-18.md"
    note.write_text(RESEARCH)
    assert filer.file_from(note) == 2


# --- 3b: a voice in its profile home ------------------------------------------------------

def test_the_ranker_counts_a_note_left_in_the_roles_profile_home(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "team-research-2026-09-18.md").write_text("# r\n- nnt-pick: foo/bar — x — y\n")
    for r in ("ada", "bash", "lila", "steward"):
        (inbox / f"{r}-improvement-ideas-2026-09-18.md").write_text("## Scores\nscore: foo/bar = 2 — ok kpi: ops_tok_s\n")
    home = tmp_path / "profiles" / "vera" / "home" / "Desktop" / "krew-deliverables"
    home.mkdir(parents=True)
    (home / "vera-improvement-ideas-2026-09-18.md").write_text("## Scores\nscore: foo/bar = 3 — removes a pain kpi: ops_tok_s\n")
    monkeypatch.setattr(rank, "PROFILES", tmp_path / "profiles")
    monkeypatch.setattr(rank, "PROFILE_HOME", True)
    assert rank.main(["--day", "2026-09-18", "--inbox", str(inbox), "--out-dir", str(tmp_path)]) == 0
    d = json.loads((tmp_path / "rank-2026-09-18.json").read_text())
    assert "vera" in d["voters"] and d["absent"] == []
    monkeypatch.setattr(rank, "PROFILE_HOME", False)
    rank.main(["--day", "2026-09-18", "--inbox", str(inbox), "--out-dir", str(tmp_path)])
    assert json.loads((tmp_path / "rank-2026-09-18.json").read_text())["absent"] == ["vera"]


def test_the_run_script_adopts_the_profile_home_note():
    sh = (ROOT / "crew" / "scripts" / "team-eval-run.sh").read_text()
    assert 'adopt "$p"' in sh and "TEAM_EVAL_PROFILE_HOME" in sh and "cp -p" in sh


# --- 2: the try carries its KPI and baseline ----------------------------------------------

def test_watch_line_reads_the_kpi_and_its_value_at_the_ruling(tmp_path):
    kp = tmp_path / "kpis.json"
    kp.write_text(json.dumps({"kpis": {"ops_tok_s": {"current": 64.0, "unit": "tok/s", "direction": "up"}}}))
    w = kl.watch_line("team-improve: vLLM — kpi=ops_tok_s — team vote: rank 5", "", kpis_path=kp, now=0)
    assert w.startswith("watch: kpi=ops_tok_s baseline=64.0 tok/s direction=up")
    assert "cannot be measured" in kl.watch_line("team-improve: x", "", kpis_path=kp, now=0)
    assert "baseline=unknown" in kl.watch_line("x — kpi=nope", "", kpis_path=kp, now=0)


def test_zoeys_try_stamps_the_watch_line_on_the_try_card(tmp_path, monkeypatch):
    kp = tmp_path / "kpis.json"
    kp.write_text(json.dumps({"kpis": {"ops_tok_s": {"current": 64.0, "unit": "tok/s", "direction": "up"}}}))
    monkeypatch.setattr(kl, "KPIS_JSON", kp)

    class Cli:
        def __init__(self):
            self.calls = []

        def __call__(self, *a):
            self.calls.append(list(a))
            return (0, "Created t_0000abcd (triage)\n") if a[0] == "create" else (0, "")
    c = kl.Card("t_1", "team-improve: vLLM — kpi=ops_tok_s", "kind=team-improve\ngate: green\n", "ready", "ada", "", 0,
                "steward-architect", comments=[("zoey", "try — worth it")])
    cli = Cli()
    monkeypatch.setattr(kl, "create_pinned", lambda cli_, *a, assignee: cli_("create", *a))
    assert kl.run_green([c], cli) == 1
    create = next(x for x in cli.calls if x[0] == "create")
    assert "watch: kpi=ops_tok_s baseline=64.0" in create[create.index("--body") + 1]


# --- 2 + 4: every try followed, the round ranked --------------------------------------------

def _board(tmp_path, tasks, links=()):
    d = tmp_path / "boards" / "b"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "kanban.db")
    con.executescript("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at REAL, completed_at REAL, body TEXT, result TEXT);"
                      "CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT);"
                      "CREATE TABLE task_links (parent_id TEXT, child_id TEXT);")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", tasks)
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.commit()
    con.close()
    return tmp_path / "boards"


def test_every_try_is_a_row_and_a_failed_review_is_not_landed(tmp_path):
    now = time.time()
    boards = _board(tmp_path, [
        ("t_try1", "try: team-improve: re-baseline vLLM — kpi=ops_tok_s — team vote: rank 5 of 12, sum 4", "done",
         now - 86400, now - 3600, "watch: kpi=ops_tok_s baseline=64.0 tok/s direction=up at the ruling x", None),
        ("t_impl", "Implement vLLM bump", "done", now - 80000, now - 7000, "", None),
        ("t_rev", "review: Implement vLLM bump", "done", now - 7000, now - 3700, "", "FAIL — 7 pins, not 3"),
        ("t_try2", "try: architect-try: sm120 wheel", "done", now - 86400, now - 3600,
         "watch: kpi=crew_tok_s baseline=50 tok/s direction=up at the ruling x", None),
        ("t_try3", "try: team-improve: adhd skill", "running", now - 3600, None, "", None),
    ], links=[("t_impl", "t_try1"), ("t_impl", "t_rev")])
    cards = out.board_cards(boards)
    kpis = {"ops_tok_s": {"current": 70.0, "direction": "up", "history": []},
            "crew_tok_s": {"current": 60.0, "direction": "up", "history": []}}
    rows = [out.with_now(r, kpis) for r in out.try_rows(cards, set(), kpis, now)]
    by = {r["try"]: r for r in rows}
    assert by["t_try1"]["stage"] == "closed, not landed (review FAIL)"
    assert by["t_try1"]["vote"].startswith("rank 5 of 12")
    assert by["t_try2"]["stage"] == "landed" and by["t_try2"]["baseline"] == 50.0 and by["t_try2"]["now"] == 60.0
    assert by["t_try2"]["measure"].startswith("due ")
    assert by["t_try3"]["stage"].startswith("in work (running")
    order = [r["try"] for r in sorted(rows, key=out.round_key)]
    assert order == ["t_try2", "t_try1", "t_try3"]


def test_open_ops_actions_join_the_round(tmp_path):
    p = tmp_path / "actions.jsonl"
    p.write_text(json.dumps({"ops_action_id": "a1", "title": "investigate — kpi=kanban_open", "status": "open"}) + "\n"
                 + json.dumps({"ops_action_id": "a2", "title": "done one", "status": "closed"}) + "\n")
    rows = out.open_actions(p)
    assert [(r["kpi_id"], r["stage"]) for r in rows] == [("kanban_open", "ops-action → Steward")]


def test_team_daily_prints_the_ranked_round(tmp_path):
    (tmp_path / "outcomes.json").write_text(json.dumps({"rows": [], "round": [
        {"item": "sm120 wheel", "stage": "landed", "kpi_id": "crew_tok_s", "baseline": 50, "now": 60,
         "measure": "due 2026-09-26", "try": "t_try2", "vote": "rank 1 of 3"}]}))
    rnd = tdaily._round(tmp_path / "outcomes.json")
    assert rnd and rnd[0]["item"] == "sm120 wheel"
    assert tdaily._round(tmp_path / "missing.json") is None


def test_krew_daily_counts_agreed_only_and_shows_the_rounds_top_three(tmp_path):
    sys.path.insert(0, str(ROOT / "crew" / "lib"))
    sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours
    import krew_daily as hd  # noqa: PLC0415
    imp = tmp_path / "imp"
    imp.mkdir()
    (imp / "outcomes.json").write_text(json.dumps({"rows": [
        {"item": "drain the ready lane", "stage": "landed", "source": "agreed", "kpi_id": "kanban_oldest_ready_sec", "measure": "due 2026-09-24"},
        {"item": "sm120 wheel", "stage": "landed", "source": "try", "kpi_id": "crew_tok_s", "measure": "due 2026-09-26"},
        {"item": "vLLM", "stage": "closed, not landed (review FAIL)", "source": "try", "kpi_id": "ops_tok_s"}],
        "round": [{"item": "sm120 wheel", "stage": "landed", "kpi_id": "crew_tok_s", "baseline": 50, "now": 60,
                   "measure": "due 2026-09-26"}]}))
    src = hd.Sources(inbox=tmp_path / "inbox", janitor_dir=tmp_path / "m", battery_dir=tmp_path / "b",
                     battery_latest=tmp_path / "l.json", ms_trail=tmp_path / "ms.json", improvements=imp,
                     keeper=tmp_path / "k.jsonl", dream_log=tmp_path / "d.log", t0_hits=tmp_path / "t0.jsonl",
                     kanban_home=tmp_path / "kanban", tick_state=tmp_path / "tick.json", schedule=tmp_path / "s.json",
                     temporaries=tmp_path / "t.json", gpu_mode=tmp_path / "mode", hold=tmp_path / "hold",
                     reboot_flag=tmp_path / "reboot", data_mount=tmp_path, site={"off_hours": "20:00-02:00"},   # krew: krew.yaml off_hours
                     unit_show=lambda unit, scope: {}, apt_check=lambda: (0, 0),
                     hermes_tree_status=lambda: {"patches": [], "uncovered": [], "beyond": {}},
                     # krew 2026-10-10: the Daily's hooks row ran the REAL `hermes -p <p> hooks doctor` under the scratch
                     # home, which provisioned hermes there and rewrote the user's live launcher. A test never runs hermes.
                     hooks_doctor=lambda profile: (0, ""))
    m = hd.build(dt.datetime(2026, 9, 19, 1, 50), src)
    fun = {f["label"]: f["n"] for f in m["improving"]["funnel"]}
    assert fun["Agreed by the team"] == 1 and fun["Landed"] == 2
    page = hd.render_html(m)
    assert "sm120 wheel" in page and "crew_tok_s 50 → 60" in page and 'href="team-daily.html"' in page


# --- a "nothing tonight" line is not a card; the weekly repos feed reaches the lookup -------

def test_none_this_cycle_is_not_filed():
    text = ("## Proposed cards\n- architect-try: none this cycle — render SOTA behind is Steward-owned\n"
            "- architect-watch: LMCache KV-cache layer\n- architect-try: nothing krew-ready\n")
    assert [k for k, _ in filer.parse_cards(text)] == ["architect-watch"]


def test_the_morning_collector_refreshes_the_feed_nnt_repos_reads(tmp_path, monkeypatch):
    col = _load("steward_architect_collect_round", "crew/scripts/steward-architect-collect.py")
    feed = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Top Repos Explained</title>'
            '<link href="https://youtu.be/x"/><published>2026-09-18T19:46:00+00:00</published></entry></feed>')
    monkeypatch.setattr(col, "http_text", lambda url, accept=None: (feed, "http 200"))
    monkeypatch.setattr(col, "NNT_CACHE", tmp_path / "feeds" / "nnt.xml")
    col.section_youtube()
    assert (tmp_path / "feeds" / "nnt.xml").read_text() == feed
    monkeypatch.setattr(col, "http_text", lambda url, accept=None: ("", "HTTPError 404"))
    col.section_youtube()                                   # a failed fetch keeps the last good copy
    assert (tmp_path / "feeds" / "nnt.xml").read_text() == feed


def test_the_report_render_replaces_a_copy_it_cannot_write(tmp_path):
    rh = _load("krew_report_html_round", "crew/scripts/krew-report-html.py")
    src = tmp_path / "ms-2026-09-18.md"
    src.write_text("# M&S\n\nlede: fine\n")
    dest = tmp_path / "ms-2026-09-18.html"
    dest.write_text("old")
    dest.chmod(0o444)                                   # stands in for root's copy: not writable, dir is
    assert rh.render_file(src) == dest
    assert dest.read_text() != "old" and "<html" in dest.read_text()
