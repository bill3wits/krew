"""krew-skill-builder and krew-tool-builder (the owner 2026-11-01): a stand-in Hermes (records every call, answers
cards from a file), a scratch HERMES_HOME / KREW_HOME, the shipped T0 gate on a small policy. Nothing reaches a real
profile, board or gateway; the point is that no profile gets a tool before the owner's own word, and remove undoes it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import submodule

ROOT = Path(__file__).resolve().parents[1]
TOOL_LAB = ROOT / "skills" / "krew-tool-builder" / "scripts" / "tool_lab.py"
SKILL_LAB = ROOT / "skills" / "krew-skill-builder" / "scripts" / "skill_lab.py"
GATE = ROOT / "rails" / "tier0_gate.py"

FAKE_HERMES = r'''#!PY
"""Stand-in hermes: records argv, answers kanban create/show/block/archive, plugins enable/disable, chat."""
import json, os, sys
d = os.environ["FAKE_DIR"]
argv = sys.argv[1:]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps(argv) + "\n")
rest = argv[2:] if argv[:1] == ["-p"] else argv
if rest[:2] == ["kanban", "create"]:
    n = len(open(os.path.join(d, "calls.jsonl")).read().splitlines())
    print("Created t_%08x (ready)" % (0xabc000 + n)); sys.exit(0)
if rest[:2] == ["kanban", "show"]:
    cards = json.load(open(os.path.join(d, "cards.json"))) if os.path.exists(os.path.join(d, "cards.json")) else {}
    print(json.dumps(cards.get(rest[2], {"task": {"status": "ready"}, "comments": []}))); sys.exit(0)
if "chat" in rest:
    print(os.environ.get("FAKE_CHAT", "did it\nTRIAL OK")); sys.exit(0)
sys.exit(0)
'''

POLICY = """
blocklist:
  - pattern: '\\bsudo\\b[^\\n]{0,120}\\bsystemctl\\b'
    reason: 'root systemctl (sudo) - NEEDS HUMAN'
    route: 'assent'
