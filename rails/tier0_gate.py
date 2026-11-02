#!/usr/bin/env python3
"""Parallax T0 — deterministic pre-exec gate for Hermes tool calls (krew security rails).

Ported as ALGORITHM ONLY from the museum fleet (attribution in tier0.yaml).
T1 (dual-LLM classifier) deliberately not ported: tirith + approvals.deny cover
the deterministic ground. Revisit only on a named miss (the owner 2026-07-31).

Discipline (the owner 2026-07-31):
  * a MISSING policy fails OPEN and says so loudly (a fresh install must not
    stall); a policy that is PRESENT but does not parse, or parses to zero
    rules, fails CLOSED in enforce mode (the owner 2026-09-18 "it is advertised so
    it needs to be secure and functional") — route `protected`, one hits-log
    line per call; kill `KREW_T0_BROKEN_OPEN=1` restores the old open path.
  * the hook spec carries `fail_closed: true` (hermes shell_hooks): a crashed,
    timed-out or garbled gate blocks the call instead of silently allowing it.
  * a blocked action carries its ROUTE (protected | prodgate | assent | crissa | worktree), so the
    agent is told where the action *does* belong, never a dead "denied".
  * MODE=shadow until the false-positive week is done: matches are recorded,
    nothing is blocked. Flip KREW_T0_MODE=enforce when the log is clean.
  * A NEW rule arms on its own (2026-09-24): `mode: report` on one rule records a
    hit (hits-log `"mode": "report"`) and tells the agent, but never blocks, and the
    scan goes on so it cannot mask a later rule. Hermes drops a pre_tool_call
    `context` (only block/modify reach the agent), so a report or shadow note is for
    the hits log, not the agent. `mode: enforce` refuses whatever
    KREW_T0_MODE the calling process carries: a crew run launched by a timer or the
    CLI (not a gateway with the krew-tier0.conf drop-in) gets the default `shadow`, so a
    rule that must stop something says so itself. `env_off: VAR` gives a rule its own
    kill (VAR=0 skips it).
  * A rule can read WHAT A COMMAND RUNS (2026-09-30): `targets: [command, script, write]`
    (default [command]). `script` reads each file the command executes (python3 x.py,
    bash x.sh, uv run python x, source x, ./x) unless it is a file in one of the policy's
    `trusted_repos` that is tracked and unmodified or a root-owned system file, and matches the rule against its
    text; a script that exists but cannot be read is a hit. `write` matches the content a
    write_file / patch puts into a script. `tools: [...]` limits the command and script
    targets to those tools (prose in a card comment is not a command). At 10:49 on 09-30
    a bash worker wrote crew-shot.py (os.execvp systemctl restart crew) because the
    approvals check reads only the typed command. Kill: KREW_T0_SCRIPT_BODY=0.
  * `profiles: [...]` limits a rule to those profiles; `write_roots: [...]` makes a rule fire when a
    write_file / patch path resolves (links, `..`) outside every root (2026-10-01, Crissa's folders).
  * A BLOCK LIST of paths (2026-10-01, second ruling): `write_block` fires when the resolved path is inside one
    of those folders, unless it is inside a `write_except` folder; `write_block_always` (folders or files) and
    `write_block_names` (file-name globs: credentials, kanban.db) fire even inside an exception.
  * WHY BEFORE FIX (the owner 2026-10-02): the policy's `why_before_fix:` block. After every rule has had its
    turn (an existing refusal returns first, so none changes), a crew call that reverts, reloads, restarts,
    re-enables or "repairs" something - including a display verb inside `tool_call` - and that no rule above
    has already reported, is read against the
    records that already hold intent (why_before_fix.intent_for beside this file, 7-day lookback, no new store) and sorted:
    a KNOWN cause (a Crissa ruling, an owner change) goes to Crissa with the record (route crissa); an UNKNOWN one
    is the worker's to find out and write on its card as `why:` (route why). Ships REPORT-ONLY: the hits log
    carries the cause and records, nothing is refused. Kill KREW_T0_WHY=0. A failure inside the check is
    logged (`why_error`) and never blocks.

krew's additions (the gate travels to machines that are not the one it was built on):
  * the policy is $KREW_T0_POLICY, default $KREW_HOME/tier0.yaml (config/tier0.example.yaml is the shipped one);
    MODE is the policy's `mode:` (default shadow), KREW_T0_MODE overrides it; the route texts are the policy's
    `routes:` over the defaults below, none naming a machine.
  * `scope: crew` = a kanban worker or any named profile (never `default`, the owner's own CLI). Kill for crew
    rules: KREW_T0_CREW_RULES=0. A leading `~` in a path rule is the account's real home, never a worker's
    sandboxed HOME.
  * VARS: a pattern or path may name `{NAME}` from the policy's `vars:` (a string or a list; `~` expands; a
    pattern gets the escaped value in every spelling, a list becomes an alternation; `{NAME:tail}` is the
    home-relative tail, `{NAME:abs}` the one absolute path). A rule whose var is unset is INACTIVE: skipped, and listed by `tier0_gate.py --lint`
    (never silently); `|{NAME}` as one alternative only drops out (the rule narrows, --lint says PARTIAL).
    `{USER}` is the owner's id (KREW_USER_ID, else an identity store's role "user", else vars.USER).

Wire: hooks.pre_tool_call in a profile's config.yaml (scripts/krew-install.py --hooks or
crew/scripts/krew-crew-install.py --profiles writes it). JSON on stdin, JSON on
stdout ({"decision": "block", "reason": ...} blocks the call):

    - matcher: terminal|execute_code|write_file|patch
      command: python3 ~/.krew/rails/tier0_gate.py
      timeout: 10
      fail_closed: true
"""
from __future__ import annotations

