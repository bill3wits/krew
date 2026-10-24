"""The front door's hands (crew/scripts/zoey-hands.py, the reference install's child): CLI child, not a krew spawn.
Lifted from the reference install's test file 2026-10-10; two tests stay behind, named where they stood."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "crew" / "lib"))      # krew_yaml: the script runs under krew-crew-run, which puts crew/lib on PYTHONPATH
SPEC = importlib.util.spec_from_file_location(
    "zoey_hands", ROOT / "crew" / "scripts" / "zoey-hands.py"
)
assert SPEC and SPEC.loader
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_cua_guard(monkeypatch):
    """These tests pin the kit choice; the no-click-on-the-owner's-desktop guard (on by default) has its own
    file, tests/test_zoey_hands_cua_guard.py, so it is held off here."""
    monkeypatch.setenv("KREW_HANDS_CUA_GUARD", "0")
    # and the 10-03 defaults (crew/config/zoey-hands.json) are pinned off: these tests are about the kit's ROUTING
    for k in ("KREW_HANDS_DOCS", "KREW_HANDS_LESSONS", "KREW_HANDS_CUA_ROUTE", "KREW_HANDS_SEARCH_HINT"):
        monkeypatch.setenv(k, "0")


def test_hermes_argv_is_zoey_cli_oneshot():
    cmd = h.hermes_argv("put the board up", hermes="/bin/hermes")
    assert cmd[0] == "/bin/hermes"
    assert cmd[1:6] == ["-p", "zoey", "--cli", "--accept-hooks", "--yolo"]
    assert "chat" in cmd
    assert cmd.index("chat") < cmd.index("--oneshot")
    assert "-Q" in cmd and "-q" in cmd
    q = cmd[cmd.index("-q") + 1]
    assert "put the board up" in q
    assert "team_display" in q
    assert "Never browser_*" in q
    assert "no terminal" in q.lower()
    assert "first line is the tally" in q
    assert "window in words" in q
    assert "--max-turns" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "6"   # crew/config/zoey-hands.json (10-03); 4 before
    assert cmd[cmd.index("--run-budget") + 1] == "55"
    assert "delegate_task" not in q


def test_child_env_marks_nested_and_drops_voice_skill_pin():
    env = h.child_env({"HERMES_TUI_SKILLS": "none", "PATH": "/bin"})
    assert env["KREW_HANDS_NESTED"] == "1"
    assert "HERMES_TUI_SKILLS" not in env
    assert env["PATH"] == "/bin"


def test_hands_never_auto_load_a_context_file_on_a_read():
    """09-30: one read under the hermes tree pulled its AGENTS.md into the result (+29,067 chars)."""
    assert h.child_env({"PATH": "/bin"})["HERMES_SUBDIRECTORY_HINTS"] == "0"
    back = h.child_env({"PATH": "/bin", "KREW_HANDS_SUBDIR_HINTS": "1"})
    assert "HERMES_SUBDIRECTORY_HINTS" not in back, "the kill switch leaves hermes's own default"


def test_refuse_empty_nested_and_parked_occupancy():
    assert h.refuse_reason("").startswith("hands: empty")
    assert "CLI run" in (h.refuse_reason("click that", nested="1") or "")
    assert "parked" in (h.refuse_reason("search it", mode="render") or "").lower()
    assert "Beast" in (h.refuse_reason("search it", mode="beast") or "")
    assert h.refuse_reason("search it", nested="", mode="normal") is None


def test_main_empty_is_2(capsys):
    assert h.main(["   "]) == 2
    assert "empty" in capsys.readouterr().out


def test_hands_are_taught_to_land_a_ruling():
    """2026-09-17: Zoey ruled t_7e0d596d and hands ran `team_board list` — the prompt taught
    only reading, so her answer came back as a tally and no ruling landed all day."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "zoey_hands_ruling", Path(__file__).resolve().parents[1] / "crew" / "scripts" / "zoey-hands.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    q = m.prompt_for("answer t_7e0d596d: try — run the sample generation")
    ruling = q[q.index("Ruling a card"):q.index("Crissa's list")]
    assert "action answer" in ruling and "Landed" in ruling and "her words exactly" in ruling
    assert "A list is never the answer to a ruling" in ruling
    assert q.rstrip().endswith("Job: answer t_7e0d596d: try — run the sample generation")


