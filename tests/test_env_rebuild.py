"""M&S roots, Stage D: an app env moves by rebuild, never by in-place bumps.

Everything here runs on tmp dirs: no reference env root, no uv, no Comfy, no GPU.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
sys.path.insert(0, str(ROOT / "ms" / "lib"))
import env_rebuild as rb  # noqa: E402

ENVM = {"implementation_name": "cpython", "implementation_version": "3.12.13", "os_name": "posix",
        "platform_machine": "x86_64", "platform_release": "7", "platform_system": "Linux",
        "platform_version": "", "python_full_version": "3.12.13",
        "platform_python_implementation": "CPython", "python_version": "3.12", "sys_platform": "linux"}


def D(name, version, requires=(), tops=()):
    return {"name": name, "version": version, "requires": list(requires), "tops": list(tops) or [name],
            "size": 1, "installed": 1.0, "installer": "uv", "requested": True, "editable": "", "ep_groups": []}


@pytest.fixture
def world(tmp_path, monkeypatch):
    envs = tmp_path / "envs"
    old = envs / "comfyui"
    (old / "bin").mkdir(parents=True)
    (old / "bin" / "python").write_text("")
    base = tmp_path / "uvpy" / "bin"
    base.mkdir(parents=True)
    (base / "python3.12").write_text("")
    (old / "pyvenv.cfg").write_text(f"home = {base}\nversion_info = 3.12.13\n")
    app = tmp_path / "comfy"
    (app / "custom_nodes" / "ComfyUI-Manager").mkdir(parents=True)
    (app / "workflows").mkdir()
    (app / "workflows" / "flux.json").write_text(json.dumps(
        {"1": {"class_type": "UnetLoaderGGUF", "inputs": {}}, "2": {"class_type": "KSampler", "inputs": {"seed": 42}}}))
    (app / "workflows" / "apple_split_i2v.json").write_text(json.dumps({"nodes": [{"type": "ClownSampler_Beta"}]}))
    (app / "workflows" / "flux.json.bak-t_1").write_text("{}")
    req = app / "requirements.txt"
    req.write_text("comfyui-workflow-templates==0.11.69\ntorch\ntransformers>=4.50.3\n")
    hold = tmp_path / "ms-env-hold.json"
    hold.write_text(json.dumps({"comfyui": {"reason": "rebuild"}, "other": {"reason": "x"}}))
    gpu = tmp_path / "gpu-mode"
    gpu.write_text("normal\n")
    monkeypatch.setattr(rb, "STATE_ROOT", tmp_path / "rebuilds")
    monkeypatch.setattr(rb, "ENVS_ROOT", envs)
    monkeypatch.setattr(rb, "HOLD_FILE", hold)
    # every flip/rollback writes the hold log: never the live one (a first run wrote 10 lines there, 09-29)
    monkeypatch.setattr(rb, "HOLD_LOG", tmp_path / "env-hold.jsonl")
    monkeypatch.setattr(rb, "GPU_MODE_FILE", gpu)
    monkeypatch.setattr(rb, "UV", "uv")
    monkeypatch.setenv("MS_BASELINE", "0")   # first_start measures the machine; a test never probes or files
    cfg = {"files": [str(req)], "entries": [], "entry_point_groups": {}, "data_wheels": [], "clis": [], "file_roots": [],
           "rebuild": {"app_dir": str(app), "extra_index_urls": ["https://download.pytorch.org/whl/cu130"],
                       "exclude": ["comfy-cli", "matrix-nio"], "custom_nodes": ["ComfyUI-GGUF"],
                       "workflows_dir": str(app / "workflows"), "skip_workflows": ["apple_split_i2v.json"],
                       "retire_paths": [str(app / "custom_nodes" / "ComfyUI-Manager")], "keep_days": 14}}
    old_dump = {"markers": ENVM, "imports": {"cv2": ["x.py"]}, "scanned_files": 1, "dists": {
        "comfyui-workflow-templates": D("comfyui-workflow-templates", "0.11.69"),
        "torch": D("torch", "2.12.0+cu130", ["triton==3.7.0"]), "triton": D("triton", "3.7.0"),
        "transformers": D("transformers", "5.10.2"), "opencv-python": D("opencv-python", "4.12", tops=["cv2"]),
        "comfy-cli": D("comfy-cli", "1.20.0"), "pip": D("pip", "26")}}
    return {"tmp": tmp_path, "envs": envs, "old": old, "app": app, "cfg": cfg, "hold": hold, "gpu": gpu,
            "old_dump": old_dump}


class Runner:
    def __init__(self, rc=0, on=None):
        self.calls, self.rc, self.on = [], rc, on or {}

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        for key, fn in self.on.items():
            if key in cmd:
                fn(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, stdout="", stderr="boom" if self.rc else "")


def _lock_writer(cmd):
    Path(cmd[cmd.index("-o") + 1]).write_text("torch==2.12.0+cu130\ntriton==3.7.0\n    # via torch\n")


def _plan(w, run=None):
    run = run or Runner(on={"compile": _lock_writer})
    return rb.plan("comfyui", "v0.37.4", w["cfg"], old_py=str(w["old"] / "bin" / "python"),
                   never={"torch", "triton", "transformers"}, never_prefixes=("nvidia-",),
                   run=run, probe=lambda py, entries: w["old_dump"]), run


def test_plan_resolves_fresh_from_roots_and_carries_the_never_family(world):
    st, run = _plan(world)
    sd = rb.state_dir("comfyui", "v0.37.4")
    roots_in = (sd / "roots.in").read_text()
    assert "comfyui-workflow-templates==0.11.69  # requirements.txt" in roots_in
    assert "opencv-python  # import" in roots_in and "comfy-cli" not in roots_in
    assert (sd / "constraints.txt").read_text().split() == ["torch==2.12.0+cu130", "transformers==5.10.2",
                                                            "triton==3.7.0"]
    cmd = run.calls[0]
    # constraints, never overrides: an override drops the requirement's extras (torch's cuda-toolkit[...])
    assert cmd[:3] == ["uv", "pip", "compile"] and "--constraint" in cmd and "--override" not in cmd
    assert cmd[cmd.index("--extra-index-url") + 1] == "https://download.pytorch.org/whl/cu130"
    assert "unsafe-best-match" in cmd
    assert st["steps"]["plan"]["ok"] and st["steps"]["plan"]["locked"] == 2


def test_a_failed_compile_is_refused_and_recorded(world):
    with pytest.raises(rb.Refused):
        _plan(world, Runner(rc=1))
    assert rb.load_state("comfyui", "v0.37.4")["steps"]["plan"]["ok"] is False


def test_build_makes_a_staged_env_beside_the_old_one(world):
    _plan(world)
    new = world["envs"] / "comfyui-v0.37.4"
    run = Runner(on={"venv": lambda c: (new / "bin").mkdir(parents=True)})
    st = rb.build("comfyui", "v0.37.4", world["cfg"], run=run, scope=[])
    assert run.calls[0][:3] == ["uv", "venv", str(new)]
    assert run.calls[0][-1].endswith("python3.12")  # the old env's own CPython
    assert run.calls[1][:3] == ["uv", "pip", "sync"]
    m = rb.read_marker(new)
    assert m["of"] == "comfyui" and m["role"] == "staged" and m["built"]
    assert st["steps"]["build"]["ok"]
    assert rb.discovery_skip(new).startswith("staged rebuild of comfyui")
    assert rb.discovery_skip(world["old"]) == ""  # the live env carries no marker


def test_build_never_touches_a_dir_it_did_not_stage(world):
    _plan(world)
    (world["envs"] / "comfyui-v0.37.4").mkdir()
    with pytest.raises(rb.Refused, match="not this rebuild"):
        rb.build("comfyui", "v0.37.4", world["cfg"], run=Runner(), scope=[])


def test_build_needs_a_plan(world):
    with pytest.raises(rb.Refused, match="plan first"):
        rb.build("comfyui", "v0.37.4", world["cfg"], run=Runner(), scope=[])


def _built(world):
    _plan(world)
    new = world["envs"] / "comfyui-v0.37.4"
    rb.build("comfyui", "v0.37.4", world["cfg"], scope=[],
             run=Runner(on={"venv": lambda c: (new / "bin").mkdir(parents=True)}))
    return new


def _new_dump(world, **extra):
    d = {k: v for k, v in world["old_dump"]["dists"].items() if k != "comfy-cli"}
    d.update(extra)
    return {"markers": ENVM, "imports": {}, "scanned_files": 1, "dists": d}


def test_check_passes_on_a_clean_env(world):
    _built(world)
    nodes = {"nodes": ["KSampler", "UnetLoaderGGUF", "X"], "err": "", "import_failed": []}
    st = rb.check("comfyui", "v0.37.4", world["cfg"], run=Runner(), probe=lambda py, e: _new_dump(world),
                  node_set=lambda py, rbc, port: nodes)
    c = st["steps"]["check"]
    assert c["verdict"] == "pass", c["problems"]
    assert c["workflows"] == ["flux.json"]  # the UI graph, the .bak and the skipped one are not workflows


def test_check_fails_on_each_kind_of_problem(world):
    _built(world)
    bad = _new_dump(world, **{"comfy-cli": D("comfy-cli", "1.21"),
                              "comfyui-workflow-templates": D("comfyui-workflow-templates", "0.11.70")})
    st = rb.check("comfyui", "v0.37.4", world["cfg"], run=Runner(rc=1), probe=lambda py, e: bad,
                  node_set=lambda py, rbc, port: {"nodes": ["KSampler"] if "v0.37.4" in py else ["KSampler", "Old"],
                                                  "err": "", "import_failed": ["IMPORT FAILED: GGUF"]})
    p = " | ".join(st["steps"]["check"]["problems"])
    assert st["steps"]["check"]["verdict"] == "fail"
    for word in ("uv pip check", "root-pin drift", "excluded present", "UnetLoaderGGUF",
                 "custom node import failed", "nodes the old env had"):
        assert word in p, word


def test_serve_only_in_a_render_window_and_never_beside_another_comfy(world, monkeypatch):
    _built(world)
    with pytest.raises(rb.Refused, match="Render window"):
        rb.serve("comfyui", "v0.37.4", world["cfg"], which="new", popen=None)
    world["gpu"].write_text("render turbo\n")
    monkeypatch.setattr(rb, "comfy_answers", lambda port: True)
    with pytest.raises(rb.Refused, match="already answers"):
        rb.serve("comfyui", "v0.37.4", world["cfg"], which="new", popen=None)


def _served_cmd(world, monkeypatch):
    _built(world)
    world["gpu"].write_text("render turbo\n")
    ups = iter([False, False, True, True])
    monkeypatch.setattr(rb, "comfy_answers", lambda port: next(ups, True))
    seen = {}

    class P:
        pid, returncode = 4242, None

        def poll(self):
            return None

    def popen(cmd, **kw):
        seen["cmd"] = cmd
        return P()
    rb.serve("comfyui", "v0.37.4", world["cfg"], which="new", popen=popen, scope=[])
    return seen["cmd"]


def test_serve_loads_only_the_declared_nodes_so_no_manager_can_install(world, monkeypatch):
    world["cfg"]["rebuild"]["custom_nodes"] = ["ComfyUI-GGUF", "websocket_image_save.py"]
    cmd = _served_cmd(world, monkeypatch)
    i = cmd.index("--whitelist-custom-nodes")
    assert "--disable-all-custom-nodes" in cmd
    assert cmd[i + 1:i + 3] == ["ComfyUI-GGUF", "websocket_image_save.py"]
    assert not any("manager" in c.lower() for c in cmd)


def test_serve_refuses_a_whitelisted_manager_and_the_kill_restores_old(world, monkeypatch):
    world["cfg"]["rebuild"]["custom_nodes"] = ["ComfyUI-Manager"]
    with pytest.raises(rb.Refused, match="manager"):
        _served_cmd(world, monkeypatch)
    monkeypatch.setenv("MS_REBUILD_SERVE_WHITELIST", "0")
    assert "--disable-all-custom-nodes" not in _served_cmd(world, monkeypatch)


def test_live_comfyui_rebuild_never_whitelists_the_manager():
    import json
    from pathlib import Path
    cfg = json.loads(ms_defaults_only.ROOTS_FILE.read_text())
    rbk = cfg["envs"]["comfyui"]["rebuild"] if "envs" in cfg else cfg["comfyui"]["rebuild"]
    args = rb.serve_node_args(rbk)
    assert args[0] == "--disable-all-custom-nodes" and "ComfyUI-GGUF" in args


def test_stop_old_uses_the_house_stop_in_a_render_window_only(world):
    _built(world)
    with pytest.raises(rb.Refused, match="Render window"):
        rb.stop_machine("comfyui", "v0.37.4", stopper=lambda: True)
    world["gpu"].write_text("render\n")
    st = rb.stop_machine("comfyui", "v0.37.4", stopper=lambda: True)
    assert st["steps"]["stops"][-1]["app_comfy_down"] is True
    with pytest.raises(rb.Refused, match="still answering"):
        rb.stop_machine("comfyui", "v0.37.4", stopper=lambda: False)


def test_the_machine_stop_is_the_gpu_mode_scripts_own(tmp_path, monkeypatch):
    """krew: the GPU mode script is configured (MS_MODE_BIN, default $KREW_HOME/bin/gpu-mode) and must define
    stop_comfy(); with none configured the stop refuses, it never guesses."""
    src = (ROOT / "ms" / "lib" / "env_rebuild.py").read_text()
    assert "hm.stop_comfy()" in src and "MS_MODE_BIN" in src
    monkeypatch.setattr(rb, "MODE_BIN", tmp_path / "missing-gpu-mode")
    with pytest.raises(rb.Refused, match="MS_MODE_BIN"):
        rb.stop_machine_comfy()
    script = tmp_path / "gpu-mode"
    script.write_text("def stop_comfy() -> bool:\n    return True\n")
    monkeypatch.setattr(rb, "MODE_BIN", script)
    assert rb.stop_machine_comfy() is True


def test_renders_submit_house_style_and_fetch_outputs(world):
    _built(world)
    posted = []
    client = {"post": lambda path, body: posted.append(body) or {"prompt_id": "p1"},
              "get": lambda path: {"p1": {"status": {"completed": True},
                                          "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "",
                                                                        "type": "output"}]}}}},
              "raw": lambda path: b"PNG"}
    st = rb.renders("comfyui", "v0.37.4", world["cfg"], label="old", client=client)
    r = st["steps"]["renders"]["old"]["results"]["flux.json"]
    assert r["ok"] and Path(r["files"][0]).read_bytes() == b"PNG"
    assert posted[0]["client_id"] == "krew-wip" and posted[0]["prompt"]["2"]["inputs"]["seed"] == 42


def test_a_failed_render_is_recorded_not_raised(world):
    _built(world)
    client = {"post": lambda p, b: {"prompt_id": "p1"},
              "get": lambda p: {"p1": {"status": {"completed": False, "status_str": "error"}, "outputs": {}}},
              "raw": lambda p: b""}
    st = rb.renders("comfyui", "v0.37.4", world["cfg"], label="new", client=client)
    assert st["steps"]["renders"]["new"]["results"]["flux.json"]["ok"] is False


def _renders(world, old_bytes=b"A", new_bytes=b"A"):
    for label, data in (("old", old_bytes), ("new", new_bytes)):
        client = {"post": lambda p, b: {"prompt_id": "p1"},
                  "get": lambda p: {"p1": {"status": {"completed": True},
                                           "outputs": {"9": {"images": [{"filename": "a.png"}]}}}},
                  "raw": lambda p, d=data: d}
        rb.renders("comfyui", "v0.37.4", world["cfg"], label=label, client=client)


def test_compare_pass_review_fail(world):
    import numpy as np
    _built(world)
    _renders(world)
    same = lambda p: [np.zeros((4, 4, 3), dtype=np.float32)]
    assert rb.compare("comfyui", "v0.37.4", frames=same)["steps"]["compare"]["verdict"] == "pass"
    diff = lambda p: [np.zeros((4, 4, 3), np.float32) if "old" in str(p) else np.full((4, 4, 3), 200, np.float32)]
    c = rb.compare("comfyui", "v0.37.4", frames=diff)["steps"]["compare"]
    assert c["verdict"] == "review" and c["review"] == ["flux.json"]


def test_psnr():
    import numpy as np
    a = np.zeros((2, 2, 3), np.float32)
    assert rb.psnr(a, a) == 99.0 and rb.psnr(a, np.zeros((3, 2, 3), np.float32)) == 0.0
    assert 20 < rb.psnr(a, a + 10) < 30


def _ready(world):
    import numpy as np
    new = _built(world)
    rb.check("comfyui", "v0.37.4", world["cfg"], run=Runner(), probe=lambda py, e: _new_dump(world),
             node_set=lambda py, rbc, port: {"nodes": ["KSampler", "UnetLoaderGGUF"], "err": "", "import_failed": []})
    _renders(world)
    rb.compare("comfyui", "v0.37.4", frames=lambda p: [np.zeros((2, 2, 3), np.float32)])
    return new


def test_flip_refuses_without_every_proof(world):
    _built(world)
    with pytest.raises(rb.Refused, match="--yes"):
        rb.flip("comfyui", "v0.37.4", world["cfg"], yes=False, app_running=lambda: False)
    with pytest.raises(rb.Refused, match="check"):
        rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: False)


def test_flip_refuses_while_the_app_runs(world):
    _ready(world)
    with pytest.raises(rb.Refused, match="running"):
        rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: True)


def test_flip_then_rollback_round_trip(world):
    new = _ready(world)
    old, envs, app = world["old"], world["envs"], world["app"]
    (old / "keep.txt").write_text("old env file")
    st = rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: False,
                 today=dt.date(2026, 9, 29))
    live = envs / "comfyui"
    assert live.is_symlink() and live.resolve() == new.resolve()
    prev = envs / "comfyui-inplace-20260929"
    assert (prev / "keep.txt").read_text() == "old env file"
    assert rb.read_marker(prev)["role"] == "retired" and rb.read_marker(prev)["keep_until"] == "2026-10-13"
    assert rb.read_marker(new)["role"] == "live"
    assert (app / "custom_nodes" / "ComfyUI-Manager.disabled").is_dir()
    assert json.loads(world["hold"].read_text()) == {"other": {"reason": "x"}}
    rows = {r["dir"]: r for r in rb.rebuild_rows(envs, today=dt.date(2026, 10, 14))}
    assert rows["comfyui-inplace-20260929"]["due"] is True and "comfyui" not in rows  # the symlink is not a row
    with pytest.raises(rb.Refused, match="already flipped"):
        rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: False)

    rb.rollback("comfyui", "v0.37.4", yes=True, app_running=lambda: False)
    assert live.is_dir() and not live.is_symlink() and (live / "keep.txt").exists()
    assert not prev.exists() and rb.read_marker(live) is None  # the original env gets no marker back
    assert rb.read_marker(new)["role"] == "staged"
    assert (app / "custom_nodes" / "ComfyUI-Manager").is_dir()
    assert json.loads(world["hold"].read_text())["comfyui"] == {"reason": "rebuild"}


def test_a_second_flip_swaps_the_symlink_atomically(world):
    new = _ready(world)
    rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: False, today=dt.date(2026, 9, 29))
    nxt = world["envs"] / "comfyui-v0.38.0"
    (nxt / "bin").mkdir(parents=True)
    rb.write_marker(nxt, of="comfyui", version="v0.38.0", role="staged")
    st = rb.load_state("comfyui", "v0.38.0")
    st["steps"] = {"check": {"verdict": "pass"}, "compare": {"verdict": "review"}}
    st["old_dir"] = str(new)
    rb.save_state(st)
    with pytest.raises(rb.Refused, match="accept-review"):
        rb.flip("comfyui", "v0.38.0", world["cfg"], yes=True, app_running=lambda: False)
    rb.flip("comfyui", "v0.38.0", world["cfg"], yes=True, accept_review=True, app_running=lambda: False)
    live = world["envs"] / "comfyui"
    assert live.resolve() == nxt.resolve() and rb.read_marker(new)["role"] == "retired"
    rb.rollback("comfyui", "v0.38.0", yes=True, app_running=lambda: False)
    assert live.resolve() == new.resolve() and rb.read_marker(new)["role"] == "live"


def test_no_test_writes_the_live_hold_log(world):
    assert str(rb.HOLD_LOG).startswith(str(world["tmp"]))


def test_status_needs_no_switch(world):
    s = rb.status("comfyui", "v0.37.4")
    assert s["id"] == "comfyui-v0.37.4" and s["exists"] is False


# ------------------------------------------------------------- the trail
def _trail():
    spec = importlib.util.spec_from_file_location("ms_trail_rebuild", ROOT / "ms" / "scripts" / "ms-trail-run.py")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    return ms


def test_discovery_skips_a_staged_or_retired_env(tmp_path):
    ms = _trail()
    d = tmp_path / "comfyui-v0.37.4"
    d.mkdir()
    assert ms._rebuild_marker_skip(d) == ""
    (d / ".ms-rebuild.json").write_text(json.dumps({"of": "comfyui", "version": "v0.37.4", "role": "staged"}))
    assert ms._rebuild_marker_skip(d) == "staged rebuild of comfyui (v0.37.4)"
    (d / ".ms-rebuild.json").write_text(json.dumps({"of": "comfyui", "role": "live"}))
    assert ms._rebuild_marker_skip(d) == ""  # the live build is reached through the env's symlink
    src = (ROOT / "ms" / "scripts" / "ms-trail-run.py").read_text()
    i = src.index("def discover_venvs")
    assert src.index("_rebuild_marker_skip(d)", i) < src.index('py = d / "bin" / "python"', i)


def test_the_verb_is_dark_by_default(monkeypatch, capsys):
    ms = _trail()
    monkeypatch.setattr(rb, "ENABLED", False)
    args = type("A", (), {"rebuild": "comfyui", "to": "v0.37.4", "step": "build"})()
    assert ms.rebuild_verb(args) == 2
    assert "dark" in capsys.readouterr().out
    args.to = ""
    assert ms.rebuild_verb(args) == 2


def test_the_comfyui_rebuild_block_carries_the_users_rules():
    cfg = json.loads((ms_defaults_only.ROOTS_FILE).read_text())["envs"]["comfyui"]["rebuild"]
    assert {"comfy-cli", "matrix-nio", "pygithub", "comfyui-manager"} <= set(cfg["exclude"])
    assert cfg["skip_workflows"] == ["apple_split_i2v.json"]
    assert "ComfyUI-Manager" not in " ".join(cfg["custom_nodes"])
    assert cfg["retire_paths"] == [ms_defaults_only.render("{data}/comfy/custom_nodes/ComfyUI-Manager")] and cfg["keep_days"] == 14


def test_plan_refuses_a_missing_declared_root(world):
    """audit ★5: a lock from a smaller root set than the app has is never made."""
    world["cfg"]["files"].append(str(world["app"] / "custom_nodes" / "Gone" / "requirements.txt"))
    run = Runner(on={"compile": _lock_writer})
    with pytest.raises(rb.Refused, match="missing declared roots"):
        _plan(world, run)
    assert run.calls == []  # uv never ran
    assert "Gone" in rb.load_state("comfyui", "v0.37.4")["steps"]["plan"]["missing_roots"][0]


# ------------------------------------------------------------- audit ★6: the flip asks the env's own app
def test_flip_refuses_without_a_health_url(world):
    _ready(world)
    world["cfg"]["rebuild"].pop("health_url", None)
    with pytest.raises(rb.Refused, match="no health_url declared for comfyui"):
        rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True)
    assert not (world["envs"] / "comfyui").is_symlink()  # nothing moved


def test_flip_asks_the_declared_health_url(world):
    import http.server, threading
    _ready(world)
    srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        world["cfg"]["rebuild"]["health_url"] = f"http://127.0.0.1:{srv.server_port}/nope"  # a 404 is still UP
        with pytest.raises(rb.Refused, match="app is running"):
            rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True)
    finally:
        srv.shutdown()
    world["cfg"]["rebuild"]["health_url"] = f"http://127.0.0.1:{srv.server_port}/"  # now refused = down
    st = rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, today=dt.date(2026, 9, 29))
    assert (world["envs"] / "comfyui").is_symlink() and st["rebuild"]["health_url"].endswith("/")
    rb.rollback("comfyui", "v0.37.4", yes=True)  # asks the same URL, recorded at flip time
    assert not (world["envs"] / "comfyui").is_symlink()


def test_health_answers():
    assert rb.health_answers("http://127.0.0.1:9/") is False


def test_comfyui_declares_its_health_url():
    cfg = json.loads((ms_defaults_only.ROOTS_FILE).read_text())["envs"]["comfyui"]["rebuild"]
    assert cfg["health_url"] == "http://127.0.0.1:8188/system_stats"


# ------------------------------------------------------------- audit ★13: hold changes are written down
def test_flip_and_rollback_record_every_hold_change(world, monkeypatch):
    log = world["tmp"] / "env-hold.jsonl"
    monkeypatch.setattr(rb, "HOLD_LOG", log)
    _ready(world)
    st = rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: False, today=dt.date(2026, 9, 29))
    rb.rollback("comfyui", "v0.37.4", yes=True, app_running=lambda: False)
    ev = [json.loads(l) for l in log.read_text().splitlines()]
    assert [(e["env"], e["action"], e["by"]) for e in ev] == [
        ("comfyui", "released", "flip comfyui-v0.37.4"), ("comfyui", "restored", "rollback comfyui-v0.37.4")]
    assert ev[0]["row"] == {"reason": "rebuild"} and ev[1]["row"] == {"reason": "rebuild"}
    rec = rb.load_state("comfyui", "v0.37.4")["flip"]
    assert [e["action"] for e in rec["hold_events"]] == ["released", "restored"]


def test_a_flip_with_no_hold_writes_no_event(world, monkeypatch):
    log = world["tmp"] / "env-hold.jsonl"
    monkeypatch.setattr(rb, "HOLD_LOG", log)
    world["hold"].write_text("{}")
    _ready(world)
    rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: False)
    assert not log.exists()


# ---------------------------------------------------------------- house patches (09-30)
# The live vLLM env ran on a one-line hand edit of torchaudio nobody had written down; the first build
# beside it died on import. A patch is a build step: copied over the exact stock file, checksummed both sides.
STOCK, PATCHED = "def check():\n    raise RuntimeError('cuda 13.0 != 13.2')\n", "def check():\n    return None\n"


def _patched_world(world, installed=STOCK):
    import hashlib
    h = lambda t: hashlib.sha256(t.encode()).hexdigest()  # noqa: E731
    src = world["tmp"] / "app-utils.py"
    src.write_text(PATCHED)
    world["cfg"]["rebuild"]["patches"] = [{"file": "torchaudio/_extension/utils.py", "from": str(src),
                                           "sha256_stock": h(STOCK), "sha256_patched": h(PATCHED), "why": "test"}]
    _plan(world)
    new = world["envs"] / "comfyui-v0.37.4"
    target = new / "lib" / "python3.12" / "site-packages" / "torchaudio" / "_extension" / "utils.py"

    def sync(cmd):  # what `uv pip sync` leaves: the wheel's own file
        target.parent.mkdir(parents=True, exist_ok=True)
        if installed is not None:
            target.write_text(installed)

    run = Runner(on={"venv": lambda c: (new / "bin").mkdir(parents=True), "sync": sync})
    return new, target, run


def test_build_copies_a_declared_patch_over_the_stock_file(world):
    new, target, run = _patched_world(world)
    st = rb.build("comfyui", "v0.37.4", world["cfg"], run=run, scope=[])
    assert target.read_text() == PATCHED
    row = st["steps"]["build"]["patches"][0]
    assert row["state"] == "patched" and row["applied"] is True
    assert rb.read_marker(new)["built"]
    # again (a re-run syncs the stock file back, the step re-applies; an already patched file is left alone)
    st = rb.build("comfyui", "v0.37.4", world["cfg"], scope=[], run=Runner())
    assert st["steps"]["build"]["patches"][0]["applied"] is False and target.read_text() == PATCHED


def test_a_patch_never_edits_the_file_in_place(world):
    """uv hard-links site-packages to its cache: an in-place write would patch every env linked to it."""
    new, target, run = _patched_world(world)
    cache = world["tmp"] / "uv-cache-copy.py"

    def sync_linked(cmd):
        target.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(STOCK)
        target.hardlink_to(cache)

    run.on["sync"] = sync_linked
    rb.build("comfyui", "v0.37.4", world["cfg"], run=run, scope=[])
    assert target.read_text() == PATCHED and cache.read_text() == STOCK


@pytest.mark.parametrize("installed, word", [("def check():\n    raise RuntimeError('a NEW wheel')\n", "unknown"),
                                             (None, "missing")])
def test_a_patch_is_refused_over_anything_but_its_own_stock_file(world, installed, word):
    new, target, run = _patched_world(world, installed=installed)
    with pytest.raises(rb.Refused, match=word):
        rb.build("comfyui", "v0.37.4", world["cfg"], run=run, scope=[])
    st = rb.load_state("comfyui", "v0.37.4")
    assert st["steps"]["build"]["ok"] is False and "re-make the patch" in st["steps"]["build"]["err"]
    assert not rb.read_marker(new).get("built")  # an unpatched env never counts as built
    if installed:
        assert target.read_text() == installed


def test_a_patch_source_that_is_not_the_declared_file_is_refused(world):
    new, target, run = _patched_world(world)
    (world["tmp"] / "app-utils.py").write_text("def check():\n    return 'edited after the checksum'\n")
    with pytest.raises(rb.Refused, match="not the declared patched file"):
        rb.build("comfyui", "v0.37.4", world["cfg"], run=run, scope=[])
    assert target.read_text() == STOCK


def test_check_fails_when_a_patch_is_not_in_place(world, monkeypatch):
    new, target, run = _patched_world(world)
    rb.build("comfyui", "v0.37.4", world["cfg"], run=run, scope=[])
    kw = dict(run=Runner(), probe=lambda py, e: _new_dump(world),
              node_set=lambda py, r, port: {"nodes": ["KSampler", "UnetLoaderGGUF"], "err": "", "import_failed": []})
    assert rb.check("comfyui", "v0.37.4", world["cfg"], **kw)["steps"]["check"]["verdict"] == "pass"
    target.write_text(STOCK)  # a later sync put the wheel's file back
    res = rb.check("comfyui", "v0.37.4", world["cfg"], **kw)["steps"]["check"]
    assert res["verdict"] == "fail" and any("local patch not in place" in p for p in res["problems"])


def test_the_patch_kill_switch_leaves_the_env_as_synced(world, monkeypatch):
    new, target, run = _patched_world(world)
    monkeypatch.setattr(rb, "PATCHES_ON", False)
    st = rb.build("comfyui", "v0.37.4", world["cfg"], run=run, scope=[])
    assert target.read_text() == STOCK and st["steps"]["build"]["patches"][0]["skipped"]


@pytest.mark.skip(reason="the patched torchaudio file is a modified BSD source and a one-env workaround of the reference box: not shipped (its roots row still declares it)")
def test_the_vllm_lane_env_declares_its_torchaudio_patch():
    """The real config: the file in the repo is the declared one, and it is what the live env runs."""
    import hashlib
    cfg = json.loads((ms_defaults_only.ROOTS_FILE).read_text())["envs"]["vllm-0271-cu132"]["rebuild"]
    (p,) = cfg["patches"]
    src = Path(p["from"].replace("@repo", str(ROOT)))
    assert hashlib.sha256(src.read_bytes()).hexdigest() == p["sha256_patched"] != p["sha256_stock"]
    assert "return None  # trial venv" in src.read_text()
    assert cfg["extra_index_urls"] == ["https://download.pytorch.org/whl/cu132"]
    live = Path(str(ms_defaults_only.ENVS_ROOT) + "/vllm-0271-cu132/lib/python3.12/site-packages") / p["file"]
    if live.is_file():  # on the house box: the live env already carries exactly this file
        assert hashlib.sha256(live.read_bytes()).hexdigest() == p["sha256_patched"]


# ---------------------------------------------------------------- the first start, outside the lane (09-30)
# vLLM 0.30's first-ever start built FlashInfer's NVFP4 kernels on 32 cores inside the lane: 76 GB of RAM,
# 29 GB of the voice stack in swap. A new env's first start is a rebuild step in a unit of its own.
class Sys:
    """systemctl / systemd-run, remembered; `dead` = the transient unit exits before it serves."""
    def __init__(self, dead=False, run_fails=False, lane_stays_down=False):
        self.calls, self.dead, self.run_fails, self.lane_stays_down = [], dead, run_fails, lane_stays_down

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc, out = 0, ""
        if cmd[0] == "systemd-run":
            rc = 1 if self.run_fails else 0
        elif cmd[:4] == ["systemctl", "--user", "is-active", "-q"]:
            unit = cmd[4]
            if unit.startswith("ms-first-start-"):
                rc = 3 if self.dead else 0
            else:
                rc = 3 if self.lane_stays_down else 0
        elif "MemoryPeak" in cmd:
            out = str(5 * 1024 ** 3)
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="nope" if rc else "")


def _fs_world(world):
    hold = world["tmp"] / "train-hold"
    world["cfg"]["rebuild"]["first_start"] = {
        "lane_unit": "ops.service", "script": str(world["tmp"] / "start-ops.sh"),
        "env": {"VLLM_HOUSE_ENV": "{new_dir}", "OPS_VLLM_PREFIX": "{version_mm}", "MAX_JOBS": "4"},
        "ready_url": "http://127.0.0.1:8000/v1/models", "ready_timeout_s": 30, "settle_s": 0,
        "memory_max": "48G", "memory_swap_max": "2G", "hold_file": str(hold), "lane_back_timeout_s": 6}
    new = _built(world)
    clock = iter(range(0, 100000, 1))
    return new, hold, dict(sleep=lambda s: None, clock=lambda: next(clock))


def test_first_start_runs_in_its_own_capped_unit_and_the_lane_comes_back(world):
    new, hold, kw = _fs_world(world)
    sysd = Sys()
    st = rb.first_start("comfyui", "v0.37.4", world["cfg"], yes=True, run=sysd, answers=lambda url: True, **kw)
    res = st["steps"]["first_start"]
    assert res["ok"] and res["lane_back"] and res["memory_peak_mib"] == 5120
    assert sysd.calls[0] == ["systemctl", "--user", "stop", "ops.service"]          # the lane is stopped first
    run_cmd = next(c for c in sysd.calls if c[0] == "systemd-run")
    assert "MemoryMax=48G" in run_cmd and "MemorySwapMax=2G" in run_cmd            # its own cap, never the lane's
    assert f"VLLM_HOUSE_ENV={new}" in run_cmd and "OPS_VLLM_PREFIX=0.37" in run_cmd and "MAX_JOBS=4" in run_cmd
    hook = next(a for a in run_cmd if a.startswith("ExecStopPost="))
    assert "systemctl --user start ops.service" in hook and "card=first-start-comfyui" in hook
    assert f"rm -f {hold}" in hook                                                   # no empty hold file left behind
    assert run_cmd[-1].endswith("start-ops.sh")
    assert ["systemctl", "--user", "stop", run_cmd[run_cmd.index("--unit") + 1]] in sysd.calls
    assert "ops.service" in hold.read_text() and "card=first-start-comfyui" in hold.read_text()  # held meanwhile
    assert rb.read_marker(new)["first_started"]


@pytest.mark.parametrize("setup, word", [
    (lambda w: w["cfg"]["rebuild"].pop("first_start"), "declares no rebuild.first_start"),
    (lambda w: w["gpu"].write_text("render\n"), "gpu mode is 'render'"),
])
def test_first_start_refuses_before_it_touches_the_lane(world, setup, word):
    _fs_world(world)
    setup(world)
    sysd = Sys()
    with pytest.raises(rb.Refused, match=word):
        rb.first_start("comfyui", "v0.37.4", world["cfg"], yes=True, run=sysd, answers=lambda url: True)
    assert sysd.calls == []


def test_first_start_needs_yes_and_a_build(world):
    _fs_world(world)
    with pytest.raises(rb.Refused, match="--yes"):
        rb.first_start("comfyui", "v0.37.4", world["cfg"], yes=False, run=Sys())
    with pytest.raises(rb.Refused, match="build first"):
        rb.first_start("comfyui", "v9.9.9", world["cfg"], yes=True, run=Sys())


def test_a_first_start_killed_at_its_cap_fails_and_the_lane_still_returns(world):
    new, hold, kw = _fs_world(world)
    up = iter([False] * 3 + [True] * 50)     # never serves while the unit lives; the lane answers afterwards
    with pytest.raises(rb.Refused, match="exited before it served"):
        rb.first_start("comfyui", "v0.37.4", world["cfg"], yes=True, run=Sys(dead=True),
                       answers=lambda url: next(up), **kw)
    res = rb.load_state("comfyui", "v0.37.4")["steps"]["first_start"]
    assert res["ok"] is False and res["lane_back"] is True
    assert not rb.read_marker(new).get("first_started")


def test_a_lane_that_does_not_come_back_is_said_loudly(world):
    new, hold, kw = _fs_world(world)
    with pytest.raises(rb.Refused, match="did not come back .* systemctl --user start ops.service"):
        rb.first_start("comfyui", "v0.37.4", world["cfg"], yes=True, run=Sys(lane_stays_down=True),
                       answers=lambda url: True, **kw)


def test_a_unit_that_never_started_puts_the_lane_back_itself(world):
    new, hold, kw = _fs_world(world)
    sysd = Sys(run_fails=True)
    with pytest.raises(rb.Refused, match="systemd-run failed"):
        rb.first_start("comfyui", "v0.37.4", world["cfg"], yes=True, run=sysd, answers=lambda url: True, **kw)
    by_hand = [c for c in sysd.calls if c[:2] == ["/bin/sh", "-c"]]
    assert by_hand and "systemctl --user start ops.service" in by_hand[0][2] and '"' in by_hand[0][2]


def test_flip_refuses_until_the_first_start_has_passed(world):
    new, hold, kw = _fs_world(world)
    st = rb.load_state("comfyui", "v0.37.4")
    st["steps"]["check"] = {"verdict": "pass"}
    st["steps"]["compare"] = {"verdict": "pass"}
    rb.save_state(st)
    with pytest.raises(rb.Refused, match="first start has not passed"):
        rb.flip("comfyui", "v0.37.4", world["cfg"], yes=True, app_running=lambda: False)


def test_the_vllm_lanes_declare_a_bounded_first_start():
    fs = json.loads((ms_defaults_only.ROOTS_FILE).read_text())["envs"]["vllm-0271-cu132"]["rebuild"]["first_start"]
    assert fs["lane_unit"] == "ops.service" and fs["script"] == "@repo/scripts/start-ops.sh"
    assert fs["env"]["MAX_JOBS"].isdigit() and int(fs["env"]["MAX_JOBS"]) <= 8      # FlashInfer's ninja honours it
    assert fs["env"]["KREW_VLLM_ENV"] == "{new_dir}" and fs["env"]["OPS_VLLM_PREFIX"] == "{version_mm}"
    assert fs["memory_max"] and fs["memory_swap_max"] == "2G"
    # comfyui declares none: its flip is not gated on a step it does not have
    assert "first_start" not in json.loads((ms_defaults_only.ROOTS_FILE).read_text())["envs"]["comfyui"]["rebuild"]
