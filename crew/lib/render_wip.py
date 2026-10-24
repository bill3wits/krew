"""Render work-in-progress — what Comfy is drawing RIGHT NOW, for the studio stage.

the owner 2026-09-18: "That screen is to show work in progress only. Display blank
screen with note 'Please wait...will display work-in-progress LIVE'. Show WIP
always - every version like the workspace can be watched in action."

The ComfyUI workspace watches a draw through its websocket: `progress` (step
value/max + node), a latent preview frame every step, `executed` when a file
lands. Comfy sends those ONLY to the websocket whose `clientId` equals the
prompt's `client_id` (server.py `send_bytes(..., sid)`: an unknown sid is
dropped, not broadcast). So every house submitter posts with ONE client id
(`CLIENT_ID`, "krew-wip") and this watcher holds that websocket. Previews
need no Comfy restart: `extra_data.preview_method` is a per-prompt override
(execution.py `set_preview_method`); "auto" falls back to latent2rgb with no
TAESD decoder on disk.

Files (`~/.hermes/render-wip/`): `state.json` (phase, step, node, preview seq,
what landed) and `preview.img` (the newest preview frame, JPEG or PNG — the
mime is in the state). Both are written atomically. The :8099 server runs
`watch_forever()` in a thread and serves `/v1/render-wip`, `/render-wip.img`
and `/render-out/<rel>` (every version under <comfy root>/output, crew
subfolders included). Kill: `KREW_RENDER_WIP=0` on the server.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional
try:
    import krew_comfy  # one Comfy location for krew and the Wall: render.json, else KREW_COMFY_*, else ~/ComfyUI
except ImportError:  # loaded by path without its folder on sys.path: the copy beside this file
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("krew_comfy", Path(__file__).resolve().with_name("krew_comfy.py"))
    krew_comfy = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(krew_comfy)

HERMES = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
MODE_FILE = Path(os.environ.get("KREW_GPU_MODE_FILE", str(HERMES / "krew-gpu-mode")))
WIP_DIR = Path(os.environ.get("KREW_RENDER_WIP_DIR", str(HERMES / "render-wip")))
STATE = WIP_DIR / "state.json"
PREVIEW = WIP_DIR / "preview.img"
OUT_DIR = Path(krew_comfy.get("output"))
COMFY = krew_comfy.get("url")
# The one client id every house submitter posts with (plugin `_submit`,
# scripts/render-submit.py). Change it here AND there, or the stage goes blind.
CLIENT_ID = os.environ.get("KREW_COMFY_CLIENT_ID", "krew-wip")
PREVIEW_METHOD = os.environ.get("KREW_COMFY_PREVIEW", "auto")
# a "drawing" older than this with no message is a dead watcher, not a draw
STALE_S = float(os.environ.get("KREW_RENDER_WIP_STALE_S", "120"))
VERSION_SUFFIXES = (".png", ".jpg", ".jpeg", ".mp4", ".webm")

# Comfy protocol.py BinaryEventTypes
_EV_PREVIEW = 1
_EV_PREVIEW_META = 4

_lock = threading.Lock()


def prompt_body(graph: dict, *, client_id: str = CLIENT_ID, preview: str = PREVIEW_METHOD) -> dict:
    """The POST /prompt body a house submitter sends."""
    body: dict[str, Any] = {"prompt": graph, "client_id": client_id}
    if preview and preview != "none":
        body["extra_data"] = {"preview_method": preview}
    return body


def _mode() -> str:
    try:
        return MODE_FILE.read_text().strip().splitlines()[0].split()[0].lower()
    except (OSError, IndexError):
        return ""


def render_since() -> float:
    """When this Render engaged: the mode file's mtime. 0 off Render."""
    if _mode() != "render":
        return 0.0
    try:
        return MODE_FILE.stat().st_mtime
    except OSError:
        return 0.0


