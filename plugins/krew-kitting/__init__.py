"""krew-kitting — allowlist for crew skill indexes.

Hermes only has ``skills.disabled`` (a denylist). New bundled SKILL.md
files are ON until someone adds the name. This plugin is the house

that profile. Toolsets and plugin tools are not skills and are not
touched.

Opt out: ``KREW_KITTING=0``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - the pinned Hermes venv ships ruamel.yaml; Hermes's own reader wraps it
    try:
        import hermes_yaml as yaml
    except ImportError:
        yaml = None  # type: ignore[assignment]

PLUGIN_DIR = Path(__file__).resolve().parent
PACKS_ROOT = Path(os.path.expanduser(os.environ.get("KREW_CREW_ROOT")
                                     or os.path.join(os.environ.get("KREW_HOME") or "~/.krew", "crew")))
ALLOWLIST_PATH = PLUGIN_DIR / "allowlist.yaml"
KREW_SKILLS_ROOT = PACKS_ROOT / "skills"
PLUGIN_NAME = "krew-kitting"
CREW = ("ada", "bash", "judge", "lila", "scout", "steward", "vera", "zoey")


def _env_off() -> bool:
    return os.environ.get("KREW_KITTING", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def load_allowlist(path: Path | None = None) -> dict:
    src = path or ALLOWLIST_PATH
    if yaml is None:
        raise RuntimeError("pyyaml required for krew-kitting")
    data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    bundled = data.get("bundled") or {}
    out: dict[str, set[str]] = {}
    for name, names in bundled.items():
        if names is None:
            names = []
        out[str(name)] = {str(n).strip() for n in names if str(n).strip()}
    return out


def krew_skill_catalog(root: Path | None = None) -> set[str]:
    base = root or KREW_SKILLS_ROOT
    if not base.is_dir():
        return set()
    return {p.parent.name for p in base.rglob("SKILL.md")}


def iter_skill_md(skills_dir: Path) -> Iterable[Path]:
    if not skills_dir.is_dir():
        return
    for md in skills_dir.rglob("SKILL.md"):
        if any(part in {".git", "__pycache__", "venv", ".venv", "node_modules"} for part in md.parts):
            continue
        yield md


def discover_skills(skills_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for md in iter_skill_md(skills_dir):
        name = md.parent.name
        if not name or "." in name:
            continue  # junk like pretext.bak
        found[name] = md
    return found


def is_krew_skill(name: str, md: Path, catalog: set[str]) -> bool:
    if name in catalog:
        return True
    return "house" in md.parts


class Kit:
    def __init__(
        self,
        profile: str,
        allowed: set[str] | None = None,
        disabled: list[str] | None = None,
        discovered: set[str] | None = None,
        krew_own: set[str] | None = None,
    ) -> None:
        self.profile = profile
        self.allowed = allowed or set()
        self.disabled = disabled or []
        self.discovered = discovered or set()
        self.krew_own = krew_own or set()


def plan_profile(
    profile: str,
    skills_dir: Path,
    *,
    allowlist: dict[str, set[str]] | None = None,
    catalog: set[str] | None = None,
) -> Kit:
    allowlist = allowlist if allowlist is not None else load_allowlist()
    catalog = catalog if catalog is not None else krew_skill_catalog()
    found = discover_skills(skills_dir)
    krew_own = {n for n, md in found.items() if is_krew_skill(n, md, catalog)}
    bundled = set(allowlist.get(profile, set()))
    allowed = bundled | krew_own
    disabled = sorted(n for n in found if n not in allowed)
    return Kit(
        profile=profile,
        allowed=allowed,
        disabled=disabled,
        discovered=set(found),
        krew_own=krew_own,
    )


def universe_bundled_names(
    extra_roots: Iterable[Path] = (),
    catalog: set[str] | None = None,
) -> set[str]:
    """Nous-or-third-party skill names we must be able to disable."""
    catalog = catalog if catalog is not None else krew_skill_catalog()
    names: set[str] = set()

    # is a PR workspace and can lag (or sit on a kanban branch). Pin both so
    # pack YAML recovers the trimmed belt after an update, not only names the
    # fork currently has.
    live_agent = Path.home() / ".hermes" / "hermes-agent" / "skills"
    fork_agent = Path.home() / "projects" / "hermes-agent" / "skills"
    roots = [live_agent, fork_agent, *extra_roots]
    for root in roots:
        if not root.is_dir():
            continue
        for md in iter_skill_md(root):
            name = md.parent.name
            if not name or "." in name:
                continue
            if name in catalog or "house" in md.parts:
                continue
            names.add(name)
    return names


def pack_disabled_list(profile: str, universe: set[str], allowlist: dict[str, set[str]]) -> list[str]:
    keep = set(allowlist.get(profile, set()))
    return sorted(universe - keep)


_KEY_LINE = re.compile(r"^([ \t]*)([A-Za-z0-9_]+):\s*(.*)$")


def _replace_list_under(text: str, parent: str, key: str, items: list[str]) -> str:
    """Replace ``parent.key`` sequence in YAML text; keep the rest of the file."""
    lines = text.splitlines(keepends=True)
    parent_i = None
    parent_indent = ""
    for i, line in enumerate(lines):
        m = _KEY_LINE.match(line.rstrip("\n"))
        if m and m.group(2) == parent and not m.group(1):
            # top-level only — skills: / plugins: sit at column 0 in these files
            parent_i = i
            parent_indent = m.group(1)
            rest = m.group(3).strip()
            if rest and rest != "|":
                parent_i = i  # still a mapping start if empty
            break
        if m and m.group(2) == parent:
            parent_i = i
            parent_indent = m.group(1)
            break
    if parent_i is None:
        raise ValueError(f"no {parent}: key")

    parent_len = len(parent_indent)
    end_parent = len(lines)
    for j in range(parent_i + 1, len(lines)):
        raw = lines[j]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _KEY_LINE.match(raw.rstrip("\n"))
        if m and len(m.group(1)) <= parent_len:
            end_parent = j
            break

    key_i = None
    key_indent = parent_indent + "  "
    inline = None
    for j in range(parent_i + 1, end_parent):
        m = _KEY_LINE.match(lines[j].rstrip("\n"))
        if not m:
            continue
        if m.group(2) != key:
            continue
        if len(m.group(1)) <= parent_len:
            continue
        key_i = j
        key_indent = m.group(1)
        inline = m.group(3).strip()
        break

    item_indent = key_indent + "  "

    def _nl(sample: str) -> str:
        return "\r\n" if sample.endswith("\r\n") else "\n"

    def _block(indent_key: str, indent_item: str, nlt: str) -> list[str]:
        if not items:
            return [f"{indent_key}{key}: []{nlt}"]
        out = [f"{indent_key}{key}:{nlt}"]
        for name in items:
            out.append(f"{indent_item}- {name}{nlt}")
        return out

    if key_i is None:
        insert_at = parent_i + 1
        nlt = _nl(lines[parent_i]) if parent_i < len(lines) else "\n"
        return "".join(
            lines[:insert_at] + _block(key_indent, item_indent, nlt) + lines[insert_at:]
        )

    drop_from = key_i
    drop_to = key_i + 1
    nlt = _nl(lines[key_i])
    if inline in ("[]",) or (inline.startswith("[") and inline.endswith("]")):
        drop_to = key_i + 1
    else:
        for j in range(key_i + 1, end_parent):
            s = lines[j]
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            stripped = s.lstrip()
            indent = len(s) - len(s.lstrip(" \t"))
            if stripped.startswith("- ") and indent >= len(key_indent):
                item_indent = s[:indent]
                drop_to = j + 1
                continue
            break
        else:
            drop_to = end_parent
        # include trailing comments/blanks already skipped; drop_to is last item+1
    return "".join(lines[:drop_from] + _block(key_indent, item_indent, nlt) + lines[drop_to:])


def ensure_plugin_enabled_text(text: str) -> str:
    data = yaml.safe_load(text) or {}
    enabled = ((data.get("plugins") or {}).get("enabled")) if isinstance(data.get("plugins"), dict) else None
    if isinstance(enabled, list) and PLUGIN_NAME in enabled:
        return text
    if enabled == [] or enabled is None:
        # empty list — replace plugins.enabled
        try:
            return _replace_list_under(text, "plugins", "enabled", [PLUGIN_NAME])
        except ValueError:
            pass
        if "\nplugins:\n" in text or text.startswith("plugins:"):
            return text.replace(
                "plugins:\n  enabled: []",
                "plugins:\n  enabled:\n    - krew-kitting",
                1,
            )
    try:
        current = list(enabled or [])
        if PLUGIN_NAME not in current:
            current.append(PLUGIN_NAME)
        return _replace_list_under(text, "plugins", "enabled", current)
    except ValueError:
        return text


def apply_config_text(
    text: str,
    kit: Kit,
    *,
    universe: set[str] | None = None,
    allowlist: dict[str, set[str]] | None = None,
) -> str:
    """Rewrite skills.disabled (+ enable this plugin). Pack configs use universe."""
    allowlist = allowlist if allowlist is not None else load_allowlist()
    disabled = list(kit.disabled)
    if universe is not None:
        disabled = pack_disabled_list(kit.profile, universe, allowlist)
    out = _replace_list_under(text, "skills", "disabled", disabled)
    out = ensure_plugin_enabled_text(out)
    return out


def apply_profile(
    profile_dir: Path,
    *,
    pack_config: Path | None = None,
    allowlist: dict[str, set[str]] | None = None,
    universe: set[str] | None = None,
) -> Kit:
    profile = profile_dir.name
    allowlist = allowlist if allowlist is not None else load_allowlist()
    catalog = krew_skill_catalog()
    kit = plan_profile(
        profile,
        profile_dir / "skills",
        allowlist=allowlist,
        catalog=catalog,
    )
    cfg = profile_dir / "config.yaml"
    if cfg.is_file():
        original = cfg.read_text(encoding="utf-8")
        updated = apply_config_text(
            original, kit, universe=None, allowlist=allowlist
        )
        if updated != original:
            cfg.write_text(updated, encoding="utf-8")
    if pack_config is not None and pack_config.is_file():
        original = pack_config.read_text(encoding="utf-8")
        updated = apply_config_text(
            original, kit, universe=universe, allowlist=allowlist
        )
        if updated != original:
            pack_config.write_text(updated, encoding="utf-8")
    _clear_skill_cache()
    return kit


def _clear_skill_cache() -> None:
    try:
        from agent import skill_utils  # type: ignore

        cache = getattr(skill_utils, "_RAW_CONFIG_CACHE", None)
        if isinstance(cache, dict):
            cache.clear()
    except Exception:
        pass


def _multiplex_on() -> bool:
    """True only under ONE hermes host gateway serving several profiles in this process. Off:
    one profile per process, and everything in this file does what it always did."""
    try:
        from agent.secret_scope import is_multiplex_active
        return bool(is_multiplex_active())
    except Exception:  # noqa: BLE001 - an older hermes has no such thing
        return False


def _home_key() -> str:
    """The served profile's stable key (hermes_constants.hermes_home_key), "" when unavailable."""
    try:
        from hermes_constants import hermes_home_key
        return str(hermes_home_key())
    except Exception:  # noqa: BLE001
        return ""


