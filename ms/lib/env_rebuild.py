""












































from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import env_roots

ENABLED = os.environ.get("MS_REBUILD", "0").strip().lower() in ("1", "on", "true", "yes")
_KH = Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser()
OUT_ROOT = Path(os.environ.get("MS_OUT") or _KH / "ms")
STATE_ROOT = Path(os.environ.get("MS_REBUILD_STATE", str(OUT_ROOT / "rebuilds")))
ENVS_ROOT = Path(os.environ.get("MS_ENVS_ROOT") or _KH / "envs")
HOLD_FILE = Path(os.environ.get("MS_ENV_HOLD_FILE", str(_KH / "ms-env-hold.json")))
GPU_MODE_FILE = Path(os.environ.get("MS_GPU_MODE_FILE", str(_KH / "gpu-mode")))
UV = os.environ.get("MS_UV") or shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
SCOPE = ["systemd-run", "--user", "--scope", "-q", "-p", "CPUQuota=300%"]
MARKER = ".ms-rebuild.json"
PSNR_PASS = float(os.environ.get("MS_REBUILD_PSNR_PASS", "30"))

PATCHES_ON = os.environ.get("MS_REBUILD_PATCHES", "1").strip().lower() not in ("0", "off", "false", "no")

Runner = Callable[..., subprocess.CompletedProcess]


class Refused(RuntimeError):
    ""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)



def rid(env: str, ver: str) -> str:
    return f"{env}-{ver}"


def state_dir(env: str, ver: str) -> Path:
    return STATE_ROOT / rid(env, ver)


def load_state(env: str, ver: str) -> dict:
    p = state_dir(env, ver) / "state.json"
    if p.is_file():
        return json.loads(p.read_text())
    return {"env": env, "version": ver, "new_dir": str(ENVS_ROOT / rid(env, ver)),
            "live": str(ENVS_ROOT / env), "steps": {}, "created": _now()}


def save_state(st: dict) -> None:
    _atomic_json(state_dir(st["env"], st["version"]) / "state.json", st)


def read_marker(d: Path) -> dict | None:
    try:
        return json.loads((Path(d) / MARKER).read_text())
    except (OSError, ValueError):
        return None


def write_marker(d: Path, **kw) -> None:
    m = read_marker(d) or {}
    m.update(kw, updated=_now())
    _atomic_json(Path(d) / MARKER, m)


def discovery_skip(d: Path) -> str:
    ""

    m = read_marker(d)
    if m and m.get("role") in ("staged", "retired"):
        return f"{m.get('role')} rebuild of {m.get('of')} ({m.get('version')})"
    return ""


def rebuild_rows(root: Path | None = None, *, today: dt.date | None = None) -> list[dict]:
    ""

    root = root or ENVS_ROOT
    today = today or dt.date.today()
    rows = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_symlink():
            continue
        m = read_marker(d)
        if not m:
            continue
        ku = m.get("keep_until") or ""
        due = bool(ku) and m.get("role") == "retired" and dt.date.fromisoformat(ku) < today
        rows.append({"dir": d.name, "of": m.get("of"), "version": m.get("version"),
                     "role": m.get("role"), "keep_until": ku, "due": due})
    return rows



def gpu_mode() -> str:
    try:
        return GPU_MODE_FILE.read_text().split()[0].strip().lower()
    except (OSError, IndexError):
        return ""


def http_json(url: str, *, data: dict | None = None, timeout: float = 10.0):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def comfy_answers(port: int) -> bool:
    try:
        http_json(f"http://127.0.0.1:{port}/system_stats", timeout=3)
        return True
    except Exception:
        return False


