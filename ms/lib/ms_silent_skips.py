""























from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


ENABLED = os.environ.get("MS_SILENT_SKIPS", "1") != "0"
NIGHTS_TO_FILE = 3
BATTERY_MIN_MS = 1000
SCHEMA = "ms_silent_skips/v1"


@dataclass
class Obs:
    ""

    check: str
    env: str
    ran: bool
    numbers: str = ""
    source: str = ""


@dataclass
class Change:
    key: str
    action: str
    row: dict = field(default_factory=dict)




_CANARY_NUM = re.compile(r"free\s+(\d+)\s*MiB\s*<\s*(\d+)")


def observe_ms_report(report: dict, source: str = "") -> list[Obs]:
    ""
    out: list[Obs] = []
    for env, a in sorted((report.get("applied") or {}).items()):
        c = (a or {}).get("canary") if isinstance(a, dict) else None
        if not c:
            continue
        s = str(c)
        skipped = s.upper().startswith(("UNVERIFIED", "N/A", "SKIP")) or s.strip().lower() in ("n/a", "no canary configured")
        m = _CANARY_NUM.search(s)
        nums = (f"free {m.group(1)} MiB < needed {m.group(2)} MiB" if m else s[:160]) if skipped else ""
        out.append(Obs("canary", env, not skipped, nums, source))
    for env, e in sorted((report.get("envs") or {}).items()):
        if not isinstance(e, dict):
            continue
        sb = e.get("smoke_baseline", "absent")
        if sb in (True, False):
            out.append(Obs("smoke", env, True, "", source))
        elif sb != "absent":
            out.append(Obs("smoke", env, False, f"smoke_baseline {sb!r}", source))
        if "measured" in e:
            if e.get("measured") is True and not e.get("outdated_unknown"):
                out.append(Obs("survey", env, True, "", source))
            else:
                out.append(Obs("survey", env, False,
                               f"measured {e.get('measured')!r}, outdated_unknown {e.get('outdated_unknown')!r}",
                               source))
        r = e.get("roots")
        if isinstance(r, dict) and report.get("roots"):
            if r.get("error"):
                out.append(Obs("roots", env, False, str(r["error"])[:200], source))
            else:
                out.append(Obs("roots", env, True, "", source))
    return out


_PYTEST_COUNT = re.compile(r"\b(\d+) (passed|failed|error|errors)\b")
_ZERO_TESTS = re.compile(r"\bno tests ran\b|\bcollected 0 items\b")


def observe_battery(battery: dict, source: str = "") -> list[Obs]:
    ""
    out: list[Obs] = []
    for r in battery.get("results") or []:
        if not isinstance(r, dict) or r.get("group") != "pytest":
            continue
        detail = str(r.get("detail") or "")
        ms = r.get("ms")
        ran_n = sum(int(n) for n, _ in _PYTEST_COUNT.findall(detail)) + len(r.get("failed") or [])
        status = str(r.get("status") or "").upper()
        why = ""
        if status in ("SKIP", "N/A", "UNVERIFIED"):
            why = f"status {status}"
        elif _ZERO_TESTS.search(detail):
            why = "0 tests ran"
        elif ms is not None and ms < BATTERY_MIN_MS and ran_n == 0:
            why = f"finished in {ms} ms, 0 tests ran"
        if why:
            why += f" ({detail[:160]})"
        out.append(Obs("battery", str(r.get("name") or "?"), not why, why, source))
    return out


def battery_night(battery: dict) -> str:
    ""
    ts = str(battery.get("ts") or "")
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return t.astimezone().date().isoformat()
    except ValueError:
        return ts[:10]




def _load(p: Path, empty: dict) -> dict:
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) else dict(empty)
    except (OSError, ValueError):
        return dict(empty)


