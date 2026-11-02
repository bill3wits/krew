"""krew init as a fresh buyer runs it (the 1.0.2 buyer test on crissatest, 2026-10-30).

1  no `hermes` command: init fails the cron step loudly and never ends "init done".
5  a scratch --hermes-home/--krew-home writes nothing outside the homes it was given and never runs another
   install's `hermes` (a scratch run rewrote the real launchers, 38 units and the Crissa home's panel files).
9  a model key lands where each profile reads it (profiles/<p>/.env), and the brain switch moves key and model lines.
2  Hermes FIRST: its installer runs on the clean pin, krew's patches after it, read back from the tree
   (needs a Hermes checkout at the pin: KREW_HERMES_PIN_TREE).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "krew-install.py"


def _init_mod():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("krewtest_init_buyer", ROOT / "scripts" / "krew_install_init.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _env(home: Path, **kw) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KREW_", "HERMES_", "CRISSA_", "XAI_", "ANTHROPIC_"))}
    env.update(HOME=str(home), PATH="/usr/bin:/bin", CRISSA_EAR="off")
    env.update(kw)
    return env


def _tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() or p.is_symlink():
            try:
                out[str(p)] = p.read_bytes() if p.is_file() else ("->" + os.readlink(p)).encode()
            except OSError:
                out[str(p)] = b"?"
    return out


def test_no_hermes_command_fails_the_crons_loudly_and_init_is_not_done(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run([sys.executable, str(SCRIPT), "init", "--hermes-home", str(home / ".hermes"), "--krew-home",
                        str(home / ".krew"), "--no-hermes", "--no-discover", "--yes", "--endpoint",
                        "http://127.0.0.1:9/v1", "--model", "m"],
                       capture_output=True, text=True, env=_env(home), stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 1, r.stdout[-2000:]
    assert "cron     NOT CREATED: no `hermes` command" in r.stdout and "CRONS FAILED:" in r.stdout
    assert "== init NOT done:" in r.stdout and "== init done" not in r.stdout


def test_a_scratch_init_writes_only_under_the_homes_it_was_given(tmp_path):
    home = tmp_path / "home"
    real_h = home / ".hermes" / "hermes-agent"
    # the account's REAL install: a launcher on PATH whose code root is ~/.hermes/hermes-agent, a user systemd
    # folder and a Crissa home. A scratch init must touch none of them and never run that launcher.
    (real_h / ".hermes" / "bin").mkdir(parents=True)
    marker = tmp_path / "REAL-HERMES-RAN"
    (real_h / ".hermes" / "bin" / "hermes").write_text(
        "#!/bin/sh\n# sys.path.insert(0, '%s')\ntouch %s\n" % (real_h, marker))
    (real_h / ".hermes" / "bin" / "hermes").chmod(0o755)
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "hermes").write_text("#!/bin/sh\nexec %s \"$@\"\n" % (real_h / ".hermes" / "bin" / "hermes"))
    (home / ".local" / "bin" / "hermes").chmod(0o755)
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    (home / ".crissa" / "run").mkdir(parents=True)
    (home / ".crissa" / "run" / "procs.json").write_text(json.dumps({"children": {"player": {"port": 8300}}}))
    before = _tree(home)
    scratch = tmp_path / "scratch"
    hh, kh = scratch / ".hermes", scratch / ".krew"
    r = subprocess.run([sys.executable, str(SCRIPT), "init", "--hermes-home", str(hh), "--krew-home", str(kh),
                        "--no-hermes", "--no-discover", "--yes", "--endpoint", "http://127.0.0.1:9/v1", "--model", "m"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300,
                       env=_env(home, PATH=f"{home / '.local' / 'bin'}:/usr/bin:/bin"))
    assert not marker.exists(), "the real install's hermes ran under a scratch HERMES_HOME"
    assert _tree(home) == before, "init wrote outside the homes it was given"
    assert (kh / "systemd-user").is_dir() and any((kh / "systemd-user").glob("krew-*.service"))
    assert "refused  " in r.stdout and "--crissa-home" in r.stdout
    assert "runs another install's code" in r.stdout or "another install" in r.stdout
    assert r.returncode == 1 and "CRONS FAILED" in r.stdout          # no hermes serves the scratch home: said, loudly


def test_the_key_goes_to_every_profile_env(tmp_path):
    init = _init_mod()
    hh, kh = tmp_path / "h", tmp_path / "k"
    said = []
    init.write_profiles(hh, "https://api.example/v1", "m", "ANTHROPIC_API_KEY", "sk-test-value", said.append,
                        dry=False, kh=kh)
    for p in init.CREW:
        env = (hh / "profiles" / p / ".env").read_text()
        assert env == "ANTHROPIC_API_KEY=sk-test-value\n", p
        assert oct((hh / "profiles" / p / ".env").stat().st_mode & 0o777) == "0o600"
        assert (kh / "packs" / "distributions" / p / "config.yaml").read_text() == \
            (hh / "profiles" / p / "config.yaml").read_text()
    assert (kh / "packs" / "distributions" / "BRIDGE-config.yaml").is_file()
    assert not any("sk-test-value" in x for x in said)


def test_profile_keys_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_INIT_PROFILE_KEYS", "0")
    init = _init_mod()
    hh = tmp_path / "h"
    init.write_profiles(hh, "https://api.example/v1", "m", "K", "v-123", lambda *_: None, dry=False)
    assert (hh / ".env").read_text() == "K=v-123\n" and not (hh / "profiles" / "ada" / ".env").exists()


def test_an_installer_example_config_is_replaced_by_krews(tmp_path):
    init = _init_mod()
    hh = tmp_path / "h"
    hh.mkdir()
    (hh / "config.yaml").write_text("# Hermes's cli-config.yaml.example\nmodel: {}\n")
    init.write_profiles(hh, "http://127.0.0.1:9/v1", "m", "K", "", lambda *_: None, dry=False,
                        replace={hh / "config.yaml"})
    assert "multiplex_profiles: true" in (hh / "config.yaml").read_text()
    assert (hh / "config.yaml.hermes-installer").read_text().startswith("# Hermes's")
    (hh / "config.yaml").write_text("mine: 1\n")
    init.write_profiles(hh, "http://127.0.0.1:9/v1", "m", "K", "", lambda *_: None, dry=False)
    assert (hh / "config.yaml").read_text() == "mine: 1\n"               # never yours


def test_the_brain_switch_moves_model_lines_pack_and_keys(tmp_path):
    init = _init_mod()
    hh, kh = tmp_path / "h", tmp_path / "k"
    init.write_profiles(hh, "http://127.0.0.1:8005/v1", "crew", "KREW_MODEL_API_KEY", "", lambda *_: None, dry=False,
                        kh=kh)
    b = init.resolve_brains("claude")
    said = []
    init.switch_brains(hh, kh, b, b["crew"]["key_env"], "sk-new", said.append, dry=False)
    for f in (hh / "profiles" / "ada" / "config.yaml", kh / "packs" / "distributions" / "ada" / "config.yaml",
              hh / "profiles" / "crissa" / "config.yaml", hh / "config.yaml"):
        t = f.read_text()
        assert f"base_url: {b['crew']['base_url']}" in t and f"key_env: {b['crew']['key_env']}" in t, f
    assert f"{b['crew']['key_env']}=sk-new\n" in (hh / "profiles" / "ada" / ".env").read_text()
    assert f"{b['crew']['key_env']}=sk-new\n" in (hh / ".env").read_text()
    assert not any("sk-new" in x for x in said)


@pytest.mark.skipif(not os.environ.get("KREW_HERMES_PIN_TREE")
                    or not (Path(os.environ["KREW_HERMES_PIN_TREE"]) / ".git").exists(),
                    reason="set KREW_HERMES_PIN_TREE to a Hermes git checkout at the pin")
def test_hermes_first_then_the_patches_read_from_the_tree(tmp_path, monkeypatch):
    """Hermes's installer runs on the clean pin (it autostashes local changes), the patches after it; a stand-in
    installer records whether the tree was clean when it ran and makes the launcher the way the real one does."""
    init = _init_mod()
    hh = tmp_path / "home" / ".hermes"
    tree = hh / "hermes-agent"
    subprocess.run(["git", "clone", "-q", "--shared", os.environ["KREW_HERMES_PIN_TREE"], str(tree)], check=True)
    pin = json.loads((ROOT / "config" / "hermes-pin.json").read_text())
    subprocess.run(["git", "-C", str(tree), "checkout", "-q", "--force", pin["commit"]], check=True)
    subprocess.run(["git", "-C", str(tree), "clean", "-fdq"], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-patch.py"), "apply", "--hermes", str(tree)],
                   check=True, capture_output=True)                       # a re-run: the patches are already in
    seen = tmp_path / "installer-saw.txt"
    inst = tmp_path / "install.sh"
    inst.write_text(
        "#!/bin/bash\n"
        f"git -C {tree} status --porcelain --untracked-files=no > {seen}\n"
        f"mkdir -p {tree}/.hermes/bin\n"
        f"printf '#!/bin/sh\\n# sys.path.insert(0, %s)\\n' \"'{tree}'\" > {tree}/.hermes/bin/hermes\n"
        f"chmod +x {tree}/.hermes/bin/hermes\n")
    monkeypatch.setenv("KREW_HERMES_INSTALLER", str(inst))
    steps = init.Steps(lambda *_: None)
    said = []
    got = init.hermes_first(hh, said.append, dry=False, hermes_bin="hermes", scratch=False, steps=steps)
    assert seen.read_text() == "", "the installer ran over krew's patches (it would stash them)"
    assert got == str(tree / ".hermes" / "bin" / "hermes")
    assert not steps.failed(), steps.rows
    assert any(f"applied  {len(pin['patches'])} of {len(pin['patches'])} in the tree" in x for x in said)


def test_env_pins_find_the_uv_hermes_installed(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "ms" / "lib"))
    import hermes_env_pins as P
    uv = tmp_path / "h" / "tools" / "uv-0.12.3-linux-x64" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "h"))
    assert P.find_uv() == str(uv)
