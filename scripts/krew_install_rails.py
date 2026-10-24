"""krew-install's security rails + Janitor step (called from scripts/krew-install.py).

Places rails/ and janitor/ under $KREW_HOME, seeds their settings (tier0.yaml, rails.yaml, janitor.yaml) where
missing, refreshes the commented *.example.yaml copies beside them (always: they are krew's, not yours),
renders the systemd unit templates into $KREW_HOME/systemd/user (never into systemd itself: you install and
enable them), and with --hooks writes the T0 gate into each profile's config.yaml (rails/krew_hooks.py).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETTINGS = (("tier0.example.yaml", "tier0.yaml"), ("rails.example.yaml", "rails.yaml"),
            ("janitor.example.yaml", "janitor.yaml"))


def add_args(ap) -> None:
    ap.add_argument("--no-rails", action="store_true", help="skip the security rails + Janitor")
    ap.add_argument("--hooks", action="store_true", help="write the T0 gate hook into each profile's config.yaml")
    ap.add_argument("--hooks-profiles", default="", help="comma list (default: every profile with a config.yaml)")
    ap.add_argument("--no-auto-accept", action="store_true",
                    help="do not set hooks_auto_accept: true (then approve each hook at a TTY: hermes hooks)")


def home_form(kh: Path) -> str:
    """KREW_HOME as the settings files write it: ~/... under your home, else the absolute path."""
    kh = Path(os.path.expanduser(str(kh))).resolve()
    try:
        return "~/" + str(kh.relative_to(Path.home().resolve()))
    except ValueError:
        return str(kh)


def seed_text(text: str, kh: Path) -> str:
    """The example as your first live settings file: `{krew}` names this checkout (the Janitor's helpers in
    window/), and its `~/.krew` paths name YOUR $KREW_HOME (a moved KREW_HOME must not leave the Janitor's metrics,
    or the folder the T0 gate protects, at ~/.krew)."""
    text = text.replace("{krew}", str(REPO))     # the checkout the window helpers run from
    here = home_form(kh)
    if here == "~/.krew":
        return text
    return re.sub(r"~/\.krew(?=[/\s,'\"\]]|$)", lambda _m: here, text, flags=re.M)


def render_units(kh: Path, *, dry: bool, say) -> list[Path]:
    out = []
    src = REPO / "systemd" / "user"
    if not src.is_dir():
        return out
    dst = kh / "systemd" / "user"
    for f in sorted(src.iterdir()):
        if f.suffix not in (".service", ".timer") and not f.name.endswith(".example"):
            continue
        text = f.read_text(encoding="utf-8").replace("@KREW_HOME@", str(kh)).replace("@PYTHON@", "/usr/bin/python3")
        say(f"unit     systemd/user/{f.name} -> {dst / f.name}")
        out.append(dst / f.name)
        if not dry:
            dst.mkdir(parents=True, exist_ok=True)
            (dst / f.name).write_text(text, encoding="utf-8")
    return out


def install(a, hh: Path, kh: Path, place, say) -> bool:
    if getattr(a, "no_rails", False):
        return True
    ok = True
    for d in ("rails", "janitor"):
        if (REPO / d).is_dir():
            place(REPO / d, kh / d, link=a.link, force=a.force, dry=a.dry_run, say=say)
    for example, live in SETTINGS:
        src = REPO / "config" / example
        if not src.is_file():
            continue
        if not a.dry_run:
            kh.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, kh / example)
        say(f"copy     config/{example} -> {kh / example} (krew's commented example, refreshed)")
        if (kh / live).exists():
            say(f"kept     {kh / live} (yours)")
        else:
            here = home_form(kh)
            say(f"copy     config/{example} -> {kh / live}" + (f" (~/.krew written as {here})" if here != "~/.krew" else ""))
            if not a.dry_run:
                (kh / live).write_text(seed_text(src.read_text(encoding="utf-8"), kh), encoding="utf-8")
    units = render_units(kh, dry=a.dry_run, say=say)
    if a.hooks:
        sys.path.insert(0, str(REPO / "rails"))
        import krew_hooks
        only = [x.strip() for x in a.hooks_profiles.split(",") if x.strip()] or None
        profs = krew_hooks.profiles(hh, only)
        if not profs:
            say(f"hooks    no profile config under {hh}: make the profiles first, or name them with --hooks-profiles")
            ok = False
        cfg = krew_hooks.krew_settings.load("hooks", path=kh / "rails.yaml")
        for name, path in profs.items():
            ok &= krew_hooks.install_one(name, path, kh / "rails", auto_accept=not a.no_auto_accept,
                                         dry=a.dry_run, say=say, cfg=cfg)
    say(f"\nRails:   python3 {kh / 'rails' / 'tier0_gate.py'} --lint   (which rules run, which wait on a var in tier0.yaml)")
    if not a.hooks:
        say(f"         python3 {kh / 'rails' / 'krew_hooks.py'} install --hermes-home {hh} --rails-dir {kh / 'rails'}"
            "   (or krew-install --hooks)")
    if units:
        say(f"Janitor: python3 {kh / 'janitor' / 'krew-janitor.py'} --dry-run   (one report-only tick), then install the units:")
        say(f"         cp {kh / 'systemd' / 'user'}/krew-janitor*.{{service,timer}} ~/.config/systemd/user/ && "
            "systemctl --user daemon-reload && systemctl --user enable --now krew-janitor.timer krew-janitor-weekly.timer")
    return ok
