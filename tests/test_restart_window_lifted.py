"""Crissa's restart window (the User 2026-09-17): Crissa rules, the law vetoes, the User
is last resort, and it all happens in the User's off-hours."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))
sys.path.insert(0, str(ROOT / "crew" / "lib"))

# krew: the house hard-codes its units, probes and paths; krew reads them from $KREW_HOME/restart-window.json.
# This suite is the house's, so it runs against the reference install's settings (config/restart-window.example.json
# `_reference_install`, with the unit names the house's own tests use), in a private KREW_HOME, on its own copy of
# the module (krew's tests/test_restart_window.py reloads the shared one with no settings at all).
import os  # noqa: E402
import tempfile  # noqa: E402

_KH = Path(tempfile.mkdtemp(prefix="krew-rw-lifted-"))
REF = {
    "enabled": True,
    "tiers": {"rail-stt.service": 2, "audio8-asr.service": 2, "rail-align.service": 2, "ear-bridge.service": 2,
              "rail-presence.service": 2, "rail-camera.service": 2, "audio8-tts.service": 2,
              "crew.service": 3, "turbo-voice.service": 3,
              "krew-conductor.service": 4, "krew-wall-ascii.service": 4, "krew-crissa-display.service": 4,
              "voiceonly-home.service": 4, "face-shell.service": 4,
              "ops.service": 5, "hermes-gateway.service": 5},
    "crew_units": ["crew.service"],
    "front_door_units": ["hermes-gateway.service", "ops.service", "ear-bridge.service", "krew-conductor.service",
                         "voiceonly-home.service"],
    "api_units": ["hermes-gateway.service", "ops.service"],
    "hermes_units": ["hermes-gateway.service", "hermes-dashboard.service"],
    "rulers": ["crissa", "user", "human", "dashboard"],
    "busy": {"ear_health_url": "http://127.0.0.1:8194/health", "ear_unit": "ear-bridge.service",
             "hands_procs": ["crissa-hands.py"], "speak_procs": ["krew-speak", "krew-speak.py"]},
    "api": {"health_detailed_url": "http://127.0.0.1:1/health/detailed", "key_env": "API_SERVER_KEY"},
    "list_root": str(_KH / "approvals"),
}
(_KH / "restart-window.json").write_text(json.dumps(REF), encoding="utf-8")
_saved = {k: os.environ.get(k) for k in ("KREW_HOME", "KREW_RESTART_WINDOW_SETTINGS")}
os.environ["KREW_HOME"] = str(_KH)
os.environ.pop("KREW_RESTART_WINDOW_SETTINGS", None)
_spec = importlib.util.spec_from_file_location("restart_window_lifted", ROOT / "window" / "lib" / "restart_window.py")
rw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rw)
for _k, _v in _saved.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _own_module(monkeypatch):
    """The executor script imports `restart_window` by name: hand it this suite's copy for the test."""
    monkeypatch.setitem(sys.modules, "restart_window", rw)

NIGHT = dt.datetime(2026, 9, 17, 23, 30)
DAY = dt.datetime(2026, 9, 17, 14, 0)
AFTER_MIDNIGHT = dt.datetime(2026, 9, 18, 1, 15)


def facts(**kw):
    base = {"hours": {"window": "20:00-02:00", "source": "init"}, "in_window": True, "window_key": "2026-09-17",
            "owed": [{"unit": "rail-stt.service", "tier": 2, "why": "x", "security": False},
                     {"unit": "crew.service", "tier": 3, "why": "x", "security": True},
                     {"unit": "ops.service", "tier": 5, "why": "x", "security": False}],
            "reboot": {"wanted": False, "pkgs": []}, "kanban": {"running": 0, "ready": 0},
            "occupancy": "normal", "occupancy_pending": "", "user_busy": False, "nightly_active": False}
    base.update(kw)
    return base


def card(*comments, created=100.0):
    return {"id": "t_1", "open": True, "created_at": created,
            "comments": [{"author": a, "body": b, "created_at": t} for a, b, t in comments]}


STATE = {"card": "t_1", "window_key": "2026-09-17"}


def test_the_window_is_the_users_global_from_init():
    # krew: the one global is `off_hours` in krew.yaml (the house's Init wrote site.json training_window)
    import krew_hours
    assert krew_hours.off_hours({"off_hours": "20:00-02:00"})["source"] == "init"
    unset = krew_hours.off_hours({"manners": {"quiet_hours": "21:00-06:00"}})
    assert unset["source"] == "unset" and "off_hours" in unset["why"], "quiet hours are not a work window"
    assert krew_hours.off_hours({"off_hours": "8pm"})["source"] == "invalid"
    assert krew_hours.contains(NIGHT, "20:00-02:00") and krew_hours.contains(AFTER_MIDNIGHT, "20:00-02:00")
    assert not krew_hours.contains(DAY, "20:00-02:00")
    assert krew_hours.started_on(AFTER_MIDNIGHT, "20:00-02:00") == "2026-09-17"


def test_no_window_written_means_idle_and_says_so():
    d = rw.decide({}, facts(hours={"window": None, "source": "unset", "why": "off-hours not set"}), None, NIGHT)
    assert d == {"action": "unset", "why": "off-hours not set"}


def test_set_off_hours_goes_through_inits_writer(tmp_path, monkeypatch):
    # krew: the writer is krew.yaml's off_hours line (the house wrote through Init's site writer); every other
    # line of the file is left as it is
    import krew_hours
    cfg = tmp_path / "krew.yaml"
    cfg.write_text("# mine\nhands:\n  profile: crissa\noff_hours: \"22:00-06:00\"\n")
    monkeypatch.setenv("KREW_CONFIG", str(cfg))
    assert krew_hours.set_off_hours("21:00-03:00", by="crissa")["window"] == "21:00-03:00"
    text = cfg.read_text()
    assert 'off_hours: "21:00-03:00"' in text and 'off_hours_set_by: "crissa"' in text
    assert text.startswith("# mine\nhands:\n  profile: crissa\n") and text.count("off_hours:") == 1
    import pytest
    with pytest.raises(ValueError):
        krew_hours.set_off_hours("late")


def test_the_desktop_session_is_never_owed():
    debt = {"units": {
        "gnome-session-manager" "@ubuntu.service": {"actionable": True, "maps_n": 1},
        "at-spi-dbus-bus.service": {"actionable": True, "maps_n": 1},
        "turbo-voice.service": {"actionable": True, "maps_n": 2, "venv_py": {"e": ["a 1"]}},
        "rail-stt.service": {"actionable": True, "venv_py": {"e": ["a 1"]}},
        "wall.service": {"actionable": False, "session": True},
    }}
    owed = rw.owed_units(debt)
    assert [r["unit"] for r in owed] == ["rail-stt.service", "turbo-voice.service"]
    assert owed[1]["security"] and not owed[0]["security"]


def test_outside_the_window_nothing_is_filed_or_run():
    assert rw.decide({}, facts(in_window=False), None, DAY)["action"] == "idle"


def test_first_tick_in_the_window_files_the_card():
    d = rw.decide({}, facts(), None, NIGHT)
    assert d["action"] == "file" and d["window_key"] == "2026-09-17"


def test_no_ruling_waits_on_crissa():
    assert rw.decide(dict(STATE), facts(), card(), NIGHT) == {"action": "idle", "why": "waiting on Crissa"}


def test_go_runs_in_tier_order_crew_waits_for_workers_crissa_last():
    d = rw.decide(dict(STATE), facts(kanban={"running": 2}), card(("crissa", "go", 200)), NIGHT)
    assert d["action"] == "execute"
    assert [u["unit"] for u in d["units"]] == ["rail-stt.service", "ops.service"]
    assert [u["unit"] for u in d["deferred"]] == ["crew.service"]


def test_the_law_vetoes_the_ruling():
    for kw, word in ((dict(user_busy=True), "mid-conversation"), (dict(nightly_active=True), "nightly"),
                     (dict(occupancy_pending="render"), "occupancy switch")):
        d = rw.decide(dict(STATE), facts(**kw), card(("crissa", "go", 200)), NIGHT)
        assert d["action"] == "veto" and word in d["why"]


