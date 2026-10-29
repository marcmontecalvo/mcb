param([switch]$Restart)

$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if ($Restart) {
        $dashboardPort = 8770
        if (Test-Path -LiteralPath 'runtime/dashboard-ui.json') {
            $dashboardPort = (Get-Content -LiteralPath 'runtime/dashboard-ui.json' -Raw | ConvertFrom-Json).port
        }
        if ($dashboardPort -lt 1 -or $dashboardPort -gt 65535) {
            throw 'Invalid recorded dashboard port.'
        }
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalAddress -eq '127.0.0.1' -and $_.LocalPort -eq $dashboardPort })
        foreach ($listener in $listeners) {
            $dashboardProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
            if ($dashboardProcess.Name -notmatch '^python(w)?\.exe$' -or
                $dashboardProcess.CommandLine -notmatch '(?:^|\s)-m\s+mclab\.dashboard(?:\s|$)' -or
                $dashboardProcess.CommandLine -match '(?:^|\s)--worker(?:\s|$)') {
                throw "Port $dashboardPort belongs to another process; refusing to stop it."
            }
            Stop-Process -Id $dashboardProcess.ProcessId -ErrorAction Stop
            Wait-Process -Id $dashboardProcess.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
    $env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
    if (Test-Path -LiteralPath '.env') {
        & rtk proxy uv run --env-file .env --locked python -m mclab.dashboard @args
    } else {
        & rtk proxy uv run --locked python -m mclab.dashboard @args
    }
    exit $LASTEXITCODE
} finally { Pop-Location }
