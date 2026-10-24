"""M&S step 2 (the User 09-29): every house fact is a setting, and the house profile is today.

- The house profile (config/ms-house.yaml, loaded whenever it exists in step 2) reproduces every
  value the pre-step-2 script carried as a literal - frozen in tests/fixtures/ms-reference-legacy.json.
- With no house (MS_INTEGRATIONS=none) M&S knows no envs, files no cards, pages nobody, and keeps
  its state under $XDG_STATE_HOME/krew/ms, else ~/.local/state/krew/ms.
- ms.yaml overrides the profile; an environment variable overrides both.
Each non-house case runs in a fresh interpreter: settings are read at import.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
SCRIPT = ROOT / "ms" / "scripts" / "ms-trail-run.py"
HOME_KEYS = {"HERMES_PACKS", "HERMES_LIVE_PROFILES", "MS_ENV_HOLD_FILE", "MODE_FILE", "UPSTREAM_APIS_FILE"}
LEGACY = json.loads((ROOT / "tests" / "fixtures" / "ms-reference-legacy.json").read_text())

PROBE = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ms_probe", sys.argv[1])
ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
print(json.dumps({
    "OUT_ROOT": str(ms.OUT_ROOT), "VENVS": sorted(ms.VENVS), "KNOWN_ROLES": sorted(ms.KNOWN_ROLES),
    "SMOKE_IMPORT": sorted(ms.SMOKE_IMPORT), "SERVICE_ENVS_ALL": sorted(ms.SERVICE_ENVS_ALL),
    "CANARY_ENV": ms.CANARY_ENV, "MS_KANBAN_PROFILE": ms.MS_KANBAN_PROFILE,
    "argv": ms._kanban_argv("hermes"), "NOTIFY_CMD": ms.NOTIFY_CMD, "page": ms.notify_user("x"),
    "LIVE_MANIFEST_ON": ms.LIVE_MANIFEST_ON, "DELIVERABLES_DIR": str(ms.DELIVERABLES_DIR),
    "MS_ENV_HOLD_FILE": str(ms.MS_ENV_HOLD_FILE), "MS_MITIGATION_MAX_ENVS": ms.MS_MITIGATION_MAX_ENVS,
    "kanban_default": ms._S.get("kanban.enabled"), "occupancy": ms.occupancy_gpu_mode(),
    "profiles": ms.survey_hermes_profiles(), "render": ms.survey_render_tools(sota=False),
    "assignee_drift": ms._assignee("drift"), "sources": ms._S.SETTINGS.get("_sources"),
}))
"""


