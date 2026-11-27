$ErrorActionPreference = 'Stop'
$jdk = 'D:\mcb\runtime\toolchains\jdk\jdk-25.0.4.1+1\bin'
$gson = 'D:\mcb\runtime\gradle-cache\caches\modules-2\files-2.1\com.google.code.gson\gson\2.14.0\efc0e34ede4e3204eaefb84a00e55e8c86634382\gson-2.14.0.jar'
New-Item -ItemType Directory -Force -Path '.local-planner-classes' | Out-Null
$source = 'mcb-body/src/main/java/com/vulpine/mcb/body'
& (Join-Path $jdk 'javac.exe') -cp $gson -d '.local-planner-classes' "$source/RouteMotor.java" "$source/RouteGeometry.java" "$source/RoutePlanner.java" 'mcb-body/src/verification/java/com/vulpine/mcb/body/RoutePlannerTest.java'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $jdk 'java.exe') -cp ".local-planner-classes;$gson" com.vulpine.mcb.body.RoutePlannerTest
exit $LASTEXITCODE
