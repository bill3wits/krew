"""Two-tier reboot-intent stamp: pending/consumed + jsonl audit. (A1+B1)"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "window" / "scripts" / "krew-reboot-stamp.py"


def _load(name: str = "krew_reboot_stamp"):
    loader = SourceFileLoader(name, str(SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = module_from_spec(spec)
    loader.exec_module(mod)
    return mod


st = _load()


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stale_ts(hours: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds"
    )


def _write_two_tier(path: Path, pending: dict | None, consumed: dict | None = None):
    path.write_text(json.dumps({"pending": pending, "consumed": consumed}) + "\n")


def _write_flat_old(path: Path, **rec):
    path.write_text(json.dumps(rec) + "\n")


@pytest.fixture
def metrics_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(st, "STAMP_PATH", tmp_path / "reboot-intent.json")
    monkeypatch.setattr(st, "AUDIT_PATH", tmp_path / "reboot-intent.jsonl")
    return tmp_path


# ─── _parse_list_boots (pure, testable) ─────────────────────────────────────

def test_parse_list_boots_normal():
    stdout = (
        "IDX BOOT ID                          FIRST ENTRY                 LAST ENTRY\n"
        " -2 c8ca65106ca0409bb7dfb68113c54e81 Fri 2026-09-18 12:16:16 EDT Sat 2026-09-19 15:42:56 EDT\n"
        " -1 2af4aed8fbea43babde89a45d6722e1b Sat 2026-09-19 15:43:21 EDT Thu 2026-09-24 15:52:55 EDT\n"
        "  0 489a619b26cf48f0b12a105010bb291c Thu 2026-09-24 15:54:36 EDT Thu 2026-09-24 16:08:04 EDT\n"
    )
    current, ended = st._parse_list_boots(stdout)
    assert current == "489a619b26cf48f0b12a105010bb291c"
    assert ended == "2af4aed8fbea43babde89a45d6722e1b"


def test_parse_list_boots_single_boot():
    stdout = (
        "IDX BOOT ID                          FIRST ENTRY                 LAST ENTRY\n"
        "  0 489a619b26cf48f0b12a105010bb291c Thu 2026-09-24 15:54:36 EDT Thu 2026-09-24 16:08:04 EDT\n"
    )
    current, ended = st._parse_list_boots(stdout)
    assert current == "489a619b26cf48f0b12a105010bb291c"
    assert ended == ""  # no ended boot


def test_parse_list_boots_empty():
    current, ended = st._parse_list_boots("")
    assert current == ""
    assert ended == ""


def test_parse_list_boots_bad_format():
    stdout = "IDX BOOT ID\n  0 notahexid some time\n  -1 also_not_hex some time\n"
    current, ended = st._parse_list_boots(stdout)
    assert current == ""
    assert ended == ""


# ─── _stamp_is_fresh ─────────────────────────────────────────────────────────

def test_fresh_positive_age():
    assert st._stamp_is_fresh({"ts": _fresh_ts()}) is True


def test_fresh_stale():
    assert st._stamp_is_fresh({"ts": _stale_ts(5)}) is False


def test_fresh_negative_age_rejected():
    # A future timestamp (clock skew) must not be treated as fresh.
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
    assert st._stamp_is_fresh({"ts": future}) is False


def test_fresh_none():
    assert st._stamp_is_fresh(None) is False


# ─── stamp() ─────────────────────────────────────────────────────────────────

def test_stamp_writes_pending(metrics_dir):
    st.stamp("smoke test")
    raw = json.loads(st.STAMP_PATH.read_text())
    assert "pending" in raw
    assert raw["pending"]["reason"] == "smoke test"
    assert raw["consumed"] is None


def test_stamp_appends_jsonl(metrics_dir):
    st.stamp("audit check")
    assert st.AUDIT_PATH.exists()
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    assert len(lines) >= 1
    ev = json.loads(lines[-1])
    assert ev["event"] == "stamp"
    assert ev["reason"] == "audit check"


def test_stamp_default_reason(metrics_dir):
    st.stamp(None)
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"]["reason"] == "operator reboot"


def test_stamp_overwrites_pending(metrics_dir):
    st.stamp("first")
    st.stamp("second")
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"]["reason"] == "second"
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    stamped = [json.loads(l) for l in lines if json.loads(l)["event"] == "stamp"]
    assert len(stamped) == 2


# ─── verify() ────────────────────────────────────────────────────────────────

def test_verify_fresh_pending(metrics_dir):
    st.stamp("pending reason")
    assert st.verify() == "intentional=pending reason"


def test_verify_stale_pending(metrics_dir):
    _write_two_tier(
        st.STAMP_PATH,
        {"ts": _stale_ts(5), "reason": "old", "user": "x", "boot_id": "b", "host": "h"},
    )
    assert st.verify() == "UNKNOWN"


def test_verify_no_file(metrics_dir):
    assert st.verify() == "UNKNOWN"


# ─── consume() ───────────────────────────────────────────────────────────────

def test_consume_fresh_and_matching(metrics_dir, monkeypatch):
    bid_hex = "abcdef0123456789abcdef0123456789"
    bid_hyph = f"{bid_hex[:8]}-{bid_hex[8:12]}-{bid_hex[12:16]}-{bid_hex[16:20]}-{bid_hex[20:]}"
    pending = {
        "ts": _fresh_ts(),
        "reason": "sanctioned reboot",
        "user": "agent",
        "boot_id": bid_hyph,
        "host": "ops-main",
    }
    _write_two_tier(st.STAMP_PATH, pending)
    monkeypatch.setattr(st, "_ended_boot_id_hex", lambda: bid_hex)
    monkeypatch.setattr(st, "platform", type("P", (), {"node": staticmethod(lambda: "ops-main")}))
    result = st.consume()
    assert result == "intentional=sanctioned reboot"
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"] is None
    assert raw["consumed"]["reason"] == "sanctioned reboot"
    assert "consumed_at" in raw["consumed"]
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "consumed" for e in evs)


def test_consume_stale_clears_pending(metrics_dir, monkeypatch):
    """A stale pending is cleared on consume (not left for a later boot)."""
    monkeypatch.setattr(st, "_ended_boot_id_hex", lambda: "ffff")
    pending = {
        "ts": _stale_ts(5),
        "reason": "stale reason",
        "user": "agent",
        "boot_id": "b",
        "host": "h",
    }
    _write_two_tier(st.STAMP_PATH, pending)
    assert st.consume() == "UNKNOWN"
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"] is None  # cleared, not left in place
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "consume_stale" for e in evs)


def test_consume_boot_id_mismatch_clears_pending(metrics_dir, monkeypatch):
    """A mismatched boot_id clears pending (not left for a later boot)."""
    monkeypatch.setattr(st, "_ended_boot_id_hex", lambda: "differentbootid0000000000000")
    bid_hyph = "aabbccdd-1122-3344-5566-778899aabbcc"
    pending = {
        "ts": _fresh_ts(),
        "reason": "mismatch reason",
        "user": "agent",
        "boot_id": bid_hyph,
        "host": "h",
    }
    _write_two_tier(st.STAMP_PATH, pending)
    monkeypatch.setattr(st, "platform", type("P", (), {"node": staticmethod(lambda: "h")}))
    assert st.consume() == "UNKNOWN"
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"] is None  # cleared
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "consume_boot_id_mismatch" for e in evs)


def test_consume_no_pending(metrics_dir):
    _write_two_tier(st.STAMP_PATH, None)
    assert st.consume() == "UNKNOWN"
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "consume_no_pending" for e in evs)


def test_consume_empty_reason_clears_pending(metrics_dir, monkeypatch):
    monkeypatch.setattr(st, "_ended_boot_id_hex", lambda: "abc")
    pending = {
        "ts": _fresh_ts(),
        "reason": "",
        "user": "agent",
        "boot_id": "abc",
        "host": "h",
    }
    _write_two_tier(st.STAMP_PATH, pending)
    assert st.consume() == "UNKNOWN"
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"] is None  # cleared
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "consume_empty_reason" for e in evs)


def test_consume_idempotent(metrics_dir, monkeypatch):
    """Second consume after a successful consume finds no pending → UNKNOWN."""
    bid_hex = "abcdef0123456789abcdef0123456789"
    bid_hyph = f"{bid_hex[:8]}-{bid_hex[8:12]}-{bid_hex[12:16]}-{bid_hex[16:20]}-{bid_hex[20:]}"
    _write_two_tier(
        st.STAMP_PATH,
        {"ts": _fresh_ts(), "reason": "once", "user": "a", "boot_id": bid_hyph, "host": "h"},
    )
    monkeypatch.setattr(st, "_ended_boot_id_hex", lambda: bid_hex)
    monkeypatch.setattr(st, "platform", type("P", (), {"node": staticmethod(lambda: "h")}))
    assert st.consume() == "intentional=once"
    assert st.consume() == "UNKNOWN"
    # audit has a consume_no_pending line from the second call
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "consume_no_pending" for e in evs)


def test_consume_host_mismatch_clears_pending(metrics_dir, monkeypatch):
    """A host mismatch clears pending and returns UNKNOWN."""
    bid_hex = "abcdef0123456789abcdef0123456789"
    bid_hyph = f"{bid_hex[:8]}-{bid_hex[8:12]}-{bid_hex[12:16]}-{bid_hex[16:20]}-{bid_hex[20:]}"
    pending = {
        "ts": _fresh_ts(),
        "reason": "host mismatch",
        "user": "agent",
        "boot_id": bid_hyph,
        "host": "wrong-host",
    }
    _write_two_tier(st.STAMP_PATH, pending)
    monkeypatch.setattr(st, "_ended_boot_id_hex", lambda: bid_hex)
    monkeypatch.setattr(st, "platform", type("P", (), {"node": staticmethod(lambda: "ops-main")}))
    assert st.consume() == "UNKNOWN"
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"] is None
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "consume_host_mismatch" for e in evs)


# ─── backward-compat migration ───────────────────────────────────────────────

def test_migrate_old_flat_fresh_persists(metrics_dir):
    """Old flat format, fresh timestamp → becomes pending, persisted."""
    _write_flat_old(
        st.STAMP_PATH,
        ts=_fresh_ts(), reason="old flat", user="agent",
        boot_id="abcd", host="ops-main",
    )
    tier = st._load_two_tier()
    assert tier["pending"] is not None
    assert tier["pending"]["reason"] == "old flat"
    # file was persisted as two-tier (re-read returns same shape)
    raw = json.loads(st.STAMP_PATH.read_text())
    assert "pending" in raw
    assert raw["pending"]["reason"] == "old flat"
    # jsonl has exactly one migration line
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    migr = [e for e in evs if e["event"] == "migrate_old_flat_to_pending"]
    assert len(migr) == 1
    # second load does NOT re-migrate (no second audit line)
    st._load_two_tier()
    lines2 = st.AUDIT_PATH.read_text().strip().splitlines()
    evs2 = [json.loads(l) for l in lines2]
    migr2 = [e for e in evs2 if e["event"] == "migrate_old_flat_to_pending"]
    assert len(migr2) == 1  # still one


def test_migrate_old_flat_stale_persists(metrics_dir):
    """Old flat format, stale timestamp → dropped, persisted as empty tiers."""
    _write_flat_old(
        st.STAMP_PATH,
        ts=_stale_ts(5), reason="old stale", user="agent",
        boot_id="abcd", host="ops-main",
    )
    tier = st._load_two_tier()
    assert tier["pending"] is None
    raw = json.loads(st.STAMP_PATH.read_text())
    assert raw["pending"] is None
    lines = st.AUDIT_PATH.read_text().strip().splitlines()
    evs = [json.loads(l) for l in lines]
    assert any(e["event"] == "migrate_old_flat_dropped_stale" for e in evs)


def test_verify_still_works_after_migration(metrics_dir):
    """verify() works on migrated two-tier file."""
    _write_flat_old(
        st.STAMP_PATH,
        ts=_fresh_ts(), reason="migrated", user="agent",
        boot_id="abcd", host="ops-main",
    )
    assert st.verify() == "intentional=migrated"


# ─── CLI end-to-end ───────────────────────────────────────────────────────────

def test_cli_stamp_and_verify(metrics_dir, monkeypatch, capsys):
    st.main(["stamp", "cli test"])
    out = capsys.readouterr().out
    assert "pending: cli test" in out
    st.main(["verify"])
    out = capsys.readouterr().out
    assert "intentional=cli test" in out


def test_cli_consume_no_pending(metrics_dir, monkeypatch, capsys):
    _write_two_tier(st.STAMP_PATH, None)
    st.main(["consume"])
    out = capsys.readouterr().out
    assert "UNKNOWN" in out


def test_cli_unknown_command(metrics_dir, monkeypatch, capsys):
    st.main(["bogus"])
    out = capsys.readouterr().err
    assert "unknown command" in out
