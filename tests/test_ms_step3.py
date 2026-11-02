"""M&S step 3 (the User 09-29): a user's machine starts report-only, and an installer can register an env.

- An env is applied only with a smoke test AND `apply: green`; `apply: report` is the default.
- `promote: {after_clean_nights: N}` turns a report env green after N consecutive clean nightly runs
  (Hindsight: report at install, green after a week); an unclean night resets the count.
- `--register-env` writes the env's row (interpreter, smoke by health URL, service, apply, promotion,
  health lane) into the user's ms.yaml; the env is then inventoried wherever it lives.
- Honest gaps: no uv -> pip's own check; no /proc -> "unverified", never "unused"; no canary ->
  "no canary configured"; no OS lane -> "not measured".
- The house profile marks its smoke-tested envs green, so the house applies what it applied before.
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
SCRIPT = ROOT / "ms" / "scripts" / "ms-trail-run.py"
sys.path.insert(0, str(ROOT / "tests"))
from ms_defaults_only import load_ms  # noqa: E402

ms = load_ms("ms_step3", SCRIPT)   # the house profile, never the live ms.yaml (10-03)

BATCHES = [("green", [{"package": "a"}, {"package": "b"}])]


@pytest.fixture
def envs(monkeypatch):
    """Settings rows for three envs, all with a smoke test."""
    rows = {"g": {"apply": "green"}, "r": {}, "p": {"promote": {"after_clean_nights": 3}}}
    monkeypatch.setitem(ms._S.SETTINGS, "envs", rows)
    for n in rows:
        monkeypatch.setitem(ms.SMOKE_IMPORT, n, {"code": "pass"})
    monkeypatch.setattr(ms, "MS_APPLY_SETTING", True)
    monkeypatch.setattr(ms, "MS_REQUIRE_SMOKE", True)
    return rows


def test_report_is_the_default_and_green_applies(envs, tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "PROMOTIONS_PATH", tmp_path / "promotions.json")
    rep = {"envs": {}, "applied": {}}
    assert ms.smoke_gate_batches("g", list(BATCHES), rep) == BATCHES
    assert ms.smoke_gate_batches("r", list(BATCHES), rep) == []
    assert rep["applied"]["r"]["skipped"] == "report_only_setting" and rep["applied"]["r"]["held_n"] == 2
    assert "apply: report" in ms.applied_label(rep["applied"]["r"], considered_apply=True, green_n=2)


def test_a_week_of_clean_nights_promotes(envs, tmp_path, monkeypatch):
    path = tmp_path / "promotions.json"
    monkeypatch.setattr(ms, "PROMOTIONS_PATH", path)
    clean = {"envs": {"p": {"smoke_baseline": True}}}
    dirty = {"envs": {"p": {"smoke_baseline": False}}}
    ms.update_promotions(clean, "2026-10-01")
    ms.update_promotions(clean, "2026-10-01")                  # same night counts once
    ms.update_promotions(dirty, "2026-10-02")                  # an unclean night resets
    for d in ("2026-10-03", "2026-10-04"):
        ms.update_promotions(clean, d)
    assert ms.env_apply_mode("p") == "report" and ms.load_promotions()["p"]["clean_nights"] == 2
    out = ms.update_promotions(clean, "2026-10-05")
    assert out["p"]["promoted_on"] == "2026-10-05" and ms.env_apply_mode("p") == "green"
    assert ms.smoke_gate_batches("p", list(BATCHES), {"envs": {}, "applied": {}}) == BATCHES
    ms.update_promotions(dirty, "2026-10-06")                  # promotion is sticky
    assert ms.env_apply_mode("p") == "green"


def test_a_blocking_service_is_not_a_clean_night(envs, tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "PROMOTIONS_PATH", tmp_path / "promotions.json")
    ms.update_promotions({"envs": {"p": {"smoke_baseline": True,
                                         "service": {"verdict": "down"}}}}, "2026-10-01")
    assert ms.load_promotions()["p"]["clean_nights"] == 0


def test_no_smoke_still_wins_and_kill_switch(envs, monkeypatch):
    rep = {"envs": {}, "applied": {}}
    monkeypatch.delitem(ms.SMOKE_IMPORT, "g")
    assert ms.smoke_gate_batches("g", list(BATCHES), rep) == []
    assert rep["applied"]["g"]["skipped"] == "no_declared_smoke"
    monkeypatch.setattr(ms, "MS_APPLY_SETTING", False)
    assert ms.smoke_gate_batches("r", list(BATCHES), {"envs": {}, "applied": {}}) == BATCHES


def test_the_house_applies_what_it_did():
    rows = ms._S.get("envs")
    # 09-30: the freshly registered engine env is report-only on the User's word ("report-only first");
    # every env the house applied before still applies.
    # 10-01: mediapipe is the LIVE face engine; a bump re-cuts every head, so it lands by hand.
    # 10-07 cosy-retire: the voice's env (chatterbox-turbo) is newly registered, report-only first; audio8-asr
    # carries the ear the cosyvoice env carried, green as it was.
    report_first = {"vllm-030-cu132", "mediapipe", "chatterbox-turbo"}
    for name in ms.SMOKE_IMPORT:
        assert rows[name].get("apply") == ("report" if name in report_first else "green"), name
    assert ms.env_apply_mode("hindsight") == "green"


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"healthy"}')

    def log_message(self, *a):
        pass


def test_register_env_makes_a_testable_report_env(tmp_path):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/health"
    settings = tmp_path / "ms.yaml"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MS_",))}
    env.update(MS_SETTINGS=str(settings), MS_INTEGRATIONS="none", MS_OUT=str(tmp_path / "st"))
    r = subprocess.run([sys.executable, str(SCRIPT), "--register-env", "hindsight",
                        "--env-python", sys.executable, "--env-role", "memory server",
                        "--smoke-url", url, "--service-unit", "krew-hindsight.service",
                        "--apply", "report", "--promote-after", "7", "--critical",
                        "--hindsight-env-file", str(tmp_path / "hindsight.env")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "green after 7 clean nights" in r.stdout
    import yaml
    doc = yaml.safe_load(settings.read_text())
    row = doc["envs"]["hindsight"]
    assert row["apply"] == "report" and row["promote"] == {"after_clean_nights": 7}
    assert row["services"][0] == {"unit": "krew-hindsight.service", "scope": "user", "health": url,
                                  "health_wait_s": 120, "restart": "restart"}
    assert doc["health"]["lanes"]["hindsight"] == {"url": url} and doc["health"]["critical"] == ["hindsight"]
    assert doc["hindsight"]["env_file"] == str(tmp_path / "hindsight.env")
    probe = ("import importlib.util, json, sys\n"
             "s = importlib.util.spec_from_file_location('m', sys.argv[1]); m = importlib.util.module_from_spec(s)\n"
             "s.loader.exec_module(m)\n"
             "print(json.dumps({'venvs': sorted(m.VENVS), 'smoke': m.smoke('hindsight', m.VENVS['hindsight'][0]),"
             " 'mode': m.env_apply_mode('hindsight')}))\n")
    p = subprocess.run([sys.executable, "-c", probe, str(SCRIPT)], capture_output=True, text=True,
                       env=env, timeout=120)
    srv.shutdown()
    assert p.returncode == 0, p.stderr[-2000:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got == {"venvs": ["hindsight"], "smoke": True, "mode": "report"}


def test_register_env_refuses_an_env_it_cannot_test(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MS_",))}
    env.update(MS_SETTINGS=str(tmp_path / "ms.yaml"), MS_INTEGRATIONS="none", MS_OUT=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPT), "--register-env", "x", "--env-python", sys.executable],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode != 0 and "stays report-only" in (r.stderr + r.stdout)
    assert not (tmp_path / "ms.yaml").exists()


def test_pip_check_without_uv(monkeypatch):
    monkeypatch.setattr(ms, "UV", None)
    out = ("a 1.0 requires b, which is not installed.\n"
           "c 2.0 has requirement d>=2, but you have d 1.0.\nNo broken requirements found.\n")
    seen = []
    monkeypatch.setattr(ms, "run", lambda argv, **k: seen.append(argv) or (1, out, ""))
    got = ms._pip_check_set("/x/python")
    assert seen == [["/x/python", "-m", "pip", "check"]]
    assert got == {"a 1.0 requires b, which is not installed.", "c 2.0 has requirement d>=2, but you have d 1.0."}


def test_no_proc_is_unverified_never_unused(monkeypatch):
    real = ms.Path
    monkeypatch.setattr(ms, "Path", lambda p: real("/nonexistent/maps") if p == "/proc/self/maps" else real(p))
    monkeypatch.setattr(ms, "MS_UNUSED_GUARD", True)
    out = ms.guard_unused("/x/python", {"never_imported": ["six", "attrs"]})
    assert out["never_imported"] == []
    assert out["guarded"] == {"six": "unverified (no /proc on this platform)",
                              "attrs": "unverified (no /proc on this platform)"}


def test_no_canary_says_so(monkeypatch):
    monkeypatch.setattr(ms, "CANARY_ENV", "")
    assert ms.live_canary("anything") == (True, "no canary configured")
    monkeypatch.setattr(ms, "CANARY_ENV", "vllm-x")
    assert ms.live_canary("other") == (True, "n/a")


def test_an_install_without_the_house_profile_is_none(tmp_path):
    """The house loads its profile because config/ms-house.yaml sits in this repo; a shipped
    package does not carry it, so there the defaults are `none` with no setting at all."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MS_", "XDG_STATE_HOME"))}
    env.update(MS_PROFILE_FILE=str(tmp_path / "absent.yaml"), HOME=str(tmp_path), KREW_HOME=str(tmp_path / ".krew"))
    probe = ("import importlib.util, json, sys\n"
             "s = importlib.util.spec_from_file_location('m', sys.argv[1]); m = importlib.util.module_from_spec(s)\n"
             "s.loader.exec_module(m)\n"
             "print(json.dumps({'sources': m._S.SETTINGS['_sources'], 'roles': sorted(m.KNOWN_ROLES),"
             " 'out': str(m.OUT_ROOT), 'board': m._S.get('kanban.enabled')}))\n")
    p = subprocess.run([sys.executable, "-c", probe, str(SCRIPT)], capture_output=True, text=True,
                       env=env, timeout=120, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr[-2000:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got == {"sources": [], "roles": [], "out": str(tmp_path / ".krew" / "ms"),
                   "board": False}


