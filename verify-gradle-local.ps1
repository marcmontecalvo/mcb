$env:JAVA_HOME = 'D:/mcb/runtime/toolchains/jdk/jdk-25.0.4.1+1'
$env:GRADLE_USER_HOME = (Resolve-Path 'runtime/gradle-cache').Path
& rtk proxy .\mcb-body\gradlew.bat -p mcb-body --offline verifyRouteMotor verifyRoutePlanner verifyRoutePermissions verifyPlannerMap verifyPlanData verifyTerrainPlanner
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& rtk proxy .\mcb-body\gradlew.bat -p mcb-body --offline -q terrainPlannerParity | Where-Object { $_ -match '^\d+\|\d+\|\d+\|' } | Set-Content -Encoding UTF8 'runtime/terrain-parity-output.txt'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& rtk proxy uv run --locked python scripts/analysis/terrain_java_parity.py runtime/terrain-parity-output.txt
exit $LASTEXITCODE
