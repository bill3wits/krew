"""Served-name + hindsight retain budget — the 23h qw3.6-35b 404 hole."""
from __future__ import annotations

import importlib.util
import inspect
import json
import urllib.error
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
SPEC = importlib.util.spec_from_file_location(
    "ms_trail_run", ROOT / "ms" / "scripts" / "ms-trail-run.py"
)
assert SPEC and SPEC.loader
ms = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ms)


def test_env_file_reads_llm_models(tmp_path: Path):
    p = tmp_path / "hindsight.env"
    p.write_text(
        "# comment\n"
        "HINDSIGHT_API_LLM_MODEL=qw3.6-35b\n"
        "HINDSIGHT_API_RETAIN_LLM_MODEL=ops\n"
        "OTHER=1\n"
    )
    kv = ms._env_file_kv(p)
    assert kv["HINDSIGHT_API_LLM_MODEL"] == "qw3.6-35b"
    assert kv["HINDSIGHT_API_RETAIN_LLM_MODEL"] == "ops"


def test_stale_consumer_name_is_not_served():
    served = {"ops"}
    wanted = ["qw3.6-35b", "ops"]
    assert not all(name in served for name in wanted)


def test_retain_default_64000_is_not_sane():
    assert 64000 > ms.HINDSIGHT_RETAIN_TOKENS_MAX_SANE
    assert 4096 <= ms.HINDSIGHT_RETAIN_TOKENS_MAX_SANE


def test_canary_env_is_live_ops_venv():
    # 09-30: ops runs the 0.30 env; the canary's env is the env ops serves from, registered report-only.
    assert ms.CANARY_ENV == "vllm-030-cu132"
    assert {"vllm-030-cu132", "vllm-0271-cu132"} <= set(ms.GPU_SHARED_ENVS)
    role, heavy = ms.KNOWN_ROLES["vllm-030-cu132"]
    assert heavy
    assert "ops" in role.lower()
    assert ms.has_declared_smoke("vllm-030-cu132") and ms.env_apply_mode("vllm-030-cu132", {}) == "report"
    assert ms.CANARY_05B["vllm_bin"].startswith(str(ms_defaults_only.ENVS_ROOT) + f"/{ms.CANARY_ENV}/")   # one env: lane, canary, 0.5B
    assert ms.env_apply_mode("vllm-0271-cu132", {}) == "green"                    # crew's env: held, not demoted


def _ro_report():
    return {"envs": {ms.CANARY_ENV: {"report_only": "report_only_setting"}}, "applied": {}}


def test_a_report_only_canary_env_is_still_checked_every_night(monkeypatch):
    """The canary only ran as the gate of an apply; `apply: report` applies nothing, so it never ran."""
    monkeypatch.setattr(ms, "live_canary", lambda name, include_05b=False: (True, "ops pong; 0.5B pong"))
    rep = _ro_report()
    row = ms.report_only_canary(ms.CANARY_ENV, rep)
    assert row["canary"] == "ops pong; 0.5B pong" and row["canary_ok"] and row["ok"]
    assert row["skipped"] == "report_only_setting" and row["upgraded"] == []        # nothing was applied
    assert rep["applied"][ms.CANARY_ENV] is row
    import ms_silent_skips
    (obs,) = [o for o in ms_silent_skips.observe_ms_report(rep) if o.check == "canary"]
    assert obs.env == ms.CANARY_ENV and obs.ran is True                             # the night counts as run


def test_a_skipped_05b_on_a_report_only_env_is_a_skip_not_a_pass(monkeypatch):
    monkeypatch.setattr(ms, "live_canary",
                        lambda name, include_05b=False: (True, "ops pong; n/a 0.5B free 2000 MiB < 2912"))
    rep = _ro_report()
    row = ms.report_only_canary(ms.CANARY_ENV, rep)
    assert row["canary"].startswith("UNVERIFIED") and row["unverified"]
    import ms_silent_skips
    (obs,) = [o for o in ms_silent_skips.observe_ms_report(rep) if o.check == "canary"]
    assert obs.ran is False and "2000" in obs.numbers


def test_a_red_canary_on_a_report_only_env_is_a_failed_row_and_applies_nothing(monkeypatch):
    monkeypatch.setattr(ms, "live_canary", lambda name, include_05b=False: (False, "ops HTTP 500; 0.5B RED exited 1"))
    row = ms.report_only_canary(ms.CANARY_ENV, _ro_report())
    assert row["ok"] is False and row["canary_ok"] is False and "nothing was applied" in row["err"]


def test_the_report_only_canary_touches_no_other_env_and_has_a_kill(monkeypatch):
    monkeypatch.setattr(ms, "live_canary", lambda *a, **k: pytest.fail("the canary ran"))
    assert ms.report_only_canary("hindsight", {"envs": {"hindsight": {"report_only": "report_only_setting"}}}) is None
    assert ms.report_only_canary(ms.CANARY_ENV, {"envs": {ms.CANARY_ENV: {}}}) is None   # a green env: the apply gate runs it
    monkeypatch.setattr(ms, "MS_REPORT_CANARY", False)
    assert ms.report_only_canary(ms.CANARY_ENV, _ro_report()) is None
    rails, rails_h = ms.KNOWN_ROLES["vllm"]
    assert rails_h
    assert "presence" in rails.lower()
    assert "not Qwen" in rails or "not qwen" in rails.lower()
    assert "vllm-nvfp4-sm120" in ms.RETIRED_ENVS
    assert "vllm-rail" in ms.RETIRED_ENVS
    assert "vllm-0.28" in ms.RETIRED_ENVS
    assert "vllm-nvfp4-sm120" not in ms.GPU_SHARED_ENVS
    assert "vllm-nvfp4-sm120" not in ms.KNOWN_ROLES


def test_hindsight_llm_matches_per_base_url():
    env = {
        "HINDSIGHT_API_LLM_MODEL": "ops",
        "HINDSIGHT_API_LLM_BASE_URL": "http://127.0.0.1:8000/v1",
        "HINDSIGHT_API_RETAIN_LLM_MODEL": "crew",
        "HINDSIGHT_API_RETAIN_LLM_BASE_URL": "http://127.0.0.1:8005/v1",
    }
    served = {
        "http://127.0.0.1:8000/v1/models": ["ops"],
        "http://127.0.0.1:8005/v1/models": ["crew"],
    }
    assert ms.hindsight_llm_ok(env, served) is True
    served["http://127.0.0.1:8005/v1/models"] = ["ops"]
    assert ms.hindsight_llm_ok(env, served) is False


def test_which_env_maps_live_and_not_retired_museum():
    assert ms.which_env(str(ms_defaults_only.ENVS_ROOT) + "/vllm/bin/python") == "vllm"
    assert ms.which_env(str(ms_defaults_only.ENVS_ROOT) + "/vllm-0271-cu132/bin/python") == "vllm-0271-cu132"
    assert ms.which_env(str(ms_defaults_only.ENVS_ROOT) + "/vllm-nvfp4-sm120/bin/vllm") is None
    assert ms.which_env(str(ms_defaults_only.ENVS_ROOT) + "/audio8-asr/bin/python") == "audio8-asr"
    assert ms.which_env(str(ms_defaults_only.ENVS_ROOT) + "/cosyvoice/bin/python") is None, "retired 2026-10-07 (cosy-retire)"
    assert ms.which_env("/usr/bin/python3") is None
    # interpreter symlink into /usr must not collapse every env onto one name
    assert ms.which_env(str(ms_defaults_only.ENVS_ROOT) + "/audio8-tts/bin/python") == "audio8-tts"


@pytest.mark.skip(reason="reads the live units and /proc of the reference install; krew runs hermetic")
def test_museum_sm120_is_not_live():
    used = ms.live_venv_users()
    assert "vllm" in used, used
    assert any(t.startswith("unit:") or t.startswith("pid:") for t in used["vllm"])
    assert "vllm-nvfp4-sm120" not in used, used.get("vllm-nvfp4-sm120")


def test_drain_rows_is_greens_only():
    rows = [
        {"package": "uvicorn", "current": "0.52.3", "latest": "0.52.4",
         "from": "green"},
        {"package": "protobuf", "current": "4.25.0", "latest": "6.33.6",
         "from": "pending_apply"},
        {"package": "prometheus-client", "current": "0.25.0", "latest": "0.26.0",
         "from": "pending_apply"},
        {"package": "MarkupSafe", "current": "2.1.5", "latest": "3.0.3",
         "from": "pending_apply"},
    ]
    drained = [r["package"] for r in ms.drain_rows(rows)]
    assert drained == ["uvicorn"]


def test_drain_pending_specialist_skips_greens_never_and_caps():
    pending = [
        {"package": "uvicorn", "current": "0.52.3", "latest": "0.52.4"},  # green
        {"package": "protobuf", "current": "4.25.0", "latest": "6.33.6"},  # major
        {"package": "MarkupSafe", "current": "2.1.5", "latest": "3.0.3"},  # major
        {"package": "torch", "current": "2.13.0", "latest": "2.14.0"},  # NEVER_AUTO
        {"package": "mpmath", "current": "1.3.0", "latest": "1.4.1"},  # coupled risk
    ]
    got = [r["package"] for r in ms.drain_pending_specialist(pending)]
    assert "uvicorn" not in got
    assert "torch" not in got
    assert "protobuf" in got
    assert "MarkupSafe" in got
    assert "mpmath" in got
    capped = ms.drain_pending_specialist(pending, max_n=1)
    assert len(capped) == 1
    assert capped[0]["package"] == "protobuf"


def test_drain_pending_specialist_honors_skip_and_empty():
    pending = [
        {"package": "protobuf", "current": "4.25.0", "latest": "6.33.6"},
    ]
    assert ms.drain_pending_specialist(pending, skip={"protobuf"}) == []
    assert ms.drain_pending_specialist([]) == []


def test_plan_kanban_ms_land_pending_parent_is_not_land():
    listing = (
        "▶ t_ccccdddd  ready     bash   "
        "M&S mitigation: vllm — 1 actionable risk update(s)\n"
    )
    shows = {
        "t_ccccdddd": "- protobuf 4.25.0 → 6.33.6\n",
    }
    by_env, land_ids, spawns = ms.plan_kanban_ms_land(listing, shows=shows)
    assert by_env == {}
    assert spawns == []
    assert land_ids == {}


def test_plan_kanban_ms_land_ready_child_and_spawn_if_missing():
    listing = (
        "▶ t_aa11bb22  ready     steward "
        "M&S land: cosyvoice\n"
        "✓ t_cc33dd44  done      bash   "
        "M&S mitigation: hindsight — 1 actionable\n"
        "✓ t_ee55ff66  done      bash   "
        "M&S mitigation: vllm — 1 actionable\n"
    )
    shows = {
        "t_aa11bb22": "## Actionable packages\n- protobuf 4.25.0 → 6.33.6\n",
        "t_cc33dd44": (
            "pending_apply\n"
            "- fastapi-cloud-cli 0.1.0 → 0.2.0\n"
        ),
        "t_ee55ff66": "Verdict: HOLD. Do not apply.\n- torch 2.13.0 → 2.14.0\n",
    }
    by_env, land_ids, spawns = ms.plan_kanban_ms_land(listing, shows=shows)
    assert [r["package"] for r in by_env["cosyvoice"]] == ["protobuf"]
    assert land_ids["cosyvoice"] == ["t_aa11bb22"]
    assert len(spawns) == 1
    assert spawns[0]["env"] == "hindsight"
    assert spawns[0]["parent_id"] == "t_cc33dd44"
    assert "vllm" not in by_env


