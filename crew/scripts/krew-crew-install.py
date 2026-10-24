#!/usr/bin/env python3
"""Install krew's crew machinery: the loops, gates, hooks and unit templates. Report-first: nothing outside
$KREW_HOME changes unless you ask.

    python3 crew/scripts/krew-crew-install.py                copy crew/ to $KREW_HOME/crew + crew.example.yaml; print the rest
    ... --units                                              also write the systemd user unit templates (not enabled)
    ... --enable LOOPS                                       ... and `systemctl --user enable --now` the named loops only
                                                             (comma list of unit names or stems, e.g. kanban-loop,hold-sweep;
                                                             `all` = every timer/path/service a straight clone runs; `ask` =
                                                             one yes/no per unit on a terminal). Opt-in: nothing is enabled
                                                             unnamed. steward-sweep is opt-in and stays off unless named.
    ... --profiles ada,bash,zoey                             write the crew hooks into those profiles' config.yaml and
                                                             copy each profile's crew skills (profile-kit.json skills)
    ... --crons                                              create the crew's hermes cron jobs that are missing
                                                             (crew/config/crew-crons.json; placeholder schedule, units fire them)
                                                             and a profile's own (profile-kit.json crons: Crissa's Decide watch,
                                                             its monitor script copied, its prompt pinned from the script)
    ... --approve-hooks                                      with --profiles: record each hook's approval (and its
                                                             script's mtime) where `hermes hooks doctor` reads it
    ... --dry-run                                            say what would happen

What goes where: crew/ -> $KREW_HOME/crew (scripts, lib, config, units; your edits to $KREW_HOME/crew/config/*.json
are kept unless --force); the repo's skills/ -> $KREW_HOME/crew/skills (the catalog krew-kitting allows);
config/crew.example.yaml -> $KREW_HOME/crew.example.yaml (refreshed; your crew.yaml is never touched).
Units go to ~/.config/systemd/user (or --systemd-dir). Plugins install with scripts/krew-install.py.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CREW = Path(__file__).resolve().parents[1]
REPO = CREW.parent
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
DEFAULT_HOME = "%h/.krew"


def kit(crew_root: Path) -> dict:
    return json.loads((crew_root / "config" / "profile-kit.json").read_text(encoding="utf-8"))


def hook_entries(profile: str, krew_home: Path, k: dict) -> list[dict]:
    """The pre_tool_call specs profile-kit.json gives this profile, in the reference order (`hooks._order`). A hook
    with `path` runs that file under $KREW_HOME directly (the T0 gate is never behind a runner or an off switch);
    `script` runs through krew-crew-run with the script named by its ABSOLUTE path, so `hermes hooks doctor` finds
    the script, stamps its mtime and smoke-tests it (a bare name read as a missing script). `fail_closed` is carried
    into the spec; the matcher is the profile's own (`matcher_by_profile`) else the hook's, widened by
    `matcher_extra`."""
    run = krew_home / "crew" / "scripts" / "krew-crew-run"
    order = [n for n in ((k.get("hooks") or {}).get("_order") or [])]
    hooks = kit_hooks(k)
    names = sorted(hooks, key=lambda n: order.index(n) if n in order else len(order))
    out = []
    for name in names:
        h = hooks[name]
        if profile in h["profiles"]:
            if h.get("path"):
                e = {"command": f"python3 {krew_home / h['path']}", "timeout": h["timeout"]}
            else:
                e = {"command": f"python3 {run} {krew_home / 'crew' / 'scripts' / h['script']}", "timeout": h["timeout"]}
            matcher = str((h.get("matcher_by_profile") or {}).get(profile) or h.get("matcher") or "")
            extra = (h.get("matcher_extra") or {}).get(profile)
            if extra:
                matcher = f"{matcher}|{extra}" if matcher else str(extra)
            if matcher:
                e["matcher"] = matcher
            if h.get("fail_closed"):
                e["fail_closed"] = True
            out.append(e)
    return out


def kit_hooks(k: dict) -> dict:
    return {n: h for n, h in (k.get("hooks") or {}).items() if not n.startswith("_") and isinstance(h, dict)}


def kit_plugins(k: dict) -> dict:
    return {n: v for n, v in (k.get("plugins") or {}).items() if not n.startswith("_") and isinstance(v, list)}


def kit_crons(k: dict) -> dict:
    return {n: v for n, v in (k.get("crons") or {}).items() if not n.startswith("_") and isinstance(v, dict)}


def _cron_create(cmd: list, hh: Path) -> str:
    """One `hermes cron create`: created, or FAILED with the reason (a hermes that is not there is a reason)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, HERMES_HOME=str(hh)))
    except OSError as e:
        return f"FAILED {cmd[0]}: {e.strerror or e} (KREW_HERMES_BIN names the hermes command)"
    return "created" if r.returncode == 0 else "FAILED " + (r.stderr or r.stdout).strip()[-300:]


