"""Where Hermes lives, asked in one place (the owner, 2026-11-01): a krew installed beside another
Hermes (HERMES_HOME=~/.hermes-krew) read the other install's board, because krew files built `Path.home() / ".hermes"`
themselves. crew/lib/krew_hermes.py (and its shell half krew_hermes.sh) is the rule; this file holds every copy equal
to it, refuses a built ~/.hermes path anywhere else in what krew ships, and proves the rule, the runtime lookup and
the launcher on a stand-in Hermes."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import krew_hermes as KH  # noqa: E402

# (file, the literal, why) - every built ~/.hermes path left in krew, each with its reason
ALLOW = (
    ("scripts/krew_install_homes.py", "~/.hermes",
     "the account's OWN default home, which the install guard compares a --hermes-home against (is_scratch)"),
    ("scripts/krew_install_homes.py", ".hermes",
     "<checkout>/.hermes/bin/hermes: the Hermes checkout's own launcher folder, not the root"),
    ("crew/scripts/krew-config-restore.py", ".hermes",
     "<checkout>/.hermes/bin/hermes: the Hermes checkout's own launcher folder, not the root"),
    ("scripts/krew_install_rails.py", "~/.hermes",
     "the token seed_text REPLACES with this install's Hermes home when it writes a settings file"),
    ("crew/scripts/krew-mode", ".hermes",
     "its pytest guard names the account's live trees ($HOME's and the passwd home's) on purpose: no test writes there"),
    ("rails/why_before_fix.py", "/.hermes/",
     "recognises a Hermes environment BY PATH on any account (the M&S rows it replays name the reference install's "
     "paths); this install's own root is matched beside it"),
    ("rails/why_before_fix.py", "~/.hermes", "the same recognition for a path written with ~"),
    ("crew/lib/render_window_gate.py", ".hermes",
     "its tested passwd-vs-sandbox anchor, reached only when no KREW_HERMES / KREW_HERMES_ROOT / HERMES_HOME names the root"),
)
SHELL_ALLOW = (
    ("crew/scripts/kanban-env-clean", "a test sandbox it BUILDS ($sandbox/.hermes), never a home it reads"),
)
SKIP_DIRS = {".git", "__pycache__", "tests", "docs"}


def shipped(suffixes=(".py",)) -> list[Path]:
    out = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        out += [Path(d) / f for f in files if f.endswith(suffixes)]
    return sorted(out)


def python_files() -> list[Path]:
    """Every shipped .py, and every extensionless script whose first line runs python (krew-mode, krew-crew-run, ...)."""
    out = shipped()
    for p in shipped(("",)):
        if not p.suffix and p.is_file():
            try:
                first = p.read_text(encoding="utf-8").split("\n", 1)[0]
            except (UnicodeDecodeError, OSError):
                continue
            if first.startswith("#!") and "python" in first:
                out.append(p)
    return out


def built_literals(path: Path) -> list[tuple[int, str]]:
    """Python string literals that build a Hermes root path (docstrings and prose are not literals of a path)."""
    out = []
    for t in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if t.type != tokenize.STRING:
            continue
        s = t.string.lstrip("rbfRBF").strip("'\"")
        if s == ".hermes" or s.startswith(("~/.hermes", ".hermes/", "/.hermes/")):
            out.append((t.start[0], s))
    return out


def test_every_copy_of_the_helper_is_the_same_file():
    canon = (ROOT / "crew" / "lib" / "krew_hermes.py").read_bytes()
    copies = [p for p in shipped() if p.name == "krew_hermes.py"]
    assert len(copies) > 10
    differ = [str(p.relative_to(ROOT)) for p in copies if p.read_bytes() != canon]
    assert not differ, f"krew_hermes.py copies differ from crew/lib/krew_hermes.py: {differ}"


def test_no_shipped_python_builds_a_hermes_path_itself():
    allowed = {(f, lit) for f, lit, _why in ALLOW}
    bad = []
    for p in python_files():
        rel = p.relative_to(ROOT).as_posix()
        if p.name == "krew_hermes.py":
            continue
        for line, lit in built_literals(p):
            key = lit if lit in ("~/.hermes", ".hermes") else lit
            if (rel, key) not in allowed:
                bad.append(f"{rel}:{line}: {lit!r} - ask krew_hermes (hermes_home / kanban_dir / hermes_tree)")
    assert not bad, "\n".join(bad)


def test_every_allow_entry_is_still_needed():
    stale = [f"{f}: {lit!r}" for f, lit, _w in ALLOW if lit not in {x for _l, x in built_literals(ROOT / f)}]
    assert not stale, "stale allow entries:\n" + "\n".join(stale)


def test_no_shipped_shell_script_names_the_home_itself():
    allowed = {f for f, _why in SHELL_ALLOW}
    bad = []
    for p in shipped(("",)):
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        is_shell = rel.endswith(".sh") or text.startswith(("#!/bin/bash", "#!/usr/bin/env bash", "#!/bin/sh"))
        if not is_shell or rel in allowed or p.name == "krew_hermes.sh":
            continue
        for n, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            if "$HOME/.hermes" in code or "~/.hermes" in code or "home() / '.hermes'" in code \
                    or 'home() / ".hermes' in code:
                bad.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not bad, "source crew/lib/krew_hermes.sh and use $KREW_HERMES_HOME:\n" + "\n".join(bad)


def _clean(monkeypatch, home: Path):
    for k in ("KREW_HERMES_ROOT", "KREW_HERMES", "HERMES_REAL_HOME", "KREW_REAL_HOME", "HERMES_HOME",
              "HERMES_KANBAN_HOME", "HERMES_TREE", "KREW_HERMES_BIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))


def test_the_rule(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path)
    assert KH.hermes_home() == tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes-krew"))            # the migration krew's own home
    assert KH.hermes_home() == tmp_path / ".hermes-krew"
    assert KH.kanban_dir() == tmp_path / ".hermes-krew" / "kanban"
    assert KH.hermes_tree() == tmp_path / ".hermes-krew" / "hermes-agent"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes-krew" / "profiles" / "bash"))   # a profile's process
    assert KH.hermes_home() == tmp_path / ".hermes-krew"
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "board"))
    assert KH.kanban_dir() == tmp_path / "board" / "kanban"
    monkeypatch.setenv("HERMES_REAL_HOME", str(tmp_path / "login"))                # a test's login home wins
    assert KH.hermes_home() == tmp_path / "login" / ".hermes"
    monkeypatch.setenv("KREW_HERMES_ROOT", str(tmp_path / "named"))                # krew's own override wins all
    assert KH.hermes_home() == tmp_path / "named"
    monkeypatch.delenv("KREW_HERMES_ROOT")
    monkeypatch.setenv("KREW_HERMES", str(tmp_path / "tree"))                      # a file's own knob, not the rule's
    assert KH.hermes_home() == tmp_path / "login" / ".hermes"


def test_a_profiles_own_home_reads_the_root(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path / ".hermes-krew" / "profiles" / "vera" / "home")
    assert KH.hermes_home() == tmp_path / ".hermes-krew"


def test_another_environment_is_read_whole(tmp_path):
    env = {"HOME": str(tmp_path / "u"), "HERMES_HOME": "~/.hermes-krew"}
    assert KH.hermes_home(env=env) == tmp_path / "u" / ".hermes-krew"
    assert KH.hermes_home(env={"HOME": str(tmp_path / "u")}) == tmp_path / "u" / ".hermes"


def test_the_shell_half_says_the_same(tmp_path):
    cases = [{}, {"HERMES_HOME": str(tmp_path / ".hermes-krew")},
             {"HERMES_HOME": str(tmp_path / ".hermes-krew" / "profiles" / "ada")},
             {"KREW_HERMES_ROOT": str(tmp_path / "n")}, {"HERMES_REAL_HOME": str(tmp_path / "r")}]
    homes = [str(tmp_path), str(tmp_path / ".hermes-krew" / "profiles" / "lila" / "home")]
    for home in homes:
        for extra in cases:
            env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), **extra}
            r = subprocess.run(["bash", "-c", '. "$1"; printf "%s" "$KREW_HERMES_HOME"', "x",
                                str(ROOT / "crew" / "lib" / "krew_hermes.sh")], env=env, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            assert r.stdout == str(KH.hermes_home(env=env)), (home, extra)


def fake_launcher(tmp_path, *, answer: str | None, exec_py: str) -> Path:
    """A stand-in `hermes`: answers --print-runtime-command with `answer` (None = fails), else would exec `exec_py`."""
    p = tmp_path / "bin" / "hermes"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "#!/bin/sh\n"
    if answer is not None:
        body += f"if [ \"$1\" = --print-runtime-command ]; then echo '{answer}'; exit 0; fi\n"
    else:
        body += "if [ \"$1\" = --print-runtime-command ]; then exit 3; fi\n"
    body += f"exec {exec_py} -I -c 'import sys' \"$@\"\n"
    p.write_text(body)
    p.chmod(0o755)
    return p


def test_runtime_python_asks_the_launcher_first(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path)
    hermes = fake_launcher(tmp_path, answer=json.dumps([sys.executable, "-I", "-c", "x"]), exec_py="/nonexistent/py")
    assert KH.runtime_python(str(hermes)) == sys.executable


def test_runtime_python_falls_back_to_the_exec_line(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path)
    hermes = fake_launcher(tmp_path, answer=None, exec_py=sys.executable)
    assert KH.runtime_python(str(hermes)) == sys.executable


def test_runtime_python_never_guesses(tmp_path, monkeypatch):
    _clean(monkeypatch, tmp_path)
    hermes = fake_launcher(tmp_path, answer=None, exec_py=str(tmp_path / "no" / "python3"))
    with pytest.raises(RuntimeError, match="no Hermes runtime python"):
        KH.runtime_python(str(hermes))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="no `hermes` command"):
        KH.runtime_python()


def _crew_run(tmp_path, script_body: str, extra_env: dict) -> subprocess.CompletedProcess:
    kh = tmp_path / "krew"
    (kh / "crew").mkdir(parents=True)
    script = tmp_path / "probe.py"
    script.write_text(script_body)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KREW_", "HERMES_"))}
    env.update(HOME=str(tmp_path), KREW_HOME=str(kh), KREW_CREW_CONFIG=str(tmp_path / "none.yaml"), **extra_env)
    return subprocess.run([sys.executable, str(ROOT / "crew" / "scripts" / "krew-crew-run"), str(script), "a1"],
                          env=env, capture_output=True, text=True, timeout=60)


def test_a_hermes_python_script_runs_on_the_runtime_the_launcher_names(tmp_path):
    tree = tmp_path / ".hermes-krew" / "hermes-agent"
    tree.mkdir(parents=True)
    (tree / "hermes_bootstrap.py").write_text("import os\nos.environ['BOOTED'] = 'yes'\n")
    hermes = fake_launcher(tmp_path, answer=json.dumps([sys.executable, "-I", "-c", "x"]), exec_py="/nonexistent/py")
    r = _crew_run(tmp_path, "# krew: hermes-python\nimport os, sys\nprint(os.environ.get('BOOTED'), sys.argv[1:], __file__)\n",
                  {"KREW_HERMES_BIN": str(hermes), "HERMES_HOME": str(tmp_path / ".hermes-krew")})
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == "yes" and "['a1']" in r.stdout and "probe.py" in r.stdout


def test_a_hermes_python_script_without_a_runtime_is_a_clear_error(tmp_path):
    hermes = fake_launcher(tmp_path, answer=None, exec_py=str(tmp_path / "no" / "python3"))
    r = _crew_run(tmp_path, "# krew: hermes-python\nprint('ran')\n", {"KREW_HERMES_BIN": str(hermes)})
    assert r.returncode == 2 and "needs Hermes's runtime" in r.stderr and "ran" not in r.stdout


def test_an_explicit_hermes_python_still_wins(tmp_path):
    r = _crew_run(tmp_path, "# krew: hermes-python\nimport sys\nprint(sys.executable)\n",
                  {"KREW_HERMES_PYTHON": sys.executable, "KREW_HERMES_BIN": "/nonexistent/hermes"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == sys.executable


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_loop_reads_the_board_of_its_own_home(tmp_path, monkeypatch):
    """The 2026-11-01 finding: kanban-loop beside another Hermes read ~/.hermes's board."""
    _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes-krew"))
    loop = _load("crew/scripts/kanban-loop.py", "krewtest_loop_home")
    assert loop.BOARDS == tmp_path / ".hermes-krew" / "kanban" / "boards"
    assert loop.CURRENT == tmp_path / ".hermes-krew" / "kanban" / "current"


def test_installed_units_and_settings_name_the_installs_own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    inst = _load("crew/scripts/krew-crew-install.py", "krewtest_crew_install_home")
    unit = ROOT / "crew" / "units" / "krew-kanban-loop.service"
    text = inst.unit_text(unit, tmp_path / ".krew", tmp_path / ".hermes-krew")
    assert "%h/.hermes-krew/" in text and "%h/.hermes/" not in text
    assert inst.unit_text(unit, tmp_path / ".krew", tmp_path / ".hermes") == unit.read_text()
    sys.path.insert(0, str(ROOT / "scripts"))
    import krew_install_rails as R
    seeded = R.seed_text("a: ~/.hermes/gpu-mode\nb: [~/.krew, ~/.hermes]\n", tmp_path / ".krew", tmp_path / ".hermes-krew")
    assert seeded == "a: ~/.hermes-krew/gpu-mode\nb: [~/.krew, ~/.hermes-krew]\n"
