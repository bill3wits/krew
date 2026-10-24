""

























from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence

SCHEMA = "os_update/v1"




_MAP_NOISE = (
    "/memfd:", "/dev/", "/SYSV", "/drm", "/anon_hugepage", "[heap]",
    "[stack]", "[vvar]", "[vdso]", "/anon_inode",
)






_INST = re.compile(
    r"^Inst\s+(?P<pkg>\S+)\s+"
    r"(?:\[(?P<cur>[^\]]*)\]\s+)?"
    r"\((?P<cand>\S+)\s+(?P<origin>[^)]*)\)"
)


def parse_sim_upgrade(text: str) -> list[dict]:
    ""





    rows: list[dict] = []
    for line in (text or "").splitlines():
        m = _INST.match(line.strip())
        if not m:
            continue
        origin = (m.group("origin") or "").strip()
        rows.append(
            {
                "package": m.group("pkg"),
                "current": (m.group("cur") or "").strip(),
                "candidate": m.group("cand"),
                "origin": origin,
                "security": "-security" in origin,
            }
        )
    return rows


_UU_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def parse_uu_log(text: str) -> dict:
    ""





    last_run = ""
    packages: list[str] = []
    errors: list[str] = []
    ok = None
    for line in (text or "").splitlines():
        m = _UU_TS.match(line)
        if m:
            last_run = m.group(1)
        low = line.lower()
        if "packages that will be upgraded:" in low:
            packages = line.split(":", 3)[-1].split()
        if "all upgrades installed" in low:
            ok = True
        if " error" in low or "traceback" in low or "exception" in low:
            errors.append(line.strip()[:300])
            ok = False
    return {
        "last_run": last_run,
        "packages": packages,
        "packages_n": len(packages),
        "errors": errors,
        "ok": ok,
    }


def parse_apt_config_list(text: str) -> list[str]:
    ""



    out: list[str] = []
    for line in (text or "").splitlines():
        m = re.search(r'"([^"]*)"', line)
        if m and m.group(1):
            out.append(m.group(1))
    return out


def parse_apt_history(text: str) -> list[dict]:
    ""




    out: list[dict] = []
    block: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("Start-Date:"):
            block = {"start": line.split(":", 1)[1].strip()}
        elif line.startswith("Commandline:"):
            block["commandline"] = line.split(":", 1)[1].strip()
        elif line.startswith("Upgrade:"):
            block["upgrade_raw"] = line.split(":", 1)[1].strip()
        elif line.startswith("End-Date:"):
            block["end"] = line.split(":", 1)[1].strip()
            if "unattended-upgrade" in block.get("commandline", ""):
                raw = block.get("upgrade_raw", "")
                block["n"] = raw.count("(") if raw else 0
                out.append(block)
            block = {}
    return out


def _vkey(v: str) -> tuple:
    ""
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def kernel_drift(running: str, installed: Sequence[tuple[str, str]]) -> dict:
    ""






    real = [(p, v) for p, v in (installed or []) if (v or "").strip()]
    versioned = [
        (p, v) for p, v in real if re.match(r"^linux-image-\d", p)
    ] or real
    newest, newest_abi = "", ""
    if versioned:
        pkg, newest = max(versioned, key=lambda pv: _vkey(pv[1]))






        newest_abi = re.sub(r"^linux-image-(unsigned-)?", "", pkg)




    drift = bool(newest_abi) and bool(running) and newest_abi != running
    return {
        "running": running,
        "newest": newest,
        "newest_abi": newest_abi,
        "drift": drift,
        "installed_n": len(real),
        "old_kernels_installed": max(0, len(
            [p for p, _ in real if re.match(r"^linux-image-\d", p)]
        ) - 1),
    }


def pro_state(status_json: str) -> dict:
    ""





    try:
        d = json.loads(status_json or "{}")
    except Exception:
        return {"attached": None, "available": [], "entitled": []}
    if not isinstance(d, dict):
        return {"attached": None, "available": [], "entitled": []}
    services = d.get("services") or []
    available, entitled = [], []
    for s in services:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or ""
        if s.get("available") == "yes":
            available.append(name)
        if s.get("entitled") == "yes":
            entitled.append(name)
    attached = bool(d.get("attached"))
    return {
        "attached": attached,
        "available": sorted(available),
        "entitled": sorted(entitled),
        "esm_origins_armed_but_inert": (not attached),
    }