import fnmatch
import json
import os
import pwd
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The policy is YAML, and the interpreter a hook gets is whatever `python3` is on the caller's
# PATH. Since the 2026-09-26 hermes update a kanban worker's PATH puts hermes's own Python first,
# which has no PyYAML: the policy read as "broken" and the gate failed CLOSED on every act
# (measured 2026-09-27 under a worker's PATH and in the multiplex rehearsal). Run under the
# system interpreter instead, once. Kill: KREW_T0_SYSTEM_PYTHON=0 (it fails closed as before).
_SYSTEM_PYTHON = os.environ.get("KREW_T0_PYTHON", "/usr/bin/python3")


def _yaml_ok() -> bool:
    try:
        import krew_settings
        krew_settings._safe_load("a: 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _rerun_under_system_python() -> None:
    if os.environ.get("KREW_T0_SYSTEM_PYTHON", "1") == "0" or os.environ.get("KREW_T0_REEXECED") == "1":
        return
    if _yaml_ok() or not os.access(_SYSTEM_PYTHON, os.X_OK):
        return
    if os.path.realpath(sys.executable) == os.path.realpath(_SYSTEM_PYTHON):
        return
    os.environ["KREW_T0_REEXECED"] = "1"
    os.execv(_SYSTEM_PYTHON, [_SYSTEM_PYTHON, os.path.abspath(__file__), *sys.argv[1:]])


if __name__ == "__main__":
    _rerun_under_system_python()


def _real_home() -> Path:
    """The account's own home. A kanban worker runs with HOME set to a sandbox under its profile, so
    Path.home() there is the wrong answer for every path in the policy."""
    v = os.environ.get("KREW_REAL_HOME") or os.environ.get("HERMES_REAL_HOME")
    if v:
        return Path(v)
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return Path.home()


_REAL_HOME = _real_home()


def _krew_home() -> Path:
    v = os.environ.get("KREW_HOME") or "~/.krew"
    return Path(_REAL_HOME / v[2:]) if v.startswith("~/") else Path(v)


POLICY = Path(os.environ.get("KREW_T0_POLICY") or _krew_home() / "tier0.yaml")
LOG = Path(os.environ.get("KREW_T0_LOG") or _krew_home() / "logs" / "tier0-hits.jsonl")

# Route texts: what the agent is told to do instead. The policy's `routes:` adds or replaces any of them; these
# defaults name no machine, folder or person. `{LIVE_TREE}` and friends are filled from the policy's vars.
ROUTE_GUIDANCE = {
    # Was "this profile's terminal is docker-backed (bash/steward)" — false since 2026-08-05;
    # only Ada's is (the owner 2026-09-23). Every tool it names must exist: crew/scripts/tmp-reap.
    # The policy's `routes:` adds or replaces any of these; {LIVE_TREE} and friends are filled from its vars.
    "protected": ("Nothing runs this on the host. A script nobody has run yet goes to Ada: her "
                  "terminal is the one container (a sandboxed profile), the repo mounted read-only, the venvs not mounted. "
                  "Deleting under /tmp is tmp-reap. Otherwise block for Crissa with this reason."),
    "prodgate": ("Production write — stage -> Vera review -> apply (rails/krew-prodgate). "
                 "Next profile is Vera, not Crissa. Host venv/uv is bash or nightly "
                 "Steward land, not this gate. needs-crissa last resort only if Ada + "
                 "worker + Vera are actually stuck. The owner does not make this call."),
    # Crissa is the single point of contact for what the crew may not do itself
    # (the owner 2026-09-19): a key, a gate change, a ruling. She brings it to
    # the owner when it needs hands.
    "crissa": ("Ask Crissa — she is the single point of contact. Block this card with "
             "--kind needs_input and say exactly what you need (a credential set as a "
             "value, a gate change, a ruling); it lands on her Decide queue. Never "
             "work around this gate."),
    # The live tree (2026-09-24): a git move there is refused, and the answer is not
    # a ruling but a different place to do it. Crissa only when the live tree itself must move.
    "worktree": ("Do not move {LIVE_TREE}: it is the running install. Edits there are "
                 "fine: commit by path (git add P && git commit -m ... -- P). Branch, "
                 "stash, reset or rebase work goes in your own worktree: git -C "
                 "{LIVE_TREE} worktree add .worktrees/<card> -b <branch> main, then "
                 "cd there and name that folder in every git command. Nothing is pushed off this "
                 "machine. Only if the live tree itself must change branch, block this card for "
                 "Crissa (--kind needs_input) and say why."),
    # The active board (2026-09-27): the answer is a different way to aim the call, not a ruling.
    "board": ("Do not change the active board: kanban/current is the owner's, and every "
              "reader follows it. Create a scratch board WITHOUT --switch and name it on each call: "
              "hermes kanban --board <slug> ..., or HERMES_KANBAN_BOARD=<slug> for a script. The "
              "board gate puts current back within two minutes either way."),
    # Hermes tests from a worker shell (2026-09-27): the fix is a prefix, not a ruling.
    "kanban-env": ("Prefix the command with kanban-env-clean ($KREW_HOME/crew/scripts/kanban-env-clean; "
                   "it runs the tests in their own HOME with no HERMES_KANBAN_*; the krew test sandbox hook does "
                   "the same for the profiles that run tests). env -u is NOT enough: "
                   "a test that re-imports hermes_cli still reaches the live board."),
    # The upstream hermes clone (2026-09-28): the answer is a worktree, not a ruling.
    "clone": ("Do not move {HERMES_CLONE} off main. Stash, checkout -- <path>, commits and reads "
              "there are fine. Branch work goes in its own worktree: git -C {HERMES_CLONE} worktree "
              "add ~/hermes-worktrees/<card> -b <branch> origin/main, then cd there."),
    "push": ("Nothing is pushed off this machine; the owner publishes. Commit locally (a branch in a worktree), "
             "name the branch and commit in your completion, and block for Crissa (--kind needs_input) only if "
             "the card cannot finish without a push. git push --dry-run is free."),
    # Unit control (the owner 2026-09-30): the crew never stops or restarts a unit, directly or by script.
    "window": ("The crew does not stop or restart units (the owner 2026-09-30). A restart goes only through "
               "the restart window: block this card for Crissa (--kind needs_input) naming the unit and why; "
               "the window files its card, she rules, and the executor restarts it after crew's workers finish. "
               "Never wrap the command in a script or another tool: T0 reads the scripts you run."),
    # Crissa's kit (the owner 2026-10-01): no shell or code, and her writes stay in her folders.
    "crissa-kit": ("Crissa's hands have no shell and no code runner (the owner 2026-10-01): read a card or the board "
                 "with team_board show / list, presence with team_presence. Write files under her own profile folder "
                 "(~/.hermes/profiles/crissa) or the deliverables folder; never into system folders, the repos, the hermes tree, the board, "
                 "her own config or credential files. If the job needs more, say so plainly "
                 "and stop; never route around this."),
    # Comparison checkouts and bulk test runs (the owner 2026-10-01, run 4795's OOM).
    "tmp-checkout": ("/tmp is RAM (tmpfs) and counts against your worker's memory cap. Put a comparison checkout "
                     "under your card's workspace on disk (git worktree add <workspace>/baseline <ref>), and "
                     "`git worktree remove` it when done (the owner 2026-10-01)."),
    "solo-tests": ("{SOLO_TEST} runs alone, never in a bulk run: run the test files "
                   "your change touches by name, and {SOLO_TEST} only by itself if you need it."),
    # Why before fix (the owner 2026-10-02): an unknown cause is the worker's to find, not a ruling.
    "why": ("Find out why it is the way it is before you change it: read what was named (git log, the journal, "
            "the board, restart-owed, the M&S ledger, the process's own unit). Write `why: <target> - <what you "
            "found>` as a comment on your card, then run the call again. If what you find is a ruling or a change "
            "the owner made, block this card for Crissa (--kind needs_input) with that record instead."),
    "assent": ("NEEDS HUMAN — physical world (unplugged camera, a body in the chair, "
               "hardware, root kernel). Not a software opinion. Do not ask the owner "
               "to approve a bump or a restart."),
}
# Fields a tool call may carry actual shell/command text in.
TEXT_FIELDS = ("command", "cmd", "script", "code", "input", "text", "path", "file_path", "url")


def _switch(name: str, default: str) -> str:
    return os.environ.get(name, default).strip().lower()


BROKEN_OPEN = _switch("KREW_T0_BROKEN_OPEN", "0") == "1"
# `scope: crew` rules (2026-09-19): the ruler rules. They fire for a kanban worker
# (HERMES_KANBAN_TASK, set by the dispatcher in the worker's OWN env, so a command
# cannot shed it) or any named profile; never for `default`, the owner's own CLI and
# dashboard. Kill: KREW_T0_CREW_RULES=0 skips them.
CREW_RULES = _switch("KREW_T0_CREW_RULES", "1") != "0"
_VAR = re.compile(r"\{([A-Z][A-Z0-9_]*)(?::(tail|abs))?\}")


def _load_doc():
    """(doc, state): state ok | missing | broken."""
    if not POLICY.is_file():
        return {}, "missing"
    try:
        import krew_settings
        doc = krew_settings._safe_load(POLICY.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}, "broken"
    if not isinstance(doc, dict):
        return {}, "broken"
    return doc, "ok"


def _expand_home(v: str) -> str:
    v = str(v)
    if v == "~" or v.startswith("~/"):
        return str(_REAL_HOME) + v[1:]
    return v


def _forms(v: str) -> list[str]:
    """A path value's spellings a command may use: as written, ~-form, absolute, $HOME-form."""
    v = str(v)
    out = [v]
    home = str(_REAL_HOME)
    if v == "~" or v.startswith("~/"):
        out += [home + v[1:], "$HOME" + v[1:]]
    elif v == home or v.startswith(home + "/"):
        out += ["~" + v[len(home):], "$HOME" + v[len(home):]]
    return list(dict.fromkeys(out))


def policy_vars(doc: dict) -> dict:
    v = doc.get("vars") if isinstance(doc.get("vars"), dict) else {}
    out = {str(k): val for k, val in v.items() if val not in (None, "", [])}
    uid = _user_id(out)
    if uid:
        out["USER"] = uid
    else:
        out.pop("USER", None)
    return out


def _user_id(vars_: dict | None = None) -> str:
    """The owner's id, never a literal: KREW_USER_ID, else the one person whose
    person.json says role "user" (an identity store: KREW_ID_ROOT or vars.ID_ROOT), else vars.USER.
    Unknown -> "" (the {USER} slot then drops out)."""
    vars_ = vars_ or {}
    uid = (os.environ.get("KREW_USER_ID") or "").strip().lower()
    if uid:
        return uid
    return _identity_user(vars_.get("ID_ROOT")) or str(vars_.get("USER") or "").strip().lower()


def _identity_user(id_root) -> str:
    """The owner's id from an identity store (KREW_ID_ROOT, else vars.ID_ROOT): the one person whose
    people/<id>/person.json says role "user". Unknown -> "" (the {USER} slot then drops out)."""
    root = os.environ.get("KREW_ID_ROOT") or (str(id_root) if id_root else "")
    if not root:
        return ""
    try:
        for pj in sorted((Path(_expand_home(root)) / "people").glob("*/person.json")):
            if (json.loads(pj.read_text()) or {}).get("role") == "user":
                return pj.parent.name.lower()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _tail(v: str) -> str:
    """A path's home-relative tail (`~/projects/x` -> `projects/x`), for a pattern that takes any prefix."""
    v = str(v)
    home = str(_REAL_HOME)
    if v.startswith("~/"):
        return v[2:]
    if v.startswith(home + "/"):
        return v[len(home) + 1:]
    return v


_OPTIONAL = re.compile(r"\|\{([A-Z][A-Z0-9_]*)(?::(?:tail|abs))?\}")


def _fill_pattern(pat: str, vars_: dict, dropped: list | None = None) -> tuple[str | None, list[str]]:
    """The pattern with {VAR}s filled (escaped), or (None, [missing vars]).

    `{VAR}` -> every spelling of the value (as written, ~, absolute, $HOME); a list -> an alternation of them.
    `{VAR:tail}` -> the home-relative tail only (for a pattern that already takes any prefix, e.g. `\\S*`).
    `{VAR:abs}` -> the first value, absolute, one spelling (fits inside a fixed-width lookbehind).
    `|{VAR}` (an alternative: "|{USER}", the id, or nothing if the install has no owner yet) drops out when VAR is unset (the rule narrows, --lint says so); any other unset
    var leaves the whole rule inactive."""
    missing: list[str] = []

    def drop(m):
        if m.group(1) in vars_:
            return m.group(0)
        if dropped is not None:
            dropped.append(m.group(1))
        return ""
    pat = _OPTIONAL.sub(drop, pat)

    def sub(m):
        name = m.group(1)
        if name not in vars_:
            missing.append(name)
            return m.group(0)
        val = vars_[name]
        items = val if isinstance(val, list) else [val]
        if m.group(2) == "tail":
            forms = list(dict.fromkeys(_tail(str(x)) for x in items))
        elif m.group(2) == "abs":      # one spelling only: fits a fixed-width lookbehind
            return re.escape(_expand_home(str(items[0])))
        else:
            forms = [f for x in items for f in _forms(str(x))]
        return "(?:" + "|".join(re.escape(f) for f in forms) + ")"
    out = _VAR.sub(sub, pat)
    return (None, missing) if missing else (out, [])


def _fill_paths(paths, vars_: dict) -> tuple[list[str], list[str]]:
    out, missing = [], []
    for p in paths or []:
        names = [n for n, _t in _VAR.findall(str(p))]
        if any(n not in vars_ for n in names):
            missing += [n for n in names if n not in vars_]
            continue
        m = _VAR.fullmatch(str(p).strip())
        if m and isinstance(vars_[m.group(1)], list):      # a whole entry naming a list var = all of it
            out += [_expand_home(str(x)) for x in vars_[m.group(1)]]
            continue
        filled = _VAR.sub(lambda m: str(vars_[m.group(1)]) if not isinstance(vars_[m.group(1)], list)
                          else str(vars_[m.group(1)][0]), str(p))
        out.append(_expand_home(filled))
    return out, missing


def _fill_text(text: str, vars_: dict) -> str:
    return _VAR.sub(lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ and not isinstance(vars_[m.group(1)], list)
                    else m.group(0), text)


def _load_rules(doc: dict | None = None, state: str | None = None):
    """Return (rules, state, inactive). state: "ok" | "missing" | "broken".

    missing -> the caller fails OPEN (loudly). broken (present but unparseable, or zero usable rules) -> the
    caller fails CLOSED in enforce mode. inactive = [(index, reason, missing vars)] for --lint."""
    if doc is None:
        doc, state = _load_doc()
    if state != "ok":
        return [], state, []
    vars_ = policy_vars(doc)
    out, inactive = [], []
    for i, r in enumerate(doc.get("blocklist") or []):
        if not isinstance(r, dict):
            continue
        pat = r.get("pattern")
        if not pat:
            continue
        dropped: list[str] = []
        pat, miss = _fill_pattern(str(pat), vars_, dropped)
        path_rule, pmiss = _path_rule(r, vars_)
        if miss or (pmiss and not any(path_rule.values())):
            inactive.append((i, str(r.get("reason") or ""), sorted(set(miss + pmiss))))
            continue
        if pmiss or dropped:   # still runs, without the paths / alternatives whose var is unset: --lint says so
            inactive.append((i, str(r.get("reason") or ""), ["PARTIAL, runs without " + ", ".join(sorted(set(pmiss + dropped)))]))
        try:
            out.append((re.compile(pat, re.I), _fill_text(str(r.get("reason") or "policy match"), vars_),
                        (r.get("route") or "assent").lower(),
                        str(r.get("scope") or "all").lower(),
                        str(r.get("mode") or "").strip().lower(),
                        str(r.get("env_off") or "").strip(),
                        frozenset(str(t).strip().lower() for t in (r.get("tools") or [])),
                        frozenset(str(t).strip().lower() for t in (r.get("targets") or ["command"])),
                        frozenset(str(t).strip().lower() for t in (r.get("profiles") or [])),
                        path_rule))
        except re.error as e:
            inactive.append((i, str(r.get("reason") or ""), [f"bad regex: {e}"]))
            continue
    return out, ("ok" if out else "broken"), inactive


def policy_mode(doc: dict) -> str:
    """shadow | enforce: KREW_T0_MODE, else the policy's `mode:`, else shadow."""
    v = (os.environ.get("KREW_T0_MODE") or str(doc.get("mode") or "") or "shadow").strip().lower()
    return v if v in ("shadow", "enforce") else "shadow"


def routes(doc: dict) -> dict:
    out = dict(ROUTE_GUIDANCE)
    extra = doc.get("routes") if isinstance(doc.get("routes"), dict) else {}
    out.update({str(k).lower(): str(v) for k, v in extra.items() if v})
    vars_ = policy_vars(doc)
    return {k: _fill_text(v, vars_) for k, v in out.items()}


def trusted_repos(doc: dict) -> list[Path]:
    env = (os.environ.get("KREW_T0_TRUSTED") or "").strip()     # colon-separated; overrides the policy's list
    if env:
        return [Path(_expand_home(x)) for x in env.split(":") if x.strip()]
    vars_ = policy_vars(doc)
    paths, _ = _fill_paths(doc.get("trusted_repos") or [], vars_)
    return [Path(p) for p in paths]


def _is_crew() -> bool:
    if (os.environ.get("HERMES_KANBAN_TASK") or "").strip():
        return True
    home = Path(os.environ.get("HERMES_HOME", ""))           # a profile's own home is <root>/profiles/<name>
    name = (os.environ.get("HERMES_PROFILE") or (home.name if home.parent.name == "profiles" else "") or "").strip()
    return name not in ("", "default")


def _haystack(payload: dict) -> str:
    # Hermes shell hooks: tool_input; museum/legacy: arguments|args
    args = (
        payload.get("tool_input")
        or payload.get("arguments")
        or payload.get("args")
        or {}
    )
    if isinstance(args, str):
        return args
    parts = []
    for k in TEXT_FIELDS:
        v = args.get(k) if isinstance(args, dict) else None
        if isinstance(v, str):
            parts.append(v)
    if not parts and isinstance(args, dict):
        parts.append(json.dumps(args))
    return "\n".join(parts)


SCRIPT_BODY = _switch("KREW_T0_SCRIPT_BODY", "1") != "0"
TRUSTED: list[Path] = []      # the policy's trusted_repos, set by main()
SCRIPT_MAX_BYTES = 512 * 1024
_INTERPRETERS = re.compile(r"^(?:python[0-9.]*|pypy[0-9.]*|bash|sh|zsh|dash|ksh|node|nodejs|perl|ruby|php|deno|bun)$")
_PREFIXES = {"sudo", "exec", "nice", "nohup", "time", "env", "command", "stdbuf", "ionice", "setsid"}
_SCRIPT_EXT = re.compile(r"\.(?:py|sh|bash|zsh|js|mjs|cjs|ts|pl|rb|php)$", re.I)
_SPLIT = re.compile(r"\|\||&&|[;|&\n()`]|\$\(")


def _words(segment: str) -> list[str]:
    import shlex
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _executed_files(command: str, cwd: str) -> list[Path]:
    """Files a shell command runs: the operand of an interpreter, of source / ., of `uv run`,
    or a path in command position. Modules (-m) and -c/-e code are not files."""
    out: list[Path] = []
    here = cwd or os.getcwd()
    for seg in _SPLIT.split(command or ""):
        w = _words(seg)
        if len(w) >= 2 and w[0] in ("cd", "pushd"):
            d = Path(os.path.expanduser(w[1]))
            here = str(d if d.is_absolute() else Path(here) / d)
            continue
        i = 0
        while i < len(w) and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[i]) or w[i] in _PREFIXES
                              or (w[i] == "timeout" and i + 1 < len(w))):
            i += 2 if w[i] == "timeout" else 1
            while i < len(w) and w[i].startswith("-") and w[i - 1] in ("sudo", "env", "nice", "timeout"):
                i += 1
        if i < len(w) and w[i] == "uv" and i + 1 < len(w) and w[i + 1] == "run":
            i += 2
            while i < len(w) and w[i].startswith("-"):
                i += 2 if w[i] in ("--with", "--python", "-p", "--project", "--directory") else 1
        if i >= len(w):
            continue
        head = os.path.basename(w[i])
        target = None
        operand = False
        if head in ("source", ".") and i + 1 < len(w):
            target = w[i + 1]
            operand = True
        elif _INTERPRETERS.match(head):
            j = i + 1
            while j < len(w) and w[j].startswith("-"):
                if w[j] in ("-c", "-e", "-m", "--eval"):
                    j = len(w)
                    break
                j += 2 if w[j] in ("-X", "-W", "-O", "--require", "-r") else 1
            target = w[j] if j < len(w) else None
            operand = True
        elif "/" in w[i]:
            target = w[i]
            operand = False
        if not target:
            continue
        path = Path(os.path.expanduser(target))
        if not path.is_absolute():
            path = Path(here) / path
        if path.exists() and path.is_file() and _runs(path, operand=operand):
            out.append(path)
    return out


