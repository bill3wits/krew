"""M&S roots, Stage A (report only): buckets computed from each env's roots.

The case that started it (the User 09-28): comfyui's nine "group-wait" rows
were five orphans (comfy-cli's mixpanel/semver, the legacy Manager's
matrix-nio chain) and four of ComfyUI's own requirements.txt pins - and a
green apply had already moved embedded-docs past ComfyUI's `==0.5.12`.
"""
from __future__ import annotations

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
import env_roots as er  # noqa: E402

ENVM = {"implementation_name": "cpython", "implementation_version": "3.12.13", "os_name": "posix",
        "platform_machine": "x86_64", "platform_release": "7.0", "platform_system": "Linux",
        "platform_version": "", "python_full_version": "3.12.13",
        "platform_python_implementation": "CPython", "python_version": "3.12", "sys_platform": "linux"}


def D(version, requires=(), tops=(), ep=(), size=1_000_000):
    return {"name": None, "version": version, "requires": list(requires), "tops": list(tops),
            "size": size, "installed": 1790000000.0, "installer": "uv", "requested": True,
            "editable": "", "ep_groups": list(ep)}


def dump(dists, imports=None):
    for k, d in dists.items():
        d["name"] = d["name"] or k
    return {"markers": ENVM, "dists": dists, "imports": imports or {}, "scanned_files": 1}


def row(pkg, cur, lat, **kw):
    return {"package": pkg, "current": cur, "latest": lat, **kw}


