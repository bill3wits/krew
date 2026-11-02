"""The User's off-hours: the one global window the house does its own work in.

Init collects it (step "Overnight self-training", `lila-ob-overnight`) and writes
`site.json` → `training_window` as "HH:MM-HH:MM", local time; its default when
the User takes the offer is 18:00-02:00. the User 2026-09-17: "Do we have a global
value? We collect one in the init routine. Let's design with future user in
mind … change mine in global then pull that from there into here."

Every consumer reads it here — the restart window first; kanban heavy work, the
M&S apply and training can follow — so a new User's Init answer moves them all.
Nothing in this module guesses: with no value written, `off_hours()` says
`source: "unset"` and a consumer stays idle and says so. `manners.quiet_hours`
is a different answer (when the house keeps quiet), never a fallback here.

Quiet hours (when the house does not speak up) go the other way (the User 2026-09-17:
"Should be keyed to global value … down hours stored in Init routine"): the User's
own `manners.quiet_hours` answer if Init has one, else these down hours, else the
old hard-coded 22:00-08:00 and `source: "legacy"` so a report can say so. That old
value muted "needs a human" for the first six hours of a day that starts at 02:00.

`set_off_hours()` is the one writer besides Init itself (Control Panel, Crissa),
through Init's own site writer.

krew: the global is `off_hours: "HH:MM-HH:MM"` in $KREW_HOME/krew.yaml (KREW_CONFIG overrides
the path; window/lib/krew_window_settings.py reads and writes it) in place of Init's
site.json `training_window`, and a top-level `quiet_hours:` stands beside `manners`. Same
function names, so every lifted consumer reads it unchanged.

ONE ANSWER, ASKED IN CRISSA'S SETUP (the owner 2026-10-15, round 22): with no `off_hours` in krew.yaml, the window is
the one Crissa's Setup saved ("Your hours: when may Crissa restart, render and work on her own?"), her site.json
`training_window` under her state home (CRISSA_IDENTITY, else $CRISSA_HOME or ~/.crissa, then identity/site.json);
`source: "init"`, `from: "crissa"`. krew.yaml's own value still wins. Kill KREW_OFF_HOURS_FROM_CRISSA=0.

QUIET HOURS DEFAULT TO NEVER (the owner 2026-10-15: "Quiet hours I vote delete ... why would anyone need more than a
volume button"): with no quiet answer (`manners.quiet_hours` or a top-level `quiet_hours`), quiet_hours() is
`window: None, source: "none"` and nothing is ever quiet - the off-hours are no longer a fallback, and the old
22:00-08:00 is gone with it. Kill KREW_QUIET_DEFAULT_NEVER=0 (the old chain: off_hours, then 22:00-08:00).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

import krew_window_settings as _kws   # krew: krew.yaml is read and written there (the house: Init's site.json)

FIELD = "off_hours"
_RANGE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse(window: str) -> tuple[int, int] | None:
    """Minutes after midnight (start, end), or None if not HH:MM-HH:MM."""
    m = _RANGE.match(str(window or ""))
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if not (0 <= h1 < 24 and 0 <= h2 < 24 and 0 <= m1 < 60 and 0 <= m2 < 60):
        return None
    return h1 * 60 + m1, h2 * 60 + m2


def _site(site: dict | None) -> dict:
    if site is not None:
        return site
    try:
        return _kws.krew_yaml_top(_kws.krew_yaml_path().read_text(encoding="utf-8"), (FIELD, "quiet_hours"))
    except OSError:
        return {}


def _on(var: str) -> bool:
    return os.environ.get(var, "1").strip().lower() not in ("0", "off", "false", "no")


def crissa_site_path() -> Path:
    """Crissa's site.json, where her Setup writes `training_window` (lib/crissa_init.site_path in the Crissa player)."""
    got = (os.environ.get("CRISSA_IDENTITY") or "").strip()
    if got:
        return Path(got).expanduser() / "site.json"
    return Path(os.environ.get("CRISSA_HOME") or "~/.crissa").expanduser() / "identity" / "site.json"


def crissa_window() -> str | None:
    """The window Crissa's Setup saved, or None (no Crissa, no answer, or not HH:MM-HH:MM)."""
    if not _on("KREW_OFF_HOURS_FROM_CRISSA"):
        return None
    try:
        raw = json.loads(crissa_site_path().read_text(encoding="utf-8")).get("training_window")
    except (OSError, ValueError, AttributeError):
        return None
    return str(raw).strip() if raw not in (None, "") and parse(str(raw)) else None


