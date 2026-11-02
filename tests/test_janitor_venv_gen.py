"""janitor: hermes venv-generation inventory is report-only and classifies live vs stale.

t_706a5db8: generations under ~/.hermes/installs are never pruned; the janitor
row names which are not live / not mapped, with age and size. Deletes nothing.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))

import krew_venv_gen as hv  # noqa: E402


def _mk(root: Path, install: str, gen: str, live: bool = False, env_file: bytes = b"") -> Path:
    """Create installs/<install>/environments/<gen>/venv with a stub venv
    (bin/python + a 1 MiB lib file so size is measurable), and optionally a
    facts.json naming <gen> as the live environment."""
    venv = root / install / "environments" / gen / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_bytes(b"stub")
    (venv / "lib").mkdir()
    (venv / "lib" / "stub.so").write_bytes(b"x" * 2**20)  # 1 MiB
    if live:
        env_path = str(venv)
        facts = root / install / "facts.json"
        facts.write_text(json.dumps(
            {"packages": {"venv": {"environment": env_path, "extras": ["all"]}}, "schema": 1}
        ))
    return root / install


def test_empty_tree_is_clean(tmp_path):
    root = tmp_path / "installs"
    root.mkdir()
    r = hv.hermes_venv_gen_report(root)
    assert r["error"] is None
    assert r["live"] == {}
    assert r["stale"] == []
    assert r["total_gib"] == 0.0


def test_missing_root_reports_error(tmp_path):
    r = hv.hermes_venv_gen_report(tmp_path / "nope")
    assert r["error"] and "no such dir" in r["error"]
    assert r["stale"] == []


def test_live_from_facts_is_not_stale(tmp_path):
    root = tmp_path / "installs"
    _mk(root, "aaa111", "live0001", live=True)
    _mk(root, "aaa111", "old00002")
    r = hv.hermes_venv_gen_report(root)
    assert r["live"] == {"aaa111": {"gen": "live0001", "path": str(root / "aaa111/environments/live0001/venv")}}
    assert len(r["stale"]) == 1
    stale = r["stale"][0]
    assert stale["gen"] == "old00002"
    assert stale["install_id"] == "aaa111"
    assert stale["mapped"] is False
    assert stale["age_days"] >= 0
    assert stale["size_gib"] > 0
    # nothing is deleted
    assert (root / "aaa111/environments/old00002/venv/bin/python").exists()
    assert (root / "aaa111/environments/live0001/venv/bin/python").exists()


def test_multiple_installs_each_keep_their_live_gen(tmp_path):
    root = tmp_path / "installs"
    _mk(root, "aaa111", "liveA", live=True)
    _mk(root, "bbb222", "liveB", live=True)
    _mk(root, "bbb222", "oldB")
    r = hv.hermes_venv_gen_report(root)
    assert set(r["live"]) == {"aaa111", "bbb222"}
    assert r["live"]["bbb222"]["gen"] == "liveB"
    assert [s["gen"] for s in r["stale"]] == ["oldB"]


def test_bad_facts_is_ignored_not_fatal(tmp_path):
    root = tmp_path / "installs"
    _mk(root, "ccc333", "genX")
    (root / "ccc333" / "facts.json").write_text("{not json")
    r = hv.hermes_venv_gen_report(root)
    assert r["error"] is None
    assert r["live"] == {}  # unreadable facts -> nothing claims live
    assert [s["gen"] for s in r["stale"]] == ["genX"]


def test_live_but_facts_points_elsewhere(tmp_path):
    """facts.json environment path that does not parse to a gen under root:
    the gen is still stale (facts did not claim it)."""
    root = tmp_path / "installs"
    _mk(root, "ddd444", "genY")
    (root / "ddd444" / "facts.json").write_text(json.dumps(
        {"packages": {"venv": {"environment": "/elsewhere/venv"}}, "schema": 1}
    ))
    r = hv.hermes_venv_gen_report(root)
    assert r["live"] == {}
    assert [s["gen"] for s in r["stale"]] == ["genY"]


def test_size_is_venv_only_and_gib(tmp_path):
    root = tmp_path / "installs"
    _mk(root, "eee555", "genZ")
    big = root / "eee555/environments/genZ/venv/lib"
    (big / "big.so").write_bytes(b"x" * (8 * 2**20))  # +8 MiB on top of the 1 MiB stub
    r = hv.hermes_venv_gen_report(root)
    g = r["stale"][0]["size_gib"]
    lo, hi = 9 / 1024, 9 / 1024 + 0.01  # ~0.0088 GiB, venv content only
    assert lo < g < hi


def test_no_process_mapping_is_all_unmapped(tmp_path):
    """On this box nothing runs from a tmp installs tree -> every gen unmapped."""
    root = tmp_path / "installs"
    _mk(root, "fff666", "genQ")
    r = hv.hermes_venv_gen_report(root)
    assert r["stale"][0]["mapped"] is False


def test_age_days_tracks_mtime(tmp_path):
    root = tmp_path / "installs"
    g = _mk(root, "ggg777", "genOld")
    old = time.time() - 3 * 86400
    os_utime = __import__("os").utime
    for p in [g, g / "environments", g / "environments/genOld"]:
        os_utime(p, (old, old))
    r = hv.hermes_venv_gen_report(root)
    assert 2.9 <= r["stale"][0]["age_days"] <= 3.1


def test_mapped_but_not_in_facts_is_stale_not_live(monkeypatch, tmp_path):
    """The facts-only boundary: a gen mapped by a running process but NOT
    named in facts.json is reported STALE with mapped:true. The mapped
    annotation never promotes a gen to live — the report never under-states
    what a prune could touch. This pins the exact ambiguity where the
    docstring and the code could disagree."""
    root = tmp_path / "installs"
    _mk(root, "hhh888", "mappedGen")  # NOT live: no facts.json
    # Inject a fake /proc scan that claims this gen is mapped by a process.
    monkeypatch.setattr(
        hv, "_proc_mapped_generations",
        lambda: {"hhh888": {"mappedGen"}},
    )
    r = hv.hermes_venv_gen_report(root)
    # mapped-but-not-in-facts: NOT in live, present in stale with mapped=True
    assert r["live"] == {}
    assert len(r["stale"]) == 1
    assert r["stale"][0]["gen"] == "mappedGen"
    assert r["stale"][0]["mapped"] is True


def test_report_is_idempotent_and_tree_unchanged_across_calls(tmp_path):
    """Report-only guarantee across repeated calls: the tree is byte-identical
    after N report calls, and the report itself is stable (same live/stale
    split, same sizes). No mutation beyond the exists() snapshot."""
    import os as _os
    root = tmp_path / "installs"
    _mk(root, "iii999", "liveGen", live=True)
    _mk(root, "iii999", "staleGen")

    def tree_bytes() -> dict:
        snap = {}
        for p in root.rglob("*"):
            if p.is_file():
                snap[str(p)] = p.read_bytes()
        return snap

    before = tree_bytes()
    r1 = hv.hermes_venv_gen_report(root)
    r2 = hv.hermes_venv_gen_report(root)
    after = tree_bytes()
    # tree is unchanged
    assert before == after
    # report is stable: same split, same sizes
    assert r1["live"] == r2["live"]
    assert [s["gen"] for s in r1["stale"]] == [s["gen"] for s in r2["stale"]]
    assert r1["total_gib"] == r2["total_gib"]
    assert r1["stale_gib"] == r2["stale_gib"]
    # and the stale file is still there
    assert (root / "iii999/environments/staleGen/venv/bin/python").exists()
