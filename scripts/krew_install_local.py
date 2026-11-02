"""krew's local services: long memory (Hindsight + its vector store) and web search (SearXNG), all on this machine.

The User 2026-11-01: "EVERYthing we run is local. Very important. The data not so much as the structure and we need to
ship on installation as is all local." Every pin is in crew/local-services.json; this file reads nothing else.

    memory(...)   the vendored Hermes plugin (crew/hindsight/plugin/hindsight, never fetched) + its pip pin in Hermes's
                  runtime, $HERMES_HOME/hindsight/config.json, a user-space venv with hindsight-api (CPU torch), the
                  embedding model fetched once into the shared HF home, the vector store (pg0: PostgreSQL + pgvector,
                  no sudo) and the two units krew-memory-db + krew-hindsight
    search(...)   SearXNG at its pinned commit (sha-checked, AGPL: fetched, never vendored), a venv, a loopback
                  settings.yml with its own secret, krew-searxng.service, and every profile's web search pointed at it
                  with Hermes's keyless cloud ring off

Each step is idempotent: what is already in place and matches its pin is kept and said. A scratch install (another
--hermes-home / --krew-home) writes its units to <krew home>/systemd-user and never enables them; an install into this
account's own homes runs `systemctl --user daemon-reload` and enables + starts them. `run` is injectable (tests pass a
recorder; nothing here calls systemd-run).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PINS = REPO / "crew" / "local-services.json"
UNITS = REPO / "crew" / "units"
NO_FETCH = "KREW_LOCAL_NO_FETCH"        # tests: a fetch is an error, never a download


def pins(path: Path | None = None) -> dict:
    """crew/local-services.json, or the file KREW_LOCAL_SERVICES names (a proof on other ports)."""
    alt = os.environ.get("KREW_LOCAL_SERVICES", "")
    return json.loads((path or (Path(alt) if alt else PINS)).read_text(encoding="utf-8"))


def tree_sha(root: Path, skip: tuple = ("LICENSE",)) -> str:
    """sha256 over sorted 'path\\0sha256(file)\\n' of every file under root (caches and `skip` names left out)."""
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file() and x.name not in skip and "__pycache__" not in x.parts):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).hexdigest().encode() + b"\n")
    return h.hexdigest()


def expand(s: str, env: dict | None = None) -> Path:
    """${VAR:-default} and ~ the way a shell would, against `env` (default os.environ)."""
    env = os.environ if env is None else env
    s = re.sub(r"\$\{(\w+):-([^}]*)\}", lambda m: env.get(m.group(1)) or m.group(2), s)
    s = re.sub(r"\$\{?(\w+)\}?", lambda m: env.get(m.group(1), ""), s)
    return Path(os.path.expanduser(s))


REAL_SWITCH = "KREW_LOCAL_REAL"         # =1: a test may reach the real venv / pip / download / systemctl


def under_test(env: dict | None = None) -> bool:
    """A test (pytest's own marker in the environment) never reaches a real download, pip, venv or systemctl through
    these steps: the steps' own tests pass a recorder; an install a test runs (init) gets the local file steps only."""
    env = os.environ if env is None else env
    return bool(env.get("PYTEST_CURRENT_TEST")) and env.get(REAL_SWITCH) != "1"


def default_run(cmd, env=None, cwd=None):
    if under_test():
        raise RuntimeError(f"under a test, a real {Path(str(cmd[0])).name} is refused ({REAL_SWITCH}=1 allows)")
    return subprocess.run([str(c) for c in cmd], env=env, cwd=cwd, text=True, capture_output=True)


def _tail(r) -> str:
    return ((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-1:][0][:300] if (r.stderr or r.stdout) else ""


# ---- python + venvs -------------------------------------------------------------------------------------------

def find_python(want: str, minimum: str, which=shutil.which) -> str | None:
    """python3.<want>, else the newest python3.X on PATH at `minimum` or newer, else python3 if new enough."""
    lo = tuple(int(x) for x in minimum.split("."))
    names = [f"python{want}"] + [f"python3.{m}" for m in range(20, lo[1] - 1, -1)]
    for n in names:
        p = which(n)
        if p:
            return p
    return None


def make_venv(dest: Path, py: dict, say, *, run, dry: bool, which=shutil.which) -> tuple[Path | None, str]:
    """(dest/bin/python, how). uv when present (it fetches `want` once); else a python3 at `min` or newer."""
    exe = dest / "bin" / "python"
    if exe.exists():
        return exe, "kept"
    uv = which("uv") or (str(Path("~/.local/bin/uv").expanduser()) if Path("~/.local/bin/uv").expanduser().exists() else None)
    if uv:
        cmd = [uv, "venv", "--python", py["want"], "--seed", dest]
    else:
        base = find_python(py["want"], py["min"], which)
        if not base:
            return None, f"no uv and no python3 >= {py['min']} on PATH"
        cmd = [base, "-m", "venv", dest]
    say("venv     " + " ".join(str(c) for c in cmd))
    if dry:
        return exe, "dry"
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = run(cmd)
    return (exe, "made") if r.returncode == 0 else (None, f"venv: exit {r.returncode}: {_tail(r)}")


def pip(py: Path, reqs: list, say, *, run, dry: bool, index: str = "", which=shutil.which) -> tuple[bool, str]:
    uv = which("uv") or (str(Path("~/.local/bin/uv").expanduser()) if Path("~/.local/bin/uv").expanduser().exists() else None)
    cmd = ([uv, "pip", "install", "--python", py] if uv else [py, "-m", "pip", "install"]) + list(reqs)
    if index:
        cmd += ["--index-url", index]
    say("pip      " + " ".join(str(c) for c in reqs) + (f" (from {index})" if index else ""))
    if dry:
        return True, "dry"
    r = run(cmd)
    return r.returncode == 0, "" if r.returncode == 0 else f"exit {r.returncode}: {_tail(r)}"


# ---- fetch -----------------------------------------------------------------------------------------------------

def fetch(url: str, dest: Path, sha256: str = "", *, opener=urllib.request.urlopen) -> str:
    """Download once (kept when its sha matches); returns the file's sha256. A mismatch removes nothing it did not write."""
    if dest.exists() and (not sha256 or hashlib.sha256(dest.read_bytes()).hexdigest() == sha256):
        return hashlib.sha256(dest.read_bytes()).hexdigest()
    if os.environ.get(NO_FETCH) == "1":
        raise RuntimeError(f"fetch refused ({NO_FETCH}=1): {url}")
    if opener is urllib.request.urlopen and under_test():
        raise RuntimeError(f"under a test, a real download is refused ({REAL_SWITCH}=1 allows): {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with opener(url, timeout=300) as r:
        body = r.read()
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(body)
    os.replace(tmp, dest)
    return hashlib.sha256(body).hexdigest()


def extract(tgz: Path, dest: Path) -> Path:
    """The archive's single top folder, unpacked under dest with the safe filter (no links out, no device files)."""
    with tarfile.open(tgz) as t:
        tops = {m.name.split("/")[0] for m in t.getmembers()}
        if len(tops) != 1:
            raise RuntimeError(f"{tgz.name}: {len(tops)} top folders, expected one")
        dest.mkdir(parents=True, exist_ok=True)
        t.extractall(dest, filter="data")
    return dest / tops.pop()


# ---- units -----------------------------------------------------------------------------------------------------

def unit_text(name: str, kh: Path) -> str:
    """crew/units/<name> as krew-crew-install renders it: %h/.krew becomes this krew home when it is another one."""
    text = (UNITS / name).read_text(encoding="utf-8")
    home = Path.home()
    if kh != home / ".krew":
        target = str(kh).replace(str(home), "%h", 1) if str(kh).startswith(str(home)) else str(kh)
        text = text.replace("%h/.krew", target)
    return text


def write_unit(name: str, unit_dir: Path, kh: Path, say, *, dry: bool, force: bool) -> bool:
    body = unit_text(name, kh)
    dst = unit_dir / name
    if dst.exists() and dst.read_text(encoding="utf-8") == body:
        say(f"same     {dst}")
        return True
    say(f"unit     crew/units/{name} -> {dst}")
    if not dry:
        unit_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text(body, encoding="utf-8")
    return True


USER_UNITS = Path("~/.config/systemd/user")


def start_units(names: list, say, *, run, dry: bool, scratch: bool, unit_dir: Path | None = None) -> bool:
    """Own homes AND units written to the user's own systemd folder: daemon-reload + enable --now. Anything else
    (a scratch home, units written elsewhere): say the commands, run nothing. krew-crew-install's rule: a scratch
    HOME makes ~/.hermes look like an own home (1e 2026-11-01: a proof under HOME=<scratch> reached the user's
    manager with a daemon-reload), so the folder decides too."""
    cmds = [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", *names]]
    own_folder = unit_dir is not None and unit_dir.expanduser().resolve() == USER_UNITS.expanduser().resolve()
    if scratch or dry or not own_folder:
        why = "would    " if dry else ("scratch  not run: " if scratch else f"units    in {unit_dir}, not your systemd folder: not run: ")
        for c in cmds:
            say(why + " ".join(c))
        return True
    for c in cmds:
        r = run(c)
        if r.returncode != 0:
            say(f"units    {' '.join(c)}: exit {r.returncode}: {_tail(r)}")
            return False
    say("units    enabled + started: " + " ".join(names))
    return True


def write_secret_env(path: Path, values: dict, say, *, dry: bool) -> None:
    """KEY=value lines, 0600, keys in order; an existing file keeps every key it has (a password is never rotated)."""
    have = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                have[k.strip()] = v
    merged = dict(values, **have)
    if have and merged == have:
        say(f"kept     {path}")
        return
    say(f"env      {path} ({len(merged)} keys; secrets never printed)")
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".krew-tmp")
    tmp.write_text("".join(f"{k}={v}\n" for k, v in merged.items()), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(?:export\s+)?(\w+)=(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


# ---- memory ----------------------------------------------------------------------------------------------------

def embedding_pin(p: dict, kit_root: Path | None, say) -> dict:
    """krew's copy of the shared model pin, or the Crissa kit's lock when the kit is here (one model on disk)."""
    e = dict(p["embedding"])
    lock = (kit_root / e["kit_lock"]) if kit_root else None
    if lock and lock.is_file():
        try:
            k = json.loads(lock.read_text(encoding="utf-8"))
            for key in ("repo", "revision", "dim"):
                if k.get(key) and k[key] != e[key]:
                    say(f"model    {key}: the kit's lock says {k[key]!r}, krew's pin {e[key]!r}: the kit's wins (one model on disk)")
                    e[key] = k[key]
        except (OSError, ValueError) as x:
            say(f"model    the kit's lock {lock} is unreadable ({x}); krew's pin is used")
    return e


def snapshot_dir(hf_home: Path, repo: str, revision: str, need: tuple = ()) -> Path:
    """The standard hub cache (<HF_HOME>/hub/models--...), or the same layout straight under HF_HOME when that one
    already holds every `need`ed file (a cache made with HF_HUB_CACHE=<HF_HOME>): one copy on disk either way."""
    tail = Path("models--" + repo.replace("/", "--")) / "snapshots" / revision
    if need and not all((hf_home / "hub" / tail / f).exists() for f in need) and \
            all((hf_home / tail / f).exists() for f in need):
        return hf_home / tail
    return hf_home / "hub" / tail


FETCH_MODEL = r"""
import hashlib, json, sys
from huggingface_hub import snapshot_download
a = json.loads(sys.argv[1])
d = snapshot_download(a["repo"], revision=a["revision"], allow_patterns=a["allow"])
bad = {f: hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest() for f in a["sha"]}
bad = {f: h for f, h in bad.items() if h != a["sha"][f]}
print(json.dumps({"dir": d, "bad": bad}))
"""


def fetch_model(py: Path, e: dict, hf_home: Path, say, *, run, dry: bool) -> tuple[Path | None, str]:
    need = list(e["allow"]) + list(e.get("onnx") or {})
    snap = snapshot_dir(hf_home, e["repo"], e["revision"], tuple(need))
    if snap.is_dir() and all((snap / f).exists() for f in need):
        bad = [f for f, h in (e.get("onnx") or {}).items() if hashlib.sha256((snap / f).read_bytes()).hexdigest() != h]
        if not bad:
            say(f"model    kept {snap}")
            return snap, "kept"
    say(f"model    {e['repo']}@{e['revision'][:12]} -> {hf_home} (once; offline at run time)")
    if dry:
        return snap, "dry"
    if os.environ.get(NO_FETCH) == "1":
        return None, f"fetch refused ({NO_FETCH}=1)"
    arg = json.dumps({"repo": e["repo"], "revision": e["revision"], "allow": need, "sha": e.get("onnx") or {}})
    r = run([py, "-c", FETCH_MODEL, arg], env=dict(os.environ, HF_HOME=str(hf_home), HF_HUB_DISABLE_TELEMETRY="1"))
    if r.returncode != 0:
        return None, f"model fetch: exit {r.returncode}: {_tail(r)}"
    out = json.loads(r.stdout.strip().splitlines()[-1])
    if out["bad"]:
        return None, "model files do not match the pin: " + ", ".join(out["bad"])
    return Path(out["dir"]), "fetched"


def plugin_step(hh: Path, p: dict, say, *, run, dry: bool, force: bool, runtime_python: str | None) -> tuple[bool, str]:
    """The vendored plugin into $HERMES_HOME/plugins/hindsight (refused unless its tree matches the pin), then its
    pip pin into Hermes's runtime. Never `hermes plugins install` (no fetch, no catalog scan)."""
    pp = p["hindsight"]["plugin"]
    src = REPO / pp["dir"]
    got = tree_sha(src)
    if got != pp["tree_sha256"]:
        return False, f"the vendored plugin {pp['dir']} does not match its pin ({got[:12]} != {pp['tree_sha256'][:12]})"
    dst = hh / "plugins" / "hindsight"
    if dst.is_dir() and tree_sha(dst) == got:
        say(f"same     {dst} (vendored hindsight {pp['version']} @ {pp['sha'][:12]})")
    elif dst.exists() and not force:
        say(f"kept     {dst} exists and differs from the vendored copy (--force replaces it)")
    else:
        say(f"copy     {pp['dir']} -> {dst} (vendored, upstream {pp['sha'][:12]}; no fetch)")
        if not dry:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    if not runtime_python:
        return True, "the plugin's pip pin was not installed: no Hermes runtime python (rerun with Hermes installed)"
    if run is default_run and under_test() and not dry:
        return True, f"under a test: the plugin's pip pin was not installed ({REAL_SWITCH}=1)"
    ok, why = pip(Path(runtime_python), pp["pip"], say, run=run, dry=dry)
    return ok, "" if ok else f"plugin pip pin: {why}"


def llm_values(brain: dict, hh: Path) -> dict:
    """Hindsight's one LLM = the install's own brain (an OpenAI-compatible endpoint); its key from $HERMES_HOME/.env."""
    key = read_env(hh / ".env").get(brain.get("key_env") or "", "") if brain.get("key_env") else ""
    out = {"HINDSIGHT_API_LLM_PROVIDER": "openai", "HINDSIGHT_API_LLM_MODEL": brain["model"],
           "HINDSIGHT_API_LLM_BASE_URL": brain["base_url"], "HINDSIGHT_API_LLM_API_KEY": key or "local"}
    if re.match(r"https?://(127\.0\.0\.1|localhost)[:/]", brain["base_url"]):
        # a local Qwen-family engine: thinking off or fact extraction runs out of length (the reference install, 07-19)
        out["HINDSIGHT_API_LLM_EXTRA_BODY"] = json.dumps({"chat_template_kwargs": {"enable_thinking": False}})
    return out


def memory(hh: Path, kh: Path, say, *, brain: dict, unit_dir: Path, scratch: bool, run=default_run, dry: bool = False,
           force: bool = False, runtime_python: str | None = None, kit_root: Path | None = None,
           env: dict | None = None, pin_path: Path | None = None) -> tuple[bool, list]:
    """The whole local memory. Returns (ok, [why per failed part])."""
    p = pins(pin_path)
    hs, db = p["hindsight"], p["vector_db"]
    fails = []
    ok, why = plugin_step(hh, p, say, run=run, dry=dry, force=force, runtime_python=runtime_python)
    if not ok:
        return False, [why]
    if why:
        say("memory   " + why)
    cfg = hh / "hindsight" / "config.json"
    if cfg.exists():
        say(f"kept     {cfg} (yours)")
    else:
        say(f"copy     crew/hindsight/config.example.json -> {cfg}")
        if not dry:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            body = json.loads((REPO / "crew" / "hindsight" / "config.example.json").read_text(encoding="utf-8"))
            body["api_url"] = body["apiUrl"] = f"http://127.0.0.1:{hs['port']}"
            cfg.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    if run is default_run and under_test() and not dry:
        say(f"memory   under a test: the plugin and its config only; no venv, pip, model, store or units ({REAL_SWITCH}=1)")
        return True, []
    py, how = make_venv(kh / hs["venv"], p["python"], say, run=run, dry=dry)
    if py is None:
        return False, [f"hindsight venv: {how}"]
    if how != "kept" or force:
        for reqs, index in (([hs["torch"]], hs["torch_index"]), (hs["requirements"], "")):
            ok, why = pip(py, reqs, say, run=run, dry=dry, index=index)
            if not ok:
                return False, [f"hindsight install: {why}"]
    else:
        say(f"kept     {kh / hs['venv']} (an installed venv; --force reinstalls its pins)")
    e = embedding_pin(p, kit_root, say)
    hf = expand(e["hf_home"], env)
    snap, how = fetch_model(py, e, hf, say, run=run, dry=dry)
    if snap is None:
        return False, [how]
    pg0 = (kh / hs["venv"]).joinpath("lib")
    if not dry:
        r = run([py, "-c", "import pg0, os; print(os.path.join(os.path.dirname(pg0.__file__), 'bin', 'pg0'))"])
        if r.returncode != 0:
            return False, [f"pg0 (the vector store) is not in the hindsight venv: {_tail(r)}"]
        pg0 = Path(r.stdout.strip())
    db_env = kh / "hindsight" / "db.env"
    pw = read_env(db_env).get("PG0_PASSWORD") or secrets.token_urlsafe(24)
    write_secret_env(db_env, {"PG0_BIN": pg0, "PG0_NAME": "krew", "PG0_PORT": db["port"],
                              "PG0_DATA": kh / db["data"], "PG0_USER": db["user"], "PG0_PASSWORD": pw,
                              "PG0_DATABASE": db["database"]}, say, dry=dry)
    pw = read_env(db_env).get("PG0_PASSWORD") or pw
    values = {"HINDSIGHT_API_DATABASE_URL": f"postgresql://{db['user']}:{pw}@127.0.0.1:{db['port']}/{db['database']}",
              "HINDSIGHT_API_HOST": "127.0.0.1", "HINDSIGHT_API_PORT": hs["port"], "HINDSIGHT_API_LOG_LEVEL": "info",
              "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "local", "HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL": snap,
              "HINDSIGHT_API_EMBEDDINGS_LOCAL_FORCE_CPU": "1", "HINDSIGHT_API_RERANKER_PROVIDER": hs["reranker"],
              "HINDSIGHT_API_SKIP_LLM_VERIFICATION": "1", "HF_HOME": hf, "HF_HUB_OFFLINE": "1",
              "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", **llm_values(brain, hh)}
    write_secret_env(kh / hs["env_file"], values, say, dry=dry)
    for name in ("krew-memory-db.service", "krew-hindsight.service"):
        write_unit(name, unit_dir, kh, say, dry=dry, force=force)
    if not start_units(["krew-memory-db.service", "krew-hindsight.service"], say, run=run, dry=dry, scratch=scratch,
                       unit_dir=unit_dir):
        fails.append("the memory units did not start (systemctl --user status krew-hindsight)")
    say("memory   ships empty: the first night writes the first memories")
    return not fails, fails


# ---- search ----------------------------------------------------------------------------------------------------

SETTINGS = """# krew's SearXNG (written by krew-install --search): loopback only, no limiter, json on for Hermes.
use_default_settings: true
general:
  instance_name: "krew search"
  enable_metrics: false
server:
  bind_address: "127.0.0.1"
  port: {port}
  secret_key: "{secret}"
  limiter: false
  public_instance: false
  image_proxy: false
  method: "GET"
search:
  safe_search: 0
  formats: [html, json]
ui:
  static_use_hash: true
engines:          # the two Tor (.onion) engines need a Tor proxy krew does not install: off, so they never log a failure
  - name: ahmia
    inactive: true
  - name: torch
    inactive: true
"""

FROZEN = '''# written by krew-install --search: this tree is an archive, not a git checkout (searx/version.py reads this)
VERSION_STRING = "{commit}"
VERSION_TAG = "{commit}"
DOCKER_TAG = "{commit}"
GIT_URL = "https://github.com/searxng/searxng"
GIT_BRANCH = "krew-pinned"
'''


def search(hh: Path, kh: Path, say, *, unit_dir: Path, scratch: bool, run=default_run, dry: bool = False,
           force: bool = False, opener=urllib.request.urlopen, pin_path: Path | None = None) -> tuple[bool, list]:
    p = pins(pin_path)
    s = p["searxng"]
    base = kh / s["dir"]
    if run is default_run and opener is urllib.request.urlopen and under_test() and not dry:
        say(f"search   under a test: nothing fetched, installed or pointed ({REAL_SWITCH}=1)")
        return True, []
    src = base / "src"                      # the unit's fixed path; .krew-commit says which commit is there
    mark = src / ".krew-commit"
    if (src / "searx" / "webapp.py").is_file() and mark.is_file() and mark.read_text().strip() == s["commit"]:
        say(f"kept     {src} ({s['commit'][:12]})")
    else:
        tgz = base / "dl" / (s["commit"] + ".tar.gz")
        say(f"fetch    SearXNG {s['commit'][:12]} (AGPL-3.0, fetched at install, never vendored)")
        if not dry:
            try:
                got = fetch(s["url"], tgz, s["tarball_sha256"], opener=opener)
            except Exception as x:          # noqa: BLE001 - a network failure is a plain refusal, said once
                return False, [f"SearXNG fetch: {x}"]
            tmp = base / ".extract"
            if tmp.exists():
                shutil.rmtree(tmp)
            top = extract(tgz, tmp)
            tree = tree_sha(top, skip=())
            if got != s["tarball_sha256"] and (not s.get("tree_sha256") or tree != s["tree_sha256"]):
                shutil.rmtree(tmp)
                tgz.unlink()
                return False, [f"SearXNG archive does not match its pin (sha {got[:12]}, tree {tree[:12]})"]
            (top / ".krew-commit").write_text(s["commit"] + "\n")
            if src.exists():
                shutil.rmtree(src)
            os.replace(top, src)
            shutil.rmtree(tmp)
    frozen = src / "searx" / "version_frozen.py"
    if not dry and not frozen.exists():
        frozen.write_text(FROZEN.format(commit=s["commit"]), encoding="utf-8")
    py, how = make_venv(base / ".venv", p["python"], say, run=run, dry=dry)
    if py is None:
        return False, [f"searxng venv: {how}"]
    if how != "kept" or force:
        ok, why = pip(py, ["-r", src / "requirements.txt"], say, run=run, dry=dry)
        if not ok:
            return False, [f"searxng install: {why}"]
    settings = kh / s["settings"]
    if settings.exists():
        say(f"kept     {settings}")
    else:
        say(f"settings {settings} (loopback :{s['port']}, its own secret)")
        if not dry:
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(SETTINGS.format(port=s["port"], secret=secrets.token_hex(32)), encoding="utf-8")
            os.chmod(settings, 0o600)
    write_unit("krew-searxng.service", unit_dir, kh, say, dry=dry, force=force)
    fails = []
    if not start_units(["krew-searxng.service"], say, run=run, dry=dry, scratch=scratch, unit_dir=unit_dir):
        fails.append("krew-searxng.service did not start (systemctl --user status krew-searxng)")
    point_profiles(hh, f"http://127.0.0.1:{s['port']}", say, dry=dry)
    return not fails, fails


def web_block(text: str) -> str:
    """config.yaml with `web:` searching on SearXNG and the keyless cloud ring off; every other key kept."""
    want = {"search_backend": "searxng", "keyless_fallback": "false", "keyless_rescue": "false"}
    lines = text.splitlines()
    try:
        i = next(n for n, ln in enumerate(lines) if re.match(r"^web:\s*(#.*)?$", ln))
    except StopIteration:
        body = ["web:", "  # krew-install --search: web search on this machine's SearXNG; no keyless cloud search"]
        body += [f"  {k}: {v}" for k, v in want.items()]
        return "\n".join(lines + ([""] if lines and lines[-1].strip() else []) + body) + "\n"
    j = i + 1
    while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t", "#"))):
        j += 1
    block = lines[i + 1:j]
    for k, v in want.items():
        pat = re.compile(rf"^(\s+){k}:.*$")
        hit = [n for n, ln in enumerate(block) if pat.match(ln)]
        if hit:
            block[hit[0]] = pat.sub(lambda m: f"{m.group(1)}{k}: {v}", block[hit[0]])
        else:
            block.append(f"  {k}: {v}")
    return "\n".join(lines[:i + 1] + block + lines[j:]) + "\n"


def profile_homes(hh: Path) -> list:
    """The default profile (the home itself) and every profile under profiles/."""
    out = [hh]
    if (hh / "profiles").is_dir():
        out += sorted(d for d in (hh / "profiles").iterdir() if d.is_dir())
    return out


def point_profiles(hh: Path, url: str, say, *, dry: bool) -> int:
    n = 0
    for home in profile_homes(hh):
        cfg = home / "config.yaml"
        if not cfg.exists():
            continue
        t = cfg.read_text(encoding="utf-8")
        new = web_block(t)
        envf = home / ".env"
        have = read_env(envf).get("SEARXNG_URL")
        if new == t and have == url:
            continue
        n += 1
        say(f"search   {home.name or home}: web search -> {url} (keyless cloud off)")
        if dry:
            continue
        if new != t:
            tmp = cfg.with_name(cfg.name + ".krew-tmp")
            tmp.write_text(new, encoding="utf-8")
            os.replace(tmp, cfg)
        if have != url:
            body = envf.read_text(encoding="utf-8") if envf.exists() else ""
            body = re.sub(r"^(?:export\s+)?SEARXNG_URL=.*\n?", "", body, flags=re.M)
            tmp = envf.with_name(envf.name + ".krew-tmp")
            tmp.write_text(body + ("" if not body or body.endswith("\n") else "\n") + f"SEARXNG_URL={url}\n", encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, envf)
    if not n:
        say(f"same     every profile already searches on {url}")
    return n