# ------------------------------------------------------------- the comfyui case
@pytest.fixture
def comfy(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("comfyui-workflow-templates==0.11.69\ncomfyui-embedded-docs==0.5.12\n"
                   "transformers>=4.50.3\n# a comment\n")
    dists = {
        "comfyui-workflow-templates": D("0.11.69", ["comfyui-workflow-templates-core==0.3.360",
                                                    "comfyui-workflow-templates-json==0.1.95",
                                                    "comfyui-workflow-templates-media-assets-02==0.1.5"]),
        "comfyui-workflow-templates-core": D("0.3.360"),
        "comfyui-workflow-templates-json": D("0.1.95"),
        "comfyui-workflow-templates-media-assets-02": D("0.1.5"),
        "comfyui-embedded-docs": D("0.5.13"),
        "transformers": D("5.10.2", ["tokenizers<=0.23.0,>=0.22.0"]),
        "tokenizers": D("0.22.2"),
        "comfy-cli": D("1.20.0", ["mixpanel<5", "semver~=3.0.2"]),
        "mixpanel": D("4.11.1"), "semver": D("3.0.4"),
        "matrix-nio": D("0.26.0", ["aiohttp-socks>=0.8"]),
        "aiohttp-socks": D("0.11.0", ["python-socks<3.0.0,>=2.4.3"]),
        "python-socks": D("2.8.1"),
        "pip": D("26.0"),
    }
    rows = {"group-wait": [
        row("aiohttp-socks", "0.11.0", "0.12.0"), row("comfy-cli", "1.20.0", "1.21.0"),
        row("comfyui-workflow-templates", "0.11.69", "0.11.70"),
        row("comfyui-workflow-templates-core", "0.3.360", "0.3.361"),
        row("comfyui-workflow-templates-json", "0.1.95", "0.1.96"),
        row("comfyui-workflow-templates-media-assets-02", "0.1.5", "0.1.6"),
        row("mixpanel", "4.11.1", "5.4.0"), row("python-socks", "2.8.1", "3.1.1"),
        row("semver", "3.0.4", "3.1.0")],
        "green_candidate": [row("comfyui-embedded-docs", "0.5.12", "0.5.13")]}
    latest = {
        ("comfyui-workflow-templates", "0.11.70"): ["comfyui-workflow-templates-core==0.3.361",
                                                    "comfyui-workflow-templates-json==0.1.96",
                                                    "comfyui-workflow-templates-media-assets-02==0.1.6"],
        ("comfy-cli", "1.21.0"): ["mixpanel<5", "semver~=3.0.2"],  # still pins: a permanent wait
    }
    cfg = {"files": [str(req)], "entry_point_groups": {}, "data_wheels": []}
    return cfg, dump(dists), rows, (lambda n, v: latest.get((n, v)))


def test_comfyui_nine_group_waits_are_four_app_pins_and_five_orphans(comfy):
    cfg, dp, rows, req = comfy
    a = er.analyze("comfyui", cfg, dp, {"group-wait": rows["group-wait"]}, requires_of=req)
    by = {x["package"]: x["new"] for x in a["detail"]}
    assert sorted(k for k, v in by.items() if v == "orphan") == [
        "aiohttp-socks", "comfy-cli", "mixpanel", "python-socks", "semver"]
    assert sorted(k for k, v in by.items() if v == "app-pinned") == [
        "comfyui-workflow-templates", "comfyui-workflow-templates-core",
        "comfyui-workflow-templates-json", "comfyui-workflow-templates-media-assets-02"]
    assert a["new"]["group-wait"] == 0
    sub = next(x for x in a["detail"] if x["package"] == "comfyui-workflow-templates-core")
    assert "frees it" in sub["why"] and "app-pinned" in sub["why"]  # freed by a pinner that cannot move


def test_an_app_pin_break_is_drift_and_its_green_is_app_pinned(comfy):
    cfg, dp, rows, req = comfy
    a = er.analyze("comfyui", cfg, dp, rows, requires_of=req)
    assert {"package": "comfyui-embedded-docs", "installed": "0.5.13", "root_spec": "==0.5.12",
            "root": "file:requirements.txt"} in a["drift"]
    assert next(x for x in a["detail"] if x["package"] == "comfyui-embedded-docs")["new"] == "app-pinned"


def test_orphans_say_why_and_the_needed_stay(comfy):
    cfg, dp, rows, req = comfy
    a = er.analyze("comfyui", cfg, dp, rows, requires_of=req,
                   ledger={"decisions": [{"env": "comfyui", "package": "comfy-cli", "source": "t_682529af"}]})
    names = {o["package"]: o for o in a["orphans"]}
    assert set(names) == {"comfy-cli", "mixpanel", "semver", "matrix-nio", "aiohttp-socks", "python-socks"}
    assert names["comfy-cli"]["ledger_source"] == ["t_682529af"]
    assert names["mixpanel"]["why"] == "required only by orphans: comfy-cli"
    assert "nothing installed requires it" in names["matrix-nio"]["why"]
    assert "pip" not in names and "tokenizers" not in names  # tooling / reached


# ------------------------------------------------------------- group-wait is resolved
def _pinned_case(latest_requires):
    dists = {"app": D("1.0", ["lib"]), "lib": D("2.0", ["dep<2"]), "dep": D("1.5")}
    rows = {"group-wait": [row("dep", "1.5", "2.1"), row("lib", "2.0", "2.1")]}
    a = er.analyze("e", {"app": ["app"]}, dump(dists), rows, requires_of=latest_requires)
    return {x["package"]: x for x in a["detail"]}["dep"]


def test_group_wait_only_when_the_pinners_latest_frees_it():
    assert _pinned_case(lambda n, v: ["dep<3"])["new"] == "group-wait"
    x = _pinned_case(lambda n, v: ["dep<2"])
    assert x["new"] == "pinned" and "still <2" in x["why"]
    assert _pinned_case(lambda n, v: None)["new"] == "pinned"  # unresolved is never assumed free


def test_a_pinner_at_its_latest_pins():
    dists = {"app": D("1.0", ["lib"]), "lib": D("2.0", ["dep<2"]), "dep": D("1.5")}
    a = er.analyze("e", {"app": ["app"]}, dump(dists), {"group-wait": [row("dep", "1.5", "2.1")]},
                   requires_of=lambda n, v: [])
    assert a["detail"][0]["new"] == "pinned" and "is its latest" in a["detail"][0]["why"]


def test_an_orphan_pinner_holds_nothing_we_need():
    dists = {"app": D("1.0", ["dep"]), "dep": D("1.5"), "junk": D("1.0", ["dep<2"])}
    a = er.analyze("e", {"app": ["app"]}, dump(dists), {"pinned": [row("dep", "1.5", "1.6")]},
                   classify=lambda p, c, l: "green_candidate", requires_of=lambda n, v: None)
    assert a["detail"][0]["new"] == "green" and "orphans" in a["detail"][0]["why"]


# ------------------------------------------------------------- loaded, not imported
def test_a_plugin_rides_with_its_host_and_the_host_is_not_its_own_root():
    dists = {"vllm": D("0.27.1", ep=["vllm.general_plugins"]), "plug": D("0.1", ["plugdep"], ep=["vllm.general_plugins"]),
             "plugdep": D("1.0")}
    cfg = {"entry_point_groups": {"vllm.general_plugins": "vllm"}}
    via = er.closure([], dump(dists), cfg)
    assert via == {}  # vllm naming its own group does not make it a root
    roots, _ = er.roots_of({"app": ["vllm"]}, dump(dists))
    via = er.closure(roots, dump(dists), cfg)
    assert via["plug"] == "entry-point:vllm.general_plugins" and via["plugdep"] == "plug"


def test_a_data_wheel_rides_with_its_host_only():
    dists = {"flashinfer-python": D("0.6"), "flashinfer-cubin": D("0.6", size=1_900_000_000)}
    cfg = {"data_wheels": [{"dist": "flashinfer-cubin", "for": "flashinfer-python"}]}
    assert er.closure([], dump(dists), cfg) == {}
    roots, _ = er.roots_of({"app": ["flashinfer-python"]}, dump(dists))
    assert er.closure(roots, dump(dists), cfg)["flashinfer-cubin"] == "data-wheel:flashinfer-python"


def test_file_roots_and_clis_are_roots():
    dists = {"nvidia-cudnn-cu12": D("9"), "nvidia-cublas-cu12": D("12"), "nvidia-nccl-cu12": D("2"),
             "piper-tts": D("1.3")}
    cfg = {"file_roots": [{"by": "lib/w2l.py", "match": "nvidia-(cublas|cudnn)(-cu\\d+)?"}],
           "clis": [{"dist": "piper-tts", "by": "crissa_tts"}]}
    roots, _ = er.roots_of(cfg, dump(dists))
    via = er.closure(roots, dump(dists), cfg)
    assert via["nvidia-cudnn-cu12"] == "root:file-root:lib/w2l.py"
    assert via["piper-tts"] == "root:cli:crissa_tts"
    assert "nvidia-nccl-cu12" not in via


def test_imports_map_to_dists_and_unmapped_are_listed():
    dists = {"opencv-python": D("4", tops=["cv2"]), "numpy": D("2", tops=["numpy"])}
    roots, unmapped = er.roots_of({}, dump(dists, imports={"cv2": ["a.py"], "ghost": ["b.py"]}))
    assert [(r.name, s) for r, s in roots] == [("opencv-python", "import")]
    assert unmapped == {"ghost": ["b.py"]}


def test_extras_follow_only_when_asked(tmp_path):
    dists = {"transformers": D("5", ["timm; extra == 'timm'", "tokenizers"]), "timm": D("1"), "tokenizers": D("0.22")}
    r1, _ = er.roots_of({"app": ["transformers"]}, dump(dists))
    assert "timm" not in er.closure(r1, dump(dists), {})
    req = tmp_path / "r.txt"
    req.write_text("transformers[timm]>=4\n")
    r2, _ = er.roots_of({"files": [str(req)]}, dump(dists))
    assert "timm" in er.closure(r2, dump(dists), {})


def test_drift_reads_the_app_dists_own_requirements():
    dists = {"unsloth": D("2026.7.3", ["peft>=0.18.0"]), "peft": D("0.15.2")}
    roots, _ = er.roots_of({"app": ["unsloth"]}, dump(dists))
    assert er.drift(roots, dump(dists)) == [{"package": "peft", "installed": "0.15.2",
                                            "root_spec": ">=0.18.0", "root": "unsloth 2026.7.3"}]


# ------------------------------------------------------------- the probe itself
def test_probe_scans_a_namespace_package_and_reports_third_party(tmp_path):
    app = tmp_path / "app"
    (app / "comfy").mkdir(parents=True)  # no __init__.py: ComfyUI's comfy/ is a namespace package
    (app / "comfy" / "model.py").write_text("import packaging.version\nimport os\n")
    (app / "main.py").write_text("import comfy.model\nfrom helper import x\n")
    (app / "helper.py").write_text("x = 1\nimport json\n")
    d = er.probe(sys.executable, [str(app / "main.py")])
    assert "packaging" in d["imports"] and "comfy" not in d["imports"] and "helper" not in d["imports"]
    assert d["scanned_files"] == 3
    assert "packaging" in d["dists"] and d["dists"]["packaging"]["version"]
    assert isinstance(d["duplicates"], dict)


def test_shebang_entries_expand_to_python_scripts(tmp_path):
    (tmp_path / "ref-wall-ascii").write_text("#!/usr/bin/env python3\nimport x\n")
    (tmp_path / "start.sh").write_text("#!/bin/bash\n")
    (tmp_path / "noext-sh").write_text("#!/bin/bash\n")
    assert er.expand_entries([f"shebang:{tmp_path}"]) == [str(tmp_path / "ref-wall-ascii")]


# ------------------------------------------------------------- two scans, the report
def test_two_scans_both_reported_and_totals_take_the_wide_one(monkeypatch, tmp_path):
    narrow = dump({"app": D("1"), "torch": D("2.12", size=1_000_000_000)})
    wide = dump({"app": D("1"), "torch": D("2.12", size=1_000_000_000)}, imports={"torch": ["x.py"]})
    wide["dists"]["torch"]["tops"] = ["torch"]
    calls = []

    def fake_probe(py, entries, **k):
        calls.append(entries)
        return wide if entries == [W] else narrow
    monkeypatch.setattr(er, "probe", fake_probe)
    N, W = str(tmp_path / "narrow.py"), str(tmp_path / "wide")
    Path(N).write_text("")
    Path(W).mkdir()
    cfg = {"envs": {"vllm": {"label": "the house interpreter", "app": ["app"], "entries": [N],
                             "entries_wide": [W], "scan_note": "n vs w"}}}
    r = er.run_env("vllm", "/py", {}, cfg=cfg, requires_of=lambda n, v: None)
    assert calls == [[N], [W]]
    assert r["orphans_n"] == 1 and r["wide"]["orphans_n"] == 0
    md = "\n".join(er.markdown({"vllm": r}, "2026-09-29"))
    assert "| vllm · narrow |" in md and "| vllm · wide |" in md and "REPORT ONLY" in md
    assert er.totals({"vllm": r})["orphans_installed"] == 0


def test_an_env_with_no_roots_is_an_error_row():
    assert "no roots declared" in er.run_env("ghost", "/py", {}, cfg={"envs": {}})["error"]


# ------------------------------------------------------------- the roots file
def test_the_roots_file_carries_the_users_rules():
    cfg = json.loads((ms_defaults_only.ROOTS_FILE).read_text())
    envs = cfg["envs"]
    assert {"comfyui", "hindsight", "audio8-tts", "rail-stt", "vllm", "vllm-0271-cu132", "vllm-030-cu132", "chatterbox-turbo",
            "audio8-asr", "mfa", "mediapipe", "unsloth", "unsloth-train", "wespeaker"} <= set(envs)
    assert "ARK ASR" in envs["audio8-asr"]["label"]
    assert "cosyvoice" not in envs and "cosyvoice_vllm" not in envs, "retired 2026-10-07 (cosy-retire)"
    assert envs["mfa"]["conda"] is True and envs["mfa"]["orphan_remove"] is False
    assert envs["vllm"]["entries"] and envs["vllm"]["entries_wide"]
    # every kind of loaded-not-imported root is declared from the start
    assert cfg["defaults"]["entry_point_groups"]["vllm.general_plugins"] == "vllm"
    assert {"dist": "flashinfer-cubin", "for": "flashinfer-python"} in cfg["defaults"]["data_wheels"]
    assert envs["vllm"]["clis"][0]["dist"] == "piper-tts"
    # comfyui's roots are ComfyUI + the two nodes our workflows use - never the Manager or comfy-cli
    files = " ".join(envs["comfyui"]["files"] + envs["comfyui"]["entries"])
    assert "ComfyUI-Manager" not in files and "comfy-cli" not in files and "ComfyUI-LTXVideo" in files


# ------------------------------------------------------------- the trail's hooks
def _trail():
    spec = importlib.util.spec_from_file_location("ms_trail_roots", ROOT / "ms" / "scripts" / "ms-trail-run.py")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    return ms


def test_off_by_default_and_never_on_the_survey(monkeypatch):
    ms = _trail()
    monkeypatch.delenv("MS_ROOTS", raising=False)
    assert ms.roots_enabled(False) is False
    monkeypatch.setenv("MS_ROOTS", "report")
    assert ms.roots_enabled(False) is True and ms.roots_enabled(True) is False
    monkeypatch.setenv("MS_ROOTS", "off")
    assert ms.roots_enabled(False) is False


def test_a_failing_env_is_its_own_row_never_the_nights(monkeypatch, tmp_path):
    ms = _trail()
    monkeypatch.setattr(ms, "_load_decisions", lambda *a, **k: {"decisions": []})

    def boom(*a, **k):
        raise RuntimeError("probe rc=1")
    monkeypatch.setattr(ms._roots, "run_env", boom)
    py = tmp_path / "python"
    py.write_text("")
    r = ms.env_roots_row("comfyui", str(py), {}, None)
    assert r["error"].startswith("RuntimeError") and r["label"] == "comfyui"


def test_the_roots_page_is_written_beside_the_trail(monkeypatch, tmp_path):
    ms = _trail()
    monkeypatch.setattr(ms, "REPORT_DIR", tmp_path)
    monkeypatch.setenv("MS_ROOTS", "report")
    r = er.analyze("e", {"app": ["app"]}, dump({"app": D("1"), "junk": D("1")}), {})
    report = {"envs": {"e": {"roots": r}, "f": {}}}
    p = ms.write_roots_report(report, "2026-09-29", adhoc=False)
    assert p == tmp_path / "ms-roots-2026-09-29.md" and (tmp_path / "ms-roots-LATEST.md").is_file()
    assert report["roots"]["totals"]["orphans_installed"] == 1 and report["roots"]["mode"] == "report"
    p2 = ms.write_roots_report({"envs": {"e": {"roots": r}}}, "2026-09-29", adhoc=True)
    assert "-adhoc-" in p2.name
    assert ms.write_roots_report({"envs": {"e": {}}}, "2026-09-29", adhoc=False) is None


def test_the_hook_sits_after_classification_and_before_any_apply():
    src = (ROOT / "ms" / "scripts" / "ms-trail-run.py").read_text()
    hook = src.index('report["envs"][name]["roots"] = env_roots_row(')
    assert src.index('"unused": unused_packages(py)') < hook < src.index("batches = hold_env_batches(name, batches, report)")


def test_the_env_guard_is_still_a_context_manager():
    """The first cut slid env_roots_row in under _env_guard's decorator: the
    guard stopped being a context manager and every night would have died."""
    ms = _trail()
    fails, exc, rep = [], [], {}
    with ms._env_guard("x", fails, exc, rep):
        raise ValueError("one env")
    assert exc == ["x"] and fails[0]["reason"] == "env_exception"


def test_the_trail_page_names_the_roots_section_before_health():
    src = (ROOT / "ms" / "scripts" / "ms-trail-run.py").read_text()
    assert '"## Roots (REPORT ONLY — changes nothing tonight)"' in src
    assert src.index("write_roots_report(report, day, adhoc)") < src.index('md = REPORT_DIR / f"ms-trail-{day}.md"')


# ------------------------------------------------------------- borrowed through a .pth
def test_probe_tells_local_from_borrowed_dists(tmp_path):
    """rail-stt's zz-vllm-asr.pth puts the reference env root's vllm on its path: those dists
    are importable there and installed elsewhere - never its orphans (09-29)."""
    env = tmp_path / "env"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(env)], check=True)
    py = env / "bin" / "python"
    site = Path(subprocess.run([str(py), "-c", "import site;print(site.getsitepackages()[0])"],
                               capture_output=True, text=True, check=True).stdout.strip())
    other = tmp_path / "otherenv" / "lib" / "python3.12" / "site-packages"
    for where, name, ver in ((site, "mine", "1.0"), (other, "theirs", "2.0"), (other, "mine", "0.9")):
        di = where / f"{name}-{ver}.dist-info"
        di.mkdir(parents=True)
        (di / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n")
    (site / "zz-other.pth").write_text(str(other) + "\n")
    d = er.probe(str(py), [])
    assert d["dists"]["mine"]["local"] is True and d["dists"]["mine"]["version"] == "1.0"
    assert d["dists"]["theirs"]["local"] is False and d["dists"]["theirs"]["home"] == str(tmp_path / "otherenv")
    assert d["duplicates"] == {}  # one local + one borrowed is shadowing, not a duplicate install


def _mk(dists, local=True, home=""):
    out = {}
    for k, v in dists.items():
        out[k] = D(v)
        out[k]["local"], out[k]["home"] = local, home
    return out


def test_borrowed_dists_are_never_orphans_here_and_are_roots_there():
    a_d = _mk({"ear": "1"})
    a_d["ear"]["requires"] = ["torch"]
    a_d.update(_mk({"torch": "2.12"}, local=False, home=str(ms_defaults_only.ENVS_ROOT) + "/vllm"))
    ra = er.analyze("rail-stt", {"app": ["ear"]}, dump(a_d), {})
    assert ra["borrowed"] == {str(ms_defaults_only.ENVS_ROOT) + "/vllm": ["torch"]} and ra["orphans_n"] == 0
    b_d = _mk({"app": "1", "torch": "2.12", "junk": "1"})
    rows = {"never": [row("torch", "2.12", "2.14")], "risk": [row("junk", "1", "2")]}
    rb_ = er.analyze("vllm", {"app": ["app"]}, dump(b_d), rows)
    assert {o["package"] for o in rb_["orphans"]} == {"torch", "junk"}
    res = er.lend({"rail-stt": ra, "vllm": rb_})
    v = res["vllm"]
    assert {o["package"] for o in v["orphans"]} == {"junk"} and v["lent_to"] == {"rail-stt": ["torch"]}
    assert {x["package"]: x["new"] for x in v["detail"]} == {"torch": "never", "junk": "orphan"}
    assert "lends 1 dists to rail-stt" in "\n".join(er.markdown(res, "d"))
    assert f"borrows 1 dists from `{ms_defaults_only.ENVS_ROOT}/vllm`" in "\n".join(er.markdown(res, "d"))


def test_rail_stt_declares_the_cuda_libs_its_unit_loads_by_path():
    cfg = json.loads((ms_defaults_only.ROOTS_FILE).read_text())["envs"]["rail-stt"]
    import re as _re
    pat = [_re.compile(f["match"]) for f in cfg["file_roots"]]
    for lib in ("nvidia-cudnn-cu12", "nvidia-cublas-cu12", "nvidia-cuda-nvrtc-cu12"):
        assert any(p.fullmatch(lib) for p in pat), lib


# ------------------------------------------------------------- audit ★5: a missing declared root
def test_a_missing_declared_root_is_an_error_row(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("numpy\n")
    ok = {"files": [str(req)], "entries": [str(tmp_path)], "app": ["app"]}
    d = dump({"app": D("1"), "numpy": D("2")})
    assert er.missing_roots(ok, d) == [] and "error" not in er.analyze("e", ok, d, {})
    gone = {"files": [str(tmp_path / "moved.txt")], "entries": [str(tmp_path / "server.py"), "shebang:/nope"],
            "entries_wide": [str(tmp_path / "wide")], "app": ["app", "ghost"],
            "clis": [{"dist": "piper-tts", "by": "x"}]}
    miss = er.missing_roots(gone, d)
    assert miss == sorted([f"file {tmp_path / 'moved.txt'}", f"entry {tmp_path / 'server.py'}", "entry shebang:/nope",
                           f"entry {tmp_path / 'wide'}", "app ghost (not installed)", "cli piper-tts (not installed)"])
    r = er.analyze("e", gone, d, {})
    assert r["error"].startswith("missing declared roots:") and r["missing_roots"] == miss
    md = "\n".join(er.markdown({"e": r}, "d"))
    assert "error: missing declared roots" in md
    assert er.totals({"e": r})["errors"] == 1 and er.totals({"e": r})["envs"] == 0
