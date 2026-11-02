#!/usr/bin/env python3
"""hermes-tree — the house's changes to ~/.hermes/hermes-agent as a patch queue.

Why (the User 2026-09-12: "any way to clean the tree? Is there a better way so
they can pull updates?"): `hermes update` resets the checkout to origin/main
(diverged local commits are parked under a rescue ref, dirty edits are
autostashed and re-applied only when they still fit). The two Steward commits
and eight dirty files on the tree today would not survive it, and the two
patch files under config/hermes were already stale against the tree. The
patch queue is the one source of truth: every house change is a patch with a
header (why, card, upstream PR, tests). The tree is origin/main + the queue.

  status    what the tree carries vs the queue (applied / missing / stale /
            uncovered)
  capture   write <queue>/<name>.patch from the tree (the queue: HERMES_PATCH_QUEUE, else krew's patches/)
              --base origin/main   committed + dirty (default)
              --base HEAD          dirty only
            with --why/--card/--pr/--tests headers
  verify    exit 1 unless every patch is applied and nothing is uncovered
            (for the nightly trail)
  update    stop the house timers that call hermes, `hermes update
            --no-gateway-restart`, re-apply the queue (--3way) and run each
            patch's tests (sandboxed), THEN restart the gateway; on any failure
            put back the tree (branch, main), the configs, and hermes's
            completion marker, and start the timers again. Receipt in the
            queue's last-update.json, full output under ~/.hermes/logs.
            --dry-run shows the plan.

A patch retires when its upstream PR merges: `git apply --check` then reports
"already in tree" and `status` says retire it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import difflib
import tempfile
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

TREE = Path(os.environ.get("HERMES_TREE") or str(KH.hermes_tree()))
QUEUE = Path(os.environ.get("HERMES_PATCH_QUEUE", str(Path(__file__).resolve().parents[2] / "patches")))   # krew's own patches/ (krew-patch.py applies it)
HERMES = Path.home() / ".local" / "bin" / "hermes"
HEADER_KEYS = ("why", "card", "pr", "tests", "captured")


def git(*args: str, cwd: Path = TREE, check: bool = False) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# --- patch files -----------------------------------------------------------

def parse_header(text: str) -> dict:
    """`# key: value` lines before the first `diff --git`."""
    out: dict = {}
    for ln in text.splitlines():
        if ln.startswith("diff --git") or ln.startswith("From "):
            break
        m = re.match(r"^#\s*([a-z]+):\s*(.*)$", ln)
        if m and m.group(1) in HEADER_KEYS:
            out[m.group(1)] = m.group(2).strip()
    return out


def patch_files(text: str) -> list[str]:
    return sorted(set(re.findall(r"^diff --git a/(\S+) b/", text, re.M)))


def load_queue(queue: Path = QUEUE) -> list[dict]:
    out = []
    for p in sorted(queue.glob("*.patch")):
        text = p.read_text(encoding="utf-8", errors="replace")
        out.append({"name": p.stem, "path": p, "header": parse_header(text), "files": patch_files(text)})
    return out


def patch_state(p: Path, tree: Path = TREE) -> str:
    """applied | missing | stale.

    A committed patch is checked against the index (HEAD) as well as the
    working tree: a later dirty patch on the same file makes the working-tree
    reverse-apply fail while the commits are plainly there."""
    if git("apply", "--check", "-R", str(p), cwd=tree)[0] == 0:
        return "applied"
    if git("apply", "--check", "-R", "--cached", str(p), cwd=tree)[0] == 0:
        return "applied"
    if git("apply", "--check", str(p), cwd=tree)[0] == 0:
        return "missing"
    return "stale"


# --- status / verify --------------------------------------------------------

def tree_facts(tree: Path = TREE) -> dict:
    _, ahead = git("rev-list", "--count", "origin/main..HEAD", cwd=tree)
    _, behind = git("rev-list", "--count", "HEAD..origin/main", cwd=tree)
    _, dirty = git("diff", "--name-only", cwd=tree)
    _, committed = git("diff", "--name-only", "origin/main...HEAD", cwd=tree)
    _, head = git("rev-parse", "--short", "HEAD", cwd=tree)
    return {
        "head": head.strip(), "ahead": int((ahead.strip() or "0")), "behind": int((behind.strip() or "0")),
        "dirty": sorted(x for x in dirty.splitlines() if x.strip()),
        "committed": sorted(x for x in committed.splitlines() if x.strip()),
    }


def beyond_patches(tree: Path = TREE, queue: Path = QUEUE, facts: dict | None = None) -> dict[str, str]:
    """Dirty files a patch covers whose working-tree bytes are NOT `HEAD + the
    queue` — {file: why}. The file-level "uncovered" check cannot see this:
    2026-09-17 22:36 a bash worker (t_c7f12942) edited hermes_cli/kanban_db.py
    and gateway/kanban_watchers.py in place, both under applied patches, and
    `status` read all green. Rebuilds each file from HEAD in a scratch dir,
    applies the patches that are not already committed there, and diffs."""
    facts = facts or tree_facts(tree)
    rows = load_queue(queue)
    out: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="hermes-tree-") as td:
        tmp = Path(td)
        for f in sorted(set(facts["dirty"])):
            touching = [q for q in rows if f in q["files"]]
            if not touching:
                continue                                  # `uncovered` names it
            r = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=str(tree), capture_output=True)
            if r.returncode != 0:
                continue                                  # not in HEAD: a new file, the queue's business
            dest = tmp / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.stdout)
            stale = ""
            for q in touching:
                if git("apply", "--check", "-R", "--cached", str(q["path"]), cwd=tree)[0] == 0:
                    continue                              # already committed on the tree
                a = subprocess.run(["git", "apply", f"--include={f}", str(q["path"])], cwd=str(tmp),
                                   capture_output=True, text=True)
                if a.returncode != 0:
                    stale = f"patch {q['name']} does not apply on HEAD"
                    break
            if stale:
                out[f] = stale
                continue
            live = (tree / f).read_bytes() if (tree / f).exists() else b""
            if live != dest.read_bytes():
                want = dest.read_text(errors="replace").splitlines()
                have = live.decode(errors="replace").splitlines()
                n = sum(1 for l in difflib.unified_diff(want, have, lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---"))
                out[f] = f"{n} line(s) differ from HEAD + patches"
    return out


def stack_check(tree: Path = TREE, queue: Path = QUEUE) -> dict:
    """Does the WHOLE queue, reversed newest-first from the live tree, land
    exactly on the tree's upstream base (merge-base HEAD origin/main)?

    Why (2026-09-19): overlapping patches (10, 60, 70, 80, 90, 95 all touch
    hermes_cli/kanban_db.py) cannot each reverse-apply alone once a later one
    rewrites their context, so `patch_state` read 10 and 80 "stale" and
    `update` refused — while the queue, applied in order on the base, rebuilt
    the tree to the byte. Runs in a throwaway index (GIT_INDEX_FILE): the
    tree's own index and files are never touched.
    Kill: HERMES_TREE_STACK=0 (status goes back to per-patch only)."""
    if os.environ.get("HERMES_TREE_STACK", "1").strip() == "0":
        return {"ok": False, "why": "off"}
    rows = load_queue(queue)
    rc, mb = git("merge-base", "HEAD", "origin/main", cwd=tree)
    mb = mb.strip()
    if rc != 0 or not mb:
        return {"ok": False, "why": "no merge-base"}
    with tempfile.TemporaryDirectory(prefix="hermes-tree-stack-") as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))

        def run(*a: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *a], cwd=str(tree), env=env, capture_output=True, text=True)

        if run("read-tree", "HEAD").returncode != 0:
            return {"ok": False, "why": "read-tree HEAD failed"}
        files = sorted({f for q in rows for f in q["files"]})
        run("add", "-A", "--", *[f for f in files if (tree / f).exists()])
        for q in reversed(rows):
            r = run("apply", "--cached", "-R", str(q["path"]))
            if r.returncode != 0:
                return {"ok": False, "broke_at": q["name"], "why": (r.stderr or r.stdout).strip()}
        top = run("write-tree").stdout.strip()
        differs = [x for x in run("diff", "--name-only", top, mb, "--", *files).stdout.split() if x]
    return {"ok": not differs, "base": mb[:10], "differs": differs}


def status(tree: Path = TREE, queue: Path = QUEUE) -> dict:
    facts = tree_facts(tree)
    rows = []
    covered: set[str] = set()
    for q in load_queue(queue):
        st = patch_state(q["path"], tree)
        covered.update(q["files"])
        rows.append({"name": q["name"], "state": st, "files": q["files"], **{k: q["header"].get(k, "") for k in HEADER_KEYS}})
    local = set(facts["dirty"]) | set(facts["committed"])
    uncovered = sorted(local - covered)
    beyond = beyond_patches(tree, queue, facts)
    stack = stack_check(tree, queue)
    if stack.get("ok"):
        # The queue rebuilds the tree from its base to the byte: a patch that
        # only reads stale alone is applied in the stack, and nothing under a
        # patch is beyond it.
        for r in rows:
            if r["state"] == "stale":
                r["state"] = "applied"
                r["stacked"] = True
        beyond = {}
    missing = [] if queue.is_dir() else [f"no patch queue at {queue} (HERMES_PATCH_QUEUE names it; a krew checkout "
                                          "keeps it at patches/)"]
    return {"tree": facts, "patches": rows, "uncovered": uncovered, "beyond": beyond, "stack": stack,
            "missing": missing}


def print_status(s: dict) -> None:
    t = s["tree"]
    print(f"tree {t['head']}: {t['ahead']} local commit(s), {t['behind']} behind origin/main, "
          f"{len(t['dirty'])} dirty file(s)")
    for m in s.get("missing") or []:
        print(f"  MISSING: {m}")
    for r in s["patches"]:
        pr = f" pr={r['pr']}" if r.get("pr") else ""
        card = f" card={r['card']}" if r.get("card") else ""
        stk = " (in the stack)" if r.get("stacked") else ""
        print(f"  [{r['state']:7}] {r['name']}{card}{pr}{stk} — {', '.join(r['files'])}")
    st = s.get("stack") or {}
    if st.get("ok"):
        print(f"  STACK: clean — base {st['base']} + the queue in order == the tree, to the byte")
    elif st.get("why") != "off":
        where = f" at {st['broke_at']}" if st.get("broke_at") else ""
        print(f"  STACK: broken{where} — {st.get('why') or 'differs: ' + ', '.join(st.get('differs') or [])}")
    if s["uncovered"]:
        print("  UNCOVERED (in the tree, in no patch — capture or drop): " + ", ".join(s["uncovered"]))
    for f, why in sorted((s.get("beyond") or {}).items()):
        print(f"  BEYOND PATCHES (edited in place under a patch — capture or drop): {f}: {why}")


def verify(tree: Path = TREE, queue: Path = QUEUE) -> int:
    s = status(tree, queue)
    print_status(s)
    bad = [r["name"] for r in s["patches"] if r["state"] != "applied"]
    beyond = sorted(s.get("beyond") or {})
    missing = s.get("missing") or []
    if bad or s["uncovered"] or beyond or missing:
        print(f"VERIFY RED: not applied={bad} uncovered={s['uncovered']} beyond={beyond}"
              + (f" missing={missing}" if missing else ""))
        return 1
    print("VERIFY GREEN: tree == origin/main + queue")
    return 0


# --- capture ----------------------------------------------------------------

def capture(name: str, paths: list[str], *, base: str = "origin/main", why: str = "", card: str = "",
            pr: str = "", tests: str = "", tree: Path = TREE, queue: Path = QUEUE) -> Path:
    if ".." in base:                      # A..B = committed range only, from the merge-base
        a, b = base.split("..", 1)
        mb = git("merge-base", a, b, cwd=tree)[1].strip() or a
        rc, diff = git("diff", mb, b, "--", *paths, cwd=tree)
    elif base == "HEAD":                  # working-tree edits only
        rc, diff = git("diff", "HEAD", "--", *paths, cwd=tree)
    else:                                 # committed + dirty vs base
        rc, diff = git("diff", f"{base}", "--", *paths, cwd=tree)
    if rc != 0:
        raise RuntimeError(cut(diff, 300, "git diff in the hermes tree shows the rest"))
    if not diff.strip():
        raise RuntimeError(f"nothing to capture for {name}: {paths}")
    hdr = [f"# why: {why}", f"# card: {card}", f"# pr: {pr}", f"# tests: {tests}",
           f"# captured: {time.strftime('%Y-%m-%d')} from {base} — {' '.join(paths)}", ""]
    queue.mkdir(parents=True, exist_ok=True)
    out = queue / f"{name}.patch"
    out.write_text("\n".join(hdr) + diff, encoding="utf-8")
    return out


# --- update -----------------------------------------------------------------

SANDBOX = Path(__file__).resolve().parent / "kanban-env-clean"
# A name, so a test of update() can point it away: the real script deep-merges the packs over the LIVE
# configs and bounces gateways (only its off switch kept the test suite from doing that, 09-30).
KIT_REAPPLY = Path(__file__).resolve().parent / "krew-kit-reapply.sh"


def live_runtime_python() -> str | None:
    """The interpreter the hermes launcher runs (lib/hermes_runtime), or None."""
    try:
        lib = str(Path(__file__).resolve().parent.parent / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import hermes_runtime
        return hermes_runtime.runtime_python() if hermes_runtime.home_is_the_install() else None
    except Exception:  # noqa: BLE001
        return None


def run_tests(spec: str, tree: Path = TREE) -> int:
    """A patch's tests, in the kanban-env-clean sandbox (own HOME): hermes kanban tests that
    reach the live board dispatched real cards with a fake pid on 09-27. Kill: HERMES_TREE_TESTS_SANDBOX=0."""
    if not spec.strip():
        return 0
    # THE LIVE ENVIRONMENT, NEVER `hermes-agent/venv` (the User 2026-09-30): that venv was retired by the
    # 09-26 hermes (python3.11, no ruamel) and a patch test that passed there proved nothing about the
    # code the launcher runs. The launcher's interpreter + hermes_bootstrap is the runtime; when it has no
    # pytest the run FAILS and says so (HERMES_TREE_TEST_PY names another interpreter on purpose).
    override = os.environ.get("HERMES_TREE_TEST_PY", "").strip()
    if override:
        cmd = [override, "-m", "pytest", "-q", "-p", "no:cacheprovider", *spec.split()]
    else:
        py = live_runtime_python()
        if not py:
            print("patch tests: the hermes launcher names no interpreter - cannot run in the live environment")
            return 3
        boot = ("import sys; sys.path.insert(0, %r); import hermes_bootstrap\n"
                "try:\n    import pytest\nexcept ImportError:\n"
                "    print('patch tests: no pytest in the live hermes environment'); sys.exit(3)\n"
                "sys.exit(pytest.main(sys.argv[1:]))" % str(tree))
        cmd = [py, "-I", "-c", boot, "-q", "-p", "no:cacheprovider", *spec.split()]
    if SANDBOX.is_file() and os.environ.get("HERMES_TREE_TESTS_SANDBOX", "1") != "0":
        cmd = [str(SANDBOX), *cmd]
    r = subprocess.run(cmd, cwd=str(tree), capture_output=True, text=True)
    print((r.stdout or "").strip().splitlines()[-1] if r.stdout else r.stderr[-200:])
    return r.returncode


# --- the house around an update (2026-09-28) ----------------------------------
# The first try on 09-28 04:36 failed in hermes's own completion step: once `hermes update`
# arms its `source-completion-pending` marker, EVERY `hermes` launch "finishes" the update
# (build the products, migrate configs), so the house's own timers and Crissa's hands started
# competing builds ("Build inputs changed during compilation"). The rollback then left that
# marker (every later `hermes` call kept rebuilding), a detached HEAD, `main` fast-forwarded,
# and one profile config migrated for code that was no longer there. Kill: HERMES_TREE_QUIET=0.

#: House timers whose services call `hermes`; stopped for the update, started again after.
QUIET_TIMERS = (
    "krew-hold-sweep.timer", "krew-kanban-loop.timer", "krew-kanban-chatter.timer",
    "krew-window-tick.timer", "krew-restart-window.timer", "krew-render-window.timer",
    "krew-render-collect.timer", "krew-janitor.timer", "krew-janitor-weekly.timer",
    "ms-survey.timer",
)


def _systemctl(*args: str) -> tuple[int, str]:
    r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip()


def quiet_house(timers=QUIET_TIMERS, *, run=_systemctl, wait_s: float = 300, sleep=time.sleep) -> list[str]:
    """Stop every ACTIVE timer in *timers*, then wait (bounded) for their services to finish a
    run already in flight. Returns the timers it stopped - exactly what `wake_house` restarts."""
    if os.environ.get("HERMES_TREE_QUIET", "1") == "0":
        return []
    stopped = []
    for t in timers:
        if run("is-active", t)[1] == "active" and run("stop", t)[0] == 0:
            stopped.append(t)
    services = [t[:-len(".timer")] + ".service" for t in stopped]
    deadline = time.time() + wait_s
    while services and time.time() < deadline:
        services = [s for s in services if run("is-active", s)[1] in ("active", "activating", "deactivating")]
        if services:
            sleep(2)
    if services:
        print(f"quiet: still running after {int(wait_s)} s: {services} (continuing)")
    print(f"quiet: {len(stopped)} house timer(s) stopped")
    return stopped


def wake_house(stopped: list[str], *, run=_systemctl) -> list[str]:
    """Start again what `quiet_house` stopped; returns any that failed to start."""
    failed = [t for t in stopped if run("start", t)[0] != 0]
    if stopped:
        print(f"quiet: {len(stopped) - len(failed)} house timer(s) started again" + (f", FAILED {failed}" if failed else ""))
    return failed


#: Restarted by THIS script once the queue is back on the new code (`hermes update` runs with
#: --no-gateway-restart): hermes's own restart came BEFORE the re-apply, so the gateway ran the
#: new code without the house patches until its next restart. Plain systemctl, never
#: `hermes gateway restart` - that "auto-refreshes the unit", and the host unit carries the
#: house's drop-ins (cutover checkpoint: never gateway install|migrate).
FLEET_UNITS = ("hermes-gateway.service", "hermes-dashboard.service")


def restart_fleet(units=FLEET_UNITS, *, run=_systemctl, wait_s: float = 120, sleep=time.sleep) -> bool:
    """Restart the fleet; True when the gateway (first unit) is active on a NEW main pid."""
    if not units:
        return True
    gw = units[0]
    old = run("show", gw, "-p", "MainPID", "--value")[1]
    run("restart", gw)
    for u in units[1:]:
        run("restart", u)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        pid = run("show", gw, "-p", "MainPID", "--value")[1]
        if run("is-active", gw)[1] == "active" and pid not in ("", "0", old):
            print(f"fleet: {gw} back on pid {pid}")
            return True
        sleep(3)
    print(f"fleet: {gw} did not come back active on a new pid within {int(wait_s)} s")
    return False


def completion_marker(hermes: Path = HERMES) -> Path | None:
    """hermes's `source-completion-pending` marker for this install (`hermes update --install-id`)."""
    try:
        r = subprocess.run([str(hermes), "update", "--install-id"], capture_output=True, text=True, timeout=60)
        iid = (r.stdout or "").strip().splitlines()[0].strip()
    except (OSError, IndexError, subprocess.SubprocessError):
        return None
    if not re.fullmatch(r"[0-9a-f]{8,64}", iid):
        return None
    return KH.hermes_home() / "installs" / iid / "source-completion-pending"