def _install_runtime_filter(disabled: set[str]) -> None:
    """In-process belt: union computed disabled even if config is stale.

    Under ONE host gateway the wrapped function is every served profile's, and each register()
    stacked another wrapper on it: after N profiles a skill was hidden by the UNION of all their
    belts (a skill on Zoey's keep-list but disabled for steward vanished for Zoey too). There the
    belt is a table keyed by the served home, installed once. One profile per process: unchanged.
    """
    try:
        from agent import skill_utils  # type: ignore
    except Exception:
        return
    if _multiplex_on():
        table = getattr(skill_utils, "_krew_kitting_disabled", None)
        if table is None:
            table = {}
            skill_utils._krew_kitting_disabled = table
            keyed_orig = skill_utils.get_disabled_skill_names

            def keyed(platform=None):
                return set(keyed_orig(platform)) | table.get(_home_key(), set())

            skill_utils.get_disabled_skill_names = keyed  # type: ignore[method-assign]
        table[_home_key()] = set(disabled)
        _clear_skill_cache()
        return
    orig = skill_utils.get_disabled_skill_names

    def wrapped(platform=None):
        return set(orig(platform)) | disabled

    skill_utils.get_disabled_skill_names = wrapped  # type: ignore[method-assign]
    _clear_skill_cache()