# A data file is read, not run (the owner 2026-10-01: 42 of 42 report-day hits were data). The segment split
# runs inside `python3 -c "..."` code and heredoc bodies too, so `json.load(open('/srv/.../decisions.json'))`
# left a bare path in command position and the ledger's own prose ("a clean `systemctl stop`") read as a
# script. Kill: KREW_T0_DATA_IS_NOT_RUN=0.
_DATA_EXT = re.compile(r"\.(?:json|jsonl|txt|md|db|sqlite3?|ya?ml|csv|tsv|log|ini|toml|cfg|conf|xml|html?|"
                       r"jpe?g|png|gif|webp|wav|mp[34]|pdf|lock|bak|orig)(?:[.-][\w.-]*)?$", re.I)
DATA_IS_NOT_RUN = _switch("KREW_T0_DATA_IS_NOT_RUN", "1") != "0"


def _runs(path: Path, *, operand: bool) -> bool:
    """Would this file actually run? An interpreter / source operand runs unless it is plainly data; a path in
    command position runs only when it is executable or starts with `#!`."""
    if not DATA_IS_NOT_RUN:
        return True
    if operand:
        return not (_DATA_EXT.search(path.name) and not _SCRIPT_EXT.search(path.name))
    try:
        if os.access(path, os.X_OK):
            return True
        with path.open("rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def _trusted(path: Path) -> bool:
    """A file tracked and unmodified in one of the policy's trusted_repos, or a root-owned system file."""
    import subprocess
    try:
        real = path.resolve()
        if real.stat().st_uid == 0:
            return True
        for repo in TRUSTED:
            root = repo.resolve()
            if root not in real.parents:
                continue
            rel = str(real.relative_to(root))
            tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
                                     capture_output=True, timeout=5).returncode == 0
            clean = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", rel],
                                   capture_output=True, timeout=5).returncode == 0
            return tracked and clean
        return False
    except Exception:
        return False


