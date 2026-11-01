"""Named cost profiles (Phase 6): race, safe, frugal, hunt, escort.

Each profile is a set of TerrainPolicy overrides in config/profiles/<name>.json,
applied on top of the run's terrain profile, plus an optional goal kind.
"""

import json
from dataclasses import replace
from pathlib import Path

from mclab.terrain_steps import TerrainPolicy

PROFILE_DIR = Path(__file__).resolve().parents[2] / "config" / "profiles"
PROFILES = ("race", "safe", "frugal", "hunt", "escort")
GOAL_KINDS = {"goto", "follow"}

HOSTILE = frozenset(
    "minecraft:" + name
    for name in (
        "zombie husk drowned zombie_villager skeleton stray bogged creeper spider "
        "cave_spider witch slime magma_cube phantom pillager vindicator evoker "
        "ravager vex silverfish endermite blaze ghast hoglin zoglin piglin_brute "
        "wither_skeleton guardian elder_guardian breeze warden"
    ).split()
)


def load_profile(name):
    """The profile's JSON, validated: known name, TerrainPolicy fields only."""
    if name not in PROFILES:
        raise ValueError(f"Unknown cost profile: {name}")
    data = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    terrain = data.get("terrain")
    if (
        data.get("name") != name
        or not isinstance(terrain, dict)
        or not terrain.keys() <= TerrainPolicy.__dataclass_fields__.keys()
    ):
        raise ValueError(f"Invalid cost profile file: {name}")
    goal = data.get("goal", {"kind": "goto"})
    if goal.get("kind") not in GOAL_KINDS:
        raise ValueError(f"Invalid cost profile goal: {name}")
    return {**data, "goal": goal}


def apply_profile(name, terrain_policy):
    """TerrainPolicy with the profile's overrides (validated by the dataclass)."""
    return replace(terrain_policy, **load_profile(name)["terrain"])


def threats(observation):
    """Positions of visible hostile entities, for the planner's avoidance term."""
    return tuple(
        tuple(entity["position"])
        for entity in observation.get("visible_entities", ())
        if entity.get("type") in HOSTILE and len(entity.get("position", ())) == 3
    )
