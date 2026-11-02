"""THE unit registry: one list of every crew service, timer, path and socket, discovered from the
machine, never typed by hand.

The owner 2026-10-06: "If it's worth setting up, it's worth watching." The panel's tiles, the janitor's watch
list and the All OS Off / Turn On All Services switch all read this registry, so a unit cannot be on one
and missing from another.

WHAT COUNTS AS A UNIT OF OURS (read from systemd, user and system scope, each row says which rule hit):
  name      `registry.name_prefixes` in $KREW_HOME/janitor.yaml (default krew-*, crissa-*, hermes-*)
  exec      ExecStart contains one of `registry.exec_markers` (default ~/.krew, ~/.hermes)
  folder    the unit file is installed in ~/.config/systemd/user (where the machine installs its units;
            the desktop's own units live in /usr/lib/systemd/user)
  agent     a system unit installed in /etc/systemd/system that runs as the crew's user
  notes     named in $KREW_HOME/unit-notes.json (config/unit-notes.example.json; a container with no crew path in argv)
Templates (foo@.service), masked units (the retired per-profile gateways) and not-found names are kept
OUT of the watched set but listed under "masked" so nothing hides.

ROLE (how it is watched): service (enabled: should be running, or active/exited for a oneshot that
remains), timer / path / socket (should be waiting, and the job it starts must not have failed), job
(a static/oneshot service something triggers: red only when its last run failed), manual (installed,
not enabled, nothing triggers it: listed, red only if failed), masked.

REFRESH: the night's job (janitor/krew-units-refresh.py, unit krew-units-refresh.service, a row in
$KREW_HOME/window-schedule.json after the M&S nightly) and a cheap check on every janitor tick (cheap_check:
compares the unit-file names systemd lists; a full discovery only when they changed). A new unit is added with a visible `new`
note; a unit that disappeared is culled with a visible `removed` note (CHANGES log + the panel). Notes stay
for 7 days.

Notes ($KREW_HOME/unit-notes.json, shape config/unit-notes.example.json) only DESCRIBE units - label, what,
feeds, off_file, expect, log_check, port, section. They never decide membership: a unit with no note is still watched ("no description yet"),
and a note for a unit that is not on the machine is reported, not obeyed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import sys as _sys
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))
import krew_settings  # noqa: E402
import krew_hermes as KH  # noqa: E402  where Hermes lives


def _cfg(section: str) -> dict:
    """One section of $KREW_HOME/janitor.yaml, or of $KREW_JANITOR_CONFIG (config/janitor.example.yaml lists every key)."""
    return krew_settings.load(section, path=os.environ.get("KREW_JANITOR_CONFIG") or krew_settings.krew_home() / "janitor.yaml")


def _p(v) -> Path:
    return Path(os.path.expanduser(str(v)))

HOME = Path.home()
_R = _cfg("registry")
NOTES_FILE = Path(os.environ.get("KREW_UNIT_NOTES") or _p(_R.get("notes") or krew_settings.krew_home() / "unit-notes.json"))
STATE = Path(os.environ.get("KREW_UNITS_DIR") or _p(_R.get("state_dir") or krew_settings.krew_home() / "units"))
REGISTRY = STATE / "registry.json"
CHANGES = STATE / "changes.jsonl"
USER_UNIT_DIR = Path(os.environ.get("KREW_USER_UNIT_DIR", str(HOME / ".config" / "systemd" / "user")))
SYSTEM_UNIT_DIR = Path(os.environ.get("KREW_SYSTEM_UNIT_DIR", "/etc/systemd/system"))
KREW_USER = os.environ.get("USER") or HOME.name

KINDS = (".service", ".timer", ".path", ".socket")
NAME_PREFIXES = tuple(_R.get("name_prefixes") or ("krew-", "crissa-", "hermes-"))
EXEC_MARKERS = tuple(os.path.expanduser(str(x)) for x in (_R.get("exec_markers") or ("~/.krew", str(KH.hermes_home()))))
NOTE_DAYS = 7
SHOW_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "Type", "FragmentPath", "User", "ExecStart",
              "TriggeredBy", "Triggers", "UnitFileState", "RemainAfterExit", "Description", "Result")

Runner = Callable[[list, float], "tuple[int, str, str]"]


def default_run(argv: list, timeout: float = 60.0) -> "tuple[int, str, str]":
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except OSError as e:
        return 127, "", f"{type(e).__name__}: {e}"


def sc(scope: str) -> list:
    return ["systemctl", "--user"] if scope == "user" else ["systemctl", "--no-ask-password"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---- notes ---------------------------------------------------------------------------------------------
def load_notes(path: Path | None = None) -> dict:
    try:
        d = json.loads(Path(path or NOTES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("notes", {})
    d.setdefault("gaps", [])
    return d


# ---- discovery -----------------------------------------------------------------------------------------
def parse_show(text: str) -> list:
    blocks, cur = [], {}
    for ln in text.splitlines():
        if not ln.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        k, _, v = ln.partition("=")
        cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def show(scope: str, units, run: Runner = default_run, props=SHOW_PROPS) -> dict:
    units = list(dict.fromkeys(units))
    out = {}
    for i in range(0, len(units), 150):          # argv stays short; one call per 150 units
        part = units[i:i + 150]
        rc, txt, _ = run(sc(scope) + ["show"] + [f"-p{p}" for p in props] + part, 60.0)
        for asked, b in zip(part, parse_show(txt)):
            out[asked] = b
    return out


def unit_file_names(scope: str, run: Runner = default_run) -> list:
    """Every unit file systemd knows in this scope, by name - the cheap check's whole input."""
    rc, txt, _ = run(sc(scope) + ["list-unit-files", "--no-legend", "--plain", "--no-pager",
                                  "--type=service,timer,path,socket"], 30.0)
    names = []
    for ln in txt.splitlines():
        parts = ln.split()
        if parts and parts[0].endswith(KINDS) and "@." not in parts[0]:
            names.append(parts[0])
    return sorted(set(names))


