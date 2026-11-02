#!/usr/bin/env python3
"""krew: ops-lane-probe.py (lifted from the reference install; see crew/README.md)."""





















from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request

SYSTEM = ("You are Crissa, the krew assistant. You rule the kanban board: read a card before you decide, "
          "and answer the user in one or two short sentences.")
TOOLS = [
    {"type": "function", "function": {
        "name": "kanban_show", "description": "Read one kanban card by id.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {
        "name": "kanban_comment", "description": "Write a comment on a kanban card.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}, "text": {"type": "string"}},
                       "required": ["task_id", "text"]}}},
    {"type": "function", "function": {
        "name": "krew_status", "description": "Read the krew status (mode, lanes, holds).",
        "parameters": {"type": "object", "properties": {}}}},
]
TURN = [{"role": "system", "content": SYSTEM},
        {"role": "user", "content": "Card t_4c1fdafa has been blocked since last night. Look at it and tell me why."}]
THINK = [{"role": "user", "content": "A board has 12 open cards, 5 are parked and 2 of the parked ones wait on a "
                                     "finished card. How many cards can run now? Answer with the number and one reason."}]
SEED = 20260930


def _req(base: str, body: dict, timeout: float = 180.0):
    r = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=timeout)


def ask(base: str, model: str, messages: list, *, max_tokens: int, thinking: bool | None = None, tools=None) -> dict:
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": SEED}
    if thinking is not None:
        body["chat_template_kwargs"] = {"enable_thinking": thinking}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    t0 = time.time()
    d = json.load(_req(base, body))
    m = d["choices"][0]["message"]
    return {"s": round(time.time() - t0, 3), "finish": d["choices"][0].get("finish_reason"),
            "content": m.get("content") or "", "reasoning": m.get("reasoning") or m.get("reasoning_content") or "",
            "tool_calls": [{"name": c["function"]["name"], "arguments": _args(c["function"]["arguments"])}
                           for c in m.get("tool_calls") or []],
            "completion_tokens": (d.get("usage") or {}).get("completion_tokens")}


def _args(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {"_unparsed": raw}


def stream(base: str, model: str, messages: list, *, max_tokens: int, thinking: bool | None = None, tools=None) -> dict:
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": SEED,
            "stream": True, "stream_options": {"include_usage": True}}
    if thinking is not None:
        body["chat_template_kwargs"] = {"enable_thinking": thinking}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    t0 = time.time()
    first = first_any = None
    content, reasoning, calls, finish, usage = "", "", {}, None, {}
    for raw in _req(base, body):
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:") or line.endswith("[DONE]"):
            continue
        d = json.loads(line[5:])
        usage = d.get("usage") or usage
        for ch in d.get("choices") or []:
            delta = ch.get("delta") or {}
            finish = ch.get("finish_reason") or finish
            piece_r = delta.get("reasoning") or delta.get("reasoning_content") or ""
            piece_c = delta.get("content") or ""
            if (piece_r or piece_c or delta.get("tool_calls")) and first_any is None:
                first_any = time.time() - t0
            if piece_c and first is None:
                first = time.time() - t0
            content += piece_c
            reasoning += piece_r
            for c in delta.get("tool_calls") or []:
                slot = calls.setdefault(c.get("index", 0), {"name": "", "arguments": ""})
                fn = c.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
    dt = time.time() - t0
    n = usage.get("completion_tokens")
    return {"s": round(dt, 3), "ttft_s": None if first is None else round(first, 3),
            "first_any_s": None if first_any is None else round(first_any, 3), "finish": finish,
            "content": content, "reasoning": reasoning,
            "tool_calls": [{"name": v["name"], "arguments": _args(v["arguments"])} for _, v in sorted(calls.items())],
            "completion_tokens": n,
            "tok_s": round(n / (dt - (first_any or 0)), 1) if n and dt > (first_any or 0) else None}


def metrics(base: str) -> dict:
    out: dict = {}
    try:
        text = urllib.request.urlopen(base + "/metrics", timeout=10).read().decode()
    except Exception as e:  # noqa: BLE001 - a lane with no /metrics still answers the rest
        return {"error": repr(e)[:120]}
    for line in text.splitlines():
        for key in ("spec_decode_num_draft_tokens_total", "spec_decode_num_accepted_tokens_total",
                    "spec_decode_num_drafts_total", "generation_tokens_total"):
            if line.startswith("vllm:" + key + "{"):
                out[key] = out.get(key, 0.0) + float(line.rsplit(" ", 1)[1])
    return out


def version(base: str) -> str:
    try:
        return json.load(urllib.request.urlopen(base + "/version", timeout=5)).get("version", "?")
    except Exception:  # noqa: BLE001
        return "?"


