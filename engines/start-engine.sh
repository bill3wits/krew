#!/usr/bin/env bash
# One brain, as the reference box runs it: start-engine.sh ops | crew
#
# Two engines serve the same checkpoint on one GPU: `ops` (:8000, the talking profile and the decomposer, priority
# scheduling) and `crew` (:8005, the board's workers). Hermes only ever asks for the served names `ops` and `crew`;
# when the checkpoint moves, change ENGINE_MODEL here, never the served name or the port.
#
# Settings come from $KREW_HOME/engines.env (copy config/engines.example.env), then the environment, prefixed by the
# engine's name in capitals (OPS_PORT, CREW_KV_BYTES, ...). The values in the example file are the reference box's.
#
# Parking: a line `beast` or `render` in the GPU mode flag parks `ops` (exit 0, the unit stays inactive); a unit
# named in $KREW_TRAIN_HOLD (default /tmp/krew-train-hold, one `<unit>.service` per line: `ops.service` or this
# engine's own instance, `krew-engine@<name>.service`) is parked the same way. The flag is KREW_GPU_MODE_FILE, else the one the
# mode switch writes (crew/scripts/krew-mode: ~/.hermes/krew-gpu-mode, the file the keeper and the board gate read),
# else $KREW_HOME/gpu-mode. The mode switch, the keeper and the hold sweep write those files; this script only reads.
set -euo pipefail
NAME="${1:?start-engine.sh ops|crew}"
case "$NAME" in ops|crew) ;; *) echo "engine must be ops or crew, not $NAME" >&2; exit 2 ;; esac
UP="$(echo "$NAME" | tr '[:lower:]' '[:upper:]')"
KREW_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KREW_ROOT
KREW_HOME="${KREW_HOME:-$HOME/.krew}"
if [ -f "$KREW_HOME/engines.env" ]; then
  set -a; . "$KREW_HOME/engines.env"; set +a
fi
v() { local k="${UP}_$1"; printf '%s' "${!k:-$2}"; }

MODE_FILE="${KREW_GPU_MODE_FILE:-}"
if [ -z "$MODE_FILE" ]; then
  MODE_FILE="$HOME/.hermes/krew-gpu-mode"
  [ -f "$MODE_FILE" ] || MODE_FILE="$KREW_HOME/gpu-mode"
fi
if [ -f "$MODE_FILE" ]; then
  MODE="$(head -1 "$MODE_FILE" | tr -d '[:space:]')"
  if [ "$NAME" = "ops" ] && { [ "$MODE" = "beast" ] || [ "$MODE" = "render" ]; }; then
    echo "$NAME parked: gpu-mode=$MODE"; exit 0
  fi
fi
HOLD="${KREW_TRAIN_HOLD:-/tmp/krew-train-hold}"
if [ -f "$HOLD" ] && grep -qxE "(krew-engine@)?${NAME}\.service" "$HOLD"; then
  echo "$NAME parked: hold lists ${NAME}"; exit 0
fi

. "$KREW_ROOT/engines/vllm-env.sh"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-$HOME/.cache/vllm/$NAME}"

MODEL="$(v MODEL "$HF_HOME/hub-local/Qwen3.8-27B-NVFP4-unsloth")"
CHAT_TEMPLATE="$(v CHAT_TEMPLATE "$KREW_ROOT/engines/chat-templates/qwen3.8-froggeric-v22.1.jinja")"
TEMPLATE_KWARGS="$(v TEMPLATE_KWARGS '{"enable_thinking":true,"preserve_thinking":true}')"
UTIL="$(v UTIL "$([ "$NAME" = ops ] && echo 0.42 || echo 0.40)")"
KV_BYTES="$(v KV_BYTES 0)"
if [ "$KV_BYTES" = "0" ]; then
  echo "${UP}_KV_BYTES must be a byte count (0 = let vLLM take a fraction of VRAM, which starves the other engine). Set it in $KREW_HOME/engines.env" >&2
  exit 2
