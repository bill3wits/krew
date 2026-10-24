"""The allow-list manifest (config/krew-packages.json), kept honest by recounting.

Every file the repo carries sits in exactly one package; every file a package names exists; and every import in
every shipped Python file is PLACED: the standard library, a module shipped beside it (same folder), a module the
Hermes runtime provides (plugins only), or a third-party module the manifest declares optional for that package.
Anything else fails, naming the file and the import. The checker is a plain function, so the planted tests at
the bottom prove it fails on the shapes it exists to catch.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS = "config/krew-packages.d"


def load_manifest(root: Path = ROOT) -> dict:
    """config/krew-packages.json plus every fragment in config/krew-packages.d/*.json (sorted by name).

    A fragment adds packages (and import names) with the same shape as the main file, so parallel lifts each own
    one file. A package name defined twice is an error, never a silent override. The fragment files themselves
    are placed by a `manifest-fragments` package added here, so no lane has to edit the main file to ship one."""
    man = json.loads((root / "config" / "krew-packages.json").read_text(encoding="utf-8"))
    frags = sorted((root / FRAGMENTS).glob("*.json")) if (root / FRAGMENTS).is_dir() else []
    for f in frags:
        d = json.loads(f.read_text(encoding="utf-8"))
        for name, pkg in (d.get("packages") or {}).items():
            if name in man["packages"]:
                raise ValueError(f"{FRAGMENTS}/{f.name}: package {name!r} is already defined")
            man["packages"][name] = pkg
        for k, names in (d.get("imports") or {}).items():
            have = man["imports"].setdefault(k, [])
            have += [n for n in names if n not in have]
    if frags and "manifest-fragments" not in man["packages"]:
        man["packages"]["manifest-fragments"] = {
            "kind": "config", "about": "Manifest fragments: each adds packages to config/krew-packages.json.",
            "files": [f"{FRAGMENTS}/*.json"]}
    return man


MAN = load_manifest()
STD = set(sys.stdlib_module_names) | {"__future__"}


def repo_files(root: Path = ROOT) -> list[str]:
    """Tracked files when this is a git checkout, else every file (minus caches)."""
    r = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip() and (root / ".git").exists():
        return sorted(r.stdout.split())
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not any(x in rel.split("/") for x in ("__pycache__", ".git", ".pytest_cache")):
            out.append(rel)
    return sorted(out)


def imports_of(src: str) -> list[tuple[str, int]]:
    """(module, level) for every import in the source, at any depth (inside functions and try blocks too)."""
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out += [(a.name, 0) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                if n.module:
                    out.append((n.module, n.level))
                else:
                    out += [(a.name, n.level) for a in n.names]
            else:
                out.append((n.module or "", 0))
    return out


def unplaced(root: Path, man: dict, files: list[str]) -> list[str]:
    """Every import in every .py of every package that is not placed. [] = clean."""
    bad = []
    hermes = set(man["imports"]["hermes_provided"])
    for name, pkg in man["packages"].items():
        allow_hermes = pkg.get("kind") == "plugin" or bool(pkg.get("runs_in_hermes_env"))
        optional = set(pkg.get("optional_imports") or []) | set(pkg.get("absent_ok_imports") or {})
        local = {p.stem for d in pkg.get("local_paths") or [] for p in (root / d).glob("*.py")}
        test_only = set(man["imports"].get("test_only") or []) if pkg.get("kind") == "tests" else set()
        for rel in pkg_files(pkg, files):
            if not rel.endswith(".py"):
                continue
            here = {p.stem for p in (root / rel).parent.glob("*.py")}
            for mod, level in imports_of((root / rel).read_text(encoding="utf-8")):
                top = mod.split(".")[0]
                if level:
                    if level == 1 and top in here:
                        continue
                    bad.append(f"{rel}: relative import {'.' * level}{mod} is not a file beside it")
                elif top in STD or top in here or top in local:
                    continue
                elif allow_hermes and top in hermes:
                    continue
                elif top in optional or top in test_only:
                    continue
                else:
                    bad.append(f"{rel}: import {mod} is not placed (stdlib / beside it / hermes_provided / optional)")
    return bad


def unguarded_absent(root: Path, man: dict, files: list[str]) -> list[str]:
    """An import the manifest says may be ABSENT must sit inside a try that catches its ImportError."""
    bad = []
    for name, pkg in man["packages"].items():
        absent = set(pkg.get("absent_ok_imports") or {})
        if not absent:
            continue
        for rel in pkg_files(pkg, files):
            if not rel.endswith(".py"):
                continue
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
            guarded = set()
            for t in ast.walk(tree):
                if isinstance(t, ast.Try) and any(h.type is None or any(
                        getattr(n, "id", "") in ("ImportError", "ModuleNotFoundError", "Exception")
                        for n in ast.walk(h.type)) for h in t.handlers):
                    for s in t.body:
                        guarded |= {id(n) for n in ast.walk(s)}
            for n in ast.walk(tree):
                mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module or ""] if isinstance(n, ast.ImportFrom) and not n.level else []
                for m in mods:
                    if m.split(".")[0] in absent and id(n) not in guarded:
                        bad.append(f"{rel}:{n.lineno}: import {m} may be absent but is not inside a try")
    return bad


def pkg_files(pkg: dict, files: list[str]) -> list[str]:
    return [f for f in files if any(fnmatch.fnmatchcase(f, g) for g in pkg["files"])]


def placement(man: dict, files: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(files in no package, files in two or more, package globs that match nothing)."""
    owner: dict[str, list[str]] = {f: [] for f in files}
    empty = []
    for name, pkg in man["packages"].items():
        for g in pkg["files"]:
            hit = [f for f in files if fnmatch.fnmatchcase(f, g)]
            if not hit and not pkg.get("composed"):
                empty.append(f"{name}: {g}")
            for f in hit:
                owner[f].append(name)
    return ([f for f, o in owner.items() if not o], [f"{f}: {o}" for f, o in owner.items() if len(o) > 1], empty)


FILES = repo_files()


def test_every_file_is_in_exactly_one_package():
    none, many, _ = placement(MAN, FILES)
    assert not none, "files in no package (add them to config/krew-packages.json or drop them):\n" + "\n".join(none)
    assert not many, "files in two packages:\n" + "\n".join(many)


def test_every_package_glob_matches_a_file():
    _, _, empty = placement(MAN, FILES)
    assert not empty, "manifest entries that match no file:\n" + "\n".join(empty)


def test_every_import_is_placed():
    bad = unplaced(ROOT, MAN, FILES)
    assert not bad, "\n".join(bad)


def test_absent_ok_imports_are_guarded_and_really_absent():
    assert not unguarded_absent(ROOT, MAN, FILES)
    for name, pkg in MAN["packages"].items():
        for m in pkg.get("absent_ok_imports") or {}:
            assert not list(ROOT.rglob(f"{m}.py")), f"{m} ships now: drop it from absent_ok_imports"


def test_krew_settings_copies_are_identical():
    copies = sorted(ROOT.glob("plugins/*/krew_settings.py"))
    assert len(copies) >= 2
    first = copies[0].read_bytes()
    differ = [str(c.relative_to(ROOT)) for c in copies if c.read_bytes() != first]
    assert not differ, f"krew_settings.py copies differ from {copies[0].relative_to(ROOT)}: {differ}"


