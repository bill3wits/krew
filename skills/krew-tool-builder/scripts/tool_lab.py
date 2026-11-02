#!/usr/bin/env python3
"""krew tool lab: a new tool is written, tested, reviewed, read by T0 and approved by the OWNER before any profile
gets it (the owner 2026-11-01: "how about one skillbuilder skill and tool builder skill").

    tool_lab.py new NAME --desc "..." --profiles bash,lila [--kind script|plugin] [--by PROFILE]
    tool_lab.py test NAME            its own tests, on the lab copy
    tool_lab.py submit NAME          tests green -> one review card to Vera (the same files the tests passed)
    tool_lab.py t0 NAME              the T0 gate reads what the tool runs, writes and reads, per named profile
    tool_lab.py ask-owner NAME       review PASS + T0 clean -> one card blocked on the owner (approve | no)
    tool_lab.py status NAME          every step, read fresh from the cards
    tool_lab.py install NAME         only with all four for the files as they are now; copies + enables per profile
    tool_lab.py remove NAME [--profiles a,b]   puts it back out (disable + delete the copy), every profile by default

The lab is $KREW_HOME/tool-lab/<name>/: `tool.json` (what it is, who asked, the profiles, what it reads / writes /
runs), `plugin/` (plugin.yaml, __init__.py, tool.py, schema.json: the one shape a Hermes profile loads), `tests/`,
`state.json`. Every step is stamped with the sha of plugin/ + tests/, so an edit after a step re-opens the steps after
it: what Vera read, what T0 read and what the owner approved are the bytes that get installed. One log line per verb:
$KREW_HOME/tool-lab/log.jsonl (who, why, when, what happened).

It never enables itself: no verb but `install` writes into a profile, and `install` refuses unless the owner's own
word is on the card (a comment by an author in KREW_OWNER_AUTHORS, default `user,dashboard`; T0 refuses a crew
profile that signs as one). Kill: KREW_TOOL_LAB=0 (every verb refuses).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_hermes as KH  # noqa: E402  where Hermes lives

NAME_RX = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
REVIEWER = os.environ.get("KREW_REVIEWER") or "vera"
OWNER_PROFILE = os.environ.get("KREW_OWNER_PROFILE") or "crissa"       # the front door: the owner's questions land there
OWNER_AUTHORS = tuple(a.strip().lower() for a in (os.environ.get("KREW_OWNER_AUTHORS") or "user,dashboard").split(",")
                      if a.strip())
YES = ("approve", "approved", "yes", "go")
NO = ("no", "deny", "denied", "reject", "rejected")


def krew_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


def hermes_home() -> Path:
    """This profile's own home ($HERMES_HOME as given: the profile a skill or tool installs into), else the root."""
    given = os.environ.get("HERMES_HOME")
    return Path(os.path.expanduser(given)) if given else KH.hermes_home()


def lab_root() -> Path:
    return krew_home() / "tool-lab"


def hermes_bin() -> str:
    given = os.environ.get("HERMES_BIN")
    if given:
        return given
    local = Path.home() / ".local" / "bin" / "hermes"
    return str(local) if local.is_file() else "hermes"


def on() -> bool:
    return os.environ.get("KREW_TOOL_LAB", "1").strip().lower() not in ("0", "false", "no", "off")


def profile_home(p: str) -> Path:
    """A profile's own Hermes home: the default profile is $HERMES_HOME itself."""
    return hermes_home() if p in ("", "default", "main") else hermes_home() / "profiles" / p


class Refused(Exception):
    pass


# ---- the lab ------------------------------------------------------------------------------------------------------

def lab(name: str) -> Path:
    if not NAME_RX.match(name or ""):
        raise Refused(f"name {name!r}: lowercase letters, digits and '-', 2-41 characters, starting with a letter")
    return lab_root() / name


