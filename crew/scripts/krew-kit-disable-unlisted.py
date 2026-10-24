#!/usr/bin/env python3
"""A hermes update's bundled-skill sync must not add a skill a profile's allowlist excludes (the User 2026-10-02).

09-26 18:09 a hermes update reset the live tree to origin/main and its bundled-skill sync copied
`autonomous-ai-agents/hermes-agent` into every profile. The allowlist keeps it for most of the crew, but judge's
row is empty (house skills only). Nothing disabled it: krew-kit-reapply runs krew-config-restore only when a
config would change, and restore re-applies the kit only to a config it rewrote - a sync that adds skill FILES
changes no config, so both steps passed. The pin check found it on 10-02, when judge joined the kit's CREW.

This closes that gap with the pin check's own rule: for every profile in krew-kitting's CREW, every skill on
its disk that is neither kept (allowlist + house skills) nor already in `skills.disabled` is ADDED to
`skills.disabled` in its live config. Additive only: it never removes a name and never enables one, so with
nothing new on disk it changes nothing (no config change, no gateway bounce). krew-kit-reapply runs it after
the YAML step on every update trigger, before the pin check.

    krew-kit-disable-unlisted.py [--dry-run] [--profiles-root DIR]

Kill: KREW_KIT_DISABLE_UNLISTED=0.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # krew: the pinned Hermes venv has ruamel.yaml, not PyYAML
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import krew_yaml as yaml  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
KITTING = Path(os.path.expanduser(os.environ.get("KREW_KITTING_PLUGIN") or os.path.join(
    os.environ.get("HERMES_HOME") or "~/.hermes", "plugins", "krew-kitting", "__init__.py")))   # krew: the live plugin


def kitting():
    spec = importlib.util.spec_from_file_location("krew_kitting_unlisted", KITTING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def unlisted(mod, profile_dir: Path, allowlist: dict, catalog: set[str]) -> list[str]:
    """On-disk names the pin check would call 'neither allowed nor disabled'."""
    kit = mod.plan_profile(profile_dir.name, profile_dir / "skills", allowlist=allowlist, catalog=catalog)
    try:
        data = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    live_dis = set((data.get("skills") or {}).get("disabled") or [])
    return sorted(set(kit.discovered) - kit.allowed - live_dis)


def disable(mod, cfg: Path, names: list[str]) -> None:
    text = cfg.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    now = list((data.get("skills") or {}).get("disabled") or [])
    out = mod._replace_list_under(text, "skills", "disabled", sorted(set(now) | set(names)))
    tmp = cfg.with_name(cfg.name + ".unlisted-tmp")
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, cfg)


def run(profiles_root: Path, *, write: bool) -> dict[str, list[str]]:
    mod = kitting()
    allowlist = mod.load_allowlist()
    catalog = mod.krew_skill_catalog()
    found: dict[str, list[str]] = {}
    for name in mod.CREW:
        pdir = profiles_root / name
        if not (pdir / "config.yaml").is_file() or not (pdir / "skills").is_dir():
            continue
        extra = unlisted(mod, pdir, allowlist, catalog)
        if extra:
            found[name] = extra
            if write:
                disable(mod, pdir / "config.yaml", extra)
    if write and found:
        mod._clear_skill_cache()
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profiles-root", default=str(Path.home() / ".hermes" / "profiles"))
    a = ap.parse_args(argv)
    if os.environ.get("KREW_KIT_DISABLE_UNLISTED", "1").strip().lower() in ("0", "false", "no", "off"):
        print("disable-unlisted: off (KREW_KIT_DISABLE_UNLISTED=0)")
        return 0
    found = run(Path(a.profiles_root), write=not a.dry_run)
    verb = "would disable" if a.dry_run else "disabled"
    if not found:
        print("disable-unlisted: every on-disk skill is kept or disabled")
    for name, names in found.items():
        print(f"disable-unlisted: {name} {verb} {len(names)}: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