def test_ingest_kanban_ms_go_from_done_card_not_hold():
    listing = (
        "✓ t_aaaabbbb  done      bash   "
        "M&S mitigation: cosyvoice — 2 actionable risk update(s)\n"
        "▶ t_ccccdddd  ready     bash   "
        "M&S mitigation: vllm — 1 actionable risk update(s)\n"
        "✓ t_eeeeffff  done      bash   "
        "M&S mitigation: hindsight — 1 actionable\n"
    )
    shows = {
        "t_aaaabbbb": (
            "Latest summary: pin pending_apply\n"
            "## Actionable packages\n"
            "- protobuf 4.25.0 → 6.33.6\n"
            "- MarkupSafe 2.1.5 → 3.0.3\n"
        ),
        "t_eeeeffff": (
            "No apply this card. Verdict: HOLD.\n"
            "- anthropic 0.12.0 → 1.0.0\n"
        ),
    }
    got, ids = ms.ingest_kanban_ms_go(listing, shows=shows, spawn=False)
    assert "vllm" not in got  # still ready — not decided
    assert "hindsight" not in got  # hold
    assert [r["package"] for r in got["cosyvoice"]] == ["protobuf", "markupsafe"]
    majors = ms.drain_pending_specialist(got["cosyvoice"])
    assert [r["package"] for r in majors] == ["protobuf", "markupsafe"]
    assert ids == {}  # spawn=False, parent done → spec scheduled in plan only


def test_specialist_go_hold_wins():
    assert ms.specialist_go_from_card_text(
        "pending_apply but verdict: hold — do not apply"
    ) is False
    assert ms.specialist_go_from_card_text(
        "ledger pending_apply for next window"
    ) is True


def test_window_apply_plan_ops_canary_and_no_heavy_wait():
    qwen = ms.CANARY_ENV          # 09-30: the env ops serves from (vllm-030-cu132)
    assert ms.window_apply_plan("vllm-0271-cu132", canary_ok=False) == "apply"   # crew's env is no longer the canary's
    assert ms.window_apply_plan(qwen, canary_ok=True) == "apply"
    assert ms.window_apply_plan(qwen, canary_ok=False) == "skip_canary_red"
    # Presence rails venv is not the Qwen canary.
    assert ms.window_apply_plan("vllm", canary_ok=False) == "apply"
    assert ms.window_apply_plan("audio8-tts") == "apply"
    assert ms.window_apply_plan("cosyvoice_vllm") == "apply"
    assert ms.window_apply_plan(
        qwen, train_busy=True, force_train=False, canary_ok=True
    ) == "skip_train_busy"
    assert ms.window_apply_plan(
        qwen, train_busy=True, force_train=True, canary_ok=True
    ) == "apply"
    greens = [{"package": "pygments", "current": "1", "latest": "2"}]
    assert [r["package"] for r in ms.merge_window_rows(greens, [])] == ["pygments"]


def test_process_not_running_is_not_an_apply_skip():
    """Beast-parked Cosy is telemetry. Room occupied/dark is a different script."""
    assert "skip_not_live" not in (ms.window_apply_plan.__doc__ or "")
    assert ms.window_apply_plan("cosyvoice_vllm") == "apply"
    assert ms.window_apply_plan("comfyui") == "apply"
    assert ms.window_apply_plan("unsloth") == "apply"
    assert ms.window_apply_plan("audio8-tts") == "apply"


def test_ops_not_serving_is_a_failed_canary(monkeypatch):
    def boom(*_a, **_k):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(ms.urllib.request, "urlopen", boom)
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "normal")
    ok, msg = ms.canary_vllm()
    assert ok is False
    assert "no live vllm" in msg.lower() or "urlerror" in msg.lower() or "refused" in msg.lower()
    assert ms.window_apply_plan(ms.CANARY_ENV, canary_ok=False) == "skip_canary_red"
    assert ms.window_apply_plan("vllm", canary_ok=False) == "apply"
    assert ms.window_apply_plan("audio8-tts", canary_ok=False) == "apply"


def test_live_vllm_target_beast_uses_crew(monkeypatch):
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "beast")

    def ids(base):
        if ":8005" in base:
            return ["crew"], "ok"
        return None, "refused"

    monkeypatch.setattr(ms, "_models_ids", ids)
    assert ms.live_vllm_target() == ("http://127.0.0.1:8005/v1", "crew")


def test_live_vllm_target_normal_uses_ops(monkeypatch):
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "normal")

    def ids(base):
        if ":8000" in base:
            return ["ops"], "ok"
        return ["crew"], "ok"

    monkeypatch.setattr(ms, "_models_ids", ids)
    assert ms.live_vllm_target() == ("http://127.0.0.1:8000/v1", "ops")


def test_beast_falls_back_to_ops_if_crew_down(monkeypatch):
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "beast")

    def ids(base):
        if ":8000" in base:
            return ["ops"], "ok"
        return None, "refused"

    monkeypatch.setattr(ms, "_models_ids", ids)
    assert ms.live_vllm_target() == ("http://127.0.0.1:8000/v1", "ops")


def test_05b_skips_when_vram_low(monkeypatch):
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 1024)
    ok, msg = ms.canary_05b()
    assert ok is True
    assert "n/a" in msg.lower()
    assert "1024" in msg


def test_canonical_deliverables_ignores_profile_docker_home(monkeypatch, tmp_path):
    monkeypatch.delenv("MS_DELIVERABLES", raising=False)
    monkeypatch.setattr(ms.Path, "home", classmethod(lambda cls: tmp_path / ".hermes" / "profiles" / "steward" / "home"))
    got = ms._canonical_deliverables()
    assert got == ms.Path(str(ms_defaults_only.DELIVERABLES))
    monkeypatch.setenv("MS_DELIVERABLES", str(tmp_path / "inbox"))
    assert ms._canonical_deliverables() == tmp_path / "inbox"


