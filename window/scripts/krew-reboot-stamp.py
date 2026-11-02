#!/usr/bin/env python3
"""Pre-reboot intent stamp — two-tier (pending/consumed) + append-only audit.

Records an operator's intention to reboot the host *before* the reboot, so the
janitor's reboot-detected card can distinguish a deliberate, sanctioned reboot
from one that needs a full forensic pass.

Storage (two-tier):
  $KREW_METRICS_ROOT/reboot-intent.json —
    {"pending": <stamp>|null, "consumed": <stamp+verdict>|null}
  $KREW_METRICS_ROOT/reboot-intent.jsonl — append-only audit (one line per
  stamp, consume, or stale verdict)

Flow:
  1. Operator runs `stamp "reason"` → written to `pending`, appended to jsonl.
  2. Reboot happens.
  3. Janitor detects boot_id change → calls `consume`.
  4. `consume` checks: is `pending` fresh (<2h) AND does its `boot_id` match
     the boot that just ended (journalctl --list-boots idx -1)?
     - Yes → move pending → consumed, append jsonl, print "intentional=<reason>"
     - No  → pending is CLEARED (moved to jsonl only), print "UNKNOWN".
       A failed consume never leaves a stale pending that a later boot could
       be mis-consumed with.

One-card-per-boot: the janitor files at most one reboot card per boot via its
state diff (state["boot_id"]). Consume is called at most once per boot by the
janitor. The stamp's consumed tier records the boot_id consumed, so a second
call for the same ended boot is a no-op (consume_no_pending → UNKNOWN).

Backward compat: if the JSON is still the old flat format, `_load_two_tier`
migrates it to the two-tier layout exactly once (persisted), and `verify`
works on the migrated shape.

Commands:
  stamp [reason]   Write a fresh stamp to `pending`; append to jsonl.
  verify           Print "intentional=<reason>" if `pending` is fresh;
                   else "UNKNOWN". (Read-only; preserved for compat.)
  consume          Janitor path: check pending freshness + boot_id match
                   against the ended boot. On match: move pending → consumed,
                   print "intentional=<reason>". On mismatch/stale: clear
                   pending (jsonl only), print "UNKNOWN".

Stdlib only. Part of the reboot-mitigation plan (2026-09-24, A1+B1).

krew: $KREW_METRICS_ROOT defaults to $KREW_HOME/metrics (the reference install kept them on its data disk).
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

METRICS_DIR = Path(os.path.expanduser(os.environ.get("KREW_METRICS_ROOT")
                                    or str(Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser() / "metrics")))
STAMP_PATH = METRICS_DIR / "reboot-intent.json"
AUDIT_PATH = METRICS_DIR / "reboot-intent.jsonl"
FRESH_SECONDS = 2 * 60 * 60  # 2h: a stamp is only trusted if it was recent


# ─── boot id helpers ──────────────────────────────────────────────────────────

def _boot_id() -> str:
    """Current boot_id (hyphenated form as in /proc)."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _boot_id_hex() -> str:
    """Current boot_id without hyphens (journalctl form)."""
    return _boot_id().replace("-", "")


def _parse_list_boots(stdout: str) -> tuple[str, str]:
    """Parse `journalctl --list-boots` output.

    Returns (current_boot_id_hex, ended_boot_id_hex) — both hex, no hyphens.
    current = idx 0 (last data line); ended = idx -1 (second-to-last).
    Either may be "" if the journal has fewer than 2 boots or the format
    drifted. Pure function — testable without subprocess.
    """
    lines = [l for l in stdout.strip().splitlines() if l.strip()]
    # Drop the header line ("IDX BOOT ID ...").
    data_lines = [l for l in lines if not l.startswith("IDX")]
    if len(data_lines) < 1:
        return ("", "")
    current_hex = ""
    ended_hex = ""
    if len(data_lines) >= 1:
        # Last line is the current boot (idx 0).
        parts = data_lines[-1].split()
        # Format: "0  <hex>  <first>  <last>"
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{32}", parts[1]):
            current_hex = parts[1]
    if len(data_lines) >= 2:
        # Second-to-last is the ended boot (idx -1).
        parts = data_lines[-2].split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{32}", parts[1]):
            ended_hex = parts[1]
    return (current_hex, ended_hex)


def _ended_boot_id_hex() -> str:
    """Boot_id of the boot that just ended (journalctl --list-boots idx -1).

    Returns hex form (no hyphens). Empty string on failure.
    """
    try:
        r = subprocess.run(
            ["journalctl", "--list-boots"],
            capture_output=True, text=True, timeout=10,
        )
        _, ended = _parse_list_boots(r.stdout)
        return ended
    except Exception:
        return ""


def _normalize_boot_id(bid: str) -> str:
    """Strip hyphens for comparison."""
    return (bid or "").replace("-", "").lower()


# ─── time helpers ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(ts_raw: str) -> float | None:
    """Return epoch seconds or None."""
    try:
        parsed = datetime.fromisoformat(ts_raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError):
        return None


def _stamp_is_fresh(rec: dict | None) -> bool:
    """True if the stamp's ts is within the last FRESH_SECONDS (positive age).

    A stamp is trusted only if it was written *before now* and within 2h.
    A negative age (future timestamp, clock skew) is rejected — it would
    widen the window and let a stale stamp survive across a reboot.
    """
    if not rec:
        return False
    epoch = _parse_ts(rec.get("ts", ""))
    if epoch is None:
        return False
    age = time.time() - epoch
    return 0 <= age < FRESH_SECONDS


# ─── jsonl audit ─────────────────────────────────────────────────────────────

def _append_audit(event: str, **fields) -> None:
    """Append one JSON line to the audit log."""
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    line = {"event": event, "ts": _now_iso(), **fields}
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ─── two-tier JSON I/O ───────────────────────────────────────────────────────

