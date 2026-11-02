""
























from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]
ROOTS_FILE = Path(os.environ.get("MS_ROOTS_FILE") or Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser() / "ms-env-roots.json")
MODE = os.environ.get("MS_ROOTS", "off").strip().lower() or "off"
try:
    import ms_settings as _MSS
except Exception:
    _MSS = None
OUT_ROOT = Path(os.environ.get("MS_OUT") or (str(_MSS.state_dir()) if _MSS
                else str(Path.home() / ".local" / "state" / "krew" / "ms")))
_COMFY_PREFIX = ((_MSS.expand(_MSS.get("render.comfy_root")).rstrip("/") + "/")
                 if _MSS and _MSS.get("render.comfy_root") else "")
PYPI_DIR = OUT_ROOT / "pypi-requires"
PYPI_MAX = int(os.environ.get("MS_ROOTS_PYPI_MAX", "200"))
PROBE_TIMEOUT = int(os.environ.get("MS_ROOTS_PROBE_TIMEOUT", "600"))
TOOLING = ("pip", "setuptools", "wheel", "uv", "packaging")
BUCKETS = ("green", "risk", "never", "app-pinned", "pinned", "group-wait", "orphan")
_pypi_calls = 0


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "")).lower().split("[")[0].strip()




PROBE = r'''
import ast, importlib.metadata as md, json, os, platform, sys
from pathlib import Path

DUPS = {}

PREFIX = os.path.realpath(sys.prefix)

def dists():
    # LOCAL = installed under this env's own prefix. A .pth can put another env's
    # site-packages on the path (a .pth file pointing into another env):
    # those dists are BORROWED - importable here, installed there.
    out = {}
    for d in md.distributions():
        name = (d.metadata.get("Name") or "").strip()
        if not name:
            continue
        key = name.lower().replace("_", "-").replace(".", "-")
        path = getattr(d, "_path", None)
        real = os.path.realpath(str(path)) if path else ""
        local = real.startswith(PREFIX + os.sep)
        if key in out:
            if out[key]["local"] and local:
                DUPS.setdefault(key, [out[key]["version"]]).append(d.version)
            if out[key]["local"] or not local:
                continue  # the importer sees the first on the path; a local one wins the row
        files = d.files or []
        size = 0
        tops = set()
        for f in files:
            try:
                size += Path(d.locate_file(f)).stat().st_size
            except OSError:
                pass
            parts = Path(str(f)).parts
            if not parts:
                continue
            p0 = parts[0]
            if p0.endswith((".dist-info", ".egg-info", ".data", ".pth")) or p0 in ("..", "__pycache__"):
                continue
            tops.add(p0[:-3] if p0.endswith(".py") else p0.split(".")[0])
        tl = d.read_text("top_level.txt")
        if tl:
            tops |= {x.strip() for x in tl.splitlines() if x.strip()}
        groups = []
        ep = d.read_text("entry_points.txt") or ""
        for line in ep.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                groups.append(line[1:-1].strip())
        editable = ""
        try:
            du = d.read_text("direct_url.json")
            if du and (json.loads(du).get("dir_info") or {}).get("editable"):
                editable = json.loads(du).get("url", "")
        except Exception:
            pass
        try:
            installed = Path(path).stat().st_mtime if path else None
        except OSError:
            installed = None
        out[key] = {"name": name, "version": d.version, "requires": list(d.requires or []),
                    "local": local, "home": "" if local else real.split("/lib/python")[0],
                    "tops": sorted(tops), "size": size, "installed": installed,
                    "installer": (d.read_text("INSTALLER") or "").strip(),
                    "requested": d.read_text("REQUESTED") is not None,
                    "editable": editable, "ep_groups": groups}
    return out

def markers():
    impl = sys.implementation
    return {"implementation_name": impl.name,
            "implementation_version": "{0.major}.{0.minor}.{0.micro}".format(impl.version),
            "os_name": os.name, "platform_machine": platform.machine(),
            "platform_release": platform.release(), "platform_system": platform.system(),
            "platform_version": platform.version(), "python_full_version": platform.python_version(),
            "platform_python_implementation": platform.python_implementation(),
            "python_version": ".".join(platform.python_version_tuple()[:2]),
            "sys_platform": sys.platform}

def scan(entries):
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    seen, third, queue = set(), {}, []
    for e in entries:
        p = Path(e)
        if p.is_dir():
            queue += [(f, p) for f in p.rglob("*.py")]
        elif p.is_file():
            queue.append((p, p.parent))
    while queue:
        f, base = queue.pop()
        f = f.resolve()
        if f in seen:
            continue
        seen.add(f)
        try:
            tree = ast.parse(f.read_text(errors="replace"))
        except (SyntaxError, ValueError, OSError):
            continue
        roots = [f.parent, Path(base), Path(base) / "lib", Path(base).parent / "lib"]
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                if top in stdlib or top == "__future__":
                    continue
                local = None
                for r in roots:
                    # a directory with no __init__.py is a namespace package (ComfyUI's comfy/)
                    for cand in (r / f"{top}.py", r / top / "__init__.py", r / top):
                        if cand.exists() and (cand.is_file() or any(cand.glob("*.py"))):
                            local = cand
                            break
                    if local:
                        break
                if local is None:
                    third.setdefault(top, set()).add(str(f))
                elif local.is_dir():
                    queue += [(x, base) for x in local.rglob("*.py")]
                elif local.name == "__init__.py":
                    queue += [(x, base) for x in local.parent.rglob("*.py")]
                else:
                    queue.append((local, base))
    return {k: sorted(v)[:3] for k, v in third.items()}, len(seen)

cfg = json.loads(sys.argv[1])
imports, n = scan(cfg.get("entries") or [])
ds = dists()
print(json.dumps({"python": sys.executable, "markers": markers(), "dists": ds,
                  "imports": imports, "scanned_files": n, "duplicates": DUPS}))
'''


