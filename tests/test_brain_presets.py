"""The crew's brains ship as two named presets and no placeholder (the User 2026-10-10).

engines = this box's own vLLM servers (Zoey on ops :8000, everyone else on crew :8005); xai = xAI's API for both,
the model xAI's docs recommend (grok-4.7, checked 2026-10-10), the key the user's own at install.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import krew_install_init as init  # noqa: E402

PRESETS = json.loads((ROOT / "crew" / "config" / "brain-presets.json").read_text())
PLACEHOLDER = re.compile(r"your[-_ ]model|model[-_ ]name|<model>|CHANGEME|TODO|example\.com|xxx", re.I)


def test_both_presets_name_real_brains_and_carry_no_placeholder():
    assert set(PRESETS["presets"]) == {"engines", "xai", "claude"} and PRESETS["default"] == "engines"
    for name, p in PRESETS["presets"].items():
        for brain in ("ops", "crew"):
            b = p[brain]
            assert re.match(r"https?://[^/\s]+/v1$", b["base_url"]), (name, brain, b)
            assert b["model"] and not PLACEHOLDER.search(b["model"]) and not PLACEHOLDER.search(b["base_url"]), (name, b)
    e, x = PRESETS["presets"]["engines"], PRESETS["presets"]["xai"]
    assert (e["ops"]["base_url"], e["ops"]["model"]) == ("http://127.0.0.1:8000/v1", "ops")
    assert (e["crew"]["base_url"], e["crew"]["model"]) == ("http://127.0.0.1:8005/v1", "crew")
    assert x["ops"]["base_url"] == x["crew"]["base_url"] == "https://api.x.ai/v1"
    assert x["ops"]["model"] == x["crew"]["model"] == "grok-4.7" and x["crew"]["key_env"] == "XAI_API_KEY"
    assert not e["ops"]["key_env"] and not e["crew"]["key_env"]
    c = PRESETS["presets"]["claude"]
    assert c["ops"]["base_url"] == c["crew"]["base_url"] == "https://api.anthropic.com/v1"
    assert c["ops"]["model"] == c["crew"]["model"] == "claude-fable-5-1" and c["crew"]["key_env"] == "ANTHROPIC_API_KEY"


def test_no_placeholder_model_anywhere_krew_ships():
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
    bad = []
    for rel in r.stdout.split():
        if rel.startswith("tests/") or not rel.endswith((".py", ".yaml", ".yml", ".json", ".md", ".example", ".env")):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if re.search(r"your-model-name", text):
            bad.append(rel)
    assert not bad, bad


def test_resolve_takes_the_preset_and_a_custom_endpoint_over_both():
    assert init.resolve_brains("")["crew"]["model"] == "crew"
    x = init.resolve_brains("xai")
    assert x["ops"]["base_url"] == "https://api.x.ai/v1" and x["name"] == "xai"
    c = init.resolve_brains("xai", "http://127.0.0.1:9/v1")
    assert c["ops"]["base_url"] == c["crew"]["base_url"] == "http://127.0.0.1:9/v1" and c["crew"]["model"] == "grok-4.7"
    with pytest.raises(SystemExit):
        init.resolve_brains("nope")


def _model(cfg: Path) -> tuple[str, str]:
    t = cfg.read_text()
    return (re.search(r"^  base_url: (\S+)", t, re.M).group(1), re.search(r"^  default: (\S+)", t, re.M).group(1))


@pytest.mark.parametrize("preset", ["engines", "xai", "claude"])
def test_init_writes_zoey_on_ops_and_the_rest_on_crew(tmp_path, preset):
    hh, kh = tmp_path / "h", tmp_path / "k"
    # a stand-in `hermes` that only logs its argv, first on PATH and named by KREW_HERMES_BIN: init's crons and plugin
    # steps must never reach the real hermes (2026-10-10: a first cut of this test let `cron create` find it on PATH)
    fake = tmp_path / "bin" / "hermes"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho \"$@\" >> \"$(dirname \"$0\")/calls.log\"\n")
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KREW_", "HERMES_", "XAI_"))}
    env.update(HOME=str(tmp_path), ZOEYOS_HOME=str(tmp_path / "no-zoeyos"), ZOEYOS_PLAYER="http://127.0.0.1:9",
               KREW_HERMES_BIN=str(fake), PATH=str(fake.parent) + ":/usr/bin:/bin")
    args = [sys.executable, str(ROOT / "scripts" / "krew-install.py"), "init", "--hermes-home", str(hh), "--krew-home",
            str(kh), "--unit-dir", str(tmp_path / "units"), "--no-hermes", "--no-discover", "--yes", "--brains", preset,
            "--hermes-bin", str(fake)]
    if preset != "engines":
        args += ["--api-key", "test-key-value"]
    r = subprocess.run(args, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    p = PRESETS["presets"][preset]
    assert _model(hh / "profiles" / "zoey" / "config.yaml") == (p["ops"]["base_url"], p["ops"]["model"])
    for prof in ("ada", "bash", "steward", "vera", "lila", "scout", "judge"):
        assert _model(hh / "profiles" / prof / "config.yaml") == (p["crew"]["base_url"], p["crew"]["model"]), prof
    assert _model(hh / "config.yaml") == (p["crew"]["base_url"], p["crew"]["model"])
    every = "".join(f.read_text() for f in hh.rglob("config.yaml"))
    assert "your-model-name" not in every
    if preset != "engines":
        var = p["crew"]["key_env"]
        assert (hh / ".env").read_text() == f"{var}=test-key-value\n" and "test-key-value" not in every
        assert f"key_env: {var}" in (hh / "profiles" / "bash" / "config.yaml").read_text()
    else:
        assert not (hh / ".env").exists() and "key_env:" not in (hh / "profiles" / "bash" / "config.yaml").read_text()
    assert f"brain    ops: {p['ops']['base_url']} model {p['ops']['model']}" in r.stdout
    assert (fake.parent / "calls.log").exists()                    # the crons went to the stand-in, nowhere else