"""


def env_for(tmp: Path) -> dict:
    fake = tmp / "fake"
    fake.mkdir(exist_ok=True)
    hb = fake / "hermes"
    hb.write_text(FAKE_HERMES.replace("#!PY", "#!" + sys.executable))
    hb.chmod(0o755)
    (tmp / "tier0.yaml").write_text(POLICY)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KREW_", "HERMES_"))}
    env.update(HOME=str(tmp), HERMES_HOME=str(tmp / "hermes"), KREW_HOME=str(tmp / "krew"), HERMES_BIN=str(hb),
               FAKE_DIR=str(fake), KREW_T0_GATE=str(GATE), KREW_T0_POLICY=str(tmp / "tier0.yaml"),
               KREW_T0_LOG=str(tmp / "t0-hits.jsonl"), KREW_REAL_HOME=str(tmp), PYTHONDONTWRITEBYTECODE="1")
    return env


def run(script: Path, env: dict, *args: str) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=env, timeout=300)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        raise AssertionError(f"not JSON (rc {r.returncode}): {r.stdout}\n{r.stderr}")


def calls(env: dict) -> list[list[str]]:
    p = Path(env["FAKE_DIR"]) / "calls.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def set_card(env: dict, tid: str, card: dict) -> None:
    p = Path(env["FAKE_DIR"]) / "cards.json"
    cards = json.loads(p.read_text()) if p.exists() else {}
    cards[tid] = card
    p.write_text(json.dumps(cards))


def enables(env: dict) -> list[list[str]]:
    return [c for c in calls(env) if "plugins" in c and "enable" in c]


def test_tool_route_installs_only_after_the_owners_word_and_remove_undoes_it(tmp_path):
    env = env_for(tmp_path)
    rc, out = run(TOOL_LAB, env, "new", "word-count", "--desc", "Count the words in a text when asked how long it is",
                  "--profiles", "bash", "--by", "bash", "--why", "asked three times this week")
    assert rc == 0 and out["ok"], out
    lab = tmp_path / "krew" / "tool-lab" / "word-count"
    assert (lab / "plugin" / "plugin.yaml").is_file() and (lab / "plugin" / "schema.json").is_file()

    rc, out = run(TOOL_LAB, env, "install", "word-count")
    assert rc == 2 and "tests" in out["refused"] and "owner" in out["refused"]
    rc, out = run(TOOL_LAB, env, "submit", "word-count")
    assert rc == 2 and "tests" in out["refused"]                 # no review before green tests
    rc, out = run(TOOL_LAB, env, "test", "word-count")
    assert rc == 0 and out["ok"], out
    rc, out = run(TOOL_LAB, env, "submit", "word-count")
    assert rc == 0, out
    review = out["card"]
    create = [c for c in calls(env) if "create" in c][-1]
    assert create[create.index("--assignee") + 1] == "vera"

    rc, out = run(TOOL_LAB, env, "ask-owner", "word-count")
    assert rc == 2 and "review" in out["refused"]                # Vera has not answered
    set_card(env, review, {"task": {"status": "done", "result": "PASS - it does what it says"}, "comments": []})
    rc, out = run(TOOL_LAB, env, "ask-owner", "word-count")
    assert rc == 2 and "t0: not read" in out["refused"]
    rc, out = run(TOOL_LAB, env, "t0", "word-count")
    assert rc == 0 and out["ok"] and out["asked"] >= 1, out
    rc, out = run(TOOL_LAB, env, "ask-owner", "word-count")
    assert rc == 0, out
    owner = out["card"]
    block = [c for c in calls(env) if "block" in c][-1]
    assert block[block.index("--kind") + 1] == "needs_input" and block[block.index("block") + 1] == owner

    rc, out = run(TOOL_LAB, env, "install", "word-count")
    assert rc == 2 and "owner: waiting" in out["refused"]
    # a crew profile saying approve is not the owner
    set_card(env, owner, {"task": {"status": "blocked"}, "comments": [{"author": "bash", "body": "approve"}]})
    rc, out = run(TOOL_LAB, env, "install", "word-count")
    assert rc == 2 and not enables(env)
    set_card(env, owner, {"task": {"status": "blocked"}, "comments": [{"author": "user", "body": "approve, thanks"}]})
    rc, out = run(TOOL_LAB, env, "install", "word-count")
    assert rc == 0 and out["installed"] == ["bash"], out
    dst = tmp_path / "hermes" / "profiles" / "bash" / "plugins" / "word-count"
    assert (dst / "tool.py").is_file()
    assert enables(env) == [["-p", "bash", "plugins", "enable", "word-count"]]
    page = tmp_path / "krew" / "built" / "tools" / "word-count.md"
    assert page.is_file() and "word_count" in page.read_text()

    rc, out = run(TOOL_LAB, env, "remove", "word-count")
    assert rc == 0 and out["removed"][0]["copy_removed"], out
    assert not dst.exists() and not page.exists()
    assert ["-p", "bash", "plugins", "disable", "word-count"] in calls(env)

    rows = [json.loads(x) for x in (tmp_path / "krew" / "tool-lab" / "log.jsonl").read_text().splitlines()]
    assert [r["verb"] for r in rows if r["ok"]][-3:] == ["ask-owner", "install", "remove"]
    assert all(r["tool"] == "word-count" and r["ts"] and r["by"] for r in rows)


def test_an_edit_after_approval_reopens_every_step(tmp_path):
    env = env_for(tmp_path)
    run(TOOL_LAB, env, "new", "echo-back", "--desc", "Echo a text back when a check needs a fixed answer",
        "--profiles", "lila", "--by", "lila")
    run(TOOL_LAB, env, "test", "echo-back")
    _, out = run(TOOL_LAB, env, "submit", "echo-back")
    set_card(env, out["card"], {"task": {"status": "done", "result": "PASS"}, "comments": []})
    run(TOOL_LAB, env, "t0", "echo-back")
    _, out = run(TOOL_LAB, env, "ask-owner", "echo-back")
    set_card(env, out["card"], {"comments": [{"author": "dashboard", "body": "yes"}]})
    tool = tmp_path / "krew" / "tool-lab" / "echo-back" / "plugin" / "tool.py"
    tool.write_text(tool.read_text().replace('return str(args.get("text", ""))', 'return "changed"'))
    rc, out = run(TOOL_LAB, env, "install", "echo-back")
    assert rc == 2 and "tests" in out["refused"] and "review" in out["refused"] and "owner" in out["refused"]
    assert not enables(env)


def test_t0_finding_stops_the_route_before_the_owner(tmp_path):
    env = env_for(tmp_path)
    run(TOOL_LAB, env, "new", "fixer", "--desc", "Restart a stuck service when the panel says it is down",
        "--profiles", "bash", "--by", "bash")
    meta = tmp_path / "krew" / "tool-lab" / "fixer" / "tool.json"
    m = json.loads(meta.read_text())
    m["runs"] = ["sudo systemctl restart docker"]
    meta.write_text(json.dumps(m))
    rc, out = run(TOOL_LAB, env, "t0", "fixer")
    assert rc == 2 and out["findings"] and "root systemctl" in out["findings"][0]["why"]
    run(TOOL_LAB, env, "test", "fixer")
    _, sub = run(TOOL_LAB, env, "submit", "fixer")
    set_card(env, sub["card"], {"task": {"status": "done", "result": "PASS"}, "comments": []})
    rc, out = run(TOOL_LAB, env, "ask-owner", "fixer")
    assert rc == 2 and "finding" in out["refused"]
    assert not [c for c in calls(env) if "block" in c]


def test_no_gate_is_not_clean(tmp_path):
    env = env_for(tmp_path)
    env["KREW_T0_GATE"] = str(tmp_path / "absent.py")
    run(TOOL_LAB, env, "new", "lonely", "--desc", "A tool on a box with no T0 gate installed at all",
        "--profiles", "bash")
    rc, out = run(TOOL_LAB, env, "t0", "lonely")
    assert rc == 2 and "no T0 gate" in out["findings"][0]["why"]


def test_kill_switch_refuses_every_verb(tmp_path):
    env = env_for(tmp_path)
    env["KREW_TOOL_LAB"] = "0"
    r = subprocess.run([sys.executable, str(TOOL_LAB), "new", "x-tool", "--desc", "anything at all here",
                        "--profiles", "bash"], capture_output=True, text=True, env=env)
    assert r.returncode == 3 and not (tmp_path / "krew" / "tool-lab").exists()


DRAFT_BODY = """## When to use

