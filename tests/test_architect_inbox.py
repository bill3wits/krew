"""Archive-by-filename-date + deny glob fixtures."""
from __future__ import annotations

import sys
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "scripts"))

from architect_lib import (  # noqa: E402
    DENY_SHOULD_MATCH,
    DENY_SHOULD_MISS,
    deny_matches,
    filename_date,
    is_report_name,
    kpi_history_n,
    kpi_insufficient,
    plan_archive_moves,
    register_kpi,
)


def test_filename_dates():
    assert filename_date("architect-world-2026-08-24.md") == date(2026, 8, 24)
    assert filename_date("daily-digest-20260825.md") == date(2026, 8, 25)
    assert filename_date("spike-foo-bar-2026-08-20.md") == date(2026, 8, 20)
    assert filename_date("team-research-2026-08-25.md") == date(2026, 8, 25)
    assert filename_date("readme.txt") is None


def test_report_prefixes():
    assert is_report_name("architect-stack-2026-08-24.md")
    assert is_report_name("ms-2026-08-24.md")
    assert is_report_name("daily-digest-20260824.md")
    assert is_report_name("team-research-2026-08-24.md")
    assert is_report_name("team-sources-2026-08-01.md")
    assert is_report_name("ops-efficiency-2026-08-25.html")
    assert is_report_name("ops-efficiency-2026-08-25.json")
    assert is_report_name("team-daily-2026-09-09.html")
    assert not is_report_name("dep-review-summary-2026-08-21.md")


def test_plan_moves_by_file_date(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "architect-world-2026-08-20.md").write_text("old")
    (inbox / "architect-world-2026-08-25.md").write_text("today")
    (inbox / "dep-review-2026-08-19.md").write_text("kanban")
    (inbox / "no-date.md").write_text("leave")
    (inbox / "krew-renders").mkdir()
    moves = plan_archive_moves(inbox, as_of=date(2026, 8, 25))
    names = {src.name: str(dest) for src, dest in moves}
    assert "architect-world-2026-08-20.md" in names
    assert "krew-reports/2026-08-20" in names["architect-world-2026-08-20.md"]
    assert "dep-review-2026-08-19.md" in names
    assert "kanban-archive/2026-08-19" in names["dep-review-2026-08-19.md"]
    assert "architect-world-2026-08-25.md" not in names
    assert "no-date.md" not in names


def test_deny_globs_match_and_miss():
    for cmd in DENY_SHOULD_MATCH:
        assert deny_matches(cmd), f"should deny {cmd!r}"
    for cmd in DENY_SHOULD_MISS:
        assert deny_matches(cmd) is None, f"should allow {cmd!r}"


def test_kpi_history_file_exists_is_not_enough():
    hist = kpi_history_n(
        {
            "kanban_blocked": {"history": [{}, {}, {}]},
            "kanban_oldest_ready_sec": {"history": []},
        }
    )
    assert hist["kanban_blocked"] == 3
    assert hist["kanban_oldest_ready_sec"] == 0
    assert kpi_insufficient(hist) == ["kanban_oldest_ready_sec"]


def test_register_kpi_is_idempotent(tmp_path: Path):
    path = tmp_path / "kpis.json"
    a = register_kpi(
        kpi_id="ops_tok_s",
        name="Ops decode tok/s",
        unit="tok/s",
        direction="up",
        how_measured="speed_probe.py --lane ops",
        sample="adhoc",
        path=path,
    )
    b = register_kpi(
        kpi_id="ops_tok_s",
        name="Ops decode tok/s",
        unit="tok/s",
        direction="up",
        how_measured="speed_probe.py --lane ops",
        sample="adhoc",
        path=path,
    )
    assert a["kpi_id"] == "ops_tok_s"
    assert b["history"] == []
    assert a["sample"] == "adhoc"
    data = json.loads(path.read_text())
    assert list(data["kpis"]) == ["ops_tok_s"]
