<#
.SYNOPSIS
  Start the backend natively on Windows (no WSL2 required).

.DESCRIPTION
  Windows counterpart of start.sh. Launches llama-server.exe and the FastAPI app
  in the same way, so the WPF overlay client in windows\OverlayClient keeps
  talking to http://127.0.0.1:8012 unchanged.

  Get llama-server.exe first:
    .\app\scripts\fetch_llama_win.ps1        # Vulkan build, works on AMD APUs

  Settings are read from environment variables:
    WEB_PORT, LLAMA_PORT, LLAMA_CTX, LLAMA_PARALLEL, LLAMA_BIN, LLAMA_MODEL,
    LLAMA_MMPROJ, LLAMA_MODEL_NAME, LLAMA_SPEC_DRAFT_MODEL, LLAMA_SPEC_TYPE,
    LLAMA_SPEC_DRAFT_N_MAX, LLAMA_REASONING, LLAMA_THINK_BUDGET,
    SKIP_LLAMACPP, LLAMA_SERVER_URL

.EXAMPLE
  .\start.ps1
  $env:LLAMA_CTX=4096; .\start.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-EnvOrDefault($name, $default) {
    $v = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrEmpty($v)) { return $default }
    return $v
}

# Always operate from the repository root so the CWD-relative paths used by the
# app (app\static, kokoro-v1.0.onnx, voices-v1.0.bin, ...) resolve correctly.
Set-Location -Path $PSScriptRoot

$WebPort      = Get-EnvOrDefault "WEB_PORT"      "8012"
$LlamaPort    = Get-EnvOrDefault "LLAMA_PORT"    "8009"
$LlamaCtx     = Get-EnvOrDefault "LLAMA_CTX"     "8192"
$LlamaParallel= Get-EnvOrDefault "LLAMA_PARALLEL" "1"
$LlamaBin     = Get-EnvOrDefault "LLAMA_BIN"     "llama.cpp-win\llama-server.exe"
$SkipLlama    = Get-EnvOrDefault "SKIP_LLAMACPP" "0"

$DefaultModel      = "models\gemma-4-26B_q4_0-it.gguf"
$DefaultMmproj     = "models\gemma-4-26B-it-mmproj.gguf"
$DefaultModelName  = "Gemma-4-26B-A4B-It-QAT"
$DefaultSpecDraft  = "models\mtp-gemma-4-26B-A4B-it.gguf"

$LlamaModel     = Get-EnvOrDefault "LLAMA_MODEL"      $DefaultModel
$LlamaMmproj    = Get-EnvOrDefault "LLAMA_MMPROJ"     $DefaultMmproj
$LlamaModelName = Get-EnvOrDefault "LLAMA_MODEL_NAME" $DefaultModelName
$LlamaReasoning = Get-EnvOrDefault "LLAMA_REASONING"  ""
$LlamaThink     = Get-EnvOrDefault "LLAMA_THINK_BUDGET" ""

# Optional speculative decoding through an MTP head (see docs\mtp.md).
$SpecDraft     = Get-EnvOrDefault "LLAMA_SPEC_DRAFT_MODEL" ""
$SpecType      = Get-EnvOrDefault "LLAMA_SPEC_TYPE" "draft-mtp"
$SpecDraftNMax = Get-EnvOrDefault "LLAMA_SPEC_DRAFT_N_MAX" "1"
if ([string]::IsNullOrEmpty($SpecDraft) -and
    $LlamaModel -eq $DefaultModel -and (Test-Path $DefaultSpecDraft)) {
    $SpecDraft = $DefaultSpecDraft
}

# Gemma 4 has no use for thinking in this pipeline; suppress it as start.sh does.
if ($LlamaModel -match "gemma-4|Gemma-4") {
    if ([string]::IsNullOrEmpty($LlamaReasoning)) { $LlamaReasoning = "off" }
    if ([string]::IsNullOrEmpty($LlamaThink))     { $LlamaThink = "0" }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install from https://docs.astral.sh/uv/ or: pip install uv"
}