def test_every_plugin_has_a_manifest_entry_and_a_plugin_yaml():
    import re
    for d in sorted((ROOT / "plugins").iterdir()):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        name = re.search(r"^name:\s*(\S+)\s*$", (d / "plugin.yaml").read_text(encoding="utf-8"), re.M).group(1)
        assert name == d.name, f"{d.name}/plugin.yaml names {name!r}"
        assert d.name in MAN["packages"] and MAN["packages"][d.name]["kind"] == "plugin"


def test_composed_package_is_present_in_a_built_repo():
    """A package the build composes (none since zoeyos-wall became krew's own, 2026-10-06) must be whole in a built repo."""
    for name, pkg in MAN["packages"].items():
        if pkg.get("composed") and (ROOT / "plugins" / name).exists():
            _, _, empty = placement({"packages": {name: {**pkg, "composed": False}}}, FILES)
            assert not empty, f"{name} is part-built: {empty}"


# ---- planted: the checker fails on what it exists to catch ----------------------------------------

def _tree(tmp_path: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return sorted(files)


MINI = {"imports": {"hermes_provided": ["agent", "tools"], "test_only": ["pytest"]},
        "packages": {"p": {"kind": "plugin", "files": ["plugins/p/*"]},
                     "s": {"kind": "scripts", "files": ["scripts/*"]}}}


def test_planted_unplaced_import_fails(tmp_path):
    files = _tree(tmp_path, {"plugins/p/__init__.py": "from . import helper\nimport requests\n",
                             "plugins/p/helper.py": "import json\n"})
    bad = unplaced(tmp_path, MINI, files)
    assert len(bad) == 1 and "import requests is not placed" in bad[0]


def test_planted_private_module_import_fails(tmp_path):
    files = _tree(tmp_path, {"plugins/p/__init__.py": "def f():\n    import my_private_lib\n"})
    assert unplaced(tmp_path, MINI, files), "an import buried in a function must still be found"


def test_planted_hermes_import_outside_a_plugin_fails(tmp_path):
    files = _tree(tmp_path, {"scripts/x.py": "from agent import y\n"})
    assert unplaced(tmp_path, MINI, files)


def test_planted_relative_import_of_a_missing_file_fails(tmp_path):
    files = _tree(tmp_path, {"plugins/p/__init__.py": "from . import ghost\n"})
    assert unplaced(tmp_path, MINI, files)


def test_planted_unguarded_absent_import_fails(tmp_path):
    man = {"imports": MINI["imports"], "packages": {"s": {"kind": "scripts", "files": ["scripts/*"],
                                                        "absent_ok_imports": {"ghost": "x"}}}}
    files = _tree(tmp_path, {"scripts/a.py": "import ghost\n", "scripts/b.py": "try:\n    import ghost\nexcept ImportError:\n    ghost = None\n"})
    bad = unguarded_absent(tmp_path, man, files)
    assert len(bad) == 1 and bad[0].startswith("scripts/a.py")


def test_fragments_add_packages_and_place_themselves(tmp_path):
    (tmp_path / "config" / "krew-packages.d").mkdir(parents=True)
    (tmp_path / "config" / "krew-packages.json").write_text(json.dumps(MINI))
    (tmp_path / "config" / "krew-packages.d" / "lane.json").write_text(json.dumps(
        {"imports": {"test_only": ["hypothesis"]}, "packages": {"x": {"kind": "scripts", "files": ["x/*"]}}}))
    man = load_manifest(tmp_path)
    assert man["packages"]["x"]["files"] == ["x/*"] and "hypothesis" in man["imports"]["test_only"]
    files = _tree(tmp_path, {"x/a.py": "", "plugins/p/__init__.py": ""}) + ["config/krew-packages.d/lane.json"]
    none, many, _ = placement(man, files)
    assert none == [] and many == []


def test_a_fragment_cannot_redefine_a_package(tmp_path):
    (tmp_path / "config" / "krew-packages.d").mkdir(parents=True)
    (tmp_path / "config" / "krew-packages.json").write_text(json.dumps(MINI))
    (tmp_path / "config" / "krew-packages.d" / "lane.json").write_text(json.dumps(
        {"packages": {"p": {"kind": "plugin", "files": ["plugins/p/*"]}}}))
    with pytest.raises(ValueError, match="already defined"):
        load_manifest(tmp_path)


def test_planted_stray_file_fails(tmp_path):
    files = _tree(tmp_path, {"plugins/p/__init__.py": "", "notes/transcript.txt": "x"})
    none, _, _ = placement(MINI, files)
    assert none == ["notes/transcript.txt"]