def test_wait_goes_when_the_board_empties():
    c = card(("crissa", "wait 1", 200))
    assert rw.decide(dict(STATE), facts(kanban={"running": 3}), c, NIGHT)["action"] == "idle"
    assert rw.decide(dict(STATE), facts(kanban={"running": 1}), c, NIGHT)["action"] == "execute"


def test_later_goes_at_its_time_across_midnight():
    c = card(("crissa", "later 01:00", 200))
    assert rw.decide(dict(STATE), facts(), c, NIGHT)["action"] == "idle"
    assert rw.decide(dict(STATE), facts(), c, AFTER_MIDNIGHT)["action"] == "execute"


def test_a_reboot_is_the_users_yes_not_crissas(tmp_path, monkeypatch):
    f = facts(reboot={"wanted": True, "pkgs": ["fwupd"]})
    d = rw.decide(dict(STATE), f, card(("crissa", "reboot", 200)), NIGHT)
    assert d["action"] == "escalate" and d["ask"] == "reboot"
    asked = dict(STATE, list_item="ask-1", list_asked="reboot")
    monkeypatch.setattr(rw, "list_answer", lambda i, root=None: "yes")
    assert rw.decide(asked, dict(f, kanban={"running": 1}), card(("crissa", "reboot", 200)), NIGHT)["action"] == "idle"
    d = rw.decide(asked, f, card(("crissa", "reboot", 200)), NIGHT)
    assert d["action"] == "execute" and d["reboot"] is True and d["ruling"]["who"] == "user"
    monkeypatch.setattr(rw, "list_answer", lambda i, root=None: "no")
    assert rw.decide(asked, f, card(("crissa", "reboot", 200)), NIGHT)["action"] == "close"


def test_the_board_roll_does_not_lose_crissas_ruling():
    """00:05 re-mints open cards without comments; the ruling was copied into state."""
    kept = dict(STATE, ruling={"verdict": "go", "arg": "", "who": "crissa", "ts": 200}, ruling_window="2026-09-17")
    assert rw.decide(kept, facts(), None, AFTER_MIDNIGHT)["action"] == "execute"
    assert rw.decide(dict(STATE), facts(), None, AFTER_MIDNIGHT)["action"] == "refile"
    # a ruling kept from an earlier window does not carry into this one
    old = dict(kept, ruling_window="2026-09-16")
    assert rw.decide(old, facts(), None, AFTER_MIDNIGHT)["action"] == "refile"


def test_park_closes_for_the_window_and_stays_closed():
    d = rw.decide(dict(STATE), facts(), card(("crissa", "park", 200)), NIGHT)
    assert d["action"] == "close"
    closed = dict(STATE, closed_window="2026-09-17")
    assert rw.decide(closed, facts(), card(("crissa", "park", 200)), NIGHT)["action"] == "idle"


def test_the_user_is_last_resort():
    # Crissa asks for hands
    d = rw.decide(dict(STATE), facts(), card(("crissa", "hands — this is the GPU", 200)), NIGHT)
    assert d["action"] == "escalate"
    # silence on a security-relevant restart for two windows
    silent = dict(STATE, missed_windows=1)
    assert rw.decide(silent, facts(), card(), NIGHT)["action"] == "escalate"
    # silence on a plain one never pages the User
    plain = facts(owed=[{"unit": "rail-stt.service", "tier": 2, "why": "x", "security": False}])
    assert rw.decide(dict(silent), plain, card(), NIGHT)["action"] == "idle"


def test_the_users_list_answer_is_a_ruling(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "LIST_ROOT", tmp_path)
    item = rw.list_ask("Restart 3 house services tonight?", "why", card_id="t_1", root=tmp_path, now=1000)
    pending = json.loads((tmp_path / "pending" / f"{item}.json").read_text())
    assert pending["options"] == ["yes", "no"] and pending["card_id"] == "t_1"
    (tmp_path / "decided").mkdir()
    (tmp_path / "decided" / f"decided_{item}.json").write_text(json.dumps({"choice": "yes"}))
    monkeypatch.setattr(rw, "list_answer", lambda i, root=tmp_path: json.loads(
        (tmp_path / "decided" / f"decided_{i}.json").read_text())["choice"])
    d = rw.decide(dict(STATE, list_item=item, list_asked="go"), facts(), card(), NIGHT)
    assert d["action"] == "execute"


def test_only_rulers_rule_and_old_comments_do_not_count():
    c = card(("ada", "go", 200), ("restart-window", "go", 210), ("crissa", "go", 50))
    assert rw.latest_ruling(c["comments"], since=100) is None
    assert rw.parse_ruling("UNBLOCK: Park — not tonight") == ("park", "")
    assert rw.parse_ruling("wait 2, board is busy") == ("wait", "2")


def test_crissa_answer_holds_a_restart_window_card(monkeypatch):
    """Her `answer` must not unblock this card (a worker would take it)."""
    src = (ROOT / "plugins/team-crissa/__init__.py").read_text()
    fn = "def _answer_holds(" + src.split("def _answer_holds(", 1)[1].split("\ndef ", 1)[0]
    ns: dict = {}
    exec("import os\n" + fn, ns)
    monkeypatch.delenv("CRISSA_ANSWER_HOLD", raising=False)
    body = rw.card_body(facts())
    assert ns["_answer_holds"]("go", body) is True
    assert ns["_answer_holds"]("go", "an ordinary card") is False


def test_the_card_tells_crissa_the_words_and_that_she_goes_last():
    body = rw.card_body(facts(reboot={"wanted": True, "pkgs": ["fwupd"]}))
    assert body.startswith(rw.GATE_MARK)
    for w in ("`go`", "`wait`", "`later 01:00`", "`reboot`", "`park`", "`hands`"):
        assert w in body
    assert "restarts you last" in body and "fwupd" in body


def test_last_opened_is_the_most_recent_night_after_close():
    import datetime as _dt
    import krew_hours
    w = "20:00-02:00"
    assert krew_hours.last_opened(_dt.datetime(2026, 9, 18, 2, 25), w) == "2026-09-17"
    assert krew_hours.last_opened(_dt.datetime(2026, 9, 18, 14, 0), w) == "2026-09-17"
    assert krew_hours.last_opened(_dt.datetime(2026, 9, 18, 21, 0), w) == "2026-09-18"
    assert krew_hours.started_on(_dt.datetime(2026, 9, 18, 2, 25), w) == "2026-09-18", "unchanged: tonight's key"


def test_a_wait_takes_its_number_from_wait_not_from_the_sentence():
    """2026-09-17 20:09: "wait — 17 restarts owed, 3 workers still running; restart once
    board is down to 2" was read as `wait 17` (satisfied at once) and 16 units restarted
    with 3 workers running, logged "go (ruled by crissa)"."""
    assert rw.parse_ruling("wait — 17 restarts owed, 3 workers still running; restart once board is down to 2") == ("wait", "2")
    assert rw.parse_ruling("wait 2, board is busy") == ("wait", "2")
    assert rw.parse_ruling("wait until fewer than 3 are running") == ("wait", "3")
    assert rw.parse_ruling("wait — 17 restarts owed") == ("wait", "")       # no bar named: the default applies
    assert rw.wait_satisfied("2", {"kanban": {"running": 3}}) is False
    assert rw.wait_satisfied("", {"kanban": {"running": 3}}) is False


# ── a hermes env the running gateway has not loaded (09-29) ──────────────────

def _hermes_box(tmp_path, running_gen, current_gen, *, extra_gen=None):
    inst = tmp_path / "installs" / "08f7d59f9758a75d"
    for g in {running_gen, current_gen, extra_gen} - {None}:
        (inst / "environments" / g / "venv").mkdir(parents=True, exist_ok=True)
    facts = inst / "facts.json"
    facts.write_text(json.dumps({"packages": {"venv": {"environment": str(inst / "environments" / current_gen / "venv")}}}))
    proc = tmp_path / "proc" / "4242"
    proc.mkdir(parents=True)
    lines = [f"7f00-7f01 r-xp 0 08:01 1 {inst}/environments/{running_gen}/venv/lib/python3.14/site-packages/pydantic_core/_core.so"]
    if extra_gen:
        lines.append(f"7f02-7f03 r-xp 0 08:01 2 {inst}/environments/{extra_gen}/venv/lib/x.so")
    (proc / "maps").write_text("\n".join(lines) + "\n")
    return tmp_path / "installs", tmp_path / "proc", facts


