param([switch]$UpdateLocks)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $jdk = Get-ChildItem -LiteralPath 'runtime/toolchains/jdk' -Directory | Select-Object -First 1
    if (-not $jdk) { throw 'Run setup first' }
    $env:JAVA_HOME = $jdk.FullName
    $env:GRADLE_USER_HOME = Join-Path (Get-Location) 'runtime/gradle-cache'
    $gradle = Join-Path (Get-Location) 'client-bridge/gradlew.bat'
    if ($UpdateLocks) {
        & rtk proxy $gradle -p client-bridge build --write-locks --write-verification-metadata sha256
    } else {
        & rtk proxy $gradle -p client-bridge build
    }
    if ($LASTEXITCODE -ne 0) { throw 'Client bridge build failed' }
} finally { Pop-Location }
