#!/usr/bin/env bash
set -euo pipefail

LLAMA_REPO=${LLAMA_REPO:-https://github.com/ggml-org/llama.cpp}
LLAMA_DIR=${LLAMA_DIR:-llama.cpp}

# GPU backend to compile. cuda is the default (NVIDIA); vulkan is the portable
# choice and is what AMD APUs such as Ryzen AI Max+ 395 (Strix Halo) should use;
# hip targets ROCm directly; cpu builds without any GPU offload.
GPU_BACKEND=${GPU_BACKEND:-cuda}

case "$GPU_BACKEND" in
  cuda)   CMAKE_GPU_ARGS=(-DGGML_CUDA=ON) ;;
  vulkan) CMAKE_GPU_ARGS=(-DGGML_VULKAN=ON) ;;
  hip)    CMAKE_GPU_ARGS=(-DGGML_HIP=ON) ;;
  cpu)    CMAKE_GPU_ARGS=() ;;
  *)
    echo "[ERROR] unknown GPU_BACKEND='$GPU_BACKEND' (use cuda, vulkan, hip or cpu)" >&2
    exit 1
    ;;
esac

if [ ! -d "$LLAMA_DIR" ]; then
  echo "[INFO] cloning llama.cpp from $LLAMA_REPO"
  git clone --depth=1 "$LLAMA_REPO" "$LLAMA_DIR"
fi

cd "$LLAMA_DIR"

# Speculative decoding with an MTP head needs a recent llama.cpp; older checkouts
# load the NextN tensors but never use them for inference.
echo "[INFO] llama.cpp commit: $(git log -1 --format='%h %ad %s' --date=short)"

echo "[INFO] configuring with GPU_BACKEND=$GPU_BACKEND"
# Disable CURL to avoid a hard libcurl dependency on minimal hosts.
cmake -B build "${CMAKE_GPU_ARGS[@]}" -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release

# parallel build; fall back to 4 if nproc is unavailable
JOBS=${JOBS:-$(command -v nproc >/dev/null 2>&1 && nproc || echo 4)}
cmake --build build -j "${JOBS}"

echo "[INFO] llama.cpp built. Binary: $(pwd)/build/bin/llama-server"
