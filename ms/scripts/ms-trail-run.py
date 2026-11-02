#!/usr/bin/env python3
""
































































from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if _LIB.is_dir() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


import ms_settings as _S
import krew_hermes as KH  # noqa: E402  where Hermes lives
_S.export_env()
import ms_text as _TX
_T = _TX.T
import hermes_env_pins as _HPE
_vtuple = _HPE.vtuple


def _sp(v) -> str:
    ""
    return _S.expand(v)


def _assignee(kind: str) -> str:
    ""
    return str((_S.get("kanban.assignees") or {}).get(kind) or "")


try:
    import os_update as _osu
except Exception:
    _osu = None



try:
    import env_roots as _roots
except Exception as _e:
    _roots = None
    _ROOTS_IMPORT_ERR = repr(_e)[:200]
else:
    _ROOTS_IMPORT_ERR = ""


def roots_enabled(survey: bool) -> bool:
    ""
    return (os.environ.get("MS_ROOTS", "off").strip().lower() or "off") != "off" and not survey

OUT_ROOT = _S.state_dir()
MANIFEST_DIR = OUT_ROOT / "manifests"
REPORT_DIR = OUT_ROOT / "reports"
KANBAN_DIR = OUT_ROOT / "kanban-queue"


DECISIONS_PATH = Path(
    os.environ.get("MS_DECISIONS", str(OUT_ROOT / "decisions.json"))
)

WINDOW_PATH = Path(
    os.environ.get("MS_WINDOW", str(OUT_ROOT / "maintenance-window.json"))
)
def _canonical_deliverables() -> Path:
    ""



    env = os.environ.get("MS_DELIVERABLES")
    if env:
        return Path(env).expanduser()
    d = _sp(_S.get("deliverables_dir"))
    return Path(d) if d else OUT_ROOT / "deliverables"


DELIVERABLES_DIR = _canonical_deliverables()


KANBAN_TOP_N = int(os.environ.get("MS_KANBAN_TOP_N", "25"))

REPORT_DETAIL_CAP = int(os.environ.get("MS_REPORT_DETAIL_CAP", "40"))


MS_MITIGATION_MAX_ENVS = int(_S.get("kanban.max_mitigation_envs", 3, env="MS_MITIGATION_MAX_ENVS") or 0)

MS_FILE_MITIGATIONS = os.environ.get("MS_FILE_MITIGATIONS", "1").strip() not in (
    "0",
    "false",
    "no",
)







MS_ENV_HOLD = os.environ.get("MS_ENV_HOLD", "1") != "0"
MS_ENV_HOLD_FILE = Path(_sp(_S.get("hold_file", env="MS_ENV_HOLD_FILE"))
                        or OUT_ROOT / "env-hold.json")


def env_hold(env: str) -> dict | None:
    ""
    if not MS_ENV_HOLD:
        return None
    try:
        raw = MS_ENV_HOLD_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        return {"reason": f"hold file unreadable ({e.__class__.__name__}) - every env held"}
    try:
        rows = json.loads(raw)
        if not isinstance(rows, dict):
            raise ValueError("not an object")
    except ValueError as e:
        return {"reason": f"hold file unreadable ({e}) - every env held"}
    row = rows.get(env)
    if row is None:
        return None
    return row if isinstance(row, dict) else {"reason": str(row)}


def hold_env_batches(name: str, batches: list, report: dict) -> list:
    ""


    hold = env_hold(name)
    if not hold:
        return batches
    report.setdefault("envs", {}).setdefault(name, {})["hold"] = hold
    if not batches:
        return batches
    n_held = sum(len(rows) for _b, rows in batches)
    log(f"apply-green {name}: env on hold — {hold.get('reason')}; "
        f"{n_held} package(s) not applied")
    report.setdefault("applied", {})[name] = {
        "ok": True, "upgraded": [], "skipped": "env_hold", "changed_n": 0,
        "held_n": n_held, "hold": hold.get("reason")}
    return []







MS_REQUIRE_SMOKE = os.environ.get("MS_REQUIRE_SMOKE", "1") != "0"


def has_declared_smoke(name: str) -> bool:
    return name in SMOKE_IMPORT









MS_APPLY_SETTING = os.environ.get("MS_APPLY_SETTING", "1") != "0"
PROMOTIONS_PATH = Path(os.environ.get("MS_PROMOTIONS", str(OUT_ROOT / "promotions.json")))


def _env_row(name: str) -> dict:
    row = (_S.get("envs") or {}).get(name)
    return row if isinstance(row, dict) else {}


def env_promote_after(name: str) -> int:
    return int((_env_row(name).get("promote") or {}).get("after_clean_nights") or 0)


def load_promotions(path: Path | None = None) -> dict:
    try:
        d = json.loads((path or PROMOTIONS_PATH).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def env_apply_mode(name: str, promotions: dict | None = None) -> str:
    ""
    if str(_env_row(name).get("apply") or "report").lower() == "green":
        return "green"
    if env_promote_after(name) and (promotions if promotions is not None
                                    else load_promotions()).get(name, {}).get("promoted_on"):
        return "green"
    return "report"


PROMOTED_ROLLBACKS_ALERT = int(_S.get("alerts.promoted_rollbacks", 2, env="MS_PROMOTED_ROLLBACKS_ALERT") or 0)


def _track_promoted_rollbacks(name: str, row: dict, report: dict, day: str) -> None:
    ""


    if row.get("last_apply_night") == day:
        return
    t = (apply_tally(report.get("applied") or {}).get("envs") or {}).get(name) or {}
    if not t.get("attempted"):
        return
    row["last_apply_night"] = day
    if t.get("rolled"):
        row["rollbacks_in_row"] = int(row.get("rollbacks_in_row") or 0) + 1
        row["last_rolled"] = list(t["rolled"])[:8]
    elif t.get("landed"):
        row["rollbacks_in_row"] = 0
        row.pop("alerted_on", None)
    need = PROMOTED_ROLLBACKS_ALERT
    if need and int(row.get("rollbacks_in_row") or 0) >= need and not row.get("alerted_on"):
        row["alerted_on"] = day
        report.setdefault("promotion_alerts", []).append(
            {"env": name, "rollbacks_in_row": row["rollbacks_in_row"], "promoted_on": row.get("promoted_on"),
             "rolled": row.get("last_rolled"), "sent": raise_promotion_alert(name, row, day)})


def raise_promotion_alert(name: str, row: dict, day: str) -> str:
    ""

    n = row.get("rollbacks_in_row")
    title = f"M&S: {name} rolled back {n} nights in a row after its promotion to green"
    body = (f"{name} was promoted to `apply: green` on {row.get('promoted_on')} after its clean nights. "
            f"Since then its nightly apply has been rolled back {n} times in a row (latest {day}; "
            f"packages: {', '.join(row.get('last_rolled') or []) or '?'}). M&S has NOT demoted it: it keeps "
            f"trying each night and rolling back on a RED smoke. Decide: leave it green, set `apply: report` "
            f"for {name} in ms.yaml, or fix what the upgrade breaks. Evidence: {REPORT_DIR}/ms-trail-{day}.json "
            f"(applied.{name}).")
    log(f"promotion alert {name}: {n} rollbacks in a row since {row.get('promoted_on')} - raised, not demoted")
    if _S.get("kanban.enabled"):
        tid = _kanban_create_card_id(title=title, body=body, assignee=_assignee("promotion_alert"),
                                     idemp=f"ms-promotion-rollback:{name}:{row.get('promoted_on')}")
        if tid:
            rc, out_s, err = run([*_kanban_argv(), "kanban", "block", tid,
                                  _T("alert.promoted.block_reason", name=name, n=n), "--kind",
                                  "capability"], timeout=30)
            return f"card {tid}" + ("" if rc == 0 else f" (block failed rc={rc})")
    sent = notify_user(f"{title}. {body[:300]}")
    return f"notify: {sent}"


CANARY_SKIP_CARD = os.environ.get("MS_CANARY_SKIP_CARD", "1").strip().lower() not in ("0", "false", "no", "off")


def raise_canary_skip_card(row: dict, day: str) -> str:
    ""





    if not CANARY_SKIP_CARD:
        return "off (MS_CANARY_SKIP_CARD=0)"
    env = str(row.get("env") or "?")
    nights = list(row.get("nights") or [])
    nums = row.get("numbers") or {}
    per_night = "\n".join(f"- {n}: {nums.get(n) or '?'}" for n in nights)
    fields = dict(env=env, n=len(nights), first=nights[0] if nights else "?", last=nights[-1] if nights else "?",
                  per_night=per_night, finding=row.get("key") or "", day=day,
                  report=str(REPORT_DIR / f"ms-trail-{day}.json"))
    title, body = _T("card.canary_skip.title", **fields), _T("card.canary_skip.body", **fields)
    reason = _T("card.canary_skip.block_reason", **fields)
    log(f"canary skip {env}: {len(nights)} nights ({fields['first']}..{fields['last']}) - capability card")
    if _S.get("kanban.enabled"):
        tid = _kanban_create_card_id(title=title, body=body, assignee=_assignee("canary_skip"),
                                     idemp=str(row.get("key") or f"silent-skip:canary:{env}"))
        if tid:
            rc, _o, _e = run([*_kanban_argv(), "kanban", "block", tid, reason, "--kind", "capability"],
                             timeout=30)
            return f"card {tid}" + ("" if rc == 0 else f" (block failed rc={rc})")
        return "card create failed"
    return f"notify: {notify_user(f'{title}. {body[:300]}')}"


def canary_skip_cards(changes: list, day: str) -> list[dict]:
    ""

    return [{"key": c.key, "sent": raise_canary_skip_card(c.row, day)}
            for c in changes if c.action == "opened" and c.key.startswith("silent-skip:canary:")]


def _kanban_create_card_id(*, title: str, body: str, assignee: str, idemp: str) -> str | None:
    argv = [*_kanban_argv(), "kanban", "create", title, "--body", body[:5500],
            "--idempotency-key", idemp, "--created-by", "ms-trail"]
    if assignee:
        argv[argv.index("--body"):argv.index("--body")] = ["--assignee", assignee]
    rc, out_s, err = run(argv, timeout=30)
    m = re.search(r"\b(t_[0-9a-f]{8})\b", (out_s or "") + (err or ""))
    if rc != 0 or not m:
        log(f"promotion alert card FAIL rc={rc}: {((err or out_s) or '')[:160]}")
        return None
    return m.group(1)


def update_promotions(report: dict, day: str, *, path: Path | None = None) -> dict:
    ""


    p = path or PROMOTIONS_PATH
    state = load_promotions(p)
    out: dict = {}
    for name in sorted((_S.get("envs") or {})):
        need = env_promote_after(name)
        if not need or str(_env_row(name).get("apply") or "report").lower() == "green":
            continue
        row = dict(state.get(name) or {})
        env = (report.get("envs") or {}).get(name) or {}
        if row.get("promoted_on"):
            _track_promoted_rollbacks(name, row, report, day)
            out[name] = row
            state[name] = row
            continue
        if not env:
            out[name] = row
            continue
        clean = env.get("smoke_baseline") is True and not service_blocks_apply(env.get("service"))
        if row.get("last_night") != day:
            row["clean_nights"] = int(row.get("clean_nights") or 0) + 1 if clean else 0
            row["last_night"] = day
            row["last_clean"] = clean
        row["needed"] = need
        if row["clean_nights"] >= need:
            row["promoted_on"] = day
            log(f"promotion {name}: {need} clean nights - apply: green from now on")
        state[name] = out[name] = row
    if out:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, p)
    return out


def smoke_gate_batches(name: str, batches: list, report: dict) -> list:
    ""


    if not MS_REQUIRE_SMOKE:
        return batches
    why = ""
    if not has_declared_smoke(name):
        why = "no_declared_smoke"
    elif MS_APPLY_SETTING and env_apply_mode(name) != "green":
        why = "report_only_setting"
    if not why:
        return batches
    report.setdefault("envs", {}).setdefault(name, {})["report_only"] = why
    if not batches:
        return batches
    n_held = sum(len(rows) for _b, rows in batches)
    log(f"apply-green {name}: " + ("no declared smoke test" if why == "no_declared_smoke"
                                   else "settings say apply: report") +
        f" - report only; {n_held} package(s) not applied")
    report.setdefault("applied", {})[name] = {
        "ok": True, "upgraded": [], "skipped": why, "changed_n": 0, "held_n": n_held}
    return []





MS_PENDING_MAX_PER_ENV = int(os.environ.get("MS_PENDING_MAX_PER_ENV", "4"))


MS_REFUSE_MAX = int(os.environ.get("MS_REFUSE_MAX", "3"))




MS_FORWARD_DEPS = int(os.environ.get("MS_FORWARD_DEPS", "1"))





MS_HOLD_TTL_DAYS = int(os.environ.get("MS_HOLD_TTL_DAYS", "90"))



MS_TTL_ANCHOR = str(_S.get("holds.ttl_anchor", "", env="MS_TTL_ANCHOR") or "")






KNOWN_ROLES: dict[str, tuple[str, bool]] = {
    name: (str(e.get("role") or ""), bool(e.get("heavy")))
    for name, e in (_S.get("envs") or {}).items() if isinstance(e, dict) and "role" in e
}







RETIRED_ENVS = set(_S.get("retired_envs") or [])





ALIAS_ENVS: dict[str, str] = {}


REBUILD_SKIPPED: dict[str, str] = {}


DROPPED: dict[str, str] = {}


def _rebuild_marker_skip(d: Path) -> str:
    try:
        m = json.loads((d / ".ms-rebuild.json").read_text())
    except (OSError, ValueError):
        return ""
    if isinstance(m, dict) and m.get("role") in ("staged", "retired"):
        return f"{m.get('role')} rebuild of {m.get('of')} ({m.get('version')})"
    return ""



_HP = _S.get("hermes_profiles") or {}
HERMES_PROFILES_ON = bool(_HP.get("enabled"))
HERMES_CREW = tuple(_HP.get("names") or ())
HERMES_PACKS = Path(_sp(_HP.get("packs_dir")) or "/nonexistent/ms-hermes-packs")
HERMES_LIVE_PROFILES = Path(_sp(_HP.get("live_dir")) or "/nonexistent/ms-hermes-profiles")
CONFIG_SUPPORT_FLOOR = int(_HP.get("config_floor") or 0)
KANBAN_WORKERS = tuple(_HP.get("workers") or ())


def _yaml_scalar(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.M)
    if not m:
        return None
    return m.group(1).strip().strip("'\"")


def survey_hermes_profiles() -> dict:
    ""
    if not HERMES_PROFILES_ON:
        return {}
    rows: list[dict] = []
    for name in HERMES_CREW:
        live_cfg = HERMES_LIVE_PROFILES / name / "config.yaml"
        pack_cfg = HERMES_PACKS / name / "config.yaml"
        live_dist = HERMES_LIVE_PROFILES / name / "distribution.yaml"
        pack_dist = HERMES_PACKS / name / "distribution.yaml"
        live_text = live_cfg.read_text() if live_cfg.is_file() else ""
        pack_text = pack_cfg.read_text() if pack_cfg.is_file() else ""
        live_ver = _yaml_scalar(live_text, "_config_version")
        pack_ver = _yaml_scalar(pack_text, "_config_version")
        try:
            live_n = int(live_ver) if live_ver is not None else None
        except ValueError:
            live_n = None
        below = live_n is not None and live_n < CONFIG_SUPPORT_FLOOR
        dist = _yaml_scalar(
            (live_dist.read_text() if live_dist.is_file() else "")
            or (pack_dist.read_text() if pack_dist.is_file() else ""),
            "version",
        )
        rows.append({
            "name": name,
            "live": live_cfg.is_file(),
            "config_version": live_n,
            "pack_config_version": (
                int(pack_ver) if pack_ver and pack_ver.isdigit() else pack_ver
            ),
            "distribution": dist,
            "below_floor": below,
            "kanban_worker": name in KANBAN_WORKERS,
            "note": str((_HP.get("notes") or {}).get(name) or ""),
        })
    return {
        "profiles": rows,
        "below_floor": [r["name"] for r in rows if r.get("below_floor")],
        "floor": CONFIG_SUPPORT_FLOOR,
    }







ENVS_ROOTS: list[Path] = _S.env_roots()
ENVS_ROOT = ENVS_ROOTS[0] if ENVS_ROOTS else Path("/nonexistent/ms-envs-root")
_ENVS_PREFIX = str(ENVS_ROOT).rstrip("/") + "/"
_ROOTS_ALT = "|".join(re.escape(str(r).rstrip("/") + "/") for r in (ENVS_ROOTS or [ENVS_ROOT]))
_ENVS_NAME_RE = re.compile("(?:" + _ROOTS_ALT + r")([^/]+)")
_ENVS_TOKEN_RE = re.compile("(?:" + _ROOTS_ALT + r")[^\s;:\"']+")


def discover_venvs() -> dict[str, tuple[str, str, bool]]:
    ""


    found: dict[str, tuple[str, str, bool]] = {}
    seen_real: dict[str, str] = {}
    for root in ENVS_ROOTS or []:
        if not root.is_dir():
            DROPPED[str(root)] = "env root does not exist"
    for d in sorted(p for root in (ENVS_ROOTS or []) if root.is_dir() for p in root.iterdir()):
        if not d.is_dir():
            DROPPED[str(d)] = "not a folder"
            continue
        if re.search(r"-bak(-|$)|-pip-bak|\.broken\.", d.name):
            DROPPED[str(d)] = "backup or broken leftover (name has -bak, -pip-bak or .broken.)"
            continue
        if d.name in RETIRED_ENVS:
            DROPPED[str(d)] = "listed in retired_envs"
            continue
        why = "" if d.is_symlink() else _rebuild_marker_skip(d)
        if why:
            REBUILD_SKIPPED[d.name] = why
            DROPPED[str(d)] = why
            continue
        py = d / "bin" / "python"
        if not py.is_file():
            py = d / ".venv" / "bin" / "python"
        if not py.is_file():
            DROPPED[str(d)] = "no bin/python or .venv/bin/python"
            continue



        real = str(d.resolve())
        if real in seen_real:
            ALIAS_ENVS[d.name] = seen_real[real]
            DROPPED[str(d)] = f"alias of {seen_real[real]} (same folder)"
            continue
        seen_real[real] = d.name
        role, heavy = KNOWN_ROLES.get(d.name, ("UNKNOWN — assign role in KNOWN_ROLES", True))
        found[d.name] = (str(py), role, heavy)
    return found


VENVS: dict[str, tuple[str, str, bool]] = discover_venvs()


for _name, _row in (_S.get("envs") or {}).items():
    _py = _S.expand((_row or {}).get("python")) if isinstance(_row, dict) else ""
    if _py and _name not in VENVS and _name not in RETIRED_ENVS and Path(_py).is_file():
        VENVS[_name] = (_py, str(_row.get("role") or "registered"), bool(_row.get("heavy")))
    elif _py and _name not in VENVS and not Path(_py).is_file():
        DROPPED[f"envs.{_name}"] = f"python {_py} does not exist"
    elif _name in RETIRED_ENVS and _name not in VENVS:
        DROPPED.setdefault(f"envs.{_name}", "listed in retired_envs")


def _systemctl_user(args: list[str], timeout: int = 8) -> str:
    try:
        return subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True, text=True, timeout=timeout,
        ).stdout or ""
    except Exception:
        return ""


def which_env(path: str, prefixes: list[tuple[str, str]] | None = None) -> str | None:
    ""






    del prefixes
    if not path:
        return None
    raw = path.split(";", 1)[0].strip().split(":", 1)[0]
    m = _ENVS_NAME_RE.search(raw.replace("\\", "/"))
    if m:
        name = m.group(1)
        if name in VENVS:
            return name
        if name in ALIAS_ENVS:
            return ALIAS_ENVS[name]
        try:
            real_dir = str((ENVS_ROOT / name).resolve())
        except OSError:
            real_dir = ""
        for env_name in VENVS:
            try:
                if str((ENVS_ROOT / env_name).resolve()) == real_dir:
                    return env_name
            except OSError:
                continue
    try:
        real = str(Path(raw).resolve())
    except OSError:
        return None
    hits: list[tuple[int, str]] = []
    for env_name in VENVS:
        try:
            env_dir = str((ENVS_ROOT / env_name).resolve())
        except OSError:
            continue
        if real == env_dir or real.startswith(env_dir + "/"):
            hits.append((len(env_dir), env_name))
    if hits:
        hits.sort(reverse=True)
        return hits[0][1]
    return None


def live_venv_users() -> dict[str, list[str]]:
    ""




    used: dict[str, list[str]] = {}

    def add(name: str | None, tag: str) -> None:
        if not name:
            return
        bucket = used.setdefault(name, [])
        if tag not in bucket:
            bucket.append(tag)

    units_out = _systemctl_user(
        ["list-units", "--type=service", "--state=running",
         "--no-legend", "--plain", "--no-pager"]
    )
    for line in units_out.splitlines():
        unit = (line.split() or [""])[0]
        if not unit.endswith(".service"):
            continue
        blob = _systemctl_user(
            ["show", unit, "-p", "ExecStart", "-p", "Environment",
             "-p", "FragmentPath"]
        )
        for token in _ENVS_TOKEN_RE.findall(blob):
            add(which_env(token), f"unit:{unit[:-8]}")
    for env_name, tags in _system_service_env_users().items():
        for tag in tags:
            add(env_name, tag)

    try:
        pids = list(Path("/proc").iterdir())
    except OSError:
        pids = []
    for proc in pids:
        if not proc.name.isdigit():
            continue
        blobs: list[str] = []
        try:
            blobs.append(os.readlink(proc / "exe"))
        except OSError:
            pass
        try:
            blobs.append(
                (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    "utf-8", "replace"
                )
            )
        except OSError:
            pass
        hay = " ".join(blobs)
        if not _ENVS_TOKEN_RE.search(hay):
            continue
        for token in _ENVS_TOKEN_RE.findall(hay):
            add(which_env(token), f"pid:{proc.name}")
    return used


NEVER_AUTO = {
    "torch",
    "torchvision",
    "torchaudio",
    "vllm",
    "transformers",
    "triton",
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "xformers",
    "flash-attn",
    "unsloth",
    "bitsandbytes",
    "nemo-toolkit",
    "onnx",
    "cuda-python",
}


NEVER_AUTO_PREFIXES = ("nvidia-", "cuda-", "onnxruntime", "tensorrt", "nemo-")








COUPLED_RISK = {
    "numpy", "pydantic-core", "pydantic",
    "antlr4-python3-runtime", "fsspec", "protobuf",
    "huggingface-hub", "mpmath", "accelerate",
}



SMOKE_IMPORT: dict[str, dict] = {
    name: dict(e["smoke"])
    for name, e in (_S.get("envs") or {}).items() if isinstance(e, dict) and e.get("smoke")
}





_CN = (_S.get("canary") or {}) if _S.upgrades_on() else {}
CANARY_ENV = str(_CN.get("env") or "") if _CN.get("enabled") else ""
TRAIN_BUSY = Path(_sp(_S.get("occupancy.train_busy_file")) or "/nonexistent/ms-train-busy")
OCCUPANCY_ON = bool(_S.get("occupancy.enabled"))
MODE_FILE = Path(os.environ.get("MS_GPU_MODE_FILE")
                 or _sp(_S.get("occupancy.mode_file")) or "/nonexistent/ms-gpu-mode")
CANARY_ENDPOINTS = [dict(e) for e in (_CN.get("endpoints") or []) if isinstance(e, dict)]
_NO_THINKING = {str(e.get("model")) for e in CANARY_ENDPOINTS if e.get("no_thinking")}

CANARY_05B = dict(_CN.get("model_05b") or {})

GPU_SHARED_ENVS = {
    name for name, e in (_S.get("envs") or {}).items() if isinstance(e, dict) and e.get("gpu_shared")
}


def canon(pkg: str) -> str:
    return pkg.lower().split("[")[0].replace("_", "-").replace(".", "-")


def log(msg: str) -> None:
    print(f"[ms] {msg}", flush=True)


def run(
    cmd: list[str], timeout: int = 120, extra_env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **extra_env} if extra_env else None,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def find_uv() -> str | None:
    ""
    p = shutil.which("uv")
    if p:
        return p
    home = Path.home() / ".local" / "bin" / "uv"
    if home.is_file():
        return str(home)
    return None


UV = find_uv()


def packaging_available() -> bool:
    try:
        import packaging.markers
        import packaging.requirements
        import packaging.specifiers
        import packaging.version

        return True
    except ImportError:
        return False


def require_packaging() -> None:
    ""









    if packaging_available():
        return
    msg = (
        "ms-trail-run needs the 'packaging' package to classify pins "
        f"(Requires-Dist). This interpreter is {sys.executable!r} and "
        "cannot import it. Pin/group-wait would silently become 0. "
+ _T("error.packaging_hint")
    )
    log(msg)
    raise SystemExit(2)


def _freeze_text(py: str) -> str | None:
    ""
    if not Path(py).is_file():
        return None
    if UV:
        rc, out_s, err = run([UV, "pip", "freeze", "--python", py], timeout=180)
    else:
        rc, out_s, err = run([py, "-m", "pip", "freeze"], timeout=180)
    return out_s if rc == 0 else None


def survey_env(name: str, py: str) -> dict[str, Any]:
    ""











    latest = MANIFEST_DIR / f"{name}-LATEST.txt"
    prev = _freeze_versions(latest) if latest.is_file() else {}
    now_text = _freeze_text(py)
    if now_text is None:
        return {"ok": False, "reason": "freeze failed"}
    cur: dict[str, str] = {}
    for line in now_text.splitlines():
        if "==" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("==")
            cur[canon(k.strip())] = v.strip()
    changed = {k: [prev[k], cur[k]] for k in cur if k in prev and prev[k] != cur[k]}
    added = sorted(k for k in cur if k not in prev)
    removed = sorted(k for k in prev if k not in cur)
    drift = {
        "ok": True,
        "had_manifest": bool(prev),
        "changed": changed,
        "added": added,
        "removed": removed,
        "added_v": {k: cur[k] for k in added},
        "removed_v": {k: prev[k] for k in removed},
        "n": len(changed) + len(added) + len(removed),
    }
    if prev and drift["n"]:
        log(f"survey {name}: OUT-OF-BAND DRIFT since last manifest — "
            f"{len(changed)} changed, {len(added)} added, {len(removed)} removed: "
            + ", ".join(f"{k} {a}->{b}" for k, (a, b) in list(changed.items())[:5]))
    return drift


def freeze_env(name: str, py: str, *, write_latest: bool = True) -> Path | None:
    if not Path(py).is_file():
        log(f"skip freeze {name}: no python {py}")
        return None
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().isoformat()
    out = MANIFEST_DIR / f"{name}-{day}.txt"
    latest = MANIFEST_DIR / f"{name}-LATEST.txt"
    if UV:
        rc, out_s, err = run([UV, "pip", "freeze", "--python", py], timeout=180)
        tool = "uv"
    else:
        rc, out_s, err = run([py, "-m", "pip", "freeze"], timeout=180)
        tool = "pip"
    if rc != 0:
        log(f"freeze FAIL {name} ({tool}): {err[:200]}")
        return None
    out.write_text(out_s)


    if write_latest:
        latest.write_text(out_s)
    log(f"freeze {name} via {tool} → {out} ({len(out_s.splitlines())} pkgs)")
    return out


def outdated(name: str, py: str) -> list[dict] | None:
    ""



    if not Path(py).is_file():
        return []

    if UV:
        rc, out_s, err = run(
            [UV, "pip", "list", "--python", py, "--outdated"],
            timeout=300,
        )
        if rc == 0 and not out_s.strip():
            return []
        if rc == 0 and out_s.strip():
            rows = _parse_uv_outdated(out_s)
            if rows:
                return rows

            if "Package" in out_s or not out_s.strip():
                return []
        log(f"outdated uv miss {name}: {err[:160] or 'parse empty'}; try pip")
    rc, out_s, err = run(
        [py, "-m", "pip", "list", "--outdated", "--format=json"],
        timeout=300,
    )
    if rc != 0:
        log(f"outdated UNKNOWN {name}: {err[:200]}")
        return None
    try:
        got = json.loads(out_s or "[]")
    except json.JSONDecodeError:
        log(f"outdated UNKNOWN {name}: pip json did not parse")
        return None
    return got if isinstance(got, list) else None