def _probe(env_extra: dict, tmp_path: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MS_", "XDG_STATE_HOME", "KREW_HOME"))}
    env["MS_PROFILE_FILE"] = str(ms_defaults_only.REF_PROFILE)   # the reference profile, never a live layer
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", PROBE, str(SCRIPT)], capture_output=True,
                         text=True, env=env, timeout=180, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def _module():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ms_defaults_only import load_ms
    return load_ms("ms_settings_house", SCRIPT)   # the house profile, never the live ms.yaml (10-03)


NEW_ENV = "vllm-030-cu132"
COSY_RETIRE_NEW = {"audio8-asr", "chatterbox-turbo"}
COSY_RETIRE_OLD = {"cosyvoice", "cosyvoice_vllm"}


def test_the_house_profile_is_todays_behaviour():
    ms = _module()
    assert ms._S.SETTINGS["_sources"][0].endswith("ms-reference-profile.yaml")
    conv = {"KNOWN_ROLES": lambda v: {k: list(x) for k, x in v.items()}, "RETIRED_ENVS": sorted,
            "GPU_SHARED_ENVS": sorted, "OPS_VLLM": list, "CREW_VLLM": list, "HERMES_CREW": list,
            "KANBAN_WORKERS": list, "OPTIONAL_HEALTH": sorted, "VENVS": sorted}
    # {repo}-relative: the legacy value was the live tree's absolute path
    # LIVE_MANIFEST is {repo}-relative now (checked below); VENVS is what is on disk today, not a
    # moved literal (09-29: `voxtral-cpu-trial` appeared between the snapshot and the next run)
    # step 4: the canary endpoints are an ordered list (CANARY_ENDPOINTS) and the 0.5B spec carries
    # its paths; both checked below against the legacy values
    skip = {"LIVE_MANIFEST", "VENVS", "OPS_VLLM", "CREW_VLLM", "CANARY_05B"}
    for key, want in LEGACY.items():
        if key in skip:
            continue
        got = conv.get(key, lambda v: v)(getattr(ms, key))
        # 09-30, the engine switch (on purpose, the User): ops serves from a NEW env, registered report-only,
        # and the canary's env is that env. Everything else must still equal the legacy snapshot.
        if key == "CANARY_ENV":
            assert got == NEW_ENV
            continue
        if isinstance(got, dict) and NEW_ENV in got:
            got = {k: v for k, v in got.items() if k != NEW_ENV}
        elif isinstance(got, list) and NEW_ENV in got:
            got = [x for x in got if x != NEW_ENV]
        # 10-07, cosy-retire (on purpose, the User): the ear's env and the voice's env replace the two Cosy envs,
        # which join retired_envs. Everything else must still equal the legacy snapshot.
        if key == "RETIRED_ENVS":
            want = sorted(set(want) | COSY_RETIRE_OLD)
        elif isinstance(got, dict):
            got = {k: v for k, v in got.items() if k not in COSY_RETIRE_NEW}
            want = {k: v for k, v in want.items() if k not in COSY_RETIRE_OLD} if isinstance(want, dict) else want
        elif isinstance(got, list):
            got = [x for x in got if x not in COSY_RETIRE_NEW]
            want = [x for x in want if x not in COSY_RETIRE_OLD] if isinstance(want, list) else want
        if key == "KNOWN_ROLES":
            assert "crew" in got["vllm-0271-cu132"][0] and NEW_ENV in got["vllm-0271-cu132"][0]
            got = {**got, "vllm-0271-cu132": want["vllm-0271-cu132"]}
        if not isinstance(want, str):   # krew: the snapshot carries the reference profile's placeholders
            want = json.loads(ms_defaults_only.render(json.dumps(want).replace("{repo}", str(ROOT))))
        if isinstance(want, str):
            got = str(got)
            if key in HOME_KEYS:   # ~-relative in the old code and the new; snapshot taken under the reference home
                want = str(Path(want).expanduser())
            want = ms_defaults_only.render(want.replace("{repo}", str(ROOT)))   # the rendered reference profile
        assert json.loads(json.dumps(got)) == want, key
    assert str(ms.LIVE_MANIFEST) == ms_defaults_only.render("${KREW_HOME}/ms/MANIFEST-SYSTEM-LIVE.md")
    eps = ms.CANARY_ENDPOINTS
    assert [eps[0]["url"], eps[0]["model"]] == LEGACY["OPS_VLLM"]
    assert [eps[1]["url"], eps[1]["model"]] == LEGACY["CREW_VLLM"] and eps[1]["prefer_in"] == ["beast"]
    # the User 09-30 (canary brief item 1): util + free_mib_min resized to the measured 0.5B, on purpose
    # 09-30 engine switch: the 0.5B starts from the 0.30 env (peak 2656 MiB -> free_mib_min 2912)
    canary_want = {**json.loads(ms_defaults_only.render(json.dumps(LEGACY["CANARY_05B"]))), "util": "0.025", "free_mib_min": 2912}
    for k in ("vllm_bin", "path"):
        if k in canary_want:
            canary_want[k] = canary_want[k].replace("vllm-0271-cu132", NEW_ENV)
    assert {k: ms.CANARY_05B[k] for k in LEGACY["CANARY_05B"]} == canary_want
    assert ms._kanban_argv("hermes") == ["hermes", "-p", "ada"]
    assigned = {k: ms._assignee(k) for k in ("drift", "land", "diagnose", "summary", "doctor",
                                             "security", "mitigation", "render")}
    assert assigned == {"drift": "ada", "land": "steward", "diagnose": "bash", "summary": "ada",
                        "doctor": "ada", "security": "ada", "mitigation": "bash", "render": "ada"}


def test_no_house_means_no_envs_no_board_no_pager(tmp_path):
    state = tmp_path / "state"
    got = _probe({"MS_INTEGRATIONS": "none", "XDG_STATE_HOME": str(state), "HOME": str(tmp_path)}, tmp_path)
    assert got["sources"] == []
    assert got["OUT_ROOT"] == str(tmp_path / ".krew" / "ms")   # krew: $KREW_HOME/ms, never XDG
    for k in ("VENVS", "KNOWN_ROLES", "SMOKE_IMPORT", "SERVICE_ENVS_ALL"):
        assert got[k] == [], k
    assert got["CANARY_ENV"] == "" and got["MS_KANBAN_PROFILE"] == "" and got["argv"] == ["hermes"]
    assert got["NOTIFY_CMD"] == [] and got["page"] == "no notify.cmd"
    assert got["kanban_default"] is False and got["LIVE_MANIFEST_ON"] is False
    assert got["DELIVERABLES_DIR"] == str(tmp_path / ".krew" / "ms" / "deliverables")
    assert got["MS_ENV_HOLD_FILE"] == str(tmp_path / ".krew" / "ms" / "env-hold.json")
    assert got["MS_MITIGATION_MAX_ENVS"] == 3 and got["assignee_drift"] == ""
    assert got["occupancy"] == "normal" and got["profiles"] == {}
    assert got["render"].get("skipped") == "render integration off"


def test_state_is_under_krew_home(tmp_path):
    got = _probe({"MS_INTEGRATIONS": "none", "HOME": str(tmp_path), "KREW_HOME": str(tmp_path / "kh")}, tmp_path)
    assert got["OUT_ROOT"] == str(tmp_path / "kh" / "ms")


def test_ms_yaml_overrides_the_profile_and_env_overrides_both(tmp_path):
    user = tmp_path / "ms.yaml"
    user.write_text("kanban:\n  profile: builder\n  assignees:\n    drift: builder\n"
                    "notify:\n  cmd: [/bin/echo, hi]\n")
    got = _probe({"MS_SETTINGS": str(user)}, tmp_path)
    assert got["sources"][-1] == str(user) and got["sources"][0].endswith("ms-reference-profile.yaml")
    assert got["MS_KANBAN_PROFILE"] == "builder" and got["assignee_drift"] == "builder"
    assert got["NOTIFY_CMD"] == ["/bin/echo", "hi"]
    assert "zoey" not in " ".join(got["NOTIFY_CMD"])
    assert got["KNOWN_ROLES"]                      # the rest of the house profile still applies
    got = _probe({"MS_SETTINGS": str(user), "MS_KANBAN_PROFILE": "ops-board"}, tmp_path)
    assert got["MS_KANBAN_PROFILE"] == "ops-board"


def test_a_user_file_can_turn_the_house_off(tmp_path):
    user = tmp_path / "ms.yaml"
    user.write_text(f"integrations: none\nstate_dir: {tmp_path / 'st'}\n")
    got = _probe({"MS_SETTINGS": str(user)}, tmp_path)
    assert got["KNOWN_ROLES"] == [] and got["OUT_ROOT"] == str(tmp_path / "st")


def test_the_moved_literals_are_gone_from_the_code():
    src = SCRIPT.read_text()
    assert "/data/" not in src and "/home/" not in src   # krew: no machine path at all (the reference listed three)
    for literal in ('CANARY_ENV = "vllm-0271-cu132"',
                    'os.environ.get("MS_KANBAN_PROFILE", "ada")',
                    '"--assignee", "ada"', '"--assignee", "steward"',
                    '"http://127.0.0.1:8191/health"',
                    '"zoey-page.py"', 'HERMES_CREW = ("ada"'):
        assert literal not in src, literal


def test_paths_handed_to_the_libs_are_expanded(tmp_path):
    """09-29 proof run: export_env() handed env_roots MS_ROOTS_FILE='{repo}/...' unexpanded,
    and every roots row failed FileNotFoundError. Placeholders expand in ms_settings itself."""
    probe = (
        "import importlib.util, json, os, sys\n"
        "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(json.dumps({k: os.environ.get(k, '') for k in ('MS_ROOTS_FILE', 'MS_OUT', 'MS_ENVS_ROOT')}"
        " | {'roots_file': str(m._roots.ROOTS_FILE), 'exists': m._roots.ROOTS_FILE.is_file()}))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MS_",))}
    env["MS_PROFILE_FILE"] = str(ms_defaults_only.REF_PROFILE)
    out = subprocess.run([sys.executable, "-c", probe, str(SCRIPT)], capture_output=True, text=True,
                         env=env, timeout=180, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    for k in ("MS_ROOTS_FILE", "MS_OUT", "MS_ENVS_ROOT"):
        assert "{" not in got[k] and "~" not in got[k], (k, got[k])
    assert got["roots_file"] == str(ms_defaults_only.ROOTS_FILE) and got["exists"]


def test_the_reference_profile_block_is_intact():
    """The reference install's settings sit commented out in config/ms.example.yaml: the block must parse whole,
    keep its placeholders, and the live keys above it (upgrades off, discovery) must stay what they are."""
    import yaml
    text = (ROOT / "config" / "ms.example.yaml").read_text()
    live = yaml.safe_load(text)
    assert live["upgrades"] is False and "discovery" in live and "envs" not in live
    d = ms_defaults_only.reference_settings()
    assert d["upgrades"] is True and d["state_dir"] == "${KREW_HOME}/ms" and d["env_roots"] == ["${MS_ENVS_ROOT}"]
    assert len(d["envs"]) >= 16 and d["render"]["env"] == "comfyui" and d["canary"]["env"] == "vllm-030-cu132"
    assert "/data/" not in text and "/home/" not in text
