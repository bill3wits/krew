"""A fresh install files no cards by itself (2026-10-12, before the public release): every card generator is off or
capped by default, `--enable all` included, and the README's "Card generators" table says how to turn each on."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _unit(name: str) -> str:
    for d in ("crew/units", "units"):
        p = ROOT / d / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise AssertionError(name)


def test_the_auto_decomposer_is_off_in_the_template_init_writes():
    text = (ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8")
    assert re.search(r"^  auto_decompose: false\b", text, re.M) and not re.search(r"^  auto_decompose: true", text, re.M)


def test_kanban_loop_cuts_and_decomposes_nothing_by_default():
    u = _unit("krew-kanban-loop.service")
    assert "Environment=MAX_CUTS_PER_TICK=0" in u and "Environment=MAX_DECOMPOSE_PER_TICK=0" in u


def test_the_board_gate_files_no_review_or_route_cards_by_default():
    u = _unit("krew-hold-sweep.service")
    assert "Environment=KREW_REVIEW_GATE=0" in u and "Environment=KREW_OWNERLESS_GATE=0" in u


def test_adas_board_beat_is_created_paused():
    row = json.loads((ROOT / "crew" / "config" / "profile-kit.json").read_text(encoding="utf-8"))["crons"]["kanban-heartbeat"]
    assert row["paused"] is True
    src = (ROOT / "crew" / "scripts" / "krew-crew-install.py").read_text(encoding="utf-8")
    assert 'if j.get("paused"):' in src and '"--paused", "--paused-reason"' in src


def test_the_steward_sweep_is_opt_in_and_the_breaker_reports():
    assert "# krew: opt-in" in _unit("steward-sweep.timer")
    gate = (ROOT / "crew" / "scripts" / "kanban-board-gate.py").read_text(encoding="utf-8")
    assert 'os.environ.get("KREW_CARD_BREAKER", "report")' in gate


def test_the_improvement_filer_is_capped():
    src = (ROOT / "crew" / "scripts" / "steward-file-architect-cards.py").read_text(encoding="utf-8")
    assert 'os.environ.get("IMPROVE_FILE_CAP", "2")' in src and 'os.environ.get("TEAM_EVAL_QUORUM", "3")' in src


def test_the_readme_says_how_to_turn_each_on():
    sec = README.split("### Card generators on a fresh install", 1)[1].split("**The brains are a preset**", 1)[0]
    for needle in ("auto_decompose: false", "MAX_CUTS_PER_TICK=1", "MAX_DECOMPOSE_PER_TICK=1", "KREW_REVIEW_GATE=1",
                   "KREW_OWNERLESS_GATE=1", "IMPROVE_FILE_CAP", "cron resume kanban-heartbeat", "--enable steward-sweep",
                   "KREW_CARD_BREAKER=report"):
        assert needle in sec, needle
