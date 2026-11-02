"""Local ComfyUI backend for Hermes image_generate / video_generate.

Krew SoT: your ComfyUI root (krew_comfy.py: the Wall's render.json comfy_dir, else KREW_COMFY_ROOT, else ~/ComfyUI:
weights + workflows). HTTP only, 127.0.0.1:8188.
generate() refuses unless occupancy is already Render (panel / krew_gpu_mode
/ krew-mode). It does not enter, does not leave, does not systemd-stop
brains. Comfy start lives only in krew-mode.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import pwd
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
try:
    from . import krew_hermes as KH
except ImportError:                 # imported alone (tests, scripts)
    import krew_hermes as KH  # noqa: E402

logger = logging.getLogger(__name__)

try:
    from agent.image_gen_provider import ImageGenProvider as _ImageGenBase
except ImportError:  # studio CLI / tests without Hermes
    class _ImageGenBase:  # type: ignore[no-redef]
        pass

try:
    from agent.video_gen_provider import VideoGenProvider as _VideoGenBase
except ImportError:
    class _VideoGenBase:  # type: ignore[no-redef]
        pass

try:
    from . import krew_comfy  # one Comfy location for krew and the Wall: render.json, else KREW_COMFY_*, else ~/ComfyUI
except ImportError:           # loaded by path as a plain module (render-cycle, render-still): the copy beside it
    import importlib.util as _ilu
    _kc_spec = _ilu.spec_from_file_location("krew_comfy", Path(__file__).resolve().with_name("krew_comfy.py"))
    krew_comfy = _ilu.module_from_spec(_kc_spec)
    _kc_spec.loader.exec_module(krew_comfy)
COMFY = krew_comfy.get("url")
WF_DIR = Path(krew_comfy.get("workflows"))
OUT_DIR = Path(krew_comfy.get("output"))
IN_DIR = Path(krew_comfy.get("input"))
# Passwd home, not $HOME — kanban workers sandbox HOME under the profile.
_PASSWD_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
HERMES_DIR = Path(os.environ.get("KREW_HERMES") or KH.hermes_home(passwd=True))
HERMES = Path(os.environ.get("HERMES_HOME", str(HERMES_DIR)))
# Not krew-mode.json (that file is auth). GPU occupancy is krew-gpu-mode.
MODE_FILE = Path(
    os.environ.get("KREW_GPU_MODE_FILE", str(HERMES_DIR / "krew-gpu-mode"))
)

def _recipes_path() -> Path:
    env = os.environ.get("KREW_COMFY_RECIPES")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "recipes.json"


def load_recipes() -> list:
    try:
        data = json.loads(_recipes_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        recs = data.get("recipes") or []
    elif isinstance(data, list):
        recs = data
    else:
        recs = []
    return [r for r in recs if isinstance(r, dict) and r.get("id")]


def recipe_wired(rec: dict) -> bool:
    wf = rec.get("workflow")
    if not wf:
        return False
    return (WF_DIR / str(wf)).is_file()


def _as_model(rec: dict) -> dict:
    out = {
        "display": rec.get("display") or rec.get("purpose") or rec["id"],
        "workflow": rec.get("workflow") or "",
        "vram_gib": float(rec.get("vram_gib") or 0),
        "prompt_node": rec.get("prompt_node"),
        "size_nodes": tuple(rec.get("size_nodes") or ()),
        "kind": rec.get("kind") or "t2i",
        "license": rec.get("license") or "",
    }
    if rec.get("image_node"):
        out["image_node"] = rec["image_node"]
    for k in (
        "first_image_node",
        "last_image_node",
        "width",
        "height",
        "length",
        "fps",
        "first_frame_idx",
        "last_frame_idx",
        "strength",
        "crf",
    ):
        if rec.get(k) is not None:
            out[k] = rec[k]
    return out


def _split_models() -> tuple:
    images: Dict[str, dict] = {}
    videos: Dict[str, dict] = {}
    for rec in load_recipes():
        if not rec.get("workflow"):
            continue
        kind = str(rec.get("kind") or "t2i")
        model = _as_model(rec)
        if kind in ("i2v", "t2v", "flf2v"):
            videos[rec["id"]] = model
        else:
            images[rec["id"]] = model
    return images, videos


IMAGE_MODELS, VIDEO_MODELS = _split_models()
_COMMERCIAL_OK = __import__("re").compile(r"apache|\bmit\b|\bopen\b", __import__("re").I)
_NOT_COMMERCIAL = __import__("re").compile(r"internal|community|research|non-commercial|cloud", __import__("re").I)


def commercial_ok(rec: dict) -> bool:
    """The recipe's licence allows publishing (the render cards read the same words)."""
    lic = str(rec.get("license") or "")
    return bool(_COMMERCIAL_OK.search(lic)) and not _NOT_COMMERCIAL.search(lic) and not rec.get("opt_in")


