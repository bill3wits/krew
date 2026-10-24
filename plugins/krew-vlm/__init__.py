"""krew-vlm: look at a render AGAINST its target (lifted from the reference install, 2026-09-27).

The owner: "they NEED image and video VLM abilities when rendering that has been the problem why
their renders suck." Measured the same day: the vision brain sees a still in 9 s and
a clip in 1 s, and the framework's `vision_analyze` / `video_analyze` both answer for Lila and Vera. What
was missing is the COMPARISON: each of those tools takes ONE file, so a render was described on its
own ("a meticulously composed interior") and never seen beside the reference it had to match.
In one call, the brain given the target and the clip answered: face morphs at 3.8 s, identity changes
at 4.7 s, does not end on the target, FAIL.

`look_compare(target, render, spec?)`
  target  the reference still the render must match (absolute path)
  render  the rendered still, or the rendered clip (mp4 / webm / mov)
  spec    what the render was asked to be, in words (optional)

A still is compared to the target in one look. A clip gets three looks: the target beside the
clip's FIRST and LAST frame (does it start and end on the target, what a seam needs), the
target beside the whole clip (does identity hold, where does it break), and the target beside frames
sampled through its middle, one a second (the owner 2026-10-08: not only the ends). The verdict is FAIL when
any look fails, and UNREADABLE, never PASS, when the brain's answer could not be read.

Nothing here generates, edits or moves a file. Kill: KREW_VLM_COMPARE=0 (the tool answers that it
is off). The brain is `auxiliary.vision` from the profile's own config, read at call time.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

TOOL = "look_compare"
STILLS = (".png", ".jpg", ".jpeg", ".webp")
CLIPS = (".mp4", ".webm", ".mov", ".mkv")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}
MAX_STILL_MB = float(os.environ.get("KREW_VLM_MAX_STILL_MB", "12"))
MAX_CLIP_MB = float(os.environ.get("KREW_VLM_MAX_CLIP_MB", "40"))
FALLBACK = {"base_url": os.environ.get("KREW_VLM_URL") or "http://127.0.0.1:8000/v1", "model": "crew", "api_key": "sk-local", "timeout": 180}

STILL_Q = (
    "Image 1 is the TARGET reference. Image 2 is a RENDER that must match it.{spec}\n"
    "Compare them as a strict reviewer. Answer with JSON only, no prose, no code fence:\n"
    '{{"same_person": true|false, "same_pose": true|false, "same_framing": true|false, '
    '"same_lighting": true|false, "differences": ["most visible first, short"], '
    '"defects": ["anatomy, hands, eyes, teeth, text, artifacts - only what you see"], '
    '"verdict": "PASS"|"FAIL", "fix": "the one change that would most improve the next render"}}\n'
    "PASS only if a viewer would not notice a jump when the render is cut against the target."
)
ENDS_Q = (
    "Image 1 is the TARGET reference. Image 2 is the FIRST frame of a rendered clip, image 3 is its "
    "LAST frame. The clip must start and end on the target.{spec}\n"
    "Answer with JSON only, no prose, no code fence:\n"
    '{{"starts_on_target": true|false, "ends_on_target": true|false, '
    '"first_frame_differences": ["short"], "last_frame_differences": ["short"], '
    '"verdict": "PASS"|"FAIL"}}\n'
    "PASS only if a viewer would not notice a jump at either end."
)
FRAMES_Q = (
    "Image 1 is the TARGET reference for a rendered clip. The images after it are frames sampled through the "
    "clip, about {every} second apart, in order.{spec}\n"
    "Judge the middle of the clip, not only its ends. Answer with JSON only, no prose, no code fence:\n"
    '{{"identity_holds": true|false, "steady": true|false, '
    '"defects": ["short, each with the frame number it first shows in"], '
    '"verdict": "PASS"|"FAIL", "fix": "the one change that would most improve the next render"}}\n'
    "FAIL on a morphing face, identity drift, warped hands, flicker or a jump between two frames."
)
# frames sampled through a clip for the frames look (the owner 2026-10-08: not only first and last)
FRAME_EVERY_S = float(os.environ.get("KREW_VLM_FRAME_EVERY_S", "1.0"))
FRAME_MAX = int(os.environ.get("KREW_VLM_FRAME_MAX", "12"))
CLIP_Q = (
    "Image 1 is the TARGET reference for a rendered clip. Then the clip itself.{spec}\n"
    "Watch the whole clip. Answer with JSON only, no prose, no code fence:\n"
    '{{"identity_holds": true|false, "motion": "one sentence: what moves", '
    '"defects": ["short, each with the approximate second"], "verdict": "PASS"|"FAIL", '
    '"fix": "the one change that would most improve the next render"}}\n'
    "FAIL on any morphing face, identity drift, warped hands, flicker or a jump cut."
)


def _on() -> bool:
    return os.environ.get("KREW_VLM_COMPARE", "1").strip().lower() not in ("0", "false", "no", "off")


def brain() -> dict:
    """`auxiliary.vision` of the profile this call is for, read now; crew when it cannot be read."""
    cfg: dict = {}
    try:
        from hermes_cli.config import load_config
        aux = (load_config() or {}).get("auxiliary") or {}
        cfg = dict(aux.get("vision") or {})
    except Exception:  # noqa: BLE001
        cfg = {}
    out = dict(FALLBACK)
    for k in ("base_url", "model", "api_key", "timeout"):
        if cfg.get(k):
            out[k] = cfg[k]
    out["extra_body"] = cfg.get("extra_body") if isinstance(cfg.get("extra_body"), dict) else {}
    return out


def _part(path: Path) -> dict:
    suffix = path.suffix.lower()
    kind = "video_url" if suffix in CLIPS else "image_url"
    data = base64.b64encode(path.read_bytes()).decode()
    return {"type": kind, kind: {"url": f"data:{MIME[suffix]};base64,{data}"}}


def _check(path_text: str, what: str, kinds: tuple, limit_mb: float) -> tuple[Path | None, str]:
    raw = str(path_text or "").strip()
    if not raw:
        return None, f"{what} is required: an absolute path"
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        return None, f"{what} must be an absolute path, got {raw!r}"
    if not p.is_file():
        return None, f"{what} not found: {p}"
    if p.suffix.lower() not in kinds:
        return None, f"{what} is {p.suffix or 'no extension'}; expected one of {', '.join(kinds)}"
    size = p.stat().st_size / 1e6
    if size == 0:
        return None, f"{what} is an empty file: {p}"
    if size > limit_mb:
        return None, f"{what} is {size:.0f} MB, over the {limit_mb:.0f} MB cap"
    return p, ""


def ask(parts: list, *, post=None) -> str:
    """One chat call to the vision brain. Returns the text, or raises RuntimeError with the reason."""
    b = brain()
    body = {"model": b["model"], "messages": [{"role": "user", "content": parts}],
            "max_tokens": 700, "temperature": 0.1}
    extra = dict(b.get("extra_body") or {})
    extra.setdefault("chat_template_kwargs", {"enable_thinking": False})
    body.update({k: v for k, v in extra.items() if k not in body})
    if post is not None:
        return post(b, body)
    req = urllib.request.Request(str(b["base_url"]).rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {b['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=float(b["timeout"])) as r:
            doc = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"the vision brain answered HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"the vision brain did not answer ({type(e).__name__}: {str(e)[:160]})")
    try:
        return str(doc["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("the vision brain answered without a message")


def read_json(text: str) -> dict | None:
    """The brain's JSON, with or without a code fence or a sentence around it."""
    s = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None
        s = s[i:j + 1]
    try:
        doc = json.loads(s)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _verdict(doc: dict | None) -> str:
    if not doc:
        return "UNREADABLE"
    v = str(doc.get("verdict") or "").strip().upper()
    return v if v in ("PASS", "FAIL") else "UNREADABLE"