def _script_bodies(command: str, cwd: str, depth: int = 0) -> list[tuple[str, str]]:
    """(path, text) for every untrusted file the command runs; text None-safe: an unreadable
    file comes back as ("path", "\0unreadable"). A shell script's own runs are followed once."""
    found: list[tuple[str, str]] = []
    for path in _executed_files(command, cwd):
        if _trusted(path):
            continue
        try:
            with path.open("rb") as fh:
                text = fh.read(SCRIPT_MAX_BYTES).decode("utf-8", "replace")
        except OSError:
            found.append((str(path), "\0unreadable"))
            continue
        found.append((str(path), text))
        shell = path.suffix in (".sh", ".bash") or re.match(r"#!\S*(?:/|env\s+)(?:ba|z|da|k)?sh\b", text)
        if depth < 1 and shell:
            found += _script_bodies(text, str(path.parent), depth + 1)
    return found


def _write_payload(tool: str, args: dict) -> str:
    """The script text a write_file / patch puts on disk, else ''."""
    if tool not in ("write_file", "patch") or not isinstance(args, dict):
        return ""
    path = str(args.get("path") or args.get("file_path") or "")
    body = "\n".join(str(args.get(k) or "") for k in ("content", "new_string", "patch"))
    if _SCRIPT_EXT.search(path) or body.lstrip().startswith("#!"):
        return body
    if tool == "patch" and not (DATA_IS_NOT_RUN and path and _DATA_EXT.search(path)):
        return body  # a patch with no path, or to a file that is not plainly data, is still read
    return ""


