#!/usr/bin/env python3
"""Fail if the Hermes-side pins drifted: skill belts, Crissa's skill preload and SOUL cap, the patch queue on the
Hermes tree, the T0 gate, fallbacks that are the primary, and the crew's SOUL caps.

The Janitor ticks this (its pin tick imports the file and calls check()); krew-kit-reapply.sh runs it after a
reapply; kanban-lean.sh names it as the next step. An input that is not installed here is listed under
`missing` and printed, never skipped silently. The reference install also pins KV bytes, GPU occupancy (Render
switchover) and its packs checkout against the live profiles; those checks stay with that install.
Settings: the process env, else the `env:` mapping of $KREW_HOME/crew.yaml (config/crew.example.yaml).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# THE REAL HOME, not the worker's. A crew profile runs with HOME set to
# ~/.hermes/profiles/<name>/home, so Path.home() there has no
# .config/systemd/user and every run filed a JANITOR RED "missing crew/ops

# units belong to the login user.
import pwd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402
_REAL_HOME = Path(os.environ.get("KREW_REAL_HOME") or pwd.getpwuid(os.getuid()).pw_dir)

def _crew_settings() -> tuple[dict, str]:
    """The `env:` mapping of $KREW_HOME/crew.yaml, read by krew-crew-run's own reader (the Janitor imports this
    file into its own process, so no launcher exported it). ({}, note) when the launcher is not beside this file."""
    run = Path(__file__).resolve().parent / "krew-crew-run"
    if not run.is_file():
        return {}, f"crew settings not read: no krew-crew-run beside {Path(__file__).name} ({run})"
    try:
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("krew_crew_run_settings", str(run))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return dict(mod.settings_env()), ""
    except Exception as e:  # noqa: BLE001
        return {}, f"crew settings not read: {type(e).__name__}: {e}"


_SETTINGS, _SETTINGS_NOTE = _crew_settings()


def _env(name: str, default: str = "") -> str:
    """The process env, else crew.yaml's `env:`, else the code default."""
    v = os.environ.get(name)
    return v if v not in (None, "") else str(_SETTINGS.get(name) or default)


try:
    import yaml as _YAML
except ImportError:  # the pinned Hermes venv has ruamel.yaml, not PyYAML
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    import krew_yaml as _YAML  # noqa: E402

# what this run could not check, and why (check() returns it; the Janitor shows it as a warn)
MISSING: list[str] = []
_TRUNK_FILE = Path(os.path.expanduser(_env("KREW_SOULS_TRUNK") or str(ROOT.parent / "souls" / "TRUNK.md")))
try:
    _TRUNK_TEXT = _TRUNK_FILE.read_text(encoding="utf-8").rstrip()
except OSError:
    _TRUNK_TEXT = ""


def _persona_chars(text: str) -> int:
    """What a SOUL cap measures: the profile's own text. krew-install writes souls/TRUNK.md (the crew-wide
    trunk) ahead of each crew SOUL; the caps were set on SOULs without it."""
    if _TRUNK_TEXT and text.startswith(_TRUNK_TEXT):
        return len(text[len(_TRUNK_TEXT):].lstrip("\n"))
    return len(text)

HERMES_ROOT = KH.hermes_home(passwd=True)
HERMES_PROFILES = HERMES_ROOT / "profiles"
KITTING = Path(os.path.expanduser(_env("KREW_KITTING_PLUGIN") or str(HERMES_ROOT / "plugins" / "krew-kitting" / "__init__.py")))
KREW_HOME = Path(os.path.expanduser(_env("KREW_HOME") or "~/.krew"))