def _exec_text(p: dict) -> str:
    return p.get("ExecStart") or ""


def crew_reason(scope: str, name: str, p: dict, notes: dict) -> str:
    """Why this unit is the machine's, or '' when it is not. First matching rule wins (all are shown in tests)."""
    if name.startswith(NAME_PREFIXES):
        return "name " + next(x for x in NAME_PREFIXES if name.startswith(x)) + "*"
    ex = _exec_text(p)
    for m in EXEC_MARKERS:
        if m in ex:
            return f"exec into {m}"
    frag = p.get("FragmentPath") or ""
    if scope == "user" and frag.startswith(str(USER_UNIT_DIR) + "/"):
        return f"installed in {USER_UNIT_DIR}"
    if scope == "system" and frag.startswith(str(SYSTEM_UNIT_DIR) + "/") and (p.get("User") or "") == KREW_USER:
        return f"system unit run as {KREW_USER}"
    if name in notes:
        return "named in unit-notes.json"
    return ""


def _role(name: str, p: dict) -> str:
    if (p.get("LoadState") or "") == "masked":
        return "masked"
    for k in (".timer", ".path", ".socket"):
        if name.endswith(k):
            return k[1:]
    if (p.get("TriggeredBy") or "").strip():
        return "job"
    if (p.get("UnitFileState") or "") in ENABLED_STATES:
        return "service"
    if (p.get("ActiveState") or "") in ("active", "activating", "reloading"):
        return "service"          # not enabled but someone started it: watched while it runs
    return "manual"


ENABLED_STATES = ("enabled", "enabled-runtime", "linked", "linked-runtime", "alias", "generated")


def _expect(role: str, p: dict) -> str:
    """running | exited | any | timer | path | socket | off. `off` = installed but disabled: listed with
    that word, never red unless it failed. A oneshot that does not remain (runs at boot and ends) is `any`."""
    enabled = (p.get("UnitFileState") or "") in ENABLED_STATES
    if role == "service":
        if p.get("Type") == "oneshot":
            return "exited" if p.get("RemainAfterExit") == "yes" else "any"
        return "running"
    if role in ("timer", "path", "socket"):
        return role if enabled else "off"
    return "any"


