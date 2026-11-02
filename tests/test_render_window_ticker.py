"""The render slot's 2-min ticker lives on its own user units, not the architect install's chain.

The architect notes (logs / world / stack) are a SIDE BRANCH off krew-window-tick, and the
slot only opens once the evening chain (pose-freelance + improvement-outcomes) has a verdict.
render-window.py is a standing 2-min poller that refuses to open the slot before that verdict
— so it must tick on its own timer, independently of the architect collect jobs (commit
1ea02161 shipped the unit files but no install step ever copied them live; the House Daily's
watch entry for krew-render-window.service was silently silent since then).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMER = (ROOT / "crew" / "units" / "krew-render-window.timer").read_text()
SERVICE = (ROOT / "crew" / "units" / "krew-render-window.service").read_text()
INSTALL = (ROOT / "crew" / "scripts" / "krew-architect-window-install.sh").read_text()


def test_render_window_units_are_a_standing_2min_user_ticker():
    # its own timer (the slot gate is a clock poll, not a window-chain event)
    assert "OnUnitActiveSec=2min" in TIMER
    # no Persistent (the User 2026-10-02): on a monotonic timer its stamp stalled the timer after a session restart
    assert not re.search(r"^\s*Persistent\s*=", TIMER, re.M)
    assert "WantedBy=timers.target" in TIMER
    # drives render-window.py tick and honours the day label the window units carry
    assert "render-window.py tick" in SERVICE
    assert "Type=oneshot" in SERVICE


def test_the_architect_window_install_enables_the_render_window_ticker():
    # the units ship to the user's systemd and the timer is enabled there
    assert 'install -m 0644 "$R"/config/systemd/user/krew-render-window.{service,timer} "$U/"' in INSTALL
    assert "systemctl --user daemon-reload" in INSTALL
    assert "systemctl --user enable --now krew-render-window.timer" in INSTALL
    # ... but it is NOT chained onto the window ticker (independent of the architect collect jobs)
    code = "\n".join(l for l in INSTALL.splitlines() if not l.lstrip().startswith("#"))
    step = re.search(r"4/4.*?^python3", code, re.S | re.M)
    assert step
    assert "krew-window-tick" not in step.group(0)
    # and the install proves the slot sees the ticker
    assert 'systemctl --user list-timers --all --no-pager | grep "krew-render-window"' in INSTALL


def test_the_render_slot_refuses_to_open_before_the_evening_chain_verdict():
    # the independence is real: the slot gate reads the chain's verdict, so a missing chain
    # cannot be papered over by an eager ticker
    script = (ROOT / "crew" / "scripts" / "render-window.py").read_text()
    m = re.search(r"CHAIN_JOBS\s*=\s*\((.*?)\)", script)
    assert m and "pose-freelance" in m.group(1) and "improvement-outcomes" in m.group(1)
    assert "the evening chain has not finished" in script


def test_the_three_session_restart_timers_carry_no_persistent_stamp():
    """The User 2026-10-02: restart-window, render-window and ms-survey stalled after the 05:42 session restart -
    Persistent=true on a monotonic-only timer left a stamp the new manager read as 'fired this boot'."""
    for rel in ("units/krew-restart-window.timer", "crew/units/krew-render-window.timer",
                "units/krew-ms-survey.timer"):
        text = (ROOT / rel).read_text()
        assert "OnUnitActiveSec=" in text and not re.search(r"^\s*Persistent\s*=", text, re.M), rel
