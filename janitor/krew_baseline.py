"""krew-baseline: did the house come back to where it started? (the User 09-30)

Every leak that bit this house was the same shape - something temporary ended and left a residue nobody owned:
31 of 64 PipeWire slots held by orphaned `pactl subscribe`, a GPU hold whose card no board knew, a Render flag
only one exit path wrote home, a demo unit stopped but left enabled (it read a story at the next boot), and
09-30's 24 GB of the voice stack in swap after one cold engine start. A cleanup step runs only when the thing
closes the way its author imagined, and a side effect on a NEIGHBOUR is nobody's cleanup at all. So this does
not clean anything. It measures the house before and after anything temporary and says what did not return.

    snapshot(label)            one reading of every probe -> <state>/<label>.json
    diff(before, after)        the items that did not come back, and the probes that could not be read
    start(label) / end(label)  the pair a lane swap, a Render, a canary or a test run calls (hooks)
    record(label, result)      open / update / close `not-returned:<label>:<item>` rows in findings.json,
                               the way lib/ms_silent_skips does (same file, same lock, atomic)

REPORT ONLY. Nothing here stops, frees, kills or restarts anything. A probe that cannot be read is UNKNOWN,
listed, and never reads as "came back". Armed by ONE file, `<home>/krew-baseline.json` (<home>: $KREW_STATE_HOME, else the root of
$HERMES_HOME, else ~/.hermes)
(`{"hooks": true, "findings": true}`; missing or unreadable = off), because the call sites run in dozens of
units and shells. An env var overrides the file for its own process:
    KREW_BASELINE_HOOKS=1|0      start()/end() do something when a call site invokes them
    KREW_BASELINE_FINDINGS=1|0   end() / `diff --record` write findings.json (else the report file only)
Kill: KREW_BASELINE=0 (the CLI still reads, the hooks and the findings writer do nothing).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

SCHEMA = "krew_baseline/v1"


def state_home() -> Path:
    """$KREW_STATE_HOME, else $HERMES_HOME (a profile's `<root>/profiles/<name>` reads <root>), else ~/.hermes."""
    v = os.environ.get("KREW_STATE_HOME")
    if v:
        return Path(os.path.expanduser(v))
    h = os.environ.get("HERMES_HOME")
    if h:
        p = Path(os.path.expanduser(h))
        return p.parent.parent if p.parent.name == "profiles" else p
    return Path.home() / ".hermes"


HOME = state_home()
STATE = Path(os.environ.get("KREW_BASELINE_DIR") or HOME / "krew-baseline")


ARM_FILE = Path(os.environ.get("KREW_BASELINE_ARM_FILE") or HOME / "krew-baseline.json")


def _on(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "off", "false", "no", "")


def _armed(key: str, env: str) -> bool:
    """An env var decides when it is set (1 = on, 0 = off for that process); otherwise the arm file does.
    The call sites run in forty units, a nightly and every shell - one file arms them all, no drop-ins."""
    if os.environ.get(env, "").strip():
        return _on(env, "0")
    try:
        return bool(json.loads(ARM_FILE.read_text()).get(key))
    except (OSError, ValueError, AttributeError):
        return False     # missing or unreadable = off: report-only tooling never arms itself by accident


def enabled() -> bool:
    return _on("KREW_BASELINE", "1")


def hooks_on() -> bool:
    return enabled() and _armed("hooks", "KREW_BASELINE_HOOKS")


def findings_on() -> bool:
    return enabled() and _armed("findings", "KREW_BASELINE_FINDINGS")


# What counts as "did not come back". One place, in the units each probe reports.
TOLERANCE = {
    "vram_used_mib": 512,       # a lane's CUDA graphs and caches breathe a few hundred MiB
    "swap_used_mib": 256,
    "unit_swap_mib": 128,       # per unit, so the finding names WHO was pushed out
    "audio_clients": 3,         # a browser tab or a one-line speaker comes and goes
    "headroom_mib": 4096,       # available RAM minus what sits in swap; see diff()
}

Runner = Callable[..., subprocess.CompletedProcess]
EPHEMERAL_FROM = 32768


def _run(cmd: list[str], timeout: float = 8.0, run: Runner = subprocess.run) -> str:
    r = run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {r.returncode}: {(r.stderr or '').strip()[:120]}")
    return r.stdout


def _unit_of(pid: int | str) -> str:
    try:
        last = Path(f"/proc/{pid}/cgroup").read_text().strip().splitlines()[-1]
        return last.rsplit("/", 1)[-1] or "?"
    except OSError:
        return "?"


# ---------------------------------------------------------------- probes (each reads, none acts)
def probe_vram(run: Runner = subprocess.run) -> dict:
    used, total = [int(x) for x in _run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                                         "--format=csv,noheader,nounits"], run=run).splitlines()[0].split(",")]
    by_unit: dict[str, int] = {}
    for line in _run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                      "--format=csv,noheader,nounits"], run=run).splitlines():
        pid, mib = [x.strip() for x in line.split(",")][:2]
        unit = _unit_of(pid)
        by_unit[unit] = by_unit.get(unit, 0) + int(mib)
    return {"used_mib": used, "total_mib": total, "by_unit": by_unit}


