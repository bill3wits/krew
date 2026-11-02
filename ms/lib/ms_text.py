""








from __future__ import annotations

from pathlib import Path

import ms_settings as _S

NEUTRAL: dict[str, str] = {

    "alert.promoted.block_reason": "{name}: {n} rollbacks in a row after promotion - needs a decision",

    "error.packaging_hint": "Run it with an interpreter that has `packaging` (e.g. `uv run --with packaging`).",
    "error.unlock_kpi_hint": "",

    "canary.no_target": "no live vLLM (mode={mode}; none of the configured canary endpoints answered)",

    "card.land.body": ("M&S land child for `{env}`.\n"
                       "Parent {parent} already GO. Nightly apply: uv + smoke + rollback. "
                       "Completing this card completes the parent.\n"
                       "## Actionable packages\n{arrows}\n"),
    "card.land.complete": "{env}: M&S landed {n} upgrade(s); smoke/canary ok.",
    "card.canary_skip.title": "M&S: the {env} canary has not run for {n} nights ({first}..{last})",
    "card.canary_skip.body": ("The vLLM canary for `{env}` returned n/a / UNVERIFIED on {n} nights in a row, so every "
                              "upgrade for that env waited quietly. Free VRAM vs what the canary needed, per night:\n"
                              "{per_night}\n\nDecide: resize the canary, free the VRAM, or test the env another "
                              "way. Finding: {finding}. Evidence: {report}."),
    "card.canary_skip.block_reason": "{env} canary skipped {n} nights in a row - needs a decision",
    "card.restart_owed_tail": "report-only units are not restarted by M&S",
    "card.summary.tail": "Apply only after a GREEN smoke.",
    "card.footer": ("M&S applies venv upgrades itself (uv + smoke + rollback). Record a decision with "
                    "`--record-decision`; a land card is a root card, never a child of this one."),
    "card.mitigation.criteria": ("## ACTIONABLE ONLY - completion criteria\n"
                                 "1. Read `{decisions}` first; cite existing holds.\n"
                                 "2. Write one deliverable: `{deliverables}/{name}-dep-mitigation-{day}.md`.\n"
                                 "3. GREEN targets land on the nightly apply (uv --no-deps + smoke). A major that "
                                 "needs a decision: record it with `--record-decision`.\n"
                                 "4. Do NOT decompose into per-package children. Do NOT edit freeze manifests.\n"
                                 "5. NEVER_AUTO packages stay off this path."),
    "card.render_sota.path": "## Path\n1. Smoke the saved workflows on the new tag in a render window, then apply.",
    "card.render_sota.footer": "",

    "notify.apply_failed_tail": ").",
    "notify.osv_tail": ".",
    "log.notify": "notify",

    "render.schema": "render-survey/v1",
    "render.sota_path": "Smoke the saved workflows on the new tag, then apply.",
    "health.comfy.render_up": "v{ver} up",
    "health.comfy.render_down": "{mode} mode - down",
    "os.browser_autoupgrade_line": "- a browser auto-upgrade watch is off",

    "window.label": "nightly",
    "window.note": ("Greens that did not apply this run + pending_apply majors. The nightly --apply-green "
                    "drains them; hold/wontfix/NEVER_AUTO never land."),

    "help.apply_pending": "land ledger pending_apply majors (venv uv + smoke)",
    "help.which": "with --step serve (which env to start) / stop",
    "help.accept_review": "with --step flip: accept a compare verdict of review",
    "help.survey": ("re-read live envs, report out-of-band drift vs each manifest, append it to drift.jsonl, "
                    "refresh manifests (never LAST-GOOD), then exit. No PyPI queries, no apply."),
    "help.unlock_kpi": "kpi_id to chart",

    "report.note": ("Manifests under {manifests}/*-LATEST.txt are the current venv truth. GREEN applies only "
                    "in envs whose settings say apply: green, each behind its smoke test. Tool: {tool}."),
    "md.canary_line": ("- Pip-green installs, each behind the env's smoke test{canary} and rollback on RED."),
    "md.kanban_heading": "## Board (summary)",

    "manifest.title": "# System manifest - live (auto-generated)",
    "manifest.profiles_note": "",
    "manifest.engines": "",
    "manifest.render_heading": "## Render tools (surveyed this run)",
    "manifest.inventory_heading": "## Venv inventory (rebuilt this run)",
    "manifest.unused_note": ("Installed is not used. A name here stays until the ledger says `wontfix` "
                             "(keep) or the package is uninstalled."),
    "manifest.stt": "",
}

_HOUSE: dict[str, str] | None = None


def _installed() -> dict[str, str]:
    ""
    global _HOUSE
    if _HOUSE is None:
        path = _S.expand(_S.get("text_file"))
        _HOUSE = {}
        if path and Path(path).is_file():
            data = _S._load_file(Path(path))
            _HOUSE = {str(k): str(v) for k, v in (data.get("text") or data).items()}
    return _HOUSE


def T(key: str, **fields) -> str:
    tpl = _installed().get(key)
    if tpl is None:
        tpl = NEUTRAL.get(key)
    if tpl is None:
        return f"[missing text {key}]"
    for k, v in fields.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl


def lines(key: str, **fields) -> list[str]:
    ""
    s = T(key, **fields)
    return s.split("\n") if s else []
