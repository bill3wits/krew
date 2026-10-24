"""crew/scripts/krew-pin-check.py: the reference install's pin check, Hermes-side checks only (the owner 2026-10-06).

It ships where its callers already call it (the Janitor's pin tick, krew-kit-reapply.sh, kanban-lean.sh, the skills),
the checks kept by the reference install (KV bytes, GPU occupancy, the packs checkout) are not in it, every check it keeps still
catches its drift, and an input that is not installed is named under `missing`, never skipped silently."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PC = ROOT / "crew" / "scripts" / "krew-pin-check.py"


def _box(tmp_path, monkeypatch, **env):
    """A machine with nothing installed: the login home, the Hermes root and KREW_HOME all under tmp_path."""
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KREW_REAL_HOME", str(home))
    for k in ("KREW_HERMES_ROOT", "KREW_KITTING_PLUGIN", "HERMES_TREE", "HERMES_PATCH_QUEUE", "KREW_T0_GATE_SOURCE",
              "KREW_T0_POLICY", "KREW_T0_ENFORCE_GATEWAYS", "KREW_T0_MIN_RULES", "KREW_ZOEY_SOUL_CAP",
              "KREW_GATEWAY_TOPOLOGY", "KREW_CREW_CONFIG", "KREW_SOULS_TRUNK") + tuple(k for k in os.environ if k.startswith("KREW_SOUL_CAP_")):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    return home


def _mod(name="krew_pin_check_t"):
    loader = SourceFileLoader(name, str(PC))
    spec = spec_from_loader(loader.name, loader)
    m = module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_it_ships_where_every_caller_calls_it():
    # krew-kit-reapply.sh and kanban-lean.sh: $(dirname $0)/.. is crew/, then scripts/krew-pin-check.py
    for sh in ("krew-kit-reapply.sh", "kanban-lean.sh"):
        text = (ROOT / "crew" / "scripts" / sh).read_text()
        assert re.search(r'^(REPO|ROOT)="\$\(cd "\$\(dirname "\$0"\)/\.\." && pwd\)"', text, re.M), sh
        assert '/scripts/krew-pin-check.py' in text, sh
    # the Janitor: its default is the crew copy beside it ($KREW_HOME/janitor -> $KREW_HOME/crew/scripts, or the checkout)
    jan = (ROOT / "janitor" / "krew-janitor.py").read_text()
    assert 'PIN_CHECK = _p(_P.get("pin_check") or _HERE.parent / "crew" / "scripts" / "krew-pin-check.py")' in jan
    assert (ROOT / "janitor").resolve().parent / "crew" / "scripts" / "krew-pin-check.py" == PC
    for skill in ("ada-orchestrate", "render-lane", "steward-sweep"):
        assert "krew-pin-check.py" in (ROOT / "skills" / skill / "SKILL.md").read_text(), skill


def test_the_reference_only_checks_are_not_in_it(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch)
    m = _mod()
    for kept in ("check_skills", "check_preload", "check_hermes_tree_patches", "check_tier0", "check_fallbacks",
                 "check_crew_souls"):
        assert callable(getattr(m, kept)), kept
    for gone in ("check_kv", "load_pins", "check_occupancy", "check_toolsets", "check_zoey_voice_kit",
                 "check_kanban_protocol", "check_hindsight_recall"):
        assert not hasattr(m, gone), gone
    src = PC.read_text()
    for word in ("kv-pins", "KV_BYTES", "hermes-packs", "PACK_DIST", ":8005", ":8000", "bad Render switchover"):
        assert word not in src, word


def test_a_bare_machine_is_not_red_and_names_every_check_it_could_not_run(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch, KREW_KITTING_PLUGIN=tmp_path / "no-kitting.py", HERMES_PATCH_QUEUE=tmp_path / "no-queue")
    m = _mod()
    r = m.check()
    assert r["ok"] is True and r["failures"] == []
    text = "\n".join(r["missing"])
    for what in ("skill belts not checked", "preload pin not checked", "Hermes patches not checked", "T0 not checked"):
        assert what in text, what
    # the CLI prints them (never a bare "ok" that hid what it did not look at)
    out = subprocess.run([sys.executable, str(PC)], capture_output=True, text=True, env=os.environ.copy())
    assert out.returncode == 0 and out.stdout.startswith("pin-check ok")
    assert "missing: skill belts not checked" in out.stdout
    js = json.loads(subprocess.run([sys.executable, str(PC), "--json"], capture_output=True, text=True,
                                   env=os.environ.copy()).stdout)
    assert js["ok"] is True and js["missing"]


def test_preload_pin_holds_the_plugin_the_env_and_the_soul(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch)
    m = _mod()
    link = tmp_path / "krew-skill-preload"; link.mkdir(); (link / "__init__.py").write_text("x")
    zoey = tmp_path / "zoey"; zoey.mkdir()
    cfg = zoey / "config.yaml"
    cfg.write_text("plugins:\n  enabled: [team-zoey, krew-skill-preload]\n")
    drop = tmp_path / "skills.conf"; drop.write_text("[Service]\nEnvironment=HERMES_TUI_SKILLS=none\n")
    soul = zoey / "SOUL.md"; soul.write_text("identity\n" * 10)
    kw = dict(plugin_link=link, zoey_config=cfg, dropin=drop, unit_env="none", soul=soul)
    assert m.check_preload(**kw) == []
    cfg.write_text("plugins:\n  enabled: [team-zoey]\n")
    assert any("does not enable krew-skill-preload" in x for x in m.check_preload(**kw))
    cfg.write_text("plugins:\n  enabled: [krew-skill-preload]\n")
    assert any("preloads 'all'" in x for x in m.check_preload(**{**kw, "unit_env": "all"}))
    drop.write_text("[Service]\n")
    assert any("does not preload none" in x for x in m.check_preload(**kw))
    drop.write_text("[Service]\nEnvironment=HERMES_TUI_SKILLS=none\n")
    soul.write_text("x" * (m.ZOEY_SOUL_CAP + 1))
    f = m.check_preload(**kw)
    assert any(f"cap {m.ZOEY_SOUL_CAP}" in x and "do not trim" in x and "KREW_ZOEY_SOUL_CAP" in x for x in f)
    soul.write_text("x" * m.ZOEY_SOUL_CAP)                     # at the cap: not a failure
    assert m.check_preload(**kw) == []
    m.MISSING.clear()
    assert m.check_preload(**{**kw, "zoey_config": tmp_path / "no-zoey" / "config.yaml"}) == []
    assert any("no zoey profile" in x for x in m.MISSING)


def _git(tree):
    return lambda *a: subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                     check=True, capture_output=True, text=True)


def test_hermes_tree_patches_catch_a_reset(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch)
    m = _mod()
    tree = tmp_path / "hermes-agent"; tree.mkdir()
    git = _git(tree)
    git("init", "-q")
    (tree / "model_tools.py").write_text("a = 1\nb = 2\nc = 3\n")
    git("add", "."), git("commit", "-q", "-m", "upstream")
    (tree / "model_tools.py").write_text("a = 1\nb = 2  # lean kit\nc = 3\n")
    queue = tmp_path / "patches"; queue.mkdir()
    (queue / "50-kanban-lean-spawn.patch").write_text("# why: x\n" + git("diff").stdout)
    assert m.check_hermes_tree_patches(tree=tree, queue=queue) == []
    git("checkout", "-q", "--", "model_tools.py")                       # the update wiped it
    f = m.check_hermes_tree_patches(tree=tree, queue=queue)
    assert len(f) == 1 and "50-kanban-lean-spawn.patch" in f[0] and "apply" in f[0]
    assert m.check_hermes_tree_patches(tree=tmp_path / "none", queue=queue) != []
    (tree / "untracked.py").write_text("x")                              # extra dirt is not a pin
    git("apply", str(queue / "50-kanban-lean-spawn.patch"))
    assert m.check_hermes_tree_patches(tree=tree, queue=queue) == []
    # its default queue is krew's own patches/ (the one krew-patch.py applies), HERMES_PATCH_QUEUE overrides
    m.MISSING.clear()
    assert m.check_hermes_tree_patches(tree=tree) in ([], m.check_hermes_tree_patches(tree=tree))
    assert not any("no patch queue" in x for x in m.MISSING)
    monkeypatch.setenv("HERMES_PATCH_QUEUE", str(tmp_path / "elsewhere"))
    m.MISSING.clear()
    assert m.check_hermes_tree_patches(tree=tree) == [] and any("no patch queue" in x for x in m.MISSING)


def test_overlapping_patches_are_on_the_tree_when_the_stack_rebuilds_it(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch)
    m = _mod()
    tree = tmp_path / "hermes-agent"; tree.mkdir()
    git = _git(tree)
    git("init", "-q")
    (tree / "w.py").write_text("a = 1\nb = 2\nc = 3\n")
    git("add", "."), git("commit", "-q", "-m", "upstream")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    queue = tmp_path / "patches"; queue.mkdir()
    (tree / "w.py").write_text("a = 1\nb = 2  # 70\nc = 3\n")
    (queue / "70-x.patch").write_text("# why: x\n" + git("diff").stdout)
    git("add", "w.py")
    (tree / "w.py").write_text("a = 1\nb = 2  # 70 + 75\nc = 3\n")
    (queue / "75-y.patch").write_text("# why: y\n" + git("diff").stdout)
    git("add", "w.py")
    assert m.check_hermes_tree_patches(tree=tree, queue=queue) == []    # crew/scripts/hermes-tree.py stack_check
    git("checkout", "-q", "HEAD", "--", "w.py")                           # the update wiped both
    assert len(m.check_hermes_tree_patches(tree=tree, queue=queue)) == 2


def test_soul_caps_are_knobs_from_the_env_or_crew_yaml(tmp_path, monkeypatch):
    home = _box(tmp_path, monkeypatch)
    prof = home / ".hermes" / "profiles"
    (prof / "scout").mkdir(parents=True)
    (prof / "scout" / "SOUL.md").write_text("x" * 2001)
    m = _mod()
    f = m.check_crew_souls()
    assert len(f) == 1 and "scout SOUL.md is 2001 chars (cap 2000)" in f[0]
    assert "NEVER move or summarise the standing laws" in f[0] and "raise KREW_SOUL_CAP_SCOUT" in f[0]
    monkeypatch.setenv("KREW_SOUL_CAP_SCOUT", "3000")
    assert _mod().check_crew_souls() == []
    # the Janitor imports this file in its own process: crew.yaml's env: is read here too
    monkeypatch.delenv("KREW_SOUL_CAP_SCOUT")
    (Path(os.environ["KREW_HOME"]) / "crew.yaml").write_text('env:\n  KREW_SOUL_CAP_SCOUT: "2500"\n')
    m2 = _mod()
    assert m2.soul_cap_for("scout", 2000) == 2500 and m2.check_crew_souls() == []
    monkeypatch.setenv("KREW_SOUL_CAP_SCOUT", "100")                     # the env still wins
    assert _mod().soul_cap_for("scout", 2000) == 100


def test_tier0_pin(tmp_path, monkeypatch):
    home = _box(tmp_path, monkeypatch)
    m = _mod()
    gate = tmp_path / "rails" / "tier0_gate.py"; gate.parent.mkdir(); gate.write_text("# gate\n")
    live = tmp_path / "live" / "tier0_gate.py"; live.parent.mkdir(); live.write_text("# gate\n")
    policy = tmp_path / "tier0.yaml"
    policy.write_text("blocklist:\n" + "".join(f"  - pattern: 'rm -rf /x{i}'\n" for i in range(25)))
    units = tmp_path / "units"
    (units / "hermes-ada-gateway.service.d").mkdir(parents=True)
    (units / "hermes-ada-gateway.service").write_text("[Service]\n")
    (units / "hermes-ada-gateway.service.d" / "krew-tier0.conf").write_text("[Service]\nEnvironment=KREW_T0_MODE=enforce\n")
    live_dir = tmp_path / "profiles"; (live_dir / "bash").mkdir(parents=True)
    (live_dir / "bash" / "config.yaml").write_text(
        "hooks:\n  pre_tool_call:\n    - command: python3 ~/.krew/rails/tier0_gate.py\n      fail_closed: true\n")
    kw = dict(repo=gate, live=live, policy=policy, live_dir=live_dir, unit_dir=units, gateways=("ada", "lila"))
    assert m.check_tier0(**kw) == []                    # lila has no unit here: nothing to run shadow
    live.write_text("# edited\n")
    assert any("!=" in x for x in m.check_tier0(**kw))
    live.write_text("# gate\n")
    policy.write_text("blocklist:\n  - pattern: 'x'\n")
    assert any("rules (< 20)" in x for x in m.check_tier0(**kw))
    policy.write_text("blocklist:\n" + "".join(f"  - pattern: 'rm -rf /x{i}'\n" for i in range(25)))
    (live_dir / "bash" / "config.yaml").write_text(
        "hooks:\n  pre_tool_call:\n    - command: python3 ~/.krew/rails/tier0_gate.py\n")
    assert any("without fail_closed" in x for x in m.check_tier0(**kw))
    (live_dir / "bash" / "config.yaml").write_text("{}\n")
    (units / "hermes-ada-gateway.service.d" / "krew-tier0.conf").write_text("[Service]\n")
    assert any("hermes-ada-gateway has no KREW_T0_MODE=enforce" in x for x in m.check_tier0(**kw))
    m.MISSING.clear()
    assert m.check_tier0(**{**kw, "live": tmp_path / "x.py", "policy": tmp_path / "x.yaml"}) == []
    assert any("rails are not installed" in x for x in m.MISSING)
    m.MISSING.clear()
    assert m.check_tier0(**{**kw, "gateways": ("vera",)}) == [] and any("T0 enforce not checked" in x for x in m.MISSING)
    # the defaults are krew's: the rails under KREW_HOME, the checkout's rails/tier0_gate.py, KREW_T0_POLICY
    assert m.T0_GATE_LIVE == Path(os.environ["KREW_HOME"]) / "rails" / "tier0_gate.py"
    assert m.T0_GATE_REPO == ROOT / "rails" / "tier0_gate.py"
    assert m.T0_POLICY == Path(os.environ["KREW_HOME"]) / "tier0.yaml"
    _ = home


def test_a_fallback_that_is_the_primary_is_red(tmp_path, monkeypatch):
    home = _box(tmp_path, monkeypatch)
    m = _mod()
    root = home / ".hermes" / "config.yaml"
    root.write_text("model:\n  base_url: http://127.0.0.1:8000/v1\nfallback_model:\n  base_url: http://127.0.0.1:8000/v1/\n")
    f = m.check_fallbacks()
    assert len(f) == 1 and "fallback is the primary: host" in f[0]
    root.write_text("model:\n  base_url: http://127.0.0.1:8000/v1\nfallback_model:\n  base_url: http://other:9/v1\n")
    assert m.check_fallbacks() == []


def test_the_skill_belt_reads_the_installed_kitting_plugin(tmp_path, monkeypatch):
    home = _box(tmp_path, monkeypatch)
    kit = home / ".hermes" / "plugins" / "krew-kitting"
    kit.mkdir(parents=True)
    for f in (ROOT / "plugins" / "krew-kitting").iterdir():
        if f.is_file():
            (kit / f.name).write_bytes(f.read_bytes())
    (home / ".hermes" / "profiles").mkdir()
    m = _mod()
    assert m.KITTING == kit / "__init__.py"
    m.MISSING.clear()
    assert m.check_skills() == [] and m.MISSING == []          # no crew profile installed: nothing to drift


def test_a_fresh_install_is_named_not_red(tmp_path, monkeypatch):
    """krew-install seeds a Zoey SOUL but no config.yaml and no gateway unit: the preload is named as not
    checked (as T0 is), never red, and her SOUL cap is still checked."""
    _box(tmp_path, monkeypatch)
    m = _mod()
    zoey = tmp_path / "zoey"; zoey.mkdir()
    soul = zoey / "SOUL.md"; soul.write_text("identity\n")
    units = tmp_path / "units"; (units / "hermes-zoey-gateway.service.d").mkdir(parents=True)
    kw = dict(plugin_link=tmp_path / "no-link", zoey_config=zoey / "config.yaml",
              dropin=units / "hermes-zoey-gateway.service.d" / "skills.conf", soul=soul)
    m.MISSING.clear()
    assert m.check_preload(**kw) == []
    assert any("has no config.yaml" in x for x in m.MISSING)
    soul.write_text("x" * (m.ZOEY_SOUL_CAP + 1))
    assert any(f"cap {m.ZOEY_SOUL_CAP}" in x for x in m.check_preload(**kw))
    soul.write_text("identity\n")
    (zoey / "config.yaml").write_text("plugins:\n  enabled: [krew-skill-preload]\n")
    link = tmp_path / "link"; link.mkdir(); (link / "__init__.py").write_text("x")
    m.MISSING.clear()
    assert m.check_preload(**{**kw, "plugin_link": link}) == []
    assert any("hermes-zoey-gateway.service is not under" in x for x in m.MISSING)
    (units / "hermes-zoey-gateway.service").write_text("[Service]\n")
    f = m.check_preload(**{**kw, "plugin_link": link})
    assert any("drop-in missing" in x for x in f), "an installed gateway without the drop-in is red"


def test_soul_caps_leave_out_the_crew_trunk(tmp_path, monkeypatch):
    trunk = tmp_path / "TRUNK.md"; trunk.write_text("<!-- TRUNK -->\n# Honesty\n" + "t" * 3000 + "\n")
    _box(tmp_path, monkeypatch, KREW_SOULS_TRUNK=trunk)
    m = _mod()
    live = tmp_path / "profiles"; (live / "judge").mkdir(parents=True)
    soul = live / "judge" / "SOUL.md"
    soul.write_text(trunk.read_text().rstrip() + "\n\n" + "j" * 100)        # what krew-install writes
    assert m.check_crew_souls(live_dir=live) == []
    soul.write_text(trunk.read_text().rstrip() + "\n\n" + "j" * 2601)
    assert any("judge SOUL.md is 2601 chars (cap 2600)" in x for x in m.check_crew_souls(live_dir=live))
    soul.write_text("j" * 2601)                                               # no trunk: all of it counts
    assert any("cap 2600" in x for x in m.check_crew_souls(live_dir=live))
    assert (ROOT / "souls" / "TRUNK.md").is_file(), "the default trunk is the shipped one"
