"""THE unit registry (the owner 2026-10-06: "if it's worth setting up, it's worth watching").
lib/krew_registry.py discovers every crew unit from systemd; the panel tiles, the janitor's watch list and
All OS Off read it. config/unit-notes.json only describes units."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))
import krew_registry as hr  # noqa: E402

HOME = Path.home()


class FakeSystemd:
    """list-unit-files + show for two scopes. units[(scope, name)] = props."""

    def __init__(self, units):
        self.units = units
        self.calls = []

    def __call__(self, argv, timeout=60.0):
        self.calls.append(argv)
        scope = "user" if "--user" in argv else "system"
        if "list-unit-files" in argv:
            names = sorted(n for (s, n) in self.units if s == scope)
            return 0, "".join(f"{n} {self.units[(scope, n)].get('UnitFileState', 'static')} -\n" for n in names), ""
        if "show" in argv:
            names = [a for a in argv[argv.index("show") + 1:] if not a.startswith("-p")]
            blocks = []
            for n in names:
                p = dict(self.units.get((scope, n)) or {"LoadState": "not-found"})
                p.setdefault("Id", n)
                p.setdefault("LoadState", "loaded")
                blocks.append("\n".join(f"{k}={v}" for k, v in p.items()))
            return 0, "\n\n".join(blocks) + "\n", ""
        return 0, "", ""


def U(**kw):
    base = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "Type": "simple",
            "UnitFileState": "enabled", "FragmentPath": "/usr/lib/systemd/user/x.service", "ExecStart": ""}
    base.update(kw)
    return base


def fake_box():
    ud = str(hr.USER_UNIT_DIR)
    return FakeSystemd({
        ("user", "crew-wall-ascii.service"): U(),                                      # name rule
        ("user", "crew-lila-doctor.service"): U(),
        ("user", "audio8-asr.service"): U(ExecStart="{ path=<envs>/audio8/bin/python ; }"),   # exec rule
        ("user", "cloudflared-crissa.service"): U(FragmentPath=f"{ud}/cloudflared-crissa.service",
                                                 ExecStart="{ path=/usr/bin/cloudflared ; }"),  # folder rule
        ("user", "pipewire.service"): U(),                                              # the desktop: not crew
        ("user", "hermes-crissa-gateway.service"): U(LoadState="masked", ActiveState="inactive",
                                                   UnitFileState="masked"),
        ("user", "krew-janitor.timer"): U(SubState="waiting", Triggers="krew-janitor.service"),
        ("user", "krew-janitor.service"): U(ActiveState="inactive", SubState="dead", Type="oneshot",
                                             UnitFileState="static", TriggeredBy="krew-janitor.timer"),
        ("user", "keep-warm.timer"): U(ActiveState="inactive", SubState="dead", UnitFileState="disabled",
                                       FragmentPath=f"{ud}/keep-warm.timer"),
        ("system", "approvals-api.service"): U(User=hr.KREW_USER,
                                               FragmentPath=f"{hr.SYSTEM_UNIT_DIR}/approvals-api.service"),
        ("system", "steward-sweep.timer"): U(SubState="waiting", Triggers="steward-sweep.service",
                                             FragmentPath=f"{hr.SYSTEM_UNIT_DIR}/steward-sweep.timer"),
        ("system", "steward-sweep.service"): U(ActiveState="inactive", Type="oneshot", UnitFileState="static",
                                               User=hr.KREW_USER, TriggeredBy="steward-sweep.timer",
                                               FragmentPath=f"{hr.SYSTEM_UNIT_DIR}/steward-sweep.service"),
        ("system", "sshd.service"): U(FragmentPath=f"{hr.SYSTEM_UNIT_DIR}/sshd.service"),   # the computer
        ("system", "searxng.service"): U(ExecStart="{ path=/usr/bin/podman ; }"),
    })


NOTES = {"notes": {"searxng.service": {"scope": "system", "label": "Searxng", "what": "web search"},
                   "crew-wall-ascii.service": {"label": "Wall", "what": "the Tk glass", "feeds": "the wall",
                                                "off_file": "~/.hermes/wall.off"}},
         "gaps": []}


def test_discovery_rules_each_say_why_and_skip_the_computer():
    rows = {r["unit"]: r for r in hr.discover(fake_box(), NOTES)}
    assert rows["crew-wall-ascii.service"]["why_house"] == "name crew-*"
    assert rows["audio8-asr.service"]["why_house"] == "exec into <envs>"
    assert rows["cloudflared-crissa.service"]["why_house"].startswith("installed in")
    assert rows["approvals-api.service"]["why_house"].startswith("system unit run as")
    assert rows["searxng.service"]["why_house"] == "named in unit-notes.json"
    assert rows["steward-sweep.timer"]["why_house"] == "starts steward-sweep.service", "a timer of a crew job is crew"
    assert "pipewire.service" not in rows and "sshd.service" not in rows


def test_roles_and_expectations():
    rows = {r["unit"]: r for r in hr.discover(fake_box(), NOTES)}
    assert rows["crew-wall-ascii.service"]["role"] == "service" and rows["crew-wall-ascii.service"]["expect"] == "running"
    assert rows["krew-janitor.service"]["role"] == "job" and rows["krew-janitor.service"]["expect"] == "any"
    assert rows["krew-janitor.timer"]["expect"] == "timer"
    assert rows["keep-warm.timer"]["expect"] == "off", "disabled timer: listed, never red for being off"
    assert rows["hermes-crissa-gateway.service"]["role"] == "masked"
    watched = {e["unit"] for e in hr.watch_entries({"units": list(rows.values())}, NOTES)}
    assert "hermes-crissa-gateway.service" not in watched, "masked units are listed, not watched"


def test_merge_first_scan_is_a_baseline_then_new_and_removed_are_visible():
    found = hr.discover(fake_box(), NOTES)
    reg, ch = hr.merge({}, found, by="t", now=hr.time.strftime("%Y-%m-%dT%H:%M:%S", hr.time.localtime(hr.time.time() - 3600)))  # inside NOTE_DAYS
    assert ch == [] and all(not r.get("new_since") for r in reg["units"])
    newbox = fake_box()
    newbox.units[("user", "krew-crissa-new-thing.service")] = U()
    del newbox.units[("user", "crew-lila-doctor.service")]
    reg2, ch2 = hr.merge(reg, hr.discover(newbox, NOTES), by="t", now=hr.time.strftime("%Y-%m-%dT%H:%M:%S"))
    kinds = {(c["change"], c["unit"]) for c in ch2}
    assert ("new", "krew-crissa-new-thing.service") in kinds
    assert ("removed", "crew-lila-doctor.service") in kinds, "a vanished unit is culled WITH a note"
    assert "crew-lila-doctor.service" not in {r["unit"] for r in reg2["units"]}
    assert next(r for r in reg2["units"] if r["unit"] == "krew-crissa-new-thing.service")["new_since"]
    assert len(reg2["changes"]) == 2


def test_cheap_check_only_rediscovers_when_unit_files_change(tmp_path):
    box = fake_box()
    p = tmp_path / "registry.json"
    r1 = hr.cheap_check(box, path=p)
    assert r1["changed"] and p.is_file()
    n_show = sum(1 for c in box.calls if "show" in c)
    r2 = hr.cheap_check(box, path=p)
    assert r2["changed"] is False
    assert sum(1 for c in box.calls if "show" in c) == n_show, "no show calls when nothing changed"
    box.units[("user", "rail-new.service")] = U()
    r3 = hr.cheap_check(box, path=p)
    assert r3["changed"] and r3["new"] == ["rail-new.service"]


def test_an_unregistered_crew_unit_is_caught(tmp_path):
    """The missing-unit check itself: registry written, then a new crew unit appears on the box -> named."""
    box = fake_box()
    p = tmp_path / "registry.json"
    hr.refresh(box, by="test", path=p, notes=NOTES)
    assert hr.missing(hr.discover(box, NOTES), hr.load(p)) == []
    box.units[("user", "krew-crissa-sneaky.service")] = U()
    box.units[("system", "crew-new-sys.service")] = U(FragmentPath=f"{hr.SYSTEM_UNIT_DIR}/crew-new-sys.service")
    assert hr.missing(hr.discover(box, NOTES), hr.load(p)) == ["system:crew-new-sys.service",
                                                                "user:krew-crissa-sneaky.service"]




def test_the_polkit_grant_is_generated_start_stop_only_for_system_crew_units(tmp_path):
    reg = {"units": [r for r in hr.discover(fake_box(), NOTES)]}
    text = hr.polkit_rule(reg, user="agent")
    p = tmp_path / "51.rules"
    p.write_text(text)
    assert hr.polkit_file_units(p) == ["approvals-api.service", "searxng.service", "steward-sweep.service",
                                       "steward-sweep.timer"], "system scope only; sshd (not crew) never"
    assert 'verb != "start" && verb != "stop"' in text and "restart" not in text.split("var verb", 1)[1]
    assert 'subject.user != "agent"' in text and "GENERATED" in text


def test_watch_entries_use_notes_and_never_drop_an_undescribed_unit():
    reg = {"units": hr.discover(fake_box(), NOTES)}
    es = {e["unit"]: e for e in hr.watch_entries(reg, NOTES)}
    assert es["crew-wall-ascii.service"]["label"] == "Wall" and es["crew-wall-ascii.service"]["off_file"]
    assert es["crew-lila-doctor.service"]["what"], "no note still watched"
    assert "crew-lila-doctor.service" in es
    assert "crew-wall-ascii.service" not in {e["unit"] for e in hr.watch_entries(reg, NOTES, skip={"crew-wall-ascii.service"})}


def test_panel_tiles_append_every_untiled_crew_service():
    reg = {"units": hr.discover(fake_box(), NOTES)}
    tiles = {t["name"]: t for t in hr.panel_tiles(reg, NOTES, tiled={"crew-wall-ascii"})}
    assert "crew-wall-ascii" not in tiles
    assert {"crew-lila-doctor", "audio8-asr", "cloudflared-crissa", "approvals-api", "searxng"} <= set(tiles)
    assert tiles["searxng"]["label"] == "Searxng"
    assert tiles["crew-lila-doctor"]["section"] == "New · not yet sorted"


def test_notes_never_decide_membership():
    reg = {"units": hr.discover(fake_box(), NOTES)}
    notes = {"notes": dict(NOTES["notes"], **{"ghost.service": {"label": "gone"}}), "gaps": []}
    assert hr.notes_without_units(reg, notes) == ["ghost.service"]
    assert "ghost.service" not in {e["unit"] for e in hr.watch_entries(reg, notes)}




# ---- this machine ---------------------------------------------------------------------------------------
# a user manager answers one of these; "offline" / "unknown" / nothing (no bus) means it was not reached
_REACHED = {"initializing", "starting", "running", "degraded", "maintenance", "stopping"}


def user_systemd_reachable(run=None) -> bool:
    """True only when `systemctl --user` reached a user manager. is-system-running exits 1 for `degraded`
    (reached) and for "Failed to connect to bus" (not), so the word it answers decides, never the exit code."""
    if run is None:
        if shutil.which("systemctl") is None:
            return False
        run = subprocess.run
    try:
        r = run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 - no systemctl, a hung bus: not reachable
        return False
    return r.returncode in (0, 1) and (r.stdout or "").strip() in _REACHED


LIVE = user_systemd_reachable()
live = pytest.mark.skipif(not LIVE, reason="needs a reachable user systemd")


def test_a_reachable_user_systemd_is_told_apart_from_an_unreachable_one():
    def answers(rc, out, err=""):
        return lambda *a, **k: SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    assert user_systemd_reachable(answers(0, "running\n"))
    assert user_systemd_reachable(answers(1, "degraded\n")), "degraded exits 1 and is reachable"
    assert not user_systemd_reachable(answers(1, "", "Failed to connect to bus: No medium found\n"))
    assert not user_systemd_reachable(answers(1, "offline\n"))
    assert not user_systemd_reachable(answers(4, "unknown\n"))

    def no_systemctl(*a, **k):
        raise FileNotFoundError("systemctl")
    assert not user_systemd_reachable(no_systemctl)


SEEDED = {"crew-wall.service", "crissa-tunnel.service", "audio-asr.service", "keep-warm.timer",
          "crew-sweep.service", "crew-sweep.timer"}


@pytest.fixture
def scratch_units(tmp_path, monkeypatch):
    """A user and a system unit folder in tmp, seeded with each kind of file the discovery must handle;
    hr reads them instead of ~/.config/systemd/user and /etc/systemd/system."""
    user, system = tmp_path / "user-units", tmp_path / "system-units"
    user.mkdir()
    system.mkdir()
    svc = "[Service]\nExecStart={exe}\n{extra}"
    (user / "crew-wall.service").write_text(svc.format(exe="/usr/bin/true", extra=""))               # name rule
    (user / "crissa-tunnel.service").write_text(svc.format(exe="/usr/bin/cloudflared", extra=""))    # folder rule
    (user / "audio-asr.service").write_text(svc.format(exe="/usr/bin/true", extra=""))
    (user / "live-ear.service").symlink_to(user / "audio-asr.service")                               # an alias
    (user / "hermes-crissa-gateway.service").symlink_to("/dev/null")                                   # masked
    (user / "crew-job@.service").write_text(svc.format(exe="/usr/bin/true", extra=""))               # a template
    (user / "keep-warm.timer").write_text("[Timer]\nOnCalendar=daily\n")
    (system / "crew-sweep.service").write_text(svc.format(exe="/usr/bin/true", extra=f"User={hr.KREW_USER}\n"))
    (system / "crew-sweep.timer").write_text("[Timer]\nOnCalendar=daily\n")
    (system / "sshd.service").write_text(svc.format(exe="/usr/sbin/sshd", extra=""))                 # the computer
    monkeypatch.setattr(hr, "USER_UNIT_DIR", user)
    monkeypatch.setattr(hr, "SYSTEM_UNIT_DIR", system)
    return user, system


def folder_systemd(user, system):
    """list-unit-files + show over exactly the files in two folders, the way systemd answers them: a link to
    /dev/null is masked, a link to another file in the folder is an alias (its Id is the file it points at)."""
    units = {}
    for scope, d in (("user", user), ("system", system)):
        for p in sorted(d.iterdir()):
            if not p.name.endswith(hr.KINDS) or "@." in p.name:
                continue
            if p.is_symlink() and os.readlink(p) == "/dev/null":
                units[(scope, p.name)] = U(LoadState="masked", ActiveState="inactive", UnitFileState="masked",
                                           FragmentPath="")
                continue
            target = Path(os.path.realpath(p))
            text = target.read_text()
            props = U(Id=target.name, FragmentPath=str(target), UnitFileState="alias" if p.is_symlink() else "enabled")
            exe = re.search(r"^ExecStart=(\S+)", text, re.M)
            props["ExecStart"] = f"{{ path={exe.group(1)} ; }}" if exe else ""
            who = re.search(r"^User=(\S+)", text, re.M)
            if who:
                props["User"] = who.group(1)
            units[(scope, p.name)] = props
    return FakeSystemd(units)


@pytest.fixture
def live_rows(scratch_units):
    return hr.discover()


def test_every_installed_crew_unit_file_is_discovered(scratch_units):
    """Independent of the rules' code: every unit file installed in the user folder (and crew-* in the system
    folder) must be in the registry's discovery. Fails the day a rule misses one. Scratch folders, never the
    machine's own."""
    have = {r["unit"] for r in hr.discover(run=folder_systemd(*scratch_units), notes={"notes": {}})}
    want = set()
    for p in hr.USER_UNIT_DIR.iterdir():
        if p.name.endswith(hr.KINDS) and "@." not in p.name:
            if p.is_symlink() and os.readlink(p) == "/dev/null":
                continue
            if p.is_symlink() and Path(os.path.realpath(p)).parent == hr.USER_UNIT_DIR:
                continue        # an alias of another file here (live-ear -> audio8-asr)
            want.add(p.name)
    for p in hr.SYSTEM_UNIT_DIR.glob("crew-*"):
        if p.name.endswith(hr.KINDS) and not p.is_symlink():
            want.add(p.name)
    missing = sorted(want - have)
    assert not missing, f"crew unit files the registry does not discover: {missing}"
    assert want == SEEDED, f"the scratch folders read as {sorted(want)}"




@live
def test_no_crew_unit_is_missing_from_the_registry_file(live_rows):
    """THE test the owner asked for: a crew unit on this machine that the registry file does not list fails here.
    (The janitor's cheap check adds it within 5 minutes; the M&S night sweeps the rest.)"""
    reg = hr.load()
    if not reg.get("units"):
        pytest.skip(f"no registry written yet at {hr.REGISTRY} (first janitor tick after the apply writes it)")
    missing = hr.missing(live_rows, reg)
    assert not missing, f"crew units missing from {hr.REGISTRY}: {missing}"




