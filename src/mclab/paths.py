"""Project-scoped configuration and content hashes."""

import hashlib
import json
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"  # shared: toolchains and downloads for every instance
LAB_INSTANCE = "lab"
_INSTANCE_NAME = re.compile(r"[a-z][a-z0-9-]{0,15}")


@dataclass(frozen=True)
class InstancePaths:
    """Every per-instance file location. The lab keeps its historical layout."""

    name: str
    config_file: Path
    runtime: Path  # supervisor.json, launch.lock, logs, mod-build.json
    server: Path
    arena_lock_name: str  # file under results/

    @property
    def state(self) -> Path:
        return self.runtime / "supervisor.json"

    @property
    def launch_lock(self) -> Path:
        return self.runtime / "launch.lock"

    @property
    def supervisor_log(self) -> Path:
        return self.runtime / "supervisor.log"

    @property
    def console_log(self) -> Path:
        return self.runtime / "server-console.log"

    @property
    def mod_build(self) -> Path:
        return self.runtime / "mod-build.json"

    @property
    def companion_log(self) -> Path:
        return self.runtime / "companion-host.log"


def instance_paths(name: str | None, root: Path = ROOT) -> InstancePaths:
    """Resolve an instance name ("" / None / "lab" = the lab) to its locations."""
    name = name or LAB_INSTANCE
    if name == LAB_INSTANCE:
        runtime = root / "runtime"
        return InstancePaths(
            name, root / "config/lab.json", runtime, runtime / "server", ".arena.lock"
        )
    if not _INSTANCE_NAME.fullmatch(name):
        raise ValueError(f"Invalid MCLAB_INSTANCE {name!r}: use [a-z][a-z0-9-]{{0,15}}")
    runtime = root / "runtime/instances" / name
    return InstancePaths(
        name,
        root / "config/instances" / f"{name}.json",
        runtime,
        runtime / "server",
        f".arena-{name}.lock",
    )


INSTANCE = instance_paths(os.environ.get("MCLAB_INSTANCE"))
SERVER = INSTANCE.server
INSTANCE_RUNTIME = INSTANCE.runtime
# Two independent server mods: the body (nervous system) and the lab (courses, scoring).
MODS = ("mcb-body", "mcb-lab")
# Source-bound inputs archived with evidence and hashed for provenance.
MOD_SOURCES = tuple(f"{mod}/src" for mod in MODS) + tuple(
    f"{mod}/build.gradle" for mod in MODS
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def config_ports(value: dict) -> list[str]:
    """Names of the port keys this instance's config must define."""
    keys = ["game_port", "bridge_port", "supervisor_port"]
    if value.get("lab_mod", True):
        keys.append("lab_port")
    if "companion_port" in value:
        keys.append("companion_port")
    return keys


def config() -> dict:
    value = read_json(INSTANCE.config_file)
    value.setdefault("lab_mod", True)
    if value["server_host"] != "127.0.0.1":
        raise ValueError("This desktop launch profile requires loopback binding")
    if not isinstance(value.setdefault("lan", False), bool):
        raise ValueError("lan is true or false (open the game port to the LAN)")
    ports = [value[key] for key in config_ports(value)]
    if len(set(ports)) != len(ports) or any(not 1024 <= p <= 65535 for p in ports):
        raise ValueError("Use distinct unprivileged game/bridge/lab/supervisor ports")
    if not 1 <= value["heap_gb"] <= 16:
        raise ValueError("Server heap must be between 1 and 16 GB")
    return value


def game_bind(settings: dict) -> str:
    """Minecraft's ``server-ip``. With ``lan`` the game port listens on every
    interface (empty), so other PCs on the network can join; this PC still joins at
    127.0.0.1. Every other port (bridge, supervisor, companion) stays loopback."""
    return "" if settings.get("lan") else settings.get("server_host", "127.0.0.1")


def _udp_probe():
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def lan_ip(*, probe=_udp_probe) -> str | None:
    """This PC's address on its network: the source address of the default route
    (a UDP connect picks the route and sends nothing). None when offline."""
    sock = probe()
    try:
        sock.connect(("10.255.255.255", 1))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def lock() -> dict:
    return read_json(ROOT / "config/dependencies.lock.json")
