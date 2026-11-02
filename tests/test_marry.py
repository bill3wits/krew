"""krew-install's marry step (2026-10-06): a Hermes profile offered to the Wall (Crissa) as an endpoint, key
referenced never copied, and nothing the Wall uses changes until the user picks it."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import ROOT

SCRIPT = ROOT / "scripts" / "krew-install.py"
KEY = "k" * 24


def run(hh, kh, zh, *a):
    env = dict(os.environ, CRISSA_HOME=str(zh), CRISSA_PLAYER="http://127.0.0.1:9")
    return subprocess.run([sys.executable, str(SCRIPT), "--hermes-home", str(hh), "--krew-home", str(kh),
                           "--no-skills", "--only", "krew-board", *a], capture_output=True, text=True, env=env)


def _profile(hh, name, env_text):
    d = hh if name == "main" else hh / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".env").write_text(env_text)
    return d / ".env"


def test_no_crissa_says_so_and_writes_nothing(tmp_path):
    r = run(tmp_path / "h", tmp_path / "k", tmp_path / "z")
    assert r.returncode == 0, r.stderr
    assert "Crissa not installed" in r.stdout and not (tmp_path / "z").exists()


def test_the_offer_references_the_key_and_leaves_the_wall_alone(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    envf = _profile(hh, "crissa", 'API_SERVER_ENABLED=true\nexport API_SERVER_KEY="%s"\nAPI_SERVER_PORT=8650\n' % KEY)
    zh.mkdir()
    (zh / "agent.json").write_text('{"url": "http://127.0.0.1:1234/v1"}')
    r = run(hh, tmp_path / "k", zh)
    assert r.returncode == 0, r.stderr
    offer = json.loads((zh / "endpoints.d" / "hermes-crissa.json").read_text())
    assert offer == {"name": "Hermes (crissa)", "url": "http://127.0.0.1:8650/v1", "model": "crissa",
                     "session_header": "X-Hermes-Session-Id", "system_prompt": "",
                     "key_ref": {"file": str(envf.resolve()), "name": "API_SERVER_KEY"}, "by": "krew"}
    assert KEY not in r.stdout + r.stderr and KEY not in (zh / "endpoints.d" / "hermes-crissa.json").read_text()
    assert (zh / "agent.json").read_text() == '{"url": "http://127.0.0.1:1234/v1"}', "the live endpoint is untouched"
    assert oct((zh / "endpoints.d" / "hermes-crissa.json").stat().st_mode & 0o777) == "0o600"
    assert "click Use" in r.stdout
    assert "same" in run(hh, tmp_path / "k", zh).stdout


def test_an_api_server_that_is_off_says_how_to_turn_it_on(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    _profile(hh, "crissa", "API_SERVER_KEY=%s\n" % KEY)
    zh.mkdir()
    r = run(hh, tmp_path / "k", zh)
    assert "the API server is off" in r.stdout and "API_SERVER_ENABLED=true" in r.stdout
    assert not (zh / "endpoints.d").exists()
    _profile(hh, "crissa", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=short\n")
    assert "16+ characters" in run(hh, tmp_path / "k", zh).stdout


def test_a_multiplexed_gateway_serves_the_profile_under_p(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    root = _profile(hh, "main", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\nAPI_SERVER_HOST=0.0.0.0\n" % KEY)
    (hh / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")
    own = _profile(hh, "crissa", "OTHER=1\n")
    zh.mkdir()
    # a named profile never inherits the owner's key (Hermes answers 401 at /p/crissa): no own key, no offer
    out = run(hh, tmp_path / "k", zh).stdout
    assert "16+ characters" in out and str(own) in out and not (zh / "endpoints.d").exists()
    own.write_text("OTHER=1\nAPI_SERVER_KEY=%s\n" % KEY[::-1])
    run(hh, tmp_path / "k", zh)
    offer = json.loads((zh / "endpoints.d" / "hermes-crissa.json").read_text())
    assert offer["url"] == "http://127.0.0.1:%d/p/crissa/v1" % 8642 and offer["key_ref"]["file"] == str(own.resolve())
    assert root != own


def test_multiplex_wins_when_named_profile_also_has_enabled(tmp_path):
    """A leftover API_SERVER_ENABLED+PORT on the named profile must not beat multiplex_profiles."""
    hh, zh = tmp_path / "h", tmp_path / "z"
    _profile(hh, "main", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\nAPI_SERVER_PORT=%d\n" % (KEY, 8642))
    (hh / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")
    own = _profile(hh, "crissa", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\nAPI_SERVER_PORT=%d\n" % (KEY[::-1], 8652))
    zh.mkdir()
    run(hh, tmp_path / "k", zh)
    offer = json.loads((zh / "endpoints.d" / "hermes-crissa.json").read_text())
    assert offer["url"] == "http://127.0.0.1:%d/p/crissa/v1" % 8642
    assert offer["key_ref"]["file"] == str(own.resolve())


def test_main_and_no_marry(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    _profile(hh, "main", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n" % KEY)
    zh.mkdir()
    assert "\nmarry " not in "\n" + run(hh, tmp_path / "k", zh, "--no-marry").stdout
    assert not (zh / "endpoints.d").exists()
    run(hh, tmp_path / "k", zh)
    offer = json.loads((zh / "endpoints.d" / "hermes-main.json").read_text())
    assert offer["model"] == "hermes-agent" and offer["url"] == "http://127.0.0.1:%d/v1" % 8642


# --- the 1.0.2 buyer test (2026-10-30): init turns the API server on (finding 3) and the marry step writes where
#     her player really is (finding 10) ---

def _multiplex(hh):
    hh.mkdir(parents=True, exist_ok=True)
    (hh / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")


def test_api_server_on_marries_a_fresh_install_with_no_hand_step(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    _multiplex(hh)
    (hh / "profiles" / "crissa").mkdir(parents=True)
    zh.mkdir()
    r = run(hh, tmp_path / "k", zh, "--api-server-on")
    assert r.returncode == 0, r.stderr
    import importlib.util
    spec = importlib.util.spec_from_file_location("m", ROOT / "scripts" / "krew_install_marry.py")
    M = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(M)
    root, own = M.read_env(hh / ".env"), M.read_env(hh / "profiles" / "crissa" / ".env")
    assert root["API_SERVER_ENABLED"] == "true" and root["API_SERVER_HOST"] == "127.0.0.1"
    assert int(root["API_SERVER_PORT"]) != 8642 and len(root["API_SERVER_KEY"]) >= 16
    assert len(own["API_SERVER_KEY"]) >= 16 and own["API_SERVER_KEY"] != root["API_SERVER_KEY"]
    offer = json.loads((zh / "endpoints.d" / "hermes-crissa.json").read_text())
    assert offer["url"] == "http://127.0.0.1:%s/p/crissa/v1" % root["API_SERVER_PORT"]
    out = r.stdout + r.stderr
    assert root["API_SERVER_KEY"] not in out and own["API_SERVER_KEY"] not in out, "a key value was printed"
    assert oct((hh / ".env").stat().st_mode & 0o777) == "0o600"
    again = run(hh, tmp_path / "k", zh, "--api-server-on")
    assert M.read_env(hh / ".env") == root and "marry    set" not in again.stdout     # idempotent, yours kept


def test_free_port_never_the_house_port():
    import importlib.util
    spec = importlib.util.spec_from_file_location("m2", ROOT / "scripts" / "krew_install_marry.py")
    M = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(M)
    assert M.free_port(first=8640, free=lambda p: p >= 8642) == 8643
    assert M.free_port(free=lambda p: p != 8792) == 8793


def test_marry_writes_her_real_player_from_her_launcher_table(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    _profile(hh, "crissa", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n" % KEY)
    (zh / "run").mkdir(parents=True)
    (zh / "run" / "procs.json").write_text(json.dumps(
        {"schema": "crissa-procs/1", "children": {"player": {"port": 8300, "state": "running"}}}))
    r = run(hh, tmp_path / "k", zh)
    assert r.returncode == 0, r.stderr
    assert "CRISSA_PLAYER=http://127.0.0.1:8300\n" in (hh / ".env").read_text()
    (zh / "run" / "procs.json").write_text(json.dumps({"children": {"player": {"port": 8400}}}))
    run(hh, tmp_path / "k", zh)
    text = (hh / ".env").read_text()
    assert text.count("CRISSA_PLAYER=") == 1 and "CRISSA_PLAYER=http://127.0.0.1:8400\n" in text   # krew's own line moves


def test_a_player_line_of_yours_is_kept(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    _profile(hh, "main", "CRISSA_PLAYER=http://127.0.0.1:9999\n")
    (zh / "run").mkdir(parents=True)
    (zh / "run" / "procs.json").write_text(json.dumps({"children": {"player": {"port": 8300}}}))
    r = run(hh, tmp_path / "k", zh)
    assert "kept     CRISSA_PLAYER=http://127.0.0.1:9999" in r.stdout
    assert (hh / ".env").read_text() == "CRISSA_PLAYER=http://127.0.0.1:9999\n"


def test_player_kill_switch(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    (zh / "run").mkdir(parents=True)
    (zh / "run" / "procs.json").write_text(json.dumps({"children": {"player": {"port": 8300}}}))
    env = dict(os.environ, CRISSA_HOME=str(zh), KREW_MARRY_PLAYER="0")
    subprocess.run([sys.executable, str(SCRIPT), "--hermes-home", str(hh), "--krew-home", str(tmp_path / "k"),
                    "--no-skills", "--only", "krew-board"], capture_output=True, text=True, env=env)
    assert not (hh / ".env").exists()