def _path_rule(r: dict, vars_: dict | None = None) -> tuple[dict, list[str]]:
    """(the path keys of one rule, or {} for a text rule; vars a path named that are unset). A path whose var
    is unset is left out (and named by --lint); `write_block_names` are globs, never expanded."""
    keys = ("write_roots", "write_block", "write_except", "write_block_always")
    out: dict = {}
    missing: list[str] = []
    for k in keys:
        if r.get(k):
            got, miss = _fill_paths(r.get(k), vars_ or {})
            missing += miss
            if got:
                out[k] = tuple(got)
    if r.get("write_block_names"):
        out["write_block_names"] = tuple(str(x) for x in r["write_block_names"])
    return out, missing


def _real(path: str) -> Path:
    return Path(os.path.realpath(_expand_home(path)))


def _under(real: Path, roots) -> bool:
    for root in roots:
        try:
            r = _real(root)
        except Exception:  # noqa: BLE001
            continue
        if real == r or r in real.parents:
            return True
    return False


def _blocked_write(args: dict, rule: dict, cwd: str) -> str:
    """'' when the write is allowed by a path rule; else what was refused (2026-10-01).
    Links and `..` are resolved on both sides; a call that names no single path is refused (a multi-file patch)."""
    if rule.get("write_roots") and not any(k in rule for k in ("write_block", "write_block_always", "write_block_names")):
        return _outside_roots(args, rule["write_roots"], cwd)
    path = str((args or {}).get("path") or (args or {}).get("file_path") or "").strip() if isinstance(args, dict) else ""
    if not path:
        return "no single path to check"
    p = Path(_expand_home(path))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    try:
        real = Path(os.path.realpath(p))
    except Exception:  # noqa: BLE001 - unresolvable counts as blocked
        return str(p)
    if _under(real, rule.get("write_block_always", ())):
        return f"{real} (always blocked)"
    if any(fnmatch.fnmatch(real.name, g) for g in rule.get("write_block_names", ())):
        return f"{real} (a credential or board file)"
    if _under(real, rule.get("write_block", ())) and not _under(real, rule.get("write_except", ())):
        return f"{real} (a blocked folder)"
    return ""