def test_the_0928_case_is_owed(tmp_path, monkeypatch):
    """The Hindsight plugin's env landed while the gateway ran the old one."""
    monkeypatch.delenv("KREW_RESTART_HERMES_ENV", raising=False)
    installs, proc, facts = _hermes_box(tmp_path, "aaaa1111", "bbbb2222")
    rows = rw.hermes_env_owed(lambda u: 4242, installs=installs, proc=proc)
    assert len(rows) == 1
    r = rows[0]
    assert r["unit"] == "hermes-gateway.service" and r["tier"] == rw.TIER_OF["hermes-gateway.service"]
    assert "running aaaa1111, current bbbb2222" in r["why"] and r["since"] == facts.stat().st_mtime
    assert rw.merge_owed([], rows)[0]["unit"] == "hermes-gateway.service"


def test_a_gateway_on_the_current_env_owes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("KREW_RESTART_HERMES_ENV", raising=False)
    installs, proc, _ = _hermes_box(tmp_path, "bbbb2222", "bbbb2222")
    assert rw.hermes_env_owed(lambda u: 4242, installs=installs, proc=proc) == []


def test_unknown_is_never_a_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("KREW_RESTART_HERMES_ENV", raising=False)
    installs, proc, facts = _hermes_box(tmp_path, "aaaa1111", "bbbb2222", extra_gen="cccc3333")
    assert rw.hermes_env_owed(lambda u: 4242, installs=installs, proc=proc) == []      # two gens mapped: ambiguous
    assert rw.hermes_env_owed(lambda u: None, installs=installs, proc=proc) == []      # not running
    installs, proc, facts = _hermes_box(tmp_path / "b", "aaaa1111", "bbbb2222")
    facts.write_text("{not json")
    assert rw.hermes_env_owed(lambda u: 4242, installs=installs, proc=proc) == []      # unreadable facts


def test_hermes_env_kill_switch(tmp_path, monkeypatch):
    installs, proc, _ = _hermes_box(tmp_path, "aaaa1111", "bbbb2222")
    monkeypatch.setenv("KREW_RESTART_HERMES_ENV", "0")
    assert rw.hermes_env_owed(lambda u: 4242, installs=installs, proc=proc) == []


def test_the_window_gathers_the_hermes_env_row():
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text()
    assert "rw.hermes_env_owed(unit_main_pid)" in src and "def unit_main_pid(" in src


# ── is the User mid-conversation? the check reads fields that exist (2026-09-30) ──
# The ear bridge's own /health, captured live 2026-09-30 10:46. The old check read
# hear.session_open / crissa.speaking from the oracle's /v1/house, which has neither.
EAR_IDLE = {"ok": True, "speaking": False, "session_open": False, "paused": True, "dispatch_busy": False,
            "comms_hold": False, "comms_hold_until": 0.0, "comms_hold_sec": 3600.0,
            "speak_quiet_sec": 2.0, "flush_fast_sec": 0.3}
CRISSA_OWED = [{"unit": "hermes-gateway.service", "tier": 4, "why": "config: PyJWT", "security": True}]


def _script():
    spec = importlib.util.spec_from_file_location("krew_restart_window_busy", ROOT / "window" / "scripts" / "krew-restart-window.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_an_idle_krew_is_not_busy():
    assert rw.busy_reason(EAR_IDLE, hands=False, on_air=False, quiet_for=600.0) == ""
    assert rw.busy_reason(EAR_IDLE, quiet_for=None) == ""              # unmeasured quiet does not invent a talk


def test_mid_conversation_the_window_holds_crissas_restart(monkeypatch):
    """He is talking to her: a session is open, the ear heard him 4 s ago. Crissa rules `go`. Nothing restarts."""
    m = _script()
    monkeypatch.setattr(rw, "ear_health", lambda: dict(EAR_IDLE, session_open=True, paused=False))
    monkeypatch.setattr(rw, "proc_running", lambda *names: False)
    monkeypatch.setattr(rw, "ear_quiet_for", lambda window_min=10: 4.0)
    monkeypatch.setattr(rw, "api_runs", lambda: {"runs": 0})
    m.log = lambda msg: None
    assert m.busy_reason() == "a hear session is open"
    assert m.user_busy() is True
    f = facts(owed=CRISSA_OWED, user_busy=m.user_busy())
    d = rw.decide(dict(STATE), f, card(("crissa", "go", 200)), NIGHT)
    assert d["action"] == "veto" and "mid-conversation" in d["why"]
    now, later = rw.plan(CRISSA_OWED, f)
    assert now == [] and later[0]["deferred"] == "the User is talking to Crissa"
    # and the same ruling goes through once he has stopped
    monkeypatch.setattr(rw, "ear_health", lambda: dict(EAR_IDLE))
    monkeypatch.setattr(rw, "ear_quiet_for", lambda window_min=10: 300.0)
    assert m.user_busy() is False
    d = rw.decide(dict(STATE), facts(owed=CRISSA_OWED, user_busy=False), card(("crissa", "go", 200)), NIGHT)
    assert d["action"] == "execute" and [u["unit"] for u in d["units"]] == ["hermes-gateway.service"]


def test_every_way_a_conversation_is_in_flight_holds():
    assert "speaking" in rw.busy_reason(dict(EAR_IDLE, speaking=True), quiet_for=600.0)
    assert "brain" in rw.busy_reason(dict(EAR_IDLE, dispatch_busy=True), quiet_for=600.0)
    assert "session" in rw.busy_reason(dict(EAR_IDLE, session_open=True), quiet_for=600.0)
    assert rw.busy_reason(EAR_IDLE, hands=True, quiet_for=600.0) == "a hands run is in flight"
    assert rw.busy_reason(EAR_IDLE, on_air=True, quiet_for=600.0) == "a line is being spoken"
    # a pause between two sentences is not the end of a talk: 90 s of quiet, by default
    assert rw.QUIET_S == 90
    assert "ago" in rw.busy_reason(EAR_IDLE, quiet_for=89.0)
    assert rw.busy_reason(EAR_IDLE, quiet_for=90.0) == ""
    assert rw.busy_reason(EAR_IDLE, quiet_for=20.0, quiet_s=10) == ""


def test_a_health_body_that_lost_its_keys_holds_instead_of_reading_idle():
    """How it went blind: the body answered, the keys were not in it, and .get() said False forever."""
    oracle_house = {"ok": True, "voice": {"ear": {"open": True}}, "chat": [], "ear_open": True}
    assert rw.busy_reason(oracle_house, quiet_for=600.0).startswith("cannot tell")
    assert rw.busy_reason({}, quiet_for=600.0).startswith("cannot tell")


def test_an_ear_that_does_not_answer_cannot_be_carrying_a_conversation():
    assert rw.busy_reason(None, quiet_for=600.0) == ""
    assert rw.busy_reason(None, hands=True) == "a hands run is in flight"     # her hands still can


def test_the_script_reads_the_ears_own_health_and_never_the_oracle_for_this():
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text(encoding="utf-8")
    lib = (ROOT / "window" / "lib" / "restart_window.py").read_text(encoding="utf-8")
    body = src.split("def user_busy()", 1)[1].split("\ndef ", 1)[0] + src.split("def busy_reason(", 1)[1].split("\ndef ", 1)[0]
    assert "KREW_URL" not in body and 'get("hear")' not in src and 'get("crissa")' not in src
    assert ":8194/health" in lib and "rw.busy_now(unit)" in body
    for key in rw.EAR_BUSY_KEYS:
        assert key in EAR_IDLE, f"{key} is not in the ear bridge's health"


def test_a_running_script_is_found_by_argv_not_by_command_text(tmp_path):
    import subprocess
    m = rw
    name = "zz-busy-probe-%d.py" % id(tmp_path)
    assert m.proc_running(name) is False
    # a shell whose command text merely MENTIONS the name is not that script running
    sh = subprocess.Popen(["/bin/sh", "-c", f"sleep 20; : {name}"])
    try:
        assert m.proc_running(name) is False
    finally:
        sh.kill(); sh.wait()
    script = tmp_path / name
    script.write_text("import time\ntime.sleep(20)\n")
    py = subprocess.Popen([sys.executable, str(script)])
    try:
        import time as _t
        _t.sleep(0.3)
        assert m.proc_running(name) is True
    finally:
        py.kill(); py.wait()


# ── the executor asks again before every unit (2026-09-30) ──────────────────
TONIGHT = [{"unit": "ear-bridge.service", "tier": 2, "why": "config: answer after a tool", "security": False},
           {"unit": "krew-conductor.service", "tier": 3, "why": "config: turn fill", "security": False},
           {"unit": "voiceonly-home.service", "tier": 4, "why": "config: heavy-render", "security": False}]


def test_the_ear_the_wall_and_the_glass_server_are_crissas_units():
    for u in ("ear-bridge.service", "krew-conductor.service", "voiceonly-home.service",
              "hermes-gateway.service", "ops.service"):
        assert u in rw.CRISSA_HER_UNITS
    now, later = rw.plan(TONIGHT, {"user_busy": True, "kanban": {"running": 0}})
    assert now == [] and [d["deferred"] for d in later] == ["the User is talking to Crissa"] * 3
    now, later = rw.plan(TONIGHT, {"user_busy": False, "kanban": {"running": 0}})
    assert [r["unit"] for r in now] == [r["unit"] for r in TONIGHT] and later == []


def test_run_units_stops_where_the_conversation_starts():
    asked, ran = [], []
    answers = iter(["", "a hear session is open", "SHOULD NEVER BE ASKED"])

    def busy(row):
        asked.append((len(ran), row["unit"]))
        return next(answers)

    results, held = rw.run_units(TONIGHT, lambda r: ran.append(r["unit"]) or {"unit": r["unit"], "ok": True}, busy)
    assert ran == ["ear-bridge.service"] and [x["unit"] for x in results] == ["ear-bridge.service"]
    assert [h["unit"] for h in held] == ["krew-conductor.service", "voiceonly-home.service"]
    assert all(h["deferred"] == "stopped before it: a hear session is open" for h in held)
    assert asked == [(0, "ear-bridge.service"), (1, "krew-conductor.service")]   # before each unit, told which


def test_run_units_runs_everything_when_nobody_talks_and_nothing_when_he_already_is():
    results, held = rw.run_units(TONIGHT, lambda r: {"unit": r["unit"], "ok": True}, lambda r: "")
    assert len(results) == 3 and held == []
    results, held = rw.run_units(TONIGHT, lambda r: pytest_fail("restarted under a conversation"), lambda r: "Crissa is speaking")
    assert results == [] and len(held) == 3
    results, held = rw.run_units(TONIGHT, lambda r: {"unit": r["unit"], "ok": True}, None)   # the kill switch's shape
    assert len(results) == 3 and held == []
    assert rw.recheck_enabled({}) and not rw.recheck_enabled({"KREW_RESTART_RECHECK": "0"})


def pytest_fail(msg):
    raise AssertionError(msg)


def _executor(monkeypatch, tmp_path, busy_answers):
    """The real execute() with the house stubbed out: no systemctl, no board, its own state."""
    import types
    m = _script()
    restarted, comments, completed, store = [], [], [], {"executing": {"unit": "x"}, "window_key": "2026-09-30", "card": "t_1"}
    pu = types.ModuleType("panel_units")
    pu.unit_props = lambda name, scope: {"LoadState": "loaded", "InvocationID": "inv"}
    pu._sysctl = lambda scope: ["systemctl", "--user"]
    pu._run = lambda cmd: (restarted.append(cmd[-1]) or (0, "", ""))
    pu.postcheck = lambda name, scope, port, inv: {"ok": True, "note": "new run, settled"}
    monkeypatch.setitem(sys.modules, "panel_units", pu)
    monkeypatch.setattr(rw, "load_state", lambda path=None: dict(store))
    monkeypatch.setattr(rw, "save_state", lambda state, path=None: (store.clear(), store.update(state)))
    monkeypatch.delenv("KREW_RESTART_RECHECK", raising=False)
    answers = iter(busy_answers)
    m.busy_reason = lambda unit=None: next(answers)
    m.unit_ports = lambda: {}
    m.comment = lambda tid, text: comments.append(text)
    m.complete = lambda tid, result: completed.append(tid)
    m.log = lambda msg: None
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"card": "t_1", "units": TONIGHT, "deferred": [], "reboot": False,
                                "ruling": {"verdict": "go", "who": "crissa"}}))
    rc = m.execute(plan)
    return types.SimpleNamespace(rc=rc, restarted=restarted, comments=comments, completed=completed, state=store)