def config_paths(home: Path | None = None) -> list[Path]:
    home = home or KH.hermes_home()
    return [p for p in [home / "config.yaml", *sorted((home / "profiles").glob("*/config.yaml"))] if p.is_file()]


def snapshot_configs(paths: list[Path]) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in paths}


def restore_configs(snap: dict[Path, bytes]) -> list[Path]:
    """Put back, byte for byte and by atomic replace, every snapshotted config that changed."""
    restored = []
    for p, data in snap.items():
        try:
            if p.is_file() and p.read_bytes() == data:
                continue
            mode = p.stat().st_mode & 0o777 if p.exists() else 0o600
            tmp = p.with_name(f".{p.name}.hermes-tree-restore")
            tmp.write_bytes(data)
            os.chmod(tmp, mode)
            os.replace(tmp, p)
            restored.append(p)
        except OSError as e:
            print(f"rollback: could not restore {p}: {e}")
    return restored


def rollback(tree: Path, pre: str, pre_branch: str, pre_main: str, *, configs: dict[Path, bytes],
             marker: Path | None, marker_existed: bool) -> dict:
    """Return the tree AND what hermes changed around it to the pre-update state."""
    git("reset", "-q", "--hard", cwd=tree)
    git("checkout", "-q", "-f", pre, cwd=tree)
    if pre_main and pre_branch != "main":
        git("update-ref", "refs/heads/main", pre_main, cwd=tree)
    if pre_branch:
        git("update-ref", f"refs/heads/{pre_branch}", pre, cwd=tree)
        git("checkout", "-q", pre_branch, cwd=tree)
    git("stash", "pop", cwd=tree)
    restored = restore_configs(configs)
    cleared = False
    if marker is not None and not marker_existed and marker.exists():
        marker.unlink()
        cleared = True
    out = {"head": git("rev-parse", "HEAD", cwd=tree)[1].strip(), "branch": pre_branch or None,
           "configs_restored": [str(p) for p in restored], "marker_cleared": cleared}
    print(f"rollback: tree {pre[:10]}{' on ' + pre_branch if pre_branch else ''}; "
          f"{len(restored)} config(s) restored; completion marker {'cleared' if cleared else 'untouched'}")
    return out


