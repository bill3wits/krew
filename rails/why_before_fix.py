"""Why before fix (krew security rails; lifted as-is from the crew it was built for): before the crew reverts, reloads, restarts, re-enables or
"repairs" something, it finds out whether the owner or a session changed it on purpose.

Two halves, both read-only:

* ``classify(tool, args)`` names the revert-type actions in one tool call: a display reload / home /
  tab / close (also inside ``tool_call``, the Tool Search path the crew reaches the display by), a git
  revert / reset --hard / checkout -- / restore / stash / clean in a repo, a unit start / stop / restart
  / enable / daemon-reload, a switch file or unit file written, a package re-pinned in an env, a file
  put back from a backup, and a process kill.
* ``intent_for(action)`` reads the records that already hold intent - git log and status, switch
  files, ``restart-owed.json``, the journal, the board, the M&S ledger and env hold, hermes's install
  facts, the display browser's tab list, the process table - over a 7-day lookback, and sorts the
  action into a cause:

  - ``known``: a Zoey (or User) board ruling that keeps something as it is, or a change the owner made
    (a commit, switch or hold on the owner's word, the owner's own tabs). It goes to Zoey with the record;
    she settles it, reaching the owner through her list when it needs him.
  - ``unknown``: nothing on record says why. The worker finds out before acting and writes
    ``why: <target> - <what it found>`` on its card.

No new store: everything is read from where it already lives. T0 (rails/tier0_gate.py, beside this file) calls
``check`` for a crew call; the rule ships REPORT-ONLY. Kill: KREW_T0_WHY=0. Every machine value (the repos read,
the display's own tabs, the M&S ledger, where the packs live) comes from the policy's `why_before_fix:` block.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

def _real_home() -> Path:
    v = os.environ.get("KREW_REAL_HOME") or os.environ.get("HERMES_REAL_HOME")
    if v:
        return Path(v)
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError):
        return Path.home()


REAL_HOME = _real_home()
HERMES = Path(os.environ.get("KREW_HERMES_ROOT") or REAL_HOME / ".hermes")
DAY = 86400.0

DEFAULTS: dict[str, Any] = {
    # The machine's values live in the policy ($KREW_HOME/tier0.yaml `why_before_fix:`); these are the neutral
    # fallbacks a fresh install gets (nothing user-, seat- or machine-specific in code).
    "lookback_days": 7,
    "user_markers": ["the owner"],        # a commit, row or hold made on the owner's word says so
    "rulers": ["zoey", "user", "dashboard"],
    "keep_verdicts": ["park", "archive", "drop", "wait", "later", "hands", "no", "keep", "leave", "hold"],
    "own_tabs": [],                      # tabs the crew opens in the display browser; any other is the owner's
    "display_unit": "",                  # the unit that runs the display browser
    "display_cdp": "",                   # its tab list (read with one GET)
    "env_update_grep": r"hermes update|hermes-tree update|rebuild",
    "why_comment_ttl_s": 7200,           # a worker's own `why:` comment settles an unknown cause this long
    "repos": [],                         # repos whose history is read; the first is the crew's own
    "ms_ledger": "",                     # the M&S decisions ledger (JSON); "" = not read (env MS_LEDGER wins)
    "pack_paths": {},                    # {skill: "<repo path>/{skill}", profile: "<repo path>/{profile}"}: where
                                         #   the deployment copy of a profile's skills / config lives in repos[0]
}


@dataclass
class Action:
    kind: str        # display | git | unit | switch | unit-file | env | restore | kill
    verb: str
    target: str
    detail: dict = field(default_factory=dict)


@dataclass
class Finding:
    action: Action
    cause: str                       # known | unknown | settled
    records: list[dict] = field(default_factory=list)

    @property
    def settle_by(self) -> str:
        return "zoey" if self.cause == "known" else ("worker" if self.cause == "unknown" else "")

    def as_dict(self) -> dict:
        return {"kind": self.action.kind, "verb": self.action.verb, "target": self.action.target,
                "cause": self.cause, "settle_by": self.settle_by, "records": self.records[:8]}


# --------------------------------------------------------------------------- classify

DISPLAY_TOOLS = ("show_me", "team_display")
DISPLAY_VERBS = ("home", "reload", "close", "tab", "view", "activate")
SHELL_TOOLS = ("terminal", "execute_code", "process", "process_manage")


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def unwrap(tool: str, args: Any) -> list[tuple[str, dict]]:
    """(tool, args) for every call this one makes: `tool_call` carries its calls in `calls`."""
    args = _json(args)
    if tool == "tool_call" and isinstance(args, dict):
        out = []
        for c in args.get("calls") or []:
            if isinstance(c, dict) and c.get("name"):
                a = _json(c.get("arguments") or c.get("args") or {})
                out.append((str(c["name"]), a if isinstance(a, dict) else {}))
        return out
    return [(tool, args if isinstance(args, dict) else {})]


def _expand_vars(cmd: str) -> str:
    """Put simple `NAME=value` assignments made earlier in the command into later `$NAME` / `${NAME}`."""
    env: dict[str, str] = {}
    out = []
    for line in re.split(r"(;|&&|\|\||\n)", cmd):
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*$", line)
        for k, v in env.items():
            line = line.replace("${" + k + "}", v).replace("$" + k, v)
        if m:
            env[m.group(1)] = m.group(2).strip("'\"")
        out.append(line)
    return "".join(out)


def _repo_of(cmd: str, cwd: str) -> str:
    m = re.search(r"\bgit\s+-C\s+(\S+)", cmd)
    if m:
        return os.path.expanduser(m.group(1).strip("'\""))
    cds = re.findall(r"\bcd\s+([^\s;&|]+)", cmd)
    if cds:
        return os.path.expanduser(cds[-1].strip("'\""))
    return cwd or ""


_GIT = [("revert", r"\bgit\b[^;&|\n]*\brevert\b(?!\s+--abort)"),
        ("reset --hard", r"\bgit\b[^;&|\n]*\breset\s+--hard"),
        ("checkout --", r"\bgit\b[^;&|\n]*\bcheckout\s+(?:\S+\s+)?--\s+\S"),
        ("restore", r"\bgit\b[^;&|\n]*\brestore\s+(?!--staged\b)\S"),
        ("stash", r"\bgit\b[^;&|\n]*\bstash(?:\s+(?:push|save)\b|\s+-|\s*$|\s*[;&|])(?!\s*(?:list|show|pop|apply|drop))"),
        ("clean", r"\bgit\b[^;&|\n]*\bclean\s+-\w*f")]
_UNIT = re.compile(r"\bsystemctl\s+(?:--user\s+|--system\s+|-q\s+|--no-block\s+)*"
                   r"(restart|start|stop|try-restart|reload-or-restart|enable|disable|unmask|reset-failed|kill|daemon-reload)"
                   r"((?:\s+(?!--)[\w@.:-]+)*)")
_SWITCH = re.compile(r"(?:\brm\b(?:\s+-\w+)*|\btouch\b|\bmv\b|>>?)\s*(\S*\.(?:off|on|mode))\b")
_UNIT_FILE = re.compile(r"(?:\bcp\b|\bmv\b|\binstall\b|\btee\b|>>?|sed\s+-i)[^;&|\n]*?(\S*systemd/user/\S+)")
# A profile's capability put in place by hand: a skill, its config or its SOUL (replay 10-02: on 09-26 a steward
# worker copied another profile's skill into its own skills folder and edited it; neither T0 nor this rule saw it).
_PROFILE_FILE = re.compile(r"(?:\bcp\b|\bmv\b|\binstall\b|\btee\b|\brsync\b|>>?|sed\s+-i)[^;&|\n]*?"
                           r"(\S*/\.hermes/profiles/[\w-]+/(?:skills/\S+|config\.yaml|SOUL\.md))")
_PIP = re.compile(r"\b(?:uv\s+pip|pip3?|python[0-9.]*\s+-m\s+(?:uv\s+)?pip)\s+(install|uninstall)\b([^;&|\n]*)")
_RESTORE = re.compile(r"\b(?:cp|mv)\b(?:\s+-\w+)*\s+(\S+\.(?:bak|orig|pre)[\w.-]*)\s+(\S+)")
_KILL = re.compile(r"\b(pkill|killall)\b((?:\s+-\w+)*)\s+(\"[^\"]+\"|'[^']+'|\S+)|\bkill\s+-(?:9|KILL|TERM|15|s\s+\w+)\s+(\d+)")


def _split(text: str) -> list[str]:
    """shlex words, or plain words when the text has an unbalanced quote (a heredoc, a cut-off command)."""
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _drop_prints(cmd: str) -> str:
    """Printing a command does not run it: blank `echo` / `printf` segments."""
    parts = re.split(r"(;|&&|\|\||\n)", cmd)
    return "".join("" if re.match(r"\s*(?:echo|printf)\b", p) else p for p in parts)


def _shell_actions(cmd: str, cwd: str) -> list[Action]:
    cmd = _drop_prints(_expand_vars(cmd or ""))
    acts: list[Action] = []
    for verb, rx in _GIT:
        m = re.search(rx, cmd, re.M)
        if m:
            seg = re.split(r";|&&|\|\||\n", cmd[m.start():], maxsplit=1)[0]
            paths = _split(seg.split(" -- ", 1)[1]) if " -- " in seg else []
            if verb in ("checkout --", "restore") and not paths:
                paths = [w for w in _split(seg)[2:] if not w.startswith("-")][-1:]
            acts.append(Action("git", verb, _repo_of(cmd, cwd), {"command": cmd[:300], "paths": paths}))
    for m in _UNIT.finditer(cmd):
        verb = m.group(1)
        if verb == "daemon-reload":
            continue  # loads changed unit files, restarts nothing; the unit-file write itself is what is read
        # a unit name, never shell syntax that follows it (replay 10-02: `2>&1` read as unit "2")
        units = [u for u in (m.group(2) or "").split() if re.fullmatch(r"[A-Za-z][\w@.:-]*", u)
                 and u not in ("systemctl", "ls", "echo", "sleep", "cat", "grep")]
        for u in units:
            acts.append(Action("unit", verb, u if "." in u else u + ".service"))
    for m in _SWITCH.finditer(cmd):
        acts.append(Action("switch", "write", os.path.expanduser(m.group(1))))
    for m in _UNIT_FILE.finditer(cmd):
        acts.append(Action("unit-file", "write", os.path.expanduser(m.group(1).rstrip("'\""))))
    for m in _PROFILE_FILE.finditer(cmd):
        p = os.path.expanduser(m.group(1).rstrip("'\";"))
        if p.startswith(str(HERMES / "profiles") + "/"):   # the live profiles only, never a sandbox copy nested elsewhere
            acts.append(Action("profile-file", "write", p))
    for m in _PIP.finditer(cmd):
        rest = m.group(2)
        pins = re.findall(r"['\"]?([A-Za-z0-9_.\-\[\]]+)==([\w.]+)['\"]?", rest)
        force = "--force-reinstall" in rest or m.group(1) == "uninstall"
        if "--dry-run" in rest or not (pins or force):
            continue
        env = re.search(r"(?:--python|-p)\s+(\S+)", rest)
        target = env.group(1).strip("'\"") if env else ""
        if not target:
            pre = cmd[:m.start()].split()
            target = pre[-1] if pre and "/bin/" in pre[-1] else ""
        envdir = re.sub(r"/bin/[^/]+$", "", target) if target else ""
        acts.append(Action("env", m.group(1), envdir,
                           {"packages": {p.split("[")[0].lower(): v for p, v in pins}}))
    for m in _RESTORE.finditer(cmd):
        acts.append(Action("restore", "from-backup", os.path.expanduser(m.group(2)), {"backup": m.group(1)}))
    for m in _KILL.finditer(cmd):
        if m.group(1):
            acts.append(Action("kill", m.group(1), m.group(3).strip("'\""), {"full": "-f" in (m.group(2) or "")}))
        elif m.group(4):
            acts.append(Action("kill", "kill", m.group(4), {"pid": True}))
    return acts


def classify(tool: str, args: Any, cwd: str = "") -> list[Action]:
    acts: list[Action] = []
    for name, a in unwrap(tool, args):
        if name in DISPLAY_TOOLS:
            verb = str(a.get("action") or a.get("what") or "").strip().lower()
            if verb in DISPLAY_VERBS:
                acts.append(Action("display", verb, "display", {"tool": name, "args": a}))
        elif name in SHELL_TOOLS:
            acts += _shell_actions(str(a.get("command") or a.get("code") or a.get("input") or ""),
                                   str(a.get("workdir") or cwd or ""))
        elif name in ("write_file", "patch"):
            p = str(a.get("path") or a.get("file_path") or "")
            if re.search(r"\.(?:off|on|mode)$", p):
                acts.append(Action("switch", "write", os.path.expanduser(p)))
            elif "systemd/user/" in p:
                acts.append(Action("unit-file", "write", os.path.expanduser(p)))
            elif (re.search(r"/\.hermes/profiles/[\w-]+/(?:skills/|config\.yaml$|SOUL\.md$)", p)
                  and os.path.expanduser(p).startswith(str(HERMES / "profiles") + "/")):
                acts.append(Action("profile-file", "write", os.path.expanduser(p)))
    return acts


# --------------------------------------------------------------------------- sources (read-only)

def _run(argv: list[str], timeout: float = 4.0) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:  # noqa: BLE001 - a missing record is a missing record
        return ""


class LiveSources:
    """The records where they already live. Every read is read-only and bounded."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def git_log(self, repo: str, since: float, until: float, paths=(), grep: str = "") -> list[dict]:
        if not repo or not Path(repo, ".git").exists():
            return []
        argv = ["git", "-C", repo, "log", "--all", f"--since=@{int(since)}", f"--until=@{int(until)}",
                "--format=%h%x1f%at%x1f%s"]
        if grep:
            argv += ["-i", "-E", f"--grep={grep}"]
        if paths:
            argv += ["--", *paths]
        out = []
        for line in _run(argv).splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                out.append({"sha": parts[0], "ts": float(parts[1]), "subject": parts[2]})
        return out

    def git_dirty(self, repo: str, paths=()) -> list[str]:
        if not repo or not Path(repo, ".git").exists():
            return []
        return [l for l in _run(["git", "-C", repo, "status", "--porcelain", "--", *paths]).splitlines() if l.strip()]

    def file_mtime(self, path: str) -> float | None:
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    def restart_owed(self) -> dict:
        try:
            return json.loads((HERMES / "restart-owed.json").read_text())
        except Exception:  # noqa: BLE001
            return {}

    def unit_description(self, unit: str) -> str:
        return _run(["systemctl", "--user", "show", unit, "-p", "Description", "--value"]).strip()

    def unit_starts(self, unit: str, since: float, until: float) -> list[float]:
        out = _run(["journalctl", "--user", "-u", unit, "-o", "short-unix", "--no-pager",
                    f"--since=@{int(since)}", f"--until=@{int(until)}", "--grep", "Started"])
        return [float(l.split()[0]) for l in out.splitlines() if re.match(r"^\d+(\.\d+)?\s", l)]

    def _board(self):
        cur = (HERMES / "kanban" / "current")
        slug = cur.read_text().strip() if cur.is_file() else ""
        db = HERMES / "kanban" / "boards" / slug / "kanban.db"
        if not slug or not db.is_file():
            return None
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)

    def board_comments(self, card: str) -> list[dict]:
        if not card:
            return []
        try:
            c = self._board()
            if c is None:
                return []
            return [{"author": a, "body": b, "ts": float(t)} for a, b, t in
                    c.execute("select author, body, created_at from task_comments where task_id=? order by id", (card,))]
        except sqlite3.Error:
            return []

    def board_blocks(self, card: str) -> list[dict]:
        if not card:
            return []
        try:
            c = self._board()
            if c is None:
                return []
            return [{"kind": k, "payload": p or "", "ts": float(t)} for k, p, t in
                    c.execute("select kind, payload, created_at from task_events where task_id=? and kind in "
                              "('blocked','unblocked') order by id", (card,))]
        except sqlite3.Error:
            return []

    def board_card(self, card: str) -> dict:
        if not card:
            return {}
        try:
            c = self._board()
            row = c.execute("select title, body, created_by from tasks where id=?", (card,)).fetchone() if c else None
        except sqlite3.Error:
            row = None
        return {"title": row[0] or "", "body": row[1] or "", "created_by": row[2] or ""} if row else {}

    def env_hold(self) -> dict:
        try:
            return json.loads((HERMES / "ms-env-hold.json").read_text())
        except Exception:  # noqa: BLE001
            return {}

    def ms_decisions(self) -> list[dict]:
        v = os.environ.get("MS_LEDGER") or str(self.cfg.get("ms_ledger") or "")
        if not v:
            return []
        p = Path(os.path.expanduser(v))
        try:
            d = json.loads(p.read_text())
            rows = d.get("decisions", d)
            return list(rows.values() if isinstance(rows, dict) else rows)
        except Exception:  # noqa: BLE001
            return []

    def hermes_envs(self) -> list[dict]:
        out = []
        for f in (HERMES / "installs").glob("*/facts.json"):
            try:
                env = json.loads(f.read_text())["packages"]["venv"]["environment"]
                out.append({"facts": str(f), "env": env, "installed": f.stat().st_mtime})
            except Exception:  # noqa: BLE001
                continue
        return out

    def display_tabs(self) -> list[dict]:
        import urllib.request
        if not self.cfg.get("display_cdp"):
            return []
        try:
            with urllib.request.urlopen(self.cfg["display_cdp"], timeout=1.0) as r:
                rows = json.loads(r.read().decode())
            return [{"id": t.get("id", ""), "title": t.get("title", ""), "url": t.get("url", "")}
                    for t in rows if t.get("type") == "page"]
        except Exception:  # noqa: BLE001
            return []

    def processes(self) -> list[dict]:
        out = []
        for p in Path("/proc").glob("[0-9]*"):
            try:
                cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                cg = (p / "cgroup").read_text().strip().rsplit("/", 1)[-1]
                out.append({"pid": int(p.name), "cmd": cmd, "comm": (p / "comm").read_text().strip(), "scope": cg})
            except OSError:
                continue
        return out

    def own_scope(self) -> str:
        try:
            return Path("/proc/self/cgroup").read_text().strip().rsplit("/", 1)[-1]
        except OSError:
            return ""