def test_a_conversation_that_starts_partway_through_the_run_stops_it_and_the_rest_stay_owed(monkeypatch, tmp_path):
    """Crissa ruled go, the ear restarted, and then he started talking: the wall and the glass server are
    NOT restarted under him, the card stays open, the window is not closed, and their rows are still owed."""
    x = _executor(monkeypatch, tmp_path, ["", "a hear session is open"])
    assert x.restarted == ["ear-bridge.service"]
    said = x.comments[-1]
    assert "1 restarted and checked, 0 failed. STOPPED with 2 left owed" in said
    assert "- ok ear-bridge.service" in said
    assert "- deferred krew-conductor.service: stopped before it: a hear session is open" in said
    assert "- deferred voiceonly-home.service: stopped before it: a hear session is open" in said
    assert x.completed == [] and "closed_window" not in x.state          # the card and the window stay open
    assert "executing" not in x.state and x.rc == 0                       # so the next tick decides again
    # the rows: only a unit that restarted AFTER its row was written drops out
    owed = tmp_path / "restart-owed.json"
    owed.write_text(json.dumps({r["unit"]: {"since": 1000.0, "why": r["why"], "by": "t"} for r in TONIGHT}))
    started = {"ear-bridge.service": 2000.0, "krew-conductor.service": 500.0, "voiceonly-home.service": 500.0}
    left = [r["unit"] for r in rw.config_owed(started.get, path=owed)]
    assert left == ["krew-conductor.service", "voiceonly-home.service"]
    # next tick, still talking: the law holds the standing ruling; once he stops, the two that are left run
    f = facts(owed=rw.config_owed(started.get, path=owed), user_busy=True)
    assert rw.decide(dict(STATE), f, card(("crissa", "go", 200)), NIGHT)["action"] == "veto"
    d = rw.decide(dict(STATE), dict(f, user_busy=False), card(("crissa", "go", 200)), NIGHT)
    assert d["action"] == "execute" and [u["unit"] for u in d["units"]] == left


def test_a_quiet_krew_gets_the_whole_run_and_the_card_closes(monkeypatch, tmp_path):
    x = _executor(monkeypatch, tmp_path, ["", "", ""])
    assert x.restarted == [r["unit"] for r in TONIGHT]
    assert "STOPPED" not in x.comments[-1] and x.completed == ["t_1"] and x.state.get("closed_window") == "2026-09-30"


def test_already_talking_when_the_executor_starts_restarts_nothing(monkeypatch, tmp_path):
    x = _executor(monkeypatch, tmp_path, ["a hands run is in flight"])
    assert x.restarted == [] and x.completed == [] and "STOPPED with 3 left owed" in x.comments[-1]