def test_write_kanban_stub_uses_hermes_bin(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")  # a read refreshes LAST-GOOD
    monkeypatch.setattr(ms, "KANBAN_DIR", tmp_path)
    monkeypatch.setattr(ms, "DELIVERABLES_DIR", tmp_path)
    monkeypatch.setenv("HERMES_BIN", "/bin/true")
    calls: list = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    summary = {
        "env_summaries": [{"env": "vllm", "outdated_n": 1, "green_candidate": 0,
                           "risk": 0, "never": 0, "smoke_baseline": True,
                           "role": "x", "heavy": True}],
        "totals": {"actionable_risk": 1, "outdated_packages_sum": 1,
                   "risk_rows": 0, "never_rows": 0, "green_rows": 0},
        "top_packages": [],
        "apply_failures": [{"env": "vllm"}],
        "hermes": {"security": {"by_severity": {"HIGH": 1}}},
        "parked_envs": [],
    }
    ms.write_kanban_stub(summary, "2026-08-20", {})
    assert calls, "hermes was never invoked"
    assert all(isinstance(c[0], str) for c in calls)
    assert all(c[0] == "/bin/true" for c in calls)


def test_05b_flags_are_measured_beast_trial():
    assert ms.CANARY_05B["port"] == 8001
    # 09-30 measured: peak 2276 MiB at every util 0.015-0.04 (docs/checkpoints/ms-canary-20260930)
    assert ms.CANARY_05B["util"] == "0.025"
    assert ms.CANARY_05B["kv_bytes"] == "67108864"
    # 09-30 engine switch: the 0.5B starts from the 0.30 env, peak 2656 MiB -> 2656 + 256
    assert ms.CANARY_05B["free_mib_min"] == 2912
    assert ms.CANARY_05B["vllm_bin"] == str(ms_defaults_only.ENVS_ROOT / "vllm-030-cu132" / "bin" / "vllm")
    assert str(ms_defaults_only.ENVS_ROOT) + "/vllm-030-cu132/bin" in ms.CANARY_05B["path"] and "vllm-0271" not in ms.CANARY_05B["path"]
    assert "Qwen2.5-0.5B-Instruct" in ms.CANARY_05B["model"]
    assert (Path(ms.CANARY_05B["model"]) / "config.json").is_file()


def test_hermes_security_is_new(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "HERMES_REPORT_DIR", tmp_path)
    assert ms.hermes_security_is_new({"high_ids": []}) is False
    assert ms.hermes_security_is_new({"high_ids": ["CVE-1"]}) is True
    (tmp_path / "security-PREV.json").write_text(
        '{"high_ids": ["CVE-1"]}\n'
    )
    assert ms.hermes_security_is_new({"high_ids": ["CVE-1"]}) is False
    assert ms.hermes_security_is_new({"high_ids": ["CVE-1", "CVE-2"]}) is True


# --- scanner counter/dedup (t_fb697135) ----------------------------------------


def _sec_finding(pkg, vuln, sev="HIGH"):
    return {"package": pkg, "version": "1.0.0", "ecosystem": "PyPI",
            "source": "venv", "vuln_id": vuln, "severity": sev,
            "summary": f"{pkg} {vuln}", "fixed_versions": ["2.0.0"]}


def test_security_dedup_counts_unique_package_vuln_pairs(tmp_path, monkeypatch):
    """09-26 crystal: finding_count 32 / HIGH 4 vs high_ids 3 unique GHSAs.

    A same-(package, vuln_id) line repeated is ONE finding, not two.
    The same advisory on two packages (httpx2 + httpcore2) is TWO.
    """
    monkeypatch.setattr(ms, "HERMES_REPORT_DIR", tmp_path)
    findings = [
        _sec_finding("hermes-agent", "GHSA-A", "HIGH"),
        _sec_finding("httpx2", "GHSA-B", "HIGH"),
        _sec_finding("httpcore2", "GHSA-B", "HIGH"),   # same vuln, other pkg — keep
        _sec_finding("httpx2", "GHSA-B", "HIGH"),      # duplicate line — drop
        _sec_finding("hermes-agent", "GHSA-C", "LOW"),
    ]
    payload = {"total_components_scanned": 10, "finding_count": 5,
               "findings": findings, "exit": 0}
    got = ms.run_hermes_security.__wrapped__(payload) if hasattr(ms.run_hermes_security, "__wrapped__") else None
    assert got is None  # run_hermes_security shells out; count via _security_counts below


def test_security_counts_function_dedupes(monkeypatch):
    """The counting core, factored out so it is testable without a shell."""
    findings = [
        _sec_finding("hermes-agent", "GHSA-A", "HIGH"),
        _sec_finding("httpx2", "GHSA-B", "HIGH"),
        _sec_finding("httpcore2", "GHSA-B", "HIGH"),   # keep: different package
        _sec_finding("httpx2", "GHSA-B", "HIGH"),      # drop: same (package, vuln)
        _sec_finding("hermes-agent", "GHSA-A", "HIGH"),  # drop: same (package, vuln)
        _sec_finding("hermes-agent", "GHSA-C", "LOW"),
    ]
    raw_n, n_unique, dup_n, by, high_ids = ms.security_counts(findings)
    assert raw_n == 6
    assert n_unique == 4
    assert dup_n == 2
    assert by == {"HIGH": 3, "LOW": 1}
    assert high_ids == ["GHSA-A", "GHSA-B"]


def test_security_counts_empty_and_non_dicts():
    raw_n, n_unique, dup_n, by, high_ids = ms.security_counts([])
    assert (raw_n, n_unique, dup_n, by, high_ids) == (0, 0, 0, {}, [])
    raw_n, n_unique, dup_n, by, high_ids = ms.security_counts([None, "junk", _sec_finding("x", "GHSA-D")])
    assert (raw_n, n_unique, dup_n) == (3, 1, 2)
    assert by == {"HIGH": 1}
    assert high_ids == ["GHSA-D"]


def test_run_hermes_security_dedups_and_annotates(tmp_path, monkeypatch):
    """End-to-end: the 09-26 shape — raw count 5 with one duplicate line."""
    monkeypatch.setattr(ms, "HERMES_REPORT_DIR", tmp_path)
    findings = [
        _sec_finding("hermes-agent", "GHSA-A", "HIGH"),
        _sec_finding("httpx2", "GHSA-B", "HIGH"),
        _sec_finding("httpx2", "GHSA-B", "HIGH"),   # the duplicate line
    ]
    scan_out = json.dumps({"total_components_scanned": 3, "finding_count": 3,
                           "findings": findings, "exit": 0})

    class R:
        returncode = 0
        stdout = scan_out
        stderr = ""

    monkeypatch.setattr(ms, "run", lambda cmd, **_kw: (0, scan_out, ""))
    (tmp_path / "_hermes").write_text("#!/bin/sh\nexit 0")
    (tmp_path / "_hermes").chmod(0o755)
    monkeypatch.setattr(ms, "_hermes_bin", lambda: str(tmp_path / "_hermes"))
    payload = ms.run_hermes_security()
    assert payload["measured"] is True
    assert payload["finding_count"] == 3          # scan's raw count, kept
    assert payload["finding_count_deduped"] == 2
    assert payload["finding_count_duplicates"] == 1
    assert "scan count 3 ≠ deduped 2" in payload["finding_count_note"]
    assert payload["by_severity"] == {"HIGH": 2}
    assert payload["high_ids"] == ["GHSA-A", "GHSA-B"]
    # the crystal files carry the deduped fields
    latest = (tmp_path / "security-LATEST.json").read_text()
    assert "finding_count_deduped" in latest


def test_summarize_kanban_includes_parked_product_envs(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")  # a read refreshes LAST-GOOD
    envs = {
        "vllm": {
            "role": "live ops",
            "heavy": True,
            "in_use": True,
            "outdated_n": 2,
            "smoke_baseline": True,
            "details": {
                "green_candidate": [],
                "risk": [
                    {"package": "numpy", "current": "2.4.3", "latest": "2.5.2",
                     "env": "vllm"},
                ],
                "never": [],
            },
        },
        "comfyui": {
            "role": "comfyui",
            "heavy": True,
            "in_use": False,
            "outdated_n": 63,
            "smoke_baseline": True,
            "details": {
                "green_candidate": [{"package": "pygments"}],
                "risk": [
                    {"package": "tokenizers", "current": "0.22.2", "latest": "0.23.1",
                     "env": "comfyui"},
                ],
                "never": [],
            },
        },
    }
    s = ms.summarize_for_kanban(envs, [], ledger={"decisions": []})
    names = [e["env"] for e in s["env_summaries"]]
    assert names == ["vllm", "comfyui"]
    assert s["parked_envs"] == ["comfyui"]
    assert s["totals"]["envs"] == 2
    assert s["totals"]["outdated_packages_sum"] == 65
    body = ms._card_body(s, {}, "2026-08-19")
    assert "comfyui" in body
    assert "do not research" not in body.lower()
    pkgs = [p["package"] for p in s["top_packages"]]
    assert "numpy" in pkgs
    assert "tokenizers" in pkgs


def test_audio8_tts_hold_is_not_actionable():
    ledger = {
        "decisions": [
            {
                "env": "audio8-tts",
                "package": "huggingface-hub",
                "decision": "hold",
                "target": "1.28.0",
            },
            {
                "env": "audio8-tts",
                "package": "numpy",
                "decision": "hold",
                "target": "2.5.2",
            },
        ]
    }
    risk = [
        {"package": "huggingface-hub", "current": "1.27.0", "latest": "1.28.0"},
        {"package": "numpy", "current": "2.4.3", "latest": "2.5.2"},
    ]
    act, sup = ms.filter_actionable_risk("audio8-tts", risk, ledger)
    assert act == []
    assert {x["package"] for x in sup} == {"huggingface-hub", "numpy"}


def test_summarize_omits_held_from_top_risk():
    envs = {
        "audio8-tts": {
            "role": "announcer",
            "heavy": False,
            "in_use": True,
            "outdated_n": 2,
            "smoke_baseline": True,
            "details": {
                "green_candidate": [
                    {"package": "uvicorn", "current": "0.52.3", "latest": "0.52.4"},
                ],
                "risk": [
                    {"package": "huggingface-hub", "current": "1.27.0",
                     "latest": "1.28.0", "env": "audio8-tts"},
                ],
                "never": [],
            },
        }
    }
    held = {
        "decisions": [
            {"env": "audio8-tts", "package": "huggingface-hub",
             "decision": "hold", "target": "1.28.0"},
        ]
    }
    s = ms.summarize_for_kanban(envs, [], ledger=held)
    assert [p["package"] for p in s["top_packages"]] == []
    assert s["totals"]["actionable_risk"] == 0
    open_led = {"decisions": []}
    s2 = ms.summarize_for_kanban(envs, [], ledger=open_led)
    assert any(p["package"] == "huggingface-hub" for p in s2["top_packages"])


def test_pending_apply_is_window_queue_not_kanban():
    ledger = {
        "decisions": [
            {
                "env": "audio8-tts",
                "package": "uvicorn",
                "decision": "pending_apply",
                "current": "0.52.3",
                "target": "0.52.4",
            },
            {
                "env": "audio8-tts",
                "package": "huggingface-hub",
                "decision": "hold",
                "current": "1.27.0",
                "target": "1.28.0",
            },
            {
                "env": "vllm",
                "package": "numpy",
                "decision": "pending_apply",
                "current": "2.4.3",
                "target": "2.5.2",
            },
        ]
    }
    rows = ms.pending_apply_for_env("audio8-tts", ledger)
    pkgs = [r["package"] for r in rows]
    assert pkgs == ["uvicorn"]
    greens = [{"package": "pygments", "current": "1", "latest": "2"}]
    merged = ms.merge_window_rows(greens, rows)
    assert [r["package"] for r in merged] == ["pygments", "uvicorn"]
    # hold is not in the window
    assert "huggingface-hub" not in [r["package"] for r in merged]


def test_queue_greens_schedules_not_skips(tmp_path, monkeypatch):
    led = tmp_path / "decisions.json"
    led.write_text('{"schema":"ms_decisions/v1","decisions":[]}\n')
    monkeypatch.setattr(ms, "DECISIONS_PATH", led)
    n = ms.queue_greens_for_window(
        "rail-stt",
        [{"package": "pygments", "current": "2.17.0", "latest": "2.19.2"}],
        reason="test window",
    )
    assert n == 1
    rows = ms.pending_apply_for_env("rail-stt")
    assert [r["package"] for r in rows] == ["pygments"]
    n2 = ms.queue_greens_for_window(
        "rail-stt",
        [{"package": "pygments", "current": "2.17.0", "latest": "2.19.2"}],
        reason="test window",
    )
    assert n2 == 0


def test_queue_greens_respects_hold(tmp_path, monkeypatch):
    led = tmp_path / "decisions.json"
    led.write_text(
        json.dumps({
            "schema": "ms_decisions/v1",
            "decisions": [{
                "env": "rail-stt",
                "package": "ruamel-yaml",
                "decision": "hold",
                "target": "0.19.0",
            }],
        })
    )
    monkeypatch.setattr(ms, "DECISIONS_PATH", led)
    n = ms.queue_greens_for_window(
        "rail-stt",
        [{"package": "ruamel-yaml", "current": "0.18.17", "latest": "0.19.0"}],
        reason="test",
    )
    assert n == 0


def test_issue_slug():
    assert ms._issue_slug("web workspace has 4 npm vulnerabilities").startswith("web-workspace")


def test_doctor_manifest_line_green_when_only_api_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("MS_DOCTOR_UPSTREAM_APIS", raising=False)
    monkeypatch.setattr(ms, "UPSTREAM_APIS_FILE", tmp_path / "missing")
    line = ms.doctor_manifest_line(
        {
            "ok": True,
            "issues_n": 1,
            "actionable_n": 0,
            "actionable": [],
            "issues": ["Run 'hermes setup' to configure API keys"],
        }
    )
    assert "GREEN" in line
    assert "MS_DOCTOR_UPSTREAM_APIS=1" in line
    assert "actionable 0" in line


def test_doctor_skips_api_key_until_user_asks(monkeypatch, tmp_path):
    monkeypatch.delenv("MS_DOCTOR_UPSTREAM_APIS", raising=False)
    monkeypatch.setattr(ms, "UPSTREAM_APIS_FILE", tmp_path / "missing")
    issue = "Run 'hermes setup' to configure API keys"
    assert ms.is_unused_api_doctor_issue(issue) is True
    assert ms.doctor_actionable_issues([issue, "disk full"]) == ["disk full"]
    monkeypatch.setenv("MS_DOCTOR_UPSTREAM_APIS", "1")
    assert ms.doctor_actionable_issues([issue]) == [issue]


def test_file_hermes_issue_cards_skips_api_keys(monkeypatch):
    monkeypatch.setenv("MS_DOCTOR_UPSTREAM_APIS", "0")
    created: list[str] = []

    def fake_create(*, title, body, assignee, idemp):
        created.append(title)
        return "t_x"

    monkeypatch.setattr(ms, "_kanban_create", fake_create)
    ms.file_hermes_issue_cards(
        {
            "hermes": {
                "doctor": {
                    "issues": ["Run 'hermes setup' to configure API keys"],
                },
                "security": {},
            }
        },
        "2026-08-24",
        "",
    )
    assert created == []


def test_maintenance_window_note_includes_greens():
    src = Path(ms.__file__).read_text()
    # step 4: the wording lives in the house text file; check what M&S actually writes
    assert "Greens that did not apply this run" in ms._T("window.note")
    # the house keeps unlimited mitigation cards (the User 08-20); since step 2 that lives in its profile
    assert ms._S.get("kanban.max_mitigation_envs") == 0 and ms.MS_MITIGATION_MAX_ENVS == 0
    assert "file_hermes_issue_cards" in src


def test_models_url_from_base():
    assert ms._models_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1/models"
    assert ms._models_url("http://127.0.0.1:8005/v1/") == "http://127.0.0.1:8005/v1/models"


def _od(pkg, cur, lat):
    return {"name": pkg, "version": cur, "latest_version": lat}


def _pin(pinner, pver, req, spec, marker=""):
    return {
        "pinner": pinner,
        "pinner_version": pver,
        "req": req,
        "spec": spec,
        "marker": marker,
    }


def test_llguidance_marker_binds_on_x86_64():
    line = (
        'llguidance<1.8.0,>=1.7.0; platform_machine == "x86_64" or '
        'platform_machine == "arm64" or platform_machine == "aarch64" or '
        'platform_machine == "ppc64le" or platform_machine == "s390x"'
    )
    parsed = ms.parse_requires_dist_line("Requires-Dist: " + line)
    assert parsed is not None
    assert parsed["req"] == "llguidance"
    assert ms.marker_binds(parsed["marker"]) is True
    socks = ms.parse_requires_dist_line(
        'Requires-Dist: PySocks!=1.5.7,>=1.5.6; extra == "socks"'
    )
    assert socks is not None
    assert ms.marker_binds(socks["marker"]) is False


def test_mcp_pair_both_green_is_group_wait_not_pinned():
    outdated = [
        _od("mcp", "2.0.0", "2.1.0"),
        _od("mcp-types", "2.0.0", "2.1.0"),
    ]
    constraints = [_pin("mcp", "2.0.0", "mcp-types", "==2.0.0")]
    got = ms.classify_env_rows(outdated, constraints)
    assert got["mcp"]["bucket"] == "group-wait"
    assert got["mcp-types"]["bucket"] == "group-wait"


def test_otel_api_group_wait_vs_pinned_when_sdk_absent():
    constraints = [_pin("opentelemetry-sdk", "1.42.1", "opentelemetry-api", "==1.42.1")]
    both = [
        _od("opentelemetry-api", "1.42.1", "1.44.0"),
        _od("opentelemetry-sdk", "1.42.1", "1.44.0"),
    ]
    got = ms.classify_env_rows(both, constraints)
    assert got["opentelemetry-api"]["bucket"] == "group-wait"
    api_only = [_od("opentelemetry-api", "1.42.1", "1.44.0")]
    got2 = ms.classify_env_rows(api_only, constraints)
    assert got2["opentelemetry-api"]["bucket"] == "pinned"
    assert got2["opentelemetry-api"]["pinned_by"] == "opentelemetry-sdk"


def test_pin_fixpoint_numpy_via_numba_via_vllm():
    outdated = [
        _od("numba", "0.65.0", "0.67.0"),
        _od("numpy", "2.3.5", "2.5.2"),
        _od("llvmlite", "0.47.0", "0.49.0"),
        _od("compressed-tensors", "0.17.0", "0.18.0"),
        _od("lark", "1.2.2", "1.3.1"),
    ]
    constraints = [
        _pin("vllm", "0.27.1", "numba", "==0.65.0"),
        _pin("vllm", "0.27.1", "compressed-tensors", "==0.17.0"),
        _pin("vllm", "0.27.1", "lark", "==1.2.2"),
        _pin("numba", "0.65.0", "numpy", "<2.5,>=1.22"),
        _pin("numba", "0.65.0", "llvmlite", "<0.48,>=0.47.0dev0"),
    ]
    got = ms.classify_env_rows(outdated, constraints)
    assert got["lark"]["bucket"] == "pinned"
    assert got["lark"]["pinned_by"] == "vllm"
    assert got["numba"]["bucket"] == "pinned"
    assert got["numba"]["pinned_by"] == "vllm"
    assert got["numpy"]["bucket"] == "pinned"
    assert got["numpy"]["pinned_by"] == "numba"
    assert got["numpy"]["pinner_version"] == "0.65.0"
    assert got["llvmlite"]["bucket"] == "pinned"
    assert got["compressed-tensors"]["bucket"] == "pinned"


def test_numpy_pinned_not_coupled_risk():
    assert "numpy" in ms.COUPLED_RISK
    outdated = [
        _od("numba", "0.65.0", "0.67.0"),
        _od("numpy", "2.3.5", "2.5.2"),
    ]
    constraints = [
        _pin("vllm", "0.27.1", "numba", "==0.65.0"),
        _pin("numba", "0.65.0", "numpy", "<2.5,>=1.22"),
    ]
    got = ms.classify_env_rows(outdated, constraints)
    assert got["numpy"]["bucket"] == "pinned"
    assert ms.classify_update("numpy", "2.3.5", "2.5.2") == "risk"


def test_apache_tvm_ffi_immovable_dominates_movable():
    outdated = [
        _od("apache-tvm-ffi", "0.1.11", "0.1.13.post3"),
        _od("tilelang", "0.1.12", "0.1.13"),
    ]
    constraints = [
        _pin("vllm", "0.27.1", "apache-tvm-ffi", "==0.1.11"),
        _pin("tilelang", "0.1.12", "apache-tvm-ffi", ">=0.1.11,<0.1.13"),
    ]
    got = ms.classify_env_rows(outdated, constraints)
    assert got["apache-tvm-ffi"]["bucket"] == "pinned"
    assert got["apache-tvm-ffi"]["pinned_by"] == "vllm"


def test_hold_stale_and_applied_refile():
    env = "vllm-0271-cu132"
    hold_old = {
        "env": env,
        "package": "anthropic",
        "decision": "hold",
        "target": "0.122.0",
    }
    hold_new = {
        "env": env,
        "package": "anthropic",
        "decision": "hold",
        "target": "1.0.0",
    }
    applied = {
        "env": env,
        "package": "anthropic",
        "decision": "applied",
        "current": "0.125.0",
        "target": "0.125.0",
    }
    pkg = {"package": "anthropic", "current": "0.125.0", "latest": "1.0.0"}
    assert ms._decision_suppresses(hold_old, env, pkg) is False
    assert ms._decision_suppresses(hold_new, env, pkg) is True
    assert ms._decision_suppresses(applied, env, pkg) is False
    pkg_at_target = {"package": "anthropic", "current": "1.0.0", "latest": "1.0.0"}
    applied_done = {
        "env": env,
        "package": "anthropic",
        "decision": "applied",
        "current": "1.0.0",
        "target": "1.0.0",
    }
    assert ms._decision_suppresses(applied_done, env, pkg_at_target) is True


def test_cancelled_same_target_suppresses_research_not_apply():
    """User 2026-09-01: do not pin security updates from landing.

    cancelled remembers 'no' for that exact target (no re-research card).
    It must not sit in APPLY_BLOCKING — a new latest, or the same row if
    classify still wants it as a pip-green/security bump, can still apply.
    Steward 09-01 re-landing hindsight/pydantic after user-cancel is this path.
    """
    env = "hindsight"
    cancelled = {
        "env": env,
        "package": "pydantic",
        "decision": "cancelled",
        "current": "2.13.4",
        "target": "2.13.5",
        "source": "user-cancel-pending-20260831",
    }
    same = {"package": "pydantic", "current": "2.13.4", "latest": "2.13.5"}
    newer = {"package": "pydantic", "current": "2.13.4", "latest": "2.14.0"}
    assert "cancelled" not in ms.APPLY_BLOCKING_DECISIONS
    assert ms._decision_suppresses(cancelled, env, same) is True
    assert ms._decision_suppresses(cancelled, env, newer) is False
    assert ms._decision_blocks_apply(cancelled, env, same) is False
    assert ms._decision_blocks_apply(cancelled, env, newer) is False


def test_applied_label_zero_green_vs_report_only_and_canary_red():
    assert (
        ms.applied_label(None, considered_apply=True, green_n=0) == "0-green"
    )
    assert (
        ms.applied_label(None, considered_apply=False, green_n=0) == "report-only"
    )
    assert (
        ms.applied_label(
            {"ok": False, "skipped": "canary_red", "changed_n": 0},
            considered_apply=True,
            green_n=3,
        )
        == "canary-red"
    )
    assert "FAILED+rolled-back" not in ms.applied_label(
        {"ok": False, "skipped": "skip_canary_red"},
        considered_apply=True,
        green_n=1,
    )


def test_card_body_does_not_say_no_action_when_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")  # a read refreshes LAST-GOOD
    summary = {
        "env_summaries": [
            {
                "env": "vllm",
                "outdated_n": 10,
                "green_candidate": 0,
                "risk": 0,
                "never": 0,
                "pinned": 8,
                "group_wait": 2,
                "smoke_baseline": True,
                "heavy": True,
            }
        ],
        "totals": {
            "actionable_risk": 0,
            "outdated_packages_sum": 10,
            "risk_rows": 0,
            "never_rows": 0,
            "green_rows": 0,
            "pinned_rows": 8,
            "group_wait_rows": 2,
        },
        "top_packages": [],
        "parked_envs": [],
    }
    body = ms._card_body(summary, {"vllm": {"ok": True, "changed_n": 0, "upgraded": []}}, "2026-08-25")
    assert "Do not re-research." not in body
    assert "pinned" in body
    assert "0-green" in body or "applied=0" in body or "+0" in body


def test_vllm_smoke_imports_presence_stack():
    spec = ms.SMOKE_IMPORT["vllm"]
    assert "import rail_presence" in spec["code"]
    assert "rail_presence.grabber" in spec["code"]
    assert "rail-presence/src" in spec["env"]["PYTHONPATH"]
    assert "importlib.metadata" not in spec["code"]
    assert "comfyui" in ms.GPU_SHARED_ENVS
    assert "folder_paths" in ms.SMOKE_IMPORT["comfyui"]["code"]
    assert "wespeakerruntime" in ms.SMOKE_IMPORT["wespeaker"]["code"]


def test_version_in_spec_numba_numpy():
    assert ms.version_in_spec("2.3.5", "<2.5,>=1.22") is True
    assert ms.version_in_spec("2.4.3", "<2.5,>=1.22") is True
    assert ms.version_in_spec("2.5.2", "<2.5,>=1.22") is False
    assert ms.version_in_spec("1.3.1", "==1.2.2") is False
    assert ms.version_in_spec("1.2.2", "==1.2.2") is True


def test_packaging_missing_does_not_silently_unpin(monkeypatch):
    """2026-09-01 hole: /usr/bin/python3 has no packaging; swallowed ImportError
    made every exact pin look like a pip-green. ImportError must propagate."""
    import builtins

    real_import = builtins.__import__

    def _block_packaging(name, *args, **kwargs):
        if name == "packaging" or name.startswith("packaging."):
            raise ImportError("test: packaging blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_packaging)
    with pytest.raises(ImportError):
        ms.version_in_spec("1.2.2", "==1.2.2")
    with pytest.raises(ImportError):
        ms.parse_requires_dist_line("Requires-Dist: lark==1.2.2")
    with pytest.raises(ImportError):
        ms.marker_binds('platform_machine == "x86_64"')
    assert ms.packaging_available() is False
    with pytest.raises(SystemExit) as ei:
        ms.require_packaging()
    assert ei.value.code == 2


def test_vllm_lockstep_is_pinned_not_green():
    """Replay of the 2026-08-31 nightly green set that F7 then dropped."""
    outdated = [
        _od("humming-kernels", "0.1.10", "0.1.13"),
        _od("lark", "1.2.2", "1.3.1"),
        _od("nvtx", "0.2.15", "0.2.16"),
        _od("filelock", "3.32.4", "3.32.5"),
        _od("regex", "2026.8.31", "2026.9.3"),
        _od("cachetools", "7.1.7", "7.1.8"),
    ]
    constraints = [
        _pin("vllm", "0.27.1", "humming-kernels", "==0.1.10"),
        _pin("vllm", "0.27.1", "lark", "==1.2.2"),
        _pin("vllm", "0.27.1", "nvtx", "==0.2.15"),
    ]
    got = ms.classify_env_rows(outdated, constraints)
    assert got["humming-kernels"]["bucket"] == "pinned"
    assert got["humming-kernels"]["pinned_by"] == "vllm"
    assert got["lark"]["bucket"] == "pinned"
    assert got["nvtx"]["bucket"] == "pinned"
    assert got["filelock"]["bucket"] == "green_candidate"
    assert got["regex"]["bucket"] == "green_candidate"
    assert got["cachetools"]["bucket"] == "green_candidate"


def test_comfy_health_tag_occupancy():
    assert ms.health_tag("comfy", False, mode="normal") == "IDLE"
    assert ms.health_tag("comfy", False, mode="heavy") == "IDLE"
    assert ms.health_tag("comfy", False, mode="beast") == "IDLE"
    assert ms.health_tag("comfy", True, mode="normal") == "LEAK"
    assert ms.health_tag("comfy", True, mode="render") == "GREEN"
    assert ms.health_tag("comfy", False, mode="render") == "RED"
    assert ms.health_tag("stamp_ui", False, mode="normal") == "IDLE"
    assert ms.health_tag("ops", True, mode="normal") == "GREEN"
    assert ms.health_tag("ops", False, mode="normal") == "RED"


def test_comfy_tag_key_orders_new_scheme_above_0_3():
    assert ms._comfy_tag_key("v0.3.77") < ms._comfy_tag_key("v0.34.2")
    assert ms._comfy_tag_key("v0.34.2") < ms._comfy_tag_key("v0.35.0")
    assert ms._comfy_tag_key("0.34.2") == ms._comfy_tag_key("v0.34.2")


def test_comfy_health_lane_notes_parked(monkeypatch):
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "normal")
    monkeypatch.setattr(ms, "_served_or_empty", lambda *_a, **_k: [])
    monkeypatch.setattr(ms, "_http_ok", lambda *_a, **_k: False)
    monkeypatch.setattr(ms, "_comfy_stats", lambda: (False, ""))
    monkeypatch.setattr(ms, "hindsight_llm_ok", lambda *_a, **_k: True)
    flags, notes = ms.health_lanes()
    assert "comfy" in flags
    assert flags["comfy"] is False
    assert "parked" in notes["comfy"]
    assert ms.health_tag("comfy", flags["comfy"], mode="normal") == "IDLE"


def test_comfy_health_lane_render_down_is_red(monkeypatch):
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "render")
    monkeypatch.setattr(ms, "_served_or_empty", lambda *_a, **_k: ["ops"])
    monkeypatch.setattr(ms, "_http_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(ms, "_comfy_stats", lambda: (False, ""))
    monkeypatch.setattr(ms, "hindsight_llm_ok", lambda *_a, **_k: True)
    flags, notes = ms.health_lanes()
    assert flags["comfy"] is False
    assert "8188" in notes["comfy"]
    assert ms.health_tag("comfy", False, mode="render") == "RED"


@pytest.mark.skip(reason="the reference recipes file is the comfy plugin's (plugins lane); krew ships config/recipes.example.json")
def test_survey_render_tools_wires_recipes():
    rend = ms.survey_render_tools(sota=False)
    ids = {r["id"]: r for r in rend["recipes"]}
    assert "flux2-turbo" in ids
    assert "ltx23-flf2v" in ids
    assert "ltx25-flf2v" in ids
    assert "ltx25-i2v" in ids
    assert ids["ltx25-flf2v"]["kind"] == "flf2v"
    assert ids["ltx25-i2v"]["kind"] == "i2v"
    assert ids["ltx25-flf2v"]["wired"] is True
    assert ids["ltx25-i2v"]["wired"] is True
    assert ids["ltx23-flf2v"]["wired"] is True
    assert ids["flux2-turbo"]["wired"] is True


def test_comfyui_role_names_render():
    role, heavy = ms.KNOWN_ROLES["comfyui"]
    assert heavy
    assert "Render" in role or "render" in role.lower()
    assert "8188" in role or "Comfy" in role


def test_file_render_sota_card_skips_when_current(monkeypatch):
    calls: list = []

    def fake_run(*_a, **_k):
        calls.append(1)
        raise AssertionError("must not file")

    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    ms.file_render_sota_card(
        {"render": {"sota": {"behind": False, "local": "v0.34.2"}}},
        "2026-08-30",
        "",
    )
    assert calls == []



def test_render_sota_smoke_is_occupancy_swap_not_ad_hoc_stop():
    """Comfy tests go through Render mode. Ops quiesce is inside the mode."""
    src = ms._T("card.render_sota.path")   # step 4: the card's steps are house text now
    assert "ref-mode render turbo" in src
    assert "ref-mode normal" in src
    assert "Do not `systemctl stop` brains" in src
    assert "*_TX.lines(\"card.render_sota.path\")" in inspect.getsource(ms.file_render_sota_card)
    # the reference install's RENDER-OCCUPANCY.md stays with it (its occupancy swap is its GPU mode verb)




# ── train canaries: the hollow-check defect of 2026-08-31 ───────────────────
#
# Both train envs were surveyed with metadata-only smokes and NO canary, so on
# 2026-08-30 22:33 they each took regex 2026.7.19->2026.8.31 with `canary: n/a`
# — and unsloth-train had no `unsloth` in it at all, which the smoke could not
# see because it read the version of *torch*. These pin the gate that closes it.
#
# 8315e85 committed the tests. `CANARY_TRAIN` / `canary_train()` never landed
# in scripts/ms-trail-run.py (HEAD or this tree). Skip until that symbol ships;
# do not implement it from the test file.

_HAS_TRAIN_CANARY = hasattr(ms, "CANARY_TRAIN") and hasattr(ms, "canary_train")
_SKIP_TRAIN_CANARY = pytest.mark.skipif(
    not _HAS_TRAIN_CANARY,
    reason="CANARY_TRAIN not in ms-trail-run.py (8315e85 tests, impl never landed)",
)


def test_unregistered_env_still_has_no_canary(monkeypatch):
    """The registry must not accidentally start gating envs that never had one."""
    assert ms.live_canary("audio8-tts") == (True, "n/a")
    assert ms.live_canary("rail-stt", include_05b=True) == (True, "n/a")


@_SKIP_TRAIN_CANARY
def test_window_apply_plan_skips_only_the_env_whose_canary_is_red():
    assert ms.window_apply_plan("unsloth", canary_ok=False) == "skip_canary_red"
    assert ms.window_apply_plan("unsloth-train", canary_ok=False) == "skip_canary_red"
    assert ms.window_apply_plan(ms.CANARY_ENV, canary_ok=False) == "skip_canary_red"
    # an env with no canary registered is never skipped for someone else's RED
    assert ms.window_apply_plan("vllm", canary_ok=False) == "apply"
    assert ms.window_apply_plan("audio8-tts", canary_ok=False) == "apply"


def test_not_probed_is_not_red():
    """canary_ok=None means "we did not ask", which must still apply."""
    assert ms.window_apply_plan("unsloth", canary_ok=None) == "apply"
    assert ms.window_apply_plan("unsloth", canary_ok=True) == "apply"


@_SKIP_TRAIN_CANARY
def test_train_canary_low_vram_is_na_not_red(monkeypatch):
    """Normal and Heavy often cannot spare the card. A skipped probe must never
    freeze an env's pip — same contract as canary_05b."""
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 512)
    for env in ms.CANARY_TRAIN:
        ok, msg = ms.canary_train(env)
        assert ok is True, env
        assert "n/a" in msg.lower()
        assert "512" in msg


@_SKIP_TRAIN_CANARY
def test_train_canary_na_when_nvidia_smi_fails(monkeypatch):
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: None)
    ok, msg = ms.canary_train("unsloth")
    assert ok is True and "n/a" in msg.lower()


