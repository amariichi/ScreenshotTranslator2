#!/usr/bin/env bash
# =============================================================================
#  run_vllm_backend.sh  —  Optional vLLM / DiffusionGemma backend launcher
#  EXPERIMENTAL. Requires an NVIDIA Blackwell/Hopper GPU. See README.
#
#  Usage (from the repository root):
#    ./app/scripts/run_vllm_backend.sh start     # start (default; idempotent + warmup)
#    ./app/scripts/run_vllm_backend.sh stop      # stop & remove the container
#    ./app/scripts/run_vllm_backend.sh status    # show status
#
#  Then point the app at it (no app code changes):
#    SKIP_LLAMACPP=1 \
#    LLAMA_SERVER_URL=http://127.0.0.1:8000 \
#    LLAMA_MODEL_NAME=nvidia/diffusiongemma-26B-A4B-it-NVFP4 \
#    ./start.sh
# =============================================================================
set -euo pipefail

# ---- configurable (override via environment) --------------------------------
NAME="${VLLM_NAME:-dgemma}"
PORT="${VLLM_PORT:-8000}"
MODEL="${VLLM_MODEL:-nvidia/diffusiongemma-26B-A4B-it-NVFP4}"
# Pinned by digest for reproducibility. This image is PRE-RELEASE/experimental
# (tag: vllm/vllm-openai:gemma); update the digest when a newer one is published.
# Pinned 2026-06-11.
IMAGE="${VLLM_IMAGE:-vllm/vllm-openai@sha256:9c719fc0c869092c7d0533f8357d6985a38d5ff03b20ffb6a4620c2b4806dd4b}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
GPU_MEM_UTIL="${VLLM_GPU_MEM_UTIL:-0.75}"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-4}"

# Tiny 64x64 PNG used to warm the vision+decode path (the first request after
# startup is otherwise slow ~4-5s and lower quality due to one-time compile work).
WARMUP_PNG_B64="iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAh0lEQVR4nO3asQ3CQBAAQR65GOqgApdDRODIZVAJtZE6NEJo9NJOfC/d6tIft/V1mdlVL/CrArQCtAK0ArQCtAK0ArQCtAK0ArTl2wfvbf/HHkf35+P88PQXKEArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAmz5g9OEJK0ArQCtAK0ArQCtAmz7gAyiYBbEBQEuCAAAAAElFTkSuQmCC"

die(){ echo "❌ $*" >&2; exit 1; }
is_running(){ [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" = "true" ]; }

cmd_status(){ if is_running; then echo "✅ running → http://localhost:$PORT/v1"; else echo "⛔ not running"; fi; }
cmd_stop(){ if docker rm -f "$NAME" >/dev/null 2>&1; then echo "🛑 stopped ($NAME)"; else echo "(not running)"; fi; }

cmd_start(){
  command -v docker >/dev/null || die "docker not found"
  if is_running; then echo "✅ already running → http://localhost:$PORT/v1"; return 0; fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true

  echo "🚀 starting $NAME (first load takes a few minutes)..."
  docker run -d --name "$NAME" --gpus all \
    -v "$HF_CACHE:/root/.cache/huggingface" \
    -p "$PORT:8000" \
    -e VLLM_USE_V2_MODEL_RUNNER=1 \
    -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    "$IMAGE" "$MODEL" \
    --trust-remote-code \
    --max-num-seqs "$MAX_NUM_SEQS" \
    --max-model-len "$MAX_MODEL_LEN" \
    --gpu-memory-utilization "$GPU_MEM_UTIL" \
    --attention-backend TRITON_ATTN \
    --enable-auto-tool-choice \
    --tool-call-parser gemma4 \
    --reasoning-parser gemma4 \
    --override-generation-config '{"max_new_tokens": null}' \
    --default-chat-template-kwargs '{"enable_thinking":false}' >/dev/null

  printf "⏳ loading"
  local ready=
  for _ in $(seq 1 240); do
    if curl -fsS "http://localhost:$PORT/v1/models" >/dev/null 2>&1; then ready=1; break; fi
    is_running || { echo; echo "❌ container exited:"; docker logs --tail 30 "$NAME"; exit 1; }
    printf "."; sleep 3
  done
  echo
  [ "$ready" = 1 ] || die "startup timeout — check: docker logs $NAME"

  echo "🔥 warming up..."
  printf '{"model":"%s","messages":[{"role":"user","content":[{"type":"text","text":"describe"},{"type":"image_url","image_url":{"url":"data:image/png;base64,%s"}}]}],"max_tokens":64}' \
    "$MODEL" "$WARMUP_PNG_B64" \
    | curl -fsS "http://localhost:$PORT/v1/chat/completions" -H 'Content-Type: application/json' --data @- >/dev/null 2>&1 || true

  echo "✅ ready → http://localhost:$PORT/v1"
  echo "   point the app at it:"
  echo "     SKIP_LLAMACPP=1 LLAMA_SERVER_URL=http://127.0.0.1:$PORT LLAMA_MODEL_NAME=$MODEL ./start.sh"
}

case "${1:-start}" in
  start)  cmd_start  ;;
  stop)   cmd_stop   ;;
  status) cmd_status ;;
  *) die "usage: $0 [start|stop|status]" ;;
esac