def test_the_recheck_has_a_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_RESTART_RECHECK", "0")
    import types
    m = _script()
    assert '(lambda r: busy_reason(r["unit"])) if rw.recheck_enabled() else None' in (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text()
    assert rw.recheck_enabled() is False


# ── a paid row leaves the file by itself (2026-09-30) ───────────────────────

def _owed_file(tmp_path, rows):
    p = tmp_path / "restart-owed.json"
    p.write_text(json.dumps(rows, indent=1))
    return p


def test_a_row_is_dropped_once_its_unit_started_after_it(tmp_path):
    p = _owed_file(tmp_path, {
        "voiceonly-home.service": {"since": 1000.0, "why": "panel learned heavy-render", "by": "a"},
        "krew-conductor.service": {"since": 2000.0, "why": "turn fill", "by": "b"},
        "hermes-gateway.service": {"since": 5000.0, "why": "env re-pinned", "by": "c"}})
    started = {"voiceonly-home.service": 3000.0, "krew-conductor.service": 3000.0,   # one boot paid both
               "hermes-gateway.service": 4000.0}                                        # started BEFORE its row
    dropped = rw.prune_paid(started.get, path=p)
    assert [d["unit"] for d in dropped] == ["krew-conductor.service", "voiceonly-home.service"]
    assert dropped[0]["started"] == 3000.0 and dropped[0]["why"] == "turn fill"
    left = json.loads(p.read_text())
    assert list(left) == ["hermes-gateway.service"] and left["hermes-gateway.service"]["why"] == "env re-pinned"
    # what the window then reads as owed is exactly what the file says
    assert [r["unit"] for r in rw.config_owed(started.get, path=p)] == list(left)
    assert rw.prune_paid(started.get, path=p) == []                     # nothing more; the file is not rewritten
    started["hermes-gateway.service"] = 5000.0                          # the same second counts, as in config_owed
    assert [d["unit"] for d in rw.prune_paid(started.get, path=p)] == ["hermes-gateway.service"]
    assert json.loads(p.read_text()) == {}


def test_an_unreadable_start_time_keeps_the_row(tmp_path):
    p = _owed_file(tmp_path, {"ops.service": {"since": 1000.0, "why": "x", "by": "a"}})
    assert rw.prune_paid(lambda u: None, path=p) == []
    assert list(json.loads(p.read_text())) == ["ops.service"]


def test_prune_leaves_a_missing_or_broken_file_alone(tmp_path):
    assert rw.prune_paid(lambda u: 9e9, path=tmp_path / "none.json") == []
    assert not (tmp_path / "none.json").exists()
    p = tmp_path / "restart-owed.json"
    p.write_text("{not json")
    assert rw.prune_paid(lambda u: 9e9, path=p) == [] and p.read_text() == "{not json"
    p.write_text("[]")
    assert rw.prune_paid(lambda u: 9e9, path=p) == [] and p.read_text() == "[]"


def test_a_row_written_while_pruning_is_not_lost(tmp_path):
    """owe_restart and prune_paid take the same lock: a prune that read the file before a new row
    landed must not write the old contents back over it."""
    import threading
    p = _owed_file(tmp_path, {"a.service": {"since": 1.0, "why": "old", "by": "t"}})
    gate, entered = threading.Event(), threading.Event()

    def slow_started(unit):
        entered.set()
        gate.wait(5)
        return 9e9                                                      # a.service is paid

    t = threading.Thread(target=lambda: rw.prune_paid(slow_started, path=p))
    t.start()
    assert entered.wait(5)                                              # prune holds the lock, mid-read
    w = threading.Thread(target=lambda: rw.owe_restart("b.service", "new", "t", since=9e9 + 1, path=p))
    w.start()
    w.join(0.3)
    assert w.is_alive()                                                 # the writer waits for the lock
    gate.set()
    t.join(5); w.join(5)
    assert list(json.loads(p.read_text())) == ["b.service"]             # a dropped, b kept


def test_the_tick_prunes_and_the_read_only_verb_does_not():
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text(encoding="utf-8")
    prune = src.split("def prune_owed(", 1)[1].split("\ndef ", 1)[0]
    assert "if dry or not rw.prune_enabled():" in prune and "rw.prune_paid(unit_started)" in prune
    tick = src.split("def tick(", 1)[1].split("\ndef ", 1)[0]
    assert tick.index("prune_owed(dry)") < tick.index("if not rw.enabled():") < tick.index("facts = gather()")
    assert rw.prune_enabled({}) and not rw.prune_enabled({"KREW_RESTART_PRUNE": "0"})



# ── a run in flight on the gateway's API holds a gateway restart (2026-09-30) ──
QUIET = dict(hands=False, on_air=False, quiet_for=600.0)


def test_the_api_term_holds_on_a_run_passes_on_none_and_holds_when_it_cannot_tell():
    assert rw.busy_reason(EAR_IDLE, api={"runs": 1}, **QUIET) == "1 run(s) in flight on the gateway's API"
    assert rw.busy_reason(EAR_IDLE, api={"runs": 0}, **QUIET) == ""
    for bad in ({"error": "HTTP 401"}, {"error": "no api_server.active_runs in the body"}, {"runs": None},
                {"runs": "1"}, {"runs": True}, {}):
        assert rw.busy_reason(EAR_IDLE, api=bad, **QUIET).startswith("cannot tell: the gateway's API"), bad
    assert rw.busy_reason(EAR_IDLE, api={"down": True}, **QUIET) == ""     # not listening: nothing in flight
    assert rw.busy_reason(EAR_IDLE, api=None, **QUIET) == ""               # not an API unit: not asked


def test_the_1227_shape_holds():
    """No crissa-hands.py, nothing on the air, the ear quiet well past 90 s - and one api_server run:
    the steward's board-sweep message to Crissa between two hands calls. The restart interrupted it."""
    assert rw.busy_reason(EAR_IDLE, hands=False, on_air=False, quiet_for=240.0, api={"runs": 1}) != ""


def _house(monkeypatch, api, *, calls=None):
    monkeypatch.setattr(rw, "ear_health", lambda: dict(EAR_IDLE))
    monkeypatch.setattr(rw, "proc_running", lambda *names: False)
    monkeypatch.setattr(rw, "ear_quiet_for", lambda window_min=10: 600.0)

    def runs():
        if calls is not None:
            calls.append(1)
        return api() if callable(api) else api

    monkeypatch.setattr(rw, "api_runs", runs)
    monkeypatch.delenv("KREW_RESTART_API_RUNS", raising=False)
    # a whole hermes tree unless a test says otherwise: never the live tree from a test
    monkeypatch.setattr(rw, "hermes_stack", lambda: dict(CLEAN))
    monkeypatch.setattr(rw, "hermes_node_deps", lambda tree=None: dict(NODE_OK))


def test_busy_now_asks_the_api_only_for_the_units_it_would_interrupt(monkeypatch):
    calls = []
    _house(monkeypatch, {"runs": 1}, calls=calls)
    for u in ("hermes-gateway.service", "ops.service"):
        assert rw.busy_now(u) == "1 run(s) in flight on the gateway's API"
    assert len(calls) == 2          # the host gateway and ops (the per-profile unit is retired, 2026-10-08)
    for u in ("ear-bridge.service", "krew-conductor.service", "rail-stt.service", None):
        assert rw.busy_now(u) == ""
    assert len(calls) == 2                               # never asked for a unit it does not concern
    monkeypatch.setenv("KREW_RESTART_API_RUNS", "0")
    assert rw.busy_now("hermes-gateway.service") == "" and len(calls) == 2


def test_busy_now_is_the_same_rule_as_busy_reason(monkeypatch):
    """The one-liner a session's waiter uses cannot drift from the window: it IS the window's call."""
    _house(monkeypatch, {"runs": 0})
    monkeypatch.setattr(rw, "ear_quiet_for", lambda window_min=10: 30.0)
    assert rw.busy_now("hermes-gateway.service") == rw.busy_reason(EAR_IDLE, quiet_for=30.0, api={"runs": 0})
    assert rw.busy_now("hermes-gateway.service", quiet_s=10) == ""
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text(encoding="utf-8")
    assert "def ear_health" not in src and "def proc_running" not in src and "def ear_quiet_for" not in src


def test_the_executor_sees_a_run_start_before_the_gateway(monkeypatch, tmp_path):
    """Per-unit recheck with the REAL busy_reason: the ear and the conductor restart; by the time the
    gateway is next, a run is in flight on its API - the gateway is not restarted and stays owed."""
    units = [{"unit": "ear-bridge.service", "tier": 2, "why": "x", "security": False},
             {"unit": "krew-conductor.service", "tier": 3, "why": "x", "security": False},
             {"unit": "hermes-gateway.service", "tier": 5, "why": "config: PyJWT", "security": True}]
    seen = []
    _house(monkeypatch, lambda: {"runs": 1 if len(seen) >= 2 else 0}, calls=None)
    import types
    m = _script()
    restarted, comments, completed, store = [], [], [], {"executing": {"unit": "x"}, "window_key": "k", "card": "t_1"}
    pu = types.ModuleType("panel_units")
    pu.unit_props = lambda name, scope: {"LoadState": "loaded", "InvocationID": "inv"}
    pu._sysctl = lambda scope: ["systemctl", "--user"]
    pu._run = lambda cmd: (restarted.append(cmd[-1]) or seen.append(cmd[-1]) or (0, "", ""))
    pu.postcheck = lambda name, scope, port, inv: {"ok": True, "note": "ok"}
    monkeypatch.setitem(sys.modules, "panel_units", pu)
    monkeypatch.setattr(rw, "load_state", lambda path=None: dict(store))
    monkeypatch.setattr(rw, "save_state", lambda state, path=None: (store.clear(), store.update(state)))
    monkeypatch.delenv("KREW_RESTART_RECHECK", raising=False)
    m.unit_ports = lambda: {}
    m.comment = lambda tid, text: comments.append(text)
    m.complete = lambda tid, result: completed.append(tid)
    m.log = lambda msg: None
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"card": "t_1", "units": units, "deferred": [], "reboot": False,
                                "ruling": {"verdict": "go", "who": "crissa"}}))
    m.execute(plan)
    assert restarted == ["ear-bridge.service", "krew-conductor.service"]
    assert ("- deferred hermes-gateway.service: stopped before it: 1 run(s) in flight on the gateway's API"
            in comments[-1])
    assert completed == [] and "closed_window" not in store


