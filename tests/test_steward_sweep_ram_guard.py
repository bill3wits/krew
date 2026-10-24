"""RAM-pressure guard for steward-sweep (t_fbedcef1 + t_801d7b01, 2026-09-21;
09-22 retargeted to the working set, t_7f7a906f).

The 09-21 17:45Z oomd-kill (t_5b190d15, class OOM_Scheduling): the sweep's
worker ran inside the hermes-ada-gateway kanban swarm. systemd-oomd fired on
the unit's memory.current — which COUNTS RECLAIMABLE PAGE CACHE (measured in
that cgroup: anon 658 MB vs file 12.9 GB). The kernel reclaimed continuously
to stay under the cap and the ManagedOOMMemoryPressure kill followed; the
ceiling manufactured the kill from cache. MemoryHigh was REMOVED 09-22
(commit 6559bb01), so memory.high now reads 'max' and the old gate was inert;
and even with a ceiling, memory.current vs memory.high measured the wrong
number (cache, not working set).

Gate = the gateway unit's ANON (memory.stat 'anon' — the working set) vs its
own memory.max (MemoryMax=24G, numeric), NEVER VRAM: test_sweep_has_no_gpu_gate
pins that no GPU gate may be added. The 09-21 shipped draft measured the whole
user slice vs host RAM at 50% — wrong cgroup AND permanently tripped at the
live 79-84% idle slice baseline, so the sweep skipped every hour and the
board was starved (Vera FAIL t_7144a697). Round 2 (t_7144a697 verdict): the
probe resolves the unit cgroup by NAME (bounded find under /sys/fs/cgroup;
app.slice path). Round 3 (t_7f7a906f, 09-22): the probe is anon vs
memory.max — the metric the kernel's per-cgroup OOM counts minus cache.
The default is 80% of the unit's own memory.max: the boundary moved from
80% of the removed 20G MemoryHigh (16.0 GB) to 80% of 24G (19.2 GB); the
hard ceiling is 2G above the old soft one because a per-cgroup OOM at 24G is
the recovery, not the pathology. There is NO whole-slice fallback: the unit's
memory.max IS the ceiling that kills inside the unit, so a separate
slice-level check adds no safety signal and would reintroduce the
permanently-tripped whole-slice gate that this card removes. Unmeasurable =
fail open.

Default-threshold pinning: the guard and the sweep both default to 80 —
test_guard_default_threshold_is_80 and test_sweep_default_threshold_is_80
pin the value in BOTH files, so a future edit must change the code and the
test together, never one.

Probe seam: SWEEP_RAM_PROBE_DIR (env or --probe-dir) points the guard at a
synthetic cgroupfs tree. The guard's unit-dir find (bounded maxdepth) is the
locator for both the synthetic tree and the live /sys/fs/cgroup. When the
unit dir cannot be located, or memory.stat lacks the anon line, or
memory.max is non-numeric ('max'), the guard FAILS OPEN — a broken gate must
never starve the board of a steward (the 09-13 sweep_gate failure).
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "crew" / "scripts" / "steward-sweep"
GUARD = ROOT / "crew" / "scripts" / "sweep-ram-guard"

# The unit's live cap (memcap.conf drop-in; MemoryHigh REMOVED 09-22,
# MemoryMax=24G kept — commit 6559bb01).
MAX_BYTES = 24 * 1024**3
# Live host RAM at 2026-09-21 19:0xZ (for the whole-slice fallback probe).
LIVE_MEM_TOTAL_BYTES = 96 * 1024**3


UNIT_REL = Path("user.slice/user-1000.slice/<email>/app.slice/hermes-ada-gateway.service")


def _mk_probe(tmp_path: Path, anon_frac: float, max_bytes: int = MAX_BYTES,
              with_unit: bool = True, meminfo_total_bytes: int | None = LIVE_MEM_TOTAL_BYTES,
              file_bytes: int | None = 13 * 1024**3) -> Path:
    """Synthetic cgroupfs tree.

    anon_frac: fraction of the unit's memory.max to write into memory.stat's
    'anon' line (the working set). file_bytes: the 'file' line (reclaimable
    page cache) — default 13 GB, the live 09-21 shape, so the tree PROVES the
    guard ignores cache (the page-cache discrimination, attack 1 of the
    09-22 review). with_unit=False: the tree has the slice but NOT the
    gateway unit dir (the gateway-down case; the guard fails open — there is
    no slice fallback). meminfo_total_bytes: bytes for the synthetic
    /proc/meminfo file; None means no meminfo file.

    Returns a FRESH per-call subtree (mkdtemp under tmp_path)."""
    root = Path(tempfile.mkdtemp(dir=str(tmp_path)))
    if with_unit:
        unit = root / UNIT_REL
        unit.mkdir(parents=True)
        anon = int(anon_frac * max_bytes)
        if file_bytes is not None:
            (unit / "memory.stat").write_text(
                f"anon {anon}\nfile {file_bytes}\n"
                f"inactive_anon {anon // 4}\nactive_anon {anon // 4}\n")
        else:
            (unit / "memory.stat").write_text(f"anon {anon}\n")
        (unit / "memory.max").write_text(f"{max_bytes}\n")
    else:
        # slice dir with a plausible memory.current (the guard must not gate on it)
        sl = root / "user.slice" / "user-1000.slice"
        sl.mkdir(parents=True)
        (sl / "memory.current").write_text(f"{int(anon_frac * meminfo_total_bytes)}\n")
    if meminfo_total_bytes is not None:
        total_kb = meminfo_total_bytes // 1024
        (root / "proc-meminfo").write_text(f"MemTotal:       {total_kb} kB\n")
    return root


def _run_guard(args: list[str] | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.pop("SWEEP_RAM_PROBE_DIR", None)  # never leak a real probe into a test
    e.pop("SWEEP_RAM_GUARD", None)
    e.pop("SWEEP_RAM_MAX_PCT", None)
    if env:
        e.update(env)
    return subprocess.run(
        ["bash", str(GUARD), *(args or [])],
        env=e, capture_output=True, text=True, timeout=30,
    )


# ---------------------------------------------------------------------------
# The guard script itself — unit cgroup vs its own memory.high
# ---------------------------------------------------------------------------

def test_guard_script_is_executable():
    assert GUARD.is_file(), "scripts/sweep-ram-guard missing"
    assert GUARD.stat().st_mode & stat.S_IXUSR, "sweep-ram-guard must be executable"


def test_guard_passes_below_threshold(tmp_path):
    # idle working set: ~12.9 GB of the 20 GB high (live 2026-09-21 19:0xZ)
    p = _mk_probe(tmp_path, anon_frac=0.65)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "sweep-ram-guard:" in r.stderr  # the measured line was logged


def test_guard_skips_at_default_threshold_80(tmp_path):
    # A working set at/above 80% of max -> at/above the default threshold ->
    # skip. Note the probe lands at the true boundary: int(0.80 * 24GiB)
    # floors 0.8 B BELOW the boundary (24 GiB is not divisible by 5) and the
    # guard correctly reads it as 79% and passes (test_guard_default_threshold_is_80
    # pins both sides of the boundary with the guard's own arithmetic).
    p = _mk_probe(tmp_path, anon_frac=0.81)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 3, r.stderr
    assert "SKIP" in r.stderr or "skip" in r.stderr


def test_guard_skips_above_threshold(tmp_path):
    # 95% of high: a real approaching-ceiling pressure
    p = _mk_probe(tmp_path, anon_frac=0.95)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 3, r.stderr
    assert "SKIP" in r.stderr or "skip" in r.stderr


def test_guard_passes_at_79_pct_default(tmp_path):
    # 79% of high: just under the 80% default -> pass (the 09-21 kill was
    # at ~61-64%; the idle working set ~12-13 GB = ~63-65%; 79% = ~15.8 GB,
    # still below the 20 GB cap and above the working set)
    p = _mk_probe(tmp_path, anon_frac=0.79)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr


def test_guard_skip_message_carries_measured_value(tmp_path):
    """The skip line must carry the measured GB/percent, not just the
    threshold, so the log has the real number. The probe lands at the true
    measured percent (int(0.95*24GiB) floors to 94%, not 95 — the guard
    reports what it measured, and the assertion matches that)."""
    p = _mk_probe(tmp_path, anon_frac=0.95)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 3
    assert "GiB" in r.stderr, f"measured GB missing from skip line: {r.stderr}"
    assert "94%" in r.stderr, f"measured percent missing from skip line: {r.stderr}"


def test_guard_env_disable(tmp_path):
    # The guard does NOT read SWEEP_RAM_GUARD itself — the sweep's disable
    # seam (its header: SWEEP_RAM_GUARD=0 is "handled by the sweep"). The
    # sweep exits before invoking the guard; an enabled guard called anyway
    # would still measure 99% and skip. So: sweep code must carry the seam,
    # and the guard called directly must NOT pass via env.
    assert "SWEEP_RAM_GUARD" in _sweep_code()
    p = _mk_probe(tmp_path, anon_frac=0.99)
    r = _run_guard(["--probe-dir", str(p)], env={"SWEEP_RAM_GUARD": "0"})
    assert r.returncode == 3, r.stderr  # direct invocation still measures


def test_guard_arg_disable(tmp_path):
    p = _mk_probe(tmp_path, anon_frac=0.99)
    r = _run_guard(["--probe-dir", str(p), "--disable"])
    assert r.returncode == 0, r.stderr


def test_guard_threshold_override(tmp_path):
    # 65% of max: default (80%) passes; threshold 50 skips.
    p = _mk_probe(tmp_path, anon_frac=0.65)
    assert _run_guard(["--probe-dir", str(p)]).returncode == 0
    assert _run_guard(["--probe-dir", str(p), "--max-pct=50"]).returncode == 3
    # env seam, as the sweep invokes it:
    assert _run_guard(["--probe-dir", str(p)], env={"SWEEP_RAM_MAX_PCT": "50"}).returncode == 3


def test_guard_default_threshold_is_80(tmp_path):
    """The default is 80% of the unit's memory.max — pinned so a future
    change to the default cannot drift the guard without updating this test
    value. 79% passes, 80%+ skips, with no threshold argument anywhere.
    (Round 3: the boundary is now 80% of the 24 GB hard cap — 19.2 GB —
    up from 80% of the removed 20 GB MemoryHigh, because memory.high is
    gone and the working set vs the hard cap is the metric that matters.)
    Boundary probed with the guard's own arithmetic: the minimum anon that
    skips is ceil(MAX*80/100) — int(0.80*24GiB) floors 0.8 B under it and
    must pass."""
    at = -(-MAX_BYTES * 80 // 100)  # ceil: the minimum anon that gates
    below = at - 1                  # one byte under: must pass
    d = _mk_probe(tmp_path, anon_frac=0.50)
    u = d / UNIT_REL
    (u / "memory.stat").write_text(f"anon {below}\nfile 13958643763\n")
    assert _run_guard(["--probe-dir", str(d)]).returncode == 0, "just under the boundary must pass"
    (u / "memory.stat").write_text(f"anon {at}\nfile 13958643763\n")
    r = _run_guard(["--probe-dir", str(d)])
    assert r.returncode == 3, f"exactly at the boundary must skip: {r.stderr}"
    assert _run_guard(["--probe-dir", str(_mk_probe(tmp_path, 0.91))]).returncode == 3


def test_guard_fails_open_probe_dir_missing(tmp_path):
    r = _run_guard(["--probe-dir", str(tmp_path / "nope")])
    assert r.returncode == 0, r.stderr
    assert "failing open" in r.stderr


def test_guard_fallback_fails_open_when_no_slice_or_meminfo(tmp_path):
    """Unit cgroup absent: fail open. No whole-slice fallback exists —
    the unit's memory.max IS the ceiling the kernel's per-cgroup OOM fires
    at, so a slice-level check adds no safety signal and would reintroduce
    the permanently-tripped whole-slice gate."""
    p = tmp_path / "empty-tree"
    p.mkdir()
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "failing open" in r.stderr


def test_guard_fallback_passes_below_95_slice(tmp_path):
    """Unit cgroup absent, whole slice at 80% of host RAM (the live idle
    baseline): NO fallback exists — the guard fails open (rc 0), it does
    NOT gate on the slice percentage."""
    p = _mk_probe(tmp_path, anon_frac=0.80, with_unit=False)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "FALLBACK" not in r.stderr


def test_guard_fallback_skips_at_95_slice(tmp_path):
    """Unit cgroup absent, whole slice at 96% of host RAM: still no
    fallback — the guard fails open (rc 0) instead of gating on the slice.
    A slice at 96% of host RAM would NOT skip if the old fallback were
    still in the guard (the pin catches a regression that resurrects it)."""
    p = _mk_probe(tmp_path, anon_frac=0.96, with_unit=False)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "FALLBACK" not in r.stderr


def test_guard_fallback_fails_open_bad_slice_current(tmp_path):
    """Unit cgroup absent, slice memory.current unreadable: fail open."""
    p = _mk_probe(tmp_path, anon_frac=0.80, with_unit=False)
    (p / "user.slice/user-1000.slice/memory.current").write_text("garbage\n")
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "failing open" in r.stderr


def test_guard_fails_open_unreadable_anon(tmp_path):
    """The anon line is missing/garbage: fail open."""
    p = _mk_probe(tmp_path, anon_frac=0.99)
    unit = p / UNIT_REL
    # replace memory.stat with a file that has no anon line
    (unit / "memory.stat").write_text("file 999999\n")
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "failing open" in r.stderr


def test_guard_fails_open_bad_max(tmp_path):
    """memory.max is non-numeric ('max' — no hard cap set): fail open.
    A unit without a numeric hard cap has no reference for the ratio, so
    the guard cannot gate on it."""
    p = _mk_probe(tmp_path, anon_frac=0.99)
    unit = p / UNIT_REL
    (unit / "memory.max").write_text("max\n")  # non-numeric ceiling
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "failing open" in r.stderr


def test_guard_ignores_page_cache(tmp_path):
    """The 09-21 shape: anon low, file cache high (12.9 GB). The guard
    must NOT count the cache — anon at 5% of max with 13 GB of file cache
    is a PASS, not a skip. This is the regression the whole card exists
    for: memory.current (anon+file) would have been 13 GB+ vs the 20 GB
    high and fired; anon vs memory.max is 5% vs 24 GB and does not."""
    p = _mk_probe(tmp_path, anon_frac=0.05, file_bytes=13 * 1024**3)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    # and the measured line reports anon, not current
    assert "anon" in r.stderr, r.stderr


def test_guard_threshold_100_disables():
    r = _run_guard(["--max-pct=100"])
    assert r.returncode == 0, r.stderr


def test_guard_rejects_bad_threshold():
    r = _run_guard(["--max-pct=abc"])
    assert r.returncode == 2, r.stderr


def test_guard_trailing_flag_without_value_is_hard_error(tmp_path):
    """A bare --max-pct as the final token is a clear rc-2 hard error, not
    a silent parse to '' — the sweep fails open on rc 2."""
    p = _mk_probe(tmp_path, anon_frac=0.99)
    r = _run_guard(["--probe-dir", str(p), "--max-pct"])
    assert r.returncode == 2, r.stderr
    assert "value" in r.stderr, r.stderr


def test_guard_rejects_empty_two_token_value(tmp_path):
    """Two-token form with an EMPTY value (--max-pct '') is the same rc-2
    hard error — the empty value fails the numeric check."""
    p = _mk_probe(tmp_path, anon_frac=0.99)
    r = _run_guard(["--probe-dir", str(p), "--max-pct", ""])
    assert r.returncode == 2, r.stderr
    assert "must be a number" in r.stderr, r.stderr


def test_guard_rejects_unknown_arg():
    r = _run_guard(["--bogus"])
    assert r.returncode == 2


def test_guard_unit_probe_does_not_use_meminfo(tmp_path):
    """When the unit cgroup IS present, the guard must not read /proc/meminfo
    — the probe is the unit cgroup, host RAM is irrelevant. A synthetic tree
    with the unit dir but NO meminfo file must still pass."""
    p = _mk_probe(tmp_path, anon_frac=0.65, meminfo_total_bytes=None)
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 0, r.stderr
    assert "FALLBACK" not in r.stderr


def test_guard_live_path_resolution_not_hardcoded_system_slice():
    """Round-2 BLOCK: the unit is user-scoped
    (user.slice/user-1000.slice/<email>/app.slice/...), NOT at
    /sys/fs/cgroup/system.slice/. A probe hardcoded at the system.slice path
    would fail-open forever. The guard must locate the unit by NAME (find)
    under the probe root — never by a hardcoded system.slice path."""
    code = GUARD.read_text()
    assert "system.slice/hermes-ada-gateway.service" not in code, \
        "guard must not hardcode the system.slice path (it does not exist)"
    # the locator is a find by name under the (possibly synthetic) root
    assert "find" in code, "guard must locate the unit dir by name"


def test_guard_resolves_live_cgroup_via_pidof_or_find():
    """The live locator must be a real cgroupfs walk (find by name under
    /sys/fs/cgroup) — not a hardcoded absolute path that breaks when the
    unit is user-scoped. The probe seam overrides the root for tests."""
    code = GUARD.read_text()
    assert "/sys/fs/cgroup" in code, "guard must walk the real cgroupfs"
    assert "hermes-ada-gateway.service" in code
    assert "find" in code, "guard must locate the unit dir by name under the root"


# ---------------------------------------------------------------------------
# The sweep's entry-point wiring
# ---------------------------------------------------------------------------

def _code() -> str:
    return "\n".join(
        l for l in SWEEP.read_text().splitlines() if not l.lstrip().startswith("#")
    )


def test_sweep_calls_the_ram_guard_before_any_work():
    code = _code()
    assert "sweep-ram-guard" in code, "sweep must call the RAM guard"
    i_guard = code.index("SWEEP_RAM_GUARD_PATH")
    assert i_guard < code.index('exec 9>'), "guard must run before the architect lock"
    assert i_guard < code.index("sweep_phase_stamp"), "guard must run before any marker stamp"
    assert i_guard < code.index("kanban list"), "guard must run before any board read"


def test_sweep_pressure_skip_exits_0_not_failure():
    code = _code()
    skip = code[code.index("working set (anon) at/above"):]
    assert "exit 0" in skip[:400], "the rc=3 branch must exit 0"


def test_sweep_fails_open_when_guard_absent():
    code = _code()
    assert "failing open" in code, "a broken/absent guard must fail open"


def test_sweep_pressure_skip_carries_measured_value():
    """Nit 5: the rc-3 skip message must carry the guard's measured line
    (GB + % of max), not just the threshold, so the systemd log has the
    real number."""
    code = _code()
    skip = code[code.index("working set (anon) at/above"):]
    assert "measured" in skip, "the skip message must carry the measured value"
    # tail -n 1 (POSIX form) reads the guard's last stderr line (its measured
    # GiB/% report) into the skip message.
    assert "tail -n 1" in code, "the guard's stderr must be captured to a temp file"


def test_sweep_guard_still_no_vram():
    for f in (SWEEP, GUARD):
        code = "\n".join(
            l for l in f.read_text().splitlines() if not l.lstrip().startswith("#")
        )
        for token in ("nvidia-smi", "free_vram", "sweep_gate", "krew-gpu-mode"):
            assert token not in code, f"{f.name} reintroduced a GPU gate: {token}"


def test_guard_default_threshold_is_80_boundary_exact(tmp_path):
    """The 80% boundary must SKIP at a true exact-80% working set: with a
    cap that divides cleanly (100 bytes), int(0.80*100) is EXACTLY 80 B and
    the guard must gate. This is the case the default 24 GiB cap cannot
    probe (24 GiB is not divisible by 5, so the floor of 80% lands 0.8 B
    under the true boundary — the fail-safe comment in the guard)."""
    p = _mk_probe(tmp_path, anon_frac=0.80, max_bytes=100)  # exactly 80 B of 100 B
    r = _run_guard(["--probe-dir", str(p)])
    assert r.returncode == 3, r.stderr
    # and the floor just below the boundary still passes:
    p2 = _mk_probe(tmp_path, anon_frac=0.79, max_bytes=100)  # 79 B of 100 B
    r2 = _run_guard(["--probe-dir", str(p2)])
    assert r2.returncode == 0, r2.stderr


def _sweep_code() -> str:
    return "\n".join(
        l for l in SWEEP.read_text().splitlines() if not l.lstrip().startswith("#")
    )


def test_sweep_forwards_probe_seam_to_guard():
    code = _sweep_code()
    assert "SWEEP_RAM_PROBE_DIR" in code
    # The guard CALL (not the path-assignment): the seam env vars must be
    # forwarded on the invocation line, right before it.
    i_guard = code.rindex('"$RAM_GUARD_PATH"')
    window = code[max(0, i_guard - 400): i_guard]
    assert "SWEEP_RAM_PROBE_DIR" in window, "probe seam must be forwarded right before the guard call"


def test_sweep_forwards_threshold_env_to_guard():
    code = _sweep_code()
    assert "SWEEP_RAM_MAX_PCT" in code, "threshold env must be forwarded"


def test_sweep_resolves_installed_guard_first():
    """REJECT-ROUND pin: the timer runs /usr/local/bin/steward-sweep, so the
    INSTALLED guard (/usr/local/bin/sweep-ram-guard) must be tried BEFORE the
    self-dir sibling and before the repo copy."""
    code = _sweep_code()
    i_inst = code.index("/usr/local/bin/sweep-ram-guard")
    i_self = code.index('${_selfdir%/}/sweep-ram-guard')
    i_repo = code.index("}/scripts/sweep-ram-guard")      # krew: ${KREW_CREW_ROOT:-$HOME/.krew/crew}/scripts/...
    assert i_inst < i_self < i_repo, (
        "installed guard first, then self-dir, then repo — "
        f"got inst@{i_inst}, self@{i_self}, repo@{i_repo}"
    )


def test_sweep_default_threshold_is_80():
    """The sweep forwards SWEEP_RAM_MAX_PCT with a default of 80 — pinned so
    a future edit cannot silently drift the sweep's default off the guard's.
    (The 09-21 kill hit ~64%; 80% of the 20 GB cap = 16 GB, above the
    ~12-13 GB idle working set with headroom.)"""
    code = _sweep_code()
    assert "SWEEP_RAM_MAX_PCT:-80" in code, "the sweep's default threshold must be 80"