# Install Python deps into .venv. misaki[ja] is skipped on Windows because its
# pyopenjtalk dependency has no wheels and needs MSVC; TTS still works without
# it, just with simpler Japanese G2P. To opt in: uv sync --extra ja-tts
uv sync
uv run app/scripts/setup_tts.py

$llamaProc = $null

try {
    if ($SkipLlama -ne "1") {
        if (-not (Test-Path $LlamaBin)) {
            throw @"
llama-server not found at $LlamaBin

Get a prebuilt binary:
  .\app\scripts\fetch_llama_win.ps1                    # AMD / Intel (Vulkan)
  .\app\scripts\fetch_llama_win.ps1 -Flavor cuda-13.3  # NVIDIA

Or point LLAMA_BIN at an existing one (a source build lands in
build\bin\Release\ on Windows, not build\bin\):
  `$env:LLAMA_BIN = "llama.cpp\build\bin\Release\llama-server.exe"

See docs\windows.md
"@
        }
        foreach ($p in @($LlamaModel, $LlamaMmproj)) {
            if (-not (Test-Path $p)) { throw "model file not found at $p" }
        }

        $llamaArgs = @(
            "--host", "127.0.0.1",
            "--port", $LlamaPort,
            "--parallel", $LlamaParallel,
            "-m", $LlamaModel,
            "-c", $LlamaCtx,
            "-ngl", "999",
            "--jinja",
            "--flash-attn", "on",
            "--mmproj", $LlamaMmproj
        )
        if ($LlamaReasoning) { $llamaArgs += @("--reasoning", $LlamaReasoning) }
        if ($LlamaThink)     { $llamaArgs += @("--reasoning-budget", $LlamaThink) }
        if ($SpecDraft) {
            if (-not (Test-Path $SpecDraft)) {
                throw "speculative draft model not found at $SpecDraft"
            }
            # --spec-draft-model alone is not enough: --spec-type defaults to
            # "none", so the draft head would load but never be used.
            $llamaArgs += @(
                "--spec-draft-model", $SpecDraft,
                "--spec-type", $SpecType,
                "--spec-draft-n-max", $SpecDraftNMax
            )
        }

        Write-Host "[INFO] starting llama.cpp server on port $LlamaPort"
        Write-Host "[INFO] binary     : $LlamaBin"
        Write-Host "[INFO] model      : $LlamaModel"
        Write-Host "[INFO] model name : $LlamaModelName"
        Write-Host "[INFO] mmproj     : $LlamaMmproj"
        if ($SpecDraft) {
            Write-Host "[INFO] spec draft : $SpecDraft (type: $SpecType, n-max: $SpecDraftNMax)"
        } else {
            Write-Host "[INFO] spec draft : disabled"
        }

        $llamaProc = Start-Process -FilePath $LlamaBin -ArgumentList $llamaArgs `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput "llama-server.log" `
            -RedirectStandardError  "llama-server.err.log"
    } else {
        Write-Host "[INFO] SKIP_LLAMACPP=1 -> assuming llama-server already running on $LlamaPort"
    }

    $env:LLAMA_SERVER_URL = Get-EnvOrDefault "LLAMA_SERVER_URL" "http://127.0.0.1:$LlamaPort"
    $env:LLAMA_CTX        = $LlamaCtx
    $env:LLAMA_MODEL_NAME = $LlamaModelName

    Write-Host "[INFO] starting FastAPI on port $WebPort"
    uv run uvicorn app.main:app --host 127.0.0.1 --port $WebPort
}
finally {
    if ($llamaProc -and -not $llamaProc.HasExited) {
        Write-Host "[INFO] stopping llama.cpp"
        Stop-Process -Id $llamaProc.Id -Force -ErrorAction SilentlyContinue
    }
}