def expand(p: str) -> str:
    return os.path.expanduser(str(p).replace("@repo", str(REPO)))


def expand_entries(entries: list[str]) -> list[str]:
    ""

    out = []
    for e in entries or []:
        if str(e).startswith("shebang:"):
            d = Path(expand(e[len("shebang:"):]))
            for f in sorted(d.iterdir()) if d.is_dir() else []:
                if f.is_file() and not f.suffix:
                    try:
                        head = f.open("rb").readline(200)
                    except OSError:
                        continue
                    if head.startswith(b"#!") and b"python" in head:
                        out.append(str(f))
        else:
            out.append(expand(e))
    return out


def probe(py: str, entries: list[str], *, timeout: int = PROBE_TIMEOUT) -> dict:
    ""
    argv = [py, "-c", PROBE, json.dumps({"entries": expand_entries(entries)})]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"probe rc={r.returncode}: {(r.stderr or '')[-300:]}")
    return json.loads(r.stdout)


def load_roots(path: Path | None = None) -> dict:
    d = json.loads(Path(path or ROOTS_FILE).read_text())
    return d



def _req(line: str):
    from packaging.requirements import InvalidRequirement, Requirement
    try:
        return Requirement(line)
    except InvalidRequirement:
        return None


def _marker_ok(rq, env: dict, extras: tuple = ("",)) -> bool:
    if rq.marker is None:
        return True
    for x in extras or ("",):
        try:
            if rq.marker.evaluate(dict(env, extra=x)):
                return True
        except Exception:
            return True
    return False


def _is_extra(rq) -> bool:
    return rq.marker is not None and "extra" in str(rq.marker)


def _file_lines(path: str) -> list[str]:
    p = Path(expand(path))
    if not p.is_file():
        return []
    if p.suffix == ".toml":
        import tomllib
        d = tomllib.loads(p.read_text())
        return list((d.get("project") or {}).get("dependencies") or [])
    out = []
    for line in p.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.append(line)
    return out


def _ver(v):
    from packaging.version import InvalidVersion, Version
    try:
        return Version(str(v))
    except InvalidVersion:
        return None


def pypi_requires(name: str, version: str) -> list[str] | None:
    ""

    global _pypi_calls
    PYPI_DIR.mkdir(parents=True, exist_ok=True)
    f = PYPI_DIR / f"{canon(name)}-{version}.json"
    if f.is_file():
        try:
            return json.loads(f.read_text())
        except ValueError:
            pass
    if _pypi_calls >= PYPI_MAX:
        return None
    _pypi_calls += 1
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=20) as r:
            j = json.load(r)
    except Exception:
        return None
    req = (j.get("info") or {}).get("requires_dist")
    if req is None:
        return None
    f.write_text(json.dumps(req))
    return req