def check_skills() -> list[str]:
    if not KITTING.is_file():
        MISSING.append(f"skill belts not checked: no krew-kitting plugin at {KITTING} "
                       "(scripts/krew-install.py places it; KREW_KITTING_PLUGIN names another)")
        return []
    if not HERMES_PROFILES.is_dir():
        MISSING.append(f"skill belts not checked: no Hermes profiles at {HERMES_PROFILES}")
        return []
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("krew_kitting_pin", KITTING)
        if spec is None or spec.loader is None:
            return ["kitting loader failed"]
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return [f"kitting import: {type(e).__name__}: {e}"]
    fails: list[str] = []
    allow = mod.load_allowlist()
    catalog = mod.krew_skill_catalog()
    for name in mod.CREW:
        skills_dir = HERMES_PROFILES / name / "skills"
        if not skills_dir.is_dir():
            continue
        kit = mod.plan_profile(name, skills_dir, allowlist=allow, catalog=catalog)
        cfg = HERMES_PROFILES / name / "config.yaml"
        live_dis: set[str] = set()
        if cfg.is_file():
            try:
                yaml = _YAML

                data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
                live_dis = set((data.get("skills") or {}).get("disabled") or [])
            except Exception as e:
                fails.append(f"{name} yaml: {type(e).__name__}")
                continue
        on_disk = kit.discovered
        # Allowed names must not be disabled.
        for n in sorted(kit.allowed & on_disk):
            if n in live_dis:
                fails.append(f"{name} allowed skill disabled: {n}")
        # New on-disk names not in allowlist+krew must be disabled.
        extra = on_disk - kit.allowed - live_dis
        if extra:
            fails.append(
                f"{name} {len(extra)} skill(s) on disk neither allowed nor disabled: "
                + ", ".join(sorted(extra)[:8])
            )
    return fails


def _own_lib(name: str):
    """crew/lib/<name>.py by its path: never a same-named module from another tree on sys.path."""
    import importlib.util
    lib = ROOT / "lib"
    path = lib / f"{name}.py"
    if not path.is_file():
        raise ImportError(f"no {path}")
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))      # what it imports comes from the same lib
    spec = importlib.util.spec_from_file_location(f"krew_own_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _topology():
    """lib/hermes_topology, or None when it cannot be read (then the pins ask what they always did)."""
    try:
        return _own_lib("hermes_topology")
    except Exception:  # noqa: BLE001
        if _env("KREW_GATEWAY_TOPOLOGY").strip().lower() == "host" and \
                not any("hermes_topology" in m for m in MISSING):
            MISSING.append(f"KREW_GATEWAY_TOPOLOGY=host but no lib/hermes_topology.py under {ROOT / 'lib'}: "
                           "the preload and T0 pins read one gateway unit per profile")
        return None


# ── the skill preload (Crissa room voice) ────────────────────────────────
# Skill bodies once per session on ephemeral_system_prompt. Krew path is
# plugin krew-skill-preload wrapping AIAgent.__init__ for api_server


# enabled, drop-in + running env, SOUL cap. Do NOT pin HEAD==origin/main.
HERMES_AGENT = Path(os.path.expanduser(_env("HERMES_TREE") or str(HERMES_ROOT / "hermes-agent")))
API_SERVER = HERMES_AGENT / "gateway" / "platforms" / "api_server.py"
KANBAN_DB = HERMES_AGENT / "hermes_cli" / "kanban_db.py"
VISION_TOOLS = HERMES_AGENT / "tools" / "vision_tools.py"
MODEL_TOOLS = HERMES_AGENT / "model_tools.py"
TOOLS_CONFIG = HERMES_AGENT / "hermes_cli" / "tools_config.py"
HERMES_CLI_PY = HERMES_AGENT / "cli.py"
PRELOAD_PLUGIN = "krew-skill-preload"
CRISSA_PLUGIN_LINK = HERMES_ROOT / "profiles" / "crissa" / "plugins" / PRELOAD_PLUGIN
CRISSA_CONFIG = HERMES_ROOT / "profiles" / "crissa" / "config.yaml"
CRISSA_SKILLS_DROPIN = _REAL_HOME / ".config" / "systemd" / "user" / "hermes-crissa-gateway.service.d" / "skills.conf"
CRISSA_GATEWAY = "hermes-crissa-gateway.service"
# Voice pin: no SKILL.md bodies. "none" is the explicit empty sentinel
# (plugin treats none/off/0/- as []). CLI/cron still skill_view.
CRISSA_PRELOAD = ("none",)
CRISSA_SOUL = HERMES_ROOT / "profiles" / "crissa" / "SOUL.md"

# 08b43d1b deliberately grew her SOUL (she decides and reports, spot-checks the krew,
# can reach her own kit) and 32 seconds later the janitor RED decomposed into a card
# telling bash to cut it back — the crew correcting the house's own correction, which
# is the thing the Profiles-page rule exists to stop. The cap is a real guard against
# procedure creeping into the every-turn prefix; it was just holding a stale number.
# Note this counts CHARACTERS, not bytes: `wc -c` reads 5019 where this reads 4969.

# threshold moves by env, and this one needed a commit — so when the cap fired
# wrongly at 00:15 there was no way to soften it from the card it had spawned.
CRISSA_SOUL_CAP = int(_env("KREW_CRISSA_SOUL_CAP", "5200"))
# Krew edits that live in the Hermes checkout. `hermes update` wipes them.
# Do NOT pin HEAD==origin/main — this box tracks Nous and is often hundreds
# of commits behind on purpose; update is an M&S act, not a janitor RED.
# Do NOT treat extra dirty files as RED (Steward's bak / npm lock / WIP
# tests). Named patches below are the ones we re-apply after an update.


def systemd_env_str(unit: str, key: str) -> str | None:
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "Environment", "--value"],
            capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    m = re.search(rf"{re.escape(key)}=(\S+)", r.stdout)
    return m.group(1) if m else None


