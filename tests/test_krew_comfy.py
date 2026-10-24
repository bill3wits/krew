"""One ComfyUI location for krew and the Crissa Wall (the User 2026-10-10).

The truth is the Wall's <ZOEYOS_HOME>/render.json (the render module's own settings file); krew reads it first, then
its own KREW_COMFY_* keys, then ~/ComfyUI (crew/lib/krew_comfy.py). The crew install writes krew's Comfy into
render.json when the Wall names none, so the Wall's render module reads the same folder. Every reader in krew asks the
one resolver: the mode switch, the studio, the WIP watcher, the orders, render-submit/still/collect, both plugins.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox as sb  # noqa: E402

ROOT = sb.ROOT
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import krew_comfy as kc  # noqa: E402

COPIES = ("crew/lib/krew_comfy.py", "plugins/krew-comfy/krew_comfy.py", "plugins/krew-render/krew_comfy.py")


def _wall(tmp_path, body: dict | None) -> dict:
    zh = tmp_path / "zoeyos"
    zh.mkdir(exist_ok=True)
    if body is not None:
        (zh / "render.json").write_text(json.dumps(body), encoding="utf-8")
    return {"ZOEYOS_HOME": str(zh)}


def test_the_copies_are_one_file():
    first = (ROOT / COPIES[0]).read_bytes()
    assert all((ROOT / c).read_bytes() == first for c in COPIES), COPIES


def test_no_wall_krews_key_then_the_default(tmp_path):
    e = _wall(tmp_path, None)
    assert kc.where("root", e) == (os.path.expanduser("~/ComfyUI"), "default")
    assert kc.where("url", e) == ("http://127.0.0.1:8188", "default")
    e["KREW_COMFY_ROOT"] = str(tmp_path / "mine")
    assert kc.where("root", e) == (str(tmp_path / "mine"), "KREW_COMFY_ROOT")
    assert kc.get("workflows", e) == str(tmp_path / "mine" / "workflows")
    assert kc.get("python", e) == str(tmp_path / "mine" / "venv" / "bin" / "python")
    e["KREW_COMFY_OUTPUT"] = str(tmp_path / "out")
    assert kc.where("output", e) == (str(tmp_path / "out"), "KREW_COMFY_OUTPUT")


def test_the_walls_render_json_wins_and_its_folders_follow_its_comfy_dir(tmp_path):
    e = dict(_wall(tmp_path, {"comfy_dir": str(tmp_path / "wall-comfy"), "comfy_url": "http://127.0.0.1:8190/",
                              "models": str(tmp_path / "big-disk")}),
             KREW_COMFY_ROOT=str(tmp_path / "mine"), KREW_COMFY_WORKFLOWS=str(tmp_path / "elsewhere"))
    assert kc.where("root", e) == (str(tmp_path / "wall-comfy"), "render.json")
    assert kc.get("url", e) == "http://127.0.0.1:8190"
    assert kc.get("models", e) == str(tmp_path / "big-disk")
    # a folder the file leaves out is <comfy_dir>/<folder>, the render module's own rule, never krew's key
    assert kc.where("workflows", e) == (str(tmp_path / "wall-comfy" / "workflows"), "render.json")
    assert kc.get("input", e) == str(tmp_path / "wall-comfy" / "input")
    # an empty comfy_dir (the Wall not configured yet) is no answer
    e2 = dict(_wall(tmp_path, {"comfy_dir": ""}), KREW_COMFY_ROOT=str(tmp_path / "mine"))
    assert kc.where("root", e2) == (str(tmp_path / "mine"), "KREW_COMFY_ROOT")
    assert kc.snapshot(e)["render_json"] == str(tmp_path / "zoeyos" / "render.json")


def test_zoeyos_render_config_names_another_file(tmp_path):
    f = tmp_path / "elsewhere.json"
    f.write_text(json.dumps({"comfy_dir": str(tmp_path / "c")}))
    assert kc.get("root", {"ZOEYOS_RENDER_CONFIG": str(f), "ZOEYOS_HOME": str(tmp_path / "none")}) == str(tmp_path / "c")


CRISSA = os.environ.get("CRISSA_TREE", "")


@pytest.mark.skipif(not (CRISSA and (Path(CRISSA) / "modules" / "render").is_dir()),
                    reason="set CRISSA_TREE to a Crissa pack tree to compare with its render module")
def test_krew_and_the_walls_render_module_read_the_same_folders(tmp_path, monkeypatch):
    e = _wall(tmp_path, {"comfy_dir": str(tmp_path / "wall-comfy"), "output": str(tmp_path / "renders")})
    spec = importlib.util.spec_from_file_location(
        "crissa_render_settings", Path(CRISSA) / "modules" / "render" / "zoeyos_render" / "settings.py",
        submodule_search_locations=[])
    monkeypatch.setenv("ZOEYOS_HOME", e["ZOEYOS_HOME"])
    for k in ("ZOEYOS_COMFY_DIR", "ZOEYOS_COMFY_URL", "ZOEYOS_RENDER_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    # the pack's settings.py puts the pack's lib on sys.path when it is loaded outside its package: undo all of it
    # (a pack module left in sys.modules or on the path shadows krew's own copies for every later test)
    monkeypatch.setattr(sys, "path", [str(Path(CRISSA) / "lib")] + list(sys.path))
    before = set(sys.modules)
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for name in set(sys.modules) - before:
            if str(getattr(sys.modules[name], "__file__", "") or "").startswith(str(Path(CRISSA))):
                del sys.modules[name]
    wall = mod.load(Path(e["ZOEYOS_HOME"]) / "render.json", env={})
    assert wall["comfy_dir"] == kc.get("root", e)
    for k in ("workflows", "models", "input", "output", "comfy_url"):
        assert wall[k] == kc.get("url" if k == "comfy_url" else k, e), k


def test_the_mode_switch_starts_the_walls_comfy(tmp_path, monkeypatch):
    zh = tmp_path / "zoeyos"
    zh.mkdir()
    (zh / "render.json").write_text(json.dumps({"comfy_dir": str(tmp_path / "wc"), "comfy_python": "/opt/py",
                                               "comfy_url": "http://127.0.0.1:8191"}))
    kh = tmp_path / "krew"
    kh.mkdir()
    hm = sb.load("km_comfy", "crew/scripts/krew-mode", ZOEYOS_HOME=str(zh), KREW_HOME=str(kh),
                 KREW_CONFIG=str(kh / "krew.yaml"), KREW_SERVICES=str(kh / "services.json"),
                 KREW_COMFY_ROOT=str(tmp_path / "not-this"), KREW_COMFY_MAIN="")
    assert hm.COMFY_MAIN == str(tmp_path / "wc" / "main.py") and hm.COMFY_URL == "http://127.0.0.1:8191"
    monkeypatch.setenv("ZOEYOS_HOME", str(zh))                # the launch reads it at start time, as the load did
    monkeypatch.delenv("KREW_COMFY_PYTHON", raising=False)
    assert hm._comfy("python") == "/opt/py" and hm._comfy("root") == str(tmp_path / "wc")


def test_beside_a_configured_wall_the_address_is_the_walls_comfy_port(tmp_path):
    """render.json names comfy_dir and no comfy_url: the Wall's render module listens on the Crissa table's comfy port
    (8184 + offset; ComfyUI's stock 8188 is the Wall's Pocket voice there), and krew asks the same one (2026-10-10)."""
    e = _wall(tmp_path, {"comfy_dir": str(tmp_path / "wc")})
    assert kc.where("url", e) == ("http://127.0.0.1:8184", "render.json")
    assert kc.get("url", dict(e, ZOEYOS_PORT_OFFSET="10000")) == "http://127.0.0.1:18184"
    assert kc.get("url", dict(e, ZOEYOS_PORT_COMFY="9100")) == "http://127.0.0.1:9100"
    assert kc.get("url", dict(e, ZOEYOS_COMFY_URL="http://127.0.0.1:7000/")) == "http://127.0.0.1:7000"
    assert kc.get("url", _wall(tmp_path, {"comfy_dir": str(tmp_path / "wc"), "comfy_url": "http://127.0.0.1:8191"})) \
        == "http://127.0.0.1:8191"


def test_the_studio_and_the_wip_watcher_read_the_walls_folders(tmp_path, monkeypatch):
    zh = tmp_path / "zoeyos"
    zh.mkdir()
    (zh / "render.json").write_text(json.dumps({"comfy_dir": str(tmp_path / "wc")}))
    monkeypatch.setenv("ZOEYOS_HOME", str(zh))
    for k in ("KREW_COMFY_ROOT", "KREW_COMFY_OUTPUT", "KREW_COMFY_WORKFLOWS", "KREW_COMFY_URL"):
        monkeypatch.delenv(k, raising=False)
    for rel, name in (("crew/lib/render_studio.py", "rs_comfy"), ("crew/lib/render_wip.py", "rw_comfy")):
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.OUT_DIR == tmp_path / "wc" / "output", rel
    assert m.COMFY == "http://127.0.0.1:8184"   # the Wall names comfy_dir and no comfy_url: its own ComfyUI port (2026-10-10)


def test_the_crew_install_gives_the_wall_krews_comfy_and_never_overwrites_its_own(tmp_path):
    spec = importlib.util.spec_from_file_location("kci_comfy", ROOT / "crew" / "scripts" / "krew-crew-install.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    zh = tmp_path / "zoeyos"
    zh.mkdir()
    comfy = tmp_path / "MyComfy"
    said = []
    # no Comfy anywhere: nothing written, it says so
    assert ci.comfy_location(zh, dry=False, say=said.append, env={"KREW_COMFY_ROOT": str(comfy)}) is None
    assert not (zh / "render.json").exists() and "no ComfyUI" in said[-1]
    comfy.mkdir()
    (comfy / "main.py").write_text("")
    out = ci.comfy_location(zh, dry=False, say=said.append, env={"KREW_COMFY_ROOT": str(comfy)})
    assert out == zh / "render.json" and json.loads(out.read_text()) == {"comfy_dir": str(comfy)}
    assert kc.get("root", {"ZOEYOS_HOME": str(zh)}) == str(comfy)
    # the Wall's own file is never touched
    (zh / "render.json").write_text(json.dumps({"comfy_dir": str(tmp_path / "theirs"), "highvram": True}))
    before = (zh / "render.json").read_text()
    assert ci.comfy_location(zh, dry=False, say=said.append, env={"KREW_COMFY_ROOT": str(comfy)}) is None
    assert (zh / "render.json").read_text() == before


def test_start_comfyui_asks_the_resolver(tmp_path):
    text = (ROOT / "crew" / "scripts" / "start-comfyui.sh").read_text()
    assert '"$HERE/../lib/krew_comfy.py" root' in text and '"$HERE/../lib/krew_comfy.py" url' in text
    zh = tmp_path / "zoeyos"
    zh.mkdir()
    (zh / "render.json").write_text(json.dumps({"comfy_dir": str(tmp_path / "wc")}))
    r = subprocess.run([sys.executable, str(ROOT / "crew" / "lib" / "krew_comfy.py"), "root"], capture_output=True,
                       text=True, env={**os.environ, "ZOEYOS_HOME": str(zh)}, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == str(tmp_path / "wc")


def test_no_reader_spells_the_old_inline_default():
    """Every krew reader goes through the resolver: no file but the resolver builds a Comfy path from KREW_COMFY_ROOT."""
    bad = []
    for p in list((ROOT / "crew").rglob("*.py")) + list((ROOT / "plugins").rglob("*.py")) + [ROOT / "crew" / "scripts" / "krew-mode"]:
        if p.name == "krew_comfy.py" or "__pycache__" in p.parts:
            continue
        if 'os.environ.get("KREW_COMFY_ROOT")' in p.read_text(encoding="utf-8", errors="replace"):
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, bad


# ---- the one GPU mode command (crew/lib/krew_mode_cmd.py) ---------------------------------------------------------
import krew_mode_cmd as kmc  # noqa: E402


def test_the_mode_command_is_one_rule(tmp_path):
    kh = tmp_path / "krew"
    shipped = str(ROOT / "crew" / "scripts" / "krew-mode")
    assert kmc.path({"KREW_HOME": str(kh)}) == shipped                         # nothing installed: the shipped switch
    (kh / "bin").mkdir(parents=True)
    (kh / "bin" / "krew-mode").symlink_to(shipped)
    assert kmc.path({"KREW_HOME": str(kh)}) == str(kh / "bin" / "krew-mode")   # the install's link
    assert kmc.command({"KREW_HOME": str(kh)}) == ["python3", str(kh / "bin" / "krew-mode")]
    assert kmc.path({"KREW_HOME": str(kh), "KREW_MODE_PATH": "/opt/mine"}) == "/opt/mine"
    assert kmc.command({"KREW_HOME": str(kh), "KREW_MODE_BIN": "python3 /stub mode"}) == ["python3", "/stub", "mode"]
    assert kmc.path({"KREW_HOME": str(kh), "KREW_MODE_BIN": "python3 /stub"}) == str(kh / "bin" / "krew-mode")


def test_submit_collect_and_the_hold_sweep_run_the_same_switch(tmp_path, monkeypatch):
    kh = tmp_path / "krew"
    (kh / "bin").mkdir(parents=True)
    (kh / "bin" / "krew-mode").symlink_to(ROOT / "crew" / "scripts" / "krew-mode")
    want = ["python3", str(kh / "bin" / "krew-mode")]
    over = {"KREW_HOME": str(kh), "KREW_MODE_BIN": "", "KREW_MODE_PATH": ""}
    for rel, name in (("crew/scripts/render-submit.py", "rsub_mode"), ("crew/scripts/render-collect.py", "rcol_mode")):
        m = sb.load(name, rel, **over)
        assert m.KREW_MODE_BIN == want, rel
    assert sb.load("rcol_mode2", "crew/scripts/render-collect.py", **over).KREW_MODE_PATH == want[1]
    hs = sb.load("hsweep_mode", "crew/scripts/krew-mode-hold-sweep", **over)
    monkeypatch.setenv("KREW_HOME", str(kh))
    monkeypatch.delenv("KREW_MODE_BIN", raising=False)
    monkeypatch.delenv("KREW_MODE_PATH", raising=False)
    assert hs.command() == want
    text = (ROOT / "crew" / "scripts" / "krew-mode-hold-sweep").read_text()
    assert "no GPU mode script" not in text and "krew_mode_cmd" in text


def test_a_failed_sweep_fails_the_unit_unless_told(tmp_path, monkeypatch):
    stub = tmp_path / "mode"
    stub.write_text("import sys; print('swept'); sys.exit(4)\n")
    monkeypatch.setenv("KREW_MODE_BIN", f"{sys.executable} {stub}")
    hs = sb.load("hsweep_rc", "crew/scripts/krew-mode-hold-sweep")
    monkeypatch.delenv("KREW_MODE_SWEEP_STRICT", raising=False)
    assert hs.main([]) == 4
    monkeypatch.setenv("KREW_MODE_SWEEP_STRICT", "0")
    assert hs.main([]) == 0