def cron_prompt(script: Path) -> str:
    """The CRON_PROMPT a monitor script carries (loaded by path, never imported by name)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("krew_cron_" + script.stem.replace("-", "_"), script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(getattr(mod, "CRON_PROMPT"))


def pin_cron_prompt(script: Path, jobs_path: Path) -> bool:
    """The script's own install_cron_prompt on the profile's jobs file: True when it rewrote the prompt."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("krew_cron_" + script.stem.replace("-", "_"), script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return bool(mod.install_cron_prompt(jobs_path))


def merge_hooks(cfg: dict, entries: list[dict]) -> tuple[dict, list[str]]:
    """Add each crew hook unless the profile already runs that script, and set `hooks_auto_accept: true` (the
    reference's line on every crew profile: a kanban worker has no TTY to approve a hook on). Returns (config,
    what changed)."""
    hooks = cfg.setdefault("hooks", {}) or {}
    cfg["hooks"] = hooks
    pre = hooks.setdefault("pre_tool_call", []) or []
    hooks["pre_tool_call"] = pre
    changed = []
    for e in entries:
        script = e["command"].split()[-1]
        base = os.path.basename(script)
        if any(base in str(h.get("command", "")) for h in pre if isinstance(h, dict)):
            continue          # already runs that script (by any path: rails/krew_hooks.py writes the T0 gate too)
        pre.append(e)
        changed.append(script)
    if entries and "hooks_auto_accept" not in cfg:
        cfg["hooks_auto_accept"] = True
        changed.append("hooks_auto_accept")
    return cfg, changed


def _script_of(command: str) -> str:
    """The script a hook command runs, as Hermes's shell_hooks reads it: the first token with a script extension,
    else the first path-like token."""
    parts = command.split()
    return (next((x for x in parts if x.lower().endswith((".py", ".sh"))), None)
            or next((x for x in parts if "/" in x or x.startswith("~")), None) or (parts[0] if parts else ""))


