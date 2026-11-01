"""Phase 6: D7 survival terms, named cost profiles, the Jev contract."""

from dataclasses import replace

import pytest
from test_planner_core import search, world

from mclab import jev_contract as jev
from mclab.profiles import PROFILES, apply_profile, load_profile, threats
from mclab.terrain_steps import TerrainPolicy

TICKS = TerrainPolicy(planner_tick_costs=True)


def flat(x, y, z):
    return "minecraft:stone" if y == 0 else None


def test_every_named_profile_loads_and_race_changes_nothing():
    for name in PROFILES:
        assert isinstance(apply_profile(name, TICKS), TerrainPolicy)
    assert apply_profile("race", TICKS) == TICKS
    assert load_profile("escort")["goal"] == {"kind": "follow", "radius": 3}
    with pytest.raises(ValueError):
        load_profile("reckless")


def test_safe_profile_detours_around_a_hostile_and_hunt_walks_through():
    # 9 wide corridor; the zombie stands on the straight line to the goal.
    cells = world(range(-1, 21), range(-5, 6), flat, 4)
    zombie = ((10, 1, 0),)

    def max_offset(name, **overrides):
        policy = replace(apply_profile(name, TICKS), **overrides)
        r = search(cells, (0, 1, 0), (20, 1, 0), policy, threats=zombie)
        assert r.status == "reached"
        return max(abs(n[2]) for n in r.route)

    assert max_offset("race") == 0
    # The profile's 12-block radius spans this corridor; a 3-block one leaves room.
    assert max_offset("safe", mob_radius=3) >= 3
    assert max_offset("hunt", mob_radius=3) == 0


def test_hazard_penalty_steers_away_from_lava_beside_the_line():
    def solid(x, y, z):
        if (x, z) == (5, 1) and y == 0:
            return None
        return "minecraft:stone" if y == 0 else None

    cells = world(range(-1, 11), range(-3, 4), solid, 4)
    cells[(5, 0, 1)] = {
        "position": [5, 0, 1],
        "block": "minecraft:lava",
        "fluid": "minecraft:lava",
        "collision_boxes": [],
    }
    safe = search(cells, (0, 1, 0), (10, 1, 0), apply_profile("safe", TICKS))
    assert safe.status == "reached"
    assert (5, 1, 0) not in [tuple(n) for n in safe.route]


def test_threats_are_visible_hostiles_only():
    observation = {
        "visible_entities": [
            {"type": "minecraft:zombie", "position": [1.5, 64, 2.5]},
            {"type": "minecraft:cow", "position": [3, 64, 3]},
            {"type": "minecraft:item", "position": [0, 64, 0]},
        ]
    }
    assert threats(observation) == ((1.5, 64, 2.5),)


SITUATION = jev.Situation(
    task="goto",
    health=20.0,
    food=20,
    time_of_day=1000,
    threats=0,
    blocks=64,
    goal=(10, 64, 10),
    route_classes=("40 s, no edits", "12 s, dig 2"),
)


def test_jev_answer_is_used_only_when_valid_and_on_time():
    raw = {
        "contract": jev.CONTRACT,
        "goal": [10, 64, 10],
        "profile": "safe",
        "route_class": 1,
    }
    decision, error = jev.choose(SITUATION, raw)
    assert error is None and decision.source == "jev" and decision.route_class == 1
    late, error = jev.choose(SITUATION, raw, late=True)
    assert late.source == "fallback" and error == "late"
    for bad in (
        {**raw, "profile": "reckless"},
        {**raw, "goal": [10.5, 64, 10]},
        {**raw, "route_class": 2},
        {**raw, "decision": "teleport"},
        {**raw, "contract": "bounded-local-waypoint-selector-v1"},
    ):
        decision, error = jev.choose(SITUATION, bad)
        assert decision.source == "fallback" and error


def test_fallback_profile_follows_threat_night_health_and_stock():
    assert jev.fallback(SITUATION).profile == "race"
    night = jev.Situation(**{**SITUATION.__dict__, "time_of_day": 18000})
    assert jev.fallback(night).profile == "safe"
    hurt = jev.Situation(**{**SITUATION.__dict__, "health": 6.0})
    assert jev.fallback(hurt).profile == "safe"
    poor = jev.Situation(**{**SITUATION.__dict__, "blocks": 2})
    assert jev.fallback(poor).profile == "frugal"
    assert SITUATION.payload()["night"] is False


def test_safe_profile_carries_a_longer_threat_flight_and_race_the_default():
    assert load_profile("safe")["flee"] == {
        "trigger": 5,
        "creeper_trigger": 7,
        "steps": 6,
    }
    assert load_profile("race")["flee"] is None
