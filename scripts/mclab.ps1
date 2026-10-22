$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
    & rtk proxy uv run --locked mclab @args
    exit $LASTEXITCODE
} finally { Pop-Location }
