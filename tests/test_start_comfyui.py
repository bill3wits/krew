"""start-comfyui.sh gives every caller a clean Comfy start (the owner 2026-10-08): the crew worker's Python path
(PYTHONPATH into the Hermes venv, PYTHONHOME, VIRTUAL_ENV) and its HERMES_KANBAN_* ids never reach Comfy, so a
crew card, the panel's button or a hand launch the same process. The crew rule stands beside it: never systemctl
from a card; a failed Render goes back to normal."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import ROOT

SCRIPT = ROOT / "crew" / "scripts" / "start-comfyui.sh"


def _fake_comfy(tmp_path: Path) -> Path:
    root = tmp_path / "ComfyUI"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "main.py").write_text("")
    py = root / "venv" / "bin" / "python"
    py.write_text("#!/bin/sh\nenv | sort > \"$COMFY_ENV_OUT\"\necho \"$@\" >> \"$COMFY_ENV_OUT\"\n")
    py.chmod(0o755)
    return root


def test_the_worker_python_path_and_card_ids_never_reach_comfy(tmp_path):
    root = _fake_comfy(tmp_path)
    mode = tmp_path / "gpu-mode"
    mode.write_text("render\n")
    out = tmp_path / "env.txt"
    env = dict(os.environ, KREW_GPU_MODE_FILE=str(mode), KREW_COMFY_ROOT=str(root), COMFY_ENV_OUT=str(out),
               PYTHONPATH="/x/hermes/venv/lib/python3.14/site-packages", PYTHONHOME="/x/hermes",
               VIRTUAL_ENV="/x/hermes/venv", HERMES_KANBAN_TASK="t_deadbeef", HERMES_KANBAN_BOARD="b",
               HERMES_KANBAN_DB="/x/k.db", KREW_COMFY_ARGS="")
    r = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    got = dict(ln.split("=", 1) for ln in out.read_text().splitlines() if "=" in ln)
    assert "PYTHONPATH" not in got and "PYTHONHOME" not in got
    assert not [k for k in got if k.startswith("HERMES_KANBAN_")], got
    assert got["VIRTUAL_ENV"] == str(root / "venv")          # Comfy's own venv, never the caller's
    assert got["PATH"].startswith(str(root / "venv" / "bin"))
    assert "--port 8188 --listen 127.0.0.1" in out.read_text()


def test_it_still_refuses_outside_render(tmp_path):
    root = _fake_comfy(tmp_path)
    mode = tmp_path / "gpu-mode"
    mode.write_text("normal\n")
    env = dict(os.environ, KREW_GPU_MODE_FILE=str(mode), KREW_COMFY_ROOT=str(root), COMFY_ENV_OUT=str(tmp_path / "e"))
    r = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 78 and "need render" in r.stderr and not (tmp_path / "e").exists()


def test_the_crew_rule_is_written_where_the_renderers_read_it():
    for rel in ("skills/krew-render/SKILL.md", "skills/render-lane/SKILL.md", "souls/TRUNK.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "systemctl" in text and "back" in text and "normal" in text, rel
    assert "Never run `systemctl` from a card" in (ROOT / "souls" / "TRUNK.md").read_text(encoding="utf-8")
