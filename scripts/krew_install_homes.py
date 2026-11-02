"""The homes an install may write, and the `hermes` it may run (the 1.0.2 buyer test, 2026-10-30, finding 5).

A scratch init (`--hermes-home ~/krew-pristine/.hermes --krew-home ~/krew-pristine/.krew --no-hermes`) wrote outside
the homes it was given: 38 unit files into the real ~/.config/systemd/user (the --unit-dir default), krew.json +
krew-render.json into the real Crissa home (~/.crissa / ~/.crissa, the panel and studio defaults), and the real
~/.hermes/hermes-agent/.hermes/bin/hermes{,-acp} launchers: the `hermes` on PATH is the REAL install's, and Hermes
regenerates its own launchers under whatever HERMES_HOME it is run with.

The rule: every write goes under the homes the install was given, or it is named and refused.

  * An install into this account's own homes (HERMES_HOME = ~/.hermes, KREW_HOME = ~/.krew) is a real install: the
    user systemd folder and the Crissa home are its own, as before.
  * Any other HERMES_HOME / KREW_HOME is a SCRATCH install: units go to <krew home>/systemd-user (written, never
    loaded) unless --unit-dir names a folder; the Crissa steps (panel tiles, studio pointer, marry, connect) run only
    with an explicit --crissa-home; Hermes's own installer (which writes ~/.local/bin and the shell rc) is refused.
  * `hermes` runs only from a launcher whose code root is inside the HERMES_HOME it serves (its own checkout's
    .hermes/bin/hermes, or a command on PATH that resolves there). Otherwise every step that would run it is named
    and refused: a scratch home never runs the real install's launcher.

Kill: KREW_INIT_HOME_GUARD=0 (the old defaults and the PATH `hermes`, as before).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

REAL_HERMES = "~/.hermes"
REAL_KREW = "~/.krew"
REAL_UNITS = "~/.config/systemd/user"
WALL_HOMES = ("~/.crissa",)


def guard_on() -> bool:
    return os.environ.get("KREW_INIT_HOME_GUARD", "1").strip().lower() not in ("0", "false", "no", "off")


def _x(p) -> Path:
    return Path(os.path.expanduser(str(p))).resolve()


def under(path, root) -> bool:
    try:
        _x(path).relative_to(_x(root))
        return True
    except ValueError:
        return False


def is_scratch(hh: Path, kh: Path) -> bool:
    """True when either home is not this account's own default."""
    return guard_on() and (_x(hh) != _x(REAL_HERMES) or _x(kh) != _x(REAL_KREW))


def unit_dir(given: str, hh: Path, kh: Path) -> tuple[Path, str]:
    """(folder, why). `given` is --unit-dir as typed ('' = not given)."""
    if given:
        return _x(given), "--unit-dir"
    if is_scratch(hh, kh):
        return _x(kh) / "systemd-user", ("a scratch install: units are written here, never into your user systemd "
                                          "folder (pass --unit-dir to choose)")
    return _x(REAL_UNITS), "your user systemd folder"


def wall_home(given: str, hh: Path, kh: Path) -> tuple[Path | None, str]:
    """(the Crissa home, why) or (None, the refusal)."""
    if given:
        return _x(given), "--crissa-home"
    env = os.environ.get("CRISSA_HOME", "").strip()
    if is_scratch(hh, kh):
        return None, ("refused: a scratch install never writes into a Crissa home it was not given (pass "
                      "--crissa-home)")
    if env:
        return _x(env), "$CRISSA_HOME"
    return _x(WALL_HOMES[0]), "the default Crissa home"


# --- the hermes command -------------------------------------------------------------------------------------------

_ROOT_RE = re.compile(r"sys\.path\.insert\(0,\s*'\"'\"'([^']+)'\"'\"'\)|sys\.path\.insert\(0,\s*['\"]([^'\"]+)['\"]\)")


def code_root(cmd: str) -> Path | None:
    """The Hermes checkout a launcher runs (`sys.path.insert(0, '<root>')` in Hermes's own launcher), following a
    shim (`exec <path> "$@"`) up to three hops. None when it cannot be read."""
    path = shutil.which(os.path.expanduser(cmd)) or os.path.expanduser(cmd)
    for _ in range(4):
        try:
            text = Path(path).read_bytes()[:16384].decode("utf-8", "replace")
        except OSError:
            return None
        m = _ROOT_RE.search(text)
        if m:
            return _x(m.group(1) or m.group(2))
        nxt = ""
        for ln in text.splitlines():
            w = ln.strip().split()
            if len(w) > 1 and w[0] == "exec":
                nxt = w[1].strip("'\"")
                break
        if not nxt or not os.path.isfile(nxt) or os.path.basename(nxt).startswith("python"):
            return None
        path = nxt
    return None


def tree_launcher(hh: Path) -> Path:
    return _x(hh) / "hermes-agent" / ".hermes" / "bin" / "hermes"


def hermes_for(hh: Path, given: str = "hermes") -> tuple[str | None, str]:
    """(the hermes command that serves `hh`, why) or (None, why not). With the guard off: `given` as before."""
    if not guard_on():
        return (shutil.which(os.path.expanduser(given)) or None), "the hermes on PATH (KREW_INIT_HOME_GUARD=0)"
    tree = _x(hh) / "hermes-agent"
    # A command named on purpose (KREW_HERMES_BIN, --hermes-bin) is the user's: taken unless it is readably another
    # install's launcher (a stand-in or wrapper whose code root cannot be read is theirs to answer for).
    for c in [os.environ.get("KREW_HERMES_BIN", ""), given if given and given != "hermes" else ""]:
        if not c:
            continue
        p = shutil.which(os.path.expanduser(c)) or os.path.expanduser(c)
        if not os.path.isfile(p):
            return None, f"{c} (named) does not exist"
        root = code_root(p)
        if root is None or under(root, hh):
            return p, f"named ({c})"
        return None, f"{c} (named) runs {root}, another install's code: refused for {hh}"
    cands = [str(tree_launcher(hh)), "hermes", "~/.local/bin/hermes"]
    seen = []
    for c in cands:
        p = shutil.which(os.path.expanduser(c)) or (os.path.expanduser(c) if os.path.isfile(os.path.expanduser(c)) else "")
        if not p or p in seen:
            continue
        seen.append(p)
        root = code_root(p)
        if root is not None and under(root, hh):
            return p, f"runs {root}"
    if seen:
        return None, (f"no hermes command serves {hh}: {', '.join(seen)} run{'s' if len(seen) == 1 else ''} another "
                      f"install's code, and Hermes rewrites that install's launchers under a foreign HERMES_HOME")
    return None, f"no hermes command yet (Hermes's own installer makes {tree_launcher(hh)})"


class Writes:
    """Every path an install step reports writing, for the end-of-init list and the scratch check."""

    def __init__(self):
        self.paths: list[str] = []
        self.refused: list[str] = []

    def refuse(self, what: str, why: str, say) -> None:
        self.refused.append(f"{what}: {why}")
        say(f"refused  {what}: {why}")
