"""Project-scoped configuration and content hashes."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SERVER = RUNTIME / "server"
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


def config() -> dict:
    value = read_json(ROOT / "config/lab.json")
    if value["server_host"] != "127.0.0.1":
        raise ValueError("This desktop launch profile requires loopback binding")
    ports = [
        value["game_port"],
        value["bridge_port"],
        value["lab_port"],
        value["supervisor_port"],
    ]
    if len(set(ports)) != len(ports) or any(not 1024 <= p <= 65535 for p in ports):
        raise ValueError("Use distinct unprivileged game/bridge/lab/supervisor ports")
    if not 1 <= value["heap_gb"] <= 16:
        raise ValueError("Server heap must be between 1 and 16 GB")
    return value


def lock() -> dict:
    return read_json(ROOT / "config/dependencies.lock.json")