def roots_of(env_cfg: dict, dump: dict) -> tuple[list, dict]:
    ""
    from packaging.requirements import Requirement
    dists = dump["dists"]
    out: list = []
    for f in env_cfg.get("files") or []:
        for line in _file_lines(f):
            rq = _req(line)
            if rq is not None:
                out.append((rq, f"file:{Path(expand(f)).name}" if "comfy" not in f
                            else "file:" + (expand(f).replace(_COMFY_PREFIX, "") if _COMFY_PREFIX else expand(f))))
    for a in env_cfg.get("app") or []:
        out.append((Requirement(a), "app"))
    top2 = {}
    for k, d in dists.items():
        for t in d["tops"]:
            top2.setdefault(t, set()).add(k)
    unmapped = {}
    for top, files in (dump.get("imports") or {}).items():
        ks = top2.get(top)
        if ks:
            out += [(Requirement(dists[k]["name"]), "import") for k in sorted(ks)]
        else:
            unmapped[top] = files
    for k, d in dists.items():
        if d.get("editable"):
            out.append((Requirement(d["name"]), "editable"))
    for fr in env_cfg.get("file_roots") or []:
        pat = re.compile(fr["match"])
        for k, d in dists.items():
            if pat.fullmatch(k):
                out.append((Requirement(d["name"]), f"file-root:{fr['by']}"))
    for c in env_cfg.get("clis") or []:
        if canon(c["dist"]) in dists:
            out.append((Requirement(dists[canon(c["dist"])]["name"]), f"cli:{c['by']}"))
    return out, unmapped


def closure(roots: list, dump: dict, env_cfg: dict) -> dict[str, str]:
    ""






    dists, envm = dump["dists"], dump["markers"]
    reach: dict[str, set] = {}
    via: dict[str, str] = {}
    groups = env_cfg.get("entry_point_groups") or {}
    queue = [(canon(rq.name), set(rq.extras), f"root:{src}") for rq, src in roots
             if _marker_ok(rq, envm)]
    while queue:
        while queue:
            k, extras, why = queue.pop()
            if k not in dists:
                continue
            cur = reach.get(k)
            if cur is not None and extras <= cur:
                continue
            reach[k] = (cur or set()) | extras
            via.setdefault(k, why)
            ex = tuple(reach[k])
            for line in dists[k]["requires"]:
                rq = _req(line)
                if rq is None:
                    continue
                if _is_extra(rq) and not ex:
                    continue
                if _marker_ok(rq, envm, ex or ("",)):
                    queue.append((canon(rq.name), set(rq.extras), k))
        for k, d in dists.items():
            if k in reach:
                continue
            for g in d.get("ep_groups") or []:
                host = canon(groups.get(g, ""))
                if host and host != k and host in reach:
                    queue.append((k, set(), f"entry-point:{g}"))
                    break
        for w in env_cfg.get("data_wheels") or []:
            k, host = canon(w["dist"]), canon(w["for"])
            if k in dists and k not in reach and host in reach:
                queue.append((k, set(), f"data-wheel:{host}"))
    for t in TOOLING:
        if t in dists:
            via.setdefault(t, "tooling")
    return via


def app_pins(roots: list, dump: dict) -> dict[str, dict]:
    ""
    dists, envm = dump["dists"], dump["markers"]
    pins: dict[str, dict] = {}
    for rq, src in roots:
        if not (src.startswith("file:") or src == "app"):
            continue
        k = canon(rq.name)
        if any(s.operator in ("==", "===") for s in rq.specifier):
            pins[k] = {"spec": str(rq.specifier), "by": src}
        if src == "app" and k in dists:
            for line in dists[k]["requires"]:
                r2 = _req(line)
                if r2 is None or _is_extra(r2) or not _marker_ok(r2, envm):
                    continue
                if any(s.operator in ("==", "===") for s in r2.specifier):
                    pins.setdefault(canon(r2.name), {"spec": str(r2.specifier),
                                                     "by": f"{dists[k]['name']} {dists[k]['version']}"})
    return pins