def approve_hooks(profile_home: Path, cfg: dict, *, now: str | None = None) -> list[str]:
    """Record the approval Hermes records at its own TTY prompt (or under --accept-hooks) for every pre_tool_call
    hook this profile's config names, in the profile's `shell-hooks-allowlist.json`: event, command, approved_at,
    and the script's mtime at approval. That is what `hermes hooks doctor` reads, so a fresh install reads clean;
    the installer is the owner approving the hooks it was asked to install. An entry already there is kept; one
    for a script changed since is refreshed (an install IS a review of krew's own files). Returns the commands
    approved."""
    import datetime as _dt
    path = profile_home / "shell-hooks-allowlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("approvals"), list):
        data = {"approvals": []}
    stamp = now or _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    done = []
    for h in ((cfg.get("hooks") or {}).get("pre_tool_call") or []):
        if not isinstance(h, dict) or not h.get("command"):
            continue
        cmd = str(h["command"])
        script = os.path.expanduser(_script_of(cmd))
        try:
            mtime = _dt.datetime.fromtimestamp(os.path.getmtime(script), tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        except OSError:
            mtime = None
        have = [e for e in data["approvals"] if isinstance(e, dict) and e.get("command") == cmd
                and e.get("event") in (None, "pre_tool_call")]
        if have and have[-1].get("script_mtime_at_approval") == mtime:
            continue
        data["approvals"] = [e for e in data["approvals"] if e not in have] + [
            {"event": "pre_tool_call", "command": cmd, "approved_at": stamp, "script_mtime_at_approval": mtime}]
        done.append(cmd)
    if done:
        profile_home.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".krew-tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return done


def gpu_mode_file(kh: Path) -> Path:
    """The flag crew/scripts/krew-mode writes, resolved the way it resolves it: KREW_GPU_MODE_FILE, else krew.yaml
    gpu.gpu_mode_file, else <KREW_HERMES or gpu.state_dir or the passwd home's ~/.hermes>/krew-gpu-mode."""
    import pwd
    gpu = {}
    try:
        sys.path.insert(0, str(CREW / "lib"))
        import krew_yaml
        data = krew_yaml.safe_load((kh / "krew.yaml").read_text(encoding="utf-8")) or {}
        gpu = data.get("gpu") if isinstance(data, dict) and isinstance(data.get("gpu"), dict) else {}
    except Exception:  # noqa: BLE001  (no file, no YAML reader: the defaults)
        gpu = {}
    got = os.environ.get("KREW_GPU_MODE_FILE") or str(gpu.get("gpu_mode_file") or "")
    if got.strip():
        return Path(os.path.expanduser(got))
    tree = (os.environ.get("KREW_HERMES") or str(gpu.get("state_dir") or "")
            or str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes"))
    return Path(os.path.expanduser(tree)) / "krew-gpu-mode"


def render_pointer(kh: Path, crew_dst: Path, mode_bin: Path, *, dry: bool, say) -> Path | None:
    """Write <Crissa state home>/krew-render.json when the Wall is installed here (its state home exists); else say
    how. Returns the file written (None when not)."""
    zh = Path(os.path.expanduser(os.environ.get("ZOEYOS_HOME") or "~/.zoeyos"))
    out = zh / "krew-render.json"
    if not zh.is_dir():
        say(f"studio   Crissa not installed (no {zh}): rerun this install once it is, for the Wall's Render studio")
        return None
    body = {"schema": "krew-render/1", "by": "krew", "lib": str(crew_dst / "lib"), "mode_bin": str(mode_bin),
            "gpu_mode_file": str(gpu_mode_file(kh))}
    say(f"studio   {out}   (the Wall's Render studio, Mode row and silo flag from krew)")
    if not dry:
        tmp = out.with_name(out.name + ".krew-tmp")
        tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, out)
    comfy_location(zh, dry=dry, say=say)
    return out


def comfy_location(zh: Path, *, dry: bool, say, env=None) -> Path | None:
    """ONE ComfyUI for the Wall and krew (the User 2026-10-10). The truth is the Wall's <state>/render.json, which
    krew reads first (crew/lib/krew_comfy.py). When the Wall has none yet and krew's own setting (KREW_COMFY_ROOT,
    else ~/ComfyUI) holds a Comfy (main.py there), it is written into render.json, so the Wall's render module reads
    the same folder; a render.json of the Wall's own is never touched. Returns the file written (None when not)."""
    sys.path.insert(0, str(CREW / "lib"))
    import krew_comfy
    e = dict(os.environ if env is None else env, ZOEYOS_HOME=str(zh))
    rj = krew_comfy.render_json(e)
    if krew_comfy.wall_settings(e).get("comfy_dir"):
        say(f"comfy    {rj} names the Wall's Comfy ({krew_comfy.get('root', e)}); krew reads it")
        return None
    root, src = krew_comfy.where("root", e)
    if not os.path.isfile(os.path.join(root, "main.py")):
        say(f"comfy    no ComfyUI at {root} ({src}) and none in {rj}: krew and the Wall's render module both read "
            "render.json once you name one (zoeyos-render.py --configure --comfy-dir ...)")
        return None
    body = dict(krew_comfy.wall_settings(e), comfy_dir=root)
    if (os.environ if env is None else env).get("KREW_COMFY_PYTHON"):
        body["comfy_python"] = krew_comfy.get("python", e)
    say(f"comfy    {rj}: comfy_dir {root} (krew's {src}), so the Wall and krew read one ComfyUI")
    if not dry:
        rj.parent.mkdir(parents=True, exist_ok=True)
        tmp = rj.with_name(rj.name + ".krew-tmp")
        tmp.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, rj)
    return rj


