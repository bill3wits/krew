#!/usr/bin/env python3
"""prodgate: the adversarial-review gate for production writes (krew security rails).

Bash (the coding profile) NEVER edits production files in place. It STAGES copies here,
a reviewer adversarially reviews the staged diff, and only an APPROVED gate can be
APPLIED to the live system — with automatic backups and an audit trail.

Flow (one gate per kanban task; gate-id = the kanban task id, e.g. t_ab12cd34):
  prodgate stage   <gate-id> <prod-path>...   snapshot live files into the gate (new files OK)
  prodgate path    <gate-id> <prod-path>      print the staged copy's path (edit THAT file)
  prodgate diff    <gate-id>                  unified diff: live -> staged
  prodgate approve <gate-id> --by <name> [--note TEXT]
  prodgate reject  <gate-id> --by <name> --reason TEXT
  prodgate apply   <gate-id>                  land it (refuses without fresh approval)
  prodgate status  <gate-id>                  one-screen state of the gate
  prodgate list                               all gates
  prodgate preflight <gate-id>                run pre-flight checklist (auto-checks, prints report)
  prodgate verify-staged <gate-id>            run verify_staged.py sweep on staged files
  prodgate check <gate-id> [--strict]          run Vera's pre-flight checklist (FAILS handoff if unchecked)

Safety properties:
  * apply REFUSES unless APPROVED.json exists and is NEWER than every staged edit
    (any post-approval tinkering voids the approval).
  * apply REFUSES if a live target changed since staging (stale stage -> re-stage).
  * stage REFUSES if a live target changed since first stage — builder must revert
    live, re-stage, and edit ONLY the staged copy (never the live file).
  * every applied target is backed up first (gate backup/ + <file>.bak-<ts> beside it).
  * everything is plain files under the gate store (PRODGATE_ROOT, default $KREW_HOME/gate/<id>/), auditable after
    the fact. The checklist is PRODGATE_CHECKLIST (default prodgate-checklist.md beside this file); the optional
    static sweep is PRODGATE_VERIFY (a verify_staged.py of your own; absent = the step says SKIP / MANUAL).
"""
import argparse, difflib, hashlib, json, os, shutil, stat, subprocess, sys, time
from pathlib import Path

# The account's REAL home, never $HOME: kanban workers run with a sandboxed HOME
# (~/.hermes/profiles/<p>/home), and a home-relative root gave each profile its
# own private (empty) gate store. One machine, one gate store.
import pwd as _pwd
_REAL_HOME = os.environ.get("KREW_REAL_HOME") or _pwd.getpwuid(os.getuid()).pw_dir
_KH = os.environ.get("KREW_HOME") or "~/.krew"
_KREW_HOME = (_REAL_HOME + _KH[1:]) if _KH.startswith("~/") else _KH
GATE_ROOT = os.environ.get("PRODGATE_ROOT") or os.path.join(_KREW_HOME, "gate")

# Staging targets must be PRODUCTION destinations. These prefixes are scratch —
# a builder staging its own artifact path (instead of where it should LAND) is
# the observed failure mode this guard catches.
_NON_PROD_PREFIXES = tuple(x for x in (os.environ.get("PRODGATE_SCRATCH_PREFIXES") or "").split(":") if x) or (
    "/tmp/", os.path.join(_REAL_HOME, ".hermes", "kanban", "workspaces") + "/")


def gdir(gid): return os.path.join(GATE_ROOT, gid)
def wpath(gid, target): return os.path.join(gdir(gid), "work", target.lstrip("/"))
def bpath(gid, target): return os.path.join(gdir(gid), "backup", target.lstrip("/"))
def mpath(gid): return os.path.join(gdir(gid), "manifest.json")
def apath(gid): return os.path.join(gdir(gid), "APPROVED.json")
def rpath(gid): return os.path.join(gdir(gid), "REJECTED.json")
def lpath(gid): return os.path.join(gdir(gid), "LANDED.json")

# Persistent checklist location for `check` subcommand
CHECKLIST_PATH = os.environ.get("PRODGATE_CHECKLIST") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                     "prodgate-checklist.md")
VERIFY_SCRIPT = os.environ.get("PRODGATE_VERIFY") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                  "verify_staged.py")


def _collect_staged_files(gid):
    """Collect Python and shell files from a staged gate.
    
    Uses shebang detection for extensionless files (e.g. 'prodgate' binary)
    so that files without extensions are correctly classified.
    """
    py_files = []
    sh_files = []
    for target, info in load_manifest(gid).get("targets", {}).items():
        wp = wpath(gid, target)
        if not os.path.exists(wp):
            continue
        ext = os.path.splitext(wp)[1]
        if ext == ".py":
            py_files.append(wp)
        elif ext == ".sh":
            sh_files.append(wp)
        elif ext == "" and os.path.isfile(wp):
            # Extensionless file — detect by shebang
            try:
                with open(wp, "rb") as f:
                    first_line = f.readline(256)
                if first_line.startswith(b"#!"):
                    text = first_line.decode("utf-8", errors="ignore").lower()
                    if "python" in text:
                        py_files.append(wp)
                    elif "bash" in text or ("sh" in text and not "python" in text):
                        sh_files.append(wp)
            except (OSError, IOError):
                pass
    return sorted(py_files), sorted(sh_files)