try:
    import ms_settings as _MSS
    _ROOTS = _MSS.env_roots()
except Exception:
    _ROOTS = []
ENVS_ROOT = Path(os.environ.get("MS_ENVS_ROOT") or (str(_ROOTS[0]) if _ROOTS else "/nonexistent/ms-envs-root"))

_CODE_PREFIXES = (
    "/usr/", "/lib/", "/lib32/", "/lib64/", "/libx32/", "/opt/", "/bin/",
    "/sbin/", "/snap/", f"{str(ENVS_ROOT).rstrip('/')}/",
)
_CODE_MARKERS = ("/site-packages/", "/dist-packages/")
_USER_DATA_MARKERS = (
    "/dconf/", "/gvfs-metadata/", "/.config/", "/.local/share/", "/.cache/",
)
_USER_DATA_PREFIXES = ("/run/user/", "/tmp/", "/dev/shm/", "/var/tmp/")
_SO = re.compile(r"\.(so(\.[0-9.]+)?|pyd)$")
_EXEC_DIR = re.compile(r"/(s?bin|libexec)/")


def is_code_path(path: str, perms: str = "", *, home: str | None = None) -> bool:
    ""







    p = path or ""
    if not p.startswith("/"):
        return False
    home = (home if home is not None else os.path.expanduser("~")).rstrip("/")
    if home and (
        p.startswith(home + "/.config/")
        or p.startswith(home + "/.local/share/")
        or p.startswith(home + "/.cache/")
    ):
        return False
    if p.startswith(_USER_DATA_PREFIXES):
        return False
    if any(m in p for m in _USER_DATA_MARKERS):
        return False
    if p.endswith((".db", ".sqlite", ".sqlite3", ".cache", ".log")):
        return False
    under_code = p.startswith(_CODE_PREFIXES) or any(m in p for m in _CODE_MARKERS)
    if not under_code:
        return False
    return bool(_SO.search(p) or _EXEC_DIR.search(p) or "x" in (perms or ""))