def run(base: str, model: str) -> dict:
    out = {"base": base, "model": model, "engine": version(base), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    m0 = metrics(base)
    ask(base, model, [{"role": "user", "content": "Reply with the single word pong."}], max_tokens=8)  # warm
    tt = [stream(base, model, [{"role": "user", "content": "In one sentence, what does a kanban board track?"}],
                 max_tokens=60) for _ in range(5)]
    out["ttft"] = {"runs_s": [t["ttft_s"] for t in tt],
                   "median_s": statistics.median([t["ttft_s"] for t in tt if t["ttft_s"] is not None] or [0]),
                   "content": tt[0]["content"]}
    ss = [stream(base, model, [{"role": "user", "content": "Describe in detail how a small team uses a kanban "
                                                           "board through one working week."}], max_tokens=300)
          for _ in range(3)]
    out["stream_300"] = {"tok_s": [s["tok_s"] for s in ss],
                         "median_tok_s": statistics.median([s["tok_s"] for s in ss if s["tok_s"]] or [0]),
                         "tokens": [s["completion_tokens"] for s in ss]}
    out["tool_plain"] = ask(base, model, TURN, max_tokens=300, tools=TOOLS)
    out["tool_think"] = ask(base, model, TURN, max_tokens=1200, thinking=True, tools=TOOLS)
    out["tool_stream"] = stream(base, model, TURN, max_tokens=1200, thinking=True, tools=TOOLS)
    out["think_plain"] = ask(base, model, THINK, max_tokens=1200, thinking=True)
    m1 = metrics(base)
    d = {k: m1.get(k, 0) - m0.get(k, 0) for k in m1 if k in m0}
    drafted = d.get("spec_decode_num_draft_tokens_total") or 0
    out["mtp"] = {"drafted": drafted, "accepted": d.get("spec_decode_num_accepted_tokens_total") or 0,
                  "acceptance": round((d.get("spec_decode_num_accepted_tokens_total") or 0) / drafted, 3) if drafted else None,
                  "generated": d.get("generation_tokens_total"),
                  "note": "counters are the lane's: another caller during the probe is counted too"}
    return out


def shape(r: dict) -> dict:
    """How a reply PARSED - what a caller's code branches on - not what it said."""
    return {"finish": r.get("finish"), "has_content": bool((r.get("content") or "").strip()),
            "has_reasoning": bool((r.get("reasoning") or "").strip()),
            "tool_calls": [(c["name"], json.dumps(c["arguments"], sort_keys=True)) for c in r.get("tool_calls") or []],
            "markup_in_content": any(t in (r.get("content") or "") for t in
                                     ("<think>", "</think>", "<tool_call>", "</tool_call>", "<function="))}


def compare(a: dict, b: dict, min_speed: float = 0.8) -> tuple[bool, list[str]]:
    lines, ok = [f"baseline {a.get('engine')} ({a.get('ts')})  vs  candidate {b.get('engine')} ({b.get('ts')})"], True
    for case in ("tool_plain", "tool_think", "tool_stream", "think_plain"):
        sa, sb = shape(a[case]), shape(b[case])
        same = sa == sb and not sb["markup_in_content"]
        ok &= same
        lines.append(f"  {case:12s} {'same parse' if same else 'PARSES DIFFERENTLY'}: {sb if same else (sa, sb)}")
    for label, key, sub, better in (("first token", "ttft", "median_s", "lower"),
                                    ("one stream", "stream_300", "median_tok_s", "higher")):
        va, vb = a[key][sub], b[key][sub]
        ratio = (va / vb if better == "lower" else vb / va) if va and vb else 0
        good = ratio >= min_speed
        ok &= good
        unit = "s" if better == "lower" else "tok/s"
        lines.append(f"  {label:12s} {va} -> {vb} {unit}  (x{ratio:.2f} of baseline{'' if good else ', BELOW the floor'})")
    ma, mb = a["mtp"], b["mtp"]
    lines.append(f"  MTP          acceptance {ma['acceptance']} -> {mb['acceptance']} "
                 f"(drafted {ma['drafted']:.0f} -> {mb['drafted']:.0f})")
    if ma["drafted"] and not mb["drafted"]:
        ok = False
        lines.append("  MTP          the candidate drafted NOTHING: speculative decoding is not running")
    lines.append("PASS" if ok else "FAIL")
    return ok, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", default="http://127.0.0.1:8000")
    r.add_argument("--model", default="ops")
    r.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.add_argument("--min-speed", type=float, default=0.8)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        out = run(a.base, a.model)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        for case in ("tool_plain", "tool_think", "tool_stream", "think_plain"):
            print(f"{case:12s} {shape(out[case])}")
        print(f"first token  {out['ttft']['median_s']} s   one stream {out['stream_300']['median_tok_s']} tok/s   "
              f"MTP {out['mtp']['acceptance']} of {out['mtp']['drafted']:.0f} drafted   engine {out['engine']}")
        return 0
    ok, lines = compare(json.load(open(a.baseline)), json.load(open(a.candidate)), a.min_speed)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
