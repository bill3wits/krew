""

















from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PROFILE_FILE = Path(os.environ.get("MS_PROFILE_FILE") or (REPO / "config" / "ms-profile.yaml"))


def krew_home() -> Path:
    return Path(os.environ.get("KREW_HOME") or (Path.home() / ".krew")).expanduser()


def _xdg_state_dir() -> Path:
    return krew_home() / "ms"



DEFAULTS: dict[str, Any] = {
    "integrations": "none",
    "upgrades": False,
    "discovery": {},
    "state_dir": str(_xdg_state_dir()),
    "env_roots": [],
    "envs": {},
    "retired_envs": [],
    "canary": {"enabled": False},
    "occupancy": {"enabled": False, "mode_file": "", "train_busy_file": ""},
    "health": {"lanes": {}, "critical": [], "optional": []},
    "hindsight": {"env_file": "", "err_log": "", "retain_tokens_max": 0, "default_llm_base_url": ""},
    "kanban": {"enabled": False, "profile": "", "assignees": {}, "max_mitigation_envs": 3},
    "notify": {"cmd": []},
    "deliverables_dir": "",
    "manifest": {"live_path": ""},
    "hold_file": "",
    "holds": {"ttl_anchor": ""},
    "roots_file": "",
    "text_file": "",
    "render": {"enabled": False, "comfy_root": "", "comfy_stats_url": "", "recipes": ""},
    "hermes_profiles": {"enabled": False, "names": [], "workers": [], "packs_dir": "",
                        "live_dir": "", "config_floor": 0},
    "patch_queue": {"enabled": False},
    "doctor": {"upstream_apis_file": ""},
    "os_lane": "auto",


    "alerts": {"promoted_rollbacks": 2},
}


def _yaml_load(text: str):
    if not text.strip():
        return {}
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        pass
    try:
        from ruamel.yaml import YAML
    except ImportError:
        return json.loads(text)
    return json.loads(json.dumps(YAML(typ="safe", pure=True).load(text) or {}, default=str))


def _yaml_dump(doc: dict) -> str:
    try:
        import yaml
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    except ImportError:
        pass
    import io
    from ruamel.yaml import YAML
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    buf = io.StringIO()
    y.dump(doc, buf)
    return buf.getvalue()


def _load_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    data = _yaml_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def _merge(base: dict, over: dict) -> dict:
    ""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _settings_file(profile: dict) -> Path:
    if os.environ.get("MS_SETTINGS"):
        return Path(os.environ["MS_SETTINGS"]).expanduser()
    return krew_home() / "ms.yaml"


def load() -> dict:
    ""
    sources: list[str] = []
    s = copy.deepcopy(DEFAULTS)
    wanted = (os.environ.get("MS_INTEGRATIONS") or "").strip().lower()

    use_profile = wanted == "house" or (not wanted and PROFILE_FILE.is_file())
    profile: dict = {}
    if use_profile and wanted != "none":
        profile = _load_file(PROFILE_FILE)
        s = _merge(s, profile)
        sources.append(str(PROFILE_FILE))
    user_file = _settings_file(profile)
    if user_file.is_file():
        user = _load_file(user_file)
        if str(user.get("integrations") or "").lower() == "none" and profile:
            s = _merge(copy.deepcopy(DEFAULTS), user)
            sources = []
        else:
            s = _merge(s, user)
        sources.append(str(user_file))
    if wanted:
        s["integrations"] = wanted
    s["_sources"] = sources
    return s


SETTINGS: dict = load()


def expand(v: Any) -> str:
    ""

    import sys
    v = str(v or "")
    if not v:
        return ""
    v = v.replace("{repo}", str(REPO)).replace("{python}", sys.executable)
    v = v.replace("{krew_repo}", str(REPO.parent)).replace("${KREW_HOME}", str(krew_home()))
    if "${MS_ENVS_ROOT}" in v and not os.environ.get("MS_ENVS_ROOT"):
        v = v.replace("${MS_ENVS_ROOT}", str(krew_home() / "envs"))
    return str(Path(os.path.expandvars(v)).expanduser())


def get(path: str, default: Any = None, *, env: str | None = None, settings: dict | None = None) -> Any:
    ""
    if env and os.environ.get(env) not in (None, ""):
        return os.environ[env]
    node: Any = settings if settings is not None else SETTINGS
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def state_dir() -> Path:
    return Path(expand(get("state_dir", env="MS_OUT")))


def env_roots() -> list[Path]:
    if os.environ.get("MS_ENVS_ROOT"):
        return [Path(os.environ["MS_ENVS_ROOT"])]
    return [Path(expand(p)) for p in (get("env_roots") or [])]


def export_env() -> None:
    ""

    os.environ.setdefault("MS_OUT", str(state_dir()))
    roots = env_roots()
    if roots:
        os.environ.setdefault("MS_ENVS_ROOT", str(roots[0]))
    rf = get("roots_file")
    if rf:
        os.environ.setdefault("MS_ROOTS_FILE", expand(rf))


def settings_file() -> Path:
    ""
    if os.environ.get("MS_SETTINGS"):
        return Path(os.environ["MS_SETTINGS"]).expanduser()
    return krew_home() / "ms.yaml"


def upgrades_on() -> bool:
    v = get("upgrades", False, env="MS_UPGRADES")
    return str(v).strip().lower() in ("1", "true", "yes", "on")


UPGRADES_OFF_LINE = 'M&S upgrades are OFF (upgrades: false in {}): report only. No package installs, no vLLM canary, no rollbacks, no unit restarts. Set `upgrades: true` there to allow them.'


def register_env(name: str, row: dict, *, health: dict | None = None,
                 hindsight_env_file: str = "", path: Path | None = None) -> Path:
    ""


    p = path or settings_file()
    try:
        doc = _load_file(p) if p.is_file() else {}
    except (OSError, ValueError):
        raise SystemExit(f"{p}: exists but cannot be read - not overwriting it")
    doc.setdefault("envs", {})[name] = row
    if health and health.get("url"):
        h = doc.setdefault("health", {})
        h.setdefault("lanes", {})[name] = {"url": health["url"]}
        crit = [c for c in (h.get("critical") or []) if c != name]
        if health.get("critical"):
            crit.append(name)
        h["critical"] = crit
    if hindsight_env_file:
        doc.setdefault("hindsight", {})["env_file"] = hindsight_env_file
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(_yaml_dump(doc))
    os.replace(tmp, p)
    return p

