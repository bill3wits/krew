"""krew-oracle: the Oracle visit loop's tools for the front door. HTTP only. No AUTH grant.

Lifted from the reference install's Oracle tools plugin (2026-10-09). Presence is read through the Crissa Wall's
player (`ZOEYOS_PLAYER`, `/v1/rail/presence/...`), which forwards reads and the inspector's op to the presence
rail she sits beside; the camera verbs (look at a station, name a face, embed a frame, the operator recount) need
the presence service's own address (`oracle.presence_url` in $KREW_HOME/krew.yaml, or KREW_PRESENCE_URL), and
with none set each says so instead of guessing. Nothing here writes identity or occupancy. Guests are not
occupancy.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from . import krew_settings as S

DEFAULTS = {"player": "", "presence_url": ""}
NOT_HERE = ("not on this install: the camera verbs need the presence service's own address "
            "(oracle.presence_url in krew.yaml, or KREW_PRESENCE_URL); presence itself is read through the Wall")


def settings() -> dict:
    d = dict(DEFAULTS)
    d.update(S.load("oracle"))
    return d


def player() -> str:
    cfg = settings()
    return (os.environ.get("ZOEYOS_PLAYER") or str(cfg.get("player") or "") or "http://127.0.0.1:8100").rstrip("/")


def presence_base() -> str:
    """Where presence is read: the rail's own address when configured, else the Wall's forwarder."""
    cfg = settings()
    direct = (os.environ.get("KREW_PRESENCE_URL") or str(cfg.get("presence_url") or "")).strip().rstrip("/")
    return direct or (player() + "/v1/rail/presence")


def direct_base() -> str:
    """The presence service's own address, '' when only the Wall's forwarder is known."""
    cfg = settings()
    return (os.environ.get("KREW_PRESENCE_URL") or str(cfg.get("presence_url") or "")).strip().rstrip("/")


def _get(url: str, timeout: float = 8.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url: str, body: dict, timeout: float = 45.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def register(ctx) -> None:
    def oracle_presence(*args, **kwargs):
        try:
            p = _get(presence_base() + "/v1/presence")
        except Exception as e:
            return f"presence down ({type(e).__name__})"
        rows = []
        for s in p.get("people") or []:
            rows.append(
                {
                    "name": s.get("name"),
                    "short_id": s.get("short_id"),
                    "event": s.get("event"),
                    "enter_ts": s.get("enter_ts"),
                    "exit_ts": s.get("exit_ts"),
                    "auth": (s.get("auth") or {}).get("level"),
                }
            )
        return json.dumps(
            {
                "headcount": p.get("headcount"),
                "user_auth": p.get("user_auth"),
                "people": rows,
            },
            ensure_ascii=False,
        )[:2000]

    def oracle_visit(*args, **kwargs):
        return (
            "The Oracle tools do not write identity or occupancy visits. "
            "The face visor is the ID path. Look at the camera if asked."
        )

    def oracle_chase_auth(*args, **kwargs):
        return (
            "The Oracle tools do not chase AUTH. The live face visor is the lock. "
            "Look at the camera if asked."
        )

    def oracle_inspect(*args, **kwargs):
        p = args[0] if args and isinstance(args[0], dict) else {}
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        op = str(p.get("op") or p.get("what") or "who_is_here").strip().lower()
        if op in ("scan", "scan_room", "rescan"):
            op = "scan_room"
        else:
            op = "who_is_here"
        try:
            return json.dumps(_post(presence_base() + "/v1/inspector", {"op": op}), ensure_ascii=False)[:2000]
        except Exception as e:
            return f"inspect failed ({type(e).__name__}: {e})"[:200]

    def oracle_embed(*args, **kwargs):
        """Run the embedder on a frame the Oracle picked out of the evidence."""
        p = args[0] if args and isinstance(args[0], dict) else {}
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        eid = str(p.get("event_id") or p.get("event") or "").strip()
        frame = str(p.get("frame") or p.get("file") or "").strip()
        if not eid or not frame:
            return "need event_id and frame"
        base = direct_base()
        if not base:
            return "embed " + NOT_HERE
        try:
            return json.dumps(
                _post(base + "/v1/face_embed", {"event_id": eid, "frame": frame}), ensure_ascii=False
            )[:1200]
        except Exception as e:
            return f"embed failed ({type(e).__name__}: {e})"[:200]

    def oracle_look_at(*args, **kwargs):
        """Point the camera at a station and look. Always returns to the door.

        The owner 2026-08-16: "I would like for zoey to be able to look at anything
        with it for interaction via video ... Just give her the lease on tilt
        and return at close of tool to firmware."

        Tilt is hers to set; pan is not: a station is a MEASURED pose, and an
        invented pan aims at a wall. Call with no argument to list stations.
        The lease, the de-zoom and the trip home live in the camera service, so this
        cannot leave the camera staring at someone.
        """
        p = args[0] if args and isinstance(args[0], dict) else {}
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        target = p.get("station") or p.get("target") or p.get("where")
        body = {}
        if target is not None:
            body["station"] = target
        if p.get("tilt") is not None:
            body["tilt"] = p.get("tilt")
        if p.get("facefinder") is not None:
            body["facefinder"] = bool(p.get("facefinder"))
        body["owner"] = "zoey_look_at"
        base = direct_base()
        if not base:
            return "look " + NOT_HERE
        try:
            r = _post(base + "/v1/look_at", body, timeout=90.0)
        except Exception as e:
            return f"look failed ({type(e).__name__}: {e})"[:200]
        if not target:
            return json.dumps(r, ensure_ascii=False)[:600]
        # Report the GLASS, not the accepted request.
        if not r.get("ok"):
            return json.dumps(
                {"looked": False, "why": r.get("error") or r.get("reason"),
                 "stations": r.get("known")}, ensure_ascii=False)[:600]
        return json.dumps({
            "looked": True,
            "station": r.get("station"),
            "pan": r.get("pan"),
            "tilt": r.get("tilt"),
            "frames": r.get("n_frames"),
            "n_faces": r.get("n_faces"),
            "names": r.get("names") or {},
            "facefinder": r.get("facefinder"),
            "camera_returned_home": bool((r.get("home") or {}).get("home")),
            "zoom": (r.get("home") or {}).get("zoom"),
        }, ensure_ascii=False)[:800]

    def error_check(*args, **kwargs):
        """Same walk as Who-is-here: wide + face-finder zoom + the visor. The owner is kept."""
        base = direct_base()
        if not base:
            return "error-check " + NOT_HERE
        try:
            r = _post(base + "/v1/error_check", {}, timeout=90.0)
        except Exception as e:
            return f"error-check failed ({type(e).__name__}: {e})"[:200]
        firmware = r.get("firmware") or {}
        home = firmware.get("home") if isinstance(firmware.get("home"), dict) else {}
        return json.dumps(
            {
                "ok": bool(r.get("ok")),
                "bodies": r.get("bodies_seen"),
                "seats": r.get("seats"),
                "sot": r.get("sot"),
                "facefinder": False,
                "camera_returned_home": bool(
                    r.get("camera_returned_home") or home.get("home")
                ),
                "lease_released": bool(r.get("lease_released")),
                "sweep": r.get("sweep"),
                "error": r.get("error"),
                "wall_sec": r.get("wall_sec"),
            },
            ensure_ascii=False,
        )[:2000]

    def oracle_name_face(*args, **kwargs):
        """Store the face the camera is looking at under a spoken name."""
        p = args[0] if args and isinstance(args[0], dict) else {}
        p.update({k: v for k, v in kwargs.items() if k != "task_id"})
        pid = str(p.get("person_id") or p.get("name") or p.get("who") or "").strip()
        if not pid:
            return "need a name: who is this?"
        body = {"person_id": pid, "said_by": str(p.get("said_by") or "voice")[:40]}
        if p.get("note"):
            body["note"] = str(p["note"])[:200]
        base = direct_base()
        if not base:
            return "store " + NOT_HERE
        try:
            r = _post(base + "/v1/store_face", body, timeout=60.0)
        except Exception as e:
            return f"store failed ({type(e).__name__}: {e})"[:200]
        if r.get("ok"):
            return json.dumps({
                "stored": True, "person_id": r.get("person_id"),
                "px": r.get("px"), "det": r.get("det"),
                "bank_had_thought": r.get("was_named"), "sim": r.get("was_sim"),
                "file": (r.get("stored") or "").split("/")[-1],
            }, ensure_ascii=False)[:400]
        return json.dumps({
            "stored": False, "why": r.get("error"),
            "detail": r.get("detail"), "auth": r.get("auth"),
        }, ensure_ascii=False)[:400]

    def _tool(name, description, schema, handler):
        # the model is sent the SCHEMA: the description belongs inside it (the `description=` keyword is registry
        # metadata the framework never copies back)
        ctx.register_tool(
            name,
            toolset="team",
            description=description,
            schema={"name": name, "description": description, "parameters": schema},
            handler=handler,
        )

    _tool(
        "oracle_embed",
        (
            "Run the face visor on ONE frame you chose from a door event and get the "
            "identity back. Pick the frame showing the most face: most "
            "front-on, least turned or blurred. Returns name, sim, and the "
            "quality measures (embed_px, sharpness, yaw) so you can see why a "
            "frame scored badly. Does not grant AUTH."
        ),
        {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "frame": {"type": "string"},
            },
            "required": ["event_id", "frame"],
        },
        oracle_embed,
    )
    _tool(
        "oracle_presence",
        "Read Oracle rows from the presence rail (GET /v1/presence through the Wall). Not a second count.",
        {"type": "object", "properties": {}},
        oracle_presence,
    )
    _tool(
        "oracle_visit",
        (
            "Write a visit into the one presence object. event=enter|exit|sighting. "
            "Already-present enter becomes sighting. Guests never +1. Does not grant AUTH."
        ),
        {
            "type": "object",
            "properties": {
                "event": {"type": "string"},
                "person_id": {"type": "string"},
                "clothes": {"type": "string"},
                "hair": {"type": "string"},
                "sex": {"type": "string"},
                "pack_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["event", "person_id"],
        },
        oracle_visit,
    )
    _tool(
        "oracle_chase_auth",
        (
            "Door-local owner face chase via ensure_user_auth. Use when entry is "
            "unclear or the owner's AUTH is missing. Does not stamp AUTH without the visor."
        ),
        {"type": "object", "properties": {}},
        oracle_chase_auth,
    )
    _tool(
        "oracle_inspect",
        "Inspector look: op=who_is_here|scan_room (same seat-patrol walk; wide then face-finder zoom). Returns door after.",
        {
            "type": "object",
            "properties": {"op": {"type": "string"}},
        },
        oracle_inspect,
    )
    _tool(
        "oracle_look_at",
        (
            "Look through the room camera at a named station (desk, door, the seats the "
            "camera service knows) with the face finder on by default so a seated "
            "face is big enough to name. Returns names+sims from the visor. "
            "Call with no arguments to list stations. Camera always homes."
        ),
        {
            "type": "object",
            "properties": {
                "station": {"type": "string",
                            "description": "a station the camera service lists (call with none to list them)"},
                "tilt": {"type": "integer",
                         "description": "optional tilt, -324000..360000"},
                "facefinder": {"type": "boolean"},
            },
        },
        oracle_look_at,
    )
    _tool(
        "error-check",
        (
            "Operator recount: same walk as Who-is-here. Wide frame, "
            "face-finder zoom, the visor's names. Body without a name stays "
            "S#. The owner is always kept. Camera homes."
        ),
        {"type": "object", "properties": {}},
        error_check,
    )
    _tool(
        "oracle_name_face",
        (
            "Store the face the camera is looking at under a name, on the owner's "
            "spoken say-so (\"that is <name>\"). Requires live owner AUTH. Refuses "
            "a frozen identity, a small/unclear face, or one already in a reference frame."
        ),
        {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "who this face is"},
                "note": {"type": "string"},
            },
            "required": ["person_id"],
        },
        oracle_name_face,
    )