def end_frames(clip: Path, workdir: Path, *, run=subprocess.run) -> tuple[Path | None, Path | None, str]:
    """The clip's first and last frame as PNGs. ("", "", why) when ffmpeg cannot make them."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return None, None, "ffmpeg is not on PATH"
    first, last = workdir / "first.png", workdir / "last.png"
    for out, seek in ((first, ["-i", str(clip)]), (last, ["-sseof", "-0.15", "-i", str(clip)])):
        try:
            run([ff, "-v", "error", "-y", *seek, "-frames:v", "1", str(out)],
                capture_output=True, text=True, timeout=60)
        except Exception as e:  # noqa: BLE001
            return None, None, f"ffmpeg failed ({type(e).__name__})"
        if not out.is_file() or out.stat().st_size == 0:
            return None, None, f"ffmpeg wrote no {out.stem} frame"
    return first, last, ""


def sample_frames(clip: Path, workdir: Path, *, every_s: float | None = None, limit: int | None = None,
                  run=subprocess.run) -> tuple[list, str]:
    """Frames through the clip, one every `every_s` seconds (at most `limit`), as PNGs. ([], why) when ffmpeg
    cannot make them. The ends look sees the first and last frame; this is the middle."""
    ff = shutil.which("ffmpeg")
    if not ff:
        return [], "ffmpeg is not on PATH"
    every = float(every_s if every_s is not None else FRAME_EVERY_S) or 1.0
    cap = int(limit if limit is not None else FRAME_MAX)
    try:
        run([ff, "-v", "error", "-y", "-i", str(clip), "-vf", f"fps=1/{every:g}", "-frames:v", str(cap),
             str(workdir / "f%03d.png")], capture_output=True, text=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        return [], f"ffmpeg failed ({type(e).__name__})"
    frames = sorted(f for f in workdir.glob("f*.png") if f.stat().st_size > 0)
    if not frames:
        return [], "ffmpeg wrote no sampled frames"
    return frames[:cap], ""


def compare(target: str, render: str, spec: str = "", *, post=None, run=subprocess.run) -> dict:
    if not _on():
        return {"ok": False, "verdict": "OFF", "error": "look_compare is switched off (KREW_VLM_COMPARE=0)"}
    t, why = _check(target, "target", STILLS, MAX_STILL_MB)
    if not t:
        return {"ok": False, "verdict": "NOT RUN", "error": why}
    r, why = _check(render, "render", STILLS + CLIPS, MAX_CLIP_MB)
    if not r:
        return {"ok": False, "verdict": "NOT RUN", "error": why}
    spec_line = f"\nThe render was asked to be: {spec.strip()[:600]}" if str(spec or "").strip() else ""
    out: dict = {"ok": True, "target": str(t), "render": str(r), "kind": "clip" if r.suffix.lower() in CLIPS else "still"}
    looks: dict = {}
    try:
        if out["kind"] == "still":
            text = ask([{"type": "text", "text": STILL_Q.format(spec=spec_line)}, _part(t), _part(r)], post=post)
            looks["match"] = {"answer": read_json(text), "raw": None if read_json(text) else text[:600]}
        else:
            with tempfile.TemporaryDirectory(prefix="krew-vlm-") as d:
                first, last, why = end_frames(r, Path(d), run=run)
                if first and last:
                    text = ask([{"type": "text", "text": ENDS_Q.format(spec=spec_line)},
                                _part(t), _part(first), _part(last)], post=post)
                    looks["ends"] = {"answer": read_json(text), "raw": None if read_json(text) else text[:600]}
                else:
                    looks["ends"] = {"answer": None, "raw": None, "skipped": why}
            text = ask([{"type": "text", "text": CLIP_Q.format(spec=spec_line)}, _part(t), _part(r)], post=post)
            looks["clip"] = {"answer": read_json(text), "raw": None if read_json(text) else text[:600]}
            with tempfile.TemporaryDirectory(prefix="krew-vlm-frames-") as d:
                frames, why = sample_frames(r, Path(d), run=run)
                if frames:
                    text = ask([{"type": "text", "text": FRAMES_Q.format(spec=spec_line, every=f"{FRAME_EVERY_S:g}")},
                                _part(t), *[_part(f) for f in frames]], post=post)
                    looks["frames"] = {"answer": read_json(text), "raw": None if read_json(text) else text[:600],
                                       "n": len(frames)}
                else:
                    looks["frames"] = {"answer": None, "raw": None, "skipped": why}
    except RuntimeError as e:
        return {**out, "ok": False, "verdict": "NOT RUN", "error": str(e), "looks": looks}
    out["looks"] = looks
    verdicts = [_verdict(v.get("answer")) for v in looks.values() if not v.get("skipped")]
    out["verdict"] = ("FAIL" if "FAIL" in verdicts else
                      "UNREADABLE" if (not verdicts or "UNREADABLE" in verdicts) else "PASS")
    fixes = [str((v.get("answer") or {}).get("fix") or "").strip() for v in looks.values()]
    out["fix"] = next((f for f in fixes if f), "")
    if out["verdict"] == "UNREADABLE":
        out["note"] = "the brain's answer could not be read as a verdict; this is NOT a pass - look again or ask Vera"
    return out


SCHEMA = {
    "name": TOOL,
    "description": (
        "Look at a render AGAINST its target reference, in one look. Give the target still and the "
        "rendered still or clip (absolute paths). Returns PASS or FAIL with the visible differences, "
        "the defects (with the second, for a clip) and the one fix that would most improve the next "
        "render. A clip is also checked for starting and ending on the target, and frame by frame through its middle (one frame a second). Use this before you "
        "call a render done, and again after every re-render. vision_analyze and video_analyze "
        "describe ONE file; this compares two."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Absolute path of the reference still (png/jpg/webp) the render must match."},
            "render": {"type": "string", "description": "Absolute path of the rendered still, or of the rendered clip (mp4/webm/mov)."},
            "spec": {"type": "string", "description": "Optional: what the render was asked to be, in your own words."},
        },
        "required": ["target", "render"],
    },
}


def look_compare(args: dict, **kw) -> str:
    args = args if isinstance(args, dict) else {}
    try:
        return json.dumps(compare(args.get("target", ""), args.get("render", ""), args.get("spec", "") or ""),
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 - a look that raises must read as NOT RUN, never as a pass
        logger.exception("krew-vlm: look_compare failed")
        return json.dumps({"ok": False, "verdict": "NOT RUN", "error": f"{type(e).__name__}: {str(e)[:200]}"})


def register(ctx) -> None:
    try:
        ctx.register_tool(name=TOOL, toolset="vision", schema=SCHEMA, handler=look_compare,
                          description=SCHEMA["description"], emoji="👁️")
        logger.info("krew-vlm: %s registered on toolset vision", TOOL)
    except Exception as e:  # noqa: BLE001
        logger.error("krew-vlm: %s NOT registered (%s: %s)", TOOL, e.__class__.__name__, str(e)[:200])
