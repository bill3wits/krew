"""M&S and live service envs (the owner 2026-09-17: "please fix hindsight in the M&S
routine and in the served version. Need that stable.").

Card t_c5a8e457 (09-12) uninstalled 18 "never imported" packages from the
hindsight venv. The probe skipped `extra ==` requirements and could not name a
single-file module, so fastmcp-slim[server]'s jsonref read as unused; the smoke
was `import hindsight`; the running server hid it until the 09-16 reboot.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_spec = importlib.util.spec_from_file_location("ms_trail_service", ROOT / "ms" / "scripts" / "ms-trail-run.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def _dist(site: Path, name: str, requires: list[str], files: list[str]) -> None:
    di = site / f"{name}-1.0.dist-info"
    di.mkdir(parents=True)
    meta = [f"Metadata-Version: 2.1", f"Name: {name}", "Version: 1.0"]
    meta += [f"Requires-Dist: {r}" for r in requires]
    (di / "METADATA").write_text("\n".join(meta) + "\n")
    (di / "RECORD").write_text("".join(f"{f},,\n" for f in files + [f"{di.name}/METADATA"]))
    for f in files:
        p = site / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")


def test_unused_probe_counts_extras_and_names_single_file_modules(tmp_path):
    site = tmp_path / "site"
    _dist(site, "fastmcp-slim", ['jsonref>=1.1.0; extra == "server"'], ["fastmcp/__init__.py"])
    (site / "fastmcp" / "__pycache__").mkdir()
    (site / "fastmcp" / "__pycache__" / "__init__.cpython-314.pyc").write_text("")
    _dist(site, "jsonref", [], ["jsonref.py"])                 # the one that was removed
    _dist(site, "lonely", [], ["lonely.py"])                   # imported once: has a pyc
    (site / "__pycache__").mkdir()
    (site / "__pycache__" / "lonely.cpython-314.pyc").write_text("")
    _dist(site, "squatter", [], ["squatter.py"])               # nothing uses it
    out = subprocess.run([sys.executable, "-I", "-c",
                          f"import sys; sys.path[:0]=[{str(site)!r}]\n" + ms._UNUSED_PROBE],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert "jsonref" not in d["unreferenced"], "an extra's requirement is a reference"
    assert "squatter" in d["never_imported"], "a real squatter is still caught"
    assert "lonely" not in d["never_imported"], "a single-file module with a pyc was imported"


def test_hindsight_smoke_imports_the_server_not_the_package_name():
    code = ms.SMOKE_IMPORT["hindsight"]["code"]
    assert "import hindsight_api.main" in code and "fastmcp.server" in code
    assert code.strip() != "import hindsight"


def test_hindsight_is_a_live_service_env():
    # 2026-09-17 pm: SERVICE_ENVS is env -> list of units (schema widened).
    (spec,) = ms.SERVICE_ENVS["hindsight"]
    assert spec["unit"] == "hindsight.service" and spec["scope"] == "system"
    assert spec["health"].endswith(":8888/health") and spec["restart"] == "restart"


def _state(monkeypatch, *, health, started, changed, smoke_ok, err=""):
    monkeypatch.setattr(ms, "_http_ok", lambda url, timeout=3.0: health)
    monkeypatch.setattr(ms, "_unit_started_at", lambda spec: started)
    monkeypatch.setattr(ms, "_site_packages_mtime", lambda py: changed)
    if err:
        ms.SMOKE_ERR["hindsight"] = err
    return ms.service_env_state("hindsight", "/x/python", smoke_ok)


def test_service_state_names_the_hidden_break(monkeypatch):
    s = _state(monkeypatch, health=True, started=100, changed=200, smoke_ok=False,
               err="ModuleNotFoundError: No module named 'jsonref'")
    assert s["verdict"] == "broken" and s["process_older_than_env"] is True
    assert "jsonref" in s["smoke_err"]
    assert ms.service_blocks_apply(s) == "service_smoke_red"
    assert _state(monkeypatch, health=False, started=0, changed=200, smoke_ok=True)["verdict"] == "down"
    assert ms.service_blocks_apply({"verdict": "down"}) == "service_down"
    owed = _state(monkeypatch, health=True, started=100, changed=200, smoke_ok=True)
    assert owed["verdict"] == "restart-owed" and ms.service_blocks_apply(owed) == ""
    assert _state(monkeypatch, health=True, started=300, changed=200, smoke_ok=True)["verdict"] == "ok"
    ms.SMOKE_ERR.clear()


def test_after_apply_a_live_service_is_restarted_and_rolled_back_when_it_does_not_return(monkeypatch):
    calls = []
    verdicts = iter([(False, "health did not come back"), (True, "healthy")])
    monkeypatch.setattr(ms, "service_restart_check", lambda name: next(verdicts))
    monkeypatch.setattr(ms, "run", lambda cmd, timeout=120, extra_env=None: calls.append(cmd) or (0, "", ""))
    monkeypatch.setattr(ms, "MS_SERVICE_RESTART", True)
    # the restart window's ask (1cdb9e02, 10-01) reads the LIVE house; a quiet window is this test's premise
    monkeypatch.setattr(ms, "_restart_window_busy", lambda units: "")
    res = ms.service_verify_after_apply(
        "hindsight", "/x/python", {"ok": True, "upgraded": ["boto3 1.40.0→1.43.96", "torch 2.9+cu130→2.10"]})
    assert res["ok"] is False and res["rollback"] is True
    rollback = [c for c in calls if "--reinstall" in c]
    assert rollback and "boto3==1.40.0" in rollback[0]
    assert not any("torch==" in x for x in rollback[0]), "a local build is never pinned back from PyPI"
    assert "rolled back 1 pins" in res["err"]


def test_after_apply_a_healthy_restart_keeps_the_upgrade(monkeypatch):
    monkeypatch.setattr(ms, "service_restart_check", lambda name: (True, "healthy"))
    monkeypatch.setattr(ms, "MS_SERVICE_RESTART", True)
    # the restart window's ask (1cdb9e02, 10-01) reads the LIVE house; a quiet window is this test's premise
    monkeypatch.setattr(ms, "_restart_window_busy", lambda units: "")
    res = ms.service_verify_after_apply("hindsight", "/x/python", {"ok": True, "upgraded": ["a 1→2"]})
    assert res["ok"] is True and res["service"] == "healthy"
    same = {"ok": True, "upgraded": ["a 1→2"]}
    # audio8-tts is a service env since the table widened; comfyui is not.
    assert "comfyui" not in ms.SERVICE_ENVS
    assert ms.service_verify_after_apply("comfyui", "/x/python", same) == {"ok": True, "upgraded": ["a 1→2"]}


def test_the_night_does_not_touch_a_down_or_broken_service_env():
    src = (ROOT / "ms" / "scripts" / "ms-trail-run.py").read_text(encoding="utf-8")
    loop = src.split('batches.append(("green", to_green))', 1)[1].split("for label, rows in batches:", 1)[0]
    assert "service_blocks_apply(" in loop and "batches = []" in loop
    assert "result = service_verify_after_apply(name, py, result)" in src


def test_the_card_leads_with_a_live_service_that_is_not_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")  # a read refreshes LAST-GOOD
    summary = {"totals": {}, "env_summaries": [{
        "env": "hindsight", "outdated_n": 0, "green_candidate": 0, "risk": 0, "never": 0,
        "smoke_baseline": False, "unused_n": 1, "unused_sample": ["mlx"],
        "service": {"unit": "hindsight.service", "verdict": "broken",
                    "smoke_err": "ModuleNotFoundError: No module named 'jsonref'"},
    }]}
    ms.upstream_note = lambda n: ""
    body = ms._card_body(summary, {}, "2026-09-17")
    assert body.splitlines()[1].startswith("**LIVE SERVICE hindsight (hindsight.service)")
    assert "jsonref" in body and "--service-check hindsight" in body
    assert "never as `wontfix`" in body