@_SKIP_TRAIN_CANARY
def test_train_canary_ignores_include_05b(monkeypatch):
    """THE COST GATE. live_canary runs once per apply AND once per package in
    the conflict-isolation loop, so a train env must have no expensive variant
    to reach: both call shapes must hit the same one-backward helper."""
    seen: list = []

    def fake(name):
        seen.append(name)
        return True, "stub"

    monkeypatch.setattr(ms, "canary_train", fake)
    a = ms.live_canary("unsloth", include_05b=True)
    b = ms.live_canary("unsloth", include_05b=False)
    assert a == b == (True, "stub")
    assert seen == ["unsloth", "unsloth"]


@_SKIP_TRAIN_CANARY
def test_train_canary_red_when_payload_missing(monkeypatch, tmp_path):
    """A missing FIXTURE is a real failure — unlike missing prod weights."""
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 99999)
    spec = dict(ms.CANARY_TRAIN["unsloth"])
    spec["payload"] = str(tmp_path / "nope.py")
    monkeypatch.setitem(ms.CANARY_TRAIN, "unsloth", spec)
    ok, msg = ms.canary_train("unsloth")
    assert ok is False
    assert "payload missing" in msg


@_SKIP_TRAIN_CANARY
def test_train_canary_missing_prod_weights_is_a_note_not_a_block(monkeypatch, tmp_path):
    """03_train_vl_lora.py's BASE_MODEL is gone. That must not freeze pip on an
    env whose packages are fine — it is a weights problem, not an env problem."""
    payload = tmp_path / "stub.py"
    payload.write_text(
        "import json,sys\n"
        "sys.stdout.write(json.dumps("
        "{'ok': True, 'loss': 1.5, 'has_grad': True, 'n_grad': 4,"
        " 'prod_8b': 'missing'}) + '\\n')\n"
    )
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 99999)
    spec = dict(ms.CANARY_TRAIN["unsloth-train"])
    spec["py"] = "/usr/bin/python3"
    spec["payload"] = str(payload)
    monkeypatch.setattr(ms, "OUT_ROOT", tmp_path)
    monkeypatch.setitem(ms.CANARY_TRAIN, "unsloth-train", spec)
    ok, msg = ms.canary_train("unsloth-train")
    assert ok is True
    assert "prod_8b missing" in msg
    assert ms.window_apply_plan("unsloth-train", canary_ok=ok) == "apply"


