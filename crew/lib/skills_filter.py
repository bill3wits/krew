"""filter_skills — the pure drop-decision for the board-gate skill strip (t_85a2d164).

The gate loop (kanban-board-gate.py `gate_skills_filter`) currently inlines the
decision as `dropped = [s for s in pins if s not in reg]` right before it calls
kanban-skills-add.py --remove. This module extracts exactly that decision into one
pure function so it can be unit-tested without the sqlite board, the profile
homes, or the removal verb.

Contract (2026-09-26 spec, krew-deliverables/2026-09-26-filter-skills-spec.md):
    filter_skills(card_skills, profile_skills) -> list of the DROPPED pins, in
    card order. The function only decides; the caller performs the removal.

Pure by contract: no I/O, no mutation of either input, no globals beyond this
signature, no time. Same inputs -> same list, always.
"""
from __future__ import annotations

__all__ = ["filter_skills"]


def filter_skills(card_skills: list[str], profile_skills: set[str] | None) -> list[str]:
    """Return the pins on `card_skills` that are absent from `profile_skills`.

    Parameters
    ----------
    card_skills:
        Current pins on the card, order preserved (the gate's `_skills_of` list).
    profile_skills:
        The skill names in the assignee's registry — the parent dirs of every
        `SKILL.md` under `<profile>/skills/**/`. `None` when the profile home or
        its skills dir is missing or unreadable.

    Returns
    -------
    list[str]
        The DROPPED pins, in card order: ``[s for s in card_skills if s not in
        profile_skills]``. Every absent name is reported; duplicates in
        `card_skills` are each reported (no dedupe here — dedupe is the caller's
        job if it wants it).

    Edge cases
    ----------
    - `card_skills` empty -> ``[]``.
    - `profile_skills` is ``None`` -> ``[]``. There is nothing to compare
      against, so the card is skipped — never stripped to zero (the gate's
      ``if reg is None: continue``).
    - `profile_skills` empty (``set()``) -> every pin drops. An empty registry
      resolves nothing; this mirrors the current gate and is intended.
    - Matching is case-sensitive and exact (set membership only) — no lowercasing,
      no substring or prefix matching. A pin equal to a directory that has no
      `SKILL.md` is foreign, because the registry is built from `SKILL.md` files
      only.
    """
    if not card_skills or profile_skills is None:
        return []
    return [s for s in card_skills if s not in profile_skills]