def cmd_check(a):
    """Run Vera's pre-flight checklist against a staged gate."""
    gid = a.gate
    strict = getattr(a, "strict", False)
    m = load_manifest(gid)
    targets = m.get("targets", {})

    if not targets:
        print("FAIL: no targets staged in gate '{}'".format(gid))
        sys.exit(1)

    py_files, sh_files = _collect_staged_files(gid)
    all_issues = []
    manual_items = []
    has_warn = False

    # 1.1: Substantive diff
    print("1.1  Substantive staged diff ... ", end="", flush=True)
    has_substantive = False
    for target in sorted(targets.keys()):
        info = targets[target]
        wp = wpath(gid, target)
        if info["existed"] and os.path.exists(target):
            with open(target, "rb") as f:
                live = f.read().decode("utf-8", errors="replace").splitlines(keepends=True)
        else:
            live = []
        staged = []
        if os.path.exists(wp):
            with open(wp, "rb") as f:
                staged = f.read().decode("utf-8", errors="replace").splitlines(keepends=True)
        d = list(difflib.unified_diff(live, staged, fromfile="live" + target, tofile="staged" + target))
        if d:
            substantive = [l for l in d if l[0] in "+-" and l.strip()]
            if substantive:
                has_substantive = True
                print("PASS ({} changed lines)".format(len(substantive)))
            else:
                print("FAIL (whitespace-only diff)")
                all_issues.append("1.1  Empty/whitespace-only staged diff")
                break
        elif info["existed"] and not os.path.exists(target):
            print("PASS (new file)")
            has_substantive = True
        else:
            print("FAIL (staged == live, no diff)")
            all_issues.append("1.1  Empty/decorative diff (staged == live)")
            break
    if has_substantive and not all_issues:
        print("  PASSED")

    # 1.2: Gate not already landed
    print("1.2  Gate not previously applied ... ", end="", flush=True)
    if os.path.exists(lpath(gid)):
        print("FAIL (gate already LANDED)")
        all_issues.append("1.2  Gate already landed")
    else:
        print("PASS")

    # 1.3: Process check (manual)
    print("1.3  Submit via kanban_request_review (reviewer auto-spawned) ... [MANUAL]")
    manual_items.append("1.3  Submit for review with kanban_request_review(criteria=...); the reviewer is auto-spawned. Do NOT create a review card or kanban_block.")

    # 2.1: Syntax check via verify_staged.py
    print("2.1  Syntax check (py_compile + bash -n) ... ", end="", flush=True)
    verify_script = VERIFY_SCRIPT
    syntax_ok = True
    if py_files:
        try:
            result = subprocess.run(
                [sys.executable, verify_script, gid] + py_files,
                capture_output=True, text=True, timeout=60,
            )
            out = result.stdout + result.stderr
            if result.returncode != 0 or "[ERROR]" in out or "[ERR]" in out:
                syntax_ok = False
                for line in out.splitlines():
                    if "[ERROR]" in line or "[ERR]" in line:
                        print("\n  {}".format(line), flush=True)
                        all_issues.append("2.1  Syntax error: {}".format(line.strip()))
            else:
                print("PASS")
        except FileNotFoundError:
            print("SKIP (verify_staged.py not found)")
        except subprocess.TimeoutExpired:
            print("FAIL (timed out after 60s)")
            all_issues.append("2.1  verify_staged.py timed out")
    elif sh_files:
        print("SKIP (only .sh files)")
    else:
        print("SKIP (no Python/shell files)")

    # Shell syntax check
    if sh_files and syntax_ok:
        sh_ok = True
        for sf in sh_files:
            r = subprocess.run(["bash", "-n", sf], capture_output=True, text=True)
            if r.returncode != 0:
                syntax_ok = False
                sh_ok = False
                print("\n  bash -n failed: {}: {}".format(os.path.basename(sf), r.stderr.strip()), flush=True)
                all_issues.append("2.1  Shell syntax error: {} ({})".format(os.path.basename(sf), r.stderr.strip().splitlines()[0]))
        if sh_ok and syntax_ok:
            print("PASS")

    # 2.2: No eval() on external data (AST-based, skip comments and docstrings)
    print("2.2  No eval() on external data ... ", end="", flush=True)
    eval_found = False
    for fpath in py_files:
        try:
            import ast
            with open(fpath, "rb") as f:
                src = f.read().decode("utf-8", errors="replace")
            tree = ast.parse(src, filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "eval":
                        eval_found = True
                        msg = "  WARN: eval() at {}:{}".format(os.path.basename(fpath), node.lineno)
                        print("{}".format(msg), flush=True)
        except (OSError, IOError, SyntaxError):
            pass  # can't parse — skip this file
    if eval_found:
        has_warn = True
        all_issues.append("2.2  eval() found — must be justified")
        print("WARN")
    else:
        print("PASS")

    # 2.3: AST + grep sweep via verify_staged.py
    print("2.3  AST + grep sweep ... ", end="", flush=True)
    if py_files:
        try:
            result = subprocess.run(
                [sys.executable, verify_script, gid] + py_files,
                capture_output=True, text=True, timeout=60,
            )
            out = result.stdout + result.stderr
            if result.returncode != 0:
                err_lines = [l for l in out.splitlines() if "[ERROR]" in l or "[ERR]" in l]
                for el in err_lines:
                    print("\n  {}".format(el), flush=True)
                    all_issues.append("2.3  Structural issue: {}".format(el.strip()))
                syntax_ok = False
                print("FAIL")
            elif "RESULT: WARN" in out:
                # verify_staged.py outputs plain text for warnings (exit 0, RESULT: WARN)
                warn_lines = [l for l in out.splitlines() if not l.startswith("RESULT:") and l.strip()]
                if warn_lines:
                    for wl in warn_lines:
                        print("  NOTE: {}".format(wl), flush=True)
                    has_warn = True
                    if strict:
                        all_issues.append("2.3  Strict: warnings found ({} items)".format(len(warn_lines)))
                        syntax_ok = False
                print("PASS" if syntax_ok else "FAIL")
            else:
                print("PASS")
        except FileNotFoundError:
            print("SKIP (verify_staged.py not found)")
    else:
        print("SKIP (no Python files)")

    # Spec compliance (manual)
    print("3    Spec compliance: task body features implemented ... [MANUAL]")
    manual_items.append("3.1  Spec in task body fully satisfied — no missing features")
    print("3    No scope creep: no out-of-scope changes ... [MANUAL]")
    manual_items.append("3.2  No scope creep — no removals or refactors not in task body")

    # Handoff quality (manual)
    print("4    Pre-review sweep results in handoff comment ... [MANUAL]")
    manual_items.append("4.1  Verify_staged.py results reported in handoff comment")
    print("4    Handoff metadata complete ... [MANUAL]")
    manual_items.append("4.2  Handoff includes: changed_files, tests_run, gate_id, gate_status")

    # Read checklist for reference
    print()
    try:
        with open(CHECKLIST_PATH) as f:
            print("  Checklist reference ({})".format(CHECKLIST_PATH))
    except FileNotFoundError:
        pass

    # Summary
    print()
    if all_issues:
        print("PRE-FLIGHT: FAIL — {} issue(s)".format(len(all_issues)))
        for issue in all_issues:
            print("  ISSUE: {}".format(issue))
        if manual_items:
            print()
            print("MANUAL CHECKS:")
            for mi in manual_items:
                print("  [MANUAL] {}".format(mi))
        sys.exit(1)
    elif has_warn and strict:
        print("PRE-FLIGHT: STRICT FAIL — warnings are errors")
        if manual_items:
            print()
            print("MANUAL CHECKS:")
            for mi in manual_items:
                print("  [MANUAL] {}".format(mi))
        sys.exit(1)
    else:
        print("PRE-FLIGHT: ALL CHECKS PASS")
        if has_warn:
            print("  (warnings found but strict mode is OFF)")
        if manual_items:
            print()
            print("MANUAL CHECKS:")
            for mi in manual_items:
                print("  [MANUAL] {}".format(mi))


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(gid):
    try:
        return json.load(open(mpath(gid)))
    except FileNotFoundError:
        sys.exit(f"prodgate: no gate '{gid}' (run: prodgate stage {gid} <paths>)")


def save_manifest(gid, m):
    os.makedirs(gdir(gid), exist_ok=True)
    with open(mpath(gid), "w") as f:
        json.dump(m, f, indent=2)


def _skip_derived(name):
    """Return True if the path segment is a derived artifact (bytecode, __pycache__)."""
    from pathlib import Path
    parts = Path(name).parts
    for p in parts:
        if p == "__pycache__":
            return True
    if Path(name).suffix.lstrip(".") in ("pyc", "pyo"):
        return True
    return False


def newest_work_mtime(gid):
    """Return the newest mtime among MANIFEST target files only.

    Derived artifacts ( __pycache__/, *.pyc, *.pyo) are ignored so that
    verification steps that regenerate bytecode do not void a fresh approval.
    """
    m = load_manifest(gid)
    newest = 0.0
    for target in m.get("targets", {}):
        wp = wpath(gid, target)
        if _skip_derived(wp):
            continue
        if os.path.exists(wp) and os.path.isfile(wp):
            newest = max(newest, os.path.getmtime(wp))
    return newest


# ---------------------------------------------------------------------------
# Pre-flight checklist -- Vera's top rejection patterns, auto-checked
# ---------------------------------------------------------------------------

CHECKLIST = (
    "## PRE-FLIGHT CHECKLIST (Vera's Top Rejection Patterns)\n"
    "Run BEFORE staging a prodgate review. All items must PASS.\n\n"
    "---\n\n"
    "### 1. PROTOCOL FAILURES\n\n"
    "1.1  Production write staged BEFORE modification?\n"
    "     -> prodgate diff <gate-id> must show substantive changes.\n\n"
    "1.2  No applied gate re-used without fresh review?\n"
    "     -> Applied gates are immutable. New revision = new gate (e.g., t_x-r2).\n\n"
    "1.3  Submitted for review via kanban_request_review?\n"
    "     -> kanban_request_review(criteria=...) flips THIS card to review; the reviewer is\n"
    "        auto-spawned. Do NOT create a review card or kanban_block.\n\n"
    "---\n\n"
    "### 2. CODE DEFECTS\n\n"
    "2.1  No eval() on external data?\n"
    "     -> Use float(a)/float(b) instead of eval(ratio_str).\n\n"
    "2.2  Function calls match current signatures?\n"
    "     -> Every caller passes explicit timeout, positional args correct.\n\n"
    "2.3  No shared mutable state without lock protection?\n"
    "     -> Both reads AND writes protected.\n\n"
    "2.4  SQLite queries parameterized correctly?\n"
    "     -> No dynamic IN list via parameters; use static query + Python set filter.\n\n"
    "2.5  Regex tested for word boundaries (not substring)?\n"
    "     -> word-boundary PASS not re.compile('PASS').\n\n"
    "2.6  All variables initialized before first use?\n"
    "     -> Module-level = None where needed.\n\n"
    "2.7  No dead imports, constants, comments?\n"
    "     -> Every import and constant used at least once.\n\n"
    "2.8  Spec in task body fully satisfied?\n"
    "     -> No missing features. No scope creep (removals not in scope).\n\n"
    "---\n\n"
    "### 3. GATE / DIFF QUALITY\n\n"
    "3.1  prodgate diff shows substantive changes?\n"
    "     -> No empty files or whitespace-only diffs.\n\n"
    "3.2  verify_staged.py sweep passed?\n"
    "     -> python3 $PRODGATE_VERIFY <gate-id> [files]\n"
    "     -> Report: PASS / WARN / FAIL with counts.\n\n"
    "3.3  Handoff metadata complete?\n"
    "     -> changed_files, tests_run, gate_id, gate_status, bugs_fixed.\n\n"
    "---\n\n"
    "### 4. COMMON MISSED SELF-CHECKS\n\n"
    "Items 1-3 above cover the critical patterns. Additional manual checks:\n"
    " - py_compile on .py files\n"
    " - bash -n on .sh files\n"
    " - Every eval() replaced\n"
    " - Thread safety verified\n"
)


def cmd_preflight(a):
    """Run the pre-flight checklist against the staged gate, auto-detect what you can."""
    m = load_manifest(a.gate)
    targets = m.get("targets", {})
    if not targets:
        sys.exit("prodgate preflight: nothing staged in gate '{}' -- run 'prodgate stage' first".format(a.gate))

    results = []
    errors = []
    warnings = []

    # 1.1: Check diff is substantive
    has_substantive = False
    for target in sorted(targets.keys()):
        info = targets[target]
        wp = wpath(a.gate, target)
        if info["existed"] and os.path.exists(target):
            live = open(target).read().splitlines(keepends=True)
        else:
            live = []
        staged = open(wp).read().splitlines(keepends=True) if os.path.exists(wp) else []
        d = list(difflib.unified_diff(live, staged, fromfile="live" + target, tofile="staged" + target))
        if d:
            # Check for substantive changes (not just whitespace)
            substantive = [l for l in d if l[0] in "+-" and l.strip()]
            if substantive:
                has_substantive = True
                results.append("1.1 PASS -- prodgate diff shows substantive changes ({} changed lines)".format(
                    len(substantive)))
            else:
                results.append("1.1 WARN -- prodgate diff has changes but they appear whitespace-only")
                warnings.append("1.1 whitespace-only diff")
        elif info["existed"] and not os.path.exists(target):
            results.append("1.1 PASS -- new file (live does not exist, staged is NEW)")
            has_substantive = True
        else:
            results.append("1.1 FAIL -- prodgate diff shows no changes (staged == live)")
            errors.append("1.1 empty/decorative diff")

    # 1.2: Check no previous landed gate
    if os.path.exists(lpath(a.gate)):
        results.append("1.2 FAIL -- gate '{}' already LANDED (immutable). Use a new gate id.".format(a.gate))
        errors.append("1.2 gate already landed")
    else:
        results.append("1.2 PASS -- gate not previously applied")

    # 1.3: Process check -- prodgate can't verify, mark MANUAL
    results.append("1.3 MANUAL -- submit for review with kanban_request_review(criteria=...); the reviewer is auto-spawned. Do NOT create a review card or kanban_block.")

    # 2.x: Static analysis via verify_staged.py
    gate_dir = gdir(a.gate)
    work_dir = os.path.join(gate_dir, "work")
    py_files = [str(p) for p in sorted(Path(work_dir).rglob("*.py")) if p.is_file()]
    sh_files = [str(p) for p in sorted(Path(work_dir).rglob("*.sh")) if p.is_file()]

    if py_files:
        try:
            sweep_result = subprocess.run(
                [sys.executable, VERIFY_SCRIPT, a.gate] + py_files,
                capture_output=True, text=True, timeout=60,
            )
            sweep_rc = sweep_result.returncode
            sweep_out = (sweep_result.stdout + sweep_result.stderr).strip()
            if sweep_rc == 0 and "RESULT: PASS" in sweep_out:
                results.append("2.x PASS -- verify_staged.py sweep clean (exit {}): {}".format(sweep_rc, sweep_out))
            elif sweep_rc == 0 and "RESULT: WARN" in sweep_out:
                results.append("2.x WARN -- verify_staged.py found warnings (exit {}): {}".format(sweep_rc, sweep_out))
                warnings.append("2.x sweep WARN")
            else:
                results.append("2.x FAIL -- verify_staged.py returned exit {}: {}".format(sweep_rc, sweep_out))
                errors.append("2.x sweep FAIL: {}".format(sweep_out))
        except FileNotFoundError:
            results.append("2.x MANUAL -- PRODGATE_VERIFY not found (install one or run manually)")
        except subprocess.TimeoutExpired:
            results.append("2.x FAIL -- verify_staged.py timed out after 60s")
            errors.append("2.x sweep timeout")
    else:
        results.append("2.x SKIPPED -- no .py files staged (only {} .sh file(s))".format(len(sh_files)))

    if sh_files:
        try:
            sh_ok = True
            for sf in sh_files:
                r = subprocess.run(["bash", "-n", sf], capture_output=True, text=True)
                if r.returncode != 0:
                    sh_ok = False
                    results.append("2.x FAIL -- bash -n failed on {}: {}".format(os.path.basename(sf), r.stderr.strip()))
                    errors.append("2.x shell syntax error: {}".format(os.path.basename(sf)))
            if sh_ok:
                results.append("2.x PASS -- bash -n clean on {} shell file(s)".format(len(sh_files)))
        except FileNotFoundError:
            results.append("2.x MANUAL -- bash -n check skipped (bash not on PATH)")

    # 3.1 & 3.2: Already checked above (1.1 + 2.x)
    # 3.3: Manual -- covered in handoff
    results.append("3.3 MANUAL -- verify handoff metadata includes: changed_files, tests_run, gate_id, gate_status, bugs_fixed")

    # Print report
    print("=" * 70)
    print("PRODGATE PRE-FLIGHT CHECKLIST")
    print("Gate: {} | {} target(s)".format(a.gate, len(targets)))
    print("=" * 70)
    for line in results:
        tag = "PASS" if "PASS" in line else "WARN" if "WARN" in line else "FAIL" if "FAIL" in line else "MANUAL"
        print("  [{:<6}]  {}".format(tag, line))
    print("=" * 70)
    status = "PASS" if not errors else "FAIL ({} error(s))".format(len(errors))
    if warnings and not errors:
        status = "WARN ({} warning(s), 0 errors)".format(len(warnings))
    print("OVERALL: {}".format(status))
    print("=" * 70)
    print()
    print(CHECKLIST)

    # Save preflight results to gate for handoff reference
    preflight_log = os.path.join(gate_dir, "PREFLIGHT.log")
    with open(preflight_log, "w") as f:
        f.write("gate: {}\n".format(a.gate))
        f.write("results: {} pass, {} warn, {} error\n".format(
            len([r for r in results if "PASS" in r]),
            len([r for r in results if "WARN" in r]),
            len(errors),
        ))
        f.write("status: {}\n".format(status))
        f.write("\n---\n\n")
        for line in results:
            f.write(line + "\n")
    print("Results saved to: {}".format(preflight_log))

    if errors:
        sys.exit("prodgate preflight: {} error(s) found -- fix staged files and re-run".format(len(errors)))


def cmd_verify_staged(a):
    """Run verify_staged.py on the gate's staged files and record results."""
    m = load_manifest(a.gate)
    targets = m.get("targets", {})
    gate_dir = gdir(a.gate)
    work_dir = os.path.join(gate_dir, "work")
    py_files = [str(p) for p in sorted(Path(work_dir).rglob("*.py")) if p.is_file()]
    sh_files = [str(p) for p in sorted(Path(work_dir).rglob("*.sh")) if p.is_file()]

    # Fallback: pick up extensionless Python files (e.g. 'prodgate') by shebang
    for fp in sorted(Path(work_dir).rglob("*")):
        if fp.is_file() and not str(fp).endswith((".py", ".sh")):
            try:
                first_line = fp.open().readline().strip()
                if first_line.startswith("#!") and ("python" in first_line.lower() or "/python" in first_line.lower()):
                    py_files.append(str(fp))
            except (OSError, UnicodeDecodeError):
                pass

    if not py_files and not sh_files:
        print("No .py or .sh files in staged gate '{}'".format(a.gate))
        return

    sweep_result = subprocess.run(
        [sys.executable, VERIFY_SCRIPT, a.gate] + py_files,
        capture_output=True, text=True, timeout=60,
    )
    print(sweep_result.stdout)
    print(sweep_result.stderr)
    rc = sweep_result.returncode

    # Save results to gate
    results_file = os.path.join(gate_dir, "VERIFY_STAGED.log")
    with open(results_file, "w") as f:
        f.write("gate: {}\n".format(a.gate))
        f.write("exit_code: {}\n".format(rc))
        f.write("stdout:\n{}\n".format(sweep_result.stdout))
        f.write("stderr:\n{}\n".format(sweep_result.stderr))
    print("\nResults saved to: {}".format(results_file))

    sys.exit(rc)

def cmd_stage(a):
    if os.geteuid() == 0:
        sys.exit("prodgate: do not run 'stage' as root/sudo — gate files become root-owned and "
                 "the reviewer can't write a verdict. Run as the agent user; only 'apply' "
                 "escalates (itself, per-file) when a target needs it.")
    m = {"created": time.time(), "by": a.by, "targets": {}}
    re_stage = False
    if os.path.exists(mpath(a.gate)):
        m = load_manifest(a.gate)
        if m["targets"]:
            re_stage = True
    if a.src and len(a.paths) != 1:
        sys.exit("prodgate: --from takes exactly one target path")
    if os.path.exists(lpath(a.gate)):
        sys.exit(f"prodgate: gate '{a.gate}' already LANDED — landed gates are immutable. "
                 f"Stage the new revision under a fresh gate id (e.g. {a.gate}-r2).")
    # --- Bug 4: reject __pycache__ paths at the command line ---
    for p in a.paths:
        if "__pycache__" in os.path.abspath(p):
            print(f"skipping (derived artifact): {p}", flush=True)
            continue

    for target in a.paths:
        target = os.path.abspath(os.path.expanduser(target))
        # --- Bug 4 (continued): skip if derived artifact ---
        if _skip_derived(target):
            print(f"skipping (derived artifact): {target}", flush=True)
            continue
        if any(target.startswith(p) for p in _NON_PROD_PREFIXES):
            sys.exit(f"prodgate: {target} is a workspace/scratch path, not a production "
                     f"destination. Stage the path where the file should LAND (e.g. "
                     f"/usr/local/bin/my-tool), then copy your built artifact onto the "
                     f"staged copy — or do both at once: prodgate stage <gate> <live-target> "
                     f"--from <your-built-file>")
        wp = wpath(a.gate, target)
        os.makedirs(os.path.dirname(wp), exist_ok=True)
        # Zero-diff guard: detect if the builder edited the live file after a prior
        # stage and then re-staged, producing a staged copy byte-identical to live.
        # We record sha(staged_copy) on first stage; on re-stage we compare sha(live)
        # against it. If they differ, the user edited live -> rejected.
        prev_stage_sha = m.get("targets", {}).get(target, {}).get("stage_sha")
        if prev_stage_sha and os.path.exists(target):
            if sha(target) != prev_stage_sha:
                sys.exit(
                    "prodgate: {} CHANGED since first stage.\n"
                    "  The live file no longer matches the original staged snapshot.\n"
                    "  REVERT the live file to its pre-edit state, then re-stage\n"
                    "  and edit ONLY the staged copy (via prodgate path), never the live file.".format(
                        target
                    )
                )
        if os.path.exists(target):
            if os.path.isdir(target):
                sys.exit(f"prodgate: {target} is a directory — stage files, not dirs")
            # --- Bug 3: refuse 0-byte targets ---
            if os.path.getsize(target) == 0:
                sys.exit(f"prodgate: {target} is empty (0 bytes) — refusing to stage an empty file")
            # Preserve builder's staged edits: if this target was staged in a
            # previous round (re_stage) and the live file hasn't changed since
            # the original stage_sha, skip the live→work copy so the builder's
            # edits survive. Only overwrite when live actually changed (new
            # content arrived from upstream / the builder's own edits to live).
            if re_stage and prev_stage_sha:
                live_has_changed = sha(target) != prev_stage_sha
            else:
                live_has_changed = True
            if live_has_changed:
                shutil.copy2(target, wp)
            m["targets"][target] = {"existed": True, "stage_sha": sha(wp),
                                    "mode": stat.S_IMODE(os.stat(target).st_mode)}
            if live_has_changed:
                print(f"staged (live snapshot): {target}\n  -> edit: {wp}")
            else:
                print(f"preserved (live unchanged): {target}\n  -> edit: {wp}")
        else:
            open(wp, "a").close()
            m["targets"][target] = {"existed": False, "stage_sha": None, "mode": 0o644}
            print(f"staged (NEW file): {target}\n  -> create content at: {wp}")
        if a.src:
            src = os.path.abspath(os.path.expanduser(a.src))
            shutil.copy2(src, wp)
            print(f"  loaded staged content from: {src}")

    # --- Bugs 1+2: refresh manifest SHAs for ALL previously-staged targets.
    # When re-staging with a subset of paths, earlier-round targets keep stale
    # SHAs and their work-copy may have drifted from live. Refresh every entry.
    if re_stage:
        for target, info in m.get("targets", {}).items():
            if target in a.paths:
                continue  # already processed above
            if info["existed"] and os.path.exists(target):
                m["targets"][target]["stage_sha"] = sha(target)
                print(f"refreshed (re-stage): {target}\n  SHA updated: {sha(target)[:12]}…")
            else:
                m["targets"][target]["stage_sha"] = None

    # staging anew voids any prior verdicts
    for p in (apath(a.gate), rpath(a.gate)):
        if os.path.exists(p):
            os.remove(p); print(f"(cleared stale verdict: {os.path.basename(p)})")
    save_manifest(a.gate, m)



def cmd_path(a):
    target = os.path.abspath(os.path.expanduser(a.target))
    m = load_manifest(a.gate)
    if target not in m["targets"]:
        sys.exit(f"prodgate: {target} not staged in gate '{a.gate}'")
    print(wpath(a.gate, target))


def cmd_edit(a):
    """Edit a staged copy in place, WITHOUT touching the live file.

    Solves the revert→re-stage loop (card t_4ed83258): when the live file
    has changed and `stage` refuses, the builder can use `edit` to fold
    changes onto the staged copy directly. The live file is never modified.

    Modes:
      --mode replace  (--old TEXT --new TEXT)  find-and-replace all occurrences
      --mode line     (--line N --text TEXT)   replace line N (1-indexed)
      --mode diff                         apply unified diff from stdin

    Security: if an APPROVED.json exists, editing voids it (edit after approval
    requires re-review). The manifest's stage_sha is updated so the next `stage`
    call sees the staged copy as diverged from live — the builder must edit
    (or revert+re-stage if they truly want a fresh snapshot).
    """
    gid = a.gate
    target = os.path.abspath(os.path.expanduser(a.target))

    m = load_manifest(gid)
    if target not in m.get("targets", {}):
        sys.exit(f"prodgate: {target} not staged in gate '{gid}' — stage it first with 'prodgate stage {gid} {target}'")

    wp = wpath(gid, target)
    if not os.path.exists(wp):
        sys.exit(f"prodgate: staged copy {wp} does not exist")

    mode = a.mode or "replace"
    if mode == "replace":
        if not a.old_text or not a.new_text:
            sys.exit("prodgate edit --mode replace requires --old TEXT and --new TEXT")
        with open(wp, "r") as f:
            content = f.read()
        new_content = content.replace(a.old_text, a.new_text)
        if new_content == content:
            print(f"NOTE: no occurrences of '{a.old_text}' found in staged copy (no change)")
            return
        with open(wp, "w") as f:
            f.write(new_content)
        print(f"replaced all '{a.old_text}' -> '{a.new_text}' in staged copy")

    elif mode == "line":
        if a.line is None or a.text is None:
            sys.exit("prodgate edit --mode line requires --line N and --text TEXT")
        with open(wp, "r") as f:
            lines = f.readlines()
        n = a.line
        if n < 1 or n > len(lines):
            sys.exit(f"prodgate edit: line {n} out of range (file has {len(lines)} lines)")
        lines[n - 1] = a.text
        with open(wp, "w") as f:
            f.writelines(lines)
        print(f"replaced line {n} in staged copy")

    elif mode == "diff":
        with open(wp, "r") as f:
            orig_lines = f.readlines()
        diff_text = sys.stdin.read()
        if not diff_text:
            sys.exit("prodgate edit --mode diff: no diff on stdin")
        try:
            new_lines = _apply_diff(orig_lines, diff_text)
            if new_lines == orig_lines:
                print("NOTE: diff produced no changes in staged copy")
                return
            with open(wp, "w") as f:
                f.writelines(new_lines)
            print("applied unified diff from stdin to staged copy")
        except Exception as e:
            sys.exit(f"prodgate edit: failed to apply diff: {e}")
    else:
        sys.exit(f"prodgate edit: unknown mode '{mode}' — use replace, line, or diff")

    # Update manifest SHA to reflect the new staged state
    m["targets"][target]["stage_sha"] = sha(wp)
    save_manifest(gid, m)
    print(f"updated manifest SHA for {target}")

    # Clear approval if it exists — edits after approval void the approval
    if os.path.exists(apath(gid)):
        os.remove(apath(gid))
        print("(cleared APPROVED.json — approval voided by edit; re-request review)")


def _apply_diff(original_lines, diff_text):
    """Apply a unified diff string to a list of lines.

    Minimal unified-diff parser: handles @@-hunks with ---/+++ headers,
    context lines, additions (+), and deletions (-).
    """
    import re
    result = []
    orig_idx = 0
    lines = diff_text.splitlines(keepends=True)

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("@@"):
            m = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                m = re.search(r"@@ -(\d+) \+(\d+) @@", line)
                if m:
                    orig_start = int(m.group(1))
                    new_start = int(m.group(2))
                    orig_count = 1
                    new_count = 1
                else:
                    raise ValueError(f"Cannot parse hunk header: {line.strip()}")
            else:
                orig_start = int(m.group(1))
                orig_count = int(m.group(2) or 1)
                new_start = int(m.group(3))
                new_count = int(m.group(4) or 1)

            # Consume --- and +++ header lines
            while i < len(lines) and lines[i].startswith("---"):
                i += 1
            while i < len(lines) and lines[i].startswith("+++"):
                i += 1
            # Skip the @@ hunk header itself
            i += 1

            # Advance any remaining original lines before this hunk
            if orig_idx < orig_start - 1:
                result.extend(original_lines[orig_idx:orig_start - 1])
                orig_idx = orig_start - 1

            # Process all hunk lines (context, -, +), consuming orig_count lines.
            # The hunk contains exactly orig_count original-line positions
            # and new_count new-line positions. Additions are emitted inline
            # (they may appear between context lines in a proper unified diff).
            orig_consumed = 0
            while i < len(lines):
                hunk_line = lines[i]
                if hunk_line.startswith(" ") and orig_consumed < orig_count:
                    result.append(hunk_line[1:].rstrip("\n") + "\n")
                    orig_consumed += 1
                    i += 1
                elif hunk_line.startswith("-") and orig_consumed < orig_count:
                    orig_consumed += 1
                    i += 1
                elif hunk_line.startswith("+"):
                    result.append(hunk_line[1:].rstrip("\n") + "\n")
                    i += 1
                elif hunk_line.startswith("\\"):
                    i += 1
                else:
                    break

            # Advance orig_idx past the lines consumed by this hunk
            orig_idx = orig_start - 1 + orig_consumed

        else:
            i += 1

    # Append any remaining original lines after all hunks
    if orig_idx < len(original_lines):
        result.extend(original_lines[orig_idx:])

    return result


def cmd_diff(a):
    m = load_manifest(a.gate)
    any_change = False
    for target, info in sorted(m["targets"].items()):
        wp = wpath(a.gate, target)
        live = open(target).read().splitlines(keepends=True) if info["existed"] and os.path.exists(target) else []
        staged = open(wp).read().splitlines(keepends=True) if os.path.exists(wp) else []
        d = list(difflib.unified_diff(live, staged, fromfile=f"live{target}", tofile=f"staged{target}"))
        if d:
            any_change = True
            sys.stdout.writelines(d)
            if d and not d[-1].endswith("\n"):
                print()
    if not any_change:
        print("(no staged changes differ from live)")


def cmd_approve(a):
    m = load_manifest(a.gate)
    if not m["targets"]:
        sys.exit("prodgate: nothing staged — refusing to approve an empty gate")
    caller = os.environ.get("HERMES_PROFILE", "")
    if caller and caller != a.by:
        sys.exit(f"prodgate: you are profile '{caller}' — you cannot approve as '{a.by}'. "
                 f"Verdicts belong to the reviewer; builders never approve (not even on the "
                 f"reviewer's 'behalf'). If review happened out-of-band, ask the reviewer to "
                 f"run this command themselves.")
    if os.path.exists(rpath(a.gate)):
        os.remove(rpath(a.gate))
    with open(apath(a.gate), "w") as f:
        json.dump({"by": a.by, "ts": time.time(), "note": a.note or "",
                   "caller_profile": caller, "caller_pid": os.getpid()}, f, indent=2)
    print(f"APPROVED gate '{a.gate}' by {a.by}. apply may now land it "
          f"(approval voids if anything is re-staged or edited after this moment).")


def cmd_reject(a):
    load_manifest(a.gate)
    if os.path.exists(apath(a.gate)):
        os.remove(apath(a.gate))
    with open(rpath(a.gate), "w") as f:
        json.dump({"by": a.by, "ts": time.time(), "reason": a.reason}, f, indent=2)
    print(f"REJECTED gate '{a.gate}' by {a.by}: {a.reason}")


def _writable(path):
    d = os.path.dirname(path) or "/"
    return os.access(path, os.W_OK) if os.path.exists(path) else os.access(d, os.W_OK)


def cmd_apply(a):
    m = load_manifest(a.gate)
    if os.path.exists(lpath(a.gate)):
        sys.exit(f"prodgate: gate '{a.gate}' already LANDED — make a new gate for further changes")
    try:
        appr = json.load(open(apath(a.gate)))
    except FileNotFoundError:
        sys.exit(f"prodgate: gate '{a.gate}' is NOT approved — a reviewer must run "
                 f"'prodgate approve {a.gate} --by <reviewer>' first")
    if newest_work_mtime(a.gate) > appr["ts"]:
        os.remove(apath(a.gate))
        sys.exit("prodgate: staged files were EDITED AFTER approval — approval voided. Re-request review.")
    # refuse on live drift since staging
    for target, info in m["targets"].items():
        if info["existed"]:
            if not os.path.exists(target):
                sys.exit(f"prodgate: live {target} disappeared since staging — re-stage and re-review")
            if sha(target) != info["stage_sha"]:
                sys.exit(f"prodgate: live {target} CHANGED since staging — re-stage and re-review")
        elif os.path.exists(target):
            sys.exit(f"prodgate: {target} was staged as NEW but now exists live — re-stage and re-review")
    # Decorative-gate guard (card #7 t15 on the crew this was lifted from): refuse to LAND when staged == live for
    # every target — an empty diff means the review ceremony ran but nothing actually changes.
    if not any(
        (os.path.exists(wpath(a.gate, t)) and os.path.getsize(wpath(a.gate, t)) > 0) if not i["existed"]
        else (not os.path.exists(wpath(a.gate, t)) or sha(wpath(a.gate, t)) != sha(t))
        for t, i in m["targets"].items()
    ):
        sys.exit("prodgate: nothing to apply — staged == live for every target (decorative/empty diff). "
                 "Re-stage with real changes, or drop the gate; a gate must change something to land.")
    ts = time.strftime("%Y%m%d-%H%M%S")
    backups = []
    for target, info in sorted(m["targets"].items()):
        wp = wpath(a.gate, target)
        sudo = [] if _writable(target) else ["sudo", "-n"]
        if info["existed"]:
            bp = bpath(a.gate, target)
            os.makedirs(os.path.dirname(bp), exist_ok=True)
            shutil.copy2(target, bp)
            side = f"{target}.bak-{ts}"
            subprocess.run(sudo + ["cp", "-p", target, side], check=True)
            backups.append(side)
        subprocess.run(sudo + ["mkdir", "-p", os.path.dirname(target)], check=True)
        subprocess.run(sudo + ["cp", wp, target], check=True)
        mode = stat.S_IMODE(os.stat(wp).st_mode) or info["mode"]
        subprocess.run(sudo + ["chmod", oct(mode)[2:], target], check=True)
        print(f"landed: {target}" + (f"  (backup: {target}.bak-{ts})" if info["existed"] else "  (new)"))
    with open(lpath(a.gate), "w") as f:
        json.dump({"ts": time.time(), "approved_by": appr["by"], "backups": backups}, f, indent=2)
    print(f"gate '{a.gate}' LANDED ({len(m['targets'])} file(s)). Verify the system now.")


def cmd_status(a):
    m = load_manifest(a.gate)
    state = "staged"
    if os.path.exists(rpath(a.gate)): state = "REJECTED"
    if os.path.exists(apath(a.gate)): state = "APPROVED"
    if os.path.exists(lpath(a.gate)): state = "LANDED"
    print(f"gate {a.gate}: {state}  ({len(m['targets'])} target(s), staged by {m.get('by') or '?'})")
    for target, info in sorted(m["targets"].items()):
        wp = wpath(a.gate, target)
        changed = "?"
        try:
            if not info["existed"]:
                changed = "NEW"
            else:
                changed = "modified" if sha(wp) != info["stage_sha"] else "unchanged"
        except OSError:
            pass
        print(f"  {target}  [{changed}]")
    for p, tag in ((apath(a.gate), "approved"), (rpath(a.gate), "rejected"), (lpath(a.gate), "landed")):
        if os.path.exists(p):
            d = json.load(open(p))
            who = d.get("by") or d.get("approved_by") or "?"
            print(f"  {tag}: by {who} at {time.strftime('%F %T', time.localtime(d['ts']))} "
                  f"{d.get('note') or d.get('reason') or ''}".rstrip())


def cmd_list(a):
    if not os.path.isdir(GATE_ROOT):
        print("(no gates)"); return
    for gid in sorted(os.listdir(GATE_ROOT)):
        if os.path.exists(mpath(gid)):
            state = "staged"
            if os.path.exists(rpath(gid)): state = "REJECTED"
            if os.path.exists(apath(gid)): state = "APPROVED"
            if os.path.exists(lpath(gid)): state = "LANDED"
            n = len(json.load(open(mpath(gid)))["targets"])
            print(f"{gid:24} {state:9} {n} target(s)")


def main():
    p = argparse.ArgumentParser(prog="prodgate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage"); s.add_argument("gate"); s.add_argument("paths", nargs="+")
    s.add_argument("--by", default=os.environ.get("HERMES_PROFILE", "bash"))
    s.add_argument("--from", dest="src", help="built artifact to load as the staged content (single target only)")
    s.set_defaults(fn=cmd_stage)
    s = sub.add_parser("path"); s.add_argument("gate"); s.add_argument("target"); s.set_defaults(fn=cmd_path)
    s = sub.add_parser("diff"); s.add_argument("gate"); s.set_defaults(fn=cmd_diff)
    s = sub.add_parser("edit"); s.add_argument("gate"); s.add_argument("target")
    s.add_argument("--mode", choices=["replace", "line", "diff"], default="replace")
    s.add_argument("--old", dest="old_text"); s.add_argument("--new", dest="new_text")
    s.add_argument("--line", type=int); s.add_argument("--text")
    s.set_defaults(fn=cmd_edit)
    s = sub.add_parser("approve"); s.add_argument("gate"); s.add_argument("--by", required=True)
    s.add_argument("--note"); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject"); s.add_argument("gate"); s.add_argument("--by", required=True)
    s.add_argument("--reason", required=True); s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("apply"); s.add_argument("gate"); s.set_defaults(fn=cmd_apply)
    s = sub.add_parser("status"); s.add_argument("gate"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("list"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("preflight"); s.add_argument("gate"); s.set_defaults(fn=cmd_preflight)
    s = sub.add_parser("verify-staged"); s.add_argument("gate"); s.set_defaults(fn=cmd_verify_staged)
    s = sub.add_parser("check"); s.add_argument("gate"); s.add_argument("--strict", action="store_true", help="fail on warnings")
    s.set_defaults(fn=cmd_check)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