class FixtureSources(LiveSources):
    """Records from a JSON snapshot (KREW_WHY_FIXTURE): replays and tests read a week as it was."""

    def __init__(self, cfg: dict, path: str):
        super().__init__(cfg)
        self.d = json.loads(Path(path).read_text())

    def git_log(self, repo, since, until, paths=(), grep=""):
        rows = [r for r in self.d.get("git_log", []) if since <= r["ts"] <= until
                and (not repo or r.get("repo", repo) == repo)]
        if paths:
            rows = [r for r in rows if any(p in r.get("paths", []) for p in paths)]
        if grep:
            rows = [r for r in rows if re.search(grep, r["subject"], re.I)]
        return rows

    def git_dirty(self, repo, paths=()):
        return list(self.d.get("git_dirty", {}).get(repo, []))

    def file_mtime(self, path):
        return self.d.get("mtimes", {}).get(path)

    def restart_owed(self):
        return self.d.get("restart_owed", {})

    def unit_description(self, unit):
        return self.d.get("unit_descriptions", {}).get(unit, "")

    def unit_starts(self, unit, since, until):
        return [t for t in self.d.get("unit_starts", {}).get(unit, []) if since <= t <= until]

    def board_comments(self, card):
        return self.d.get("board_comments", {}).get(card, [])

    def board_blocks(self, card):
        return self.d.get("board_blocks", {}).get(card, [])

    def env_hold(self):
        return self.d.get("env_hold", {})

    def board_card(self, card):
        return self.d.get("board_cards", {}).get(card, {})

    def ms_decisions(self):
        return self.d.get("ms_decisions", [])

    def hermes_envs(self):
        return self.d.get("hermes_envs", [])

    def display_tabs(self):
        return self.d.get("display_tabs", [])

    def processes(self):
        return self.d.get("processes", [])

    def own_scope(self):
        return self.d.get("own_scope", "")