def off_hours(site: dict | None = None) -> dict[str, Any]:
    """{"window", "start", "end", "source": "init"|"unset"|"invalid"[, "from": "crissa"]}: Crissa's window reads as "init" (every consumer asks source == "init" for a usable window) and says where it came from."""
    raw = _site(site).get(FIELD)
    if raw in (None, "") and site is None:
        got = crissa_window()
        if got:
            rng = parse(got)
            return {"window": got, "start": rng[0], "end": rng[1], "source": "init", "from": "crissa", "why": ""}
    if raw in (None, ""):
        return {"window": None, "start": None, "end": None, "source": "unset",
                "why": f"off_hours is not set in {_kws.krew_yaml_path()} (\"HH:MM-HH:MM\", local time)"}
    rng = parse(str(raw))
    if rng is None:
        return {"window": str(raw), "start": None, "end": None, "source": "invalid",
                "why": f"{_kws.krew_yaml_path()} {FIELD} is not HH:MM-HH:MM: {raw!r}"}
    return {"window": str(raw).strip(), "start": rng[0], "end": rng[1], "source": "init", "why": ""}


LEGACY_QUIET = "22:00-08:00"


def quiet_hours(site: dict | None = None) -> dict[str, Any]:
    """{"window", "start", "end", "source": "manners"|"quiet_hours"|"none"} - "none" (window None) = never quiet
    (krew: a top-level `quiet_hours` stands beside `manners`). Under KREW_QUIET_DEFAULT_NEVER=0 the old chain:
    then the `off_hours` key ("off_hours"), then 22:00-08:00 ("legacy")."""
    st = _site(site)
    man = st.get("manners") if isinstance(st.get("manners"), dict) else {}
    old = not _on("KREW_QUIET_DEFAULT_NEVER")
    for raw, source in ((man.get("quiet_hours"), "manners"), (st.get("quiet_hours"), "quiet_hours"),
                        (st.get(FIELD) if old else None, "off_hours")):
        rng = parse(str(raw)) if raw not in (None, "") else None
        if rng is not None:
            return {"window": str(raw).strip(), "start": rng[0], "end": rng[1], "source": source}
    if not old:
        return {"window": None, "start": None, "end": None, "source": "none"}
    rng = parse(LEGACY_QUIET)
    return {"window": LEGACY_QUIET, "start": rng[0], "end": rng[1], "source": "legacy"}


def is_quiet(now: dt.datetime | None = None, site: dict | None = None) -> bool:
    return contains(now or dt.datetime.now(), quiet_hours(site)["window"])


def contains(now: dt.datetime, window: str) -> bool:
    rng = parse(window)
    if rng is None:
        return False
    start, end = rng
    m = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= m < end
    return m >= start or m < end


def started_on(now: dt.datetime, window: str) -> str:
    """ISO date of the window a job running NOW belongs to.

    Inside the window: the date it opened (past midnight → yesterday). OUTSIDE the
    window it is simply today's date — the window-scoped state files (ticker,
    restart window, render window) use it as "tonight's key" and compare it only
    while the window is open. For "the most recent night", after close (a 02:25
    catch-up, a morning report), use last_opened() (a crew session, 2026-09-17).
    """
    rng = parse(window)
    day = now.date()
    if rng and rng[0] > rng[1] and (now.hour * 60 + now.minute) < rng[1]:
        day = day - dt.timedelta(days=1)
    return day.isoformat()


def last_opened(now: dt.datetime, window: str) -> str:
    """ISO date the most recent window OPENED on, open or closed: before today's start
    time that is yesterday (02:25 after a 20:00-02:00 night → last night's date)."""
    rng = parse(window)
    if rng is None:
        return now.date().isoformat()
    m = now.hour * 60 + now.minute
    return (now.date() if m >= rng[0] else now.date() - dt.timedelta(days=1)).isoformat()


def set_off_hours(window: str, *, by: str = "user") -> dict:
    """Write the global (krew: the off_hours line in krew.yaml). Raises ValueError on a bad range."""
    if parse(window) is None:
        raise ValueError(f"off-hours must be HH:MM-HH:MM, got {window!r}")

    def mut(site: dict[str, Any]) -> None:
        site[FIELD] = window.strip()
        site[f"{FIELD}_set_by"] = by

    _kws.krew_yaml_set(mut, (FIELD, f"{FIELD}_set_by", "quiet_hours"))
    return off_hours()