def classify_deleted_maps(
    maps_text: str, *, home: str | None = None
) -> tuple[list[str], list[str]]:
    ""




    code: dict[str, bool] = {}


    all_maps = os.environ.get("MS_RESTART_DEBT_ALL_MAPS", "") == "1"
    for line in (maps_text or "").splitlines():
        if not line.rstrip().endswith("(deleted)"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        perms = parts[1]
        path = parts[5].strip()
        if not path.endswith("(deleted)"):
            continue
        path = path[: -len("(deleted)")].strip()
        if not path.startswith("/"):
            continue
        if any(path.startswith(n) for n in _MAP_NOISE):
            continue

        code[path] = code.get(path, False) or all_maps or is_code_path(
            path, perms, home=home
        )
    kept = sorted(p for p, c in code.items() if c)
    ignored = sorted(p for p, c in code.items() if not c)
    return kept, ignored


def deleted_maps(maps_text: str, *, home: str | None = None) -> list[str]:
    ""
    return classify_deleted_maps(maps_text, home=home)[0]










_DIST_INFO = re.compile(r"^(?P<name>.+?)-(?P<ver>[^-]+)\.dist-info$")


def env_of_process(
    argv0: str, exe: str, maps_text: str = "", *, envs_root: str | Path = ENVS_ROOT
) -> str | None:
    ""





    root = str(envs_root).rstrip("/") + "/"
    rx = re.compile("^" + re.escape(root) + r"([^/]+)/")
    for cand in (argv0 or "", exe or ""):
        m = rx.match(cand)
        if m:
            return m.group(1)
    for line in (maps_text or "").splitlines():
        i = line.find(root)
        if i < 0 or "/site-packages/" not in line:
            continue
        m = rx.match(line[i:])
        if m:
            return m.group(1)
    return None


def newer_dist_infos(
    site_packages: Iterable[Path], started: float
) -> list[str]:
    ""
    return sorted({lbl for lbl, mt in _dist_info_rows(site_packages) if mt > started})


def env_site_packages(env: str, *, envs_root: Path = ENVS_ROOT) -> list[Path]:
    return sorted((envs_root / env).glob("lib/python*/site-packages"))


def _boot_time(stat_text: str | None = None) -> float | None:
    try:
        text = stat_text if stat_text is not None else Path("/proc/stat").read_text()
        for line in text.splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except Exception:
        return None
    return None


def process_start(pid: int, *, proc: Path = Path("/proc"), btime: float | None = None) -> float | None:
    ""





    try:
        raw = (proc / str(pid) / "stat").read_text()
        rest = raw[raw.rindex(")") + 2:].split()
        ticks = int(rest[19])
        bt = btime if btime is not None else _boot_time()
        if bt is not None:
            return bt + ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        pass
    try:
        return (proc / str(pid)).stat().st_ctime
    except Exception:
        return None




_SESSION_UNIT = re.compile(
    r"^(gnome-session|org\.gnome\.|gnome-shell|at-spi|dconf|gvfs|xdg-|"
    r"dbus(-broker)?\.service$|evolution-|localsearch|tracker-|"
    r"org\.freedesktop\.IBus)"
)


def is_session_unit(unit: str, slice_: str = "") -> bool:
    return slice_ == "session.slice" or bool(_SESSION_UNIT.match(unit or ""))


def attribute_paths(
    paths: Iterable[str],
    *,
    dpkg_search: Callable[[list[str]], dict[str, str]],
    which_env: Callable[[str], str | None],
) -> dict:
    ""





    os_hits: dict[str, int] = {}
    venv_hits: dict[str, int] = {}
    other: list[str] = []
    paths = list(paths)
    env_paths, sys_paths = [], []
    for p in paths:
        if which_env(p):
            env_paths.append(p)
        else:
            sys_paths.append(p)
    for p in env_paths:
        env = which_env(p) or "?"
        venv_hits[env] = venv_hits.get(env, 0) + 1
    owners = dpkg_search(sys_paths) if sys_paths else {}
    for p in sys_paths:
        pkg = owners.get(p)
        if pkg:
            os_hits[pkg] = os_hits.get(pkg, 0) + 1
        else:
            other.append(p)
    return {"os": os_hits, "venv": venv_hits, "other": sorted(other)}


def nvidia_blacklist_covers(patterns: Sequence[str], names: Sequence[str]) -> dict:
    ""






    out: dict[str, bool] = {}
    for n in names:
        out[n] = any(re.match(p, n) for p in patterns)
    return out


def nvidia_stack_consistent(
    *,
    userspace: str,
    module_packages: Sequence[str],
    running_kernel: str,
    newest_kernel_abi: str,
) -> dict:
    ""








    branch = ""
    m = re.search(r"(\d+)", userspace or "")
    if m:
        branch = m.group(1)
    mods = list(module_packages or [])

    def _for(abi: str) -> list[str]:
        return [p for p in mods if abi and abi in p]

    running_mod = _for(running_kernel)
    newest_mod = _for(newest_kernel_abi) if newest_kernel_abi else []
    branches = sorted({
        mm.group(1)
        for p in mods
        if (mm := re.search(r"linux-modules-nvidia-(\d+)", p))
    })
    problems = []
    if branch and branches and branch not in branches:
        problems.append(
            f"userspace is on {branch}, kernel modules on {'/'.join(branches)}"
        )
    if len(branches) > 1:
        problems.append(f"two module branches installed: {'/'.join(branches)}")
    if mods and running_kernel and not running_mod:
        problems.append(f"no nvidia module for the running kernel {running_kernel}")
    if mods and newest_kernel_abi and not newest_mod:
        problems.append(
            f"no nvidia module for the newest installed kernel "
            f"{newest_kernel_abi} — a reboot would land without a GPU"
        )
    return {
        "ok": not problems,
        "userspace_branch": branch,
        "module_branches": branches,
        "reboot_safe": bool(newest_mod) if (mods and newest_kernel_abi) else None,
        "why": "; ".join(problems),
    }







STALE_S = 36 * 3600


def panel_copy(state: dict, *, now: float | None = None) -> dict:
    ""








    now = time.time() if now is None else now
    state = state or {}
    ts = state.get("ts") or 0
    stale = (not state) or (now - float(ts or 0) > STALE_S)

    def row(key, value, level, hollow=False):
        return {"key": key, "value": value, "level": level, "hollow": hollow}

    if stale:
        rows = [
            row("security fixes", "not measured", "", True),
            row("swap owed", "not measured", "", True),
            row("machine restart", "not measured", "", True),
        ]
        return {"level": "", "rows": rows, "stale": True, "tip": TIP}


    uu = state.get("unattended") or {}
    pending = state.get("pending") or {}
    stale_days = int(uu.get("stale_days") or 0)
    if uu.get("ok") is False:
        r1 = row("security fixes", "the updater hit a problem", "bad")
    elif stale_days >= 3:
        r1 = row("security fixes", f"not checked in {stale_days} days", "bad")
    elif int(pending.get("n") or 0) > 0:
        n = int(pending["n"])
        r1 = row("security fixes", f"{n} waiting — they land tonight", "warn")
    else:
        r1 = row("security fixes", "up to date", "ok")



    cfg = state.get("config") or {}
    if cfg.get("nvidia_ok") is False:
        r1 = row("security fixes", "the graphics driver and the system kernel disagree", "bad")


    debt = state.get("restart_debt") or {}
    units = debt.get("units") or {}



    owed = debt.get("actionable_n")
    if owed is None:
        owed = sum(1 for u in units.values() if (u or {}).get("n"))
    if not debt:
        r2 = row("swap owed", "not measured", "", True)
    elif owed == 0:
        r2 = row("swap owed", "none", "ok")
    else:
        win = state.get("window") or {}
        stuck = int(win.get("consecutive_failures") or 0) >= 2 or win.get("blocked")
        if stuck:
            r2 = row("swap owed", f"{owed} parts still on the old copy — the swap is stuck", "bad")
        else:
            r2 = row("swap owed", f"{owed} parts still on the old copy — swapped overnight", "warn")


    reboot = state.get("reboot") or {}
    if not reboot:
        r3 = row("machine restart", "not measured", "", True)
    elif not reboot.get("required"):
        r3 = row("machine restart", "not needed", "ok")
    else:
        days = int(reboot.get("waiting_days") or 0)
        if days >= 2:
            r3 = row("machine restart", f"waiting {days} days — only you can do this", "bad")
        else:
            r3 = row("machine restart", "needed to finish — only you can do this", "warn")

    rank = {"": 0, "ok": 0, "warn": 1, "bad": 2}
    level = max((r["level"] for r in (r1, r2, r3)), key=lambda x: rank.get(x, 0))
    return {
        "level": "" if level == "ok" else level,
        "rows": [r1, r2, r3],
        "stale": False,
        "tip": TIP,
    }


TIP = (
    "The machine patches its own security holes every night while you sleep. "
    "A patch only counts once the part using it starts fresh, so a few parts "
    "get swapped in the small hours — you will not notice. Two things are not "
    "automatic: a full machine restart, and anything this panel lights up for. "
    "Nothing here restarts the machine on its own."
)






def _run(cmd: list[str], *, timeout: int = 60) -> str | None:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return r.stdout
    except Exception:
        return None


def _read(path: Path) -> str | None:
    ""
    try:
        if path.is_file():
            txt = path.read_text(errors="replace")
            if txt.strip():
                return txt
        gz = Path(str(path) + ".1.gz")
        if gz.is_file():
            with gzip.open(gz, "rt", errors="replace") as fh:
                return fh.read()
        if path.is_file():
            return ""
    except Exception:
        return None
    return None


def default_which_env(path: str) -> str | None:
    ""
    m = re.match(r"^" + re.escape(str(ENVS_ROOT).rstrip("/")) + r"/([^/]+)/", path or "")
    return m.group(1) if m else None


def dpkg_search(paths: list[str]) -> dict[str, str]:
    ""
    if not paths:
        return {}
    out = _run(["dpkg", "-S", *paths], timeout=120) or ""
    owners: dict[str, str] = {}
    for line in out.splitlines():
        if ": " not in line:
            continue
        pkgs, _, p = line.partition(": ")
        owners[p.strip()] = pkgs.split(",")[0].split(":")[0].strip()
    return owners


def _unit_of(pid: int, *, proc: Path = Path("/proc")) -> tuple[str, str]:
    ""








    try:
        cg = (proc / str(pid) / "cgroup").read_text()
    except Exception:
        return "", ""
    last = cg.strip().splitlines()[-1] if cg.strip() else ""
    parts = [x for x in last.split("/") if x]
    leaf = parts[-1] if parts else ""
    slice_ = next((x for x in reversed(parts[:-1]) if x.endswith(".slice")), "")
    return (leaf if leaf.endswith(".service") else ""), slice_


def _dist_info_rows(site_packages: Iterable[Path]) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for sp in site_packages:
        try:
            entries = list(Path(sp).iterdir())
        except OSError:
            continue
        for e in entries:
            m = _DIST_INFO.match(e.name)
            if not m:
                continue
            try:
                rows.append((f"{m.group('name')} {m.group('ver')}", e.stat().st_mtime))
            except OSError:
                continue
    return rows


def restart_debt(
    *,
    which_env=default_which_env,
    uid: int | None = None,
    proc: Path = Path("/proc"),
    home: str | None = None,
    envs_root: Path = ENVS_ROOT,
    dpkg=None,
    btime: float | None = None,
) -> dict:
    ""









    uid = os.getuid() if uid is None else uid
    dpkg = dpkg_search if dpkg is None else dpkg
    bt = _boot_time() if btime is None else btime
    units: dict[str, dict] = {}
    all_paths: set[str] = set()
    ignored_total: set[str] = set()
    dist_cache: dict[str, list[tuple[str, float]]] = {}
    try:
        entries = list(proc.iterdir())
    except OSError:
        entries = []
    for entry in sorted(entries, key=lambda e: e.name):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if entry.stat().st_uid != uid:
                continue
            maps = (entry / "maps").read_text(errors="replace")
        except Exception:
            continue
        dead, ignored = classify_deleted_maps(maps, home=home)
        stale_py: list[str] = []
        env = None
        try:
            argv0 = (entry / "cmdline").read_bytes().split(b"\0")[0].decode(
                errors="replace"
            )
        except Exception:
            argv0 = ""
        try:
            exe = os.readlink(entry / "exe")
        except Exception:
            exe = ""
        env = env_of_process(argv0, exe, maps, envs_root=envs_root)
        if env:
            if env not in dist_cache:
                dist_cache[env] = _dist_info_rows(
                    env_site_packages(env, envs_root=envs_root)
                )
            started = process_start(pid, proc=proc, btime=bt)
            if started is not None:
                stale_py = sorted({lbl for lbl, mt in dist_cache[env] if mt > started})
        ignored_total.update(ignored)
        if not dead and not stale_py:
            continue
        unit, slice_ = _unit_of(pid, proc=proc)
        if not unit:
            continue
        session = is_session_unit(unit, slice_)
        row = units.setdefault(unit, {
            "pid": pid,
            "pids": [],
            "slice": slice_,
            "session": session,
            "actionable": not session,
            "_paths": set(),
            "_py": {},
        })
        row["pids"].append(pid)
        row["_paths"].update(dead)
        if stale_py and env:
            row["_py"].setdefault(env, set()).update(stale_py)
        all_paths.update(dead)

    sys_paths = sorted(p for p in all_paths if not which_env(p))
    owners = dpkg(sys_paths) if sys_paths else {}
    packages: set[str] = set()
    for unit, row in units.items():
        paths = sorted(row.pop("_paths"))
        py = {e: sorted(v) for e, v in sorted(row.pop("_py").items())}
        attrib = attribute_paths(
            paths,
            dpkg_search=lambda ps, _o=owners: {p: _o[p] for p in ps if p in _o},
            which_env=which_env,
        )
        venv_maps = dict(attrib["venv"])
        venv = dict(venv_maps)
        for e, pk in py.items():
            venv[e] = venv.get(e, 0) + len(pk)
        row.update({
            "n": len(paths) + sum(len(v) for v in py.values()),
            "maps_n": len(paths),
            "py_n": sum(len(v) for v in py.values()),
            "os": attrib["os"],

            "venv": venv,
            "venv_maps": venv_maps,
            "venv_py": py,
            "other": attrib["other"],
            "paths": paths[:20],
        })
        if row["session"]:
            row["why"] = "desktop session unit — never restarted by a krew job"
        packages.update(attrib["os"])
    return {
        "units": units,
        "os_units_n": sum(1 for u in units.values() if u.get("os")),
        "venv_units_n": sum(1 for u in units.values() if u.get("venv")),
        "venv_py_units_n": sum(1 for u in units.values() if u.get("venv_py")),
        "actionable_n": sum(1 for u in units.values() if u.get("actionable")),
        "session_n": sum(1 for u in units.values() if u.get("session")),
        "ignored_user_data_n": len(ignored_total),
        "packages": sorted(packages),
    }


def gather(*, root: Path = Path("/"), which_env=default_which_env) -> dict:
    ""
    now = time.time()
    state: dict = {"schema": SCHEMA, "ts": now, "ts_local": time.strftime("%F %T")}

    sim = _run(
        ["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"], timeout=120
    )
    rows = parse_sim_upgrade(sim or "")
    state["pending"] = {
        "n": len(rows),
        "security_n": sum(1 for r in rows if r["security"]),
        "rows": rows[:80],
        "measured": sim is not None,
    }

    uu = parse_uu_log(
        _read(root / "var/log/unattended-upgrades/unattended-upgrades.log") or ""
    )
    stale_days = 0
    if uu.get("last_run"):
        try:
            t = time.mktime(time.strptime(uu["last_run"], "%Y-%m-%d %H:%M:%S"))
            stale_days = max(0, int((now - t) // 86400))
        except Exception:
            stale_days = 0
    uu["stale_days"] = stale_days
    state["unattended"] = uu

    rr = root / "var/run/reboot-required"
    pkgs = _read(root / "var/run/reboot-required.pkgs") or ""
    required = rr.is_file()
    waiting_days = 0
    if required:
        try:
            waiting_days = max(0, int((now - rr.stat().st_mtime) // 86400))
        except Exception:
            waiting_days = 0
    state["reboot"] = {
        "required": required,
        "waiting_days": waiting_days,
        "pkgs": sorted(set(pkgs.split())),
    }

    running = (_run(["uname", "-r"], timeout=10) or "").strip()
    kern_raw = _run(
        ["dpkg-query", "-W", "-f=${Package} ${Version}\n", "linux-image-*"], timeout=60
    ) or ""
    installed = []
    for line in kern_raw.splitlines():
        parts = line.split()
        installed.append((parts[0], parts[1] if len(parts) > 1 else ""))
    state["kernel"] = kernel_drift(running, installed)

    state["pro"] = pro_state(
        _read(root / "var/lib/ubuntu-advantage/status.json") or "{}"
    )

    state["restart_debt"] = restart_debt(which_env=which_env)

    mods = [
        l.split()[0]
        for l in (
            _run(
                ["dpkg-query", "-W", "-f=${Package} ${Status}\n",
                 "linux-modules-nvidia-*"], timeout=60
            ) or ""
        ).splitlines()
        if "installed" in l and l.split()
    ]
    userspace = ""
    for line in (
        _run(["dpkg-query", "-W", "-f=${Package} ${Version}\n",
              "nvidia-driver-*"], timeout=60) or ""
    ).splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[1].strip():
            userspace = parts[0]
            break
    nv = nvidia_stack_consistent(
        userspace=userspace,
        module_packages=mods,
        running_kernel=running,
        newest_kernel_abi=state["kernel"].get("newest_abi", ""),
    )




    blacklist = parse_apt_config_list(
        _run(["apt-config", "dump", "Unattended-Upgrade::Package-Blacklist"],
             timeout=30) or ""
    )
    state["config"] = {
        "nvidia_ok": nv["ok"],
        "nvidia_why": nv["why"],
        "nvidia_reboot_safe": nv["reboot_safe"],
        "blacklist": blacklist,
        "brave_autoupgrade_armed": (
            root / "etc/apt/apt.conf.d/51brave-unattended"
        ).is_file(),
    }
    return state


def write_artifact(state: dict, *, out_dir: Path) -> Path:
    ""
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "os-update-LATEST.json"
    blob = json.dumps(state, indent=2, default=str) + "\n"
    latest.write_text(blob)
    (out_dir / f"os-update-{time.strftime('%Y-%m-%d')}.json").write_text(blob)
    return latest
