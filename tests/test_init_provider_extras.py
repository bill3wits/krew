"""A brain's provider package is installed by init, before the first call (crissatest 2026-10-31: krew 1.0.3 on the
claude brain ended "init done" and Crissa's first call failed until the gateway was restarted)."""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("krew_install_init", ROOT / "scripts" / "krew_install_init.py")
I = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(I)

CLAUDE = {"ops": {"base_url": "https://api.anthropic.com/v1"}, "crew": {"base_url": "https://api.anthropic.com/v1"}}
ENGINES = {"ops": {"base_url": "http://127.0.0.1:8000/v1"}, "crew": {"base_url": "http://127.0.0.1:8005/v1"}}


def _hermes(tmp_path, rc=0):
    log = tmp_path / "argv.txt"
    h = tmp_path / "hermes"
    h.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit {rc}\n')
    h.chmod(h.stat().st_mode | stat.S_IEXEC)
    return str(h), log


def _run(brains, hermes, monkeypatch=None, **kw):
    said = []
    steps = I.Steps(said.append)
    kw = dict(dict(dry=False, scratch=False, no_hermes=False), **kw)
    need = I.provider_extras(brains, hermes, said.append, steps=steps, **kw)
    return need, steps, said


def test_the_claude_brain_installs_the_anthropic_extra(tmp_path):
    h, log = _hermes(tmp_path)
    need, steps, _ = _run(CLAUDE, h)
    assert need == ["anthropic"]
    assert log.read_text().split() == ["pm", "install", "--extra", "anthropic"]   # once, not once per brain
    assert steps.rows == [("provider packages", "ok", "")]


def test_local_engines_install_nothing(tmp_path):
    h, log = _hermes(tmp_path)
    need, steps, _ = _run(ENGINES, h)
    assert need == [] and not log.exists() and steps.rows == []


def test_a_failed_install_fails_init(tmp_path):
    h, _log = _hermes(tmp_path, rc=3)
    _need, steps, _ = _run(CLAUDE, h)
    assert steps.failed() and steps.rows[0][1] == "FAILED"


def test_a_scratch_install_never_runs_hermes(tmp_path):
    h, log = _hermes(tmp_path)
    _need, steps, _ = _run(CLAUDE, h, scratch=True)
    assert not log.exists() and steps.rows[0][1] == "off"


def test_the_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_INIT_PROVIDER_EXTRAS", "0")
    h, log = _hermes(tmp_path)
    _need, steps, _ = _run(CLAUDE, h)
    assert not log.exists() and steps.rows == [("provider packages", "off", "KREW_INIT_PROVIDER_EXTRAS=0")]


def test_init_installs_the_extra_before_the_pinned_writes():
    src = (ROOT / "scripts" / "krew_install_init.py").read_text(encoding="utf-8")
    assert src.index("    provider_extras(brains, hermes") < src.index("    pinned_writes(hh, kh, hermes")