def comfy_up(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{COMFY}/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _blank(phase: str = "idle") -> dict[str, Any]:
    return {
        "phase": phase, "prompt_id": None, "node": None, "value": 0, "max": 0,
        "queue": 0, "preview_seq": 0, "preview_ts": 0.0, "preview_mime": "",
        "preview_prompt": None, "started": 0.0, "ended": 0.0, "error": "",
        "landed": [], "connected": False, "client": CLIENT_ID, "updated": time.time(),
    }


class Tracker:
    """Comfy's websocket messages → one state dict (+ the preview file)."""

    def __init__(self, *, write: bool = True):
        self.st = _blank()
        self.write = write

    # -- text ---------------------------------------------------------------
    def on_text(self, msg: dict) -> None:
        t = str(msg.get("type") or "")
        d = msg.get("data") if isinstance(msg.get("data"), dict) else {}
        st = self.st
        if t == "status":
            try:
                st["queue"] = int(((d.get("status") or {}).get("exec_info") or {}).get("queue_remaining") or 0)
            except (TypeError, ValueError):
                st["queue"] = 0
            if st["phase"] in ("idle", "done", "error") and st["queue"] > 0:
                st["phase"] = "queued"
        elif t == "execution_start":
            st.update(phase="drawing", prompt_id=d.get("prompt_id"), node=None, value=0, max=0,
                      started=time.time(), ended=0.0, error="", landed=[])
        elif t == "executing":
            node = d.get("node")
            if node is None:
                if st["phase"] == "drawing":
                    st["phase"] = "done"
                    st["ended"] = time.time()
            else:
                st["node"] = str(node)
                if st["phase"] != "drawing":
                    st.update(phase="drawing", prompt_id=d.get("prompt_id") or st.get("prompt_id"),
                              started=st.get("started") or time.time())
        elif t == "progress":
            try:
                st["value"] = int(d.get("value") or 0)
                st["max"] = int(d.get("max") or 0)
            except (TypeError, ValueError):
                pass
            if d.get("node") is not None:
                st["node"] = str(d.get("node"))
            if st["phase"] != "drawing":
                st.update(phase="drawing", prompt_id=d.get("prompt_id") or st.get("prompt_id"),
                          started=st.get("started") or time.time())
        elif t == "executed":
            out = d.get("output") if isinstance(d.get("output"), dict) else {}
            for key in ("images", "gifs", "videos"):
                for item in out.get(key) or []:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    if item.get("type") not in (None, "output"):
                        continue
                    sub = str(item.get("subfolder") or "").strip("/")
                    rel = f"{sub}/{item['filename']}" if sub else str(item["filename"])
                    if rel not in st["landed"]:
                        st["landed"] = (st["landed"] + [rel])[-8:]
        elif t == "execution_success":
            st["phase"] = "done"
            st["ended"] = time.time()
        elif t == "execution_error":
            st["phase"] = "error"
            st["ended"] = time.time()
            st["error"] = str(d.get("exception_message") or d.get("exception_type") or "error")[:200]
        elif t == "execution_interrupted":
            st["phase"] = "interrupted"
            st["ended"] = time.time()
        else:
            return
        self._flush()

    # -- binary -------------------------------------------------------------
    @staticmethod
    def parse_binary(data: bytes) -> tuple[bytes, str] | None:
        """(image bytes, mime) from one Comfy binary frame, else None."""
        if len(data) < 8:
            return None
        ev = int.from_bytes(data[:4], "big")
        if ev == _EV_PREVIEW:
            fmt = int.from_bytes(data[4:8], "big")
            return bytes(data[8:]), ("image/png" if fmt == 2 else "image/jpeg")
        if ev == _EV_PREVIEW_META:
            mlen = int.from_bytes(data[4:8], "big")
            try:
                meta = json.loads(bytes(data[8:8 + mlen]).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                meta = {}
            mime = str(meta.get("image_type") or "image/jpeg")
            return bytes(data[8 + mlen:]), mime
        return None

    def on_binary(self, data: bytes) -> bool:
        got = self.parse_binary(data)
        if not got:
            return False
        img, mime = got
        if not img:
            return False
        st = self.st
        st["preview_seq"] = int(st.get("preview_seq") or 0) + 1
        st["preview_ts"] = time.time()
        st["preview_mime"] = mime
        st["preview_prompt"] = st.get("prompt_id")
        if self.write:
            with _lock:
                _atomic_write(PREVIEW, img)
        self._flush()
        return True

    # -- state --------------------------------------------------------------
    def set_connected(self, on: bool) -> None:
        self.st["connected"] = bool(on)
        self._flush()

    def reset(self, phase: str = "idle") -> None:
        keep_seq = int(self.st.get("preview_seq") or 0)
        self.st = _blank(phase)
        self.st["preview_seq"] = keep_seq
        self._flush()

    def _flush(self) -> None:
        self.st["updated"] = time.time()
        if not self.write:
            return
        with _lock:
            _atomic_write(STATE, json.dumps(self.st).encode("utf-8"))


def read_state() -> dict[str, Any]:
    """The watcher's state for a poll; `stale` when it stopped talking."""
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = _blank()
        st["updated"] = 0.0
    if not isinstance(st, dict):
        st = _blank()
    now = time.time()
    age = now - float(st.get("updated") or 0)
    st["age"] = round(age, 1)
    st["stale"] = bool(st.get("phase") in ("drawing", "queued") and age > STALE_S)
    st["preview_url"] = (
        f"/render-wip.img?s={int(st.get('preview_seq') or 0)}"
        if st.get("preview_seq") and PREVIEW.is_file() else None
    )
    return st


def preview_bytes() -> tuple[Optional[bytes], str]:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        mime = str(st.get("preview_mime") or "image/jpeg")
    except (OSError, ValueError, AttributeError):
        mime = "image/jpeg"
    try:
        return PREVIEW.read_bytes(), mime
    except OSError:
        return None, ""


def _rel_ok(rel: str) -> Optional[Path]:
    rel = (rel or "").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    root = OUT_DIR.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if path.suffix.lower() not in VERSION_SUFFIXES or not path.is_file():
        return None
    return path


def version_bytes(rel: str) -> tuple[Optional[bytes], str]:
    """Allowlisted read of one output (png/jpg/mp4/webm) anywhere under OUT_DIR."""
    p = _rel_ok(rel)
    if not p:
        return None, ""
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".mp4": "video/mp4", ".webm": "video/webm",
    }[p.suffix.lower()]
    try:
        return p.read_bytes(), mime
    except OSError:
        return None, ""


def versions(since: float, *, limit: int = 24, depth: int = 3) -> list[dict[str, Any]]:
    """Every output that landed since `since` (newest first): the stage shows
    the newest, the strip lists them all. Crew subfolders included."""
    if not since:
        return []
    rows: list[tuple[float, str, str]] = []
    root = OUT_DIR
    try:
        stack = [(root, 0)]
        while stack:
            d, lvl = stack.pop()
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if lvl < depth and not e.name.startswith("."):
                                stack.append((Path(e.path), lvl + 1))
                            continue
                        if not e.is_file(follow_symlinks=False):
                            continue
                        suf = os.path.splitext(e.name)[1].lower()
                        if suf not in VERSION_SUFFIXES:
                            continue
                        m = e.stat().st_mtime
                    except OSError:
                        continue
                    if m + 1 < since:
                        continue
                    rel = os.path.relpath(e.path, root)
                    rows.append((m, rel, "video" if suf in (".mp4", ".webm") else "still"))
    except OSError:
        return []
    rows.sort(key=lambda r: r[0], reverse=True)
    out = []
    for m, rel, kind in rows[:limit]:
        out.append({"name": rel, "kind": kind, "ts": round(m, 3), "url": "/render-out/" + rel})
    return out


def wip_payload() -> dict[str, Any]:
    since = render_since()
    return {"ok": True, "render_since": since, "wip": read_state(), "versions": versions(since)}


# ── the watcher ────────────────────────────────────────────────────────────

def _connect(client_id: str):
    import websocket  # websocket-client, system python and the vllm env

    url = COMFY.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?clientId={client_id}"
    ws = websocket.create_connection(url, timeout=25, suppress_origin=True)
    return ws


def watch_forever(*, stop: Optional[threading.Event] = None, client_id: str = CLIENT_ID,
                  log=None) -> None:
    """Hold Comfy's websocket as `client_id` while the house is in Render; write
    state + preview as they come. Off Render: idle state, no socket, 5 s naps."""
    import websocket as _wsmod

    say = log or (lambda m: None)
    tr = Tracker()
    tr.reset("idle")
    was_render = False
    while not (stop and stop.is_set()):
        if _mode() != "render":
            if was_render:
                tr.reset("idle")
                was_render = False
            time.sleep(5)
            continue
        was_render = True
        if not comfy_up():
            if tr.st.get("connected"):
                tr.set_connected(False)
            time.sleep(3)
            continue
        try:
            ws = _connect(client_id)
        except Exception as e:  # noqa: BLE001
            say(f"render-wip: connect failed: {type(e).__name__}: {e}")
            time.sleep(3)
            continue
        say(f"render-wip: watching Comfy as {client_id}")
        tr.set_connected(True)
        try:
            while not (stop and stop.is_set()):
                try:
                    op, frame = ws.recv_data()
                except _wsmod.WebSocketTimeoutException:
                    if _mode() != "render":
                        break
                    try:
                        ws.ping()
                    except Exception:  # noqa: BLE001
                        break
                    continue
                if op == _wsmod.ABNF.OPCODE_TEXT:
                    try:
                        msg = json.loads(frame if isinstance(frame, str) else frame.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(msg, dict):
                        tr.on_text(msg)
                elif op == _wsmod.ABNF.OPCODE_BINARY:
                    tr.on_binary(frame)
                elif op == _wsmod.ABNF.OPCODE_CLOSE:
                    break
        except Exception as e:  # noqa: BLE001
            say(f"render-wip: socket dropped: {type(e).__name__}: {e}")
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
            tr.set_connected(False)
        time.sleep(2)


if __name__ == "__main__":  # by hand: python3 lib/render_wip.py
    import sys

    watch_forever(log=lambda m: print(m, file=sys.stderr, flush=True))
