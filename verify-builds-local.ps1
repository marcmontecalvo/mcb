$ErrorActionPreference = 'Stop'
$env:JAVA_HOME = 'D:/mcb/runtime/toolchains/jdk/jdk-25.0.4.1+1'
$env:GRADLE_USER_HOME = (Resolve-Path 'runtime/gradle-cache').Path
& rtk proxy .\mcb-body\gradlew.bat -p mcb-body --offline build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& rtk proxy .\mcb-lab\gradlew.bat -p mcb-lab --offline build
exit $LASTEXITCODE