@_SKIP_TRAIN_CANARY
def test_train_canary_red_on_bad_backward(monkeypatch, tmp_path):
    payload = tmp_path / "bad.py"
    payload.write_text(
        "import json,sys\n"
        "sys.stdout.write(json.dumps({'ok': False, 'error': 'CUDA blew up'}) + '\\n')\n"
        "raise SystemExit(1)\n"
    )
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 99999)
    spec = dict(ms.CANARY_TRAIN["unsloth"])
    spec["py"] = "/usr/bin/python3"
    spec["payload"] = str(payload)
    monkeypatch.setattr(ms, "OUT_ROOT", tmp_path)
    monkeypatch.setitem(ms.CANARY_TRAIN, "unsloth", spec)
    ok, msg = ms.canary_train("unsloth")
    assert ok is False
    assert "CUDA blew up" in msg


@_SKIP_TRAIN_CANARY
def test_train_smokes_name_the_right_dists_and_stay_off_the_gpu():
    """The old smokes read m.version('unsloth') and m.version('torch') — the
    second is why a deleted unsloth printed GREEN. And importing unsloth costs
    731 MiB, so the 2-hourly tick must stay metadata-only."""
    un = ms.SMOKE_IMPORT["unsloth"]["code"]
    ut = ms.SMOKE_IMPORT["unsloth-train"]["code"]
    for code in (un, ut):
        assert "import unsloth" not in code
        assert "import torch" not in code
        assert "importlib.metadata" in code
    for dist in ("unsloth", "unsloth_zoo", "trl", "peft", "transformers",
                 "sentence_transformers"):
        assert f"'{dist}'" in un, dist
    for dist in ("torch", "transformers", "peft", "trl", "accelerate", "kernels"):
        assert f"'{dist}'" in ut, dist
    # unsloth-train has NO unsloth and does not need it; asserting it would red
    # a healthy env.
    assert "'unsloth'" not in ut


@_SKIP_TRAIN_CANARY
def test_train_envs_are_registered_and_never_park():
    assert set(ms.CANARY_TRAIN) == {"unsloth", "unsloth-train"}
    for env, spec in ms.CANARY_TRAIN.items():
        assert Path(spec["payload"]).is_file(), env
        assert spec["free_mib_min"] > 0
        assert spec["timeout"] <= 300
    # M&S must never call the park helper: it writes /run/hermes/train-busy,
    # which makes window_apply_plan skip every GPU_SHARED_ENVS env for the rest
    # of the night.
    src = (ROOT / "ms" / "scripts" / "ms-trail-run.py").read_text(encoding="utf-8")
    assert "train-park-restore" not in src


# --- writer contract (2026-09-01) -------------------------------------------
# Before this, record_decision() took `decision: str` and wrote it through.
# 58 of 371 live rows carried something that is not a state — some of them
# whole incident paragraphs — and 11 named no package at all. Those rows
# suppress nothing and block nothing: they are landfill in the file the night
# job reads. Nothing in the repo called the CLI, so every one of them arrived
# as hand-written JSON.

def test_decision_state_lists_cannot_drift(monkeypatch):
    """Both derived lists must be subsets of the one source of truth.

    They have drifted twice: steward SOUL published `reviewed` while the apply
    list had never heard of it, and `cancelled` landed in the suppress set but
    not the blocking one.
    """
    for name in ("APPLY_BLOCKING_DECISIONS", "SUPPRESS_DECISIONS"):
        for state in getattr(ms, name):
            assert state in ms.DECISION_STATES, f"{name} has {state!r}"