def fn_name(name: str) -> str:
    return name.replace("-", "_")


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def tree_sha(d: Path) -> str:
    """The sha of plugin/ + tests/ (paths and bytes), caches left out: the identity every step is stamped with."""
    h = hashlib.sha256()
    for sub in ("plugin", "tests"):
        base = d / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc" or ".pytest_cache" in p.parts:
                continue
            h.update(str(p.relative_to(d)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def log(name: str, verb: str, by: str, ok: bool, what: str, **extra) -> None:
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": name, "verb": verb, "by": by or "?", "ok": ok,
           "what": what, **extra}
    p = lab_root() / "log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def load(name: str) -> tuple[Path, dict, dict]:
    d = lab(name)
    meta = read_json(d / "tool.json")
    if not meta:
        raise Refused(f"no tool {name!r} in {lab_root()} (start with: new {name})")
    return d, meta, read_json(d / "state.json", {}) or {}


PLUGIN_YAML = """name: {name}
version: 0.1.0
description: >
  {desc}
author: krew tool lab
entry: __init__.py
kind: standalone
provides:
  - toolset: {name}
    tools: [{fn}]
"""

INIT_PY = '''"""{name}: one tool from the krew tool lab. tool.py does the work; schema.json is its arguments."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME_KW = ("task_id", "session_id", "profile", "tool_call_id")


def _tool():
    spec = importlib.util.spec_from_file_location("{fn}_tool", HERE / "tool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def register(ctx) -> None:
    params = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))
    desc = {desc!r}
    mod = _tool()

    def handler(*args, **kwargs):
        p = dict(args[0]) if args and isinstance(args[0], dict) else {{}}
        p.update({{k: v for k, v in kwargs.items() if k not in RUNTIME_KW}})
        try:
            return str(mod.run(p))
        except Exception as e:  # noqa: BLE001
            return "{fn} " + type(e).__name__ + ": " + str(e)

    ctx.register_tool(name="{fn}", toolset="{name}", description=desc,
                      schema={{"name": "{fn}", "description": desc, "parameters": params}}, handler=handler)
'''

TOOL_PY = '''"""{name}: the work. `run(args)` takes the arguments schema.json describes and returns text for the agent.

Run by hand:  python3 tool.py '{{"text": "hello"}}'
"""
from __future__ import annotations

import json
import sys


def run(args: dict) -> str:
    # Replace this body with the tool's job. Keep what it reads, writes and runs listed in tool.json: T0 reads them.
    return str(args.get("text", ""))


if __name__ == "__main__":
    print(run(json.loads(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read() or "{{}}")))
'''

SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "description": "what to work on"}},
          "required": ["text"]}

TEST_PY = '''"""{name}'s own tests: run by `tool_lab.py test {name}` before anything else may happen."""
import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "plugin" / "tool.py"


def _tool():
    spec = importlib.util.spec_from_file_location("tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_it_answers():
    assert _tool().run({{"text": "hello"}}) == "hello"
'''


