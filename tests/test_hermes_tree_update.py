"""hermes-tree update puts the house around it back too (2026-09-28).

The 09-28 04:36 update failed inside hermes's own completion step (every `hermes` launch
"finishes" an armed update, and the house's timers started competing builds), and the
rollback left: hermes's `source-completion-pending` marker (every later `hermes` call kept
rebuilding), a detached HEAD, `main` fast-forwarded, and a profile config migrated for code
no longer there. And a SUCCESSFUL run had hermes restart the gateway before the house patches
were back. Pins, with a fake `hermes` and a fake systemctl on a real git repo:

* a failed `hermes update` comes back on the pre-update BRANCH, `main` where it was, the patch
  queue's edits back, every config byte for byte, the marker cleared, full output logged, a
  receipt written, the timers stopped and started again, and the gateway never restarted;
* a marker that was there BEFORE the update is left alone;
* a good run applies the queue, restarts the gateway only after, and wakes the timers;
* a gateway that does not come back rolls the whole update back and restarts it on the old code;
* HERMES_TREE_QUIET=0 touches no timer.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hermes_tree_upd", ROOT / "crew" / "scripts" / "hermes-tree.py")
ht = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ht)

TIMERS = ("krew-hold-sweep.timer", "krew-kanban-loop.timer", "<id-repo>le.timer")


@pytest.fixture(autouse=True)
def _no_gate_suites(monkeypatch):
    """update() runs the house gate suites after a good update (lib/gate_hold, 09-28); a test of
    update() must not run the real suites or write the live hold file."""
    seen = []
    monkeypatch.setattr(ht, "post_update_gate_check", lambda label: seen.append(label) or {"stub": "pass"})
    # nor spawn the real krew-index build (09-28)
    monkeypatch.setattr(ht, "index_after_update", lambda tree, pre, post: {"stub": True})
    # nor probe or re-pin hermes's LIVE environment (09-30): unmocked, a test of update() would install
    # into the running gateway's site-packages whenever a pin was below.
    monkeypatch.setattr(ht, "env_pins_after_update", lambda: {"stub": True})
    # nor run the real kit re-apply over the live configs (it ran here at every test run until 09-30,
    # held back only by ~/.hermes/krew-kit-reapply.off)
    monkeypatch.setattr(ht, "KIT_REAPPLY", Path("/nonexistent/krew-kit-reapply.sh"))
    return seen


def _git(*a, cwd):
    return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


class FakeSystemd:
    """is-active answers 'active' for live timers, 'inactive' for their services; the gateway's
    MainPID moves on restart unless `stuck`."""

    def __init__(self, active=("krew-hold-sweep.timer", "krew-kanban-loop.timer"), stuck=False):
        self.active = set(active)
        self.calls: list[tuple] = []
        self.pid = 100
        self.stuck = stuck

    def __call__(self, *args):
        self.calls.append(args)
        verb = args[0]
        if verb == "is-active":
            unit = args[1]
            if unit.endswith(".timer"):
                return 0, "active" if unit in self.active else "inactive"
            if unit == "hermes-gateway.service":
                return 0, "active"
            return 3, "inactive"
        if verb == "stop":
            self.active.discard(args[1]); return 0, ""
        if verb == "start":
            self.active.add(args[1]); return 0, ""
        if verb == "restart":
            if args[1] == "hermes-gateway.service" and not self.stuck:
                self.pid += 1
            return 0, ""
        if verb == "show":
            return 0, str(self.pid)
        return 0, ""

    def verbs(self, verb):
        return [c[1] for c in self.calls if c[0] == verb]


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"; _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"; seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed); _git("config", "user.email", "t@t", cwd=seed); _git("config", "user.name", "t", cwd=seed)
    (seed / "core.py").write_text("LIMIT = 3\n")
    (seed / "tool.py").write_text("def gate():\n    return True\n")
    _git("add", ".", cwd=seed); _git("commit", "-qm", "base", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed); _git("push", "-q", "origin", "main", cwd=seed)
    tree = tmp_path / "tree"; _git("clone", "-q", str(origin), str(tree), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=tree); _git("config", "user.name", "t", cwd=tree)
    _git("checkout", "-q", "-b", "hermes-tree/x", cwd=tree)           # the live tree sits on its own branch
    base = _git("rev-parse", "HEAD", cwd=tree).strip()
    queue = tmp_path / "queue"
    (tree / "core.py").write_text("LIMIT = 5\n")                      # the house patch, dirty on the tree
    ht.capture("20-limit", ["core.py"], base="HEAD", why="w", tree=tree, queue=queue)
    # upstream moves on (a file the patch does not touch)
    (seed / "tool.py").write_text("def gate():\n    return 'new'\n")
    _git("commit", "-qam", "upstream new", cwd=seed); _git("push", "-q", "origin", "main", cwd=seed)
    new = _git("rev-parse", "HEAD", cwd=seed).strip()
    home = tmp_path / "hermes-home"; (home / "profiles" / "ada").mkdir(parents=True)
    (home / "config.yaml").write_text("model: a\n")
    (home / "profiles" / "ada" / "config.yaml").write_text("toolsets:\n- kanban\n")
    marker = tmp_path / "installs" / "abc123" / "source-completion-pending"; marker.parent.mkdir(parents=True)
    fake = tmp_path / "hermes"
    fake.write_text(f"""#!/usr/bin/env bash
