"""The 0.5B canary sized to the model, not the card, and a skipped canary that cannot stay quiet
(the User 09-30, canary brief items 1-2). History: tests/fixtures/silent_skips/canary-20260917-29.json
(the real 09-17..09-29 nights, from ms-trail-<day>.json)."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
sys.path.insert(0, str(ROOT / "ms" / "lib"))
import ms_silent_skips as S  # noqa: E402

spec = importlib.util.spec_from_file_location("ms_trail_canary", ROOT / "ms" / "scripts" / "ms-trail-run.py")
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)

CARD = 97887                   # this card's memory.total (nvidia-smi)
PEAK = 2276                    # the 0.5B's measured peak, 09-30, every util 0.015-0.04
ENV = "vllm-0271-cu132"
HIST = json.loads((ROOT / "tests/fixtures/silent_skips/canary-20260917-29.json").read_text())["nights"]


# --- item 1: the floor ---------------------------------------------------------------------

def test_the_floor_stays_under_4_gb_on_this_card():
    assert ms.canary_05b_floor_mib(CARD) < 4096


def test_the_floor_covers_the_measured_peak_and_vllms_own_precheck():
    floor = ms.canary_05b_floor_mib(CARD)
    util = float(ms.CANARY_05B["util"])
    assert floor >= PEAK + 256                               # room for what it really uses
    assert floor >= int(util * CARD) + 1 + 256               # vLLM never refuses a launch we make
    assert util * CARD > PEAK                                # vLLM's precheck still means something


def test_every_skipped_night_would_have_run():
    frees = {d: int(re.search(r"free (\d+) MiB", r["applied"][ENV]["canary"]).group(1))
             for d, r in HIST.items() if "free" in r["applied"].get(ENV, {}).get("canary", "")}
    assert len(frees) == 12 and min(frees.values()) == 3275 and max(frees.values()) == 7043
    assert all(f >= ms.canary_05b_floor_mib(CARD) for f in frees.values())


# --- item 2: 3 nights -> one capability card (the Daily's "Needs you") ------------------------------------------------

@pytest.fixture
def board(monkeypatch):
    calls = []

    def run(argv, timeout=30):
        calls.append(list(argv))
        return 0, "Created t_0badc0de  (ready, assignee=ada)", ""
    monkeypatch.setattr(ms, "run", run)
    monkeypatch.setattr(ms, "_kanban_argv", lambda *a: ["hermes"])
    monkeypatch.setattr(ms, "notify_user", lambda *a, **k: pytest.fail("no page when the board is on"))
    real_get = ms._S.get
    monkeypatch.setattr(ms._S, "get", lambda k, d=None: True if k == "kanban.enabled" else real_get(k, d))
    monkeypatch.setattr(ms, "CANARY_SKIP_CARD", True)
    return calls


def _replay(tmp_path):
    """The real nights through the silent-skip counter and the trail's own card hook."""
    fp = tmp_path / "findings.json"
    per_night = {}
    for day in sorted(HIST):
        changes = S.apply(day, S.observe_ms_report(HIST[day]), findings_path=fp)
        per_night[day] = ms.canary_skip_cards(changes, day)
    return per_night


def test_the_third_night_raises_one_capability_card_and_later_nights_none(tmp_path, board):
    cards = _replay(tmp_path)
    raised = {d: c for d, c in cards.items() if c}
    assert list(raised) == ["2026-09-19"]                    # 09-17, 09-18 quiet; 09-19 is night 3
    assert raised["2026-09-19"] == [{"key": f"silent-skip:canary:{ENV}", "sent": "card t_0badc0de"}]
    creates = [c for c in board if c[1:3] == ["kanban", "create"]]
    blocks = [c for c in board if c[1:3] == ["kanban", "block"]]
    assert len(creates) == 1 and len(blocks) == 1
    create = creates[0]
    assert create[create.index("--idempotency-key") + 1] == f"silent-skip:canary:{ENV}"
    assert create[create.index("--assignee") + 1] == "ada"
    assert blocks[0][3] == "t_0badc0de" and blocks[0][-2:] == ["--kind", "capability"]
    body = create[create.index("--body") + 1]
    for night, free in (("2026-09-17", 3387), ("2026-09-18", 5177), ("2026-09-19", 6501)):
        assert night in body and f"free {free} MiB" in body
    assert ENV in create[3] and "3 nights" in create[3]


def test_the_kill_switch_writes_no_card(tmp_path, board, monkeypatch):
    monkeypatch.setattr(ms, "CANARY_SKIP_CARD", False)
    cards = _replay(tmp_path)
    assert cards["2026-09-19"] == [{"key": f"silent-skip:canary:{ENV}", "sent": "off (MS_CANARY_SKIP_CARD=0)"}]
    assert board == []


def test_only_the_canary_opening_raises_a_card(board):
    other = [S.Change("silent-skip:smoke:hindsight", "opened", {"env": "hindsight"}),
             S.Change(f"silent-skip:canary:{ENV}", "updated", {"env": ENV}),
             S.Change(f"silent-skip:canary:{ENV}", "closed", {"env": ENV})]
    assert ms.canary_skip_cards(other, "2026-09-30") == [] and board == []