def discover(run: Runner = default_run, notes: dict | None = None) -> list:
    """Every crew unit on the machine, read-only. One row each, sorted, aliases folded."""
    notes = (load_notes() if notes is None else notes).get("notes") or {}
    rows, seen = [], set()
    for scope in ("system", "user"):
        names = unit_file_names(scope, run)
        noted = [n for n, v in notes.items() if (v.get("scope") or "user") == scope and n not in names]
        props = show(scope, names + noted, run)
        whys = {n: crew_reason(scope, n, props.get(n) or {}, notes) for n in names + noted}
        for n in names:     # a timer/path/socket that starts a crew unit is ours too (steward-sweep.timer)
            if not whys[n] and n.endswith((".timer", ".path", ".socket")):
                hit = [t for t in (props.get(n) or {}).get("Triggers", "").split() if whys.get(t)]
                if hit:
                    whys[n] = f"starts {hit[0]}"
        for n in names + noted:
            p = props.get(n) or {}
            why = whys[n]
            if not why:
                continue
            uid = p.get("Id") or n
            load = p.get("LoadState") or "unknown"
            if load == "not-found":
                continue
            if (scope, uid) in seen:
                continue
            seen.add((scope, uid))
            role = _role(uid, p)
            rows.append({"unit": uid, "scope": scope, "why_house": why, "role": role,
                         "expect": (notes.get(uid) or {}).get("expect") or _expect(role, p),
                         "active": p.get("ActiveState") or "unknown", "sub": p.get("SubState") or "",
                         "type": p.get("Type") or "", "file_state": p.get("UnitFileState") or "",
                         "triggered_by": (p.get("TriggeredBy") or "").split(),
                         "description": (p.get("Description") or "")[:200]})
    rows.sort(key=lambda r: (r["scope"], r["unit"]))
    return rows


# ---- the registry file + change notes ------------------------------------------------------------------
def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path | None = None) -> dict:
    d = _read(Path(path or REGISTRY))
    d.setdefault("units", [])
    d.setdefault("changes", [])
    return d


def _key(r: dict) -> tuple:
    return (r["scope"], r["unit"])


def merge(old: dict, found: list, *, by: str, now: Optional[str] = None) -> "tuple[dict, list]":
    """New registry from what was found. New units get first_seen + a `new` note; units that vanished are
    culled with a `removed` note. Returns (registry, changes made this run)."""
    now = now or _now()
    before = {_key(r): r for r in old.get("units") or []}
    after = {}
    changes = []
    for r in found:
        k = _key(r)
        prev = before.get(k)
        row = dict(r, first_seen=(prev or {}).get("first_seen") or now, last_seen=now)
        if prev is None and before:          # the very first scan is a baseline, not 138 "new" notes
            row["new_since"] = now
            changes.append({"ts": now, "change": "new", "unit": r["unit"], "scope": r["scope"],
                            "role": r["role"], "why_house": r["why_house"], "by": by})
        elif prev is not None and prev.get("new_since"):
            row["new_since"] = prev["new_since"]
        after[k] = row
    for k, prev in before.items():
        if k not in after:
            changes.append({"ts": now, "change": "removed", "unit": prev["unit"], "scope": prev["scope"],
                            "role": prev.get("role"), "last_seen": prev.get("last_seen"), "by": by})
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - NOTE_DAYS * 86400))
    kept = [c for c in old.get("changes") or [] if str(c.get("ts") or "") >= cutoff]
    for r in after.values():
        if r.get("new_since") and r["new_since"] < cutoff:
            r.pop("new_since")
    reg = {"schema": "crew_units/v1", "updated": now, "by": by,
           "rules": {"name": list(NAME_PREFIXES), "exec": list(EXEC_MARKERS), "folder": str(USER_UNIT_DIR),
                     "agent": f"system units in {SYSTEM_UNIT_DIR} with User={KREW_USER}"},
           "file_names": old.get("file_names") or {},
           "units": sorted(after.values(), key=lambda r: (r["scope"], r["unit"])),
           "changes": kept + changes}
    return reg, changes