def _atomic(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    os.replace(tmp, p)


@contextlib.contextmanager
def _lock(findings_path: Path):
    lp = findings_path.with_name(findings_path.name + ".lock")
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def finding_key(check: str, env: str) -> str:
    return f"silent-skip:{check}:{env}"


def _body(check: str, env: str, skips: list[dict], gaps: list[str]) -> str:
    lines = [f"The {check} check has not really run for {env} on {len(skips)} nights in a row: it "
             "answered skip / n/a / unverified / zero tests, which reads like a pass."]
    for s in skips:
        lines.append(f"- {s['night']}: {s['numbers'] or 'skipped'}" + (f" [{s['source']}]" if s.get("source") else ""))
    if gaps:
        lines.append(f"- no record (not evidence either way): {', '.join(gaps)}")
    lines.append("Closes by itself the first night the check really runs.")
    return "\n".join(lines)


def apply(night: str, observations: list[Obs], *, findings_path: Path,
          state_path: Path | None = None, need: int = NIGHTS_TO_FILE) -> list[Change]:
    ""

    state_path = state_path or findings_path.with_name("silent-skips.json")
    changes: list[Change] = []
    with _lock(findings_path):
        state = _load(state_path, {"schema": SCHEMA, "streaks": {}})
        streaks = state.setdefault("streaks", {})
        store = _load(findings_path, {"schema": "ms_findings/v1", "findings": []})
        rows = [x for x in store.get("findings") or [] if isinstance(x, dict)]
        by_key = {x.get("key"): x for x in rows if x.get("key") and x.get("status") == "open"}
        for o in observations:
            key = finding_key(o.check, o.env)
            st = streaks.setdefault(key, {"skips": [], "last_seen": ""})
            if st.get("last_seen") and st["last_seen"] >= night:
                continue
            st["last_seen"] = night
            if o.ran:
                st["skips"] = []
                row = by_key.pop(key, None)
                if row is not None:
                    row.update(status="closed", closed_on=night,
                               closed_by=f"{o.check} really ran for {o.env} on {night}",
                               ts=dt.datetime.now().isoformat(timespec="seconds"))
                    changes.append(Change(key, "closed", row))
                continue

            st["skips"].append({"night": night, "numbers": o.numbers, "source": o.source})
            skips = st["skips"]
            if len(skips) < need:
                continue
            gaps = _gaps(skips)
            row = by_key.get(key)
            fields = {
                "title": f"silent skip: {o.check} for {o.env}, {len(skips)} nights in a row",
                "finding": _body(o.check, o.env, skips, gaps),
                "source": "ms_silent_skips",
                "evidence": "; ".join(sorted({s["source"] for s in skips if s.get("source")}))[:1500],
                "check": o.check, "env": o.env,
                "nights": [s["night"] for s in skips], "no_record": gaps,
                "numbers": {s["night"]: s["numbers"] for s in skips},
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
            }
            if row is None:
                row = {"key": key, "status": "open", "opened_on": night, **fields}
                rows.append(row)
                by_key[key] = row
                changes.append(Change(key, "opened", row))
            else:
                row.update(fields)
                changes.append(Change(key, "updated", row))
        if changes:
            store["findings"] = rows
            store["schema"] = "ms_findings/v1"
            _atomic(findings_path, store)
        _atomic(state_path, state)
    return changes


def _gaps(skips: list[dict]) -> list[str]:
    ""
    out = []
    for prev, cur in zip(skips, skips[1:]):
        try:
            d = dt.date.fromisoformat(prev["night"]) + dt.timedelta(days=1)
            end = dt.date.fromisoformat(cur["night"])
        except ValueError:
            continue
        while d < end:
            out.append(d.isoformat())
            d += dt.timedelta(days=1)
    return out




def default_findings_path() -> Path:
    ""
    env = os.environ.get("MS_FINDINGS")
    if env:
        return Path(env)
    import ms_settings
    return ms_settings.state_dir() / "findings.json"


def record_ms_night(report: dict, day: str, *, source: str = "",
                    findings_path: Path | None = None) -> list[Change]:
    ""
    if not ENABLED:
        return []
    return apply(day, observe_ms_report(report, source=source),
                 findings_path=findings_path or default_findings_path())


def record_battery_night(battery: dict, *, source: str = "",
                         findings_path: Path | None = None) -> list[Change]:
    ""
    if not ENABLED:
        return []
    return apply(battery_night(battery), observe_battery(battery, source=source),
                 findings_path=findings_path or default_findings_path())


def summary(changes: list[Change]) -> list[dict]:
    return [{"key": c.key, "action": c.action, "nights": len(c.row.get("nights") or [])} for c in changes]
