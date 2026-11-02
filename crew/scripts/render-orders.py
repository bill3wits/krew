#!/usr/bin/env python3
"""render-orders — the written list of renders (lib/render_orders.py, heavy-render H1).

  render-orders add --workflow flux2-turbo-txt2img --set 12.text="a red fox" --seed 7 --by crissa
  render-orders list [--state pending|running|done|failed]
  render-orders show <id>
  render-orders cancel <id> --by <who>
  render-orders plan            # the batches a runner would take, by model family (touches nothing)

Adding an order renders nothing: the heavy-render runner (H2) empties the list.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
# krew: the queue module also ships in the krew-render plugin; a crew without the lib copy finds it there
for _d in (os.environ.get("KREW_RENDER_PLUGIN", ""),
           os.path.join(os.environ.get("HERMES_HOME") or str(KH.hermes_home()), "plugins", "krew-render"),
           str(Path(__file__).resolve().parents[2] / "plugins" / "krew-render")):
    if _d and os.path.isfile(os.path.join(_d, "render_orders.py")) and not (Path(__file__).resolve().parent.parent / "lib" / "render_orders.py").is_file():
        sys.path.insert(0, _d)
        break
import render_orders as ro  # noqa: E402


def _value(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="render-orders", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--workflow", required=True, help="a saved workflow name or an API-graph path")
    a.add_argument("--set", action="append", default=[], metavar="NODE.INPUT=VALUE")
    a.add_argument("--seed", type=int)
    a.add_argument("--by", required=True, help="who asked for it")
    a.add_argument("--card", default="")
    a.add_argument("--priority", type=int, default=0)
    a.add_argument("--out-dir", default="")
    ls = sub.add_parser("list")
    ls.add_argument("--state", choices=ro.STATES)
    sh = sub.add_parser("show")
    sh.add_argument("id")
    c = sub.add_parser("cancel")
    c.add_argument("id")
    c.add_argument("--by", required=True)
    sub.add_parser("plan")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            ov = {}
            for s in args.set:
                k, eq, v = s.partition("=")
                if not eq:
                    raise ro.OrderError(f"--set {s!r}: use NODE.INPUT=VALUE")
                ov[k] = _value(v)
            o = ro.add(args.workflow, overrides=ov, seed=args.seed, by=args.by, card=args.card,
                       priority=args.priority, out_dir=args.out_dir)
            print(f"{o['id']}  pending  {o['workflow']}  family={o['family']}  need={o['need_gib']} GiB")
        elif args.cmd == "list":
            for o in ro.orders(args.state):
                print(f"{o['id']}  {o['state']:8} {o['workflow']:28} {o['family']:12} "
                      f"{o['need_gib']:6} GiB  by {o['requested_by']}" + (f"  card {o['card']}" if o.get("card") else ""))
        elif args.cmd == "show":
            o = ro.show(args.id)
            o = {k: v for k, v in o.items() if k != "graph"}
            print(json.dumps(o, indent=1))
        elif args.cmd == "cancel":
            o = ro.cancel(args.id, by=args.by)
            print(f"{o['id']}  cancelled")
        else:
            for b in ro.plan():
                print(f"{b['family']:12} {b['n']:3} order(s)  union {b['need_gib']:6} GiB  "
                      f"priority {b['top_priority']}  oldest {b['oldest']}")
    except ro.OrderError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