def probe_memory(proc: Path = Path("/proc")) -> dict:
    mi = {}
    for line in (proc / "meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        mi[k] = int(v.split()[0]) // 1024
    by_unit: dict[str, int] = {}
    for st in proc.glob("[0-9]*/status"):
        try:
            m = re.search(r"^VmSwap:\s+(\d+)", st.read_text(), re.M)
        except OSError:
            continue
        if m and int(m.group(1)) > 0:
            unit = _unit_of(st.parent.name)
            by_unit[unit] = by_unit.get(unit, 0) + int(m.group(1)) // 1024
    return {"mem_available_mib": mi.get("MemAvailable", 0),
            "swap_used_mib": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0),
            "swap_cached_mib": mi.get("SwapCached", 0),
            "swap_by_unit": {k: v for k, v in by_unit.items() if v >= 16}}


def probe_audio(run: Runner = subprocess.run) -> dict:
    clients = [l.split("\t") for l in _run(["pactl", "list", "short", "clients"], timeout=4, run=run).splitlines() if l]
    names: dict[str, int] = {}
    for c in clients:
        n = c[2] if len(c) > 2 else "?"
        names[n] = names.get(n, 0) + 1
    inputs = [l for l in _run(["pactl", "list", "short", "sink-inputs"], timeout=4, run=run).splitlines() if l]
    subs = [l for l in _run(["ps", "-eo", "args="], run=run).splitlines() if l.strip().startswith("pactl subscribe")]
    return {"clients": len(clients), "clients_by_name": names, "sink_inputs": len(inputs),
            "pactl_subscribe": len(subs)}


def probe_holds() -> dict:
    hold = Path(os.environ.get("KREW_TRAIN_HOLD", "/tmp/krew-train-hold"))
    lines = [l for l in hold.read_text().splitlines() if l.strip()] if hold.is_file() else []
    def first(p: Path) -> str:
        try:
            return p.read_text().split("\n", 1)[0].strip()
        except OSError:
            return ""
    return {"hold_lines": lines, "hold_file": hold.exists(),
            "gpu_mode": first(HOME / "crew-gpu-mode"), "glass_mode": first(HOME / "crew-glass-mode"),
            "board": first(HOME / "kanban" / "current"),
            "off_switches": sorted(p.name for p in HOME.glob("*.off"))}


def probe_units(run: Runner = subprocess.run) -> dict:
    def names(args: list[str]) -> list[str]:
        out = _run(["systemctl", "--user", *args, "--no-legend", "--plain", "--no-pager"], run=run)
        return sorted({l.split()[0] for l in out.splitlines() if l.strip()})
    return {"enabled": names(["list-unit-files", "--state=enabled"]),
            "active": names(["list-units", "--type=service", "--state=active"]),
            "failed": names(["list-units", "--state=failed"]),
            "scopes": len(names(["list-units", "--type=scope", "--state=active"]))}