def _profile_dir_from_env() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        env = os.environ.get("HERMES_HOME", "").strip()
        if env:
            return Path(env)
        return Path.home() / ".hermes"


def apply_crew(
    *,
    live_root: Path | None = None,
    packs_root: Path | None = None,
    write: bool = True,
) -> dict[str, Kit]:
    """Rewrite pack + live ``skills.disabled`` from the allowlist.

    Live and pack get the same denylist: (known Nous names) − (keep).
    Krew skills are never on that list.
    """
    live_root = live_root or (Path.home() / ".hermes" / "profiles")
    packs_root = packs_root or PACKS_ROOT
    allowlist = load_allowlist()
    catalog = krew_skill_catalog()
    extra = [live_root / n / "skills" for n in CREW]
    universe = universe_bundled_names(extra_roots=extra, catalog=catalog)
    results: dict[str, Kit] = {}
    for name in CREW:
        profile_dir = live_root / name
        kit = plan_profile(
            name,
            profile_dir / "skills",
            allowlist=allowlist,
            catalog=catalog,
        )
        nous = universe | (kit.discovered - kit.krew_own)
        disabled = pack_disabled_list(name, nous, allowlist)
        kit.disabled = disabled
        results[name] = kit
        if not write:
            continue
        for cfg in (
            profile_dir / "config.yaml",
            packs_root / "distributions" / name / "config.yaml",
        ):
            if not cfg.is_file():
                continue
            original = cfg.read_text(encoding="utf-8")
            updated = apply_config_text(
                original, kit, universe=nous, allowlist=allowlist
            )
            if updated != original:
                cfg.write_text(updated, encoding="utf-8")
    if write:
        _clear_skill_cache()
    return results


def register(ctx) -> None:
    if _env_off():
        return
    profile_dir = _profile_dir_from_env()
    if profile_dir.name not in CREW:
        return
    kit = apply_profile(profile_dir)
    _install_runtime_filter(set(kit.disabled))
