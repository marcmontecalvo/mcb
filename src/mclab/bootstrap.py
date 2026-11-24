"""Checksum-locked, project-local runtime installation."""

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

from mclab.paths import INSTANCE, RUNTIME, SERVER, config, game_bind, lock


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(artifact: dict) -> Path:
    path = RUNTIME / "downloads" / artifact["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or sha256(path) != artifact["sha256"]:
        temp = path.with_suffix(path.suffix + ".partial")
        request = urllib.request.Request(
            artifact["url"], headers={"User-Agent": "mclab/0.1"}
        )
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            temp.open("wb") as out,
        ):
            shutil.copyfileobj(response, out)
        if sha256(temp) != artifact["sha256"]:
            temp.unlink()
            raise ValueError(f"Checksum mismatch for {artifact['name']}")
        temp.replace(path)
    return path


def java() -> Path:
    candidates = sorted((RUNTIME / "toolchains/jdk").glob("*/bin/java.exe"))
    if len(candidates) != 1:
        raise RuntimeError("Run mclab bootstrap to install the project JDK")
    return candidates[0]


def bootstrap() -> dict:
    settings = config()
    artifacts = lock()["artifacts"]
    for name, artifact in artifacts.items():
        archive = download(artifact)
        if name in {"jdk", "gradle"}:
            target = RUNTIME / "toolchains" / name
            marker = target / ".sha256"
            if not marker.exists() or marker.read_text() != artifact["sha256"]:
                if target.exists():
                    raise RuntimeError(
                        f"Unexpected existing toolchain at {target}; inspect it"
                    )
                target.mkdir(parents=True)
                with zipfile.ZipFile(archive) as zipped:
                    for member in zipped.namelist():
                        if (
                            not (target / member)
                            .resolve()
                            .is_relative_to(target.resolve())
                        ):
                            raise ValueError("Unsafe archive member")
                    zipped.extractall(target)
                marker.write_text(artifact["sha256"])
        else:
            relative = {
                "minecraft_server": "server.jar",
                "fabric_launcher": "fabric-server-launch.jar",
                "carpet": "mods/" + artifact["name"],
                "fabric_api": "mods/" + artifact["name"],
            }[name]
            destination = SERVER / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, destination)
    properties = SERVER / "server.properties"
    if not properties.exists():
        values = (
            _lab_properties(settings)
            if INSTANCE.name == "lab"
            else _world_properties(settings)
        )
        properties.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    eula = SERVER / "eula.txt"
    if not eula.exists():
        eula.write_text(
            "# Accept https://aka.ms/MinecraftEULA before starting.\neula=false\n"
        )
    return {
        "java": str(java()),
        "artifacts_verified": len(artifacts),
        "server": str(SERVER),
    }


def _world_properties(settings: dict) -> dict:
    """server.properties for a play or arena instance: its world's type, and for a
    flat world (the arena server's empty void) the generator layers, whether
    structures generate and how many may join."""
    world = settings["world"]
    values = {
        "server-ip": game_bind(settings),
        "server-port": settings["game_port"],
        "online-mode": str(settings["online_mode"]).lower(),
        "level-name": "world",
        "level-type": world["level_type"],
        "level-seed": world["seed"],
        "gamemode": "survival",
        "difficulty": world["difficulty"],
        "spawn-protection": 0,
        "max-players": world.get("max_players", 8),
        "view-distance": world["view_distance"],
        "simulation-distance": world["simulation_distance"],
        "pause-when-empty-seconds": -1,
        "enable-rcon": "false",
        "enable-query": "false",
        "white-list": "false",
        "motd": world["motd"],
    }
    if "generator_settings" in world:
        values["generator-settings"] = world["generator_settings"]
    if "generate_structures" in world:
        values["generate-structures"] = str(world["generate_structures"]).lower()
    return values


def _lab_properties(settings: dict) -> dict:
    return {
        "server-ip": game_bind(settings),
        "server-port": settings["game_port"],
        "online-mode": str(settings["online_mode"]).lower(),
        "level-name": "world",
        "level-type": "minecraft:flat",
        "level-seed": settings["world_seed"],
        "gamemode": "survival",
        "difficulty": "normal",
        "spawn-protection": 0,
        "max-players": 4,
        "view-distance": 6,
        "simulation-distance": 5,
        "pause-when-empty-seconds": -1,
        "enable-rcon": "false",
        "enable-query": "false",
        "white-list": "false",
        "motd": "Minecraft AI Lab - development, unscored",
        "generator-settings": '{"layers":[{"block":"minecraft:bedrock",'
        '"height":1},{"block":"minecraft:dirt","height":2},'
        '{"block":"minecraft:grass_block","height":1}],'
        '"biome":"minecraft:plains"}',
    }
