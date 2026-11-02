#!/usr/bin/env bash
# The vLLM environment the engines run in. Sourced by start-engine.sh.
#   KREW_VLLM_ENV   the venv that holds vllm (default $HOME/.krew/envs/vllm; the reference box pins engines/vllm-0271-cu132.lock.txt)
#   KREW_CUDA_HOME  the CUDA toolkit (default /usr/local/cuda-13.2, what the lock was built against)
#   HF_HOME         where the weights live (default $HOME/.cache/huggingface)
_CUDA="${KREW_CUDA_HOME:-/usr/local/cuda-13.2}"
_ENV="${KREW_VLLM_ENV:-$HOME/.krew/envs/vllm}"
if [ ! -x "$_ENV/bin/vllm" ] && [ "${KREW_ENGINE_DRY:-0}" != "1" ]; then
  echo "KREW_VLLM_ENV=$_ENV has no bin/vllm (build it from engines/vllm-0271-cu132.lock.txt)" >&2
  exit 2
fi
export CUDA_HOME="$_CUDA"
export PATH="$_CUDA/bin:$_ENV/bin:/usr/local/bin:/usr/bin:/bin"
if [ -n "${LD_LIBRARY_PATH:-}" ]; then
  export LD_LIBRARY_PATH="$_CUDA/lib64:${LD_LIBRARY_PATH}"
else
  export LD_LIBRARY_PATH="$_CUDA/lib64"
fi
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export FLASHINFER_DISABLE_VERSION_CHECK="${FLASHINFER_DISABLE_VERSION_CHECK:-1}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export VLLM_NO_USAGE_STATS=1
export DO_NOT_TRACK=1
export VLLM_USE_DEEP_GEMM="${VLLM_USE_DEEP_GEMM:-0}"
