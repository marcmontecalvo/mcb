"""Declarative development courses; evaluator manifests never enter policy input."""

import random
import shutil
from pathlib import Path

from mclab.paths import ROOT, SERVER, config, digest, lock, read_json, write_json


def generate(course: str, seed: int, origin: list[int]) -> dict:
    profiles = read_json(ROOT / "courses/profiles.json")
    if course not in profiles["courses"]:
        raise ValueError(f"Course {course} is not implemented")
    if len(origin) != 3 or not all(type(v) is int for v in origin):
        raise ValueError("Origin must contain three integers")
    if not 64 <= origin[1] <= 250 or any(abs(v) > 29_000_000 for v in origin[::2]):
        raise ValueError("Origin outside supported build envelope")
    rng = random.Random(seed)
    defaults = profiles["defaults"]
    profile = profiles["courses"][course]
    target = (
        rng.choice(profile["target_candidates"])
        if "target_candidates" in profile
        else defaults["target"]
    )
    geometry = [defaults["platform"]]
    offset = (
        rng.choice(profile["obstacle_x_offsets"])
        if "obstacle_x_offsets" in profile
        else 0
    )
    for feature in profile.get("geometry", []):
        feature["from"][0] += offset
        feature["to"][0] += offset
        geometry.append(feature)
    geometry.append({"from": target, "to": target, "block": defaults["target_block"]})
    value = {
        "schema_version": 1,
        "course_id": course,
        "seed": seed,
        "generator_version": profiles["generator_version"],
        "origin": origin,
        "track": "perception_goal",
        "sensor_profile": "pending-qualification",
        "development_only": True,
        "tick_limit": profile["tick_limit"],
        "spawn": defaults["spawn"],
        "yaw": rng.randrange(-180, 180)
        if profile.get("random_yaw")
        else defaults["yaw"],
        "pitch": defaults["pitch"],
        "target": target,
        "geometry": geometry,
        "initial_inventory": defaults["initial_inventory"],
        "allowed_region": defaults["allowed_region"],
        "clear_region": defaults["clear_region"],
        "pit_plane": defaults["pit_plane"],
        "break_whitelist": [target],
        "place_whitelist": [],
        "success": "causal_target_break_drop_pickup",
        "qualification": "not_run",
    }
    value["spec_hash"] = digest(value)
    return value


def absolute(local: list[int], origin: list[int]) -> str:
    return " ".join(str(a + b) for a, b in zip(local, origin, strict=True))


def compile_build(spec: dict) -> list[str]:
    origin = spec["origin"]
    lo, hi = spec["clear_region"]["min"], spec["clear_region"]["max"]
    # Each fill is below vanilla's 32768-block command limit.
    lines = []
    for y in range(lo[1], hi[1] + 1, 8):
        a, b = [lo[0], y, lo[2]], [hi[0], min(y + 7, hi[1]), hi[2]]
        lines.append(f"fill {absolute(a, origin)} {absolute(b, origin)} minecraft:air")
    for geometry in spec["geometry"]:
        a, b = absolute(geometry["from"], origin), absolute(geometry["to"], origin)
        lines.append(f"fill {a} {b} {geometry['block']}")
    return lines


def build(course: str, seed: int, destination: Path | None = None) -> dict:
    settings = config()
    destination = destination or SERVER / "world/datapacks/mclab"
    destination.mkdir(parents=True, exist_ok=True)
    # Course replacement is allowed only with a stopped server (enforced by CLI).
    functions = destination / "data/mclab/function"
    functions.mkdir(parents=True, exist_ok=True)
    all_specs = {}
    for name in read_json(ROOT / "courses/profiles.json")["courses"]:
        spec = generate(name, seed, settings["origin"])
        all_specs[name] = spec
        write_json(destination / "evaluator" / f"{name}.json", spec)
        (functions / f"{name.lower()}_build.mcfunction").write_text(
            "\n".join(compile_build(spec)) + "\n", encoding="utf-8"
        )
    if course not in all_specs:
        raise ValueError(f"Unknown course {course}")
    spec = all_specs[course]
    version = lock()["datapack_version"]
    pack_format = [version["data_major"], version["data_minor"]]
    write_json(
        destination / "pack.mcmeta",
        {
            "pack": {
                "description": "MCLab 26.3 development courses; unscored",
                "min_format": pack_format,
                "max_format": pack_format,
            }
        },
    )
    x, y, z = spec["origin"]
    # Generate once after chunks load. Reloading must not silently rebuild an episode.
    load = [
        "execute unless data storage mclab:bootstrap "
        f'{{spec_hash:"{spec["spec_hash"]}"}} run schedule function '
        "mclab:initialize 2s replace"
    ]
    initialize = [
        f"forceload add {x - 24} {z - 24} {x + 24} {z + 24}",
        "schedule function mclab:build_active 2s replace",
    ]
    active = [
        f"function mclab:{course.lower()}_build",
        f"setworldspawn {x} {y} {z}",
        "time set noon",
        "weather clear",
        "gamerule minecraft:advance_time false",
        "gamerule minecraft:advance_weather false",
        "gamerule minecraft:spawn_mobs false",
        "data modify storage mclab:bootstrap spec_hash set value "
        f'"{spec["spec_hash"]}"',
    ]
    for name, commands in {
        "load": load,
        "initialize": initialize,
        "build_active": active,
    }.items():
        (functions / f"{name}.mcfunction").write_text("\n".join(commands) + "\n")
    write_json(
        destination / "data/minecraft/tags/function/load.json",
        {"values": ["mclab:load"]},
    )
    write_json(destination / "evaluator/active.json", spec)
    # Reproduction copy outside the Minecraft world. Not an authoritative state digest.
    output = ROOT / "results/generated" / f"{course}-{seed}"
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(destination / "evaluator/active.json", output / "spec.json")
    (output / "build.mcfunction").write_text("\n".join(compile_build(spec)) + "\n")
    return {
        "pack": str(destination),
        "course": course,
        "seed": seed,
        "spec_hash": spec["spec_hash"],
        "status": "compiled_not_world_verified",
    }


def verify_generated(destination: Path | None = None) -> dict:
    destination = destination or SERVER / "world/datapacks/mclab"
    spec = read_json(destination / "evaluator/active.json")
    expected = generate(spec["course_id"], spec["seed"], spec["origin"])
    if spec != expected:
        raise ValueError("Generated specification has drifted")
    commands = (
        (
            destination
            / "data/mclab/function"
            / f"{spec['course_id'].lower()}_build.mcfunction"
        )
        .read_text()
        .splitlines()
    )
    if commands != compile_build(expected):
        raise ValueError("Generated commands do not match the specification")
    return {
        "status": "generated_files_verified",
        "spec_hash": spec["spec_hash"],
        "authoritative_world_verified": False,
    }