def unit_text(src: Path, krew_home: Path) -> str:
    text = src.read_text(encoding="utf-8")
    home = Path.home()
    if krew_home != home / ".krew":
        target = str(krew_home).replace(str(home), "%h", 1) if str(krew_home).startswith(str(home)) else str(krew_home)
        text = text.replace(DEFAULT_HOME, target)
    return text


def choose_enable(spec: str, starts: list[str], optin: list[str], say) -> list[str]:
    """Which units --enable turns on (the owner 2026-10-08: opt-in per loop). '' = none; 'all' = every unit a
    straight clone runs (never the opt-in ones); a comma list names units or their stems (kanban-loop =
    krew-kanban-loop.timer; an opt-in unit may be named); 'ask' = one yes/no per unit on a terminal, none off one."""
    spec = (spec or "").strip()
    if not spec:
        return []
    every = starts + optin
    if spec == "all":
        return list(starts)
    if spec == "ask":
        if not sys.stdin.isatty():
            say("enable   not a terminal: nothing enabled (name the loops: --enable kanban-loop,hold-sweep or --enable all)")
            return []
        out = []
        for n in every:
            tag = " (opt-in)" if n in optin else ""
            try:
                ans = input(f"enable {n}{tag}? [y/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("y", "yes"):
                out.append(n)
        return out
    out, unknown = [], []
    for item in [x.strip() for x in spec.split(",") if x.strip()]:
        hits = [n for n in every if n == item or n.split(".", 1)[0] in (item, "krew-" + item)]
        if not hits:
            unknown.append(item)
        for n in hits:
            if n not in out:
                out.append(n)
    if unknown:
        say("enable   no such unit(s): " + ", ".join(unknown) + " (have: " + " ".join(every) + ")")
    return out


def _yaml_python() -> str | None:
    """A python that can read and write YAML: this one, else the Hermes venv's (it ships ruamel.yaml)."""
    sys.path.insert(0, str(CREW / "lib"))
    try:
        import krew_yaml  # noqa: F401
        return None
    except ImportError:
        pass
    py = os.path.expanduser(os.environ.get("KREW_HERMES_PYTHON") or "~/.hermes/hermes-agent/venv/bin/python")
    return py if os.path.exists(py) and os.path.realpath(py) != os.path.realpath(sys.executable) else None


def main(argv: list[str] | None = None) -> int:
    py = _yaml_python() if argv is None else None
    if py:                                    # re-run under the Hermes venv so profile hooks can be merged
        os.execv(py, [py, os.path.abspath(__file__), *sys.argv[1:]])
    ap = argparse.ArgumentParser(prog="krew-crew-install", description=__doc__.splitlines()[0])
    ap.add_argument("--krew-home", default=os.environ.get("KREW_HOME") or "~/.krew")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME") or "~/.hermes")
    ap.add_argument("--units", action="store_true")
    ap.add_argument("--enable", nargs="?", const="ask", default="",
                    help="which loops to enable --now: a comma list of units/stems, `all`, or `ask` (per unit, on a terminal)")
    ap.add_argument("--systemd-dir", default="~/.config/systemd/user")
    ap.add_argument("--profiles", default="", help="comma list: write the crew hooks into these profiles")
    ap.add_argument("--force", action="store_true", help="replace $KREW_HOME/crew/config/*.json too")
    ap.add_argument("--crons", action="store_true", help="create the missing crew cron jobs (hermes -p <p> cron create)")
    ap.add_argument("--approve-hooks", action="store_true",
                    help="with --profiles: record the hooks written in each profile's shell-hooks-allowlist.json "
                         "(what Hermes's approval prompt records), so `hermes hooks doctor` reads clean")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    kh = Path(os.path.expanduser(a.krew_home))
    hh = Path(os.path.expanduser(a.hermes_home))
    dry = a.dry_run
    say = print
    dst = kh / "crew"

    # 1. crew/ -> $KREW_HOME/crew (keep the user's config/*.json)
    kept = {}
    if (dst / "config").is_dir() and not a.force:
        kept = {p.name: p.read_bytes() for p in (dst / "config").glob("*.json")}
    say(f"copy     crew/ -> {dst}" + (f" (keeping your {', '.join(sorted(kept))})" if kept else ""))
    if not dry:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(CREW, dst, ignore=IGNORE)
        for name, data in kept.items():
            (dst / "config" / name).write_bytes(data)
        if (REPO / "skills").is_dir():
            shutil.copytree(REPO / "skills", dst / "skills", ignore=IGNORE)
    # 1a. the GPU mode switch where every krew caller looks for it (KREW_MODE_PATH, default $KREW_HOME/bin/krew-mode):
    #     a link to the copy just placed; a krew-mode of your own there is kept
    mode_bin = kh / "bin" / "krew-mode"
    if mode_bin.exists() and not mode_bin.is_symlink():
        say(f"kept     {mode_bin} (yours)")
    else:
        say(f"link     {mode_bin} -> {dst / 'scripts' / 'krew-mode'}")
        if not dry:
            mode_bin.parent.mkdir(parents=True, exist_ok=True)
            if mode_bin.is_symlink():
                mode_bin.unlink()
            mode_bin.symlink_to(dst / "scripts" / "krew-mode")
    # 1b. the Wall (Crissa) finds the Render studio, the mode switch and its flag through one pointer in its state
    #     home: <ZOEYOS_HOME or ~/.zoeyos>/krew-render.json. Its feed loads render_studio from `lib` when it carries
    #     none, runs `mode_bin` from the Mode row, and reads `gpu_mode_file` for the studio's silo check.
    render_pointer(kh, dst, mode_bin, dry=dry, say=say)
    ex = kh / "crew.example.yaml"
    say(f"copy     config/crew.example.yaml -> {ex}")
    if not dry:
        shutil.copyfile(REPO / "config" / "crew.example.yaml", ex)
    say(f"{'kept' if (kh / 'crew.yaml').exists() else 'none'}     {kh / 'crew.yaml'}"
        + ("" if (kh / "crew.yaml").exists() else " (every key has a default; copy the example to change one)"))

    # 1b. the team research source list seeds KREW_DOCS_DIR (Scout's monthly scan grows it there; never overwritten)
    docs = Path(os.path.expanduser(os.environ.get("KREW_DOCS_DIR") or str(kh / "docs")))
    seed = docs / "TEAM-RESEARCH-SOURCES.json"
    if seed.exists():
        say(f"kept     {seed}")
    else:
        say(f"seed     crew/config/team-research-sources.json -> {seed}")
        if not dry:
            docs.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CREW / "config" / "team-research-sources.json", seed)

    # 2. unit templates
    units = sorted((CREW / "units").glob("*.*"))
    sd = Path(os.path.expanduser(a.systemd_dir))
    if a.units or a.enable:
        for u in units:
            say(f"unit     {u.name} -> {sd / u.name}")
            if not dry:
                sd.mkdir(parents=True, exist_ok=True)
                (sd / u.name).write_text(unit_text(u, kh), encoding="utf-8")
        if not dry and shutil.which("systemctl") and sd == Path(os.path.expanduser("~/.config/systemd/user")):
            r = subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
            say(f"reload   systemctl --user daemon-reload: {'ok' if r.returncode == 0 else (r.stderr or r.stdout).strip()}")
        elif not dry:
            say(f"reload   not run: {sd} is not your user systemd folder (load the units from there yourself)")
    else:
        say(f"\nUnits ({len(units)} templates in crew/units, not written): rerun with --units, or --enable to start the timers.")
    # timers and paths, and long-running services that install into default.target; a unit marked
    # `# krew: opt-in` (it needs something krew does not install, e.g. Hindsight) is written but never enabled
    starts = [u.name for u in units if "# krew: opt-in" not in u.read_text(encoding="utf-8")
              and (u.suffix in (".timer", ".path") or "WantedBy=default.target" in u.read_text(encoding="utf-8"))]
    optin = [u.name for u in units if "# krew: opt-in" in u.read_text(encoding="utf-8")]
    chosen = choose_enable(a.enable, starts, optin, say)
    if chosen and not dry:
        for n in chosen:
            r = subprocess.run(["systemctl", "--user", "enable", "--now", n], capture_output=True, text=True)
            say(f"enable   {n}: " + ("ok" if r.returncode == 0 else (r.stderr or r.stdout).strip()))
    elif chosen:
        say("would enable: " + " ".join(chosen))
    elif starts:
        say("Units a straight clone runs (none enabled; --enable all, --enable <list>, or --enable to be asked): "
            + " ".join(starts))
    if optin:
        say("Opt-in (written, never enabled unnamed; steward-sweep, the keeper, the patrols): " + " ".join(optin))

    # 3. hooks into profiles
    k = kit(CREW)
    profiles = [p.strip() for p in a.profiles.split(",") if p.strip()]
    for prof in profiles:
        cfg_path = hh / "profiles" / prof / "config.yaml" if prof != "main" else hh / "config.yaml"
        entries = hook_entries(prof, kh, k)
        if not entries:
            say(f"hooks    {prof}: none in profile-kit.json")
            continue
        try:
            sys.path.insert(0, str(CREW / "lib"))
            import krew_yaml as yaml
        except ImportError:
            say(f"hooks    {prof}: no YAML library here (PyYAML or ruamel.yaml; or set KREW_HERMES_PYTHON); add by hand to {cfg_path}:\n" + json.dumps({"hooks": {"pre_tool_call": entries}}, indent=2))
            continue
        cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else None) or {}
        cfg, changed = merge_hooks(cfg, entries)
        say(f"hooks    {prof}: " + (f"add {', '.join(changed)}" if changed else "already has them"))
        if changed and not dry:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            if cfg_path.is_file():
                shutil.copyfile(cfg_path, cfg_path.with_suffix(".yaml.pre-krew-crew"))
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if a.approve_hooks and not dry:
            ok = approve_hooks(cfg_path.parent, cfg)
            say(f"approve  {prof}: " + (f"{len(ok)} hook(s) recorded in shell-hooks-allowlist.json" if ok
                                         else "every hook already approved"))
    # 3b. crew skills into the profiles that carry them (profile-kit.json skills; idempotent, a newer copy replaces)
    sk = {n: v for n, v in k.get("skills", {}).items() if not n.startswith("_") and isinstance(v, list)}
    for prof in profiles:
        mine = sorted(n for n, profs in sk.items() if prof in profs)
        for n in mine:
            src, to = REPO / "skills" / n, hh / "profiles" / prof / "skills" / "krew" / n
            if not (src / "SKILL.md").is_file():
                say(f"skill    {prof}: {n} not in this repo (another krew package ships it)")
                continue
            say(f"skill    {prof}: {n} -> {to}")
            if not dry:
                if to.exists():
                    shutil.rmtree(to)
                shutil.copytree(src, to, ignore=IGNORE)

    # 3c. the crew's hermes cron jobs (placeholder schedule: a unit or the window ticker fires each one)
    cj = json.loads((CREW / "config" / "crew-crons.json").read_text(encoding="utf-8"))
    hermes = os.environ.get("KREW_HERMES_BIN") or shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    for j in cj["jobs"]:
        have = False
        jp = hh / "profiles" / j["profile"] / "cron" / "jobs.json"
        if jp.is_file():
            try:
                have = any(x.get("name") == j["name"] for x in json.loads(jp.read_text(encoding="utf-8")).get("jobs", []))
            except (ValueError, AttributeError):
                have = False
        if have:
            say(f"cron     {j['profile']}/{j['name']}: present")
            continue
        cmd = [hermes, "-p", j["profile"], "cron", "create", cj["schedule"], j["prompt"], "--name", j["name"],
               "--deliver", j.get("deliver") or "local"] + [x for s_ in j["skills"] for x in ("--skill", s_)]
        if os.environ.get("KREW_CRON_MODEL"):
            cmd += ["--model", os.environ["KREW_CRON_MODEL"]]
        if os.environ.get("KREW_CRON_PROVIDER"):
            cmd += ["--provider", os.environ["KREW_CRON_PROVIDER"]]
        if not a.crons or dry:
            say(f"cron     {j['profile']}/{j['name']}: missing" + ("" if a.crons else " (--crons creates it)"))
            continue
        say(f"cron     {j['profile']}/{j['name']}: " + _cron_create(cmd, hh))

    # 3d. a profile's own cron jobs (profile-kit.json `crons`: a real schedule, a monitor script, the prompt pinned
    # from the script's CRON_PROMPT so the job and the script never drift)
    for name, j in kit_crons(k).items():
        prof = j["profile"]
        pdir = hh / "profiles" / prof
        jp = pdir / "cron" / "jobs.json"
        mon = j.get("monitor_script") or ""
        if mon:
            src, to = CREW / "scripts" / mon, pdir / "scripts" / mon
            if not src.is_file():
                say(f"cron     {prof}/{name}: monitor script {mon} is not in this repo")
                continue
            say(f"cron     {prof}/{name}: monitor {src.name} -> {to}")
            if not dry:
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, to)
        have = False
        if jp.is_file():
            try:
                have = any(x.get("name") == name for x in json.loads(jp.read_text(encoding="utf-8")).get("jobs", []))
            except (ValueError, AttributeError):
                have = False
        if have:
            if j.get("prompt_from") and not dry:
                try:
                    pinned = pin_cron_prompt(CREW / "scripts" / j["prompt_from"], jp)
                except Exception as e:  # noqa: BLE001 - a prompt that cannot be pinned is said, never hidden
                    say(f"cron     {prof}/{name}: present; prompt NOT pinned ({type(e).__name__}: {e})")
                    continue
                say(f"cron     {prof}/{name}: present" + (", prompt pinned" if pinned else ", prompt current"))
            else:
                say(f"cron     {prof}/{name}: present")
            continue
        try:
            prompt = cron_prompt(CREW / "scripts" / j["prompt_from"]) if j.get("prompt_from") else str(j.get("prompt") or "")
        except Exception as e:  # noqa: BLE001
            say(f"cron     {prof}/{name}: no prompt ({type(e).__name__}: {e})")
            continue
        cmd = [hermes, "-p", prof, "cron", "create", j["schedule"], prompt, "--name", name,
               "--deliver", j.get("deliver") or "local"]
        if mon:
            cmd += ["--monitor-script", mon]
        if j.get("paused"):      # a card generator starts paused on a fresh install; `hermes -p <p> cron resume <name>`
            cmd += ["--paused", "--paused-reason", "krew installs card generators paused; resume when you want it"]
        if j.get("reasoning_effort"):
            cmd += ["--reasoning-effort", str(j["reasoning_effort"])]
        if os.environ.get("KREW_CRON_MODEL"):
            cmd += ["--model", os.environ["KREW_CRON_MODEL"]]
        if os.environ.get("KREW_CRON_PROVIDER"):
            cmd += ["--provider", os.environ["KREW_CRON_PROVIDER"]]
        if not a.crons or dry:
            say(f"cron     {prof}/{name}: missing" + ("" if a.crons else " (--crons creates it)"))
            continue
        got = _cron_create(cmd, hh)
        say(f"cron     {prof}/{name}: " + got + (f" (paused: hermes -p {prof} cron resume {name} starts it)"
                                                  if j.get("paused") and got == "created" else ""))

    if not profiles:
        say("\nHooks (not written; --profiles a,b to write them). The reference placement:")
        for name, h in kit_hooks(k).items():
            say(f"  {name:13} {(h.get('script') or h.get('path') or ''):24} on {', '.join(h['profiles'])}")
    say("\nPlugins (install with scripts/krew-install.py, then enable per profile):")
    for p, profs in kit_plugins(k).items():
        say(f"  {p:20} on {', '.join(profs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