def _load_two_tier() -> dict:
    """Load the two-tier JSON. Returns {"pending": ..., "consumed": ...}.

    Migrates the old flat format exactly once: a flat record becomes
    `pending` if fresh, or is dropped. The migrated layout is persisted
    so subsequent reads do not re-migrate (audit line emitted once).
    """
    try:
        raw = json.loads(STAMP_PATH.read_text())
    except (OSError, ValueError):
        return {"pending": None, "consumed": None}

    # Already two-tier?
    if isinstance(raw, dict) and ("pending" in raw or "consumed" in raw):
        return {"pending": raw.get("pending"), "consumed": raw.get("consumed")}

    # Old flat format: migrate exactly once, persist the result.
    if isinstance(raw, dict) and "ts" in raw:
        fresh = _stamp_is_fresh(raw)
        tier: dict = {"pending": None, "consumed": None}
        if fresh:
            tier["pending"] = raw
            _append_audit("migrate_old_flat_to_pending", **{
                k: raw.get(k) for k in ("ts", "reason", "user", "boot_id", "host")
            })
        else:
            _append_audit("migrate_old_flat_dropped_stale", **{
                k: raw.get(k) for k in ("ts", "reason", "user", "boot_id", "host")
            })
        _save_two_tier(tier)  # persist so re-reads do not re-migrate
        return tier

    return {"pending": None, "consumed": None}


def _save_two_tier(tier: dict) -> None:
    """Write the two-tier JSON."""
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"pending": tier.get("pending"), "consumed": tier.get("consumed")}
    fd = os.open(str(STAMP_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, json.dumps(payload, indent=2).encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


# ─── public commands ─────────────────────────────────────────────────────────

def stamp(reason: str | None) -> Path:
    """Write a fresh stamp to `pending`; append to jsonl audit."""
    reason = (reason or "").strip() or "operator reboot"
    rec = {
        "ts": _now_iso(),
        "reason": reason,
        "user": getpass.getuser(),
        "boot_id": _boot_id(),
        "host": platform.node(),
    }
    tier = _load_two_tier()
    tier["pending"] = rec
    _save_two_tier(tier)
    _append_audit("stamp", **rec)
    return STAMP_PATH


def verify() -> str:
    """Read-only check: "intentional=<reason>" if pending is fresh,
    else "UNKNOWN". Preserved for backward compatibility.
    """
    tier = _load_two_tier()
    pending = tier.get("pending")
    if pending and _stamp_is_fresh(pending):
        reason = (pending.get("reason") or "").strip()
        if reason:
            return f"intentional={reason}"
    return "UNKNOWN"


def consume() -> str:
    """Janitor consume path — called at most once per boot by the janitor.

    Checks (all must pass):
      1. `pending` exists with a non-empty reason.
      2. `pending` is fresh (<2h, positive age only).
      3. `pending.boot_id` matches the boot that just ended
         (journalctl --list-boots idx -1).
      4. `pending.host` matches the current host (if present).

    On all-pass: moves pending → consumed, appends jsonl, returns
    "intentional=<reason>".
    On any-fail: clears pending (moved to jsonl only), returns "UNKNOWN".
    A failed consume never leaves a pending that a later boot could
    mis-consume.
    """
    tier = _load_two_tier()
    pending = tier.get("pending")

    if not pending:
        _append_audit("consume_no_pending")
        return "UNKNOWN"

    reason = (pending.get("reason") or "").strip()
    if not reason:
        _append_audit("consume_empty_reason", **pending)
        tier["pending"] = None
        _save_two_tier(tier)
        return "UNKNOWN"

    # Freshness check (positive age only).
    if not _stamp_is_fresh(pending):
        _append_audit("consume_stale", **pending)
        tier["pending"] = None
        _save_two_tier(tier)
        return "UNKNOWN"

    # Host check (if the stamp carries a host field).
    if pending.get("host") and pending["host"] != platform.node():
        _append_audit("consume_host_mismatch",
                      stamp_host=pending["host"],
                      current_host=platform.node(),
                      reason=reason)
        tier["pending"] = None
        _save_two_tier(tier)
        return "UNKNOWN"

    # Boot_id match: pending was stamped during the boot that just ended.
    ended_hex = _ended_boot_id_hex()
    pending_hex = _normalize_boot_id(pending.get("boot_id", ""))
    if not ended_hex or not pending_hex or ended_hex != pending_hex:
        _append_audit("consume_boot_id_mismatch",
                      pending_boot_id=pending_hex,
                      ended_boot_id=ended_hex,
                      reason=reason)
        # Clear pending so a later boot cannot consume this stale stamp.
        tier["pending"] = None
        _save_two_tier(tier)
        return "UNKNOWN"

    # All checks passed: consume.
    consumed_rec = dict(pending)
    consumed_rec["consumed_at"] = _now_iso()
    tier["pending"] = None
    tier["consumed"] = consumed_rec
    _save_two_tier(tier)
    _append_audit("consumed", **consumed_rec)
    return f"intentional={reason}"


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    doc = __doc__ or ""
    if not argv or argv[0] in ("-h", "--help"):
        print(doc.strip())
        return 0
    cmd = argv[0]
    if cmd == "stamp":
        reason = argv[1] if len(argv) > 1 and argv[1] not in ("", "-") else None
        path = stamp(reason)
        tier = _load_two_tier()
        stored = (tier.get("pending") or {}).get("reason", "")
        print(f"stamp: {path} (pending: {stored})")
        return 0
    if cmd == "verify":
        print(verify())
        return 0
    if cmd == "consume":
        print(consume())
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    print(doc.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
