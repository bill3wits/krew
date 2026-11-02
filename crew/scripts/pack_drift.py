"""Classify pack vs live profile YAML: occupancy pins are not DRIFT.

krew-mode and TurboKrew rewrite live ``model.provider`` / ``base_url``,
``custom_providers``, Render ``default_assignee``, bash spawn caps. A
whole-file pack clobber puts Vera/Steward back on crew while Turbo is on,
or Ada's decomposer onto the shared ops 27B. Pack-drift-check used to flag
those as DRIFT and Krew filed clobber cards.

Ada ``kanban_decomposer.api_key`` (a local placeholder) and
``skills.disabled`` pack pins (``kanban-orchestration-patterns``) are pack
truth — those still DRIFT if live is missing them.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - krew: the pinned Hermes venv has ruamel.yaml
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import krew_yaml as yaml  # noqa: E402
    except ImportError:
        yaml = None  # type: ignore

VOLATILE_MODEL = ("provider", "base_url", "default", "extra_body")
VOLATILE_KANBAN = ("default_assignee",)
VOLATILE_DELEGATION = ("max_concurrent_children", "max_spawn_depth")
VOLATILE_AUX_ROUTE = ("provider", "model", "base_url")


def _load(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("pyyaml required")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _strip_occupancy(data: dict[str, Any]) -> dict[str, Any]:
    d = copy.deepcopy(data)
    model = d.get("model")
    if isinstance(model, dict):
        for k in VOLATILE_MODEL:
            model.pop(k, None)
    kanban = d.get("kanban")
    if isinstance(kanban, dict):
        for k in VOLATILE_KANBAN:
            kanban.pop(k, None)
    deleg = d.get("delegation")
    if isinstance(deleg, dict):
        for k in VOLATILE_DELEGATION:
            deleg.pop(k, None)
    d.pop("custom_providers", None)
    aux = d.get("auxiliary")
    if isinstance(aux, dict):
        for name in list(aux.keys()):
            blk = aux[name]
            if isinstance(blk, dict):
                for k in VOLATILE_AUX_ROUTE:
                    blk.pop(k, None)
                # An aux route block that reduces to no keys after the
                # volatile strip carries no house intent (kitting noise) —
                # drop it so it does not manufacture a pack-vs-live diff.
                if not blk:
                    del aux[name]
        if not aux:
            d.pop("auxiliary", None)
    skills = d.get("skills")
    if isinstance(skills, dict):
        skills.pop("disabled", None)  # pack-subset checked separately; extras are kitting
    return d


def classify_config(pack: Path, live: Path) -> str:
    """``match`` | ``pin`` | ``drift``.

    ``pin``: only occupancy / Turbo / Render routing differs.
    ``drift``: toolkit pins or other pack truth differs (decomposer key,
    missing ``skills.disabled`` entries, toolsets, …).
    """
    if not pack.is_file() or not live.is_file():
        return "drift"
    if pack.read_bytes() == live.read_bytes():
        return "match"
    try:
        p, l = _load(pack), _load(live)
    except Exception:
        return "drift"
    # Pack-required disables must be present live; extra live disables are kitting.
    p_dis = set((p.get("skills") or {}).get("disabled") or [])
    l_dis = set((l.get("skills") or {}).get("disabled") or [])
    if p_dis - l_dis:
        return "drift"
    stripped_eq = _strip_occupancy(p) == _strip_occupancy(l)
    if stripped_eq:
        if p == l:
            return "match"
        return "pin"
    return "drift"


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pack")
    ap.add_argument("live")
    args = ap.parse_args(argv)
    print(classify_config(Path(args.pack), Path(args.live)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
