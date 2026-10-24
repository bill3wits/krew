"""The conftest's launcher guard: only a healthy launcher is ever put back, and only a change AWAY from the user's
own install fails a test (2026-10-10: a guard that restored whatever it saw at test start could undo a repair)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("krew_conftest_under_test", Path(__file__).with_name("conftest.py"))
C = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(C)

OWN = str(C._REAL_HOME / ".hermes" / "tools" / "python-3.14.7+20260901-linux-x64" / "bin" / "python3")


def _launcher(interp: str) -> bytes:
    return f"#!/bin/sh\nexec {interp} -I -c 'from hermes_cli.main import main' \"$@\"\n".encode()


def test_own_interpreter_is_healthy():
    assert C._healthy(_launcher(OWN))


def test_scratch_home_interpreter_is_not():
    assert not C._healthy(_launcher("/tmp/kanban-env-clean.AbCdEf/.hermes/tools/python3"))


def test_a_lookalike_prefix_is_not():
    # ~/.hermes-old/... must not pass for ~/.hermes/...
    assert not C._healthy(_launcher(str(C._REAL_HOME) + "/.hermes-old/tools/python3"))


def test_missing_or_empty_is_not():
    assert not C._healthy(None)
    assert not C._healthy(b"")
    assert not C._healthy(b"#!/bin/sh\necho no exec line\n")
