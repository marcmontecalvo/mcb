$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force runtime/downloads,docs,config,scripts,src/mclab,tests,courses | Out-Null
$urls = @{
    mojang='https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'
    loader='https://meta.fabricmc.net/v2/versions/loader/26.3'
    loom='https://maven.fabricmc.net/net/fabricmc/fabric-loom/maven-metadata.xml'
    api='https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/maven-metadata.xml'
    carpet='https://api.github.com/repos/gnembon/fabric-carpet/releases/tags/v26.3'
}
foreach ($key in $urls.Keys) {
    try {
        Invoke-WebRequest -UseBasicParsing $urls[$key] -OutFile ('runtime/downloads/'+$key+'.txt')
        Write-Output ($key+' downloaded')
    } catch { Write-Output ($key+': '+$_.Exception.Message) }
}
