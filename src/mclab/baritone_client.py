"""Baritone reference client: Prism instance setup, launch and loopback bridge calls.

The Microsoft session stays inside Prism Launcher; this module never reads account
files or tokens. The bridge token is a local secret in runtime/ and never recorded.
Bridge acknowledgments are not task outcomes; server scoring stays authoritative.
"""

import hashlib
import http.client
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from mclab.paths import ROOT, RUNTIME, config, read_json, write_json

STATE = RUNTIME / "client-bridge.json"
TOKEN_FILE = RUNTIME / "client-bridge.token"
INSTANCE_ID = "mclab-baritone"
BRIDGE_PORT = 8767
BRIDGE_JAR = ROOT / "client-bridge/build/libs/mclab-client-bridge-0.1.0.jar"
# ponytail: default is this machine's portable Prism; override with MCLAB_PRISM_DIR.
DEFAULT_PRISM = Path(r"D:\Games\PrismLauncher-Windows-MinGW-w64-Portable-11.1.1")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prism_dir() -> Path:
    path = Path(os.environ.get("MCLAB_PRISM_DIR", DEFAULT_PRISM))
    if not (path / "prismlauncher.exe").exists():
        raise RuntimeError(f"Prism Launcher not found at {path}; set MCLAB_PRISM_DIR")
    return path


def prepare() -> dict:
    """Write the dedicated 26.3 Fabric instance with Baritone and the bridge."""
    pins = read_json(ROOT / "config/dependencies.lock.json")
    baritone = read_json(ROOT / "config/baselines.lock.json")["baritone"]
    baritone_jar = RUNTIME / "downloads" / baritone["artifact"]
    if _sha256(baritone_jar) != baritone["sha256"]:
        raise RuntimeError("Baritone artifact does not match its pinned SHA-256")
    if not BRIDGE_JAR.exists():
        raise RuntimeError(
            "Build client-bridge first (scripts/build-client-bridge.ps1)"
        )
    jdk = next((RUNTIME / "toolchains/jdk").iterdir())
    token = secrets.token_hex(32)  # rotated on every prepare

    instance = prism_dir() / "instances" / INSTANCE_ID
    mods = instance / "minecraft/mods"
    if mods.exists():
        shutil.rmtree(mods)  # only the two pinned jars belong here
    mods.mkdir(parents=True)
    shutil.copyfile(baritone_jar, mods / baritone_jar.name)
    shutil.copyfile(BRIDGE_JAR, mods / BRIDGE_JAR.name)
    # Prism logs JVM arguments, so pass a path to the secret, never the secret itself.
    TOKEN_FILE.write_text(token, encoding="utf-8")
    jvm = (
        f"-Dmclab.client_bridge_token_file={TOKEN_FILE.as_posix()} "
        f"-Dmclab.client_bridge_port={BRIDGE_PORT}"
    )
    (instance / "instance.cfg").write_text(
        "\n".join(
            [
                "[General]",
                "ConfigVersion=1.2",
                "InstanceType=OneSix",
                "name=mclab Baritone reference",
                "OverrideJavaLocation=true",
                f"JavaPath={(jdk / 'bin/javaw.exe').as_posix()}",
                "OverrideJavaArgs=true",
                f"JvmArgs={jvm}",
                # Prism drops --server when the launch is handed to a running window.
                "JoinServerOnLaunch=true",
                f"JoinServerOnLaunchAddress=127.0.0.1:{config()['game_port']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    pack = {
        "formatVersion": 1,
        "components": [
            {"uid": "net.minecraft", "version": pins["minecraft"], "important": True},
            {"uid": "net.fabricmc.fabric-loader", "version": pins["loader"]},
        ],
    }
    (instance / "mmc-pack.json").write_text(
        json.dumps(pack, indent=4), encoding="utf-8"
    )
    value = {
        "schema_version": 1,
        "token": token,
        "port": BRIDGE_PORT,
        "instance": str(instance),
        "baritone_sha256": baritone["sha256"],
        "bridge_jar_sha256": _sha256(BRIDGE_JAR),
    }
    write_json(STATE, value)
    return {k: v for k, v in value.items() if k != "token"}


def launch() -> dict:
    """Start the instance through Prism with its signed-in account, joining the lab.

    Run `prepare` first: rewriting files here races Prism's instance-folder watcher.
    """
    if not STATE.exists():
        raise RuntimeError("Run `mclab baritone prepare` first")
    result = {k: v for k, v in read_json(STATE).items() if k != "token"}
    address = f"127.0.0.1:{config()['game_port']}"
    subprocess.Popen(
        [
            str(prism_dir() / "prismlauncher.exe"),
            "--launch",
            INSTANCE_ID,
            "--server",
            address,
        ]
    )
    return {**result, "launched": True, "server": address}


class BaritoneClient:
    def __init__(self, timeout: float = 4):
        state = read_json(STATE)
        self._token = state["token"]
        self._connection = http.client.HTTPConnection(
            "127.0.0.1", state["port"], timeout=timeout
        )

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload)
        headers = {
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
        }
        self._connection.request(method, path, body=body, headers=headers)
        response = self._connection.getresponse()
        value = json.loads(response.read() or b"{}")
        if response.status != 200:
            raise RuntimeError(
                f"Client bridge {path} -> {response.status}: {value.get('error')}"
            )
        return value

    def status(self) -> dict:
        return self._call("GET", "/status")

    def goto(self, x: int, y: int, z: int) -> dict:
        return self._call("POST", "/goto", {"x": x, "y": y, "z": z})

    def mine(self, block: str, quantity: int) -> dict:
        return self._call("POST", "/mine", {"block": block, "quantity": quantity})

    def cancel(self) -> dict:
        return self._call("POST", "/cancel", {})

    def policy(self, **flags: bool) -> dict:
        return self._call("POST", "/policy", flags)