def test_the_tick_defers_api_units_while_a_run_is_in_flight():
    owed = [{"unit": "hermes-gateway.service", "tier": 5, "why": "x", "security": True},
            {"unit": "rail-stt.service", "tier": 2, "why": "x", "security": False}]
    now, later = rw.plan(owed, {"user_busy": False, "api_busy": "1 run(s) in flight on the gateway's API",
                                "kanban": {"running": 0}})
    assert [r["unit"] for r in now] == ["rail-stt.service"]
    assert later[0]["unit"] == "hermes-gateway.service" and "gateway's API" in later[0]["deferred"]
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text(encoding="utf-8")
    assert '"api_busy": rw.api_reason(rw.api_runs()) if rw.api_runs_enabled() else ""' in src


def test_api_runs_reads_the_live_adapter_count_not_the_state_file():
    src = (ROOT / "window" / "lib" / "restart_window.py").read_text(encoding="utf-8")
    body = src.split("def api_runs()", 1)[1].split("\ndef ", 1)[0]
    # krew: the URL and its key come from restart-window.json (api.health_detailed_url, api.key_env), not the
    # house's hermes_endpoint
    assert "health_detailed_url" in body and 'api.get("key_env")' in body
    assert '.get("api_server") or {}).get("active_runs")' in body
    assert "gateway_state.json" not in body and "active_agents" not in body


def test_api_runs_reader_on_a_closed_port_and_on_a_body_without_the_count(monkeypatch):
    import http.server
    import socket
    import threading
    s = socket.socket(); s.bind(("127.0.0.1", 0)); closed = s.getsockname()[1]; s.close()
    api = dict(rw.RW.get("api") or {})
    monkeypatch.setitem(rw.RW, "api", api)
    monkeypatch.setitem(api, "health_detailed_url", "http://127.0.0.1:%d/health/detailed" % closed)
    monkeypatch.setenv("API_SERVER_KEY", "k")
    assert rw.api_runs() == {"down": True}

    bodies = iter([b'{"api_server": {"active_runs": 2}}', b'{"status": "ok"}'])

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.headers.get("Authorization") == "Bearer k" else 401)
            self.end_headers()
            self.wfile.write(next(bodies))

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setitem(api, "health_detailed_url", "http://127.0.0.1:%d/health/detailed" % srv.server_address[1])
        assert rw.api_runs() == {"runs": 2}
        got = rw.api_runs()
        assert "error" in got and rw.api_reason(got).startswith("cannot tell")
    finally:
        srv.shutdown()


# ── a hermes unit holds while its tree is not whole (2026-09-30) ────────────
BROKEN = {"ok": False, "broke_at": "161-test-plugin-toolset-cache-custom-names",
          "why": "error: tests/hermes_cli/test_plugin_toolset_cache_custom_names.py: does not exist in index"}
CLEAN = {"ok": True, "base": "35272ce28b", "differs": []}
NODE_OK = {"rc": 0, "why": ""}
NODE_STALE = {"rc": 1, "why": "Workspace dependencies are stale or missing and lazy installs are disabled; run an explicit build/update"}


def test_tree_reason_holds_on_a_broken_stack_or_failing_node_deps():
    assert rw.tree_reason(CLEAN, NODE_OK) == ""
    assert rw.tree_reason(BROKEN, NODE_OK).startswith("the hermes patch stack is broken at 161-test-plugin")
    assert rw.tree_reason({"ok": False, "base": "x", "differs": ["model_tools.py"]}, NODE_OK) == \
        "the hermes patch stack is broken (differs: model_tools.py)"
    assert rw.tree_reason(CLEAN, NODE_STALE).startswith("the hermes node dependency check fails (Workspace dependencies")
    assert rw.tree_reason(BROKEN, NODE_STALE).startswith("the hermes patch stack")      # the stack is named first
    assert rw.tree_reason({"ok": False, "why": "off"}, NODE_OK) == ""                  # HERMES_TREE_STACK=0 is hermes-tree's own switch
    assert rw.tree_reason({"ok": False, "error": "SyntaxError: x"}, NODE_OK).startswith("cannot tell")
    assert rw.tree_reason(CLEAN, {"rc": None, "why": "no node"}).startswith("cannot tell")


def _tree(monkeypatch, stack, node, calls=None):
    def s():
        if calls is not None:
            calls.append("stack")
        return dict(stack)
    monkeypatch.setattr(rw, "hermes_stack", s)
    monkeypatch.setattr(rw, "hermes_node_deps", lambda tree=None: dict(node))
    monkeypatch.delenv("KREW_RESTART_TREE_CHECK", raising=False)


def test_only_hermes_units_ask_about_the_tree(monkeypatch):
    calls = []
    _tree(monkeypatch, BROKEN, NODE_STALE, calls)
    for u in ("hermes-gateway.service", "hermes-dashboard.service"):
        assert rw.tree_now(u).startswith("the hermes patch stack is broken")
    for u in ("ear-bridge.service", "ops.service", "krew-conductor.service", None):
        assert rw.tree_now(u) == ""
    assert len(calls) == 2          # the host gateway and the dashboard (the per-profile unit is retired, 2026-10-08)
    monkeypatch.setenv("KREW_RESTART_TREE_CHECK", "0")
    assert rw.tree_now("hermes-gateway.service") == "" and len(calls) == 2


def test_busy_now_holds_the_gateway_on_a_half_landed_tree_with_the_krew_quiet(monkeypatch):
    """13:36: nobody talking, no API run - and steward mid-way through landing patches 160/161."""
    _house(monkeypatch, {"runs": 0})
    _tree(monkeypatch, BROKEN, NODE_OK)
    assert rw.busy_now("hermes-gateway.service").startswith("the hermes patch stack is broken at 161")
    assert rw.busy_now("ear-bridge.service") == ""                       # not a hermes unit: goes
    _tree(monkeypatch, CLEAN, NODE_STALE)
    assert "node dependency check fails" in rw.busy_now("hermes-gateway.service")
    _tree(monkeypatch, CLEAN, NODE_OK)
    assert rw.busy_now("hermes-gateway.service") == ""                   # whole tree, quiet house: go


def test_a_conversation_is_named_before_the_tree(monkeypatch):
    _house(monkeypatch, {"runs": 0})
    monkeypatch.setattr(rw, "ear_quiet_for", lambda window_min=10: 5.0)
    _tree(monkeypatch, BROKEN, NODE_STALE)
    assert "the ear was in a turn" in rw.busy_now("hermes-gateway.service")