def drift(roots: list, dump: dict) -> list[dict]:
    ""

    dists, envm = dump["dists"], dump["markers"]
    specs = []
    for rq, src in roots:
        if src.startswith("file:") and rq.specifier and _marker_ok(rq, envm):
            specs.append((rq, src))
        if src == "app" and canon(rq.name) in dists:
            d = dists[canon(rq.name)]
            for line in d["requires"]:
                r2 = _req(line)
                if r2 is not None and r2.specifier and not _is_extra(r2) and _marker_ok(r2, envm):
                    specs.append((r2, f"{d['name']} {d['version']}"))
    out, seen = [], set()
    for rq, src in specs:
        k = canon(rq.name)
        if k not in dists or (k, src) in seen:
            continue
        v = _ver(dists[k]["version"])
        if v is not None and v not in rq.specifier:
            seen.add((k, src))
            out.append({"package": k, "installed": dists[k]["version"],
                        "root_spec": str(rq.specifier), "root": src})
    return out


def classify_rows(rows: dict[str, list], dump: dict, via: dict, pins: dict, *,
                  classify: Callable[[str, str, str], str] | None = None,
                  requires_of: Callable[[str, str], list | None] = pypi_requires) -> list[dict]:
    ""
    from packaging.specifiers import SpecifierSet
    dists, envm = dump["dists"], dump["markers"]
    flat = []
    for b, lst in (rows or {}).items():
        for r in lst or []:
            if isinstance(r, dict) and r.get("package") and not r.get("truncated"):
                flat.append((b, r))
    outdated = {canon(r["package"]): r for _b, r in flat}

    cons: dict[str, list] = {}
    for pk in via:
        d = dists.get(pk)
        if not d:
            continue
        for line in d["requires"]:
            rq = _req(line)
            if rq is None or not rq.specifier or _is_extra(rq) or not _marker_ok(rq, envm):
                continue
            cons.setdefault(canon(rq.name), []).append((pk, rq.specifier))
    detail, pinners_of = [], {}
    for old, r in flat:
        k = canon(r["package"])
        cur, lat = str(r.get("current") or ""), str(r.get("latest") or "")
        vc, vl = _ver(cur), _ver(lat)
        why = ""
        if k not in via:
            nb = "orphan"
        elif old == "never":
            nb = "never"
        elif k in pins and vl is not None and vl not in SpecifierSet(pins[k]["spec"]):
            nb, why = "app-pinned", f"{pins[k]['by']} {pins[k]['spec']}"
        else:
            excl = [(pk, sp) for pk, sp in cons.get(k, [])
                    if vc is not None and vl is not None and vc in sp and vl not in sp]
            if not excl:
                if old in ("green_candidate", "risk"):
                    nb = "green" if old == "green_candidate" else "risk"
                else:
                    nb = classify(k, cur, lat) if classify else "risk"
                    nb = {"green_candidate": "green"}.get(nb, nb)
                    why = "its only pinners are orphans" if old in ("pinned", "group-wait") else ""
            else:
                freed, notes = True, []
                for pk, _sp in excl:
                    prow = outdated.get(pk)
                    if not prow:
                        freed = False
                        notes.append(f"{pk} {dists[pk]['version']} is its latest")
                        continue
                    req = requires_of(dists[pk]["name"], str(prow.get("latest")))
                    if req is None:
                        freed = False
                        notes.append(f"{pk} {prow.get('latest')} unresolved")
                        continue
                    ok = True
                    for line in req:
                        rq = _req(line)
                        if (rq is not None and canon(rq.name) == k and rq.specifier
                                and not _is_extra(rq) and _marker_ok(rq, envm) and vl not in rq.specifier):
                            ok = False
                            notes.append(f"{pk} {prow.get('latest')} still {rq.specifier}")
                    if ok:
                        notes.append(f"{pk} {prow.get('latest')} frees it")
                    freed &= ok
                nb, why = ("group-wait" if freed else "pinned"), "; ".join(notes)
                pinners_of[k] = [pk for pk, _ in excl]
        detail.append({"package": k, "current": cur, "latest": lat, "old": old, "new": nb, "why": why})

    by = {x["package"]: x for x in detail}
    changed = True
    while changed:
        changed = False
        for x in detail:
            if x["new"] != "group-wait":
                continue
            for pk in pinners_of.get(x["package"], []):
                pb = by.get(pk, {}).get("new", "latest")
                if pb in ("app-pinned", "never", "pinned", "latest"):
                    x["new"] = "app-pinned" if pb == "app-pinned" else "pinned"
                    x["why"] += f" | but {pk} is {pb}"
                    changed = True
                    break
    return detail


