"""Explicit maintainer operation; normal setup consumes the committed lock."""

import hashlib
import json
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runtime" / "downloads"
CACHE.mkdir(parents=True, exist_ok=True)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "mclab/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def artifact(name, url, algorithm="sha256", expected=None):
    path = CACHE / name
    if not path.exists():
        path.write_bytes(fetch(url))
    data = path.read_bytes()
    digest = hashlib.new(algorithm, data).hexdigest()
    if expected and digest != expected:
        raise ValueError(f"Checksum mismatch: {name}")
    return {
        "name": name,
        "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


manifest = json.loads((CACHE / "mojang.txt").read_text())
version = next(v for v in manifest["versions"] if v["id"] == "26.3")
metadata = json.loads(fetch(version["url"]))
(CACHE / "minecraft-version.json").write_text(json.dumps(metadata, indent=2))
server = metadata["downloads"]["server"]
assets = {}
assets["minecraft_server"] = artifact(
    "minecraft-server-26.3.jar", server["url"], "sha1", server["sha1"]
)
release = json.loads((CACHE / "carpet.txt").read_text())
carpet = next(a for a in release["assets"] if a["name"].endswith(".jar"))
assets["carpet"] = artifact(
    carpet["name"],
    carpet["browser_download_url"],
    expected="7c40b93e807ed914064176898037117097446c6cc76e6a80a856c8bf7f7659f9",
)
api_versions = ET.fromstring((CACHE / "api.txt").read_text()).findall(".//version")
api = [v.text for v in api_versions if v.text.endswith("+26.3")][-1]
assets["fabric_api"] = artifact(
    f"fabric-api-{api}.jar",
    f"https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/{api}/fabric-api-{api}.jar",
)
installers = json.loads(fetch("https://meta.fabricmc.net/v2/versions/installer"))
installer = next(i for i in installers if i["stable"])["version"]
assets["fabric_launcher"] = artifact(
    "fabric-server-launch.jar",
    f"https://meta.fabricmc.net/v2/versions/loader/26.3/0.19.5/{installer}/server/jar",
)
loom_versions = ET.fromstring((CACHE / "loom.txt").read_text()).findall(".//version")
loom = [
    v.text
    for v in loom_versions
    if v.text.startswith("1.17.") and v.text.removeprefix("1.17.").isdigit()
][-1]
jdk_releases = json.loads(
    fetch(
        "https://api.adoptium.net/v3/assets/latest/25/hotspot?architecture=x64&image_type=jdk&os=windows"
    )
)
jdk = jdk_releases[0]["binary"]["package"]
assets["jdk"] = artifact(jdk["name"], jdk["link"], expected=jdk["checksum"])
gradle_url = "https://services.gradle.org/distributions/gradle-9.6.0-bin.zip"
assets["gradle"] = artifact(
    "gradle-9.6.0-bin.zip",
    gradle_url,
    expected=fetch(gradle_url + ".sha256").decode().strip(),
)
lock = {
    "schema_version": 1,
    "minecraft": "26.3",
    "loader": "0.19.5",
    "fabric_installer": installer,
    "fabric_api": api,
    "loom": loom,
    "gradle": "9.6.0",
    "jdk_release": jdk_releases[0]["release_name"],
    "carpet_commit": "c6d48ab48551f28d367cfcce8a948414c2ca5f74",
    "datapack_version": json.loads(
        zipfile.ZipFile(CACHE / "minecraft-server-26.3.jar").read("version.json")
    )["pack_version"],
    "artifacts": assets,
}
(ROOT / "config" / "dependencies.lock.json").write_text(
    json.dumps(lock, indent=2) + "\n"
)
print(json.dumps({k: v for k, v in lock.items() if k != "artifacts"}, indent=2))
