"""Desktop markdown crystals need an HTML sibling."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

import importlib.machinery
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_loader(
    "crew_crystal_check",
    importlib.machinery.SourceFileLoader(
        "crew_crystal_check", str(ROOT / "janitor" / "krew-crystal-check")
    ),
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_desktop_md_without_html_is_not_complete(tmp_path, monkeypatch):
    inbox = tmp_path / "deliverables"
    inbox.mkdir()
    md = inbox / "ask-2026-09-06-example.md"
    md.write_text("# Ask\n\nLeave it as-is. Tradeoffs and risks named.\n", encoding="utf-8")
    monkeypatch.setattr(mod, "DELIVERABLES", inbox)
    ok, crystals, errors = mod.evaluate(
        summary="done", artifacts=[str(md)], metadata={}, workspace=None
    )
    assert ok is False
    assert any(c.startswith("A:") for c in crystals)
    assert any("HTML sibling" in e for e in errors)


def test_desktop_md_with_html_sibling_completes(tmp_path, monkeypatch):
    inbox = tmp_path / "deliverables"
    inbox.mkdir()
    md = inbox / "ask-2026-09-06-example.md"
    md.write_text("# Ask\n\nLeave it as-is. Tradeoffs and risks named.\n", encoding="utf-8")
    html = md.with_suffix(".html")
    html.write_text("<h1>Ask</h1>", encoding="utf-8")
    monkeypatch.setattr(mod, "DELIVERABLES", inbox)
    ok, crystals, errors = mod.evaluate(
        summary="done", artifacts=[str(md)], metadata={}, workspace=None
    )
    assert ok is True
    assert any("HTML sibling" not in e for e in errors) or not errors
    assert any(c.startswith("A:") for c in crystals)


def test_applied_keep_without_kpi_is_not_complete(tmp_path, monkeypatch):
    inbox = tmp_path / "deliverables"
    inbox.mkdir()
    md = inbox / "land.md"
    md.write_text("# land\n\nfastsafetensors applied.\n", encoding="utf-8")
    md.with_suffix(".html").write_text("<h1>land</h1>", encoding="utf-8")
    led = tmp_path / "decisions.json"
    led.write_text(
        '{"decisions":[{"env":"vllm","package":"fastsafetensors",'
        '"decision":"applied","unlock":{"disposition":"keep","claim":"x"}}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "DELIVERABLES", inbox)
    monkeypatch.setattr(mod, "DECISIONS", led)
    ok, crystals, errors = mod.evaluate(
        summary="applied fastsafetensors",
        artifacts=[str(md)],
        metadata={},
        workspace=None,
    )
    assert ok is False
    assert any("kpi_id" in e for e in errors)


# --- 2026-09-12: a deliverable that runs must show its run --------------------

def _harness(tmp_path, monkeypatch):
    inbox = tmp_path / "deliverables"
    d = inbox / "drift-recon-harness"
    d.mkdir(parents=True)
    (d / "recon_harness.py").write_text("print('hi')\n")
    (d / "README.md").write_text("# harness\n")
    monkeypatch.setattr(mod, "DELIVERABLES", inbox)
    return d


def test_runnable_folder_without_a_run_line_is_not_complete(tmp_path, monkeypatch):
    d = _harness(tmp_path, monkeypatch)
    ok, crystals, errors = mod.evaluate(
        summary="Harness delivered. The AC is genuinely verified: the mock worker runs cleanly (rc=0, no timeout).",
        artifacts=[str(d)], metadata={}, workspace=None)
    assert ok is False
    assert any("runnable deliverable" in e and "ran: <exact command> rc=<n>" in e for e in errors)


def test_runnable_folder_with_ran_line_completes(tmp_path, monkeypatch):
    d = _harness(tmp_path, monkeypatch)
    ok, crystals, errors = mod.evaluate(
        summary="Harness delivered.\nran: /usr/bin/python3 recon_harness.py --self-test --run-dir /tmp/x rc=0",
        artifacts=[str(d)], metadata={}, workspace=None)
    assert ok is True and "R:ran rc=0" in crystals


def test_runnable_script_with_metadata_ran_completes(tmp_path, monkeypatch):
    inbox = tmp_path / "deliverables"; inbox.mkdir()
    sh = inbox / "probe.sh"; sh.write_text("#!/bin/sh\ntrue\n")
    monkeypatch.setattr(mod, "DELIVERABLES", inbox)
    ok, crystals, errors = mod.evaluate(summary="probe delivered", artifacts=[str(sh)],
                                        metadata={"ran": {"cmd": "sh probe.sh", "rc": 0}}, workspace=None)
    assert ok is True and "R:ran rc=0" in crystals


def test_non_runnable_deliverable_is_unaffected(tmp_path, monkeypatch):
    inbox = tmp_path / "deliverables"; inbox.mkdir()
    md = inbox / "note.md"; md.write_text("# note\n\nA plain written deliverable, long enough to be durable (>= 40 bytes).\n")
    md.with_suffix(".html").write_text("<h1>n</h1>")
    monkeypatch.setattr(mod, "DELIVERABLES", inbox)
    ok, crystals, errors = mod.evaluate(summary="done", artifacts=[str(md)], metadata={}, workspace=None)
    assert ok is True and not [e for e in errors if "runnable" in e]