def _profile_name() -> str:
    return (os.environ.get("HERMES_PROFILE") or Path(os.environ.get("HERMES_HOME", "")).name or "").strip().lower()


def _outside_roots(args: dict, roots: tuple, cwd: str) -> str:
    """'' when the write's path resolves inside one of `roots`; else what was refused (2026-10-01).
    Links and `..` are resolved on both sides; a call that names no single path is refused (a multi-file patch)."""
    path = str((args or {}).get("path") or (args or {}).get("file_path") or "").strip() if isinstance(args, dict) else ""
    if not path:
        return "no single path to check"
    p = Path(_expand_home(path))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    real = p
    try:
        real = Path(os.path.realpath(p))
        for root in roots:
            r = Path(os.path.realpath(_expand_home(root)))
            if real == r or r in real.parents:
                return ""
    except Exception:  # noqa: BLE001 - unresolvable counts as outside
        pass
    return str(real)


WHY_LIB = Path(os.environ.get("KREW_T0_WHY_LIB") or HERE)


def _why_policy(doc: dict) -> dict | None:
    """The policy's `why_before_fix:` block, or None."""
    w = doc.get("why_before_fix") if isinstance(doc, dict) else None
    return w if isinstance(w, dict) else None


def _why_findings(tool: str, args, cwd: str, w: dict) -> list:
    if str(WHY_LIB) not in sys.path:
        sys.path.insert(0, str(WHY_LIB))
    import why_before_fix  # noqa: E402 - ships beside this file
    cfg = {k: v for k, v in w.items() if k not in ("mode", "scope", "env_off", "doc")}
    return why_before_fix.check(tool, args, cwd=cwd, card=(os.environ.get("HERMES_KANBAN_TASK") or "").strip(), cfg=cfg), why_before_fix


