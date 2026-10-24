#!/usr/bin/env python3
"""Re-apply the house's settings on top of live configs — without discarding
anything a Hermes version update added.

THE PROBLEM THIS SOLVES (User 2026-08-11: "we do want configs to update with
version updates BUT to RESTORE local settings")

`hermes update` legitimately rewrites live configs: new keys, new defaults,
schema migrations. We want those. But it also flattens house decisions — the
cosy TTS provider, the local brain pins, tier0 hooks, toolset grants — and
nobody notices until something is quietly wrong days later.

The old tool had only two moves, and both lose something:
  deploy  (cp pack -> live)  keeps the house, DISCARDS every upstream addition
  capture (cp live -> pack)  keeps upstream, ENSHRINES whatever clobbered us

This is the third move. It DEEP-MERGES the pack over live:
  * a key the pack sets      -> house value wins        (our settings restored)
  * a key only live has      -> left exactly as it is   (upstream additions kept)
  * nested dicts             -> merged recursively, not replaced wholesale
  * lists                    -> replaced by the pack's list

Lists replace rather than append on purpose: `toolsets` is a list, and appending
would make it impossible to ever REMOVE a toolset from a profile. That is also
the BRIDGE landmine: a pack `toolsets: [clarify]` replaces the live 30-toolset
list. Read the dry-run. Authority: docs/PACK-SYNC.md.

THIS TOOL DOES NOT TOUCH SOUL.md. targets() is YAML only (config.yaml,
profile.yaml, BRIDGE). A SOUL or SKILL change is a per-file cp / skills deploy.

USAGE
  krew-config-restore.py            # dry run — show what would change
  krew-config-restore.py --apply    # write it
  krew-config-restore.py --apply --profile zoey

AFTER EVERY `hermes update`, RUN THIS. It is idempotent: a second run is a
no-op, so running it when nothing drifted costs nothing.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    try:  # krew: the pinned Hermes venv has ruamel.yaml, not PyYAML
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import krew_yaml as yaml  # noqa: E402
    except ImportError:
        sys.exit("needs PyYAML or ruamel.yaml — run with the hermes venv python")

HOME = Path.home()
PACKS = Path(os.path.expanduser(os.environ.get("KREW_PACKS_ROOT") or "~/.krew/packs")) / "distributions"
LIVE = HOME / ".hermes/profiles"
SCRIPTS = Path(__file__).resolve().parent

# Keys that belong to Hermes, not to the house. The pack has a stale copy of
# these simply because it was captured at some past version — forcing them back
# would UNDO the very update we are trying to keep. `_config_version` is the
# sharp one: restoring the pack's 33 over a migrated 99 tells Hermes the file is
# older than it is, and it will migrate it again.
UPSTREAM_OWNED = {"_config_version"}

# Hermes will not auto-migrate an *explicit* stamp below this (July 2026 floor).
# Scout sat at 1 and was skipped on every `hermes update` sibling pass until
# restore lifted it to the floor and ran `hermes config migrate`. Same for any
# future utility profile. Scout is a first-class sibling (serves Steward).
CONFIG_SUPPORT_FLOOR = 12
CREW = ("ada", "bash", "lila", "scout", "steward", "vera", "zoey")

def _apply_kitting(live_path: Path) -> None:
    """Re-apply the crew skill allowlist after a list-replacing merge.

    Restore replaces ``skills.disabled`` with the pack list. The allowlist
    plugin is the SoT — without this, a restore would re-open new Nous
    names that the pack denylist has never heard of.
    """
    if live_path.name != "config.yaml":
        return
    if live_path.parent.parent != LIVE:
        return
    plugin = (
        Path(os.path.expanduser(os.environ.get("KREW_PACKS_ROOT") or "~/.krew/packs")) / "plugins/krew-kitting/__init__.py"
    )
    if not plugin.is_file():
        print(f"skill allowlist not re-applied: {plugin} is missing")
        return
    spec = importlib.util.spec_from_file_location("krew_kitting", plugin)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    sys.modules["krew_kitting"] = mod
    spec.loader.exec_module(mod)
    if live_path.parent.name not in getattr(mod, "CREW", ()):
        return
    try:
        kit = mod.apply_profile(live_path.parent)
        print(
            f"    kitting allowlist {live_path.parent.name}: "
            f"{len(kit.allowed)} on / {len(kit.disabled)} off"
        )
    except Exception as e:
        print(f"    kitting allowlist FAILED ({type(e).__name__}: {e})")


def _explicit_config_version(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return None
    if not isinstance(data, dict) or "_config_version" not in data:
        return None
    try:
        return max(0, int(data["_config_version"]))
    except (TypeError, ValueError):
        return 0


def _lift_below_floor(path: Path) -> int | None:
    """Rewrite an ancient explicit stamp to the support floor so migrate can run.

    Leaving `_config_version: 1` in place is why scout never climbed: Hermes
    refuses auto-migrate below v12 and leaves the file byte-for-byte.
    """
    text = path.read_text()
    ver = _explicit_config_version(path)
    if ver is None or ver >= CONFIG_SUPPORT_FLOOR:
        return ver
    new, n = re.subn(
        r"^(_config_version:\s*)\d+\s*$",
        r"\g<1>%d" % CONFIG_SUPPORT_FLOOR,
        text,
        count=1,
        flags=re.M,
    )
    if n != 1:
        return ver
    path.write_text(new)
    return CONFIG_SUPPORT_FLOOR


def _migrate_crew_profiles(*, apply: bool) -> None:
    """Equal-citizen schema climb for every named profile, including scout.

    `hermes update` already walks ``~/.hermes/profiles/*`` as siblings. A
    below-floor stamp makes that walk a no-op. Restore is the house post-
    update script, so it lifts the stamp and runs ``hermes config migrate``
    with HERMES_HOME set to that profile.
    """
    hermes = shutil.which("hermes") or str(
        Path.home() / ".local" / "bin" / "hermes"
    )
    for name in CREW:
        live_path = LIVE / name / "config.yaml"
        ver = _explicit_config_version(live_path)
        if ver is None:
            print(f"    migrate {name}: skipped (no config)")
            continue
        if ver >= 39:
            continue
        print(f"    migrate {name}: on-disk {_explicit_config_version(live_path)}")
        if not apply:
            print(f"    migrate {name}: would lift/migrate (currently {ver})")
            continue
        lifted = _lift_below_floor(live_path)
        env = dict(os.environ)
        env["HERMES_HOME"] = str(LIVE / name)
        try:
            r = subprocess.run(
                [hermes, "config", "migrate"],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"    migrate {name}: FAILED ({type(e).__name__}: {e})")
            continue
        after = _explicit_config_version(live_path)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
            print(f"    migrate {name}: FAILED rc={r.returncode} {err[0][:120]}")
            continue
        print(f"    migrate {name}: {lifted or ver} -> {after}")
        _apply_kitting(live_path)


def _repin_ops_for_occupancy() -> None:
    """Pack merge writes 27B ops pins. Beast occupancy must put them back on 122B."""
    if not (SCRIPTS / "krew-mode").is_file():   # krew: the GPU mode script is the owner's own (KREW_MODE_PATH)
        print(f"ops occupancy re-pin not run: {SCRIPTS / 'krew-mode'} is missing")
        return
    spec = importlib.util.spec_from_file_location("krew_mode", SCRIPTS / "krew-mode")
    if spec is None or spec.loader is None:
        print("ops occupancy re-pin skipped (krew-mode load failed)")
        return
    hm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hm)
    mode = hm.read_mode()
    result = hm.apply_ops_occupancy(mode, restart=False)
    if mode == "beast":
        print(f"beast occupancy: ops clients → 122B ({result})")


def _repin_turbo_krew() -> None:
    """Pack merge writes crew mains. TurboKrew lease must put Vera/Steward back on ops.

    Occupancy stays Standard during the hour; restore would otherwise un-share.
    Ada never moves. No gateway bounce.
    """
    if not (SCRIPTS / "krew-turbo-krew.py").is_file():
        print(f"turbo re-pin not run: {SCRIPTS / 'krew-turbo-krew.py'} is missing")
        return
    spec = importlib.util.spec_from_file_location(
        "krew_turbo_krew", SCRIPTS / "krew-turbo-krew.py"
    )
    if spec is None or spec.loader is None:
        print("turbo re-pin skipped (krew-turbo-krew load failed)")
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        snap = mod.apply_occupancy(quiet=True)
    except Exception as e:  # noqa: BLE001
        print(f"turbo re-pin skipped ({type(e).__name__}: {e})")
        return
    if snap.get("on") and snap.get("sharing"):
        print("turbo re-pin: Vera/Steward on ops (Ada crew)")


# (label, pack file, live file). BRIDGE is the root/default profile: its files
# sit at the top of ~/.hermes rather than under profiles/.
def targets(only: str | None) -> list[tuple[str, Path, Path]]:
    out: list[tuple[str, Path, Path]] = []
    for d in sorted(PACKS.iterdir()):
        if not d.is_dir():
            continue
        if only and d.name != only:
            continue
        for name in ("config.yaml", "profile.yaml"):
            p, l = d / name, LIVE / d.name / name
            if p.is_file():
                out.append((f"{d.name}/{name}", p, l))
    if not only or only == "BRIDGE":
        for pack_name, live_path in (
            ("BRIDGE-config.yaml", HOME / ".hermes/config.yaml"),
            ("BRIDGE-profile.yaml", HOME / ".hermes/profile.yaml"),
        ):
            p = PACKS / pack_name
            if p.is_file():
                out.append((pack_name, p, live_path))
    return out


def merge(pack, live, top=True):
    """Pack wins where it speaks; live survives everywhere else."""
    if isinstance(pack, dict) and isinstance(live, dict):
        out = copy.deepcopy(live)
        for k, v in pack.items():
            if top and k in UPSTREAM_OWNED and k in live:
                continue  # Hermes owns this one — never force it backwards
            out[k] = merge(v, live[k], top=False) if k in live else copy.deepcopy(v)
        return out
    return copy.deepcopy(pack)


def describe(pack, live, path="", top=True):
    """Human-readable list of what the merge would change in live."""
    changes: list[str] = []
    if isinstance(pack, dict) and isinstance(live, dict):
        for k, v in pack.items():
            here = f"{path}.{k}" if path else k
            if top and k in UPSTREAM_OWNED and k in live:
                continue
            if k not in live:
                changes.append(f"    + {here}  (house key missing from live — RESTORED)")
            else:
                changes.extend(describe(v, live[k], here, top=False))
    elif pack != live:
        p, l = str(pack)[:60], str(live)[:60]
        changes.append(f"    ~ {path or '(root)'}\n        live: {l}\n        house: {p}")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--profile", help="restore one profile only")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    total_changed = 0
    for label, pack_path, live_path in targets(args.profile):
        if not live_path.is_file():
            print(f"{label}: live missing — installing house copy")
            if args.apply:
                live_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pack_path, live_path)
            total_changed += 1
            continue
        try:
            pack = yaml.safe_load(pack_path.read_text()) or {}
            live = yaml.safe_load(live_path.read_text()) or {}
        except Exception as e:
            print(f"{label}: SKIPPED — unreadable ({e.__class__.__name__}: {e})")
            continue

        if label.endswith("profile.yaml"):
            # 2026-09-18: the Profiles page (live) owns what a profile SAYS it is — Ada's
            # decomposer routes by it — so a pack copy never writes it back over a live edit.
            # The pack learns it by capture, not the other way round.
            for k in ("description", "display_name"):
                pack.pop(k, None)
        merged = merge(pack, live)
        if merged == live:
            print(f"{label}: ok")
            continue

        total_changed += 1
        print(f"{label}: {len(describe(pack, live))} house setting(s) to restore")
        for line in describe(pack, live)[:12]:
            print(line)
        # Anything live has that the pack does not is upstream's — say so, so it
        # is visible that we are NOT throwing it away.
        kept = sorted(set(live) - set(pack))
        if kept:
            print(f"    (keeping {len(kept)} upstream-only top-level key(s): {', '.join(kept[:8])})")
        if args.apply:
            bak = live_path.with_suffix(live_path.suffix + f".pre-restore-{stamp}")
            shutil.copy2(live_path, bak)
            live_path.write_text(yaml.safe_dump(merged, sort_keys=False, width=100))
            print(f"    -> written (previous saved as {bak.name})")
            _apply_kitting(live_path)
    if args.apply:
        _migrate_crew_profiles(apply=True)
        _repin_ops_for_occupancy()
        _repin_turbo_krew()
    else:
        _migrate_crew_profiles(apply=False)

    print()
    if not total_changed:
        print("Nothing to restore — live already carries every house setting.")
    elif args.apply:
        print(f"Restored {total_changed} file(s). Restart the gateways to pick them up.")
    else:
        print(f"{total_changed} file(s) would change. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
