#!/usr/bin/env python3
"""krew skill lab: a repeated job becomes a Hermes skill, tried once on a real case before it is kept (the owner
2026-11-01: "how about one skillbuilder skill and tool builder skill").

    skill_lab.py new NAME --description "use when ..." --profile P --why "..."   the draft, in the lab
    skill_lab.py check NAME                         frontmatter, name, description, no placeholder left
    skill_lab.py trial NAME --profile P --case "a real case"   one one-shot run of P with the draft as its guide
    skill_lab.py record NAME --profile P --why "..."     after skill_manage installed it: the log, the catalog, the index
    skill_lab.py list                               every skill the lab has made: who, why, when

The draft lives in $KREW_HOME/skill-lab/<name>/SKILL.md until it is installed. The install itself is the agent's
own `skill_manage` (it writes into the asking profile, the one place a profile's skills live); `record` then checks
it landed there, logs who / why / when ($KREW_HOME/skill-lab/log.jsonl), puts a copy in the catalog
($KREW_HOME/built/skills/<name>.md, the folder the docs index reads as source `built`) and rebuilds that source, so
the krew and Crissa find it with krew_docs. Kill: KREW_SKILL_LAB=0.
"""
from __future__ import annotations

import argparse
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

NAME_RX = re.compile(r"^[a-z][a-z0-9-]{1,50}$")
PLACEHOLDER = re.compile(r"<(?:step|what|when|fill)[^>]*>|\bTODO\b")


def krew_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


def hermes_home() -> Path:
    """This profile's own home ($HERMES_HOME as given: the profile a skill or tool installs into), else the root."""
    given = os.environ.get("HERMES_HOME")
    return Path(os.path.expanduser(given)) if given else KH.hermes_home()


def hermes_bin() -> str:
    given = os.environ.get("HERMES_BIN")
    if given:
        return given
    local = Path.home() / ".local" / "bin" / "hermes"
    return str(local) if local.is_file() else "hermes"


def on() -> bool:
    return os.environ.get("KREW_SKILL_LAB", "1").strip().lower() not in ("0", "false", "no", "off")


def profile_home(p: str) -> Path:
    return hermes_home() if p in ("", "default", "main") else hermes_home() / "profiles" / p


class Refused(Exception):
    pass


def draft(name: str) -> Path:
    if not NAME_RX.match(name or ""):
        raise Refused(f"name {name!r}: lowercase letters, digits and '-', starting with a letter")
    return krew_home() / "skill-lab" / name / "SKILL.md"


TEMPLATE = """---
name: {name}
description: {description}
version: 1.0.0
author: {profile}
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [krew, built]
---

# {title}

Made by {profile} with krew-skill-builder on {day}. Why: {why}

## When to use

<when: the request or the situation that calls for this, in the words people use>

## Steps

1. <step: what to do first, with the exact tool or command>
2. <step>

## Done when

<what: the result that says it worked, and where it is>
"""


def frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and not line.startswith(" "):
            out[k.strip()] = v.strip()
    return out


def check_text(name: str, text: str) -> list[str]:
    fm = frontmatter(text)
    bad = []
    if not fm:
        bad.append("no frontmatter (--- name / description --- at the top)")
    if fm.get("name") != name:
        bad.append(f"frontmatter name is {fm.get('name')!r}, the folder is {name!r}")
    desc = fm.get("description", "")
    if len(desc) < 20:
        bad.append("description too short: say when to use it (it is how an agent picks the skill)")
    if len(desc) > 300:
        bad.append("description over 300 characters: one line on when to use it, the steps go in the body")
    if PLACEHOLDER.search(text):
        bad.append("a placeholder is left (<step>, <when>, <what> or TODO)")
    if "## Steps" not in text:
        bad.append("no ## Steps section")
    return bad


def log(row: dict) -> None:
    p = krew_home() / "skill-lab" / "log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **row}) + "\n")


def cmd_new(a) -> dict:
    p = draft(a.name)
    if p.exists():
        raise Refused(f"{p} exists (edit it)")
    shipped = profile_home(a.profile) / "skills"
    if shipped.is_dir() and any(shipped.rglob(f"{a.name}/SKILL.md")):
        raise Refused(f"{a.profile} already has a skill named {a.name}")
    if not (a.description or "").strip():
        raise Refused("--description: one line on WHEN to use it")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(TEMPLATE.format(name=a.name, description=a.description.strip(), profile=a.profile or "krew",
                                 title=a.name.replace("-", " ").capitalize(), day=time.strftime("%Y-%m-%d"),
                                 why=a.why or "(say why)"), encoding="utf-8")
    return {"ok": True, "draft": str(p), "next": "write When to use / Steps / Done when, then: check, trial"}


def cmd_check(a) -> dict:
    p = draft(a.name)
    if not p.is_file():
        raise Refused(f"no draft {p}")
    bad = check_text(a.name, p.read_text(encoding="utf-8"))
    return {"ok": not bad, "problems": bad}


