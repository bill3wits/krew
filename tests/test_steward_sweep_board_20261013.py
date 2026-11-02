"""steward-sweep's marker dir is the board kanban/current names, and its card body runs no backticks (2026-10-13)."""
import re
import subprocess
from pathlib import Path

SWEEP = Path(__file__).resolve().parents[1] / "crew" / "scripts" / "steward-sweep"


def _marker_dir(home: Path, board_env: str | None = None) -> str:
    src = SWEEP.read_text()
    start = src.index('SWEEP_BOARD="${HERMES_KANBAN_BOARD:-}"')
    end = src.index("\n", src.index('MARKER_DIR="$KREW_HERMES_HOME/kanban/boards/$SWEEP_BOARD/sweep-markers"'))
    helper = SWEEP.parents[1] / "lib" / "krew_hermes.sh"           # the sweep sources it: where Hermes lives
    snippet = f'. "{helper}"\n' + src[start:end] + '\nprintf %s "$MARKER_DIR"\n'
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    if board_env is not None:
        env["HERMES_KANBAN_BOARD"] = board_env
    return subprocess.run(["bash", "-c", snippet], env=env, capture_output=True, text=True, check=True).stdout


def test_the_pointer_names_the_board(tmp_path):
    (tmp_path / ".hermes" / "kanban").mkdir(parents=True)
    (tmp_path / ".hermes" / "kanban" / "current").write_text("daily-2026-09-16\n")
    assert _marker_dir(tmp_path).endswith("/.hermes/kanban/boards/daily-2026-09-16/sweep-markers")


def test_no_pointer_is_default_never_current(tmp_path):
    assert _marker_dir(tmp_path).endswith("/.hermes/kanban/boards/default/sweep-markers")


def test_the_env_still_wins(tmp_path):
    (tmp_path / ".hermes" / "kanban").mkdir(parents=True)
    (tmp_path / ".hermes" / "kanban" / "current").write_text("other")
    assert _marker_dir(tmp_path, "mine").endswith("/boards/mine/sweep-markers")


def test_a_bad_pointer_is_default(tmp_path):
    (tmp_path / ".hermes" / "kanban").mkdir(parents=True)
    (tmp_path / ".hermes" / "kanban" / "current").write_text("../etc")
    assert _marker_dir(tmp_path).endswith("/boards/default/sweep-markers")


def test_the_card_body_runs_no_backticks():
    src = SWEEP.read_text()
    for m in re.finditer(r'^BODY="(.*?)"$', src, flags=re.S | re.M):
        assert "`" not in m.group(1), "a backtick inside a double-quoted BODY is run as a command"
