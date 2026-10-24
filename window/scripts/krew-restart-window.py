#!/usr/bin/env python3
"""krew-restart-window — Crissa's restart window (lib/restart_window.py).

  tick              every 5 min (krew-restart-window.timer): facts → decide → act
  facts             print the facts and the decision, change nothing
  execute --plan F  the detached executor (systemd-run): restarts in tier order,
                    post-checks each, restarts Crissa last; or reboots the host

The judgement is Crissa's (her ruling on the card); the vetoes are the law's
(lib/restart_window.vetoes/plan); this script only does what the two allow.

krew: the window is `off_hours` in $KREW_HOME/krew.yaml; the units, tiers, rulers and probes come from
$KREW_HOME/restart-window.json (shape and the reference install's values: config/restart-window.example.json),
filled from the one services list ($KREW_HOME/services.json) where that file is empty. OFF until `enabled: true`
there; while off, `tick` and `facts` log one line saying so and do nothing else (the owed list is kept).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


import krew_hours
import restart_window as rw
import text_cut

HERMES = os.environ.get("KREW_HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
PROFILE = os.environ.get("KREW_RESTART_WINDOW_PROFILE", "ada")
GPU_MODE = rw._KWS.path((rw.RW.get("occupancy") or {}).get("mode_file") or "", unset="occupancy.mode_file")
GPU_PENDING = rw._KWS.path((rw.RW.get("occupancy") or {}).get("pending_file") or "", unset="occupancy.pending_file")
LOG = rw._KH / "logs" / "restart-window.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def kanban(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run([HERMES, "-p", PROFILE, "kanban", *args], capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


# ── facts ─────────────────────────────────────────────────────────────────
def kanban_counts() -> dict:
    rc, out = kanban("list", "--json")
    try:
        rows = json.loads(out) if rc == 0 else []
    except json.JSONDecodeError:
        rows = []
    st = [str(r.get("status") or "") for r in rows if isinstance(r, dict)]
    return {"running": st.count("running"), "ready": st.count("ready"), "measured": rc == 0}


def busy_reason(unit: str | None = None) -> str:
    """Why a restart of `unit` must hold now ('' = nothing in flight): rw.busy_now, the one rule and its readers."""
    return rw.busy_now(unit)


def user_busy() -> bool:
    why = busy_reason()
    if why:
        log(f"user busy: {why}")
    return bool(why)


def nightly_active() -> bool:
    p = subprocess.run(["systemctl", "--user", "is-active", str(rw.RW.get("nightly_unit") or "krew-ms-nightly.service")],
                       capture_output=True, text=True)
    return p.stdout.strip() in ("active", "activating")


def unit_started(unit: str) -> float | None:
    """Start time from the user manager, else the system one (one copy: lib/restart_window._unit_started)."""
    return rw._unit_started(unit)


def unit_main_pid(unit: str) -> int | None:
    try:
        p = subprocess.run(["systemctl", "--user", "show", "-P", "MainPID", unit],
                           capture_output=True, text=True, timeout=10)
        v = int((p.stdout or "0").strip() or 0)
        return v or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def gather(now: dt.datetime | None = None) -> dict:
    import os_update
    now = now or dt.datetime.now()
    hours = krew_hours.off_hours()
    spec = hours.get("window") or ""
    try:
        debt = os_update.restart_debt()
    except Exception as e:  # noqa: BLE001
        log(f"restart debt unreadable: {e!r}")
        debt = {}
    pending = GPU_PENDING.read_text().split("\n", 1)[0].strip() if GPU_PENDING.is_file() else ""
    # a .path / .timer switched off (its off file, or its own condition) is never on the list
    owed, skipped_off = rw.split_switched_off(rw.merge_owed(
        rw.owed_units(debt), rw.config_owed(unit_started), rw.hermes_env_owed(unit_main_pid)))
    return {
        "now": now.isoformat(timespec="seconds"),
        "hours": hours,
        "in_window": hours["source"] == "init" and krew_hours.contains(now, spec),
        "window_key": krew_hours.started_on(now, spec) if hours["source"] == "init" else "",
        "owed": owed,
        "skipped_off": skipped_off,
        "reboot": rw.reboot_wanted(),
        "kanban": kanban_counts(),
        "occupancy": GPU_MODE.read_text().strip() if GPU_MODE.is_file() else "",
        "occupancy_pending": pending,
        "user_busy": user_busy(),
        # the gateway's API: asked here once for plan() to defer API_UNITS, and again before each unit
        "api_busy": rw.api_reason(rw.api_runs()) if rw.api_runs_enabled() else "",
        # the hermes tree a hermes unit would load: its patch stack and its node dependencies
        "tree_busy": rw.tree_now("hermes-gateway.service"),
        "nightly_active": nightly_active(),
    }


# ── the card ──────────────────────────────────────────────────────────────
def card_state(card_id: str) -> dict | None:
    if not card_id:
        return None
    rc, out = kanban("show", card_id, "--json")
    if rc != 0:
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    t = d.get("task") or {}
    status = str(t.get("status") or "")
    return {"id": card_id, "status": status, "open": status not in ("done", "archived"),
            "created_at": float(t.get("created_at") or 0), "comments": d.get("comments") or []}


def file_card(facts: dict) -> str:
    title = rw.card_title(facts["owed"], facts["reboot"])
    rc, out = kanban("create", title, "--assignee", "ada", "--body", text_cut.clip(rw.card_body(facts), 5500, label="restart-window-card-body"),
                     "--created-by", rw.AUTHOR, "--idempotency-key", f"restart-window:{facts['window_key']}")
    m = re.search(r"\b(t_[0-9a-f]{8})\b", out)
    if rc != 0 or not m:
        log(f"file failed: {text_cut.flat(out)}")
        return ""
    tid = m.group(1)
    rc2, out2 = kanban("block", tid, "Restart window: go | wait [N] | later HH:MM | reboot | park | hands?",
                       "--kind", "needs_input")
    if rc2 != 0:
        log(f"{tid}: block failed ({text_cut.flat(out2)}) — archiving so no worker takes it")
        kanban("archive", tid)
        return ""
    log(f"filed {tid}: {title}")
    return tid


def find_carried(old_id: str) -> str:
    """The board roll's copy of a card: body says 'Carried from … <old_id>'."""
    if not old_id:
        return ""
    rc, out = kanban("list", "--json")
    try:
        rows = json.loads(out) if rc == 0 else []
    except json.JSONDecodeError:
        return ""
    for r in rows:
        body = str(r.get("body") or "")
        if old_id in body[:400] and "Carried from" in body[:200] and rw.GATE_MARK in body:
            return str(r.get("id") or "")
    return ""