def health_answers(url: str, *, timeout: float = 3.0) -> bool:
    ""

    import urllib.error
    try:
        urllib.request.urlopen(url, timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def app_running_check(env: str, rb: dict) -> Callable[[], bool]:
    ""

    url = (rb or {}).get("health_url") or ""
    if not url:
        raise Refused(f"no health_url declared for {env} (config/ms-env-roots.json rebuild block) - "
                      "the flip must be able to ask whether the app is running")
    return lambda: health_answers(url)


def base_python(old_dir: Path) -> str:
    ""

    cfg = (Path(old_dir) / "pyvenv.cfg").read_text()
    home = next((l.split("=", 1)[1].strip() for l in cfg.splitlines() if l.startswith("home")), "")
    ver = next((l.split("=", 1)[1].strip() for l in cfg.splitlines() if l.startswith("version_info")), "")
    mm = ".".join(ver.split(".")[:2])
    for name in (f"python{mm}", "python3", "python"):
        p = Path(home) / name
        if p.exists():
            return str(p)
    raise Refused(f"no base interpreter under {home}")


def _index_flags(rb: dict) -> list[str]:
    out = []
    for u in rb.get("extra_index_urls") or []:
        out += ["--extra-index-url", u]
    if rb.get("extra_index_urls"):
        out += ["--index-strategy", "unsafe-best-match"]
    return out


def _is_never(name: str, never: set, prefixes: tuple) -> bool:
    k = env_roots.canon(name)
    return k in never or k.startswith(prefixes)



def plan(env: str, ver: str, env_cfg: dict, *, old_py: str, never: set, never_prefixes: tuple,
         run: Runner = subprocess.run, probe=env_roots.probe) -> dict:
    ""
    st = load_state(env, ver)
    rb = env_cfg.get("rebuild") or {}
    sd = state_dir(env, ver)
    sd.mkdir(parents=True, exist_ok=True)
    dump = probe(old_py, env_cfg.get("entries") or [])
    miss = env_roots.missing_roots(env_cfg, dump)
    if miss:
        st["steps"]["plan"] = {"ts": _now(), "ok": False, "missing_roots": miss}
        save_state(st)
        raise Refused("missing declared roots: " + "; ".join(miss))
    roots, unmapped = env_roots.roots_of(env_cfg, dump)
    via = env_roots.closure(roots, dump, env_cfg)
    exclude = {env_roots.canon(x) for x in rb.get("exclude") or []}
    lines, named = [], set()
    for f in env_cfg.get("files") or []:
        for line in env_roots._file_lines(f):
            rq = env_roots._req(line)
            if rq is None or env_roots.canon(rq.name) in exclude:
                continue
            lines.append(f"{line}  # {Path(env_roots.expand(f)).name}")
            named.add(env_roots.canon(rq.name))
    for rq, src in roots:
        k = env_roots.canon(rq.name)
        if k in named or k in exclude or src.startswith("file:"):
            continue
        lines.append(f"{dump['dists'][k]['name'] if k in dump['dists'] else rq.name}  # {src}")
        named.add(k)
    for k, why in via.items():
        if (why.startswith("data-wheel:") or why.startswith("entry-point:")) and k not in named:
            lines.append(f"{dump['dists'][k]['name']}  # {why}")
            named.add(k)
    carried = sorted(f"{dump['dists'][k]['name']}=={dump['dists'][k]['version']}"
                     for k in via if k in dump["dists"] and _is_never(k, never, never_prefixes))
    (sd / "roots.in").write_text("\n".join(lines) + "\n")
    (sd / "constraints.txt").write_text("\n".join(carried) + "\n")
    (sd / "overrides.txt").unlink(missing_ok=True)
    lock = sd / "lock.txt"
    cmd = [UV, "pip", "compile", str(sd / "roots.in"), "--constraint", str(sd / "constraints.txt"),
           "--python", old_py, "-o", str(lock), "--no-header", *_index_flags(rb)]
    r = run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        st["steps"]["plan"] = {"ts": _now(), "ok": False, "err": (r.stderr or "")[-1500:]}
        save_state(st)
        raise Refused(f"uv pip compile failed: {(r.stderr or '')[-600:]}")
    pins = [l for l in lock.read_text().splitlines() if l and not l.startswith((" ", "#"))]
    st["old_dir"] = str(Path(old_py).parent.parent)
    st["steps"]["plan"] = {"ts": _now(), "ok": True, "roots_in": len(lines), "carried": carried,
                           "locked": len(pins), "unmapped_imports": sorted(unmapped),
                           "excluded": sorted(exclude), "cmd": cmd}
    save_state(st)
    return st



def build(env: str, ver: str, env_cfg: dict, *, run: Runner = subprocess.run, scope: list | None = None) -> dict:
    st = load_state(env, ver)
    if not (st["steps"].get("plan") or {}).get("ok"):
        raise Refused("plan first")
    rb = env_cfg.get("rebuild") or {}
    new = Path(st["new_dir"])
    if new.exists():
        m = read_marker(new)
        if not m or m.get("of") != env or m.get("role") != "staged":
            raise Refused(f"{new} exists and is not this rebuild's staged env - not touching it")
    old = Path(st["old_dir"])
    scope = SCOPE if scope is None else scope
    if not new.exists():
        r = run([UV, "venv", str(new), "--python", base_python(old)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise Refused(f"uv venv failed: {(r.stderr or '')[-400:]}")
    write_marker(new, of=env, version=ver, role="staged", built=None, lock=str(state_dir(env, ver) / "lock.txt"))
    t0 = time.time()
    r = run([*scope, UV, "pip", "sync", str(state_dir(env, ver) / "lock.txt"), "--python", str(new / "bin" / "python"),
             *_index_flags(rb)], capture_output=True, text=True, timeout=5400)
    ok = r.returncode == 0
    st["steps"]["build"] = {"ts": _now(), "ok": ok, "seconds": round(time.time() - t0),
                            "err": "" if ok else (r.stderr or "")[-1500:]}
    if not ok:
        save_state(st)
        raise Refused(f"uv pip sync failed: {(r.stderr or '')[-600:]}")
    if rb.get("patches"):
        try:
            st["steps"]["build"]["patches"] = apply_patches(new, rb)
        except Refused as e:
            st["steps"]["build"].update(ok=False, err=str(e))
            save_state(st)
            raise
    write_marker(new, built=_now())
    save_state(st)
    return st



def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _site_packages(env_dir: Path) -> Path:
    sp = sorted(Path(env_dir).glob("lib/python*/site-packages"))
    if not sp:
        raise Refused(f"no site-packages under {env_dir}")
    return sp[0]


def patch_state(env_dir: Path, rb: dict) -> list[dict]:
    ""
    rows = []
    for p in rb.get("patches") or []:
        target = _site_packages(env_dir) / p["file"]
        row = {"file": p["file"], "why": p.get("why") or ""}
        if not target.is_file():
            row["state"] = "missing"
        else:
            got = _sha256(target)
            row["sha256"] = got
            row["state"] = ("patched" if got == p["sha256_patched"] else
                            "stock" if got == p["sha256_stock"] else "unknown")
        rows.append(row)
    return rows


def apply_patches(env_dir: Path, rb: dict) -> list[dict]:
    ""







    rows = patch_state(env_dir, rb)
    if not PATCHES_ON:
        return [{**r, "applied": False, "skipped": "MS_REBUILD_PATCHES=0"} for r in rows]
    for p, row in zip(rb.get("patches") or [], rows):
        target = _site_packages(env_dir) / p["file"]
        if row["state"] == "patched":
            row["applied"] = False
            continue
        if row["state"] != "stock":
            raise Refused(f"patch {p['file']}: the installed file is {row['state']} "
                          f"({row.get('sha256', 'absent')[:12]}), not the stock file this patch was made for "
                          f"({p['sha256_stock'][:12]}) - the wheel changed, re-make the patch")
        src = Path(env_roots.expand(p["from"]))
        if not src.is_file() or _sha256(src) != p["sha256_patched"]:
            raise Refused(f"patch {p['file']}: {src} is missing or is not the declared patched file")
        tmp = target.with_name(target.name + ".ms-patch")
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
        if _sha256(target) != p["sha256_patched"]:
            raise Refused(f"patch {p['file']}: wrote it and it does not read back as the patched file")
        row.update(state="patched", sha256=p["sha256_patched"], applied=True)
    return rows



def _fill(value: str, st: dict) -> str:
    ver = str(st["version"]).lstrip("v")
    return (str(value).replace("{new_dir}", str(st["new_dir"])).replace("{version}", ver)
            .replace("{version_mm}", ".".join(ver.split(".")[:2])))


def first_start(env: str, ver: str, env_cfg: dict, *, yes: bool, run: Runner = subprocess.run,
                answers: Callable[[str], bool] | None = None, sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.time) -> dict:
    ""





    st = load_state(env, ver)
    fs = (env_cfg.get("rebuild") or {}).get("first_start")
    if not fs:
        raise Refused(f"{env} declares no rebuild.first_start")
    if not (st["steps"].get("build") or {}).get("ok"):
        raise Refused("build first")
    if not yes:
        raise Refused(f"firststart stops {fs['lane_unit']} for a few minutes - it needs --yes")
    mode = GPU_MODE_FILE.read_text().split("\n", 1)[0].strip() if GPU_MODE_FILE.is_file() else ""
    if mode not in ("", "normal"):
        raise Refused(f"gpu mode is {mode!r}: a first start needs the lane's slot in a normal machine")
    answers = answers or health_answers
    lane, unit = fs["lane_unit"], f"ms-first-start-{rid(env, ver)}".replace("_", "-").replace(".", "-")
    hold = Path(fs.get("hold_file") or _KH / "train-hold")
    mark = f"card=first-start-{env}"
    res: dict = {"ts": _now(), "ok": False, "unit": unit, "lane_unit": lane}

    def step(cmd, timeout=120):
        return run(cmd, capture_output=True, text=True, timeout=timeout)

    try:
        import ms_baseline
    except Exception:
        ms_baseline = None
    if ms_baseline:
        ms_baseline.start(f"first-start-{env}")
    with hold.open("a") as fh:
        fh.write(f"# unit {lane} {mark}\n{lane}\n")
    step(["systemctl", "--user", "stop", lane])
    restore = (f"/bin/sh -c 'sed -i \"/{mark}/,+1d\" {hold}; "
               f"[ -s {hold} ] || rm -f {hold}; systemctl --user start {lane}'")
    cmd = ["systemd-run", "--user", "--unit", unit, "--collect",
           "-p", f"RuntimeMaxSec={int(fs.get('runtime_max_s', 1800))}",
           "-p", f"MemoryMax={fs.get('memory_max', '48G')}", "-p", f"MemorySwapMax={fs.get('memory_swap_max', '2G')}",
           "-p", f"ExecStopPost={restore}"]
    for k, v in (fs.get("env") or {}).items():
        cmd += ["-E", f"{k}={_fill(v, st)}"]
    cmd.append(env_roots.expand(_fill(fs["script"], st)))
    t0 = clock()
    r = step(cmd)
    if r.returncode != 0:

        step(["/bin/sh", "-c", restore.split("-c ", 1)[1].strip("'")])
        res["err"] = f"systemd-run failed: {(r.stderr or '')[-300:]}"
    else:
        deadline = t0 + float(fs.get("ready_timeout_s", 1200))
        while clock() < deadline:
            if answers(fs["ready_url"]):
                res["ready_s"] = round(clock() - t0)
                break
            if step(["systemctl", "--user", "is-active", "-q", unit]).returncode != 0:
                res["err"] = "the first start exited before it served (killed at its memory cap, or a start error)"
                break
            sleep(2.0)
        else:
            res["err"] = f"not serving after {int(fs.get('ready_timeout_s', 1200))} s"
        if "ready_s" in res:
            sleep(float(fs.get("settle_s", 20)))
        peak = step(["systemctl", "--user", "show", unit, "-p", "MemoryPeak", "--value"]).stdout.strip()
        res["memory_peak_mib"] = int(peak) // 1048576 if peak.isdigit() else None
        step(["systemctl", "--user", "stop", unit], timeout=180)
    back = clock() + float(fs.get("lane_back_timeout_s", 600))
    res["lane_back"] = False
    while clock() < back:
        if step(["systemctl", "--user", "is-active", "-q", lane]).returncode == 0 and answers(fs["ready_url"]):
            res["lane_back"] = True
            break
        sleep(2.0)
    if ms_baseline:
        ms_baseline.end(f"first-start-{env}")
    res["ok"] = "ready_s" in res and res["lane_back"]
    st["steps"]["first_start"] = res
    save_state(st)
    if res["ok"]:
        write_marker(Path(st["new_dir"]), first_started=_now())
    elif not res["lane_back"]:
        raise Refused(f"{lane} did not come back after the first start - start it by hand: "
                      f"systemctl --user start {lane} ({res.get('err') or 'first start served'})")
    else:
        raise Refused(f"first start failed: {res.get('err')}")
    return st



def workflows(rb: dict) -> dict[str, dict]:
    d = Path(rb.get("workflows_dir") or "")
    skip = set(rb.get("skip_workflows") or [])
    out = {}
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        if f.name in skip or ".bak" in f.name or "smoke" in f.name:
            continue
        try:
            g = json.loads(f.read_text())
        except ValueError:
            continue
        if isinstance(g, dict) and "nodes" not in g and all(isinstance(v, dict) for v in g.values()):
            out[f.name] = g
    return out


def node_types(graphs: dict[str, dict]) -> dict[str, list[str]]:
    need: dict[str, list[str]] = {}
    for name, g in graphs.items():
        for node in g.values():
            t = node.get("class_type")
            if t:
                need.setdefault(t, []).append(name)
    return need


def cpu_node_set(py: str, rb: dict, *, port: int, popen=subprocess.Popen, wait_s: float = 240.0) -> dict:
    ""


    app = Path(rb["app_dir"])
    tmp = Path(tempfile.mkdtemp(prefix="ms-rebuild-cpu-"))
    for sub in ("user", "output", "temp"):
        (tmp / sub).mkdir()
    cmd = [py, str(app / "main.py"), "--cpu", "--listen", "127.0.0.1", "--port", str(port),
           "--disable-auto-launch", "--user-directory", str(tmp / "user"),
           "--database-url", f"sqlite:///{tmp / 'db.sqlite'}", "--output-directory", str(tmp / "output"),
           "--temp-directory", str(tmp / "temp"), "--disable-all-custom-nodes",
           "--whitelist-custom-nodes", *(rb.get("custom_nodes") or [])]
    envv = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1",
                HF_HOME=os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    log = (tmp / "comfy.log").open("wb")
    p = popen(cmd, cwd=str(app), env=envv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    info, err = None, ""
    try:
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if p.poll() is not None:
                err = f"exited rc={p.returncode}"
                break
            try:
                info = http_json(f"http://127.0.0.1:{port}/object_info", timeout=20)
                break
            except Exception:
                time.sleep(2)
        else:
            err = f"no /object_info in {wait_s:.0f} s"
    finally:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=30)
            except Exception:
                p.kill()
        log.close()
    tail = ""
    try:
        tail = (tmp / "comfy.log").read_text(errors="replace")[-3000:]
    except OSError:
        pass
    failed = [l.strip() for l in tail.splitlines() if "IMPORT FAILED" in l]
    shutil.rmtree(tmp, ignore_errors=True)
    return {"nodes": sorted(info) if isinstance(info, dict) else [], "err": err,
            "import_failed": failed, "log_tail": tail[-800:] if err else ""}



def check(env: str, ver: str, env_cfg: dict, *, run: Runner = subprocess.run, probe=env_roots.probe,
          node_set=cpu_node_set, ports=(18190, 18191)) -> dict:
    st = load_state(env, ver)
    if not (st["steps"].get("build") or {}).get("ok"):
        raise Refused("build first")
    rb = env_cfg.get("rebuild") or {}
    new_py = str(Path(st["new_dir"]) / "bin" / "python")
    old_py = str(Path(st["old_dir"]) / "bin" / "python")
    res: dict = {"ts": _now()}
    r = run([UV, "pip", "check", "--python", new_py], capture_output=True, text=True, timeout=300)
    res["pip_check"] = {"ok": r.returncode == 0, "out": ((r.stdout or "") + (r.stderr or ""))[-1200:]}
    dump = probe(new_py, env_cfg.get("entries") or [])
    a = env_roots.analyze(env, env_cfg, dump, {})
    res["drift"] = a["drift"]
    res["orphans"] = [o["package"] for o in a["orphans"]]
    res["unmapped_imports"] = a["unmapped_imports"]
    present = [x for x in (rb.get("exclude") or []) if env_roots.canon(x) in dump["dists"]]
    res["excluded_present"] = present
    need = node_types(workflows(rb))
    res["workflows"] = sorted({w for ws in need.values() for w in ws})
    if rb.get("app_dir"):
        new_nodes = node_set(new_py, rb, port=ports[0])
        old_nodes = node_set(old_py, rb, port=ports[1])
        ns, os_ = set(new_nodes["nodes"]), set(old_nodes["nodes"])
        res["nodes"] = {"new": len(ns), "old": len(os_), "new_err": new_nodes["err"], "old_err": old_nodes["err"],
                        "new_import_failed": new_nodes["import_failed"],
                        "old_import_failed": old_nodes["import_failed"],
                        "missing_for_workflows": sorted(t for t in need if t not in ns),
                        "lost_vs_old": sorted(os_ - ns), "gained_vs_old": sorted(ns - os_)[:50],
                        "log_tail": new_nodes.get("log_tail") or ""}
    res["patches"] = patch_state(Path(st["new_dir"]), rb) if rb.get("patches") else []
    problems = []
    if not res["pip_check"]["ok"]:
        problems.append("uv pip check")
    if PATCHES_ON:
        problems += [f"local patch not in place: {p['file']} is {p['state']}"
                     for p in res["patches"] if p["state"] != "patched"]
    if res["drift"]:
        problems.append(f"{len(res['drift'])} root-pin drift")
    if present:
        problems.append(f"excluded present: {present}")
    n = res.get("nodes") or {}
    if n.get("new_err"):
        problems.append(f"app did not start on the CPU: {n['new_err']}")
    if n.get("missing_for_workflows"):
        problems.append(f"nodes missing for the workflows: {n['missing_for_workflows']}")
    if n.get("new_import_failed"):
        problems.append(f"custom node import failed: {n['new_import_failed']}")
    if n.get("lost_vs_old"):
        problems.append(f"{len(n['lost_vs_old'])} nodes the old env had")
    res["problems"] = problems
    res["verdict"] = "pass" if not problems else "fail"
    st["steps"]["check"] = res
    save_state(st)
    return st



def serve_node_args(rb: dict) -> list:
    ""




    if os.environ.get("MS_REBUILD_SERVE_WHITELIST", "1") == "0":
        return []
    nodes = list(rb.get("custom_nodes") or [])
    bad = [n for n in nodes if "manager" in n.lower()]
    if bad:
        raise Refused(f"custom_nodes names a manager ({', '.join(bad)}) - it installs packages into the env")
    return ["--disable-all-custom-nodes", *(["--whitelist-custom-nodes", *nodes] if nodes else [])]


def serve(env: str, ver: str, env_cfg: dict, *, which: str, port: int = 8188,
          popen=subprocess.Popen, scope: list | None = None, wait_s: float = 600.0) -> dict:
    ""

    st = load_state(env, ver)
    rb = env_cfg.get("rebuild") or {}
    if gpu_mode() != "render":
        raise Refused(f"gpu mode is {gpu_mode() or 'unset'} - serve runs in a Render window only")
    for p in {8188, port}:
        if comfy_answers(p):
            raise Refused(f"a Comfy already answers on :{p} - stop it first (one GPU app at a time)")
    d = Path(st["new_dir"] if which == "new" else st["old_dir"])
    py = str(d / "bin" / "python")
    app = Path(rb["app_dir"])
    cmd = [*(SCOPE if scope is None else scope), py, str(app / "main.py"), "--port", str(port),
           "--listen", "127.0.0.1", *serve_node_args(rb), *(rb.get("serve_args") or [])]
    envv = dict(os.environ, VIRTUAL_ENV=str(d), PATH=f"{d / 'bin'}:{os.environ.get('PATH', '')}",
                HF_HOME=os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")), PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    logp = state_dir(env, ver) / f"serve-{which}.log"
    logp.parent.mkdir(parents=True, exist_ok=True)
    p = popen(cmd, cwd=str(app), env=envv, stdout=logp.open("ab"), stderr=subprocess.STDOUT, start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < wait_s and not comfy_answers(port):
        if p.poll() is not None:
            raise Refused(f"the {which} env's app exited rc={p.returncode} - see {logp}")
        time.sleep(2)
    st.setdefault("serving", {})[which] = {"pid": p.pid, "port": port, "ts": _now(), "up": comfy_answers(port)}
    save_state(st)
    return st


MODE_BIN = Path(os.environ.get("MS_MODE_BIN") or _KH / "bin" / "gpu-mode")


def stop_machine_comfy() -> bool:
    ""

    if not MODE_BIN.is_file():
        raise Refused(f"no GPU mode script at {MODE_BIN} (MS_MODE_BIN): it must define stop_comfy()")
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("mode_for_rebuild", str(MODE_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    hm = importlib.util.module_from_spec(spec)
    loader.exec_module(hm)
    return bool(hm.stop_comfy())


def stop_machine(env: str, ver: str, *, stopper=stop_machine_comfy) -> dict:
    ""

    if gpu_mode() != "render":
        raise Refused("stop --which old runs in a Render window only")
    st = load_state(env, ver)
    ok = stopper()
    st.setdefault("steps", {}).setdefault("stops", []).append({"ts": _now(), "app_comfy_down": ok})
    save_state(st)
    if not ok:
        raise Refused("the machine's Comfy is still answering on :8188")
    return st


def stop_served(env: str, ver: str, *, killer=os.killpg) -> dict:
    st = load_state(env, ver)
    for which, s in list((st.get("serving") or {}).items()):
        try:
            killer(os.getpgid(s["pid"]), 15)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        st["serving"].pop(which, None)
    save_state(st)
    return st


def renders(env: str, ver: str, env_cfg: dict, *, label: str, port: int = 8188,
            timeout_s: float = 3600.0, client=None) -> dict:
    ""

    import sys as _sys
    _crew_lib = str(Path(__file__).resolve().parents[2] / "crew" / "lib")
    if _crew_lib not in _sys.path:
        _sys.path.append(_crew_lib)
    import render_wip
    st = load_state(env, ver)
    rb = env_cfg.get("rebuild") or {}
    base = f"http://127.0.0.1:{port}"
    client = client or {"post": lambda path, body: http_json(base + path, data=body, timeout=30),
                        "get": lambda path: http_json(base + path, timeout=30),
                        "raw": lambda path: urllib.request.urlopen(base + path, timeout=120).read()}
    out_root = state_dir(env, ver) / "renders" / label
    results = {}
    for name, graph in workflows(rb).items():
        t0 = time.time()
        rec = {"ok": False, "files": []}
        try:
            r = client["post"]("/prompt", render_wip.prompt_body(graph))
            pid = r["prompt_id"]
            while time.time() - t0 < timeout_s:
                h = (client["get"](f"/history/{pid}") or {}).get(pid)
                if h and (h.get("status") or {}).get("completed") is not None:
                    break
                time.sleep(3)
            else:
                raise TimeoutError(f"no result in {timeout_s:.0f} s")
            if not (h.get("status") or {}).get("completed"):
                raise RuntimeError(f"status {h.get('status')}")
            dest = out_root / Path(name).stem
            dest.mkdir(parents=True, exist_ok=True)
            for node_id in sorted(h.get("outputs") or {}, key=str):
                o = h["outputs"][node_id]
                for kind in ("images", "gifs", "videos", "animated"):
                    for item in o.get(kind) or []:
                        if not isinstance(item, dict) or "filename" not in item:
                            continue
                        q = urllib.parse.urlencode({k: item.get(k, "") for k in ("filename", "subfolder", "type")})
                        f = dest / f"{node_id}-{item['filename']}"
                        f.write_bytes(client["raw"](f"/view?{q}"))
                        rec["files"].append(str(f))
            rec["ok"] = bool(rec["files"])
        except Exception as e:
            rec["err"] = repr(e)[:400]
        rec["seconds"] = round(time.time() - t0)
        results[name] = rec
    st.setdefault("steps", {}).setdefault("renders", {})[label] = {"ts": _now(), "port": port, "results": results}
    save_state(st)
    return st


def _frames(path: Path, n: int = 3) -> list:
    ""
    import numpy as np
    from PIL import Image
    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        return [np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)]
    tmp = Path(tempfile.mkdtemp(prefix="ms-rebuild-frames-"))
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "fps=2", "-frames:v", "64",
                        str(tmp / "f%03d.png")], check=True, timeout=300)
        fs = sorted(tmp.glob("f*.png"))
        pick = [fs[0], fs[len(fs) // 2], fs[-1]] if len(fs) >= 3 else fs
        return [np.asarray(Image.open(f).convert("RGB"), dtype=np.float32) for f in pick[:n]]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def psnr(a, b) -> float:
    import numpy as np
    if a.shape != b.shape:
        return 0.0
    mse = float(np.mean((a - b) ** 2))
    return 99.0 if mse == 0 else round(10 * np.log10(255.0 ** 2 / mse), 2)


def compare(env: str, ver: str, *, old: str = "old", new: str = "new", frames=_frames) -> dict:
    st = load_state(env, ver)
    rr = (st.get("steps") or {}).get("renders") or {}
    if old not in rr or new not in rr:
        raise Refused(f"renders for both '{old}' and '{new}' first")
    rows, problems, review = {}, [], []
    for name, o in rr[old]["results"].items():
        n = rr[new]["results"].get(name) or {}
        if not o.get("ok") or not n.get("ok") or len(o["files"]) != len(n["files"]):
            rows[name] = {"verdict": "fail", "why": f"old ok={o.get('ok')} new ok={n.get('ok')} "
                                                   f"files {len(o.get('files') or [])}/{len(n.get('files') or [])}"}
            problems.append(name)
            continue
        worst = 99.0
        for fo, fn in zip(o["files"], n["files"]):
            for a, b in zip(frames(Path(fo)), frames(Path(fn))):
                worst = min(worst, psnr(a, b))
        v = "pass" if worst >= PSNR_PASS else "review"
        rows[name] = {"verdict": v, "min_psnr": worst}
        if v == "review":
            review.append(name)
    verdict = "fail" if problems else ("review" if review else "pass")
    st["steps"]["compare"] = {"ts": _now(), "verdict": verdict, "rows": rows, "psnr_pass": PSNR_PASS,
                              "review": review, "failed": problems}
    save_state(st)
    return st



HOLD_LOG = Path(os.environ.get("MS_ENV_HOLD_LOG", str(OUT_ROOT / "env-hold.jsonl")))


def hold_event(env: str, action: str, row: dict | None, *, by: str, why: str = "") -> dict:
    ""



    ev = {"ts": _now(), "env": env, "action": action, "row": row, "by": by, "why": why}
    HOLD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with HOLD_LOG.open("a") as f:
        f.write(json.dumps(ev) + "\n")
    return ev


def _hold_rows() -> dict:
    try:
        return json.loads(HOLD_FILE.read_text())
    except FileNotFoundError:
        return {}


def flip(env: str, ver: str, env_cfg: dict, *, yes: bool, accept_review: bool = False,
         app_running: Callable[[], bool] | None = None, today: dt.date | None = None) -> dict:
    st = load_state(env, ver)
    rb = env_cfg.get("rebuild") or {}
    if not yes:
        raise Refused("flip needs --yes")
    if (st["steps"].get("check") or {}).get("verdict") != "pass":
        raise Refused("check has not passed")
    if rb.get("first_start") and not (st["steps"].get("first_start") or {}).get("ok"):
        raise Refused("the first start has not passed (--step firststart): a lane must never be the first "
                      "to start a new env")
    cv = (st["steps"].get("compare") or {}).get("verdict")
    if cv != "pass" and not (cv == "review" and accept_review):
        raise Refused(f"compare verdict is {cv or 'missing'} (review needs --accept-review on the owner's word)")
    running = (app_running or app_running_check(env, rb))()
    if running:
        raise Refused(f"the app is running ({rb.get('health_url') or 'injected check'}) - flip with it down")
    if st.get("flip") and not st["flip"].get("rolled_back"):
        raise Refused("already flipped")
    live, new = Path(st["live"]), Path(st["new_dir"])
    today = today or dt.date.today()
    keep_until = (today + dt.timedelta(days=int(rb.get("keep_days", 14)))).isoformat()
    rec = {"ts": _now(), "keep_until": keep_until, "retired_paths": [], "hold_row": None}
    st["rebuild"] = {"health_url": rb.get("health_url") or ""}
    if live.is_symlink():
        prev = live.resolve()
        tmp = live.with_name(f".{live.name}.flip")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(new.name)
        os.replace(tmp, live)
        rec.update(prev=str(prev), prev_was="symlink")
    else:
        prev = live.with_name(f"{live.name}-inplace-{today.strftime('%Y%m%d')}")
        if prev.exists():
            raise Refused(f"{prev} exists")
        live.rename(prev)
        live.symlink_to(new.name)
        rec.update(prev=str(prev), prev_was="dir")
    write_marker(prev, of=env, version=(read_marker(prev) or {}).get("version") or "inplace",
                 role="retired", keep_until=keep_until, retired_by=rid(env, ver))
    write_marker(new, role="live", flipped=_now())
    for p in rb.get("retire_paths") or []:
        src = Path(p)
        if src.exists():
            dst = src.with_name(src.name + ".disabled")
            src.rename(dst)
            rec["retired_paths"].append([str(src), str(dst)])
    rows = _hold_rows()
    if env in rows:
        rec["hold_row"] = rows.pop(env)
        _atomic_json(HOLD_FILE, rows)
        rec["hold_events"] = [hold_event(env, "released", rec["hold_row"], by=f"flip {rid(env, ver)}",
                                         why="the rebuild went live")]
    st["flip"] = rec
    save_state(st)
    return st


def rollback(env: str, ver: str, *, yes: bool, app_running: Callable[[], bool] | None = None) -> dict:
    st = load_state(env, ver)
    rec = st.get("flip")
    if not rec or rec.get("rolled_back"):
        raise Refused("nothing flipped")
    if not yes:
        raise Refused("rollback needs --yes")
    rbk = st.get("rebuild") or {}
    if not rbk.get("health_url"):
        try:
            rbk = ((env_roots.load_roots().get("envs") or {}).get(env) or {}).get("rebuild") or {}
        except (OSError, ValueError):
            rbk = {}
    if (app_running or app_running_check(env, rbk))():
        raise Refused("the app is running - roll back with it down")
    live, new, prev = Path(st["live"]), Path(st["new_dir"]), Path(rec["prev"])
    if rec["prev_was"] == "dir":
        live.unlink()
        prev.rename(live)
        back = live
    else:
        tmp = live.with_name(f".{live.name}.flip")
        tmp.symlink_to(prev.name)
        os.replace(tmp, live)
        back = prev
    m = read_marker(back) or {}
    if m.get("version") == "inplace":
        (back / MARKER).unlink(missing_ok=True)
    else:
        write_marker(back, role="live", keep_until="")
    write_marker(new, role="staged")
    for src, dst in rec.get("retired_paths") or []:
        if Path(dst).exists() and not Path(src).exists():
            Path(dst).rename(src)
    if rec.get("hold_row") is not None:
        rows = _hold_rows()
        rows[env] = rec["hold_row"]
        _atomic_json(HOLD_FILE, rows)
        rec.setdefault("hold_events", []).append(
            hold_event(env, "restored", rec["hold_row"], by=f"rollback {rid(env, ver)}", why="the flip was undone"))
    rec["rolled_back"] = _now()
    save_state(st)
    return st


def status(env: str, ver: str) -> dict:
    st = load_state(env, ver)
    new = Path(st["new_dir"])
    return {"id": rid(env, ver), "new_dir": str(new), "exists": new.exists(), "marker": read_marker(new),
            "steps": {k: (v.get("verdict") or v.get("ok")) if isinstance(v, dict) and k != "renders"
                      else sorted(v) for k, v in (st.get("steps") or {}).items()},
            "flip": st.get("flip")}