def default_image_model() -> Optional[str]:
    """The default still: the recipes file's `default` when it is wired and commercial-OK, else the first wired
    commercial-OK still (qwen-image-2512, then sdxl, ...). An opt-in recipe (flux2-*, ltx25-*) is never the
    default (the owner 2026-10-08). None = nothing publishable is wired."""
    recs = {r["id"]: r for r in load_recipes()}
    try:
        want = str((json.loads(_recipes_path().read_text(encoding="utf-8")) or {}).get("default") or "")
    except (OSError, ValueError, AttributeError):
        want = ""
    order = ([want] if want else []) + ["qwen-image-2512", "sdxl"] + [r for r in recs]
    for rid in order:
        r = recs.get(rid)
        if r and rid in IMAGE_MODELS and commercial_ok(r) and recipe_wired(r):
            return rid
    return None

ASPECT = {
    "square": (1024, 1024),
    "landscape": (1280, 768),
    "portrait": (768, 1280),
}


def _free_vram_gib() -> Optional[float]:
    try:
        import subprocess

        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=3,
        )
        return float(out.strip().splitlines()[0]) / 1024.0
    except Exception:
        return None


def comfy_up() -> bool:
    try:
        with urllib.request.urlopen(f"{COMFY}/system_stats", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _load_wf(name: str) -> Dict[str, Any]:
    path = WF_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"workflow missing: {path}")
    return json.loads(path.read_text())


def _set_text(graph: dict, node_id: Optional[str], text: str) -> None:
    def apply(node: dict) -> None:
        inp = node.setdefault("inputs", {})
        ct = str(node.get("class_type") or "")
        if ct.startswith("TextEncodeQwen") or "prompt" in inp:
            inp["prompt"] = text
        else:
            inp["text"] = text

    if node_id and node_id in graph:
        apply(graph[node_id])
        return
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        if ct == "CLIPTextEncode" or ct.startswith("TextEncodeQwen"):
            apply(node)
            return


def _set_size(graph: dict, node_ids: tuple, w: int, h: int) -> None:
    for nid in node_ids:
        node = graph.get(nid)
        if not isinstance(node, dict):
            continue
        inp = node.setdefault("inputs", {})
        if "width" in inp:
            inp["width"] = w
        if "height" in inp:
            inp["height"] = h


def _stage_input_image(image_url: str) -> str:
    """Copy/download a source image into Comfy input/. Return basename."""
    IN_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(image_url).expanduser()
    dest = IN_DIR / f"hermes_{int(time.time())}_{src.name or 'in.png'}"
    if src.is_file():
        dest.write_bytes(src.read_bytes())
        return dest.name
    if image_url.startswith("http://") or image_url.startswith("https://"):
        with urllib.request.urlopen(image_url, timeout=30) as r:
            dest.write_bytes(r.read())
        return dest.name
    raise FileNotFoundError(f"image not found: {image_url}")


def _submit(graph: dict, prefix: str) -> str:
    # ONE CLIENT ID FOR THE CREW (crew/lib/render_wip.py, the owner 2026-09-18
    # "show WIP always"): Comfy streams progress + latent previews only to the
    # websocket that holds the prompt's client_id, and the Wall's feed watcher holds
    # "krew-wip" — so the Control Panel stage can watch every draw live.
    # preview_method is a per-prompt override (no Comfy restart, no TAESD).
    body = json.dumps({
        "prompt": graph,
        "client_id": os.environ.get("KREW_COMFY_CLIENT_ID", "krew-wip"),
        "extra_data": {"preview_method": os.environ.get("KREW_COMFY_PREVIEW", "auto")},
    }).encode()
    req = urllib.request.Request(
        f"{COMFY}/prompt",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        pid = json.loads(r.read().decode()).get("prompt_id")
    if not pid:
        raise RuntimeError("Comfy returned no prompt_id")
    deadline = time.time() + float(os.environ.get("KREW_COMFY_TIMEOUT", "600"))
    while time.time() < deadline:
        time.sleep(2)
        with urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10) as r:
            hist = json.loads(r.read().decode())
        rec = hist.get(pid)
        if not rec:
            continue
        outputs = rec.get("outputs") or {}
        for node in outputs.values():
            for key in ("images", "gifs", "videos"):
                items = node.get(key) or []
                if not items:
                    continue
                item = items[0]
                name = item.get("filename")
                sub = item.get("subfolder") or ""
                path = OUT_DIR / sub / name if name else None
                if path and path.is_file():
                    return str(path)
        if rec.get("status", {}).get("status_str") == "error":
            raise RuntimeError(f"Comfy error: {rec.get('status')}")
    raise TimeoutError(f"Comfy job {pid} timed out")


