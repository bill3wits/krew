"""kanban-loop: green -> triage, repeated failure -> frame -> triage, and the
retreat to Crissa after two frames. Reads a temp board, writes to a fake CLI."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_loop", ROOT / "crew" / "scripts" / "kanban-loop.py")
kl = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_loop"] = kl   # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(kl)


import pytest


@pytest.fixture(autouse=True)
def _quiet_log(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "LOG", tmp_path / "kanban-loop.log")


class FakeCli:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.n = 0

    def __call__(self, *args):
        self.calls.append(list(args))
        if args[0] == "create":
            self.n += 1
            return 0, f"Created t_c0ffee0{self.n}  (triage, assignee=ada)"
        if args[0] == "diagnostics":
            return 0, "[]"
        return 0, "ok"

    def of(self, verb):
        return [c for c in self.calls if c[0] == verb]


def _board(tmp_path: Path, tasks, comments=(), events=()):
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, "
                "block_kind TEXT, consecutive_failures INTEGER, created_by TEXT, last_failure_error TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", tasks)
    con.executemany("INSERT INTO task_comments (task_id, author, body) VALUES (?,?,?)", comments)
    con.executemany("INSERT INTO task_events (task_id, kind) VALUES (?,?)", events)
    con.commit(); con.close()
    return p


GATE_BODY = "Filed from note.\nkind=team-improve\ngate: green\nDo not implement.\n"


def test_green_try_cuts_a_triage_card_and_archives_the_gate(tmp_path):
    db = _board(tmp_path,
                [("t_00000001", "team-improve: NNT 404 six nights", GATE_BODY, "ready", "ada", "", 0, "steward-architect", "")],
                comments=[("t_00000001", "crissa", "try — the 00:20 collect is the bug, not the source")])
    cli = FakeCli()
    assert kl.run_green(kl.read_cards(db), cli) == 1
    create = cli.of("create")[0]
    assert create[1] == "try: team-improve: NNT 404 six nights" and "--triage" in create
    body = create[create.index("--body") + 1]
    assert "source: t_00000001" in body and "crissa: try" in body and "gate: greened" in body
    assert cli.of("archive") == [["archive", "t_00000001"]]


def test_green_park_archives_and_later_waits_then_returns(tmp_path):
    db = _board(tmp_path, [
        ("t_00000002", "architect-try: x", GATE_BODY, "ready", "ada", "", 0, "steward-architect", ""),
        ("t_00000003", "architect-try: y", GATE_BODY, "ready", "ada", "", 0, "steward-architect", ""),
        ("t_00000004", "architect-try: z", GATE_BODY, "blocked", "ada", "dependency", 0, "steward-architect", ""),
    ], comments=[("t_00000002", "crissa", "park: duplicate of last week"),
                 ("t_00000003", "crissa", "later 2026-10-01"),
                 ("t_00000004", "crissa", "later 2026-09-01")])
    cli = FakeCli()
    assert kl.run_green(kl.read_cards(db), cli, today=date(2026, 9, 12)) == 3
    assert ["archive", "t_00000002"] in cli.calls
    b3 = next(c for c in cli.of("block") if c[1] == "t_00000003")
    assert b3[3:] == ["--kind", "dependency"] and "2026-10-01" in b3[2]
    # the one whose date passed goes back on the Decide queue
    assert ["unblock", "t_00000004", "--reason", "later 2026-09-01 reached — back on the Decide queue"] in cli.calls
    b4 = next(c for c in cli.of("block") if c[1] == "t_00000004")
    assert b4[3:] == ["--kind", "needs_input"]
    assert not cli.of("create")


def test_already_blocked_gate_without_verdict_is_not_reasked(tmp_path):
    """Open gates with block_kind None/transient were re-blocked every 20 min
    (green moves 4 forever). Skip them until Crissa answers."""
    db = _board(tmp_path, [
        ("t_00000031", "team-improve: a", GATE_BODY, "blocked", "ada", None, 0, "steward-architect", ""),
        ("t_00000032", "team-improve: b", GATE_BODY, "blocked", "ada", "transient", 0, "steward-architect", ""),
    ])
    cli = FakeCli()
    assert kl.run_green(kl.read_cards(db), cli) == 0
    assert cli.calls == []


def test_green_waits_on_the_queue_and_reasks_when_unblocked_empty(tmp_path):
    db = _board(tmp_path, [
        ("t_00000005", "team-improve: a", GATE_BODY, "blocked", "ada", "needs_input", 0, "steward-architect", ""),
        ("t_00000006", "team-improve: b", GATE_BODY, "ready", "ada", "", 0, "steward-architect", ""),
    ])
    cli = FakeCli()
    assert kl.run_green(kl.read_cards(db), cli) == 1
    assert cli.calls == [["block", "t_00000006",
                          "Unblocked without a ruling. Green this try? try | park | later <date>.",
                          "--kind", "needs_input"]]


def test_gate_answer_reads_crissas_first_word_only():
    c = kl.Card("t_1", "x", GATE_BODY, "ready", "ada", "", 0, "s",
                comments=[("ada", "try this"), ("crissa", "Park. Not this quarter.")])
    assert kl.gate_answer(c)[0] == "park"
    c.comments.append(("crissa", "Yes, go"))
    assert kl.gate_answer(c)[0] == "try"
    c.comments.append(("crissa", "hmm not sure"))
    assert kl.gate_answer(c)[0] == ""


def test_gate_answer_skips_leading_unblock_token():
    """17:24 Crissa wrote 'UNBLOCK: try — …' / 'UNBLOCK: park — …'; first token
    was unblock so the loop re-asked. Skip it, then try/park/later."""
    c = kl.Card("t_1", "x", GATE_BODY, "ready", "ada", "", 0, "s",
                comments=[("crissa", "UNBLOCK: try — re-affirmed: null token/s")])
    assert kl.gate_answer(c)[0] == "try"
    c.comments.append(("crissa", "UNBLOCK: park — re-affirmed: no spike"))
    assert kl.gate_answer(c)[0] == "park"
    c.comments.append(("crissa", "UNBLOCK: later 2026-10-01"))
    assert kl.gate_answer(c)[0] == "later"


def test_greened_try_child_is_not_a_gate(tmp_path):
    """try: children copy the parent body with gate: greened. That must not
    match is_gate or the next tick blocks them as unrulied."""
    child_body = (
        "kind=team-improve\ngate: greened\ncrissa: UNBLOCK: try — x\n"
        "source: t_00000021\nCut by kanban-loop from Crissa's try.\n"
    )
    db = _board(tmp_path, [
        ("t_c0ffee01", "try: team-improve: ops_tok_s KPI", child_body, "triage", "ada", "", 0, "kanban-loop", ""),
        ("t_00000021", "team-improve: ops_tok_s KPI",
         GATE_BODY.replace("gate: green", "gate: greened"), "archived", "ada", "", 0, "steward-architect", ""),
    ])
    cards = kl.read_cards(db)
    child = next(x for x in cards if x.id == "t_c0ffee01")
    assert not kl.is_gate(child)
    assert "gate: greened" in (child.body or "")
    cli = FakeCli()
    assert kl.run_green(cards, cli) == 0
    assert cli.calls == []


def test_green_unblock_try_cuts_and_unblock_park_archives(tmp_path):
    db = _board(tmp_path, [
        ("t_00000021", "team-improve: ops_tok_s KPI", GATE_BODY, "ready", "ada", "needs_input", 0, "steward-architect", ""),
        ("t_00000022", "architect-try: none", GATE_BODY, "ready", "ada", "needs_input", 0, "steward-architect", ""),
        ("t_00000023", "team-improve: still waiting", GATE_BODY, "blocked", "ada", "needs_input", 0, "steward-architect", ""),
        ("t_00000024", "team-improve: reblocked after ruling", GATE_BODY, "blocked", "ada", "needs_input", 0, "steward-architect", ""),
    ], comments=[
        ("t_00000021", "crissa", "UNBLOCK: try — re-affirmed: null token/s since 09-11"),
        ("t_00000022", "crissa", "UNBLOCK: park — re-affirmed: 09-14 stack note names no spike"),
        ("t_00000024", "crissa", "UNBLOCK: try — re-affirmed after the loop re-asked"),
    ])
    cli = FakeCli()
    assert kl.run_green(kl.read_cards(db), cli) == 3
    titles = [c[1] for c in cli.of("create")]
    assert "try: team-improve: ops_tok_s KPI" in titles
    assert "try: team-improve: reblocked after ruling" in titles
    assert ["archive", "t_00000021"] in cli.calls
    assert ["archive", "t_00000022"] in cli.calls
    assert ["archive", "t_00000024"] in cli.calls
    assert not any(c[1] == "t_00000023" for c in cli.calls)


def test_repeated_failure_gets_a_frame_and_a_triage_child(tmp_path):
    db = _board(tmp_path, [
        ("t_00000010", "pack drift: 3 path(s)", "body", "blocked", "ada", "", 4, "steward",
         "worker exited cleanly without a terminal kanban call"),
        ("t_00000011", "fine card", "body", "ready", "bash", "", 1, "ada", ""),
    ])
    cli = FakeCli()
    diag = [{"task_id": "t_00000010", "diagnostics": [{"kind": "repeated_failures", "data": {"consecutive_failures": 4}}]}]
    frames = []
    def framer(card):
        frames.append(card.id)
        return "frame: 1. reconcile three paths ... 6. software"
    assert kl.run_reframe(kl.read_cards(db), diag, cli, framer=framer) == 1
    assert frames == ["t_00000010"]
    assert cli.of("comment")[0][1] == "t_00000010" and cli.of("comment")[0][2].startswith("frame:")
    create = cli.of("create")[0]
    assert create[1].startswith("reframe: pack drift") and "--triage" in create
    body = create[create.index("--body") + 1]
    assert "reframe-gen: 1" in body and "source: t_00000010" in body and body.startswith("frame:")
    assert create[create.index("--idempotency-key") + 1] == "reframe:t_00000010:g1"
    assert any(c[2].startswith("reframe-cut: t_c0ffee01") for c in cli.of("comment"))
    # the marker the specifier cannot rewrite lands on the new card
    assert ["comment", "t_c0ffee01", "reframe-gen: 1 source: t_00000010", "--author", "kanban-loop"] in cli.calls
    # already blocked: no second block
    assert not cli.of("block")


def test_workers_own_frame_wins_and_a_ready_card_is_superseded(tmp_path):
    db = _board(tmp_path, [
        ("t_00000012", "mint the plate", "body", "blocked", "bash", "", 2, "ada", "no image_gen"),
    ], comments=[("t_00000012", "bash", "frame: 1. mint a plate 2. sandbox path 3. assumed file paths work 4. base64 / another lane 5. one call 6. software")])
    cli = FakeCli()
    n = kl.run_reframe(kl.read_cards(db), [], cli, framer=lambda c: (_ for _ in ()).throw(RuntimeError("no model")))
    assert n == 1
    assert not [c for c in cli.of("comment") if c[2].startswith("frame:")]   # not rewritten
    create = cli.of("create")[0]
    body = create[create.index("--body") + 1]
    assert body.startswith("frame: 1. mint a plate")
    assert create[-2:] == ["--skill", "ada-map"]          # skills once per thread: the map rides every cut
    # item 1: the cut is linked OVER the source and the source is held with hermes' own park verb
    assert ["link", "t_c0ffee01", "t_00000012"] in cli.calls
    hold = cli.of("schedule")[0]
    assert hold[1] == "t_00000012" and hold[2].startswith("hold: until:t_c0ffee01")
    assert any(c[2].startswith("hold: reframe t_c0ffee01 until:t_c0ffee01") for c in cli.of("comment"))
    assert not cli.of("block")


def test_capability_block_is_never_reframed(tmp_path):
    """t_a44ce2b8 (09-18): a NEEDS-HUMAN card (missing file, 42 h) was reframed as software."""
    db = _board(tmp_path, [
        ("t_00000012", "mint the plate", "body", "blocked", "bash", "capability", 2, "ada", "no image_gen"),
    ], comments=[("t_00000012", "bash", "frame: 1. mint a plate 2. sandbox path 3. assumed file paths work 4. base64 / another lane 5. one call 6. software")])
    assert kl.reframe_candidates(kl.read_cards(db), []) == []


def test_a_frame_counts_only_from_the_assignee_or_a_ruler(tmp_path):
    """120 of 130 `frame:` comments were the loop's own; steward's sweep wrote 3 (item 3)."""
    db = _board(tmp_path, [
        ("t_00000031", "a", "body", "blocked", "bash", "", 0, "ada", ""),
        ("t_00000032", "b", "body", "blocked", "bash", "", 0, "ada", ""),
        ("t_00000033", "c", "body", "blocked", "bash", "", 0, "ada", ""),
    ], comments=[("t_00000031", "kanban-loop", "frame: (mechanical — x) card t_00000031 failed 1x"),
                 ("t_00000032", "steward", "frame: (mechanical — sweep t_06a4e5ad)"),
                 ("t_00000033", "crissa", "frame: 1. … 6. software")])
    cards = {c.id: c for c in kl.read_cards(db)}
    assert kl.worker_frame(cards["t_00000031"]) == ""
    assert kl.worker_frame(cards["t_00000032"]) == ""
    assert kl.worker_frame(cards["t_00000033"]).startswith("frame: 1.")
    assert [c.id for c in kl.reframe_candidates(list(cards.values()), [])] == ["t_00000033"]


def test_no_model_frame_never_mints_a_card(tmp_path):
    """Item 2: 12 of 18 cuts on 09-18 were the failure re-posted as a new card. A mechanical
    frame comments why and mints nothing; the label says what happened, not "unreachable"."""
    db = _board(tmp_path, [("t_00000013", "canary", "body", "ready", "bash", "", 3, "ada", "Free memory on device")])
    cli = FakeCli()
    def boom(card):
        raise ConnectionError("8005 down")
    assert kl.run_reframe(kl.read_cards(db), [], cli, framer=boom, mint_mechanical=False) == 0
    assert not cli.of("create") and not cli.of("block") and not cli.of("schedule") and not cli.of("link")
    note = cli.of("comment")[0]
    assert note[1] == "t_00000013" and note[2].startswith("reframe-noframe: model unreachable: ConnectionError") and "(1 of 3)" in note[2]
    # a thin reply is a no-frame too, and the label names it
    cli = FakeCli()
    assert kl.run_reframe(kl.read_cards(db), [], cli, framer=lambda c: "frame: ok", mint_mechanical=False) == 0
    assert cli.of("comment")[0][2].startswith("reframe-noframe: model returned 9 chars")
    text, who, why = kl.make_frame(kl.read_cards(db)[0], framer=boom)
    assert who == "mechanical" and text.startswith("frame: (mechanical — model unreachable") and "Free memory on device" in text
    assert "no reframer model reachable" not in text


def test_three_noframe_ticks_retreat_to_crissa(tmp_path):
    db = _board(tmp_path, [("t_00000013", "canary", "body", "blocked", "bash", "", 3, "ada", "err")],
                events=[("t_00000013", "gave_up")],
                comments=[("t_00000013", "kanban-loop", "reframe-noframe: model unreachable (1 of 3); not cut"),
                          ("t_00000013", "kanban-loop", "reframe-noframe: model unreachable (2 of 3); not cut")])
    cli = FakeCli()
    def boom(card):
        raise ConnectionError("8005 down")
    assert kl.run_reframe(kl.read_cards(db), [], cli, framer=boom, mint_mechanical=False) == 1
    assert not cli.of("create")
    b = cli.of("block")[0]
    assert b[1] == "t_00000013" and b[3:] == ["--kind", "needs_input"] and "No model frame in 3 ticks" in b[2]
    assert cli.calls.index(cli.of("unblock")[0]) < cli.calls.index(b)
    assert any(c[2].startswith("reframe-cut: none (no frame)") for c in cli.of("comment"))


def test_hand_mechanical_flag_still_mints(tmp_path):
    db = _board(tmp_path, [("t_00000013", "canary", "body", "ready", "bash", "", 3, "ada", "Free memory on device")])
    cli = FakeCli()
    n = kl.run_reframe(kl.read_cards(db), [], cli, framer=lambda c: kl.mechanical_frame(c, "hand: --mechanical"),
                       mint_mechanical=True)
    assert n == 1 and cli.of("create") and cli.of("comment")[0][2].startswith("frame: (mechanical — hand: --mechanical)")
    assert any(c[2].endswith("frame by mechanical)") for c in cli.of("comment"))


def test_second_generation_retreats_to_crissa(tmp_path):
    db = _board(tmp_path, [("t_00000014", "reframe: canary", "frame: ...\nreframe-gen: 2\nsource: t_00000013\n",
                            "blocked", "ada", "", 3, "kanban-loop", "still red")])
    cli = FakeCli()
    assert kl.run_reframe(kl.read_cards(db), [], cli, framer=lambda c: "frame: another go, one level up, in six points") == 1
    assert not cli.of("create")
    b = cli.of("block")[0]
    assert b[1] == "t_00000014" and b[3:] == ["--kind", "needs_input"]
    assert "try | park | hands" in b[2] and "t_00000013" in b[2]
    # a blocked card cannot be blocked again: released first, typed in the same breath
    assert cli.calls.index(cli.of("unblock")[0]) < cli.calls.index(b)


def test_a_try_after_a_retreat_allows_one_more_generation(tmp_path):
    db = _board(tmp_path, [("t_00000014", "reframe: canary", "frame: ...\nreframe-gen: 2\nsource: t_00000013\n",
                            "blocked", "ada", "", 3, "kanban-loop", "still red")],
                comments=[("t_00000014", "kanban-loop", "reframe-cut: none (gen 2 >= max 2); to Crissa"),
                          ("t_00000014", "crissa", "try — one more, the frame missed the disk")])
    cli = FakeCli()
    assert kl.run_reframe(kl.read_cards(db), [], cli, framer=lambda c: "frame: another go, one level up, in six points") == 1
    assert cli.of("create") and cli.of("create")[0][cli.of("create")[0].index("--idempotency-key") + 1] == "reframe:t_00000014:g3"


def test_a_try_after_a_cut_reopens_candidacy_and_hands_or_a_future_later_park_it(tmp_path):
    db = _board(tmp_path, [
        ("t_00000015", "cut then try", "body", "blocked", "ada", "", 5, "x", ""),
        ("t_00000017", "hands", "body", "blocked", "ada", "", 5, "x", ""),
        ("t_00000018", "later ahead", "body", "blocked", "ada", "", 5, "x", ""),
        ("t_00000019", "later passed", "body", "blocked", "ada", "", 5, "x", ""),
    ], comments=[("t_00000015", "kanban-loop", "reframe-cut: t_deadbeef (gen 1)"),
                 ("t_00000015", "crissa", "try again, the reframe was archived"),
                 ("t_00000017", "crissa", "hands — the User plugs the camera"),
                 ("t_00000018", "crissa", "later 2099-01-01"),
                 ("t_00000019", "crissa", "later 2020-01-01")])
    ids = [c.id for c in kl.reframe_candidates(kl.read_cards(db), [])]
    assert ids == ["t_00000015", "t_00000019"]


def test_already_cut_or_with_crissa_is_left_alone(tmp_path):
    db = _board(tmp_path, [
        ("t_00000015", "a", "body", "blocked", "ada", "", 5, "x", ""),
        ("t_00000016", "b", "body", "blocked", "ada", "needs_input", 5, "x", ""),
    ], comments=[("t_00000015", "kanban-loop", "reframe-cut: t_deadbeef (gen 1)")])
    assert kl.reframe_candidates(kl.read_cards(db), []) == []


def test_gave_up_park_is_a_reframe_candidate_at_one_failure(tmp_path):
    """Verb wall parks at 1; patch 70 will not retry. Kind-none is the
    usual stamp but the class is last event gave_up, not NULL kind."""
    db = _board(
        tmp_path,
        [
            ("t_00000040", "team-improve: crash-vs-block", "body", "blocked", "bash", None, 1, "x",
             "worker exited cleanly (rc=0) without calling kanban_complete"),
            ("t_00000041", "untyped worker block", "body", "blocked", "lila", None, 0, "x", ""),
            ("t_00000042", "needs_input even if gave_up", "body", "blocked", "ada", "needs_input", 1, "x", "err"),
            ("t_00000043", "unblocked after gave_up", "body", "blocked", "bash", None, 1, "x", "err"),
        ],
        events=[
            ("t_00000040", "protocol_violation"),
            ("t_00000040", "gave_up"),
            ("t_00000041", "blocked"),
            ("t_00000042", "gave_up"),
            ("t_00000043", "gave_up"),
            ("t_00000043", "unblocked"),
        ],
    )
    ids = [c.id for c in kl.reframe_candidates(kl.read_cards(db), [])]
    assert ids == ["t_00000040"]


def test_cut_budget_per_tick(tmp_path):
    db = _board(tmp_path, [(f"t_0000002{i}", f"c{i}", "body", "ready", "bash", "", 3, "ada", "e") for i in range(5)])
    cli = FakeCli()
    assert kl.run_reframe(kl.read_cards(db), [], cli, framer=lambda c: "frame: x y z " * 5, max_cuts=2) == 2


def test_reframe_only_skips_green_and_decompose(tmp_path, monkeypatch):
    db = _board(tmp_path, [
        ("t_00000001", "team-improve: x", GATE_BODY, "ready", "ada", "", 0, "steward-architect", ""),
        ("t_00000010", "pack drift", "body", "blocked", "ada", "", 4, "steward", "err"),
    ], comments=[("t_00000001", "crissa", "try now")])
    monkeypatch.setattr(kl, "OFF_SWITCH", tmp_path / "nope")
    monkeypatch.setattr(kl, "board_db", lambda: db)
    monkeypatch.setattr(kl, "LOG", tmp_path / "log")
    cli = FakeCli()
    monkeypatch.setattr(kl, "Cli", lambda dry=False: cli)
    monkeypatch.setattr(kl, "diagnostics_json", lambda c: [])
    assert kl.main(["--reframe-only", "--mechanical"]) == 0
    assert not cli.of("archive")
    assert cli.of("create") and cli.of("create")[0][1].startswith("reframe:")


def test_off_switch_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(kl, "OFF_SWITCH", tmp_path / "off")
    (tmp_path / "off").write_text("")
    monkeypatch.setattr(kl, "board_db", lambda: (_ for _ in ()).throw(AssertionError("read the board")))
    assert kl.main([]) == 0


def test_reframer_prompt_is_the_skill_method(tmp_path):
    sysm = kl.reframer_system(kl.REFRAME_SKILL)
    assert "Shared assumption" in sysm and "Hands or software" in sysm and "6. Is any part" in sysm
    assert "## Method" not in sysm and "Three frames that were right" not in sysm   # the section, not the file
    assert kl.reframer_system(tmp_path / "missing.md").startswith("You are the reframer")


def test_model_frame_carries_the_krew_numbers_and_picks_the_engine(monkeypatch):
    seen = {}
    class R:
        status = 200
        def read(self): return b'{"choices":[{"message":{"content":"1. x 2. y 3. z 4. a 5. b 6. software"}}]}'
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["payload"] = json.loads(req.data.decode())
        return R()
    import json
    monkeypatch.setattr(kl.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(kl, "think_engine_up", lambda url=None: False)
    c = kl.Card("t_1", "render take", "body", "blocked", "vera", "", 2, "ada", last_error="free 17 GiB < 24")
    out = kl.model_frame(c, numbers="GPU now: used 80049 of 97887 MiB; residents: EngineCore 40692 MiB; Comfy 37452 MiB")
    assert out.startswith("frame:")
    assert "8005" in seen["url"] and "residents" in seen["payload"]["messages"][1]["content"]
    assert seen["payload"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    monkeypatch.setattr(kl, "think_engine_up", lambda url=None: True)
    kl.model_frame(c, numbers="")
    assert "8000" in seen["url"] and seen["payload"]["reasoning_effort"] == kl.THINK_EFFORT and "extra_body" not in seen["payload"]
    assert seen["payload"]["max_tokens"] >= 2000        # room for the reasoning AND the content


def test_a_reply_that_spent_its_budget_thinking_is_a_no_frame_and_the_crew_is_tried_once(tmp_path, monkeypatch):
    import json
    seen = []
    class R:
        status = 200
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def urlopen(req, timeout=0):
        seen.append(req.full_url)
        if "8000" in req.full_url:
            return R(b'{"choices":[{"finish_reason":"length","message":{"content":"","reasoning":"thinking..."}}],"usage":{"completion_tokens":900}}')
        return R(b'{"choices":[{"finish_reason":"stop","message":{"content":"1. x 2. y 3. z 4. a 5. b 6. software, from crew"}}]}')
    monkeypatch.setattr(kl.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(kl, "think_engine_up", lambda url=None: True)
    monkeypatch.setattr(kl, "krew_numbers", lambda: "")
    c = kl.Card("t_1", "render take", "body", "blocked", "vera", "", 2, "ada", last_error="x")
    text, who, why = kl.make_frame(c)
    assert who == "model" and "from crew" in text
    assert [("8000" in u) for u in seen] == [True, False]


def test_loop_decomposes_triage_the_gateway_left(tmp_path):
    import time as _t
    now = 1_789_200_000
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, block_kind TEXT, "
                "consecutive_failures INTEGER, created_by TEXT, last_failure_error TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("t_00000030", "old triage", "b", "triage", None, None, 0, "x", "", now - 600),
        ("t_00000031", "fresh triage", "b", "triage", None, None, 0, "x", "", now - 60),
        ("t_00000032", "ready", "b", "ready", None, None, 0, "x", "", now - 600),
    ])
    con.commit(); con.close()
    assert kl.triage_older_than(p, 5, now=now) == ["t_00000030"]
    class D(FakeCli):
        def __call__(self, *args):
            self.calls.append(list(args))
            return 0, '{"task_id": "%s", "ok": true, "reason": "single task (no fanout)"}' % args[1]
    cli = D()
    assert kl.run_decompose(p, cli, now=now) == 1
    assert cli.calls == [["decompose", "t_00000030", "--json", "--author", "kanban-loop"]]


def test_loop_decompose_holds_at_wip_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_DECOMPOSE_WIP_CEILING", "150")
    now = 1_789_200_000
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, block_kind TEXT, "
                "consecutive_failures INTEGER, created_by TEXT, last_failure_error TEXT, created_at INTEGER)")
    rows = [
        (f"t_{i:08d}", "open", "b", "todo", None, None, 0, "x", "", now - 600)
        for i in range(151)
    ]
    rows[0] = ("t_00000030", "old triage", "b", "triage", None, None, 0, "x", "", now - 600)
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    class D(FakeCli):
        def __call__(self, *args):
            self.calls.append(list(args))
            return 0, '{"ok": true}'
    cli = D()
    assert kl.count_open_cards(p) == 151
    assert kl.run_decompose(p, cli, now=now) == 0
    assert cli.calls == []


def test_cli_always_names_the_profile(monkeypatch):
    seen = {}
    class R:
        returncode = 0
        stdout = "Created t_00000099"
        stderr = ""
    def run(argv, **kw):
        seen["argv"] = argv
        return R()
    monkeypatch.setattr(kl.subprocess, "run", run)
    kl.Cli(hermes=Path("/x/hermes"))("list")
    assert seen["argv"][:4] == ["/x/hermes", "-p", "ada", "kanban"]


# --- 2026-09-17: Park is sticky, generation survives the specifier ----------

import json as _json

T_RULE = 1_789_642_000          # the 23:03:22 ruling; only order matters


def _live_board(tmp_path: Path, tasks, comments=(), events=(), runs=(), links=()):
    """The live schema's columns this loop reads (timestamps, payloads, runs, links).

    tasks:    (id, title, body, status, created_by, created_at, idempotency_key[, failures, block_kind])
    comments: (task_id, author, body, created_at)
    events:   (task_id, kind, payload dict|None, created_at)
    runs:     (task_id, summary, ended_at)
    links:    (parent_id, child_id) — hermes: the child waits on the parent
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "live.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, assignee TEXT, status TEXT, "
                "created_by TEXT, created_at INTEGER, idempotency_key TEXT, consecutive_failures INTEGER DEFAULT 0, "
                "last_failure_error TEXT, block_kind TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, "
                "body TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, "
                "kind TEXT, payload TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, status TEXT, "
                "started_at INTEGER, ended_at INTEGER, outcome TEXT, summary TEXT)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    for t in tasks:
        tid, title, body, status, by, at, idem = t[:7]
        fails = t[7] if len(t) > 7 else 0
        kind = t[8] if len(t) > 8 else None
        con.execute("INSERT INTO tasks (id,title,body,assignee,status,created_by,created_at,idempotency_key,"
                    "consecutive_failures,last_failure_error,block_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, title, body, "ada", status, by, at, idem, fails, "", kind))
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", comments)
    con.executemany("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                    [(a, k, _json.dumps(pl) if pl is not None else None, at) for a, k, pl, at in events])
    con.executemany("INSERT INTO task_runs (task_id, status, started_at, ended_at, outcome, summary) "
                    "VALUES (?, 'blocked', ?, ?, 'blocked', ?)", [(a, at, at, s) for a, s, at in runs])
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.commit(); con.close()
    return p


ROOT_ID = "t_6cfdca80"
PARK_SUMMARY = ("Per Crissa 2026-09-16 23:03 (UNBLOCK: Park): parked until (a) a granted root channel exists "
                "and (b) a scheduled maintenance window is agreed")
CRISSA_PARK = "Park. Sudo route is T0/assent-blocked and every sudo call hits the same wall"


def _incident(tmp_path, *, root_status, kids=(), extra_comments=()):
    """t_6cfdca80 as it happened: Steward measures, Crissa parks, the unblock
    re-runs it, block_loop_detected routes it to triage."""
    tasks = [(ROOT_ID, "Apply OS security updates and resolve restart debt", "Apply the updates.",
              root_status, "auto-decomposer", T_RULE - 1700, None, 0, "needs_input")]
    tasks += list(kids)
    comments = [
        (ROOT_ID, "steward", "Measured state re-measured live: restart debt is dconf/gvfs noise", T_RULE - 494),
        (ROOT_ID, "steward", "Execution path measured: sudo -n -l is T0-blocked", T_RULE - 481),
        (ROOT_ID, "crissa", CRISSA_PARK, T_RULE),
        (ROOT_ID, "crissa", "UNBLOCK: " + CRISSA_PARK, T_RULE + 1),
        *extra_comments,
    ]
    events = [
        (ROOT_ID, "created", {"by": "auto-decomposer", "from_decompose_of": "t_f502b061"}, T_RULE - 1700),
        (ROOT_ID, "blocked", {"reason": "Root kernel action — needs human assent."}, T_RULE - 402),
        (ROOT_ID, "unblocked", None, T_RULE + 1),
        (ROOT_ID, "block_loop_detected", {"reason": PARK_SUMMARY}, T_RULE + 32),
    ]
    runs = [(ROOT_ID, "Root kernel action — needs human assent.", T_RULE - 402),
            (ROOT_ID, PARK_SUMMARY, T_RULE + 32)]
    for k in kids:
        events.append((k[0], "created", {"by": "kanban-loop", "from_decompose_of": ROOT_ID}, k[5]))
    links = [(k[0], ROOT_ID) for k in kids]
    return _live_board(tmp_path, tasks, comments, events, runs, links)


class DecomposeCli(FakeCli):
    def __call__(self, *args):
        self.calls.append(list(args))
        if args[0] == "decompose":
            return 0, '{"ok": true, "reason": "fanout 4"}'
        return 0, "ok"


def test_park_ruling_reads_comment_unblock_and_summary():
    c = kl.Card("t_1", "x", "b", "todo", "ada", "", 0, "s",
                comments=[("crissa", CRISSA_PARK)], comment_at=[100])
    assert kl.park_ruling(c)[1] == "crissa"
    # the summary alone (a worker quoting the ruling) is a ruling too
    c2 = kl.Card("t_2", "x", "b", "todo", "ada", "", 0, "s", summaries=[(100, PARK_SUMMARY)])
    assert kl.park_ruling(c2) is not None
    # a worker saying "park" is not a ruling; kanban-loop's own words never are
    c3 = kl.Card("t_3", "x", "b", "todo", "ada", "", 0, "s",
                 comments=[("bash", "park this for now"), ("kanban-loop", "park-sticky: x")], comment_at=[1, 2])
    assert kl.park_ruling(c3) is None
    # a later chat line from Crissa with no verdict does not lift the park
    c.comments.append(("crissa", "thanks, noted")); c.comment_at.append(200)
    assert kl.park_ruling(c) is not None


def test_t_6cfdca80_park_in_triage_is_never_decomposed(tmp_path, monkeypatch):
    """23:03:54 block_loop_detected put the parked card in triage; 23:04:10 the
    loop decomposed it into four children incl. 'restart gnome-session-manager'."""
    monkeypatch.setenv("KANBAN_DECOMPOSE_WIP_CEILING", "0")
    db = _incident(tmp_path, root_status="triage")
    cards = kl.read_cards(db)
    root = next(c for c in cards if c.id == ROOT_ID)
    assert kl.park_ruling(root) is not None and kl.is_parked(root)
    cli = DecomposeCli()
    now = T_RULE + 48
    assert kl.run_decompose(db, cli, now=now, after_min=5) == 0
    assert not cli.of("decompose")
    assert kl.reframe_candidates(cards, [{"task_id": ROOT_ID, "diagnostics": [
        {"kind": "repeated_failures", "data": {"consecutive_failures": 4}}]}]) == []
    # triage cannot be scheduled (hermes schedules todo/ready/running/blocked): one comment, no schedule
    assert kl.run_park(cards, cli) == 0
    assert not cli.of("schedule")
    notes = [c for c in cli.of("comment") if c[1] == ROOT_ID]
    assert len(notes) == 1 and notes[0][2].startswith("park-sticky:") and "never decomposed" in notes[0][2]
    # kill switches: the old behaviour decomposes it (sticky park off AND the block_loop_detected
    # guard off — 2026-09-18 item 7 skips a looped triage card on its own)
    monkeypatch.setenv("KANBAN_LOOP_PARK_STICKY", "0")
    monkeypatch.setenv("KANBAN_LOOP_DECOMPOSE_LOOPED", "1")
    cli2 = DecomposeCli()
    assert kl.run_decompose(db, cli2, now=now, after_min=5) == 1
    assert cli2.of("decompose")[0][1] == ROOT_ID
    assert kl.run_park(kl.read_cards(db), cli2) == 0 and not cli2.of("comment")


KIDS = [
    ("t_4c9af80c", "Identify and stage OS security packages", "b", "done", "kanban-loop", T_RULE + 48, None),
    ("t_d219431e", "Execute OS package updates", "b", "blocked", "kanban-loop", T_RULE + 48, None),
    ("t_ee023848", "Restart services to clear restart debt", "b", "todo", "kanban-loop", T_RULE + 48, None),
    ("t_a423c69f", "Verify system stability post-update", "b", "todo", "kanban-loop", T_RULE + 48, None),
    # a child cut BEFORE the ruling is not the loop's mistake
    ("t_0000c41d", "Earlier child", "b", "todo", "kanban-loop", T_RULE - 600, None),
]


def test_t_6cfdca80_park_schedules_card_and_children_cut_after_the_ruling(tmp_path):
    db = _incident(tmp_path, root_status="todo", kids=KIDS)
    everything = kl.read_cards(db, statuses=None)
    cards = [c for c in everything if c.status in kl.OPEN_CARD_STATUSES]
    cli = FakeCli()
    n = kl.run_park(cards, cli, index=kl.index_of(everything))
    sched = cli.of("schedule")
    assert sched[0][1] == ROOT_ID and len(sched[0]) == 3          # reason is positional words, no --reason
    assert sched[0][2].startswith("park ruled by crissa") and "Park. Sudo route" in sched[0][2]
    assert {s[1] for s in sched[1:]} == {"t_d219431e", "t_ee023848", "t_a423c69f"}
    assert n == 4
    notes = [c for c in cli.of("comment") if c[1] == ROOT_ID]
    assert len(notes) == 1 and "t_d219431e" in notes[0][2] and "t_ee023848" in notes[0][2]
    assert "t_0000c41d" not in notes[0][2]
    assert not cli.of("create") and not cli.of("decompose") and not cli.of("unblock")
    # a scheduled child is no reframe target
    kid = next(c for c in everything if c.id == "t_ee023848")
    assert kid.status == "scheduled" and kl.reframe_candidates([kid], [{"task_id": kid.id, "diagnostics": [
        {"kind": "repeated_failures", "data": {"consecutive_failures": 5}}]}]) == []


def test_parked_card_already_scheduled_and_marked_is_left_alone(tmp_path):
    marker = (ROOT_ID, "kanban-loop", "park-sticky: crissa ruled park; scheduled", T_RULE + 60)
    kids = [(k[0], k[1], k[2], "done" if k[3] == "done" else "scheduled", k[4], k[5], k[6]) for k in KIDS[:4]]
    db = _incident(tmp_path, root_status="scheduled", kids=kids, extra_comments=[marker])
    everything = kl.read_cards(db, statuses=None)
    cli = FakeCli()
    assert kl.run_park(everything, cli, index=kl.index_of(everything)) == 0
    assert kl.run_complete_frames(db, cli, index=kl.index_of(everything)) == 0
    assert kl.run_reframe(everything, [], cli, framer=lambda c: "frame: x" * 10) == 0
    assert cli.calls == []


def test_park_releases_only_on_a_different_ruling(tmp_path):
    later = (ROOT_ID, "default", "UNBLOCK: try — root channel granted, window tonight", T_RULE + 9000)
    db = _incident(tmp_path / "a", root_status="ready", extra_comments=[later])
    root = next(c for c in kl.read_cards(db) if c.id == ROOT_ID)
    assert kl.park_ruling(root) is None and not kl.is_parked(root)
    cli = FakeCli()
    assert kl.run_park([root], cli) == 0 and cli.calls == []
    # 2026-09-21 pm: a bare unblock BY A HAND now releases it — the loop was
    # enforcing a ruler's older word against her own newer act (see
    # tests/test_kanban_loop_done_verb.py). A MACHINE's bare unblock still does not:
    # 84 of the last 200 audit lines were Ada's gate landings, and those are not
    # rulings. Kill KANBAN_LOOP_HAND_UNBLOCK=0.
    bare = (ROOT_ID, "ada", "UNBLOCK: releasing a stuck ownerless batch", T_RULE + 9000)
    db2 = _incident(tmp_path / "b", root_status="ready", extra_comments=[bare])
    root2 = next(c for c in kl.read_cards(db2) if c.id == ROOT_ID)
    assert kl.park_ruling(root2) is not None
    cli2 = FakeCli()
    kl.run_park([root2], cli2)
    assert cli2.of("schedule")[0][1] == ROOT_ID


def test_park_sticky_survives_the_dispatcher_reclaim_tick(tmp_path, monkeypatch):
    """t_b16be5b2 churn (09-24/25): a park-sticky card whose stale timeout expired got
    re-promoted to ready every tick (13+ respawn cycles). The dispatcher's stale re-claim
    bucket (dispatch_stale_timeout_seconds=3600) selects cards whose last_dispatched is old
    regardless of the park marker. This test pins the loop-side contract: a scheduled
    card carrying a park-sticky comment must NOT be touched by the loop during a
    re-claim tick — no unblock, no re-schedule, no decompose, no reframe.

    The fix lives in the hermes gateway (commit c93fcee4d2: _dependency_repark_loop
    wired into recompute_ready at kanban_db.py:4731/4871). Without that guard the
    dispatcher re-promotes a todo/blocked card the instant its dependency clears;
    this test exercises the companion loop-side guarantee that a card the loop
    itself parked (status='scheduled' + 'park-sticky:' marker + ruler's park ruling)
    is left completely alone by every loop lane — the re-claim can only come from
    the stale bucket, and the loop never emits the unblock/re-schedule that the
    bucket would then act on.

    Builds on:
    - lines ~562-710: sticky-park mechanics (park_ruling, is_parked, run_park,
      _live_board, _incident)
    - test_loop_and_gateway_count_the_same_statuses (~921): loop/gateway status
      parity (patch 70 ceiling)
    - lines ~1242-1432: dependency-park + scheduled-hold mechanics
    """
    # NOW is one hour past the ruling — the stale timeout (3600 s) has expired,
    # so the dispatcher's stale re-claim bucket WOULD select this card if the
    # loop did not protect it.
    NOW = T_RULE + 3600
    marker = (ROOT_ID, "kanban-loop",
              "park-sticky: crissa ruled park 2026-09-25; this card is not "
              "reframed, decomposed or re-queued; scheduled. Release: unblock "
              "with a different ruling (try | later <date).", T_RULE + 60)
    db = _incident(tmp_path, root_status="scheduled", extra_comments=[marker])
    everything = kl.read_cards(db, statuses=None)
    root = next(c for c in everything if c.id == ROOT_ID)
    # Preconditions: the card is recognisably parked.
    assert root.status == "scheduled"
    assert kl.is_parked(root), "precondition: card must be recognized as parked"
    assert kl.park_ruling(root) is not None, "precondition: park ruling must be readable"
    # The re-claim tick: run_park must be a no-op — the card is already parked.
    cli = FakeCli()
    n = kl.run_park(everything, cli, index=kl.index_of(everything))
    assert n == 0, "run_park must not re-act on an already-parked card"
    assert cli.calls == [], (
        f"loop touched a parked card during a re-claim tick: {cli.calls}"
    )
    assert not cli.of("unblock"), "park-sticky card must not be unblocked by the loop"
    assert not cli.of("schedule"), "park-sticky card must not be re-scheduled"
    assert not cli.of("decompose"), "park-sticky card must not be decomposed"
    assert not cli.of("create"), "no new cards from a parked card"
    # The reframe lane also skips it (13+ cycles of the live defect is exactly
    # the repeated_failures signature the reframe lane would have seen).
    assert kl.reframe_candidates(everything, [{
        "task_id": ROOT_ID,
        "diagnostics": [{"kind": "repeated_failures",
                         "data": {"consecutive_failures": 13}}]
    }]) == [], "park-sticky card must not be a reframe candidate"
    # Kill switch: with sticky off the loop does not recognise the park RULING
    # (park_ruling is None, so the sticky path of is_parked is False). is_parked
    # still reads True via the status='scheduled' branch — that is the loop's
    # scheduled-lane guard, not the sticky park. run_park returns early (no
    # sticky action), but the loop must still not emit an unblock or a fresh
    # schedule that the stale bucket would then re-claim.
    monkeypatch.setenv("KANBAN_LOOP_PARK_STICKY", "0")
    everything2 = kl.read_cards(db, statuses=None)
    root2 = next(c for c in everything2 if c.id == ROOT_ID)
    # rulings() is not gated by the switch — the gate is park_ruling() itself.
    # The contract: with sticky off, the ruler's park does not translate into
    # a sticky park (park_ruling is None), and run_park returns early.
    assert kl.park_ruling(root2) is None, "with sticky off the park ruling is not read as sticky"
    cli2 = FakeCli()
    n2 = kl.run_park(everything2, cli2, index=kl.index_of(everything2))
    assert n2 == 0
    assert cli2.of("unblock") == [], (
        "even with sticky off the loop must not unblock a scheduled card — "
        "only a ruler's newer ruling (or a hand's unblock) releases it"
    )
    assert not cli2.of("schedule"), "with sticky off the loop must not re-schedule either"


def test_non_gate_park_through_main_tick_does_not_decompose(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_DECOMPOSE_WIP_CEILING", "0")
    db = _incident(tmp_path, root_status="triage")
    monkeypatch.setattr(kl, "OFF_SWITCH", tmp_path / "nope")
    monkeypatch.setattr(kl, "board_db", lambda: db)
    cli = DecomposeCli()
    monkeypatch.setattr(kl, "Cli", lambda dry=False: cli)
    monkeypatch.setattr(kl, "diagnostics_json", lambda c: [])
    assert kl.main([]) == 0
    assert not cli.of("decompose") and not cli.of("create")


# generation ------------------------------------------------------------------

def test_generation_survives_the_specifier_rewrite():
    rewritten = "Goal: Assess the risk of upgrading peft. Steps: 1. read 2. write"   # no marker left
    by_comment = kl.Card("t_a", "Verify peft 0.21.0 compatibility", rewritten, "todo", "ada", "", 0, "kanban-loop",
                         comments=[("auto-decomposer", "Specified — updated title, body"),
                                   ("kanban-loop", "reframe-gen: 2 source: t_9c768401")])
    assert kl.generation(by_comment) == 2 and kl.source_of(by_comment) == "t_9c768401"
    by_key = kl.Card("t_b", "Verify", rewritten, "todo", "ada", "", 0, "kanban-loop",
                     idempotency_key="reframe:t_11111111:g2")
    assert kl.generation(by_key) == 2 and kl.source_of(by_key) == "t_11111111"
    old_key = kl.Card("t_c", "Verify", rewritten, "done", "ada", "", 0, "kanban-loop",
                      idempotency_key="reframe-t_9c768401-1")
    assert kl.generation(old_key) == 1 and kl.source_of(old_key) == "t_9c768401"
    body_only = kl.Card("t_d", "reframe: x", "frame\nreframe-gen: 1\nsource: t_22222222\n", "todo", "ada", "", 0,
                        "kanban-loop")
    assert kl.generation(body_only) == 1
    # a decomposed child inherits its frame's generation and source
    kid = kl.Card("t_e", "Write the ledger row", "b", "blocked", "bash", "", 1, "auto-decomposer", decompose_of="t_b")
    idx = kl.index_of([by_key, kid])
    assert kl.generation(kid, idx) == 2 and kl.source_of(kid, idx) == "t_11111111"
    assert kl.generation(kid) == 0   # no index, no lineage


def test_third_generation_failure_retreats_to_crissa_with_rewritten_cards(tmp_path):
    """source t_aaaaaaaa -> gen1 t_bbbbbbbb -> gen2 t_cccccccc, every title and
    body rewritten by the specifier; t_cccccccc's decomposed child trips the
    breaker. That is generation three: Crissa, not a third frame."""
    rw = "Goal: do the thing. Steps: 1. 2. 3."
    db = _live_board(tmp_path, [
        ("t_aaaaaaaa", "Smoke test flux2-turbo hub", "orig", "blocked", "ada", 1000, None, 4),
        ("t_bbbbbbbb", "Diagnose the flux2 hub smoke", rw, "blocked", "kanban-loop", 2000, "reframe-t_aaaaaaaa-1", 3),
        ("t_cccccccc", "Verify flux2 hub VRAM floor", rw, "todo", "kanban-loop", 3000, "reframe:t_bbbbbbbb:g2"),
        ("t_dddddddd", "Run the VRAM probe", rw, "blocked", "auto-decomposer", 3100, None, 1),
    ], comments=[
        ("t_aaaaaaaa", "kanban-loop", "reframe-cut: t_bbbbbbbb (gen 1, frame by model)", 2000),
        ("t_bbbbbbbb", "kanban-loop", "reframe-gen: 1 source: t_aaaaaaaa", 2000),
        ("t_bbbbbbbb", "auto-decomposer", "Specified — updated title, body and promoted to todo.", 2010),
        ("t_bbbbbbbb", "kanban-loop", "reframe-cut: t_cccccccc (gen 2, frame by model)", 3000),
        ("t_cccccccc", "kanban-loop", "reframe-gen: 2 source: t_bbbbbbbb", 3000),
        ("t_cccccccc", "auto-decomposer", "Specified — updated title, body and promoted to todo.", 3010),
    ], events=[
        ("t_dddddddd", "created", {"by": "auto-decomposer", "from_decompose_of": "t_cccccccc"}, 3100),
        ("t_dddddddd", "protocol_violation", None, 3200),
        ("t_dddddddd", "gave_up", {"failures": 1}, 3200),
    ], links=[("t_dddddddd", "t_cccccccc")])
    everything = kl.read_cards(db, statuses=None)
    cards = [c for c in everything if c.status in kl.OPEN_CARD_STATUSES]
    idx = kl.index_of(everything)
    assert kl.generation(idx["t_dddddddd"], idx) == 2
    cli = FakeCli()
    assert kl.run_reframe(cards, [], cli, framer=lambda c: "frame: the VRAM floor is the frame " * 3, index=idx) == 1
    assert not cli.of("create")
    b = cli.of("block")[0]
    assert b[1] == "t_dddddddd" and b[3:] == ["--kind", "needs_input"]
    assert "try | park | hands" in b[2] and "generation 2" in b[2] and "t_bbbbbbbb" in b[2]
    assert any(c[1] == "t_dddddddd" and c[2].startswith("reframe-cut: none") for c in cli.of("comment"))


def test_complete_frames_by_marker_done_only_and_superseded_source(tmp_path):
    rw = "Goal: rewritten by the specifier"
    db = _live_board(tmp_path, [
        # frame whose title+body were rewritten; children via decompose, links child -> frame
        ("t_f0000001", "Verify pack drift", rw, "todo", "kanban-loop", 10, "reframe:t_50000001:g1"),
        ("t_c0000001", "implement", "b", "done", "auto-decomposer", 11, None),
        ("t_c0000002", "verify", "b", "done", "auto-decomposer", 11, None),
        # an archived child is NOT done
        ("t_f0000002", "Verify other", rw, "todo", "kanban-loop", 10, None),
        ("t_c0000003", "implement", "b", "done", "auto-decomposer", 11, None),
        ("t_c0000004", "dropped", "b", "archived", "auto-decomposer", 11, None),
        # dependents of a frame (the frame is their link parent) are not its children
        ("t_f0000003", "reframe: still going", rw, "ready", "kanban-loop", 10, "reframe:t_50000003:g1"),
        ("t_d0000001", "downstream", "b", "done", "ada", 11, None),
        # superseded source: its reframe (single task, specified, done)
        ("t_9c768401", "M&S mitigation: rail-stt peft", "b", "blocked", "ada", 5, None, 1),
        ("t_62e61662", "Verify peft 0.21.0 compatibility", rw, "done", "kanban-loop", 6, "reframe-t_9c768401-1"),
    ], comments=[
        ("t_f0000002", "kanban-loop", "reframe-gen: 1 source: t_50000002", 10),
        ("t_9c768401", "kanban-loop", "reframe-cut: t_62e61662 (gen 1, frame by mechanical)", 6),
    ], events=[
        ("t_c0000001", "created", {"from_decompose_of": "t_f0000001"}, 11),
        ("t_c0000002", "created", {"from_decompose_of": "t_f0000001"}, 11),
        ("t_c0000003", "created", {"from_decompose_of": "t_f0000002"}, 11),
        ("t_c0000004", "created", {"from_decompose_of": "t_f0000002"}, 11),
    ], links=[("t_c0000001", "t_f0000001"), ("t_c0000002", "t_f0000001"),
              ("t_c0000003", "t_f0000002"), ("t_c0000004", "t_f0000002"),
              ("t_f0000003", "t_d0000001")])
    cli = FakeCli()
    assert kl.run_complete_frames(db, cli) == 3
    # a todo frame: promote, then complete with --result (a bare second arg was read as a task id)
    assert ["promote", "t_f0000001", "children done — frame is not work"] in cli.calls
    assert ["complete", "t_f0000001", "--result", "children done — frame is not work"] in cli.calls
    # item 5 held this frame behind its not-done (archived) child — and because `done`
    # never comes for an archived child, "held" meant "open for good" (2026-09-21 pm:
    # t_7d374fc5, two children done and one archived, nothing left to wait for). It is
    # RETIRED now, never completed: a frame's `done` is what closes its source as
    # "superseded … which is done", so a retired child must not manufacture one.
    # The source meets the `reframe-archived:` branch and goes to Crissa. Kill
    # KANBAN_LOOP_FRAME_RETIRE=0.
    assert ["archive", "t_f0000002"] in cli.calls
    assert not any(c[0] in ("promote", "complete", "schedule") and c[1] == "t_f0000002" for c in cli.calls)
    assert not any(c[1] == "t_f0000003" for c in cli.calls)
    assert ["complete", "t_9c768401", "--result", "superseded by reframe t_62e61662, which is done"] in cli.calls


def test_help_and_unknown_flags_never_tick(monkeypatch, capsys):
    """2026-09-17: `kanban-loop.py --help` ran a live tick."""
    monkeypatch.setattr(kl, "board_db", lambda: (_ for _ in ()).throw(AssertionError("read the board")))
    monkeypatch.setattr(kl, "Cli", lambda dry=False: (_ for _ in ()).throw(AssertionError("made a cli")))
    assert kl.main(["--help"]) == 0
    assert kl.main(["-h"]) == 0
    assert kl.main(["--dryrun"]) == 2
    assert "usage:" in capsys.readouterr().out


def test_close_sources_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_CLOSE_SOURCES", "0")
    db = _live_board(tmp_path, [
        ("t_9c768401", "M&S mitigation: rail-stt peft", "b", "blocked", "ada", 5, None, 1),
        ("t_62e61662", "Verify peft 0.21.0 compatibility", "rw", "done", "kanban-loop", 6, "reframe-t_9c768401-1"),
    ], comments=[("t_9c768401", "kanban-loop", "reframe-cut: t_62e61662 (gen 1, frame by mechanical)", 6)])
    cli = FakeCli()
    assert kl.run_complete_frames(db, cli) == 0 and cli.calls == []


# --- 2026-09-17: the block verb's real grammar --------------------------------

def _hermes_block_parser():
    """The signature `hermes kanban block --help` prints: task_id, a trailing
    nargs=* reason, --kind. Not a mirror of our call — the CLI's own grammar."""
    import argparse
    p = argparse.ArgumentParser(prog="hermes kanban block", exit_on_error=False)
    p.add_argument("--ids", nargs="+")
    p.add_argument("--kind", choices=["capability", "dependency", "needs_input", "transient"])
    p.add_argument("task_id")
    p.add_argument("reason", nargs="*")
    return p


def test_block_args_parse_under_the_cli_grammar():
    p = _hermes_block_parser()
    ns = p.parse_args(list(kl.block_args("t_00000001", "needs_input", "Green this try? try | park")[1:]))
    assert ns.kind == "needs_input" and ns.reason == ["Green this try? try | park"]
    # hermes runs on Python 3.11, where a reason AFTER --kind is "unrecognized
    # arguments" (3.12's argparse takes it, which is how the bug hid from local
    # checks). So the pin is structural: nothing positional follows the flag.
    args = list(kl.block_args("t_00000001", "needs_input", "Green this try?"))
    assert args[-2:] == ["--kind", "needs_input"]


def test_every_block_the_loop_issues_parses(tmp_path):
    p = _hermes_block_parser()
    db = _board(tmp_path, [
        ("t_00000006", "team-improve: b", GATE_BODY, "ready", "ada", "", 0, "steward-architect", ""),
        ("t_00000013", "canary", "body", "ready", "ada", "", 3, "bash", "Free memory on device"),
        ("t_00000014", "reframe: canary", "frame: ...\nreframe-gen: 2\nsource: t_00000013\n",
         "blocked", "ada", "", 3, "kanban-loop", "still red"),
    ])
    cli = FakeCli()
    cards = kl.read_cards(db)
    kl.run_green(cards, cli)
    kl.run_reframe(cards, [], cli, framer=lambda c: "frame: one level up " * 4, max_cuts=9)
    blocks = cli.of("block")
    assert len(blocks) >= 2                       # green's re-ask + the gen-2 retreat; the source is held, not blocked
    assert cli.of("schedule") and cli.of("schedule")[0][2].startswith("hold: until:")
    for b in blocks:
        ns = p.parse_args(b[1:])
        assert ns.kind and ns.reason
        assert b[-2] == "--kind"          # 3.11: the flag is last, the reason is not


# --- 2026-09-17: the ceiling counts work in flight, and both counters agree -------

def test_parks_do_not_count_toward_the_wip_ceiling(tmp_path, monkeypatch):
    """One standing board + sticky parks: counting blocked/scheduled held decompose
    for good (136 open vs 150 the day the board was born). A park costs no worker."""
    monkeypatch.setenv("KANBAN_DECOMPOSE_WIP_CEILING", "150")
    now = 1_789_200_000
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, block_kind TEXT, "
                "consecutive_failures INTEGER, created_by TEXT, last_failure_error TEXT, created_at INTEGER)")
    rows = [(f"t_{i:08d}", "parked", "b", "blocked" if i % 2 else "scheduled", None, None, 0, "x", "", now - 600)
            for i in range(200)]
    rows += [(f"t_1{i:07d}", "work", "b", "todo", None, None, 0, "x", "", now - 600) for i in range(20)]
    rows.append(("t_00000030", "old triage", "b", "triage", None, None, 0, "x", "", now - 600))
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    assert kl.count_open_cards(p) == 21

    class D(FakeCli):
        def __call__(self, *args):
            self.calls.append(list(args))
            return 0, '{"ok": true, "reason": "decomposed"}'
    cli = D()
    assert kl.run_decompose(p, cli, now=now, skip=set()) == 1 and cli.of("decompose")


def test_loop_and_gateway_count_the_same_statuses():
    """The loop's run_decompose and Ada's gateway (hermes patch 70) must hold at the same
    number — they did not on 09-16, and both post-landing decomposes ran over the ceiling."""
    import re
    patch = (Path(kl.__file__).resolve().parents[2] / "patches"
             / "70-kanban-sticky-park-cap-ceiling.patch").read_text()
    m = re.search(r"^\+_OPEN_STATUSES = \(([^)]*)\)", patch, re.M)
    assert m, "patch 70 no longer defines _OPEN_STATUSES"
    gateway = tuple(x.strip().strip('"') for x in m.group(1).split(",") if x.strip())
    assert set(gateway) == set(kl.OPEN_STATUSES)
    assert "blocked" not in kl.OPEN_STATUSES and "scheduled" not in kl.OPEN_STATUSES


# --- 2026-09-17: a salad park is the tripwire's, not a repeat ----------------------

def test_salad_parked_card_is_not_reframed_until_its_releases_are_spent(tmp_path, monkeypatch):
    """hermes records a token-salad run as a protocol violation; a frame cut from that text
    is a frame about the protocol (~40 such cards on 09-17)."""
    monkeypatch.delenv("KANBAN_LOOP_SALAD_SKIP", raising=False)
    monkeypatch.setattr(kl.Path, "home", lambda: tmp_path)
    mk = lambda cid, comments: kl.Card(id=cid, title="Verify pack drift", body="b", status="blocked", assignee="bash",
                                       block_kind="", consecutive_failures=1, created_by="x",
                                       comments=comments, last_error="protocol violation", breaker_parked=True)
    burned = mk("t_aaaaaaaa", [("salad-tripwire", "salad-park: this run decoded token salad")])
    spent = mk("t_bbbbbbbb", [("salad-tripwire", "salad-park: x")] + [("salad-tripwire", f"salad-release: {i}") for i in range(3)])
    plain = mk("t_cccccccc", [])
    got = [c.id for c in kl.reframe_candidates([burned, spent, plain], [])]
    assert got == ["t_bbbbbbbb", "t_cccccccc"]
    monkeypatch.setenv("KANBAN_LOOP_SALAD_SKIP", "0")
    assert "t_aaaaaaaa" in [c.id for c in kl.reframe_candidates([burned], [])]


def test_a_reframe_that_became_a_project_asks_crissa_instead_of_closing_the_source(tmp_path, monkeypatch):
    """2026-09-17: t_697564ed (render a clip) was closed "superseded by reframe t_52fb1371,
    which is done" — that reframe's three children rewrote the worker protocol and Vera
    found no clip. A plain reframe still closes its source; a decomposed one asks."""
    monkeypatch.delenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", raising=False)
    db = _live_board(tmp_path, [
        ("t_697564ed", "Render freelance chain", "b", "blocked", "pose-freelance", 5, None, 1),
        ("t_52fb1371", "reframe: Render freelance chain", "rw", "done", "kanban-loop", 6, "reframe:t_697564ed:g1"),
        ("t_e12f5047", "Fix worker termination", "b", "done", "auto-decomposer", 7, None),
        ("t_55c01d27", "GPU memory pre-check", "b", "done", "auto-decomposer", 7, None),
        # the plain case from 09-17 still closes
        ("t_9c768401", "M&S mitigation: rail-stt peft", "b", "blocked", "ada", 5, None, 1),
        ("t_62e61662", "Verify peft 0.21.0 compatibility", "rw", "done", "kanban-loop", 6, "reframe-t_9c768401-1"),
    ], comments=[
        ("t_697564ed", "kanban-loop", "reframe-cut: t_52fb1371 (gen 1, frame by model)", 6),
        ("t_9c768401", "kanban-loop", "reframe-cut: t_62e61662 (gen 1, frame by mechanical)", 6),
    ], events=[
        ("t_e12f5047", "created", {"from_decompose_of": "t_52fb1371"}, 7),
        ("t_55c01d27", "created", {"from_decompose_of": "t_52fb1371"}, 7),
    ], links=[("t_e12f5047", "t_52fb1371"), ("t_55c01d27", "t_52fb1371")])
    cli = FakeCli()
    assert kl.run_complete_frames(db, cli) == 2
    assert not any(c[0] == "complete" and c[1] == "t_697564ed" for c in cli.calls)
    assert ["unblock", "t_697564ed", "--reason", "reframe t_52fb1371 closed as a project; asking Crissa"] in cli.calls
    ask = [c for c in cli.calls if c[0] == "block" and c[1] == "t_697564ed"]
    assert ask and ask[0][-2:] == ["--kind", "needs_input"] and "done (close this card) | try" in ask[0][2]
    assert ["comment", "t_697564ed", "reframe-project: t_52fb1371 — asked Crissa, not closed", "--author", "kanban-loop"] in cli.calls
    assert ["complete", "t_9c768401", "--result", "superseded by reframe t_62e61662, which is done"] in cli.calls
    # asked once: the next tick leaves her card alone
    db2 = _live_board(tmp_path / "again", [
        ("t_697564ed", "Render freelance chain", "b", "blocked", "pose-freelance", 5, None, 1, "needs_input"),
        ("t_52fb1371", "reframe: Render freelance chain", "rw", "done", "kanban-loop", 6, "reframe:t_697564ed:g1"),
        ("t_e12f5047", "Fix worker termination", "b", "done", "auto-decomposer", 7, None),
    ], comments=[
        ("t_697564ed", "kanban-loop", "reframe-cut: t_52fb1371 (gen 1, frame by model)", 6),
        ("t_697564ed", "kanban-loop", "reframe-project: t_52fb1371 — asked Crissa, not closed", 8),
    ], links=[("t_e12f5047", "t_52fb1371")])
    cli2 = FakeCli()
    assert kl.run_complete_frames(db2, cli2) == 0 and not any(c[1] == "t_697564ed" for c in cli2.calls)
    # kill switch: the old rule
    monkeypatch.setenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", "0")
    cli3 = FakeCli()
    kl.run_complete_frames(db, cli3)
    assert ["complete", "t_697564ed", "--result", "superseded by reframe t_52fb1371, which is done"] in cli3.calls


# --- 2026-09-18: `later <date>` is a park with an alarm on ANY card ------------------------

def test_later_date_reached_returns_a_plain_card_to_the_decide_queue(tmp_path):
    """t_f2156ac4: Crissa's 'later 2026-09-18' unblocked at once and nobody read the date.
    Now the plugin holds it as a dated dependency block; the loop is the alarm."""
    db = _board(tmp_path, [
        ("t_due", "vLLM smoke bench", "plain body", "blocked", "bash", "dependency", 0, "ada", ""),
        ("t_wait", "vLLM smoke bench", "plain body", "blocked", "bash", "dependency", 0, "ada", ""),
        ("t_dep", "child", "plain body", "blocked", "bash", "dependency", 0, "ada", ""),
        ("t_gate", "improve", GATE_BODY, "blocked", "ada", "dependency", 0, "steward", ""),
    ], comments=[
        ("t_due", "crissa", "later 2026-09-18 — window-dependent GPU smoke bench"),
        ("t_due", "crissa", "BLOCKED: later 2026-09-18: kanban-loop puts it back on the Decide queue then"),
        ("t_wait", "crissa", "later 2026-09-30 — after the reboot"),
        ("t_dep", "steward", "later 2026-09-01 — a worker's note is not a ruling"),
        ("t_gate", "crissa", "later 2026-09-01"),
    ])
    cli = FakeCli()
    assert kl.run_later(kl.read_cards(db), cli, today=date(2026, 9, 18)) == 1
    assert cli.of("unblock")[0][1] == "t_due"
    block = cli.of("block")[0]
    assert block[1] == "t_due" and block[-2:] == ["--kind", "needs_input"] and block[2].startswith("Later date")
    assert all(c[1] == "t_due" for c in cli.calls)          # t_wait future, t_dep no ruler, t_gate is run_green's


def test_a_todo_frame_behind_open_children_is_held_not_run(tmp_path, monkeypatch):
    """Item 5: 52 frames on the standing board were promoted and run as work; 8 sat in todo."""
    db = _board(tmp_path, [
        ("t_00000050", "reframe: fix the thing", "frame: …\nreframe-gen: 1\nsource: t_00000049\n", "todo", "ada", "", 0, "kanban-loop", ""),
        ("t_00000051", "child one", "body", "done", "bash", "", 0, "auto-decomposer", ""),
        ("t_00000052", "child two", "body", "blocked", "bash", "", 0, "auto-decomposer", ""),
    ])
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.executemany("INSERT INTO task_links VALUES (?,?)", [("t_00000051", "t_00000050"), ("t_00000052", "t_00000050")])
    con.commit(); con.close()
    cli = FakeCli()
    assert kl.run_complete_frames(db, cli) == 1
    hold = cli.of("schedule")[0]
    assert hold[1] == "t_00000050" and hold[2].startswith("hold: frame waits on children t_00000052")
    assert any(c[1] == "t_00000050" and c[2].startswith("hold: frame waits on children") for c in cli.of("comment"))
    assert not cli.of("promote") and not cli.of("complete")
    # once held and the children finish, the closer releases and completes it
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET status='scheduled' WHERE id='t_00000050'")
    con.execute("UPDATE tasks SET status='done' WHERE id='t_00000052'")
    con.execute("INSERT INTO task_comments (task_id, author, body) VALUES (?,?,?)",
                ("t_00000050", "kanban-loop", "hold: frame waits on children t_00000052"))
    con.commit(); con.close()
    cli = FakeCli()
    assert kl.run_complete_frames(db, cli) == 1
    verbs = [c[0] for c in cli.calls]
    assert verbs == ["unblock", "promote", "complete"] and cli.of("complete")[0][1] == "t_00000050"


def test_a_retreat_on_a_card_already_on_the_decide_queue_is_a_comment_only(tmp_path):
    """7: a second needs_input block on the same cause routes the card to triage (hermes)."""
    db = _board(tmp_path, [("t_00000014", "reframe: canary", "frame: ...\nreframe-gen: 2\nsource: t_00000013\n",
                            "blocked", "ada", "needs_input", 3, "kanban-loop", "still red")],
                events=[("t_00000014", "gave_up")])
    cli = FakeCli()
    c = kl.read_cards(db)[0]
    c.block_kind = ""          # make it a candidate for the retreat path, then restore the queue kind
    c.block_kind = "needs_input"
    kl._retreat(cli, c, 2, "frame: a long enough frame one level up", {c.id: c})
    assert not cli.of("unblock") and not cli.of("block")
    assert cli.of("comment")[0][2].startswith("decide: Reframe generation 2 failed again")


def test_loop_detected_triage_cards_are_not_decomposed(tmp_path, monkeypatch):
    db = _board(tmp_path, [
        ("t_00000060", "looped", "body", "triage", "ada", "", 0, "x", ""),
        ("t_00000061", "fresh", "body", "triage", "ada", "", 0, "x", ""),
    ], events=[("t_00000060", "blocked"), ("t_00000060", "block_loop_detected"), ("t_00000061", "created")])
    con = sqlite3.connect(db); con.execute("ALTER TABLE tasks ADD COLUMN created_at INTEGER DEFAULT 0"); con.commit(); con.close()
    monkeypatch.setenv("KANBAN_DECOMPOSE_WIP_CEILING", "0")
    class DecomposeCli(FakeCli):
        def __call__(self, *args):
            if args[0] == "decompose":
                self.calls.append(list(args))
                return 0, '{"ok": true, "reason": "3 children"}'
            return super().__call__(*args)
    cli = DecomposeCli()
    assert kl.run_decompose(db, cli, now=10_000, skip=set()) == 1
    assert [c[1] for c in cli.of("decompose")] == ["t_00000061"]
    note = cli.of("comment")[0]
    assert note[1] == "t_00000060" and note[2].startswith("loop-detected:")


def _held_project_source_board(tmp_path):
    return _live_board(tmp_path, [
        ("t_d3b9a1db", "Benchmark baseline vLLM v0.27.1 performance", "b", "scheduled", "steward", 5, None),
        ("t_e4a0a492", "reframe: Benchmark baseline vLLM v0.27.1", "rw", "done", "kanban-loop", 6, "reframe:t_d3b9a1db:g1"),
        ("t_30e9caea", "Measure baseline", "b", "done", "auto-decomposer", 7, None),
    ], comments=[
        ("t_d3b9a1db", "kanban-loop", "reframe-cut: t_e4a0a492 (gen 1, frame by model)", 6),
        ("t_d3b9a1db", "kanban-loop", "hold: reframe t_e4a0a492 until:t_e4a0a492", 7),
    ], events=[
        ("t_30e9caea", "created", {"from_decompose_of": "t_e4a0a492"}, 7),
    ], links=[("t_30e9caea", "t_e4a0a492")])


def test_a_held_source_is_released_before_its_project_question(tmp_path, monkeypatch):
    """2026-09-19: a loop-held source is `scheduled`; hermes refuses to block it, so the ask
    never reached Crissa. The ask unblocks it first, exactly like a blocked source."""
    monkeypatch.delenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", raising=False)
    db = _held_project_source_board(tmp_path)
    cli = FakeCli()
    kl.run_complete_frames(db, cli)
    verbs = [(c[0], c[1]) for c in cli.calls if c[1:2] == ["t_d3b9a1db"]]
    assert verbs.index(("unblock", "t_d3b9a1db")) < verbs.index(("block", "t_d3b9a1db"))
    assert ["comment", "t_d3b9a1db", "reframe-project: t_e4a0a492 — asked Crissa, not closed",
            "--author", "kanban-loop"] in cli.calls


def test_a_refused_project_question_leaves_no_marker(tmp_path, monkeypatch):
    """The marker means "asked once"; a refused block was not an ask, so the question is still
    owed — the card must not carry `reframe-project:` (2026-09-19)."""
    monkeypatch.delenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", raising=False)
    db = _held_project_source_board(tmp_path)

    class Refuses(FakeCli):
        def __call__(self, *args):
            if args[0] == "block":
                self.calls.append(list(args))
                return 1, "cannot block t_d3b9a1db"
            return super().__call__(*args)

    cli = Refuses()
    kl.run_complete_frames(db, cli)
    assert not any(c[0] == "comment" and "reframe-project:" in c[2] for c in cli.calls)


def _todo_project_source_board(tmp_path, comments=()):
    """The live 2026-09-20 shape: the source is `todo` behind an OPEN parent (t_f66f5a01),
    its reframe closed as a project. hermes blocks only running/ready, and promote refuses
    while a parent is open — so the ask cannot land at all."""
    return _live_board(tmp_path, [
        ("t_4c1fdafa", "Review + finish freelance clip: sat back", "b", "todo", "user", 5, None),
        ("t_fa15f715", "reframe: Review + finish freelance clip: sat back", "rw", "done",
         "kanban-loop", 6, "reframe:t_4c1fdafa:g1"),
        ("t_kid00001", "Render FLF segments", "b", "done", "auto-decomposer", 7, None),
        ("t_f66f5a01", "render-now: mint sat-back stretch chain", "b", "todo", "user", 4, None),
    ], comments=[
        ("t_4c1fdafa", "kanban-loop", "reframe-cut: t_fa15f715 (gen 1, frame by model)", 6),
        *comments,
    ], events=[
        ("t_kid00001", "created", {"from_decompose_of": "t_fa15f715"}, 7),
    ], links=[("t_kid00001", "t_fa15f715"), ("t_f66f5a01", "t_4c1fdafa")])


class _RefusesUnpromotable(FakeCli):
    """hermes: `promote` refuses a todo card whose parent is open; `block` takes only running/ready."""

    def __call__(self, *args):
        if args[0] in ("promote", "block"):
            self.calls.append(list(args))
            return 1, f"cannot {args[0]} {args[1]}: parent t_f66f5a01 is not done"
        return super().__call__(*args)


def test_a_todo_source_is_promoted_before_its_project_question(tmp_path, monkeypatch):
    """2026-09-20: the 09-19 `scheduled` fix lands the card in TODO (unblock re-gates on
    parents) and hermes blocks only running/ready. `promote` is todo's exit."""
    monkeypatch.delenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", raising=False)
    monkeypatch.delenv("KANBAN_LOOP_ASK_PROMOTES", raising=False)
    db = _todo_project_source_board(tmp_path)
    cli = FakeCli()
    kl.run_complete_frames(db, cli)
    verbs = [(c[0], c[1]) for c in cli.calls if c[1:2] == ["t_4c1fdafa"]]
    assert verbs.index(("promote", "t_4c1fdafa")) < verbs.index(("block", "t_4c1fdafa"))
    assert ["comment", "t_4c1fdafa", "reframe-project: t_fa15f715 — asked Crissa, not closed",
            "--author", "kanban-loop"] in cli.calls


def test_an_unaskable_source_records_the_refusal_once_and_stops(tmp_path, monkeypatch):
    """The 10-hour bug: a refused ask left no marker of ANY kind, so every 20-min tick retried
    it in silence (t_4c1fdafa, 29 ticks). The refusal is recorded once; the second tick is quiet."""
    monkeypatch.delenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", raising=False)
    monkeypatch.delenv("KANBAN_LOOP_ASK_PROMOTES", raising=False)
    db = _todo_project_source_board(tmp_path)

    cli = _RefusesUnpromotable()
    kl.run_complete_frames(db, cli)
    notes = [c for c in cli.calls if c[0] == "comment" and c[2].startswith("ask-refused:")]
    assert len(notes) == 1, notes
    assert "reframe-project t_fa15f715" in notes[0][2]
    # the question is still owed: no "asked once" marker
    assert not any(c[0] == "comment" and c[2].startswith("reframe-project:") for c in cli.calls)

    # tick two, with the note now on the card: no comment, no block, no promote
    db2 = _todo_project_source_board(tmp_path / "two", comments=[
        ("t_4c1fdafa", "kanban-loop", notes[0][2], 8)])
    cli2 = _RefusesUnpromotable()
    kl.run_complete_frames(db2, cli2)
    assert [c for c in cli2.calls if c[1:2] == ["t_4c1fdafa"]] == []


def test_the_recorded_refusal_does_not_gag_the_ask_once_the_card_is_askable(tmp_path, monkeypatch):
    """The note is not a park: when the parent finishes the card leaves todo, and the question
    Crissa is owed is put for real."""
    monkeypatch.delenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", raising=False)
    monkeypatch.delenv("KANBAN_LOOP_ASK_PROMOTES", raising=False)
    db = _live_board(tmp_path, [
        ("t_4c1fdafa", "Review + finish freelance clip: sat back", "b", "ready", "user", 5, None),
        ("t_fa15f715", "reframe: Review + finish freelance clip: sat back", "rw", "done",
         "kanban-loop", 6, "reframe:t_4c1fdafa:g1"),
        ("t_kid00001", "Render FLF segments", "b", "done", "auto-decomposer", 7, None),
    ], comments=[
        ("t_4c1fdafa", "kanban-loop", "reframe-cut: t_fa15f715 (gen 1, frame by model)", 6),
        ("t_4c1fdafa", "kanban-loop", "ask-refused: reframe-project t_fa15f715 — todo cannot "
         "take a needs_input block (parent open).", 7),
    ], events=[
        ("t_kid00001", "created", {"from_decompose_of": "t_fa15f715"}, 7),
    ], links=[("t_kid00001", "t_fa15f715")])
    cli = FakeCli()
    kl.run_complete_frames(db, cli)
    assert any(c[0] == "block" and c[1] == "t_4c1fdafa" for c in cli.calls)
    assert ["comment", "t_4c1fdafa", "reframe-project: t_fa15f715 — asked Crissa, not closed",
            "--author", "kanban-loop"] in cli.calls


def test_ask_promotes_kill_switch_restores_the_old_retry(tmp_path, monkeypatch):
    monkeypatch.delenv("KANBAN_LOOP_PROJECT_REFRAME_ASKS", raising=False)
    monkeypatch.setenv("KANBAN_LOOP_ASK_PROMOTES", "0")
    db = _todo_project_source_board(tmp_path)
    cli = _RefusesUnpromotable()
    kl.run_complete_frames(db, cli)
    assert not any(c[0] == "promote" for c in cli.calls)
    assert not any(c[0] == "comment" and c[2].startswith("ask-refused:") for c in cli.calls)


def test_a_dashboard_comment_is_the_users_ruling():
    """2026-09-19 (the User: "that has been my comments issue"): the Hermes dashboard
    signs every UI comment 'dashboard'. Only the User uses it, so a ruling typed
    there is his — it used to be ignored by every ruler check."""
    c = kl.Card(id="t_x", title="t", body="b", status="blocked", assignee="bash",
                block_kind="needs_input", consecutive_failures=0, created_by="ada",
                comments=[("ada", "BLOCKED: rule please"), ("dashboard", "park — not now")])
    assert kl.park_ruling(c) is not None
    assert "dashboard" in kl.RULERS


# --- a park whose wait is over (2026-09-22) ----------------------------------------------------
# `scheduled` is sticky: recompute_ready promotes a todo/blocked card the moment its parents go
# terminal, but it skips parks, and unblock_task is a park's only exit. So a card parked
# "waiting on X" stayed parked after X finished — and carrying no needs_input, it never reached
# Crissa either. Measured with the board idle at 0 running and 0 of 4 todo dispatchable: two such
# parks held five live cards between them.

def _links(db, pairs):
    """pairs: (prerequisite, dependent) — task_links.parent_id is the PREREQUISITE."""
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS task_links (parent_id TEXT, child_id TEXT)")
    con.executemany("INSERT INTO task_links VALUES (?,?)", pairs)
    con.commit(); con.close()


def test_a_dependency_park_releases_when_every_prerequisite_is_finished(tmp_path):
    db = _board(tmp_path, [
        ("t_00000010", "Validate final clip", "", "scheduled", "lila", "dependency", 0, "ada", ""),
    ])
    _links(db, [("t_00000011", "t_00000010")])      # prerequisite is closed: absent from the board
    cli = FakeCli()
    cards = kl.read_cards(db)
    assert kl.run_dependency_clear(cards, cli, db) == 1
    unblock = cli.of("unblock")[0]
    assert unblock[1] == "t_00000010" and "every prerequisite is finished" in unblock[3]


def test_it_leaves_a_park_whose_prerequisite_is_still_open(tmp_path):
    db = _board(tmp_path, [
        ("t_00000010", "Validate final clip", "", "scheduled", "lila", "dependency", 0, "ada", ""),
        ("t_00000011", "The thing it waits on", "", "running", "bash", "", 0, "ada", ""),
    ])
    _links(db, [("t_00000011", "t_00000010")])
    cli = FakeCli()
    assert kl.run_dependency_clear(kl.read_cards(db), cli, db) == 0
    assert not cli.calls


def test_a_prose_only_wait_is_not_ours_to_judge(tmp_path):
    """No recorded prerequisite means nothing to check. Never guess from the body."""
    db = _board(tmp_path, [
        ("t_00000010", "Waiting on something", "", "scheduled", "lila", "dependency", 0, "ada", ""),
    ])
    cli = FakeCli()
    assert kl.run_dependency_clear(kl.read_cards(db), cli, db) == 0
    assert not cli.calls


def test_a_rulers_park_outranks_the_graph(tmp_path):
    """She parked it herself. Her word outranks the links — this is the guard that stopped
    t_1ca673a5 being released out from under Crissa's own ruling."""
    db = _board(tmp_path, [
        ("t_00000010", "Implement guard logic", "", "scheduled", "bash", "dependency", 0, "ada", ""),
    ], comments=[("t_00000010", "crissa", "park — not until the acceptance lands")])
    _links(db, [("t_00000011", "t_00000010")])
    cli = FakeCli()
    assert kl.run_dependency_clear(kl.read_cards(db), cli, db) == 0
    assert not cli.calls


def test_a_rulers_park_whose_cited_card_has_finished_goes_back_to_her(tmp_path):
    """Correct when she said it, stale now. It does NOT release — that would be releasing on
    a ruler's park — it puts the decision back where it belongs, once, with the evidence."""
    db = _board(tmp_path, [
        ("t_00000010", "Implement guard logic", "", "scheduled", "bash", "dependency", 0, "ada", ""),
    ], comments=[("t_00000010", "crissa",
                  "park — threshold acceptance t_00000099 is still in flight")])
    cli = FakeCli()
    cards = kl.read_cards(db)
    assert kl.run_park_premise_expired(cards, cli) == 1
    assert any(c[0] == "comment" and c[2].startswith("park-premise: t_00000099") for c in cli.calls)
    blocks = cli.of("block")
    assert blocks and blocks[0][3:] == ["--kind", "needs_input"]
    assert "has since finished" in blocks[0][2]


def test_it_does_not_re_ask_once_she_has_been_asked(tmp_path):
    db = _board(tmp_path, [
        ("t_00000010", "Implement guard logic", "", "scheduled", "bash", "dependency", 0, "ada", ""),
    ], comments=[("t_00000010", "crissa", "park — waiting on t_00000099"),
                 ("t_00000010", "kanban-loop", "park-premise: t_00000099")])
    cli = FakeCli()
    assert kl.run_park_premise_expired(kl.read_cards(db), cli) == 0
    assert not cli.calls


def test_a_park_citing_a_card_still_open_is_left_alone(tmp_path):
    db = _board(tmp_path, [
        ("t_00000010", "Implement guard logic", "", "scheduled", "bash", "dependency", 0, "ada", ""),
        ("t_00000099", "The acceptance", "", "running", "bash", "", 0, "ada", ""),
    ], comments=[("t_00000010", "crissa", "park — waiting on t_00000099")])
    cli = FakeCli()
    assert kl.run_park_premise_expired(kl.read_cards(db), cli) == 0
    assert not cli.calls


def test_a_park_citing_nothing_is_left_alone(tmp_path):
    db = _board(tmp_path, [
        ("t_00000010", "Some card", "", "scheduled", "bash", "dependency", 0, "ada", ""),
    ], comments=[("t_00000010", "crissa", "park — not worth doing this quarter")])
    cli = FakeCli()
    assert kl.run_park_premise_expired(kl.read_cards(db), cli) == 0
    assert not cli.calls


# ── 2026-09-24: a gate-routed park is read for its premise; a park cycle is named ─────────
def test_a_park_ada_routed_through_the_gate_is_read_for_its_premise(tmp_path):
    """t_506667f7: Ada's `VERB: park awaiting t_9b4416ae`, landed by the gate 09-19 19:28;
    t_9b4416ae archived 20:38 the same night; the park sat five days because only a RULER's
    park was ever read. The exit asks Crissa once, exactly as it would on her own park."""
    db = _board(tmp_path, [
        ("t_00000010", "Validate terminal signal propagation", "", "scheduled", "bash", "", 0, "auto-decomposer", ""),
    ], comments=[("t_00000010", "ada", "VERB: park awaiting t_00000099 prodgate apply — the deploy step is subsumed"),
                 ("t_00000010", "ada", "SCHEDULED: awaiting t_00000099 prodgate apply — the deploy step is subsumed"),
                 ("t_00000010", "kanban-board-gate", "gate: routed → park awaiting t_00000099 prodgate apply — the deploy step is subsumed")])
    cli = FakeCli()
    assert kl.run_park_premise_expired(kl.read_cards(db), cli) == 1
    assert any(c[0] == "comment" and c[2].startswith("park-premise: t_00000099") for c in cli.calls)
    blocks = cli.of("block")
    assert blocks and blocks[0][3:] == ["--kind", "needs_input"] and "has since finished" in blocks[0][2]


def test_a_routed_park_is_not_read_once_a_ruler_has_spoken(tmp_path, monkeypatch):
    """A ruler's word outranks the machine's: her `try` (card still scheduled for whatever
    reason) means the routed premise is not ours to raise. And the kill switch holds."""
    db = _board(tmp_path, [
        ("t_00000010", "Validate", "", "scheduled", "bash", "", 0, "ada", ""),
    ], comments=[("t_00000010", "kanban-board-gate", "gate: routed → park awaiting t_00000099"),
                 ("t_00000010", "crissa", "try — go ahead")])
    cli = FakeCli()
    assert kl.run_park_premise_expired(kl.read_cards(db), cli) == 0
    db2 = _board(tmp_path / "b", [
        ("t_00000010", "Validate", "", "scheduled", "bash", "", 0, "ada", ""),
    ], comments=[("t_00000010", "kanban-board-gate", "gate: routed → park awaiting t_00000099")]) if (tmp_path / "b").mkdir() is None else None
    monkeypatch.setenv("KANBAN_LOOP_PREMISE_ROUTED", "0")
    assert kl.run_park_premise_expired(kl.read_cards(db2), cli) == 0
    assert not cli.calls


def test_a_park_cycle_is_reported_and_not_written_by_default(tmp_path, monkeypatch):
    """t_ee5aa2e2 'deploy …, then unblock t_506667f7' had t_506667f7 as its PREREQUISITE, and
    t_506667f7 was parked waiting for the deploy. Report-only: a log line and a count."""
    monkeypatch.delenv("KANBAN_LOOP_CYCLE", raising=False)
    db = _board(tmp_path, [
        ("t_00000010", "Validate terminal signal propagation", "", "scheduled", "bash", "", 0, "ada", ""),
        ("t_00000020", "deploy the hard-stop patch, then unblock t_00000010 validation", "", "todo", "ada", "", 0, "steward", ""),
    ], comments=[("t_00000010", "kanban-board-gate", "gate: routed → park awaiting the prodgate apply")])
    _links(db, [("t_00000010", "t_00000020")])
    cli = FakeCli()
    assert kl.run_park_cycle(kl.read_cards(db), cli, db) == 1
    assert not cli.calls
    assert "park cycle t_00000010 ⇄ t_00000020" in kl.LOG.read_text()


def test_a_park_cycle_armed_asks_crissa_on_the_park_once(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_CYCLE", "act")
    db = _board(tmp_path, [
        ("t_00000010", "Validate", "", "scheduled", "bash", "", 0, "ada", ""),
        ("t_00000020", "deploy the patch", "", "todo", "ada", "", 0, "steward", ""),
    ], comments=[("t_00000010", "ada", "VERB: park awaiting t_00000020 — the deploy lands first")])
    _links(db, [("t_00000010", "t_00000020")])
    cli = FakeCli()
    assert kl.run_park_cycle(kl.read_cards(db), cli, db) == 1
    marks = [c for c in cli.calls if c[0] == "comment" and c[2].startswith("park-cycle: t_00000020")]
    assert len(marks) == 1 and marks[0][1] == "t_00000010"
    assert cli.of("unblock") and cli.of("unblock")[0][1] == "t_00000010"      # the PARK is asked, never the todo
    blocks = cli.of("block")
    assert blocks and blocks[0][1] == "t_00000010" and blocks[0][3:] == ["--kind", "needs_input"]
    assert "t_00000020" in blocks[0][2] and "archive t_00000020 first" in blocks[0][2]
    # once: the mark stops a second ask
    db2 = _board(tmp_path / "b", [
        ("t_00000010", "Validate", "", "scheduled", "bash", "", 0, "ada", ""),
        ("t_00000020", "deploy the patch", "", "todo", "ada", "", 0, "steward", ""),
    ], comments=[("t_00000010", "ada", "VERB: park awaiting t_00000020"),
                 ("t_00000010", "kanban-loop", "park-cycle: t_00000020 — asked")]) if (tmp_path / "b").mkdir() is None else None
    _links(db2, [("t_00000010", "t_00000020")])
    cli2 = FakeCli()
    assert kl.run_park_cycle(kl.read_cards(db2), cli2, db2) == 0 and not cli2.calls


def test_a_todo_whose_park_prerequisite_does_not_name_it_is_no_cycle(tmp_path, monkeypatch):
    """An ordinary child waiting on an ordinary park: the park is somebody's to rule on its own
    rope, and the child is waiting on its parent — not the ruler's."""
    monkeypatch.setenv("KANBAN_LOOP_CYCLE", "act")
    db = _board(tmp_path, [
        ("t_00000010", "Decide the vendor", "", "scheduled", "bash", "", 0, "ada", ""),
        ("t_00000020", "Wire the chosen vendor", "", "todo", "ada", "", 0, "ada", ""),
    ], comments=[("t_00000010", "crissa", "park — not this quarter")])
    _links(db, [("t_00000010", "t_00000020")])
    cli = FakeCli()
    assert kl.run_park_cycle(kl.read_cards(db), cli, db) == 0 and not cli.calls


# --- phantom-ref validation in the close handler (t_d808c2bf) ---------------


def _card(task_id, status):
    return kl.Card(task_id, "t", "", status, "ada", "", 0, "steward-architect")


def test_close_card_close_ref_cites_unknown_id_but_close_is_for_known_card(tmp_path):
    """The close is for `card.id`; a closer cited in the note is not the close's
    subject. An unknown closer must not refuse a close of a known card (the
    done-ruling line itself embeds open children IDs — over-broad validation
    refused legitimate closes there)."""
    cli = FakeCli()
    card = _card("t_00000001", "todo")
    reg = {card.id: card}
    rc, out = kl.close_card(cli, card, "superseded by reframe t_ffffffff, which is done", registry=reg)
    assert rc == 0
    assert cli.calls[0][0] == "promote"


def test_close_card_card_not_in_registry_refuses_and_logs(tmp_path):
    cli = FakeCli()
    card = _card("t_00000001", "todo")
    rc, out = kl.close_card(cli, card, "children done — frame is not work", registry={})
    assert rc == 1
    assert "phantom-ref" in out and "t_00000001" in out
    assert cli.calls == []
    text = (tmp_path / "kanban-loop.log").read_text()
    assert "event=phantom_ref_close" in text
    assert "task=t_00000001" in text
    assert "invalid_ids=[t_00000001]" in text


def test_close_card_repeated_phantom_refusal_logs_once(tmp_path):
    """run_done_rulings re-fires every tick: the first phantom refusal logs the
    structured line, and once the done-refused mark is on the card's thread the
    retry suppresses the repeat (t_d808c2bf — unattended-daemon log noise)."""
    cli = FakeCli()
    card = _card("t_00000001", "todo")
    rc, out = kl.close_card(cli, card, "children done — frame is not work", registry={})
    assert rc == 1 and "phantom-ref" in out
    # the loop then writes the done-refused mark onto the card's thread:
    card.comments.append(("kanban-loop", "done-refused: retry keeps trying"))
    rc, out = kl.close_card(cli, card, "children done — frame is not work",
                            registry={}, phantom_once="done-refused:")
    assert rc == 1 and "phantom-ref" in out
    text = (tmp_path / "kanban-loop.log").read_text()
    assert text.count("event=phantom_ref_close") == 1


def test_close_card_one_missing_id_among_valid_refs_refuses_and_lists_only_it(tmp_path):
    """Mixed IDs: the registry holds every ID the note cites, but the close's
    own subject is absent — one missing ID fails the whole close, and the
    structured line names only the missing ID, not the valid ones."""
    cli = FakeCli()
    card = _card("t_00000003", "todo")          # phantom subject
    other = _card("t_00000002", "done")
    reg = {other.id: other}                     # the cited closer IS valid
    rc, out = kl.close_card(cli, card, "superseded by reframe t_00000002, which is done",
                            registry=reg)
    assert rc == 1
    assert "phantom-ref" in out and "t_00000003" in out
    assert cli.calls == []                      # no state change: nothing was sent
    assert "t_00000002" not in out              # the valid ID is not named invalid
    text = (tmp_path / "kanban-loop.log").read_text()
    assert "event=phantom_ref_close" in text
    assert "invalid_ids=[t_00000003]" in text
    assert "t_00000002" not in text


def test_close_card_valid_registry_unchanged():
    cli = FakeCli()
    card = _card("t_00000001", "todo")
    other = _card("t_00000002", "done")
    reg = {card.id: card, other.id: other}
    rc, out = kl.close_card(cli, card, "superseded by reframe t_00000002, which is done", registry=reg)
    assert rc == 0
    assert cli.of("promote") and cli.of("complete")
    # todo card promotes, then completes — the pre-existing flow, untouched
    assert cli.calls[0][0] == "promote" and cli.calls[1][0] == "complete"


def test_close_card_no_registry_unchanged():
    cli = FakeCli()
    card = _card("t_00000001", "todo")
    rc, out = kl.close_card(cli, card, "superseded by reframe t_ffffffff, which is done")
    assert rc == 0  # pre-existing flow: no registry, no validation
    assert cli.calls[0][0] == "promote"
