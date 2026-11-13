"""Phase 6: D7 survival terms, named cost profiles, the Jev contract."""

import pytest

from mclab import jev_contract as jev
from mclab.profiles import PROFILES, apply_profile, load_profile, threats
from mclab.terrain_steps import TerrainPolicy

TICKS = TerrainPolicy(planner_tick_costs=True)


def test_every_named_profile_loads_and_race_changes_nothing():
    for name in PROFILES:
        assert isinstance(apply_profile(name, TICKS), TerrainPolicy)
    assert apply_profile("race", TICKS) == TICKS
    assert load_profile("escort")["goal"] == {"kind": "follow", "radius": 3}
    with pytest.raises(ValueError):
        load_profile("reckless")


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


def test_flee_is_off_in_every_shipped_profile():
    # Nov 1 night rerun: a 6-block flight raised damage 93.8 -> 118 (ROADMAP item 13).
    assert all(load_profile(name)["flee"] is None for name in PROFILES)
