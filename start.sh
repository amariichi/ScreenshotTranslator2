#!/usr/bin/env bash
set -euo pipefail

# Configurables (can be overridden via environment variables)
WEB_PORT=${WEB_PORT:-8012}
LLAMA_PORT=${LLAMA_PORT:-8009}
LLAMA_MODEL=${LLAMA_MODEL:-models/Qwen3-VL-30B-A3B-Instruct-UD-Q4_K_XL.gguf}
LLAMA_MMPROJ=${LLAMA_MMPROJ:-models/mmproj-F32.gguf}
LLAMA_CTX=${LLAMA_CTX:-8192}
LLAMA_BIN=${LLAMA_BIN:-./llama.cpp/build/bin/llama-server}
SKIP_LLAMACPP=${SKIP_LLAMACPP:-0}

if ! command -v uv >/dev/null 2>&1; then
  echo "[ERROR] uv is required. Install via: pip install uv" >&2
  exit 1
fi

# Install Python deps inside .venv via uv (no global installs)
uv sync

if [ "$SKIP_LLAMACPP" != "1" ]; then
  if [ ! -x "$LLAMA_BIN" ]; then
    echo "[ERROR] llama-server binary not found at $LLAMA_BIN"
    echo "Run scripts/build_llama.sh to clone & build llama.cpp with CUDA."
    exit 1
  fi

  echo "[INFO] starting llama.cpp server on port $LLAMA_PORT"
  set +e
  $LLAMA_BIN \
    --host 0.0.0.0 \
    --port "$LLAMA_PORT" \
    -m "$LLAMA_MODEL" \
    -c "$LLAMA_CTX" \
    -ngl 999 \
    --jinja \
    -ub 4096 \
    -b 4096 \
    --flash-attn on \
    --mmproj "$LLAMA_MMPROJ" \
    > llama-server.log 2>&1 &
  LLAMA_PID=$!
  set -e
  trap 'echo "[INFO] stopping llama.cpp"; kill $LLAMA_PID 2>/dev/null || true' EXIT
else
  echo "[INFO] SKIP_LLAMACPP=1 -> assuming llama-server already running on $LLAMA_PORT"
fi

export LLAMA_SERVER_URL=${LLAMA_SERVER_URL:-http://127.0.0.1:${LLAMA_PORT}}
export LLAMA_CTX

echo "[INFO] starting FastAPI on port $WEB_PORT"
uv run uvicorn app.main:app --host 0.0.0.0 --port "$WEB_PORT"
