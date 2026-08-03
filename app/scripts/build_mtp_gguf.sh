#!/usr/bin/env bash
# =============================================================================
#  build_mtp_gguf.sh — build the optional MTP (multi-token prediction) head GGUF
#
#  Speculative decoding needs a small "assistant" head published alongside the
#  main model. Google ships it as safetensors only, so it has to be converted.
#  This is entirely optional: without it the app runs normally, just slower.
#
#  Usage (from the repository root):
#    ./app/scripts/build_mtp_gguf.sh                 # 26B-A4B (default)
#    MTP_HF_REPO=google/gemma-4-12B-it-assistant \
#      MTP_OUTFILE=models/mtp-gemma-4-12B-it.gguf \
#      ./app/scripts/build_mtp_gguf.sh
#
#  Requires: uv, and a llama.cpp checkout recent enough to know the
#  "gemma4-assistant" architecture (see docs/mtp.md).
# =============================================================================
set -euo pipefail

MTP_HF_REPO="${MTP_HF_REPO:-google/gemma-4-26B-A4B-it-assistant}"
MTP_OUTFILE="${MTP_OUTFILE:-models/mtp-gemma-4-26B-A4B-it.gguf}"
MTP_OUTTYPE="${MTP_OUTTYPE:-f16}"
LLAMA_DIR="${LLAMA_DIR:-llama.cpp}"
# Where the safetensors checkout is downloaded to before conversion.
MTP_SRC_DIR="${MTP_SRC_DIR:-.mtp_src/$(basename "$MTP_HF_REPO")}"

CONVERTER="$LLAMA_DIR/convert_hf_to_gguf.py"
REQUIREMENTS="$LLAMA_DIR/requirements/requirements-convert_hf_to_gguf.txt"

command -v uv >/dev/null 2>&1 || { echo "[ERROR] uv is required." >&2; exit 1; }
[ -f "$CONVERTER" ] || {
  echo "[ERROR] $CONVERTER not found. Run app/scripts/build_llama.sh first." >&2
  exit 1
}

if [ -f "$MTP_OUTFILE" ]; then
  echo "[INFO] $MTP_OUTFILE already exists; nothing to do."
  exit 0
fi

# --- 1. fetch the assistant checkout -----------------------------------------
if [ ! -f "$MTP_SRC_DIR/model.safetensors" ]; then
  echo "[INFO] downloading $MTP_HF_REPO -> $MTP_SRC_DIR"
  mkdir -p "$MTP_SRC_DIR"
  base="https://huggingface.co/$MTP_HF_REPO/resolve/main"
  for f in config.json generation_config.json tokenizer.json tokenizer_config.json model.safetensors; do
    curl -fsSL -C - "$base/$f" -o "$MTP_SRC_DIR/$f" \
      || { echo "[ERROR] failed to download $f" >&2; exit 1; }
  done
else
  echo "[INFO] reusing existing checkout at $MTP_SRC_DIR"
fi

# --- 2. work around a tokenizer_config quirk ---------------------------------
# Google's assistant repos ship "extra_special_tokens": [] (a list). transformers
# expects a mapping there and dies with
#   AttributeError: 'list' object has no attribute 'keys'
# Rewriting the empty list to an empty object is enough and changes nothing else.
python3 - "$MTP_SRC_DIR/tokenizer_config.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    cfg = json.load(f)
if isinstance(cfg.get("extra_special_tokens"), list) and not cfg["extra_special_tokens"]:
    cfg["extra_special_tokens"] = {}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("[INFO] patched extra_special_tokens: [] -> {}")
else:
    print("[INFO] tokenizer_config.json needs no patching")
PY

# --- 3. convert ---------------------------------------------------------------
VENV="${MTP_VENV:-.mtp_venv}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "[INFO] creating conversion venv at $VENV"
  uv venv "$VENV" --python 3.12
fi
# The requirements file pins a CPU torch wheel from a second index, so uv needs
# permission to resolve across both indexes.
uv pip install --quiet --python "$VENV/bin/python" \
  --index-strategy unsafe-best-match -r "$REQUIREMENTS"

mkdir -p "$(dirname "$MTP_OUTFILE")"
echo "[INFO] converting -> $MTP_OUTFILE (outtype: $MTP_OUTTYPE)"
"$VENV/bin/python" "$CONVERTER" "$MTP_SRC_DIR" \
  --outfile "$MTP_OUTFILE" --outtype "$MTP_OUTTYPE"

echo "[INFO] done: $MTP_OUTFILE"
echo "[INFO] start.sh picks it up automatically when the filename matches the default."