def _record(entry: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        print(json.dumps({}))
        return 0

    global TRUSTED
    doc, dstate = _load_doc()
    rules, state, _inactive = _load_rules(doc, dstate)
    if state == "broken" and not BROKEN_OPEN:
        # Present but unusable: a hand edit broke it. Fail CLOSED (enforce) —
        # nothing acts until the policy parses; shadow only says so. The policy's own
        # mode cannot be read, so only KREW_T0_MODE=shadow keeps this to a note.
        mode = "shadow" if _switch("KREW_T0_MODE", "") == "shadow" else "enforce"
        _record({"ts": datetime.now().isoformat(timespec="seconds"), "mode": mode,
                 "event": "policy_broken", "policy": str(POLICY),
                 "note": "T0 FAILED CLOSED - policy present but unparseable / no rules"})
        msg = (f"T0 policy unreadable ({POLICY}): every act is denied until it parses "
               f"(route: protected). Fix the YAML; `python3 tier0_gate.py --lint` names the problem.")
        if mode == "enforce":
            print(json.dumps({"decision": "block", "reason": msg}))
        else:
            print(json.dumps({"context": f"[T0 shadow] would block: {msg}"}))
        return 0
    if state != "ok":
        # Missing (or broken with the kill set): fail OPEN, loudly — an
        # invisible guard is worse than none, and a fresh install must not stall.
        _record({"ts": datetime.now().isoformat(timespec="seconds"),
                 "event": "policy_unavailable", "policy": str(POLICY),
                 "note": "T0 FAILED OPEN — no rules loaded"})
        print(json.dumps({"context": f"[T0 gate inactive: no policy at {POLICY}; krew-install copies one]"}))
        return 0
    MODE = policy_mode(doc)
    ROUTES = routes(doc)
    TRUSTED = trusted_repos(doc)

    tool = payload.get("tool_name") or payload.get("tool") or "?"
    hay = _haystack(payload)
    if not hay.strip():
        print(json.dumps({}))
        return 0

    crew = _is_crew() if any(r[3] == "crew" for r in rules) else False
    args = payload.get("tool_input") or payload.get("arguments") or payload.get("args") or {}
    cwd = str((args.get("workdir") if isinstance(args, dict) else None) or payload.get("cwd") or "")
    bodies = None      # read once, only when a rule asks for it
    reported = []
    for rx, reason, route, scope, rule_mode, env_off, tools, targets, profiles, write_roots in rules:
        if scope == "crew" and not (CREW_RULES and crew):
            continue
        if profiles and _profile_name() not in profiles:
            continue
        if env_off and os.environ.get(env_off, "1").strip() == "0":
            continue
        m, where = None, ""
        if write_roots:
            # A folder rule (2026-10-01): the write's resolved path, not its text.
            if tools and tool not in tools:
                continue
            outside = _blocked_write(args if isinstance(args, dict) else {}, write_roots, cwd)
            if not outside:
                continue
            m, where = re.match(r".*", outside[:120]), "outside"
            reason = f"{reason} ({outside[:160]})"
        elif "command" in targets and (not tools or tool in tools):
            m = rx.search(hay)
        if not m and not write_roots and "script" in targets and SCRIPT_BODY and (not tools or tool in tools):
            if bodies is None:
                bodies = _script_bodies(hay, cwd)
            for spath, text in bodies:
                if text == "\0unreadable":
                    m, where = re.match(r".*", "unreadable script"), spath
                    break
                m = rx.search(text) or rx.search(re.sub(r"\s+", " ", text))
                if m:
                    where = spath
                    break
        if not m and not write_roots and "write" in targets and SCRIPT_BODY:
            wtext = _write_payload(tool, args if isinstance(args, dict) else {})
            if wtext:
                m = rx.search(wtext) or rx.search(re.sub(r"\s+", " ", wtext))
                where = "write" if m else ""
        if not m:
            continue
        if where and where not in ("write", "outside"):
            reason = f"{reason} (in the script it runs: {where})"
        elif where == "write":
            reason = f"{reason} (in the script being written)"
        mode = rule_mode if rule_mode in ("report", "enforce") else MODE
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "mode": mode,
                 "tool": tool, "reason": reason, "route": route,
                 "match": m.group(0)[:120], "snippet": hay[:200],
                 "profile": os.environ.get("HERMES_PROFILE") or Path(
                     os.environ.get("HERMES_HOME", "")).name}
        _record(entry)
        guidance = ROUTES.get(route, ROUTES["assent"])
        if mode == "report":
            # Report-only rule: allow, say what would be refused, keep scanning.
            reported.append(f"[T0 report] would refuse: {reason} (route: {route}). {guidance}")
            continue
        if mode == "enforce":
            print(json.dumps({
                "decision": "block",
                "reason": f"T0 blocked: {reason} (route: {route}). {guidance}",
            }))
        else:
            # Shadow: allow, but tell the agent what would have happened.
            print(json.dumps({
                "context": f"[T0 shadow] would block: {reason} (route: {route}). {guidance}",
            }))
        return 0

    # Why before fix: only after every rule above has had its turn (a refusal has already returned), and only
    # when none of them spoke - one hit per call, an earlier rule's word stands. No lib, no rule (silently).
    w = _why_policy(doc) if not reported and (WHY_LIB / "why_before_fix.py").is_file() else None
    if w and os.environ.get(str(w.get("env_off") or "KREW_T0_WHY"), "1").strip() != "0" and (
            str(w.get("scope") or "crew").lower() != "crew" or (CREW_RULES and _is_crew())):
        why_mode = str(w.get("mode") or "report").strip().lower()
        try:
            findings, wbf = _why_findings(tool, args, cwd, w)
        except Exception as e:  # noqa: BLE001 - this rule never blocks on its own failure
            findings, wbf = [], None
            _record({"ts": datetime.now().isoformat(timespec="seconds"), "event": "why_error", "tool": tool,
                     "error": f"{type(e).__name__}: {str(e)[:200]}", "profile": _profile_name()})
        modes = w.get("modes") if isinstance(w.get("modes"), dict) else {}
        for f in findings:
            route = "crissa" if f.cause == "known" else "why"
            msg = wbf.message(f)
            # Arming can go by cause (the owner 2026-10-02, replay): `modes: {known: enforce, display: enforce,
            # unknown: report}` - the kind's own key wins over the cause's, else the block's `mode`.
            kind_key = f"{f.action.kind}_{f.cause}" if f"{f.action.kind}_{f.cause}" in modes else f.action.kind
            why_mode = str(modes.get(kind_key) or modes.get(f.cause) or w.get("mode") or "report").strip().lower()
            _record({"ts": datetime.now().isoformat(timespec="seconds"), "mode": why_mode, "tool": tool,
                     "reason": msg[:300], "route": route, "match": f"{f.action.kind} {f.action.verb}",
                     "snippet": hay[:200], "profile": os.environ.get("HERMES_PROFILE") or Path(
                         os.environ.get("HERMES_HOME", "")).name,
                     "card": (os.environ.get("HERMES_KANBAN_TASK") or "").strip(), "why": f.as_dict()})
            if why_mode == "enforce":
                print(json.dumps({"decision": "block",
                                  "reason": f"T0 blocked: {msg} (route: {route}). {ROUTES[route]}"}))
                return 0
            # Report mode writes the hits log only: hermes drops a pre_tool_call context anyway, and an
            # allowed call's output stays byte-for-byte what it was.

    print(json.dumps({"context": " ".join(reported)} if reported else {}))
    return 0


