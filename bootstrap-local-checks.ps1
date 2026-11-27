$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force 'runtime/gradle-cache','runtime/downloads' | Out-Null
Copy-Item -Recurse 'D:/mcb/runtime/gradle-cache/caches' 'runtime/gradle-cache/'
Copy-Item -Recurse 'D:/mcb/runtime/gradle-cache/wrapper' 'runtime/gradle-cache/'
Copy-Item 'D:/mcb/runtime/downloads/fabric-carpet-26.3+v260915.jar' 'runtime/downloads/'
$env:JAVA_HOME = 'D:/mcb/runtime/toolchains/jdk/jdk-25.0.4.1+1'
$env:GRADLE_USER_HOME = (Resolve-Path 'runtime/gradle-cache').Path
& rtk proxy .\mcb-body\gradlew.bat -p mcb-body --offline verifyRoutePlanner
exit $LASTEXITCODE