def borrowed(dump: dict, via: dict) -> dict[str, list[str]]:
    ""
    out: dict[str, list[str]] = {}
    for k in via:
        d = dump["dists"].get(k)
        if d and d.get("local") is False:
            out.setdefault(d.get("home") or "?", []).append(k)
    return {h: sorted(v) for h, v in out.items()}


def orphans(dump: dict, via: dict, *, env: str, ledger: dict | None = None) -> list[dict]:
    dists = dump["dists"]
    rows = [r for r in ((ledger or {}).get("decisions") or []) if isinstance(r, dict)
            and str(r.get("env") or "").lower() == env.lower()]
    out = []
    for k, d in dists.items():
        if k in via or d.get("local") is False:
            continue
        req_by = sorted(pk for pk, dd in dists.items() if pk != k and any(
            (lambda rq: rq is not None and canon(rq.name) == k)(_req(l)) for l in dd["requires"]))
        src = sorted({str(r.get("source") or "") for r in rows if canon(r.get("package", "")) == k} - {""})
        out.append({
            "package": k, "version": d["version"], "mb": round((d.get("size") or 0) / 1e6, 2),
            "installed": time.strftime("%Y-%m-%d", time.localtime(d["installed"])) if d.get("installed") else "",
            "installer": d.get("installer") or "", "requested": bool(d.get("requested")),
            "ledger_source": src[:3],
            "why": ("required only by orphans: " + ", ".join(req_by[:5])) if req_by
                   else "nothing installed requires it and no root names it",
        })
    out.sort(key=lambda o: -o["mb"])
    return out


def missing_roots(env_cfg: dict, dump: dict | None = None) -> list[str]:
    ""







    miss: list[str] = []
    for f in env_cfg.get("files") or []:
        if not Path(expand(f)).is_file():
            miss.append(f"file {f}")
    for e in list(env_cfg.get("entries") or []) + list(env_cfg.get("entries_wide") or []):
        path = e[len("shebang:"):] if str(e).startswith("shebang:") else e
        if not Path(expand(path)).exists():
            miss.append(f"entry {e}")
    if dump is not None:
        from packaging.requirements import Requirement
        for a in env_cfg.get("app") or []:
            if canon(Requirement(a).name) not in dump["dists"]:
                miss.append(f"app {a} (not installed)")
        for c in env_cfg.get("clis") or []:
            if canon(c["dist"]) not in dump["dists"]:
                miss.append(f"cli {c['dist']} (not installed)")
    return sorted(set(miss))


def analyze(env: str, env_cfg: dict, dump: dict, rows: dict, *, ledger: dict | None = None,
            classify=None, requires_of=pypi_requires, unused: dict | None = None) -> dict:
    roots, unmapped = roots_of(env_cfg, dump)
    miss = missing_roots(env_cfg, dump)
    via = closure(roots, dump, env_cfg)
    pins = app_pins(roots, dump)
    det = classify_rows(rows, dump, via, pins, classify=classify, requires_of=requires_of)
    counts = {b: 0 for b in BUCKETS}
    for x in det:
        counts[x["new"]] = counts.get(x["new"], 0) + 1
    orph = orphans(dump, via, env=env, ledger=ledger)
    unref = [canon(x) for x in ((unused or {}).get("unreferenced") or [])]
    return {
        "label": env_cfg.get("label") or env,
        "dists": len(dump["dists"]), "reachable": len(via),
        "roots": sorted({f"{canon(rq.name)} ({src})" for rq, src in roots}),
        "root_kinds": sorted({src.split(":")[0] for _rq, src in roots}),
        "scanned_files": dump.get("scanned_files", 0),
        "unmapped_imports": sorted(unmapped),
        "old": {b: len(v or []) for b, v in (rows or {}).items()},
        "new": counts, "detail": det, "app_pins": pins, "drift": drift(roots, dump),
        "orphans": orph, "orphans_n": len(orph), "orphan_mb": round(sum(o["mb"] for o in orph), 1),
        "orphan_remove": bool(env_cfg.get("orphan_remove", True)),
        "conda": bool(env_cfg.get("conda")), "review": env_cfg.get("review") or "",


        "unused_but_reached": sorted(set(unref) & set(via)),
        "duplicates": dump.get("duplicates") or {},
        "local": sum(1 for d in dump["dists"].values() if d.get("local", True)),
        "borrowed": borrowed(dump, via),
        "missing_roots": miss,


        **({"error": "missing declared roots: " + "; ".join(miss)} if miss else {}),
    }


