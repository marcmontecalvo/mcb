$ErrorActionPreference = 'Stop'
$env:JAVA_HOME = 'D:/mcb/runtime/toolchains/jdk/jdk-25.0.4.1+1'
$env:GRADLE_USER_HOME = (Resolve-Path 'runtime/gradle-cache').Path
& rtk proxy uv run --locked python scripts/analysis/route_differential.py generate runtime/route-cases.json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& rtk proxy .\mcb-body\gradlew.bat -p mcb-body --offline -q routeDifferential '-Pargs=../runtime/route-cases.json' | Where-Object { $_.StartsWith('{') } | Set-Content -Encoding UTF8 'runtime/route-java.jsonl'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& rtk proxy uv run --locked python scripts/analysis/route_differential.py compare runtime/route-cases.json runtime/route-java.jsonl
exit $LASTEXITCODE
