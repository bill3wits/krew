"""krew-install's guided first step: `python3 scripts/krew-install.py init` (the owner 2026-10-08).

One pass, in order, each step saying what it did:

  1. Hermes at the pin: clone config/hermes-pin.json's repo into $HERMES_HOME/hermes-agent when there is no
     checkout, check the pinned commit out, apply krew's patches (scripts/krew-patch.py apply). Installing the
     `hermes` command itself is Hermes's own step (its README); init says so when it is missing and goes on.
  2. The eight profiles (zoey, ada, bash, steward, vera, lila, scout, judge) + the default profile: each gets a
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

Flags make it non-interactive: --endpoint --model --key-env --api-key --yes --enable ... ; with --dry-run it
writes nothing. On a terminal, a missing value is asked for; off one, it is left as the example's placeholder.
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
CREW = ("zoey", "ada", "bash", "steward", "vera", "lila", "scout", "judge")
KIT_PATH = REPO / "crew" / "config" / "profile-kit.json"
PRESETS_PATH = REPO / "crew" / "config" / "brain-presets.json"
FRONT_DOOR = ("zoey",)          # the profiles on the `ops` brain; every other profile (the default too) is on `crew`


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


# Kept for callers that read the cli line per profile (the zoey line is her belt).
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


def _ask(prompt: str, default: str = "", *, tty: bool) -> str:
    if not tty:
        return default
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return v or default


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
        if profile != "zoey" and row.get("toolsets"):
            lines += "\n  cron: " + _flow(row["toolsets"]) + "\n  api_server: " + _flow(row["toolsets"])
        t = re.sub(r"^  cli: \[.*\]$", lambda _m: lines, t, count=1, flags=re.M)
    ts = row.get("tool_search")
    if profile != "zoey" and isinstance(ts, dict):
        body = "".join(f"    {k}: {_scalar(v)}\n" for k, v in ts.items() if not k.startswith("_"))
        t = re.sub(r"^  tool_search:\n(?:    .*\n)+", lambda _m: "  tool_search:\n" + body, t, count=1, flags=re.M)
    if row and row.get("memory_enabled") is False:
        t = re.sub(r"^(  memory_enabled: )true", r"\1false", t, count=1, flags=re.M)
    if profile == "zoey":
        t = re.sub(r"^#zoey ", "", t, flags=re.M)          # the front door's own blocks (voice, ear, hands' ceiling)
    if profile == "default":
        t = t.replace("# gateway:\n#   multiplex_profiles: true", "gateway:\n  multiplex_profiles: true")
    else:
        t = t.replace("  dispatch_in_gateway: true          # this gateway runs the dispatcher",
                      "  dispatch_in_gateway: false         # only the default profile's gateway dispatches")
    if profile != "zoey" and row:
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


def write_profiles(hh: Path, endpoint: str, model: str, key_env: str, api_key: str, say, *, dry: bool,
                   values: dict | None = None, docker: bool | None = None, brains: dict | None = None) -> list:
    """Each profile's config.yaml; with `brains` (resolve_brains), Zoey takes the ops brain and every other profile the
    crew brain, else every profile takes `endpoint` / `model`."""
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
        if dst.exists():
            say(f"kept     {dst} (yours)")
            continue
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
        if p == "zoey":
            cua = dst.parent / "cua-manifest.yaml"
            if cua.exists():
                say(f"kept     {cua} (yours)")
            else:
                say(f"manifest crew/config/zoey-cua-manifest.yaml -> {cua} (her computer-use ceiling)")
                if not dry:
                    cua.write_text((REPO / "crew" / "config" / "zoey-cua-manifest.yaml").read_text(encoding="utf-8"),
                                   encoding="utf-8")
    env = hh / ".env"
    if api_key:
        have = env.read_text(encoding="utf-8") if env.exists() else ""
        if re.search(rf"^{re.escape(key_env)}=", have, re.M):
            say(f"kept     {env}: {key_env} already set")
        else:
            say(f"key      {key_env} -> {env} (the value is written nowhere else)")
            if not dry:
                env.parent.mkdir(parents=True, exist_ok=True)
                with env.open("a", encoding="utf-8") as fh:
                    fh.write(f"{key_env}={api_key}\n")
                os.chmod(env, 0o600)
    return written


def hermes_at_pin(hh: Path, say, *, dry: bool, hermes_bin: str) -> None:
    pin = json.loads((REPO / "config" / "hermes-pin.json").read_text(encoding="utf-8"))
    tree = Path(os.path.expanduser(os.environ.get("HERMES_TREE") or str(hh / "hermes-agent")))
    if not (tree / ".git").is_dir():
        _run(["git", "clone", pin["repo"], tree], say, dry=dry)
    if dry and not (tree / ".git").is_dir():
        say(f"would    git -C {tree} checkout {pin['commit']}; krew-patch.py apply")
    else:
        _run(["git", "-C", tree, "checkout", "--quiet", pin["commit"]], say, dry=dry)
        _run([sys.executable, REPO / "scripts" / "krew-patch.py", "apply", "--hermes", tree], say, dry=dry)
    if not shutil.which(os.path.expanduser(hermes_bin)):
        say(f"hermes   no `{hermes_bin}` command yet: install Hermes from {tree} the way its README says (it makes the"
            " venv and the command), then rerun init; the rest of this pass still lands")


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
    profiles/<p>/plugins, Zoey's preload set to `none` (the Wall's room voice carries no skill bodies), the
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
        if prof == "zoey" and "krew_preload:" not in text:
            text = text.rstrip("\n") + "\nkrew_preload:\n  skills: none      # the room voice loads no skill bodies (krew-skill-preload)\n"
            say("preload  zoey: krew_preload.skills none")
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
    say("== krew init: Hermes at the pin, the crew's profiles, the services list, the loops ==")
    if not a.no_hermes:
        hermes_at_pin(hh, say, dry=dry, hermes_bin=a.hermes_bin)
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
    write_profiles(hh, endpoint, model, a.key_env, key, say, dry=dry, values=values, brains=brains)
    souls = ",".join(CREW + ("main",))
    unit_dir = Path(os.path.expanduser(a.unit_dir))
    base = [sys.executable, REPO / "scripts" / "krew-install.py", "--hermes-home", hh, "--krew-home", kh, "--souls", souls,
            "--owner", values["owner"], "--deliverables", values["deliverables_dir"],
            "--units", "--unit-dir", unit_dir, "--hermes-bin", a.hermes_bin]
    if dry:
        base.append("--dry-run")
    if a.memory:
        base.append("--memory")
    _run(base, say, dry=False)
    crew = [sys.executable, REPO / "crew" / "scripts" / "krew-crew-install.py", "--hermes-home", hh, "--krew-home", kh,
            "--units", "--systemd-dir", unit_dir, "--profiles", ",".join(CREW), "--approve-hooks", "--crons"]
    if a.enable:
        crew += ["--enable", a.enable]
    if dry:
        crew.append("--dry-run")
    _run(crew, say, dry=False)
    enable_own_units(a.enable, unit_dir, say, dry=dry)
    plugins_and_gateway(hh, say, dry=dry, unit_dir=unit_dir)
    if not a.no_discover:
        services(kh, say, dry=dry, yes=a.yes, tty=tty)
    say("== init done. Next: `hermes -p zoey chat` to meet the front door; the loops you named are on, the rest wait "
        "for `krew-crew-install.py --enable <loop>`; the services list is yours to edit ==")
    return 0