def run_env(env: str, py: str, rows: dict, *, cfg: dict | None = None, ledger: dict | None = None,
            classify=None, unused: dict | None = None, requires_of=pypi_requires) -> dict:
    ""
    cfg = cfg if cfg is not None else load_roots()
    ec = (cfg.get("envs") or {}).get(env)
    if ec is None:
        return {"label": env, "error": "no roots declared for this env (config/ms-env-roots.json)"}
    ec = dict(ec)
    dflt = cfg.get("defaults") or {}
    for key in ("data_wheels", "clis", "file_roots"):
        ec[key] = list(dflt.get(key) or []) + list(ec.get(key) or [])
    ec["entry_point_groups"] = {**(dflt.get("entry_point_groups") or {}), **(ec.get("entry_point_groups") or {})}
    out = analyze(env, ec, probe(py, ec.get("entries") or []), rows, ledger=ledger,
                  classify=classify, requires_of=requires_of, unused=unused)
    if ec.get("entries_wide"):
        wide = analyze(env, ec, probe(py, ec["entries_wide"]), rows, ledger=ledger,
                       classify=classify, requires_of=requires_of, unused=unused)
        out["scans"] = {
            "narrow": {k: out[k] for k in ("reachable", "new", "orphans_n", "orphan_mb", "scanned_files")},
            "wide": {k: wide[k] for k in ("reachable", "new", "orphans_n", "orphan_mb", "scanned_files")},
        }
        out["wide_orphans"] = [o["package"] for o in wide["orphans"]]
        out["wide"] = {k: wide[k] for k in ("reachable", "dists", "local", "old", "new", "orphans_n", "orphan_mb",
                                            "drift", "detail", "orphans")}
        out["scan_note"] = ec.get("scan_note") or ""
    return out



def _unorphan(r: dict, pkgs: set, lender: str) -> list[str]:
    ""

    moved = sorted(o["package"] for o in r.get("orphans") or [] if o["package"] in pkgs)
    if not moved:
        return []
    r["orphans"] = [o for o in r["orphans"] if o["package"] not in pkgs]
    r["orphans_n"] = len(r["orphans"])
    r["orphan_mb"] = round(sum(o["mb"] for o in r["orphans"]), 1)
    for x in r.get("detail") or []:
        if x["new"] == "orphan" and x["package"] in pkgs:
            x["new"] = {"green_candidate": "green", "risk": "risk", "never": "never"}.get(x["old"], "pinned")
            x["why"] = f"lent to {lender}"
    counts = {b: 0 for b in BUCKETS}
    for x in r.get("detail") or []:
        counts[x["new"]] = counts.get(x["new"], 0) + 1
    r["new"] = counts
    return moved


ENVS_ROOT = os.environ.get("MS_ENVS_ROOT") or (
    str(_MSS.env_roots()[0]) if _MSS and _MSS.env_roots() else "")


def lend(results: dict, envs_root: str | None = None) -> dict:
    ""

    envs_root = str(envs_root or ENVS_ROOT)
    if not envs_root:
        return results
    for a, r in results.items():
        for home, pkgs in (r.get("borrowed") or {}).items():
            b = Path(home).name if str(home).startswith(envs_root.rstrip("/") + "/") else ""
            rb = results.get(b)
            if not rb or rb.get("error"):
                continue
            rb.setdefault("lent_to", {})[a] = sorted(pkgs)
            moved = _unorphan(rb, set(pkgs), a)
            if rb.get("wide"):
                _unorphan(rb["wide"], set(pkgs), a)
            if moved:
                rb.setdefault("lent_moved", {})[a] = moved
    return results



