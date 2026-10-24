"""The two brains ship with krew (the owner 2026-10-08: a person who builds the reference box and installs the packs
gets the same experience): the engine script, its environment lock, the chat template with its licence, the unit,
the example settings, and no machine-specific path in any of them."""
from __future__ import annotations

import json
import re
import subprocess

from conftest import ROOT

ENG = ROOT / "engines"


def test_the_engine_files_are_present_and_packaged():
    pk = json.loads((ROOT / "config" / "krew-packages.json").read_text())["packages"]
    files = pk["engines"]["files"]
    for f in files:
        assert (ROOT / f).is_file(), f
    assert "config/engines.example.env" in pk["config"]["files"]
    assert (ROOT / "units" / "krew-engine@.service").is_file()


def test_the_scripts_parse_and_refuse_a_zero_kv_pin(tmp_path):
    for s in ("start-engine.sh", "vllm-env.sh"):
        subprocess.run(["bash", "-n", str(ENG / s)], check=True)
    r = subprocess.run(["bash", str(ENG / "start-engine.sh"), "zoey"], capture_output=True, text=True)
    assert r.returncode == 2 and "ops or crew" in r.stderr
    home = tmp_path / "krew"
    home.mkdir()
    (home / "engines.env").write_text("OPS_KV_BYTES=0\n")
    env = {"HOME": str(tmp_path), "KREW_HOME": str(home), "PATH": "/usr/bin:/bin", "KREW_VLLM_ENV": str(tmp_path / "none")}
    r = subprocess.run(["bash", str(ENG / "start-engine.sh"), "ops"], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and ("KV_BYTES" in r.stderr or "no bin/vllm" in r.stderr)
    (home / "gpu-mode").write_text("render\n")
    r = subprocess.run(["bash", str(ENG / "start-engine.sh"), "ops"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "parked" in r.stdout


def test_the_lock_the_template_and_the_settings_say_what_they_are():
    lock = (ENG / "vllm-0271-cu132.lock.txt").read_text()
    assert re.search(r"^vllm==0\.27\.1$", lock, re.M) and re.search(r"^torch==2\.13", lock, re.M)
    src = (ENG / "chat-templates" / "SOURCE.txt").read_text()
    assert "Apache-2.0" in src and "froggeric" in src
    assert "template_version" in (ENG / "chat-templates" / "qwen3.8-froggeric-v22.1.jinja").read_text()[:200]
    ex = (ROOT / "config" / "engines.example.env").read_text()
    for k in ("OPS_PORT=8000", "CREW_PORT=8005", "OPS_KV_BYTES=", "CREW_KV_BYTES=", "CREW_SPEC_TOKENS=0", "OPS_SPEC_TOKENS=3"):
        assert k in ex, k
    unit = (ROOT / "units" / "krew-engine@.service").read_text()
    assert "{krew}/engines/start-engine.sh %i" in unit and "MemoryMax=" in unit


def test_no_machine_of_ours_is_named():
    bad = re.compile("|".join(("/home/[a-z]+/", "/da" + "ta/en" + "vs", "/da" + "ta/hf", "projects/ho" + "use-rails")))
    for f in list(ENG.rglob("*")) + [ROOT / "config" / "engines.example.env", ROOT / "units" / "krew-engine@.service"]:
        if f.is_file() and f.suffix != ".jinja":
            text = f.read_text(errors="replace")
            m = bad.search(text)
            assert not m, (f.name, m.group(0))


def test_the_reference_box_map_names_both_packs():
    doc = (ENG / "README.md").read_text()
    for w in ("ops", "crew", "Crissa pack", "krew", "Composer", "studio-only", "oracle"):
        assert w in doc, w


def test_a_dry_run_renders_the_reference_commands(tmp_path):
    """KREW_ENGINE_DRY=1 prints the vLLM command and starts nothing: ops and crew as the reference box runs them
    (read off its live units 2026-10-08)."""
    home = tmp_path / "krew"
    home.mkdir()
    ex = (ROOT / "config" / "engines.example.env").read_text().replace("$HF_HOME", str(tmp_path))
    (home / "engines.env").write_text(ex)
    env = {"HOME": str(tmp_path), "KREW_HOME": str(home), "PATH": "/usr/bin:/bin", "KREW_ENGINE_DRY": "1"}
    out = {}
    for name in ("ops", "crew"):
        r = subprocess.run(["bash", str(ENG / "start-engine.sh"), name], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        out[name] = r.stdout.splitlines()
    ops, crew = out["ops"], out["crew"]
    assert "--served-model-name" in ops and ops[ops.index("--served-model-name") + 1] == "ops"
    assert ops[ops.index("--port") + 1] == "8000" and crew[crew.index("--port") + 1] == "8005"
    assert ops[ops.index("--max-model-len") + 1] == "131072" and crew[crew.index("--max-model-len") + 1] == "102400"
    assert ops[ops.index("--kv-cache-memory-bytes") + 1] == "15032385536"
    assert crew[crew.index("--kv-cache-memory-bytes") + 1] == "15554576384"
    assert any("num_speculative_tokens\":3" in a for a in ops) and not any("speculative" in a for a in crew)
    for argv in (ops, crew):
        assert "--scheduling-policy" in argv and "--enable-prefix-caching" in argv and "--kv-cache-dtype" in argv
        assert argv[argv.index("--attention-backend") + 1] == "triton_attn"
        assert argv[argv.index("--chat-template") + 1].endswith("qwen3.8-froggeric-v22.1.jinja")
    assert "--enable-sleep-mode" in crew and "--enable-sleep-mode" not in ops
    assert "--max-num-batched-tokens" in ops and "--max-num-batched-tokens" not in crew