def refresh(run: Runner = default_run, *, by: str = "manual", path: Path | None = None,
            notes: dict | None = None) -> dict:
    """Full discovery -> registry file. Returns {ok, n, new, removed}."""
    path = Path(path or REGISTRY)
    old = load(path)
    found = discover(run, notes)
    reg, changes = merge(old, found, by=by)
    reg["file_names"] = {s: unit_file_names(s, run) for s in ("system", "user")}
    _write(path, reg)
    if changes:
        try:
            with (path.parent / CHANGES.name).open("a", encoding="utf-8") as fh:
                for c in changes:
                    fh.write(json.dumps(c) + "\n")
        except OSError:
            pass
    return {"ok": True, "n": len(reg["units"]), "new": [c["unit"] for c in changes if c["change"] == "new"],
            "removed": [c["unit"] for c in changes if c["change"] == "removed"]}


def cheap_check(run: Runner = default_run, *, path: Path | None = None) -> dict:
    """The janitor tick's check: two `list-unit-files` calls. Full refresh only when the names changed
    (or there is no registry yet)."""
    path = Path(path or REGISTRY)
    old = load(path)
    now = {s: unit_file_names(s, run) for s in ("system", "user")}
    if old.get("units") and old.get("file_names") == now:
        return {"ok": True, "changed": False, "n": len(old["units"])}
    r = refresh(run, by="janitor tick", path=path)
    r["changed"] = True
    return r


def missing(found: list, reg: dict | None = None) -> list:
    """Crew units on the machine (`found`, from discover()) that the registry file does not list, as
    "scope:unit". The test that fails if a crew unit is unregistered calls this."""
    reg = load() if reg is None else reg
    listed = {(r.get("scope"), r.get("unit")) for r in reg.get("units") or []}
    return sorted(f"{r['scope']}:{r['unit']}" for r in found if (r["scope"], r["unit"]) not in listed)


# ---- views ---------------------------------------------------------------------------------------------
WATCHED_ROLES = ("service", "timer", "path", "socket", "job", "manual")


def units(reg: dict | None = None, *, roles=WATCHED_ROLES) -> list:
    reg = load() if reg is None else reg
    return [r for r in reg.get("units") or [] if r.get("role") in roles]


def recent_changes(reg: dict | None = None) -> list:
    reg = load() if reg is None else reg
    return list(reg.get("changes") or [])


def watch_entries(reg: dict | None = None, notes: dict | None = None, *, skip: set = frozenset()) -> list:
    """The janitor's watch list = every watched registry unit, described by its note when there is one."""
    reg = load() if reg is None else reg
    nd = (load_notes() if notes is None else notes).get("notes") or {}
    out = []
    for r in units(reg):
        if r["unit"] in skip:
            continue
        n = nd.get(r["unit"]) or {}
        e = {"unit": r["unit"], "scope": r["scope"], "role": r["role"],
             "label": n.get("label") or r["unit"].rsplit(".", 1)[0],
             "what": n.get("what") or r.get("description") or "no description yet",
             "feeds": n.get("feeds") or "not written down yet",
             "expect": n.get("expect") or r.get("expect") or "running"}
        for k in ("off_file", "log_check", "pause", "reason_file"):
            if n.get(k):
                e[k] = n[k]
        if r.get("new_since"):
            e["new_since"] = r["new_since"]
        out.append(e)
    return out


def notes_without_units(reg: dict | None = None, notes: dict | None = None) -> list:
    """Notes naming a unit the machine does not have: reported (the panel + a test), never obeyed."""
    reg = load() if reg is None else reg
    have = {r["unit"] for r in reg.get("units") or []}
    nd = (load_notes() if notes is None else notes).get("notes") or {}
    return sorted(u for u in nd if u not in have)