def sources(cfg: dict) -> LiveSources:
    fx = os.environ.get("KREW_WHY_FIXTURE", "").strip()
    return FixtureSources(cfg, fx) if fx else LiveSources(cfg)


# --------------------------------------------------------------------------- intent_for

def _by_user(text: str, cfg: dict) -> bool:
    return any(m.lower() in (text or "").lower() for m in cfg["user_markers"])


def _first_word(body: str) -> str:
    b = re.sub(r"^(?:UNBLOCK|BLOCKED|SCHEDULED)\s*:\s*", "", (body or "").strip(), flags=re.I)
    m = re.match(r"\W*([A-Za-z]+)", b)
    return m.group(1).lower() if m else ""


def _rulings(card: str, src: LiveSources, cfg: dict, since: float, until: float) -> list[dict]:
    """Rulings on the card that keep something as it is (a park, a done, a wait...)."""
    out = []
    for c in src.board_comments(card):
        if c["author"] in cfg["rulers"] and since <= c["ts"] <= until and _first_word(c["body"]) in cfg["keep_verdicts"]:
            out.append({"source": "board", "card": card, "ts": c["ts"], "by": c["author"],
                        "what": f"{c['author']} ruled {_first_word(c['body'])}: {c['body'][:140]}"})
    return out


def _settled(card: str, src: LiveSources, cfg: dict, until: float) -> bool:
    """Zoey settled it: this gate's own block on the card, then a ruler's unblock after it."""
    blocks = src.board_blocks(card)
    t_block = max((b["ts"] for b in blocks if b["kind"] == "blocked" and "why-before-fix" in b["payload"]), default=None)
    if t_block is None:
        return False
    return any(b["kind"] == "unblocked" and t_block < b["ts"] <= until for b in blocks)


