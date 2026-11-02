"""The Decide sheet's privacy watch ships as a unit (parity sweep 2026-10-26, gap 4): krew had the sheet and its
`watch` verb, and nothing ran it."""
from __future__ import annotations

import json
import subprocess
import sys

from conftest import ROOT

UNIT = ROOT / "crew" / "units" / "krew-decide-sheet-watch.service"


def test_the_unit_runs_the_sheets_watch_through_the_krew_runner():
    text = UNIT.read_text()
    exec_line = next(ln for ln in text.splitlines() if ln.startswith("ExecStart="))
    assert exec_line.endswith("%h/.krew/crew/scripts/krew-crew-run krew-decide-sheet-watch.py")
    assert "Restart=always" in text and "WantedBy=default.target" in text
    assert "# krew: opt-in" not in text, "the privacy watch runs on a straight clone's --enable all"
    assert (ROOT / "plugins" / "team-crissa" / "crissa-decide-list.py").is_file()
    assert "def watch(" in (ROOT / "plugins" / "team-crissa" / "crissa-decide-list.py").read_text()


def test_its_notes_row_names_the_runners_off_switch():
    notes = json.loads((ROOT / "config" / "unit-notes.example.json").read_text())
    row = notes["notes"]["krew-decide-sheet-watch.service"]
    assert row["expect"] == "running" and row["off_file"] == "~/.krew/krew-decide-sheet-watch.off"
    assert "krew-decide-sheet-watch.off" in UNIT.read_text()


def test_crew_install_writes_it_and_lists_it_with_the_units_a_clone_runs(tmp_path):
    sd = tmp_path / "units"
    r = subprocess.run([sys.executable, str(ROOT / "crew" / "scripts" / "krew-crew-install.py"), "--hermes-home",
                        str(tmp_path / "h"), "--krew-home", str(tmp_path / "k"), "--units", "--systemd-dir", str(sd)],
                       capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert (sd / "krew-decide-sheet-watch.service").is_file()
    starts = next(ln for ln in r.stdout.splitlines() if ln.startswith("Units a straight clone runs"))
    assert "krew-decide-sheet-watch.service" in starts


def test_the_wrapper_execs_the_plugins_watch_from_the_hermes_home(tmp_path):
    wrap = ROOT / "crew" / "scripts" / "krew-decide-sheet-watch.py"
    env = {"PATH": "/usr/bin:/bin", "KREW_HERMES": str(tmp_path / "h")}
    r = subprocess.run([sys.executable, str(wrap)], capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0 and "no team-crissa plugin" in r.stdout, "no plugin: nothing to guard, exit 0"
    plug = tmp_path / "h" / "plugins" / "team-crissa"
    plug.mkdir(parents=True)
    (plug / "crissa-decide-list.py").write_text("import sys; print('ran', sys.argv[1:])\n")
    r = subprocess.run([sys.executable, str(wrap)], capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == "ran ['watch']"
