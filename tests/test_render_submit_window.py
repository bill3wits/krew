"""render-submit uses the shared render-window gate (lib/render_window_gate.py)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_submit_calls_the_gate_and_blocks_with_the_window_mark():
    code = "\n".join(
        l for l in (ROOT / "crew" / "scripts" / "render-submit.py").read_text().splitlines()
        if not l.lstrip().startswith("#")
    )
    assert "render_window_gate.refuse_reason(a.card)" in code
    i = code.index("render_window_gate.refuse_reason(a.card)")
    tail = code[i:i + 700]
    assert 'kanban("block", a.card, why)' in tail
    assert "render_window_gate.MARK" in tail
    assert "return 3" in tail
    win = (ROOT / "crew" / "scripts" / "render-window.py").read_text()
    assert 'MARK = "gate: render window"' in win
    gate = (ROOT / "crew" / "lib" / "render_window_gate.py").read_text()
    assert 'MARK = "gate: render window"' in gate