def _investigated(action: Action, card: str, src: LiveSources, cfg: dict, until: float) -> bool:
    """The worker's own `why:` comment on its card names this target, recently."""
    if not card:
        return False
    needle = (action.target or action.verb).rstrip("/").rsplit("/", 1)[-1].lower()
    for c in src.board_comments(card):
        body = c["body"].strip()
        if (c["author"] not in cfg["rulers"] and re.match(r"(?i)why\s*:", body)
                and until - cfg["why_comment_ttl_s"] <= c["ts"] <= until and needle in body.lower()):
            return True
    return False


def _env_names(envdir: str) -> set[str]:
    """What the M&S ledger and the env hold call this env (replay 10-02: a hold on one env read as every env's)."""
    if not envdir:
        return set()
    parts = [p for p in envdir.rstrip("/").split("/") if p]
    names = {parts[-1].lower()}
    if parts[-1] in ("venv", ".venv") and len(parts) > 1:
        names.add(parts[-2].lower())
    if "/.hermes/" in envdir + "/":
        names |= {"hermes", "hermes-venv"}
    return names


def _scratch(path: str) -> bool:
    """A worker's own scratch: a profile cache, a card workspace, /tmp, a worktree (replay 10-02)."""
    return bool(re.search(r"/(?:cache/scratch|workspaces|\.?worktrees|tmp)/|^/tmp/|/\.review-venv\b", (path or "") + "/"))