def _parse_uv_outdated(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Package") or set(line) <= {"-", " "}:
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        if parts[-1].lower() in ("wheel", "sdist", "editable"):
            pkg = " ".join(parts[:-3]) if len(parts) > 3 else parts[0]

            if len(parts) >= 4:
                pkg, cur, lat = parts[0], parts[1], parts[2]
            else:
                continue
        else:
            pkg, cur, lat = parts[0], parts[1], parts[2]
        if not pkg or pkg.lower() == "package":
            continue
        rows.append({"name": pkg, "version": cur, "latest_version": lat})
    return rows


def _pkg_major(v: str) -> str:
    m = re.match(r"(\d+)", v or "0")
    return m.group(1) if m else "0"


def _pkg_minor(v: str) -> str:
    m = re.match(r"\d+\.(\d+)", v or "0.0")
    return m.group(1) if m else "0"


def version_in_spec(version: str, spec: str) -> bool:
    ""



    if not version or not spec:
        return False
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    try:
        return Version(version) in SpecifierSet(spec)
    except Exception:
        return False


def major_crosses(target: str, latest: str) -> bool:
    ""
    if not target or not latest:
        return False
    return _pkg_major(target) != _pkg_major(latest)


def marker_binds(marker: str | None, *, extra: str = "") -> bool:
    ""



    if not marker or not str(marker).strip():
        return True
    from packaging.markers import Marker, default_environment

    try:
        env = default_environment()
        env["extra"] = extra
        return bool(Marker(str(marker)).evaluate(env))
    except Exception:
        return False


def parse_requires_dist_line(line: str) -> dict | None:
    ""
    raw = line.strip()
    if raw.lower().startswith("requires-dist:"):
        raw = raw.split(":", 1)[1].strip()
    if not raw:
        return None
    from packaging.requirements import Requirement

    try:
        r = Requirement(raw)
        marker = str(r.marker) if r.marker is not None else ""
        return {
            "req": canon(r.name),
            "spec": str(r.specifier) if r.specifier else "",
            "marker": marker,
        }
    except Exception:
        return None


def classify_update(pkg: str, current: str, latest: str) -> str:
    ""




    name = canon(pkg)
    if name in NEVER_AUTO or name.startswith(NEVER_AUTO_PREFIXES):
        return "never"
    if _pkg_major(current) != _pkg_major(latest):
        return "risk"


    if _pkg_major(current) == "0" and _pkg_minor(current) != _pkg_minor(latest):
        return "risk"
    if name in COUPLED_RISK:
        return "risk"
    return "green_candidate"


def _row_pkg_cur_lat(row: dict) -> tuple[str, str, str]:
    pkg = canon(str(row.get("package") or row.get("name") or row.get("req") or ""))
    cur = str(row.get("current") or row.get("version") or "")
    lat = str(row.get("latest") or row.get("latest_version") or "")
    return pkg, cur, lat


def excluding_pinners(
    pkg: str, current: str, latest: str, constraints: list[dict]
) -> list[dict]:
    ""
    out: list[dict] = []
    want = canon(pkg)
    for c in constraints or []:
        if canon(str(c.get("req") or "")) != want:
            continue
        if not marker_binds(c.get("marker")):
            continue
        spec = str(c.get("spec") or "")
        if not spec:
            continue
        if version_in_spec(current, spec) and not version_in_spec(latest, spec):
            out.append(c)
    return out


def pinner_is_immovable(
    pinner: str,
    *,
    outdated_names: set[str],
    buckets: dict[str, str],
    never_auto: set[str],
    never_prefixes: tuple[str, ...],
) -> bool:
    name = canon(pinner)
    if name in never_auto or name.startswith(never_prefixes):
        return True
    if name not in outdated_names:
        return True
    if buckets.get(name) in ("pinned", "never"):
        return True
    return False


def classify_env_rows(
    outdated: list[dict],
    constraints: list[dict],
    *,
    never_auto: set[str] | None = None,
    never_prefixes: tuple[str, ...] | None = None,
    coupled: set[str] | None = None,
) -> dict[str, dict]:
    ""




    never_auto = never_auto if never_auto is not None else NEVER_AUTO
    never_prefixes = never_prefixes if never_prefixes is not None else NEVER_AUTO_PREFIXES
    coupled = coupled if coupled is not None else COUPLED_RISK

    rows: dict[str, tuple[str, str]] = {}
    for raw in outdated or []:
        pkg, cur, lat = _row_pkg_cur_lat(raw)
        if pkg:
            rows[pkg] = (cur, lat)
    outdated_names = set(rows)
    buckets: dict[str, str] = {}
    meta: dict[str, dict] = {}

    for pkg, (cur, lat) in rows.items():
        if pkg in never_auto or pkg.startswith(never_prefixes):
            buckets[pkg] = "never"



    changed = True
    while changed:
        changed = False
        for pkg, (cur, lat) in rows.items():
            if buckets.get(pkg) in ("never", "pinned"):
                continue
            excl = excluding_pinners(pkg, cur, lat, constraints)
            if not excl:
                continue
            immov = []
            mov = []
            for c in excl:
                pin = canon(str(c.get("pinner") or ""))
                if pinner_is_immovable(
                    pin,
                    outdated_names=outdated_names,
                    buckets=buckets,
                    never_auto=never_auto,
                    never_prefixes=never_prefixes,
                ):
                    immov.append(c)
                else:
                    mov.append(c)
            if immov:
                src = immov[0]
                new = "pinned"
                meta[pkg] = {
                    "pinned_by": canon(str(src.get("pinner") or "")),
                    "pinner_version": str(src.get("pinner_version") or ""),
                }
                if buckets.get(pkg) != new:
                    buckets[pkg] = new
                    changed = True
            elif mov:
                if buckets.get(pkg) != "group-wait":
                    buckets[pkg] = "group-wait"
                    changed = True
                for c in mov:
                    pin = canon(str(c.get("pinner") or ""))
                    if pin in outdated_names and buckets.get(pin) not in (
                        "never",
                        "pinned",
                    ):
                        if buckets.get(pin) != "group-wait":
                            buckets[pin] = "group-wait"
                            changed = True

    out: dict[str, dict] = {}
    for pkg, (cur, lat) in rows.items():
        b = buckets.get(pkg)
        if not b:



            if pkg in coupled:
                b = "risk"
            else:
                b = classify_update(pkg, cur, lat)
                if b == "never":
                    b = "never"
        info = {"bucket": b, "package": pkg, "current": cur, "latest": lat}
        info.update(meta.get(pkg) or {})
        out[pkg] = info
    return out


def collect_installed_constraints(py: str) -> list[dict]:
    ""
    if not py or not Path(py).is_file():
        return []
    rc, out_s, _err = run(
        [
            py,
            "-c",
            "import site,sys,os\n"
            "paths=list(site.getsitepackages())\n"
            "try:\n"
            " paths.append(site.getusersitepackages())\n"
            "except Exception:\n"
            " pass\n"
            "print('\\n'.join(p for p in paths if p and os.path.isdir(p)))",
        ],
        timeout=30,
    )
    if rc != 0:
        return []
    facts: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for sp in (out_s or "").splitlines():
        root = Path(sp.strip())
        if not root.is_dir():
            continue
        for meta in root.glob("*.dist-info/METADATA"):
            try:
                text = meta.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            pinner = ""
            pver = ""
            for line in text.splitlines():
                if line.startswith("Name:"):
                    pinner = canon(line.split(":", 1)[1].strip())
                elif line.startswith("Version:"):
                    pver = line.split(":", 1)[1].strip()
            if not pinner:
                continue
            for line in text.splitlines():
                if not line.lower().startswith("requires-dist:"):
                    continue
                parsed = parse_requires_dist_line(line)
                if not parsed or not parsed.get("req"):
                    continue
                if not marker_binds(parsed.get("marker")):
                    continue
                key = (pinner, parsed["req"], parsed["spec"])
                if key in seen:
                    continue
                seen.add(key)
                facts.append(
                    {
                        "pinner": pinner,
                        "pinner_version": pver,
                        "req": parsed["req"],
                        "spec": parsed["spec"],
                        "marker": parsed.get("marker") or "",
                    }
                )
    return facts


def classify_outdated(
    od: list[dict],
    constraints: list[dict],
    *,
    env: str = "",
    role: str = "",
) -> dict[str, list]:
    ""
    buckets: dict[str, list] = {
        "green_candidate": [],
        "risk": [],
        "never": [],
        "pinned": [],
        "group-wait": [],
    }
    info = classify_env_rows(od, constraints)
    for raw in od:
        pkg, cur, lat = _row_pkg_cur_lat(raw)
        if not pkg:
            continue
        got = info.get(pkg) or {}
        bucket = got.get("bucket") or classify_update(pkg, cur, lat)
        if bucket not in buckets:
            bucket = "risk"
        rec = {
            "package": raw.get("name") or raw.get("package") or pkg,
            "current": cur,
            "latest": lat,
            "env": env,
            "role": role,
            "bucket": bucket,
        }
        if got.get("pinned_by"):
            rec["pinned_by"] = got["pinned_by"]
            rec["pinner_version"] = got.get("pinner_version") or ""
        buckets[bucket].append(rec)
    return buckets


def applied_label(
    a: dict | None,
    *,
    considered_apply: bool,
    green_n: int,
) -> str:
    ""
    if a is None:
        if considered_apply and green_n == 0:
            return "0-green"
        return "report-only"
    skipped = str(a.get("skipped") or "")
    if skipped in ("train-busy", "skip_train_busy"):
        return "train-busy"
    if skipped in ("canary_red", "skip_canary_red") or a.get("reason") == "canary_red_pre":
        return "canary-red"
    if skipped == "ledger_unreadable":
        return "LEDGER-UNREADABLE (no apply)"
    if skipped == "no_declared_smoke":
        return f"report-only: no smoke test ({int(a.get('held_n') or 0)} waited)"
    if skipped == "report_only_setting":
        return f"report-only: apply: report ({int(a.get('held_n') or 0)} waited)"
    if skipped == "canary_unverified" or a.get("unverified"):
        return "canary-UNVERIFIED (deferred to window)"
    if a.get("ok"):
        n = int(a.get("changed_n") or 0)
        extra: list[str] = []
        if a.get("failed"):
            extra.append(f"{len(a['failed'])} failed")
        if a.get("pinned_n"):
            extra.append(f"{a['pinned_n']} pinned")
        if a.get("group_wait_n"):
            extra.append(f"{a['group_wait_n']} group-wait")
        return f"+{n}" + (f" ({', '.join(extra)})" if extra else "")
    err = (a.get("err") or "")[:60]
    return f"APPLY-FAILED({err})" if err else "APPLY-FAILED"


def smoke(name: str, py: str) -> bool:
    spec = SMOKE_IMPORT.get(name) or {"code": "import sys; print(sys.version)"}
    rc, out_s, err = run(
        [py, "-c", spec["code"]],
        timeout=spec.get("timeout", 60),
        extra_env=spec.get("env"),
    )
    ok = rc == 0
    if ok:
        SMOKE_ERR.pop(name, None)
    else:
        tail = [ln for ln in (err or out_s or "").splitlines() if ln.strip()]
        SMOKE_ERR[name] = (tail[-1] if tail else f"rc {rc}")[:240]
    log(f"smoke {name}: {'GREEN' if ok else 'RED'} {err[:120] if not ok else out_s.strip()[:80]}")
    return ok


SMOKE_ERR: dict[str, str] = {}

























SERVICE_ENVS_ALL: dict[str, list[dict]] = {
    name: [dict(u) for u in e["services"]]
    for name, e in (_S.get("envs") or {}).items() if isinstance(e, dict) and e.get("services")
}
MS_SERVICE_ENVS_WIDE = os.environ.get("MS_SERVICE_ENVS_WIDE", "1") != "0"
SERVICE_ENVS: dict[str, list[dict]] = (
    SERVICE_ENVS_ALL if MS_SERVICE_ENVS_WIDE
    else {k: v for k, v in SERVICE_ENVS_ALL.items() if k == "hindsight"}
)
MS_SERVICE_RESTART = _S.upgrades_on() and os.environ.get("MS_SERVICE_RESTART", "1") != "0"





MS_RESTART_ASK_WINDOW = os.environ.get("MS_RESTART_ASK_WINDOW", "1") != "0"


def _restart_window_busy(units: list[dict]) -> str:
    ""
    if not MS_RESTART_ASK_WINDOW:
        return ""
    try:
        import restart_window as _rw
    except ModuleNotFoundError as e:
        if e.name == "restart_window":
            return ""
        return f"restart window unreadable: {type(e).__name__}"
    except Exception as e:
        return f"restart window unreadable: {type(e).__name__}"
    for spec in units:
        if _unit_policy(spec) != "restart":
            continue
        try:
            why = _rw.busy_now(spec["unit"])
        except Exception as e:
            why = f"busy_now unreadable: {type(e).__name__}"
        if why:
            return f"{spec['unit']}: {why}"
    return ""


def _rollback_pins(result: dict) -> list[str]:
    pins = []
    for u in result.get("upgraded") or []:
        m = re.match(r"(\S+) (\S+)→", str(u))
        if m and "+" not in m.group(2):
            pins.append(f"{m.group(1)}=={m.group(2)}")
    return pins


def _service_units(name: str) -> list[dict]:
    spec = SERVICE_ENVS.get(name) or []
    return [spec] if isinstance(spec, dict) else list(spec)


def _unit_policy(spec: dict) -> str:
    return "report" if str(spec.get("restart") or "restart") == "report" else "restart"


def _unit_short(spec: dict) -> str:
    u = str(spec.get("unit") or "")
    return u[:-8] if u.endswith(".service") else u


def _svc_systemctl(spec: dict, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    cmd = ["systemctl"] + (["--user"] if spec.get("scope") == "user" else []) + ["--no-ask-password"]
    return run(cmd + args, timeout=timeout)


def _site_packages_mtime(py: str) -> float:
    rc, out_s, _ = run([py, "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"], timeout=60)
    try:
        return os.stat(out_s.strip()).st_mtime if rc == 0 else 0.0
    except OSError:
        return 0.0


def _unit_started_at(spec: dict) -> float:
    rc, out_s, _ = _svc_systemctl(spec, ["show", spec["unit"], "-p", "MainPID", "--value"])
    try:
        pid = int(out_s.strip() or 0)
        return os.stat(f"/proc/{pid}").st_ctime if pid > 0 else 0.0
    except (ValueError, OSError):
        return 0.0


def _unit_parked(spec: dict, mode: str) -> bool:
    return mode in {str(m).lower() for m in (spec.get("occupancy_parked_in") or [])}


def service_unit_state(spec: dict, changed: float, mode: str) -> dict:
    ""
    health = _http_ok(spec["health"])
    started = _unit_started_at(spec) if health else 0.0
    stale = bool(started and changed and changed > started)
    if not health:
        verdict = "parked" if _unit_parked(spec, mode) else "down"
    elif stale:
        verdict = "restart-owed"
    else:
        verdict = "ok"
    return {"unit": spec["unit"], "restart": _unit_policy(spec), "health": health,
            "process_older_than_env": stale, "verdict": verdict}


def service_env_state(name: str, py: str, smoke_ok: bool | None) -> dict | None:
    ""




    units = _service_units(name)
    if not units:
        return None
    mode = occupancy_gpu_mode()
    changed = _site_packages_mtime(py)
    rows = [service_unit_state(u, changed, mode) for u in units]
    v = [r["verdict"] for r in rows]
    if any(r["verdict"] == "down" and r["restart"] == "restart" for r in rows):
        verdict = "down"
    elif smoke_ok is False:
        verdict = "broken"
    elif "down" in v:
        verdict = "report-down"
    elif "restart-owed" in v:
        verdict = "restart-owed"
    elif v and all(x == "parked" for x in v):
        verdict = "parked"
    else:
        verdict = "ok"
    return {
        "unit": ", ".join(r["unit"] for r in rows),
        "health": all(r["health"] for r in rows if r["verdict"] != "parked"),
        "smoke": smoke_ok,
        "smoke_err": SMOKE_ERR.get(name, "") if smoke_ok is False else "",
        "process_older_than_env": any(r["process_older_than_env"] for r in rows),
        "verdict": verdict,
        "mode": mode,
        "units": rows,
        "down": [r["unit"] for r in rows if r["verdict"] == "down"],
        "restart_owed": [r["unit"] for r in rows if r["verdict"] == "restart-owed"],
        "parked": [r["unit"] for r in rows if r["verdict"] == "parked"],
    }


def service_blocks_apply(state: dict | None) -> str:
    ""


    if not state:
        return ""
    if state.get("verdict") == "down":
        return "service_down"
    if state.get("verdict") == "broken" or state.get("smoke") is False:
        return "service_smoke_red"
    return ""


def _wait_health(spec: dict) -> bool:
    deadline = time.time() + float(spec.get("health_wait_s") or 60)
    while time.time() < deadline:
        if _http_ok(spec["health"]):
            time.sleep(5)
            if _http_ok(spec["health"]):
                return True
        time.sleep(2)
    return False


def _journal_argv(unit: str, scope: str = "system") -> list[str]:
    ""


    return (["journalctl"] + (["--user"] if scope == "user" else [])
            + ["-u", unit, "--since", "-5min", "-n", "50", "-o", "short-iso", "--no-pager"])


def _crash_evidence(unit: str, scope: str = "system") -> str:
    ""



    bits: list[str] = []
    try:
        rc, o, _e = run(_journal_argv(unit, scope), timeout=20)
        lines = [l for l in (o or "").splitlines()
                 if re.search(r"ERROR|Error|Traceback|Exception|ExceptionGroup|error\b|failed|refused|denied|ModuleNotFoundError|FileNotFoundError|status=", l)]
        if lines:


            bits.append("journal: " + " | ".join(l.strip() for l in lines[:2] + lines[-1:]))
    except Exception:
        pass
    if unit.startswith("hindsight") and _S.get("hindsight.err_log"):
        try:
            p = Path(_sp(_S.get("hindsight.err_log")))
            if p.is_file():
                tail = p.read_text(errors="replace").splitlines()[-8:]
                if any(re.search(r"ERROR|Traceback|Error", l) for l in tail):
                    bits.append("app-log %s: %s" % (p.name, " | ".join(l.strip() for l in tail if l.strip())[-400:]))
        except Exception:
            pass
    return " | ".join(bits)[:1000]



def service_restart_check(name: str) -> tuple[bool, str]:
    ""


    mode = occupancy_gpu_mode()
    ok = True
    notes: list[str] = []
    for spec in _service_units(name):
        if _unit_policy(spec) == "report":
            if _http_ok(spec["health"]):
                notes.append(f"{spec['unit']} healthy (report only, not restarted; restart owed)")
            elif _unit_parked(spec, mode):
                notes.append(f"{spec['unit']} parked ({mode})")
            else:
                notes.append(f"{spec['unit']} DOWN (report only, not restarted)")
            continue
        if _unit_parked(spec, mode) and not _http_ok(spec["health"]):
            notes.append(f"{spec['unit']} parked ({mode}); not started")
            continue
        rc, _o, err = _svc_systemctl(spec, ["restart", spec["unit"]], timeout=120)
        if rc != 0:
            ok = False
            notes.append(f"restart {spec['unit']} refused: {(err or '').strip()[:160]}")
            continue
        if _wait_health(spec):
            notes.append(f"{spec['unit']} restarted, {spec['health']} healthy")
        else:
            ok = False
            ev = _crash_evidence(spec["unit"], spec.get("scope") or "system")
            notes.append(f"{spec['unit']} restarted but {spec['health']} did not come back"
                         + (f" — cause: {ev}" if ev else ""))
    return ok, "; ".join(notes)


def service_verify_after_apply(name: str, py: str, result: dict) -> dict:
    ""


    if name not in SERVICE_ENVS or not result.get("upgraded"):
        return result
    units = _service_units(name)
    owed = [u["unit"] for u in units if _unit_policy(u) == "report"]
    if owed:
        result["restart_owed"] = owed
    if not any(_unit_policy(u) == "restart" for u in units):
        result["service"] = ("restart owed (report-only units, not restarted by M&S): "
                             + ", ".join(owed))
        return result
    if not MS_SERVICE_RESTART:
        result["service"] = "restart owed (MS_SERVICE_RESTART=0)"
        result["restart_owed"] = [u["unit"] for u in units]
        return result
    busy = _restart_window_busy(units)
    if busy:
        pins = _rollback_pins(result)
        if pins:
            run(_installer(py) + ["--no-deps", "--reinstall", *pins], timeout=1800)
        log(f"service {name}: restart window says hold ({busy}) - rolled back {len(pins)} pins "
            f"without a restart; the greens wait for the window")
        result.update(ok=True, upgraded=[], failed=[], rolled_back=len(pins),
                      unverified=f"restart window busy: {busy}",
                      service=f"deferred: restart window busy ({busy}); {len(pins)} pins rolled back, nothing restarted")
        return result
    ok, note = service_restart_check(name)
    log(f"service {name}: {'GREEN' if ok else 'RED'} {note}")
    if ok:
        result["service"] = note
        return result
    pins = _rollback_pins(result)
    if pins:
        run(_installer(py) + ["--no-deps", "--reinstall", *pins], timeout=1800)
    ok2, note2 = service_restart_check(name)
    log(f"service {name}: rolled back {len(pins)} pins — {'GREEN' if ok2 else 'STILL RED'} {note2}")
    result.update(
        ok=False, failed=[u.split(" ")[0] for u in result.get("upgraded") or []],
        upgraded=[], rollback=True, service=note2,
        err=f"service RED after apply ({note}) — rolled back {len(pins)} pins"
            + ("" if ok2 else "; STILL RED — needs hands"),
    )
    return result


def _system_service_env_users() -> dict[str, list[str]]:
    ""


    out: dict[str, list[str]] = {}
    for env, _units in SERVICE_ENVS.items():
        for spec in _service_units(env):
            if spec.get("scope") != "system":
                continue
            rc, o, _e = _svc_systemctl(spec, ["is-active", spec["unit"]], timeout=10)
            if rc == 0 and o.strip() == "active":
                out.setdefault(env, []).append(f"unit:{_unit_short(spec)}")
    return out












MS_LAST_GOOD = os.environ.get("MS_LAST_GOOD", "1") != "0"
LAST_GOOD_KEEP = int(os.environ.get("MS_LAST_GOOD_KEEP", "14"))


def last_good_path(name: str) -> Path:
    return MANIFEST_DIR / f"{name}-LAST-GOOD.txt"


def last_good_qualifies(name: str, smoke_ok: bool | None, service_state: dict | None) -> tuple[bool, str]:
    if smoke_ok is not True:
        return False, "smoke not GREEN"
    if name in SERVICE_ENVS:
        verdict = (service_state or {}).get("verdict")
        if verdict != "ok":
            return False, f"service {verdict or 'unknown'}"
        return True, "smoke GREEN, service ok"
    return True, "smoke GREEN"


def write_last_good(
    name: str,
    py: str,
    *,
    smoke_ok: bool | None,
    service_state: dict | None,
    source: str,
    freeze_text: str | None = None,
) -> Path | None:
    ""

    if not MS_LAST_GOOD:
        return None
    ok, why = last_good_qualifies(name, smoke_ok, service_state)
    if not ok:
        log(f"last-good {name}: not written ({why})")
        return None
    text = freeze_text if freeze_text is not None else _freeze_text(py)
    if not text or not _freeze_versions_from_text(text):
        log(f"last-good {name}: not written (freeze failed)")
        return None
    now = dt.datetime.now()
    head = f"# LAST-GOOD {name} {now.isoformat(timespec='seconds')} source={source} ({why})\n"
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    out = last_good_path(name)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(head + text)
    tmp.replace(out)
    hist_dir = MANIFEST_DIR / "last-good"
    hist_dir.mkdir(parents=True, exist_ok=True)
    (hist_dir / f"{name}-{now.date().isoformat()}.txt").write_text(head + text)
    pat = re.compile(re.escape(name) + r"-\d{4}-\d{2}-\d{2}\.txt")
    hist = sorted(p for p in hist_dir.iterdir() if pat.fullmatch(p.name))
    for old in hist[:-LAST_GOOD_KEEP] if LAST_GOOD_KEEP > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    log(f"last-good {name}: {out} ({why}, source={source})")
    return out


def nightly_last_good(report: dict) -> dict[str, str | None]:
    ""


    out: dict[str, str | None] = {}
    if not MS_LAST_GOOD:
        return out
    applied = report.get("applied") or {}
    for name, info in (report.get("envs") or {}).items():
        py = str(info.get("python") or "")
        if not py or not Path(py).is_file():
            continue
        try:
            a = applied.get(name) or {}
            touched = bool(a.get("upgraded") or a.get("rollback") or a.get("failed"))
            smoke_ok = smoke(name, py) if touched else info.get("smoke_baseline")
            state = service_env_state(name, py, smoke_ok) if name in SERVICE_ENVS else None
            p = write_last_good(name, py, smoke_ok=smoke_ok, service_state=state, source="nightly")
            out[name] = str(p) if p else None
        except Exception as e:
            log(f"last-good {name}: error {e!r}")
            out[name] = None
    return out


def restore_plan_lines(
    name: str, py: str, last_good_text: str, now_text: str, *, uv: str | None = None
) -> list[str]:
    ""


    import shlex

    good = _freeze_versions_from_text(last_good_text)
    now = _freeze_versions_from_text(now_text)
    head = next((ln for ln in last_good_text.splitlines() if ln.startswith("# LAST-GOOD")), "")
    pins, local = [], []
    for pkg in sorted(good):
        if now.get(pkg) == good[pkg]:
            continue
        pin = f"{pkg}=={good[pkg]}"
        (local if "+" in good[pkg] else pins).append((pkg, pin))
    added = sorted(f"{k}=={v}" for k, v in now.items() if k not in good)
    lines = [f"# restore plan for {name} from {last_good_path(name)}"]
    if head:
        lines.append(f"# {head.lstrip('# ')}")
    if not pins and not local:
        lines.append("# nothing to restore: every LAST-GOOD package is installed at its LAST-GOOD version")
    else:
        lines.append(f"# {len(pins) + len(local)} package(s) missing or at a different version. "
                     "Printed, not run.")
        for pkg, pin in pins + local:
            lines.append(f"#   {pkg}: {now.get(pkg) or 'MISSING'} -> {pin.split('==', 1)[1]}")
    if pins:
        lines.append(" ".join([uv or UV or "uv", "pip", "install", "--python", shlex.quote(py),
                               "--no-deps"] + [shlex.quote(p) for _, p in pins]))
    if local:
        lines.append("# local builds (not on PyPI; reinstall from their own wheel/index by hand): "
                     + " ".join(p for _, p in local))
    if added:
        lines.append(f"# installed since LAST-GOOD (this plan does not remove them): {' '.join(added[:40])}"
                     + (f" (+{len(added) - 40})" if len(added) > 40 else ""))
    lines.append("# cu13 envs: keep the exact pins and --no-deps. A resolver run pulls cu12 wheels "
                 "from PyPI (2026-08-05 libnccl clobber). After it: smoke, and "
                 f"`ms-trail-run.py --service-check {name}` for a live service env.")
    return lines












DRIFT_PATH = Path(os.environ.get("MS_DRIFT", str(OUT_ROOT / "drift.jsonl")))
MS_DRIFT_CARDS = os.environ.get("MS_DRIFT_CARDS", "1") != "0"



MS_SURVEY_CARDS = os.environ.get("MS_SURVEY_CARDS", "0").strip() == "1"

MS_PARK_DATE_HOLDS = os.environ.get("MS_PARK_DATE_HOLDS", "1").strip() != "0"




MS_DEDUPE_CLOSED_DAYS = int(os.environ.get("MS_DEDUPE_CLOSED_DAYS", "14") or 0)

MS_RENDER_SOTA_MINOR = os.environ.get("MS_RENDER_SOTA_MINOR", "1").strip() != "0"


MS_RETIRE_OWN = os.environ.get("MS_RETIRE_OWN", "1").strip() != "0"
MS_RETIRE_QUIET_NIGHTS = int(os.environ.get("MS_RETIRE_QUIET_NIGHTS", "3") or 3)
DRIFT_HOLD_DECISIONS = ("hold", "pin", "never", "wontfix", "review_due")
_UNINSTALL_TARGETS = ("", "uninstalled", "uninstall", "removed", "none", "-")


def drift_moves(drift: dict) -> dict[str, tuple[str | None, str | None]]:
    ""
    moves: dict[str, tuple[str | None, str | None]] = {}
    for k, (a, b) in (drift.get("changed") or {}).items():
        moves[canon(k)] = (a, b)
    for k in drift.get("added") or []:
        moves[canon(k)] = (None, (drift.get("added_v") or {}).get(k))
    for k in drift.get("removed") or []:
        moves[canon(k)] = ((drift.get("removed_v") or {}).get(k), None)
    return moves


def reconcile_drift(env: str, drift: dict, ledger: dict, day: str) -> dict:
    rows = [r for r in (ledger.get("decisions") or []) if isinstance(r, dict)
            and str(r.get("env") or "").lower() == env.lower()]
    try:
        d0 = dt.date.fromisoformat(day[:10])
        days = {d0.isoformat(), (d0 - dt.timedelta(days=1)).isoformat()}
    except ValueError:
        days = {day[:10]}
    explained, unexplained, against = [], [], []
    for pkg, (frm, to) in sorted(drift_moves(drift).items()):
        mine = [r for r in rows if canon(str(r.get("package") or "")) == pkg]
        hit = None
        for r in mine:
            if effective_decision(r) != "applied" or str(r.get("ts") or "")[:10] not in days:
                continue
            tgt = str(r.get("target") or "").strip()
            if to is not None and (tgt == to or (not tgt and str(r.get("current") or "").strip() == to)):
                hit = r
            elif to is None and (tgt.lower() in _UNINSTALL_TARGETS
                                 or "uninstall" in str(r.get("reason") or "").lower()):
                hit = r
            if hit:
                break
        if hit:
            explained.append({"package": pkg, "to": to, "decision": "applied",
                              "ts": hit.get("ts"), "source": hit.get("source")})
        else:
            unexplained.append(pkg)
        for r in mine:
            dec = effective_decision(r)
            if not (dec in DRIFT_HOLD_DECISIONS and _decision_blocks_apply(r, env, {"package": pkg})):
                continue
            held = str(r.get("current") or "").strip()





            if dec == "wontfix":
                crossed = to is None
            else:
                crossed = to is None or not held or to != held
            if crossed:
                against.append({"package": pkg, "from": frm, "to": to, "decision": dec,
                                "target": r.get("target"), "source": r.get("source"),
                                "reason": str(r.get("reason") or "")[:240]})
                break
    return {"explained_by": explained, "unexplained": unexplained, "against_hold": against}


def _drift_rows() -> list[dict]:
    if not DRIFT_PATH.is_file():
        return []
    out = []
    for ln in DRIFT_PATH.read_text(errors="replace").splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _drift_card_create(*, title: str, body: str, idemp: str) -> tuple[bool, str]:
    ""

    closed = recently_closed(idemp)
    if closed:
        return False, f"not re-filed: {closed} closed inside {MS_DEDUPE_CLOSED_DAYS} d"
    try:
        r = subprocess.run(
            [*_kanban_argv(), "kanban", "create", title, "--body", body[:5500],
             "--assignee", _assignee("drift"), "--idempotency-key", idemp,
             "--workspace", f"dir:{DELIVERABLES_DIR}", "--created-by", "ms-trail"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        return False, repr(e)[:200]
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "")[:200]
    log(f"kanban drift card filed: {(r.stdout or '').strip()[:120] or title[:60]}")
    return True, (r.stdout or "").strip()[:120]


def _drift_card_body(row: dict) -> str:
    env = row["env"]
    held = {h["package"]: h for h in row.get("against_hold") or []}
    lines = [
        f"Out-of-band change to `{env}` seen by the {row.get('source')} at {row.get('ts')}. "
        "No ledger `applied` row at that version explains "
        + ("it" if row.get("unexplained") else "the hold crossings") + ".",
        "",
        "Changes:",
    ]
    for pkg, (frm, to) in sorted((row.get("changes") or {}).items()):
        tag = ""
        if pkg in held:
            h = held[pkg]
            tag = (f" — **against {h['decision']}** ({h.get('source') or 'no source'}: "
                   f"{h.get('reason') or 'no reason'})")
        elif pkg not in (row.get("unexplained") or []):
            tag = " — explained by an `applied` row"
        lines.append(f"- {pkg} {frm or '(absent)'} → {to or '(removed)'}{tag}")
    smoke_s = {True: "GREEN", False: "RED", None: "not run"}.get(row.get("smoke"))
    lines += [
        "",
        f"Smoke: {smoke_s}" + (f" — {row.get('smoke_err')}" if row.get("smoke_err") else ""),
    ]
    sv = row.get("service") or {}
    if sv:
        lines.append(f"Service: {sv.get('verdict')}"
                     + (f" · down {', '.join(sv.get('down') or [])}" if sv.get("down") else "")
                     + (f" · restart owed {', '.join(sv.get('restart_owed') or [])}"
                        if sv.get("restart_owed") else ""))
    lines += [
        f"Restore plan (prints, runs nothing): `python3 scripts/ms-trail-run.py --restore-plan {env}` "
        f"— from manifests/{env}-LAST-GOOD.txt, never from LATEST (the survey rewrites LATEST).",
        f"Drift ledger: {DRIFT_PATH}",
        "",
        "Do: find who installed it (journal, agent logs, cards around the timestamp). Keep it → "
        "`--record-decision --decision applied` with the reason (a change across a hold re-reads "
        "that hold's research first). Not wanted → run the restore plan's command, then the smoke"
        + (f" and `--service-check {env}`" if env in SERVICE_ENVS else "")
        + ". kanban_complete with: who, kept or restored, evidence.",
    ]
    return "\n".join(lines)


def record_drift(
    env: str,
    py: str,
    drift: dict,
    *,
    source: str,
    ledger: dict | None = None,
    smoke_ok: bool | None = None,
    service_state: dict | None = None,
    file_card: bool = True,
    now: dt.datetime | None = None,
) -> dict | None:
    ""

    moves = drift_moves(drift or {})
    if not moves:
        return None
    now = now or dt.datetime.now()
    day = now.date().isoformat()
    ledger = ledger if ledger is not None else _load_decisions()
    rec = reconcile_drift(env, drift, ledger, day)
    row: dict = {
        "ts": now.isoformat(timespec="seconds"),
        "day": day,
        "source": source,
        "env": env,
        "changes": {k: [a, b] for k, (a, b) in moves.items()},
        **rec,
        "smoke": None,
        "service": None,
        "card": None,
    }
    if rec["unexplained"] or rec["against_hold"]:
        if smoke_ok is None and py and Path(py).is_file():
            smoke_ok = smoke(env, py)
        row["smoke"] = smoke_ok
        if smoke_ok is False:
            row["smoke_err"] = SMOKE_ERR.get(env, "")
        if env in SERVICE_ENVS:
            st = service_state if service_state is not None else (
                service_env_state(env, py, smoke_ok) if py and Path(py).is_file() else None)
            if st:
                row["service"] = {k: st.get(k) for k in ("verdict", "down", "restart_owed", "parked")}
        key = f"ms-drift:{env}:{day}"
        if file_card and MS_DRIFT_CARDS:
            if any(r.get("card") == key and r.get("card_filed") for r in _drift_rows()):
                row["card"] = key
                row["card_filed"] = False
                row["card_note"] = "already filed today"
            else:
                n_hold = len(rec["against_hold"])
                title = (f"M&S drift: {env} — {len(moves)} out-of-band change(s)"
                         + (f", {n_hold} against a hold" if n_hold else "") + f" {day}")
                ok, note = _drift_card_create(title=title, body=_drift_card_body(row), idemp=key)
                row["card"] = key
                row["card_filed"] = ok
                row["card_note"] = note
    log(f"drift {env} ({source}): {len(moves)} change(s), {len(rec['unexplained'])} unexplained, "
        f"{len(rec['against_hold'])} against a hold"
        + (f", card {row['card']}" if row.get("card") else ""))
    DRIFT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DRIFT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def nightly_record_drift(report: dict, *, file_card: bool) -> list[dict]:
    ""

    out = []
    ledger = _load_decisions()
    for name, info in (report.get("envs") or {}).items():
        d = info.get("drift_since_last_manifest") or {}
        if not (d.get("ok") and d.get("had_manifest") and d.get("n")):
            continue
        try:
            row = record_drift(name, str(info.get("python") or ""), d, source="nightly",
                               ledger=ledger, smoke_ok=info.get("smoke_baseline"),
                               service_state=info.get("service"), file_card=file_card)
            if row:
                out.append({"env": name, "unexplained": row["unexplained"],
                            "against_hold": [h["package"] for h in row["against_hold"]],
                            "card": row.get("card")})
        except Exception as e:
            log(f"drift {name}: record error {e!r}")
    return out


def occupancy_gpu_mode() -> str:
    if not OCCUPANCY_ON:
        return "normal"
    try:
        raw = MODE_FILE.read_text(encoding="utf-8").strip().split()
        return (raw[0] if raw else "normal").lower()
    except Exception:
        return "normal"


def _models_ids(base: str) -> tuple[list[str] | None, str]:
    url = _models_url(base)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        ids = [str(x.get("id") or "") for x in (data.get("data") or [])]
        return [i for i in ids if i], "ok"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def live_vllm_target() -> tuple[str, str] | None:
    ""
    mode = occupancy_gpu_mode()
    order = sorted(CANARY_ENDPOINTS, key=lambda e: 0 if mode in (e.get("prefer_in") or []) else 1)
    for base, name in ((str(e.get("url") or ""), str(e.get("model") or "")) for e in order):
        ids, _err = _models_ids(base)
        if ids is not None and name in ids:
            return base, name
    return None


def canary_pong(base: str, model: str, *, timeout: int = 90) -> tuple[bool, str]:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word pong."}],
        "max_tokens": 8,
        "temperature": 0,
    }
    if model in _NO_THINKING:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        if getattr(r, "status", 200) >= 400:
            return False, f"{model} complete http {r.status}"
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get(
            "content"
        ) or ""
        if not str(text).strip():
            return False, f"{model} complete empty content"
        return True, f"{model} ok {text.strip()[:48]!r}"
    except Exception as e:
        return False, f"{model} complete {type(e).__name__}: {e}"


def canary_vllm() -> tuple[bool, str]:
    ""
    target = live_vllm_target()
    if not target:
        return False, _T("canary.no_target", mode=occupancy_gpu_mode())
    base, name = target
    return canary_pong(base, name)


def canary_ops() -> tuple[bool, str]:
    ""
    return canary_vllm()


def gpu_total_mib() -> int | None:
    rc, out, _err = run(
        [
            "nvidia-smi",
            "--query-gpu=memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=8,
    )
    if rc != 0:
        return None
    try:
        return int(float((out.strip().splitlines() or ["0"])[0].strip()))
    except ValueError:
        return None


def canary_05b_floor_mib(total_mib: int | None, spec: dict | None = None) -> int:
    ""


















    spec = spec or CANARY_05B
    floor = int(spec["free_mib_min"])
    if total_mib:
        floor = max(floor, int(math.ceil(float(spec["util"]) * int(total_mib))) + 256)
    return floor


VLLM_NO_ROOM = "is less than desired GPU memory utilization"


def _canary_log_error_line(text: str) -> str:
    ""
    for ln in reversed(text.splitlines()):
        if "Error" in ln and "Traceback" not in ln:
            return " ".join(ln.split())[-220:]
    return " ".join(text[-400:].split())[-200:]


def gpu_free_mib() -> int | None:
    rc, out, _err = run(
        [
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ],
        timeout=8,
    )
    if rc != 0:
        return None
    try:
        return int(float((out.strip().splitlines() or ["0"])[0].strip()))
    except Exception:
        return None


def _port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _kill_pg(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()


def canary_05b() -> tuple[bool, str]:
    ""





    spec = CANARY_05B
    _ms_baseline("start", "canary-05b")
    try:
        return _canary_05b(spec)
    finally:
        _ms_baseline("end", "canary-05b")


def _ms_baseline(which: str, label: str) -> None:
    ""
    try:
        import ms_baseline
        ms_baseline.start(label) if which == "start" else ms_baseline.end(label, settle_s=5.0)
    except Exception:
        pass


def _canary_05b(spec: dict) -> tuple[bool, str]:
    model = Path(spec["model"])
    if not (model / "config.json").is_file():
        return False, f"0.5B weights missing {model}"
    free = gpu_free_mib()
    if free is None:
        return True, "n/a 0.5B (nvidia-smi failed)"
    floor = canary_05b_floor_mib(gpu_total_mib(), spec)
    if free < floor:
        return True, f"n/a 0.5B free {free} MiB < {floor}"
    port = int(spec["port"])
    if _port_open(port):
        return True, f"n/a 0.5B :{port} already bound"
    vllm_bin = Path(_sp(spec.get("vllm_bin")) or "/nonexistent/ms-vllm")
    if not vllm_bin.is_file():
        return False, f"0.5B missing {vllm_bin}"
    log_path = OUT_ROOT / "logs" / "canary-05b.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        **({"PATH": str(spec["path"])} if spec.get("path") else {}),
        **({"CUDA_HOME": str(spec["cuda_home"])} if spec.get("cuda_home") else {}),
        "LD_LIBRARY_PATH": ":".join(p for p in (str(spec.get("ld_library_path") or ""),
                                                os.environ.get("LD_LIBRARY_PATH") or "") if p),
        "HF_HOME": os.environ.get("HF_HOME", str(spec.get("hf_home") or "")),
        "FLASHINFER_DISABLE_VERSION_CHECK": "1",
        "PYTORCH_CUDA_ALLOC_CONF": os.environ.get(
            "PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"
        ),
    }
    cmd = [
        str(vllm_bin),
        "serve",
        str(model),
        "--served-model-name",
        str(spec["name"]),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--dtype",
        "bfloat16",
        "--max-model-len",
        str(spec["max_len"]),
        "--max-num-seqs",
        str(spec["max_seqs"]),
        "--enforce-eager",
        "--gpu-memory-utilization",
        str(spec["util"]),
        "--kv-cache-memory-bytes",
        str(spec["kv_bytes"]),
        "--kv-cache-dtype",
        "fp8",
    ]
    logf = log_path.open("w", encoding="utf-8")
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        deadline = time.time() + 120
        base = f"http://127.0.0.1:{port}/v1"
        while time.time() < deadline:
            if proc.poll() is not None:
                text = ""
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
                if VLLM_NO_ROOM in text:


                    return True, (
                        f"n/a 0.5B no VRAM room (free {free} MiB, floor {floor}): "
                        + _canary_log_error_line(text)[:160]
                    )
                return False, f"0.5B exited {proc.returncode} {_canary_log_error_line(text)}"
            ids, err = _models_ids(base)
            if ids is not None and str(spec["name"]) in ids:
                ok, msg = canary_pong(base, str(spec["name"]), timeout=30)
                return (True, f"0.5B {msg}") if ok else (False, f"0.5B {msg}")
            time.sleep(1)
        return False, "0.5B timeout waiting /v1/models"
    except Exception as e:
        return False, f"0.5B {type(e).__name__}: {e}"
    finally:
        if proc is not None:
            _kill_pg(proc)
        try:
            logf.close()
        except Exception:
            pass


def live_canary(name: str, *, include_05b: bool = False) -> tuple[bool, str]:
    ""
    if name != CANARY_ENV:
        return True, ("n/a" if CANARY_ENV else "no canary configured")
    ok, msg = canary_vllm()
    log(f"canary live ({occupancy_gpu_mode()} {name}): {'GREEN' if ok else 'RED'} {msg}")
    if not ok or not include_05b:
        return ok, msg
    ok5, msg5 = canary_05b()
    log(f"canary 0.5B: {'UNVERIFIED' if (ok5 and canary_05b_skipped(msg5)) else 'GREEN' if ok5 else 'RED'} {msg5}")
    if ok5:
        return True, f"{msg}; {msg5}"
    return False, f"{msg}; 0.5B RED {msg5}"






MS_CANARY_SKIP_OK = os.environ.get("MS_CANARY_SKIP_OK", "0").strip() in ("1", "true", "on", "yes")








MS_REPORT_CANARY = os.environ.get("MS_REPORT_CANARY", "1").strip() not in ("0", "false", "off", "no")


def report_only_canary(name: str, report: dict) -> dict | None:
    ""
    if (not MS_REPORT_CANARY or name != CANARY_ENV
            or (report.get("envs", {}).get(name) or {}).get("report_only") != "report_only_setting"):
        return None
    ok, msg = live_canary(name, include_05b=True)
    unverified = ok and canary_05b_skipped(msg)
    row = report.setdefault("applied", {}).setdefault(
        name, {"ok": True, "upgraded": [], "skipped": "report_only_setting", "changed_n": 0})
    row["canary"] = f"UNVERIFIED {msg}" if unverified else msg
    row["canary_ok"] = ok
    if unverified:
        row["unverified"] = msg
    if not ok:
        row["ok"] = False
        row["err"] = f"vLLM canary RED on the live engine (report only, nothing was applied): {msg}"
    log(f"canary report-only {name}: {'UNVERIFIED' if unverified else 'GREEN' if ok else 'RED'} {msg}")
    return row


def canary_05b_skipped(msg: str) -> bool:
    ""
    return "n/a 0.5B" in (msg or "")


def canary_05b_precheck() -> str:
    ""


    spec = CANARY_05B
    free = gpu_free_mib()
    if free is None:
        return "n/a 0.5B (nvidia-smi failed)"
    floor = canary_05b_floor_mib(gpu_total_mib(), spec)
    if free < floor:
        return f"n/a 0.5B free {free} MiB < {floor}"
    port = int(spec["port"])
    if _port_open(port):
        return f"n/a 0.5B :{port} already bound"
    return ""


def drain_rows(window_rows: list[dict]) -> list[dict]:
    ""
    out: list[dict] = []
    for row in window_rows or []:
        pkg = str(row.get("package") or "")
        cur = str(row.get("current") or "")
        lat = str(row.get("latest") or row.get("target") or "")
        if classify_update(pkg, cur, lat) == "green_candidate":
            out.append(row)
    return out


def drain_pending_specialist(
    pending: list[dict],
    *,
    skip: set[str] | None = None,
    never_auto: set[str] | None = None,
    never_prefixes: tuple[str, ...] | None = None,
    max_n: int = 0,
) -> list[dict]:
    ""






    never_auto = never_auto if never_auto is not None else NEVER_AUTO
    never_prefixes = never_prefixes if never_prefixes is not None else NEVER_AUTO_PREFIXES
    skip = {canon(x) for x in (skip or set())}
    out: list[dict] = []
    for row in pending or []:
        pkg = canon(str(row.get("package") or ""))
        if not pkg or pkg in skip:
            continue
        if pkg in never_auto or pkg.startswith(never_prefixes):
            continue
        cur = str(row.get("current") or "")
        lat = str(row.get("latest") or row.get("target") or "")
        cls = classify_update(pkg, cur, lat)
        if cls in ("green_candidate", "never"):
            continue
        out.append(row)
        if max_n and len(out) >= max_n:
            break
    return out


_MITIGATION_ENV = re.compile(
    r"M&S mitigation:\s*([A-Za-z0-9_.-]+)\s+—", re.I
)
_LAND_ENV = re.compile(r"M&S land:\s*([A-Za-z0-9_.-]+)\b", re.I)
_PKG_ARROW = re.compile(
    r"^[-*]\s+([A-Za-z0-9_.\[\]-]+)\s+(\S+)\s*(?:→|->)\s*(\S+)",
    re.M,
)
_KANBAN_DONE = re.compile(
    r"^[✓?]\s+(t_[0-9a-f]{8})\s+(done|completed)\b", re.I
)
_KANBAN_ROW = re.compile(
    r"^[⊘◻✓?●▶⏱]\s+(t_[0-9a-f]{8})\s+(\S+)\s+(\S+)\s+(.*)$"
)
_GO_NO = (
    "no apply this card",
    "do not apply",
    "decision=hold",
    "decision: hold",
    "wontfix",
    "verdict: hold",
    "verdict hold",
)
_GO_YES = (
    "pending_apply",
    "land now",
    "host apply",
    "decision=applied",
    "decision: applied",
)


def parse_ms_mitigation_env(title: str) -> str | None:
    m = _MITIGATION_ENV.search(title or "")
    return m.group(1) if m else None


def parse_ms_pkg_arrows(text: str) -> list[dict]:
    ""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _PKG_ARROW.finditer(text or ""):
        pkg = canon(m.group(1))
        if not pkg or pkg in seen:
            continue
        seen.add(pkg)
        out.append(
            {
                "package": pkg,
                "current": m.group(2).strip(),
                "latest": m.group(3).strip().rstrip(".,;"),
                "from": "kanban",
            }
        )
    return out






_TEMPLATE_VERDICT_MARKERS = (
    "only if it must never land",
    "on a green target is the deferral queue",
    "refuse complete if there is",
)


def _strip_template_lines(text: str) -> str:
    return "\n".join(
        ln for ln in (text or "").splitlines()
        if not any(m in ln.lower() for m in _TEMPLATE_VERDICT_MARKERS)
    )


def specialist_go_from_card_text(text: str) -> bool:
    ""



    text = _strip_template_lines(text)
    low = (text or "").lower()
    if any(n in low for n in _GO_NO):
        return False
    if any(n in low for n in _GO_YES):
        return True
    return bool(parse_ms_pkg_arrows(text))


def parse_kanban_list_row(line: str) -> dict | None:
    m = _KANBAN_ROW.match((line or "").strip())
    if not m:
        return None
    return {
        "id": m.group(1),
        "status": m.group(2).lower(),
        "assignee": m.group(3).lower(),
        "title": m.group(4).strip(),
        "line": line,
    }


def _card_env(r: dict, key_prefix: str, title_parse) -> str | None:
    ""
    k = str(r.get("idempotency_key") or "")
    if k.startswith(key_prefix):
        return k[len(key_prefix):].split(":", 1)[0] or None
    if k:
        return None
    return title_parse(r.get("title") or "") or title_parse(r.get("line") or "")


def plan_kanban_ms_land(
    listing: str,
    shows: dict[str, str] | None = None,
    cards: list[dict] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, list[str]], list[dict]]:
    ""





    if cards is None:
        rows = [parse_kanban_list_row(ln) for ln in (listing or "").splitlines()]
        rows = [r for r in rows if r]
    else:
        rows = [dict(r, line=r.get("line") or "") for r in cards]
    land_envs = {_card_env(r, "ms-land:", parse_ms_land_env) for r in rows}
    land_envs.discard(None)
    by_env: dict[str, list[dict]] = {}
    land_ids: dict[str, list[str]] = {}
    spawns: list[dict] = []

    def _show(tid: str) -> tuple[str, str]:
        ""
        if shows is not None:
            s = shows.get(tid) or ""
            return s, s
        return _kanban_show_parts(tid)

    for r in rows:
        env = _card_env(r, "ms-land:", parse_ms_land_env)
        if not env:
            continue
        if r["status"] not in ("ready", "scheduled"):
            continue
        text, _said = _show(r["id"])
        pkgs = parse_ms_pkg_arrows(text) or parse_ms_pkg_arrows(r["line"])
        if not pkgs:
            log(f"kanban land {r['id']} {env}: no package arrows")
            continue
        by_env[env] = merge_window_rows(by_env.get(env) or [], pkgs)
        land_ids.setdefault(env, []).append(r["id"])

    for r in rows:
        env = _card_env(r, "ms-mitigation:", parse_ms_mitigation_env)
        if not env:
            continue
        if r["status"] not in ("done", "completed"):
            continue
        if env in land_envs or env in by_env:
            continue
        text, said = _show(r["id"])
        blob = r["line"] + "\n" + said
        if not specialist_go_from_card_text(blob):
            continue
        pkgs = parse_ms_pkg_arrows(text) or parse_ms_pkg_arrows(said)
        if not pkgs:
            continue
        spawns.append(
            {
                "env": env,
                "parent_id": r["id"],
                "rows": pkgs,
            }
        )
    return by_env, land_ids, spawns


def parse_ms_land_env(title: str) -> str | None:
    m = _LAND_ENV.search(title or "")
    return m.group(1) if m else None


def _kanban_show_parts(tid: str) -> tuple[str, str]:
    ""


    try:
        rr = subprocess.run(
            [*_kanban_argv(), "kanban", "show", tid, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        d = json.loads(rr.stdout or "")
        if not isinstance(d, dict):
            raise ValueError("show --json is not an object")
    except Exception as e:
        log(f"kanban show --json {tid} failed {e!r} — text show, template lines dropped")
        text = _kanban_show(tid)
        return text, _strip_template_lines(text)
    task = d.get("task") or {}
    said = [task.get("result"), d.get("latest_summary")]
    said += [c.get("body") for c in d.get("comments") or [] if isinstance(c, dict)]
    said += [r.get("summary") for r in d.get("runs") or [] if isinstance(r, dict)]
    return str(task.get("body") or ""), "\n".join(str(x) for x in said if x)


def _kanban_show(tid: str) -> str:
    try:
        rr = subprocess.run(
            [*_kanban_argv(), "kanban", "show", tid],
            capture_output=True, text=True, timeout=30,
        )
        return rr.stdout or ""
    except Exception as e:
        log(f"kanban show {tid} failed {e!r}")
        return ""


def _kanban_create_land_child(env: str, parent_id: str, rows: list[dict]) -> str | None:
    ""
    arrows = "\n".join(
        f"- {r['package']} {r.get('current') or '?'} → {r.get('latest') or '?'}"
        for r in rows
    )
    body = _T("card.land.body", env=env, parent=parent_id, arrows=arrows)
    title = f"M&S land: {env}"
    hermes = _hermes_bin()
    try:
        r = subprocess.run(
            [
                *_kanban_argv(hermes), "kanban", "create", title,
                "--body", body[:5500],
                "--assignee", _assignee("land"),
                "--parent", parent_id,
                "--idempotency-key", f"ms-land:{env}:{parent_id}",
                "--created-by", "ms-trail",
                "--workspace", f"dir:{DELIVERABLES_DIR}",
            ],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        log(f"kanban land child FAIL {env}: {e!r}")
        return None
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        log(f"kanban land child FAIL {env}: {out[:200]}")
        return None
    m = re.search(r"\b(t_[0-9a-f]{8})\b", out)
    tid = m.group(1) if m else None
    log(f"kanban land child {tid or '?'} {env} parent={parent_id}")
    return tid


def ingest_kanban_ms_go(
    listing: str | None = None,
    *,
    shows: dict[str, str] | None = None,
    spawn: bool = True,
    cards: list[dict] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    ""




    hermes = _hermes_bin()
    if listing is None:
        try:
            r = subprocess.run(
                [*_kanban_argv(hermes), "kanban", "list"],
                capture_output=True, text=True, timeout=30,
            )
            listing = r.stdout or ""
        except Exception as e:
            log(f"kanban ingest list failed {e!r}")
            return {}, {}
        if cards is None:
            cards = kanban_board_cards(listing)
    by_env, land_ids, spawns = plan_kanban_ms_land(listing, shows=shows, cards=cards)
    for spec in spawns:
        env = spec["env"]
        tid = None
        hold = env_hold(env)
        if hold:
            log(f"kanban schedule land {env}: env on hold — {hold.get('reason')}; "
                f"no land card, parent={spec['parent_id']}")
            continue
        if spawn and shows is None:
            tid = _kanban_create_land_child(env, spec["parent_id"], spec["rows"])
        by_env[env] = merge_window_rows(by_env.get(env) or [], spec["rows"])
        if tid:
            land_ids.setdefault(env, []).append(tid)
        log(
            f"kanban schedule land {env} parent={spec['parent_id']} "
            f"child={tid or 'dry'} pkgs="
            + ",".join(r["package"] for r in spec["rows"][:8])
        )
    return by_env, land_ids


def complete_ms_land_cards(env: str, ids: list[str], result: dict) -> None:
    ""
    if not ids:
        return
    ok = bool(result.get("ok", True))
    n = len(result.get("upgraded") or [])
    if not ok or n == 0:
        log(f"kanban land leave open {env}: ok={ok} upgraded={n}")
        return
    summary = _T("card.land.complete", env=env, n=n)
    hermes = _hermes_bin()
    for tid in ids:
        rr = subprocess.run(
            [*_kanban_argv(hermes), "kanban", "complete", tid, "--result", summary[:500]],
            capture_output=True, text=True, timeout=30,
        )
        log(f"kanban complete land {tid} {env} rc={rr.returncode}")


def window_apply_plan(
    name: str,
    *,
    train_busy: bool = False,
    force_train: bool = False,
    canary_ok: bool | None = None,
    canary_unverified: str = "",
) -> str:
    ""








    if name in GPU_SHARED_ENVS and train_busy and not force_train:
        return "skip_train_busy"
    if name == CANARY_ENV and canary_ok is False:
        return "skip_canary_red"
    if name == CANARY_ENV and canary_unverified and not MS_CANARY_SKIP_OK:
        return "skip_canary_unverified"
    return "apply"


NOTIFY_CMD = [_sp(x) for x in (_S.get("notify.cmd") or [])]
NOTIFY_SCRIPT = Path(NOTIFY_CMD[-1]) if NOTIFY_CMD else Path("/nonexistent/ms-notify")


def notify_user(text: str) -> str:
    ""
    if not NOTIFY_CMD:
        log("notify: no notify.cmd set - message not sent")
        return "no notify.cmd"
    if NOTIFY_CMD[-1].endswith(".py") and not NOTIFY_SCRIPT.is_file():
        return f"missing {NOTIFY_SCRIPT.name}"
    msg = (text or "").strip()[:400]
    if not msg:
        return "empty"
    try:
        r = subprocess.run(
            [*NOTIFY_CMD, msg],
            capture_output=True,
            text=True,
            timeout=25,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        log(f"{_T('log.notify')} rc={r.returncode} {out[:160]}")
        return out[:200]
    except Exception as e:
        log(f"{_T('log.notify')} fail {type(e).__name__}: {e}")
        return f"{type(e).__name__}: {e}"


def own_prefix_packages(py: str) -> set[str] | None:
    ""



    code = (
        "import json,sys\n"
        "import importlib.metadata as md\n"
        "own=[]\n"
        "for d in md.distributions():\n"
        "    try: p=str(d.locate_file(''))\n"
        "    except Exception: continue\n"
        "    if p.startswith(sys.prefix): own.append(d.metadata['Name'])\n"
        "print(json.dumps(own))"
    )
    rc, out_s, err = run([py, "-c", code], timeout=120)
    if rc != 0:
        log(f"own-prefix probe FAIL: {err[:160]}")
        return None
    try:
        return {canon(x) for x in json.loads(out_s) if x}
    except json.JSONDecodeError:
        return None


_UNUSED_PROBE = r"""
import json, re, sys
import importlib.metadata as md
from pathlib import Path
def canon(n): return re.sub(r"[-_.]+", "-", (n or "")).lower()
dists = list(md.distributions())
required = set()
for d in dists:
    for r in d.requires or []:
        # An `extra ==` requirement counts too (2026-09-17). Skipping them
        # called fastmcp-slim[server]'s jsonref "required by nothing"; the
        # unused sweep removed it and hindsight crash-looped at the next boot.
        # An installed extra dep is referenced; a squatter is still caught.
        body, _, marker = r.partition(";")
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", body)
        if m: required.add(canon(m.group(1)))
unreferenced, never = [], []
for d in dists:
    n = canon(d.metadata["Name"])
    if not n or n in required: continue
    try:
        tops = (d.read_text("top_level.txt") or "").split()
    except Exception:
        tops = []
    if not tops:
        files = list(d.files or [])
        tops = sorted({f.parts[0] for f in files if f.suffix == ".py" and len(f.parts) > 1}
                      | {f.stem for f in files if f.suffix == ".py" and len(f.parts) == 1})
    site = Path(str(d.locate_file("")))
    seen = False
    for t in tops:
        pd = site / t
        if pd.is_dir() and any(pd.rglob("__pycache__/*.pyc")): seen = True; break
        if (site / (t + ".py")).exists() and any((site / "__pycache__").glob(t + ".*.pyc")): seen = True; break
    unreferenced.append(n)
    if not seen: never.append(n)
print(json.dumps({"unreferenced": sorted(unreferenced), "never_imported": sorted(never), "dists": len(dists)}))
"""


def unused_packages(py: str) -> dict | None:
    ""









    rc, out_s, err = run([py, "-c", _UNUSED_PROBE], timeout=180)
    if rc != 0:
        log(f"unused probe FAIL: {(err or '')[:160]}")
        return None
    try:
        d = json.loads(out_s)
    except json.JSONDecodeError:
        return None
    return guard_unused(py, {
        "unreferenced": [canon(x) for x in d.get("unreferenced") or []],
        "never_imported": [canon(x) for x in d.get("never_imported") or []],
        "dists": int(d.get("dists") or 0),
    })


def unused_undecided(env: str, unused: dict | None, ledger: dict | None = None) -> list[str]:
    ""

    if not unused:
        return []
    ledger = ledger or _load_decisions()
    decided = {
        canon(str(r.get("package") or ""))
        for r in (ledger.get("decisions") or [])
        if isinstance(r, dict) and str(r.get("env") or "").lower() == env.lower()
        and str(r.get("decision") or "").lower() in ("wontfix", "hold", "pin", "never", "reviewed", "cancelled")
    }
    return [x for x in unused.get("never_imported") or [] if x not in decided]








MS_UNUSED_GUARD = os.environ.get("MS_UNUSED_GUARD", "1") != "0"
UNUSED_GUARD_MIN_AGE_DAYS = float(os.environ.get("MS_UNUSED_MIN_AGE_DAYS", "7"))
UNUSED_GUARD_PREFIXES = NEVER_AUTO_PREFIXES + ("torch", "triton", "xformers", "flash-attn")

_DIST_FILES_PROBE = r"""
import json, os, re, sys
import importlib.metadata as md
def canon(n): return re.sub(r"[-_.]+", "-", (n or "")).lower()
want = set(json.loads(sys.argv[1]))
out = {}
for d in md.distributions():
    n = canon(d.metadata["Name"])
    if n not in want or n in out: continue
    di = getattr(d, "_path", None)
    try:
        st = os.stat(str(di)); ts = max(st.st_mtime, st.st_ctime)
    except Exception:
        ts = None
    files = []
    for f in d.files or []:
        try: files.append(os.path.realpath(str(d.locate_file(f))))
        except Exception: pass
    out[n] = {"dist_info": str(di), "installed_ts": ts, "files": files}
print(json.dumps(out))
"""


def _dist_files(py: str, names: list[str]) -> dict | None:
    ""

    rc, out_s, err = run([py, "-c", _DIST_FILES_PROBE, json.dumps(sorted(set(names)))], timeout=180)
    if rc != 0:
        log(f"unused guard probe FAIL: {(err or '')[:160]}")
        return None
    try:
        return {canon(k): v for k, v in json.loads(out_s).items()}
    except (json.JSONDecodeError, AttributeError):
        return None


_MAPPED_CACHE: dict = {"ts": 0.0, "paths": set()}


def _live_mapped_paths(proc_root: Path = Path("/proc"), *, max_age_s: float = 300.0) -> set[str]:
    ""

    if proc_root == Path("/proc") and time.time() - _MAPPED_CACHE["ts"] < max_age_s:
        return _MAPPED_CACHE["paths"]
    paths: set[str] = set()
    try:
        procs = list(proc_root.iterdir())
    except OSError:
        procs = []
    for p in procs:
        if not p.name.isdigit():
            continue
        try:
            text = (p / "maps").read_text(errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            parts = ln.split(None, 5)
            if len(parts) == 6 and parts[5].startswith("/"):
                path = parts[5]
                if path.endswith(" (deleted)"):
                    path = path[: -len(" (deleted)")]
                paths.add(path)
    if proc_root == Path("/proc"):
        _MAPPED_CACHE.update(ts=time.time(), paths=paths)
    return paths


def unused_guard_reasons(
    names: list[str],
    dists: dict | None,
    mapped: set[str],
    now: float,
    *,
    min_age_days: float = UNUSED_GUARD_MIN_AGE_DAYS,
) -> dict[str, str]:
    ""
    reasons: dict[str, str] = {}
    for n in names:
        c = canon(n)
        if c in NEVER_AUTO or c.startswith(UNUSED_GUARD_PREFIXES):
            reasons[n] = "never-auto class (CUDA/torch family loads by dlopen, no .pyc)"
            continue
        info = (dists or {}).get(c)
        if dists is None or info is None:
            reasons[n] = "unverified (dist probe failed or dist not found)"
            continue
        hit = next((f for f in info.get("files") or [] if f in mapped), None)
        if hit:
            reasons[n] = f"mapped by a live process: {hit}"
            continue
        ts = info.get("installed_ts")
        if ts is None:
            reasons[n] = "unverified (dist-info age unknown)"
        elif now - float(ts) < min_age_days * 86400:
            reasons[n] = f"installed {(now - float(ts)) / 86400:.1f} d ago (< {min_age_days:g})"
    return reasons


def guard_unused(py: str, unused: dict | None) -> dict | None:
    ""

    if not unused or not MS_UNUSED_GUARD:
        return unused
    names = list(unused.get("never_imported") or [])
    if not names:
        unused["guarded"] = {}
        return unused
    if not Path("/proc/self/maps").exists():

        unused["never_imported"] = []
        unused["guarded"] = {n: "unverified (no /proc on this platform)" for n in names}
        return unused
    reasons = unused_guard_reasons(names, _dist_files(py, names), _live_mapped_paths(), time.time())
    unused["never_imported"] = [n for n in names if n not in reasons]
    unused["guarded"] = reasons
    return unused



PYPI_CACHE_DAYS = int(os.environ.get("MS_PYPI_CACHE_DAYS", "7"))
PYPI_LOOKUPS_MAX = int(os.environ.get("MS_PYPI_LOOKUPS_MAX", "40"))
DEAD_UPSTREAM_YEARS = int(os.environ.get("MS_DEAD_UPSTREAM_YEARS", "5"))
_pypi_lookups_tonight = 0


def pypi_upstream(pkg: str) -> dict | None:
    ""


    global _pypi_lookups_tonight
    name = canon(pkg)
    if not name:
        return None
    cache = OUT_ROOT / "pypi-cache" / f"{name}.json"
    try:
        if cache.is_file():
            age = time.time() - cache.stat().st_mtime
            if age < PYPI_CACHE_DAYS * 86400:
                return json.loads(cache.read_text())
    except Exception:
        pass
    if _pypi_lookups_tonight >= PYPI_LOOKUPS_MAX:
        return None
    _pypi_lookups_tonight += 1
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    info = d.get("info") or {}
    last = ""
    for _v, files in (d.get("releases") or {}).items():
        for f in files or []:
            t = str(f.get("upload_time") or "")[:10]
            if t > last:
                last = t
    latest_files = (d.get("releases") or {}).get(str(info.get("version") or ""), []) or []
    out = {
        "name": name,
        "latest": str(info.get("version") or ""),
        "last_upload": last,
        "sdist_only": bool(latest_files) and all(str(f.get("packagetype")) == "sdist" for f in latest_files),
        "summary": str(info.get("summary") or "")[:120],
        "author": str(info.get("author") or "")[:60],
        "fetched": dt.date.today().isoformat(),
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out))
    except Exception:
        pass
    return out


def upstream_is_dead(info: dict | None, *, years: int | None = None, today: dt.date | None = None) -> bool:
    ""


    if not info or not info.get("last_upload"):
        return False
    years = DEAD_UPSTREAM_YEARS if years is None else years
    try:
        last = dt.date.fromisoformat(str(info["last_upload"])[:10])
    except ValueError:
        return False
    return (today or dt.date.today()) - last > dt.timedelta(days=365 * years)


def upstream_note(pkg: str) -> str:
    ""
    info = pypi_upstream(pkg)
    if not upstream_is_dead(info):
        return ""
    bits = [f"last release {info.get('last_upload')}"]
    if info.get("sdist_only"):
        bits.append("sdist only")
    if info.get("summary"):
        bits.append(f'"{info["summary"][:60]}"')
    return f"PyPI `{canon(pkg)}`: " + ", ".join(bits)


def _freeze_versions(path: Path | None) -> dict[str, str]:
    if not path or not Path(path).is_file():
        return {}
    vers: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r"([A-Za-z0-9_.\-]+)==(\S+)$", line.strip())
        if m:
            vers[canon(m.group(1))] = m.group(2)
    return vers


def _installer(py: str) -> list[str]:
    if UV:
        return [UV, "pip", "install", "--python", py]
    return [py, "-m", "pip", "install"]


def _pip_check_set(py: str) -> set[str]:
    ""



    if not UV:


        rc, out_s, err = run([py, "-m", "pip", "check"], timeout=120)
        text = (out_s or "") + "\n" + (err or "")
        return {ln.strip() for ln in text.splitlines()
                if ("requires" in ln and "installed" in ln) or "has requirement" in ln}
    rc, out_s, err = run([UV, "pip", "check", "--python", py], timeout=120)
    text = (out_s or "") + "\n" + (err or "")
    return {
        ln.strip() for ln in text.splitlines()
        if "requires" in ln and "installed" in ln
    }


def _missing_forward_deps(bad_lines, touched: set[str]) -> list[str]:
    ""
    out: list[str] = []
    for ln in bad_lines or []:
        m = _FWD_DEP_RE.search(str(ln))
        if not m or canon(m.group(1)) not in touched:
            continue
        dep = _req_name(m.group(2))
        if dep and dep not in out:
            out.append(dep)
    return out


def _forward_dep_allowed(env: str, dep: str) -> bool:
    d = canon(dep)
    if d in NEVER_AUTO or d.startswith(NEVER_AUTO_PREFIXES):
        return False
    _ok, blocked = filter_apply_blocked(env, [{"package": d}])
    return not blocked


MS_LOCAL_VERSION_GUARD = os.environ.get("MS_LOCAL_VERSION_GUARD", "1") != "0"
MS_KANBAN_INGEST_FOLLOWS_FLAG = os.environ.get("MS_KANBAN_INGEST_FOLLOWS_FLAG", "1") != "0"


def is_local_version_row(row: dict) -> bool:
    ""


    if not MS_LOCAL_VERSION_GUARD:
        return False
    return any("+" in str(row.get(k) or "") for k in ("current", "latest", "target"))


def apply_green(name: str, py: str, rows: list[dict]) -> dict:
    ""

    if not rows:
        return {"ok": True, "upgraded": [], "failed": []}


    rows, blocked = filter_apply_blocked(name, rows)
    blocked_pkgs = sorted(x["package"] for x in blocked)
    if blocked_pkgs:
        log(f"apply-green {name}: {len(blocked_pkgs)} blocked by decision ledger: "
            f"{blocked_pkgs[:6]}")
    if not rows:
        return {"ok": True, "upgraded": [], "failed": [],
                "blocked_by_decision": blocked_pkgs}
    own = own_prefix_packages(py)
    if own is None:
        return {"ok": False, "upgraded": [], "failed": [],
                "err": "own-prefix probe failed — refusing to touch env"}






    local = sorted(x["package"] for x in rows if is_local_version_row(x))
    if local:
        log(f"apply-green {name}: skipping {len(local)} local-version build(s) "
            f"(+label, not from PyPI): {local[:6]}")
        rows = [x for x in rows if not is_local_version_row(x)]
    layered = sorted(x["package"] for x in rows if canon(x["package"]) not in own)
    todo = [x["package"] for x in rows if canon(x["package"]) in own]
    if layered:
        log(f"apply-green {name}: skipping {len(layered)} layered/shared pkgs "
            f"(outside env prefix): {layered[:6]}")
    if not todo:
        return {"ok": True, "upgraded": [], "failed": [], "skipped_layered": layered,
                "skipped_local": local, "blocked_by_decision": blocked_pkgs}
    pre_bad = _pip_check_set(py)
    before = freeze_env(name + "-pre", py, write_latest=False)
    if before is None:
        return {"ok": False, "upgraded": [], "failed": todo, "err": "pre-freeze failed"}


    rc, out_s, err = run(_installer(py) + ["--no-deps", "-U", *todo], timeout=1800)
    failed: list[dict] = []
    if rc != 0:

        log(f"apply-green {name}: batch resolve failed, per-package retry")
        for p in todo:
            rc1, _, e1 = run(_installer(py) + ["--no-deps", "-U", p], timeout=600)
            if rc1 != 0:
                failed.append({"package": p, "err": (e1 or "")[:200]})
    after = freeze_env(name + "-post", py, write_latest=False)
    post_bad = _pip_check_set(py)
    pre_v, post_v = _freeze_versions(before), _freeze_versions(after)
    changed = {k: (pre_v[k], post_v[k])
               for k in post_v if k in pre_v and pre_v[k] != post_v[k]}
    added = sorted(k for k in post_v if k not in pre_v)







    touched = {canon(k) for k in changed} | {canon(k) for k in added}
    new_bad = sorted(x for x in (post_bad - pre_bad)
                     if any(p and p in x.lower() for p in touched))
    if new_bad:
        log(f"apply-green {name}: NEW dependency conflicts from this apply — "
            + " | ".join(new_bad[:3]))
    smoke_ok = smoke(name, py)
    canary_ok, canary_msg = live_canary(name, include_05b=True)

    def _rollback(changed_map: dict[str, tuple[str, str]], added_pkgs: list[str]) -> None:
        pins = [f"{k}=={old}" for k, (old, _new) in changed_map.items() if "+" not in old]
        if added_pkgs:
            if UV:
                run([UV, "pip", "uninstall", "--python", py, *added_pkgs], timeout=600)
            else:
                run([py, "-m", "pip", "uninstall", "-y", *added_pkgs], timeout=600)
        if pins:
            rc_r, _, err_r = run(
                _installer(py) + ["--no-deps", "--reinstall", *pins], timeout=1800
            )
            if rc_r != 0:
                log(f"apply-green {name}: ROLLBACK INSTALL FAILED: {err_r[:200]}")

    if (not smoke_ok or not canary_ok) and changed:
        why = "vLLM canary RED" if not canary_ok else "smoke RED after upgrade"
        log(f"apply-green {name}: {why} — rolling back {len(changed)} pins")
        _rollback(changed, added)
        recovered = smoke(name, py)
        if name == CANARY_ENV:
            cok, cmsg = live_canary(name, include_05b=False)
            recovered = recovered and cok
            canary_msg = cmsg
        return {
            "ok": False,
            "upgraded": [],




            "failed": todo,
            "rollback": True,
            "rolled_back": len(changed),
            "pre_freeze": str(before),
            "new_conflicts": new_bad,
            "canary": canary_msg,
            "err": f"{why} — rolled back to pre-freeze pins"
                   + ("" if recovered else "; ROLLBACK STILL RED — needs hands"),
        }

    if (name == CANARY_ENV and changed and not MS_CANARY_SKIP_OK
            and canary_05b_skipped(canary_msg)):
        log(f"apply-green {name}: canary UNVERIFIED ({canary_msg}) — rolling back "
            f"{len(changed)} pins; the window retries")
        _rollback(changed, added)
        return {
            "ok": True,
            "upgraded": [],
            "failed": [],
            "unverified": canary_msg,
            "rolled_back": len(changed),
            "changed_n": 0,
            "canary": canary_msg,
            "skipped_layered": layered,
            "blocked_by_decision": blocked_pkgs,
        }

    if new_bad:



        log(f"apply-green {name}: isolating {len(todo)} packages after batch conflict")
        _rollback(changed, added)
        kept: list[str] = []
        for p in todo:
            pre_text = _freeze_text(py) or ""
            pre_v = _freeze_versions_from_text(pre_text)
            pre_bad_i = _pip_check_set(py)
            rc1, _, e1 = run(_installer(py) + ["--no-deps", "-U", p], timeout=600)
            if rc1 != 0:
                failed.append({"package": p, "err": (e1 or "")[:200]})
                continue
            post_text = _freeze_text(py) or ""
            post_v = _freeze_versions_from_text(post_text)
            one_changed = {
                k: (pre_v[k], post_v[k])
                for k in post_v if k in pre_v and pre_v[k] != post_v[k]
            }
            one_added = [k for k in post_v if k not in pre_v]
            touched_i = {canon(k) for k in one_changed} | {canon(k) for k in one_added}
            bad_i = sorted(
                x for x in (_pip_check_set(py) - pre_bad_i)
                if any(t and t in x.lower() for t in touched_i)
            )
            fwd = _missing_forward_deps(bad_i, touched_i) if (bad_i and MS_FORWARD_DEPS) else []
            fwd = [d for d in fwd if _forward_dep_allowed(name, d)]
            if fwd:
                log(f"apply-green {name}: {p} now requires {fwd} — installing forward dep(s) --no-deps")
                rcf, _, ef = run(_installer(py) + ["--no-deps", "-U", *fwd], timeout=600)
                if rcf != 0:
                    log(f"apply-green {name}: forward dep install failed: {(ef or '')[:160]}")
                post_text = _freeze_text(py) or ""
                post_v = _freeze_versions_from_text(post_text)
                one_changed = {
                    k: (pre_v[k], post_v[k])
                    for k in post_v if k in pre_v and pre_v[k] != post_v[k]
                }
                one_added = [k for k in post_v if k not in pre_v]
                touched_i = {canon(k) for k in one_changed} | {canon(k) for k in one_added}
                bad_i = sorted(
                    x for x in (_pip_check_set(py) - pre_bad_i)
                    if any(t and t in x.lower() for t in touched_i)
                )
            s_ok = smoke(name, py)
            c_ok, c_msg = live_canary(name, include_05b=False)
            canary_msg = c_msg
            if bad_i or not s_ok or not c_ok:
                log(f"apply-green {name}: drop {p} "
                    + ("conflict" if bad_i else "smoke/canary RED"))
                _rollback(one_changed, one_added)
                failed.append({"package": p, "err": (bad_i[0] if bad_i else "smoke/canary RED")[:200]})
                continue
            if one_changed:
                k0 = next(iter(one_changed))
                old, new = one_changed[k0]
                kept.append(f"{k0} {old}→{new}")
        freeze_env(name, py)
        return {
            "ok": True,
            "upgraded": kept,
            "changed_n": len(kept),
            "failed": failed,
            "skipped_layered": layered,
            "blocked_by_decision": blocked_pkgs,
            "canary": canary_msg,
            "isolated_after_conflict": True,
        }

    freeze_env(name, py)
    return {
        "ok": True,
        "upgraded": [f"{k} {old}→{new}" for k, (old, new) in sorted(changed.items())],
        "changed_n": len(changed),
        "failed": failed,
        "skipped_layered": layered,
        "blocked_by_decision": blocked_pkgs,
        "canary": canary_msg,
    }


def _freeze_versions_from_text(text: str) -> dict[str, str]:
    vers: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"([A-Za-z0-9_.\-]+)==(\S+)$", line.strip())
        if m:
            vers[canon(m.group(1))] = m.group(2)
    return vers


HINDSIGHT_ENV = Path(_sp(_S.get("hindsight.env_file")) or "/nonexistent/ms-hindsight.env")



HINDSIGHT_RETAIN_TOKENS_MAX_SANE = int(_S.get("hindsight.retain_tokens_max") or 0)







OPTIONAL_HEALTH = set(_S.get("health.optional") or [])
RENDER_ON = bool(_S.get("render.enabled"))
COMFY_STATS_URL = os.environ.get("MS_COMFY_STATS") or str(_S.get("render.comfy_stats_url") or "")
COMFY_ROOT = Path(os.environ.get("MS_COMFY_ROOT") or _sp(_S.get("render.comfy_root")) or "/nonexistent/ms-comfy")
COMFY_WF_DIR = Path(os.environ.get("MS_COMFY_WORKFLOWS", str(COMFY_ROOT / "workflows")))
APP_ROOT = Path(__file__).resolve().parents[1]
COMFY_RECIPES = Path(_sp(_S.get("render.recipes")) or "/nonexistent/ms-recipes.json")


def _env_file_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _served_model_ids(url: str, timeout: float = 3.0) -> list[str]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return [str(x.get("id") or "") for x in (data.get("data") or []) if x.get("id")]


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _comfy_stats(timeout: float = 3.0) -> tuple[bool, str]:
    ""
    try:
        with urllib.request.urlopen(COMFY_STATS_URL, timeout=timeout) as r:
            if not (200 <= r.status < 300):
                return False, ""
            data = json.loads(r.read().decode())
        ver = str((data.get("system") or {}).get("comfyui_version") or "")
        return True, ver
    except Exception:
        return False, ""


def health_tag(name: str, up: bool, *, mode: str, note: str = "") -> str:
    ""
    if name == "comfy":
        if mode == "render":
            return "GREEN" if up else "RED"
        return "LEAK" if up else "IDLE"
    if name in OPTIONAL_HEALTH and not up:
        return "IDLE"
    return "GREEN" if up else "RED"


def _git_describe(repo: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _git_short(repo: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _comfy_tag_key(tag: str) -> tuple[int, ...]:
    t = tag.strip().lstrip("v")
    head = t.split("-")[0]
    nums: list[int] = []
    for p in head.split("."):
        if p.isdigit():
            nums.append(int(p))
        else:
            break
    return tuple(nums or (0,))


def _git_latest_tag(repo: Path, timeout: float = 20.0) -> str:
    ""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "ls-remote", "--tags", "origin"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            return ""
        tags: list[str] = []
        for line in (r.stdout or "").splitlines():
            ref = line.split("\t")[-1].strip()
            if not ref.startswith("refs/tags/"):
                continue
            name = ref[len("refs/tags/") :]
            if name.endswith("^{}"):
                name = name[:-3]
            if re.match(r"^v?\d+\.\d+", name):
                tags.append(name)
        if not tags:
            return ""
        return max(tags, key=_comfy_tag_key)
    except Exception:
        return ""


def survey_render_tools(*, sota: bool = True) -> dict:
    ""




    if not RENDER_ON:
        return {"schema": _T("render.schema"), "skipped": "render integration off"}
    out: dict = {
        "schema": _T("render.schema"),
        "root": str(COMFY_ROOT),
        "git": {},
        "nodes": {},
        "recipes": [],
        "sota": {},
    }
    if (COMFY_ROOT / ".git").exists() or (COMFY_ROOT / ".git").is_file():
        desc = _git_describe(COMFY_ROOT)
        sha = _git_short(COMFY_ROOT)
        out["git"] = {"describe": desc, "sha": sha}
        latest = _git_latest_tag(COMFY_ROOT) if sota else ""
        behind = False
        if latest and desc:
            local = desc.split("-")[0]
            behind = _comfy_tag_key(latest) > _comfy_tag_key(local)
        out["sota"] = {
            "local": desc,
            "origin_latest": latest or None,
            "behind": behind,
            "path": _T("render.sota_path"),
        }
    nodes_root = COMFY_ROOT / "custom_nodes"
    for name in ("ComfyUI-LTXVideo", "ComfyUI-GGUF", "ComfyUI-Manager"):
        p = nodes_root / name
        if not (p / ".git").exists() and not (p / ".git").is_file():
            continue
        row = {"sha": _git_short(p), "describe": _git_describe(p)}
        if sota:
            latest = _git_latest_tag(p)
            row["origin_latest"] = latest or None
        out["nodes"][name] = row
    recs: list = []
    if COMFY_RECIPES.is_file():
        try:
            data = json.loads(COMFY_RECIPES.read_text(encoding="utf-8"))
            recs = data.get("recipes") or []
        except (OSError, json.JSONDecodeError):
            recs = []
    for rec in recs:
        if not isinstance(rec, dict) or not rec.get("id"):
            continue
        wf = str(rec.get("workflow") or "")
        out["recipes"].append(
            {
                "id": rec["id"],
                "kind": rec.get("kind") or "t2i",
                "workflow": wf,
                "wired": bool(wf) and (COMFY_WF_DIR / wf).is_file(),
                "studio": bool(rec.get("studio")),
            }
        )
    return out


def _served_or_empty(url: str, timeout: float = 3.0) -> list[str]:
    try:
        return _served_model_ids(url, timeout=timeout)
    except Exception:
        return []


def _models_url(base: str) -> str:
    b = (base or "").rstrip("/")
    if b.endswith("/v1"):
        return b + "/models"
    if b.endswith("/v1/models"):
        return b
    return (b or str(_S.get("hindsight.default_llm_base_url") or "")) + "/models"


def hindsight_llm_ok(
    env: dict[str, str],
    served_by_models_url: dict[str, list[str]],
) -> bool:
    ""





    prefixes = ("HINDSIGHT_API_LLM", "HINDSIGHT_API_RETAIN_LLM",
                "HINDSIGHT_API_REFLECT_LLM", "HINDSIGHT_API_CONSOLIDATION_LLM")
    wanted: list[tuple[str, str]] = []
    for pref in prefixes:
        model = (env.get(f"{pref}_MODEL") or "").strip()
        if not model:
            continue
        base = (env.get(f"{pref}_BASE_URL") or str(_S.get("hindsight.default_llm_base_url") or "")).strip()
        wanted.append((model, _models_url(base)))
    if not wanted:
        return False
    for model, url in wanted:
        ids = served_by_models_url.get(url)
        if ids is None:
            return False
        if model not in ids:
            return False
    return True


def health_lanes() -> tuple[dict[str, bool], dict[str, str]]:
    ""




    lanes = _S.get("health.lanes") or {}
    mode = occupancy_gpu_mode()
    comfy_up, comfy_ver = _comfy_stats() if any(
        (v or {}).get("kind") == "comfy" for v in lanes.values()) else (False, "")
    out: dict[str, bool] = {}
    served: dict[str, list] = {}
    for name, lane in lanes.items():
        lane = lane or {}
        if lane.get("kind") == "comfy":
            out[name] = comfy_up
        elif lane.get("model"):
            served[name] = _served_or_empty(lane["url"])
            out[name] = lane["model"] in served[name]
        else:
            out[name] = _http_ok(lane.get("url") or "")
    notes: dict[str, str] = {}
    for lname, lane in lanes.items():
        lane = lane or {}
        if lname not in out or out[lname] or lane.get("kind") == "comfy":
            continue
        if mode in (lane.get("parked_in") or []):
            notes[lname] = f"parked ({mode} occupancy)"
        elif lane.get("model"):
            notes[lname] = f"served {served.get(lname) or 'down'} — want '{lane['model']}'"
        elif lane.get("note_when_down"):
            notes[lname] = str(lane["note_when_down"])
    comfy = next((n for n, lane in lanes.items() if (lane or {}).get("kind") == "comfy"), None)
    if comfy is None:
        pass
    elif mode == "render":
        notes[comfy] = (
            _T("health.comfy.render_up", ver=comfy_ver) if comfy_up
            else _T("health.comfy.render_down", mode=mode)
        )
    elif comfy_up:
        notes[comfy] = (
            f"up during {mode} (v{comfy_ver or '?'}) — occupancy leak, park it"
        )
    else:
        notes[comfy] = f"parked ({mode} occupancy)"
    if not _S.get("hindsight.env_file"):
        notes["hindsight"] = "not checked: hindsight.env_file is not set in ms.yaml"
        return out, notes
    env = _env_file_kv(HINDSIGHT_ENV)
    served_map = {(lanes.get(n) or {}).get("url"): ids for n, ids in served.items()}

    for k, v in env.items():
        if k.endswith("_LLM_BASE_URL") and v:
            u = _models_url(v)
            if u not in served_map:
                served_map[u] = _served_or_empty(u)
    out["hindsight_llm"] = hindsight_llm_ok(env, served_map)
    if not out["hindsight_llm"]:
        notes["hindsight_llm"] = "consumer model name missing on its BASE_URL /v1/models"
    retain = env.get("HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS", "")
    retain_set = bool(str(retain).strip())
    try:
        retain_n = int(retain) if retain_set else 64000
    except ValueError:
        retain_n = 64000
        retain_set = False
    out["hindsight_retain_budget"] = (0 < retain_n <= HINDSIGHT_RETAIN_TOKENS_MAX_SANE
                                      if HINDSIGHT_RETAIN_TOKENS_MAX_SANE else True)
    if not out["hindsight_retain_budget"]:
        if not retain_set:
            notes["hindsight_retain_budget"] = (
                "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS unset; "
                "library default 64000"
            )
        else:
            notes["hindsight_retain_budget"] = (
                f"retain {retain_n} > sane {HINDSIGHT_RETAIN_TOKENS_MAX_SANE}"
            )
    return out, notes


def summarize_for_kanban(
    per_env: dict[str, dict],
    extra_failures: list[dict],
    ledger: dict | None = None,
) -> dict:
    ""
    env_summaries = []
    top_risks: list[dict] = []
    total_outdated = 0
    total_risk = 0
    total_never = 0
    total_green = 0

    parked: list[str] = []
    ledger = ledger if ledger is not None else _load_decisions()
    total_actionable = 0
    for env, info in per_env.items():
        details = info.get("details") or {}
        risk = details.get("risk") or []
        never = details.get("never") or []
        green = details.get("green_candidate") or []
        pinned = details.get("pinned") or []
        group_wait = details.get("group-wait") or []
        n_od = int(info.get("outdated_n") or 0)
        if info.get("in_use") is False:
            parked.append(env)
        actionable, _suppressed = filter_actionable_risk(env, risk, ledger)
        unused_open = unused_undecided(env, info.get("unused"), ledger)
        total_actionable += len(actionable)
        total_outdated += n_od
        total_risk += len(risk)
        total_never += len(never)
        total_green += len(green)
        env_summaries.append(
            {
                "env": env,
                "role": info.get("role"),
                "heavy": info.get("heavy"),
                "outdated_n": n_od,
                "outdated_unknown": bool(info.get("outdated_unknown")),
                "green_candidate": len(green),
                "risk": len(risk),
                "actionable_risk": len(actionable),
                "never": len(never),
                "pinned": len(pinned),
                "group_wait": len(group_wait),
                "smoke_baseline": info.get("smoke_baseline"),
                "service": info.get("service"),

                "unused_n": len(unused_open),
                "unused_sample": unused_open[:8],

                "risk_sample": [
                    f"{x['package']} {x['current']}→{x['latest']}" for x in risk[:5]
                ],
                "never_sample": [
                    f"{x['package']} {x['current']}→{x['latest']}" for x in never[:5]
                ],
            }
        )
        for x in actionable:
            top_risks.append({**x, "bucket": "risk"})
        for x in never:

            pkg = (x.get("package") or "").lower()
            if pkg in NEVER_AUTO or pkg.startswith("nvidia-") or pkg in (
                "vllm",
                "transformers",
                "triton",
            ):
                top_risks.append({**x, "bucket": "never"})


    multi = Counter(x.get("package", "").lower() for x in top_risks)
    top_risks.sort(
        key=lambda x: (-multi.get((x.get("package") or "").lower(), 0), x.get("package") or "")
    )

    seen: set[str] = set()
    unique_top: list[dict] = []
    for x in top_risks:
        k = (x.get("package") or "").lower()
        if not k or k in seen:
            continue
        seen.add(k)
        unique_top.append(
            {
                "package": x.get("package"),
                "example_env": x.get("env"),
                "current": x.get("current"),
                "latest": x.get("latest"),
                "bucket": x.get("bucket"),
                "envs_hit": multi.get(k, 1),
            }
        )
        if len(unique_top) >= KANBAN_TOP_N:
            break

    return {
        "schema": "ms_kanban_queue/v1",
        "format": "summary",
        "totals": {
            "outdated_packages_sum": total_outdated,
            "risk_rows": total_risk,
            "never_rows": total_never,
            "green_rows": total_green,
            "pinned_rows": sum(len((info.get("details") or {}).get("pinned") or []) for info in per_env.values()),
            "group_wait_rows": sum(len((info.get("details") or {}).get("group-wait") or []) for info in per_env.values()),
            "envs": len(env_summaries),
            "envs_on_disk": len(per_env),
            "envs_parked": len(parked),
            "actionable_risk": total_actionable,
        },
        "parked_envs": parked,
        "env_summaries": env_summaries,
        "top_packages": unique_top,
        "apply_failures": extra_failures[:40],
        "note": (
            "Summary only — full outdated lists live in reports/ms-trail-*.json "
            f"under envs.*.details (capped at {REPORT_DETAIL_CAP}/bucket). "
            "Manifests: manifests/*-LATEST.txt. Do not expand this into N kanban cards."
        ),
    }


def _os_card_lines(os_state: dict) -> list[str]:
    ""





    if not os_state:
        return []
    said: list[str] = []
    pend = (os_state.get("pending") or {}).get("n") or 0
    sec = (os_state.get("pending") or {}).get("security_n") or 0
    uu = os_state.get("unattended") or {}
    debt = os_state.get("restart_debt") or {}
    units = debt.get("units") or {}
    reboot = os_state.get("reboot") or {}
    cfg = os_state.get("config") or {}
    pro = os_state.get("pro") or {}
    kern = os_state.get("kernel") or {}

    if pend:
        said.append(f"- {pend} package(s) pending, {sec} from -security")
    if uu.get("ok") is False:
        said.append(f"- unattended-upgrades reported errors: {(uu.get('errors') or [''])[0][:120]}")
    elif int(uu.get("stale_days") or 0) >= 3:
        said.append(f"- unattended-upgrades has not run in {uu['stale_days']} days")
    if units:
        worst = sorted(units.items(), key=lambda kv: -(kv[1].get("n") or 0))[:3]
        said.append(
            f"- restart debt: {len(units)} unit(s) still map replaced files "
            f"({debt.get('os_units_n', 0)} os / {debt.get('venv_units_n', 0)} venv)"
        )
        for name, row in worst:
            venv = row.get("venv") or {}
            osp = row.get("os") or {}


            src = (
                f"{sum(venv.values())} from our own apply-green ({'/'.join(venv)})"
                if venv else f"{sum(osp.values())} from OS packages"
            )
            said.append(f"    - {name}: {row.get('n')} mapped, {src}")
    if reboot.get("required"):
        said.append(
            f"- reboot required ({reboot.get('waiting_days', 0)}d) — human decision, "
            "never taken by this job"
        )
    if kern.get("drift"):
        said.append(
            f"- kernel drift: running {kern.get('running')}, newest installed "
            f"{kern.get('newest_abi')}"
        )
    if cfg.get("nvidia_ok") is False:
        said.append(f"- NVIDIA stack assert RED: {cfg.get('nvidia_why')}")
    if cfg.get("brave_autoupgrade_armed"):
        said.append(_T("os.browser_autoupgrade_line"))
    if pro.get("esm_origins_armed_but_inert"):
        said.append(
            "- ESM origins are armed in unattended-upgrades but the machine is "
            "not attached, so they match nothing"
        )
    if not said:
        return []
    return ["", "**OS updates**"] + said


TREND_PATH = Path(os.environ.get("MS_TREND", str(OUT_ROOT / "trend.jsonl")))
TREND_KEEP = int(os.environ.get("MS_TREND_KEEP", "180"))


def _ledger_census(ledger: dict | None = None) -> dict:
    ""

    if not ledger:
        ledger = _ledger_or_none()
        if ledger is None:
            return {"unreadable": 1}
    out: dict[str, int] = {"invalid": 0,
                           "stray_keys": ledger_validation(ledger)["stray_keys"]}
    for r in ledger.get("decisions") or []:
        if not isinstance(r, dict):
            out["invalid"] = out.get("invalid", 0) + 1
            continue
        dec = effective_decision(r)



        if dec not in DECISION_STATES:
            dec = "invalid"
        elif not str(r.get("env") or "").strip() or not str(r.get("package") or "").strip():
            dec = "invalid"
        out[dec] = out.get(dec, 0) + 1
    return out


def append_trend(summary: dict, applied: dict, os_state: dict, day: str,
                 *, write: bool = True) -> dict:
    ""







    t = summary.get("totals") or {}
    census = _ledger_census()
    debt = (os_state or {}).get("restart_debt") or {}
    row = {
        "day": day,
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "applied_n": sum(int((a or {}).get("changed_n") or 0) for a in (applied or {}).values()),
        "failed_n": sum(len((a or {}).get("failed") or []) for a in (applied or {}).values()),
        "green_rows": t.get("green_rows"),
        "risk_rows": t.get("risk_rows"),
        "pinned_rows": t.get("pinned_rows"),
        "group_wait_rows": t.get("group_wait_rows"),
        "envs": t.get("envs"),
        "ledger": census,
        "restart_debt_units": debt.get("actionable_n"),
        "restart_debt_venv": debt.get("venv_units_n"),
        "os_pending": ((os_state or {}).get("pending") or {}).get("n"),


        "apply_failures": sorted({
            f"{f.get('env')}:{f.get('reason')}"
            for f in (summary.get("apply_failures") or []) if isinstance(f, dict)
        }),
    }
    if not write:
        return row
    try:
        TREND_PATH.parent.mkdir(parents=True, exist_ok=True)
        prev = _read_trend()
        prev = [r for r in prev if r.get("day") != day][-TREND_KEEP:]
        with TREND_PATH.open("w") as fh:
            for r in prev + [row]:
                fh.write(json.dumps(r) + "\n")
    except Exception as e:
        log(f"trend append failed {e!r}")
    return row


REPEAT_FAILURE_NIGHTS = int(os.environ.get("MS_REPEAT_FAILURE_NIGHTS", "2"))


def repeat_failures(hist: list[dict], row: dict, *, window: int = 7,
                    min_nights: int | None = None) -> list[dict]:
    ""


    min_nights = REPEAT_FAILURE_NIGHTS if min_nights is None else min_nights
    prev = [h for h in hist if h.get("day") != row.get("day")][-(window - 1):]
    counts: dict[str, list[str]] = {}
    for h in prev + [row]:
        for key in h.get("apply_failures") or []:
            counts.setdefault(str(key), []).append(str(h.get("day")))
    out = []
    for key, days in counts.items():
        if len(days) >= min_nights and key in (row.get("apply_failures") or []):
            env, _, reason = key.partition(":")
            out.append({"env": env, "reason": reason, "nights": sorted(set(days))})
    return sorted(out, key=lambda x: -len(x["nights"]))


MS_DIAGNOSE_MAX = int(os.environ.get("MS_DIAGNOSE_MAX", "5"))


def file_diagnose_cards(report: dict, day: str, listing: str,
                        cards: list[dict] | None = None) -> int:
    ""


    row = report.get("trend") or {}
    hist = _read_trend()
    n = 0
    for f in repeat_failures(hist, row):
        if MS_DIAGNOSE_MAX > 0 and n >= MS_DIAGNOSE_MAX:
            log(f"diagnose cards: cap {MS_DIAGNOSE_MAX} reached — the rest wait a night")
            break
        env, reason = f["env"], f["reason"]
        needle = f"M&S diagnose: {env} — {reason}"
        if _kanban_task_ids(listing, needle, cards=cards,
                            key_prefix=f"ms-diagnose-{env}-{reason}-", open_only=True):
            continue
        a = (report.get("applied") or {}).get(env) or {}
        week = dt.date.fromisoformat(day[:10]).isocalendar()
        body = "\n".join([
            f"`{env}` apply-green failed with `{reason}` on {len(f['nights'])} of the last 7 "
            f"nights ({', '.join(f['nights'])}). Each night rolled back and reported itself as a "
            "one-off. It is not.",
            "",
            f"- last err: {str(a.get('err') or '')[:300]}",
            f"- canary: {str(a.get('canary') or '')[:300]}",
            f"- packages in the rolled-back batch: {', '.join(str(x.get('package') if isinstance(x, dict) else x) for x in (a.get('failed') or [])[:12])}",
            f"- logs: {OUT_ROOT / 'logs' / ('nightly-' + day.replace('-', '') + '.log')}, "
            f"{OUT_ROOT / 'logs' / 'canary-05b.log'}, report {REPORT_DIR / f'ms-trail-{day}.json'}",
            "",
            "Do: read the log for the first error line (not the traceback tail). Name the cause. "
            "If it is the harness (VRAM floor, port, timeout, wrong interpreter) fix the harness in "
            "scripts/ms-trail-run.py with a test. If it is a package, record the decision with "
            "`--record-decision`. Do not retry the apply by hand; the night retries. "
            "kanban_complete with: cause, fix or decision, evidence path.",
        ])
        ok = _kanban_create(
            title=f"{needle} {len(f['nights'])}x in 7 nights",
            body=body,
            assignee=_assignee("diagnose"),
            idemp=f"ms-diagnose-{env}-{reason}-{week[0]}w{week[1]:02d}",
        )
        n += 1 if ok else 0
    return n


def _read_trend() -> list[dict]:
    if not TREND_PATH.is_file():
        return []
    out = []
    for line in TREND_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _installed_from_manifest(env: str, pkg: str) -> str | None:
    ""
    path = OUT_ROOT / "manifests" / f"{env}-LATEST.txt"
    if not path.is_file():
        return None
    want = canon(pkg)
    for ln in path.read_text(errors="replace").splitlines():
        if "==" not in ln:
            continue
        name, _, ver = ln.strip().partition("==")
        if canon(name) == want:
            return ver.strip()
    return None


def refusal_is_stale(row: dict) -> bool:
    ""


    inst = _installed_from_manifest(str(row.get("env") or ""), str(row.get("package") or ""))
    cur = str(row.get("current") or "").strip()
    return bool(inst and cur and inst != cur)


def refusal_streaks(ledger: dict | None = None, *, min_attempts: int = 2) -> list[dict]:
    ""
    ledger = ledger or _load_decisions()
    out = []
    for r in ledger.get("decisions") or []:
        if not isinstance(r, dict) or str(r.get("decision")).lower() != "refused":
            continue
        n = int(r.get("attempts") or 0)
        if n >= min_attempts and not refusal_is_stale(r):
            out.append({
                "env": r.get("env"), "package": r.get("package"),
                "attempts": n, "target": r.get("last_target"),
                "outcome": r.get("outcome"),
                "err": " ".join(str(r.get("last_error") or "").split())[:120],
            })
    return sorted(out, key=lambda x: -x["attempts"])


_FWD_DEP_RE = re.compile(r"The package `([^`]+)` requires `([^`]+)`, but it's not installed")
_PEER_CAP_RE = re.compile(r"The package `([^`]+)` requires `([^`]+)`, but `([^`]+)` is installed")


def _req_name(req: str) -> str:
    return canon(re.split(r"[<>=!~;\[ ]", req.strip(), 1)[0])


def refusal_resolution(err: str, outcome: str = "", pkg: str = "") -> str:
    ""


    e = " ".join((err or "").split())
    if not e:
        return ("no installer error on file: this was a canary/smoke rollback of the whole "
                "batch, not this package refusing — the row is noise, drop it")
    m = _FWD_DEP_RE.search(e)
    if m:
        return (f"forward dep: `{_req_name(m.group(2))}` is new to this env and --no-deps never "
                f"installs it — the night installs it beside the bump (MS_FORWARD_DEPS=1)")
    el = e.lower()
    if "failed to build" in el or "build backend" in el:
        note = upstream_note(pkg) if pkg else ""
        if note:
            return f"name collision: {note} — not the project this env runs; wontfix"
        return ("build-fail: an sdist-only release — check that the PyPI project under this "
                "name is the one the service runs before calling the service behind; "
                "wontfix if it is a name collision")
    m = _PEER_CAP_RE.search(e)
    if m:
        return (f"peer cap: the target caps `{_req_name(m.group(2))}` below what this env runs "
                f"({m.group(3)}); lands when a target lifts the cap, hold otherwise")
    return "installer refused: read last_error, then wontfix or retarget"


def _new_holds_since(day: str, ledger: dict | None = None) -> tuple[list[dict], list[dict]]:
    ""
    ledger = ledger or _load_decisions()
    live: list[dict] = []
    orphan: list[dict] = []
    for r in ledger.get("decisions") or []:
        if not isinstance(r, dict) or effective_decision(r) not in ("hold", "review_due"):
            continue
        if day and str(r.get("ts") or "")[:10] < day[:10]:
            continue
        env = str(r.get("env") or "")
        if env in VENVS or (ENVS_ROOT / env).is_dir():
            live.append(r)
        elif env.lower().startswith(tuple(k.lower() for k in VENVS)) or _ENVS_PREFIX in str(r.get("reason") or ""):
            orphan.append(r)
        else:
            live.append(r)
    return live, orphan


def _trend_lines(row: dict, os_state: dict) -> list[str]:
    ""
    hist = _read_trend()
    lines: list[str] = ["", "**Trend** (this is what the card could never say before)"]
    led = _ledger_or_none()
    if led is None:
        lines.append("- decisions ledger UNREADABLE — holds, refusals and review-due not read; "
                     "no env applied tonight")
        led = {"schema": "ms_decisions/v1", "decisions": []}
    prev = [h for h in hist if h.get("day") != row.get("day")]
    week = prev[-7:]
    if week:
        first = week[0].get("ledger") or {}
        now_l = row.get("ledger") or {}
        dh = (now_l.get("hold") or 0) - (first.get("hold") or 0)
        da = (now_l.get("applied") or 0) - (first.get("applied") or 0)





        landed = sum(int(h.get("applied_n") or 0) for h in week) + int(row.get("applied_n") or 0)
        live_new, orphan_new = _new_holds_since(str(week[0].get("day") or ""), led)
        lines.append(
            f"- over the last {len(week)} run(s): holds {dh:+d}, applied rows {da:+d}, "
            f"upgrades landed by the nights {landed}"
        )
        if orphan_new:
            envs = sorted({str(r.get("env")) for r in orphan_new})
            lines.append(
                f"    - {len(orphan_new)} of the new holds sit on env(s) no longer on disk "
                f"({', '.join(envs[:3])}) — ledger landfill, not this box drifting"
            )

        if (dh - len(orphan_new)) > landed:
            lines.append(
                "    - holds on live envs are outgrowing what the nights land; "
                "that direction is the opposite of keeping this box current"
            )
    else:
        lines.append("- first recorded run — no history to compare against yet")
    streaks = refusal_streaks(led)
    if streaks:
        lines.append(f"- {len(streaks)} package(s) refused more than once at the same version:")
        for x in streaks[:5]:
            lines.append(
                f"    - {x['env']}/{x['package']} -> {x['target']}: {x['attempts']}x "
                f"({x['outcome']}) {x['err'][:70]}"
            )
            lines.append(
                f"        resolve: {refusal_resolution(x['err'], str(x['outcome'] or ''), str(x['package'] or ''))}"
            )
    due = [
        r for r in (led.get("decisions") or [])
        if isinstance(r, dict) and effective_decision(r) == "review_due"
    ]
    if due:
        lines.append(
            f"- {len(due)} hold(s) have aged out and are due a second look "
            "(still blocked from auto-apply)"
        )
    debt = (os_state or {}).get("restart_debt") or {}
    venv_units = {
        n: sum((u.get("venv") or {}).values())
        for n, u in (debt.get("units") or {}).items() if u.get("venv")
    }
    if venv_units:
        worst = sorted(venv_units.items(), key=lambda kv: -kv[1])[0]
        lines.append(
            f"- {worst[0]} is still running {worst[1]} file(s) this trail "
            "replaced itself — applied is not the same as live"
        )
    return lines if len(lines) > 2 else []


def _unused_card_lines(env_summaries: list[dict]) -> list[str]:
    ""


    rows = [e for e in env_summaries if int(e.get("unused_n") or 0) > 0]
    if not rows:
        return []
    out = ["**Unused** (installed, required by nothing, never imported; wontfix = keep, or uninstall):"]
    svc_rows = sorted(e["env"] for e in rows if e["env"] in SERVICE_ENVS)
    if svc_rows:
        out.append("Live service envs (" + ", ".join(svc_rows) + "): an uninstall is "
                   "done only when `ms-trail-run.py --service-check <env>` is GREEN (real import + "
                   "restart + health). RED → `--restore-plan <env>` (manifests/<env>-LAST-GOOD.txt; "
                   "never LATEST, the survey rewrites it). "
                   "Record an uninstall as the uninstall, never as `wontfix` (that word means keep).")
    for e in rows:
        names = []
        for n in (e.get("unused_sample") or [])[:8]:
            note = upstream_note(n)
            names.append(f"{n} [{note}]" if note else n)
        more = int(e.get("unused_n") or 0) - len(names)
        out.append(
            f"- {e['env']}: {int(e.get('unused_n') or 0)} — " + ", ".join(names)
            + (f" (+{more} in the report json)" if more > 0 else "")
        )
    return out


def _card_body(summary: dict, applied: dict, day: str) -> str:
    ""


    t = summary.get("totals") or {}
    lines = [
        f"M&S {day} — outdated={t.get('outdated_packages_sum')} "
        f"green={t.get('green_rows')} risk={t.get('risk_rows')} "
        f"never(pinned)={t.get('never_rows')} live_envs={t.get('envs')} "
        f"parked={t.get('envs_parked', 0)} "
        f"actionable_risk={t.get('actionable_risk', 0)} "
        f"pinned={t.get('pinned_rows', 0)} group-wait={t.get('group_wait_rows', 0)}",
        "never = deliberate pins (NVIDIA cu13 stack, torch family) — NOT actionable.",
        "pinned = installed already satisfies an immovable pinner (e.g. vLLM exact); not green.",
        "Live = a running unit or process has this venv open. Parked leftovers "
        "stay on disk and are surveyed, but they are not research work. "
        "Held ledger rows are not 'review by hand'.",
        "",
    ]
    led_c = (summary.get("trend") or {}).get("ledger") or {}
    if int(led_c.get("invalid") or 0) or int(led_c.get("stray_keys") or 0):
        lines.insert(1, f"ledger has {int(led_c.get('invalid') or 0)} invalid rows / "
                        f"{int(led_c.get('stray_keys') or 0)} stray keys — write through --record-decision")
    if summary.get("ledger_unreadable"):
        lines.insert(1, f"**LEDGER UNREADABLE — no env applied tonight.** {summary['ledger_unreadable']}")
    for e in summary.get("env_summaries") or []:
        sv = e.get("service") or {}
        if sv and sv.get("verdict") not in ("ok", "parked"):
            say = {
                "down": "DOWN — health fails",
                "broken": "real import RED — the next restart crashes",
                "report-down": "a report-only unit is down (not restarted by M&S)",
                "restart-owed": "env changed after the process started — restart and check health",
            }.get(sv.get("verdict"), sv.get("verdict"))
            lines.insert(1, f"**LIVE SERVICE {e['env']} ({sv.get('unit')}): {say}**"
                         + (f" · {sv.get('smoke_err')}" if sv.get("smoke_err") else "")
                         + (f" · down: {', '.join(sv.get('down'))}" if sv.get("down") else "")
                         + (f" · restart owed: {', '.join(sv.get('restart_owed'))}"
                            if sv.get("restart_owed") else "")
                         + f" · fix, then `ms-trail-run.py --service-check {e['env']}`"
                         + f" · restore: `--restore-plan {e['env']}` (LAST-GOOD)")
        owed = ((applied or {}).get(e["env"]) or {}).get("restart_owed") or []
        if owed:
            lines.insert(1, f"**RESTART OWED {e['env']}: {', '.join(owed)}** — tonight changed the env; "
                         + _T("card.restart_owed_tail"))
    considered = bool(applied)
    for e in summary.get("env_summaries") or []:
        a = applied.get(e["env"]) if applied else None
        ap = applied_label(
            a, considered_apply=considered, green_n=int(e.get("green_candidate") or 0)
        )
        lines.append(
            f"- {e['env']}{' [HEAVY]' if e.get('heavy') else ''}: "
            f"outdated={'UNKNOWN (check failed — not clean)' if e.get('outdated_unknown') else e['outdated_n']} "
            f"green={e['green_candidate']} "
            f"risk={e['risk']} never={e['never']} "
            f"pinned={e.get('pinned', 0)} group-wait={e.get('group_wait', 0)} "
            f"smoke={'OK' if e.get('smoke_baseline') else 'RED'} "
            f"unused={e.get('unused_n', 0)} · {ap}"
        )
    unused_lines = _unused_card_lines(summary.get("env_summaries") or [])
    if unused_lines:
        lines += [""] + unused_lines
    parked = summary.get("parked_envs") or []
    if parked:
        lines += [
            "",
            "No process at report time (occupancy, not a skip): "
            + ", ".join(parked),
        ]
    lines += _trend_lines(summary.get("trend") or {}, summary.get("os_update") or {})
    lines += _os_card_lines(summary.get("os_update") or {})
    top = [p for p in (summary.get("top_packages") or []) if p.get("bucket") == "risk"]
    if top:
        lines += ["", "Top *actionable* risk packages (holds already decided — omitted):"]
        for p in top[:10]:
            lines.append(
                f"- {p.get('package')} {p.get('current')}→{p.get('latest')} "
                f"(envs_hit={p.get('envs_hit')})"
            )
    elif t.get("risk_rows") or t.get("pinned_rows") or t.get("group_wait_rows") or t.get("actionable_risk"):
        openish = []
        if t.get("group_wait_rows"):
            openish.append("group-wait (Friday landable set)")
        if t.get("pinned_rows"):
            openish.append("pinned (immovable pinner; not research)")
        if t.get("actionable_risk"):
            openish.append("hold-stale or undecided risk")
        lines += [
            "",
            "Remaining outdated: "
            + ("; ".join(openish) if openish else "holds/pins")
            + f" — see `{DECISIONS_PATH.name}`. Do not re-research pinned rows.",
        ]
    hermes = summary.get("hermes") or {}
    if hermes:
        doc = hermes.get("doctor") or {}
        sec = hermes.get("security") or {}
        lines += [
            "",
            "## Hermes doctor / security",
            doctor_manifest_line(doc),
            (f"- security: UNKNOWN — scan did not complete ({str(sec.get('error') or '')[:120]}), not clean"
             if sec.get("measured") is False else
             f"- security: {sec.get('finding_count')} findings {sec.get('by_severity') or {}}"),
            hermes_tree_line(hermes.get("tree") or {}),
        ]
    lines += [
        "",
        f"Full data: {REPORT_DIR}/ms-trail-{day}.json · "
        f"queue: {KANBAN_DIR}/ms-deps-{day}.json · "
        f"manifests: {MANIFEST_DIR}/*-LATEST.txt",
        "Do not expand into per-package cards.",
    ]
    return "\n".join(lines)[:6000]


NO_DECOMPOSE_LINE = (
    "Summary only — not for decomposition. Env changes land only through the "
    "nightly apply or an ms-land card filed by the trail."
)


NO_DECOMPOSE_WORK_LINE = (
    "Not for decomposition — one worker, one card. Env changes land only "
    "through the nightly apply or an ms-land card filed by the trail."
)


def write_kanban_stub(summary: dict, day: str, applied: dict | None = None) -> Path | None:
    ""
    if not summary or not summary.get("env_summaries"):
        return None
    KANBAN_DIR.mkdir(parents=True, exist_ok=True)
    path = KANBAN_DIR / f"ms-deps-{day}.json"
    payload = {
        **summary,
        "ts": time.time(),
        "ts_local": dt.datetime.now().isoformat(timespec="seconds"),
        "assignee_hint": _assignee("summary"),
        "priority": "normal",
        "title": f"M&S dep review {day} — summary (not per-pkg flood)",
        "body": (
            "Auto-filed by ms-trail-run (summary format). "
            f"totals={summary.get('totals')}. "
+ _T("card.summary.tail")
        ),
    }
    path.write_text(json.dumps(payload, indent=2))


    hermes_bin = os.environ.get(
        "HERMES_BIN", os.path.expanduser("~/.local/bin/hermes"))

    try:
        r0 = subprocess.run([*_kanban_argv(hermes_bin), "kanban", "list"],
                            capture_output=True, text=True, timeout=30)
        already = f"M&S dep review {day} " in (r0.stdout or "")
    except Exception:
        already = False
    if already:
        log("kanban summary card already open for today — not re-filing")
        return path
    tot = summary.get("totals") or {}
    hermes_rep = summary.get("hermes") or {}
    sec = hermes_rep.get("security") or {}
    n_high = int((sec.get("by_severity") or {}).get("HIGH") or 0) + int(
        (sec.get("by_severity") or {}).get("CRITICAL") or 0
    )
    n_doc = int((hermes_rep.get("doctor") or {}).get("actionable_n") or 0)
    quiet = (
        int(tot.get("actionable_risk") or 0) == 0
        and not (summary.get("apply_failures") or [])
        and n_high == 0
        and n_doc == 0

        and not summary.get("ledger_unreadable")
        and (hermes_rep.get("doctor") or {}).get("measured") is not False
        and sec.get("measured") is not False
        and not any(e.get("outdated_unknown") for e in summary.get("env_summaries") or [])
    )
    if quiet:
        log("kanban summary: quiet night (no actionable risk, no apply fail, "
            "no Hermes HIGH) — queue json only, no card")
        return path
    try:
        title = payload["title"]
        desc = NO_DECOMPOSE_LINE + "\n\n" + _card_body(summary, applied or {}, day)
        r = subprocess.run(
            [
                *_kanban_argv(hermes_bin),
                "kanban",
                "create",
                title,
                "--body",
                desc,
                "--assignee",
                _assignee("summary"),


                "--idempotency-key",
                f"ms-dep-summary:{day}",
                "--workspace",
                f"dir:{DELIVERABLES_DIR}",
                "--created-by",
                "ms-trail",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            log(f"kanban card NOT filed rc={r.returncode}: "
                f"{(r.stderr or r.stdout or '')[:200]}")
        else:
            log(f"kanban card filed: {(r.stdout or '').strip()[:120]}")
    except Exception as e:
        log(f"kanban card NOT filed: {e!r}")
    log(
        f"kanban queue → {path} "
        f"(envs={len(summary.get('env_summaries') or [])} "
        f"top={len(summary.get('top_packages') or [])} — not per-pkg)"
    )
    return path


LIVE_MANIFEST = Path(_sp(_S.get("manifest.live_path")) or "/nonexistent/ms-live-manifest.md")
LIVE_MANIFEST_ON = bool(_S.get("manifest.live_path"))
HERMES_REPORT_DIR = OUT_ROOT / "hermes"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


MS_KANBAN_PROFILE = str(_S.get("kanban.profile", "", env="MS_KANBAN_PROFILE") or "")


def _kanban_argv(bin_path: str | None = None) -> list[str]:
    ""


    prof = ["-p", MS_KANBAN_PROFILE] if MS_KANBAN_PROFILE else []
    return [str(bin_path or _hermes_bin()), *prof]


def _hermes_bin() -> str:
    return os.environ.get(
        "HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes")
    )





_API_KEY_DOCTOR_RE = re.compile(
    r"hermes setup|configure API keys|API keys",
    re.I,
)
UPSTREAM_APIS_FILE = Path(
    os.environ.get(
        "MS_DOCTOR_UPSTREAM_APIS_FILE",
        _sp(_S.get("doctor.upstream_apis_file")) or str(OUT_ROOT / "doctor-upstream-apis"),
    )
)


def user_wants_upstream_apis() -> bool:
    v = os.environ.get("MS_DOCTOR_UPSTREAM_APIS", "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    try:
        first = UPSTREAM_APIS_FILE.read_text(encoding="utf-8").splitlines()[:1]
    except OSError:
        return False
    return bool(first) and first[0].strip().lower() in ("on", "1", "true", "yes")


def is_unused_api_doctor_issue(text: str) -> bool:
    return bool(_API_KEY_DOCTOR_RE.search(text or ""))


def doctor_actionable_issues(issues: list | None) -> list[str]:
    items = [str(i).strip() for i in (issues or []) if str(i).strip()]
    if user_wants_upstream_apis():
        return items
    return [i for i in items if not is_unused_api_doctor_issue(i)]


def doctor_manifest_line(doc: dict) -> str:
    ""
    if doc.get("measured") is False:
        return (f"- **doctor:** UNKNOWN — did not complete "
                f"({str(doc.get('error') or '')[:120]}); not clean")
    ok = bool(doc.get("ok"))
    n = doc.get("issues_n")
    act = doc.get("actionable_n")
    skipped = 0
    if n is not None and act is not None:
        skipped = max(0, int(n) - int(act))
    tag = "GREEN" if ok else "ISSUES"
    extra = ""
    if ok and skipped and not user_wants_upstream_apis():
        extra = (
            f" · M&S ignores {skipped} unused-API doctor issue(s) "
            "until MS_DOCTOR_UPSTREAM_APIS=1 (hermes doctor unchanged)"
        )
    shown = list(doc.get("actionable") or [])
    if not ok:
        shown = shown or list(doc.get("issues") or [])

    listed = (" — " + "; ".join(str(s) for s in shown)) if shown else ""
    return (
        f"- **doctor:** {tag} · actionable {act if act is not None else n} "
        f"/ raw {n} issue(s){extra}{listed}"
    )


_CAUSE_RE = re.compile(r"\b\w*(?:Error|Exception|Failed)\b:\s*\S.*")


def apply_cause(err: str) -> str:
    ""


    err = str(err or "")
    for part in err.split(" | "):
        m = _CAUSE_RE.search(part.strip())
        if m:
            return m.group(0).strip()[:200]
    return err.split(" — cause:")[0].strip()[:200]


def apply_tally(applied: dict) -> dict:
    ""






    envs: dict[str, dict] = {}
    for name, a in (applied or {}).items():
        a = a or {}
        landed = int(a.get("changed_n") or 0)
        outs = [o for o in (a.get("outcomes") or []) if isinstance(o, dict)]
        rolled = [o.get("package") for o in outs if o.get("outcome") == "rolled-back"]
        failed = [str(x.get("package") if isinstance(x, dict) else x)
                  for x in (a.get("failed") or [])]
        if not rolled and not a.get("ok", True):
            rolled = failed
        other = [p for p in failed if p not in rolled]
        envs[name] = {
            "landed": landed,
            "rolled": [str(p) for p in rolled],
            "failed": other,
            "attempted": landed + len(rolled) + len(other),
            "cause": apply_cause(a.get("err") or next(
                (o.get("err") for o in outs if o.get("err")), "")) if (rolled or other) else "",
        }
    return {
        "envs": envs,
        "landed": sum(e["landed"] for e in envs.values()),
        "attempted": sum(e["attempted"] for e in envs.values()),
        "rolled": sum(len(e["rolled"]) for e in envs.values()),
    }


def apply_env_line(t: dict) -> str | None:
    ""
    if not t or not t.get("attempted"):
        return None
    s = f"- apply: landed {t['landed']} of {t['attempted']} attempted"
    if t["rolled"]:
        s += (f" · **rolled back {len(t['rolled'])}** ({', '.join(t['rolled'])})"
              + (f" — cause: {t['cause']}" if t.get("cause") else ""))
    if t["failed"]:
        s += f" · failed {len(t['failed'])} ({', '.join(t['failed'])})"
    return s


def hermes_tree_line(tree: dict) -> str:
    ""

    if not tree or tree.get("ok") is None:
        return f"- **hermes tree:** not checked ({(tree or {}).get('note', 'no verify')})"
    if tree.get("ok"):
        return f"- **hermes tree:** GREEN — {tree.get('last', '')}"
    return (f"- **hermes tree:** RED — {tree.get('last', '')} → `python3 scripts/hermes-tree.py status`; "
            "capture what is uncovered, re-capture what is stale, before any `hermes update`")


def run_hermes_tree_verify() -> dict:
    ""



    if not _S.get("patch_queue.enabled"):
        return {"ok": None, "note": "patch queue check off (settings patch_queue.enabled)"}
    tool = Path(_sp(_S.get("patch_queue.script")) or APP_ROOT.parent / "crew" / "scripts" / "hermes-tree.py")
    if not tool.is_file():
        return {"ok": None, "note": f"hermes-tree.py missing ({tool}; patch_queue.script names another)"}
    rc, out, err = run([sys.executable, str(tool), "verify"], timeout=120)
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    return {"ok": rc == 0, "rc": rc, "last": (lines[-1] if lines else (err or "")[:160])[:200],
            "patches": [ln.strip() for ln in lines if ln.strip().startswith("[")][:12]}


def run_hermes_doctor() -> dict:
    ""
    bin_ = _hermes_bin()
    if not Path(bin_).is_file():
        return {
            "ok": False,
            "measured": False,
            "error": f"missing {bin_}",
            "issues_n": None,
            "issues": [],
            "actionable_n": None,
            "actionable": [],
        }
    rc, out, err = run(
        [bin_, "doctor"],
        timeout=180,
        extra_env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    text = _ANSI_RE.sub("", (out or "") + ("\n" + err if err else ""))
    HERMES_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().isoformat()
    path = HERMES_REPORT_DIR / f"doctor-{day}.txt"
    latest = HERMES_REPORT_DIR / "doctor-LATEST.txt"
    path.write_text(text)
    latest.write_text(text)
    issues: list[str] = []
    grab = False
    for ln in text.splitlines():
        if re.search(r"Found \d+ issue", ln, re.I):
            grab = True
            continue
        if grab:
            s = ln.strip()
            if re.match(r"^\d+\.\s+", s):
                issues.append(re.sub(r"^\d+\.\s+", "", s))
            elif s.startswith("Tip:"):
                grab = False
    m = re.search(r"Found (\d+) issue", text, re.I)
    n = int(m.group(1)) if m else len(issues)
    actionable = doctor_actionable_issues(issues)


    measured = rc != 124 and bool(m or re.search(r"All checks passed", text, re.I))
    if not measured:
        log(f"hermes doctor: UNKNOWN rc={rc} — no verdict line; not clean")
        return {
            "ok": False,
            "measured": False,
            "error": (err or "no verdict line")[:200],
            "exit": rc,
            "issues_n": None,
            "issues": issues[:12],
            "actionable_n": None,
            "actionable": [],
            "path": str(path),
        }
    log(
        f"hermes doctor: rc={rc} issues={n} actionable={len(actionable)} "
        f"upstream_apis={int(user_wants_upstream_apis())}"
    )
    return {
        "ok": len(actionable) == 0,
        "measured": True,
        "exit": rc,
        "issues_n": n,
        "issues": issues[:12],
        "actionable_n": len(actionable),
        "actionable": actionable[:12],
        "path": str(path),
    }


def _hermes_live_version(tree: str | Path | None = None) -> str:
    ""











    t = Path(tree) if tree else KH.hermes_tree()
    if not (t / ".git").exists():
        return ""
    try:
        rc, out, _err = run(["git", "-C", str(t), "describe", "--tags"], timeout=30)
    except Exception:
        return ""
    if rc != 0:
        return ""
    return str((out or "").strip()).lstrip("v")


def hermes_real_version_components() -> list:
    ""







    v = _hermes_live_version()
    if not v:
        return []
    return [{"package": "hermes-agent", "version": v, "ecosystem": "PyPI",
             "source": "live-tree"}]


def _advisory_fixed(fixed_versions: list, live_version: str) -> str | None:
    ""







    live = _vtuple(live_version)
    if not live:
        return None
    ok = [str(v) for v in (fixed_versions or []) if _vtuple(str(v)) and live >= _vtuple(str(v))]
    return min(ok) if ok else None


def _advisory_applies(finding: dict, live_version: str, raw_version: str | None = None) -> bool:
    ""










    if not isinstance(finding, dict):
        return True
    if str(finding.get("package") or "").lower() != "hermes-agent":
        return True
    if not raw_version:

        return True






    return _advisory_fixed(finding.get("fixed_versions") or [], live_version) is None


def security_counts(findings: list) -> tuple[int, int, int, dict, list]:
    ""









    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    raw_n = len(findings)
    for f in findings:
        if not isinstance(f, dict):
            continue
        key = (str(f.get("package") or ""), str(f.get("vuln_id") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    by: dict[str, int] = {}
    high_ids: list[str] = []
    for f in deduped:
        sev = str(f.get("severity") or "UNKNOWN").upper()
        by[sev] = by.get(sev, 0) + 1
        if sev in ("HIGH", "CRITICAL"):
            vid = str(f.get("vuln_id") or "")
            if vid:
                high_ids.append(vid)
    n_unique = len(deduped)
    return raw_n, n_unique, raw_n - n_unique, by, sorted(set(high_ids))


def run_hermes_security() -> dict:
    ""
    bin_ = _hermes_bin()
    if not Path(bin_).is_file():
        return {"ok": False, "measured": False, "error": f"missing {bin_}",
                "finding_count": None, "findings": []}
    rc, out, err = run(
        [bin_, "security", "audit", "--json"],
        timeout=300,
        extra_env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    HERMES_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().isoformat()
    raw_path = HERMES_REPORT_DIR / f"security-{day}.json"
    latest = HERMES_REPORT_DIR / "security-LATEST.json"
    payload: dict
    try:
        payload = json.loads(out or "{}")
        if not isinstance(payload, dict):
            payload = {"raw": out, "parse": "not-object"}
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "error": (err or out or "invalid json")[:400],
            "finding_count": None,
            "findings": [],
        }
    payload.setdefault("exit", rc)


    payload["measured"] = bool(
        rc != 124 and ("finding_count" in payload or isinstance(payload.get("findings"), list))
        and payload.get("finding_count", 0) is not None
    )
    if not payload["measured"]:
        payload.setdefault("error", (err or "no scan result")[:400])
        log(f"hermes security: UNKNOWN rc={rc} — scan did not complete; not clean")





    live_version = _hermes_live_version()
    raw_findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    raw_version = ""
    for _f in raw_findings:
        if isinstance(_f, dict) and str(_f.get("package") or "").lower() == "hermes-agent" and _f.get("version"):
            raw_version = str(_f["version"])
            break
    applied_findings = [f for f in raw_findings if _advisory_applies(f, live_version, raw_version)]
    dropped = [f for f in raw_findings if not _advisory_applies(f, live_version, raw_version)]
    payload["live_version"] = live_version
    payload["scanned_version"] = raw_version
    payload["findings_applied_count"] = len(applied_findings)
    payload["findings"] = applied_findings
    if dropped:
        payload["findings_superseded"] = dropped
        payload["findings_superseded_note"] = (
            f"{len(dropped)} hermes-agent finding(s) fixed in a version the live tree meets "
            f"(scanned {raw_version}, tag {live_version or 'unknown'}); the 0.0.0 dist of the "
            f"tree's origin/main+patch-queue install carries those fixes — kept above, not deleted."
        )
    if not live_version:
        payload["live_version_note"] = (
            "the live tree's version tag could not be read; hermes-agent findings "
            "were kept unfiltered (0.0.0 reading)."
        )






    raw_n, n_unique, dup_n, by, high_ids = security_counts(applied_findings)
    payload["by_severity"] = by
    payload["high_ids"] = sorted(set(high_ids))
    payload["finding_count_deduped"] = n_unique
    payload["finding_count_duplicates"] = dup_n




    if payload.get("finding_count") is None and n_unique:
        payload["finding_count"] = n_unique
        payload["finding_count_note"] = "scan reported no count; recount from deduped findings"
    elif payload.get("finding_count") not in (None, n_unique):
        payload["finding_count_note"] = (
            f"scan count {payload['finding_count']} ≠ deduped {n_unique} "
            f"({dup_n} duplicate line(s) dropped)"
        )
    raw_path.write_text(json.dumps(payload, indent=2))
    latest.write_text(json.dumps(payload, indent=2))
    log(
        f"hermes security: rc={rc} scanned={payload.get('total_components_scanned')} "
        f"findings={payload.get('finding_count')} (deduped {n_unique}, "
        f"{dup_n} duplicate line(s)) by={by}"
    )
    return payload


def hermes_security_is_new(current: dict) -> bool:
    ""
    ids = set(current.get("high_ids") or [])
    prev_path = HERMES_REPORT_DIR / "security-PREV.json"
    latest = HERMES_REPORT_DIR / "security-LATEST.json"

    if not prev_path.is_file():
        return bool(ids)
    try:
        prev = json.loads(prev_path.read_text())
        old = set(prev.get("high_ids") or [])
    except Exception:
        return bool(ids)
    return ids != old


def rotate_hermes_security_prev() -> None:
    latest = HERMES_REPORT_DIR / "security-LATEST.json"
    prev = HERMES_REPORT_DIR / "security-PREV.json"
    if latest.is_file():
        prev.write_text(latest.read_text())


KANBAN_CLOSED = ("done", "completed", "archived")


def _kanban_board_db() -> Path | None:
    ""

    pinned = os.environ.get("MS_KANBAN_DB", "").strip()
    if pinned:
        return Path(pinned)
    try:
        r = subprocess.run([*_kanban_argv(), "kanban", "boards", "list", "--json"],
                           capture_output=True, text=True, timeout=30)
        boards = json.loads(r.stdout or "[]")
        for b in boards if isinstance(boards, list) else []:
            if isinstance(b, dict) and b.get("is_current") and b.get("db_path"):
                return Path(str(b["db_path"]))
    except Exception as e:
        log(f"kanban boards list failed {e!r} — title matching only")
    return None


def kanban_board_cards(listing: str | None = None, *, db: Path | None = None) -> list[dict]:
    ""







    db = db if db is not None else _kanban_board_db()
    if db is not None and Path(db).is_file():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                rows = con.execute(
                    "SELECT id, status, assignee, title, idempotency_key FROM tasks "
                    "WHERE status IS NULL OR status != 'archived'"
                ).fetchall()
                park_until = _park_dates(con)
            finally:
                con.close()
            return [
                {"id": str(i), "status": str(st or "").lower(), "assignee": str(a or "").lower(),
                 "title": str(t or ""), "idempotency_key": str(k or ""), "line": "",
                 "park_until": park_until.get(str(i), "")}
                for i, st, a, t, k in rows
            ]
        except Exception as e:
            log(f"kanban board read {db} failed {e!r} — title matching only")
    out: list[dict] = []
    for ln in (listing or "").splitlines():
        row = parse_kanban_list_row(ln)
        if row:
            row["idempotency_key"] = ""
            out.append(row)
    return out


_PARK_LATER_RE = re.compile(r"^\s*later\b[\s:,-]*(\d{4}-\d{2}-\d{2})", re.I)


def _park_dates(con: sqlite3.Connection) -> dict[str, str]:
    ""



    try:
        rows = con.execute(
            "SELECT e.task_id, e.payload FROM task_events e JOIN tasks t ON t.id = e.task_id "
            "WHERE e.kind = 'scheduled' AND t.status = 'scheduled' ORDER BY e.id"
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for tid, payload in rows:
        try:
            reason = str((json.loads(payload or "{}") or {}).get("reason") or "")
        except (ValueError, AttributeError):
            reason = ""
        m = _PARK_LATER_RE.match(reason)
        out[str(tid)] = m.group(1) if m else ""
    return out


def _card_ids(
    cards: list[dict],
    *,
    key: str | None = None,
    key_prefix: str | None = None,
    title_substr: str | None = None,
    open_only: bool = False,
) -> list[str]:
    ""

    ids: list[str] = []
    for c in cards or []:
        if open_only and str(c.get("status") or "") in KANBAN_CLOSED:
            continue
        k = str(c.get("idempotency_key") or "")
        if k:
            hit = (key is not None and k == key) or (key_prefix is not None and k.startswith(key_prefix))
        else:
            hay = f"{c.get('title') or ''} {c.get('line') or ''}"
            hit = bool(title_substr) and title_substr in hay
        if hit and c.get("id") and c["id"] not in ids:
            ids.append(str(c["id"]))
    return ids


def _kanban_task_ids(
    hay: str,
    title_substr: str,
    *,
    cards: list[dict] | None = None,
    key: str | None = None,
    key_prefix: str | None = None,
    open_only: bool = False,
) -> list[str]:
    if cards is not None:
        return _card_ids(cards, key=key, key_prefix=key_prefix,
                         title_substr=title_substr, open_only=open_only)
    ids: list[str] = []
    for ln in hay.splitlines():
        if title_substr not in ln:
            continue
        m = re.search(r"\b(t_[0-9a-f]{8})\b", ln)
        if m:
            ids.append(m.group(1))
    return ids


_PROD_GATE = _T("card.footer")


_DEDUPE_EXEMPT = ("ms-dep-summary", "ms-security-low")
_KEY_DATE_TAIL = re.compile(r"(?::\d{4}-W\d{2}|:\d{4}-\d{2}-\d{2}|-\d{4}w\d{2})$")
_BOARD_DB_CACHE: list = []


def _board_db_file() -> Path | None:
    ""
    pinned = os.environ.get("MS_KANBAN_DB", "").strip()
    if pinned:
        return Path(pinned)
    kanban = KH.kanban_dir()
    try:
        slug = (kanban / "current").read_text().strip()
    except OSError:
        return None
    p = kanban / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def _key_base(idemp: str) -> str:
    ""
    return _KEY_DATE_TAIL.sub("", str(idemp or ""))


def recently_closed(idemp: str, *, db: Path | None = None, now: float | None = None,
                    days: int | None = None) -> str:
    ""
    days = MS_DEDUPE_CLOSED_DAYS if days is None else days
    base = _key_base(idemp)
    if days <= 0 or not base or base.startswith(_DEDUPE_EXEMPT):
        return ""
    if db is None:
        if not _BOARD_DB_CACHE:
            _BOARD_DB_CACHE.append(_board_db_file())
        db = _BOARD_DB_CACHE[0]
    if db is None or not Path(db).is_file():
        return ""
    since = (time.time() if now is None else now) - days * 86400
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT t.id, t.idempotency_key FROM tasks t WHERE t.status IN ('done', 'archived') "
                "AND t.idempotency_key LIKE ? AND (SELECT max(e.created_at) FROM task_events e "
                "WHERE e.task_id = t.id AND e.kind IN ('completed', 'archived')) >= ?",
                (base.replace("%", "") + "%", since)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        log(f"dedupe: board read failed {e!r} — filing as before")
        return ""
    for tid, k in rows:
        if _key_base(k) == base:
            return str(tid)
    return ""


def _kanban_create(
    *,
    title: str,
    body: str,
    assignee: str,
    idemp: str,
) -> bool:
    closed = recently_closed(idemp)
    if closed:
        log(f"not re-filed {idemp}: {closed} closed inside {MS_DEDUPE_CLOSED_DAYS} d ({title[:60]!r})")
        return False
    hermes = _hermes_bin()
    try:
        r = subprocess.run(
            [
                *_kanban_argv(hermes),
                "kanban",
                "create",
                title,
                "--body",
                (NO_DECOMPOSE_WORK_LINE + "\n\n" + body)[:5500],
                "--assignee",
                assignee,


                "--idempotency-key",
                idemp,
                "--workspace",
                f"dir:{DELIVERABLES_DIR}",
                "--created-by",
                "ms-trail",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        log(f"kanban create FAIL {title[:60]!r}: {e!r}")
        return False
    if r.returncode != 0:
        log(
            f"kanban create FAIL {title[:60]!r}: "
            f"{(r.stderr or r.stdout or '')[:200]}"
        )
        return False
    log(f"kanban card filed: {(r.stdout or '').strip()[:120] or title[:60]}")
    return True


def iso_week(day: str) -> str:
    ""
    try:
        d = dt.date.fromisoformat(str(day)[:10])
    except ValueError:
        d = dt.date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def weekly_key(base: str, day: str) -> str:
    ""




    return f"{base}:{iso_week(day)}"


def issue_card_open(cards: list[dict] | None, base: str, day: str) -> list[str]:
    ""






    this = weekly_key(base, day)
    today = str(day)[:10]
    ids: list[str] = []
    for c in cards or []:
        k = str(c.get("idempotency_key") or "")
        if not (k == base or k.startswith(base + ":")):
            continue
        st = str(c.get("status") or "")
        if st in KANBAN_CLOSED:
            continue
        if st == "scheduled" and k != this and not (
                MS_PARK_DATE_HOLDS and str(c.get("park_until") or "") >= today):
            continue
        if c.get("id"):
            ids.append(str(c["id"]))
    return ids


def _issue_slug(text: str, *, n: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n] or "issue")


def file_hermes_issue_cards(report: dict, day: str, listing: str,
                            cards: list[dict] | None = None) -> None:
    ""
    hermes = report.get("hermes") or {}
    doc = hermes.get("doctor") or {}
    sec = hermes.get("security") or {}
    for issue in doctor_actionable_issues(doc.get("issues") or []):
        if not issue:
            continue
        title = f"M&S Hermes doctor: {issue}"[:120]
        if (title[:40] in listing or f"M&S Hermes doctor: {issue[:40]}" in listing
                or issue_card_open(cards, f"ms-doctor:{_issue_slug(issue)}", day)):
            log(f"doctor card already open: {issue[:60]}")
            continue
        body = (
            f"Nightly Hermes doctor {day}.\n"
            f"Issue: {issue}\n"
            f"Crystal: `{HERMES_REPORT_DIR / 'doctor-LATEST.txt'}`\n"
            f"{_PROD_GATE}\n"
            "Do not complete until doctor no longer lists this issue."
        )
        _kanban_create(
            title=title,
            body=body,
            assignee=_assignee("doctor"),
            idemp=weekly_key(f"ms-doctor:{_issue_slug(issue)}", day),
        )
    findings = sec.get("findings") if isinstance(sec.get("findings"), list) else []
    keep = []
    mild = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity") or "UNKNOWN").upper()
        if sev in ("HIGH", "CRITICAL", "MODERATE"):
            keep.append(f)
        else:
            mild.append(f)
    for f in keep:
        vid = str(f.get("vuln_id") or "").strip()
        pkg = str(f.get("package") or "?")
        title = f"M&S Hermes security: {vid or pkg} ({f.get('severity')})"
        if (vid and vid in listing) or issue_card_open(
            cards, f"ms-security:{vid or _issue_slug(pkg)}", day
        ):
            log(f"security card already open: {vid or pkg}")
            continue
        if title[:50] in listing:
            log(f"security card already open: {title[:50]}")
            continue
        fixed = ", ".join(str(x) for x in (f.get("fixed_versions") or [])[:4]) or "—"
        body = (
            f"OSV {day} `{pkg}` {f.get('version')} {vid} "
            f"{f.get('severity')}.\n"
            f"{(f.get('summary') or '')[:400]}\n"
            f"Fixed in: {fixed}\n"
            f"Crystal: `{HERMES_REPORT_DIR / 'security-LATEST.json'}`\n"
            f"{_PROD_GATE}\n"
            "Do not complete while this vuln_id is still in the scan."
        )
        _kanban_create(
            title=title[:120],
            body=body,
            assignee=_assignee("security"),
            idemp=weekly_key(f"ms-security:{vid or _issue_slug(pkg)}", day),
        )
    if mild:
        title = f"M&S Hermes security: {len(mild)} low/unknown ({day})"
        if ("M&S Hermes security: " in listing and "low/unknown" in listing) or issue_card_open(
                cards, "ms-security-low", day):
            log("security low/unknown card already open")
        else:
            lines = [
                f"OSV {day} remaining LOW/UNKNOWN (not HIGH).",
                f"Crystal: `{HERMES_REPORT_DIR / 'security-LATEST.json'}`",
                _PROD_GATE,
            ]
            for f in mild[:12]:
                if isinstance(f, dict):
                    lines.append(
                        f"- {f.get('package')} {f.get('vuln_id')} {f.get('severity')}"
                    )
            _kanban_create(
                title=title,
                body="\n".join(lines),
                assignee=_assignee("security"),
                idemp=f"ms-security-low:{day}",
            )




_RETIRABLE = ("todo", "ready", "blocked", "triage")


def _ms_complete(tid: str, result: str, what: str) -> bool:
    try:
        rr = subprocess.run([*_kanban_argv(_hermes_bin()), "kanban", "complete", tid,
                             "--result", result[:500]], capture_output=True, text=True, timeout=30)
    except Exception as e:
        log(f"retire {tid} {what}: {e!r}")
        return False
    log(f"retire {tid} {what} rc={rr.returncode} {(rr.stderr or '')[:100] if rr.returncode else ''}")
    return rr.returncode == 0


def plan_own_retires(report: dict, day: str, cards: list[dict], *, trend: list[dict] | None = None,
                     drift_rows: list[dict] | None = None) -> list[tuple[str, str, str]]:
    ""


    out: list[tuple[str, str, str]] = []
    live = [c for c in cards or [] if str(c.get("status") or "") in _RETIRABLE]
    hermes_rep = report.get("hermes") or {}
    doc, sec = hermes_rep.get("doctor") or {}, hermes_rep.get("security") or {}


    if doc.get("measured") is not False and int(doc.get("actionable_n") or 0) > 0:
        cur = {_issue_slug(i) for i in doctor_actionable_issues(doc.get("issues") or [])}
        if len(cur) >= int(doc.get("actionable_n") or 0):
            for c in live:
                k = _key_base(c.get("idempotency_key") or "")
                if k.startswith("ms-doctor:") and k[len("ms-doctor:"):] not in cur:
                    out.append((c["id"], f"hermes doctor no longer lists this issue ({day})", "doctor"))

    findings = sec.get("findings")
    if sec.get("measured") is not False and isinstance(findings, list) and int(sec.get("finding_count") or 0) > 0:
        vids = {str(f.get("vuln_id") or "") for f in findings if isinstance(f, dict)}
        vids |= {_issue_slug(str(f.get("package") or "?")) for f in findings if isinstance(f, dict)}
        mild = [f for f in findings if isinstance(f, dict)
                and str(f.get("severity") or "UNKNOWN").upper() not in ("HIGH", "CRITICAL", "MODERATE")]
        for c in live:
            k = _key_base(c.get("idempotency_key") or "")
            if k.startswith("ms-security:") and k[len("ms-security:"):] not in vids:
                out.append((c["id"], f"OSV no longer reports {k[len('ms-security:'):]} ({day})", "security"))
            elif k.startswith("ms-security-low") and not mild:
                out.append((c["id"], f"no LOW/UNKNOWN findings left ({day})", "security-low"))

    for c in live:
        k = str(c.get("idempotency_key") or "")
        if k.startswith("ms-dep-summary:") and k[len("ms-dep-summary:"):] < day[:10]:
            out.append((c["id"], f"superseded by the {day} nightly review", "dep-review"))

    quiet = MS_RETIRE_QUIET_NIGHTS
    today = dt.date.fromisoformat(day[:10])
    rows = drift_rows if drift_rows is not None else _drift_rows()
    for c in live:
        k = str(c.get("idempotency_key") or "")
        m = re.match(r"^ms-drift:(.+):(\d{4}-\d{2}-\d{2})$", k)
        if not m:
            continue
        env, filed = m.group(1), dt.date.fromisoformat(m.group(2))
        if (today - filed).days < quiet:
            continue
        since = (today - dt.timedelta(days=quiet)).isoformat()
        loud = [r for r in rows if r.get("env") == env and str(r.get("day") or "") > since
                and (r.get("unexplained") or r.get("against_hold"))]
        if not loud:
            out.append((c["id"], f"no unexplained drift in {env} for {quiet} nights; drift.jsonl "
                                 "keeps the record (drift is a report)", "drift"))

    hist = trend if trend is not None else _read_trend()
    row = report.get("trend") or {}
    nights = ([h for h in hist if h.get("day") != row.get("day")] + ([row] if row else []))[-quiet:]
    if len(nights) >= quiet:
        failing = {str(f) for h in nights for f in (h.get("apply_failures") or [])}
        for c in live:
            k = str(c.get("idempotency_key") or "")
            if not k.startswith("ms-diagnose-"):
                continue
            if any(k.startswith(f"ms-diagnose-{f.partition(':')[0]}-{f.partition(':')[2]}-") for f in failing):
                continue
            out.append((c["id"], f"not seen for {quiet} nights ({', '.join(str(h.get('day')) for h in nights)})",
                        "diagnose"))
    return out


def retire_spent_ms_cards(report: dict, day: str, cards: list[dict] | None = None) -> None:
    ""





    hermes = _hermes_bin()
    try:
        r = subprocess.run(
            [*_kanban_argv(hermes), "kanban", "list"],
            capture_output=True, text=True, timeout=30,
        )
        listing = r.stdout or ""
    except Exception as e:
        log(f"retire ms cards: list failed {e!r}")
        return
    if cards is None:
        cards = kanban_board_cards(listing)

    hermes_rep = report.get("hermes") or {}
    doc = hermes_rep.get("doctor") or {}
    sec = hermes_rep.get("security") or {}

    if doc.get("measured") is False:
        log("retire ms cards: doctor UNKNOWN — doctor cards stay open")
    elif int(doc.get("actionable_n") or 0) == 0:
        for tid in _kanban_task_ids(listing, "M&S Hermes doctor:", cards=cards,
                                    key_prefix="ms-doctor:", open_only=True):
            rr = subprocess.run(
                [
                    *_kanban_argv(hermes), "kanban", "complete", tid,
                    "--result", f"doctor clean {day}",
                ],
                capture_output=True, text=True, timeout=30,
            )
            log(f"retire {tid} doctor rc={rr.returncode}")
    if sec.get("measured") is False:
        log("retire ms cards: security scan UNKNOWN — security cards stay open")
    elif int(sec.get("finding_count") or 0) == 0:
        for tid in _kanban_task_ids(listing, "M&S Hermes security:", cards=cards,
                                    key_prefix="ms-security", open_only=True):
            rr = subprocess.run(
                [
                    *_kanban_argv(hermes), "kanban", "complete", tid,
                    "--result", f"OSV clean {day}",
                ],
                capture_output=True, text=True, timeout=30,
            )
            log(f"retire {tid} Hermes security rc={rr.returncode}")

    if MS_RETIRE_OWN:
        for tid, result, what in plan_own_retires(report, day, cards):
            _ms_complete(tid, result, what)

    ledger = _ledger_or_none()
    if ledger is None:
        log("retire ms cards: ledger UNREADABLE — mitigation cards stay open")
        return
    for name, info in (report.get("envs") or {}).items():
        if info.get("outdated_unknown"):
            log(f"retire ms cards: {name} outdated UNKNOWN — not 0 actionable, card stays")
            continue
        risk = (info.get("details") or {}).get("risk") or []
        act, _ = filter_actionable_risk(name, [x for x in risk if not x.get("truncated")], ledger)
        if act:
            continue

        if (info.get("details") or {}).get("group-wait"):
            continue



        if MS_RETIRE_OWN and not any(
                str(r.get("env") or "") == name and str(r.get("decision") or "").lower() == "pending_apply"
                for r in (ledger.get("decisions") or []) if isinstance(r, dict)):
            for tid in _kanban_task_ids(listing, f"M&S land: {name}", cards=cards,
                                        key_prefix=f"ms-land:{name}:", open_only=True):
                st = next((str(c.get("status") or "") for c in cards if c.get("id") == tid), "")
                if st in _RETIRABLE:
                    _ms_complete(tid, f"nothing left to land in {name}: 0 actionable risk and no "
                                      f"pending_apply in the ledger ({day})", "land")
        needle = f"M&S mitigation: {name} — "
        for tid in _kanban_task_ids(listing, needle, cards=cards,
                                    key=f"ms-mitigation:{name}",
                                    key_prefix=f"ms-mitigation:{name}:", open_only=True):
            rr = subprocess.run(
                [
                    *_kanban_argv(hermes), "kanban", "complete", tid,
                    "--result",
                    f"{name}: 0 actionable risk after {day} scan; "
                    "holds in decisions.json. Card retired.",
                ],
                capture_output=True, text=True, timeout=30,
            )
            log(
                f"retire {tid} mitigation {name} rc={rr.returncode} "
                f"{(rr.stderr or rr.stdout or '')[:120]}"
            )


def write_live_manifest(report: dict, day: str) -> None:
    ""


    if not LIVE_MANIFEST_ON:
        return
    official = sorted(LIVE_MANIFEST.parent.glob("MANIFEST-ULTRA-*.md"))
    lines = [
        _T("manifest.title"),
        "",
        f"Refreshed by `scripts/ms-trail-run.py` each M&S run. Last: "
        f"`{report['ts_local']}` · tool **{report['package_tool']}**.",
        f"Official frozen snapshot: `docs/{official[-1].name}`."
        if official else "",
        "Do not hand-edit — changes are overwritten nightly.",
        "",
        "## Service health (probed this run)",
        "",
        "| Lane | State | Note |",
        "|---|---|---|",
    ]
    notes = report.get("health_notes") or {}
    mode = str(report.get("gpu_mode") or occupancy_gpu_mode())
    for k, v in (report.get("health") or {}).items():
        extra = notes.get(k, "")
        tag = health_tag(k, bool(v), mode=mode, note=extra)
        lines.append(f"| {k} | {tag} | {extra} |")
    hermes = report.get("hermes") or {}
    doc = hermes.get("doctor") or {}
    sec = hermes.get("security") or {}
    lines += [
        "",
        "## Hermes doctor + security (this run)",
        "",
        doctor_manifest_line(doc),
        f"- **security audit (OSV, Hermes venv/plugins/MCP):** "
        f"scanned {sec.get('total_components_scanned', '?')} · "
        f"findings {sec.get('finding_count', '?')} · {sec.get('by_severity') or {}}",
        f"- reports: `{HERMES_REPORT_DIR}/doctor-LATEST.txt` · "
        f"`{HERMES_REPORT_DIR}/security-LATEST.json`",
        f"- **maintenance window:** `{WINDOW_PATH}` — land here or stay "
        "`pending_apply`. Kanban is not the queue.",
    ]
    profs = (report.get("hermes_profiles") or survey_hermes_profiles())
    prow = profs.get("profiles") or []
    if prow:
        lines += [
            "",
            "## Hermes profiles (this run)",
            "",
*_TX.lines("manifest.profiles_note"),
            "",
            "| Profile | Dist | `_config_version` | Floor | Kanban worker |",
            "|---|---|---|---|---|",
        ]
        for r in prow:
            ver = r.get("config_version")
            floor = "BELOW" if r.get("below_floor") else "ok"
            worker = "yes" if r.get("kanban_worker") else "no"
            live = "Y" if r.get("live") else "missing"
            lines.append(
                f"| {r.get('name')} ({live}) | {r.get('distribution') or '—'} | "
                f"{ver if ver is not None else '—'} | {floor} | {worker} |"
            )
        if profs.get("below_floor"):
            lines.append(
                f"- **below v{profs.get('floor')} floor:** "
                + ", ".join(profs["below_floor"])
                + " — restore --apply lifts + migrates"
            )
        lines.append("")
    lines += _TX.lines("manifest.engines")
    rend = report.get("render") or {}
    if rend:
        git = rend.get("git") or {}
        sota = rend.get("sota") or {}
        recs = rend.get("recipes") or []
        wired_n = sum(1 for r in recs if r.get("wired"))
        lines += [
            _T("manifest.render_heading"),
            "",
            f"- Comfy git: `{git.get('describe') or '?'}` (`{git.get('sha') or '?'}`)",
            f"- origin latest: `{sota.get('origin_latest') or 'unread'}` · "
            f"behind={bool(sota.get('behind'))}",
            f"- recipes wired: **{wired_n}/{len(recs)}** "
            f"({', '.join(r['id'] for r in recs if r.get('wired')) or 'none'})",
            "",
        ]
        nodes = rend.get("nodes") or {}
        if nodes:
            lines.append("| Custom node | HEAD | origin latest |")
            lines.append("|---|---|---|")
            for n, info in nodes.items():
                lines.append(
                    f"| {n} | `{info.get('sha') or '?'}` | "
                    f"`{info.get('origin_latest') or '—'}` |"
                )
            lines.append("")
    lines += [
        _T("manifest.inventory_heading"),
        "",
        "| Env | Role | Live | Used by | Heavy | Pkgs | Outdated | green/risk/never/pinned/gw | Unused | Smoke | Applied tonight |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    applied = report.get("applied") or {}
    considered = bool(applied)
    for name, info in (report.get("envs") or {}).items():
        c = info.get("classified") or {}
        a = applied.get(name)
        ap = applied_label(
            a,
            considered_apply=considered,
            green_n=int(c.get("green_candidate") or 0),
        )
        pkg_n = ""
        man = info.get("manifest")
        if man and Path(man).is_file():
            pkg_n = str(len(Path(man).read_text().splitlines()))
        used = ", ".join((info.get("used_by") or [])[:4]) or "—"
        lines.append(
            f"| {name} | {info.get('role')} | "
            f"{'Y' if info.get('in_use') else 'N'} | {used} | "
            f"{'Y' if info.get('heavy') else 'N'} "
            f"| {pkg_n} | {info.get('outdated_n')} "
            f"| {c.get('green_candidate')}/{c.get('risk')}/{c.get('never')}/"
            f"{c.get('pinned', 0)}/{c.get('group-wait', 0)} "
            f"| {len(((info.get('unused') or {}).get('never_imported') or []))} "
            f"| {'OK' if info.get('smoke_baseline') else 'RED'} | {ap} |"
        )
    unused_rows = [
        (name, (info.get("unused") or {}).get("never_imported") or [])
        for name, info in (report.get("envs") or {}).items()
        if (info.get("unused") or {}).get("never_imported")
    ]
    if unused_rows:
        lines += [
            "",
            "## Unused packages (installed, required by nothing, never imported)",
            "",
_T("manifest.unused_note"),
            "",
        ]
        for name, names in unused_rows:
            lines.append(f"- **{name}** ({len(names)}): " + ", ".join(names[:30])
                         + (f" … +{len(names) - 30}" if len(names) > 30 else ""))
    lines += [
        *_TX.lines("manifest.stt"),
        "`never` = deliberate pins (NVIDIA cu13 stack, torch family, ORT) — not actionable.",
        f"Freeze manifests: `{MANIFEST_DIR}/*-LATEST.txt` · "
        f"full report: `{REPORT_DIR}/ms-trail-{day}.json`",
        "",
    ]
    LIVE_MANIFEST.write_text("\n".join(lines))
    log(f"live manifest → {LIVE_MANIFEST}")


def os_update_snapshot() -> dict:
    ""





    lane = str(_S.get("os_lane") or "auto").lower()
    if _osu is None or lane == "off" or (lane == "auto" and not shutil.which("dpkg")):
        return {}
    try:
        state = _osu.gather()
        _osu.write_artifact(state, out_dir=OUT_ROOT / "os")
        return state
    except Exception as e:
        log(f"os-update gather failed {e!r} — leg skipped")
        return {}


import contextlib
import fcntl
import sqlite3
import tempfile
import traceback





MS_LEDGER_STRICT = os.environ.get("MS_LEDGER_STRICT", "1").strip() not in ("0", "false", "off", "no")
LEDGER_KEYS = ("schema", "decisions", "updated_at", "updated_by")


class LedgerUnreadable(RuntimeError):
    ""






def _ledger_last_good() -> Path:
    return DECISIONS_PATH.with_name(DECISIONS_PATH.stem + ".LAST-GOOD" + DECISIONS_PATH.suffix)


def _ledger_lock_path() -> Path:
    return DECISIONS_PATH.with_name(DECISIONS_PATH.name + ".lock")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    ""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


_LEDGER_LOCK_DEPTH = 0


@contextlib.contextmanager
def _ledger_lock():
    ""

    global _LEDGER_LOCK_DEPTH
    if _LEDGER_LOCK_DEPTH:
        _LEDGER_LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LEDGER_LOCK_DEPTH -= 1
        return
    lp = _ledger_lock_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lp, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        _LEDGER_LOCK_DEPTH = 1
        try:
            yield
        finally:
            _LEDGER_LOCK_DEPTH = 0
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


@contextlib.contextmanager
def ledger_txn():
    ""






    with _ledger_lock():
        yield _load_decisions()


def _load_decisions() -> dict:
    ""




    if not DECISIONS_PATH.is_file():
        return {"schema": "ms_decisions/v1", "decisions": []}
    why = ""
    raw = b""
    d = None
    try:
        raw = DECISIONS_PATH.read_bytes()
        d = json.loads(raw.decode("utf-8"))
        if not isinstance(d, dict):
            why = f"top level is {type(d).__name__}, not an object"
        elif not isinstance(d.get("decisions", []), list):
            why = f"`decisions` is {type(d.get('decisions')).__name__}, not a list"
    except Exception as e:
        why = f"{type(e).__name__}: {e}"
    if why:
        lg = _ledger_last_good()
        msg = (f"decisions ledger {DECISIONS_PATH} is unreadable ({why[:200]}); "
               + (f"restore from {lg}" if lg.is_file() else f"no {lg.name} to restore from")
               + " — then write only through --record-decision")
        if MS_LEDGER_STRICT:
            log(f"LEDGER UNREADABLE: {msg}")
            raise LedgerUnreadable(msg)
        log(f"decisions load fail ({why[:160]}) — treating as empty (MS_LEDGER_STRICT=0)")
        return {"schema": "ms_decisions/v1", "decisions": []}
    d.setdefault("schema", "ms_decisions/v1")
    d.setdefault("decisions", [])
    if any(isinstance(r, dict) for r in d["decisions"]):
        lg = _ledger_last_good()
        try:
            if not lg.is_file() or lg.read_bytes() != raw:
                _atomic_write_bytes(lg, raw)
        except OSError as e:
            log(f"ledger LAST-GOOD copy failed {e!r}")
    return d


def _ledger_or_none() -> dict | None:
    ""
    try:
        return _load_decisions()
    except LedgerUnreadable:
        return None


def ledger_validation(ledger: dict | None = None) -> dict:
    ""



    ledger = ledger if ledger is not None else _load_decisions()
    stray = sorted(k for k in ledger if k not in LEDGER_KEYS)
    invalid = 0
    for r in ledger.get("decisions") or []:
        if (
            not isinstance(r, dict)
            or str(r.get("decision") or "").lower() not in DECISION_STATES
            or not str(r.get("env") or "").strip()
            or not str(r.get("package") or "").strip()
        ):
            invalid += 1
    return {"invalid": invalid, "stray_keys": len(stray), "stray_key_names": stray[:12]}


def save_decisions(ledger: dict) -> None:
    ""
    with _ledger_lock():
        ledger["schema"] = "ms_decisions/v1"
        ledger["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _atomic_write_bytes(DECISIONS_PATH, (json.dumps(ledger, indent=2) + "\n").encode("utf-8"))
    log(f"decisions → {DECISIONS_PATH}")


REASON_MAX = int(os.environ.get("MS_REASON_MAX", "2000"))

FINDINGS_PATH = Path(
    os.environ.get("MS_FINDINGS", str(OUT_ROOT / "findings.json"))
)


def _load_findings() -> dict:
    ""
    if not FINDINGS_PATH.is_file():
        return {"schema": "ms_findings/v1", "findings": []}
    try:
        d = json.loads(FINDINGS_PATH.read_text())
        if not isinstance(d, dict):
            return {"schema": "ms_findings/v1", "findings": []}
        d.setdefault("schema", "ms_findings/v1")
        d.setdefault("findings", [])
        return d
    except Exception as e:
        log(f"findings load fail {e!r} — treating as empty")
        return {"schema": "ms_findings/v1", "findings": []}


def record_finding(
    *, title: str, finding: str, source: str = "", evidence: str = ""
) -> None:
    ""







    if not str(title).strip() or not str(finding).strip():
        raise ValueError("--record-finding needs --title and --reason")
    store = _load_findings()
    rows = [x for x in store.get("findings") or [] if isinstance(x, dict)]
    rows.append(
        {
            "title": title.strip(),
            "finding": finding,
            "source": source,
            "evidence": evidence,
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
        }
    )
    store["findings"] = rows
    store["schema"] = "ms_findings/v1"
    FINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FINDINGS_PATH.write_text(json.dumps(store, indent=2) + "\n")
    log(f"findings → {FINDINGS_PATH}")


def annotate_decision(
    *, env: str, package: str, note: str, source: str = ""
) -> bool:
    ""






    if not str(note).strip():
        raise ValueError("--annotate needs --reason (the note text)")
    with ledger_txn() as ledger:
        rows = [x for x in ledger.get("decisions") or [] if isinstance(x, dict)]
        pkg = canon(package)
        env_l = str(env).lower()
        hit = None
        for x in rows:
            if (
                str(x.get("env", "")).lower() == env_l
                and canon(str(x.get("package") or "")) == pkg
            ):
                hit = x
                break
        if hit is None:
            return False
        notes = hit.get("notes")
        if not isinstance(notes, list):
            notes = []
        notes.append(
            {
                "note": note,
                "source": source,
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
            }
        )
        hit["notes"] = notes
        ledger["decisions"] = rows
        save_decisions(ledger)
        return True


UNLOCK_DISPOSITIONS = ("keep", "toss", "land-only")
WINDOW_UNLOCK = {
    "claim": "window/pip apply",
    "path_on": True,
    "flag": "n/a",
    "measure": "none",
    "kpi_id": None,
    "disposition": "land-only",
}


def _as_bool(v, *, default: bool | None = None) -> bool | None:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return default
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"unlock.path_on {v!r} is not true/false")


def validate_unlock(unlock: dict) -> dict:
    ""





    if not isinstance(unlock, dict):
        raise ValueError("unlock must be an object")
    d = str(unlock.get("disposition") or "").strip()
    if d not in UNLOCK_DISPOSITIONS:
        raise ValueError(
            f"unlock.disposition {d!r} is not keep|toss|land-only. "
            "helps=keep (track a KPI), hurts=toss, neutral/security=land-only."
        )
    claim = str(unlock.get("claim") or "").strip()
    if not claim:
        raise ValueError(
            "unlock.claim is required — one line for the upstream advantage"
        )
    measure = str(unlock.get("measure") or "").strip() or "none"
    flag = str(unlock.get("flag") or "n/a").strip() or "n/a"
    path_on = _as_bool(unlock.get("path_on"), default=False)
    kpi_id = str(unlock.get("kpi_id") or "").strip() or None
    if d == "keep":
        if path_on is not True:
            raise ValueError(
                "unlock keep requires path_on=true (the flag is in the start "
                "script). Wheel in the env but unused → land-only, or turn it on."
            )
        if not kpi_id or measure in ("", "none"):
            raise ValueError(
                "unlock keep requires kpi_id and measure. "
+ _T("error.unlock_kpi_hint")
            )
    if d == "land-only" and kpi_id:
        raise ValueError("land-only does not track; omit kpi_id")
    return {
        "claim": claim,
        "path_on": bool(path_on),
        "flag": flag,
        "measure": measure,
        "kpi_id": kpi_id,
        "disposition": d,
    }


def normalize_unlock(unlock, *, decision: str) -> dict | None:
    ""
    if decision != "applied":
        if not unlock:
            return None
        return validate_unlock(unlock)
    if not unlock:
        return dict(WINDOW_UNLOCK)
    return validate_unlock(unlock)


def record_decision(
    *,
    env: str,
    package: str,
    decision: str,
    reason: str,
    current: str = "",
    target: str = "",
    source: str = "",
    actionable: bool = False,
    until: str | None = None,
    extra: dict | None = None,
    unlock: dict | None = None,
) -> None:
    ""








    decision = (decision or "").strip()
    if decision not in DECISION_STATES:
        raise ValueError(
            f"decision {decision!r} is not a state. Valid: "
            + "|".join(DECISION_STATES)
            + ". For a discovery that is not a package decision, use "
            "--record-finding; to add evidence to an existing row, --annotate."
        )
    if not str(env).strip() or not str(package).strip():
        raise ValueError(
            "env and package are both required — a row that names no package "
            "matches nothing and is never read again."
        )
    if len(reason or "") > REASON_MAX:
        raise ValueError(
            f"reason is {len(reason)} chars (max {REASON_MAX}). A reason this "
            "long is a finding: use --record-finding and cite it here."
        )
    with ledger_txn() as ledger:
        rows = [x for x in ledger.get("decisions") or [] if isinstance(x, dict)]
        pkg = package.lower().replace("_", "-")
        env_l = env.lower()
        kept = [
            x
            for x in rows
            if not (
                str(x.get("env", "")).lower() == env_l
                and str(x.get("package", "")).lower().replace("_", "-") == pkg
            )
        ]
        extra_out = dict(extra or {})
        unlock_in = unlock if unlock is not None else extra_out.get("unlock")
        unlock_norm = normalize_unlock(unlock_in, decision=decision)
        if unlock_norm is not None:
            extra_out["unlock"] = unlock_norm
        else:
            extra_out.pop("unlock", None)
        kept.append(
            {
                "env": env,
                "package": package,
                "decision": decision,
                "current": current,
                "target": target,
                "reason": reason,
                "source": source,
                "actionable": bool(actionable),
                "until": until,
                "ts": dt.date.today().isoformat(),
                **extra_out,
            }
        )
        ledger["decisions"] = kept
        save_decisions(ledger)


def queue_greens_for_window(
    env: str,
    greens: list[dict],
    *,
    reason: str,
    source: str = "ms-green-window",
) -> int:
    ""




    if not greens:
        return 0
    with ledger_txn() as ledger:
        rows = [x for x in ledger.get("decisions") or [] if isinstance(x, dict)]
        n = 0
        for g in greens:
            pkg = str(g.get("package") or "").strip()
            if not pkg:
                continue
            cur = str(g.get("current") or "")
            lat = str(g.get("latest") or g.get("target") or "")
            pkg_l = pkg.lower().replace("_", "-")
            env_l = env.lower()
            existing = next(
                (
                    x
                    for x in rows
                    if str(x.get("env", "")).lower() == env_l
                    and str(x.get("package") or "").lower().replace("_", "-") == pkg_l
                ),
                None,
            )
            if existing:
                dec = str(existing.get("decision") or "").lower()
                if dec in ("hold", "wontfix", "never", "pin"):
                    continue
                if dec == "pending_apply" and str(existing.get("target") or "") == lat:
                    continue
                if dec == "applied" and str(existing.get("target") or "") == lat:
                    continue
                rows = [
                    x
                    for x in rows
                    if not (
                        str(x.get("env", "")).lower() == env_l
                        and str(x.get("package") or "").lower().replace("_", "-") == pkg_l
                    )
                ]
            rows.append(
                {
                    "env": env,
                    "package": pkg,
                    "decision": "pending_apply",
                    "current": cur,
                    "target": lat,
                    "reason": reason,
                    "source": source,
                    "actionable": True,
                    "until": None,
                    "ts": dt.date.today().isoformat(),
                }
            )
            n += 1
        if n:
            ledger["decisions"] = rows
            save_decisions(ledger)
        return n


def refusal_row(env: str, package: str, ledger: dict | None = None) -> dict:
    ""
    ledger = ledger or _load_decisions()
    pkg = canon(package)
    env_l = str(env).lower()
    for x in ledger.get("decisions") or []:
        if not isinstance(x, dict):
            continue
        if str(x.get("decision") or "").lower() != "refused":
            continue
        if str(x.get("env", "")).lower() == env_l and canon(str(x.get("package") or "")) == pkg:
            return x
    return {}


def record_refusal(
    env: str, package: str, *, target: str, current: str, outcome: str, err: str
) -> int:
    ""







    with ledger_txn() as ledger:
        prev = refusal_row(env, package, ledger)
        attempts = int(prev.get("attempts") or 0) + 1 if prev.get("last_target") == target else 1
        record_decision(
            env=env,
            package=package,
            decision="refused",
            reason=f"installer refused ({outcome}) at {target}: {err[:300]}",
            current=current,
            target=target,
            source="ms-apply-refusal",
            extra={
                "attempts": attempts,
                "last_target": target,
                "last_error": err[:300],
                "outcome": outcome,
            },
        )
    return attempts


def skip_nightly_refused(env: str, rows: list[dict], ledger: dict | None = None) -> set[str]:
    ""







    if MS_REFUSE_MAX <= 0:
        return set()
    ledger = ledger or _load_decisions()
    out: set[str] = set()
    for row in rows or []:
        pkg = canon(str(row.get("package") or ""))
        latest = str(row.get("latest") or row.get("target") or "")
        prev = refusal_row(env, pkg, ledger)
        if not prev:
            continue
        if str(prev.get("last_target") or "") != latest:
            continue
        if int(prev.get("attempts") or 0) >= MS_REFUSE_MAX:
            out.add(pkg)
    return out


def pending_apply_for_env(env: str, ledger: dict | None = None) -> list[dict]:
    ""
    ledger = ledger or _load_decisions()
    out: list[dict] = []
    env_l = env.lower()
    for row in ledger.get("decisions") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("env") or "").lower() != env_l:
            continue
        if str(row.get("decision") or "").lower() != "pending_apply":
            continue
        pkg = str(row.get("package") or "").strip()
        tgt = str(row.get("target") or "").strip()
        cur = str(row.get("current") or "").strip()
        if not pkg or not tgt:
            continue
        if cur and cur == tgt:
            continue
        out.append({
            "package": pkg,
            "current": cur,
            "latest": tgt,
            "env": env,
            "from": "pending_apply",
        })
    return out


def merge_window_rows(*groups: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for group in groups:
        for row in group or []:
            k = canon(str(row.get("package") or ""))
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(row)
    return out


def mark_pending_landed(env: str, pending: list[dict], result: dict) -> None:
    ""
    if not result.get("ok"):
        return
    landed = set()
    for blob in result.get("upgraded") or []:
        token = str(blob).split()[0] if blob else ""
        if token:
            landed.add(canon(token))
    with _ledger_lock():
        for row in pending:
            pkg = str(row.get("package") or "")
            if canon(pkg) not in landed:
                continue
            record_decision(
                env=env,
                package=pkg,
                decision="applied",
                reason="landed in M&S maintenance window (apply-green + smoke)",
                current=str(row.get("latest") or ""),
                target=str(row.get("latest") or ""),
                source="ms-window",
            )


def write_maintenance_window(items: list[dict], day: str) -> Path:
    ""
    WINDOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "ms_maintenance_window/v1",
        "window": _T("window.label"),
        "day": day,
        "ts_local": dt.datetime.now().isoformat(timespec="seconds"),
        "note": _T("window.note"),
        "n": len(items),
        "items": items,
    }
    WINDOW_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    log(f"maintenance window → {WINDOW_PATH} ({len(items)} items)")
    return WINDOW_PATH


def hold_expiry(row: dict) -> dt.date | None:
    ""






    if not isinstance(row, dict):
        return None
    explicit = str(row.get("until") or "").strip()
    if explicit:
        try:
            return dt.date.fromisoformat(explicit[:10])
        except ValueError:
            return None
    if MS_HOLD_TTL_DAYS <= 0:
        return None
    if str(row.get("decision") or "").lower() != "hold":
        return None
    base_s = str(row.get("ts") or "").strip()[:10]
    try:
        base = dt.date.fromisoformat(base_s)
    except ValueError:
        try:
            base = dt.date.fromisoformat(MS_TTL_ANCHOR)
        except ValueError:
            return None
    key = f"{row.get('env')}/{row.get('package')}"
    jitter = (int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) % 61) - 30



    return base + dt.timedelta(days=max(1, MS_HOLD_TTL_DAYS + jitter))


def hold_is_due(row: dict, *, today: dt.date | None = None) -> bool:
    ""
    exp = hold_expiry(row)
    if exp is None:
        return False
    return exp < (today or dt.date.today())


def effective_decision(row: dict, *, today: dt.date | None = None) -> str:
    ""






    dec = str(row.get("decision") or "").lower()
    if dec == "hold" and hold_is_due(row, today=today):
        return "review_due"
    return dec


def _decision_suppresses(row: dict, env: str, pkg_row: dict) -> bool:
    ""
    if not isinstance(row, dict):
        return False
    if str(row.get("env", "")).lower() != env.lower():
        return False
    pkg = canon(str(pkg_row.get("package") or "")).lower()
    if canon(str(row.get("package") or "")).lower() != pkg:
        return False



    if hold_is_due(row):
        return False
    until = row.get("until")
    if until:
        try:
            if dt.date.fromisoformat(str(until)[:10]) < dt.date.today():
                return False
        except ValueError:
            pass
    dec = str(row.get("decision") or "").lower()




    if dec in SUPPRESS_DECISIONS:
        target = str(row.get("target") or "").strip()
        latest = str(pkg_row.get("latest") or "").strip()
        if dec == "applied" and target and latest and target != latest:
            current = str(pkg_row.get("current") or "").strip()
            if current == latest:
                return True
            return False



        if dec == "cancelled" and target and latest and target != latest:
            return False

        if dec == "hold" and major_crosses(target, latest):
            return False


        if row.get("actionable") is True and dec in ("open",):
            return False
        return True
    return False








DECISION_STATES = (
    "hold",
    "pin",
    "never",
    "wontfix",
    "deferred",
    "cancelled",

    "applied",
    "pending_apply",
    "reviewed",
    "refused",
    "review_due",
)








APPLY_BLOCKING_DECISIONS = ("hold", "pin", "never", "wontfix", "deferred", "review_due")


SUPPRESS_DECISIONS = (
    "hold", "wontfix", "applied", "pin", "never",
    "reviewed", "pending_apply", "deferred", "cancelled",
)


def _decision_blocks_apply(row: dict, env: str, pkg_row: dict) -> bool:
    ""







    if not isinstance(row, dict):
        return False
    if str(row.get("env", "")).lower() != env.lower():
        return False
    pkg = canon(str(pkg_row.get("package") or "")).lower()
    if canon(str(row.get("package") or "")).lower() != pkg:
        return False
    dec = effective_decision(row)
    if dec == "review_due":



        return True
    until = row.get("until")
    if until:
        try:
            if dt.date.fromisoformat(str(until)[:10]) < dt.date.today():
                return False
        except ValueError:
            pass
    return dec in APPLY_BLOCKING_DECISIONS


def filter_apply_blocked(
    env: str, rows: list[dict], ledger: dict | None = None
) -> tuple[list[dict], list[dict]]:
    ""
    ledger = ledger or _load_decisions()
    decisions = [x for x in (ledger.get("decisions") or []) if isinstance(x, dict)]
    ok_rows: list[dict] = []
    blocked: list[dict] = []
    for x in rows:
        if any(_decision_blocks_apply(d, env, x) for d in decisions):
            blocked.append(x)
        else:
            ok_rows.append(x)
    return ok_rows, blocked


def filter_actionable_risk(
    env: str, risk: list[dict], ledger: dict | None = None
) -> tuple[list[dict], list[dict]]:
    ""
    ledger = ledger or _load_decisions()
    decisions = [x for x in (ledger.get("decisions") or []) if isinstance(x, dict)]
    actionable: list[dict] = []
    suppressed: list[dict] = []
    for x in risk:
        if x.get("truncated"):
            continue
        if any(_decision_suppresses(d, env, x) for d in decisions):
            suppressed.append(x)
        else:
            actionable.append(x)
    return actionable, suppressed


def file_mitigation_cards(report: dict, day: str) -> None:
    ""









    if not MS_FILE_MITIGATIONS:
        log("mitigation cards: MS_FILE_MITIGATIONS=0 — skip")
        return
    hermes = os.environ.get("HERMES_BIN", os.path.expanduser("~/.local/bin/hermes"))
    ledger = _load_decisions()
    try:
        r = subprocess.run(
            [*_kanban_argv(hermes), "kanban", "list"], capture_output=True, text=True, timeout=30
        )
        open_cards = r.stdout or ""
    except Exception as e:
        log(f"mitigation cards: kanban list failed {e!r} — skipping")
        return
    board_cards = kanban_board_cards(open_cards)

    candidates: list[tuple[str, dict, list[dict], list[dict]]] = []
    for name, info in (report.get("envs") or {}).items():
        risk = (info.get("details") or {}).get("risk") or []
        risk = [x for x in risk if not x.get("truncated")]
        if not risk:
            continue
        hold = env_hold(name)
        if hold:
            log(f"mitigation {name}: env on hold — {hold.get('reason')}; no card")
            continue
        actionable, suppressed = filter_actionable_risk(name, risk, ledger)
        if not actionable:
            log(
                f"mitigation {name}: 0 new risk "
                f"({len(suppressed)} already in decisions.json — scheduled/held, not a skip)"
            )
            continue
        candidates.append((name, info, actionable, suppressed))


    candidates.sort(key=lambda t: -len(t[2]))
    if MS_MITIGATION_MAX_ENVS > 0:
        candidates = candidates[:MS_MITIGATION_MAX_ENVS]

    DELIVERABLES_DIR.mkdir(parents=True, exist_ok=True)
    for name, info, actionable, suppressed in candidates:
        title = (
            f"M&S mitigation: {name} — {len(actionable)} actionable "
            f"risk update(s)"
        )


        idemp = weekly_key(f"ms-mitigation:{name}", day)
        if issue_card_open(board_cards, f"ms-mitigation:{name}", day) or _card_ids(
                [c for c in board_cards if not c.get("idempotency_key")],
                title_substr=f"M&S mitigation: {name} — ", open_only=True
        ) or f"M&S mitigation: {name} — " in open_cards:
            log(f"mitigation skip {name}: already open on board")
            continue
        closed = recently_closed(idemp)
        if closed:
            log(f"mitigation skip {name}: {closed} closed inside {MS_DEDUPE_CLOSED_DAYS} d")
            continue
        body_lines = [
            f"Nightly M&S {day}: **actionable** major bumps in `{name}` "
            f"({info.get('role')}).",
            f"Suppressed by decision ledger: **{len(suppressed)}** "
            f"(see `{DECISIONS_PATH}` + `docs/DEPS-PINNED-FINDINGS.md`).",
            "",
*_TX.lines("card.mitigation.criteria", decisions=DECISIONS_PATH, deliverables=DELIVERABLES_DIR,
                       name=name, day=day),
            "",
            "## Actionable packages",
        ]
        for x in actionable:
            body_lines.append(
                f"- {x['package']} {x['current']} → {x['latest']}"
            )
        if suppressed:
            body_lines += ["", "## Already decided (do not re-research)"]
            for x in suppressed[:20]:
                body_lines.append(
                    f"- {x['package']} {x['current']} → {x['latest']}"
                )
        body_lines += [
            "",
            "never-class packages (NVIDIA/torch/ORT) are excluded by design.",
            f"Freeze ref: {MANIFEST_DIR}/{name}-LATEST.txt (what is installed)",
            f"Restore ref: manifests/{name}-LAST-GOOD.txt — `ms-trail-run.py --restore-plan {name}`",
            f"Workspace: dir:{DELIVERABLES_DIR}",
        ]
        try:
            r = subprocess.run(
                [
                    *_kanban_argv(hermes),
                    "kanban",
                    "create",
                    title,
                    "--body",
                    "\n".join([NO_DECOMPOSE_WORK_LINE, ""] + body_lines)[:5500],
                    "--assignee",
                    _assignee("mitigation"),
                    "--idempotency-key",
                    idemp,
                    "--workspace",
                    f"dir:{DELIVERABLES_DIR}",
                    "--created-by",
                    "ms-trail",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode == 0:
                log(
                    f"mitigation card filed: {name} "
                    f"({len(actionable)} actionable / {len(suppressed)} held)"
                )
            else:
                log(
                    f"mitigation card FAIL {name}: "
                    f"{(r.stderr or r.stdout or '')[:200]}"
                )
        except Exception as e:
            log(f"mitigation card FAIL {name}: {e!r}")


_SEMVER = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")


def minor_bump(local, origin) -> bool | None:
    ""

    a, b = _SEMVER.search(str(local or "")), _SEMVER.search(str(origin or ""))
    if not a or not b:
        return None
    return (int(b.group(1)), int(b.group(2))) > (int(a.group(1)), int(a.group(2)))


def file_render_sota_card(report: dict, day: str, open_cards: str,
                          cards: list[dict] | None = None) -> None:
    ""




    if not MS_FILE_MITIGATIONS:
        return
    rend = report.get("render") or {}
    sota = rend.get("sota") or {}
    if not sota.get("behind"):
        return
    hold = env_hold(_S.get("render.env")) if _S.get("render.env") else None
    if hold:

        log(f"render sota: comfyui env on hold — {hold.get('reason')}; "
            f"{sota.get('local')} -> {sota.get('origin_latest')} not a card")
        return
    if MS_RENDER_SOTA_MINOR and minor_bump(sota.get("local"), sota.get("origin_latest")) is False:
        log(f"render sota: {sota.get('local')} -> {sota.get('origin_latest')} is a patch bump; not a card")
        return
    title = (
        f"M&S render SOTA: Comfy {sota.get('local') or '?'} → "
        f"{sota.get('origin_latest')}"
    )
    if "M&S render SOTA:" in (open_cards or "") or _card_ids(
        cards if cards is not None else [], key="ms-render-sota", open_only=True
    ):
        log("render sota: already open on board")
        return
    recs = rend.get("recipes") or []
    wired = [r["id"] for r in recs if r.get("wired")]
    missing = [r["id"] for r in recs if not r.get("wired")]
    nodes = rend.get("nodes") or {}
    node_lines = [
        f"- `{n}` `{info.get('sha') or '?'}` origin `{info.get('origin_latest') or '—'}`"
        for n, info in nodes.items()
    ]
    body = "\n".join(
        [
            f"Nightly M&S {day}: Comfy git is behind origin.",
            f"Local `{sota.get('local')}` → origin `{sota.get('origin_latest')}`.",
            "",
*_TX.lines("card.render_sota.path"),
            "",
            f"Recipes wired: {', '.join(wired) or 'none'}.",
            f"Unwired: {', '.join(missing) or 'none'}.",
            "Custom nodes:",
            *(node_lines or ["- none"]),
            "",
            *_TX.lines("card.render_sota.footer"),
        ]
    )
    hermes = os.environ.get("HERMES_BIN", os.path.expanduser("~/.local/bin/hermes"))
    try:
        r = subprocess.run(
            [
                *_kanban_argv(hermes),
                "kanban",
                "create",
                title,
                "--body",
                (NO_DECOMPOSE_WORK_LINE + "\n\n" + body)[:5500],
                "--assignee",
                _assignee("render"),
                "--idempotency-key",
                "ms-render-sota",
                "--workspace",
                f"dir:{DELIVERABLES_DIR}",
                "--created-by",
                "ms-trail",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            log(f"render sota card filed: {title}")
        else:
            log(f"render sota card FAIL: {(r.stderr or r.stdout or '')[:200]}")
    except Exception as e:
        log(f"render sota card FAIL: {e!r}")


def _cap_bucket(rows: list[dict], cap: int) -> list[dict]:
    if len(rows) <= cap:
        return rows
    return rows[:cap] + [
        {
            "package": f"… +{len(rows) - cap} more (see outdated_n / freeze)",
            "current": "",
            "latest": "",
            "truncated": True,
        }
    ]






MS_ADHOC_REPORTS = os.environ.get("MS_ADHOC_REPORTS", "1").strip() not in ("0", "false", "off", "no")
EMPTY_LEDGER = {"schema": "ms_decisions/v1", "decisions": []}


def rebuild_verb(args) -> int:
    ""
    try:
        import env_rebuild as rb
    except ImportError:
        print("rebuild is not part of this install")
        return 2
    env, ver, step = args.rebuild.strip(), args.to.strip(), args.step
    if not ver:
        print("--rebuild needs --to <version>")
        return 2
    if step != "status" and not rb.ENABLED:
        print("Stage D is dark: MS_REBUILD=1 for every step but status")
        return 2
    cfg = (_roots.load_roots() if _roots else {}).get("envs", {}).get(env)
    if cfg is None:
        print(f"no roots for {env!r} in config/ms-env-roots.json")
        return 2
    cfg = dict(cfg)
    dflt = (_roots.load_roots().get("defaults") or {}) if _roots else {}
    for key in ("data_wheels", "clis", "file_roots"):
        cfg[key] = list(dflt.get(key) or []) + list(cfg.get(key) or [])
    cfg["entry_point_groups"] = {**(dflt.get("entry_point_groups") or {}), **(cfg.get("entry_point_groups") or {})}
    live_py = str(rb.ENVS_ROOT / env / "bin" / "python")
    try:
        if step == "status":
            out = rb.status(env, ver)
        elif step == "plan":
            out = rb.plan(env, ver, cfg, old_py=live_py, never=set(NEVER_AUTO),
                          never_prefixes=tuple(NEVER_AUTO_PREFIXES))["steps"]["plan"]
        elif step == "build":
            out = rb.build(env, ver, cfg)["steps"]["build"]
        elif step == "firststart":
            out = rb.first_start(env, ver, cfg, yes=args.yes)["steps"]["first_start"]
        elif step == "check":
            out = rb.check(env, ver, cfg)["steps"]["check"]
        elif step == "serve":
            out = rb.serve(env, ver, cfg, which=args.which, port=args.port).get("serving")
        elif step == "renders":
            out = rb.renders(env, ver, cfg, label=args.label or args.which, port=args.port)["steps"]["renders"]
        elif step == "compare":
            out = rb.compare(env, ver)["steps"]["compare"]
        elif step == "stop":
            out = (rb.stop_machine(env, ver)["steps"]["stops"][-1] if args.which == "old"
                   else rb.stop_served(env, ver).get("serving"))
        elif step == "flip":
            out = rb.flip(env, ver, cfg, yes=args.yes, accept_review=args.accept_review)["flip"]
        else:
            out = rb.rollback(env, ver, yes=args.yes)["flip"]
    except rb.Refused as e:
        print(f"refused: {e}")
        return 3
    print(json.dumps(out, indent=1, default=str)[:20000])
    if isinstance(out, dict) and out.get("verdict") == "fail":
        return 1
    return 0


def env_roots_row(name: str, py: str, classified: dict, unused: dict | None) -> dict:
    ""
    if _roots is None:
        return {"label": name, "error": f"env_roots did not import: {_ROOTS_IMPORT_ERR}"}
    if not Path(py).is_file():
        return {"label": name, "error": f"no interpreter at {py}"}
    try:
        try:
            ledger = _load_decisions()
        except Exception:
            ledger = {}
        return _roots.run_env(name, py, classified, ledger=ledger,
                              classify=classify_update, unused=unused)
    except Exception as e:
        log(f"roots {name}: {e!r} (report only; the night goes on)")
        return {"label": name, "error": repr(e)[:300]}


def write_roots_report(report: dict, day: str, adhoc: bool) -> Path | None:
    ""
    rows = {n: i["roots"] for n, i in (report.get("envs") or {}).items() if i.get("roots")}
    if not rows or _roots is None:
        return None
    if adhoc:
        path = REPORT_DIR / f"ms-roots-{day}-adhoc-{dt.datetime.now().strftime('%H%M%S')}.md"
    else:
        path = REPORT_DIR / f"ms-roots-{day}.md"
    _roots.lend(rows, str(ENVS_ROOT))
    md = "\n".join(_roots.markdown(rows, day)) + "\n"
    path.write_text(md)
    if not adhoc:
        (REPORT_DIR / "ms-roots-LATEST.md").write_text(md)
    report["roots"] = {"mode": os.environ.get("MS_ROOTS", "off"), "md": str(path),
                       "totals": _roots.totals(rows)}
    return path


@contextlib.contextmanager
def _env_guard(name: str, apply_failures: list[dict], env_exceptions: list[str], report: dict):
    ""
    try:
        yield
    except Exception as e:
        log(f"=== {name}: EXCEPTION {e!r} — recorded, next env ===\n{traceback.format_exc()}")
        env_exceptions.append(name)
        apply_failures.append({"env": name, "reason": "env_exception", "err": repr(e)[:200]})
        applied = (report.get("applied") or {}).get(name) or {}
        report.setdefault("applied", {})[name] = {
            **applied, "ok": False, "changed_n": int(applied.get("changed_n") or 0),
            "upgraded": applied.get("upgraded") or [], "err": f"env_exception: {repr(e)[:200]}",
        }


@contextlib.contextmanager
def _night_guard(what: str, night_errors: list[dict]):
    ""
    try:
        yield
    except Exception as e:
        log(f"{what}: EXCEPTION {e!r} — report still written\n{traceback.format_exc()}")
        night_errors.append({"phase": what, "err": repr(e)[:200]})


def register_env_row(args) -> dict:
    ""

    if not args.env_python:
        raise SystemExit("--register-env needs --env-python")
    if args.smoke_url:
        code = ("import urllib.request; urllib.request.urlopen(%r, timeout=15).read(64); "
                "print('health OK')" % args.smoke_url)
    elif args.smoke_code:
        code = args.smoke_code
    else:
        raise SystemExit("--register-env needs --smoke-url or --smoke-code: an env M&S cannot test stays report-only")
    row: dict = {"python": args.env_python, "role": args.env_role or "registered",
                 "heavy": False, "smoke": {"code": code, "timeout": 120}, "apply": args.apply}
    if args.service_unit:
        row["services"] = [{"unit": args.service_unit, "scope": args.service_scope,
                            "health": args.health_url or args.smoke_url or "",
                            "health_wait_s": 120, "restart": "restart"}]
    if args.promote_after:
        row["promote"] = {"after_clean_nights": int(args.promote_after)}
    return row


def _register_health(args) -> dict | None:
    url = args.health_url or args.smoke_url
    return {"url": url, "critical": bool(args.critical)} if url else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply-green",
        action="store_true",
        help="upgrade green_candidate in every env (light + heavy). Nightly default. Also lands specialist pending majors unless --no-apply-pending.",
    )
    ap.add_argument(
        "--apply-pending",
        action="store_true",
        help=_T("help.apply_pending"),
    )
    ap.add_argument(
        "--no-apply-pending",
        action="store_true",
        help="with --apply-green, skip specialist-major pass (greens only).",
    )
    ap.add_argument(
        "--force-heavy",
        action="store_true",
        help="apply GPU-shared envs even when /run/hermes/train-busy exists",
    )
    ap.add_argument("--kanban", action="store_true", default=bool(_S.get("kanban.enabled")),
                    help="queue summary to kanban (default: settings kanban.enabled)")
    ap.add_argument("--no-kanban", action="store_true")
    ap.add_argument("--where", action="store_true",
                    help="print where M&S keeps its state, settings, reports and ledger, and exit")
    rg = ap.add_argument_group("register an env (what an installer runs, step 3)")
    rg.add_argument("--register-env", default="", metavar="NAME",
                    help="write NAME's row into ms.yaml and exit")
    rg.add_argument("--env-python", default="", help="the env's interpreter")
    rg.add_argument("--env-role", default="", help="one line: what the env runs")
    rg.add_argument("--smoke-url", default="", help="health URL the env's python must fetch")
    rg.add_argument("--smoke-code", default="", help="python run in the env as its smoke test")
    rg.add_argument("--service-unit", default="", help="systemd unit the env serves")
    rg.add_argument("--service-scope", default="user", choices=("user", "system"))
    rg.add_argument("--health-url", default="", help="the unit's health URL (default: --smoke-url)")
    rg.add_argument("--apply", default="report", choices=("report", "green"))
    rg.add_argument("--promote-after", type=int, default=0, metavar="N",
                    help="apply: green after N consecutive clean nightly runs")
    rg.add_argument("--critical", action="store_true", help="a failing health lane fails the run")
    rg.add_argument("--hindsight-env-file", default="", help="Hindsight's env file (its LLM + retain checks)")
    ap.add_argument("--only", default="", help="comma env names to include")
    ap.add_argument(
        "--service-check",
        default="",
        metavar="ENV",
        help="live service env: real-import smoke, restart the unit, wait for health "
             "(run after any uninstall/upgrade by hand). Exit 0 GREEN, 1 RED. "
             "GREEN with every unit ok writes manifests/<env>-LAST-GOOD.txt.",
    )
    ap.add_argument("--rebuild", default="", metavar="ENV",
                    help="Stage D: rebuild ENV beside the old one (lib/env_rebuild.py); needs MS_REBUILD=1 "
                         "for every step but status")
    ap.add_argument("--to", default="", metavar="VER", help="with --rebuild: the app version the build is for")
    ap.add_argument("--step", default="status",
                    choices=["status", "plan", "build", "firststart", "check", "serve", "renders", "compare",
                             "stop", "flip", "rollback"], help="with --rebuild")
    ap.add_argument("--which", default="new", choices=["old", "new"],
                    help=_T("help.which"))
    ap.add_argument("--port", type=int, default=8188, help="with --step serve/renders")
    ap.add_argument("--label", default="", help="with --step renders: old|new (default = --which)")
    ap.add_argument("--yes", action="store_true", help="with --step flip/rollback")
    ap.add_argument("--accept-review", action="store_true",
                    help=_T("help.accept_review"))
    ap.add_argument(
        "--restore-plan",
        default="",
        metavar="ENV",
        help="print (never run) the exact `uv pip install --no-deps` that puts "
             "manifests/<env>-LAST-GOOD.txt back. Exit 2 when there is no LAST-GOOD.",
    )
    ap.add_argument(
        "--survey",
        action="store_true",
        help=_T("help.survey"),
    )
    ap.add_argument(
        "--record-decision",
        action="store_true",
        help="append a decision row to the ledger and exit "
        "(use with --env --package --decision --reason). This is the ONLY "
        "sanctioned writer — do not hand-edit decisions.json.",
    )
    ap.add_argument(
        "--record-finding",
        action="store_true",
        help="append a discovery that is NOT a package decision "
        "(use with --title --reason [--source] [--evidence]) and exit",
    )
    ap.add_argument(
        "--annotate",
        action="store_true",
        help="add evidence to an existing ledger row WITHOUT changing its "
        "decision (use with --env --package --reason) and exit",
    )
    ap.add_argument("--title", default="", help="with --record-finding: one-line title")
    ap.add_argument("--evidence", default="", help="with --record-finding: paths/ids")
    ap.add_argument("--env", default="", help="with --record-decision: env name")
    ap.add_argument("--package", default="", help="with --record-decision: package name")
    ap.add_argument(
        "--decision",
        default="",
        help="with --record-decision, one of: " + "|".join(DECISION_STATES),
    )
    ap.add_argument("--reason", default="", help="with --record-decision: why")
    ap.add_argument("--current", default="", help="with --record-decision: installed ver")
    ap.add_argument("--target", default="", help="with --record-decision: held/applied ver")
    ap.add_argument("--source", default="", help="with --record-decision: doc/card id")
    ap.add_argument(
        "--actionable",
        action="store_true",
        help="with --record-decision: keep package visible as still open work",
    )
    ap.add_argument("--until", default="", help="with --record-decision: ISO date hold expires")
    ap.add_argument(
        "--unlock-disposition",
        default="",
        choices=("", *UNLOCK_DISPOSITIONS),
        help="with --record-decision applied: keep (track KPI) | toss | land-only",
    )
    ap.add_argument("--unlock-claim", default="", help="upstream advantage, one line")
    ap.add_argument(
        "--unlock-path-on",
        action="store_true",
        help="the start script / flag actually uses this update",
    )
    ap.add_argument("--unlock-flag", default="", help="exact flag, or n/a")
    ap.add_argument(
        "--unlock-measure",
        default="",
        help="how we measure it (keep requires this + --unlock-kpi)",
    )
    ap.add_argument("--unlock-kpi", default="", help=_T("help.unlock_kpi"))

    args = ap.parse_args()
    if not _S.upgrades_on():
        print(_S.UPGRADES_OFF_LINE.format(_S.settings_file()), flush=True)
        _wants = [f for f, on in (("--apply-green", args.apply_green), ("--apply-pending", args.apply_pending),
                                  ("--service-check", bool(args.service_check)), ("--force-heavy", args.force_heavy),
                                  ("--rebuild", bool(args.rebuild) and args.step not in ("status", "plan"))) if on]
        if _wants:
            print("refused: " + ", ".join(_wants) + " change an environment and need `upgrades: true`. "
                  "Nothing was changed.", flush=True)
            return 2
    if args.service_check:
        name = args.service_check.strip()
        if name not in SERVICE_ENVS or name not in VENVS:
            print(f"not a live service env: {name!r} (known: {', '.join(sorted(SERVICE_ENVS))})")
            return 2
        py = VENVS[name][0]
        ok = smoke(name, py)
        if not ok:
            print(f"RED {name}: real import fails — {SMOKE_ERR.get(name, '')}. "
                  f"Do not restart; restore LAST-GOOD: `ms-trail-run.py --restore-plan {name}` "
                  f"prints the install ({last_good_path(name)}; LATEST is rewritten by the survey)")
            return 1
        ok, note = service_restart_check(name)
        print(("GREEN " if ok else "RED ") + note)
        if ok:
            freeze_env(name, py)
            st = service_env_state(name, py, True)
            lg = write_last_good(name, py, smoke_ok=True, service_state=st, source="service-check")
            print(f"LAST-GOOD {'written: ' + str(lg) if lg else 'not written: service ' + str((st or {}).get('verdict'))}")
        return 0 if ok else 1
    if args.rebuild:
        return rebuild_verb(args)
    if args.restore_plan:
        name = args.restore_plan.strip()
        if name not in VENVS:
            print(f"unknown env: {name!r}")
            return 2
        py = VENVS[name][0]
        lgp = last_good_path(name)
        if not lgp.is_file():
            print(f"no LAST-GOOD for {name} ({lgp}): nothing has passed smoke"
                  + (" + service" if name in SERVICE_ENVS else "") + " since LAST-GOOD shipped. "
                  "Do not restore from LATEST — the survey rewrites it every 2 h.")
            return 2
        now_text = _freeze_text(py)
        if now_text is None:
            print(f"cannot freeze {name} ({py})")
            return 2
        print("\n".join(restore_plan_lines(name, py, lgp.read_text(errors="replace"), now_text)))
        return 0
    if args.record_finding:
        try:
            record_finding(
                title=args.title,
                finding=args.reason,
                source=args.source,
                evidence=args.evidence,
            )
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(f"recorded finding {args.title!r} → {FINDINGS_PATH}")
        return 0

    if args.annotate:
        try:
            ok = annotate_decision(
                env=args.env,
                package=args.package,
                note=args.reason,
                source=args.source,
            )
        except (ValueError, LedgerUnreadable) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if not ok:
            print(
                f"ERROR: no ledger row for {args.env}/{args.package} — "
                "--annotate adds evidence to an EXISTING row; use "
                "--record-decision to create one.",
                file=sys.stderr,
            )
            return 2
        print(f"annotated {args.env}/{args.package} → {DECISIONS_PATH}")
        return 0

    if args.where:
        print(json.dumps({"state_dir": str(OUT_ROOT), "settings_file": str(_S.settings_file()),
                          "settings_read": _S.SETTINGS.get("_sources") or [],
                          "reports": str(REPORT_DIR), "ledger": str(DECISIONS_PATH)}, indent=1))
        return 0
    if args.register_env:
        row = register_env_row(args)
        where = _S.register_env(args.register_env, row, health=_register_health(args),
                                hindsight_env_file=args.hindsight_env_file or "")
        print(f"registered {args.register_env} in {where}: apply {row['apply']}"
              + (f", green after {row['promote']['after_clean_nights']} clean nights"
                 if row.get("promote") else ""))
        return 0
    if args.record_decision:
        if not (args.env and args.package and args.decision and args.reason):
            print(
                "ERROR: --record-decision needs --env --package --decision --reason",
                file=sys.stderr,
            )
            return 2
        try:
            unlock = None
            if args.unlock_disposition:
                unlock = {
                    "disposition": args.unlock_disposition,
                    "claim": args.unlock_claim,
                    "path_on": bool(args.unlock_path_on),
                    "flag": args.unlock_flag or "n/a",
                    "measure": args.unlock_measure or "none",
                    "kpi_id": args.unlock_kpi or None,
                }
            record_decision(
                env=args.env,
                package=args.package,
                decision=args.decision,
                reason=args.reason,
                current=args.current,
                target=args.target,
                source=args.source,
                actionable=bool(args.actionable),
                until=args.until or None,
                unlock=unlock,
            )
        except (ValueError, LedgerUnreadable) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(f"recorded {args.decision} {args.env}/{args.package} → {DECISIONS_PATH}")
        return 0

    if args.survey:
        only_s = {x.strip() for x in args.only.split(",") if x.strip()}
        drifted: dict[str, dict] = {}
        for name, (py, role, heavy) in VENVS.items():
            if only_s and name not in only_s:
                continue
            if not Path(py).is_file():
                continue
            d = survey_env(name, py)
            freeze_env(name, py)
            if d.get("ok") and d.get("had_manifest") and d.get("n"):
                drifted[name] = d
                try:
                    record_drift(name, py, d, source="survey",
                                 file_card=MS_SURVEY_CARDS and not args.no_kanban)
                except Exception as e:
                    log(f"drift {name}: record error {e!r}")
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        (MANIFEST_DIR / "_survey-LATEST.json").write_text(
            json.dumps(
                {"ts_local": dt.datetime.now().isoformat(timespec="seconds"),
                 "envs_surveyed": len(VENVS), "drifted": drifted,
                 "hermes_profiles": survey_hermes_profiles()},
                indent=2,
            ) + "\n"
        )
        os_update_snapshot()
        if drifted:
            log(f"survey: {len(drifted)} env(s) changed OUT OF BAND since last manifest: "
                + ", ".join(drifted))
        else:
            log(f"survey: all {len(VENVS)} envs match their manifests")
        return 0

    do_kanban = args.kanban and not args.no_kanban


    do_pending = (args.apply_pending or args.apply_green) and not args.no_apply_pending

    require_packaging()
    try:
        import packaging as _packaging

        _packaging_ver = getattr(_packaging, "__version__", "?")
    except Exception:
        _packaging_ver = "?"
    log(f"trail python={sys.executable} packaging={_packaging_ver}")

    day = dt.date.today().isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    only = {x.strip() for x in args.only.split(",") if x.strip()}


    adhoc = MS_ADHOC_REPORTS and not ((args.apply_green or not _S.upgrades_on()) and not only)
    ledger_unreadable = ""
    try:
        _load_decisions()
    except LedgerUnreadable as e:
        ledger_unreadable = str(e)
        do_pending = False
    env_exceptions: list[str] = []
    night_errors: list[dict] = []
    summary: dict = {}

    report: dict = {
        "schema": "ms_trail_run/v1",
        "day": day,
        "ts_local": dt.datetime.now().isoformat(timespec="seconds"),
        "trail_python": sys.executable,
        "packaging": _packaging_ver,
        "uv": UV or None,
        "package_tool": "uv" if UV else "pip",
        "health": {},
        "health_notes": {},
        "envs": {},
        "kanban_summary": None,
        "applied": {},
        "note": _T("report.note", manifests=MANIFEST_DIR, tool=("uv " + UV if UV else "pip")),
    }
    report["ledger_unreadable"] = ledger_unreadable or None
    report["adhoc"] = bool(adhoc)
    if ledger_unreadable:
        log(f"LEDGER UNREADABLE — no env applies tonight: {ledger_unreadable}")
    flags, notes = health_lanes()
    report["gpu_mode"] = occupancy_gpu_mode()
    report["health"] = flags
    report["health_notes"] = notes


    report["render"] = survey_render_tools(sota=not args.survey)
    report["os_update"] = os_update_snapshot()
    report["os_lane"] = ({"measured": True} if report["os_update"] else
                         {"measured": False, "why": "not measured (os_lane "
                          + str(_S.get("os_lane") or "auto") + ", no apt/dpkg here or the OS leg is off)"})
    live_users = live_venv_users()
    report["live_venv_users"] = live_users
    log(
        "live venvs: "
        + (", ".join(f"{n}[{','.join(u[:3])}]" for n, u in sorted(live_users.items()))
           or "(none)")
    )

    log(f"tool={report['package_tool']} uv={UV!r}")
    log(f"health {report['health']}")
    if notes:
        log(f"health_notes {notes}")

    apply_failures: list[dict] = []
    window_items: list[dict] = []
    kanban_go: dict[str, list[dict]] = {}
    kanban_land_ids: dict[str, list[str]] = {}




    board_ok = do_kanban or not MS_KANBAN_INGEST_FOLLOWS_FLAG
    if (do_pending or args.apply_green) and not ledger_unreadable and board_ok:
        kanban_go, kanban_land_ids = ingest_kanban_ms_go()
        if kanban_go:
            log(
                "kanban GO ingest "
                + ", ".join(
                    f"{e}:{len(v)}" for e, v in sorted(kanban_go.items())
                )
            )
        report["kanban_go"] = {
            e: [r.get("package") for r in v] for e, v in kanban_go.items()
        }
        report["kanban_land_ids"] = kanban_land_ids

    for name, (py, role, heavy) in VENVS.items():
        if only and name not in only:
            continue
        with _env_guard(name, apply_failures, env_exceptions, report):
            log(f"=== {name} ({role}) heavy={heavy} ===")
            drift = survey_env(name, py) if Path(py).is_file() else {"ok": False}
            man = freeze_env(name, py)
            od_raw = outdated(name, py) if Path(py).is_file() else []
            od = od_raw if od_raw is not None else []
            pins = collect_installed_constraints(py) if Path(py).is_file() else []
            classified = classify_outdated(od, pins, env=name, role=role)
            in_use = name in live_users
            report["envs"][name] = {
                "python": py,
                "role": role,
                "heavy": heavy,
                "in_use": in_use,
                "used_by": live_users.get(name) or [],
                "manifest": str(man) if man else None,
                "drift_since_last_manifest": drift,
                "outdated_n": len(od),
                "outdated_unknown": od_raw is None,
                "measured": od_raw is not None,
                "classified": {k: len(v) for k, v in classified.items()},

                "details": {
                    k: _cap_bucket(v, REPORT_DETAIL_CAP) for k, v in classified.items()
                },
                "smoke_baseline": smoke(name, py) if Path(py).is_file() else False,
                "unused": unused_packages(py) if Path(py).is_file() else None,
            }
            if roots_enabled(args.survey):
                report["envs"][name]["roots"] = env_roots_row(
                    name, py, classified, report["envs"][name].get("unused"))
            if name in SERVICE_ENVS and Path(py).is_file():
                report["envs"][name]["service"] = service_env_state(
                    name, py, report["envs"][name]["smoke_baseline"])
            if ledger_unreadable:

                report["applied"][name] = {"ok": False, "upgraded": [], "changed_n": 0,
                                           "skipped": "ledger_unreadable",
                                           "err": ledger_unreadable[:200]}
                continue
            pending = merge_window_rows(
                pending_apply_for_env(name),
                kanban_go.get(name) or [],
            )
            skip_nightly = {
                canon(str(x.get("package") or ""))
                for x in (classified.get("pinned") or []) + (classified.get("group-wait") or [])
            }



            refused_skip = skip_nightly_refused(
                name, (classified.get("green_candidate") or []) + pending
            )
            if refused_skip:
                log(f"{name}: skipping {len(refused_skip)} package(s) refused "
                    f">={MS_REFUSE_MAX}x at the same version: {sorted(refused_skip)[:6]}")
            skip_nightly |= refused_skip
            to_green = [
                row
                for row in drain_rows(
                    merge_window_rows(classified["green_candidate"], pending)
                )
                if canon(str(row.get("package") or "")) not in skip_nightly
            ]
            to_major = drain_pending_specialist(
                pending,
                skip=skip_nightly,
                max_n=MS_PENDING_MAX_PER_ENV,
            )
            to_apply = to_green
            for row in pending:
                window_items.append({
                    "env": name,
                    "package": row.get("package"),
                    "current": row.get("current"),
                    "target": row.get("latest") or row.get("target"),
                    "source": "pending_apply",
                    "live": bool(in_use),
                })

            apply_attempted = False
            cunv = ""
            batches: list[tuple[str, list[dict]]] = []
            if args.apply_green and to_green:
                batches.append(("green", to_green))
            if do_pending and to_major:
                batches.append(("pending-major", to_major))
            batches = hold_env_batches(name, batches, report)
            batches = smoke_gate_batches(name, batches, report)




            ro_canary = report_only_canary(name, report)
            if ro_canary is not None and not ro_canary.get("canary_ok"):
                apply_failures.append({"env": name, "reason": "canary_red_report_only",
                                       "err": ro_canary.get("err") or ""})
            gated = str((report["applied"].get(name) or {}).get("skipped") or "") in (
                "env_hold", "no_declared_smoke", "report_only_setting")
            svc_block = service_blocks_apply(report["envs"][name].get("service"))
            if batches and svc_block:
                st = report["envs"][name].get("service") or {}
                log(f"apply-green {name}: live service {svc_block} — not touching the env "
                    f"({st.get('smoke_err') or st.get('unit')})")
                report["applied"][name] = {"ok": False, "upgraded": [], "skipped": svc_block,
                                           "changed_n": 0, "err": st.get("smoke_err") or svc_block}
                apply_failures.append({"env": name, "reason": svc_block,
                                       "err": st.get("smoke_err") or f"{st.get('unit')} health down"})
                batches = []
            if batches:
                cok: bool | None = None
                cmsg = ""
                if name == CANARY_ENV:
                    cok, cmsg = canary_vllm()
                    log(f"canary live pre-apply ({occupancy_gpu_mode()}): "
                        f"{'GREEN' if cok else 'RED'} {cmsg}")
                    if cok and not MS_CANARY_SKIP_OK:
                        cunv = canary_05b_precheck()
                        if cunv:
                            log(f"canary 0.5B pre-apply: UNVERIFIED {cunv} — {name} waits for the window")
                plan = window_apply_plan(
                    name,
                    train_busy=TRAIN_BUSY.is_file(),
                    force_train=args.force_heavy,
                    canary_ok=cok,
                    canary_unverified=cunv,
                )
                if plan != "apply":
                    skipped = {
                        "skip_train_busy": "train-busy",
                        "skip_canary_red": "canary_red",
                        "skip_canary_unverified": "canary_unverified",
                    }.get(plan, plan)
                    log(f"apply-green {name}: defer to window — {skipped}")
                    report["applied"][name] = {
                        "ok": plan != "skip_canary_red",
                        "upgraded": [],
                        "skipped": skipped,
                        "changed_n": 0,
                    }
                    if plan == "skip_canary_unverified":
                        report["applied"][name]["canary"] = f"UNVERIFIED {cunv}"
                        report["applied"][name]["unverified"] = cunv
                    if plan == "skip_canary_red":
                        report["applied"][name]["canary"] = cmsg
                        report["applied"][name]["err"] = (
                            f"vLLM canary RED before apply: {cmsg}"
                        )
                        apply_failures.append(
                            {"env": name, "reason": "canary_red_pre", "err": cmsg}
                        )
                else:
                    apply_attempted = True
                    merged: dict = {
                        "ok": True,
                        "upgraded": [],
                        "failed": [],
                        "outcomes": [],
                        "pinned_n": len(classified.get("pinned") or []),
                        "group_wait_n": len(classified.get("group-wait") or []),
                    }
                    for label, rows in batches:
                        log(f"apply-green {name}: {len(rows)} {label}")
                        result = apply_green(name, py, rows)
                        result = service_verify_after_apply(name, py, result)
                        if result.get("unverified"):
                            merged["unverified"] = result["unverified"]
                            n_q = queue_greens_for_window(
                                name, rows, reason=f"canary unverified: {result['unverified']}")
                            log(f"apply-green {name}: UNVERIFIED {label} rolled back — "
                                f"{n_q} queued for the window")
                        mark_pending_landed(name, pending, result)
                        merged["upgraded"].extend(result.get("upgraded") or [])
                        merged["failed"].extend(result.get("failed") or [])
                        if not result.get("ok", True):
                            merged["ok"] = False
                            merged["err"] = result.get("err")
                        failed_names = {
                            canon(str(x.get("package") or x)) if isinstance(x, dict) else canon(str(x))
                            for x in (result.get("failed") or [])
                        }
                        rolled_back = bool(result.get("rollback"))
                        for row in rows:
                            pkg = canon(str(row.get("package") or ""))
                            if pkg not in failed_names:
                                continue
                            if rolled_back:





                                merged["outcomes"].append(
                                    {"package": pkg, "outcome": "rolled-back",
                                     "err": str(result.get("err") or "")[:200], "pass": label}
                                )
                                continue
                            err = ""
                            for fx in result.get("failed") or []:
                                if isinstance(fx, dict) and canon(str(fx.get("package") or "")) == pkg:
                                    err = str(fx.get("err") or "")
                                    break
                            el = err.lower()
                            oc = "build-fail" if ("failed to build" in el or "build backend" in el) else "refused"
                            merged["outcomes"].append(
                                {"package": pkg, "outcome": oc, "err": err[:200], "pass": label}
                            )



                            apply_failures.append(
                                {"env": name, "reason": f"{oc}:{pkg}", "package": pkg,
                                 "err": err[:200], "kind": "package"}
                            )



                            try:
                                n_try = record_refusal(
                                    name,
                                    pkg,
                                    target=str(row.get("latest") or row.get("target") or ""),
                                    current=str(row.get("current") or ""),
                                    outcome=oc,
                                    err=err,
                                )
                                if n_try >= MS_REFUSE_MAX:
                                    log(f"apply-green {name}: {pkg} refused {n_try}x "
                                        f"at the same version — parking it")
                            except Exception as e:
                                log(f"refusal record failed for {name}/{pkg}: {e!r}")
                    merged["changed_n"] = len(merged["upgraded"])
                    report["applied"][name] = merged
                    if board_ok:
                        complete_ms_land_cards(
                            name, kanban_land_ids.get(name) or [], merged
                        )
                    if not merged.get("ok"):
                        apply_failures.append(
                            {
                                "env": name,
                                "packages": [x["package"] for x in to_green + to_major],
                                "reason": "apply_green_failed",
                                "err": merged.get("err"),
                            }
                        )

            elif args.apply_green and not gated:
                report["applied"][name] = {
                    "ok": True,
                    "upgraded": [],
                    "changed_n": 0,
                    "pinned_n": len(classified.get("pinned") or []),
                    "group_wait_n": len(classified.get("group-wait") or []),
                }



            if to_apply and not apply_attempted and not gated:
                n = queue_greens_for_window(
                    name,
                    to_apply,
                    reason=(f"canary unverified: {cunv}" if cunv
                            else "green scheduled for next M&S apply-green window"),
                )
                log(
                    f"window {name}: scheduled {n} greens "
                    f"(next --apply-green, not skipped)"
                )
                for g in to_apply:
                    window_items.append({
                        "env": name,
                        "package": g.get("package"),
                        "current": g.get("current"),
                        "target": g.get("latest") or g.get("target"),
                        "source": "green_window",
                        "live": bool(in_use),
                    })

    with _night_guard("drift", night_errors):
        report["drift"] = nightly_record_drift(report, file_card=do_kanban)
    with _night_guard("last good", night_errors):
        report["last_good"] = nightly_last_good(report)
    with _night_guard("maintenance window", night_errors):
        report["maintenance_window"] = str(write_maintenance_window(window_items, day))
    env_level = [f for f in apply_failures if f.get("kind") != "package"]
    if env_level or ledger_unreadable:
        bits = [
            f"{f.get('env')}:{f.get('reason')}" for f in env_level[:6]
        ] + (["ledger_unreadable"] if ledger_unreadable else [])
        with _night_guard("page", night_errors):
            notify_user(
                "M&S apply-green failed ("
                + ", ".join(bits)
                + _T("notify.apply_failed_tail")
            )
    with _night_guard("hermes checks", night_errors):
        report["hermes_profiles"] = survey_hermes_profiles()
        report["hermes"] = {
            "doctor": run_hermes_doctor(),
            "security": run_hermes_security(),
            "profiles": report["hermes_profiles"],
            "tree": run_hermes_tree_verify(),
        }
        sec = report["hermes"]["security"] or {}
        if hermes_security_is_new(sec) and (sec.get("high_ids") or []):
            notify_user(
                "Hermes OSV has new HIGH/CRITICAL: "
                + ", ".join(str(x) for x in (sec.get("high_ids") or [])[:8])
                + _T("notify.osv_tail")
            )
        if sec.get("measured") is not False:
            rotate_hermes_security_prev()
    with _night_guard("summary + trend", night_errors):
        summary = summarize_for_kanban(
            report["envs"], apply_failures,
            ledger=EMPTY_LEDGER if ledger_unreadable else None,
        )
        summary["ledger_unreadable"] = ledger_unreadable or None
        summary["hermes"] = report.get("hermes") or {}
        summary["os_update"] = report.get("os_update") or {}
        summary["trend"] = append_trend(
            summary, report.get("applied") or {}, summary["os_update"], day,
            write=not adhoc,
        )
        report["trend"] = summary["trend"]
    report["kanban_summary"] = {
        "totals": summary.get("totals"),
        "env_count": len(summary.get("env_summaries") or []),
        "parked": summary.get("parked_envs") or [],
        "top_n": len(summary.get("top_packages") or []),
        "render_behind": bool(
            ((report.get("render") or {}).get("sota") or {}).get("behind")
        ),
        "actionable_risk": (summary.get("totals") or {}).get("actionable_risk"),
    }

    if do_kanban:
        with _night_guard("kanban summary card", night_errors):
            report["kanban_path"] = str(
                write_kanban_stub(summary, day, report["applied"]) or ""
            )
        listing = ""
        try:
            lr = subprocess.run(
                [*_kanban_argv(), "kanban", "list"],
                capture_output=True, text=True, timeout=30,
            )
            listing = lr.stdout or ""
        except Exception as e:
            log(f"kanban list for hermes cards failed {e!r}")
        cards: list[dict] = []
        with _night_guard("kanban board read", night_errors):
            cards = kanban_board_cards(listing)
        with _night_guard("kanban hermes cards", night_errors):
            file_hermes_issue_cards(report, day, listing, cards=cards)
        if not ledger_unreadable:
            with _night_guard("kanban mitigation cards", night_errors):
                file_mitigation_cards(report, day)
        with _night_guard("kanban render sota card", night_errors):
            file_render_sota_card(report, day, listing, cards=cards)
        with _night_guard("kanban diagnose cards", night_errors):
            file_diagnose_cards(report, day, listing, cards=cards)
        with _night_guard("kanban retire", night_errors):
            retire_spent_ms_cards(report, day, cards=cards)
    if not only:
        with _night_guard("live manifest", night_errors):
            write_live_manifest(report, day)

    report["env_exceptions"] = env_exceptions
    report["night_errors"] = night_errors


    report["apply_tally"] = apply_tally(report.get("applied") or {})
    try:
        import env_rebuild as _rb
        report["rebuilds"] = {"rows": _rb.rebuild_rows(), "skipped": dict(REBUILD_SKIPPED)}
    except ImportError:
        report["rebuilds"] = {"skipped": "rebuild is not part of this install"}
    except Exception as e:
        report["rebuilds"] = {"error": repr(e)[:200]}
    report["dropped"] = dict(DROPPED)
    report["upgrades"] = _S.upgrades_on()
    try:
        write_roots_report(report, day, adhoc)
    except Exception as e:
        log(f"roots report: {e!r}")
        report["roots"] = {"error": repr(e)[:300]}
    try:
        report["promotions"] = (load_promotions() if adhoc
                                else update_promotions(report, day)) if MS_APPLY_SETTING else {}
    except Exception as e:
        log(f"promotions: {e!r}")
        report["promotions"] = {"error": repr(e)[:200]}
    if not adhoc:

        try:
            import ms_silent_skips as _sk
            _changes = _sk.record_ms_night(
                report, day, source=str(REPORT_DIR / f"ms-trail-{day}.json"), findings_path=FINDINGS_PATH)
            report["silent_skips"] = _sk.summary(_changes)
            for c in report["silent_skips"]:
                log(f"silent skip {c['action']}: {c['key']} ({c['nights']} nights)")

            cards = canary_skip_cards(_changes, day)
            if cards:
                report["canary_skip_cards"] = cards
        except Exception as e:
            log(f"silent skips: {e!r}")
            report["silent_skips"] = {"error": repr(e)[:200]}
    if adhoc:
        stamp = dt.datetime.now().strftime("%H%M%S")
        out_path = REPORT_DIR / f"ms-trail-{day}-adhoc-{stamp}.json"
        latest = out_path
        out_path.write_text(json.dumps(report, indent=2))
    else:
        out_path = REPORT_DIR / f"ms-trail-{day}.json"
        latest = REPORT_DIR / "ms-trail-LATEST.json"
        out_path.write_text(json.dumps(report, indent=2))
        latest.write_text(json.dumps(report, indent=2))

    md = REPORT_DIR / f"ms-trail-{day}.md"
    lines = [
        f"# M&S trail run — {day}",
        "",
        f"Generated `{report['ts_local']}` · tool **{report['package_tool']}**"
        + (f" (`{UV}`)" if UV else ""),
        "",
        "## Health",
        "",
    ]
    _rt = (report.get("roots") or {})
    if _rt.get("totals"):
        _t = _rt["totals"]
        lines[-2:-2] = [
            "## Roots (REPORT ONLY — changes nothing tonight)",
            "",
            f"{_t['envs']} envs · {_t['orphans_installed']} orphan packages ({_t['orphan_mb']} MB) · "
            f"{_t['drift']} root-pin drift rows · group-wait {_t['old'].get('group-wait', 0)} → "
            f"{_t['new'].get('group-wait', 0)} true, {_t['new'].get('app-pinned', 0)} app-pinned, "
            f"{_t['new'].get('orphan', 0)} outdated orphans. Full page: `{Path(_rt['md']).name}`.",
            "",
        ]
    elif _rt.get("error"):
        lines[-2:-2] = [f"## Roots\n\nroots report failed: {_rt['error']}", ""]
    _rbr = (report.get("rebuilds") or {}).get("rows") or []
    if _rbr:
        lines[-2:-2] = ["## Rebuilds (Stage D)", ""] + [
            f"- `{r['dir']}` {r['role']} build of {r['of']} {r['version']}"
            + (f" · keep until {r['keep_until']}" if r.get("keep_until") else "")
            + (" · **past keep_until: remove by hand**" if r.get("due") else "")
            for r in _rbr] + [""]
    notes = report.get("health_notes") or {}
    mode = str(report.get("gpu_mode") or occupancy_gpu_mode())
    for k, v in report["health"].items():
        extra = f" — {notes[k]}" if notes.get(k) else ""
        tag = health_tag(k, bool(v), mode=mode, note=notes.get(k, ""))
        lines.append(f"- **{k}**: {tag}{extra}")
    rend = report.get("render") or {}
    if rend:
        sota = rend.get("sota") or {}
        recs = rend.get("recipes") or []
        wired_n = sum(1 for r in recs if r.get("wired"))
        lines += [
            "",
            "## Render tools",
            "",
            f"- git `{ (rend.get('git') or {}).get('describe') or '?' }` · "
            f"origin `{sota.get('origin_latest') or 'unread'}` · "
            f"behind={bool(sota.get('behind'))}",
            f"- recipes wired {wired_n}/{len(recs)}",
        ]
    hermes = report.get("hermes") or {}
    if hermes:
        doc = hermes.get("doctor") or {}
        sec = hermes.get("security") or {}
        lines += [
            "",
            "## Hermes doctor / security",
            "",
            doctor_manifest_line(doc),
            f"- security: {sec.get('finding_count')} findings "
            f"{sec.get('by_severity') or {}}",
        ]
    profs = report.get("hermes_profiles") or {}
    prow = profs.get("profiles") or []
    if prow:
        lines += ["", "## Hermes profiles", ""]
        for r in prow:
            tag = "BELOW-FLOOR" if r.get("below_floor") else "ok"
            worker = "worker" if r.get("kanban_worker") else "not a kanban worker"
            lines.append(
                f"- **{r.get('name')}**: dist `{r.get('distribution')}` · "
                f"config {r.get('config_version')} · {tag} · {worker}"
                + (f" — {r['note']}" if r.get("note") else "")
            )
        if profs.get("below_floor"):
            lines.append(
                "- restore `--apply` lifts below-floor stamps and runs "
                "`hermes config migrate`"
            )
    _tally = apply_tally(report.get("applied") or {})
    lines += ["", "## Envs", ""]
    for name, info in report["envs"].items():
        live = (
            "YES · " + ", ".join(info.get("used_by") or [])
            if info.get("in_use") else "no process (occupancy — still in apply set)"
        )
        lines.append(
            f"### {name} ({info['role']})"
            f"{' · HEAVY' if info['heavy'] else ''}"
        )
        lines.append(f"- live: {live}")
        lines.append(f"- manifest: `{info['manifest']}`")
        lines.append(f"- outdated: {info['outdated_n']} · {info['classified']}")
        lines.append(f"- baseline smoke: {'GREEN' if info['smoke_baseline'] else 'RED'}")
        _al = apply_env_line(_tally["envs"].get(name) or {})
        if _al:
            lines.append(_al)
        lines.append("")
    tot = summary.get("totals") or {}






    heavy_names = [n for n, i in report["envs"].items() if i.get("heavy")]
    heavy_outdated = sum(
        i.get("outdated_n", 0) for i in report["envs"].values() if i.get("heavy")
    )
    applied_n = sum(
        int((a or {}).get("changed_n") or 0)
        for a in (report.get("applied") or {}).values()
    )
    pend: dict[str, int] = {}
    _led: list = []
    try:
        _led = json.loads(DECISIONS_PATH.read_text()).get("decisions") or []
        for _r in _led:
            if not isinstance(_r, dict):
                continue
            if str(_r.get("decision") or "").lower() == "pending_apply":
                pend[str(_r.get("env") or "?")] = pend.get(str(_r.get("env") or "?"), 0) + 1
    except Exception:
        pend = {}
        _led = []
    pend_total = sum(pend.values())




    _hd: dict[str, int] = {}
    for _r in (_led if isinstance(_led, list) else []):
        if str(_r.get("env") or "") in heavy_names:
            _hd[str(_r.get("decision") or "?").lower()] = (
                _hd.get(str(_r.get("decision") or "?").lower(), 0) + 1
            )
    _pinned = _hd.get("hold", 0) + _hd.get("pin", 0) + _hd.get("never", 0) + _hd.get("wontfix", 0)
    lines += [
        "## What moved, and what did not",
        "",
        f"- **applied this run: {applied_n}**"
        + (f" (landed {_tally['landed']} of {_tally['attempted']} attempted; "
           f"**{_tally['rolled']} rolled back**: "
           + "; ".join(f"{n} {len(t['rolled'])} — {t['cause']}"
                       for n, t in _tally["envs"].items() if t["rolled"])
           + ")" if _tally["rolled"] else ""),
        f"- outdated sum: **{tot.get('outdated_packages_sum', 0)}** across envs; "
        f"**{heavy_outdated}** of that is in HEAVY envs "
        f"({', '.join(heavy_names) or 'none'}).",
        _T("md.canary_line", canary_env=CANARY_ENV,
           canary=(f", the canary `{CANARY_ENV}`" if CANARY_ENV else "")),
        f"- **{_pinned} heavy packages are PINNED by researched decision** — held "
        f"deliberately because they cannot land without damage. Reasons are in "
        f"`decisions.json`; do not re-research them, cite them.",
        f"- auto-apply is `green_candidate` in **every** env. `pinned` is at an "
        f"immovable pinner (not a failed install). risk/never are not nightly pip. "
        f"`{_hd.get('applied', 0)}` heavy ledger rows already marked applied.",
        "",
    ]
    if ALIAS_ENVS:
        lines += [
            "- aliases (symlinks, counted ONCE): "
            + ", ".join(f"`{a}` → `{r}`" for a, r in sorted(ALIAS_ENVS.items()))
            + ".",
            "",
        ]
    if pend_total:
        lines += [
            f"### Maintenance window queue: {pend_total} `pending_apply`",
            "",
            "Kanban is not the queue. These are **majors** (`pending_apply`). "
            f"File: `{WINDOW_PATH}`. Pip-green is not this list. "
            "hold/wontfix never land. Train-busy defers GPU-shared. "
            "Canary RED defers the vllm env until the open server answers.",
            "",
        ]
        for _e, _n in sorted(pend.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{_e}`: {_n}")
        lines.append("")
    lines += [
        _T("md.kanban_heading"),
        "",
        f"- format: **summary** (was one-row-per-pkg flood)",
        f"- risk/never/green rows: {tot.get('risk_rows')}/{tot.get('never_rows')}/{tot.get('green_rows')}",
        f"- top packages listed: **{len(summary.get('top_packages') or [])}** (cap {KANBAN_TOP_N})",
        f"- queue file: `{report.get('kanban_path')}`",
        "",
    ]
    if summary.get("top_packages"):
        lines.append("### Top packages (sample)")
        lines.append("")
        for p in summary["top_packages"][:15]:
            lines.append(
                f"- `{p.get('package')}` {p.get('current')}→{p.get('latest')} "
                f"({p.get('bucket')}, envs_hit={p.get('envs_hit')})"
            )
        lines.append("")
    md_body = "\n".join(lines)
    if adhoc:
        log(f"report (ad hoc — dated report, LATEST and trend untouched) → {out_path}")
    else:
        md.write_text(md_body)

        latest_md = REPORT_DIR / "ms-trail-LATEST.md"
        latest_md.write_text(md_body)
        log(f"report → {out_path} (+ {latest.name})")
        log(f"md → {md} (+ {latest_md.name})")
    if env_exceptions or night_errors or ledger_unreadable:
        log(f"exit 3: env exceptions {env_exceptions} · phase errors "
            f"{[x['phase'] for x in night_errors]} · ledger unreadable {bool(ledger_unreadable)}")
        return 3

    h = report["health"]
    if any(not h.get(lane) for lane in (_S.get("health.critical") or [])):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