def test_the_window_defers_hermes_units_and_the_executor_stops_before_them(monkeypatch):
    owed = [{"unit": "rail-stt.service", "tier": 2, "why": "x", "security": False},
            {"unit": "hermes-gateway.service", "tier": 5, "why": "drain 30 s", "security": False}]
    now, later = rw.plan(owed, {"user_busy": False, "api_busy": "", "tree_busy": rw.tree_reason(BROKEN, NODE_OK),
                                "kanban": {"running": 0}})
    assert [r["unit"] for r in now] == ["rail-stt.service"]
    assert later[0]["unit"] == "hermes-gateway.service" and "patch stack is broken" in later[0]["deferred"]
    _house(monkeypatch, {"runs": 0})
    _tree(monkeypatch, BROKEN, NODE_OK)
    ran = []
    results, held = rw.run_units(owed, lambda r: ran.append(r["unit"]) or {"unit": r["unit"], "ok": True},
                                 lambda r: rw.busy_now(r["unit"]))
    assert ran == ["rail-stt.service"] and held[0]["unit"] == "hermes-gateway.service"
    assert "patch stack is broken at 161" in held[0]["deferred"]
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text(encoding="utf-8")
    assert '"tree_busy": rw.tree_now("hermes-gateway.service"),' in src


def test_the_readers_never_install_and_never_touch_the_tree(tmp_path, monkeypatch):
    lib = (ROOT / "window" / "lib" / "restart_window.py").read_text(encoding="utf-8")
    node = lib.split("def hermes_node_deps(", 1)[1].split("\ndef ", 1)[0]
    assert '"--no-install"' in node and '"--source"' in node and '"--reuse"' in node
    for bad in ("npm ci", '"ci"', '"install"', "--install"):
        assert bad not in node.replace('"--no-install"', ""), bad
    # hermes_stack runs hermes-tree.py's own stack_check (a throwaway git index); a broken script is "cannot tell"
    good = tmp_path / "good.py"
    good.write_text("def stack_check(tree=None, queue=None):\n    return {'ok': True, 'base': 'b', 'differs': []}\n")
    monkeypatch.setattr(rw, "HERMES_TREE_SCRIPT", good)
    assert rw.hermes_stack() == {"ok": True, "base": "b", "differs": []}
    bad = tmp_path / "bad.py"
    bad.write_text("def stack_check(:\n")
    monkeypatch.setattr(rw, "HERMES_TREE_SCRIPT", bad)
    assert rw.tree_reason(rw.hermes_stack(), None).startswith("cannot tell")
    assert "GIT_INDEX_FILE" in (ROOT / "crew" / "scripts" / "hermes-tree.py").read_text(encoding="utf-8")


def test_the_node_reader_runs_hermes_own_env_and_never_installs(tmp_path):
    import shutil as _sh
    node = _sh.which("node") or str(Path.home() / ".hermes" / "node" / "bin" / "node")
    build = {"env": {"PATH": str(Path(node).parent), "CI": "1"}, "workspaces": ["ui-tui", "web"]}
    assert rw.hermes_node_deps(tmp_path, build) == {"rc": 0, "why": "no node-deps.mjs"}
    s = tmp_path / "scripts" / "build"
    s.mkdir(parents=True)
    (s / "node-deps.mjs").write_text(
        "const a = process.argv;\n"
        "if (!a.includes('--no-install') || !a.includes('--source') || a.includes('apps/desktop')) process.exit(9);\n"
        "if (process.env.CI !== '1') process.exit(8);\n"
        "console.error('Error: Workspace dependencies are stale or missing'); process.exit(1);\n")
    assert rw.hermes_node_deps(tmp_path, build) == {"rc": 0, "why": "no node workspaces"}
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text("{}")
    # hermes's env and workspace set are passed through as they are (the receipt key hashes them)
    assert rw.hermes_node_deps(tmp_path, build) == {"rc": 1, "why": "Workspace dependencies are stale or missing"}
    assert rw.hermes_node_deps(tmp_path, {"error": "no hermes runtime"}) == {"rc": None, "why": "no hermes runtime"}
    assert rw.tree_reason(CLEAN, rw.hermes_node_deps(tmp_path, {"error": "x"})).startswith("cannot tell")


def test_hermes_build_env_never_runs_under_pytest():
    """It imports hermes_bootstrap in hermes's interpreter; under a test that has provisioned a foreign
    home before (09-27, 09-29). The test suite reaches it only stubbed."""
    assert "error" in rw.hermes_build_env() and "pytest" in rw.hermes_build_env()["error"]


# ── a second reason for the same unit is added, never written over (2026-09-30) ──

def test_a_second_reason_is_added_and_the_later_time_kept(tmp_path):
    """13:28 the drain timeout owed hermes-gateway a restart; 13:40 steward's patches owed it another,
    and its row replaced the first: the record then named one change of two."""
    p = tmp_path / "restart-owed.json"
    never = lambda u: 100.0                                              # started long before both rows
    rw.owe_restart("hermes-gateway.service", "restart_drain_timeout 0 -> 30 s", "session-51", since=1000.0,
                   path=p, started=never)
    row = rw.owe_restart("hermes-gateway.service", "patches 160+161", "steward", since=1720.0, path=p, started=never)
    assert row["since"] == 1720.0                                        # only a restart after BOTH pays it
    assert row["why"] == "restart_drain_timeout 0 -> 30 s | ALSO: patches 160+161"
    assert row["by"] == "session-51 + steward"
    assert [w["since"] for w in row["whys"]] == [1000.0, 1720.0]
    assert json.loads(p.read_text())["hermes-gateway.service"] == row
    # an earlier time arriving second does not pull `since` back
    row = rw.owe_restart("hermes-gateway.service", "late report of an old change", "c4", since=500.0, path=p, started=never)
    assert row["since"] == 1720.0 and len(row["whys"]) == 3 and row["by"] == "session-51 + steward + c4"
    # the same reason twice is one reason
    row = rw.owe_restart("hermes-gateway.service", "patches 160+161", "steward", since=1800.0, path=p, started=never)
    assert len(row["whys"]) == 3 and row["since"] == 1800.0


def test_a_paid_row_is_replaced_not_merged(tmp_path):
    p = tmp_path / "restart-owed.json"
    rw.owe_restart("ops.service", "old change", "a", since=1000.0, path=p, started=lambda u: 100.0)
    row = rw.owe_restart("ops.service", "new change", "b", since=3000.0, path=p, started=lambda u: 2000.0)
    assert row == {"since": 3000.0, "why": "new change", "by": "b"}      # started 2000 >= 1000: the old one was paid


def test_an_unreadable_start_time_merges_rather_than_losing_a_reason(tmp_path):
    p = tmp_path / "restart-owed.json"
    rw.owe_restart("ops.service", "one", "a", since=1000.0, path=p, started=lambda u: None)
    row = rw.owe_restart("ops.service", "two", "b", since=2000.0, path=p, started=lambda u: None)
    assert row["why"] == "one | ALSO: two" and row["since"] == 2000.0


def test_the_window_reads_a_merged_row_as_one_owed_unit_and_the_prune_pays_it_once(tmp_path):
    p = tmp_path / "restart-owed.json"
    never = lambda u: 100.0
    rw.owe_restart("hermes-gateway.service", "a", "x", since=1000.0, path=p, started=never)
    rw.owe_restart("hermes-gateway.service", "b", "y", since=2000.0, path=p, started=never)
    owed = rw.config_owed(lambda u: 1500.0, path=p)                      # restarted between the two changes
    assert [r["unit"] for r in owed] == ["hermes-gateway.service"] and "a | ALSO: b" in owed[0]["why"]
    assert rw.prune_paid(lambda u: 1500.0, path=p) == []                 # not paid yet
    assert [d["unit"] for d in rw.prune_paid(lambda u: 2500.0, path=p)] == ["hermes-gateway.service"]



