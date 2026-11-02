#!/usr/bin/env python3
"""Install krew's plugins, skills and SOUL templates into a Hermes home. Changes nothing outside it and $KREW_HOME.

    python3 scripts/krew-install.py init            the guided first install (scripts/krew_install_init.py): Hermes at the
                                                    pin + patches, the eight profiles with config, the model endpoint and
                                                    key, the services list from discovery (you name and approve each row),
                                                    the loops you name with --enable (opt-in per loop)
    python3 scripts/krew-install.py                 plugins + skills + $KREW_HOME/krew.yaml (if missing); prints the enable commands
    python3 scripts/krew-install.py --enable        ... and runs `hermes plugins enable <name>` for each plugin
    python3 scripts/krew-install.py --souls crissa,ada,bash,steward
                                                    ... and writes TRUNK + SOUL into $HERMES_HOME/profiles/<name>/SOUL.md
                                                    where that profile has none yet (yours are never overwritten);
                                                    {{owner}} / {{deliverables_dir}} filled from --owner / --deliverables
    python3 scripts/krew-install.py --link          symlink instead of copy (a krew checkout you `git pull`)
    python3 scripts/krew-install.py --only krew-board,krew-docs     a subset of the plugins
    python3 scripts/krew-install.py --dry-run       say what would happen
    python3 scripts/krew-install.py --hooks         ... and write the T0 gate hook into each profile's config.yaml
                                                    (security rails + Janitor go to $KREW_HOME unless --no-rails)
    python3 scripts/krew-install.py --memory        ... and the crew's shared long memory, all on this machine (crew/local-services.json):
                                                    the vendored Hindsight plugin (never fetched), its config.json,
                                                    hindsight-api in $KREW_HOME/hindsight/.venv (CPU), the embedding model
                                                    fetched once, the vector store (embedded PostgreSQL + pgvector, no
                                                    sudo) and krew-memory-db + krew-hindsight, enabled. Memory ships empty.
    python3 scripts/krew-install.py --search        ... and web search on this machine: SearXNG at its pinned commit in
                                                    $KREW_HOME/searxng, krew-searxng on loopback, every profile's web
                                                    search pointed at it, Hermes's keyless cloud search off.
                                                    init does both unless --no-memory / --no-search.
    python3 scripts/krew-install.py --marry-profile crissa
                                                    with the Wall (Crissa) installed: offer that profile's API server
                                                    to it (<crissa home>/endpoints.d; the key is referenced, never
                                                    copied); the Wall switches only when you click Use in Connect.
                                                    On by default when Crissa is here; --no-marry skips it.

HERMES_HOME defaults to ~/.hermes (or --hermes-home); KREW_HOME to ~/.krew. An existing plugin or skill folder of
the same name is replaced only with --force (copy mode) and is otherwise left alone and reported.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_install_rails  # noqa: E402  (security rails + Janitor step)
import krew_install_panel_tiles  # noqa: E402  (krew's tiles in the Crissa Control Panel)
import krew_install_marry  # noqa: E402  (the Wall's endpoint offer, 2026-10-06)
import krew_install_init  # noqa: E402  (the guided first install, 2026-10-08)
import krew_install_homes as H  # noqa: E402  (the homes an install may write, the hermes it may run; 2026-10-30)
import krew_install_local as LOCAL  # noqa: E402  (memory + search on this machine; 2026-11-01)
import krew_hermes as KH  # noqa: E402  where Hermes lives
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def plugins(only: set[str] | None) -> list[Path]:
    out = [p for p in sorted((REPO / "plugins").iterdir()) if (p / "plugin.yaml").is_file()]
    if only:
        unknown = only - {p.name for p in out}
        if unknown:
            raise SystemExit(f"krew-install: no such plugin(s): {', '.join(sorted(unknown))}. "
                             f"Shipped: {', '.join(p.name for p in out)}")
        out = [p for p in out if p.name in only]
    return out


def skills() -> list[Path]:
    return [p for p in sorted((REPO / "skills").iterdir()) if (p / "SKILL.md").is_file()]


def place(src: Path, dst: Path, *, link: bool, force: bool, dry: bool, say) -> bool:
    if dst.is_symlink() and link and dst.resolve() == src.resolve():
        say(f"same     {dst} (already linked)")
        return True
    if dst.exists() or dst.is_symlink():
        if not force:
            say(f"skipped  {dst} exists (use --force to replace it)")
            return False
        if not dry:
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
            else:
                shutil.rmtree(dst)
    say(f"{'link' if link else 'copy'}     {src.relative_to(REPO)} -> {dst}")
    if dry:
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    if link:
        dst.symlink_to(src, target_is_directory=True)
    else:
        shutil.copytree(src, dst, ignore=IGNORE)
    return True


def unit_python(hermes_bin: str) -> str:
    """The hermes command's interpreter (it has `packaging`, which M&S needs), else this python.

    Hermes's launcher is a shell script today (`#!/bin/sh` + `exec <python> -I -c ...`, following a link from
    ~/.local/bin): a shell shebang is not the interpreter, so its `exec` line is read instead (2026-10-10: the units
    were rendered with /bin/sh as their python)."""
    path = shutil.which(os.path.expanduser(hermes_bin)) or os.path.expanduser(hermes_bin)
    try:
        for _ in range(3):              # a shim that only execs the real launcher: follow it
            with open(path, "rb") as fh:
                lines = fh.read(8192).decode("utf-8", "replace").splitlines()
            first = lines[0].strip() if lines else ""
            py = first[2:].split()[0] if first.startswith("#!") else ""
            if not py or os.path.basename(py) not in ("sh", "bash", "dash"):
                break
            words = next((ln.split() for ln in lines[1:] if ln.lstrip().startswith("exec ")), [])
            py = words[1] if len(words) > 1 else ""
            if "python" in os.path.basename(py):
                break
            if py and os.path.isfile(py):
                path = py
                continue
            py = ""
            break
        if py and not py.endswith("/env") and os.path.basename(py) not in ("sh", "bash", "dash") and os.path.exists(py):
            return py
    except (OSError, IndexError):
        pass
    return sys.executable


OPT_IN = "# krew: opt-in"


def unit_sets(src_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """(starts, opt_in) among krew's own units/ with crew/scripts/krew-crew-install.py's rule: a timer, a path, or a
    service wanted by default.target starts with `--enable all`; a unit marked `# krew: opt-in` (the reference
    install's dated one-shots, the fixed-time M&S nightly that stands in for the ticker) starts only when named."""
    d = src_dir or (REPO / "units")
    starts, optin = [], []
    for u in sorted(d.glob("krew-*.*")):
        if "@." in u.name:      # a template (krew-engine@.service): only an instance can be enabled, and starting
            continue            # a model engine is the user's own step (engines/README.md), never --enable all
        text = u.read_text(encoding="utf-8")
        if OPT_IN in text:
            optin.append(u.name)
        elif u.suffix in (".timer", ".path") or "WantedBy=default.target" in text:
            starts.append(u.name)
    return starts, optin


def write_units(unit_dir: Path, krew_home: Path, python: str, *, force: bool, dry: bool, say) -> list[str]:
    """Render units/*.service|timer into the user's systemd folder. Enabling them is the owner's step."""
    done = []
    for src in sorted((REPO / "units").glob("krew-*.*")):
        dst = unit_dir / src.name
        body = (src.read_text(encoding="utf-8").replace("{krew_home}", str(krew_home))
                .replace("{krew}", str(REPO)).replace("{python}", python))
        if dst.exists() and not force and dst.read_text(encoding="utf-8") != body:
            say(f"kept     {dst} (differs from {src.relative_to(REPO)}; --force replaces it)")
            continue
        say(f"unit     {src.relative_to(REPO)} -> {dst}")
        if not dry:
            unit_dir.mkdir(parents=True, exist_ok=True)
            dst.write_text(body, encoding="utf-8")
        done.append(src.name)
    starts, optin = unit_sets()
    if optin:
        say("Opt-in (written, never enabled unnamed): " + " ".join(optin))
    say("Units written, none enabled here (init's --enable picks among them). To run them:")
    say("  systemctl --user daemon-reload")
    say("  systemctl --user enable --now krew-window-tick.timer krew-ms-survey.timer   # set off_hours in krew.yaml first")
    say("  (no off_hours? enable krew-ms-nightly.timer instead: the ticker hands the nightly to it)")
    say("  krew-restart-window.timer: only after `enabled: true` in restart-window.json (it restarts units)")
    say("  units/system/fleet-bootcheck.service is a system unit: not written here (see its header)")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="krew-install", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", nargs="?", choices=("init",), help="init = the guided first install")
    ap.add_argument("--enable", nargs="?", const="ask", default="",
                    help="init: which loops to enable (comma list, all, or ask); alone: run `hermes plugins enable` per plugin")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME") or str(KH.hermes_home()))
    ap.add_argument("--krew-home", default=os.environ.get("KREW_HOME") or "~/.krew")
    ap.add_argument("--only", default="", help="comma list of plugin names")
    ap.add_argument("--no-skills", action="store_true")
    ap.add_argument("--souls", default="", help="comma list of SOUL templates to seed into profiles (template[:profile])")
    ap.add_argument("--link", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--hermes-bin", default="hermes")
    ap.add_argument("--memory", action="store_true", help="the crew's long memory on this machine (Hindsight + its vector "
                    "store, crew/local-services.json); init does it unless --no-memory")
    ap.add_argument("--no-memory", action="store_true", help="init: leave the long memory out")
    ap.add_argument("--search", action="store_true", help="web search on this machine (SearXNG, loopback); init does it "
                    "unless --no-search")
    ap.add_argument("--no-search", action="store_true", help="init: leave local search out")
    ap.add_argument("--memory-brain", default="", help="the brain Hindsight uses, as base_url|model|key_env (init passes "
                    "its crew brain; default: the brain preset file's default crew brain)")
    ap.add_argument("--kit-root", default=os.environ.get("CRISSA_KIT_ROOT", ""),
                    help="the Crissa kit's code folder: its embedding lock wins (one model on disk)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--units", action="store_true",
                    help="write krew's systemd --user units (window ticker, M&S nightly + survey); never enables them")
    ap.add_argument("--unit-dir", default="", help="where units are written (default: your user systemd folder; a "
                    "scratch --hermes-home/--krew-home writes them to <krew home>/systemd-user)")
    ap.add_argument("--python", default="", help="python the units run (default: the hermes command's own)")
    krew_install_rails.add_args(ap)
    krew_install_marry.add_args(ap)
    krew_install_init.add_args(ap)
    a = ap.parse_args(argv)
    hh = Path(os.path.expanduser(a.hermes_home))
    kh = Path(os.path.expanduser(a.krew_home))
    try:                                    # one log in order: the steps' own processes write to the same stream
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    say = print
    if a.verb == "init":
        return krew_install_init.run(a, hh, kh, say)
    only = {x.strip() for x in a.only.split(",") if x.strip()} or None

    installed = []
    for p in plugins(only):
        if place(p, hh / "plugins" / p.name, link=a.link, force=a.force, dry=a.dry_run, say=say):
            installed.append(p.name)
    if not a.no_skills:
        for s in skills():
            place(s, hh / "skills" / s.name, link=a.link, force=a.force, dry=a.dry_run, say=say)

    cfg = kh / "krew.yaml"
    if not cfg.exists():
        say(f"copy     config/krew.example.yaml -> {cfg}")
        if not a.dry_run:
            kh.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "config" / "krew.example.yaml", cfg)
    else:
        say(f"kept     {cfg} (yours)")
    ex = kh / "ms.example.yaml"           # M&S settings example (never ms.yaml: with none, M&S starts empty + discovery)
    if not ex.exists() or a.force:
        say(f"copy     config/ms.example.yaml -> {ex}")
        if not a.dry_run:
            kh.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "config" / "ms.example.yaml", ex)
    else:
        say(f"kept     {ex}")
    pins = kh / "hermes-env-pins.json"    # security minimums for the Hermes pin krew ships (status reads; apply needs upgrades)
    if not pins.exists():
        say(f"copy     config/hermes-env-pins.example.json -> {pins}")
        if not a.dry_run:
            kh.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "config" / "hermes-env-pins.example.json", pins)
    else:
        say(f"kept     {pins} (yours)")
    roots = kh / "ms-env-roots.json"      # M&S env roots (empty: every env reports "no roots declared")
    if not roots.exists():
        say(f"copy     config/ms-env-roots.example.json -> {roots}")
        if not a.dry_run:
            kh.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "config" / "ms-env-roots.example.json", roots)
    else:
        say(f"kept     {roots} (yours)")
    notes = kh / "unit-notes.json"        # words for the unit registry (label, what, feeds, off_file); never membership
    if not notes.exists():
        say(f"copy     config/unit-notes.example.json -> {notes}")
        if not a.dry_run:
            kh.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "config" / "unit-notes.example.json", notes)
    else:
        say(f"kept     {notes} (yours)")
    sched = kh / "window-schedule.json"   # krew's night jobs (the M&S nightly, the unit-registry refresh); the ticker idles until off_hours is set
    if not sched.exists():
        say(f"copy     config/window-schedule.example.json -> {sched}")
        if not a.dry_run:
            kh.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / "config" / "window-schedule.example.json", sched)
    else:
        say(f"kept     {sched} (yours)")
    hermes, hwhy = H.hermes_for(hh, a.hermes_bin)
    if a.units:
        udir, uwhy = H.unit_dir(a.unit_dir, hh, kh)
        if not a.unit_dir:
            say(f"units    -> {udir} ({uwhy})")
        write_units(udir, kh, a.python or (unit_python(hermes) if hermes else sys.executable),
                    force=a.force, dry=a.dry_run, say=say)

    trunk = (REPO / "souls" / "TRUNK.md").read_text(encoding="utf-8")
    for item in [x.strip() for x in a.souls.split(",") if x.strip()]:
        tmpl, _, prof = item.partition(":")
        src = REPO / "souls" / tmpl / "SOUL.md"
        if not src.is_file():
            raise SystemExit(f"krew-install: no SOUL template {tmpl!r} (have: "
                             f"{', '.join(sorted(p.name for p in (REPO / 'souls').iterdir() if p.is_dir()))})")
        prof = prof or tmpl
        # `main` is Hermes's default profile (the Bridge, your console): its SOUL is $HERMES_HOME/SOUL.md, and it is
        # not crew, so it gets no TRUNK
        dst = hh / "SOUL.md" if prof == "main" else hh / "profiles" / prof / "SOUL.md"
        if dst.exists():
            say(f"kept     {dst} (the profile already has a SOUL)")
            continue
        say(f"soul     souls/{tmpl} -> {dst}")
        if not a.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = krew_install_init.fill(src.read_text(encoding="utf-8"),
                                          krew_install_init.soul_values(a.owner, a.deliverables))
            dst.write_text(body if prof == "main" else trunk.rstrip() + "\n\n" + body, encoding="utf-8")

    local_ok = True
    if a.memory or a.search:
        udir, uwhy = H.unit_dir(a.unit_dir, hh, kh)
        scratch = H.is_scratch(hh, kh)
    if a.memory:
        if a.memory_brain:
            url, _, rest = a.memory_brain.partition("|")
            model, _, key_env = rest.partition("|")
            brain = {"base_url": url, "model": model, "key_env": key_env}
        else:
            brain = krew_install_init.resolve_brains()["crew"]
        ok, why = LOCAL.memory(hh, kh, say, brain=brain, unit_dir=udir, scratch=scratch, dry=a.dry_run, force=a.force,
                               runtime_python=unit_python(hermes) if hermes else None,
                               kit_root=Path(os.path.expanduser(a.kit_root)) if a.kit_root else None)
        say("memory   " + ("ready: Hindsight on this machine" if ok else "NOT ready: " + "; ".join(why)))
        local_ok = local_ok and ok
    if a.search:
        ok, why = LOCAL.search(hh, kh, say, unit_dir=udir, scratch=scratch, dry=a.dry_run, force=a.force)
        say("search   " + ("ready: SearXNG on this machine" if ok else "NOT ready: " + "; ".join(why)))
        local_ok = local_ok and ok
    rails_ok = krew_install_rails.install(a, hh, kh, place, say)
    krew_install_panel_tiles.install(a, kh, place, say)
    krew_install_marry.marry(a, hh, say)

    if a.enable and installed and not a.dry_run and not hermes:
        say(f"enable   not run: {hwhy}")
    elif a.enable and installed and not a.dry_run:
        env = {**os.environ, "HERMES_HOME": str(hh)}
        for n in installed:
            r = subprocess.run([hermes, "plugins", "enable", n], env=env, text=True, capture_output=True)
            say(f"enable   {n}: " + ("ok" if r.returncode == 0 else f"exit {r.returncode}: {(r.stderr or r.stdout).strip()}"))
    elif installed:
        say("\nNext: enable them (each is opt-in in Hermes):")
        for n in installed:
            say(f"  hermes plugins enable {n}")
    say("\nThen: put the tools in your profiles' toolsets (config/config.example.yaml shows where),")
    say("build the docs index once:  python3 scripts/krew-docs-index.py build")
    say("and apply the Hermes patches to your pinned checkout:  python3 scripts/krew-patch.py check, then apply")
    say("M&S (this machine's map + nightly report):  python3 ms/scripts/krew-ms.py   (report only until upgrades: true)")
    return 0 if rails_ok and local_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