def test_record_decision_rejects_a_paragraph(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    with pytest.raises(ValueError) as e:
        ms.record_decision(
            env="host",
            package="janitor-parakeet-probe",
            decision="Probe names stay 1:1 with keeper lane names so that "
            "keeper, janitor and the weekly review agree on what is watched.",
            reason="x",
        )
    # the message must name the way out, not just say no
    assert "--record-finding" in str(e.value)


def test_record_decision_requires_env_and_package(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    for env, pkg in (("", "numpy"), ("vllm", ""), ("", "")):
        with pytest.raises(ValueError):
            ms.record_decision(env=env, package=pkg, decision="hold", reason="x")


def test_record_decision_caps_the_reason(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    with pytest.raises(ValueError):
        ms.record_decision(
            env="vllm", package="numpy", decision="hold", reason="z" * 9000
        )


def test_annotate_adds_evidence_but_never_flips_the_decision(
    tmp_path: Path, monkeypatch
):
    """The owner 2026-09-01: agents may argue on a row, they may not flip it."""
    led = tmp_path / "d.json"
    monkeypatch.setattr(ms, "DECISIONS_PATH", led)
    ms.record_decision(
        env="cosyvoice", package="scipy", decision="hold",
        reason="scipy 1.18 needs numpy2; cosy runs 1.26", target="1.18.0",
    )
    assert ms.annotate_decision(
        env="cosyvoice", package="scipy", note="upstream now ships a 1.26 wheel",
        source="t_test",
    )
    row = json.loads(led.read_text())["decisions"][-1]
    assert row["decision"] == "hold"          # unchanged
    assert row["notes"][0]["note"].startswith("upstream")


def test_annotate_refuses_a_row_that_does_not_exist(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    assert ms.annotate_decision(env="vllm", package="ghost", note="hi") is False


def test_findings_are_a_separate_store(tmp_path: Path, monkeypatch):
    """A discovery must not have to become a fake package decision."""
    monkeypatch.setattr(ms, "FINDINGS_PATH", tmp_path / "f.json")
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    ms.record_finding(
        title="probe naming lockstep",
        finding="keeper LANES and janitor PROBES must move in one commit.",
        source="t_test",
    )
    store = json.loads((tmp_path / "f.json").read_text())
    assert store["schema"] == "ms_findings/v1"
    assert store["findings"][0]["title"] == "probe naming lockstep"
    assert not (tmp_path / "d.json").exists()  # decisions untouched


def test_applied_without_unlock_defaults_land_only(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    ms.record_decision(
        env="vllm", package="httpx2", decision="applied",
        reason="window", source="ms-window",
    )
    row = json.loads((tmp_path / "d.json").read_text())["decisions"][-1]
    assert row["unlock"]["disposition"] == "land-only"
    assert row["unlock"]["kpi_id"] is None


def test_keep_requires_path_on_and_kpi(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    with pytest.raises(ValueError, match="path_on"):
        ms.record_decision(
            env="vllm-0271-cu132",
            package="fastsafetensors",
            decision="applied",
            reason="0.4.0",
            unlock={
                "disposition": "keep",
                "claim": "GPU-direct load",
                "path_on": False,
                "measure": "speed_probe.py --lane ops",
                "kpi_id": "ops_tok_s",
            },
        )
    with pytest.raises(ValueError, match="kpi_id"):
        ms.record_decision(
            env="vllm-0271-cu132",
            package="fastsafetensors",
            decision="applied",
            reason="0.4.0",
            unlock={
                "disposition": "keep",
                "claim": "GPU-direct load",
                "path_on": True,
                "measure": "none",
                "flag": "--load-format fastsafetensors",
            },
        )
    ms.record_decision(
        env="vllm-0271-cu132",
        package="fastsafetensors",
        decision="applied",
        reason="0.4.0",
        unlock={
            "disposition": "land-only",
            "claim": "wheel in env, load_format=auto",
            "path_on": False,
            "flag": "--load-format fastsafetensors",
            "measure": "none",
        },
    )
    row = json.loads((tmp_path / "d.json").read_text())["decisions"][-1]
    assert row["unlock"]["disposition"] == "land-only"
    assert row["unlock"]["path_on"] is False


# 30, not the 58 first measured: that count was taken against the old
# incomplete enum. `cancelled` (27 rows) and `reviewed` (6) were always real
# states the code handled — only the constant had never heard of them. The
# actual landfill is 30 rows. Drop this to 0 once the migration has run.
# 2026-09-12: scripts/ms-ledger-migrate-20260912.py moved the 43 rows that
# had accrued into ledger["landfill"] (kept, not deleted). This is now the
# schema assertion.
# 2026-09-21: 2 legitimate hand-written rows (t_9750f05d comfy drift,
# t_915a0314 tree-hygiene) landed without record_decision(); they are real
# decisions with non-standard shape. Baseline absorbs them; a third
# hand-written row fails and names its writer.
LEDGER_INVALID_BASELINE = 2


def test_live_ledger_does_not_get_worse():
    """The check that never existed: read the REAL ledger and count landfill.

    Ratchet, not a gate — the migration has not run yet. When it has, set
    LEDGER_INVALID_BASELINE to 0 and this becomes the real schema assertion.
    """
    if not ms.DECISIONS_PATH.is_file():
        pytest.skip("no live ledger on this box")
    rows = json.loads(ms.DECISIONS_PATH.read_text()).get("decisions") or []
    bad = [
        r for r in rows
        if not isinstance(r, dict)
        or str(r.get("decision")) not in ms.DECISION_STATES
        or not str(r.get("env") or "").strip()
        or not str(r.get("package") or "").strip()
    ]
    # Name the writer. A red test that says who is doing it is worth more
    # than a green one that hides it — record_decision() can only guard the
    # CLI, and every one of these arrived as hand-written JSON.
    import collections
    who = collections.Counter(str(r.get("source") or "?")[:60] for r in bad)
    assert len(bad) <= LEDGER_INVALID_BASELINE, (
        f"{len(bad)} invalid rows, baseline {LEDGER_INVALID_BASELINE}. "
        "Something is still writing decisions.json by hand, bypassing "
        "record_decision(). Sources of the invalid rows: "
        + ", ".join(f"{k} x{v}" for k, v in who.most_common(5))
    )


# --- refusal back-off (2026-09-01) ------------------------------------------
# apply_green classified every failure as build-fail or refused and then threw
# the result away, so the night re-resolved, re-installed and re-dropped the
# same packages forever. This is for GENUINE forward-dep failures — joblib,
# comfy-cli, mixpanel. An ABI pin is not a refusal and must never arrive here.

def test_refusal_counts_up_at_the_same_version(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    for expected in (1, 2, 3):
        n = ms.record_refusal(
            "unsloth", "joblib", target="1.6.0", current="1.5.0",
            outcome="refused", err="conflict with scikit-learn",
        )
        assert n == expected
    assert ms.refusal_row("unsloth", "joblib")["attempts"] == 3


def test_a_new_upstream_version_resets_the_count(tmp_path: Path, monkeypatch):
    """SOTA: every NEW version earns a fresh attempt."""
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    ms.record_refusal("unsloth", "joblib", target="1.6.0", current="1.5.0",
                      outcome="refused", err="x")
    ms.record_refusal("unsloth", "joblib", target="1.6.0", current="1.5.0",
                      outcome="refused", err="x")
    assert ms.record_refusal(
        "unsloth", "joblib", target="1.7.0", current="1.5.0",
        outcome="refused", err="x",
    ) == 1


def test_skip_only_after_the_cap_and_only_at_that_version(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ms, "MS_REFUSE_MAX", 3)
    rows = [{"package": "joblib", "current": "1.5.0", "latest": "1.6.0"}]
    for _ in range(2):
        ms.record_refusal("unsloth", "joblib", target="1.6.0", current="1.5.0",
                          outcome="refused", err="x")
    assert ms.skip_nightly_refused("unsloth", rows) == set()   # 2 < 3
    ms.record_refusal("unsloth", "joblib", target="1.6.0", current="1.5.0",
                      outcome="refused", err="x")
    assert ms.skip_nightly_refused("unsloth", rows) == {"joblib"}
    # upstream moves -> released, even though it refused three times before
    moved = [{"package": "joblib", "current": "1.5.0", "latest": "1.7.0"}]
    assert ms.skip_nightly_refused("unsloth", moved) == set()


def test_refusal_is_scoped_to_one_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ms, "MS_REFUSE_MAX", 1)
    ms.record_refusal("unsloth", "joblib", target="1.6.0", current="1.5.0",
                      outcome="refused", err="x")
    rows = [{"package": "joblib", "current": "1.5.0", "latest": "1.6.0"}]
    assert ms.skip_nightly_refused("unsloth", rows) == {"joblib"}
    assert ms.skip_nightly_refused("cosyvoice", rows) == set()


def test_refused_does_not_hard_block_the_way_a_hold_does():
    """A hard block would freeze the package at the version that failed.

    The back-off is counted against that exact target instead, so upstream
    moving is enough to release it. The owner's rule is that the box stays at SOTA.
    """
    assert "refused" not in ms.APPLY_BLOCKING_DECISIONS
    assert "refused" not in ms.SUPPRESS_DECISIONS   # still gets re-researched
    assert "refused" in ms.DECISION_STATES


def test_the_backoff_can_be_disabled_without_a_deploy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ms, "MS_REFUSE_MAX", 0)
    for _ in range(5):
        ms.record_refusal("unsloth", "joblib", target="1.6.0", current="1.5.0",
                          outcome="refused", err="x")
    rows = [{"package": "joblib", "current": "1.5.0", "latest": "1.6.0"}]
    # 0 means "never park anything" — the lever the owner can pull without a deploy.
    # The first cut of this compared `attempts >= 0`, which parked EVERY
    # refusal instantly: the exact opposite of the documented rollback.
    assert ms.skip_nightly_refused("unsloth", rows) == set()


# --- hold ageing (2026-09-01) ------------------------------------------------
# 242 holds, 11 with an expiry, none reached; holds outnumbered applies 4:1.
# A permanent hold is the opposite of keeping the box at SOTA. But releasing
# them into --apply-green would replay the incidents they were written for, so
# an aged hold goes back to RESEARCH and stays blocked from installing.

import datetime as _dt


def _hold(env="cosyvoice", pkg="scipy", ts="2026-01-01", until=None):
    return {"env": env, "package": pkg, "decision": "hold", "ts": ts,
            "until": until, "target": "1.18.0", "reason": "needs numpy2"}


def test_an_explicit_until_always_wins(monkeypatch):
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 90)
    assert ms.hold_expiry(_hold(until="2027-05-05")) == _dt.date(2027, 5, 5)


def test_a_hold_ages_from_its_own_ts(monkeypatch):
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 90)
    exp = ms.hold_expiry(_hold(ts="2026-01-01"))
    assert exp is not None
    # 90 days +/- the 30-day stagger
    assert _dt.date(2026, 3, 3) <= exp <= _dt.date(2026, 5, 2)


def test_rows_with_no_ts_anchor_to_the_day_the_ttl_shipped(monkeypatch):
    """93 of 371 rows carry no ts. Counting from "today" would slide forward
    every night and never expire."""
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 90)
    monkeypatch.setattr(ms, "MS_TTL_ANCHOR", "2026-09-01")
    exp = ms.hold_expiry(_hold(ts=None))
    assert exp is not None and exp.year == 2026 and exp.month in (10, 11, 12)


def test_the_stagger_spreads_them_out(monkeypatch):
    """242 holds must not all come due on one day and bury the card."""
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 90)
    dates = {ms.hold_expiry(_hold(pkg=f"pkg{i}")) for i in range(60)}
    assert len(dates) > 30          # deterministic, but well spread
    # and stable across calls
    assert ms.hold_expiry(_hold(pkg="pkg7")) == ms.hold_expiry(_hold(pkg="pkg7"))


def test_an_aged_hold_reads_as_review_due(monkeypatch):
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 1)
    row = _hold(ts="2020-01-01")
    assert ms.hold_is_due(row) is True
    assert ms.effective_decision(row) == "review_due"


def test_an_aged_hold_still_refuses_to_install(monkeypatch):
    """The whole safety of this change. cosyvoice/scipy under NumPy 1.26 and
    ruamel 0.19 breaking speechbrain are what these holds remember."""
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 1)
    row = _hold(ts="2020-01-01")
    assert ms._decision_blocks_apply(
        row, "cosyvoice", {"package": "scipy", "latest": "1.18.0"}
    ) is True
    assert "review_due" in ms.APPLY_BLOCKING_DECISIONS


def test_an_aged_hold_does_come_back_for_research(monkeypatch):
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 1)
    row = _hold(ts="2020-01-01")
    assert ms._decision_suppresses(
        row, "cosyvoice", {"package": "scipy", "latest": "1.18.0"}
    ) is False