def comment(tid: str, text: str) -> None:
    if tid:
        kanban("comment", tid, text_cut.clip(text, 4000, label="restart-window-comment"), "--author", rw.AUTHOR)


def complete(tid: str, result: str) -> None:
    if not tid:
        return
    c = card_state(tid) or {}
    if not c.get("open"):
        return
    if c.get("status") in ("ready", "running"):
        rc, _ = kanban("complete", tid, "--result", text_cut.clip(result, 1500, label="restart-window-result"))
        if rc == 0:
            return
    # blocked / scheduled / todo: never unblock (a worker would take it). The
    # reason goes on as a comment, then archive — the card keeps its thread.
    comment(tid, result)
    kanban("archive", tid)


# ── execution ─────────────────────────────────────────────────────────────
def unit_ports() -> dict[str, int]:
    return {str(k): int(v) for k, v in (rw.RW.get("unit_ports") or {}).items()}


def spawn_executor(decision: dict, state: dict) -> None:
    plan_file = rw.STATE_PATH.with_name("restart-window-plan.json")
    plan_file.write_text(json.dumps({**decision, "card": state.get("card")}, indent=2))
    unit = f"krew-restart-window-exec-{int(time.time())}"
    cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect",
           sys.executable, str(Path(__file__).resolve()), "execute", "--plan", str(plan_file)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    log(f"executor {unit}: rc={p.returncode} {text_cut.flat(p.stderr or p.stdout)}")
    if p.returncode == 0:
        state["executing"] = {"unit": unit, "since": time.time()}
        rw.save_state(state)


