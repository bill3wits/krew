"""krew: lifted from the reference install (tests/test_crissa_her_screen.py, 2026-10-10), paths and switches renamed.

Her private screen must be CHECKED isolated before computer_use comes back (the User 2026-10-02)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import crissa_her_screen as zs  # noqa: E402


@pytest.fixture(autouse=True)
def _no_live_proc_scan(monkeypatch):
    """check() scans /proc for her processes; the tests here never read the live machine's table."""
    monkeypatch.setattr(zs, "her_processes", lambda disp, proc=None: [])


@pytest.fixture(autouse=True)
def _her_chromium(tmp_path, monkeypatch):
    """krew: her screen browser is found under the hermes home's tools/; a test run's scratch HOME has none, so the
    tests carry their own (the reference tests read the live machine's ~/.hermes/tools)."""
    exe = tmp_path / "tools" / "chromium-1208" / "chrome-linux64" / "chrome"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(zs, "TOOLS", tmp_path / "tools")


def fake(windows: dict, pids: dict, *, pointer_moves=False, browser_lines=()):
    state = {"ptr": "x:500 y:300 screen:0 window:77"}

    def run(argv, env=None, timeout=8):
        if argv[0] == "xdotool":
            disp = (env or {}).get("DISPLAY")
            if argv[1] == "getmouselocation":
                return 0, state["ptr"]
            if argv[1] == "getactivewindow":
                return 0, "77"
            if argv[1] == "mousemove":
                if pointer_moves or disp in (":0", ":1"):
                    state["ptr"] = "x:37 y:41 screen:0 window:77"
                return 0, ""
        if argv[0] == "pgrep" and "--user-data-dir" in argv[-1]:
            return (0, "\n".join(browser_lines)) if browser_lines else (1, "")
        if argv[0] == "xwininfo":
            d = argv[argv.index("-display") + 1]
            if d not in windows:
                return 1, ""
            return 0, "\n".join(f'     0x{i:x} "{n}": ()' for i, n in enumerate(windows[d], 1))
        if argv[0] == "pgrep":
            for d, pid in pids.items():
                if f":{d.lstrip(':')}" in argv[-1]:
                    return (0, f"{pid} /usr/bin/X {d}") if pid else (1, "")
            return 1, ""
        return 1, ""
    return run


def env(tmp_path, disp=":21", auth="/home/z/.hermes/profiles/crissa/bot-desktop/Xauthority"):
    p = tmp_path / "env"
    p.write_text(f"DISPLAY={disp}\nXAUTHORITY={auth}\n")
    return p


def test_isolated_screen_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    run = fake({":0": ["Brave", "Terminal"], ":21": ["Chromium", "xfce4-panel"]}, {":0": 100, ":21": 200})
    rec = zs.check(env_file=env(tmp_path), user_display=":0", run=run, landing=False)
    assert rec["ok"], rec


def test_his_display_or_his_windows_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    run = fake({":0": ["Brave", "Terminal"]}, {":0": 100})
    assert not zs.check(env_file=env(tmp_path, ":0"), user_display=":0", run=run, landing=False)["ok"]
    run = fake({":0": ["Brave", "Terminal"], ":21": ["Brave"]}, {":0": 100, ":21": 200})
    rec = zs.check(env_file=env(tmp_path), user_display=":0", run=run, landing=False)
    assert not rec["ok"] and any("shared: Brave" in c["why"] for c in rec["checks"])


def test_same_server_or_his_xauthority_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    run = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 100})
    assert not zs.check(env_file=env(tmp_path), user_display=":0", run=run, landing=False)["ok"]
    run = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200})
    assert not zs.check(env_file=env(tmp_path, auth="/run/user/1000/gdm/Xauthority"), user_display=":0", run=run, landing=False)["ok"]


def test_record_counts_only_for_the_display_she_has_now(tmp_path):
    rec = tmp_path / "rec.json"
    zs.write_record({"ok": True, "display": ":21", "checks": []}, rec)
    assert zs.isolated(path=rec, env_file=env(tmp_path, ":21"))
    assert not zs.isolated(path=rec, env_file=env(tmp_path, ":22"))
    zs.write_record({"ok": False, "display": ":21", "checks": []}, rec)
    assert not zs.isolated(path=rec, env_file=env(tmp_path, ":21"))
    assert not zs.isolated(path=tmp_path / "none.json", env_file=env(tmp_path, ":21"))


