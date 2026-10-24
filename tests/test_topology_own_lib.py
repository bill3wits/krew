"""The gateway topology (crew/lib/hermes_topology.py and hermes_endpoint.py) comes only from krew's own crew/lib,
loaded by file path: a same-named module from another tree on sys.path (the reference install's lib/, when krew sits
inside its worktree) never stands in. Every caller is covered: the board gate, the pin check, the keeper,
render-collect, the cua doctor, the Janitor, kit reapply and sync-packs. When the lib is absent, each one says so.
hermes-tree's default patch queue is krew's own patches/; when there is no queue, it is named, never passed."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

CREW = ROOT / "crew"
LIB = CREW / "lib"
HOST = "hermes-gateway.service"
_SHARED = ("hermes_topology", "hermes_endpoint", "krew_own_hermes_topology", "krew_own_hermes_endpoint")


@pytest.fixture(autouse=True)
def _restore_modules():
    before = dict(sys.modules)
    yield
    for k in [k for k in sys.modules if k not in before]:
        del sys.modules[k]
    for k, v in before.items():
        if sys.modules.get(k) is not v:
            sys.modules[k] = v


@pytest.fixture
def host(tmp_path, monkeypatch):
    """One host gateway in a tmp Hermes home, and a decoy topology from another tree first on sys.path."""
    other = tmp_path / "other-lib"
    other.mkdir()
    (other / "hermes_topology.py").write_text(
        "HOST_UNIT = 'leak.service'\nHERMES_ROOT = None\n\n\ndef is_host():\n    return True\n\n\n"
        "def unit_for(p):\n    return 'leak.service'\n\n\ndef profile_of_unit(u):\n    return 'leak'\n")
    (other / "hermes_endpoint.py").write_text(
        "def health_url(p):\n    return 'http://leak/health'\n\n\ndef host_port():\n    return 1\n")
    monkeypatch.syspath_prepend(str(other))
    for m in _SHARED:
        monkeypatch.delitem(sys.modules, m, raising=False)
    root = tmp_path / ".hermes"
    (root / "profiles" / "zoey").mkdir(parents=True)
    monkeypatch.setenv("KREW_HERMES_ROOT", str(root))
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    monkeypatch.setenv("KREW_HERMES_HOST_PORT", "8660")
    return root


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _own(mod) -> bool:
    return Path(mod.__file__).resolve() == (LIB / f"{mod.__name__.removeprefix('krew_own_')}.py").resolve()


def _crew_without_topology(tmp_path: Path, *scripts: str) -> Path:
    """A crew/ copy with every lib but the two topology ones, and the named scripts."""
    crew = tmp_path / "bare" / "crew"
    shutil.copytree(LIB, crew / "lib", ignore=shutil.ignore_patterns("__pycache__", "hermes_topology.py",
                                                                      "hermes_endpoint.py"))
    (crew / "scripts").mkdir(parents=True)
    for s in scripts:
        shutil.copy2(CREW / "scripts" / s, crew / "scripts" / s)
    return crew


def test_krew_ships_both_libs_and_the_endpoint_reads_its_own_sibling(host):
    assert (LIB / "hermes_topology.py").is_file() and (LIB / "hermes_endpoint.py").is_file()
    ep = _load(LIB / "hermes_endpoint.py", "hermes_endpoint_t")
    assert Path(ep.topo.__file__).resolve() == (LIB / "hermes_topology.py").resolve()
    assert ep.topo.HOST_UNIT == HOST and ep.host_port() == 8660
    assert ep.health_url("zoey") == "http://127.0.0.1:8660/health"
    for f in ("hermes_topology.py", "hermes_endpoint.py"):
        src = (LIB / f).read_text()
        assert "\nimport hermes_topology" not in src and "sys.path.insert" not in src


def test_the_board_gate_and_the_pin_check_read_krews_copy(host):
    gate = _load(CREW / "scripts" / "kanban-board-gate.py", "kanban_board_gate_topo_t")
    t = gate._topology()
    assert t is not None and _own(t) and t.HOST_UNIT == HOST
    pc = _load(CREW / "scripts" / "krew-pin-check.py", "krew_pin_check_topo_t")
    t = pc._topology()
    assert t is not None and _own(t) and t.HOST_UNIT == HOST
    assert not any("hermes_topology" in m for m in pc.MISSING)


def test_the_keeper_reads_krews_copy(host, capsys, monkeypatch):
    monkeypatch.setenv("KREW_SERVICES", str(ROOT / "config" / "services.example.json"))   # the lanes (2026-10-08)
    k = _load(CREW / "scripts" / "krew-keeper.py", "krew_keeper_topo_t")
    assert _own(k._hermes_topology) and _own(k._hermes_endpoint)
    assert k.LANES["zoey-gw"] == ("http://127.0.0.1:8660/health", "user", HOST)
    assert "did not load" not in capsys.readouterr().err


def test_render_collect_unparks_through_krews_copy(host, monkeypatch):
    rc = _load(CREW / "scripts" / "render-collect.py", "render_collect_topo_t")
    asked = []

    def run(argv, **kw):
        asked.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    r = rc._start_unit("hermes-zoey-gateway.service")
    assert r.returncode == 0 and r.args == ["unpark", "zoey"]
    assert asked and asked[-1][-4:] == ["-p", "zoey", "gateway", "start"]


def test_the_cua_doctor_asks_krews_copy(host):
    sys.path.insert(0, str(LIB))
    cd = _load(LIB / "cua_doctor.py", "cua_doctor_topo_t")
    assert cd._gateway_unit() == HOST


def test_the_janitor_prefers_krews_copy_and_keeps_lib_dirs_as_the_fallback(host, tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    import janitor_env  # noqa: F401
    j = _load(ROOT / "janitor" / "krew-janitor.py", "krew_janitor_topo_t")
    t, e = j._gateway_libs()
    assert _own(t) and _own(e) and t.HOST_UNIT == HOST
    # a Janitor with no krew crew/ beside it (the reference install's own) reads `paths.lib_dirs` / sys.path
    alone = tmp_path / "alone" / "janitor"
    shutil.copytree(ROOT / "janitor", alone, ignore=shutil.ignore_patterns("__pycache__"))
    j2 = _load(alone / "krew-janitor.py", "krew_janitor_alone_t")
    t2, _ = j2._gateway_libs()
    assert t2.HOST_UNIT == "leak.service"


def test_absent_the_gate_names_it_only_when_host_is_asked_for(tmp_path, monkeypatch, capsys):
    crew = _crew_without_topology(tmp_path, "kanban-board-gate.py")
    gate = _load(crew / "scripts" / "kanban-board-gate.py", "kanban_board_gate_bare_t")
    monkeypatch.delenv("KREW_GATEWAY_TOPOLOGY", raising=False)
    assert gate._topology() is None and capsys.readouterr().err == ""
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    assert gate._topology() is None and gate._topology() is None
    assert capsys.readouterr().err.count("KREW_GATEWAY_TOPOLOGY=host but lib/hermes_topology did not load") == 1


def test_absent_no_other_tree_stands_in(host, tmp_path, capsys):
    crew = _crew_without_topology(tmp_path, "kanban-board-gate.py", "krew-pin-check.py", "krew-keeper.py",
                                  "render-collect.py")
    assert _load(crew / "scripts" / "kanban-board-gate.py", "kanban_board_gate_leak_t")._topology() is None
    pc = _load(crew / "scripts" / "krew-pin-check.py", "krew_pin_check_leak_t")
    assert pc._topology() is None and any("no lib/hermes_topology.py" in m for m in pc.MISSING)
    k = _load(crew / "scripts" / "krew-keeper.py", "krew_keeper_bare_t")
    assert "zoey-gw" not in k.LANES or "leak" not in str(k.LANES["zoey-gw"])
    rc = _load(crew / "scripts" / "render-collect.py", "render_collect_bare_t")
    assert rc.LANES if hasattr(rc, "LANES") else True
    err = capsys.readouterr().err
    assert "krew-keeper: KREW_GATEWAY_TOPOLOGY=host but lib/hermes_topology did not load" in err


def test_absent_render_collect_says_so_and_starts_the_unit(host, tmp_path, monkeypatch, capsys):
    crew = _crew_without_topology(tmp_path, "render-collect.py")
    rc = _load(crew / "scripts" / "render-collect.py", "render_collect_bare2_t")
    asked = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: asked.append(list(argv))
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    rc._start_unit("hermes-zoey-gateway.service")
    assert asked == [["systemctl", "--user", "start", "hermes-zoey-gateway.service"]]
    assert "hermes_topology.py is missing" in capsys.readouterr().err


@pytest.mark.parametrize("script, expr", [
    ("krew-kit-reapply.sh", 'topo="$REPO/lib/hermes_topology.py"'),
    ("sync-packs.sh", 'topo="$(dirname "$(readlink -f "$0")")/../lib/hermes_topology.py"'),
])
def test_the_shell_callers_use_krews_copy_and_name_it_missing(script, expr):
    src = (CREW / "scripts" / script).read_text()
    assert expr in src and 'if [ ! -f "$topo" ]' in src and "missing: $topo" in src
    assert "projects/" not in src.split(expr, 1)[1].split("fi\n", 1)[0]
    calls = [ln for ln in src.splitlines() if "hermes_topology.py" in ln and not ln.lstrip().startswith("#")]
    assert calls == [ln for ln in calls if "topo=" in ln], "every call goes through $topo"
    if script == "krew-kit-reapply.sh":
        assert 'REPO="$(cd "$(dirname "$0")/.." && pwd)"' in src      # crew/scripts/.. = krew's crew/
    r = subprocess.run(["bash", "-n", str(CREW / "scripts" / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _hermes_tree():
    sys.path.insert(0, str(LIB))
    return _load(CREW / "scripts" / "hermes-tree.py", "hermes_tree_topo_t")


def test_hermes_tree_queues_krews_own_patches(monkeypatch):
    monkeypatch.delenv("HERMES_PATCH_QUEUE", raising=False)
    ht = _hermes_tree()
    assert ht.QUEUE.resolve() == (ROOT / "patches").resolve() and any(ht.QUEUE.glob("*.patch"))


def test_hermes_tree_names_a_missing_queue_and_is_red(tmp_path, capsys):
    ht = _hermes_tree()
    tree = tmp_path / "hermes-agent"
    tree.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    import os
    for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "base"], ["update-ref", "refs/remotes/origin/main", "HEAD"]):
        subprocess.run(["git", *a], cwd=tree, check=True, env={**os.environ, **env}, capture_output=True)
    gone = tmp_path / "no-queue"
    s = ht.status(tree, gone)
    assert s["missing"] and str(gone) in s["missing"][0]
    assert ht.verify(tree, gone) == 1
    out = capsys.readouterr().out
    assert "MISSING: no patch queue at" in out and "VERIFY RED" in out and "missing=" in out
    q = tmp_path / "queue"
    q.mkdir()
    assert ht.status(tree, q)["missing"] == [] and ht.verify(tree, q) == 0
