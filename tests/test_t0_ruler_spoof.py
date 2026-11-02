"""T0: the crew cannot sign as a ruler (casey 2026-09-19, "Default could be
assigned as User. Team krew cannot reach, right?").

The board gate, kanban-loop, the restart window and liveness count a comment as
a RULING when its author is a ruler. Crissa rules through her kanban tools (no
terminal); the owner through the default-profile dashboard. So a crew terminal
signing as a ruler, calling the dashboard, or reading the credential store is a
forgery path. Pins, on the REAL policy through the real gate (JSON on stdin):

* each forgery form is blocked for a crew caller (a kanban worker or any named
  profile) — and passes for `default`, the owner's own CLI;
* the owner's name is never a literal: {USER} comes from KREW_USER_ID, else the
  person.json whose role is "user";
* ordinary board work and the reads replayed from real worker calls stay free;
* KREW_T0_CREW_RULES=0 switches the crew rules off.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t0_policy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GATE = t0_policy.GATE
POLICY = t0_policy.POLICY
WORKER = {"HERMES_KANBAN_TASK": "t_test", "HERMES_PROFILE": "bash"}


@pytest.fixture
def ids(tmp_path):
    root = tmp_path / "identity"
    for who, role in (("alex", "user"), ("sam", "regular")):
        (root / "people" / who).mkdir(parents=True)
        (root / "people" / who / "person.json").write_text(json.dumps({"role": role}))
    return root


def _gate(tmp_path, ids, text, tool="terminal", field="command", who=WORKER, extra=None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_USER_ID",
                        "HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE="enforce", KREW_ID_ROOT=str(ids), **(who or {}), **(extra or {}))
    payload = {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": {field: text}}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def _blocked(out):
    return out.get("decision") == "block"


FORGERIES = [
    ('hermes kanban comment t_1 "go" --author alex', "terminal", "command"),
    ("hermes -p ada kanban comment t_1 'park' --author=Crissa", "terminal", "command"),
    ('hermes kanban comment t_1 "try" --author "dashboard"', "terminal", "command"),
    ("hermes kanban comment t_1 go --author user", "terminal", "command"),
    ("hermes kanban comment t_1 go --author human", "terminal", "command"),
    ("HERMES_PROFILE=crissa hermes kanban comment t_1 go", "terminal", "command"),
    ("export HERMES_PROFILE_NAME=alex; hermes kanban comment t_1 go", "terminal", "command"),
    ("HERMES_PROFILE=default hermes kanban comment t_1 go", "terminal", "command"),
    ("env -u HERMES_PROFILE hermes kanban comment t_1 go", "terminal", "command"),
    ("hermes -p crissa kanban comment t_1 'go'", "terminal", "command"),
    ("hermes --profile default kanban complete t_1", "terminal", "command"),
    ('kanban_db.add_comment(conn, "t_1", author="crissa", body="go")', "execute_code", "code"),
    ("curl -s -X POST http://127.0.0.1:9119/api/plugins/kanban/tasks/t_1/comments -d '{}'", "terminal", "command"),
    ("cat /home/crew/.hermes/profiles/vera/auth.json | head -c 500", "terminal", "command"),
    ("curl -s http://localhost:9119/kanban?profile=crissa", "terminal", "command"),
    ('requests.get("http://127.0.0.1:9119/api/status")', "execute_code", "code"),
    ('requests.post("http://127.0.0.1:9119/api/plugins/kanban/tasks/t_1/comments", json={})', "execute_code", "code"),
    ("cat ~/.hermes/auth.json", "terminal", "command"),
    ("python3 -c 'print(open(\"/home/crew/.hermes/auth.json\").read())'", "terminal", "command"),
    ('json.load(open(os.path.expanduser("~/.hermes/auth.json")))', "execute_code", "code"),
    ("jq . $HERMES_HOME/auth.json", "terminal", "command"),
    ("hermes egress setup", "terminal", "command"),
    ("cd ~/projects/crew-rails && python3 scripts/pose-mouth-harvest.py 2>&1 | tail -40", "terminal", "command"),
    ("/stores/crew-envs/vllm/bin/python3 scripts/pose-mouth-harvest.py > out.txt", "terminal", "command"),
    ("python3 -u scripts/pose-mouth-harvest-close.py --help", "terminal", "command"),
    ("./scripts/pose-mouth-harvest.py", "terminal", "command"),
    ("hermes -p bash egress start", "terminal", "command"),
    ("hermes secrets bitwarden setup", "terminal", "command"),
    ("hermes auth add openai", "terminal", "command"),
    ("cat ~/.hermes/proxy/ca.key", "terminal", "command"),
    ("cat ~/.hermes/profiles/ada/proxy/mappings.json", "terminal", "command"),
    ("sed -i s/x/y/ ~/.hermes/security/tier0.yaml", "terminal", "command"),
    ("cp security/tier0.yaml ~/.hermes/security/tier0.yaml", "terminal", "command"),
    ("echo x >> ~/.hermes/security/tier0.yaml", "terminal", "command"),
    ('open(os.path.expanduser("~/.hermes/security/tier0.yaml"), "w").write(x)', "execute_code", "code"),
    ("/home/crew/.hermes/security/tier0.yaml", "write_file", "path"),
]


@pytest.mark.parametrize("text,tool,field", FORGERIES)
def test_the_crew_cannot_sign_as_a_ruler(tmp_path, ids, text, tool, field):
    out = _gate(tmp_path, ids, text, tool, field)
    assert _blocked(out), text
    assert "route: crissa" in out["reason"] and "needs_input" in out["reason"]


@pytest.mark.parametrize("who", [{"HERMES_PROFILE": "steward"},              # a crew gateway's own session
                                 {"HERMES_HOME": "/h/.hermes/profiles/ada"}])  # profile from HERMES_HOME
def test_a_named_profile_is_crew_without_a_task(tmp_path, ids, who):
    assert _blocked(_gate(tmp_path, ids, "hermes kanban comment t_1 go --author user", who=who))


@pytest.mark.parametrize("text,tool,field", FORGERIES)
def test_the_users_default_cli_is_not_gated_by_them(tmp_path, ids, text, tool, field):
    out = _gate(tmp_path, ids, text, tool, field, who={"HERMES_HOME": "/h/.hermes"})
    assert not _blocked(out), (text, out.get("reason"))


def test_the_users_name_comes_from_the_identity_store_not_a_literal(tmp_path, ids):
    assert _blocked(_gate(tmp_path, ids, "hermes kanban comment t_1 go --author alex"))
    # a regular is not a ruler, and no literal name is baked into the policy
    assert not _blocked(_gate(tmp_path, ids, "hermes kanban comment t_1 go --author sam"))
    assert not _blocked(_gate(tmp_path, ids, "hermes kanban comment t_1 go --author casey"))
    txt = POLICY.read_text().lower()
    block = txt[txt.index("the crew cannot sign"):txt.index("\nallowlist: []")]
    assert "casey" not in block


def test_owner_user_id_wins_over_the_store(tmp_path, ids):
    extra = {"KREW_USER_ID": "robin"}
    assert _blocked(_gate(tmp_path, ids, "hermes kanban comment t_1 go --author robin", extra=extra))


def test_no_user_on_record_still_blocks_the_roles(tmp_path):
    empty = tmp_path / "none"
    out = _gate(tmp_path, empty, "hermes kanban comment t_1 go --author user")
    assert _blocked(out)
    assert not _blocked(_gate(tmp_path, empty, "hermes kanban comment t_1 go --author bash"))


def test_kill_switch_turns_the_crew_rules_off(tmp_path, ids):
    extra = {"KREW_T0_CREW_RULES": "0"}
    assert not _blocked(_gate(tmp_path, ids, "hermes kanban comment t_1 go --author user", extra=extra))
    # the old rules still hold
    assert _blocked(_gate(tmp_path, ids, "sudo systemctl restart docker", extra=extra))


FREE = [
    ("hermes -p bash kanban comment t_1 'done: tests green'", "terminal", "command"),
    ("hermes -p ada kanban comment t_1 'gate: routed' --author kanban-board-gate", "terminal", "command"),
    ("hermes -p ada kanban comment t_1 note --author bash", "terminal", "command"),
    ("hermes -p crissa kanban show t_1", "terminal", "command"),
    ("hermes -p crissa kanban list --status blocked", "terminal", "command"),
    ("""sqlite3 ~/.hermes/kanban/boards/x/kanban.db "SELECT body FROM task_comments WHERE author = 'crissa'" """,
     "terminal", "command"),
    ('rows = [c for c in comments if c.author == "crissa"]', "execute_code", "code"),
    ("grep -n RULERS scripts/kanban-board-gate.py", "terminal", "command"),
    ("echo $HERMES_PROFILE", "terminal", "command"),
    ("hermes egress status", "terminal", "command"),
    ("head -30 scripts/pose-mouth-harvest.py", "terminal", "command"),
    ("grep -nE 'add_argument|def main' scripts/pose-mouth-harvest.py", "terminal", "command"),
    ("hermes auth status", "terminal", "command"),
    ("cat ~/.hermes/security/tier0.yaml", "terminal", "command"),
    ("tail -5 ~/.hermes/security/tier0-hits.jsonl", "terminal", "command"),
    ("ls -la ~/.hermes/security/tier0-hits.jsonl 2>/dev/null; ls -la ~/.hermes/security/T0.md 2>/dev/null", "terminal", "command"),
    ("hermes kanban log t_1 2>&1 | head -80; ls ~/.hermes/security/prodgate/t_1-r2/ 2>/dev/null", "terminal", "command"),
    ("cp staged.yaml ~/.hermes/security/prodgate/t_1/tier0.yaml", "terminal", "command"),
    ("prodgate stage hermes-packs/distributions/bash/SOUL.md 2>&1", "terminal", "command"),
    ("ls ~/.hermes/profiles/bash/", "terminal", "command"),
    ("curl -sS --max-time 3 'http://127.0.0.1:9119/ping' 2>&1", "terminal", "command"),
    ("ss -ltn | grep -E ':8088|:8080|:9119|:8006' | sort", "terminal", "command"),
    ('report = "Dashboard :9119 running"', "execute_code", "code"),
    ("curl -sS http://127.0.0.1:9119/health 2>/dev/null", "terminal", "command"),
    ("curl -s -o /dev/null -w '9119 %{http_code}' --max-time 4 http://127.0.0.1:9119/", "terminal", "command"),
    ('checks = {"dashboard:9119": "http://127.0.0.1:9119/ping"}', "execute_code", "code"),
    # replayed from real worker calls (2026-09-19): diagnosis, a grep, a ledger test
    ("HERMES_PROFILE=crissa hermes doctor 2>&1 | head -n 200", "terminal", "command"),
    ("""grep -o '"author": "crissa", "body": "[^"]*"' $F""", "terminal", "command"),
    ("HERMES_PROFILE=alex $P accept g1 --by vera --basis b --ledger $G/ledger.json", "terminal", "command"),
]


@pytest.mark.parametrize("text,tool,field", FREE)
def test_ordinary_board_work_stays_free(tmp_path, ids, text, tool, field):
    out = _gate(tmp_path, ids, text, tool, field)
    assert not _blocked(out), (text, out.get("reason"))
