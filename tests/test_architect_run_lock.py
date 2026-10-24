"""research is the head of the window's improve chain: it must run or say it did not.

2026-09-17: a busy architect lock made research `exit 0` having done nothing; the window
ticker recorded ok and team-eval followed a completion that never happened.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "crew" / "scripts" / "steward-architect-run.sh").read_text()
CATCHUP = (ROOT / "crew" / "scripts" / "team-research-catchup.sh").read_text()


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def test_research_waits_for_the_lock_and_fails_loudly():
    code = _code(RUN)
    m = re.search(r'if \[ "\$JOB" = research \] \|\| \[ -n "\$\{WINDOW_RUN:-\}" \]; then\n(.*?)\nelif ! flock -n 9; then\n(.*?)\nfi', code, re.S)
    assert m, "research must have its own lock branch"
    research, others = m.group(1), m.group(2)
    assert "flock -w" in research and "exit 75" in research and "exit 0" not in research
    assert "exit 0" in others                      # wall-clock jobs keep the quiet skip


def test_scripts_parse():
    for name in ("steward-architect-run.sh", "team-research-catchup.sh"):
        assert subprocess.run(["bash", "-n", str(ROOT / "crew" / "scripts" / name)], check=False).returncode == 0


def test_catchup_label_is_the_day_the_window_opened(tmp_path):
    """At 02:25 the calendar is a day ahead of last night's chain. Run the script's own
    python with a fake clock and a fake krew_hours — the effect, not the source text."""
    block = CATCHUP[CATCHUP.index("<<PY"):].split("\n", 1)[1].split("\nPY\n", 1)[0]
    fake = tmp_path / "lib"
    fake.mkdir()
    (fake / "krew_hours.py").write_text(
        "def off_hours():\n    return {'source': 'init', 'window': '20:00-02:00', 'start': 1200, 'end': 120}\n")
    def label(hhmm: str) -> str:
        h, m = (int(x) for x in hhmm.split(":"))
        prog = ("import datetime as _d\n"
                f"class _DT(_d.datetime):\n    @classmethod\n    def now(cls, tz=None): return cls(2026, 9, 18, {h}, {m})\n"
                "_d.datetime = _DT\n" + block.replace('"$ROOT/lib"', repr(str(fake))))
        return subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True, check=True).stdout.strip()
    assert label("02:25") == "2026-09-17"          # after close: last night's label, not today
    assert label("01:25") == "2026-09-17"          # inside the window, past midnight
    assert label("20:05") == "2026-09-18"          # tonight's window has opened
    assert 'export KREW_DAY="$TODAY"' in _code(CATCHUP)
