#!/usr/bin/env python3
"""Write krew's security-rail hooks into each Hermes profile's config.yaml (scripts/krew-install.py --hooks).

    krew_hooks.py install --hermes-home ~/.hermes --rails-dir ~/.krew/rails [--profiles zoey,bash] [--dry-run]
    krew_hooks.py check   --hermes-home ~/.hermes --rails-dir ~/.krew/rails      exit 1 when a profile lacks one

The block written (per profile; the matcher widens where that profile has more acting tools):

    hooks:
      pre_tool_call:
        - matcher: terminal|execute_code|write_file|patch
          command: python3 <rails-dir>/tier0_gate.py
          timeout: 10
          fail_closed: true            # a crashed or timed-out gate refuses the call
        - matcher: ^terminal$          # only on the profiles that run tests (hooks.test_sandbox_profiles)
          command: python3 <rails-dir>/krew_test_sandbox.py
          timeout: 5
    hooks_auto_accept: true            # Hermes runs a shell hook only once it is approved; a kanban worker has no
                                       # TTY to approve it on, so without this the gate never runs there

The edit is textual (your comments and order are kept), backed up first (config.yaml.krew-bak-<time>), and
re-read afterwards: if the result does not parse or lacks an entry, the original is put back and the block to
add by hand is printed. An existing T0 hook (any command naming tier0_gate.py) is kept, never duplicated.
Settings: `hooks:` in $KREW_HOME/rails.yaml (config/rails.example.yaml).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import krew_settings  # noqa: E402

BASE_MATCHER = "terminal|execute_code|write_file|patch"
# Where a profile acts through more tools, its gate matcher says so (the defaults the crew runs with).
EXTRA_MATCHERS = {"default": "computer_use", "zoey": "browser", "lila": "browser_navigate"}
TEST_SANDBOX_PROFILES = ["bash", "steward", "vera"]


def settings() -> dict:
    return krew_settings.load("hooks", path=krew_settings.krew_home() / "rails.yaml")


def profiles(hermes_home: Path, only: list[str] | None = None) -> dict[str, Path]:
    """name -> config.yaml: `default` is $HERMES_HOME/config.yaml, the rest $HERMES_HOME/profiles/<name>/."""
    out: dict[str, Path] = {}
    if (hermes_home / "config.yaml").is_file():
        out["default"] = hermes_home / "config.yaml"
    for d in sorted((hermes_home / "profiles").glob("*/config.yaml")):
        out[d.parent.name] = d
    if only:
        missing = [n for n in only if n not in out]
        for n in missing:     # a named profile with no config yet gets one with just the hooks
            out[n] = hermes_home / "config.yaml" if n == "default" else hermes_home / "profiles" / n / "config.yaml"
        out = {n: out[n] for n in only}
    return out


def entries_for(profile: str, rails_dir: Path, cfg: dict | None = None) -> list[dict]:
    cfg = settings() if cfg is None else cfg
    extra = dict(EXTRA_MATCHERS)
    extra.update({str(k): str(v) for k, v in (cfg.get("extra_matchers") or {}).items()})
    matcher = str(cfg.get("tier0_matcher") or BASE_MATCHER)
    if extra.get(profile):
        matcher += "|" + extra[profile]
    out = [{"matcher": matcher, "command": f"python3 {rails_dir / 'tier0_gate.py'}", "timeout": 10,
            "fail_closed": True, "key": "tier0_gate.py"}]
    sandbox = cfg.get("test_sandbox_profiles")
    sandbox = TEST_SANDBOX_PROFILES if sandbox is None else list(sandbox)
    if profile in sandbox:
        out.append({"matcher": "^terminal$", "command": f"python3 {rails_dir / 'krew_test_sandbox.py'}",
                    "timeout": 5, "key": "krew_test_sandbox.py"})
    return out


def _item_lines(e: dict, indent: str) -> list[str]:
    lines = [f"{indent}- matcher: {_q(e['matcher'])}", f"{indent}  command: {_q(e['command'])}",
             f"{indent}  timeout: {e['timeout']}"]
    if e.get("fail_closed"):
        lines.append(f"{indent}  fail_closed: true")
    return lines


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'" if any(c in v for c in ":#{}[],&*!|>'\"%@`^$") else v


def present(doc: dict, key: str) -> str:
    """The command of an existing pre_tool_call hook naming `key`, else ''."""
    hooks = doc.get("hooks") if isinstance(doc.get("hooks"), dict) else {}
    for h in hooks.get("pre_tool_call") or []:
        if isinstance(h, dict) and key in str(h.get("command") or ""):
            return str(h.get("command"))
    return ""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def merge(text: str, add: list[dict], *, auto_accept: bool) -> str:
    """The config text with `add` placed under hooks.pre_tool_call (and hooks_auto_accept when asked)."""
    lines = text.splitlines()
    top = next((i for i, ln in enumerate(lines) if ln.startswith("hooks:")), None)
    if add:
        if top is None or lines[top].split(":", 1)[1].strip() in ("{}", "null", "~"):
            block = ["# krew security rails (krew-install --hooks): the T0 gate on every act, fail-closed.",
                     "hooks:", "  pre_tool_call:"] + [x for e in add for x in _item_lines(e, "    ")]
            if top is None:
                lines += ([""] if lines and lines[-1].strip() else []) + block
            else:
                lines[top:top + 1] = block
        else:
            end = next((j for j in range(top + 1, len(lines)) if lines[j].strip() and not lines[j].startswith((" ", "#"))),
                       len(lines))
            child = [j for j in range(top + 1, end) if lines[j].strip() and not lines[j].lstrip().startswith("#")]
            ci = _indent(lines[child[0]]) if child else 2
            ptc = next((j for j in child if lines[j].strip().startswith("pre_tool_call:")), None)
            if ptc is None:
                lines[top + 1:top + 1] = [" " * ci + "pre_tool_call:"] + [x for e in add for x in _item_lines(e, " " * (ci + 2))]
            else:
                if lines[ptc].split(":", 1)[1].strip() not in ("", "[]", "null", "~"):
                    raise ValueError("hooks.pre_tool_call is not a block list")
                items = [j for j in range(ptc + 1, end) if lines[j].strip() and not lines[j].lstrip().startswith("#")]
                ii = _indent(lines[items[0]]) if items and lines[items[0]].lstrip().startswith("-") else _indent(lines[ptc]) + 2
                lines[ptc] = " " * _indent(lines[ptc]) + "pre_tool_call:"
                lines[ptc + 1:ptc + 1] = [x for e in add for x in _item_lines(e, " " * ii)]
    if auto_accept and not any(ln.startswith("hooks_auto_accept:") for ln in lines):
        lines.append("hooks_auto_accept: true   # krew-install --hooks: workers have no TTY to approve a hook on")
    return "\n".join(lines) + "\n"


def install_one(name: str, path: Path, rails_dir: Path, *, auto_accept: bool, dry: bool, say, cfg=None) -> bool:
    want = entries_for(name, rails_dir, cfg)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        doc = krew_settings._safe_load(text) or {} if text.strip() else {}
    except Exception as e:  # noqa: BLE001
        say(f"hooks    {name}: {path} does not parse ({type(e).__name__}); left alone. Add by hand:")
        say("\n".join(x for e in want for x in _item_lines(e, "    ")))
        return False
    add = []
    for e in want:
        have = present(doc, e["key"])
        if have:
            say(f"hooks    {name}: kept existing {e['key']} hook ({have})")
        else:
            add.append(e)
    accept_set = doc.get("hooks_auto_accept")
    if not add and (accept_set is not None or not auto_accept):
        if accept_set is False:
            say(f"hooks    {name}: hooks_auto_accept is false (yours): a kanban worker cannot approve the gate")
        return True
    try:
        new = merge(text, add, auto_accept=auto_accept)
        check = krew_settings._safe_load(new) or {}
        lost = [e["key"] for e in want if not present(check, e["key"])]
        if lost:
            raise ValueError("missing after merge: " + ", ".join(lost))
    except Exception as e:  # noqa: BLE001
        say(f"hooks    {name}: could not merge into {path} ({e}); nothing changed. Add under hooks.pre_tool_call:")
        say("\n".join(x for e in add for x in _item_lines(e, "    ")))
        return False
    say(f"hooks    {name}: + " + ", ".join(e["key"] for e in add) + (" + hooks_auto_accept" if auto_accept and accept_set is None else "")
        + f" -> {path}")
    if dry:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + f".krew-bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    tmp = path.with_name(path.name + ".krew-tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return True


def check_one(name: str, path: Path, rails_dir: Path, cfg=None) -> list[str]:
    try:
        doc = krew_settings._safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return [f"{name}: {path} unreadable ({type(e).__name__})"]
    out = [f"{name}: no {e['key']} hook in {path}" for e in entries_for(name, rails_dir, cfg) if not present(doc, e["key"])]
    if present(doc, "tier0_gate.py") and doc.get("hooks_auto_accept") is not True:
        out.append(f"{name}: hooks_auto_accept is not true: the gate runs only where someone approved it at a TTY")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="krew_hooks", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("install", "check"))
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME") or "~/.hermes")
    ap.add_argument("--rails-dir", default=str(HERE))
    ap.add_argument("--profiles", default="", help="comma list (default: every profile with a config.yaml, and default)")
    ap.add_argument("--no-auto-accept", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    hh = Path(os.path.expanduser(a.hermes_home))
    rails = Path(os.path.expanduser(a.rails_dir))
    only = [x.strip() for x in a.profiles.split(",") if x.strip()] or None
    profs = profiles(hh, only)
    if not profs:
        print(f"hooks    no profile config under {hh} (make the profiles first, or name them with --profiles)")
        return 1
    if a.cmd == "check":
        bad = [x for n, p in profs.items() for x in check_one(n, p, rails)]
        for b in bad:
            print("MISSING  " + b)
        print(f"hooks    {len(profs)} profile(s) checked, {len(bad)} problem(s)")
        return 1 if bad else 0
    ok = all([install_one(n, p, rails, auto_accept=not a.no_auto_accept, dry=a.dry_run, say=print) for n, p in profs.items()])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
