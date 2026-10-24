#!/usr/bin/env python3
"""Apply krew's Hermes patches to a Hermes Agent checkout at the pinned commit.

    python3 scripts/krew-patch.py check  [--hermes DIR]      would the series apply? changes nothing
    python3 scripts/krew-patch.py apply  [--hermes DIR]      apply the whole series, or nothing
    python3 scripts/krew-patch.py status [--hermes DIR]      pin, applied patches, tree state
    python3 scripts/krew-patch.py revert [--hermes DIR]      take the series back out
    python3 scripts/krew-patch.py test   [--hermes DIR] [--python PY]   run each patch's own tests

DIR defaults to $HERMES_TREE, else ~/.hermes/hermes-agent. The pin (commit, patch list, sha256 of every
patch) is config/hermes-pin.json. Refusals, each with what to do instead, and nothing changed:
  - DIR is not a git checkout of Hermes Agent;
  - HEAD is not the pinned commit (another Hermes version: the patches were proved on the pin only);
  - the checkout has local changes (apply needs a clean tree, so revert can restore it exactly);
  - a patch file does not match its sha256 in the pin.
`apply` checks each patch on top of the ones before it; if any fails, the ones already applied are reverted.
A later `hermes update` moves the checkout off the pin: run `revert` first, update, then use the krew
release made for the new pin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN_FILE = REPO / "config" / "hermes-pin.json"
PATCHES = REPO / "patches"
STATE = "krew-patches.json"


class Refuse(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"krew-patch: {msg}\nNothing was changed.")


def pin() -> dict:
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))


def git(d: Path, *args: str, check: bool = True, inp: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(d), *args], input=inp, capture_output=True, check=check)


def hermes_dir(arg: str | None) -> Path:
    d = arg or os.environ.get("HERMES_TREE") or "~/.hermes/hermes-agent"
    return Path(os.path.expanduser(d)).resolve()


def state_path(d: Path) -> Path:
    r = git(d, "rev-parse", "--git-dir", check=False)
    if r.returncode != 0:
        raise OSError(f"{d} is not a git checkout")
    gd = r.stdout.decode().strip()
    return (d / gd if not os.path.isabs(gd) else Path(gd)) / STATE


def verify(d: Path, p: dict, *, need_clean: bool) -> str:
    if not (d / ".git").exists() or git(d, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        raise Refuse(f"{d} is not a git checkout. Clone Hermes Agent at the pin first:\n"
                     f"  git clone {p['repo']} {d} && git -C {d} checkout {p['commit']}")
    if not (d / "hermes_cli").is_dir() and not (d / "run_agent.py").exists():
        raise Refuse(f"{d} does not look like Hermes Agent (no hermes_cli/). Pass --hermes <your hermes-agent checkout>.")
    head = git(d, "rev-parse", "HEAD").stdout.decode().strip()
    if head != p["commit"]:
        desc = git(d, "describe", "--tags", "--always", check=False).stdout.decode().strip() or head[:12]
        raise Refuse(
            f"Hermes version mismatch: {d} is at {head[:12]} ({desc}); these patches are for "
            f"{p['commit'][:12]} ({p['describe']}, version {p['version']}).\n"
            f"They were proved on that commit only, so they are not applied to any other.\n"
            f"Either check out the pin in a checkout you can spare:\n"
            f"  git -C {d} fetch origin && git -C {d} checkout {p['commit']}\n"
            f"or use the krew release made for your Hermes version.")
    for e in p["patches"]:
        f = PATCHES / e["file"]
        if not f.is_file():
            raise Refuse(f"patch {e['file']} is missing from {PATCHES}.")
        got = hashlib.sha256(f.read_bytes()).hexdigest()
        if got != e["sha256"]:
            raise Refuse(f"patch {e['file']} does not match its sha256 in {PIN_FILE.name} ({got[:12]} != "
                         f"{e['sha256'][:12]}): the file was edited. Restore it from the krew repo.")
    if need_clean:
        dirty = git(d, "status", "--porcelain", "--untracked-files=no").stdout.decode().strip()
        if dirty:
            n = len(dirty.splitlines())
            raise Refuse(f"{d} has {n} locally changed file(s) (git status). Apply needs a clean checkout so "
                         f"revert can restore it exactly. Commit or stash them, or use a fresh checkout at the pin.")
    return head


def read_state(d: Path) -> dict:
    try:
        return json.loads(state_path(d).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(d: Path, st: dict) -> None:
    sp = state_path(d)
    if st:
        sp.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    elif sp.exists():
        sp.unlink()


def _apply_one(d: Path, f: Path, reverse: bool = False, check_only: bool = False) -> subprocess.CompletedProcess:
    args = ["apply", "--whitespace=nowarn"] + (["--reverse"] if reverse else []) + (["--check"] if check_only else [])
    return git(d, *args, str(f), check=False)


def _revert(d: Path, files: list[str]) -> list[str]:
    bad = []
    for name in reversed(files):
        r = _apply_one(d, PATCHES / name, reverse=True)
        if r.returncode != 0:
            bad.append(f"{name}: {r.stderr.decode().strip()}")
    return bad


def cmd_check(d: Path, p: dict) -> int:
    st = read_state(d)
    verify(d, p, need_clean=not st.get("applied"))
    if st.get("applied"):
        print(f"already applied: {len(st['applied'])} patch(es) on {p['commit'][:12]}. `status` shows them.")
        return 0
    wt = Path(git(d, "rev-parse", "--git-dir").stdout.decode().strip())
    tmp = (d / wt if not wt.is_absolute() else wt) / "krew-check-tree"
    if tmp.exists():
        git(d, "worktree", "remove", "--force", str(tmp), check=False)
    git(d, "worktree", "add", "--detach", str(tmp), p["commit"])
    try:
        for e in p["patches"]:
            r = _apply_one(tmp, PATCHES / e["file"])
            if r.returncode != 0:
                print(f"FAIL {e['file']}: {r.stderr.decode().strip()}")
                return 1
            print(f"ok   {e['file']}")
    finally:
        git(d, "worktree", "remove", "--force", str(tmp), check=False)
    print(f"all {len(p['patches'])} patches apply on {p['commit'][:12]}")
    return 0


def cmd_apply(d: Path, p: dict) -> int:
    st = read_state(d)
    if st.get("applied"):
        verify(d, p, need_clean=False)
        print(f"already applied ({len(st['applied'])} patches); nothing to do. `revert` takes them out.")
        return 0
    verify(d, p, need_clean=True)
    done: list[str] = []
    for e in p["patches"]:
        f = PATCHES / e["file"]
        r = _apply_one(d, f, check_only=True)
        if r.returncode == 0:
            r = _apply_one(d, f)
        if r.returncode != 0:
            bad = _revert(d, done)
            msg = f"krew-patch: {e['file']} does not apply:\n{r.stderr.decode().strip()}\n"
            msg += (f"The {len(done)} patch(es) applied before it were reverted; the checkout is as it was."
                    if not bad else "Reverting the earlier ones ALSO failed (the checkout is part-patched):\n  "
                    + "\n  ".join(bad) + f"\nRestore with: git -C {d} checkout -- . (it was clean before apply)")
            print(msg, file=sys.stderr)
            return 1
        done.append(e["file"])
        print(f"applied {e['file']}")
    write_state(d, {"commit": p["commit"], "applied": done,
                    "sha256": {e["file"]: e["sha256"] for e in p["patches"]}})
    print(f"all {len(done)} patches applied on {p['commit'][:12]}. Restart Hermes (gateway / CLI) to load them.")
    return 0


def cmd_revert(d: Path, p: dict) -> int:
    st = read_state(d)
    if not st.get("applied"):
        print("no krew patches recorded as applied here; nothing to revert.")
        return 0
    verify(d, p, need_clean=False)
    bad = _revert(d, list(st["applied"]))
    if bad:
        print("krew-patch: some patches did not reverse cleanly (the tree was edited after apply):\n  "
              + "\n  ".join(bad), file=sys.stderr)
        return 1
    write_state(d, {})
    print(f"reverted {len(st['applied'])} patches; the checkout is back at the clean pin.")
    return 0


def cmd_status(d: Path, p: dict) -> int:
    print(f"pin      {p['commit'][:12]} ({p['describe']}), {len(p['patches'])} patches")
    if git(d, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        print(f"hermes   {d}: not a git checkout")
        return 1
    head = git(d, "rev-parse", "HEAD").stdout.decode().strip()
    print(f"hermes   {d} at {head[:12]}" + ("" if head == p["commit"] else "  (NOT the pin)"))
    st = read_state(d)
    applied = st.get("applied") or []
    changed = len(git(d, "status", "--porcelain", "--untracked-files=no").stdout.decode().splitlines())
    print(f"applied  {len(applied)} of {len(p['patches'])}" + (f": {', '.join(applied)}" if applied else ""))
    print(f"tree     {changed} changed file(s)")
    return 0 if head == p["commit"] else 1


def cmd_test(d: Path, p: dict, python: str, extra: list[str]) -> int:
    verify(d, p, need_clean=False)
    if not read_state(d).get("applied"):
        raise Refuse("the patches are not applied here; run `apply` first.")
    tests = []
    for e in p["patches"]:
        for t in e.get("tests") or []:
            if t not in tests:
                tests.append(t)
    if not tests:
        print("no patch declares tests")
        return 0
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra, *tests]
    print("+ " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    return subprocess.run(cmd, cwd=d).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="krew-patch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "apply", "status", "revert", "test"])
    ap.add_argument("--hermes", help="the Hermes Agent checkout (default $HERMES_TREE or ~/.hermes/hermes-agent)")
    ap.add_argument("--python", default=sys.executable, help="python of the Hermes venv, for `test`")
    ap.add_argument("pytest_args", nargs="*", help="extra pytest arguments for `test` (after --)")
    a = ap.parse_args(argv)
    d, p = hermes_dir(a.hermes), pin()
    if a.cmd == "check":
        return cmd_check(d, p)
    if a.cmd == "apply":
        return cmd_apply(d, p)
    if a.cmd == "revert":
        return cmd_revert(d, p)
    if a.cmd == "status":
        return cmd_status(d, p)
    return cmd_test(d, p, a.python, a.pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
