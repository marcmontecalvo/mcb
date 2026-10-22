$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
    & rtk proxy uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
    & rtk proxy uv run --locked mclab bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap failed' }
    & rtk proxy uv run --locked mclab course build --course C1 --seed 0
    if ($LASTEXITCODE -ne 0) { throw 'Course build failed' }
    & (Join-Path $PSScriptRoot 'build-mod.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Mod build failed' }
    & rtk proxy uv run --locked mclab mod-install
    if ($LASTEXITCODE -ne 0) { throw 'Mod installation failed' }
    & rtk proxy uv run --locked mclab doctor
    exit $LASTEXITCODE
} finally { Pop-Location }
