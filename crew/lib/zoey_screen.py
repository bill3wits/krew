"""Is Zoey's private screen really hers alone? (the User 2026-10-02)
krew: copied from the reference install (lib/zoey_screen.py, 2026-10-10); seams: KREW_HERMES for its hermes home, KREW_ZOEY_SCREEN_* switches.

computer_use comes back to her hands - and the drill's CUA tasks run - only after her Bot Desktop is
CHECKED to be isolated from his desktop. A published env file is not proof: on 10-01 a foreground click
landed on display :0. The check writes one record, ~/.hermes/zoey-screen-check.json; the hands guard and
the drill read it.

Isolated means all of:
  * her env names a DISPLAY that is not :0/:1 and not the User's session DISPLAY;
  * an X server answers on it, and it is a different server process from the one serving :0;
  * none of the User's named windows on :0 appears on her display (window names compared);
  * her XAUTHORITY is her own file, not his session's.

    scripts/zoey-screen-check.py         # run the check, write the record
Kill: KREW_ZOEY_SCREEN_CHECK=0 (the guard then trusts the env file alone, as before 10-02 evening).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("KREW_HERMES", str(Path.home() / ".hermes")))
ENV_FILE = HERMES_HOME / "profiles" / "zoey" / "bot-desktop" / "env"
RECORD = HERMES_HOME / "zoey-screen-check.json"
USER_DISPLAYS = {":0", ":1"}


def check_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_ZOEY_SCREEN_CHECK", "1")).strip().lower() not in ("0", "false", "no", "off")


# HIS DISPLAY AT BOOT (the User 2026-10-04): the unit starts before his session's Xwayland :0 is up, and a unit's
# env is frozen at its start with no XAUTHORITY in it - so the 04:39:38 check read his pointer, focus and window list
# as unreadable and failed closed (NOT ISOLATED) with nothing wrong on her screen. With KREW_ZOEY_SCREEN_WAIT_S > 0
# the check first waits for his display to answer, re-reading the user manager's environment on every try (that is
# where his session publishes DISPLAY/XAUTHORITY once it is up). Past the limit, if the only failures are his
# pointer and focus still unreadable, the oneshot stays up and the record says deferred: isolation is not claimed
# (ok stays false) and her screen is not torn down. A real failure on her side still fails closed. Unset/0 wait
# is off. Kill the deferral: KREW_ZOEY_SCREEN_DEFER=0.
def wait_limit(env: dict | None = None) -> float:
    e = os.environ if env is None else env
    try:
        return max(0.0, float(str(e.get("KREW_ZOEY_SCREEN_WAIT_S", "0")).strip() or 0))
    except ValueError:
        return 0.0


def manager_display_env(run=None) -> dict:
    """DISPLAY and XAUTHORITY as the user manager has them NOW (not as this process was started with)."""
    rc, out = (run or _run)(["systemctl", "--user", "show-environment"])
    if rc != 0:
        return {}
    env = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k in ("DISPLAY", "XAUTHORITY") and v:
            env[k] = v
    return env


# A pre-login boot (2026-10-07 04:00): the 120 s wait ended at the greeter, his ubuntu session came up at 05:13,
# and the two unreadable readings failed the oneshot, so systemd stopped a screen that had already started.
# Those two lines alone, after a wait that did not succeed, leave her screen up. Anything else still fails.
_HIS_SILENT = {
    ("his pointer did not move", "his pointer unreadable"),
    ("his focused window did not change", "his focused window unreadable"),
}


def defer_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_ZOEY_SCREEN_DEFER", "1")).strip().lower() not in ("0", "false", "no", "off")


def screen_stays_up(rec: dict, waited: dict | None, env: dict | None = None) -> bool:
    """True when the wait ran out and his display still does not answer, and her own checks passed.

    The record stays ok false, so computer_use does not treat the screen as isolated. The oneshot
    exits 0 so systemd leaves her screen running. Kill: KREW_ZOEY_SCREEN_DEFER=0.
    """
    if not defer_on(env):
        return False
    if not waited or waited.get("ok") or rec.get("ok"):
        return False
    bad = [c for c in (rec.get("checks") or []) if not c.get("ok")]
    return bool(bad) and all((c.get("check"), c.get("why")) in _HIS_SILENT for c in bad)


def his_display_readable(display: str, xauth: str = "", run=None) -> bool:
    """Every reading the check takes of his display answers: pointer, focused window, window list."""
    run = run or _run
    e = {"DISPLAY": display, **({"XAUTHORITY": xauth} if xauth else {})}
    return (run(["xdotool", "getmouselocation"], e)[0] == 0
            and run(["xdotool", "getactivewindow"], e)[0] == 0
            and window_names(display, xauth, run) is not None)


def wait_for_his_display(limit: float, *, run=None, sleep=time.sleep, clock=time.monotonic, step: float = 2.0,
                         log=None) -> dict:
    """Wait up to `limit` s for his display to be readable. {"ok", "display", "xauthority", "waited_s", "tries"}."""
    t0, tries = clock(), 0
    while True:
        tries += 1
        env = manager_display_env(run)
        disp = env.get("DISPLAY") or os.environ.get("DISPLAY") or ":0"
        auth = env.get("XAUTHORITY") or os.environ.get("XAUTHORITY", "")
        ok = his_display_readable(disp, auth, run)
        waited = clock() - t0
        if ok or waited + step > limit:
            return {"ok": ok, "display": disp, "xauthority": auth, "waited_s": round(waited, 1), "tries": tries}
        if log:
            log(f"wait his display {disp} unreadable (auth {auth or '-'}), try {tries}, {waited:.0f}/{limit:.0f} s")
        sleep(step)


def read_env(path: Path | None = None) -> dict:
    out = {}
    try:
        for line in (path or ENV_FILE).read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z_]+)=['\"]?([^'\"]*)['\"]?\s*$", line)
            if m:
                out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def screen_env(base: dict, env_file: Path | None = None) -> dict:
    """`base` re-pointed at HER screen only. His Wayland must be UNREACHABLE, not merely unnamed (10-02 16:57: a
    test Chromium with DISPLAY=:20 and no WAYLAND_DISPLAY still opened on his desktop - XDG_SESSION_TYPE=wayland
    made it look for wayland-0 in XDG_RUNTIME_DIR and find his): her screen's own env, X11 session type, no
    Wayland variables, a private runtime dir."""
    ef = env_file or ENV_FILE
    env = dict(base)
    env.update(read_env(ef))
    for key in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
        env.pop(key, None)
    env["XDG_SESSION_TYPE"] = "x11"
    run_dir = ef.parent / "runtime"
    run_dir.mkdir(mode=0o700, exist_ok=True)
    env["XDG_RUNTIME_DIR"] = str(run_dir)
    exe = screen_browser()
    if exe:
        env["AGENT_BROWSER_EXECUTABLE_PATH"] = exe
    env["AGENT_BROWSER_PROFILE"] = str(browser_profile())
    return env


def _run(argv: list[str], env: dict | None = None, timeout: float = 8) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})})
        return p.returncode, p.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def window_names(display: str, xauth: str = "", run=_run) -> set[str] | None:
    rc, out = run(["xwininfo", "-root", "-tree", "-display", display], {"XAUTHORITY": xauth} if xauth else None)
    if rc != 0:
        return None
    names = set(re.findall(r'^\s+0x[0-9a-f]+ "([^"]+)"', out, re.M))
    return {n for n in names if n.strip()}


def server_pid(display: str, run=_run) -> str:
    num = display.lstrip(":").split(".")[0]
    rc, out = run(["pgrep", "-af", rf"(Xvnc|Xtigervnc|Xvfb|Xwayland|Xorg)\b.*(:{num}\b)"])
    return out.split()[0] if rc == 0 and out.strip() else ""


# HER BROWSER FOR CUA LIVES ON HER SCREEN, apart from his Brave and the ZOS Brave (the User 2026-10-02): hermes's own
# Chromium (never a Brave binary), its profile inside her profile folder, never a CDP port of 9222.
TOOLS = HERMES_HOME / "tools"
HER_HOME = HERMES_HOME / "profiles" / "zoey"
NOT_HERS = (Path.home() / ".config" / "BraveSoftware", HERMES_HOME / "display")


def screen_browser(tools: Path | None = None) -> str:
    """Newest hermes-managed Chromium (chromium-<build>/chrome-linux64/chrome), or ''."""
    found = sorted((tools or TOOLS).glob("chromium-*/chrome-linux64/chrome"),
                   key=lambda q: int(re.sub(r"\D", "", q.parent.parent.name) or 0))
    return str(found[-1]) if found else ""


def browser_profile() -> Path:
    return HER_HOME / "bot-desktop" / "browser-profile"


def browser_checks(add, *, exe: str, run=_run) -> None:
    add("her screen browser is hermes's Chromium, not a Brave", bool(exe) and "brave" not in exe.lower(), exe or "none found")
    prof = browser_profile()
    add("her browser profile is inside her folder", all(not str(prof).startswith(str(n)) for n in NOT_HERS)
        and str(prof).startswith(str(HER_HOME)), str(prof))
    rc, out = run(["pgrep", "-af", "--", f"--user-data-dir={prof}"])
    lines = [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []
    add("no CDP 9222 on her browser", not any("--remote-debugging-port=9222" in ln for ln in lines),
        f"{len(lines)} of her browser's processes running")
    add("no Brave on her profile", not any("brave" in ln.lower() for ln in lines), "")


# HER DESKTOP MOUNTS NOTHING ON HIS RUNTIME FOLDER (the User 2026-10-04): with his /run/user/<uid> inherited, her
# Xfce session's gvfsd-fuse and xdg-document-portal mounted gvfs/doc on top of his; systemd's SIGKILL of her failed
# start left both dead and his own document portal could not start. Her processes (every process whose environment
# names her DISPLAY) must carry her own XDG_RUNTIME_DIR, and none may name a path under his on its command line
# (gvfsd-fuse takes its mountpoint as argv). krew's Hermes patch 210 (patches/210-bot-desktop-private-runtime.patch) gives her the folder; this proves it.
def his_runtime_dir() -> str:
    return f"/run/user/{os.getuid()}"


def her_processes(disp: str, proc: Path = Path("/proc")) -> list[dict]:
    """[{pid, comm, argv, runtime}] for every readable process whose environment names DISPLAY=`disp`."""
    out, me = [], os.getpid()
    want = f"DISPLAY={disp}".encode()
    for d in proc.iterdir():
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            env = (d / "environ").read_bytes().split(b"\0")
            if want not in env:
                continue
            argv = [a.decode(errors="replace") for a in (d / "cmdline").read_bytes().split(b"\0") if a]
            comm = (d / "comm").read_text().strip()
        except OSError:
            continue
        rt = next((e[16:].decode(errors="replace") for e in env if e.startswith(b"XDG_RUNTIME_DIR=")), "")
        out.append({"pid": int(d.name), "comm": comm, "argv": argv, "runtime": rt})
    return out


def runtime_leaks(procs: list[dict], his_dir: str) -> list[str]:
    """Her processes that would mount (or did) under his runtime folder."""
    under = lambda p: p == his_dir or p.startswith(his_dir.rstrip("/") + "/")  # noqa: E731
    bad = []
    for p in procs:
        if under(p.get("runtime") or ""):
            bad.append(f"{p['comm']}[{p['pid']}] XDG_RUNTIME_DIR={p['runtime']}")
        elif any(under(a) for a in (p.get("argv") or [])[1:]):
            bad.append(f"{p['comm']}[{p['pid']}] argv names {next(a for a in p['argv'][1:] if under(a))}")
    return bad


# HIS OWN POINTER (10-05 01:22): the boot check ran 6 s after his password login and read his pointer at
# x:1920 y:960 then x:1932 y:942 across her click - his session settling / his hand, not her click (her move goes to
# x:37 y:41, and that is where a leak puts his pointer). One before/after pair cannot tell the two apart, so the
# probe now waits for his pointer and focus to hold still across a quiet gap, and a change that is not her spot is
# retried on a NEW spot (a retried leak would land on the new spot; a repeat click on the old one would not move it).
# Her spot on his screen is never retried. Past the tries it fails closed exactly as before.
PROBE_SPOTS = ((37, 41), (53, 29), (71, 63), (29, 77), (89, 47))


def _ptr_xy(reading: str) -> tuple[int, int] | None:
    m = re.search(r"x:(-?\d+)\s+y:(-?\d+)", reading or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def focus_checks(add, disp: str, xauth: str, mine: str, run=_run, *, sleep=time.sleep, quiet_s: float = 0.3,
                 settle_s: float = 2.0, tries: int = len(PROBE_SPOTS)) -> None:
    """Move and click on HER screen; his pointer and his focused window must not change."""
    his_env = {"DISPLAY": mine}
    her_env = {"DISPLAY": disp, "XAUTHORITY": xauth}
    read = lambda what: run(["xdotool", what], his_env)  # noqa: E731
    notes: list[str] = []
    for attempt in range(1, max(1, tries) + 1):
        spot = PROBE_SPOTS[(attempt - 1) % len(PROBE_SPOTS)]
        rc1, before_ptr = read("getmouselocation")
        rcw, before_win = read("getactivewindow")
        if rc1 == 0 and rcw == 0 and attempt < tries:
            sleep(quiet_s)                       # nothing sent: does his pointer move by itself right now?
            q_ptr, q_win = read("getmouselocation")[1], read("getactivewindow")[1]
            if q_ptr.strip() != before_ptr.strip() or q_win.strip() != before_win.strip():
                notes.append(f"try {attempt}: his pointer/focus moving on its own ({before_ptr.strip()} -> "
                             f"{q_ptr.strip()}), waited")
                sleep(settle_s)
                continue
        rc2, _ = run(["xdotool", "mousemove", str(spot[0]), str(spot[1]), "click", "1"], her_env)
        _, after_ptr = read("getmouselocation")
        _, after_win = read("getactivewindow")
        ptr_same = rc1 == 0 and before_ptr.strip() == after_ptr.strip()
        win_same = rcw == 0 and before_win.strip() == after_win.strip()
        leak = _ptr_xy(after_ptr) == spot and _ptr_xy(before_ptr) != spot
        # retried ONLY when his pointer moved somewhere that is not her spot; a focus change with his pointer
        # still is never put down to his hand - it fails closed
        if ptr_same or rc1 != 0 or rcw != 0 or rc2 != 0 or leak or attempt >= tries:
            break
        notes.append(f"try {attempt}: changed but not to her spot {spot[0]},{spot[1]} "
                     f"({before_ptr.strip()} -> {after_ptr.strip()}), retried")
        sleep(settle_s)
    tail = (" [" + "; ".join(notes) + "]") if notes else ""
    add("a click on her screen ran", rc2 == 0, "" if rc2 == 0 else "xdotool missing or her display refused it")
    add("his pointer did not move", ptr_same,
        (f"{before_ptr.strip()} -> {after_ptr.strip()}" + (" - HER SPOT, a leak" if leak else "") + tail)
        if rc1 == 0 else "his pointer unreadable")
    # an unreadable reading is a FAIL, never "unchanged" (10-02: two identical errors read as no change)
    add("his focused window did not change", win_same,
        f"{before_win.strip()} -> {after_win.strip()}" if rcw == 0 else "his focused window unreadable")


def landing_check(add, disp: str, xauth: str, mine: str, *, env_file: Path | None = None, launch=None,
                  run=_run, wait_s: float = 15.0) -> None:
    """Open a marker page in her screen browser with the env her hands get; it must land on HER screen and
    never on his. Its own throwaway profile; killed by process group whatever happens."""
    import signal
    import tempfile
    marker = f"zoey-screen-landing-{int(time.time())}"
    exe = screen_browser()
    if not exe:
        add("her browser lands on her screen", False, "no Chromium")
        return
    env = screen_env(dict(os.environ), env_file)
    prof = tempfile.mkdtemp(prefix="zoey-landing-", dir=str((env_file or ENV_FILE).parent))
    # --password-store=basic: her screen has no desktop keyring, and a fresh Chromium profile waits on one forever
    # (10-02 17:1x: no window in 25 s without it, 1 s with it); automation launchers pass the same flag.
    argv = [exe, f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check", "--password-store=basic",
            f"data:text/html,<title>{marker}</title>"]
    proc = (launch or (lambda a, e: subprocess.Popen(a, env=e, stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.DEVNULL, start_new_session=True)))(argv, env)
    on_hers = on_his = False
    try:
        t0 = time.time()
        while time.time() - t0 < wait_s and not on_hers:
            time.sleep(1.0)
            on_hers = marker in (run(["xwininfo", "-root", "-tree", "-display", disp], {"XAUTHORITY": xauth})[1] or "")
        on_his = marker in (run(["xwininfo", "-root", "-tree", "-display", mine])[1] or "")
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        import shutil
        shutil.rmtree(prof, ignore_errors=True)
    add("her browser lands on her screen", on_hers, "" if on_hers else f"no {marker} window on {disp} in {wait_s:.0f} s")
    add("her browser never lands on his", not on_his, "" if not on_his else f"{marker} seen on {mine}")


def check(*, env_file: Path | None = None, user_display: str | None = None, run=_run, landing: bool = True) -> dict:
    env = read_env(env_file)
    disp = env.get("DISPLAY", "")
    xauth = env.get("XAUTHORITY", "")
    mine = user_display or os.environ.get("DISPLAY") or ":0"
    checks = []

    def add(name: str, ok: bool, why: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "why": why})

    add("her env names a display", bool(disp), disp or "no DISPLAY in the env file")
    add("not his display", bool(disp) and disp not in USER_DISPLAYS | {mine}, f"hers {disp}, his {mine}")
    his_auth = os.environ.get("XAUTHORITY", "")
    add("her own X authority", bool(xauth) and xauth != his_auth, f"hers {xauth or '-'}, his {his_auth or '-'}")
    hers = window_names(disp, xauth, run) if disp else None
    add("an X server answers on her display", hers is not None, "xwininfo failed" if hers is None else "")
    pid_h, pid_u = server_pid(disp, run) if disp else "", server_pid(mine, run)
    add("a different X server from his", bool(pid_h) and pid_h != pid_u, f"hers pid {pid_h or '-'}, his {pid_u or '-'}")
    his = window_names(mine, his_auth, run)
    shared = sorted((hers or set()) & (his or set())) if hers is not None and his is not None else []
    add("none of his windows on her screen", hers is not None and not shared,
        ("shared: " + ", ".join(shared[:8])) if shared else ("his list unreadable" if his is None else ""))
    browser_checks(add, exe=screen_browser(), run=run)
    if disp:                 # before focus/landing: their own short-lived probes carry her DISPLAY too
        leaks = runtime_leaks(her_processes(disp), his_runtime_dir())
        add("nothing of hers mounts on his runtime folder", not leaks,
            ("; ".join(leaks[:6]) + (f" (+{len(leaks) - 6})" if len(leaks) > 6 else "")) if leaks else "")
    if disp and hers is not None:
        focus_checks(add, disp, xauth, mine, run=run)
        if landing:
            landing_check(add, disp, xauth, mine, env_file=env_file, run=run)
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "display": disp, "ts": time.time(), "checks": checks}


def write_record(rec: dict, path: Path | None = None) -> Path:
    p = Path(path or RECORD)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return p


def isolated(*, path: Path | None = None, env_file: Path | None = None) -> bool:
    """A passing record for the display her env names NOW (a re-started screen on a new number needs a new check)."""
    if not check_on():
        return True
    try:
        rec = json.loads(Path(path or RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(rec.get("ok")) and rec.get("display") == read_env(env_file).get("DISPLAY")
