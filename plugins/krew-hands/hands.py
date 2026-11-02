"""hands: a voice front door's one action tool, with the full CLI kit behind it.

A spoken front door (Crissa) answers fastest when its prompt carries almost no tool schemas. `hands`
is its one action tool: the handler runs the SAME profile's CLI kit as a one-shot child
(`hermes -p <profile> --cli chat --oneshot -Q -q <job>`) and hands back what the child said. Not a
crew spawn and not delegate_task: the child is the front door itself, with every toolset its config
grants (or `hands.toolsets`), for one job.

One job at a time (a lock file under $KREW_HOME); a child never starts a child (KREW_HANDS_NESTED).
The child's reply comes back whole. Settings: `hands:` in $KREW_HOME/krew.yaml.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess

from . import krew_settings as S

NESTED_FLAG = "KREW_HANDS_NESTED"
RUNTIME_KW = frozenset({"task_id", "session_id", "user_task", "enabled_tools", "parent_agent", "callback",
                        "tool_call_id", "turn_id", "api_request_id", "scope"})
DEFAULTS = {"profile": "crissa", "toolsets": "", "max_turns": 24, "timeout_s": 300, "yolo": False,
            "subdirectory_hints": False}
EMPTY_ASK = ('hands: empty ask. One argument, the job in plain words: {"ask": "list the board, all statuses"}. '
             "No action names, no JSON inside.")
PROMPT_TAIL = ("\n\nEnd with a short summary a person can hear in one breath. Say a thing was filed, shown or "
               "done only if a tool reply in this run says so.")


def settings(override: dict | None = None) -> dict:
    d = dict(DEFAULTS)
    d.update(S.load("hands"))
    d.update(override or {})
    return d


def args_of(args, kwargs: dict) -> dict:
    p: dict = {}
    if isinstance(args, dict):
        p.update(args)
    elif isinstance(args, (list, tuple)) and args and isinstance(args[0], dict):
        p.update(args[0])
    p.update({k: v for k, v in kwargs.items() if k not in RUNTIME_KW})
    for k in RUNTIME_KW:
        p.pop(k, None)
    return p


def ask_from(p: dict) -> str:
    """The job in plain words, from whatever shape the caller sent. A model that has just read a skill
    describing the child's tools often sends {"action": "team_board", "action_args": "{...}"}; the child
    understands "team_board list" perfectly well, so that shape is folded into words, not refused."""
    ask = str(p.get("ask") or p.get("text") or p.get("job") or "").strip()
    if ask:
        return ask
    words: list[str] = []
    action = p.get("action") or p.get("tool") or p.get("name")
    if action:
        words.append(str(action).strip())
    extra = p.get("action_args") or p.get("args") or p.get("arguments") or p.get("params")
    if isinstance(extra, str):
        s = extra.strip()
        if s.startswith("{"):
            try:
                extra = json.loads(s)
            except ValueError:
                extra = s
        else:
            extra = s
    if isinstance(extra, dict):
        for k, v in extra.items():
            if v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            words.append(f"{k} {v}" if str(k) != "action" else str(v))
    elif isinstance(extra, (list, tuple)):
        words.extend(str(x) for x in extra if x not in (None, ""))
    elif extra:
        words.append(str(extra))
    skip = {"ask", "text", "job", "action", "tool", "name", "action_args", "args", "arguments", "params"}
    for k, v in p.items():
        if k not in skip and isinstance(v, str) and v.strip():
            words.append(f"{k} {v.strip()}")
    return " ".join(w for w in words if w).strip()


def hermes_bin() -> str:
    return S.hermes_bin()


def argv_for(ask: str, profile: str, cfg: dict) -> list[str]:
    argv = [hermes_bin()]
    if profile:
        argv += ["-p", profile]
    argv += ["--cli", "--accept-hooks"] + (["--yolo"] if cfg.get("yolo") else []) + ["chat"]
    if str(cfg.get("toolsets") or "").strip():
        argv += ["-t", str(cfg["toolsets"]).strip()]
    argv += ["--oneshot", "--max-turns", str(int(cfg["max_turns"])), "-Q", "-q", ask + PROMPT_TAIL]
    return argv


def child_env(cfg: dict, base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    env[NESTED_FLAG] = "1"
    if not cfg.get("subdirectory_hints"):
        # krew patch 120: a file read never appends a context file (AGENTS.md...) to the tool result
        env["HERMES_SUBDIRECTORY_HINTS"] = "0"
    return env


def run(ask: str, *, profile: str, cfg: dict | None = None, runner=None, lock_path=None) -> str:
    cfg = cfg or settings()
    ask = (ask or "").strip()
    if not ask:
        return EMPTY_ASK
    if os.environ.get(NESTED_FLAG, "").strip().lower() in ("1", "true", "yes", "on"):
        return "hands: already inside a hands run; do the job yourself."
    lock = lock_path or (S.krew_home() / "hands.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "hands: another job is running. Say so, and ask again when it is done."
        try:
            r = (runner or subprocess.run)(argv_for(ask, profile, cfg), capture_output=True, text=True,
                                           timeout=float(cfg["timeout_s"]), env=child_env(cfg))
        except subprocess.TimeoutExpired:
            return (f"hands: the job ran past {cfg['timeout_s']} s and was stopped. It did not finish; say so. "
                    "A smaller piece may fit.")
        except Exception as e:  # noqa: BLE001
            return f"hands {type(e).__name__}: {e}"
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            return f"hands did not finish (rc {r.returncode}). Say plainly it did not work. What it got to:\n{out}"
        return out or f"hands: the job ended with no reply (rc {r.returncode})."
    finally:
        os.close(fd)