fi
MAX_LEN="$(v MAX_LEN "$([ "$NAME" = ops ] && echo 131072 || echo 102400)")"
SPEC_TOKENS="$(v SPEC_TOKENS "$([ "$NAME" = ops ] && echo 3 || echo 0)")"
ATTN="$(v ATTENTION_BACKEND triton_attn)"
case "$ATTN" in flashinfer|triton_attn) ;; *) echo "bad ${UP}_ATTENTION_BACKEND: $ATTN (flashinfer|triton_attn)" >&2; exit 2 ;; esac
if [ "$SPEC_TOKENS" != "0" ] && [ "$ATTN" = "flashinfer" ]; then SEQS="$(v MAX_NUM_SEQS 1)"; else SEQS="$(v MAX_NUM_SEQS 8)"; fi
LONG_PREFILL="$(v LONG_PREFILL 2048)"
BATCHED="$(v MAX_BATCHED_TOKENS "$([ "$NAME" = ops ] && echo 8192 || echo 0)")"
SLEEP_MODE="$(v SLEEP_MODE "$([ "$NAME" = ops ] && echo 0 || echo 1)")"   # crew is the sleeper (parked for a render)
DRY="${KREW_ENGINE_DRY:-0}"
PORT="$(v PORT "$([ "$NAME" = ops ] && echo 8000 || echo 8005)")"
HOST="$(v HOST 127.0.0.1)"
VLLM_PREFIX="$(v VLLM_PREFIX 0.27)"
if [ "$DRY" != "1" ]; then
  if [ ! -f "$MODEL/config.json" ]; then echo "no model at $MODEL (set ${UP}_MODEL)" >&2; exit 2; fi
  VLLM_PREFIX="$VLLM_PREFIX" python -c "import os, vllm; assert vllm.__version__.startswith(os.environ['VLLM_PREFIX']), vllm.__version__; print('vllm', vllm.__version__)"
fi

KV_ARGS=( --gpu-memory-utilization "$UTIL" --kv-cache-memory-bytes "$KV_BYTES" )
SPEC_ARGS=()
if [ "$SPEC_TOKENS" != "0" ]; then
  SPEC_ARGS+=( --speculative-config "{\"method\":\"mtp\",\"num_speculative_tokens\":${SPEC_TOKENS},\"attention_backend\":\"${ATTN}\"}" )
fi
if [ "$ATTN" != "flashinfer" ]; then SPEC_ARGS+=( --attention-backend "$ATTN" ); fi
TEMPLATE_ARGS=()
if [ -n "$CHAT_TEMPLATE" ] && [ "$CHAT_TEMPLATE" != "0" ] && [ -f "$CHAT_TEMPLATE" ]; then TEMPLATE_ARGS+=( --chat-template "$CHAT_TEMPLATE" ); fi
if [ -n "$TEMPLATE_KWARGS" ] && [ "$TEMPLATE_KWARGS" != "0" ]; then TEMPLATE_ARGS+=( --default-chat-template-kwargs "$TEMPLATE_KWARGS" ); fi
SCHED_ARGS=( --scheduling-policy priority )
SLEEP_ARGS=()
if [ "$SLEEP_MODE" = "1" ]; then export VLLM_SERVER_DEV_MODE=1; SLEEP_ARGS+=( --enable-sleep-mode ); fi
if [ "$(v PREFIX_CACHE 1)" != "0" ]; then SCHED_ARGS+=( --enable-prefix-caching ); fi
if [ -n "$LONG_PREFILL" ] && [ "$LONG_PREFILL" != "0" ]; then SCHED_ARGS+=( --long-prefill-token-threshold "$LONG_PREFILL" ); fi
if [ -n "$BATCHED" ] && [ "$BATCHED" != "0" ]; then SCHED_ARGS+=( --max-num-batched-tokens "$BATCHED" ); fi

echo "$NAME :$PORT model=$MODEL max-len=$MAX_LEN kv_bytes=$KV_BYTES util=$UTIL seqs=$SEQS mtp=$SPEC_TOKENS attn=$ATTN prefill=$LONG_PREFILL batched=$BATCHED sleep=$SLEEP_MODE"
ARGV=( python3 "$KREW_ROOT/engines/vllm_alive.py" "$MODEL" \
  --served-model-name "$NAME" \
  --disable-uvicorn-access-log \
  --tensor-parallel-size 1 \
  --max-model-len "$MAX_LEN" \
  --kv-cache-dtype fp8 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  "${TEMPLATE_ARGS[@]}" \
  --mm-encoder-tp-mode data \
  "${SPEC_ARGS[@]}" \
  --max-num-seqs "$SEQS" \
  "${KV_ARGS[@]}" \
  "${SCHED_ARGS[@]}" \
  "${SLEEP_ARGS[@]}" \
  --host "$HOST" --port "$PORT" )
if [ "$DRY" = "1" ]; then printf '%s\n' "${ARGV[@]}"; exit 0; fi   # KREW_ENGINE_DRY=1: show the command, start nothing
exec "${ARGV[@]}"