def lint(doc: dict | None = None, state: str | None = None) -> list[str]:
    """Every rule that will not run, and why (an unset var, a bad regex), plus the policy's own state."""
    if doc is None:
        doc, state = _load_doc()
    if state == "missing":
        return [f"no policy at {POLICY} (the gate fails OPEN, loudly)"]
    if state == "broken":
        return [f"{POLICY} does not parse (the gate fails CLOSED in enforce mode)"]
    rules, st, inactive = _load_rules(doc, state)
    out = [f"rule {i} {'' if m and m[0].startswith('PARTIAL') else 'INACTIVE '}({', '.join(m)}): {reason[:100]}"
           for i, reason, m in inactive]
    if st == "broken":
        out.append(f"{POLICY} has no usable rules (the gate fails CLOSED in enforce mode)")
    return out


def _cli(argv: list[str]) -> int:
    if argv and argv[0] == "--lint":
        doc, state = _load_doc()
        rules, _, _ = _load_rules(doc, state)
        problems = lint(doc, state)
        print(f"policy {POLICY}: {state}, mode {policy_mode(doc) if state == 'ok' else '?'}, "
              f"{len(rules)} active rule(s), {len(problems)} note(s)")
        for line in problems:
            print("  " + line)
        return 1 if state != "ok" else 0
    return main()


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