def execute(plan_path: Path) -> int:
    import panel_units
    plan = json.loads(plan_path.read_text())
    tid = plan.get("card") or ""
    state = rw.load_state()
    ports = unit_ports()
    who = (plan.get("ruling") or {}).get("who") or "Crissa"
    if plan.get("reboot"):
        comment(tid, f"reboot: ruled by {who}; rebooting the host now. The window checks the machine after boot.")
        state.pop("executing", None)
        state["rebooting"] = {"since": time.time(), "card": tid}
        rw.save_state(state)
        # Zoey-ruled reboot → known-intent by construction; stamp it so the
        # post-boot janitor card carries the intent and skips the forensic pass.
        stamp = Path(__file__).resolve().parent / "krew-reboot-stamp.py"
        try:
            subprocess.run(
                [sys.executable, str(stamp), "stamp", f"reboot-window (ruled by {who})"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception as e:  # a failed stamp must not block the reboot
            log(f"reboot-stamp failed (continuing): {text_cut.flat(e)}")
        p = subprocess.run(["systemctl", "--no-ask-password", "reboot"], capture_output=True, text=True)
        if p.returncode != 0:
            # This house grants no reboot permission to its services (the User
            # keeps the host): the YES becomes a job for the User's hands.
            state.pop("rebooting", None)
            state["failure_item"] = rw.list_ask(
                "Reboot this machine from the desktop power menu",
                "You said YES to tonight's reboot; this machine may not reboot itself. "
                "Reboot from the power menu when you are ready; the window checks everything after boot.",
                card_id=tid, kind="task")
            state["rebooting"] = {"since": time.time(), "card": tid, "by_hand": True}
            rw.save_state(state)
            comment(tid, "reboot needs the User's hands (no reboot permission for services); asked on Crissa's list")
            log(f"reboot refused: {text_cut.flat(p.stderr or p.stdout)}")
            return 0
        return 0
    def restart_one(r: dict) -> dict:
        unit = r["unit"]
        off = rw.switched_off(unit)
        if off:
            # switched off since the card was ruled: not restarted, and not a failure
            log(f"{unit}: skipped: switched off ({off})")
            return {"unit": unit, "ok": True, "skipped": "switched off", "note": f"skipped: switched off ({off})"}
        name = unit[:-8] if unit.endswith(".service") else unit
        scope = "user"
        before = panel_units.unit_props(name, "user")
        if before.get("LoadState") in (None, "", "not-found"):
            scope = "system"
            before = panel_units.unit_props(name, "system")
        rc, out, err = panel_units._run(panel_units._sysctl(scope) + ["restart", "--no-block", unit])
        if rc != 0:
            return {"unit": unit, "ok": False, "note": err or out}
        res = panel_units.postcheck(name, scope, ports.get(unit), before.get("InvocationID", ""))
        log(f"{unit}: {'OK' if res.get('ok') else 'FAILED'} {res.get('note')}")
        return {"unit": unit, **res}

    # Asked again before EVERY unit: the tick decided minutes ago, and he may have started talking.
    ran, held = rw.run_units(plan.get("units") or [], restart_one,
                             (lambda r: busy_reason(r["unit"])) if rw.recheck_enabled() else None)
    results = [x for x in ran if x.get("ok")]
    failed = [x for x in ran if not x.get("ok")]
    if held:
        log(f"run stopped, {len(held)} left owed: {held[0]['deferred']}")
    lines = [f"go (ruled by {who}): {len(results)} restarted and checked, {len(failed)} failed."
             + (f" STOPPED with {len(held)} left owed: the User is mid-conversation." if held else "")]
    lines += [f"- ok {x['unit']}: {x.get('note', '')}" for x in results]
    lines += [f"- FAILED {x['unit']}: {x.get('note', '')}" for x in failed]
    deferred = (plan.get("deferred") or []) + held
    lines += [f"- deferred {d['unit']}: {d['deferred']}" for d in deferred]
    started = launch_after_checks(tid, results)
    if started:
        lines.append("after-checks started: " + ", ".join(started) + " - each posts its RESULT line on this card")
    comment(tid, "\n".join(lines))
    state = rw.load_state()
    state.pop("executing", None)
    if failed:
        item = rw.list_ask(
            f"{len(failed)} service(s) did not come back after tonight's restart",
            "Crissa's restart window restarted them and the post-check failed:\n"
            + "\n".join(f"- {x['unit']}: {x.get('note', '')}" for x in failed)
            + "\nIf this is hardware (a cable, a device, the GPU), it needs hands. "
              "YES = I looked; NO = leave it for the crew.",
            card_id=tid, kind="task")
        state["failure_item"] = item
        log(f"asked the User for eyes: {item}")
    if not deferred and not failed:
        complete(tid, "\n".join(lines))
        state["closed_window"] = state.get("window_key")
    rw.save_state(state)
    return 0 if not failed else 1


def launch_after_checks(tid: str, results: list, *, launcher=None) -> list:
    """The last step of a window (the User 2026-10-03): for each unit restarted OK that has a due after-check in
    config/restart-window-after.json, launch scripts/restart-after-check.py as its own transient unit - it posts its
    RESULT line on this card. Never inside the tick (a check may wait out a conversation). Kill KREW_RESTART_AFTER_CHECKS=0."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("restart_after_check", str(Path(__file__).resolve().parent / "restart-after-check.py"))
    rac = importlib.util.module_from_spec(spec); spec.loader.exec_module(rac)
    if not rac.enabled():
        return []
    started = []
    for x in results:
        unit = x.get("unit") or ""
        if not x.get("ok") or x.get("skipped") or not rac.due(unit):
            continue
        name = re.sub(r"[^a-z0-9-]", "-", unit.replace(".service", "")) + f"-{int(time.time())}"
        argv = ["systemd-run", "--user", "--collect", f"--unit=restart-after-{name}",
                sys.executable, str(Path(__file__).resolve().parent / "restart-after-check.py"), "--card", tid, "--unit", unit]
        rc = (launcher or subprocess.run)(argv, capture_output=True, text=True).returncode
        log(f"after-check for {unit}: {'launched' if rc == 0 else 'NOT launched'} (restart-after-{name})")
        if rc == 0:
            started.append(unit)
    return started


def post_reboot(state: dict) -> None:
    rb = state.get("rebooting") or {}
    if rb.get("by_hand") and time.time() - float(rb.get("since") or 0) > 3 * 86400:
        state.pop("rebooting", None)   # the User did not reboot in three days: stop waiting on it
        rw.save_state(state)
        return
    boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
    if boot < float(rb.get("since") or 0):
        return  # still the old boot (reboot pending)
    if time.time() - boot < 240:
        return
    checks = {str(k): str(v) for k, v in (rw.RW.get("post_reboot_checks") or {}).items()}
    bad = []
    for name, url in checks.items():
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if not 200 <= r.status < 300:
                    bad.append(name)
        except Exception:  # noqa: BLE001
            bad.append(name)
    tid = rb.get("card") or ""
    if bad:
        comment(tid, f"after the reboot: not answering: {', '.join(bad)}")
        state["failure_item"] = rw.list_ask(
            "This machine did not come all the way back after tonight's reboot",
            "Not answering: " + ", ".join(bad) + ". If this is hardware, it needs hands.",
            card_id=tid, kind="task")
    else:
        complete(tid, "reboot done; every post-reboot check answers: " + (", ".join(checks) or "none configured"))
        state["closed_window"] = state.get("window_key")
    state.pop("rebooting", None)
    rw.save_state(state)


# ── tick ──────────────────────────────────────────────────────────────────
def prune_owed(dry: bool = False) -> None:
    """A row is paid the moment its unit starts after it; take it out so the file says what is owed.
    Runs BEFORE the off switch (the User 2026-10-01): with restart-window.off set the tick returned
    first, so a unit restarted by hand or by the User's override kept reading as owed."""
    if dry or not rw.prune_enabled():
        return
    for d in rw.prune_paid(unit_started):
        log("owed row dropped, paid: %s started %s, after its row of %s (%s)" % (
            d["unit"], time.strftime("%m-%d %H:%M:%S", time.localtime(d["started"])),
            time.strftime("%m-%d %H:%M:%S", time.localtime(d["since"])), d["why"]))


def tick(dry: bool = False) -> int:
    prune_owed(dry)
    if not rw.enabled():
        log("restart window is OFF (enabled: false in %s, restart-window.off or KREW_RESTART_WINDOW=0): nothing filed, nothing restarted; what is owed stays in %s" % (rw._KWS.settings_file("restart-window"), rw.CONFIG_OWED))
        return 0
    state = rw.load_state()
    if state.get("executing"):
        u = state["executing"].get("unit", "")
        p = subprocess.run(["systemctl", "--user", "is-active", u], capture_output=True, text=True)
        if p.stdout.strip() not in ("active", "activating"):
            state.pop("executing", None)
            rw.save_state(state)
    facts = gather()
    seen = state.get("skipped_off_logged") or {}
    day = time.strftime("%Y-%m-%d")
    for r in facts.get("skipped_off") or []:
        mark = f"{day} {r['off']}"
        if seen.get(r["unit"]) != mark:
            log(f"skipped: switched off: {r['unit']} ({r['off']}) - not on the list, not a failure")
            seen[r["unit"]] = mark
    if not dry and seen != (state.get("skipped_off_logged") or {}):
        state["skipped_off_logged"] = seen
        rw.save_state(state)
    card = card_state(state.get("card", ""))
    key = facts.get("window_key")
    if key and card and card.get("comments"):
        ruled = rw.latest_ruling(card["comments"], since=card.get("created_at") or 0)
        if ruled and (state.get("ruling_window") != key
                      or float(ruled["ts"]) > float((state.get("ruling") or {}).get("ts") or 0)):
            state.update(ruling=ruled, ruling_window=key)
            rw.save_state(state)
    decision = rw.decide(state, facts, card, dt.datetime.now())
    if dry:
        print(json.dumps({"facts": facts, "card": {k: v for k, v in (card or {}).items() if k != "comments"},
                          "decision": decision}, indent=2, default=str))
        return 0
    act = decision["action"]
    tid = state.get("card", "")
    if act == "post_reboot":
        post_reboot(state)
    elif act == "close":
        complete(tid, f"restart window: {decision.get('why')}")
        state["closed_window"] = facts["window_key"]
        rw.save_state(state)
        log(f"closed {tid}: {decision.get('why')}")
    elif act == "unset":
        if state.get("unset_logged") != time.strftime("%Y-%m-%d"):
            log(f"idle: {decision['why']}")
            state["unset_logged"] = time.strftime("%Y-%m-%d")
            rw.save_state(state)
    elif act in ("file", "refile"):
        if act == "file" and card and card.get("open"):
            complete(tid, "The window closed with this still open; the next window's card carries it.")
        if act == "refile":
            carried = find_carried(tid)
            if carried:
                complete(carried, f"Carried copy of {tid} by the board roll without its block or ruling; "
                                  "the restart window re-filed it.")
        new = file_card(facts)
        if new:
            if act == "refile":
                comment(new, f"Re-filed: {tid} was lost to the board roll. Rule again if you had not ruled.")
            state.update(card=new, window_key=facts["window_key"], filed_at=time.time())
            if act == "file":
                state["missed_windows"] = decision.get("missed", 0)
                for k in ("list_item", "list_asked", "closed_window", "ruling", "ruling_window", "last_veto"):
                    state.pop(k, None)
            rw.save_state(state)
    elif act == "escalate":
        body = (decision.get("text") or "") + "\n\n" + rw.card_body(facts)
        q = ("Reboot this machine tonight?" if decision.get("ask") == "reboot"
             else f"Restart {len(facts['owed'])} service(s) tonight? (about 5 minutes; voice and brain blink)")
        state["list_item"] = rw.list_ask(q, f"{decision.get('why')}.\n{body}", card_id=tid)
        state["list_asked"] = decision.get("ask")
        rw.save_state(state)
        comment(tid, f"brought to the User (last resort): {decision.get('why')}")
        log(f"escalated {tid}: {decision.get('why')}")
    elif act == "veto":
        if state.get("last_veto") != decision.get("why"):
            comment(tid, f"ruling held by the law: {decision.get('why')}. Retrying every 5 minutes inside the window.")
            state["last_veto"] = decision.get("why")
            rw.save_state(state)
    elif act == "execute":
        comment(tid, "executing: " + (", ".join(u["unit"] for u in decision.get("units") or []) or "host reboot"))
        spawn_executor(decision, state)
    else:
        if decision.get("why"):
            log(f"idle: {decision['why']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tick")
    sub.add_parser("facts")
    ex = sub.add_parser("execute")
    ex.add_argument("--plan", required=True)
    args = ap.parse_args()
    if args.cmd == "tick":
        return tick()
    if args.cmd == "facts":
        return tick(dry=True)
    return execute(Path(args.plan))


if __name__ == "__main__":
    sys.exit(main())