def probe_ports(run: Runner = subprocess.run) -> dict:
    ports = set()
    for line in _run(["ss", "-ltnH"], run=run).splitlines():
        f = line.split()
        port = f[3].rsplit(":", 1)[-1] if len(f) >= 4 else ""
        if port.isdigit() and int(port) < EPHEMERAL_FROM:   # a kernel-picked port is nobody's residue
            ports.add(port)
    return {"listening": sorted(ports, key=int)}


PROBES: dict[str, Callable[[], dict]] = {
    "vram": probe_vram, "memory": probe_memory, "audio": probe_audio,
    "holds": probe_holds, "units": probe_units, "ports": probe_ports,
}


# A lane is being swapped, a first start is running, a Render is on: the house is MEANT to look different.
SWAP_UNIT = re.compile(r"^(ops-lane-|ops-coldboot-|ms-first-start-)")
SWAP_LABEL = re.compile(r"^(lane-swap|first-start|cold-boot|render|heavy-render)")
IN_FLIGHT_MAX_S = 6 * 3600     # a start whose end never came stops excusing anything after this


def in_flight(own: str = "", state: Path | None = None, now: float | None = None) -> list[str]:
    """Labels of swap-like runs that have started and not ended (a fresh <label>.before.json with no newer
    <label>.after.json), other than `own`."""
    d, now, out = state or STATE, now or time.time(), []
    for b in sorted(d.glob("*.before.json")) if d.is_dir() else []:
        label = b.name[: -len(".before.json")]
        if label == _safe(own) or not SWAP_LABEL.match(label):
            continue
        a = d / f"{label}.after.json"
        try:
            started = b.stat().st_mtime
            if now - started < IN_FLIGHT_MAX_S and (not a.is_file() or a.stat().st_mtime < started):
                out.append(label)
        except OSError:
            continue
    return out


def context_of(snap: dict, *, state: Path | None = None) -> dict:
    """Why a reading taken now is not the resting house: a GPU hold, a swap unit, a swap-like run in flight."""
    p = snap.get("probes") or {}
    why = []
    if (p.get("holds") or {}).get("hold_lines"):
        why.append("a GPU hold is active")
    units = [u for u in (p.get("units") or {}).get("active") or [] if SWAP_UNIT.match(u)]
    if units:
        why.append("a lane swap unit is running: " + ", ".join(units))
    try:
        flying = in_flight(snap.get("label") or "", state)
    except Exception:  # noqa: BLE001
        flying = []
    if flying:
        why.append("in flight: " + ", ".join(flying))
    return {"active": bool(why), "why": why}


