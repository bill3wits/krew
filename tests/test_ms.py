"""M&S in krew: report only by default, empty without settings, discovery read-only and visible, nothing dropped
silently, and no reference-install value inside the code. Every run here is hermetic: a temp HOME and KREW_HOME,
a fake /proc (KREW_MS_PROC_DIR), PATH with no systemctl / nvidia-smi / apt, and no envs that would reach PyPI."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MS = ROOT / "ms"
WRAP = MS / "scripts" / "krew-ms.py"
sys.path.insert(0, str(MS / "lib"))
import ms_discover as D  # noqa: E402

OFF = "M&S upgrades are OFF"


def _home(tmp: Path) -> dict:
    home = tmp / "home"
    (home / ".krew").mkdir(parents=True, exist_ok=True)
    proc = tmp / "proc" / "net"
    proc.mkdir(parents=True, exist_ok=True)
    hdr = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (proc / "tcp").write_text(hdr)
    (proc / "tcp6").write_text(hdr)
    return {"HOME": str(home), "USER": "krewtest", "LANG": "C.UTF-8", "PATH": "/nonexistent",
            "KREW_HOME": str(home / ".krew"), "KREW_MS_PROC_DIR": str(tmp / "proc"),
            "HERMES_BIN": str(tmp / "no-hermes"), "PYTHONDONTWRITEBYTECODE": "1"}


def _run(env: dict, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WRAP), *args], env=env, capture_output=True, text=True,
                          timeout=timeout, cwd=env["HOME"])


def _ms_yaml(env: dict, text: str) -> None:
    Path(env["KREW_HOME"], "ms.yaml").write_text(text)


@pytest.fixture
def trail_ok():
    pytest.importorskip("packaging", reason="the trail needs `packaging` (the Hermes env has it)")


# ---- report only by default -----------------------------------------------------------------------------------
def test_where_is_under_krew_home_and_says_upgrades_are_off(tmp_path, trail_ok):
    env = _home(tmp_path)
    r = _run(env, "--where")
    assert r.returncode == 0, r.stderr
    assert OFF in r.stdout
    where = json.loads(r.stdout[r.stdout.index("{"):])
    kh = env["KREW_HOME"]
    assert where["state_dir"] == f"{kh}/ms" and where["ledger"] == f"{kh}/ms/decisions.json"
    assert where["settings_file"] == f"{kh}/ms.yaml"


def test_upgrade_flags_refuse_and_change_nothing(tmp_path, trail_ok):
    env = _home(tmp_path)
    for flag in (["--apply-green"], ["--apply-pending"], ["--force-heavy"], ["--service-check", "x"],
                 ["--rebuild", "x", "--step", "build"]):
        r = _run(env, "--no-discover", *flag)
        assert r.returncode == 2, (flag, r.stdout, r.stderr)
        assert OFF in r.stdout and "refused:" in r.stdout and "Nothing was changed" in r.stdout
    assert not list(Path(env["KREW_HOME"], "ms").glob("reports/ms-trail-*.json"))
    log = Path(env["KREW_HOME"], "ms", "krew-ms.log").read_text()
    assert OFF in log


def test_upgrades_true_drops_the_off_line(tmp_path, trail_ok):
    env = _home(tmp_path)
    r = _run({**env, "MS_UPGRADES": "1"}, "--where")
    assert r.returncode == 0 and OFF not in r.stdout
    _ms_yaml(env, "upgrades: true\n")
    r = _run(env, "--where")
    assert r.returncode == 0 and OFF not in r.stdout


def test_no_settings_means_empty_lists(tmp_path):
    env = _home(tmp_path)
    code = ("import json, ms_settings as S; print(json.dumps({k: S.get(k) for k in "
            "('envs','env_roots','retired_envs','canary','health','kanban','upgrades','notify')}))")
    r = subprocess.run([sys.executable, "-c", code], env={**env, "PYTHONPATH": str(MS / "lib")},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    assert s["envs"] == {} and s["env_roots"] == [] and s["retired_envs"] == []
    assert s["canary"] == {"enabled": False} and s["health"]["lanes"] == {}
    assert s["kanban"]["enabled"] is False and s["kanban"]["assignees"] == {}
    assert s["upgrades"] is False and s["notify"]["cmd"] == []


# ---- the report run: map, report, ledger, dropped ---------------------------------------------------------------
def test_report_run_writes_map_report_ledger_and_every_drop(tmp_path, trail_ok):
    env = _home(tmp_path)
    roots = tmp_path / "envs"
    for d in ("old-bak-1", "nopy", "gone"):
        (roots / d).mkdir(parents=True)
    (roots / "a-file").write_text("x")
    _ms_yaml(env, f"env_roots: [{roots}]\nretired_envs: [gone]\nos_lane: 'off'\n")
    r = _run(env, "--no-kanban")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    out = r.stdout
    assert OFF in out and "NOT SCANNED" in out and "DROPPED" in out
    assert "no NVIDIA GPU found" in out
    ms = Path(env["KREW_HOME"], "ms")
    rep = json.loads((ms / "reports" / "ms-trail-LATEST.json").read_text())
    assert rep["upgrades"] is False
    dropped = rep["dropped"]
    assert dropped[str(roots / "gone")] == "listed in retired_envs"
    assert "no bin/python" in dropped[str(roots / "nopy")]
    assert "backup" in dropped[str(roots / "old-bak-1")]
    led = json.loads((ms / "decisions.json").read_text())
    assert led["schema"] == "ms_decisions/v1" and led["decisions"] == []
    m = json.loads((ms / "discovery" / "map.json").read_text())
    assert m["schema"] == "krew_ms_map/v1" and m["baseline"] is True
    assert {d["item"] for d in m["trail_dropped"]} == set(dropped)
    whats = " ".join(n["what"] + " " + n["why"] for n in m["not_scanned"])
    assert "systemctl is not installed" in whats
    log = (ms / "krew-ms.log").read_text()
    assert "ledger started" in log and all(f"M&S dropped {k}" in log for k in dropped)
    r2 = _run(env, "--record-decision", "--env", "hermes", "--package", "pip", "--decision", "reviewed",
              "--reason", "test row")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    rows = json.loads((ms / "decisions.json").read_text())["decisions"]
    assert [(x["env"], x["package"], x["decision"]) for x in rows] == [("hermes", "pip", "reviewed")]


def test_discover_only_prints_the_off_line_and_the_map(tmp_path):
    env = _home(tmp_path)
    r = _run(env, "--discover-only")
    assert r.returncode == 0, r.stderr
    assert OFF in r.stdout and "First scan on this machine" in r.stdout
    assert Path(env["KREW_HOME"], "ms", "discovery", "map.md").is_file()
    assert _run(env, "--discover-only", "--no-discover").returncode == 2


# ---- discovery, with a fake machine ----------------------------------------------------------------------------
def _ctx(tmp: Path, **kw) -> D.Ctx:
    home = tmp / "h"
    home.mkdir(exist_ok=True)
    base = dict(home=home, uid=1000, user="me", env={"HOME": str(home)}, which=lambda n: None,
                proc=tmp / "proc", run=lambda a, t=0: (127, "", "no"), probe=lambda u, t: (0, ""))
    base.update(kw)
    return D.Ctx(**base)


def test_units_are_found_by_name_exec_and_user_dir(tmp_path):
    home = tmp_path / "h"
    files = "hermes-gw.service enabled\nfoo.service enabled\nbar.service enabled\nnight.timer enabled\nx@.service static\n"
    show = {
        "hermes-gw.service": "Id=hermes-gw.service\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled\n"
                             "ExecStart={ path=/opt/v/bin/python3 ; argv[]=x }\n",
        "foo.service": f"Id=foo.service\nLoadState=loaded\nExecStart={{ path={home}/bin/run ; }}\nTriggeredBy=night.timer\n",
        "bar.service": "Id=bar.service\nLoadState=loaded\nExecStart=/usr/bin/true\n",
        "night.timer": "Id=night.timer\nLoadState=loaded\nTriggers=foo.service\n"}

    def run(argv, t=0):
        if "list-unit-files" in argv:
            return (0, files, "") if "--user" in argv else (1, "", "Failed to connect to bus")
        names = [a for a in argv if a in show]
        return 0, "\n\n".join(show[n] for n in names) + "\n", ""
    ctx = _ctx(tmp_path, run=run, which=lambda n: "/bin/" + n if n == "systemctl" else None)
    f = D.Found()
    D.discover_units(ctx, D.settings(override={"units": {"prefixes": ["hermes-"]}}), f)
    got = {i["id"]: i for i in f.items}
    assert set(got) == {"user:hermes-gw.service", "user:foo.service", "user:night.timer"}
    assert got["user:hermes-gw.service"]["python"] == "/opt/v/bin/python3"
    assert got["user:foo.service"]["role"] == "job" and got["user:night.timer"]["why"] == "starts foo.service"
    assert any("system" in s["what"] and "Failed to connect" in s["why"] for s in f.not_scanned)
    assert any("x@.service" in d["item"] for d in f.dropped) is False   # a template nobody points at is not ours


def test_endpoints_find_served_models_and_name_the_ports_they_skip(tmp_path):
    net = tmp_path / "proc" / "net"
    net.mkdir(parents=True)
    hdr = "sl local rem st txrx trtm retr uid timeout inode\n"
    row = lambda port, uid, ino: f"0: 0100007F:{port:04X} 00000000:0000 0A 0:0 0:0 0 {uid} 0 {ino}\n"
    (net / "tcp").write_text(hdr + row(8000, 1000, "11") + row(9000, 1000, "12") + row(5432, 0, "13"))
    (net / "tcp6").write_text(hdr)

    def probe(url, t):
        if url.endswith(":8000/v1/models"):
            return 200, json.dumps({"data": [{"id": "my-model"}]})
        if url.endswith(":9000/health"):
            return 200, "ok"
        return 404, ""
    f = D.Found()
    D.discover_endpoints(_ctx(tmp_path, probe=probe), D.settings(), f)
    ep = {i["id"]: i for i in f.items if i["kind"] == "endpoint"}
    assert ep["127.0.0.1:8000"]["api"] == "openai" and ep["127.0.0.1:8000"]["models"] == ["my-model"]
    assert ep["127.0.0.1:9000"]["api"] == "health"
    assert any(i["id"] == "served:my-model@127.0.0.1:8000" for i in f.items)
    assert any("5432" in s["why"] for s in f.not_scanned), "a port it did not probe must be named"


def test_gpu_lines_are_plain(tmp_path):
    f = D.Found()
    D.discover_gpus(_ctx(tmp_path), {}, f)
    assert f.lines == ["GPUs: no NVIDIA GPU found (nvidia-smi is not installed). AMD and Apple GPUs are not checked yet."]
    f = D.Found()
    D.discover_gpus(_ctx(tmp_path, which=lambda n: "/x/nvidia-smi",
                         run=lambda a, t=0: (0, "0, Some GPU, 24576, 100, 555.1\n", "")), {}, f)
    assert f.items[0]["id"] == "nvidia:0" and "1 NVIDIA" in f.lines[0]


def test_hermes_profiles_and_missing_crew(tmp_path):
    hh = tmp_path / "h" / ".hermes"
    (hh / "profiles" / "crissa").mkdir(parents=True)
    (hh / "profiles" / "crissa" / "SOUL.md").write_text("x")
    (hh / "plugins" / "krew-board").mkdir(parents=True)
    (hh / "plugins" / "krew-board" / "plugin.yaml").write_text("name: krew-board\n")
    (hh / "plugins" / "stray").mkdir()
    (hh / "skills" / "a").mkdir(parents=True)
    (hh / "skills" / "a" / "SKILL.md").write_text("x")
    f = D.Found()
    D.discover_hermes(_ctx(tmp_path), {}, f)
    ids = {(i["kind"], i["id"]) for i in f.items}
    assert {("hermes_profile", "default"), ("hermes_profile", "crissa"), ("hermes_plugin", "krew-board"),
            ("hermes_skill", "a")} <= ids
    assert "crew profiles not made yet: lila" in f.lines[0]
    assert any(d["item"].endswith("stray") for d in f.dropped)


def test_first_scan_is_a_baseline_then_new_and_removed_are_noted(tmp_path):
    f1 = D.Found()
    f1.add("env", "a")
    f1.add("env", "b")
    reg, ch = D.merge({}, f1, by="t", now="2026-10-06T01:00:00")
    assert reg["baseline"] and ch == []
    f2 = D.Found()
    f2.add("env", "b")
    f2.add("env", "c")
    reg2, ch2 = D.merge(reg, f2, by="t", now="2026-10-06T02:00:00")
    assert {(c["change"], c["id"]) for c in ch2} == {("new", "c"), ("removed", "a")}
    text = D.render(reg2)
    assert "NEW env c" in text and "REMOVED env a" in text


def test_a_failing_step_is_not_scanned_not_a_crash(tmp_path, monkeypatch):
    def boom(ctx, cfg, f):
        raise RuntimeError("bad")
    monkeypatch.setattr(D, "STEPS", (("units", boom),))
    f = D.discover(_ctx(tmp_path), D.settings())
    assert f.not_scanned == [{"what": "units", "why": "discovery step failed: RuntimeError: bad"}]


# ---- the reference install stays in the example ------------------------------------------------------------------
def _reference_block() -> str:
    t = (ROOT / "config" / "ms.example.yaml").read_text()
    return t[t.index("# THE REFERENCE INSTALL"):]


def test_reference_values_are_not_in_ms_code():
    ref = _reference_block()
    units = set(re.findall(r"\b([\w.-]+\.(?:service|timer))\b", ref))
    urls = set(re.findall(r"https?://(?:127\.0\.0\.1|localhost):\d+", ref))
    m = re.search(r"^# envs:\n((?:#   .*\n)+)", ref, re.M)
    envs = set(re.findall(r"^#   ([\w.-]+):\s*$", m.group(1), re.M)) if m else set()
    assert units and urls and envs, "the reference block lost its lists"
    consts = []
    for p in sorted(MS.rglob("*.py")):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                consts.append((p.relative_to(ROOT), n.value))
    hits = [f"{p}: {u}" for p, c in consts for u in units | urls if u in c]
    # Not env choices: package names in the never-auto list, and the Hindsight memory service's settings section.
    allowed = {"hermes", "hindsight", "vllm", "unsloth"}
    hits += [f"{p}: env {c}" for p, c in consts for e in envs - allowed if c == e]
    assert not hits, "reference-install values in code (move them to ms.example.yaml):\n" + "\n".join(hits)


def test_hermes_env_pins_apply_is_an_upgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HOME", str(tmp_path))
    monkeypatch.delenv("MS_SETTINGS", raising=False)
    monkeypatch.delenv("MS_UPGRADES", raising=False)
    profile = os.environ.get("MS_PROFILE_FILE")   # the lifted tests load the reference profile (upgrades: true)
    monkeypatch.delenv("MS_PROFILE_FILE", raising=False)
    import importlib
    import ms_settings
    import hermes_env_pins as hp
    try:
        importlib.reload(ms_settings)
        importlib.reload(hp)
        assert hp.PINS_FILE == tmp_path / "hermes-env-pins.json"
        ran = []
        st = hp.apply([{"dist": "x", "min": "9"}], runner=lambda *a, **k: ran.append(a))
        assert "M&S upgrades are OFF" in st["refused"] and not st["applied"]
        assert not [a for a in ran if "install" in a[0]], "nothing installed while upgrades are off"
    finally:   # one shared module: hand it back as the other tests loaded it
        if profile:
            monkeypatch.setenv("MS_PROFILE_FILE", profile)
        importlib.reload(ms_settings)
    ex = json.loads((ROOT / "config" / "hermes-env-pins.example.json").read_text())
    assert ex["pins"] and all("card" not in r for r in ex["pins"])


def test_reexec_finds_the_hermes_venv_python_even_when_it_links_to_this_one(tmp_path, monkeypatch):
    """crissatest 06:29: the venv python is a symlink to the system python, so comparing resolved paths said
    'same interpreter' and the trail refused for want of `packaging`."""
    import importlib.util
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(os.path.realpath(sys.executable))
    hermes = tmp_path / "hermes"
    hermes.write_text(f"#!{venv / 'python'}\nimport sys\n")
    spec = importlib.util.spec_from_file_location("krew_ms_wrap", WRAP)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setitem(sys.modules, "packaging", None)          # this interpreter "lacks" packaging
    monkeypatch.setattr(m.sys, "executable", os.path.realpath(sys.executable))
    monkeypatch.setattr(m.subprocess if hasattr(m, "subprocess") else __import__("subprocess"), "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0))
    assert m._python_with_packaging(str(hermes)) == str(venv / "python")