def _apply_flf_guides(graph: dict, spec: dict) -> None:
    """Write recipe strength / CRF / frame_idx onto the two AddGuide nodes."""
    guides: list[dict] = []
    for nid in sorted(graph, key=lambda x: int(x) if str(x).isdigit() else 10**9):
        node = graph.get(nid)
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") in {
            "LTXVAddGuideAdvanced",
            "LTXVAddGuide",
        }:
            guides.append(node)
    if spec.get("strength") is not None:
        for node in guides:
            node.setdefault("inputs", {})["strength"] = float(spec["strength"])
    if spec.get("crf") is not None:
        for node in guides:
            ct = str(node.get("class_type") or "")
            if "crf" in node.get("inputs", {}) or ct == "LTXVAddGuideAdvanced":
                node.setdefault("inputs", {})["crf"] = int(spec["crf"])
    if spec.get("first_frame_idx") is not None and guides:
        guides[0].setdefault("inputs", {})["frame_idx"] = int(spec["first_frame_idx"])
    if spec.get("last_frame_idx") is not None and len(guides) >= 2:
        guides[1].setdefault("inputs", {})["frame_idx"] = int(spec["last_frame_idx"])


def gpu_mode() -> str:
    """Occupancy flag. Missing/unknown = normal (do not render by accident)."""
    try:
        text = MODE_FILE.read_text(encoding="utf-8")
    except OSError:
        return "normal"
    first = text.strip().splitlines()[0].strip().lower() if text.strip() else ""
    if first in {"render", "normal", "beast", "off"}:
        return first
    return "normal"


# One ops seat at --max-num-seqs 1 now has two mouths: the Control Panel studio
# and a kanban card. They are different PROCESSES, so the studio's in-process
# lock cannot see us and ours cannot see it. These two files are the handshake.
# They live in the KREW home, where the Control Panel reads and writes them

# worker or a profile's gateway is the PROFILE's home: the card looked for the studio's seat in
# ~/.hermes/profiles/lila/render-studio.json, a file that has never existed, so studio_busy()
# always answered "" and a card never stood aside for the studio; and its own marker was written
# where the studio never looks. The handshake shook no hands. Kill: KREW_COMFY_SEAT_HOME=profile.
_SEAT_HOME = HERMES if os.environ.get("KREW_COMFY_SEAT_HOME", "house").strip().lower() == "profile" else HERMES_DIR
STUDIO_STATE = Path(
    os.environ.get("RENDER_STUDIO_STATE", str(_SEAT_HOME / "render-studio.json"))
)
LANE_BUSY = Path(
    os.environ.get("KREW_RENDER_LANE_BUSY", str(_SEAT_HOME / "render-lane-busy"))
)
# A card that dies mid-graph must not wedge the studio forever.
LANE_BUSY_TTL = float(os.environ.get("KREW_RENDER_LANE_TTL", "900"))