def snapshot(label: str = "", probes: dict | None = None) -> dict:
    """One reading of every probe. A probe that raises is recorded as unknown, never dropped."""
    snap = {"schema": SCHEMA, "label": label, "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "probes": {}, "unknown": {}}
    for name, fn in (probes or PROBES).items():
        try:
            snap["probes"][name] = fn()
        except Exception as e:  # noqa: BLE001 - one dead probe must not blind the rest
            snap["unknown"][name] = f"{type(e).__name__}: {str(e)[:160]}"
    snap["context"] = context_of(snap)
    return snap


# ---------------------------------------------------------------- the comparison
def _set_delta(item: str, what: str, before: list, after: list, out: list) -> None:
    b, a = set(before or []), set(after or [])
    for x in sorted(a - b):
        out.append({"item": f"{item}:{x}", "what": f"{what} appeared: {x}", "before": None, "after": x})
    for x in sorted(b - a):
        out.append({"item": f"{item}:-{x}", "what": f"{what} is gone: {x}", "before": x, "after": None})


def diff(before: dict, after: dict, tolerance: dict | None = None) -> dict:
    """What did not come back. `not_returned` = measured differences beyond tolerance; `unknown` = probes that
    could not be read on either side (never counted as returned)."""
    tol = {**TOLERANCE, **(tolerance or {})}
    out: list[dict] = []
    bp, ap = before.get("probes") or {}, after.get("probes") or {}
    unknown = sorted(set(before.get("unknown") or {}) | set(after.get("unknown") or {})
                     | (set(PROBES) - set(bp)) | (set(PROBES) - set(ap)))
    unknown = [u for u in unknown if u not in bp or u not in ap]

    def both(name):
        return (bp[name], ap[name]) if name in bp and name in ap else (None, None)

    b, a = both("vram")
    if b is not None:
        d = a["used_mib"] - b["used_mib"]
        if d > tol["vram_used_mib"]:
            who = sorted(((a["by_unit"].get(u, 0) - b["by_unit"].get(u, 0), u) for u in a["by_unit"]), reverse=True)
            out.append({"item": "vram", "what": f"{d} MiB more VRAM in use" +
                        (f" (largest: {who[0][1]} +{who[0][0]} MiB)" if who and who[0][0] > 0 else ""),
                        "before": b["used_mib"], "after": a["used_mib"]})
    b, a = both("memory")
    if b is not None:
        d = a["swap_used_mib"] - b["swap_used_mib"]
        if d > tol["swap_used_mib"]:
            out.append({"item": "swap", "what": f"{d} MiB more in swap", "before": b["swap_used_mib"],
                        "after": a["swap_used_mib"]})
        for unit, mib in sorted(a["swap_by_unit"].items(), key=lambda kv: -kv[1]):
            grew = mib - b["swap_by_unit"].get(unit, 0)
            if grew > tol["unit_swap_mib"]:
                out.append({"item": f"swap:{unit}", "what": f"{unit} has {grew} MiB more of its memory in swap",
                            "before": b["swap_by_unit"].get(unit, 0), "after": mib})
        # Headroom = available RAM minus what is parked in swap. Available alone reads a swap CLEAR as a loss
        # (09-30: 25 GB came back from swap into RAM and the first diff called it "20 GB less available").
        hb_, ha_ = (b["mem_available_mib"] - b["swap_used_mib"]), (a["mem_available_mib"] - a["swap_used_mib"])
        if hb_ - ha_ > tol["headroom_mib"]:
            out.append({"item": "mem_headroom", "what": f"{hb_ - ha_} MiB less memory headroom "
                        "(available RAM minus swap in use)", "before": hb_, "after": ha_})
    b, a = both("audio")
    if b is not None:
        d = a["clients"] - b["clients"]
        if d > tol["audio_clients"]:
            grew = sorted(((n - b["clients_by_name"].get(k, 0), k) for k, n in a["clients_by_name"].items()), reverse=True)
            out.append({"item": "audio_clients", "what": f"{d} more PipeWire clients ({a['clients']} of 64)" +
                        (f", mostly {grew[0][1]}" if grew and grew[0][0] > 0 else ""),
                        "before": b["clients"], "after": a["clients"]})
        if a["pactl_subscribe"] > b["pactl_subscribe"]:
            out.append({"item": "pactl_subscribe", "what": f"{a['pactl_subscribe'] - b['pactl_subscribe']} more "
                        "`pactl subscribe` process(es) - each holds a client slot for good",
                        "before": b["pactl_subscribe"], "after": a["pactl_subscribe"]})
    b, a = both("holds")
    if b is not None:
        _set_delta("hold", "GPU hold line", b["hold_lines"], a["hold_lines"], out)
        if a.get("hold_file") and b.get("hold_file") is False:
            out.append({"item": "hold_file", "what": "the GPU hold file exists and did not before"
                        + ("" if a["hold_lines"] else " (it is empty: a leftover, not a hold)"),
                        "before": False, "after": True})
        _set_delta("off_switch", "off-switch file", b["off_switches"], a["off_switches"], out)
        for key, word in (("gpu_mode", "house GPU mode"), ("glass_mode", "glass mode"), ("board", "kanban board")):
            if b[key] != a[key]:
                out.append({"item": key, "what": f"{word} is {a[key]!r}, was {b[key]!r}", "before": b[key], "after": a[key]})
    b, a = both("units")
    if b is not None:
        _set_delta("enabled", "enabled unit", b["enabled"], a["enabled"], out)
        _set_delta("failed", "failed unit", b["failed"], [u for u in a["failed"]], out)
        for u in sorted(set(b["active"]) - set(a["active"])):
            out.append({"item": f"active:-{u}", "what": f"a service that was running is not: {u}", "before": u, "after": None})
        for u in sorted(set(a["active"]) - set(b["active"])):
            out.append({"item": f"active:{u}", "what": f"a service is running that was not: {u}", "before": None, "after": u})
    b, a = both("ports")
    if b is not None:
        fixed = lambda ports: [x for x in ports if str(x).isdigit() and int(x) < EPHEMERAL_FROM]  # noqa: E731
        _set_delta("port", "listening port", fixed(b["listening"]), fixed(a["listening"]), out)
    # a failed unit that is simply no longer failed is good news, not a finding
    out = [o for o in out if not o["item"].startswith("failed:-")]
    # The User 09-30: a reading taken while a hold, a lane swap or a Render was active compares against a house
    # that was MEANT to be different (two pytest "VRAM" findings opened and closed around the 0.30 swaps).
    # Those differences are reported as expected: they open no finding, and - not being evidence of a resting
    # house either - they close none.
    during = []
    for side, snap in (("before", before), ("after", after)):
        ctx = snap.get("context") or {}
        during += [f"{side}: {w}" for w in ctx.get("why") or []] if ctx.get("active") else []
    expected, out = (out, []) if during else ([], out)
    return {"schema": SCHEMA, "label": after.get("label") or before.get("label") or "",
            "before_ts": before.get("ts"), "after_ts": after.get("ts"),
            "not_returned": out, "expected": expected, "during": during, "unknown": unknown,
            "clean": not out and not unknown and not expected}


def render(result: dict) -> str:
    head = f"krew-baseline {result['label'] or '(no label)'}: {result['before_ts']} -> {result['after_ts']}"
    lines = [head]
    if result["clean"]:
        lines.append("  everything measured came back")
    for o in result["not_returned"]:
        lines.append(f"  did not come back: {o['what']}")
    for o in result.get("expected") or []:
        lines.append(f"  expected, not a finding: {o['what']}")
    if result.get("during"):
        lines.append("  measured while " + "; ".join(result["during"]))
    for u in result["unknown"]:
        lines.append(f"  UNKNOWN (not read on both sides): {u}")
    return "\n".join(lines)


# ---------------------------------------------------------------- files
def _atomic(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    os.replace(tmp, p)


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "run"


def save(snap: dict, name: str, state: Path | None = None) -> Path:
    p = (state or STATE) / f"{_safe(name)}.json"
    _atomic(p, snap)
    return p


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------- findings (the ms_silent_skips way)
def default_findings_path() -> Path:
    env = os.environ.get("KREW_BASELINE_FINDINGS_PATH") or os.environ.get("MS_FINDINGS")
    if env:
        return Path(env)
    try:
        import ms_settings          # the M&S night's own settings, when M&S is installed
        return ms_settings.state_dir() / "findings.json"
    except ImportError:
        kh = os.environ.get("KREW_HOME") or os.path.expanduser("~/.krew")
        return Path(os.path.expanduser(kh)) / "ms" / "findings.json"


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


def finding_key(label: str, item: str) -> str:
    return f"not-returned:{_safe(label)}:{item}"


def record(label: str, result: dict, *, findings_path: Path | None = None) -> list[dict]:
    """One OPEN row per (label, item) that did not come back; a repeat updates it, never duplicates it; an item
    that is back on a later run of the same label closes its row. An UNKNOWN probe closes nothing."""
    path = findings_path or default_findings_path()
    now = dt.datetime.now().isoformat(timespec="seconds")
    changes: list[dict] = []
    with _lock(path):
        try:
            store = json.loads(path.read_text())
            if not isinstance(store, dict):
                raise ValueError("findings.json is not an object")
        except FileNotFoundError:
            store = {"schema": "ms_findings/v1", "findings": []}
        rows = [x for x in store.get("findings") or [] if isinstance(x, dict)]
        prefix = f"not-returned:{_safe(label)}:"
        open_rows = {x["key"]: x for x in rows if str(x.get("key", "")).startswith(prefix) and x.get("status") == "open"}
        seen = set()
        for o in result["not_returned"]:
            key = finding_key(label, o["item"])
            seen.add(key)
            fields = {"title": f"did not come back after {label}: {o['what']}",
                      "finding": f"Measured before ({result['before_ts']}) and after ({result['after_ts']}) {label}: "
                                 f"{o['what']}. Before: {o['before']!r}; after: {o['after']!r}. Report only - "
                                 "nothing was cleaned up. Closes when a later run of the same label measures it back.",
                      "source": "krew_baseline", "evidence": json.dumps(o)[:1500], "label": label, "item": o["item"],
                      "ts": now}
            row = open_rows.get(key)
            if row is None:
                row = {"key": key, "status": "open", "opened_on": now[:10], "times": 1, **fields}
                rows.append(row)
                changes.append({"key": key, "action": "opened"})
            else:
                row.update(fields, times=int(row.get("times") or 1) + 1)
                changes.append({"key": key, "action": "updated"})
        unknown_probes = set(result["unknown"])
        for key, row in open_rows.items():
            probe = _probe_of(row.get("item") or "")
            if result.get("during"):
                continue        # a reading during a hold or a swap is not evidence that anything came back
            if key not in seen and probe not in unknown_probes:
                row.update(status="closed", closed_on=now[:10], closed_by=f"measured back after {label}", ts=now)
                changes.append({"key": key, "action": "closed"})
        if changes:
            store["findings"], store["schema"] = rows, store.get("schema") or "ms_findings/v1"
            _atomic(path, store)
    return changes


def _probe_of(item: str) -> str:
    head = item.split(":", 1)[0]
    return {"vram": "vram", "swap": "memory", "mem_headroom": "memory", "audio_clients": "audio",
            "pactl_subscribe": "audio", "hold": "holds", "hold_file": "holds", "off_switch": "holds", "gpu_mode": "holds",
            "glass_mode": "holds", "board": "holds", "enabled": "units", "failed": "units", "active": "units",
            "port": "ports"}.get(head, head)


# ---------------------------------------------------------------- the pair a call site uses
def start(label: str, *, state: Path | None = None) -> Path | None:
    """Snapshot before something temporary. Never raises; does nothing unless KREW_BASELINE_HOOKS=1."""
    if not hooks_on():
        return None
    try:
        return save(snapshot(label), f"{label}.before", state)
    except Exception:  # noqa: BLE001 - an instrument must never stop the thing it watches
        return None


def end(label: str, *, state: Path | None = None, settle_s: float = 0.0) -> dict | None:
    """Snapshot after, compare with start(label)'s, write <label>.report.json (+ findings when enabled)."""
    if not hooks_on():
        return None
    try:
        d = state or STATE
        before_p = d / f"{_safe(label)}.before.json"
        if not before_p.is_file():
            return None
        if settle_s:
            time.sleep(settle_s)
        after = snapshot(label)
        save(after, f"{label}.after", d)
        result = diff(load(before_p), after)
        if findings_on():
            result["findings"] = record(label, result)
        save(result, f"{label}.report", d)
        return result
    except Exception:  # noqa: BLE001
        return None


def end_later(label: str, delay_s: float) -> bool:
    """end(label) from a detached child after `delay_s`: the caller (krew-mode leaving Render) returns at once,
    the lanes it woke need minutes to be "back". Never raises; nothing unless KREW_BASELINE_HOOKS=1."""
    if not hooks_on():
        return False
    try:
        cli = Path(__file__).resolve().parents[1] / "scripts" / "krew-baseline.py"
        subprocess.Popen([os.environ.get("KREW_BASELINE_PYTHON") or "python3", str(cli), "end", label,
                          "--settle", str(delay_s)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except Exception:  # noqa: BLE001
        return False