When the owner asks for the weekly disk report.

## Steps

1. Run `df -h` and keep the lines over 80 %.
2. Write them to the deliverables folder as disk-YYYY-MM-DD.md.

## Done when

The note is in the folder and says which disks are over 80 %.
"""


def test_skill_route_trial_install_record_and_log(tmp_path):
    env = env_for(tmp_path)
    rc, out = run(SKILL_LAB, env, "new", "weekly-disk-report", "--profile", "bash",
                  "--description", "Use when the owner asks how full the disks are this week",
                  "--why", "the owner asked three Mondays running")
    assert rc == 0, out
    draft = Path(out["draft"])
    rc, out = run(SKILL_LAB, env, "check", "weekly-disk-report")
    assert rc == 2 and any("placeholder" in p for p in out["problems"])
    text = draft.read_text()
    draft.write_text(text[: text.index("## When to use")] + DRAFT_BODY)
    rc, out = run(SKILL_LAB, env, "check", "weekly-disk-report")
    assert rc == 0, out

    rc, out = run(SKILL_LAB, env, "record", "weekly-disk-report", "--profile", "bash")
    assert rc == 2 and "trial" in out["refused"]
    env_fail = dict(env, FAKE_CHAT="it broke\nTRIAL FAILED no df here")
    rc, out = run(SKILL_LAB, env_fail, "trial", "weekly-disk-report", "--profile", "bash", "--case", "this Monday's")
    assert rc == 2
    rc, out = run(SKILL_LAB, env, "trial", "weekly-disk-report", "--profile", "bash", "--case", "this Monday's")
    assert rc == 0, out
    chat = [c for c in calls(env) if "chat" in c][-1]
    assert chat[:2] == ["-p", "bash"] and "--oneshot" in chat and "this Monday's" in chat[-1]

    rc, out = run(SKILL_LAB, env, "record", "weekly-disk-report", "--profile", "bash")
    assert rc == 2 and "skill_manage" in out["refused"]          # not installed to the profile yet
    # what skill_manage does for the asking profile
    inst = tmp_path / "hermes" / "profiles" / "bash" / "skills" / "weekly-disk-report" / "SKILL.md"
    inst.parent.mkdir(parents=True)
    inst.write_text(draft.read_text())
    rc, out = run(SKILL_LAB, env, "record", "weekly-disk-report", "--profile", "bash", "--why", "Mondays")
    assert rc == 0, out
    assert Path(out["catalog"]).is_file()
    rows = run(SKILL_LAB, env, "list")[1]["skills"]
    assert rows[-1]["skill"] == "weekly-disk-report" and rows[-1]["who"] == "bash" and rows[-1]["why"] == "Mondays"
    assert rows[-1]["ts"]


def test_docs_index_reads_what_the_builders_made_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HOME", str(tmp_path / "krew"))
    di = submodule("krew-docs", "docs_index")
    c = dict(di.cfg(), sources={})
    assert "built" in di.sources_map(c)
