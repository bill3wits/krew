"""M&S discovery: what is on THIS machine, read-only, with visible new / removed / not-scanned notes.

Finds systemd units, listening endpoints (and the models they serve), Python envs, NVIDIA GPUs, model caches and
Hermes profiles / plugins / skills. Nothing here changes the machine: it lists, reads files and sends GET requests
to local ports you own.

The pattern is the unit registry's (discover -> merge -> notes): one map file, keyed by (kind, id). A new item gets
a `new` note and a vanished one a `removed` note; notes stay `discovery.note_days` days (default 7). The very first
scan is a baseline, not a flood of "new". Every check that could not run is a NOT SCANNED line with the reason, and
every folder or item looked at and left out is a DROPPED line with the reason. Nothing is left out silently.

Settings: `discovery:` in ms.yaml (see config/ms.example.yaml). Every key optional.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
import krew_hermes as KH  # noqa: E402  where Hermes lives

SCHEMA = "krew_ms_map/v1"
CREW = ("crissa", "lila", "ada", "bash", "steward", "vera", "scout", "judge")
DEFAULTS: dict[str, Any] = {
    "units": {"prefixes": ["hermes-", "krew-"], "exec_markers": []},
    "env_roots": ["~/.venvs", "~/venvs", "~/envs"],
    "ports": {"owners": "self", "probe_timeout_s": 0.8},
    "model_dirs": [],
    "note_days": 7,
}
UNIT_KINDS = (".service", ".timer", ".path", ".socket")
ENABLED_STATES = ("enabled", "enabled-runtime", "linked", "linked-runtime", "alias", "generated")
SHOW_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "Type", "FragmentPath", "User", "ExecStart",
              "TriggeredBy", "Triggers", "UnitFileState", "Description")

Runner = Callable[[list, float], "tuple[int, str, str]"]


def default_run(argv: list, timeout: float = 30.0) -> "tuple[int, str, str]":
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except OSError as e:
        return 127, "", f"{type(e).__name__}: {e}"


def default_probe(url: str, timeout: float) -> "tuple[int, str]":
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                    timeout=timeout) as r:
            return r.status, r.read(65536).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - a closed or non-HTTP port is an answer, not an error
        code = getattr(e, "code", 0)
        return int(code or 0), ""


class Ctx:
    """Everything discovery touches, injectable for tests."""

    def __init__(self, *, run: Runner = default_run, probe=default_probe, home: Optional[Path] = None,
                 uid: Optional[int] = None, env: Optional[dict] = None, which=shutil.which,
                 proc: Optional[Path] = None, user_unit_dir: Optional[Path] = None,
                 system_unit_dir: Path = Path("/etc/systemd/system"), user: Optional[str] = None):
        self.run, self.probe, self.which = run, probe, which
        self.env = dict(os.environ if env is None else env)
        self.proc = Path(proc or self.env.get("KREW_MS_PROC_DIR") or "/proc")   # a container's or a test's /proc
        self.home = Path(home or self.env.get("HOME") or Path.home())
        self.uid = os.getuid() if uid is None else uid
        self.user = user or self.env.get("USER") or self.home.name
        self.user_unit_dir = user_unit_dir or (self.home / ".config" / "systemd" / "user")
        self.system_unit_dir = system_unit_dir

    def expand(self, p: str) -> Path:
        p = str(p)
        if p.startswith("~"):
            p = str(self.home) + p[1:]
        return Path(os.path.expandvars(p))

    def krew_home(self) -> Path:
        return self.expand(self.env.get("KREW_HOME") or "~/.krew")

    def hermes_home(self) -> Path:
        return self.expand(self.env.get("HERMES_HOME") or str(KH.hermes_home(env=self.env)))


def settings(ms_settings=None, override: Optional[dict] = None) -> dict:
    """DEFAULTS deep-merged with ms.yaml `discovery:` (and the trail's own env_roots / retired_envs)."""
    cfg = copy.deepcopy(DEFAULTS)
    src: dict = {}
    if ms_settings is not None:
        src = ms_settings.get("discovery") or {}
        cfg["settings_env_roots"] = [str(p) for p in ms_settings.get("env_roots") or []]
        cfg["retired_envs"] = list(ms_settings.get("retired_envs") or [])
    for k, v in {**src, **(override or {})}.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return cfg


class Found:
    def __init__(self):
        self.items: list[dict] = []
        self.not_scanned: list[dict] = []
        self.dropped: list[dict] = []
        self.lines: list[str] = []

    def add(self, kind: str, id_: str, **attrs) -> dict:
        row = {"kind": kind, "id": id_, **attrs}
        self.items.append(row)
        return row

    def skip(self, what: str, why: str) -> None:
        self.not_scanned.append({"what": what, "why": why})

    def drop(self, item: str, why: str) -> None:
        self.dropped.append({"item": item, "why": why})


# ---- systemd units (the unit registry's rules, with your markers) --------------------------------------------
def _parse_show(text: str) -> list:
    blocks, cur = [], {}
    for ln in text.splitlines():
        if not ln.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        k, _, v = ln.partition("=")
        cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def _sc(scope: str) -> list:
    return ["systemctl", "--user"] if scope == "user" else ["systemctl", "--no-ask-password"]


def _first_line(*texts: str) -> str:
    for t in texts:
        for ln in (t or "").splitlines():
            if ln.strip():
                return ln.strip()
    return "no output"


def unit_reason(scope: str, name: str, p: dict, ctx: Ctx, cfg: dict) -> str:
    prefixes = tuple((cfg.get("units") or {}).get("prefixes") or ())
    if prefixes and name.startswith(prefixes):
        return "name " + next(x for x in prefixes if name.startswith(x)) + "*"
    markers = list((cfg.get("units") or {}).get("exec_markers") or []) or \
        [str(ctx.home), str(ctx.krew_home()), str(ctx.hermes_home())]
    ex = p.get("ExecStart") or ""
    for m in markers:
        m = str(ctx.expand(m))
        if m and m in ex:
            return f"exec into {m}"
    frag = p.get("FragmentPath") or ""
    if scope == "user" and frag.startswith(str(ctx.user_unit_dir) + "/"):
        return f"installed in {ctx.user_unit_dir}"
    if scope == "system" and frag.startswith(str(ctx.system_unit_dir) + "/") and (p.get("User") or "") == ctx.user:
        return f"system unit run as {ctx.user}"
    return ""


def _role(name: str, p: dict) -> str:
    if (p.get("LoadState") or "") == "masked":
        return "masked"
    for k in (".timer", ".path", ".socket"):
        if name.endswith(k):
            return k[1:]
    if (p.get("TriggeredBy") or "").strip():
        return "job"
    if (p.get("UnitFileState") or "") in ENABLED_STATES:
        return "service"
    if (p.get("ActiveState") or "") in ("active", "activating", "reloading"):
        return "service"
    return "manual"


def exec_python(execstart: str) -> str:
    """The interpreter a unit runs, when it is a python: `{ path=/x/bin/python3 ; argv[]=... }` or a plain line."""
    m = re.search(r"path=(\S+)", execstart or "") or re.match(r"\s*(\S+)", execstart or "")
    p = m.group(1) if m else ""
    return p if re.search(r"/bin/python[0-9.]*$", p) else ""


def discover_units(ctx: Ctx, cfg: dict, f: Found) -> None:
    if not ctx.which("systemctl"):
        f.skip("systemd units", "systemctl is not installed (no systemd on this machine)")
        return
    for scope in ("system", "user"):
        rc, txt, err = ctx.run(_sc(scope) + ["list-unit-files", "--no-legend", "--plain", "--no-pager",
                                             "--type=service,timer,path,socket"], 30.0)
        if rc != 0:
            f.skip(f"systemd units ({scope})", f"systemctl list-unit-files: {_first_line(err, txt)}")
            continue
        names = sorted({ln.split()[0] for ln in txt.splitlines() if ln.split() and ln.split()[0].endswith(UNIT_KINDS)
                        and "@." not in ln.split()[0]})
        templates = sorted({ln.split()[0] for ln in txt.splitlines() if ln.split() and "@." in ln.split()[0]})
        props: dict = {}
        for i in range(0, len(names), 150):
            part = names[i:i + 150]
            rc2, out, err2 = ctx.run(_sc(scope) + ["show"] + [f"-p{p}" for p in SHOW_PROPS] + part, 60.0)
            if rc2 != 0 and not out:
                f.skip(f"systemd unit details ({scope}, {len(part)} units)", f"systemctl show: {_first_line(err2)}")
            for asked, b in zip(part, _parse_show(out)):
                props[asked] = b
        whys = {n: unit_reason(scope, n, props.get(n) or {}, ctx, cfg) for n in names}
        for n in names:
            if not whys[n] and n.endswith((".timer", ".path", ".socket")):
                hit = [t for t in (props.get(n) or {}).get("Triggers", "").split() if whys.get(t)]
                if hit:
                    whys[n] = f"starts {hit[0]}"
        mine = 0
        for n in names:
            p = props.get(n) or {}
            if not whys[n]:
                continue
            if (p.get("LoadState") or "") == "not-found":
                f.drop(f"{scope}:{n}", "unit file listed but systemd cannot load it (not-found)")
                continue
            mine += 1
            f.add("unit", f"{scope}:{p.get('Id') or n}", scope=scope, why=whys[n], role=_role(n, p),
                  active=p.get("ActiveState") or "unknown", sub=p.get("SubState") or "",
                  file_state=p.get("UnitFileState") or "", python=exec_python(p.get("ExecStart") or ""),
                  description=p.get("Description") or "")
        for t in templates:
            if unit_reason(scope, t, {}, ctx, cfg):
                f.drop(f"{scope}:{t}", "template unit (instances are listed when they run)")
        f.lines.append(f"units ({scope}): {mine} yours of {len(names)} unit files")


# ---- listening sockets -> endpoints -> served models -----------------------------------------------------------
def _hex_addr(h: str) -> str:
    if len(h) == 8:
        return socket.inet_ntop(socket.AF_INET, bytes.fromhex(h)[::-1])
    b = bytes.fromhex(h)
    return socket.inet_ntop(socket.AF_INET6, b"".join(b[i:i + 4][::-1] for i in range(0, 16, 4)))


def listeners(ctx: Ctx, f: Found) -> list[dict]:
    out, read_any = [], False
    for fam in ("tcp", "tcp6"):
        p = ctx.proc / "net" / fam
        try:
            text = p.read_text()
        except OSError as e:
            f.skip(f"listening sockets ({fam})", f"cannot read {p}: {e.__class__.__name__}")
            continue
        read_any = True
        for ln in text.splitlines()[1:]:
            parts = ln.split()
            if len(parts) < 10 or parts[3] != "0A":
                continue
            addr, port = parts[1].split(":")
            out.append({"addr": _hex_addr(addr), "port": int(port, 16), "uid": int(parts[7]), "inode": parts[9]})
    if not read_any:
        return []
    return out


def socket_owners(ctx: Ctx, inodes: set) -> dict:
    """inode -> (pid, exe, cmdline) for YOUR processes (others' /proc fds are not readable, by design)."""
    found = {}
    try:
        pids = [d for d in ctx.proc.iterdir() if d.name.isdigit()]
    except OSError:
        return found
    for d in pids:
        try:
            for fd in (d / "fd").iterdir():
                m = re.match(r"socket:\[(\d+)\]", os.readlink(fd))
                if m and m.group(1) in inodes:
                    exe = os.readlink(d / "exe") if (d / "exe").exists() else ""
                    cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                    found[m.group(1)] = (int(d.name), exe, cmd)
        except OSError:
            continue
    return found


def discover_endpoints(ctx: Ctx, cfg: dict, f: Found) -> None:
    pc = cfg.get("ports") or {}
    owners = str(pc.get("owners") or "self").lower()
    timeout = float(pc.get("probe_timeout_s") or 0.8)
    ls = listeners(ctx, f)
    mine = [x for x in ls if owners == "all" or x["uid"] == ctx.uid]
    others = sorted({x["port"] for x in ls if x not in mine})
    if others:
        f.skip(f"{len(others)} listening port(s) owned by other users",
               f"not probed (discovery.ports.owners: {owners}): ports {', '.join(map(str, others))}")
    procs = socket_owners(ctx, {x["inode"] for x in mine})
    seen = set()
    for x in sorted(mine, key=lambda r: (r["port"], r["addr"])):
        if x["port"] in seen:
            continue
        seen.add(x["port"])
        a = x["addr"]
        if a in ("0.0.0.0", "::") or a.startswith("127.") or a == "::1":
            host = "127.0.0.1" if ":" not in a or a == "::" else "[::1]"
        else:
            host = a if ":" not in a else f"[{a}]"
        base = f"http://{host}:{x['port']}"
        api, models = "tcp", []
        st, body = ctx.probe(base + "/v1/models", timeout)
        if st == 200:
            try:
                models = [str(m.get("id")) for m in (json.loads(body).get("data") or []) if isinstance(m, dict)]
                api = "openai"
            except (ValueError, AttributeError):
                api = "http"
        if api == "tcp":
            for path, name in (("/health", "health"), ("/system_stats", "comfy"), ("/", "http")):
                st, _ = ctx.probe(base + path, timeout)
                if st and st < 500:
                    api = name if st == 200 else "http"
                    break
        pid, exe, cmd = procs.get(x["inode"], (None, "", ""))
        f.add("endpoint", f"{host}:{x['port']}", bind=a, api=api, models=models, pid=pid, exe=exe, cmd=cmd)
        for m in models:
            f.add("model", f"served:{m}@{host}:{x['port']}", source="endpoint", endpoint=base)
    f.lines.append(f"endpoints: {len(seen)} listening port(s) of yours probed")


# ---- Python envs ----------------------------------------------------------------------------------------------
def _env_python(d: Path) -> Optional[Path]:
    for c in (d / "bin" / "python", d / ".venv" / "bin" / "python", d / "bin" / "python3"):
        if c.exists():
            return c
    return None


def hermes_bin(ctx: Ctx) -> str:
    for k in ("KREW_HERMES_BIN", "HERMES_BIN"):
        if ctx.env.get(k):
            return ctx.env[k]
    try:
        txt = (ctx.krew_home() / "krew.yaml").read_text(encoding="utf-8")
        m = re.search(r"^hermes_bin:\s*['\"]?([^'\"#\n]+)", txt, re.M)
        if m and m.group(1).strip():
            return str(ctx.expand(m.group(1).strip()))
    except OSError:
        pass
    return ctx.which("hermes") or str(ctx.home / ".local" / "bin" / "hermes")


def _shebang_python(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            first = fh.readline(400).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    if not first.startswith("#!"):
        return ""
    parts = first[2:].split()
    if parts and parts[0].endswith("/env") and len(parts) > 1:
        return ""
    return parts[0] if parts and re.search(r"/bin/python[0-9.]*$", parts[0]) else ""


def _env_dir(py: str) -> Path:
    d = Path(py).parent.parent
    return d.parent if d.name == ".venv" else d


def discover_envs(ctx: Ctx, cfg: dict, f: Found) -> None:
    retired = set(cfg.get("retired_envs") or [])
    roots = list(dict.fromkeys([*(cfg.get("env_roots") or []), *(cfg.get("settings_env_roots") or [])]))
    envs: dict[str, dict] = {}
    real_seen: dict[str, str] = {}

    def keep(name: str, py: Path, how: str) -> None:
        real = str(_env_dir(str(py)).resolve())
        if real in real_seen:
            if real_seen[real] != name:
                f.drop(f"env {name} ({py})", f"same env as {real_seen[real]}")
            return
        base, n = name, 2
        while name in envs:
            name, n = f"{base}-{n}", n + 1
        real_seen[real] = name
        rc, out, _ = ctx.run([str(py), "-c", "import sys; print(sys.version.split()[0])"], 15.0)
        envs[name] = {"python": str(py), "how": how, "version": out.strip() if rc == 0 else ""}
        if rc != 0:
            envs[name]["version_error"] = f"{py} did not start (rc {rc})"

    for r in roots:
        root = ctx.expand(r)
        if not root.is_dir():
            f.skip(f"env root {root}", "folder does not exist")
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                f.drop(str(d), "not a folder")
            elif re.search(r"-bak(-|$)|-pip-bak|\.broken\.", d.name):
                f.drop(str(d), "backup or broken leftover (name has -bak, -pip-bak or .broken.)")
            elif d.name in retired:
                f.drop(str(d), "listed in retired_envs")
            elif not _env_python(d):
                f.drop(str(d), "no bin/python or .venv/bin/python")
            else:
                keep(d.name, _env_python(d), f"under env root {root}")
    hb = hermes_bin(ctx)
    if Path(hb).is_file():
        py = _shebang_python(hb)
        if py and Path(py).exists():
            keep("hermes", Path(py), f"the hermes command ({hb})")
        else:
            f.drop(f"hermes command {hb}", "no python interpreter in its first line")
    else:
        f.skip("the Hermes env", f"no hermes command at {hb}")
    for it in [i for i in f.items if i.get("python")]:
        if Path(it["python"]).exists():
            keep(_env_dir(it["python"]).name, Path(it["python"]), f"runs {it['kind']} {it['id']}")
        else:
            f.drop(f"{it['kind']} {it['id']} python {it['python']}", "interpreter does not exist")
    for it in [i for i in f.items if i["kind"] == "endpoint" and i.get("exe")]:
        exe = it["exe"]
        if re.search(r"/bin/python[0-9.]*$", exe) and "/usr/" not in exe[:5]:
            cmd0 = (it.get("cmd") or "").split()[:1]
            py = cmd0[0] if cmd0 and re.search(r"/bin/python[0-9.]*$", cmd0[0]) else exe
            keep(_env_dir(py).name, Path(py), f"serves endpoint {it['id']}")
    for name, row in envs.items():
        f.add("env", name, **row)
    f.lines.append(f"python envs: {len(envs)}")


# ---- GPUs -----------------------------------------------------------------------------------------------------
def discover_gpus(ctx: Ctx, cfg: dict, f: Found) -> None:
    smi = ctx.which("nvidia-smi")
    if not smi:
        f.lines.append("GPUs: no NVIDIA GPU found (nvidia-smi is not installed). AMD and Apple GPUs are not checked yet.")
        return
    rc, out, err = ctx.run([smi, "--query-gpu=index,name,memory.total,memory.used,driver_version",
                            "--format=csv,noheader,nounits"], 20.0)
    rows = [[c.strip() for c in ln.split(",")] for ln in out.splitlines() if ln.strip()] if rc == 0 else []
    if not rows:
        f.lines.append(f"GPUs: no NVIDIA GPU found (nvidia-smi: {_first_line(err, out)}).")
        return
    for r in rows:
        if len(r) >= 5:
            f.add("gpu", f"nvidia:{r[0]}", name=r[1], memory_total_mib=r[2], memory_used_mib=r[3], driver=r[4])
    f.lines.append(f"GPUs: {len(rows)} NVIDIA ({', '.join(r[1] for r in rows if len(r) > 1)})")


# ---- model caches ---------------------------------------------------------------------------------------------
def discover_models(ctx: Ctx, cfg: dict, f: Found) -> None:
    dirs = []
    if ctx.env.get("HF_HUB_CACHE"):
        dirs.append(ctx.expand(ctx.env["HF_HUB_CACHE"]))
    if ctx.env.get("HF_HOME"):
        dirs.append(ctx.expand(ctx.env["HF_HOME"]) / "hub")
    dirs.append(ctx.home / ".cache" / "huggingface" / "hub")
    dirs += [ctx.expand(d) for d in cfg.get("model_dirs") or []]
    n = 0
    for d in dict.fromkeys(dirs):
        if not d.is_dir():
            f.skip(f"model cache {d}", "folder does not exist")
            continue
        for m in sorted(d.iterdir()):
            if m.is_dir() and m.name.startswith("models--"):
                f.add("model", "hf:" + m.name[len("models--"):].replace("--", "/", 1), source="hf-cache", path=str(m))
                n += 1
            elif m.name not in (".locks", "version.txt", ".gitignore") and not m.name.startswith(("datasets--", "spaces--")):
                f.drop(str(m), "not a model folder (models--org--name)")
    ol = ctx.which("ollama")
    if not ol:
        f.skip("ollama models", "ollama is not installed")
    else:
        rc, out, err = ctx.run([ol, "list"], 20.0)
        if rc != 0:
            f.skip("ollama models", f"ollama list: {_first_line(err, out)}")
        else:
            for ln in out.splitlines()[1:]:
                if ln.split():
                    f.add("model", "ollama:" + ln.split()[0], source="ollama")
                    n += 1
    served = sum(1 for i in f.items if i["kind"] == "model" and i.get("source") == "endpoint")
    f.lines.append(f"models: {n} on disk, {served} served")


# ---- Hermes -------------------------------------------------------------------------------------------------
def discover_hermes(ctx: Ctx, cfg: dict, f: Found) -> None:
    hh = ctx.hermes_home()
    if not hh.is_dir():
        f.skip("Hermes profiles, plugins and skills", f"no Hermes home at {hh}")
        return
    profiles = [("default", hh)] + [(p.name, p) for p in sorted((hh / "profiles").glob("*")) if p.is_dir()]
    for name, p in profiles:
        f.add("hermes_profile", name, soul=(p / "SOUL.md").is_file(), config=(p / "config.yaml").is_file(),
              crew=name in CREW, path=str(p))
    missing = [c for c in CREW if not (hh / "profiles" / c).is_dir()]
    plugins = sorted(p.name for p in (hh / "plugins").glob("*") if (p / "plugin.yaml").is_file())
    for p in plugins:
        f.add("hermes_plugin", p, path=str(hh / "plugins" / p))
    for d in sorted((hh / "plugins").glob("*")):
        if d.is_dir() and not (d / "plugin.yaml").is_file():
            f.drop(str(d), "no plugin.yaml (not a plugin)")
    skills = sorted({str(s.parent.relative_to(hh / "skills")) for s in (hh / "skills").rglob("SKILL.md")}) \
        if (hh / "skills").is_dir() else []
    for s in skills:
        f.add("hermes_skill", s)
    f.lines.append(f"Hermes ({hh}): {len(profiles)} profile(s), {len(plugins)} plugin(s), {len(skills)} skill(s)"
                   + (f"; crew profiles not made yet: {', '.join(missing)}" if missing else ""))


STEPS = (("units", discover_units), ("endpoints", discover_endpoints), ("envs", discover_envs),
         ("gpus", discover_gpus), ("models", discover_models), ("hermes", discover_hermes))


def discover(ctx: Optional[Ctx] = None, cfg: Optional[dict] = None) -> Found:
    ctx, cfg = ctx or Ctx(), cfg or settings()
    f = Found()
    for name, step in STEPS:
        try:
            step(ctx, cfg, f)
        except Exception as e:  # noqa: BLE001 - one broken step is a NOT SCANNED line, never a crash
            f.skip(name, f"discovery step failed: {e.__class__.__name__}: {e}")
    return f


# ---- the map file + notes ---------------------------------------------------------------------------------
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def merge(old: dict, f: Found, *, by: str, note_days: int = 7, now: Optional[str] = None) -> "tuple[dict, list]":
    now = now or _now()
    before = {(r["kind"], r["id"]): r for r in old.get("items") or []}
    after, changes = {}, []
    for r in f.items:
        k = (r["kind"], r["id"])
        if k in after:
            continue
        prev = before.get(k)
        row = dict(r, first_seen=(prev or {}).get("first_seen") or now, last_seen=now)
        if prev is None and before:
            row["new_since"] = now
            changes.append({"ts": now, "change": "new", "kind": r["kind"], "id": r["id"], "by": by})
        elif prev is not None and prev.get("new_since"):
            row["new_since"] = prev["new_since"]
        after[k] = row
    for k, prev in before.items():
        if k not in after:
            changes.append({"ts": now, "change": "removed", "kind": prev["kind"], "id": prev["id"],
                            "last_seen": prev.get("last_seen"), "by": by})
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - int(note_days) * 86400))
    kept = [c for c in old.get("changes") or [] if str(c.get("ts") or "") >= cutoff]
    for r in after.values():
        if r.get("new_since") and r["new_since"] < cutoff:
            r.pop("new_since")
    reg = {"schema": SCHEMA, "updated": now, "by": by, "host": socket.gethostname(),
           "baseline": not before, "summary": list(f.lines),
           "items": sorted(after.values(), key=lambda r: (r["kind"], r["id"])),
           "changes": kept + changes, "not_scanned": list(f.not_scanned), "dropped": list(f.dropped)}
    return reg, changes