def panel_tiles(reg: dict | None = None, notes: dict | None = None, *, tiled: set = frozenset()) -> list:
    """Every long-running crew service the panel has no tile for yet, as tiles to append under
    'New - not yet sorted' (label from the note, or the unit name). Timers/jobs show on the Janitor tile."""
    reg = load() if reg is None else reg
    nd = (load_notes() if notes is None else notes).get("notes") or {}
    out = []
    for r in units(reg, roles=("service",)):
        bare = r["unit"][:-8] if r["unit"].endswith(".service") else r["unit"]
        if bare in tiled or r["unit"] in tiled:
            continue
        n = nd.get(r["unit"]) or {}
        out.append({"name": bare, "label": n.get("label") or bare, "port": n.get("port"),
                    "section": n.get("section") or "New · not yet sorted", "scope": r["scope"],
                    "new_since": r.get("new_since")})
    return out


# ---- the polkit grant for All OS Off/On (generated, never hand-edited) ------------------------------------
POLKIT_FILE = Path(os.environ.get("KREW_POLKIT_FILE") or _p(_R.get("polkit_file") or krew_settings.krew_home() / "polkit" / "51-krew-os-power.rules"))
POLKIT_ROLES = ("service", "timer", "path", "socket", "job")


def polkit_units(reg: dict | None = None) -> list:
    """System-scope crew units All OS Off may stop and Turn On All Services may start again. `manual`
    (static, nothing starts them) and masked units are left out: if one is ever up at Off, the refusal is
    named on the panel, never silent."""
    reg = load() if reg is None else reg
    return sorted(r["unit"] for r in reg.get("units") or []
                  if r.get("scope") == "system" and r.get("role") in POLKIT_ROLES)


def polkit_rule(reg: dict | None = None, user: str = KREW_USER) -> str:
    names = polkit_units(reg)
    body = ",\n        ".join(json.dumps(n) for n in names)
    return f"""// GENERATED by `lib/krew_registry.py polkit` from THE unit registry - do not hand-edit.
// All OS Off / Turn On All Services (janitor/krew_os_power.py, the owner 2026-10-06): the crew's user may
// START and STOP the system-scope crew units below without a password prompt. Restart for the three
// panel tiles stays in 50-house-panel-restart.rules (gated by the owner signal).
// A new system crew unit makes tests/test_krew_registry.py fail until this file is regenerated:
//   python3 lib/krew_registry.py polkit > config/polkit/51-krew-os-power.rules
// Install (sudo, once per regeneration):
//   sudo install -m 0644 config/polkit/51-krew-os-power.rules /etc/polkit-1/rules.d/
// Remove the file to take the permission back (polkit rereads rules.d on change).
polkit.addRule(function(action, subject) {{
    if (action.id != "org.freedesktop.systemd1.manage-units") return;
    if (subject.user != {json.dumps(user)}) return;
    var verb = action.lookup("verb");
    if (verb != "start" && verb != "stop") return;
    var units = [
        {body}
    ];
    if (units.indexOf(action.lookup("unit")) >= 0) {{
        return polkit.Result.YES;
    }}
}});
"""


def polkit_file_units(path: Path | None = None) -> list:
    import re as _re
    try:
        text = Path(path or POLKIT_FILE).read_text(encoding="utf-8")
    except OSError:
        return []
    block = text.split("var units = [", 1)[1].split("];", 1)[0] if "var units = [" in text else ""
    return sorted(_re.findall(r'"([^"]+)"', block))


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="krew-units", description="the unit registry")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="discover and print, write nothing")
    sub.add_parser("refresh", help="discover and write the registry (new/removed notes)")
    sub.add_parser("check", help="the janitor's cheap check")
    sub.add_parser("polkit", help="print the generated All OS Off/On polkit rule (from a live discovery)")
    a = ap.parse_args(argv)
    if a.cmd == "polkit":
        print(polkit_rule({"units": discover()}), end="")
        return 0
    if a.cmd == "list":
        for r in discover():
            print(f"{r['scope']:6} {r['role']:7} {r['unit']:46} {r['active']:9} {r['why_house']}")
        return 0
    res = refresh(by="cli") if a.cmd == "refresh" else cheap_check()
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
