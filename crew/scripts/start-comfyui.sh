#!/bin/bash
# ComfyUI — local image/video generation. Live path workers actually run:
#
#
#
# Gate: occupancy SoT is ~/.hermes/krew-gpu-mode. A worker must not start
#
#
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/../lib/krew_hermes.sh"   # $KREW_HERMES_HOME: where Hermes lives
MODE_FILE="${KREW_GPU_MODE_FILE:-$KREW_HERMES_HOME/krew-gpu-mode}"
MODE="$(awk '{print $1; exit}' "$MODE_FILE" 2>/dev/null || true)"
if [ "$MODE" != "render" ]; then
  echo "start-comfyui: krew-gpu-mode is '${MODE:-unset}' (need render). Refusing." >&2
  echo "  krew-mode render turbo   # then retry" >&2
  exit 78
fi
# A clean start for every caller (the owner 2026-10-08): a crew worker carries PYTHONPATH into the Hermes
# venv (Python 3.14) and its card ids; Comfy is its own Python and died on pydantic_core when it inherited that
# path. Drop them here, so a crew card, the panel's button or a hand all launch the same Comfy.
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV
for v in $(env | sed -n 's/^\(HERMES_KANBAN_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done
# The one Comfy location krew shares with the Wall (crew/lib/krew_comfy.py: the Wall's render.json, else
# KREW_COMFY_ROOT, else ~/ComfyUI; the User 2026-10-10).
HERE="$(cd "$(dirname "$0")" && pwd)"
COMFY_ROOT="$(python3 "$HERE/../lib/krew_comfy.py" root 2>/dev/null || true)"
COMFY_ROOT="${COMFY_ROOT:-${KREW_COMFY_ROOT:-$HOME/ComfyUI}}"
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VIRTUAL_ENV="${KREW_COMFY_VENV:-$COMFY_ROOT/venv}"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# ...and its port from the same resolver (2026-10-10): beside the Wall it is the Wall's own ComfyUI port, never 8188.
COMFY_URL="$(python3 "$HERE/../lib/krew_comfy.py" url 2>/dev/null || true)"
COMFY_PORT="${COMFY_URL##*:}"; COMFY_PORT="${COMFY_PORT%%/*}"
case "$COMFY_PORT" in ''|*[!0-9]*) COMFY_PORT=8188 ;; esac
cd "$COMFY_ROOT"
exec "$VIRTUAL_ENV/bin/python" "$COMFY_ROOT/main.py" --port "${KREW_COMFY_PORT:-$COMFY_PORT}" --listen 127.0.0.1 ${KREW_COMFY_ARGS---highvram}
