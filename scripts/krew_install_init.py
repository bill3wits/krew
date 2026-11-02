"""krew-install's guided first step: `python3 scripts/krew-install.py init` (the owner 2026-10-08).

One pass, in order, each step saying what it did. HERMES FIRST, THEN KREW'S PINNED WRITES ON TOP (the owner
2026-10-30, after the 1.0.2 buyer test: "hermes then krew with those pinned writes"): Hermes's own installer
autostashed krew's 17 patches when it ran after them, and copied its own example config where krew's would go.

  1. Hermes at the pin: clone config/hermes-pin.json's repo into $HERMES_HOME/hermes-agent when there is no
     checkout and check the pinned commit out; when no `hermes` command serves this Hermes home, run Hermes's own
     installer FIRST (<checkout>/scripts/install.sh --commit <pin> --hermes-home <home> --skip-setup --skip-browser
     --skip-computer-use; kill KREW_INIT_HERMES_INSTALLER=0 = say how and go on, as before), then apply krew's
     patches (scripts/krew-patch.py apply) and read them back FROM THE TREE (krew-patch.py status): fewer than all
     is a failed init.
  2. The eight profiles (crissa, ada, bash, steward, vera, lila, scout, judge) + the default profile: each gets a
     config.yaml from config/config.example.yaml with the reference install's own settings for that profile
     (crew/config/profile-kit.json `profiles`: toolsets on cli / cron / api_server, agent caps, delegation, memory,
     tool_search, terminal, skills guards, hooks_auto_accept), a profile.yaml with its Board class description (what
     Ada's decomposer routes by; its SOUL carries the same line), the model endpoint and key name you give (the
     key's VALUE goes to $HERMES_HOME/.env only), the dispatcher block in the default profile, and its SOUL (TRUNK +
     template, {{owner}} and {{deliverables_dir}} filled) where it has none. Nothing you already have is overwritten.
  3. krew's plugins, skills, rails, units, and every profile's hooks (approved where `hermes hooks doctor` reads
     them) and cron jobs: scripts/krew-install.py and crew/scripts/krew-crew-install.py --units --profiles
     --approve-hooks --crons.
  4. The one services list: discovery (systemd + listening ports) pre-fills $KREW_HOME/services.json; you name
     and approve every row (or --yes approves them as found, role observe: nothing is restarted from a row you
     did not set to watch).
  5. Loops: `--enable` names which timers start (opt-in per loop; steward-sweep stays off unless named), the crew's
     (crew/units) and krew's own (units/: the window ticker, the M&S survey, the restart window, ...), all written
     to --unit-dir. A unit marked `# krew: opt-in` (the reference install's two dated one-shots, the fixed-time M&S
     nightly that stands in for the ticker) is written and enabled only when named, never by `--enable all`.

  6. The pinned writes on top of Hermes (the routine `hermes-tree.py update` runs after `hermes update`):
     every config init wrote is also krew's pack ($KREW_HOME/packs/distributions/<p>/config.yaml, BRIDGE-config.yaml
     for the default profile), and crew/scripts/krew-config-restore.py --migrate-first --migrate-unstamped runs
     Hermes's own config migration and then lays krew's keys back on top; the security minimums for the pin
     ($KREW_HOME/hermes-env-pins.json) go into Hermes's environment (ms/lib/hermes_env_pins; kill
     KREW_INIT_ENV_PINS=0); the patches are read from the tree once more.
  7. The Wall: the marry step turns the profile's API server on (--api-server-on: a free port, never 8642, and the
     profile's own key), writes her player's real address (CRISSA_PLAYER from her launcher table) and offers the
     endpoint. A model key goes into every profile's own .env too (one gateway serves every profile, and a named
     profile reads its secrets from profiles/<p>/.env).

Every step's result is collected: init ends "init done" only when none failed, else "init NOT done" with the list
and exit 1 (a missing `hermes` command fails the cron step loudly). The long memory (Hindsight + its vector store) and
web search (SearXNG) are installed on this machine unless --no-memory / --no-search (crew/local-services.json); without
memory init says plainly that it is off and how to add it. Homes: every write goes under the homes init was
given, or is named and refused (scripts/krew_install_homes.py: a scratch --hermes-home/--krew-home never writes the
user systemd folder, a Crissa home it was not given, or runs another install's `hermes`).

Flags make it non-interactive: --endpoint --model --key-env --api-key --yes --enable ... ; with --dry-run it
writes nothing. On a terminal, a missing value is asked for; off one, it is left as the example's placeholder.
--switch-brains rewrites the model lines of configs that already exist (the brain switch), key included.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_install_homes as H  # noqa: E402  (the homes an install may write, the hermes it may run; 2026-10-30)
CREW = ("crissa", "ada", "bash", "steward", "vera", "lila", "scout", "judge")
KIT_PATH = REPO / "crew" / "config" / "profile-kit.json"
PRESETS_PATH = REPO / "crew" / "config" / "brain-presets.json"
FRONT_DOOR = ("crissa",)          # the profiles on the `ops` brain; every other profile (the default too) is on `crew`


def brain_presets(path: Path | None = None) -> dict:
    """crew/config/brain-presets.json: {"default": name, "presets": {name: {"ops": {...}, "crew": {...}}}}."""
    return json.loads((path or PRESETS_PATH).read_text(encoding="utf-8"))


def brain_of(profile: str) -> str:
    return "ops" if profile in FRONT_DOOR else "crew"


def resolve_brains(preset: str = "", endpoint: str = "", model: str = "", path: Path | None = None) -> dict:
    """{"name", "ops": {base_url, model, key_env}, "crew": {...}} for the preset (default: the file's), with
    --endpoint / --model laid over BOTH brains (one custom endpoint). An unknown preset is a SystemExit."""
    data = brain_presets(path)
    name = preset or data.get("default") or "engines"
    presets = data.get("presets") or {}
    if name not in presets:
        raise SystemExit(f"krew-install: no brain preset {name!r} (have: {', '.join(sorted(presets))})")
    out = {"name": name}
    for brain in ("ops", "crew"):
        b = dict(presets[name][brain])
        if endpoint:
            b["base_url"] = endpoint
        if model:
            b["model"] = model
        out[brain] = b
    if endpoint or model:
        out["name"] = name + " (custom)"
    return out


def kit() -> dict:
    return json.loads(KIT_PATH.read_text(encoding="utf-8"))


def kit_profiles(k: dict | None = None) -> dict:
    k = kit() if k is None else k
    return {n: v for n, v in (k.get("profiles") or {}).items() if not n.startswith("_") and isinstance(v, dict)}


def _flow(items) -> str:
    return "[" + ", ".join(str(x) for x in items) + "]"


def cli_toolsets(row: dict) -> list:
    """The reference's cli list: the profile's toolsets plus `docs` (krew_docs; an empty toolset where the profile's
    docs switch is off). The front door's belt stays as it is."""
    ts = list(row.get("toolsets") or [])
    return ts if "hands" in ts or "docs" in ts else ts + ["docs"]


# Kept for callers that read the cli line per profile (the crissa line is her belt).
TOOLSETS = {n: _flow(cli_toolsets(r)) for n, r in kit_profiles().items()}


def add_args(ap) -> None:
    ap.add_argument("--brains", default="", help="the brain preset (crew/config/brain-presets.json): engines = this box's "
                    "own vLLM servers (ops :8000 for Crissa, crew :8005 for the rest); claude = Anthropic's OpenAI-compatible endpoint "
                    "for both (no GPU); xai = xAI's API for both (no GPU); default: the file's default (engines)")
    ap.add_argument("--endpoint", default="", help="one custom endpoint for both brains (OpenAI-compatible base URL)")
    ap.add_argument("--model", default="", help="the model name a custom --endpoint serves")
    ap.add_argument("--key-env", default="", help="the env var that holds the endpoint's key (default: the preset's, "
                    "else KREW_MODEL_API_KEY)")
    ap.add_argument("--api-key", default="", help="the key's value (goes to $HERMES_HOME/.env only; '' = none)")
    ap.add_argument("--yes", action="store_true", help="approve every discovered service as found (role observe)")
    ap.add_argument("--no-hermes", action="store_true", help="skip the Hermes clone / checkout / patch step")
    ap.add_argument("--no-discover", action="store_true", help="skip the services discovery")
    ap.add_argument("--owner", default=os.environ.get("KREW_OWNER_NAME", ""),
                    help="your name as the SOULs say it ({{owner}}; default: 'the owner')")
    ap.add_argument("--deliverables", default=os.environ.get("KREW_DELIVERABLES", ""),
                    help="the folder every report lands in ({{deliverables_dir}}; default ~/Desktop/krew-deliverables)")
    ap.add_argument("--switch-brains", action="store_true",
                    help="init: rewrite the model lines (base_url, default, key_env) of configs that already exist to "
                         "--brains / --endpoint / --model, and the key into each profile's .env (the brain switch)")


def _ask(prompt: str, default: str = "", *, tty: bool) -> str:
    if not tty:
        return default
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return v or default


def _run_env(argv: list, say, *, env: dict) -> int:
    say("run      " + " ".join(str(x) for x in argv))
    return subprocess.run([str(x) for x in argv], env=env).returncode


def _run(argv: list, say, *, dry: bool, cwd: Path | None = None) -> int:
    say("run      " + " ".join(str(x) for x in argv))
    if dry:
        return 0
    return subprocess.run([str(x) for x in argv], cwd=str(cwd) if cwd else None).returncode


def fill(text: str, values: dict) -> str:
    """The {{name}} placeholders a SOUL or a profile description carries, filled (an unknown one is left)."""
    for k, v in values.items():
        text = text.replace("{{%s}}" % k, str(v))
    return text


def _block(name: str, d: dict) -> str:
    """A top-level YAML block for a flat-ish mapping (scalars, flow lists, one nested level)."""
    out = [f"{name}:"]
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out.append(f"  {k}:")
            out += [f"    {kk}: {_scalar(vv)}" for kk, vv in v.items() if not str(kk).startswith("_")]
        else:
            out.append(f"  {k}: {_scalar(v)}")
    return "\n".join(out) + "\n"


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return _flow(_scalar(x) for x in v)
    if v is None:
        return "null"
    s = str(v)
    return s if re.fullmatch(r"[\w./~:{}@+-]+", s) and not s.startswith(("{", "@")) else json.dumps(s)


def terminal_block(row: dict, values: dict, *, docker: bool | None = None) -> str:
    """The profile's terminal, as the reference runs it. Ada's is the one Docker sandbox there (consultants for risky
    or untrusted work): written only where a `docker` command exists, its volumes filled ({{hermes_home}},
    {{live_tree}} = this krew checkout, read-only; {{deliverables_dir}}); a volume whose host folder does not exist
    is left out. No Docker: Hermes's own local backend, and init says so."""
    t = dict(row.get("terminal") or {})
    if not t:
        return ""
    if t.get("backend") == "docker":
        if not (shutil.which("docker") if docker is None else docker):
            return ""
        vols = []
        for v in t.get("docker_volumes") or []:
            v = fill(v, values)
            host = os.path.expanduser(v.split(":", 1)[0])
            if os.path.isdir(host):
                vols.append(v)
        t["docker_volumes"] = vols
    return _block("terminal", t)


def crew_blocks(profile: str, row: dict, values: dict, *, docker: bool | None = None) -> str:
    """The reference profile's own blocks after the shared template: agent caps, delegation, the skills guards,
    hooks_auto_accept, terminal."""
    parts = []
    if row.get("agent"):
        parts.append(_block("agent", row["agent"]))
    if row.get("delegation"):
        parts.append(_block("delegation", row["delegation"]))
    parts.append(_block("skills", {"guard_agent_created": True, "write_approval": True}))
    parts.append("hooks_auto_accept: true   # a kanban worker has no TTY to approve a hook on\n")
    term = terminal_block(row, values, docker=docker)
    if term:
        parts.append(term)
    return "\n# The reference install's settings for this profile (crew/config/profile-kit.json).\n" + "\n".join(parts)


def profile_config(template: str, profile: str, endpoint: str, model: str, key_env: str, *, api_key: bool,
                   values: dict | None = None, docker: bool | None = None) -> str:
    """config.example.yaml with this profile's settings (profile-kit.json), the endpoint and the key name; the default
    profile keeps the dispatcher block and turns the gateway multiplex on, a named profile drops the dispatcher's own
    switch."""
    t = template
    values = values or {}
    if endpoint:
        t = re.sub(r"^(  base_url: )\S+", lambda m: m.group(1) + endpoint, t, count=1, flags=re.M)
    if model:
        t = re.sub(r"^(  default: )\S+", lambda m: m.group(1) + model, t, count=1, flags=re.M)
    if api_key:
        t = re.sub(r"^(  key_env: )\S+", lambda m: m.group(1) + key_env, t, count=1, flags=re.M)
    else:
        t = re.sub(r"^  key_env: .*\n", "", t, count=1, flags=re.M)
    row = kit_profiles().get(profile) or {}
    if profile in TOOLSETS:
        lines = "  cli: " + TOOLSETS[profile]
        if profile != "crissa" and row.get("toolsets"):
            lines += "\n  cron: " + _flow(row["toolsets"]) + "\n  api_server: " + _flow(row["toolsets"])
        t = re.sub(r"^  cli: \[.*\]$", lambda _m: lines, t, count=1, flags=re.M)
    ts = row.get("tool_search")
    if profile != "crissa" and isinstance(ts, dict):
        body = "".join(f"    {k}: {_scalar(v)}\n" for k, v in ts.items() if not k.startswith("_"))
        t = re.sub(r"^  tool_search:\n(?:    .*\n)+", lambda _m: "  tool_search:\n" + body, t, count=1, flags=re.M)
    if row and row.get("memory_enabled") is False:
        t = re.sub(r"^(  memory_enabled: )true", r"\1false", t, count=1, flags=re.M)
    if profile == "crissa":
        t = re.sub(r"^#crissa ", "", t, flags=re.M)          # the front door's own blocks (voice, ear, hands' ceiling)
    if profile == "default":
        t = t.replace("# gateway:\n#   multiplex_profiles: true", "gateway:\n  multiplex_profiles: true")
    else:
        t = t.replace("  dispatch_in_gateway: true          # this gateway runs the dispatcher",
                      "  dispatch_in_gateway: false         # only the default profile's gateway dispatches")
    if profile != "crissa" and row:
        t = t.rstrip("\n") + "\n" + crew_blocks(profile, row, values, docker=docker)
    return t


def profile_yaml(profile: str, row: dict, values: dict) -> str:
    """profiles/<p>/profile.yaml: the Board class description Hermes's decomposer puts in its roster."""
    desc = fill(str(row.get("description") or ""), values)
    lines = [f"name: {profile}", f"display_name: {row.get('display_name') or profile.capitalize()}",
             "description: >-"]
    import textwrap
    lines += ["  " + x for x in textwrap.wrap(desc, 96, break_on_hyphens=False)] or ["  ''"]
    lines.append("description_auto: false")
    return "\n".join(lines) + "\n"


def soul_values(owner: str = "", deliverables: str = "") -> dict:
    return {"owner": owner or "the owner",
            "deliverables_dir": deliverables or "~/Desktop/krew-deliverables"}


def put_key(env: Path, key_env: str, api_key: str, say, *, dry: bool, replace: bool = False) -> str:
    """KEY=value in one .env (0600). Absent: appended. Present: kept, unless `replace` (an explicit new key: the brain
    switch). The value is never printed."""
    have = env.read_text(encoding="utf-8") if env.exists() else ""
    pat = re.compile(rf"^(?:export\s+)?{re.escape(key_env)}=.*$", re.M)
    if pat.search(have):
        cur = pat.search(have).group(0).split("=", 1)[1].strip().strip("'\"")
        if not replace or cur == api_key:
            say(f"kept     {env}: {key_env} already set")
            return "kept"
        say(f"key      {key_env} -> {env} (replaced; the value is written nowhere else)")
        if not dry:
            body = pat.sub(lambda _m: f"{key_env}={api_key}", have, count=1)
            tmp = env.with_name(env.name + ".krew-tmp")
            tmp.write_text(body, encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, env)
        return "replaced"
    say(f"key      {key_env} -> {env} (the value is written nowhere else)")
    if not dry:
        env.parent.mkdir(parents=True, exist_ok=True)
        with env.open("a", encoding="utf-8") as fh:
            fh.write(("" if not have or have.endswith("\n") else "\n") + f"{key_env}={api_key}\n")
        os.chmod(env, 0o600)
    return "added"


def profile_keys_on() -> bool:
    """2026-10-30 (the 1.0.2 buyer test, finding 9): under the one host gateway a named profile's secrets come from its
    own profiles/<p>/.env, so a key only in $HERMES_HOME/.env reached Anthropic as the wrong key (401). The key goes to
    every profile that names it. Kill: KREW_INIT_PROFILE_KEYS=0 ($HERMES_HOME/.env only, as before)."""
    return os.environ.get("KREW_INIT_PROFILE_KEYS", "1").strip().lower() not in ("0", "false", "no", "off")


def pack_dir(kh: Path) -> Path:
    return Path(kh) / "packs" / "distributions"


def write_pack(kh: Path, profile: str, cfg: Path, say, *, dry: bool) -> None:
    """The config init just wrote, kept as krew's pack: what krew-config-restore lays back over the live config after
    Hermes changes it (its installer, `hermes update`, a migration). A pack file of yours is never overwritten."""
    dst = pack_dir(kh) / ("BRIDGE-config.yaml" if profile == "default" else f"{profile}/config.yaml")
    if dst.exists():
        say(f"kept     {dst} (krew's pack: yours)")
        return
    say(f"pack     {profile} -> {dst} (krew's settings, laid back over Hermes's changes)")
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")


def write_profiles(hh: Path, endpoint: str, model: str, key_env: str, api_key: str, say, *, dry: bool,
                   values: dict | None = None, docker: bool | None = None, brains: dict | None = None,
                   replace: set | None = None, kh: Path | None = None) -> list:
    """Each profile's config.yaml; with `brains` (resolve_brains), Crissa takes the ops brain and every other profile the
    crew brain, else every profile takes `endpoint` / `model`. `replace`: config files Hermes's own installer created
    this run (its example config, never the user's): krew's is written over them, the installer's copy kept beside.
    `kh`: also keep each written config as krew's pack (write_pack)."""
    template = (REPO / "config" / "config.example.yaml").read_text(encoding="utf-8")
    values = dict(soul_values(), **(values or {}))
    values.setdefault("hermes_home", str(hh))
    values.setdefault("live_tree", str(REPO))
    rows = kit_profiles()
    written = []
    for p in ("default",) + CREW:
        dst = hh / "config.yaml" if p == "default" else hh / "profiles" / p / "config.yaml"
        if p in rows:
            py = dst.parent / "profile.yaml"
            if py.exists():
                say(f"kept     {py} (yours)")
            else:
                say(f"profile  {p} -> {py} (its Board class description)")
                if not dry:
                    py.parent.mkdir(parents=True, exist_ok=True)
                    py.write_text(profile_yaml(p, rows[p], values), encoding="utf-8")
        if dst.exists() and dst not in (replace or set()):
            say(f"kept     {dst} (yours)")
            continue
        if dst.exists():
            say(f"config   {p}: {dst} is Hermes's installer's example (made this run); krew's goes over it, the "
                f"example kept as {dst.name}.hermes-installer")
            if not dry:
                shutil.copyfile(dst, dst.with_name(dst.name + ".hermes-installer"))
        say(f"config   {p} -> {dst}")
        written.append(p)
        row = rows.get(p) or {}
        if (row.get("terminal") or {}).get("backend") == "docker" and not (shutil.which("docker") if docker is None else docker):
            say(f"terminal {p}: the reference runs this profile's terminal in Docker; no `docker` here, so Hermes's"
                " local backend (install Docker and add the terminal block from profile-kit.json to sandbox it)")
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            b = (brains or {}).get(brain_of(p)) or {}
            dst.write_text(profile_config(template, p, b.get("base_url") or endpoint, b.get("model") or model, key_env,
                                          api_key=bool(api_key), values=values, docker=docker), encoding="utf-8")
        if kh is not None:
            write_pack(kh, p, dst, say, dry=dry)
        if p == "crissa":
            cua = dst.parent / "cua-manifest.yaml"
            if cua.exists():
                say(f"kept     {cua} (yours)")
            else:
                say(f"manifest crew/config/crissa-cua-manifest.yaml -> {cua} (her computer-use ceiling)")
                if not dry:
                    cua.write_text((REPO / "crew" / "config" / "crissa-cua-manifest.yaml").read_text(encoding="utf-8"),
                                   encoding="utf-8")
    if api_key:
        put_key(hh / ".env", key_env, api_key, say, dry=dry)
        if profile_keys_on():
            for p in CREW:
                if (hh / "profiles" / p / "config.yaml").exists() or p in written:
                    put_key(hh / "profiles" / p / ".env", key_env, api_key, say, dry=dry)
    return written


def switch_brains(hh: Path, kh: Path, brains: dict, key_env: str, api_key: str, say, *, dry: bool) -> list:
    """The brain switch on an existing install: each profile's three model lines (base_url, default, key_env) to its
    brain (Crissa ops, the rest crew), in the live config AND krew's pack (else the next restore would switch it back),
    and the key into $HERMES_HOME/.env and every profile's own .env. A backup of each config is kept."""
    done = []
    for p in ("default",) + CREW:
        b = brains.get(brain_of(p)) or {}
        for f in ([hh / "config.yaml", pack_dir(kh) / "BRIDGE-config.yaml"] if p == "default"
                  else [hh / "profiles" / p / "config.yaml", pack_dir(kh) / p / "config.yaml"]):
            if not f.is_file():
                continue
            t = f.read_text(encoding="utf-8")
            n = re.sub(r"^(  base_url: )\S+", lambda m: m.group(1) + b["base_url"], t, count=1, flags=re.M)
            n = re.sub(r"^(  default: )\S+", lambda m: m.group(1) + b["model"], n, count=1, flags=re.M)
            if api_key or b.get("key_env"):
                if re.search(r"^  key_env: ", n, re.M):
                    n = re.sub(r"^(  key_env: )\S+", lambda m: m.group(1) + key_env, n, count=1, flags=re.M)
                else:
                    n = re.sub(r"^(  base_url: .*)$", lambda m: m.group(1) + f"\n  key_env: {key_env}", n, count=1, flags=re.M)
            if n != t:
                say(f"brain    {p}: {f} -> {b['base_url']} model {b['model']}")
                done.append(str(f))
                if not dry:
                    shutil.copyfile(f, f.with_name(f.name + ".pre-switch-brains"))
                    f.write_text(n, encoding="utf-8")
    if api_key:
        put_key(hh / ".env", key_env, api_key, say, dry=dry, replace=True)
        for p in CREW:
            if (hh / "profiles" / p).is_dir() and profile_keys_on():
                put_key(hh / "profiles" / p / ".env", key_env, api_key, say, dry=dry, replace=True)
    say("brain    restart the gateway to load the new brains")
    return done


class Steps:
    """Each step's result, for the end-of-init verdict: ok | FAILED | refused | off."""

    def __init__(self, say):
        self.say, self.rows = say, []

    def add(self, name: str, state: str, why: str = "") -> str:
        self.rows.append((name, state, why))
        if state != "ok":
            self.say(f"{state:8} {name}" + (f": {why}" if why else ""))
        return state

    def failed(self) -> list:
        return [r for r in self.rows if r[1] == "FAILED"]


def installer_on() -> bool:
    return os.environ.get("KREW_INIT_HERMES_INSTALLER", "1").strip().lower() not in ("0", "false", "no", "off")


def _patch(verb: str, tree: Path, say, *, dry: bool) -> tuple[int, str]:
    argv = [sys.executable, REPO / "scripts" / "krew-patch.py", verb, "--hermes", tree]
    say("run      " + " ".join(str(x) for x in argv))
    if dry:
        return 0, ""
    r = subprocess.run([str(x) for x in argv], capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    for ln in out.strip().splitlines()[-6:]:
        say("         " + ln)
    return r.returncode, out


def hermes_tree(hh: Path) -> Path:
    """$HERMES_TREE when it lies inside the Hermes home, else <home>/hermes-agent (never a tree outside it)."""
    env_tree = os.environ.get("HERMES_TREE", "")
    return Path(os.path.expanduser(env_tree)) if env_tree and H.under(env_tree, hh) else hh / "hermes-agent"


def hermes_first(hh: Path, say, *, dry: bool, hermes_bin: str, scratch: bool, steps: Steps) -> str | None:
    """Hermes at the pin, its own installer FIRST when no `hermes` serves this home, krew's patches on top, read back
    from the tree. Returns the hermes command that serves `hh` (None when there is none)."""
    pin = json.loads((REPO / "config" / "hermes-pin.json").read_text(encoding="utf-8"))
    tree = hermes_tree(hh)
    if not (tree / ".git").is_dir():
        if _run(["git", "clone", pin["repo"], tree], say, dry=dry) != 0:
            steps.add("Hermes clone", "FAILED", f"git clone {pin['repo']} {tree}")
            return None
    hermes, why = H.hermes_for(hh, hermes_bin)
    if hermes is None and not dry:
        if scratch:
            steps.add("Hermes's installer", "refused", "a scratch install: Hermes's installer writes ~/.local/bin and "
                      "your shell rc, outside the homes init was given")
        elif not installer_on():
            say(f"hermes   no `hermes` command yet: install Hermes from {tree} the way its README says (it makes the"
                " venv and the command), then rerun init (KREW_INIT_HERMES_INSTALLER=0)")
        else:
            # Hermes FIRST: the installer autostashes local changes, so it runs on the clean pin, before the patches.
            rc, out = _patch("status", tree, say, dry=dry)
            if re.search(r"applied  [1-9]\d* of", out):
                _patch("revert", tree, say, dry=dry)
            _run(["git", "-C", tree, "checkout", "--quiet", pin["commit"]], say, dry=dry)
            installer = os.environ.get("KREW_HERMES_INSTALLER") or str(tree / "scripts" / "install.sh")
            argv = ["bash", installer, "--commit", pin["commit"], "--hermes-home", hh,
                    "--dir", tree, "--skip-setup", "--skip-browser", "--skip-computer-use"]
            say("hermes   Hermes's own installer first (it makes the venv and the `hermes` command), then krew on top")
            say("run      " + " ".join(str(x) for x in argv))
            log = hh / "logs" / "krew-init-hermes-install.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("w", encoding="utf-8") as fh:
                r = subprocess.run([str(x) for x in argv], stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, HERMES_HOME=str(hh)))
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            for ln in tail:
                say("         " + ln[:160])
            steps.add("Hermes's installer", "ok" if r.returncode == 0 else "FAILED",
                      "" if r.returncode == 0 else f"exit {r.returncode} (full output: {log})")
            hermes, why = H.hermes_for(hh, hermes_bin)
    if dry and not (tree / ".git").is_dir():
        say(f"would    git -C {tree} checkout {pin['commit']}; krew-patch.py apply; krew-patch.py status")
        return hermes
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != pin["commit"]:
        _run(["git", "-C", tree, "checkout", "--quiet", pin["commit"]], say, dry=dry)
    rc, _ = _patch("apply", tree, say, dry=dry)
    steps.add("krew patches", "ok" if rc == 0 else "FAILED", "" if rc == 0 else "krew-patch.py apply failed")
    rc, out = _patch("status", tree, say, dry=dry)
    n = len(pin["patches"])
    ok = dry or (rc == 0 and f"applied  {n} of {n} in the tree" in out)
    steps.add("patches read from the tree", "ok" if ok else "FAILED", "" if ok else f"the tree does not carry all {n}")
    if hermes is None:
        steps.add("hermes command", "FAILED", why)
    else:
        say(f"hermes   {hermes} ({why})")
    return hermes


def pinned_writes(hh: Path, kh: Path, hermes: str | None, say, *, dry: bool, scratch: bool, steps: Steps,
                  no_hermes: bool = False) -> None:
    """krew's pinned writes on top of whatever Hermes did (the routine hermes-tree.py update runs after `hermes
    update`): configs from krew's pack (Hermes's migration first, krew's keys last), the env pins."""
    argv = [sys.executable, REPO / "crew" / "scripts" / "krew-config-restore.py", "--apply", "--hermes-home", hh,
            "--packs", kh / "packs", "--migrate-first", "--migrate-unstamped", "--no-repin"]
    if hermes:
        argv += ["--hermes-bin", hermes]
    if dry:
        say("run      " + " ".join(str(x) for x in argv) + "   (dry run: not run)")
    else:
        say("run      " + " ".join(str(x) for x in argv))
        r = subprocess.run([str(x) for x in argv], capture_output=True, text=True)
        out = (r.stdout or "") + (r.stderr or "")
        for ln in [x for x in out.splitlines() if x.strip()][-12:]:
            say("         " + ln[:180])
        bad = r.returncode != 0 or "FAILED" in out
        steps.add("configs: Hermes's migration, then krew's keys", "FAILED" if bad else "ok",
                  "krew-config-restore.py (see above)" if bad else "")
    if os.environ.get("KREW_INIT_ENV_PINS", "1").strip().lower() in ("0", "false", "no", "off"):
        steps.add("env pins", "off", "KREW_INIT_ENV_PINS=0")
        return
    if no_hermes:
        steps.add("env pins", "off", "--no-hermes: Hermes's environment is yours to pin (ms/scripts/hermes-env-pins.py)")
        return
    if scratch:
        steps.add("env pins", "refused", "a scratch install: Hermes's environment is found from your real home")
        return
    if dry or not hermes:
        say("pins     " + ("dry run: not applied" if dry else "no hermes command: not applied"))
        return
    try:
        sys.path.insert(0, str(REPO / "ms" / "lib"))
        os.environ.setdefault("MS_HERMES_ENV_PINS_FILE", str(kh / "hermes-env-pins.json"))
        import hermes_env_pins
        st = hermes_env_pins.apply(at_install=True, owe=lambda *a, **k: None, by="krew init")
    except Exception as e:  # noqa: BLE001
        steps.add("env pins", "FAILED", f"{type(e).__name__}: {e}")
        return
    if st.get("error"):
        steps.add("env pins", "FAILED", str(st["error"])[:200])
        return
    for a in st.get("applied") or []:
        say(f"pins     {a['dist']} {a.get('was')} -> {a.get('now')}" + ("" if a["rc"] == 0 else f" FAILED: {a['out'][-160:]}"))
    left = st.get("below") or []
    steps.add("env pins", "ok" if not left else "FAILED",
              "" if not left else "below the minimum: " + ", ".join(f"{b['dist']} {b.get('have')} < {b['min']}" for b in left))
    if not left:
        say(f"pins     {len(st.get('pins') or [])} minimum(s) met in {st.get('site') or 'the environment'}")


# A BRAIN'S PROVIDER PACKAGE BEFORE THE FIRST CALL (crissatest, 2026-10-31: krew 1.0.3's init on the claude brain ended
# "init done", then Crissa's first call failed "The 'anthropic' package is required for the Anthropic provider: venv:
# anthropic installed; restart Hermes to activate it" - Hermes installs a provider's extra on first use and needs a
# restart to load it). Init installs the extra the chosen brains need, before the gateway ever starts. Kill:
# KREW_INIT_PROVIDER_EXTRAS=0.
PROVIDER_EXTRAS = (("api.anthropic.com", "anthropic"),)


def provider_extras(brains: dict, hermes: str | None, say, *, dry: bool, scratch: bool, no_hermes: bool,
                    steps: Steps) -> list:
    need = []
    for brain in ("ops", "crew"):
        url = str((brains.get(brain) or {}).get("base_url") or "")
        for host, extra in PROVIDER_EXTRAS:
            if host in url and extra not in need:
                need.append(extra)
    if not need:
        return []
    if os.environ.get("KREW_INIT_PROVIDER_EXTRAS", "1").strip().lower() in ("0", "false", "no", "off"):
        steps.add("provider packages", "off", "KREW_INIT_PROVIDER_EXTRAS=0")
        return need
    if no_hermes or scratch or not hermes:
        steps.add("provider packages", "off", "no Hermes of this home to install into: " + ", ".join(need) +
                  " installs on the first call (then restart the gateway)")
        return need
    argv = [hermes, "pm", "install"]
    for e in need:
        argv += ["--extra", e]
    rc = _run(argv, say, dry=dry)
    steps.add("provider packages", "ok" if rc == 0 else "FAILED",
              "" if rc == 0 else "`%s` exit %d" % (" ".join(argv), rc))
    return need


def memory_search(a) -> tuple[bool, bool]:
    """init installs the long memory and local search unless told not to (the User 2026-11-01: "EVERYthing we run is
    local"); --memory / --search alone still name them."""
    return (bool(getattr(a, "memory", False)) or not getattr(a, "no_memory", False),
            bool(getattr(a, "search", False)) or not getattr(a, "no_search", False))


def memory_line(hh: Path, memory: bool, say, steps: Steps, dry: bool = False) -> None:
    """One line on the memory: what the krew-install step above said is the detail; this is the init summary row."""
    have = (hh / "plugins" / "hindsight").is_dir()
    if have:
        say(f"memory   Hindsight installed ({hh / 'plugins' / 'hindsight'}); it ships empty")
        steps.add("memory", "ok")
    elif memory and dry:
        say("memory   would install Hindsight on this machine (dry run)")
        steps.add("memory", "ok")
    elif memory:
        steps.add("memory", "FAILED", "the memory step did not install Hindsight (see the memory lines above)")
    else:
        say("memory   OFF (--no-memory): the profiles run with Hermes's own per-profile memory. To add it: "
            "python3 scripts/krew-install.py --memory")
        steps.add("memory", "off", "--no-memory")


def services(kh: Path, say, *, dry: bool, yes: bool, tty: bool) -> int:
    sys.path.insert(0, str(REPO / "crew" / "lib"))
    import krew_services  # noqa: E402
    dst = kh / "services.json"
    if dst.exists():
        say(f"kept     {dst} (yours; edit it, or move it away and rerun init to rediscover)")
        return len(krew_services.load(dst, approved_only=False))
    rows = krew_services.discover()
    say(f"found    {len(rows)} candidate services (systemd units of yours + listening ports)")
    kept = []
    for r in rows:
        line = f"{r['unit'] or '(no unit)'} :{r['port'] or '-'} {r['kind']}"
        if yes:
            r["approved"] = True
            say(f"approve  {r['name']:24} {line}  (as found, role {r['role']})")
            kept.append(r)
            continue
        if not tty:
            say(f"listed   {r['name']:24} {line}  (not approved: no terminal and no --yes)")
            kept.append(r)
            continue
        name = _ask(f"{line}\n  name (empty = skip)", r["name"], tty=tty)
        if not name:
            continue
        r["name"] = name
        r["label"] = _ask("  label", r["label"], tty=tty)
        r["role"] = _ask("  role watch|report-only|observe|never-restart|retired", "observe", tty=tty)
        r["health"] = _ask("  health path on its port (e.g. /health; empty = unit state only)", r["health"], tty=tty)
        r["gpu"] = _ask("  holds the GPU? y/N", "n", tty=tty).lower().startswith("y")
        r["approved"] = True
        kept.append(r)
    say(f"services {len([r for r in kept if r.get('approved')])} approved of {len(kept)} -> {dst}")
    if not dry:
        krew_services.write(kept, dst, by="krew-install init")
    return len(kept)


def plugins_and_gateway(hh: Path, say, *, dry: bool, unit_dir: Path | None = None) -> list[str]:
    """What krew's own pin check wants of a fresh install and nothing wrote (the owner 2026-10-08, a real init into a
    scratch home): each profile's plugins from profile-kit.json ENABLED in its config.yaml and linked under
    profiles/<p>/plugins, Crissa's preload set to `none` (the Wall's room voice carries no skill bodies), the
    one-host-gateway topology file, and the gateway's KREW_T0_MODE=enforce drop-in (written, never daemon-reloaded:
    the restart is yours). Idempotent; a config that already names a plugin is left as it is."""
    import json as _json
    kit = _json.loads((REPO / "crew" / "config" / "profile-kit.json").read_text(encoding="utf-8"))
    by_profile: dict[str, list[str]] = {}
    for plugin, profs in (kit.get("plugins") or {}).items():
        if plugin.startswith("_") or not isinstance(profs, list):
            continue
        for prof in profs:
            by_profile.setdefault(prof, []).append(plugin)
    done = []
    for prof, names in sorted(by_profile.items()):
        cfg = hh / "config.yaml" if prof == "main" else hh / "profiles" / prof / "config.yaml"
        if not cfg.is_file():
            continue
        text = cfg.read_text(encoding="utf-8")
        missing = [n for n in names if not re.search(r"^\s+- %s\s*$" % re.escape(n), text, re.M)]
        if missing:
            if re.search(r"^plugins:\s*\n\s+enabled:\s*\n", text, re.M):
                text = re.sub(r"^(plugins:\s*\n\s+enabled:\s*\n)", lambda m: m.group(1) + "".join("    - %s\n" % n for n in missing),
                              text, count=1, flags=re.M)
            else:
                text = text.rstrip("\n") + "\nplugins:\n  enabled:\n" + "".join("    - %s\n" % n for n in missing)
            say(f"plugins  {prof}: enabled {', '.join(missing)}")
            done.append(f"{prof}:{','.join(missing)}")
        if prof == "crissa" and "krew_preload:" not in text:
            text = text.rstrip("\n") + "\nkrew_preload:\n  skills: none      # the room voice loads no skill bodies (krew-skill-preload)\n"
            say("preload  crissa: krew_preload.skills none")
        if not dry:
            cfg.write_text(text, encoding="utf-8")
        if prof != "main":
            for n in names:
                src, dst = hh / "plugins" / n, hh / "profiles" / prof / "plugins" / n
                if src.is_dir() and not (dst.exists() or dst.is_symlink()) and not dry:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.symlink_to(src, target_is_directory=True)
    topo = hh / "krew-topology.env"
    if not topo.exists():
        say(f"topology {topo}: KREW_GATEWAY_TOPOLOGY=host (one Hermes gateway serves every profile)")
        if not dry:
            topo.write_text("KREW_GATEWAY_TOPOLOGY=host\n", encoding="utf-8")
    unit_dir = unit_dir or Path(os.path.expanduser("~/.config/systemd/user"))
    conf = unit_dir / "hermes-gateway.service.d" / "krew.conf"
    if not conf.exists():
        say(f"gateway  {conf}: KREW_T0_MODE=enforce (daemon-reload + the gateway restart are yours)")
        if not dry:
            conf.parent.mkdir(parents=True, exist_ok=True)
            conf.write_text("# krew: the T0 gate enforces on the one host gateway and every worker it spawns.\n"
                            "# After writing: systemctl --user daemon-reload && systemctl --user restart hermes-gateway\n"
                            "[Service]\nEnvironment=KREW_T0_MODE=enforce\n", encoding="utf-8")
    return done


def _load_script(rel: str, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def enable_own_units(spec: str, unit_dir: Path, say, *, dry: bool, run=subprocess.run) -> list[str]:
    """krew's own units/ (the window ticker, the M&S survey, the restart window and its morning report, ...): the
    same `--enable` choice as the crew's units, by the same rule (crew/scripts/krew-crew-install.py choose_enable).
    The reference install's dated one-shots and the fixed-time M&S nightly carry `# krew: opt-in`: `--enable all`
    never turns them on (a fresh install would fire both one-shots at once, for nothing); naming one does. Units are
    enabled only in the user's own systemd folder; elsewhere this says what it would enable."""
    starts, optin = _load_script("scripts/krew-install.py", "krew_install_main").unit_sets()
    chosen = _load_script("crew/scripts/krew-crew-install.py", "krew_crew_install").choose_enable(spec, starts, optin, say)
    if not chosen:
        if starts:
            say("units    krew's own (none enabled; --enable all or --enable <list>): " + " ".join(starts))
        if optin:
            say("units    opt-in, never enabled unnamed: " + " ".join(optin))
        return []
    if dry or Path(unit_dir) != Path(os.path.expanduser("~/.config/systemd/user")) or not shutil.which("systemctl"):
        say("would enable: " + " ".join(chosen) + ("" if dry else f" ({unit_dir} is not your user systemd folder)"))
        return chosen
    for n in chosen:
        r = run(["systemctl", "--user", "enable", "--now", n], capture_output=True, text=True)
        say(f"enable   {n}: " + ("ok" if r.returncode == 0 else (r.stderr or r.stdout).strip()))
    return chosen


def run(a, hh: Path, kh: Path, say) -> int:
    dry = bool(a.dry_run)
    tty = sys.stdin.isatty()
    steps = Steps(say)
    say("== krew init: Hermes first (its installer, the pin), krew's patches and pinned writes on top, the crew's "
        "profiles, the services list, the loops ==")
    scratch = H.is_scratch(hh, kh)
    unit_dir, uwhy = H.unit_dir(getattr(a, "unit_dir", ""), hh, kh)
    zh, zwhy = H.wall_home(getattr(a, "crissa_home", ""), hh, kh)
    say(f"homes    hermes {hh}, krew {kh}, units {unit_dir} ({uwhy}), crissa {zh or '-'} ({zwhy})")
    if zh is None:
        steps.add("the Wall (panel tiles, studio pointer, marry)", "refused", zwhy)
    cfg0 = hh / "config.yaml"
    had_cfg = cfg0.exists()
    hermes = None
    if not a.no_hermes:
        hermes = hermes_first(hh, say, dry=dry, hermes_bin=a.hermes_bin, scratch=scratch, steps=steps)
    else:
        hermes, why = H.hermes_for(hh, a.hermes_bin)
        if hermes is None:
            say(f"hermes   {why}")
    replace = {cfg0} if (cfg0.exists() and not had_cfg) else set()
    brains = resolve_brains(a.brains, a.endpoint, a.model)
    for brain in ("ops", "crew"):
        say(f"brain    {brain}: {brains[brain]['base_url']} model {brains[brain]['model']} (preset {brains['name']})")
    endpoint, model = brains["crew"]["base_url"], brains["crew"]["model"]
    a.key_env = a.key_env or brains["crew"].get("key_env") or "KREW_MODEL_API_KEY"
    key = a.api_key or os.environ.get(a.key_env, "") or (_ask(f"the endpoint's key (empty = a local server with no auth); kept in {hh / '.env'} as {a.key_env}",
                            "", tty=tty))
    values = soul_values(a.owner, a.deliverables)
    if brains["crew"].get("key_env") and not key:
        say(f"key      none given: {a.key_env} is the preset's key and stays unset (--api-key, or {a.key_env} in the "
            f"environment, then rerun init; nothing else needs redoing)")
    write_profiles(hh, endpoint, model, a.key_env, key, say, dry=dry, values=values, brains=brains, replace=replace,
                   kh=kh)
    if getattr(a, "switch_brains", False):
        switch_brains(hh, kh, brains, a.key_env, key, say, dry=dry)
    souls = ",".join(CREW + ("main",))
    sub_env = dict(os.environ)
    sub_env["KREW_BRAIN_URLS"] = " ".join(str(brains[b]["base_url"]) for b in ("ops", "crew"))   # the panel's engine tiles
    if zh is None:
        sub_env["KREW_WALL_REFUSED"] = zwhy
    else:
        sub_env["CRISSA_HOME"] = str(zh)
    if hermes:
        sub_env["KREW_HERMES_BIN"] = hermes
    else:
        sub_env.pop("KREW_HERMES_BIN", None)
        sub_env["KREW_NO_HERMES"] = "no `hermes` command serves " + str(hh)
    base = [sys.executable, REPO / "scripts" / "krew-install.py", "--hermes-home", hh, "--krew-home", kh, "--souls", souls,
            "--owner", values["owner"], "--deliverables", values["deliverables_dir"],
            "--units", "--unit-dir", unit_dir, "--hermes-bin", hermes or "/nonexistent/hermes", "--api-server-on"]
    if zh is not None:
        base += ["--crissa-home", zh]
    if getattr(a, "marry_profile", ""):
        base += ["--marry-profile", a.marry_profile]
    if getattr(a, "no_marry", False):
        base.append("--no-marry")
    if dry:
        base.append("--dry-run")
    memory, search = memory_search(a)
    if memory:
        crew_brain = brains["crew"]
        base += ["--memory", "--memory-brain",
                 f"{crew_brain['base_url']}|{crew_brain['model']}|{crew_brain.get('key_env') or ''}"]
    if search:
        base.append("--search")
    rc = _run_env(base, say, env=sub_env)
    steps.add("plugins, skills, SOULs, rails, units, panel, marry", "ok" if rc == 0 else "FAILED",
              "" if rc == 0 else f"krew-install.py exit {rc}")
    crew = [sys.executable, REPO / "crew" / "scripts" / "krew-crew-install.py", "--hermes-home", hh, "--krew-home", kh,
            "--units", "--systemd-dir", unit_dir, "--profiles", ",".join(CREW), "--approve-hooks", "--crons"]
    if a.enable:
        crew += ["--enable", a.enable]
    if dry:
        crew.append("--dry-run")
    rc = _run_env(crew, say, env=sub_env)
    steps.add("crew: hooks, skills, units, cron jobs", "ok" if rc == 0 else "FAILED",
              "" if rc == 0 else "krew-crew-install.py exit %d (CRONS FAILED above, when it is the crons)" % rc)
    enable_own_units(a.enable, unit_dir, say, dry=dry)
    plugins_and_gateway(hh, say, dry=dry, unit_dir=unit_dir)
    # before the pinned writes: a package install syncs Hermes's venv, and krew's env pins must land after it
    provider_extras(brains, hermes, say, dry=dry, scratch=scratch, no_hermes=a.no_hermes, steps=steps)
    pinned_writes(hh, kh, hermes, say, dry=dry, scratch=scratch, steps=steps, no_hermes=a.no_hermes)
    if not a.no_hermes and not dry:
        rc, out = _patch("status", hermes_tree(hh), say, dry=dry)
        n = len(json.loads((REPO / "config" / "hermes-pin.json").read_text(encoding="utf-8"))["patches"])
        ok = rc == 0 and f"applied  {n} of {n} in the tree" in out
        steps.add("patches still in the tree after the pinned writes", "ok" if ok else "FAILED",
                  "" if ok else "something after the patch step changed the Hermes tree")
    if not a.no_discover:
        services(kh, say, dry=dry, yes=a.yes, tty=tty)
    memory_line(hh, memory, say, steps, dry=dry)
    bad = steps.failed()
    if bad:
        say("== init NOT done: " + "; ".join(f"{n}: {w}" for n, _s, w in bad) + " ==")
        return 1
    others = [f"{n} {st}" for n, st, _w in steps.rows if st in ("refused", "off")]
    say("== init done" + (f" ({'; '.join(others)})" if others else "") + ". Next: `hermes -p crissa chat` to meet the "
        "front door; the loops you named are on, the rest wait for `krew-crew-install.py --enable <loop>`; the "
        "services list is yours to edit ==")
    return 0