def _row_before(row: dict, until: float) -> bool:
    """A ledger row counts only if it was written before the action (the replay read today's ledger)."""
    ts = str(row.get("ts") or "")[:10]
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d")) <= until
    except ValueError:
        return True


def _card_asks(action: Action, info: dict) -> bool:
    text = (str(info.get("title") or "") + "\n" + str(info.get("body") or "")).lower()
    if not text.strip():
        return False
    needles = [n.lower() for n in ((action.detail or {}).get("packages") or {})]
    t = (action.target or "").rstrip("/")
    if action.kind != "env" and t:
        needles.append(t.rsplit("/", 1)[-1].lower().replace(".service", ""))
    return any(n and len(n) >= 3 and n in text for n in needles)


def _own_repo(cfg: dict) -> str:
    return os.path.expanduser(str(cfg["repos"][0])) if cfg.get("repos") else ""


def _live_tree(path: str, cfg: dict) -> bool:
    """Inside one of the policy's live trees and not inside a worktree kept under it."""
    return bool(path and _repo_root(path, cfg)) and not re.search(r"/\.(?:claude/)?worktrees/", path + "/")


def _repo_root(path: str, cfg: dict) -> str:
    for r in (os.path.expanduser(str(x)) for x in cfg.get("repos") or []):
        if path == r or path.startswith(r.rstrip("/") + "/"):
            return r
    return ""


