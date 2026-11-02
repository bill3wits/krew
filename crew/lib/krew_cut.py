"""Coach, don't cap: the one way crew code shortens text.

A cut always says so: how much was kept, how long the whole was, and where the rest can be read. Text that fits
comes back untouched. Nothing in crew/ shortens agent-facing text any other way."""
from __future__ import annotations


def cut(text, n: int, more: str = "") -> str:
    text = "" if text is None else str(text)
    if len(text) <= n:
        return text
    where = f"; {more}" if more else ""
    return f"{text[:n]} [cut at {n} of {len(text)} chars{where}]"