def test_no_screen_today_means_no_cua_tonight():
    """Today there is no Bot Desktop: the hands guard keeps computer_use out and the drill skips CUA."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("zh_screen", ROOT / "crew" / "scripts" / "crissa-hands.py")
    h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
    if not h.BOT_DESKTOP_ENV.exists():
        assert h.private_screen_ready() is False


def test_a_click_that_moves_his_pointer_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    run = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200}, pointer_moves=True)
    rec = zs.check(env_file=env(tmp_path), user_display=":0", run=run, landing=False)
    assert not rec["ok"] and any(c["check"] == "his pointer did not move" and not c["ok"] for c in rec["checks"])


def test_her_browser_on_cdp_9222_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    run = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200},
               browser_lines=["900 chrome --user-data-dir=/x --remote-debugging-port=9222"])
    assert not zs.check(env_file=env(tmp_path), user_display=":0", run=run, landing=False)["ok"]


def test_her_browser_is_never_a_brave_and_lives_in_her_folder(tmp_path):
    (tmp_path / "chromium-1208" / "chrome-linux64").mkdir(parents=True)
    (tmp_path / "chromium-1208" / "chrome-linux64" / "chrome").write_text("")
    (tmp_path / "chromium-1300" / "chrome-linux64").mkdir(parents=True)
    (tmp_path / "chromium-1300" / "chrome-linux64" / "chrome").write_text("")
    assert zs.screen_browser(tmp_path).endswith("chromium-1300/chrome-linux64/chrome")
    prof = str(zs.browser_profile())
    assert "/profiles/crissa/" in prof and "BraveSoftware" not in prof and "/.hermes/display" not in prof


def test_no_xdotool_is_a_fail_not_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
    base = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200})

    def run(argv, env=None, timeout=8):
        if argv[0] == "xdotool":
            return 127, "[Errno 2] No such file or directory: 'xdotool'"
        return base(argv, env, timeout)
    rec = zs.check(env_file=env(tmp_path), user_display=":0", run=run, landing=False)
    bad = {c["check"] for c in rec["checks"] if not c["ok"]}
    assert not rec["ok"] and "his focused window did not change" in bad and "his pointer did not move" in bad


def test_landing_on_hers_passes_on_his_fails(tmp_path):
    class P:
        pid = -1
        def kill(self): pass
    checks = []
    add = lambda n, ok, why="": checks.append((n, ok))
    seen = {}

    def launch(argv, env):
        assert env["XDG_SESSION_TYPE"] == "x11" and "WAYLAND_DISPLAY" not in env
        assert env["XDG_RUNTIME_DIR"].endswith("/runtime")
        seen["marker"] = argv[-1].split("<title>")[1].split("<")[0]
        return P()

    def run_hers(argv, env=None, timeout=8):
        d = argv[argv.index("-display") + 1]
        return 0, (f'0x1 "{seen["marker"]}"' if d == ":21" else "")
    e = env(tmp_path)
    zs.landing_check(add, ":21", "", ":0", env_file=e, launch=launch, run=run_hers, wait_s=2)
    assert dict(checks) == {"her browser lands on her screen": True, "her browser never lands on his": True}
    checks.clear()

    def run_his(argv, env=None, timeout=8):
        d = argv[argv.index("-display") + 1]
        return 0, (f'0x1 "{seen["marker"]}"' if d == ":0" else "")
    zs.landing_check(add, ":21", "", ":0", env_file=e, launch=launch, run=run_his, wait_s=2)
    assert dict(checks) == {"her browser lands on her screen": False, "her browser never lands on his": False}


# ── the boot race (the User 2026-10-04): his display unreadable until his session is up ─────────────────────

def boot_run(ready_after: int):
    """His display answers only from the `ready_after`-th manager read on, and only with the auth it publishes."""
    calls = {"env": 0}

    def run(argv, env=None, timeout=8):
        if argv[:3] == ["systemctl", "--user", "show-environment"]:
            calls["env"] += 1
            if calls["env"] < ready_after:
                return 0, "HOME=/h\nPATH=/bin\n"
            return 0, "DISPLAY=:0\nXAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.T\nPATH=/bin\n"
        auth = (env or {}).get("XAUTHORITY", "")
        if not auth.endswith("Xwaylandauth.T"):
            return 1, "Can't open display"
        if argv[0] == "xdotool":
            return 0, "x:1 y:2 screen:0 window:3" if argv[1] == "getmouselocation" else "3"
        if argv[0] == "xwininfo":
            return 0, '     0x1 "Terminal": ()'
        return 1, ""
    return run, calls


def clock_of(t):
    return lambda: t[0]


def test_wait_is_off_by_default():
    assert zs.wait_limit({}) == 0.0
    assert zs.wait_limit({"KREW_CRISSA_SCREEN_WAIT_S": "0"}) == 0.0
    assert zs.wait_limit({"KREW_CRISSA_SCREEN_WAIT_S": "junk"}) == 0.0
    assert zs.wait_limit({"KREW_CRISSA_SCREEN_WAIT_S": "120"}) == 120.0


def test_wait_rereads_the_manager_env_until_his_display_answers(monkeypatch):
    monkeypatch.delenv("XAUTHORITY", raising=False)
    run, calls = boot_run(ready_after=4)
    t = [0.0]
    w = zs.wait_for_his_display(120, run=run, clock=clock_of(t), sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert w["ok"] and w["tries"] == 4 and calls["env"] == 4
    assert w["display"] == ":0" and w["xauthority"].endswith("Xwaylandauth.T")
    assert w["waited_s"] == 6.0


def _unreadable_his(extra=()):
    checks = [
        {"check": "her env names a display", "ok": True, "why": ":20"},
        {"check": "his pointer did not move", "ok": False, "why": "his pointer unreadable"},
        {"check": "his focused window did not change", "ok": False, "why": "his focused window unreadable"},
        *extra,
    ]
    return {"ok": False, "display": ":20", "ts": 1, "checks": checks}


def test_his_silence_after_the_wait_leaves_her_screen_up():
    waited = {"ok": False, "display": ":0", "xauthority": "", "waited_s": 118.3, "tries": 60}
    assert zs.screen_stays_up(_unreadable_his(), waited)
    assert not zs.screen_stays_up(_unreadable_his(), {**waited, "ok": True})
    assert not zs.screen_stays_up(_unreadable_his(), None)
    leaked = _unreadable_his(({"check": "an X server answers on her display", "ok": False, "why": "xwininfo failed"},))
    assert not zs.screen_stays_up(leaked, waited)
    assert not zs.screen_stays_up({"ok": True, "checks": []}, waited)
    assert not zs.screen_stays_up(_unreadable_his(), waited, {"KREW_CRISSA_SCREEN_DEFER": "0"})


def test_the_check_exits_zero_when_his_display_never_answers(monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "crissa_her_screen_check_defer", ROOT / "crew" / "scripts" / "crissa-screen-check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    saved = {}
    monkeypatch.setattr(zs, "wait_limit", lambda env=None: 120.0)
    monkeypatch.setattr(zs, "wait_for_his_display",
                        lambda *a, **k: {"ok": False, "display": ":0", "xauthority": "",
                                         "waited_s": 118.3, "tries": 60})
    monkeypatch.setattr(zs, "check", lambda **k: _unreadable_his())
    monkeypatch.setattr(zs, "write_record", lambda rec, path=None: saved.update(rec=rec) or Path("rec"))
    assert mod.main() == 0
    assert saved["rec"]["ok"] is False
    assert saved["rec"]["deferred"] == "his display unreadable after the wait; her screen stays up"
    assert "DEFERRED" in capsys.readouterr().out
    monkeypatch.setattr(zs, "check", lambda **k: _unreadable_his(
        ({"check": "an X server answers on her display", "ok": False, "why": "xwininfo failed"},)))
    assert mod.main() == 1
    monkeypatch.setenv("KREW_CRISSA_SCREEN_DEFER", "0")
    monkeypatch.setattr(zs, "check", lambda **k: _unreadable_his())
    assert mod.main() == 1


def test_wait_gives_up_at_the_limit_and_says_not_ok(monkeypatch):
    monkeypatch.delenv("XAUTHORITY", raising=False)
    run, _ = boot_run(ready_after=10**6)
    t = [0.0]
    w = zs.wait_for_his_display(10, run=run, clock=clock_of(t), sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert not w["ok"] and w["waited_s"] <= 10 and w["tries"] == 6


def test_his_display_readable_needs_all_three_readings():
    def run(argv, env=None, timeout=8):
        if argv[0] == "xdotool" and argv[1] == "getactivewindow":
            return 1, ""          # focus unreadable alone is enough to keep waiting
        return 0, '     0x1 "Terminal": ()' if argv[0] == "xwininfo" else "x"
    assert not zs.his_display_readable(":0", "", run)


# 10-05 01:22: his own pointer moved during the boot check (x:1920 y:960 -> x:1932 y:942) and the gate failed with
# nothing wrong on her screen. His motion is retried on a new spot; her spot on his screen never is.
def _moving_run(base, moves):
    """His pointer reads from `moves` in turn (his hand / his session settling), whatever is sent to her screen."""
    seq = list(moves)

    def run(argv, env=None, timeout=8):
        if argv[:2] == ["xdotool", "getmouselocation"] and (env or {}).get("DISPLAY") == ":0":
            return 0, (seq.pop(0) if len(seq) > 1 else seq[0])
        return base(argv, env, timeout)
    return run


def _focus(run, tries=5):
    out = []
    zs.focus_checks(lambda n, ok, why="": out.append((n, ok, why)), ":21", "/x/Xauthority", ":0", run=run,
                    sleep=lambda s: None, tries=tries)
    return {n: (ok, why) for n, ok, why in out}


def test_his_own_pointer_motion_is_retried_then_passes():
    base = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200})
    a, b, c = ("x:1920 y:960 screen:0 window:7", "x:1932 y:942 screen:0 window:7", "x:1940 y:930 screen:0 window:7")
    # try 1: still across the quiet gap, then moves during her click; try 2: still before and after
    run = _moving_run(base, [a, a, b, c, c, c])
    res = _focus(run)
    assert res["his pointer did not move"][0] is True, res
    assert "retried" in res["his pointer did not move"][1]


def test_a_pointer_that_never_holds_still_fails_closed():
    base = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200})
    run = _moving_run(base, [f"x:{1900 + i} y:900 screen:0 window:7" for i in range(40)])
    assert _focus(run)["his pointer did not move"][0] is False


def test_her_spot_on_his_screen_is_a_leak_and_never_retried():
    calls = []
    base = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200}, pointer_moves=True)

    def run(argv, env=None, timeout=8):
        if argv[:2] == ["xdotool", "mousemove"]:
            calls.append(argv)
        return base(argv, env, timeout)
    res = _focus(run)
    assert res["his pointer did not move"][0] is False and "HER SPOT" in res["his pointer did not move"][1]
    assert len(calls) == 1


def test_a_focus_change_with_his_pointer_still_fails_closed():
    calls, wins = [], iter(["77", "77", "78"])  # before, quiet gap, after her click
    base = fake({":0": ["Brave"], ":21": ["Chromium"]}, {":0": 100, ":21": 200})

    def run(argv, env=None, timeout=8):
        if argv[:2] == ["xdotool", "getactivewindow"]:
            return 0, next(wins, "78")
        if argv[:2] == ["xdotool", "mousemove"]:
            calls.append(argv)
        return base(argv, env, timeout)
    res = _focus(run)
    assert res["his focused window did not change"][0] is False and len(calls) == 1


# ---- krew: the hands child's screen pin imports the shipped helper (2026-10-10) ----------------------------------

def test_the_hands_pin_imports_crissa_her_screen_from_its_own_files(tmp_path, monkeypatch):
    """crew/scripts/crissa-hands.py `pin_screen` imports crissa_her_screen from ../lib; in a tree of only the two shipped
    files (and the hands config), a computer_use kit is re-pointed at her screen."""
    import importlib.util
    import shutil
    tree = tmp_path / "crew"
    for rel in ("scripts/crissa-hands.py", "lib/crissa_her_screen.py", "config/crissa-hands.json"):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "crew" / rel, tree / rel)
    monkeypatch.setenv("KREW_HERMES", str(tmp_path / "hermes"))
    for k in [k for k in sys.modules if k == "crissa_her_screen"]:
        monkeypatch.delitem(sys.modules, k)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if not p.endswith("/crew/lib")])
    spec = importlib.util.spec_from_file_location("zh_pin_tree", tree / "scripts" / "crissa-hands.py")
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    e = env(tmp_path, ":21", str(tmp_path / "Xauthority"))
    out = h.pin_screen({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, "terminal,computer_use", env_file=e)
    assert out["DISPLAY"] == ":21" and "WAYLAND_DISPLAY" not in out and out["XDG_SESSION_TYPE"] == "x11"
    assert sys.modules["crissa_her_screen"].__file__ == str(tree / "lib" / "crissa_her_screen.py")
    assert h.pin_screen({"DISPLAY": ":0"}, "terminal", env_file=e) == {"DISPLAY": ":0"}   # no CUA: untouched