cd {tree}
git checkout -q main && git pull -q --ff-only origin main
echo 'source update tail not finished' > {marker}
printf 'toolsets:\\n  - kanban\\n' > {home}/profiles/ada/config.yaml
echo "building desktop ... Build inputs changed during compilation"
[ "${{FAKE_HERMES_RC:-0}}" = 0 ] || {{ echo 'Source update completion failed' >&2; exit 1; }}
rm -f {marker}
""")
    fake.chmod(0o755)
    monkeypatch.setattr(ht, "run_tests", lambda spec, tree=None: 0)
    return dict(tree=tree, queue=queue, base=base, new=new, home=home, marker=marker, hermes=fake,
                logs=tmp_path / "logs", ada=(home / "profiles" / "ada" / "config.yaml").read_bytes())


def _update(rig, sysd, **kw):
    return ht.update(tree=rig["tree"], queue=rig["queue"], hermes=rig["hermes"], home=rig["home"],
                     marker=rig["marker"], log_dir=rig["logs"], timers=TIMERS, run=sysd, **kw)


def test_a_failed_update_puts_the_krew_back(rig, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_RC", "1")
    sysd = FakeSystemd()
    assert _update(rig, sysd) == 3
    tree = rig["tree"]
    assert _git("rev-parse", "HEAD", cwd=tree).strip() == rig["base"]
    assert _git("symbolic-ref", "--short", "HEAD", cwd=tree).strip() == "hermes-tree/x"   # not detached
    assert _git("rev-parse", "main", cwd=tree).strip() == rig["base"]                     # main not advanced
    assert (tree / "core.py").read_text() == "LIMIT = 5\n"                                # the queue's edit is back
    assert (rig["home"] / "profiles" / "ada" / "config.yaml").read_bytes() == rig["ada"]    # config byte for byte
    assert not rig["marker"].exists()                                                     # no rebuild on every call
    r = json.loads((rig["queue"] / "last-update.json").read_text())
    assert r["failed"]["name"] == "hermes update" and r["rollback"]["marker_cleared"]
    assert "Build inputs changed" in Path(r["log"]).read_text()                           # full output kept
    assert sysd.verbs("stop") == ["krew-hold-sweep.timer", "krew-kanban-loop.timer"]    # only the active ones
    assert sorted(sysd.verbs("start")) == sorted(sysd.verbs("stop"))
    assert "hermes-gateway.service" not in sysd.verbs("restart")


def test_a_marker_that_was_there_before_is_left_alone(rig, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_RC", "1")
    rig["marker"].write_text("owed from before\n")
    assert _update(rig, FakeSystemd()) == 3
    assert rig["marker"].exists()


def test_a_good_update_restarts_the_gateway_only_after_the_queue(rig):
    sysd = FakeSystemd()
    assert _update(rig, sysd) == 0
    tree = rig["tree"]
    assert _git("rev-parse", "HEAD", cwd=tree).strip() == rig["new"]
    assert (tree / "core.py").read_text() == "LIMIT = 5\n" and "'new'" in (tree / "tool.py").read_text()
    assert sysd.verbs("restart")[:2] == ["hermes-gateway.service", "hermes-dashboard.service"]
    assert sorted(sysd.verbs("start")) == sorted(sysd.verbs("stop"))
    r = json.loads((rig["queue"] / "last-update.json").read_text())
    assert r["post"] == rig["new"] and r["fleet"] == "restarted" and r["failed"] is None


def test_a_gateway_that_does_not_come_back_rolls_it_all_back(rig, monkeypatch):
    sysd = FakeSystemd()
    calls = []
    monkeypatch.setattr(ht, "restart_fleet", lambda units=ht.FLEET_UNITS, *, run=None, **k: calls.append(1) or False)
    assert _update(rig, sysd) == 5
    assert len(calls) == 2                                  # the new code, then the old code after rollback
    tree = rig["tree"]
    assert _git("rev-parse", "HEAD", cwd=tree).strip() == rig["base"]
    assert _git("symbolic-ref", "--short", "HEAD", cwd=tree).strip() == "hermes-tree/x"
    assert (tree / "core.py").read_text() == "LIMIT = 5\n"
    r = json.loads((rig["queue"] / "last-update.json").read_text())
    assert r["failed"]["name"] == "gateway restart" and r["rollback"]["head"] == rig["base"]


def test_restart_fleet_reads_a_new_pid(monkeypatch):
    ok, stuck = FakeSystemd(), FakeSystemd(stuck=True)
    assert ht.restart_fleet(run=ok, wait_s=1, sleep=lambda s: None)
    assert not ht.restart_fleet(run=stuck, wait_s=0.01, sleep=lambda s: None)


def test_quiet_off_touches_no_timer(monkeypatch):
    monkeypatch.setenv("HERMES_TREE_QUIET", "0")
    sysd = FakeSystemd()
    assert ht.quiet_house(TIMERS, run=sysd) == [] and sysd.calls == []


def test_a_good_update_runs_the_gate_check_before_the_timers_wake(tmp_path, monkeypatch):
    """The User 09-28: the gate suites run BEFORE the next patrol tick - the timers are still
    stopped - and the verdicts land on the receipt."""
    src = (ROOT / "crew" / "scripts" / "hermes-tree.py").read_text()
    body = src[src.index("def update("):src.index("def post_update_gate_check")]
    assert body.index('receipt["gate_check"] = post_update_gate_check(post)') < body.index("wake_house(stopped")


def test_index_after_update_keeps_a_note_and_starts_the_build(tmp_path, monkeypatch):
    """The User 09-28: new docs reach her index on an upgrade - the docs are rebuilt from the
    live tree and the commit log pre..post is kept as an addendum."""
    import importlib.util as iu
    spec = iu.spec_from_file_location("hermes_tree_idx", ROOT / "crew" / "scripts" / "hermes-tree.py")
    m = iu.module_from_spec(spec); spec.loader.exec_module(m)       # the fixture stubbed `ht`'s copy
    tree = tmp_path / "tree"; tree.mkdir()
    _git("init", "-q", cwd=tree); _git("config", "user.email", "t@t", cwd=tree); _git("config", "user.name", "t", cwd=tree)
    (tree / "website" / "docs").mkdir(parents=True)
    (tree / "website" / "docs" / "a.md").write_text("one\n")
    _git("add", "-A", cwd=tree); _git("commit", "-qm", "base", cwd=tree)
    pre = _git("rev-parse", "HEAD", cwd=tree).strip()
    (tree / "website" / "docs" / "a.md").write_text("two\n")
    _git("commit", "-qam", "docs: memory providers moved to the catalog", cwd=tree)
    post = _git("rev-parse", "HEAD", cwd=tree).strip()
    monkeypatch.setenv("KREW_INDEX_DB", str(tmp_path / "idx" / "index.db"))
    ran = []
    real = subprocess.run
    monkeypatch.setattr(m.subprocess, "run", lambda cmd, **k: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")
                        if cmd[0] == "systemd-run" else real(cmd, **k))
    out = m.index_after_update(tree, pre, post)
    note = Path(out["note"]).read_text()
    assert "memory providers moved to the catalog" in note and "website/docs/a.md" in note
    assert out["build"] == "started"
    assert ran and ran[0][0] == "systemd-run" and "hermes,hermes-changes" in ran[0]
    monkeypatch.setenv("KREW_INDEX_ON_UPDATE", "0")
    assert m.index_after_update(tree, pre, post) == {"off": True}
