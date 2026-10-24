"""Read crew/ops vLLM journal + tok/s. Used by serve-vllm-log.py."""
from __future__ import annotations

import os
import re
import subprocess
import urllib.request

_ENGINE = re.compile(
    r"Avg prompt throughput:\s*([0-9.]+)\s*tokens/s,\s*"
    r"Avg generation throughput:\s*([0-9.]+)\s*tokens/s"
    r"(?:,\s*Running:\s*(\d+)\s*reqs,\s*Waiting:\s*(\d+)\s*reqs)?"
    r"(?:,\s*GPU KV cache usage:\s*([0-9.]+)%)?"
    r"(?:,\s*Prefix cache hit rate:\s*([0-9.]+)%)?"
)

LANES = {
    "crew": {"unit": os.environ.get("KREW_CREW_ENGINE_UNIT") or "crew.service",
             "metrics": os.environ.get("KREW_CREW_METRICS") or "http://127.0.0.1:8000/metrics"},
    "ops": {"unit": os.environ.get("KREW_OPS_ENGINE_UNIT") or "ops.service",
            "metrics": os.environ.get("KREW_OPS_METRICS") or "http://127.0.0.1:8000/metrics"},
}


def _journal(args: list[str]) -> list[str]:
    p = subprocess.run(
        ["journalctl", "--user", "--no-pager", "--output=short-iso", *args],
        capture_output=True,
        text=True,
        timeout=8,
    )
    text = p.stdout or p.stderr or ""
    return [ln for ln in text.splitlines() if ln.strip()]


def read_journal(unit: str, n: int = 120) -> list[str]:
    n = max(20, min(int(n), 400))
    return _journal(["-u", unit, "-n", str(n)])


def read_engine_lines(unit: str, n: int = 8) -> list[str]:
    """Last Engine tok-counter lines, even if the tail is only HTTP access."""
    return _journal(["-u", unit, "-g", "Avg prompt throughput", "-n", str(max(1, min(n, 20)))])


def parse_engine_line(line: str) -> dict | None:
    """vLLM loggers.py: Avg prompt / generation throughput (the tok counter)."""
    m = _ENGINE.search(line)
    if not m:
        return None
    out: dict = {
        "prompt_tok_s": float(m.group(1)),
        "gen_tok_s": float(m.group(2)),
        "line": line.strip(),
    }
    if m.group(3) is not None:
        out["running"] = int(m.group(3))
        out["waiting"] = int(m.group(4) or 0)
    if m.group(5) is not None:
        out["kv_pct"] = float(m.group(5))
    if m.group(6) is not None:
        out["prefix_hit_pct"] = float(m.group(6))
    return out


def latest_engine(lines: list[str]) -> dict | None:
    for ln in reversed(lines):
        rec = parse_engine_line(ln)
        if rec:
            return rec
    return None


def parse_metric_counters(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip() or " " not in line:
            continue
        if 'le="' in line:
            continue
        left, val_s = line.rsplit(" ", 1)
        try:
            val = float(val_s)
        except ValueError:
            continue
        name = left.split("{", 1)[0]
        if name in (
            "vllm:prompt_tokens_total",
            "vllm:generation_tokens_total",
            "vllm:prompt_tokens_cached_total",
            "vllm:num_requests_running",
            "vllm:num_requests_waiting",
            "vllm:spec_decode_num_accepted_tokens_total",
            "vllm:spec_decode_num_draft_tokens_total",
        ):
            if "{" in left and name.endswith("_total"):
                out[name] = out.get(name, 0.0) + val
            else:
                out[name] = val
    return out


def fetch_metrics(url: str) -> dict[str, float]:
    raw = urllib.request.urlopen(url, timeout=3).read().decode()
    return parse_metric_counters(raw)


def snapshot(lane: str = "crew", n: int = 120) -> dict:
    spec = LANES.get(lane) or LANES["crew"]
    lines = read_journal(spec["unit"], n)
    engine_lines = read_engine_lines(spec["unit"], 8)
    mets: dict[str, float] = {}
    err = None
    try:
        mets = fetch_metrics(spec["metrics"])
    except Exception:
        err = "metrics-down"
    return {
        "lane": lane if lane in LANES else "crew",
        "unit": spec["unit"],
        "metrics_url": spec["metrics"],
        "lines": lines,
        "metrics": mets,
        "engine": latest_engine(engine_lines or lines),
        "error": err,
        "log_path": f"journalctl --user -u {spec['unit']}",
    }