def check_preload(*, plugin_link: Path = CRISSA_PLUGIN_LINK, crissa_config: Path = CRISSA_CONFIG,
                  dropin: Path = CRISSA_SKILLS_DROPIN, unit_env: str | None = None,
                  soul: Path = CRISSA_SOUL) -> list[str]:
    fails: list[str] = []
    if not crissa_config.parent.is_dir():
        MISSING.append(f"Crissa's preload pin not checked: no crissa profile at {crissa_config.parent}")
        return fails
    if not crissa_config.is_file():
        MISSING.append(f"Crissa's skill preload not checked: her profile has no config.yaml ({crissa_config}); "
                       "her SOUL cap still is")
        plugin_link = dropin = None
    if plugin_link is not None and not (plugin_link / "__init__.py").is_file():
        fails.append(
            f"{PRELOAD_PLUGIN} is not linked into Crissa's profile ({plugin_link}) — "
            "ln -sfn ~/.hermes/plugins/krew-skill-preload there"
        )
    cfg: dict = {}
    if crissa_config.is_file():
        try:
            yaml = _YAML
            cfg = yaml.safe_load(crissa_config.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            cfg = {}
            fails.append(f"crissa config.yaml: {type(e).__name__}")
        if PRELOAD_PLUGIN not in (((cfg.get("plugins") or {}).get("enabled")) or []):
            fails.append(f"crissa config.yaml does not enable {PRELOAD_PLUGIN}")
    want = ",".join(CRISSA_PRELOAD)
    if dropin is not None and unit_env is None and getattr(_topology(), "is_host", lambda: False)():
        # one host gateway: a unit's env would be everyone's, so her preload is her config's
        # `krew_preload.skills` (krew-skill-preload reads it under multiplex)
        got = str(((cfg.get("krew_preload") or {}).get("skills")) if isinstance(cfg, dict) else "")
        if got != want:
            fails.append(f"crissa config.yaml krew_preload.skills is {got!r}, not {want!r}")
        dropin = None
    if dropin is not None and unit_env is None and not (dropin.parent.parent / CRISSA_GATEWAY).exists():
        MISSING.append(f"Crissa's gateway preload not checked: {CRISSA_GATEWAY} is not under {dropin.parent.parent}")
        dropin = None
    if dropin is None:
        pass
    elif not dropin.is_file():
        fails.append(f"crissa gateway drop-in missing: {dropin} (Environment=HERMES_TUI_SKILLS={want})")
    else:
        txt = dropin.read_text(encoding="utf-8")
        if f"HERMES_TUI_SKILLS={want}" not in txt:
            fails.append(f"crissa gateway drop-in does not preload {want}")
    live = unit_env if unit_env is not None or dropin is None else systemd_env_str(CRISSA_GATEWAY, "HERMES_TUI_SKILLS")
    if dropin is None:
        pass
    elif live is None:
        fails.append("crissa gateway runs without HERMES_TUI_SKILLS (daemon-reload + restart after the drop-in)")
    elif live != want:
        fails.append(f"crissa gateway preloads {live!r}, not {want!r}")
    if soul.is_file():
        n = _persona_chars(soul.read_text(encoding="utf-8"))
        if n > CRISSA_SOUL_CAP:
            # This string is not a note to a human: the janitor files it as a card
            # body and the decomposer hands it to a worker as the acceptance

            # and bash began trimming fifteen minutes after four standing laws

            # write and Crissa's review stopped it. So the sentence itself has to
            # say what may move and what may never.
            fails.append(
                f"crissa SOUL.md is {n} chars (cap {CRISSA_SOUL_CAP}) — the every-turn prefix is "
                "growing. MOVE ONLY procedure: how-to steps, examples, tool recipes, anything "
                "that belongs in a skill the map can reach. NEVER move or summarise the "
                "standing laws, the AUTH stances, or the Board class line: the SOUL is the "
                "every-turn prefix, so a law relocated into a skill stops being standing and "
                "binds only when that skill is loaded. If nothing is procedure, the cap is the "
                "wrong number — raise KREW_CRISSA_SOUL_CAP and say so on the card; do not trim "
                "to fit.")
    return fails


def check_hermes_tree_patches(*, tree: Path = HERMES_AGENT, queue: Path | None = None) -> list[str]:
    """Every patch in the queue is ON the Hermes checkout. Missing = an update wiped it.


    from the pre-split ``kanban_db.py`` and stayed RED on a correct tree after upstream took half
    the patchset as its own — and a RED pin files a crew card to "restore" what must not come
    back. A patch is on the tree when it reverse-applies cleanly, which is what
    ``scripts/hermes-tree.py status`` calls ``applied``.

    Does not require HEAD==origin/main and does not fail extra dirty files.

    """
    queue = queue or Path(os.path.expanduser(_env("HERMES_PATCH_QUEUE") or str(ROOT.parent / "patches")))
    if not queue.is_dir():
        MISSING.append(f"Hermes patches not checked: no patch queue at {queue} "
                       "(HERMES_PATCH_QUEUE names it; a krew checkout keeps it at patches/)")
        return []
    if not (tree / ".git").exists():
        return [f"hermes-agent checkout not found at {tree}"]
    fails: list[str] = []
    for p in sorted(queue.glob("*.patch")):
        try:
            r = subprocess.run(["git", "-C", str(tree), "apply", "--reverse", "--check", str(p)],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            fails.append(f"hermes-agent patch {p.name} could not be checked ({type(exc).__name__})")
            continue
        if r.returncode != 0:
            fails.append(
                f"hermes-agent lost krew patch {p.name} (hermes update?) — "
                f"git -C {tree} apply {p}"
            )
    if fails and _queue_stack_ok(tree, queue):

        # alone, while the queue reversed newest-first lands on the base to the byte — the same
        # rule `hermes-tree.py status` reads as "applied (in the stack)". A red pin here files a
        # crew card to re-apply a patch that is on the tree. Kill HERMES_TREE_STACK=0.
        return []
    return fails


def _queue_stack_ok(tree: Path, queue: Path) -> bool:
    """hermes-tree's stack_check on this tree + queue; any failure to ask is a no."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("hermes_tree_pin", ROOT / "scripts" / "hermes-tree.py")
        ht = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ht)
        return bool(ht.stack_check(tree, queue).get("ok"))
    except Exception:
        return False



# The pre-act gate is advertised. Four facts keep it real: the executed copy is
# the repo copy; the policy parses to a real rule list; every T0 hook spec
# says fail_closed: true (hermes shell hooks fail OPEN otherwise — a crashed
# gate would allow the act); the gateways that spawn workers run enforce.
T0_GATE_REPO = Path(os.path.expanduser(_env("KREW_T0_GATE_SOURCE") or str(ROOT.parent / "rails" / "tier0_gate.py")))
T0_GATE_LIVE = KREW_HOME / "rails" / "tier0_gate.py"
T0_POLICY = Path(os.path.expanduser(_env("KREW_T0_POLICY") or str(KREW_HOME / "tier0.yaml")))
T0_ENFORCE_GATEWAYS = tuple(g.strip() for g in _env("KREW_T0_ENFORCE_GATEWAYS", "ada,steward,lila,crissa").split(",") if g.strip())
T0_MIN_RULES = int(_env("KREW_T0_MIN_RULES", "20"))


def check_tier0(*, repo: Path = T0_GATE_REPO, live: Path = T0_GATE_LIVE,
                policy: Path = T0_POLICY, pack_dir: Path = None,
                live_dir: Path = None, unit_dir: Path = None,
                gateways: tuple = T0_ENFORCE_GATEWAYS) -> list[str]:
    fails: list[str] = []
    live_dir = live_dir or HERMES_PROFILES
    unit_dir = unit_dir or (_REAL_HOME / ".config" / "systemd" / "user")
    if not live.is_file() and not policy.is_file():
        MISSING.append(f"T0 not checked: no gate at {live} and no policy at {policy} (the rails are not installed here)")
        return fails
    if not repo.is_file():
        fails.append(f"T0 gate source missing: {repo} (KREW_T0_GATE_SOURCE names the krew checkout's rails/tier0_gate.py)")
    elif not live.is_file():
        fails.append(f"T0 executed copy missing: {live} (scripts/krew-install.py places the rails)")
    elif repo.read_bytes() != live.read_bytes():
        fails.append(f"T0 executed copy {live} != {repo} (rerun scripts/krew-install.py for the rails)")
    if not policy.is_file():
        fails.append(f"T0 policy missing: {policy} — the gate fails OPEN (loud)")
    else:
        try:
            _yaml = _YAML
            doc = _yaml.safe_load(policy.read_text()) or {}
            rules = [r for r in (doc.get("blocklist") or []) if isinstance(r, dict) and r.get("pattern")]
            bad = []
            for r in rules:
                try:
                    re.compile(r["pattern"], re.I)
                except re.error as e:
                    bad.append(f"{r['pattern'][:40]!r}: {e}")
            if len(rules) < T0_MIN_RULES:
                fails.append(f"T0 policy has {len(rules)} rules (< {T0_MIN_RULES}) — the gate fails CLOSED on zero")
            if bad:
                fails.append("T0 policy patterns do not compile: " + "; ".join(bad[:3]))
        except Exception as e:  # unparseable = every act denied in enforce
            fails.append(f"T0 policy does not parse ({str(e)[:80]}) — the gate is failing CLOSED")
    # hook specs: pack and live must both say fail_closed: true
    for root, label in ((pack_dir, "pack"), (live_dir, "live")):
        if not root or not Path(root).is_dir():
            continue
        for cfg in sorted(Path(root).glob("*/config.yaml")):
            try:
                _yaml = _YAML
                doc = _yaml.safe_load(cfg.read_text()) or {}
            except Exception:
                continue
            for hook in ((doc.get("hooks") or {}).get("pre_tool_call") or []):
                if "tier0_gate" in str(hook.get("command", "")) and hook.get("fail_closed") is not True:
                    fails.append(f"T0 hook without fail_closed: true — {label} {cfg.parent.name}")
    # enforce on the gateways that spawn workers (workers inherit the env)
    # One host gateway: ONE unit carries the mode for every profile and every worker it spawns.
    topo = _topology()
    names = ([topo.HOST_UNIT[:-len(".service")]] if topo and topo.is_host()
             else [f"hermes-{g}-gateway" for g in gateways])
    here = [n for n in names if (Path(unit_dir) / f"{n}.service").exists()]
    if not here:
        MISSING.append(f"T0 enforce not checked: none of {[n + '.service' for n in names]} is under {unit_dir} "
                       "(KREW_T0_ENFORCE_GATEWAYS names the gateways)")
    for name in here:
        d = Path(unit_dir) / f"{name}.service.d"
        txt = "".join(p.read_text(errors="replace") for p in d.glob("*.conf")) if d.is_dir() else ""
        if "KREW_T0_MODE=enforce" not in txt:
            fails.append(f"{name} has no KREW_T0_MODE=enforce drop-in (T0 runs shadow there)")
    return fails


# The crew's every-turn prefix. Crissa was the only profile with a cap, and she was
# the SMALLEST of the talkers: steward rode 9751 chars against her 5200 (User

# plus ~10 %, so this warns about GROWTH from here and fires on nobody today — a
# patrol that goes red on the day it lands is a patrol people learn to ignore.
# Per-profile override: KREW_SOUL_CAP_<PROFILE> (e.g. KREW_SOUL_CAP_STEWARD).
CREW_SOUL_CAPS = {
    "ada": 5200, "bash": 8200, "steward": 10700, "lila": 8200,
    "vera": 7500, "scout": 2000, "judge": 2600,
}


def soul_cap_for(profile: str, default: int) -> int:
    return int(_env(f"KREW_SOUL_CAP_{profile.upper()}", str(default)))


def check_crew_souls(*, live_dir: Path = HERMES_PROFILES) -> list[str]:
    """Every-turn prefix size for the crew. Crissa has her own check (check_preload).

    The wording matters more than the number: this string becomes a janitor card

    card nearly trimmed four standing laws out of her SOUL).
    """
    fails: list[str] = []
    for profile, default in sorted(CREW_SOUL_CAPS.items()):
        soul = live_dir / profile / "SOUL.md"
        if not soul.is_file():
            continue
        cap = soul_cap_for(profile, default)
        n = _persona_chars(soul.read_text(encoding="utf-8"))
        if n > cap:
            fails.append(
                f"{profile} SOUL.md is {n} chars (cap {cap}) — the every-turn prefix is growing. "
                f"MOVE ONLY procedure: how-to steps, examples, tool recipes, anything that belongs "
                f"in the {profile}-map index a lookup can reach. NEVER move or summarise the standing "
                f"laws, the Board class line, or a User-dated rule: the SOUL is the every-turn prefix, "
                f"so a law relocated into a skill stops being standing and binds only when that skill "
                f"is loaded. If nothing is procedure, the cap is the wrong number — raise "
                f"KREW_SOUL_CAP_{profile.upper()} and say so on the card; do not trim to fit.")
    return fails



# The host config (the dispatcher's brain) fell back crew -> CREW for months, so a
# stopped or crashed crew left it with nothing. A profile whose fallback_model points
# at the same engine as its primary has no fallback at all.
def _primary_and_fallback(text: str) -> tuple[str, str]:
    prim = fb = ""
    block = ""
    for line in text.splitlines():
        if line and not line[0].isspace():
            block = line.split(":", 1)[0].strip()
            continue
        m = re.match(r"^[ \t]+base_url:[ \t]*(\S+)", line)
        if m and block == "model" and not prim:
            prim = m.group(1)
        elif m and block == "fallback_model" and not fb:
            fb = m.group(1)
    return prim, fb


def check_fallbacks(*, root_config: Path | None = None, profiles: Path | None = None) -> list[str]:
    """Every live config with a fallback_model must fall back to ANOTHER engine."""
    fails: list[str] = []
    cfgs = [("host (config.yaml)", root_config or (HERMES_ROOT / "config.yaml"))]
    root = profiles or HERMES_PROFILES
    if root.is_dir():
        cfgs += [(p.parent.name, p) for p in sorted(root.glob("*/config.yaml"))]
    for name, path in cfgs:
        try:
            prim, fb = _primary_and_fallback(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if prim and fb and prim.rstrip("/") == fb.rstrip("/"):
            fails.append(f"fallback is the primary: {name} falls back {prim} -> {fb} (no fallback at all)")
    return fails


def check(*, skills: bool = True) -> dict:
    MISSING.clear()
    if _SETTINGS_NOTE:
        MISSING.append(_SETTINGS_NOTE)
    fails: list[str] = []
    if skills:
        fails.extend(check_skills())
    fails.extend(check_fallbacks())
    fails.extend(check_preload())
    fails.extend(check_hermes_tree_patches())
    fails.extend(check_crew_souls())
    fails.extend(check_tier0())
    return {"ok": not fails, "failures": fails, "missing": list(MISSING)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-skills", action="store_true", help="leave the skill-belt check out")
    args = ap.parse_args(argv)
    report = check(skills=not args.no_skills)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("pin-check ok" if report["ok"] else "pin-check RED")
        for f in report["failures"]:
            print(f"  {f}")
        for m in report["missing"]:
            print(f"  missing: {m}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