def update(*, dry_run: bool = False, tree: Path = TREE, queue: Path = QUEUE, hermes: Path = HERMES,
           home: Path | None = None, marker: Path | None = ..., log_dir: Path | None = None,
           timers=QUIET_TIMERS, fleet=FLEET_UNITS, run=_systemctl) -> int:
    s = status(tree, queue)
    print_status(s)
    if s["uncovered"]:
        print("refusing: uncovered local changes would be lost — capture them first")
        return 2
    pre = git("rev-parse", "HEAD", cwd=tree)[1].strip()
    stale = [r["name"] for r in s["patches"] if r["state"] == "stale"]
    if stale:
        print(f"refusing: stale patch(es) {stale} — re-capture from the tree first")
        return 2
    plan = [f"pre={pre[:10]}", f"hermes update", *[f"apply {r['name']} + tests({r['tests'] or '-'})" for r in s["patches"]]]
    print("plan: " + " → ".join(plan))
    if dry_run:
        return 0
    pre_branch = git("symbolic-ref", "-q", "--short", "HEAD", cwd=tree)[1].strip()
    rc_main, pre_main = git("rev-parse", "-q", "--verify", "refs/heads/main", cwd=tree)
    pre_main = pre_main.strip() if rc_main == 0 else ""
    if marker is ...:
        marker = completion_marker(hermes)
    marker_existed = bool(marker and marker.exists())
    configs = snapshot_configs(config_paths(home))
    log_dir = log_dir or KH.hermes_home() / "logs"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    receipt = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pre": pre, "pre_branch": pre_branch or None,
               "post": None, "applied": [], "failed": None, "rollback": None,
               "log": str(log_dir / f"hermes-tree-update-{stamp}.log")}

    def write_receipt() -> None:
        (queue / "last-update.json").write_text(json.dumps(receipt, indent=2) + "\n")

    stopped = quiet_house(timers, run=run)
    try:
        # the queue is the truth: drop local commits + edits, let update run clean
        git("stash", "push", "--include-untracked", "-m", f"hermes-tree pre-update {pre[:10]}", cwd=tree)
        r = subprocess.run([str(hermes), "update", "--yes", "--no-gateway-restart"], capture_output=True, text=True)
        text = (r.stdout or "") + (r.stderr or "")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            Path(receipt["log"]).write_text(text)
        except OSError:
            receipt["log"] = None
        print(text.strip().splitlines()[-1] if text.strip() else "hermes update: no output")
        if r.returncode != 0:
            print(f"hermes update rc={r.returncode}; restoring {pre[:10]} (full output: {receipt['log']})")
            receipt["failed"] = {"name": "hermes update", "rc": r.returncode, "out": text[-400:]}
            receipt["rollback"] = rollback(tree, pre, pre_branch, pre_main, configs=configs,
                                           marker=marker, marker_existed=marker_existed)
            write_receipt()
            return 3
        post = git("rev-parse", "HEAD", cwd=tree)[1].strip()
        receipt["post"] = post
        for q in load_queue(queue):
            if patch_state(q["path"], tree) == "applied":
                receipt["applied"].append({"name": q["name"], "note": "already upstream — retire"}); continue
            rc, out = git("apply", "--3way", str(q["path"]), cwd=tree)
            if rc != 0 or run_tests(q["header"].get("tests", ""), tree) != 0:
                receipt["failed"] = {"name": q["name"], "out": out[-400:]}
                print(f"patch {q['name']} failed on {post[:10]}: {out.strip()}")
                receipt["rollback"] = rollback(tree, pre, pre_branch, pre_main, configs=configs,
                                               marker=marker, marker_existed=marker_existed)
                print(f"returned to {pre[:10]} with the tree as it was")
                break
            receipt["applied"].append({"name": q["name"]})
        write_receipt()
        if receipt["failed"]:
            return 4
        # The update rebuilt hermes's environment from upstream's lock; put the house's minimum versions
        # back BEFORE the gateway starts on it (lib/hermes_env_pins, config/hermes/env-pins.json).
        receipt["env_pins"] = env_pins_after_update()
        write_receipt()
        if not restart_fleet(fleet, run=run):
            receipt["failed"] = {"name": "gateway restart", "out": "not active on a new pid"}
            receipt["rollback"] = rollback(tree, pre, pre_branch, pre_main, configs=configs,
                                           marker=marker, marker_existed=marker_existed)
            receipt["fleet_after_rollback"] = restart_fleet(fleet, run=run)
            write_receipt()
            return 5
        # 2026-10-01: the 09-27 update made computer_use read "unavailable" (upstream 5e4a2a3d24:
        # the driver must be hermes-packaged or named by HERMES_CUA_DRIVER_CMD) and it dropped out
        # of every hands kit with no error, until the check caught it 10-01. Run the computer-use
        # doctor + the tool tax now the fleet is up; report-only, never fails the update.
        # Kill: KREW_CUA_DOCTOR=0.
        receipt["cua_doctor"] = cua_doctor_after_update()
        write_receipt()
        git("stash", "drop", cwd=tree)
        receipt["fleet"] = "restarted"
        # the User 09-28: the house's gate suites run BEFORE the next patrol tick (the timers are
        # still stopped here); a gate whose suite fails runs report-only for 24 h (lib/gate_hold).
        receipt["gate_check"] = post_update_gate_check(post)
        write_receipt()
        # the User 10-02: the update's bundled-skill sync copies skill FILES into every profile (09-26:
        # hermes-agent into judge, whose allowlist keeps nothing bundled) and changes no config, so no
        # restore re-applies the kit - and krew-kit-reapply below may be switched off. Disable here
        # whatever the allowlist does not keep. Additive; kill KREW_KIT_DISABLE_UNLISTED=0.
        receipt["skills_unlisted"] = disable_unlisted_after_update()
        write_receipt()
    finally:
        wake_house(stopped, run=run)
    print(f"updated {pre[:10]} → {post[:10]} with {len(receipt['applied'])} patch(es)")
    # The code is back; now the kit (the User 2026-09-16: "our old kits re-written,
    # configs catch the new features"). krew-kit-reapply.sh deep-merges the
    # pack YAML over live (upstream-only keys kept), deploys house skills,
    # re-pins kanban_lean, verifies this queue and bounces only the gateways
    # whose YAML changed. The .path unit fires too; the script's flock makes
    # the second run a one-line no-op.
    kit = KIT_REAPPLY
    if kit.is_file():
        k = subprocess.run(["bash", str(kit), f"--reason=hermes-tree-update-{post[:10]}"],
                           capture_output=True, text=True)
        tail = ((k.stdout or "") + (k.stderr or "")).strip().splitlines()[-3:]
        print("kit reapply: " + (" | ".join(t.split("kit-reapply", 1)[-1].strip("[]: ") for t in tail) or "no output"))
        receipt["kit_reapply_rc"] = k.returncode
        (queue / "last-update.json").write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["krew_index"] = index_after_update(tree, pre, post)
    (queue / "last-update.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return 0


def index_after_update(tree: Path, pre: str, post: str) -> dict:
    """The User 09-28: "update her memory with new docs when we upgrade". Two halves:
    the hermes docs in krew-index are REPLACED (the build re-reads the live tree and
    prunes what the update removed, so the index is the version the dashboard serves),
    and one ADDENDUM is kept per update - the commit subjects pre..post - so "what changed
    in the last upgrade" has an answer. The build runs detached under the house CPU quota
    (embedding ~450 pages is minutes of CPU); it never fails the update.
    Kill: KREW_INDEX_ON_UPDATE=0."""
    if os.environ.get("KREW_INDEX_ON_UPDATE", "1") == "0":
        return {"off": True}
    out: dict = {}
    try:
        db = Path(os.environ.get("KREW_INDEX_DB") or (KH.hermes_home() / "krew-index" / "index.db"))
        notes = db.parent / "hermes-changes"
        notes.mkdir(parents=True, exist_ok=True)
        log = git("log", "--no-merges", "--date=short", "--format=- %h %ad %s", f"{pre}..{post}",
                  cwd=tree, check=True)[1].splitlines()
        docs = git("diff", "--stat=200", pre, post, "--", "website/docs", "docs", cwd=tree)[1].strip()
        note = notes / f"{time.strftime('%Y-%m-%d')}-{pre[:10]}-{post[:10]}.md"
        cap = 3000
        note.write_text(
            f"# Hermes update {pre[:10]} -> {post[:10]} ({time.strftime('%Y-%m-%d %H:%M')})\n\n"
            f"What changed in hermes-agent between the two versions, commit by commit "
            f"({len(log)} commits{f', first {cap} shown' if len(log) > cap else ''}). "
            f"The current docs are in the index as source `hermes`; this note is the diff.\n\n"
            f"## Commits\n\n" + "\n".join(log[:cap]) + "\n\n"
            f"## Docs pages changed\n\n```\n{docs or '(none)'}\n```\n")
        out["note"] = str(note)
        script = Path(__file__).resolve().parent / "krew-index.py"
        # The index's OWN venv (its embedder's interpreter), never sys.executable: from a
        # session shell that was ~/.krew/envs/vllm, a live service env (09-28).
        venv = Path(os.environ.get("KREW_INDEX_VENV") or (Path.home() / ".local" / "share" / "krew-index" / "venv"))
        py = venv / "bin" / "python"
        py = str(py) if py.exists() else "/usr/bin/python3"
        r = subprocess.run(["systemd-run", "--user", "--collect", "--quiet",
                            f"--unit=krew-index-after-update-{post[:10]}", "-p", "CPUQuota=300%",
                            py, str(script), "build", "--source", "hermes,hermes-changes"],
                           capture_output=True, text=True, timeout=30)
        out["build"] = "started" if r.returncode == 0 else f"rc={r.returncode} {(r.stderr or '').strip()}"
    except Exception as exc:  # the tree is already good; an index is never a reason to fail
        out["error"] = f"{type(exc).__name__}: {exc}"
    print(f"krew-index: {out}")
    return out


def env_pins_after_update() -> dict:
    """lib/hermes_env_pins.apply; never fails the update (a pin that would not install is in the receipt
    and the board gate tries again). Kill: KREW_HERMES_ENV_PINS=0."""
    try:
        lib = str(Path(__file__).resolve().parent.parent / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import hermes_env_pins
        st = hermes_env_pins.apply(by="hermes-tree update")
    except Exception as e:  # noqa: BLE001
        print(f"env pins: {type(e).__name__}: {e}")
        return {"error": str(e)}
    out = {k: st.get(k) for k in ("off", "error", "versions", "applied", "restart_owed") if st.get(k)}
    out["below"] = [f"{r['dist']} {r.get('have')} < {r['min']}" for r in st.get("below") or []]
    print("env pins: " + (", ".join(f"{a['dist']} {a['was']} -> {a.get('now')}" for a in st.get("applied") or [])
                          or ("off" if st.get("off") else st.get("error") or "all met")))
    return out


DISABLE_UNLISTED = Path(__file__).resolve().parent / "krew-kit-disable-unlisted.py"


def disable_unlisted_after_update() -> dict:
    """scripts/krew-kit-disable-unlisted.py after an update: every crew profile's on-disk skill its allowlist
    does not keep goes into its skills.disabled (additive). Never fails the update."""
    try:
        r = subprocess.run([sys.executable, str(DISABLE_UNLISTED)], capture_output=True, text=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        print(f"skills unlisted: {type(e).__name__}: {e}")
        return {"error": str(e)}
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("disable-unlisted:")]
    for ln in lines:
        print(ln)
    return {"rc": r.returncode, "lines": lines[:20],
            **({"more": f"{len(lines) - 20} more line(s): the update's output has them all"} if len(lines) > 20 else {})}


def cua_doctor_after_update() -> dict:
    """lib/cua_doctor: the computer-use doctor + the tool tax, run after an update.
    Report-only — never fails the update. Fail loud (House Daily row + one blocked
    card a day) if computer_use is unavailable, so the next update never lets
    Crissa lose her hands silently. Kill: KREW_CUA_DOCTOR=0.
    2026-10-01: the 09-27 update made computer_use read 'unavailable' (upstream
    5e4a2a3d24: the driver must be hermes-packaged or named by
    HERMES_CUA_DRIVER_CMD) and it dropped from every hands kit with no error."""
    try:
        lib = str(Path(__file__).resolve().parent.parent / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import cua_doctor
        out = cua_doctor.check()
    except Exception as e:  # noqa: BLE001 — report-only by contract
        print(f"cua doctor: {type(e).__name__}: {e}")
        return {"error": str(e)}
    if out.get("ok"):
        print("cua doctor: computer_use available")
        return {"ok": True}
    why = "; ".join(out.get("why") or ["?"])
    print(f"cua doctor: FAIL — {why}")
    try:
        alarm = cua_doctor.fail_loud(out, by="hermes-tree update")
        out["alarm"] = alarm
    except Exception as e:  # noqa: BLE001 — the alarm must not crash the update either
        print(f"cua doctor: alarm failed: {type(e).__name__}: {e}")
        out["alarm"] = {"error": str(e)}
    return out


def post_update_gate_check(label: str) -> dict:
    """lib/gate_hold.check on this repo; never fails the update (the tree is already good)."""
    repo = Path(__file__).resolve().parent.parent
    try:
        sys.path.insert(0, str(repo / "lib"))
        import gate_hold
        res = gate_hold.check(repo, label=label[:10])
    except Exception as exc:
        res = {"_error": f"{type(exc).__name__}: {exc}"}
    for gate, verdict in res.items():
        print(f"gate check: {gate} {verdict}")
    if str(res.get("decomposer", "")).startswith("FAIL"):
        print("gate check: the decomposer has no report mode - set kanban.auto_decompose: false by hand")
    return res


def crew_refusal(cmd: str, dry_run: bool = False) -> str:
    """A kanban worker never moves or re-captures the hermes tree (2026-09-27).

    09-26 18:05 a steward worker ran `update` from a card: it replaced the launcher and the
    dependency environment, migrated every config and restarted the gateways onto a hermes that
    refused them; a second worker then re-captured the queue from a dirty tree. The tree is the
    dispatcher itself, so moving it is the User's window job. Reads (`status`, `verify`,
    `update --dry-run`) stay free. T0 refuses the same acts; this holds when a worker reaches
    the script some other way. Kill: KREW_HERMES_TREE_CREW_GUARD=0.
    """
    if os.environ.get("KREW_HERMES_TREE_CREW_GUARD", "1") == "0":
        return ""
    if not (os.environ.get("HERMES_KANBAN_TASK") or "").strip():
        return ""
    if cmd == "capture" or (cmd == "update" and not dry_run):
        return (f"hermes-tree {cmd}: refused from a kanban worker ({os.environ['HERMES_KANBAN_TASK']}). "
                "The hermes tree is the dispatcher; an update or a re-capture is the User's window job. "
                "Block this card with --kind needs_input and say what you measured.")
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("verify")
    c = sub.add_parser("capture"); c.add_argument("name"); c.add_argument("paths", nargs="+")
    c.add_argument("--base", default="origin/main"); c.add_argument("--why", default=""); c.add_argument("--card", default="")
    c.add_argument("--pr", default=""); c.add_argument("--tests", default="")
    u = sub.add_parser("update"); u.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    no = crew_refusal(a.cmd, bool(getattr(a, "dry_run", False)))
    if no:
        print(no, file=sys.stderr)
        return 5
    if a.cmd == "status":
        print_status(status()); return 0
    if a.cmd == "verify":
        return verify()
    if a.cmd == "capture":
        p = capture(a.name, a.paths, base=a.base, why=a.why, card=a.card, pr=a.pr, tests=a.tests)
        print(f"captured {p} ({len(patch_files(p.read_text()))} file(s))"); return 0
    if a.cmd == "update":
        return update(dry_run=a.dry_run)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
