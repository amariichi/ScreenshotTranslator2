<#
.SYNOPSIS
  Build the optional MTP (multi-token prediction) head GGUF on Windows.

.DESCRIPTION
  Windows counterpart of app\scripts\build_mtp_gguf.sh.

  Speculative decoding needs a small "assistant" head published alongside the
  main model. Google ships it as safetensors only, so it has to be converted.
  This is entirely optional: without it the app runs normally, just slower.

  No GPU and no C++ toolchain are needed -- the conversion runs on the CPU
  torch wheel. It does need llama.cpp's convert_hf_to_gguf.py, which is *not*
  part of the prebuilt zip that fetch_llama_win.ps1 downloads, so this script
  shallow-clones the llama.cpp sources when they are missing.

  Roughly 5 GB of free space: ~0.9 GB safetensors, ~0.9 GB output, and ~3 GB
  for the torch wheel inside the conversion venv.

.PARAMETER HfRepo
  Hugging Face repository holding the assistant head.

.PARAMETER OutFile
  Where to write the GGUF. The default name is what start.ps1 auto-detects.

.PARAMETER OutType
  Conversion precision passed to convert_hf_to_gguf.py. Keep f16: a quantized
  draft head predicts worse, which lowers the acceptance rate.

.EXAMPLE
  .\app\scripts\build_mtp_gguf.ps1

.EXAMPLE
  .\app\scripts\build_mtp_gguf.ps1 -HfRepo google/gemma-4-12B-it-assistant `
    -OutFile models\mtp-gemma-4-12B-it.gguf
#>
[CmdletBinding()]
param(
    [string]$HfRepo  = "google/gemma-4-26B-A4B-it-assistant",
    [string]$OutFile = "models\mtp-gemma-4-26B-A4B-it.gguf",
    [string]$OutType = "f16",
    [string]$LlamaDir = "llama.cpp",
    [string]$SrcDir  = "",
    [string]$Venv    = ".mtp_venv"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Run from the repository root so the relative defaults resolve the same way
# they do in the bash script.
Set-Location -Path (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
# Set-Location moves PowerShell's location but not the .NET process working
# directory, and [System.IO.File] resolves relative paths against the latter.
# Without this line the tokenizer patch below reads from wherever the shell was
# started (C:\Users\<name>) instead of the repository.
[Environment]::CurrentDirectory = $PWD.ProviderPath

if (-not $SrcDir) { $SrcDir = Join-Path ".mtp_src" (Split-Path $HfRepo -Leaf) }

$converter    = Join-Path $LlamaDir "convert_hf_to_gguf.py"
$requirements = Join-Path $LlamaDir "requirements\requirements-convert_hf_to_gguf.txt"
$venvPython   = Join-Path $Venv "Scripts\python.exe"

function Assert-ExitCode($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it with: winget install astral-sh.uv"
}

if (Test-Path $OutFile) {
    Write-Host "[INFO] $OutFile already exists; nothing to do."
    exit 0
}

# --- 1. make sure the converter is available ---------------------------------
# fetch_llama_win.ps1 unpacks binaries only, so on a fresh Windows checkout the
# Python converter is usually missing even though llama-server.exe works.
if (-not (Test-Path $converter)) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "$converter not found and git is unavailable. Clone https://github.com/ggml-org/llama.cpp into $LlamaDir manually."
    }
    if ((Test-Path $LlamaDir) -and (Get-ChildItem $LlamaDir -Force | Select-Object -First 1)) {
        throw "$LlamaDir exists but has no convert_hf_to_gguf.py. Update that checkout (git -C $LlamaDir pull), remove it, or pass -LlamaDir pointing at llama.cpp sources."
    }
    Write-Host "[INFO] $converter not found -> shallow-cloning llama.cpp sources (no build needed)"
    git clone --depth=1 https://github.com/ggml-org/llama.cpp $LlamaDir
    Assert-ExitCode "git clone"
}

# --- 2. fetch the assistant checkout ------------------------------------------
$files = @("config.json", "generation_config.json", "tokenizer.json",
           "tokenizer_config.json", "model.safetensors")

if (-not (Test-Path (Join-Path $SrcDir "model.safetensors"))) {
    Write-Host "[INFO] downloading $HfRepo -> $SrcDir"
    New-Item -ItemType Directory -Force -Path $SrcDir | Out-Null
    $base = "https://huggingface.co/$HfRepo/resolve/main"
    # curl.exe ships with Windows 10 1803+ and can resume a partial download of
    # the ~0.9 GB safetensors; Invoke-WebRequest cannot, so it is only a fallback.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        $dest = Join-Path $SrcDir $f
        Write-Host "[INFO]   $f"
        if ($curl) {
            & $curl.Source -fsSL -C - "$base/$f" -o $dest
            Assert-ExitCode "download of $f"
        } else {
            $prev = $ProgressPreference
            $ProgressPreference = "SilentlyContinue"
            try { Invoke-WebRequest -Uri "$base/$f" -OutFile $dest }
            finally { $ProgressPreference = $prev }
        }
    }
} else {
    Write-Host "[INFO] reusing existing checkout at $SrcDir"
}

# --- 3. work around a tokenizer_config quirk ----------------------------------
# Google's assistant repos ship "extra_special_tokens": [] (a list). transformers
# expects a mapping there and dies with
#   AttributeError: 'list' object has no attribute 'keys'
# Rewriting the empty list to an empty object is enough and changes nothing else.
# Done as a targeted text edit rather than a JSON round-trip so the rest of the
# file (and its UTF-8-without-BOM encoding, which transformers requires) is left
# byte-for-byte alone.
$tokCfg = Join-Path $SrcDir "tokenizer_config.json"
if (-not (Test-Path $tokCfg)) {
    throw "$tokCfg is missing. Remove $SrcDir and rerun to fetch the checkout again."
}
$raw = [System.IO.File]::ReadAllText($tokCfg)
$patched = [regex]::Replace($raw, '("extra_special_tokens"\s*:\s*)\[\s*\]', '${1}{}')
if ($patched -ne $raw) {
    [System.IO.File]::WriteAllText($tokCfg, $patched, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "[INFO] patched extra_special_tokens: [] -> {}"
} else {
    Write-Host "[INFO] tokenizer_config.json needs no patching"
}

# --- 4. convert ----------------------------------------------------------------
if (-not (Test-Path $venvPython)) {
    Write-Host "[INFO] creating conversion venv at $Venv"
    uv venv $Venv --python 3.12
    Assert-ExitCode "uv venv"
}
# The requirements file pins a CPU torch wheel from a second index, so uv needs
# permission to resolve across both indexes.
Write-Host "[INFO] installing conversion dependencies (torch CPU wheel, ~3 GB on disk)"
uv pip install --quiet --python $venvPython --index-strategy unsafe-best-match -r $requirements
Assert-ExitCode "uv pip install"

$outDir = Split-Path $OutFile -Parent
if ($outDir) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }

Write-Host "[INFO] converting -> $OutFile (outtype: $OutType)"
& $venvPython $converter $SrcDir --outfile $OutFile --outtype $OutType
Assert-ExitCode "convert_hf_to_gguf.py"

Write-Host "[INFO] done: $OutFile ($([math]::Round((Get-Item $OutFile).Length / 1MB)) MB)"
Write-Host "[INFO] start.ps1 picks it up automatically when the filename matches the default."