def cmd_new(a) -> dict:
    d = lab(a.name)
    if d.exists():
        raise Refused(f"{d} exists (edit it, or remove the folder to start over)")
    profiles = [p.strip() for p in (a.profiles or "").split(",") if p.strip()]
    if not profiles:
        raise Refused("--profiles: name the profiles this tool is for (it is installed into those only)")
    if not (a.desc or "").strip():
        raise Refused("--desc: one line on what it does and when to use it (it is the tool's description)")
    fn, desc = fn_name(a.name), a.desc.strip()
    (d / "plugin").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "plugin" / "plugin.yaml").write_text(PLUGIN_YAML.format(name=a.name, fn=fn, desc=desc), encoding="utf-8")
    (d / "plugin" / "__init__.py").write_text(INIT_PY.format(name=a.name, fn=fn, desc=desc), encoding="utf-8")
    (d / "plugin" / "tool.py").write_text(TOOL_PY.format(name=a.name), encoding="utf-8")
    write_json(d / "plugin" / "schema.json", SCHEMA)
    (d / "tests" / f"test_{fn}.py").write_text(TEST_PY.format(name=a.name), encoding="utf-8")
    write_json(d / "tool.json", {"name": a.name, "kind": a.kind, "description": desc, "profiles": profiles,
                                 "by": a.by, "why": a.why or "", "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "reads": [], "writes": [], "runs": []})
    write_json(d / "state.json", {})
    return {"ok": True, "lab": str(d), "next": "write plugin/tool.py + schema.json + tests, list reads/writes/runs in "
                                              "tool.json, then: test"}


def cmd_test(a) -> dict:
    d, meta, st = load(a.name)
    sha = tree_sha(d)
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(d / "tests")],
                           cwd=str(d), capture_output=True, text=True, timeout=int(os.environ.get("KREW_TOOL_LAB_TEST_S",
                                                                                                    "300")),
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        ok, tail = r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()[-600:]
    except subprocess.TimeoutExpired:
        ok, tail = False, "tests timed out"
    st["test"] = {"ok": ok, "sha": sha, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tail": tail}
    write_json(d / "state.json", st)
    return {"ok": ok, "sha": sha[:12], "tail": tail}


# ---- the cards ----------------------------------------------------------------------------------------------------

def kanban(profile: str, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run([hermes_bin(), "-p", profile, "kanban", *args], capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def card_id(out: str) -> str:
    m = re.search(r"\b(t_[0-9a-f]{6,})\b", out or "")
    return m.group(1) if m else ""


def show(profile: str, tid: str) -> dict:
    rc, out = kanban(profile, "show", tid, "--json")
    if rc != 0:
        return {}
    try:
        return json.loads(out[out.index("{"):])
    except ValueError:
        return {}


def first_word(text: str) -> str:
    m = re.match(r"\W*([A-Za-z]+)", text or "")
    return m.group(1).lower() if m else ""


def review_verdict(card: dict) -> str:
    """PASS | FAIL | "" (not yet): the reviewer's completion, or her own comment, says it in its first word."""
    task = card.get("task") or {}
    words = []
    if str(task.get("status") or "") in ("done", "completed"):
        words.append(first_word(str(task.get("result") or card.get("latest_summary") or "")))
    for c in card.get("comments") or []:
        if str(c.get("author") or "").lower() == REVIEWER:
            words.append(first_word(str(c.get("body") or "")))
    for w in reversed(words):
        if w == "pass":
            return "PASS"
        if w in ("fail", "revise"):
            return "FAIL"
    return ""


def owner_word(card: dict) -> tuple[str, str]:
    """("approved" | "denied" | "", who): the last comment by an owner author whose first word rules."""
    word, who = "", ""
    for c in card.get("comments") or []:
        author = str(c.get("author") or "").lower()
        if author not in OWNER_AUTHORS:
            continue
        w = first_word(str(c.get("body") or ""))
        if w in YES:
            word, who = "approved", author
        elif w in NO:
            word, who = "denied", author
    return word, who


def cmd_submit(a) -> dict:
    d, meta, st = load(a.name)
    sha = tree_sha(d)
    t = st.get("test") or {}
    if not (t.get("ok") and t.get("sha") == sha):
        raise Refused("its tests have not passed on these files (run: test)")
    by = a.by or meta.get("by") or "bash"
    body = (f"Review the krew tool-lab tool `{a.name}` before it may go further.\n"
            f"What: {meta.get('description')}\nFor profiles: {', '.join(meta.get('profiles') or [])}\n"
            f"Asked by: {meta.get('by') or '?'}; why: {meta.get('why') or '?'}\n"
            f"Files: {d}/plugin (tool.py, schema.json, __init__.py) and {d}/tests; sha {sha[:12]}\n"
            f"It says it reads {meta.get('reads') or 'nothing'}, writes {meta.get('writes') or 'nothing'}, "
            f"runs {meta.get('runs') or 'nothing'}: check the code does no more.\n"
            "Complete with PASS or FAIL as the first word. A later edit re-opens this review.")
    rc, out = kanban(by, "create", f"review: tool {a.name}", "--assignee", REVIEWER, "--body", body,
                     "--created-by", "krew-tool-lab", "--idempotency-key", f"krew-tool-lab:review:{a.name}:{sha[:12]}")
    tid = card_id(out)
    if rc != 0 or not tid:
        raise Refused(f"the review card was not filed: {out.strip()[:200]}")
    st["review"] = {"card": tid, "sha": sha, "by": by, "verdict": ""}
    write_json(d / "state.json", st)
    return {"ok": True, "card": tid, "reviewer": REVIEWER}


# ---- T0 -----------------------------------------------------------------------------------------------------------

def t0_gate() -> Path | None:
    for p in (os.environ.get("KREW_T0_GATE"), str(krew_home() / "rails" / "tier0_gate.py")):
        if p and Path(os.path.expanduser(p)).is_file():
            return Path(os.path.expanduser(p))
    return None


def t0_payloads(d: Path, meta: dict) -> list[tuple[str, dict]]:
    """What the gate is asked: running the tool's own code (the gate reads the script's body), each command it
    declares, each path it writes (write_file) and reads (read_file)."""
    out = [("code", {"tool_name": "terminal",
                     "tool_input": {"command": f"python3 {d / 'plugin' / 'tool.py'}", "workdir": str(d)}})]
    out += [(f"runs: {c}", {"tool_name": "terminal", "tool_input": {"command": str(c), "workdir": str(d)}})
            for c in meta.get("runs") or []]
    out += [(f"writes: {p}", {"tool_name": "write_file", "tool_input": {"path": str(p), "content": ""}})
            for p in meta.get("writes") or []]
    out += [(f"reads: {p}", {"tool_name": "read_file", "tool_input": {"path": str(p)}})
            for p in meta.get("reads") or []]
    return out


def cmd_t0(a) -> dict:
    d, meta, st = load(a.name)
    sha = tree_sha(d)
    gate = t0_gate()
    findings, asked = [], 0
    if gate is None:
        findings.append({"what": "gate", "why": "no T0 gate installed (krew-install --hooks places it under "
                                                 "$KREW_HOME/rails; KREW_T0_GATE names another)"})
    else:
        for prof in meta.get("profiles") or []:
            for what, payload in t0_payloads(d, meta):
                env = {**os.environ, "KREW_T0_MODE": "enforce", "HERMES_PROFILE": prof}
                asked += 1
                try:
                    r = subprocess.run([sys.executable, str(gate)], input=json.dumps(
                        {"hook_event_name": "pre_tool_call", **payload}), capture_output=True, text=True, env=env,
                        timeout=30)
                    res = json.loads(r.stdout or "{}")
                except (subprocess.TimeoutExpired, ValueError) as e:
                    res = {"decision": "block", "reason": f"the gate did not answer ({type(e).__name__})"}
                if res.get("decision") == "block":
                    findings.append({"profile": prof, "what": what, "why": str(res.get("reason") or "")[:300]})
                elif "inactive" in str(res.get("context") or ""):
                    findings.append({"profile": prof, "what": what, "why": "T0 has no policy (it would let anything "
                                                                           "through): install one first"})
    clean = not findings
    st["t0"] = {"clean": clean, "sha": sha, "gate": str(gate or ""), "asked": asked, "findings": findings,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    write_json(d / "state.json", st)
    return {"ok": clean, "asked": asked, "findings": findings}


# ---- the owner ----------------------------------------------------------------------------------------------------

def refresh(name: str, d: Path, st: dict) -> dict:
    """Read the review and the owner's word fresh from their cards (never trust a stored verdict alone)."""
    rv = st.get("review") or {}
    if rv.get("card"):
        rv["verdict"] = review_verdict(show(rv.get("by") or "bash", rv["card"])) or rv.get("verdict", "")
    ow = st.get("owner") or {}
    if ow.get("card"):
        word, who = owner_word(show(OWNER_PROFILE, ow["card"]))
        ow.update(word=word, who=who)
    st["review"], st["owner"] = rv, ow
    write_json(d / "state.json", st)
    return st


def gaps(d: Path, st: dict, *, need_owner: bool) -> list[str]:
    sha = tree_sha(d)
    out = []
    t = st.get("test") or {}
    if not (t.get("ok") and t.get("sha") == sha):
        out.append("tests: not passed on these files (test)")
    rv = st.get("review") or {}
    if not (rv.get("verdict") == "PASS" and rv.get("sha") == sha):
        out.append("review: " + ("not filed (submit)" if not rv.get("card") else
                                 "filed for other files (submit again)" if rv.get("sha") != sha else
                                 f"{rv.get('verdict') or 'waiting'} on {rv['card']}"))
    t0 = st.get("t0") or {}
    if not (t0.get("clean") and t0.get("sha") == sha):
        out.append("t0: " + ("not read (t0)" if not t0 else "read for other files (t0 again)" if t0.get("sha") != sha
                             else f"{len(t0.get('findings') or [])} finding(s)"))
    if need_owner:
        ow = st.get("owner") or {}
        if not (ow.get("word") == "approved" and ow.get("sha") == sha):
            out.append("owner: " + ("not asked (ask-owner)" if not ow.get("card") else
                                    "asked about other files (ask-owner again)" if ow.get("sha") != sha else
                                    f"{ow.get('word') or 'waiting'} on {ow['card']}"))
    return out


def cmd_ask_owner(a) -> dict:
    d, meta, st = load(a.name)
    st = refresh(a.name, d, st)
    g = gaps(d, st, need_owner=False)
    if g:
        raise Refused("not ready for the owner: " + "; ".join(g))
    sha = tree_sha(d)
    profiles = ", ".join(meta.get("profiles") or [])
    body = (f"A new tool wants in: `{a.name}` - {meta.get('description')}\n"
            f"For: {profiles}. Asked by {meta.get('by') or '?'} because: {meta.get('why') or '?'}\n"
            f"Tests passed; {REVIEWER} reviewed it PASS ({(st.get('review') or {}).get('card')}); T0 read what it "
            f"reads, writes and runs and found nothing. Files: {d} (sha {sha[:12]}).\n"
            "Answer with a comment: `approve` to install it into those profiles, or `no`. Nothing is installed "
            "without your word, and `tool_lab.py remove` takes it back out.")
    rc, out = kanban(OWNER_PROFILE, "create", f"Approve tool {a.name} for {profiles}?", "--assignee", OWNER_PROFILE,
                     "--body", body, "--created-by", "krew-tool-lab",
                     "--idempotency-key", f"krew-tool-lab:owner:{a.name}:{sha[:12]}")
    tid = card_id(out)
    if rc != 0 or not tid:
        raise Refused(f"the owner's card was not filed: {out.strip()[:200]}")
    rc2, out2 = kanban(OWNER_PROFILE, "block", tid, f"Approve tool {a.name} for {profiles}? Comment approve or no.",
                       "--kind", "needs_input")
    if rc2 != 0:
        kanban(OWNER_PROFILE, "archive", tid)
        raise Refused(f"the owner's card could not be blocked on him: {out2.strip()[:200]}")
    st["owner"] = {"card": tid, "sha": sha, "word": "", "who": ""}
    write_json(d / "state.json", st)
    return {"ok": True, "card": tid}


def cmd_status(a) -> dict:
    d, meta, st = load(a.name)
    st = refresh(a.name, d, st)
    g = gaps(d, st, need_owner=True)
    return {"ok": not g, "tool": a.name, "sha": tree_sha(d)[:12], "profiles": meta.get("profiles"),
            "installed": st.get("installed") or [], "missing": g}


# ---- in and out ---------------------------------------------------------------------------------------------------

def cmd_install(a) -> dict:
    d, meta, st = load(a.name)
    st = refresh(a.name, d, st)
    g = gaps(d, st, need_owner=True)
    if g:
        raise Refused("not installed: " + "; ".join(g))
    done = []
    for prof in meta.get("profiles") or []:
        dst = profile_home(prof) / "plugins" / a.name
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(d / "plugin", dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        r = subprocess.run([hermes_bin(), "-p", prof, "plugins", "enable", a.name], capture_output=True, text=True,
                           timeout=60)
        if r.returncode != 0:
            shutil.rmtree(dst, ignore_errors=True)
            raise Refused(f"{prof}: enable failed ({(r.stderr or r.stdout).strip()[:200]}); its copy was removed")
        done.append(prof)
    st["installed"] = sorted(set(st.get("installed") or []) | set(done))
    st["installed_sha"] = tree_sha(d)
    write_json(d / "state.json", st)
    catalog(a.name, meta, st)
    return {"ok": True, "installed": done, "index": reindex(),
            "next": "a running gateway loads a new plugin at its next start"}


def catalog(name: str, meta: dict, st: dict) -> None:
    """$KREW_HOME/built/tools/<name>.md: what the docs index (source `built`) tells the crew and Crissa about it."""
    p = krew_home() / "built" / "tools" / f"{name}.md"
    if not st.get("installed"):
        p.unlink(missing_ok=True)
        return
    schema = read_json(lab(name) / "plugin" / "schema.json", {})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# Tool {fn_name(name)} ({name})\n\n{meta.get('description')}\n\n"
                 f"Installed for: {', '.join(st['installed'])} (made with krew-tool-builder; asked by "
                 f"{meta.get('by') or '?'}: {meta.get('why') or '?'}). Reviewed by {REVIEWER} "
                 f"({(st.get('review') or {}).get('card')}), read by T0, approved by the owner "
                 f"({(st.get('owner') or {}).get('card')}).\n\nArguments:\n\n```json\n{json.dumps(schema, indent=1)}\n```\n\n"
                 f"Reads {meta.get('reads') or 'nothing'}; writes {meta.get('writes') or 'nothing'}; runs "
                 f"{meta.get('runs') or 'nothing'}. Take it out: tool_lab.py remove {name}\n", encoding="utf-8")


def reindex() -> str:
    """Rebuild the docs index's `built` source when krew-docs is installed; else the night's rebuild picks it up."""
    for idx in (hermes_home() / "plugins" / "krew-docs" / "docs_index.py",
                Path(__file__).resolve().parents[3] / "plugins" / "krew-docs" / "docs_index.py"):
        if idx.is_file():
            try:
                r = subprocess.run([sys.executable, str(idx), "build", "--source", "built"], capture_output=True,
                                   text=True, timeout=600)
                return "indexed" if r.returncode == 0 else f"index exit {r.returncode}: {(r.stderr or r.stdout)[-200:]}"
            except subprocess.TimeoutExpired:
                return "index build timed out; the night's rebuild picks it up"
    return "no krew-docs here; the catalog waits for the next index build"


def cmd_remove(a) -> dict:
    d, meta, st = load(a.name)
    want = [p.strip() for p in (a.profiles or "").split(",") if p.strip()] or sorted(
        set(st.get("installed") or []) | set(meta.get("profiles") or []))
    out = []
    for prof in want:
        dst = profile_home(prof) / "plugins" / a.name
        r = subprocess.run([hermes_bin(), "-p", prof, "plugins", "disable", a.name], capture_output=True, text=True,
                           timeout=60)
        had = dst.exists()
        shutil.rmtree(dst, ignore_errors=True)
        out.append({"profile": prof, "disabled": r.returncode == 0, "copy_removed": had})
    st["installed"] = sorted(set(st.get("installed") or []) - set(want))
    write_json(d / "state.json", st)
    catalog(a.name, meta, st)
    return {"ok": True, "removed": out, "index": reindex()}


VERBS = {"new": cmd_new, "test": cmd_test, "submit": cmd_submit, "t0": cmd_t0, "ask-owner": cmd_ask_owner,
         "status": cmd_status, "install": cmd_install, "remove": cmd_remove}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tool_lab.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", choices=sorted(VERBS))
    ap.add_argument("name")
    ap.add_argument("--desc", default="")
    ap.add_argument("--profiles", default="")
    ap.add_argument("--kind", choices=("script", "plugin"), default="script")
    ap.add_argument("--by", default=os.environ.get("HERMES_PROFILE") or "")
    ap.add_argument("--why", default="")
    a = ap.parse_args(argv)
    if not on():
        print(json.dumps({"ok": False, "refused": "KREW_TOOL_LAB=0"}))
        return 3
    try:
        res = VERBS[a.verb](a)
    except Refused as e:
        res = {"ok": False, "refused": str(e)}
    try:
        log(a.name, a.verb, a.by, bool(res.get("ok")), res.get("refused") or res.get("next") or "",
            **({"why": a.why} if a.why else {}), **({k: res[k] for k in ("card", "installed", "findings", "missing")
                                                     if k in res}))
    except OSError:
        pass
    print(json.dumps(res, indent=1))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