def map_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "discovery"


def refresh(state_dir: Path, ctx: Optional[Ctx] = None, cfg: Optional[dict] = None, *, by: str = "manual") -> dict:
    cfg = cfg or settings()
    f = discover(ctx, cfg)
    path = map_dir(state_dir) / "map.json"
    reg, changes = merge(_read(path), f, by=by, note_days=int(cfg.get("note_days") or 7))
    _write(path, reg)
    if changes:
        with (path.parent / "changes.jsonl").open("a", encoding="utf-8") as fh:
            for c in changes:
                fh.write(json.dumps(c) + "\n")
    (path.parent / "map.md").write_text(render(reg), encoding="utf-8")
    return reg


def render(reg: dict) -> str:
    out = [f"# M&S map: {reg.get('host')} ({reg.get('updated')})", ""]
    out += [f"- {ln}" for ln in reg.get("summary") or []]
    if reg.get("baseline"):
        out += ["", "First scan on this machine: a baseline (from now on, changes get NEW / REMOVED notes)."]
    recent = reg.get("changes") or []
    out += ["", f"NEW / REMOVED (last notes): {len(recent) or 'none'}"]
    out += [f"- {c['change'].upper()} {c['kind']} {c['id']} ({c['ts']})" for c in recent]
    out += ["", f"NOT SCANNED: {len(reg.get('not_scanned') or []) or 'none'}"]
    out += [f"- {n['what']}: {n['why']}" for n in reg.get("not_scanned") or []]
    out += ["", f"DROPPED (looked at, left out): {len(reg.get('dropped') or []) or 'none'}"]
    out += [f"- {d['item']}: {d['why']}" for d in reg.get("dropped") or []]
    kinds: dict[str, list] = {}
    for it in reg.get("items") or []:
        kinds.setdefault(it["kind"], []).append(it)
    for k, rows in kinds.items():
        out += ["", f"## {k} ({len(rows)})"]
        for r in rows:
            extra = {x: r[x] for x in ("api", "models", "python", "version", "why", "active", "name", "crew", "how")
                     if r.get(x) not in (None, "", [], False)}
            out.append(f"- {r['id']}" + (f"  {json.dumps(extra, ensure_ascii=False)}" if extra else "")
                       + ("  NEW" if r.get("new_since") else ""))
    return "\n".join(out) + "\n"