def _load():
    spec = importlib.util.spec_from_file_location("zoey_hands_display", ROOT / "crew" / "scripts" / "zoey-hands.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_display_job_gets_the_glass_tools_alone():
    """The owner 2026-09-28: a re-run's helper reached for browser_* on "show me the board",
    put the wall on the wrong page and hit the step cap twice. A display job runs with the
    `team` toolset only (verified live: show_me, team_display, team_board ... no browser_*)."""
    zh = _load()
    for ask in ("show me the kanban board", "Show me the can ban", "put the board on the glass",
                "display the brief", "pull up the panel"):
        argv = zh.hermes_argv(ask, "hermes")
        assert argv[argv.index("chat") + 1:argv.index("chat") + 3] == ["-t", "team"], ask
        assert "show_me what=shot" in zh.prompt_for(ask), "one confirming screenshot"
    for ask in ("how many cards are open?", "answer t_1234abcd: try", "search the web for cosy"):
        argv = zh.hermes_argv(ask, "hermes")        # not a display job: the lean kit (armed 09-30), never `team` alone
        assert argv[argv.index("-t") + 1] == zh.LEAN_TOOLSETS, ask
        assert "display job" not in zh.prompt_for(ask)


def test_display_only_has_a_kill_switch(monkeypatch):
    monkeypatch.setenv("KREW_HANDS_DISPLAY_ONLY", "0")
    monkeypatch.setenv("KREW_HANDS_LEAN_DEFAULT", "0")
    off = _load()
    argv = off.hermes_argv("show me the board", "hermes")
    assert _wears_full_kit(argv, off), "display off + lean off = the whole CLI kit (10-01)"
    monkeypatch.setenv("KREW_HANDS_LEAN_DEFAULT", "1")
    argv = _load().hermes_argv("show me the board", "hermes")
    assert argv[argv.index("-t") + 1] != "team", "no display kit: it is dressed like any other job"


def test_a_run_that_stops_short_says_it_did_not_finish():
    """At the step cap hermes exits 1 with the helper's own summary; Zoey read it as done and
    promised a follow-up. The verdict goes LAST (the plugin keeps the last 900 chars)."""
    zh = _load()
    line = zh.failed_line("I'll get back to you the moment I have a real shot. " * 40, 1)
    assert line.endswith(zh.FAILED) and len(line) < 900
    assert "did not work" in zh.FAILED and "promise" in zh.FAILED


# --- the kit is chosen at spawn (2026-09-30) ---------------------------------------------------
def test_the_lean_kit_is_armed_and_has_a_way_back(monkeypatch):
    """Armed 2026-09-30 on the owner's word; KREW_HANDS_LEAN_DEFAULT=0 is the whole CLI kit again."""
    monkeypatch.delenv("KREW_HANDS_LEAN_DEFAULT", raising=False)
    zh = _load()
    cmd = zh.hermes_argv("how many cards are blocked", "hermes")
    assert cmd[cmd.index("-t") + 1] == "team,file,skill_read,web"
    assert zh.kit_for("put the board up") == "team", "the 09-28 display kit is untouched"
    monkeypatch.setenv("KREW_HANDS_LEAN_DEFAULT", "0")
    off = _load()
    assert off.kit_for("how many cards are blocked") is None
    argv = off.hermes_argv("how many cards are blocked", "hermes")
    assert _wears_full_kit(argv, off), "the whole CLI kit (10-01)"


def test_the_lean_kit_has_a_card_read_path():
    """team_board show is the read; it lives in `team`, and the prompt names it."""
    zh = _load()
    ask = "Show me the full thread of kanban card t_46af6491"
    assert "team" in zh.kit_for(ask, lean=True).split(",")
    assert "action show, id the card" in zh.prompt_for(ask)
    # (the reference test also grepped team-zoey's source for _BOARD_READ_WORDS; that plugin file is another
    # session's here and is pinned by tests/test_team_zoey.py)


def test_a_job_that_names_a_heavy_tool_keeps_the_full_kit():
    zh = _load()
    for ask in ("click the save button in the native window", "take a screenshot of the desktop",
                "recall from memory: what did he say about the wall", "who is here right now",
                "check presence", "browse the vendor website for the order", "what is in that photo"):
        assert zh.kit_for(ask, lean=True) is None, ask
    for ask in ("how many cards are blocked", "answer t_7e0d596d: try", "read the newest steward log",
                "do a web search for the release notes"):
        assert zh.kit_for(ask, lean=True) == zh.LEAN_TOOLSETS, ask


def test_a_card_read_is_not_a_display_job():
    """28 of 32 "display" jobs since 09-28 were "show me the thread of card t_..." - a read."""
    zh = _load()
    ask = "Show me the full thread of kanban card t_46af6491"
    assert zh.is_display_ask(ask) and zh.is_card_read(ask)
    assert zh.DISPLAY_STEPS not in zh.prompt_for(ask), "no 'one show_me, then stop' for a read"
    assert zh.kit_for(ask, lean=False) == "team", "unarmed it kept the team kit"
    assert zh.kit_for(ask) == zh.LEAN_TOOLSETS, "armed: a read wears the lean kit (team_board show is in it)"
    assert zh.DISPLAY_STEPS in zh.prompt_for("put the board up")


def test_every_run_notes_the_kit_it_would_wear(tmp_path, monkeypatch):
    import json
    zh = _load()
    monkeypatch.setenv("KREW_HANDS_LEAN_DEFAULT", "0")
    log = tmp_path / "logs" / "hands-kit.jsonl"
    zh.note_kit("how many cards are blocked", log)
    zh.note_kit("put the board up", log)
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert rows[0]["worn"] == "full" and rows[0]["lean_would"] == "team,file,skill_read,web" and rows[0]["armed"] is False
    assert rows[1]["worn"] == "team" and rows[1]["lean_would"] == "team"
    zh.note_kit("x", tmp_path / "logs" / "hands-kit.jsonl" / "not-a-dir")     # an unwritable log never raises


# dropped: test_the_backtest_counts_a_run_that_called_a_tool_outside_its_kit (scripts/hands-kit-backtest.py is a
# measurement tool of the reference install; krew does not ship it)


def test_filing_checking_and_fixing_are_not_display_jobs():
    """09-30 09:17: a card-create ask mentioning "the wall display" was given the display steps."""
    zh = _load()
    for ask in ("Create a kanban card, triaged, assignee ada, for this: the frame isn't landing on the :8099 wall display",
                "Check the numpy build in the display/wall environment and see what's broken, then fix it"):
        assert zh.is_display_ask(ask) and zh.is_card_read(ask), ask
        assert zh.DISPLAY_STEPS not in zh.prompt_for(ask), ask
    for ask in ("Put up the kanban board on the wall glass and keep it up", "show me the dashboard"):
        assert zh.DISPLAY_STEPS in zh.prompt_for(ask), ask


def _wears_full_kit(argv, zh) -> bool:
    """The full kit: no -t when her config blocks a shell and a code runner (10-01 block list), else the named list."""
    kit = zh.full_kit()
    return ("-t" not in argv) if kit is None else argv[argv.index("-t") + 1] == kit


def test_the_full_kit_is_a_block_list_with_the_allow_list_as_fallback(monkeypatch, tmp_path):
    """10-01 second ruling: every tool but terminal and code_execution. No -t ONLY while her config blocks both;
    a config that lost the line, or cannot be read, falls back to the named allow list (never a shell)."""
    monkeypatch.delenv("KREW_HANDS_FULL_EXPLICIT", raising=False)
    monkeypatch.delenv("KREW_HANDS_BLOCKLIST", raising=False)
    zh = _load()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("agent:\n  disabled_toolsets: [terminal, code_execution]\nplatform_toolsets:\n  cli: [file, team]\n")
    assert zh.full_kit(cfg) is None, "blocked in the config: no -t, every other toolset"
    cfg.write_text("agent:\n  disabled_toolsets: [terminal]\nplatform_toolsets:\n  cli: [file, team, terminal]\n")
    assert zh.full_kit(cfg) == "file,team", "code_execution not blocked: the named list"
    cfg.write_text("platform_toolsets:\n  cli: [file, team]\n")
    assert zh.full_kit(cfg) == "file,team", "no block list: the named list"
    assert zh.full_kit(tmp_path / "missing.yaml") == zh.FULL_TOOLSETS_FALLBACK
    cfg.write_text("agent:\n  disabled_toolsets: [terminal, code_execution]\nplatform_toolsets:\n  cli: [file, team]\n")
    monkeypatch.setenv("KREW_HANDS_BLOCKLIST", "0")
    assert _load().full_kit(cfg) == "file,team", "the kill switch keeps the allow list"


def test_the_full_kit_is_named_from_her_config_never_implied(monkeypatch, tmp_path):
    """10-01: no -t made hermes load EVERY toolset (terminal, execute_code, delegation, cron, process)."""
    monkeypatch.delenv("KREW_HANDS_FULL_EXPLICIT", raising=False)
    zh = _load()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("platform_toolsets:\n  cli: [file, team, terminal, code_execution, hands, computer_use, skills]\n")
    assert zh.full_kit(cfg) == "file,team,computer_use,skills", "terminal, code and hands are never in a hands kit"
    assert zh.full_kit(tmp_path / "missing.yaml") == zh.FULL_TOOLSETS_FALLBACK
    for banned in ("terminal", "code_execution", "delegation", "cronjob", "process", "hands"):
        assert banned not in zh.FULL_TOOLSETS_FALLBACK.split(",")
    argv = zh.hermes_argv("take a screenshot of the desktop", "hermes")
    assert zh.kit_for("take a screenshot of the desktop") is None and _wears_full_kit(argv, zh)
    monkeypatch.setenv("KREW_HANDS_FULL_EXPLICIT", "0")
    assert "-t" not in _load().hermes_argv("take a screenshot of the desktop", "hermes"), "the kill switch"


def test_thinking_marker_is_off_by_default_and_per_job(monkeypatch):
    monkeypatch.delenv("KREW_HANDS_THINK", raising=False)
    assert not h.prompt_for("open example.com").startswith("<|think")
    monkeypatch.setenv("KREW_HANDS_THINK", "on")
    assert h.prompt_for("what time is it").startswith("<|think_on|>")
    monkeypatch.setenv("KREW_HANDS_THINK", "auto")
    assert h.prompt_for("On your own screen, open python.org and tell me the version").startswith("<|think_low|>")
    assert not h.prompt_for("How many cards are open?").startswith("<|think")


def test_the_helper_never_speaks_and_shows_only_when_asked():
    q = h.prompt_for("How many cards are open?")
    assert "Never speak: no team_speak or team_announce" in q
    assert "unless the job asks to show or put something up" in q


def test_screen_errands_stay_on_her_screen_and_lookups_stay_direct(monkeypatch):
    monkeypatch.setenv("KREW_HANDS_SCREEN_ERRAND", "1")
    q = h.prompt_for("On your own screen, open en.wikipedia.org and tell me the year")
    assert "This job asks for YOUR SCREEN" in q and "Do not fetch it with web_extract" in q
    assert "YOUR SCREEN" not in h.prompt_for("Search the web for the latest Python release")
    monkeypatch.setenv("KREW_HANDS_SCREEN_ERRAND", "0")
    assert "YOUR SCREEN" not in h.prompt_for("On your own screen, open example.com")


def test_parallel_board_reads_reach_the_child(monkeypatch):
    monkeypatch.delenv("KREW_PARALLEL_BOARD_READS", raising=False)
    assert h.child_env({}, kit="team").get("KREW_PARALLEL_BOARD_READS") == "1"        # crew/config/zoey-hands.json
    monkeypatch.setenv("KREW_PARALLEL_BOARD_READS", "0")
    assert h.child_env({"KREW_PARALLEL_BOARD_READS": "0"}, kit="team").get("KREW_PARALLEL_BOARD_READS") == "0"
