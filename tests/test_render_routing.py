"""Render open draws direct; a closed machine orders or waits (the owner 2026-10-08)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_render_open_draws_direct_and_a_closed_machine_orders_or_waits():
    kr = (ROOT / "skills/krew-render/SKILL.md").read_text(encoding="utf-8")
    rl = (ROOT / "skills/render-lane/SKILL.md").read_text(encoding="utf-8")
    zc = (ROOT / "skills/crissa-render-cycle/SKILL.md").read_text(encoding="utf-8")
    for s in (kr, rl, zc):
        assert "render-orders.py add" in s and "look_compare" in s
        assert ":8188" in s
        assert "qwen-image-2512" in s and "qwen-edit-2511" in s and "leaves the machine" in s
        assert "the default path" not in s
        assert "While Render is open" in s or "Once Render is open" in s
    assert "krew-render-runner.py" in kr and "krew-render-runner.py" in zc and "krew-render-runner.py" in rl
    assert "An **order** is only" in kr and "An **order** is only" in rl
    assert "file an order, do not enter the mode" not in rl
    assert "Retired" in rl
    for s in (kr, rl):
        assert "Never run `systemctl` from a card" in s
        assert "before you block" in s
    assert "put the machine back in normal" in kr
