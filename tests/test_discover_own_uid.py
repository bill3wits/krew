"""Discovery lists only this user's listeners (the 1.0.2 buyer test, 2026-10-30: init on crissatest offered the
agent account's ports - the house's engines, its gateway - as crissatest's own services, role observe)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "crew" / "lib"


def _S():
    spec = importlib.util.spec_from_file_location("krewtest_services_uid", LIB / "krew_services.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SS = """LISTEN 0 4096 127.0.0.1:8300 0.0.0.0:* users:(("python3",pid=4242,fd=5)) uid:1002 ino:1 sk:1 <->
LISTEN 0 4096 127.0.0.1:8100 0.0.0.0:* uid:1000 ino:2 sk:2 <->
LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* ino:3 sk:3 cgroup:/system.slice/ssh.socket <->
LISTEN 0 4096 127.0.0.1:8792 0.0.0.0:* users:(("hermes",pid=77,fd=9)) <->
"""


def test_only_my_uid_and_my_processes():
    S = _S()
    got = S.listening_ports(SS, uid=1002)
    assert sorted(got) == [8300, 8792]          # mine by uid; mine by process (no -e info); never 8100 (uid 1000) or 22
    assert got[8300] == {"proc": "python3", "pid": 4242, "uid": 1002}


def test_discover_never_offers_another_accounts_port(monkeypatch):
    S = _S()
    ports = S.listening_ports(SS, uid=1002)
    rows = S.discover(run=lambda argv, timeout=30.0: "", ports=ports)
    assert {r["port"] for r in rows} == {8300, 8792}


def test_kill_switch_lists_every_port(monkeypatch):
    monkeypatch.setenv("KREW_DISCOVER_ANY_UID", "1")
    S = _S()
    assert sorted(S.listening_ports(SS, uid=1002)) == [22, 8100, 8300, 8792]