# ── the off switch stops the window, not the bookkeeping (the User 2026-10-01) ──
def _off_tick(monkeypatch, *, dry=False, prune_env=None):
    m = _script()
    paid = []
    monkeypatch.setattr(m.rw, "enabled", lambda *a, **k: False)
    monkeypatch.setattr(m.rw, "prune_paid", lambda started, *a, **k: paid.append(started) or [])
    monkeypatch.setattr(m.rw, "load_state", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran past the off switch")))
    if prune_env is not None:
        monkeypatch.setenv("KREW_RESTART_PRUNE", prune_env)
    else:
        monkeypatch.delenv("KREW_RESTART_PRUNE", raising=False)
    return m, paid, m.tick(dry=dry)


def test_with_the_window_off_the_tick_still_prunes_paid_rows(monkeypatch):
    m, paid, rc = _off_tick(monkeypatch)
    assert rc == 0 and paid == [m.unit_started]


def test_with_the_window_off_a_dry_tick_and_the_prune_kill_still_prune_nothing(monkeypatch):
    _, paid, _ = _off_tick(monkeypatch, dry=True)
    assert paid == []
    _, paid, _ = _off_tick(monkeypatch, prune_env="0")
    assert paid == []


def test_with_the_window_off_a_paid_row_leaves_the_real_file(monkeypatch, tmp_path):
    m = _script()
    owed = tmp_path / "restart-owed.json"
    monkeypatch.setattr(m.rw, "CONFIG_OWED", owed)
    rw.owe_restart("krew-kanban-loop.timer", "OnActiveSec", "test", since=1000.0, path=owed,
                   started=lambda u: None)
    rw.owe_restart("voiceonly-home.service", "panel", "test", since=1000.0, path=owed,
                   started=lambda u: None)
    monkeypatch.setattr(m.rw, "enabled", lambda *a, **k: False)
    monkeypatch.setattr(m, "unit_started", lambda u: 2000.0 if u == "krew-kanban-loop.timer" else None)
    monkeypatch.delenv("KREW_RESTART_PRUNE", raising=False)
    assert m.tick() == 0
    assert list(json.loads(owed.read_text())) == ["voiceonly-home.service"]


# ── a switched-off .path / .timer is never on the list (the User 2026-10-01) ──
def test_an_off_file_named_in_the_description_switches_a_path_off(tmp_path):
    off = tmp_path / "x.off"; off.write_text("off\n")
    desc = lambda u: f"watch the tree (off switch: {off})"
    assert rw.switched_off("krew-kit-reapply.path", describe=desc, conditions=lambda u: "") == f"off file {off}"
    off.unlink()
    assert rw.switched_off("krew-kit-reapply.path", describe=desc, conditions=lambda u: "") == ""


def test_a_failing_condition_switches_a_timer_off():
    why = rw.switched_off("x.timer", describe=lambda u: "", conditions=lambda u: "ConditionPathExists=!/a.off failed")
    assert why == "condition ConditionPathExists=!/a.off failed"


def test_a_service_is_never_judged_and_the_kill_switch_judges_nothing():
    boom = lambda u: (_ for _ in ()).throw(AssertionError("asked"))
    assert rw.switched_off("voiceonly-home.service", describe=boom, conditions=boom) == ""
    assert rw.switched_off("x.path", describe=boom, conditions=boom, env={"KREW_RESTART_SKIP_OFF": "0"}) == ""


def test_split_keeps_the_rest_and_marks_the_skipped():
    owed = [{"unit": "a.path", "tier": 1, "why": "x", "security": False},
            {"unit": "b.service", "tier": 4, "why": "y", "security": False}]
    keep, skipped = rw.split_switched_off(owed, describe=lambda u: "", conditions=lambda u: "C failed" if u == "a.path" else "")
    assert [r["unit"] for r in keep] == ["b.service"] and skipped[0]["unit"] == "a.path" and skipped[0]["off"]


def test_conditions_fail_reads_systemd_analyze(monkeypatch):
    import subprocess as sp
    out = ("spice: Unknown key 'StandardError' in section [Install], ignoring.\n"
           "krew-kit-reapply.path: ConditionPathExists=!/h/.hermes/krew-kit-reapply.off failed.\nConditions failed.\n")
    monkeypatch.setattr(sp, "run", lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": out})())
    assert rw._conditions_fail("krew-kit-reapply.path") == "ConditionPathExists=!/h/.hermes/krew-kit-reapply.off failed"
    monkeypatch.setattr(sp, "run", lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "Unit x not found."})())
    assert rw._conditions_fail("x.path") == ""          # an error is not a verdict


def test_drop_owed_takes_one_row_under_the_lock(tmp_path):
    p = tmp_path / "owed.json"
    rw.owe_restart("a.path", "x", "t", since=1.0, path=p, started=lambda u: None)
    rw.owe_restart("b.service", "y", "t", since=1.0, path=p, started=lambda u: None)
    row = rw.drop_owed("a.path", "switched off on purpose", path=p)
    assert row["unit"] == "a.path" and row["dropped"] == "switched off on purpose"
    assert list(json.loads(p.read_text())) == ["b.service"] and rw.drop_owed("nope", "z", path=p) is None


def test_the_window_lists_no_switched_off_unit_and_logs_it_once(monkeypatch, tmp_path):
    m = _script()
    logs = []
    monkeypatch.setattr(m, "log", logs.append)
    facts = {"owed": [], "skipped_off": [{"unit": "a.path", "off": "off file ~/.hermes/a.off"}], "window_key": ""}
    monkeypatch.setattr(m, "gather", lambda *a, **k: facts)
    monkeypatch.setattr(m.rw, "enabled", lambda *a, **k: True)
    st = {}
    monkeypatch.setattr(m.rw, "load_state", lambda *a, **k: st)
    monkeypatch.setattr(m.rw, "save_state", lambda s, *a, **k: st.update(s))
    monkeypatch.setattr(m, "card_state", lambda *a, **k: None)
    monkeypatch.setattr(m.rw, "decide", lambda *a, **k: {"action": "idle"})
    monkeypatch.setenv("KREW_RESTART_PRUNE", "0")
    m.tick(); m.tick()
    hits = [l for l in logs if l.startswith("skipped: switched off: a.path")]
    assert len(hits) == 1 and "not a failure" in hits[0]


def test_the_executor_skips_a_unit_switched_off_since_the_ruling():
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text(encoding="utf-8")
    body = src.split("def restart_one(", 1)[1].split("\n    def ", 1)[0].split("\ndef ", 1)[0]
    assert body.index("rw.switched_off(unit)") < body.index('["restart", "--no-block", unit]')
    assert '"ok": True, "skipped": "switched off"' in body
    gather = src.split("def gather(", 1)[1].split("\ndef ", 1)[0]
    assert "rw.split_switched_off(" in gather and '"owed": owed' in gather


# ── a system unit's start counts too (the User 2026-10-02: hindsight.service stayed owed) ──
def test_start_time_falls_back_to_the_system_manager(monkeypatch):
    import subprocess as sp
    asked = []
    def run(argv, **k):
        asked.append(argv[1])
        out = "" if argv[1] == "--user" else "@1790920856\n"     # user manager: unknown; system: 02:00:56
        return type("R", (), {"stdout": out, "returncode": 0})()
    monkeypatch.setattr(sp, "run", run)
    assert rw._unit_started("hindsight.service") == 1790920856.0 and asked == ["--user", "show"]


def test_a_user_unit_never_asks_the_system_manager(monkeypatch):
    import subprocess as sp
    asked = []
    monkeypatch.setattr(sp, "run", lambda argv, **k: asked.append(argv[1]) or type("R", (), {"stdout": "@100\n"})())
    assert rw._unit_started("ear-bridge.service") == 100.0 and asked == ["--user"]


def test_the_paid_system_row_is_pruned(tmp_path):
    p = tmp_path / "owed.json"
    rw.owe_restart("hindsight.service", "timeout 120", "t", since=1790920678.0, path=p, started=lambda u: None)
    dropped = rw.prune_paid(lambda u: 1790920856.0 if u == "hindsight.service" else None, path=p)
    assert [d["unit"] for d in dropped] == ["hindsight.service"] and json.loads(p.read_text()) == {}


def test_the_script_reads_start_times_through_the_one_copy():
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text(encoding="utf-8")
    body = src.split("def unit_started(", 1)[1].split("\ndef ", 1)[0]
    assert "rw._unit_started(unit)" in body and "--user" not in body
