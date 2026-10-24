"""The code canary on known answers (lib/code_canary.py).

The 09-22 wipe (same minute as a worker's scratch /tmp delete; mechanism unproven) left every
package folder holding only __pycache__. The canary must alarm on that shape, must never follow a
symlink itself, must keep alarming until the tree is repaired or rebaselined, and in the
default report mode must never touch the board.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))


@pytest.fixture
def cc(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_CODE_CANARY_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("KREW_CODE_CANARY_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("KREW_HERMES_BIN", str(tmp_path / "no-hermes"))
    monkeypatch.delenv("KREW_CODE_CANARY", raising=False)
    import code_canary
    mod = importlib.reload(code_canary)
    monkeypatch.setattr(mod, "live_worker_cards", lambda: ["t_bbbbbbbb"])
    monkeypatch.setattr(mod, "ms_running", lambda: False)
    return mod


def _pkg(root: Path, name: str, n: int) -> Path:
    d = root / name
    (d / "__pycache__").mkdir(parents=True)
    for i in range(n):
        (d / f"m{i}.py").write_text("x = 1\n")
        (d / "__pycache__" / f"m{i}.cpython-311.pyc").write_bytes(b"\0")
    return d


def _hollow(d: Path) -> None:
    """The 09-22 shape: the .py files go, __pycache__ stays."""
    for f in d.rglob("*.py"):
        f.unlink()


def test_a_hollowed_tree_alarms_and_names_the_suspects(cc, tmp_path):
    sp = tmp_path / "site-packages"
    _pkg(sp, "yaml", 10)
    paths = {"venv": sp}
    assert cc.check(paths=paths)["ok"]
    _hollow(sp / "yaml")
    res = cc.check(card="t_aaaaaaaa", paths=paths)
    assert not res["ok"]
    assert res["drops"] == [{"tree": "venv", "was": 10, "high": 10, "now": 0}]
    assert res["suspects"] == ["t_aaaaaaaa", "t_bbbbbbbb"], "the exiting card first, live workers after"
    assert json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])["drops"]


def test_the_alarm_stands_until_repair_or_rebaseline(cc, tmp_path):
    sp = tmp_path / "sp"
    _pkg(sp, "a", 10)
    paths = {"t": sp}
    cc.check(paths=paths)
    _hollow(sp / "a")
    assert not cc.check(paths=paths)["ok"]
    assert not cc.check(paths=paths)["ok"], "a drop never becomes the new baseline by itself"
    cc.rebaseline(paths=paths)
    assert cc.check(paths=paths)["ok"]


def test_a_vanished_tree_alarms_a_new_one_does_not(cc, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _pkg(a, "p", 3)
    cc.check(paths={"a": a})
    _pkg(b, "q", 3)
    res = cc.check(paths={"a": tmp_path / "gone", "b": b})
    assert [d["tree"] for d in res["drops"]] == ["a"] and res["drops"][0]["now"] is None


def test_small_changes_are_updates_not_damage(cc, tmp_path):
    sp = tmp_path / "sp"
    _pkg(sp, "p", 100)
    cc.check(paths={"t": sp})
    for i in range(10):
        (sp / "p" / f"m{i}.py").unlink()
    assert cc.check(paths={"t": sp})["ok"], "a 10 % change is an update"


def test_the_high_water_stops_a_slow_ratchet(cc, tmp_path):
    sp = tmp_path / "sp"
    _pkg(sp, "p", 100)
    cc.check(paths={"t": sp})
    removed = 0
    for step in range(5):              # 15 % of what is left each time: never > 20 % a step
        n = int((100 - removed) * 0.15)
        for i in range(removed, removed + n):
            (sp / "p" / f"m{i}.py").unlink()
        removed += n
        res = cc.check(paths={"t": sp})
    assert 100 - removed < 50 and not res["ok"], "below half the high-water is damage however it got there"


def test_the_canary_never_follows_a_symlink(cc, tmp_path):
    real = tmp_path / "real"
    _pkg(real, "big", 50)
    tree = tmp_path / "tree"
    _pkg(tree, "own", 2)
    (tree / "link-to-real").symlink_to(real, target_is_directory=True)
    (tree / "own" / "file-link.py").symlink_to(real / "big" / "m0.py")
    assert cc.count_py(tree) == 2


def test_report_mode_never_touches_the_board(cc, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cc, "_kanban", lambda *a: calls.append(a) or (0, "Created t_cccccccc"))
    sp = tmp_path / "sp"
    _pkg(sp, "p", 5)
    cc.check(paths={"t": sp})
    _hollow(sp / "p")
    res = cc.check(card="t_aaaaaaaa", paths={"t": sp})
    assert cc.mode() == "report"
    assert cc.act(res) == [] and calls == []


def test_act_mode_comments_the_suspects_and_asks_zoey_once(cc, tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_CODE_CANARY", "act")
    calls = []
    monkeypatch.setattr(cc, "_kanban", lambda *a: calls.append(a) or (0, "Created t_cccccccc"))
    sp = tmp_path / "sp"
    _pkg(sp, "p", 5)
    cc.check(paths={"t": sp})
    _hollow(sp / "p")
    cc.act(cc.check(card="t_aaaaaaaa", paths={"t": sp}))
    verbs = [c[0] for c in calls]
    assert verbs == ["comment", "comment", "create", "block"]
    assert calls[0][1] == "t_aaaaaaaa" and calls[0][2].startswith("cause: damage")
    create = calls[2]
    assert create[create.index("--assignee") + 1] == "ada"
    assert create[create.index("--idempotency-key") + 1].startswith("code-canary:"), "one card per incident"
    assert calls[3][-2:] == ("--kind", "needs_input"), "it reaches Zoey as a question"


def test_record_check_finds_a_listed_file_that_is_gone(cc, tmp_path):
    sp = tmp_path / "site-packages"
    _pkg(sp, "yaml", 2)
    di = sp / "PyYAML-6.0.3.dist-info"
    di.mkdir()
    (di / "RECORD").write_text("yaml/m0.py,sha256=x,1\nyaml/m1.py,sha256=x,1\n"
                               "yaml/__pycache__/m0.cpython-311.pyc,,\nPyYAML-6.0.3.dist-info/RECORD,,\n")
    assert cc.record_check(sp)["ok"]
    _hollow(sp / "yaml")
    res = cc.record_check(sp)
    assert not res["ok"] and res["missing_files"] == 2


def test_the_default_trees_include_everything_the_wipe_reached(cc):
    names = set(cc.default_trees())
    for must in ("hermes/plugins", "hermes/security", *cc.CODE_TREES):
        assert must in names
    assert "hermes-agent/venv" not in names, "retired 09-26; nothing runs from it"


def test_the_canary_watches_the_environment_hermes_runs_from(cc, tmp_path):
    inst = tmp_path / "installs" / "08f7d59f9758a75d"
    sp = inst / "environments" / "gen1" / "venv" / "lib" / "python3.14" / "site-packages"
    sp.mkdir(parents=True)
    (inst / "facts.json").write_text('{"packages": {"venv": {"environment": "%s"}}}' % sp.parents[2])
    (tmp_path / "installs" / "broken").mkdir()
    (tmp_path / "installs" / "broken" / "facts.json").write_text("{not json")
    assert cc.hermes_live_envs(tmp_path / "installs") == {"hermes-env/08f7d59f": sp}


def test_one_incident_one_key_even_when_fast_checks_run_clean_between(cc, tmp_path, monkeypatch):
    """A worker exit's fast check never counts the service envs. If it moved a crew-wide
    'last clean', every janitor tick after it would key a NEW Zoey card for the same drop."""
    env, hermes = tmp_path / "env", tmp_path / "hermes"
    _pkg(env, "torch", 10)
    _pkg(hermes, "yaml", 10)
    full = {"env/vllm": env, "hermes": hermes}
    cc.check(paths=full)
    _hollow(env / "torch")
    first = cc.check(paths=full)
    assert cc.check(card="t_aaaaaaaa", paths={"hermes": hermes})["ok"], "fast check: nothing it counts moved"
    second = cc.check(paths=full)
    assert not first["ok"] and not second["ok"]
    assert first["clean_at"] == second["clean_at"], "same incident, same Zoey card key"
    assert not first["repeat"] and second["repeat"], "one event per incident"
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1


def test_accepting_one_tree_leaves_a_real_drop_standing(cc, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _pkg(a, "p", 10)
    _pkg(b, "q", 10)
    paths = {"users-change": a, "real-damage": b}
    cc.check(paths=paths)
    _hollow(a / "p")
    _hollow(b / "q")
    cc.rebaseline(paths=paths, only=["users-change"])
    res = cc.check(paths=paths)
    assert [d["tree"] for d in res["drops"]] == ["real-damage"]
    with pytest.raises(KeyError):
        cc.rebaseline(paths=paths, only=["typo"])


def test_a_standing_drop_is_not_pinned_on_the_next_card(cc, tmp_path, monkeypatch):
    import subprocess
    sp = tmp_path / "sp"
    _pkg(sp, "p", 10)
    env = {**__import__("os").environ, "KREW_CODE_CANARY_TREES": f"t={sp}",
           "KREW_CODE_CANARY": "report"}
    cli = [sys.executable, str(ROOT / "janitor" / "krew-code-canary.py"), "check"]
    assert subprocess.run(cli, env=env).returncode == 0
    _hollow(sp / "p")
    first = subprocess.run(cli + ["--card", "t_aaaaaaaa"], env=env, capture_output=True, text=True)
    later = subprocess.run(cli + ["--card", "t_dddddddd"], env=env, capture_output=True, text=True)
    assert first.returncode == later.returncode == 3
    assert "DROP" in first.stdout and "suspects: t_aaaaaaaa" in first.stdout
    assert "standing drop since" in later.stdout and "t_dddddddd" not in later.stdout


def test_live_worker_cards_reads_both_scope_names(monkeypatch):
    """The wrapper's scope and hermes's own (2026-09-27) name the same thing: a live card."""
    import code_canary
    mod = importlib.reload(code_canary)

    class R:
        stdout = ("kanban-worker-t_aaaaaaaa-123.scope loaded active running x\n"
                  "hermes-worker-kanban-t_bbbbbbbb-run-7.scope loaded active running y\n"
                  "hermes-worker-terminal-zz.scope loaded active running z\n")

    seen = {}

    def run(argv, **kw):
        seen["argv"] = argv
        return R()

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert mod.live_worker_cards() == ["t_aaaaaaaa", "t_bbbbbbbb"]
    assert "hermes-worker-kanban-*" in seen["argv"] and "kanban-worker-*" in seen["argv"]
