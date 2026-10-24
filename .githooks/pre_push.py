"""krew's pre-push check (git passes `<local ref> <local sha> <remote ref> <remote sha>` lines on stdin).

For every pushed commit: krew's own scrub (tests/test_scrub.py's test_nothing_from_the_dev_box_ships, called directly;
pytest is not needed) runs on that commit's tree, exported to a scratch folder; each commit's names and message pass the same patterns, and its author and committer address must be a
GitHub no-reply address (or one named in this clone's own `git config --add krew.allowed-email <address>`).
Exit 1 refuses the push and says why.
"""
from __future__ import annotations

import importlib.util
import io
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ZERO = "0" * 40
NOREPLY = re.compile(r"^[\w.+-]+@users\.noreply\.github\.com$")


class _Inert:
    """Stands in for pytest when it is not installed: decorators hand the function back, the rest is inert."""

    def __getattr__(self, name):
        return self

    def __call__(self, *args, **kwargs):
        return args[0] if len(args) == 1 and callable(args[0]) and not kwargs else self


def load_scrub(tree: Path):
    sys.path.insert(0, str(tree / "tests"))
    try:
        import pytest  # noqa: F401
    except ImportError:
        sys.modules["pytest"] = _Inert()
    for name in ("test_krew_packages", "krew_scrub"):
        sys.modules.pop(name, None)
    try:
        spec = importlib.util.spec_from_file_location("krew_scrub", tree / "tests" / "test_scrub.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(tree / "tests"))


def main() -> int:
    allowed = set(subprocess.run(["git", "config", "--get-all", "krew.allowed-email"], capture_output=True,
                                 text=True).stdout.split())
    bad: list[str] = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue
        local, remote = parts[1], parts[3]
        with tempfile.TemporaryDirectory() as d:
            raw = subprocess.run(["git", "archive", local], capture_output=True, check=True).stdout
            tarfile.open(fileobj=io.BytesIO(raw)).extractall(d, filter="data")
            mod = load_scrub(Path(d))
            try:
                mod.test_nothing_from_the_dev_box_ships()
            except AssertionError as e:
                bad.append("%s: krew's scrub refuses the pushed tree:\n%s" % (local[:8], e))
        rng = local if remote == ZERO else "%s..%s" % (remote, local)
        log = subprocess.run(["git", "log", "--format=%H%x01%an%x01%ae%x01%cn%x01%ce%x01%B%x00", rng],
                             capture_output=True, text=True, check=True).stdout
        for rec in log.split("\x00"):
            if not rec.strip():
                continue
            sha, an, ae, cn, ce, msg = rec.strip("\n").split("\x01", 5)
            for who, mail in (("author", ae), ("committer", ce)):
                if mail not in allowed and not NOREPLY.match(mail):
                    bad.append("commit %s: the %s address is not one this repository publishes" % (sha[:8], who))
            text = "%s\n%s\n%s" % (an, cn, msg)
            for label, rx in mod.SCRUB:
                if rx.search(text):
                    bad.append("commit %s (message or names): %s" % (sha[:8], label))
            for hit in mod.name_offences("commit %s" % sha[:8], text):
                bad.append(hit)
    for b in bad:
        print(b, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