def _counts(d: dict, keys) -> str:
    return "/".join(str(int((d or {}).get(k) or 0)) for k in keys)


OLD_KEYS = ("green_candidate", "risk", "never", "pinned", "group-wait")
NEW_KEYS = ("green", "risk", "never", "app-pinned", "pinned", "group-wait", "orphan")


def totals(results: dict) -> dict:
    t = {"old": {k: 0 for k in OLD_KEYS}, "new": {k: 0 for k in NEW_KEYS},
         "orphans_installed": 0, "orphan_mb": 0.0, "drift": 0, "envs": 0, "errors": 0}
    for r in results.values():
        if r.get("error"):
            t["errors"] += 1
            continue
        t["envs"] += 1
        c = r.get("wide") or r
        for k in OLD_KEYS:
            t["old"][k] += int((r.get("old") or {}).get(k) or 0)
        for k in NEW_KEYS:
            t["new"][k] += int((c.get("new") or {}).get(k) or 0)
        t["orphans_installed"] += c.get("orphans_n", 0)
        t["orphan_mb"] = round(t["orphan_mb"] + c.get("orphan_mb", 0.0), 1)
        t["drift"] += len(r.get("drift") or [])
    return t


def markdown(results: dict, day: str) -> list[str]:
    t = totals(results)
    L = [f"# M&S roots — {day} (REPORT ONLY, MS_ROOTS={MODE})", "",
         "Nothing below changed what the night applied or filed. Buckets are computed from each env's "
         "roots (`config/ms-env-roots.json`). Old = the trail's buckets tonight.", "",
         "Old G/R/N/P/GW = green / risk / never / pinned / group-wait. "
         "New G/R/N/AP/P/GW/O = needed green / needed risk / never / app-pinned / pinned / "
         "true group-wait (the pinner's latest release frees it) / orphan.", "",
         "| env | runs | old G/R/N/P/GW | new G/R/N/AP/P/GW/O | reached / installed here | orphans installed | drift |",
         "|---|---|---|---|---|---|---|"]
    for env, r in results.items():
        if r.get("error"):
            L.append(f"| {env} | {r.get('label', '')} | — | — | — | error: {r['error'][:80]} | — |")
            continue
        o = f"{r['orphans_n']} ({r['orphan_mb']} MB)" + ("" if r["orphan_remove"] else " · removal OFF")
        tag = " · narrow" if r.get("wide") else ""
        L.append(f"| {env}{tag} | {r['label']} | {_counts(r['old'], OLD_KEYS)} | {_counts(r['new'], NEW_KEYS)} "
                 f"| {r['reachable']} / {r.get('local', r['dists'])} | {o} | {len(r['drift'])} |")
        w = r.get("wide")
        if w:
            L.append(f"| {env} · wide | (every app script) | {_counts(w['old'], OLD_KEYS)} | "
                     f"{_counts(w['new'], NEW_KEYS)} | {w['reachable']} / {w.get('local', w['dists'])} | "
                     f"{w['orphans_n']} ({w['orphan_mb']} MB) | {len(w['drift'])} |")
    L += ["", "Totals take the wide scan where an env has two (the conservative bound).",
          "", f"**Totals**: old {_counts(t['old'], OLD_KEYS)} → new {_counts(t['new'], NEW_KEYS)}; "
          f"{t['orphans_installed']} orphan packages installed ({t['orphan_mb']} MB); "
          f"{t['drift']} root-pin drift rows.", ""]
    for env, r in results.items():
        if r.get("error"):
            continue
        L.append(f"## {env} — {r['label']}")
        if r.get("review"):
            L.append(f"- review: {r['review']}")
        if r.get("conda"):
            L.append("- conda env: pip metadata is incomplete here; orphans are candidates only, "
                     "removal stays OFF until conda roots exist")
        if r.get("scans"):
            n, w = r["scans"]["narrow"], r["scans"]["wide"]
            L.append(f"- two scans ({r.get('scan_note')}): narrow (units' entry scripts) reaches "
                     f"{n['reachable']}, orphans {n['orphans_n']} ({n['orphan_mb']} MB), new "
                     f"{_counts(n['new'], NEW_KEYS)}; wide (every app script) reaches {w['reachable']}, "
                     f"orphans {w['orphans_n']} ({w['orphan_mb']} MB), new {_counts(w['new'], NEW_KEYS)}. "
                     "Both are rows in the table; the orphan list below is the narrow scan's, "
                     "and the wide scan still calls these orphans: "
                     + ", ".join(r.get("wide_orphans") or []))
        L.append(f"- root kinds: {', '.join(r['root_kinds'])} · {len(r['roots'])} roots · "
                 f"{r['scanned_files']} files scanned")
        if r["unmapped_imports"]:
            L.append(f"- imports no installed dist provides: {', '.join(r['unmapped_imports'][:20])}")
        for home, pkgs in (r.get("borrowed") or {}).items():
            L.append(f"- **borrows {len(pkgs)} dists from `{home}`** (a .pth puts that env on this one's path; "
                     f"they are roots THERE, never orphans here): {', '.join(pkgs[:20])}"
                     + (" …" if len(pkgs) > 20 else ""))
        for who, pkgs in (r.get("lent_to") or {}).items():
            L.append(f"- lends {len(pkgs)} dists to {who} (its .pth); "
                     f"{len((r.get('lent_moved') or {}).get(who) or [])} of them would otherwise read as orphans here")
        for k, vs in (r.get("duplicates") or {}).items():
            L.append(f"- **duplicate dist-info** {k}: {', '.join(vs)} (the importer sees one; M&S reads the first)")
        for d in r["drift"]:
            L.append(f"- **DRIFT** {d['package']} {d['installed']} breaks `{d['root_spec']}` ({d['root']})")
        moved = [x for x in r["detail"] if x["new"] != {"green_candidate": "green"}.get(x["old"], x["old"])]
        if moved:
            L.append("- rows that change bucket:")
            for x in moved:
                L.append(f"  - {x['package']} {x['current']}→{x['latest']}: {x['old']} → **{x['new']}**"
                         + (f" ({x['why']})" if x["why"] else ""))
        if r.get("unused_but_reached"):
            L.append(f"- the unused probe lists {len(r['unused_but_reached'])} names a root reaches: "
                     + ", ".join(r["unused_but_reached"][:15]))
        if r["orphans"]:
            L.append(f"- orphans ({r['orphans_n']}, {r['orphan_mb']} MB), largest first:")
            for o in r["orphans"][:40]:
                src = f" · ledger: {o['ledger_source'][0][:60]}" if o["ledger_source"] else ""
                L.append(f"  - {o['package']} {o['version']} · {o['mb']} MB · {o['installed']} "
                         f"({o['installer'] or '?'}) · {o['why']}{src}")
            if r["orphans_n"] > 40:
                L.append(f"  - … {r['orphans_n'] - 40} more in the JSON")
        L.append("")
    return L


def main(argv=None) -> int:
    ""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", action="append")
    ap.add_argument("--report", default=str(OUT_ROOT / "reports" / "ms-trail-LATEST.json"))
    ap.add_argument("--md")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    rep = json.loads(Path(a.report).read_text())
    cfg = load_roots()
    ledger = {}
    try:
        ledger = json.loads((OUT_ROOT / "decisions.json").read_text())
    except (OSError, ValueError):
        pass
    results = {}
    for env, info in (rep.get("envs") or {}).items():
        if a.env and env not in a.env:
            continue
        try:
            results[env] = run_env(env, info["python"], info.get("details") or {}, cfg=cfg,
                                   ledger=ledger, unused=info.get("unused"))
        except Exception as e:
            results[env] = {"label": env, "error": repr(e)[:300]}
    lend(results)
    md = "\n".join(markdown(results, rep.get("day") or ""))
    if a.md:
        Path(a.md).write_text(md)
    if a.json:
        Path(a.json).write_text(json.dumps({"totals": totals(results), "envs": results}, indent=1))
    if not a.md:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
