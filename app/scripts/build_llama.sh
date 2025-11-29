#!/usr/bin/env bash
set -euo pipefail

LLAMA_REPO=${LLAMA_REPO:-https://github.com/ggml-org/llama.cpp}
LLAMA_DIR=${LLAMA_DIR:-llama.cpp}

if [ ! -d "$LLAMA_DIR" ]; then
  echo "[INFO] cloning llama.cpp from $LLAMA_REPO"
  git clone --depth=1 "$LLAMA_REPO" "$LLAMA_DIR"
fi

cd "$LLAMA_DIR"
mkdir -p build
cd build

# Build with CUDA (NVCC must be available)
# llama.cpp deprecated LLAMA_CUBLAS / LLAMA_CUDA; use GGML_CUDA only
# Disable CURL to avoid missing libcurl dev warning if not installed
cmake -DGGML_CUDA=ON -DLLAMA_CURL=OFF ..

# parallel build; fall back to 4 if nproc is unavailable
JOBS=${JOBS:-$(command -v nproc >/dev/null 2>&1 && nproc || echo 4)}
cmake --build . -j "${JOBS}"

echo "[INFO] llama.cpp built. Binary: $(pwd)/bin/llama-server"
