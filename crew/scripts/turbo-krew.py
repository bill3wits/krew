#!/usr/bin/env python3
"""TurboKrew — timed Standard-only share of Crissa's :8000 with Vera + Steward.

Occupancy stays ``normal``. Ada stays on crew (her gateway loads config at
start; a pin swap would bounce her). Vera and Steward spawn per card, so
the next ``hermes -p`` picks this YAML with no restart.

Not an occupancy mode. The four hours are wall-clock from the click (no pause).
Leaving Standard (Render / Beast / All OS Off) parks Vera/Steward on crew
so they do not 404 a parked ops, and keeps the lease + timer. Back on
Standard with ops up, they pin to ops again if time remains. Explicit
off (panel / CLI / timer) ends it. Ada stays on crew.

Authority: docs/RENDER-OCCUPANCY.md (timed exception to stall law).
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

OPS_URL = "http://127.0.0.1:8000/v1"
CREW_URL = "http://127.0.0.1:8005/v1"
PROFILES = ("vera", "steward")
# Two engines, two shells each (the owner 2026-09-18 "the whole point is to double throughput"):
# Ada's caps go to the August box-sized 6/3 while the lease runs, 3/2 after. Same numbers as
# krew-mode ADA_DISPATCH["turbo"] / ["normal"] (a test pins them). Ada reads the cap at
# gateway start; kanban-board-gate `sync_ada_caps` bounces her once in a gap with 0 running.
TURBO_ADA_CAPS = (6, 3)
NORMAL_ADA_CAPS = (3, 2)
ADA = "ada"
TTL_DEFAULT_S = 14400  # four hours. Click Turbo on the CP to turn it off.
TIMER_UNIT = "turbo-krew-expire"
BACKUP_SUFFIX = ".pre-turbo-krew"
# House priority scheduler: lower number is served first (Crissa mouth = 0,
# Ada decomposer = 100 waits). Turbo workers sit in between so they share
# ops seats without jumping the mouth. Live ops is --max-num-seqs 8 on
# triton_attn (measured 2026-09-15); need ≥3 for Crissa+Vera+Steward. Do not
# bounce ops to change seqs — refuse if the live engine is still seqs=1.
TURBO_PRIORITY = 50
MIN_OPS_SEQS = 3


def _passwd_hermes() -> Path:
    return KH.hermes_home(passwd=True)


def krew_root(house: Optional[Path] = None) -> Path:
    if house is not None:
        return Path(house)
    return Path(os.environ.get("KREW_HERMES") or _passwd_hermes())


def mode_file(house: Optional[Path] = None) -> Path:
    h = krew_root(house)
    return Path(os.environ.get("KREW_GPU_MODE_FILE", str(h / "krew-gpu-mode")))


def state_file(house: Optional[Path] = None) -> Path:
    h = krew_root(house)
    return Path(os.environ.get("KREW_TURBO_KREW_STATE", str(h / "turbo-krew.json")))


def occupancy_mode(house: Optional[Path] = None) -> str:
    p = mode_file(house)
    if not p.is_file():
        return "normal"
    try:
        line = p.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except OSError:
        return "normal"
    return line or "normal"


def ops_serving() -> bool:
    env = os.environ.get("KREW_TURBO_KREW_OPS_OK")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "ok"}
    try:
        with urllib.request.urlopen(OPS_URL + "/models", timeout=1.5) as r:
            return int(getattr(r, "status", 200) or 200) == 200
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return False


def ops_max_num_seqs() -> Optional[int]:
    """Live ``--max-num-seqs`` on ops. Env override for tests. None = unknown."""
    raw = os.environ.get("KREW_TURBO_KREW_OPS_SEQS")
    if raw is not None and raw.strip() != "":
        try:
            return int(raw.strip())
        except ValueError:
            return None
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", os.environ.get("KREW_UNIT_OPS") or "ops.service", "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pid = int((r.stdout or "0").strip() or "0")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if pid <= 1:
        return None
    try:
        parts = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    args = [a.decode("utf-8", "replace") for a in parts if a]
    for i, a in enumerate(args):
        if a == "--max-num-seqs" and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                return None
        if a.startswith("--max-num-seqs="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return None
    return None


def ttl_s(override: Optional[int] = None) -> int:
    if override is not None:
        return max(1, int(override))
    raw = os.environ.get("KREW_TURBO_KREW_TTL_S")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return TTL_DEFAULT_S


def _refuse_live_under_pytest(house: Path) -> None:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    live = _passwd_hermes().resolve()
    try:
        root = house.resolve()
    except OSError:
        return
    if root == live:
        raise RuntimeError(
            "turbo krew write under pytest targeted live ~/.hermes. "
            "pass house= tmp (or KREW_HERMES)."
        )


def profile_config(name: str, house: Optional[Path] = None) -> Path:
    return krew_root(house) / "profiles" / name / "config.yaml"


def backup_path(cfg: Path) -> Path:
    return cfg.with_name(cfg.name + BACKUP_SUFFIX)


def set_top_model(
    text: str, *, default: str, provider: str, base_url: str
) -> tuple[str, bool]:
    """Rewrite only the first column-0 ``model:`` default/provider/base_url."""
    lines = text.splitlines(keepends=True)
    i, n = 0, len(lines)
    changed = False
    want = {"default": default, "provider": provider, "base_url": base_url}
    header = re.compile(r"^model:[ \t]*\n?$")
    field = re.compile(
        r"^([ \t]+)(default|provider|base_url):[ \t]*(\S+)(.*\n?)$"
    )
    while i < n:
        if not header.match(lines[i]):
            i += 1
            continue
        i += 1
        while i < n:
            raw = lines[i]
            if (
                raw.strip()
                and not raw.startswith((" ", "\t"))
                and not raw.lstrip().startswith("#")
            ):
                break
            m = field.match(raw)
            if m:
                newv = want[m.group(2)]
                repl = f"{m.group(1)}{m.group(2)}: {newv}{m.group(4)}"
                if repl != raw:
                    lines[i] = repl
                    changed = True
            i += 1
        break
    return "".join(lines), changed


def read_top_model(text: str) -> dict[str, str]:
    out = {"default": "", "provider": "", "base_url": ""}
    lines = text.splitlines()
    i, n = 0, len(lines)
    header = re.compile(r"^model:[ \t]*$")
    field = re.compile(r"^([ \t]+)(default|provider|base_url):[ \t]*(\S+)")
    while i < n:
        if not header.match(lines[i]):
            i += 1
            continue
        i += 1
        while i < n:
            raw = lines[i]
            if (
                raw.strip()
                and not raw.startswith((" ", "\t"))
                and not raw.lstrip().startswith("#")
            ):
                break
            m = field.match(raw)
            if m:
                out[m.group(2)] = m.group(3)
            i += 1
        break
    return out


def pin_kind(text: str) -> str:
    m = read_top_model(text)
    if (
        m.get("default") == "ops"
        or m.get("provider") == "custom:ops"
        or m.get("base_url") == OPS_URL
    ):
        return "ops"
    return "crew"


def ensure_named_ops_provider(text: str) -> tuple[str, bool]:
    """Add ``custom_providers`` ``name: ops`` if missing. Leave crew aux alone."""
    lines = text.splitlines(keepends=True)
    i, n = 0, len(lines)
    header_i: Optional[int] = None
    while i < n:
        if re.match(r"^custom_providers:[ \t]*\n?$", lines[i]):
            header_i = i
            break
        i += 1
    if header_i is None:
        return text, False
    i = header_i + 1
    item_indent = ""
    has_ops = False
    last_end = i
    while i < n:
        raw = lines[i]
        if (
            raw.strip()
            and not raw.startswith((" ", "\t", "-"))
            and not raw.lstrip().startswith("#")
        ):
            break
        m = re.match(r"^([ \t]*)-[ \t]*name:[ \t]*(\S+)", raw)
        if m:
            item_indent = m.group(1)
            if m.group(2).strip() == "ops":
                has_ops = True
        if raw.strip():
            last_end = i + 1
        i += 1
    if has_ops:
        return text, False
    ind = item_indent
    block = (
        f"{ind}- name: ops\n"
        f"{ind}  base_url: {OPS_URL}\n"
        f"{ind}  model: ops\n"
        f"{ind}  api_key: sk-local-noauth\n"
        f"{ind}  extra_body:\n"
        f"{ind}    chat_template_kwargs:\n"
        f"{ind}      enable_thinking: false\n"
        f"{ind}    priority: {TURBO_PRIORITY}\n"
    )
    lines.insert(last_end, block)
    return "".join(lines), True


def _ensure_priority_in_mapping(
    lines: list[str], start: int, end: int, priority: int
) -> bool:
    """Mutate lines[start:end] so extra_body.priority is set. Returns changed."""
    if start >= end:
        return False
    key_indent = None
    extra_i = None
    extra_indent = ""
    i = start
    while i < end:
        raw = lines[i]
        m = re.match(r"^([ \t]+)([A-Za-z0-9_]+):", raw)
        if m:
            ind, key = m.group(1), m.group(2)
            if key_indent is None or len(ind) == len(key_indent):
                if key_indent is None:
                    key_indent = ind
                if len(ind) == len(key_indent) and key == "extra_body":
                    extra_i = i
                    extra_indent = ind
        i += 1
    if key_indent is None:
        return False
    pri_re = re.compile(
        r"^(" + re.escape(extra_indent or key_indent) + r"  )priority:[ \t]*\S+(.*\n?)$"
    )
    if extra_i is not None:
        j = extra_i + 1
        child_indent = extra_indent + "  "
        found = None
        while j < end:
            raw = lines[j]
            m2 = re.match(r"^([ \t]+)", raw) if raw.strip() else None
            if raw.strip() and m2 and len(m2.group(1)) <= len(extra_indent):
                break
            m3 = re.match(
                r"^(" + re.escape(child_indent) + r")priority:[ \t]*(\S+)",
                raw,
            )
            if m3:
                found = j
                break
            j += 1
        if found is not None:
            nl = "\n" if lines[found].endswith("\n") else ""
            want = f"{child_indent}priority: {priority}{nl}"
            if lines[found] != want:
                lines[found] = want
                return True
            return False
        lines.insert(extra_i + 1, f"{child_indent}priority: {priority}\n")
        return True
    lines.insert(end, f"{key_indent}extra_body:\n{key_indent}  priority: {priority}\n")
    return True


def ensure_turbo_priority(text: str, priority: int = TURBO_PRIORITY) -> tuple[str, bool]:
    """Stamp extra_body.priority on top-level model and the named ops provider."""
    lines = text.splitlines(keepends=True)
    changed = False
    n = len(lines)
    # top-level model
    i = 0
    while i < n:
        if re.match(r"^model:[ \t]*\n?$", lines[i]):
            start = i + 1
            j = start
            while j < len(lines):
                raw = lines[j]
                if (
                    raw.strip()
                    and not raw.startswith((" ", "\t"))
                    and not raw.lstrip().startswith("#")
                ):
                    break
                j += 1
            if _ensure_priority_in_mapping(lines, start, j, priority):
                changed = True
            break
        i += 1
    # named ops custom_provider item
    i = 0
    in_cp = False
    while i < len(lines):
        raw = lines[i]
        if re.match(r"^custom_providers:[ \t]*\n?$", raw):
            in_cp = True
            i += 1
            continue
        if in_cp:
            if (
                raw.strip()
                and not raw.startswith((" ", "\t", "-"))
                and not raw.lstrip().startswith("#")
            ):
                break
            m = re.match(r"^([ \t]*)-[ \t]*name:[ \t]*(\S+)", raw)
            if m and m.group(2).strip() == "ops":
                start = i + 1
                j = start
                while j < len(lines):
                    nxt = lines[j]
                    if re.match(r"^([ \t]*)-[ \t]*name:", nxt):
                        break
                    if (
                        nxt.strip()
                        and not nxt.startswith((" ", "\t", "-"))
                        and not nxt.lstrip().startswith("#")
                    ):
                        break
                    j += 1
                if _ensure_priority_in_mapping(lines, start, j, priority):
                    changed = True
                i = j
                continue
        i += 1
    return "".join(lines), changed


def pin_to_ops(text: str) -> tuple[str, bool]:
    new, a = set_top_model(
        text, default="ops", provider="custom:ops", base_url=OPS_URL
    )
    new, b = ensure_named_ops_provider(new)
    new, c = ensure_turbo_priority(new, TURBO_PRIORITY)
    return new, a or b or c


def pin_to_crew(text: str) -> tuple[str, bool]:
    return set_top_model(
        text, default="crew", provider="custom:crew", base_url=CREW_URL
    )



def set_ada_caps(text: str, total: int, per: int) -> tuple[str, bool]:
    """Rewrite kanban.max_in_progress / _per_profile only (same shape as krew-mode)."""
    import re as _re
    lines = text.splitlines(keepends=True)
    header = _re.compile(r"^kanban:[ \t]*\n?$")
    field = _re.compile(r"^([ \t]+)(max_in_progress(?:_per_profile)?):[ \t]*(\S+)(.*\n?)$")
    changed = False
    i = 0
    while i < len(lines):
        if not header.match(lines[i]):
            i += 1
            continue
        i += 1
        while i < len(lines):
            raw = lines[i]
            if raw and not raw.startswith((" ", "\t")) and raw.strip():
                break
            m = field.match(raw)
            if m:
                newv = str(per if m.group(2).endswith("per_profile") else total)
                repl = f"{m.group(1)}{m.group(2)}: {newv}{m.group(4)}"
                if repl != raw:
                    lines[i] = repl
                    changed = True
            i += 1
        break
    return "".join(lines), changed


def dispatcher_config(house: Optional[Path] = None) -> Path:
    """The config.yaml whose `kanban:` caps the dispatcher reads: Ada's while she runs a gateway of
    her own, the DEFAULT profile's once one host gateway serves everyone (lib/hermes_topology)."""
    try:
        lib = str(Path(__file__).resolve().parents[1] / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import hermes_topology
        if hermes_topology.is_host():
            return krew_root(house) / "config.yaml"
    except Exception:  # noqa: BLE001
        pass
    return profile_config("ada", house)


def apply_ada_caps(caps: tuple[int, int], house: Optional[Path] = None) -> str:
    cfg = dispatcher_config(house)
    if not cfg.is_file():
        return "missing"
    raw = cfg.read_text(encoding="utf-8")
    new, changed = set_ada_caps(raw, *caps)
    if not changed:
        return f"{caps[0]}/{caps[1]} (already)"
    cfg.write_text(new, encoding="utf-8")
    return f"{caps[0]}/{caps[1]}"


def _read_state(house: Optional[Path] = None) -> dict[str, Any]:
    p = state_file(house)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_state(data: dict[str, Any], house: Optional[Path] = None) -> None:
    p = state_file(house)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _clear_state(house: Optional[Path] = None) -> None:
    p = state_file(house)
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def profile_pins(house: Optional[Path] = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in (*PROFILES, ADA):
        cfg = profile_config(name, house)
        if not cfg.is_file():
            out[name] = "missing"
            continue
        out[name] = pin_kind(cfg.read_text(encoding="utf-8"))
    return out


_HEALING = False


def lease_live(house: Optional[Path] = None) -> bool:
    st = _read_state(house)
    if not st.get("on"):
        return False
    try:
        until_f = float(st.get("until") or 0)
    except (TypeError, ValueError):
        return False
    return until_f > time.time()


def status_snapshot(house: Optional[Path] = None) -> dict[str, Any]:
    """Cheap: JSON + YAML pins. No HTTP. Safe for the 2s panel poll.

    ``sharing`` is the pin itself (Vera on ops in Standard), not gated on
    the lease. An expired hour that left YAML on :8000 must show as shared
    until heal, not vanish behind ``on = until > now``.
    """
    st = _read_state(house)
    now = time.time()
    until = st.get("until")
    try:
        until_f = float(until) if until is not None else 0.0
    except (TypeError, ValueError):
        until_f = 0.0
    lease = bool(st.get("on")) and until_f > now
    pins = profile_pins(house)
    # Watchdog: timer miss left pins on ops with a dead lease.
    global _HEALING
    if pins.get("vera") == "ops" and not lease and not _HEALING:
        _HEALING = True
        try:
            turn_off(house=house, reason="expired-orphan", quiet=True)
        finally:
            _HEALING = False
        st = {}
        until_f = 0.0
        pins = profile_pins(house)
        lease = False
    on = lease
    remaining = max(0, int(until_f - now)) if on else 0
    mode = occupancy_mode(house)
    # heavy-render (09-29): Vera/Steward ride ops because crew is off the card - not parked
    parked = on and mode not in ("normal", "heavy-render")
    sharing = mode in ("normal", "heavy-render") and pins.get("vera") == "ops"
    return {
        "ok": True,
        "on": on,
        "until": until_f if on else None,
        "remaining_s": remaining,
        "profiles": list(PROFILES),
        "pins": pins,
        "reason": st.get("reason") if on else None,
        "ops_seqs": st.get("ops_seqs") if on else None,
        "priority": TURBO_PRIORITY if on else None,
        "occupancy": mode,
        "parked": parked,
        "sharing": sharing,
    }


def _arm_timer(seconds: int) -> Optional[str]:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    if os.environ.get("KREW_TURBO_KREW_NO_TIMER") == "1":
        return None
    if not shutil.which("systemd-run"):
        return None
    _disarm_timer()
    cmd = [
        "systemd-run",
        "--user",
        "--collect",
        "--quiet",
        f"--on-active={int(seconds)}s",
        "--unit",
        TIMER_UNIT,
        "--description",
        "TurboKrew auto-off",
        sys.executable,
        str(Path(__file__).resolve()),
        "off",
        "--reason",
        "timer",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"turbo krew timer skipped: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[:200]
        print(f"turbo krew timer skipped: {err}", file=sys.stderr)
        return None
    return TIMER_UNIT


def _disarm_timer() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not shutil.which("systemctl"):
        return
    for unit in (f"{TIMER_UNIT}.timer", f"{TIMER_UNIT}.service", TIMER_UNIT):
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", unit],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        subprocess.run(
            [
                "systemctl",
                "--user",
                "reset-failed",
                f"{TIMER_UNIT}.timer",
                f"{TIMER_UNIT}.service",
                TIMER_UNIT,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _pin_profile_on(name: str, house: Path) -> str:
    cfg = profile_config(name, house)
    if not cfg.is_file():
        return "missing"
    raw = cfg.read_text(encoding="utf-8")
    bak = backup_path(cfg)
    if not bak.is_file():
        shutil.copy2(cfg, bak)
    new, changed = pin_to_ops(raw)
    if changed:
        cfg.write_text(new, encoding="utf-8")
        return "ops"
    return pin_kind(new)


def _pin_profile_park(name: str, house: Path) -> str:
    """Crew main pin; keep ``.pre-turbo-krew`` so Standard can resume."""
    cfg = profile_config(name, house)
    if not cfg.is_file():
        return "missing"
    raw = cfg.read_text(encoding="utf-8")
    new, changed = pin_to_crew(raw)
    if changed:
        cfg.write_text(new, encoding="utf-8")
    return pin_kind(new)


def _pin_profile_off(name: str, house: Path) -> str:
    """End the lease: pin live YAML to crew. Never copy the whole snapshot back.

    2026-09-16 05:05: writing ``.pre-turbo-krew`` over live restored vera's
    ``skills.disabled`` (80 names, incl. sdlc-review) from 04:00, before the
    04:35 kit re-apply. Pins are simple; the index/allowlist is not a pin.
    """
    cfg = profile_config(name, house)
    if not cfg.is_file():
        return "missing"
    bak = backup_path(cfg)
    if bak.is_file():
        try:
            bak.unlink()
        except OSError:
            pass
    raw = cfg.read_text(encoding="utf-8")
    new, changed = pin_to_crew(raw)
    if changed:
        cfg.write_text(new, encoding="utf-8")
        return pin_kind(new)
    return pin_kind(raw)


def apply_occupancy(
    mode: Optional[str] = None,
    *,
    house: Optional[Path] = None,
    quiet: bool = True,
) -> dict[str, Any]:
    """Pin from lease × occupancy. Does not stop the hour.

    Standard + ops up → Vera/Steward on ops. Heavy-render + ops up → ops as well: crew is
    off the card there (the User 09-29). Anything else (Render / Beast / Off, or
    ops not serving yet) → crew. Ada never moves. Timer stays.
    """
    h = krew_root(house)
    _refuse_live_under_pytest(h)
    dest = (mode or occupancy_mode(h)).strip().lower() or "normal"
    if dest == "standard":
        dest = "normal"
    st = _read_state(h)
    if not st.get("on"):
        return status_snapshot(h)
    try:
        until_f = float(st.get("until") or 0)
    except (TypeError, ValueError):
        until_f = 0.0
    if until_f <= time.time():
        return turn_off(house=h, reason="expired", quiet=quiet)
    applied: dict[str, str] = {}
    if dest in ("normal", "heavy-render") and ops_serving():
        for name in PROFILES:
            applied[name] = _pin_profile_on(name, h)
        note = ("TurboKrew sharing — Vera/Steward on ops." if dest == "normal"
                else "TurboKrew on ops for the heavy window — crew is off the card.")
    else:
        for name in PROFILES:
            applied[name] = _pin_profile_park(name, h)
        note = (
            f"TurboKrew parked on crew (occupancy={dest}); "
            "hour still running."
        )
    snap = status_snapshot(h)
    snap["ok"] = True
    snap["applied"] = applied
    snap["note"] = note
    if not quiet:
        print(note)
    return snap


def turn_on(
    *,
    house: Optional[Path] = None,
    ttl: Optional[int] = None,
    reason: str = "panel",
) -> dict[str, Any]:
    h = krew_root(house)
    _refuse_live_under_pytest(h)
    mode = occupancy_mode(h)
    if mode != "normal":
        return {
            "ok": False,
            "on": False,
            "error": f"TurboKrew is Standard only (occupancy={mode})",
            "pins": profile_pins(h),
        }
    if not ops_serving():
        return {
            "ok": False,
            "on": False,
            "error": "ops :8000 is not serving — refuse rather than start occupancy",
            "pins": profile_pins(h),
        }
    seqs = ops_max_num_seqs()
    if seqs is not None and seqs < MIN_OPS_SEQS:
        return {
            "ok": False,
            "on": False,
            "error": (
                f"ops --max-num-seqs {seqs} < {MIN_OPS_SEQS} "
                "(need Crissa+Vera+Steward concurrent). "
                "Standard is 8 seats on triton_attn; seqs=1 would serialize "
                "TurboKrew behind the mouth. Do not bounce ops from this toggle."
            ),
            "pins": profile_pins(h),
            "ops_seqs": seqs,
        }
    seconds = ttl_s(ttl)
    applied: dict[str, str] = {}
    try:
        for name in PROFILES:
            applied[name] = _pin_profile_on(name, h)
    except Exception as e:
        for name in PROFILES:
            try:
                _pin_profile_off(name, h)
            except OSError:
                pass
        return {
            "ok": False,
            "on": False,
            "error": f"pin failed: {type(e).__name__}: {e}"[:200],
            "pins": profile_pins(h),
        }
    until = time.time() + seconds
    ada_caps = apply_ada_caps(TURBO_ADA_CAPS, h)
    _write_state(
        {
            "on": True,
            "until": until,
            "started": time.time(),
            "ttl_s": seconds,
            "profiles": list(PROFILES),
            "reason": reason,
            "applied": applied,
            "ops_seqs": seqs,
            "priority": TURBO_PRIORITY,
            "ada_caps": ada_caps,
        },
        h,
    )
    timer = _arm_timer(seconds)
    snap = status_snapshot(h)
    snap["ok"] = True
    snap["on"] = True
    snap["timer"] = timer
    seats = f"{seqs} seats, " if seqs else ""
    snap["note"] = (
        "TurboKrew on — Vera and Steward share Crissa's :8000 for "
        f"{max(1, seconds // 60)} min ({seats}priority {TURBO_PRIORITY}; "
        "Crissa mouth stays 0). Ada stays on crew; her caps "
        f"{TURBO_ADA_CAPS[0]}/{TURBO_ADA_CAPS[1]} on disk, live at her next gap. "
        "Crissa's replies wait when they think."
    )
    return snap


def turn_off(
    *,
    house: Optional[Path] = None,
    reason: str = "manual",
    quiet: bool = False,
) -> dict[str, Any]:
    h = krew_root(house)
    _refuse_live_under_pytest(h)
    restored: dict[str, str] = {}
    for name in PROFILES:
        try:
            restored[name] = _pin_profile_off(name, h)
        except OSError as e:
            restored[name] = f"error:{e}"
    _disarm_timer()
    _clear_state(h)
    restored["ada_caps"] = apply_ada_caps(NORMAL_ADA_CAPS, h)
    snap = status_snapshot(h)
    snap["ok"] = True
    snap["on"] = False
    snap["restored"] = restored
    snap["reason"] = reason
    snap["note"] = "TurboKrew off — Vera and Steward back on crew."
    if not quiet:
        print(snap["note"])
    return snap


def toggle(*, house: Optional[Path] = None) -> dict[str, Any]:
    st = status_snapshot(house)
    if st.get("on"):
        return turn_off(house=house, reason="toggle")
    return turn_on(house=house, reason="toggle")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="turbo-krew")
    p.add_argument("cmd", choices=("on", "off", "status", "toggle"))
    p.add_argument("--reason", default="cli")
    p.add_argument("--ttl", type=int, default=None, help="seconds (default 14400 = 4h)")
    args = p.parse_args(argv)
    if args.cmd == "status":
        print(json.dumps(status_snapshot(), indent=2))
        return 0
    if args.cmd == "on":
        out = turn_on(reason=args.reason, ttl=args.ttl)
    elif args.cmd == "toggle":
        out = toggle()
    else:
        out = turn_off(reason=args.reason)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