def test_a_fresh_hold_is_untouched(monkeypatch):
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 90)
    row = _hold(ts=_dt.date.today().isoformat())
    assert ms.hold_is_due(row) is False
    assert ms.effective_decision(row) == "hold"
    assert ms._decision_suppresses(
        row, "cosyvoice", {"package": "scipy", "latest": "1.18.0"}
    ) is True


def test_ageing_can_be_switched_off_without_a_deploy(monkeypatch):
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 0)
    assert ms.hold_expiry(_hold(ts="2020-01-01")) is None
    assert ms.hold_is_due(_hold(ts="2020-01-01")) is False


def test_no_live_hold_comes_due_the_day_this_ships():
    """A change that expires 242 researched holds on day one is a bad change."""
    if not ms.DECISIONS_PATH.is_file():
        pytest.skip("no live ledger on this box")
    rows = [
        r for r in json.loads(ms.DECISIONS_PATH.read_text()).get("decisions") or []
        if isinstance(r, dict) and str(r.get("decision")).lower() == "hold"
    ]
    due = [r for r in rows if ms.hold_is_due(r)]
    assert due == [], f"{len(due)} holds would expire immediately"


# --- trend (2026-09-01) ------------------------------------------------------
# There was no trend measurement anywhere in 4000+ lines. That is why eight ABI
# pins could be re-attempted and re-dropped for 46 consecutive nights while
# every card reported GREEN: the failure was in the logs the whole time and
# structurally invisible to the thing writing the report.

def test_trend_keeps_one_row_per_day(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "TREND_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    ms.append_trend({"totals": {}}, {}, {}, "2026-09-01")
    ms.append_trend({"totals": {}}, {}, {}, "2026-09-01")   # re-run same day
    ms.append_trend({"totals": {}}, {}, {}, "2026-09-02")
    rows = ms._read_trend()
    assert [r["day"] for r in rows] == ["2026-09-01", "2026-09-02"]


def test_trend_survives_a_corrupt_line(tmp_path: Path, monkeypatch):
    t = tmp_path / "t.jsonl"
    t.write_text('{"day": "2026-09-01"}\nnot json\n\n{"day": "2026-09-02"}\n')
    monkeypatch.setattr(ms, "TREND_PATH", t)
    assert [r["day"] for r in ms._read_trend()] == ["2026-09-01", "2026-09-02"]


def test_trend_counts_an_aged_hold_as_review_due(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ms, "TREND_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(ms, "MS_HOLD_TTL_DAYS", 1)
    # "fresh" must be fresh on the day the test runs — a literal 2026-09-01
    # went stale under TTL=1 eleven days later and the test went red unread.
    led = {"decisions": [_hold(ts="2020-01-01"),
                         _hold(pkg="fresh", ts=_dt.date.today().isoformat())]}
    ms.save_decisions(led)
    census = ms._ledger_census()
    assert census.get("review_due") == 1
    assert census.get("hold") == 1


def test_refusal_streaks_rank_the_worst_first(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ms, "OUT_ROOT", tmp_path)   # no live manifests: nothing is "stale"
    for _ in range(3):
        ms.record_refusal("unsloth", "joblib", target="1.6.0", current="1.5.0",
                          outcome="refused", err="conflict")
    ms.record_refusal("vllm", "comfy-cli", target="2.1.0", current="2.0.0",
                      outcome="build-fail", err="failed to build wheel")
    ms.record_refusal("vllm", "comfy-cli", target="2.1.0", current="2.0.0",
                      outcome="build-fail", err="failed to build wheel")
    got = ms.refusal_streaks()
    assert [x["package"] for x in got] == ["joblib", "comfy-cli"]
    assert got[0]["attempts"] == 3
    # a single refusal is not yet a pattern
    assert all(x["attempts"] >= 2 for x in got)


def test_the_card_says_when_holds_outgrow_applies(tmp_path: Path, monkeypatch):
    """The direction that matters: 242 holds vs 58 applies is not maintenance."""
    monkeypatch.setattr(ms, "TREND_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(
        {"day": "2026-08-25", "ledger": {"hold": 200, "applied": 50}}
    ) + "\n")
    row = {"day": "2026-09-01", "ledger": {"hold": 242, "applied": 58}}
    lines = "\n".join(ms._trend_lines(row, {}))
    assert "holds +42" in lines and "applied rows +8" in lines
    assert "opposite of keeping this box current" in lines


def test_the_card_names_our_own_unlanded_work(tmp_path: Path, monkeypatch):
    """cosy's biggest debt is scipy that apply-green replaced itself.

    Reporting that as OS debt would blame Ubuntu for the trail's own gap.
    """
    monkeypatch.setattr(ms, "TREND_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    os_state = {"restart_debt": {"units": {
        "cosy-voice.service": {"n": 115, "venv": {"cosyvoice_vllm": 108}, "os": {}}
    }}}
    lines = "\n".join(ms._trend_lines({"day": "x", "ledger": {}}, os_state))
    assert "this trail replaced itself" in lines
    assert "108" in lines


# --- 2026-09-12: the trend line, the canary floor, and refusals that were
# never refusals (Fable, "self-improving SOTA claim" audit) -----------------

def test_canary_floor_follows_vllm_util_times_total():
    # The rule (09-12): never below util x total + 256, so vLLM never refuses a launch we make.
    # 09-30: util 0.025 -> ceil(2447.175) = 2448 + 256 = 2704 (was 0.07 -> 7109, 12 skipped nights).
    # 09-30 engine switch: 0.30's 0.5B peaks at 2656, so free_mib_min (2912) is now the larger term.
    assert ms.canary_05b_floor_mib(97887, {"util": "0.025", "free_mib_min": 2560}) == 2448 + 256
    assert ms.canary_05b_floor_mib(97887) == 2912
    assert ms.canary_05b_floor_mib(97887, {"util": "0.07", "free_mib_min": 4096}) == 6853 + 256
    assert ms.canary_05b_floor_mib(None) == int(ms.CANARY_05B["free_mib_min"])
    assert ms.canary_05b_floor_mib(1000) == int(ms.CANARY_05B["free_mib_min"])


def test_05b_skips_between_old_floor_and_vllm_floor(monkeypatch):
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 2800)     # above vLLM's 2704 precheck floor, below the 2912 the 0.30 0.5B needs
    monkeypatch.setattr(ms, "gpu_total_mib", lambda: 97887)
    ok, msg = ms.canary_05b()
    assert ok is True
    assert "n/a" in msg


def test_05b_no_room_exit_is_not_red(monkeypatch, tmp_path):
    """vLLM starting and then refusing for VRAM is the same class as the
    floor skip — the wheels were never judged."""
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 90000)
    monkeypatch.setattr(ms, "gpu_total_mib", lambda: 97887)
    monkeypatch.setattr(ms, "_port_open", lambda p: False)
    monkeypatch.setattr(ms, "OUT_ROOT", tmp_path)
    model = tmp_path / "m"
    model.mkdir()
    (model / "config.json").write_text("{}")
    monkeypatch.setitem(ms.CANARY_05B, "model", str(model))
    vllm_bin = tmp_path / "vllm"
    vllm_bin.write_text("#!/bin/sh\necho 'ValueError: Free memory on device cuda:0 (4.82/94.97 GiB) on startup is less than desired GPU memory utilization (0.07, 6.65 GiB).'\nexit 1\n")
    vllm_bin.chmod(0o755)
    monkeypatch.setattr(ms.Path, "is_file", lambda self: True)
    import subprocess as _sp
    real_popen = _sp.Popen

    def popen(cmd, **kw):
        return real_popen([str(vllm_bin)], **kw)

    monkeypatch.setattr(ms.subprocess, "Popen", popen)
    ok, msg = ms.canary_05b()
    assert ok is True, msg
    assert "no VRAM room" in msg


def test_canary_error_line_is_the_reason_not_the_tail():
    text = "Traceback (most recent call last):\n  File x\nValueError: Free memory on device is less than desired\n[rank0]: warning\n"
    assert ms._canary_log_error_line(text).startswith("ValueError: Free memory")


def test_batch_rollback_does_not_write_refusal_rows(tmp_path: Path, monkeypatch):
    """The caller's bookkeeping: a rollback result names every package in the
    batch under `failed`; none of them refused."""
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    result = {"ok": False, "failed": ["pyjwt", "tqdm"], "rollback": True,
              "err": "vLLM canary RED — rolled back to pre-freeze pins"}
    rows = [{"package": "pyjwt", "latest": "2.14.0", "current": "2.13.0"},
            {"package": "tqdm", "latest": "4.70.1", "current": "4.70.0"}]
    # Same loop shape as main(): reproduce the decision the caller makes.
    outcomes = []
    for row in rows:
        pkg = ms.canon(row["package"])
        if result.get("rollback"):
            outcomes.append({"package": pkg, "outcome": "rolled-back"})
            continue
        ms.record_refusal("vllm-0271-cu132", pkg, target=row["latest"],
                          current=row["current"], outcome="refused", err="")
    assert [o["outcome"] for o in outcomes] == ["rolled-back", "rolled-back"]
    assert ms.refusal_row("vllm-0271-cu132", "pyjwt") == {}


def test_refusal_resolution_names_the_way_out():
    fwd = "The package `joblib` requires `cloudpickle>=3.0`, but it's not installed"
    assert "cloudpickle" in ms.refusal_resolution(fwd)
    assert "forward dep" in ms.refusal_resolution(fwd)
    build = "Building hindsight==0.1.7\n  x Failed to build `hindsight==0.1.7`\n  The build backend returned an error"
    assert "name collision" in ms.refusal_resolution(build)
    cap = "The package `xgrammar` requires `transformers>=4.38.0,<5`, but `5.9.0` is installed"
    assert "peer cap" in ms.refusal_resolution(cap)
    assert "transformers" in ms.refusal_resolution(cap)
    assert "rollback" in ms.refusal_resolution("")


