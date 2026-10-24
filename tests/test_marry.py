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
    env = dict(os.environ, ZOEYOS_HOME=str(zh), ZOEYOS_PLAYER="http://127.0.0.1:9")
    return subprocess.run([sys.executable, str(SCRIPT), "--hermes-home", str(hh), "--krew-home", str(kh),
                           "--no-skills", "--only", "krew-board", *a], capture_output=True, text=True, env=env)


def _profile(hh, name, env_text):
    d = hh if name == "main" else hh / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".env").write_text(env_text)
    return d / ".env"


def test_no_zoeyos_says_so_and_writes_nothing(tmp_path):
    r = run(tmp_path / "h", tmp_path / "k", tmp_path / "z")
    assert r.returncode == 0, r.stderr
    assert "Crissa not installed" in r.stdout and not (tmp_path / "z").exists()


def test_the_offer_references_the_key_and_leaves_the_wall_alone(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    envf = _profile(hh, "zoey", 'API_SERVER_ENABLED=true\nexport API_SERVER_KEY="%s"\nAPI_SERVER_PORT=8650\n' % KEY)
    zh.mkdir()
    (zh / "agent.json").write_text('{"url": "http://127.0.0.1:1234/v1"}')
    r = run(hh, tmp_path / "k", zh)
    assert r.returncode == 0, r.stderr
    offer = json.loads((zh / "endpoints.d" / "hermes-zoey.json").read_text())
    assert offer == {"name": "Hermes (zoey)", "url": "http://127.0.0.1:8650/v1", "model": "zoey",
                     "session_header": "X-Hermes-Session-Id", "system_prompt": "",
                     "key_ref": {"file": str(envf.resolve()), "name": "API_SERVER_KEY"}, "by": "krew"}
    assert KEY not in r.stdout + r.stderr and KEY not in (zh / "endpoints.d" / "hermes-zoey.json").read_text()
    assert (zh / "agent.json").read_text() == '{"url": "http://127.0.0.1:1234/v1"}', "the live endpoint is untouched"
    assert oct((zh / "endpoints.d" / "hermes-zoey.json").stat().st_mode & 0o777) == "0o600"
    assert "click Use" in r.stdout
    assert "same" in run(hh, tmp_path / "k", zh).stdout


def test_an_api_server_that_is_off_says_how_to_turn_it_on(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    _profile(hh, "zoey", "API_SERVER_KEY=%s\n" % KEY)
    zh.mkdir()
    r = run(hh, tmp_path / "k", zh)
    assert "the API server is off" in r.stdout and "API_SERVER_ENABLED=true" in r.stdout
    assert not (zh / "endpoints.d").exists()
    _profile(hh, "zoey", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=short\n")
    assert "16+ characters" in run(hh, tmp_path / "k", zh).stdout


def test_a_multiplexed_gateway_serves_the_profile_under_p(tmp_path):
    hh, zh = tmp_path / "h", tmp_path / "z"
    root = _profile(hh, "main", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\nAPI_SERVER_HOST=0.0.0.0\n" % KEY)
    (hh / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")
    own = _profile(hh, "zoey", "OTHER=1\n")
    zh.mkdir()
    # a named profile never inherits the owner's key (Hermes answers 401 at /p/zoey): no own key, no offer
    out = run(hh, tmp_path / "k", zh).stdout
    assert "16+ characters" in out and str(own) in out and not (zh / "endpoints.d").exists()
    own.write_text("OTHER=1\nAPI_SERVER_KEY=%s\n" % KEY[::-1])
    run(hh, tmp_path / "k", zh)
    offer = json.loads((zh / "endpoints.d" / "hermes-zoey.json").read_text())
    assert offer["url"] == "http://127.0.0.1:%d/p/zoey/v1" % 8642 and offer["key_ref"]["file"] == str(own.resolve())
    assert root != own


def test_multiplex_wins_when_named_profile_also_has_enabled(tmp_path):
    """A leftover API_SERVER_ENABLED+PORT on the named profile must not beat multiplex_profiles."""
    hh, zh = tmp_path / "h", tmp_path / "z"
    _profile(hh, "main", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\nAPI_SERVER_PORT=%d\n" % (KEY, 8642))
    (hh / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n")
    own = _profile(hh, "zoey", "API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\nAPI_SERVER_PORT=%d\n" % (KEY[::-1], 8652))
    zh.mkdir()
    run(hh, tmp_path / "k", zh)
    offer = json.loads((zh / "endpoints.d" / "hermes-zoey.json").read_text())
    assert offer["url"] == "http://127.0.0.1:%d/p/zoey/v1" % 8642
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