def studio_busy() -> str:
    """In-turn studio lock, or "" if Lila is idle.

    Read off disk, not imported: lib/render_studio pulls PIL and lives in the
    panel server's environment, not this one.

    Gate on render-studio.json ``busy`` only. Do NOT treat
    ``~/.hermes/render-studio-listen`` as a held seat: welcome_pin arms that
    latch for the whole Render window (Mic on), and a card that refused while
    the mic was armed could never draw unattended — the point of the lane
    (2026-08-28). A real listen turn already sets busy to "listening".
    """
    try:
        st = json.loads(STUDIO_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(st, dict):
        return ""
    # THE STUDIO'S OWN DRAW IS NOT A SECOND MOUTH (the owner 2026-09-16: "we have no
    # lila voice in Render mode now"). _lila_turn marks busy "drawing" and then
    # loads THIS plugin in its own process to draw — so the seat it had just
    # taken refused it, every drawing turn since 09-15 raised "The Control Panel
    # studio is drawing", and Lila went silent. The seat is for OTHER processes
    # (a kanban card); render_studio._set_busy stamps busy_pid.
    if st.get("busy_pid") == os.getpid():
        return ""
    return str(st.get("busy") or "")


@contextmanager
def lane_busy(label: str):
    """Hold the lane marker while a graph is in flight, and always drop it."""
    try:
        LANE_BUSY.parent.mkdir(parents=True, exist_ok=True)
        LANE_BUSY.write_text(
            json.dumps({"pid": os.getpid(), "ts": time.time(), "what": label}),
            encoding="utf-8",
        )
    except OSError:
        pass
    try:
        yield
    finally:
        try:
            LANE_BUSY.unlink()
        except OSError:
            pass


def _window_refuse(card_id: str | None = None) -> str:
    """Kanban workers cannot enter Render / draw outside the owner's off-hours (crew/lib/render_window_gate.py)."""
    lib = Path(os.environ.get("KREW_HOME") or Path.home() / ".krew") / "crew" / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import render_window_gate
    except ImportError:
        if os.environ.get("HERMES_KANBAN_TASK"):
            return "render window gate unread — cannot start a kanban render"
        return ""
    return render_window_gate.refuse_reason(card_id)


def _refuse(need: float) -> str:
    hold = HERMES_DIR / "krew-render-hold"
    if hold.is_file():
        try:
            why = hold.read_text(encoding="utf-8").strip()
        except OSError:
            why = ""
        return why or "render hold — no further graphs (Fable morning)"
    why = _window_refuse()
    if why:
        return why
    free = _free_vram_gib()
    up = comfy_up()
    free_s = f"{free:.1f} GiB free" if free is not None else "VRAM unread"
    if gpu_mode() != "render":
        return (
            f"Krew GPU mode is {gpu_mode()} (need render). {free_s}. "
            f"Call krew_gpu_mode(mode='render') or panel Render or "
            f"krew-mode render <recipe>. Do not nvidia-smi a 24 GiB gate "
            f"and do not systemctl-stop brains."
        )
    if not up:
        return (
            f"Comfy is parked (:8188 down). {free_s}; this model wants "
            f"~{need:.0f} GiB plus Comfy itself. Render mode starts Comfy; "
            f"do not start it next to full brains."
        )
    # Hot Comfy already holds weights. Requiring a cold `need` GiB free
    # blocked iteration (9 GiB free after turbo load, 2026-08-19).
    if free is not None and free < 2.0:
        return (
            f"Comfy is up but {free_s} — card is packed, do not start another graph. "
            f"{krew_comfy.get('root')}/AGENTS-HOWTO.md."
        )
    doing = studio_busy()
    if doing:
        return (
            f"The Control Panel studio is {doing} — someone is at the glass with "
            f"Lila, and ops is --max-num-seqs 1, so a second graph would queue "
            f"behind their turn (or stall it). Wait and retry; do not switch "
            f"occupancy to get around it."
        )
    return ""


def _img_ok(**kw: Any) -> Dict[str, Any]:
    try:
        from agent.image_gen_provider import success_response

        return success_response(**kw)
    except ImportError:
        return {"success": True, **kw}


def _img_err(**kw: Any) -> Dict[str, Any]:
    try:
        from agent.image_gen_provider import error_response

        return error_response(**kw)
    except ImportError:
        return {"success": False, "image": None, **kw}


def _vid_ok(**kw: Any) -> Dict[str, Any]:
    try:
        from agent.video_gen_provider import success_response

        return success_response(**kw)
    except ImportError:
        return {"success": True, **kw}


def _vid_err(**kw: Any) -> Dict[str, Any]:
    try:
        from agent.video_gen_provider import error_response

        return error_response(**kw)
    except ImportError:
        return {"success": False, "video": None, **kw}


class HouseComfyImage(_ImageGenBase):
    @property
    def name(self) -> str:
        return "krew-comfy"

    @property
    def display_name(self) -> str:
        return "Krew ComfyUI (local)"

    def is_available(self) -> bool:
        return any(recipe_wired(r) for r in load_recipes())

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": k,
                "display": v["display"],
                "speed": f"~{v['vram_gib']} GiB VRAM",
                "strengths": f"{v.get('kind','t2i')} · {v.get('license','local')}",
                "price": "local",
            }
            for k, v in IMAGE_MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return default_image_model()

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Krew ComfyUI",
            "badge": "local",
            "tag": "Qwen-Image / SDXL on :8188 — park brains first",
            "env_vars": [],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text", "image"], "max_reference_images": 3}

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = "landscape",
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        model = str(kwargs.get("model") or self.default_model() or "")
        spec = IMAGE_MODELS.get(model)
        if spec is None:
            return _img_err(
                error=f"no recipe {model!r} (wired, commercial-OK stills: "
                      f"{', '.join(k for k, v in IMAGE_MODELS.items() if commercial_ok(v)) or 'none'})",
                error_type="invalid_request", provider=self.name, model=model, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
        why = _refuse(float(spec["vram_gib"]))
        if why:
            return _img_err(
                error=why, error_type="backend_unavailable",
                provider=self.name, model=model, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
        src = image_url or ((reference_image_urls or [None])[0])
        if spec.get("kind") == "i2i" and not src:
            return _img_err(
                error=f"{model} is image-to-image. Attach a still.",
                error_type="invalid_request",
                provider=self.name, model=model, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
        if src and spec.get("kind") != "i2i":
            return _img_err(
                error=f"{model} is text-to-image. Use qwen-edit-2511 to edit a photo.",
                error_type="unsupported",
                provider=self.name, model=model, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
        try:
            graph = copy.deepcopy(_load_wf(spec["workflow"]))
            _set_text(graph, spec.get("prompt_node"), prompt)
            w, h = ASPECT.get(aspect_ratio, ASPECT["landscape"])
            _set_size(graph, spec.get("size_nodes") or (), w, h)
            if src and spec.get("image_node"):
                basename = _stage_input_image(src)
                inode = spec["image_node"]
                if inode in graph:
                    graph[inode].setdefault("inputs", {})["image"] = basename
            # A SECOND PICTURE (2026-09-12, the owner: "Lila is the
            # designer"). Qwen-Image-Edit 2511 takes up to three pictures:
            # the first is the canvas (its latent is the frame), the rest
            # are references the prompt can name ("the man in picture 2").
            # Four Bash plates drawn FROM his bust shot all kept the bust's
            # framing — the framing must come from the base picture (Lila's
            # plate) and the face from the reference. image_url = the frame,
            # reference_image_urls = the faces/objects to bring in.
            extra = [u for u in (reference_image_urls or []) if u and u != src][:2]
            if extra:
                enc = [k for k, v in graph.items()
                       if isinstance(v, dict) and v.get("class_type") == "TextEncodeQwenImageEditPlus"]
                for j, u in enumerate(extra, start=2):
                    nid = "ref%d" % j
                    graph[nid] = {"class_type": "LoadImage",
                                  "inputs": {"image": _stage_input_image(u)}}
                    for k in enc:
                        graph[k].setdefault("inputs", {})["image%d" % j] = [nid, 0]
            with lane_busy("drawing"):
                path = _submit(graph, "img")
        except Exception as e:
            return _img_err(
                error=f"{type(e).__name__}: {e}"[:400],
                error_type="provider_error",
                provider=self.name, model=model, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
        return _img_ok(
            image=path, model=model, prompt=prompt,
            aspect_ratio=aspect_ratio, provider=self.name,
            modality="image" if spec.get("kind") == "i2i" else "text",
        )


class HouseComfyVideo(_VideoGenBase):
    @property
    def name(self) -> str:
        return "krew-comfy"

    @property
    def display_name(self) -> str:
        return "Krew ComfyUI (local video)"

    def is_available(self) -> bool:
        return any(
            (WF_DIR / str(v.get("workflow") or "")).is_file()
            for v in VIDEO_MODELS.values()
        )

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": k,
                "display": v["display"],
                "speed": f"~{v['vram_gib']} GiB VRAM",
                "strengths": str(v.get("kind") or "video"),
                "price": "local",
            }
            for k, v in VIDEO_MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return "wan22-i2v"

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Krew ComfyUI video",
            "badge": "local",
            "tag": "WAN 2.2 i2v on :8188 — needs a still + free VRAM",
            "env_vars": [],
        }

    def capabilities(self) -> Dict[str, Any]:
        # Hermes video_generate schema diet: image_url is advertised only
        # when "image" is in modalities. "video" is not a modality — that
        # lie hid hub PNGs and flf2v died with "needs first_image".
        return {"modalities": ["text", "image"], "max_reference_images": 2}

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        mid = model or self.default_model() or "wan22-i2v"
        spec = VIDEO_MODELS.get(mid) or VIDEO_MODELS["wan22-i2v"]
        # Wall FLF is submit/collect (kanban or CP). 15 s LTX 2.5 I2V is a
        # Control Panel chip: in-turn when there is no card. A kanban worker
        # still submits and ends the turn (600 s /history poll is not a path).
        wall = mid in {"ltx23-flf2v", "ltx25-flf2v"}
        worker_i2v = mid == "ltx25-i2v" and bool(os.environ.get("HERMES_KANBAN_TASK"))
        if wall or worker_i2v:
            return _vid_err(
                error=(
                    f"{mid} is a wall/long mint. "
                    "python3 ~/.krew/crew/scripts/render-submit.py "
                    "--card <id> --name <name> --recipe %s --seed <png> "
                    "--plate <png> --prompt-file <txt> then END THE TURN. "
                    "render-collect lands the mp4. Do not poll /history."
                    % mid
                ),
                error_type="invalid_request", provider=self.name,
            )
        why = _refuse(float(spec["vram_gib"]))
        if why:
            return _vid_err(
                error=why, error_type="backend_unavailable", provider=self.name,
            )
        src = image_url or ((reference_image_urls or [None])[0])
        kind = str(spec.get("kind") or "i2v")
        refs = list(reference_image_urls or [])
        first = kwargs.get("first_image") or kwargs.get("first_image_url") or src
        last = kwargs.get("last_image") or kwargs.get("last_image_url")
        if not last and len(refs) > 1:
            last = refs[1]
        if kind == "flf2v":
            if not first:
                return _vid_err(
                    error=(
                        "ltx23-flf2v needs hub PNGs. Hermes video_generate "
                        "forwards image_url (first) and reference_image_urls[1] "
                        "(last). Same image_url both ends = Law 1 loop."
                    ),
                    error_type="invalid_request", provider=self.name,
                )
            last = last or first
        elif kind != "t2v" and not src:
            return _vid_err(
                error=f"{mid} is image-to-video. Attach a still.",
                error_type="invalid_request", provider=self.name,
            )
        try:
            graph = copy.deepcopy(_load_wf(spec["workflow"]))
            _set_text(graph, spec.get("prompt_node"), prompt)
            if negative_prompt:
                for nid in ("5", "10"):
                    node = graph.get(nid)
                    if isinstance(node, dict) and str(node.get("class_type") or "") == "CLIPTextEncode":
                        node.setdefault("inputs", {})["text"] = negative_prompt
                        break
            if spec.get("width") and spec.get("height"):
                _set_size(graph, ("7",), int(spec["width"]), int(spec["height"]))
            if spec.get("length") is not None and "7" in graph:
                graph["7"].setdefault("inputs", {})["length"] = int(spec["length"])
            inode = spec.get("image_node") or spec.get("first_image_node")
            if src and inode and inode in graph:
                graph[inode].setdefault("inputs", {})["image"] = _stage_input_image(src)
            if kind == "flf2v":
                fn = spec.get("first_image_node") or "12"
                ln = spec.get("last_image_node") or "13"
                if fn in graph:
                    graph[fn].setdefault("inputs", {})["image"] = _stage_input_image(str(first))
                if ln in graph:
                    graph[ln].setdefault("inputs", {})["image"] = _stage_input_image(str(last))
                # Official 2.3 FLF: strength 0.7, CRF 25. Recipe SoT wins at submit.
                _apply_flf_guides(graph, spec)
            with lane_busy("filming"):
                path = _submit(graph, "vid")
        except Exception as e:
            return _vid_err(
                error=f"{type(e).__name__}: {e}"[:400],
                error_type="provider_error", provider=self.name,
            )
        return _vid_ok(
            video=path, model=mid, prompt=prompt,
            modality="text" if kind == "t2v" else "image",
            aspect_ratio=aspect_ratio,
            duration=int(duration or (4 if kind == "flf2v" else 2)),
            provider=self.name,
        )


# --- occupancy: let the render crew send themselves to Render ---------------
# The refusals above have always ended "Hit Render on the Control Panel" or
# "Run: krew-mode render turbo" — instructions for a human. That is the whole
# block: an agent could see exactly what was wrong and had no way to fix it.
# a render card (2026-08-27) died on it — LTX needed ~25-35 GiB, the card had 5.3,
# and the vLLM /sleep route is tier0 'assent' by design after a worker put the
# live 35B to sleep in 2026-07.
#
# This does NOT reopen that route. It shells `krew-mode`, the sanctioned
# switcher. Canonical Render systemd-stops ops :8000 (composer), starts
# Comfy (no --highvram), and keeps the crew engine so Ada/Lila/Vera/Bash stay
# on a live 27B. Voice ON keeps the voice server. Crissa and Steward sleep. tier0 does
# not gate `krew-mode`; the krew prod lock is enforced inside it, so
# this tool inherits that refusal instead of routing around it.
KREW_MODE_BIN = Path(
    os.environ.get(
        "KREW_MODE_BIN",
        os.path.expanduser(os.environ.get("KREW_MODE_PATH") or "~/.krew/bin/krew-mode"),
    )
)
# beast and off are deliberately absent. Both park the brains wholesale; they
# are a human's call at the panel, not a step inside a render loop.
GPU_MODES = ("status", "render", "normal")
_MODE_LOCK = Path(os.environ.get("KREW_GPU_MODE_LOCK", "/tmp/krew-gpu-mode.lock"))


def _krew_mode_run(argv: list, timeout: float) -> tuple:
    """Run krew-mode under a lock. Two agents switching at once is a wedge."""
    import fcntl
    import subprocess

    _MODE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_MODE_LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 111, "", "another occupancy switch is already running"
        try:
            r = subprocess.run(
                [sys.executable, str(KREW_MODE_BIN)] + argv,
                capture_output=True, text=True, timeout=timeout,
            )
            return r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            return 124, "", f"krew-mode {' '.join(argv)} timed out after {timeout:.0f}s"
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def krew_gpu_mode(args, **_kw):
    """Read or change krew GPU occupancy. Always answers with the real state."""
    args = args if isinstance(args, dict) else {}
    mode = str(args.get("mode") or "status").strip().lower()
    recipe = str(args.get("recipe") or "").strip().lower()

    if mode not in GPU_MODES:
        return json.dumps({
            "ok": False,
            "error": f"mode must be one of {', '.join(GPU_MODES)} (got {mode!r})",
            "note": "beast and off are a human's call at the Control Panel.",
        })
    if mode == "render":
        why = _window_refuse()
        if why:
            return json.dumps({"ok": False, "asked": "render", "error": why})
    if not KREW_MODE_BIN.is_file():
        return json.dumps({"ok": False, "error": f"missing {KREW_MODE_BIN}"})

    argv = [mode]
    if mode == "render" and recipe:
        argv.append(recipe)
    # Render starts Comfy and waits on VRAM; normal waits for the brains back.
    timeout = 20.0 if mode == "status" else 900.0
    rc, out, err = _krew_mode_run(argv, timeout)

    # Never report the command, report the card. A switch that was accepted and
    # did not land is exactly the lie this install has been bitten by before, so
    # the answer is always a fresh status read.
    srt, sout, _serr = _krew_mode_run(["status"], 20.0)
    state = {}
    for line in (sout or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            state[parts[0]] = parts[1].strip()

    landed = (mode == "status") or (state.get("mode") == mode)
    body = {
        "ok": bool(rc == 0 and landed),
        "asked": mode,
        "recipe": recipe or None,
        "mode": state.get("mode"),
        "comfy": state.get("comfy"),
        "free_vram": state.get("free"),
        "ops": state.get("ops"),
        "crew": state.get("crew"),
        "status": sout.strip()[:1200],
    }
    if rc != 0:
        body["error"] = (err or out or "krew-mode failed").strip()[:400]
    elif not landed:
        body["error"] = (
            f"krew-mode returned 0 but the flag reads {state.get('mode')!r}, "
            f"not {mode!r} — check the krew prod lock."
        )
    return json.dumps(body)


def register(ctx) -> None:
    ctx.register_image_gen_provider(HouseComfyImage())
    ctx.register_video_gen_provider(HouseComfyVideo())
    # Not local, not shipping: drop xAI Imagine edit/extend from video_gen.
    # Keep video_generate (this plugin). Render mode is a later krew mode.
    _strip_xai_video_tools()
    # Hermes image_generate used to ignore args.model (video_generate does not).
    install_image_model_arg()
    # register_tool's real signature is (name, toolset, schema, handler, ...).
    # `toolset` and `schema` are required and there is no `parameters` kwarg —
    # calling it the wrong way raises TypeError, and a bare `except: pass`
    # turns that into a tool that simply never exists. Log loudly instead:
    # a silently missing tool is the failure this whole card was about.
    try:
        ctx.register_tool(
            name="krew_gpu_mode",
            toolset="image_gen",
            description=(
                "Read or change krew GPU occupancy so you can render without "
                "asking a human. mode=status reads it; mode=render systemd-stops "
                "ops :8000 (Crissa's composer) and starts Comfy (no --highvram), "
                "keeping the crew engine (Ada/Lila/Vera/Bash). Voice ON keeps the voice server. "
                "Crissa and Steward sleep. mode=normal SIGKILLs Comfy and "
                "cold-boots ops (~2 min). Do not write /tmp/krew-train-hold or "
                "systemctl-stop brains. Optional recipe sizes the slice: "
                "sdxl=10G qwen=30G qwen-edit=30G wan=22G (opt-in drafts: turbo=20G "
                "klein=10G max=36G). Call this when image_generate or "
                "video_generate refuses with 'Krew GPU mode is ... (need "
                "render)'. Answers with the occupancy flag as it actually reads "
                "afterwards, not with whether the command was accepted."
            ),
            schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": list(GPU_MODES),
                        "description": "status | render | normal",
                    },
                    "recipe": {
                        "type": "string",
                        "description": "render only: which model slice to fit",
                    },
                },
            },
            handler=krew_gpu_mode,
        )
    except Exception as e:
        logger.error("krew-comfy: krew_gpu_mode did NOT register (%s: %s)",
                     type(e).__name__, e)


def _image_model_schema_entry() -> dict:
    ids = ", ".join(IMAGE_MODELS.keys())
    return {
        "type": "string",
        "description": (
            "Krew Comfy recipe id for this still. "
            f"Ids: {ids}. "
            "Omit to use image_gen.model (default: the first commercial-OK still, qwen-image-2512)."
        ),
    }


def install_image_model_arg() -> bool:
    """Make the real image_generate tool accept model=recipe id.

    video_generate already forwards args.model. image_generate did not —
    it only passed image_gen.model from config, so Lila/Bash could not pick
    SDXL/Qwen/an opt-in draft engine. Patch schema + handler; caller's
    model= wins over config.
    """
    try:
        import tools.image_generation_tool as igt
        from tools.registry import registry
    except ImportError:
        return False

    props = (igt.IMAGE_GENERATE_SCHEMA.get("parameters") or {}).get("properties")
    if isinstance(props, dict):
        props["model"] = _image_model_schema_entry()

    orig_handle = igt._handle_image_generate
    if getattr(orig_handle, "_krew_comfy_model", False):
        return True

    def handle(args, **kw):
        model = ""
        if isinstance(args, dict):
            model = str(args.get("model") or "").strip()
        if not model:
            return orig_handle(args, **kw)
        prev = igt._read_configured_image_model
        igt._read_configured_image_model = lambda: model
        try:
            return orig_handle(args, **kw)
        finally:
            igt._read_configured_image_model = prev

    handle._krew_comfy_model = True  # type: ignore[attr-defined]
    igt._handle_image_generate = handle
    entry = registry.get_entry("image_generate")
    if entry is not None:
        entry.handler = handle
        orig_dyn = entry.dynamic_schema_overrides

        def dyn():
            out: Dict[str, Any] = {}
            if callable(orig_dyn):
                try:
                    got = orig_dyn()
                    if isinstance(got, dict):
                        out = dict(got)
                except Exception:
                    out = {}
            desc = str(out.get("description") or "")
            extra = (
                " Pass `model` as a krew recipe id: "
                + ", ".join(IMAGE_MODELS.keys())
                + ". Omit for the configured default."
            )
            if "Pass `model` as a krew recipe id" not in desc:
                desc = desc.replace(
                    "and model are user-configured and not selectable by the agent. ",
                    "",
                )
                out["description"] = (desc + extra).strip()
            return out

        entry.dynamic_schema_overrides = dyn
    return True


def _strip_xai_video_tools() -> None:
    drop = {"xai_video_edit", "xai_video_extend"}
    try:
        import toolsets as ts
    except Exception:
        return
    spec = getattr(ts, "TOOLSETS", {}).get("video_gen")
    if not isinstance(spec, dict):
        return
    tools = spec.get("tools") or []
    spec["tools"] = [t for t in tools if t not in drop]
