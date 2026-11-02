"""`krew-mode render` is the last door a kanban worker could park ops through
(the owner 2026-09-17; krew-rails-15's find: bash's worker ran `krew-mode render turbo`
at 13:41 and stopped ops and Crissa's gateway in the middle of the User's day).

The rule is not restated here — it is lib/render_window_gate, the same one
render-submit and krew-comfy ask. This proves krew-mode asks it, and that the
User, Crissa and hand runs are never gated."""
from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox  # noqa: E402
hm = krew_mode_sandbox.load("krew_mode", "crew/scripts/krew-mode")   # never the passwd tree
import render_window_gate as gate  # noqa: E402

DAY = dt.datetime(2026, 9, 17, 13, 41)
NIGHT = dt.datetime(2026, 9, 17, 22, 0)
WINDOW = {"source": "init", "window": "20:00-02:00"}


def _worker(monkeypatch, *, card="t_5df2faa8", hours=WINDOW, exempt=False, released=False):
    monkeypatch.setenv("HERMES_KANBAN_TASK", card)
    monkeypatch.delenv("KREW_MODE_WORKER_WINDOW", raising=False)
    monkeypatch.delenv("KREW_RENDER_WINDOW", raising=False)
    import krew_hours
    monkeypatch.setattr(krew_hours, "off_hours", lambda *a, **k: dict(hours))
    monkeypatch.setattr(gate, "enabled", lambda: hours["source"] == "init")
    monkeypatch.setattr(gate, "card_exempt", lambda c=None: exempt)
    monkeypatch.setattr(gate, "in_released_batch", lambda c=None, now=None: released)


def test_a_worker_is_refused_in_the_users_day(monkeypatch):
    _worker(monkeypatch)
    why = hm.worker_render_refusal(DAY)
    assert why and "off-hours" in why and "t_5df2faa8" in why and "KREW_MODE_WORKER_WINDOW=0" in why


def test_at_night_it_still_waits_for_adas_released_batch(monkeypatch):
    _worker(monkeypatch)
    assert "released batch" in hm.worker_render_refusal(NIGHT)
    _worker(monkeypatch, released=True)
    assert hm.worker_render_refusal(NIGHT) == ""


def test_the_user_crissa_and_hand_runs_are_never_gated(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KREW_MODE_WORKER_WINDOW", raising=False)
    assert hm.worker_render_refusal(DAY) == ""


def test_render_now_on_the_card_passes_by_day(monkeypatch):
    _worker(monkeypatch, exempt=True)
    assert hm.worker_render_refusal(DAY) == ""


def test_no_window_written_enforces_nothing(monkeypatch):
    _worker(monkeypatch, hours={"source": "unset", "window": None})
    assert hm.worker_render_refusal(DAY) == ""


def test_the_kill_switch_opens_it(monkeypatch):
    _worker(monkeypatch)
    monkeypatch.setenv("KREW_MODE_WORKER_WINDOW", "0")
    assert hm.worker_render_refusal(DAY) == ""


def test_the_refusal_is_wired_in_before_the_render_runs():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    main = src.split("def main(", 1)[1]
    assert 'if args.cmd in WORKER_GATED_CMDS:\n        why = worker_gpu_refusal(args.cmd)' in main
    assert main.index("worker_gpu_refusal(args.cmd)") < main.index("cmd_render(args.recipe")
    assert main.index("worker_gpu_refusal(args.cmd)") < main.index("cmd_beast(")


# --- the owner 2026-09-17, two judgment calls (krew-rails-14) -------------------------

def test_beast_and_off_are_gated_too_normal_never(monkeypatch):
    """`beast` and `off` park the brains as surely as `render`; `normal` is the way back."""
    assert set(hm.WORKER_GATED_CMDS) == {"render", "beast", "off"} and "normal" not in hm.WORKER_GATED_CMDS
    _worker(monkeypatch)
    for cmd in ("beast", "off"):
        why = hm.worker_gpu_refusal(cmd, now=NIGHT)                # not in a render batch, even at night
        assert why.startswith(f"krew-mode {cmd}:") and "render-now" in why
    # a card in tonight's released RENDER batch may render — not go Beast or off
    _worker(monkeypatch, released=True)
    assert hm.worker_gpu_refusal("render", now=NIGHT) == ""
    for cmd in ("beast", "off"):
        assert "is not a render" in hm.worker_gpu_refusal(cmd, now=NIGHT)
    _worker(monkeypatch, exempt=True)
    assert hm.worker_gpu_refusal("beast", now=DAY) == ""            # an explicit word from Crissa / the User
    # no window written: the gate enforces nothing, for any verb
    _worker(monkeypatch, hours={"source": "unset", "window": None})
    assert hm.worker_gpu_refusal("beast", now=DAY) == ""


def test_an_unreadable_gate_refuses_a_worker_and_only_a_worker(monkeypatch):
    """Fail closed, like the M&S ledger: "could not check" is not yes."""
    _worker(monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("site.json unreadable")
    monkeypatch.setattr(gate, "refuse_reason", boom)
    why = hm.worker_gpu_refusal("render", now=DAY)
    assert "could not be read" in why and "t_5df2faa8" in why and "RuntimeError" in why
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert hm.worker_gpu_refusal("render", now=DAY) == ""            # a hand run is never refused
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_5df2faa8")
    monkeypatch.setenv("KREW_MODE_WORKER_WINDOW", "0")
    assert hm.worker_gpu_refusal("render", now=DAY) == ""            # the kill still opens it