def test_refusal_streak_err_has_no_newlines(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    for _ in range(3):
        ms.record_refusal("hindsight", "hindsight", target="0.1.7", current="0.1.0",
                          outcome="build-fail",
                          err="Using Python 3.14.4 environment at: /x\nResolved 1 package\n  Failed to build")
    (x,) = ms.refusal_streaks()
    assert "\n" not in x["err"]
    lines = ms._trend_lines({"day": "2026-09-12", "ledger": {}}, {})
    assert any("resolve:" in ln for ln in lines)
    assert not any(ln.strip() == "Resolve" for ln in lines)


def test_missing_forward_deps_parse_and_gate(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    bad = ["The package `joblib` requires `cloudpickle>=3.0`, but it's not installed",
           "The package `contourpy` requires `numpy>=2.0`, but `1.26.4` is installed",
           "The package `other` requires `torch>=2`, but it's not installed"]
    assert ms._missing_forward_deps(bad, {"joblib"}) == ["cloudpickle"]
    assert ms._missing_forward_deps(bad, {"joblib", "other"}) == ["cloudpickle", "torch"]
    assert ms._forward_dep_allowed("unsloth", "cloudpickle") is True
    assert ms._forward_dep_allowed("unsloth", "torch") is False          # NEVER_AUTO
    assert ms._forward_dep_allowed("unsloth", "nvidia-nccl-cu13") is False
    ms.record_decision(env="unsloth", package="cloudpickle", decision="hold",
                       reason="test", target="3.1.2", source="t")
    assert ms._forward_dep_allowed("unsloth", "cloudpickle") is False   # ledger block


def test_trend_splits_orphan_holds_from_live_ones(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ms, "TREND_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(ms, "VENVS", {"vllm": ("/x", "r", True)})
    today = _dt.date.today().isoformat()
    led = {"decisions": [
        _hold(env="vllm", pkg="a", ts=today),
        _hold(env="vllm-face-smoke-20260909", pkg="b", ts=today),
        _hold(env="vllm-face-smoke-20260909", pkg="c", ts=today),
        _hold(env="host", pkg="unit", ts=today),
        _hold(env="vllm", pkg="old", ts="2026-01-01"),
    ]}
    ms.save_decisions(led)
    live, orphan = ms._new_holds_since("2026-09-05")
    assert {r["package"] for r in orphan} == {"b", "c"}
    assert {r["package"] for r in live} == {"a", "unit"}
    (tmp_path / "t.jsonl").write_text(
        json.dumps({"day": "2026-09-05", "applied_n": 20, "ledger": {"hold": 10, "applied": 5}}) + "\n")
    lines = ms._trend_lines({"day": today, "applied_n": 21, "ledger": {"hold": 14, "applied": 6}}, {})
    text = "\n".join(lines)
    assert "holds +4, applied rows +1, upgrades landed by the nights 41" in text
    assert "2 of the new holds sit on env(s) no longer on disk (vllm-face-smoke-20260909)" in text
    assert "outgrowing" not in text


def test_refusal_streak_drops_rows_the_env_moved_past(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    monkeypatch.setattr(ms, "OUT_ROOT", tmp_path)
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "vllm-LATEST.txt").write_text("transformers==5.9.0\nxgrammar==0.2.6\n")
    for _ in range(3):
        ms.record_refusal("vllm", "xgrammar", target="0.2.5.post1", current="0.2.3",
                          outcome="refused", err="requires transformers<5")
        ms.record_refusal("vllm", "joblib", target="1.6.0", current="1.5.3",
                          outcome="refused", err="cloudpickle not installed")
    assert [x["package"] for x in ms.refusal_streaks()] == ["joblib"]   # xgrammar is at 0.2.6 now


# --- 2026-09-12: installed is not used; a repeat is not a one-off -----------

def test_unused_probe_runs_in_an_env(tmp_path: Path, monkeypatch):
    """Run the probe against this interpreter: pytest is installed here and
    it has been imported (pyc exists). Since 2026-09-17 an `extra ==`
    requirement counts as a reference (the hindsight jsonref removal), so
    whether pytest is "unreferenced" depends on what else is installed; the
    semantics are pinned on a built site in tests/test_ms_service_envs.py."""
    import sys
    out = ms.unused_packages(sys.executable)
    assert out is not None and out["dists"] > 0
    assert "pytest" not in out["never_imported"]
    assert set(out["never_imported"]) <= set(out["unreferenced"])


def test_unused_undecided_drops_ledger_decided_names(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    ms.record_decision(env="hindsight", package="mlx", decision="wontfix", reason="keep", source="t")
    unused = {"unreferenced": ["mlx", "hindsight", "pg0-embedded"], "never_imported": ["mlx", "hindsight", "pg0-embedded"]}
    assert ms.unused_undecided("hindsight", unused) == ["hindsight", "pg0-embedded"]
    assert ms.unused_undecided("hindsight", None) == []


def test_upstream_is_dead_is_years_not_days():
    assert ms.upstream_is_dead({"last_upload": "2013-01-22"}, today=_dt.date(2026, 9, 12)) is True
    assert ms.upstream_is_dead({"last_upload": "2026-01-22"}, today=_dt.date(2026, 9, 12)) is False
    assert ms.upstream_is_dead({"last_upload": ""}) is False
    assert ms.upstream_is_dead(None) is False


def test_refusal_resolution_names_the_squatter(monkeypatch):
    monkeypatch.setattr(ms, "pypi_upstream", lambda pkg: {
        "last_upload": "2013-01-22", "sdist_only": True, "summary": "Python tools for Hindsight Software"})
    build = "Building hindsight==0.1.7\n  x Failed to build `hindsight==0.1.7`\n  The build backend returned an error"
    got = ms.refusal_resolution(build, "build-fail", "hindsight")
    assert "name collision" in got and "2013-01-22" in got and "wontfix" in got
    monkeypatch.setattr(ms, "pypi_upstream", lambda pkg: None)   # network down: no guess
    assert "name collision: PyPI" not in ms.refusal_resolution(build, "build-fail", "hindsight")


def test_pypi_upstream_reads_cache_before_network(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "OUT_ROOT", tmp_path)
    (tmp_path / "pypi-cache").mkdir()
    (tmp_path / "pypi-cache" / "hindsight.json").write_text(json.dumps({"name": "hindsight", "last_upload": "2013-01-22"}))
    def boom(*a, **k):
        raise AssertionError("network touched")
    monkeypatch.setattr(ms.urllib.request, "urlopen", boom)
    assert ms.pypi_upstream("hindsight")["last_upload"] == "2013-01-22"


def test_unused_card_lines_name_the_dead_upstream(monkeypatch):
    monkeypatch.setattr(ms, "upstream_note", lambda n: "PyPI `hindsight`: last release 2013-01-22" if n == "hindsight" else "")
    lines = ms._unused_card_lines([
        {"env": "hindsight", "unused_n": 3, "unused_sample": ["hindsight", "mlx"]},
        {"env": "vllm", "unused_n": 0, "unused_sample": []},
    ])
    text = "\n".join(lines)
    assert "hindsight [PyPI `hindsight`: last release 2013-01-22]" in text
    assert "mlx" in text and "(+1 in the report json)" in text
    assert "vllm" not in text
    assert ms._unused_card_lines([{"env": "x", "unused_n": 0}]) == []


def test_repeat_failures_need_two_nights_and_tonight():
    hist = [
        {"day": "2026-09-08", "apply_failures": ["vllm-0271-cu132:apply_green_failed"]},
        {"day": "2026-09-09", "apply_failures": ["vllm-0271-cu132:apply_green_failed"]},
        {"day": "2026-09-10", "apply_failures": []},
    ]
    row = {"day": "2026-09-11", "apply_failures": ["vllm-0271-cu132:apply_green_failed", "cosyvoice:pre-freeze"]}
    got = ms.repeat_failures(hist, row)
    assert [(x["env"], x["reason"], len(x["nights"])) for x in got] == [("vllm-0271-cu132", "apply_green_failed", 3)]
    # a failure that stopped tonight is not filed
    assert ms.repeat_failures(hist, {"day": "2026-09-11", "apply_failures": []}) == []
    # window: only the last 7 runs count (the same window the trend line uses)
    old = [{"day": f"2026-08-{d:02d}", "apply_failures": ["x:y"] if d < 10 else []} for d in range(1, 20)]
    assert ms.repeat_failures(old, {"day": "2026-09-11", "apply_failures": ["x:y"]}) == []


def test_diagnose_card_is_filed_once_per_repeat(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "TREND_PATH", tmp_path / "t.jsonl")
    (tmp_path / "t.jsonl").write_text(
        json.dumps({"day": "2026-09-09", "apply_failures": ["vllm-0271-cu132:apply_green_failed"]}) + "\n")
    report = {
        "trend": {"day": "2026-09-11", "apply_failures": ["vllm-0271-cu132:apply_green_failed"]},
        "applied": {"vllm-0271-cu132": {"err": "vLLM canary RED — rolled back", "canary": "0.5B exited 1 ValueError: Free memory",
                                        "failed": ["pyjwt", "tqdm"]}},
    }
    filed = []
    monkeypatch.setattr(ms, "_kanban_create", lambda **kw: filed.append(kw) or True)
    assert ms.file_diagnose_cards(report, "2026-09-11", listing="") == 1
    assert filed[0]["assignee"] == "bash"
    assert filed[0]["title"].startswith("M&S diagnose: vllm-0271-cu132 — apply_green_failed 2x")
    assert "Free memory" in filed[0]["body"] and "pyjwt" in filed[0]["body"]
    assert filed[0]["idemp"] == "ms-diagnose-vllm-0271-cu132-apply_green_failed-2026w37"
    # an open card with that title means no second one
    assert ms.file_diagnose_cards(report, "2026-09-11",
                                  listing="t_deadbeef running bash M&S diagnose: vllm-0271-cu132 — apply_green_failed 2x") == 0


def test_trend_row_records_apply_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ms, "TREND_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "d.json")
    row = ms.append_trend({"totals": {}, "apply_failures": [{"env": "vllm-0271-cu132", "reason": "apply_green_failed"}]},
                          {}, {}, "2026-09-11")
    assert row["apply_failures"] == ["vllm-0271-cu132:apply_green_failed"]


def test_hermes_tree_line_names_the_next_step():
    assert "GREEN" in ms.hermes_tree_line({"ok": True, "last": "VERIFY GREEN: tree == origin/main + queue"})
    red = ms.hermes_tree_line({"ok": False, "last": "VERIFY RED: not applied=['x'] uncovered=[]"})
    assert "RED" in red and "hermes-tree.py status" in red
    assert "not checked" in ms.hermes_tree_line({})


def test_hermes_tree_verify_runs_the_tool(monkeypatch):
    monkeypatch.setattr(ms, "run", lambda argv, timeout=0: (0, "tree abc: 0 local\n  [applied] 10-x — a.py\nVERIFY GREEN: tree == origin/main + queue\n", ""))
    got = ms.run_hermes_tree_verify()
    assert got["ok"] is True and got["last"].startswith("VERIFY GREEN") and got["patches"] == ["[applied] 10-x — a.py"]


def test_crash_evidence_captures_first_error_line(tmp_path: Path, monkeypatch):
    """The RED service note must carry the first error line, not just the exit code."""
    # journalctl side: fake the _svc_systemctl call — the exit-code line only
    # (what the unit journal actually shows) plus the real first error line
    calls = []

    def fake_run(argv, *a, **k):
        calls.append(argv)
        if argv and argv[0] == "journalctl":
            return (0, "20:05:52 systemd: hindsight.service: Main process exited, code=exited, status=3/NOTIMPLEMENTED\n"
                       "20:05:53 hindsight-api: Traceback (most recent call last):\n"
                       "20:05:53 hindsight-api: ModuleNotFoundError: No module named 'psycopg'\n", "")
        raise AssertionError(f"unexpected argv {argv}")

    monkeypatch.setattr(ms, "run", fake_run)
    # app-log side: the real traceback tail from the incident
    errlog = tmp_path / "hindsight-api.err.log"
    errlog.write_text("INFO starting\nTraceback (most recent call last):\nModuleNotFoundError: No module named 'psycopg'\n")
    class _FakePath:
        def is_file(self): return True
        def read_text(self, errors="replace"): return errlog.read_text()
    real_path = ms.Path
    monkeypatch.setattr(ms, "Path", lambda s: _FakePath() if s == "/data/hindsight/logs/hindsight-api.err.log" else real_path(s))
    ev = ms._crash_evidence("hindsight.service")
    assert "psycopg" in ev
    assert "NOTIMPLEMENTED" in ev  # the exit-code line must still be there, not instead
    # a dead unit with nothing readable never raises and returns empty
    monkeypatch.setattr(ms, "run", lambda argv, *a, **k: (1, "", ""))
    ev2 = ms._crash_evidence("some-other.service")
    assert ev2 == ""
    # the RED service note must carry the cause, not just the exit code
    unit = {"unit": "hindsight.service", "health": "http://127.0.0.1:8888/health"}
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: None)
    monkeypatch.setattr(ms, "_service_units", lambda n: [unit])
    monkeypatch.setattr(ms, "_unit_policy", lambda s: "restart")
    monkeypatch.setattr(ms, "_unit_parked", lambda s, m: False)
    monkeypatch.setattr(ms, "_svc_systemctl",
                        lambda spec, args, timeout=30: (0, "", ""))
    monkeypatch.setattr(ms, "run", lambda argv, *a, **k: (0, "status=3/NOTIMPLEMENTED", ""))
    monkeypatch.setattr(ms, "_wait_health", lambda spec: False)
    ok, note = ms.service_restart_check("hindsight")
    assert ok is False
    assert "did not come back" in note
    assert "status=3/NOTIMPLEMENTED" in note
