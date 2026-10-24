#!/usr/bin/env python3
"""Submit a Comfy image-to-video render and LEAVE (the User 2026-09-11: a render
takes hours, a worker turn lasts minutes — "submit and collect").

Recipes: wan22-i2v, ltx25-flf2v, ltx23-flf2v, ltx25-i2v (first-frame only, 15 s life loops).

One call: pre-flight (Comfy up, VRAM — parks ops through `krew-mode render
wan` stamped with the card), stage the seed, fill the house Wan graph
(<Comfy>/workflows/apple_split_wan_i2v.json, crew/lib/krew_comfy.py), POST /prompt, record the
job in ~/.hermes/render-jobs.json, comment the job id on the card, exit.
render-collect.py (krew-hold-sweep.timer, 2 min) lands the file, scores it
against the plate, posts the numbers and puts the card back to ready.

    python3 scripts/render-submit.py --card t_5b423dc9 --name listen-think-back \\
        --seed display/zoey-poses/_takes/20260908-imagine-work-listen-type/seeds/hub-back-hd.png \\
        --plate display/zoey-poses/_takes/refs-1296/hub-back-1296.png \\
        --prompt-file docs/prompts/listen-think-back.txt --seconds 4
"""
from __future__ import annotations
import pwd
import argparse, json, os, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
# The park command. An env override so a test can point it at a stub: the absolute path here
# is exactly what made the shortfall tests escape into the live house (see the call site).
# One rule for every krew caller (crew/lib/krew_mode_cmd.py): KREW_MODE_BIN, else KREW_MODE_PATH, else the
# install's $KREW_HOME/bin/krew-mode link, else the krew-mode shipped beside this script.
sys.path.insert(0, str(ROOT / "lib"))
import krew_mode_cmd  # noqa: E402
KREW_MODE_BIN = krew_mode_cmd.command()
import render_window_gate  # noqa: E402
# ONE jobs file for the house, whatever HERMES_HOME a worker runs under
# (Vera's worker wrote take 3 into profiles/vera/ and the collector never saw it, 2026-09-11 11:59)
# THE HOUSE'S OWN HOME, not the worker's. A kanban worker runs with
# HOME=~/.hermes/profiles/<who>/home, so Path.home() sent Vera's bash-work
# job to a jobs file the collector never reads (2026-09-12). The real
# account home from the passwd entry is the same for every caller.
HOUSE = Path(os.environ.get("KREW_HERMES") or (Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes"))
JOBS = HOUSE / "render-jobs.json"
# Outer wall on `krew-mode render wan`; keep it BELOW the kanban worker's
# terminal timeout (600) so a timeout is ours to record, not the tool's to kill.
PARK_WALL_SEC = float(os.environ.get("KREW_RENDER_PARK_WALL_SEC", "420"))
import krew_comfy  # noqa: E402  one Comfy location for krew and the Wall: render.json, else KREW_COMFY_*
COMFY = krew_comfy.get("url")
WF_DIR = Path(krew_comfy.get("workflows"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpu_budget  # noqa: E402  pre-render GPU memory guard (t_55c01d27)
# RECIPES (node ids in the house graphs under <Comfy>/workflows, crew/lib/krew_comfy.py):
#   wan22-i2v    Wan 2.2 14B fp8 image-to-video: the seed is frame 0 and the
#                model decides the landing (takes 2 and 3 of listen-think-back
#                both landed eyes-down at 0.945 whatever the prompt said).
#   ltx25-flf2v  LTX 2.5 first-last-frame: BOTH ends are the plate by
#                construction (Law 1 loop), 8 distilled steps, minutes not
#                hours. The recipe's tested size is 736x384 x 97 (the wall's
#                own scale); LTX wants multiples of 32 and 8k+1 frames.
#   ltx25-i2v    LTX 2.5 first-frame only: hub PNG as frame 0, interior free,
#                wrap the tail after. 15 s life loops (eyes/mouse). 736x384
#                x 361 (15 s @ 24, 8n+1). Same weights as FLF. No last image.
RECIPES = {
    "wan22-i2v": {"wf": "apple_split_wan_i2v.json", "prompt": "9", "neg": "10", "image": ("11",), "size": "12",
                  "samplers": ("13", "14"), "fps": "16", "save": "17", "size_default": "1296x704", "len_mod": 4, "steps": 20},
    "ltx25-flf2v": {"wf": "ltx25-flf2v.json", "prompt": "4", "neg": None, "image": ("12", "13"), "size": "7",
                    "samplers": ("8",), "fps": "10", "save": "11", "size_default": "736x384", "len_mod": 8, "steps": 8},
    # ltx23-flf2v: the LTX 2.3 first-last graph is the same 15 nodes as 2.5 (ids
    # checked 2026-09-19: 4 prompt, 7 latent 736x384x97, 8 KSampler 8 steps, 10
    # CreateVideo 24, 11 SaveVideo, 12/13 LoadImage). The krew-comfy plugin
    # refuses it as a wall mint and sends workers here, where it was missing.
    "ltx23-flf2v": {"wf": "ltx23-flf2v.json", "prompt": "4", "neg": None, "image": ("12", "13"), "size": "7",
                    "samplers": ("8",), "fps": "10", "save": "11", "size_default": "736x384", "len_mod": 8, "steps": 8},
    "ltx25-i2v": {"wf": "ltx25-i2v.json", "prompt": "4", "neg": None, "image": ("12",), "size": "7",
                  "samplers": ("8",), "fps": "10", "save": "11", "size_default": "736x384", "len_mod": 8, "steps": 8,
                  "default_seconds": 15.0},
}
IN_DIR = Path(krew_comfy.get("input"))
NEED_GIB = 24.0          # measured 2026-09-11: Comfy keeps ~14 GiB of Wan staged between jobs; take 2 ran in 22-35 GiB
WAN_NEG = "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"


def load_jobs() -> list:
    try:
        return json.loads(JOBS.read_text())
    except (OSError, ValueError):
        return []


def save_jobs(jobs: list) -> None:
    JOBS.write_text(json.dumps(jobs, indent=1) + "\n")


def free_vram_gib() -> float | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        return float(out) / 1024.0
    except Exception:
        return None


def comfy_up() -> bool:
    try:
        urllib.request.urlopen(f"{COMFY}/system_stats", timeout=5).read()
        return True
    except Exception:
        return False


def kanban(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["hermes", "kanban", *args],
            capture_output=True, text=True, timeout=60,
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))[:400]
    except Exception as exc:
        return 1, str(exc)[:400]


def wan_length(seconds: float, fps: int, mod: int = 4) -> int:
    n = int(round(seconds * fps))
    return n + ((1 - n) % mod)      # Wan wants 4k+1 frames (97 = 4 s, 241 = 10 s); LTX 8k+1 (97, 193)


def pid_starttime(pid: int) -> str | None:
    """Linux /proc starttime (field 22) — pairs with pid so a reused pid is not 'live'."""
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
        rparen = st.rfind(")")
        fields = st[rparen + 2 :].split()
        return fields[19]  # starttime
    except (OSError, IndexError):
        return None


def live_collision(jobs: list, card: str, name: str) -> dict | None:
    """A second submit for the same live card+name while preparing/pending."""
    for j in jobs:
        if not isinstance(j, dict):
            continue
        if j.get("card") != card or j.get("name") != name:
            continue
        if j.get("status") == "preparing":
            return j
        if j.get("status") == "pending" and j.get("prompt_id"):
            return j
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True)
    ap.add_argument("--name", required=True, help="take name; folder display/zoey-poses/_takes/crew-<date>-<name>/")
    ap.add_argument("--seed", required=True, help="the hub still to start (and land) on")
    ap.add_argument("--plate", help="the 1296 plate the ends are scored against (default: the seed)")
    ap.add_argument("--last", help="flf2v only: a DIFFERENT still for the last frame = a slide between hubs (scored against itself). Forbidden on ltx25-i2v.")
    ap.add_argument("--guide", type=float, default=None, help="flf2v only: LTXVAddGuide strength for both ends (recipe default 0.7; 1.0 pins harder)")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--seconds", type=float, default=None, help="clip length (default: recipe — 4 s Wan/FLF, 15 s ltx25-i2v)")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--recipe", default="wan22-i2v", choices=sorted(RECIPES))
    ap.add_argument("--size", default=None, help="WxH (default: the recipe's tested size)")
    ap.add_argument("--steps", type=int, default=None, help="default: the recipe's")
    ap.add_argument("--cfg", type=float, default=3.5)
    ap.add_argument("--noise-seed", type=int, default=42)
    ap.add_argument("--out-dir")
    ap.add_argument("--no-park", action="store_true", help="never run krew-mode render (fail instead)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    # GPU pre-check (t_55c01d27; scoped 2026-09-19, User note on t_6260608e
    # "check VRAM only when the job will not park ops"): the budget gate is a
    # hard gate at the top of the task, before anything is written to the jobs
    # file — but it applies ONLY when the run will not run `krew-mode render`.
    # When it parks, the park frees ops' VRAM, so a "busy now" reading is the
    # expected state (the 2026-09-18 15:50 synthetic smoke exited 4 on exactly
    # that), and the 24 GiB NEED_GIB wait below the park is the
    # run-specific gate. ok -> proceed; not ok and no park allowed -> the
    # block is issued HERE (one owner, per the wrapper protocol) and the
    # worker exits 4/5; the render is not attempted. The standalone
    # `gpu_budget.py` probe is read-only and never blocks.
    if a.no_park:
        _g = gpu_budget.precheck(a.card)
        if not _g.ok:
            print(_g.reason, file=sys.stderr)
            brc, bout = _g.block(a.card)
            if brc != 0:
                print(f"kanban block failed rc={brc}: {bout}", file=sys.stderr)
            sys.exit(gpu_budget.EXIT_UNMEASURABLE if _g.free_mib is None else gpu_budget.EXIT_INSUFFICIENT)
    hold = HOUSE / "krew-render-hold"
    if hold.is_file():
        try:
            why = hold.read_text(encoding="utf-8").strip()
        except OSError:
            why = ""
        print(why or "render hold — no further submits (Fable morning)", file=sys.stderr)
        return 3
    # THE HOLD OWNER IS THE LIVE WORKER CARD (2026-09-13). krew-board-carry
    # copies bodies verbatim at the daily roll, so a carried card still says
    # `--card <yesterday's id>`; every hold stamp then looked ARCHIVED to the
    # sweep. When the worker runs under a kanban task, that task id is the
    # card: it stamps the hold, keys the jobs file and takes the comments.
    live_card = (os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    if live_card and live_card != a.card:
        print(f"--card {a.card} is stale (carried); the live card is {live_card} — using it", file=sys.stderr)
        a.card = live_card
    why = render_window_gate.refuse_reason(a.card)
    if why:
        print(why, file=sys.stderr)
        if live_card:
            kanban("comment", a.card, f"{render_window_gate.MARK}: {why}", "--author", "render-submit")
            kanban("block", a.card, why)
        return 3
    prompt = a.prompt or (Path(a.prompt_file).read_text().strip() if a.prompt_file else "")
    if not prompt:
        print("need --prompt or --prompt-file", file=sys.stderr); return 2
    seed = Path(a.seed).expanduser().resolve()
    plate = Path(a.plate).expanduser().resolve() if a.plate else seed
    if not seed.is_file() or not plate.is_file():
        print("seed/plate missing", file=sys.stderr); return 2
    R = RECIPES[a.recipe]
    if a.recipe == "ltx25-i2v" and a.last:
        print("ltx25-i2v is first-frame only — do not pass --last (that is FLF). Wrap the tail after.", file=sys.stderr)
        return 2
    if a.seconds is None:
        a.seconds = float(R.get("default_seconds") or 4.0)
    a.size = a.size or R["size_default"]
    a.steps = a.steps or R["steps"]
    w, h = (int(v) for v in a.size.lower().split("x"))
    length = wan_length(a.seconds, a.fps, R["len_mod"])
    WF = WF_DIR / R["wf"]
    out_dir = Path(a.out_dir) if a.out_dir else ROOT / "display" / "zoey-poses" / "_takes" / f"crew-{time.strftime('%Y%m%d')}-{a.name}"
    # take numbering follows the NAME + OUT_DIR, not the card id: a carried card
    # gets a new id every midnight and take-1.mp4 must not overwrite yesterday's.
    existing = live_collision(load_jobs(), a.card, a.name)
    if existing:
        print(json.dumps({
            "ok": False, "error": "one submit per run",
            "status": existing.get("status"), "take": existing.get("take"),
            "submitted": existing.get("submitted"),
            "prompt_id": existing.get("prompt_id"),
        }), file=sys.stderr)
        return 3
    take_n = 1 + sum(1 for j in load_jobs()
                     if j.get("name") == a.name and (j.get("out_dir") == str(out_dir) or j.get("card") == a.card))
    prefix = f"crew/{a.name}-take{take_n}"
    if not WF.is_file():
        print(f"workflow missing: {WF}", file=sys.stderr); return 2
    graph = json.loads(WF.read_text())
    graph[R["prompt"]]["inputs"]["text"] = prompt
    if R["neg"]:
        graph[R["neg"]]["inputs"]["text"] = WAN_NEG
    graph[R["size"]]["inputs"].update(width=w, height=h, length=length, batch_size=1)
    if a.recipe == "wan22-i2v":
        for nid in R["samplers"]:
            graph[nid]["inputs"].update(noise_seed=a.noise_seed, steps=a.steps, cfg=a.cfg)
        graph["13"]["inputs"].update(start_at_step=0, end_at_step=a.steps // 2)
        graph["14"]["inputs"].update(start_at_step=a.steps // 2, end_at_step=a.steps)
    else:
        for nid in R["samplers"]:
            graph[nid]["inputs"].update(seed=a.noise_seed, steps=a.steps)
        if a.guide is not None:
            for nid, node in graph.items():
                if str(node.get("class_type", "")).startswith("LTXVAddGuide"):
                    node["inputs"]["strength"] = float(a.guide)
    graph[R["fps"]]["inputs"]["fps"] = a.fps
    graph[R["save"]]["inputs"]["filename_prefix"] = prefix
    job = {"card": a.card, "name": a.name, "take": take_n, "recipe": a.recipe, "guide": a.guide, "seed": str(seed), "plate": str(plate),
           "prompt": prompt, "seconds": a.seconds, "fps": a.fps, "length": length, "size": a.size,
           "steps": a.steps, "noise_seed": a.noise_seed, "prefix": prefix, "out_dir": str(out_dir),
           "status": "pending", "submitted": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if a.dry_run:
        print(json.dumps({"dry_run": True, "job": job, "graph_nodes": len(graph)}, indent=1)); return 0
    # RECORD BEFORE YOU PARK. 2026-09-11 11:39: krew-mode render parked ops
    # and stamped the hold, the 2-min sweep ran eleven seconds before the
    # job was written, saw a blocked card with nothing pending, released the
    # hold and started ops under the render. "preparing" is live to the
    # sweeps and the janitor from this line on.
    job["status"] = "preparing"
    job["pid"] = os.getpid()
    job["pid_starttime"] = pid_starttime(os.getpid())
    job["alive_at"] = time.time()
    jobs = load_jobs(); jobs.append(job); save_jobs(jobs)

    def _mark(status: str, **kw) -> None:
        js = load_jobs()
        for j in js:
            if j is not job and j.get("card") == job["card"] and j.get("take") == job["take"] and j.get("name") == job["name"]:
                j.update(status=status, **kw)
        save_jobs(js)

    free = free_vram_gib()
    if not comfy_up() or (free is not None and free < NEED_GIB):
        # krew-mode render wan parks ops (stamped with this card) AND starts
        # Comfy when it is down (2026-09-11: the janitor had killed it)
        if a.no_park:
            print(f"Comfy up={comfy_up()} free VRAM {free} GiB < {NEED_GIB:.0f} and --no-park", file=sys.stderr); return 3
        env = dict(os.environ, HERMES_KANBAN_TASK=a.card)
        # The outer wall MUST be shorter than the worker's terminal timeout
        # (Vera: terminal.timeout 600). 2026-09-13: it was 600 == 600, the
        # tool killed this process first, `_mark("failed")` never ran and the
        # row stayed `preparing` with no prompt_id — five ghosts that pinned
        # ops and Zoey for four hours. krew-mode's own steps are bounded
        # (PARK_STEP_BUDGET_SEC, TimeoutStopSec=45, the Comfy-up poll), so
        # 420 s still covers the worst park path. If it fires, krew-mode is
        # KILLED — ops may be parked and Comfy half-started; fail fast.
        try:
            # KREW_MODE_BIN, 2026-09-22. This called krew-mode by ABSOLUTE PATH, which a
            # test's PATH stub can never intercept — so all three tests in
            # tests/test_render_submit_postpark_shortfall.py believed they had stubbed it and
            # none of them had, and every run entered Render on the LIVE machine: ops and crew
            # parked under card `t_selftest`. Worse, no board knows that id, so hold_card_live
            # answers "status unknown — keep" (the 2026-09-14 rail) and the hold-sweep can
            # never release it. Both brains sat dead until a hand cleared it.
            r = subprocess.run([*KREW_MODE_BIN, "render", "wan"], env=env,
                               capture_output=True, text=True, timeout=PARK_WALL_SEC)
        except subprocess.TimeoutExpired:
            _mark("failed", error=f"krew-mode render wan: {PARK_WALL_SEC:.0f} s outer timeout — ops may be parked; run `krew-mode normal`")
            print(f"krew-mode render wan exceeded {PARK_WALL_SEC:.0f} s — ops may be parked; "
                  "run `python3 scripts/krew-mode normal` to restore, then retry.", file=sys.stderr)
            return 3
        print(r.stdout[-600:], file=sys.stderr)
        # Test seam: the poll loop is 72 x 5 s on a real run (krew-mode render
        # is a multi-minute switch). Tests run with a short loop + no sleep so
        # the post-park branches are reachable in seconds.
        _POLL_N = int(os.environ.get("KREW_RENDER_POLL_N", "72"))
        _POLL_SLEEP = float(os.environ.get("KREW_RENDER_POLL_SLEEP", "5"))
        for _ in range(_POLL_N):
            if _POLL_SLEEP:
                time.sleep(_POLL_SLEEP)
            _mark("preparing", alive_at=time.time(), pid=os.getpid(),
                  pid_starttime=pid_starttime(os.getpid()))
            free = free_vram_gib()
            if comfy_up() and free is not None and free >= NEED_GIB:
                break
        # Post-park guards. ORDER MATTERS: the VRAM shortfall is the root
        # cause of the 40/40 shape (t_542ebba9) — the resident holds the GPU
        # and Comfy (even if up) cannot allocate — so it is checked first and
        # the block carries the numbers. Comfy-down is the second, orthogonal
        # failure (park ran but Comfy never came up); it is only reached when
        # VRAM is actually sufficient.
        if free is None or free < NEED_GIB:
            # The park ran and freed what it could; the rest is still resident.
            # Same contract as the Comfy-down branch below: mark the row AND
            # block the card with the numbers. A silent rc=3 here is the
            # "worker exited 0 without a terminal kanban call" class — the
            # card looks alive, the run budget is what eats the iterations.
            # The 24 GiB number is measured (2026-09-11), not invented: if a
            # future resident profile (beast 27B, a second engine) legitimately
            # holds VRAM that the park cannot reach, raise it in the same place
            # and say why in a comment.
            # `free is None` means the probe could not MEASURE, which is not the same fact as
            # "there is not enough" — and printing it as a quantity put the literal words
            # "only None GiB free" on a card a ruler reads (2026-09-22). Blocking on unknown
            # is still right: a render that cannot verify its VRAM is the 40/40 shape waiting
            # to happen. Only the wording changes, so the block says which of the two it is.
            said = (f"only {free:.0f} GiB free" if free is not None
                    else "free VRAM could not be measured (nvidia-smi gave no usable answer)")
            _mark("failed", error=f"{said} after krew-mode render wan")
            if live_card:
                kanban("comment", a.card,
                       f"render-submit: krew-mode render wan parked ops but {said} "
                       f"(< {NEED_GIB:.0f} needed for this recipe). Residual VRAM is held by a "
                       f"resident the park does not own. Run `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv`, "
                       f"clear it (or `python3 scripts/krew-mode normal` + park again), then resubmit.")
                kanban("block", a.card,
                       f"VRAM still short after park: {said} < {NEED_GIB:.0f} GiB needed for "
                       f"this recipe; a resident the park does not own holds the rest — clear it, then resubmit")
            print(f"still {said} after krew-mode render wan", file=sys.stderr); return 3
        if not comfy_up():
            # VRAM is sufficient but Comfy never came up after the park.
            # The card would otherwise sit in the worker with no board signal —
            # the worker burns its whole run budget on retries, then dies 0
            # without a terminal kanban call. Visible failure on the board
            # beats an invisible crash: ops is parked (the worker can restore
            # it), and the janitor's ghost sweep (stale `preparing` rows) still
            # covers the process itself.
            _mark("failed", error="Comfy not up after krew-mode render wan")
            if live_card:
                kanban("comment", a.card,
                       "render-submit: krew-mode render wan ran but Comfy :8188 did not come up. "
                       "Ops is parked — run `python3 scripts/krew-mode normal` to restore, then resubmit.")
                kanban("block", a.card,
                       "Comfy :8188 did not come up after krew-mode render wan (VRAM park ran; "
                       "run `python3 scripts/krew-mode normal` and resubmit)")
            print("Comfy :8188 still not up after krew-mode render wan", file=sys.stderr); return 3
    IN_DIR.mkdir(parents=True, exist_ok=True)
    staged = IN_DIR / f"crew_{a.name}_take{take_n}_{seed.name}"
    shutil.copy2(seed, staged)
    for nid in R["image"]:                 # i2v: the seed; flf2v: first AND last = the plate (Law 1)
        graph[nid]["inputs"]["image"] = staged.name
    if a.last and a.recipe in ("ltx25-flf2v", "ltx23-flf2v"):  # a slide: last frame is the other hub
        last = Path(a.last).expanduser().resolve()
        staged_last = IN_DIR / f"crew_{a.name}_take{take_n}_last_{last.name}"
        shutil.copy2(last, staged_last)
        graph[R["image"][1]]["inputs"]["image"] = staged_last.name
        job["last"] = str(last); job["plate_last"] = str(last)
        _mark("preparing", last=str(last), plate_last=str(last))   # the stored row predates this block
    # ONE CLIENT ID FOR THE HOUSE (lib/render_wip.py, the User 2026-09-18 "show WIP
    # always"): Comfy streams progress + previews only to the websocket that
    # holds the prompt's client_id, and the :8099 watcher holds "krew-wip".
    # preview_method is a per-prompt override — no Comfy restart.
    body = json.dumps({"prompt": graph, "client_id": os.environ.get("KREW_COMFY_CLIENT_ID", "krew-wip"),
                       "extra_data": {"preview_method": os.environ.get("KREW_COMFY_PREVIEW", "auto")}}).encode()
    req = urllib.request.Request(f"{COMFY}/prompt", data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            pid = json.loads(r.read().decode()).get("prompt_id")
    except Exception as exc:
        _mark("failed", error=f"POST /prompt: {exc}"[:200])
        print(f"Comfy refused the job: {exc}", file=sys.stderr); return 3
    if not pid:
        _mark("failed", error="no prompt_id")
        print("Comfy returned no prompt_id", file=sys.stderr); return 3
    job["prompt_id"] = pid
    _mark("pending", prompt_id=pid)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"take-{take_n}.job.json").write_text(json.dumps(job, indent=1) + "\n")
    est_min = int(round(length / 24 * 20 * 0.55)) if a.recipe == "wan22-i2v" else max(3, int(round(length / 24 * 1.5)))
    kanban("comment", a.card, f"render-submit: take {take_n} ({a.recipe}) queued on Comfy, prompt {pid[:8]}, {a.size} x {length} frames @ {a.fps}, {a.steps} steps, seed {seed.name}. "
           f"Roughly {est_min} min. Do NOT poll — render-collect lands take-{take_n}.mp4 with f0/last stills and NCC vs the plate in {out_dir.name}/, then unblocks for the judging turn.")
    brc, bout = kanban(
        "block", a.card,
        f"render pending prompt {pid[:8]}; collect will unblock for the judging turn. Do not poll.",
    )
    if brc != 0:
        print(f"kanban block failed rc={brc}: {bout}", file=sys.stderr)
    print(json.dumps({
        "ok": True, "prompt_id": pid, "take": take_n, "out_dir": str(out_dir),
        "length": length, "est_min": est_min, "board_block": brc == 0,
    }))
    return 0 if brc == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
