"""A board write is signed by whoever makes it (the User 2026-10-11: "kanban sign in as themselves").

team_board signed every create, comment and answer as "crissa", and every crew profile loads team-crissa, so any
worker could post a ruling as Crissa. Now: crissa signs crissa, the User's default profile signs user, any other
profile signs its own name; only Crissa and the User land his words (answer). Kill: KREW_BOARD_SIGN_AS_SELF=0."""
import sys
import types
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "team-crissa" / "__init__.py"
RULERS = {"crissa", "user", "human", "dashboard"}   # krew's ruler set (crew/lib/kanban_liveness.RULERS default)


def _plugin():
    loader = SourceFileLoader("team_crissa_plugin_sign", str(PLUGIN))
    spec = spec_from_loader(loader.name, loader)
    m = module_from_spec(spec)
    loader.exec_module(m)
    return m


@pytest.fixture
def env(monkeypatch):
    for k in ("HERMES_PROFILE", "HERMES_PROFILE_NAME", "KREW_BOARD_SIGN_AS_SELF"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(sys.modules, "hermes_cli", None)      # no hermes here: the env pin is the fallback
    return monkeypatch


@pytest.mark.parametrize("profile,author", [("crissa", "crissa"), ("default", "user"), ("ada", "ada"),
                                            ("bash", "bash"), ("lila", "lila"), ("steward", "steward"),
                                            ("vera", "vera"), ("custom", "custom")])
def test_each_profile_signs_as_itself(env, profile, author):
    m = _plugin()
    env.setenv("HERMES_PROFILE", profile)
    assert m._board_author() == author


def test_no_crew_profile_signs_as_a_ruler(env):
    m = _plugin()
    for p in ("ada", "bash", "lila", "steward", "vera", "judge", "scout", "custom", ""):
        env.setenv("HERMES_PROFILE", p)
        assert m._board_author() not in RULERS, p
        assert not m._board_may_rule(), p


def test_nothing_named_never_signs_crissa(env):
    m = _plugin()
    assert m._board_author() == "unknown"
    assert not m._board_may_rule()


def test_the_call_time_profile_wins_over_the_launch_pin(env):
    # the host gateway's os.environ is the LAUNCH profile's; a routed turn binds its own (hermes current_profile_name)
    m = _plugin()
    env.setenv("HERMES_PROFILE", "crissa")
    pkg = types.ModuleType("hermes_cli")
    prof = types.ModuleType("hermes_cli.profiles")
    prof.current_profile_name = lambda default=None: "bash"
    pkg.profiles = prof
    env.setitem(sys.modules, "hermes_cli", pkg)
    env.setitem(sys.modules, "hermes_cli.profiles", prof)
    assert m._board_author() == "bash" and not m._board_may_rule()


def test_ctx_profile_is_the_second_source(env):
    m = _plugin()
    ctx = types.SimpleNamespace(profile_name="lila")
    assert m._board_author(ctx) == "lila"


def test_kill_switch_restores_the_old_signature(env):
    m = _plugin()
    env.setenv("HERMES_PROFILE", "ada")
    env.setenv("KREW_BOARD_SIGN_AS_SELF", "0")
    assert m._board_author() == "crissa" and m._board_may_rule()


def test_the_rulers_may_land_his_words(env):
    m = _plugin()
    for p in ("crissa", "default"):
        env.setenv("HERMES_PROFILE", p)
        assert m._board_may_rule(), p


def test_no_board_write_carries_a_fixed_crissa_signature():
    src = PLUGIN.read_text()
    assert '"--author", "crissa"' not in src
    assert '"--created-by", "crissa"' not in src


def test_answer_asks_who_before_it_writes():
    src = PLUGIN.read_text()
    a = src.index('if action == "answer":')
    guard = src.index("_board_may_rule(ctx)", a)
    first_write = src.index("_kanban_cli(", a)
    assert guard < first_write