def intent_for(action: Action, *, card: str = "", now: float | None = None,
               cfg: dict | None = None, src: LiveSources | None = None) -> Finding:
    """What is on record about why `action.target` is the way it is (7-day lookback)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    src = src or sources(cfg)
    until = time.time() if now is None else now
    since = until - float(cfg["lookback_days"]) * DAY
    known: list[dict] = []
    seen: list[dict] = []

    # A ruler's keep-as-is ruling on the worker's own card covers whatever the card is acting on.
    known += _rulings(card, src, cfg, since, until)

    k, t = action.kind, action.target
    if k == "display":
        own = tuple(cfg["own_tabs"])
        tabs = src.display_tabs()
        theirs = [x for x in tabs if x.get("url") and not str(x["url"]).startswith(own)]
        if theirs:
            known.append({"source": "display-tabs", "by": "the owner",
                          "what": "the display browser holds the owner's tabs: " +
                                  ", ".join(f"{x.get('title') or '?'} <{x['url']}>" for x in theirs[:4])})
        starts = src.unit_starts(cfg["display_unit"], since, until) if cfg["display_unit"] else []
        if starts:
            seen.append({"source": "journal", "ts": max(starts),
                         "what": f"{cfg['display_unit']} started at {time.strftime('%m-%d %H:%M:%S', time.localtime(max(starts)))}"})
        if tabs and not theirs:
            seen.append({"source": "display-tabs", "what": "crew tabs only: " + ", ".join(x.get("id", "") for x in tabs[:4])})
    elif k == "git" and not _live_tree(os.path.expanduser(t or ""), cfg):
        # A move in a worker's own worktree, a scratch checkout or the upstream clone is not live state (replay
        # 10-02: 48 of 64 git flags were workers undoing their own edits there). Only the policy's live trees count.
        return Finding(action, "settled", [{"source": "scope", "what": f"{t} is not a live tree"}])
    elif k in ("git", "restore", "switch", "unit-file"):
        path = t if k != "git" else ""
        repo = os.path.expanduser(t) if k == "git" else _repo_root(t, cfg)
        if k == "git":
            rel = [p for p in (action.detail or {}).get("paths") or [] if p not in (".", "")]
        else:
            rel = [os.path.relpath(path, repo)] if (path and repo) else []
        # A whole-tree move (reset --hard, a bare stash) is judged on the uncommitted work it would take,
        # never on the repo's history: a commit says nothing about keeping someone's work in flight.
        for c in (src.git_log(repo, since, until, paths=rel) if rel else []):
            rec = {"source": "git", "repo": repo, "sha": c["sha"], "ts": c["ts"], "what": c["subject"][:160]}
            (known if _by_user(c["subject"], cfg) else seen).append(rec)
        dirty = src.git_dirty(repo, rel) if repo else []
        if dirty:
            seen.append({"source": "git-status", "repo": repo, "what": f"{len(dirty)} uncommitted: " + "; ".join(dirty[:5])})
        if path:
            mt = src.file_mtime(path)
            if mt and since <= mt <= until:
                seen.append({"source": "file", "ts": mt, "what": f"{path} changed {time.strftime('%m-%d %H:%M', time.localtime(mt))}"})
            name = os.path.basename(path)
            for c in src.git_log(_own_repo(cfg), since, until, grep=re.escape(name)):
                rec = {"source": "git", "sha": c["sha"], "ts": c["ts"], "what": c["subject"][:160]}
                (known if _by_user(c["subject"], cfg) else seen).append(rec)
    elif k == "profile-file":
        # the pack is the deployment truth: read the pack path's own history in the crew's repo (when configured)
        m = re.search(r"/profiles/([\w-]+)/(?:skills/(?:[\w-]+/)*?([\w-]+)/SKILL\.md|skills/([\w-]+)|(config\.yaml|SOUL\.md))", t)
        prof = m.group(1) if m else ""
        skill = (m.group(2) or m.group(3)) if m else ""
        pp = cfg.get("pack_paths") if isinstance(cfg.get("pack_paths"), dict) else {}
        tmpl = str(pp.get("skill") or "") if skill else str(pp.get("profile") or "")
        packs = [tmpl.replace("{skill}", skill).replace("{profile}", prof)] if tmpl else []
        for c in (src.git_log(_own_repo(cfg), since, until, paths=packs) if packs else []):
            rec = {"source": "git", "sha": c["sha"], "ts": c["ts"], "what": c["subject"][:160]}
            (known if _by_user(c["subject"], cfg) else seen).append(rec)
        seen.append({"source": "pack", "what": f"{t} is a live copy" + (
            f"; the pack ({packs[0]}) is the deployment truth" if packs else " of a profile file")})
    elif k == "unit":
        owed = src.restart_owed().get(t) if t else None
        if owed:
            rec = {"source": "restart-owed", "by": owed.get("by", ""), "what": f"{t} owed to the window: {str(owed.get('why'))[:160]}"}
            (known if _by_user(json.dumps(owed), cfg) else seen).append(rec)
        desc = src.unit_description(t) if t else ""
        m = re.search(r"off switch:\s*(\S+)", desc or "")
        if m and src.file_mtime(os.path.expanduser(m.group(1))):
            known.append({"source": "switch", "by": "the owner", "what": f"{t} is switched off by {m.group(1)}"})
        if t:
            for c in src.git_log(_own_repo(cfg), since, until, grep=re.escape(t.replace(".service", ""))):
                rec = {"source": "git", "sha": c["sha"], "ts": c["ts"], "what": c["subject"][:160]}
                (known if _by_user(c["subject"], cfg) else seen).append(rec)
            starts = src.unit_starts(t, since, until)
            if starts:
                seen.append({"source": "journal", "what": f"{t} started {len(starts)}x in the lookback, last "
                                                          f"{time.strftime('%m-%d %H:%M', time.localtime(max(starts)))}"})
    elif k == "env" and _scratch(t):
        return Finding(action, "settled", [{"source": "scope", "what": f"{t} is a scratch env"}])
    elif k == "env":
        envdir = t.rstrip("/")
        pkgs = (action.detail or {}).get("packages") or {}
        names = _env_names(envdir)
        for name, row in (src.env_hold() or {}).items():
            if name and name.lower() in names:
                rec = {"source": "ms-env-hold", "by": row.get("by", ""), "what": f"{name} held: {str(row.get('reason'))[:140]}"}
                (known if _by_user(json.dumps(row), cfg) else seen).append(rec)
        for d in src.ms_decisions():
            if (str(d.get("package", "")).lower() in pkgs and str(d.get("decision")) in ("wontfix", "keep", "hold")
                    and str(d.get("env", "")).lower() in names and not (card and str(d.get("source") or "") == card)
                    and _row_before(d, until)):
                known.append({"source": "ms-ledger", "what": f"{d.get('env')}/{d.get('package')}: {d.get('decision')} - {str(d.get('reason'))[:120]}"})
        for h in src.hermes_envs():
            live = str(h["env"]).rstrip("/")
            under_hermes = envdir.startswith(str(HERMES)) or envdir.startswith("~/.hermes") or "/.hermes/" in envdir
            if not under_hermes:
                continue
            installed = float(h.get("installed") or 0)
            ups = [c for c in src.git_log(_own_repo(cfg), since, until, grep=cfg["env_update_grep"]) if _by_user(c["subject"], cfg)]
            where = ("the env hermes runs" if envdir == live else f"not the env hermes runs ({live})")
            rec = {"source": "hermes-facts", "what": f"{envdir} is {where}; installed "
                   f"{time.strftime('%m-%d %H:%M', time.localtime(installed)) if installed else '?'} per {h.get('facts')}"}
            if since <= installed <= until and ups:
                rec["by"] = "the owner"
                rec["what"] += f"; by the owner's update {ups[0]['sha']} ({ups[0]['subject'][:80]})"
                known.append(rec)
            else:
                seen.append(rec)
    elif k == "kill":
        own = src.own_scope()
        rx = None
        if not action.detail.get("pid"):
            try:
                rx = re.compile(t)
            except re.error:
                rx = re.compile(re.escape(t))
        hit = []
        for p in src.processes():
            if action.detail.get("pid"):
                ok = str(p["pid"]) == t
            elif action.detail.get("full"):
                ok = bool(rx.search(p.get("cmd", "")))
            else:
                ok = bool(rx.search(p.get("comm", "")))
            if ok:
                hit.append(p)
        others = sorted({p["scope"] for p in hit if p.get("scope") and p["scope"] != own})
        seen.append({"source": "processes", "what": f"{len(hit)} match; outside this worker's scope: "
                                                    + (", ".join(others[:5]) or "none")})

    info = src.board_card(card) if card else {}
    by_user = str(info.get("created_by") or "").lower() in ("user", "dashboard")
    if known and by_user:
        # The owner's own card asks for this: his earlier changes are not a reason to stop his ask (replay 10-02:
        # the K+L arm card was stopped by the commits that made K+L). A ruler's keep ruling still stands.
        known = [r for r in known if r.get("source") == "board"]
    if k == "restore" and card and card in str((action.detail or {}).get("backup") or ""):
        return Finding(action, "settled", [{"source": "scope", "what": "puts back this card's own backup"}])
    if known:
        if _settled(card, src, cfg, until):
            return Finding(action, "settled", known + seen)
        return Finding(action, "known", known + seen)
    if k != "display" and _card_asks(action, info):
        # The card is the why: it names what it changes (an M&S land names its package, an install card its units).
        return Finding(action, "settled", seen + [{"source": "board", "what": f"the card {card} names it"}])
    if _investigated(action, card, src, cfg, until):
        return Finding(action, "settled", seen + [{"source": "board", "what": "the worker's own why: comment names it"}])
    return Finding(action, "unknown", seen)


def check(tool: str, args: Any, *, cwd: str = "", card: str = "", now: float | None = None,
          cfg: dict | None = None) -> list[Finding]:
    """Every revert-type action in one tool call, each with its cause (settled ones dropped)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    acts = classify(tool, args, cwd)
    # A profile whose display calls ARE the owner's ask (Zoey's hands show him what he asked for) is not read for
    # display (live check 10-02: her view / home / activate were blocked within minutes of arming).
    me = (os.environ.get("HERMES_PROFILE") or "").strip().lower()
    if me and me in [str(x).lower() for x in cfg.get("display_exempt_profiles") or []]:
        acts = [a for a in acts if a.kind != "display"]
    if not acts:
        return []
    if now is None and os.environ.get("KREW_WHY_NOW", "").strip():
        now = float(os.environ["KREW_WHY_NOW"])  # a replay reads the records as they stood then
    src = sources(cfg)
    out = [intent_for(a, card=card, now=now, cfg=cfg, src=src) for a in acts]
    return [f for f in out if f.cause != "settled"]


def message(f: Finding) -> str:
    """What the gate says once the rule is armed (report mode only records it)."""
    a = f.action
    head = f"why-before-fix: {a.kind} {a.verb} {a.target}".strip()
    recs = "; ".join(r.get("what", "") for r in f.records[:3])
    if f.cause == "known":
        return (f"{head} was set on purpose ({recs}). Block this card for Crissa (--kind needs_input) with a reason "
                f"that starts 'why-before-fix:' and carries that record; she settles it, and asks the owner on her "
                f"list if it needs him.")
    return (f"{head}: nothing on record says why it is the way it is ({recs or 'no record'}). Find out before you "
            f"change it - git log, the journal, the board, the person or process that set it - then write "
            f"'why: {a.target.rsplit('/', 1)[-1] or a.verb} - <what you found>' as a comment on your card and run it again.")


if __name__ == "__main__":  # a hand look at one target: python3 lib/why_before_fix.py '<tool>' '<args json>'
    import sys
    for f in check(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "{}", card=os.environ.get("HERMES_KANBAN_TASK", "")):
        print(json.dumps(f.as_dict(), indent=1))
