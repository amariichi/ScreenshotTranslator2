<#
.SYNOPSIS
  Download a prebuilt llama.cpp release for Windows (no C++ toolchain needed).

.DESCRIPTION
  Building llama.cpp on Windows needs MSVC (and the ROCm HIP SDK for AMD), so for
  a clone-and-run setup the official prebuilt releases are the practical path.

  Extracts to .\llama.cpp-win\ , which is what start.ps1 uses by default.

  For CUDA flavors the matching cudart-*.zip (the CUDA runtime DLLs) is fetched
  and extracted into the same folder automatically -- llama-server.exe does not
  start without it.

.PARAMETER Flavor
  Which build to fetch:
    vulkan      AMD / NVIDIA / Intel, works everywhere (default, smallest)
    cuda-13.3   NVIDIA, CUDA 13.x
    cuda-12.4   NVIDIA, CUDA 12.x
    hip-radeon  AMD ROCm
    cpu         no GPU offload
  Any other suffix present in the release is accepted as-is (e.g. sycl).

.PARAMETER Tag
  Release tag to fetch. Defaults to the latest release.
  Speculative decoding with an MTP head needs a recent build -- see docs/mtp.md.

.EXAMPLE
  .\app\scripts\fetch_llama_win.ps1                    # Vulkan (AMD APU など)
  .\app\scripts\fetch_llama_win.ps1 -Flavor cuda-13.3  # NVIDIA
#>
[CmdletBinding()]
param(
    [string]$Flavor = "vulkan",
    [string]$Tag = "",
    [string]$Destination = "llama.cpp-win"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$headers = @{ "User-Agent" = "screenshot-translator" }
$api = if ($Tag) {
    "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$Tag"
} else {
    "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
}

Write-Host "[INFO] querying $api"
$release = Invoke-RestMethod -Uri $api -Headers $headers

# Assets are named like: llama-b10238-bin-win-vulkan-x64.zip
# The "llama-" prefix matters: cudart-llama-bin-win-cuda-13.3-x64.zip would also
# match a bare "*bin-win-<flavor>-x64.zip" pattern, and it sorts first.
$asset = $release.assets |
         Where-Object { $_.name -like "llama-*bin-win-$Flavor-x64.zip" } |
         Select-Object -First 1

if (-not $asset) {
    $available = ($release.assets |
                  Where-Object { $_.name -like "llama-*bin-win-*" } |
                  ForEach-Object { ($_.name -replace '^llama-[^-]+-bin-win-', '') -replace '-x64\.zip$', '' }
                 ) -join ", "
    throw "No Windows asset for flavor '$Flavor' in release $($release.tag_name).`nAvailable flavors: $available"
}

Write-Host "[INFO] release : $($release.tag_name)"
Write-Host "[INFO] asset   : $($asset.name)"

if (Test-Path $Destination) {
    Write-Host "[INFO] removing previous $Destination"
    Remove-Item -Recurse -Force $Destination
}

function Expand-Asset($assetObj, $dest) {
    $zip = Join-Path $env:TEMP $assetObj.name
    Write-Host "[INFO] downloading $($assetObj.name) ($([math]::Round($assetObj.size / 1MB)) MB)"
    # Progress rendering makes Invoke-WebRequest dramatically slower on big files.
    $prev = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    try {
        Invoke-WebRequest -Uri $assetObj.browser_download_url -OutFile $zip
    } finally {
        $ProgressPreference = $prev
    }
    Expand-Archive -Path $zip -DestinationPath $dest -Force
    Remove-Item $zip -Force
}

Expand-Asset $asset $Destination

# CUDA builds link against the CUDA runtime, shipped as a separate archive.
# Without these DLLs llama-server.exe fails to start.
if ($Flavor -like "cuda-*") {
    $cudart = $release.assets |
              Where-Object { $_.name -like "cudart-*win-$Flavor-x64.zip" } |
              Select-Object -First 1
    if ($cudart) {
        Write-Host "[INFO] CUDA flavor detected -> also fetching the CUDA runtime"
        Expand-Asset $cudart $Destination
    } else {
        Write-Warning "cudart archive for '$Flavor' not found in this release. If llama-server.exe fails to start with a missing DLL error, download cudart-llama-bin-win-$Flavor-x64.zip manually and extract it into $Destination."
    }
}

$server = Join-Path $Destination "llama-server.exe"
if (-not (Test-Path $server)) {
    # Be tolerant if a future release nests the binaries one level down.
    $found = Get-ChildItem -Path $Destination -Filter "llama-server.exe" -Recurse |
             Select-Object -First 1
    if ($found) { $server = $found.FullName }
}

if (Test-Path $server) {
    Write-Host "[INFO] ready: $server"
    Write-Host "[INFO] next : .\start.ps1"
} else {
    throw "llama-server.exe not found under $Destination after extraction."
}