def cmd_trial(a) -> dict:
    """One one-shot run of the profile on a REAL case, the draft inlined as its guide (it is not installed yet, so
    --skills cannot load it). The result is kept beside the draft; record refuses without a trial that answered."""
    p = draft(a.name)
    bad = check_text(a.name, p.read_text(encoding="utf-8")) if p.is_file() else ["no draft"]
    if bad:
        raise Refused("check first: " + "; ".join(bad))
    if not (a.case or "").strip():
        raise Refused("--case: a real case from the job this skill is for (not a made-up one)")
    prompt = (f"Follow this skill on the case below. Say at the end, on its own line, TRIAL OK or TRIAL FAILED "
              f"and one reason.\n\n--- skill {a.name} ---\n{p.read_text(encoding='utf-8')}\n--- case ---\n{a.case}")
    cmd = [hermes_bin(), "-p", a.profile or "bash", "--cli", "chat", "--oneshot", "--max-turns",
           str(int(os.environ.get("KREW_SKILL_TRIAL_TURNS", "12"))), "-q", prompt]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(os.environ.get("KREW_SKILL_TRIAL_S", "600")))
        out, rc = ((r.stdout or "") + (r.stderr or "")).strip(), r.returncode
    except subprocess.TimeoutExpired:
        out, rc = "the trial hit its time cap", 124
    ok = rc == 0 and "TRIAL OK" in out and "TRIAL FAILED" not in out
    trial = {"ok": ok, "rc": rc, "profile": a.profile, "case": a.case[:500], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "tail": out[-1200:]}
    (p.parent / "trial.json").write_text(json.dumps(trial, indent=1) + "\n", encoding="utf-8")
    return {"ok": ok, "rc": rc, "tail": out[-600:]}


def installed_at(name: str, profile: str) -> Path | None:
    base = profile_home(profile) / "skills"
    hits = sorted(base.rglob(f"{name}/SKILL.md")) if base.is_dir() else []
    return hits[0] if hits else None


def reindex() -> str:
    """Rebuild the docs index's `built` source when krew-docs is installed beside the skills; else say how."""
    for idx in (hermes_home() / "plugins" / "krew-docs" / "docs_index.py",
                Path(__file__).resolve().parents[3] / "plugins" / "krew-docs" / "docs_index.py"):
        if idx.is_file():
            try:
                r = subprocess.run([sys.executable, str(idx), "build", "--source", "built"], capture_output=True,
                                   text=True, timeout=600)
                return "indexed" if r.returncode == 0 else f"index exit {r.returncode}: {(r.stderr or r.stdout)[-200:]}"
            except subprocess.TimeoutExpired:
                return "index build timed out; the night's rebuild picks it up"
    return "no krew-docs here; the catalog copy waits for the next index build"


def cmd_record(a) -> dict:
    p = draft(a.name)
    trial = {}
    try:
        trial = json.loads((p.parent / "trial.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not trial.get("ok"):
        raise Refused("no passing trial (run: trial on a real case)")
    where = installed_at(a.name, a.profile)
    if where is None:
        raise Refused(f"{a.name} is not in {a.profile}'s skills yet: install it with skill_manage first")
    if check_text(a.name, where.read_text(encoding="utf-8")):
        raise Refused(f"the installed copy at {where} does not pass check")
    cat = krew_home() / "built" / "skills" / f"{a.name}.md"
    cat.parent.mkdir(parents=True, exist_ok=True)
    head = (f"<!-- krew-skill-builder: {a.name}, made by {a.profile} on {time.strftime('%Y-%m-%d')}; why: "
            f"{a.why or '?'}; installed at {where} -->\n")
    cat.write_text(head + where.read_text(encoding="utf-8"), encoding="utf-8")
    idx = reindex()
    log({"skill": a.name, "who": a.profile, "why": a.why or "", "where": str(where), "trial_case": trial.get("case"),
         "index": idx})
    return {"ok": True, "catalog": str(cat), "index": idx}


def cmd_list(a) -> dict:
    rows = []
    p = krew_home() / "skill-lab" / "log.jsonl"
    if p.is_file():
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {"ok": True, "skills": rows}


VERBS = {"new": cmd_new, "check": cmd_check, "trial": cmd_trial, "record": cmd_record, "list": cmd_list}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skill_lab.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", choices=sorted(VERBS))
    ap.add_argument("name", nargs="?", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--profile", default=os.environ.get("HERMES_PROFILE") or "")
    ap.add_argument("--why", default="")
    ap.add_argument("--case", default="")
    a = ap.parse_args(argv)
    if not on():
        print(json.dumps({"ok": False, "refused": "KREW_SKILL_LAB=0"}))
        return 3
    if a.verb != "list" and not a.name:
        ap.error("NAME is required")
    try:
        res = VERBS[a.verb](a)
    except Refused as e:
        res = {"ok": False, "refused": str(e)}
    print(json.dumps(res, indent=1))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
