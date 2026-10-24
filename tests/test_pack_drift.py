"""Occupancy / Turbo pins are not pack DRIFT; toolkit pins still are (lifted from the reference tests 2026-10-10).
Reads crew/scripts/pack_drift.py (classify_config), crew/scripts/pack-drift-check.sh, crew/scripts/krew-config-restore.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import ROOT

yaml = pytest.importorskip("yaml")
sys.path.insert(0, str(ROOT / "crew" / "scripts"))
from pack_drift import classify_config  # noqa: E402

SH = ROOT / "crew" / "scripts" / "pack-drift-check.sh"


def _write(p: Path, data: dict) -> Path:
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


def test_byte_identical_is_match(tmp_path):
    d = {"model": {"provider": "custom:crew", "base_url": "http://127.0.0.1:8005/v1"}}
    a, b = _write(tmp_path / "p.yaml", d), _write(tmp_path / "l.yaml", d)
    assert classify_config(a, b) == "match"


def test_turbo_ops_pin_is_pin_not_drift(tmp_path):
    pack = {
        "model": {"provider": "custom:crew", "base_url": "http://127.0.0.1:8005/v1", "default": "crew"},
        "kanban": {"default_assignee": "steward"},
        "custom_providers": [{"name": "crew", "base_url": "http://127.0.0.1:8005/v1"}],
        "skills": {"disabled": ["airtable"]},
    }
    live = {
        "model": {
            "provider": "custom:ops",
            "base_url": "http://127.0.0.1:8000/v1",
            "default": "ops",
            "extra_body": {"priority": 50},
        },
        "kanban": {"default_assignee": "steward"},
        "custom_providers": [
            {"name": "crew", "base_url": "http://127.0.0.1:8005/v1"},
            {"name": "ops", "base_url": "http://127.0.0.1:8000/v1", "extra_body": {"priority": 50}},
        ],
        "skills": {"disabled": ["airtable"]},
    }
    assert classify_config(_write(tmp_path / "p.yaml", pack), _write(tmp_path / "l.yaml", live)) == "pin"


def test_missing_decomposer_key_is_drift(tmp_path):
    pack = {
        "model": {"provider": "custom:crew", "base_url": "http://127.0.0.1:8005/v1"},
        "auxiliary": {"kanban_decomposer": {
            "provider": "custom", "model": "crew",
            "base_url": "http://127.0.0.1:8005/v1",
            "api_key": "sk-local-decomposer",
        }},
        "skills": {"disabled": ["kanban-orchestration-patterns"]},
    }
    live = {
        "model": {"provider": "custom:crew", "base_url": "http://127.0.0.1:8005/v1"},
        "auxiliary": {"kanban_decomposer": {
            "provider": "auto", "model": "crew",
        }},
        "skills": {"disabled": ["kanban-orchestration-patterns"]},
    }
    assert classify_config(_write(tmp_path / "p.yaml", pack), _write(tmp_path / "l.yaml", live)) == "drift"


def test_missing_orchestration_disable_is_drift(tmp_path):
    pack = {"skills": {"disabled": ["kanban-orchestration-patterns", "airtable"]}}
    live = {"skills": {"disabled": ["airtable", "apple-notes"]}}
    assert classify_config(_write(tmp_path / "p.yaml", pack), _write(tmp_path / "l.yaml", live)) == "drift"


def test_kitting_extra_disables_are_not_drift(tmp_path):
    pack = {"skills": {"disabled": ["kanban-orchestration-patterns"]}}
    live = {"skills": {"disabled": ["airtable", "kanban-orchestration-patterns", "apple-notes"]}}
    assert classify_config(_write(tmp_path / "p.yaml", pack), _write(tmp_path / "l.yaml", live)) == "pin"


# Dropped from the reference: test_pack_sync_indexes_the_tools_routine (reads the reference's docs/PACK-SYNC.md and
# docs/KANBAN-AND-HERMES-PINS.md, which krew does not ship).


def test_pack_drift_check_script_uses_classifier():
    src = SH.read_text(encoding="utf-8")
    assert "pack_drift.py" in src
    assert "PIN" in src
    # the reference pins its decomposer key's literal; krew's scrub denies a key-shaped literal in shipped code, so the
    # script names the key it checks instead (2026-10-10)
    assert "kanban_decomposer.api_key" in src
    restore = (ROOT / "crew" / "scripts" / "krew-config-restore.py").read_text(encoding="utf-8")
    assert "_repin_turbo_krew" in restore


def test_pack_drift_check_script_has_stale_ref_guard():
    # While origin/main is ancient AND HEAD far ahead, the check must log UNVERIFIED and file no card instead of
    # mass-filing the tree. The guard probes the remote (no creds needed) and skips only when it can PROVE the ref
    # is stale.
    src = SH.read_text(encoding="utf-8")
    assert "UNVERIFIED" in src
    assert "PACK_DRIFT_FORCE" in src
    assert "git rev-list --count" in src
    assert "git fetch origin" in src


def test_pack_drift_check_reports_and_files_no_card_by_default():
    """The owner 2026-09-18 ("they never get their updates and so correct our corrections. Stop them"): a drift card
    sent the crew to copy pack -> live over hand edits. Report only; card on request."""
    sh = SH.read_text()
    tail = sh[sh.index("BODY="):]
    assert 'if [ "${KREW_PACK_DRIFT_CARDS:-0}" = "1" ]' in tail
    assert tail.index('KREW_PACK_DRIFT_CARDS:-0') < tail.index('$H kanban create "pack drift:')
    assert "report-only" in tail
