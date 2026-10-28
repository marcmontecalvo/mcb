import pytest

from mclab.development_checks import (
    bridge_completed,
    drop_refused,
    gap_completed,
    pillar_completed,
    staircase_completed,
    water_completed,
)


def result():
    return {
        "status": "failed",
        "reason": "tick_limit",
        "minimum_feet_y": 83,
        "damage_taken": 0,
        "development_hill": {"added": 0, "removed": 0},
    }


def test_refusal_requires_authoritative_no_fall_no_damage_no_edit_evidence():
    assert drop_refused(result(), 83)
    for changes in (
        {"minimum_feet_y": 82.8},
        {"minimum_feet_y": float("nan")},
        {"minimum_feet_y": True},
        {"damage_taken": 1},
        {"damage_taken": False},
        {"status": "success"},
        {"status": "invalid"},
        {"reason": "controller_watchdog"},
        {"controller_error": "stale"},
        {"development_hill": {"added": 1, "removed": 0}},
    ):
        assert not drop_refused({**result(), **changes}, 83)


@pytest.mark.parametrize(
    "missing", ["minimum_feet_y", "damage_taken", "development_hill"]
)
def test_old_or_incomplete_telemetry_cannot_prove_safe_refusal(missing):
    data = result()
    data.pop(missing)
    assert not drop_refused(data, 83)


def test_arrival_via_falling_or_extra_building_cannot_count_as_a_bridge():
    data = {
        "status": "success",
        "minimum_feet_y": 80,
        "damage_taken": 0,
        "died": False,
        "disconnected": False,
        "kit_blocks_spent_adjusted": 2,
        "development_hill": {
            "added": 2,
            "removed": 0,
            "changes": [
                {
                    "position": [1280, 79, z],
                    "before": "Block{minecraft:air}",
                    "after": "Block{minecraft:cobblestone}",
                }
                for z in (1282, 1283)
            ],
        },
    }
    assert bridge_completed(data, 0, 2)
    assert not bridge_completed({**data, "minimum_feet_y": 78}, 0, 2)
    assert not bridge_completed({**data, "kit_blocks_spent_adjusted": 3}, 0, 2)
    assert not bridge_completed(data, 0, 1)
    data["development_hill"]["changes"][0]["position"][1] = 78
    assert not bridge_completed(data, 0, 2)


def test_staircase_requires_exact_six_blocks_stock_and_real_ascent():
    data = {
        "status": "success",
        "minimum_feet_y": 80,
        "damage_taken": 0,
        "died": False,
        "disconnected": False,
        "final_position": [1280.5, 84, 1288.1],
        "kit_blocks_spent_adjusted": 6,
        "development_hill": {
            "added": 6,
            "removed": 0,
            "changes": [
                {
                    "position": [1280, y, 1280 + height],
                    "before": "Block{minecraft:air}",
                    "after": "Block{minecraft:cobblestone}",
                }
                for height in (1, 2, 3)
                for y in range(80, 80 + height)
            ],
        },
    }
    assert staircase_completed(data, 0, 6)
    assert not staircase_completed(data, 1, 6)
    assert not staircase_completed(data, 0, 5)
    assert not staircase_completed({**data, "final_position": [1280, 80, 1288]}, 0, 6)
    assert not staircase_completed({**data, "kit_blocks_spent_adjusted": 5}, 0, 6)
    data["development_hill"]["changes"].pop()
    assert not staircase_completed(data, 0, 6)


@pytest.mark.parametrize("variant", range(10))
def test_pillar_audit_requires_underfoot_column_and_real_exit(variant):
    height = 3 + variant % 2
    count = height - 1
    data = {
        "status": "success",
        "minimum_feet_y": 80,
        "damage_taken": 0,
        "died": False,
        "disconnected": False,
        "final_position": [1280.5, 80 + height, 1285 + variant // 2],
        "kit_blocks_spent_adjusted": count,
        "development_hill": {
            "added": count,
            "removed": 0,
            "changes": [
                {
                    "position": [1280, y, 1280 + variant // 2],
                    "before": "Block{minecraft:air}",
                    "after": "Block{minecraft:cobblestone}",
                }
                for y in range(80, 80 + count)
            ],
        },
    }
    assert pillar_completed(data, variant, count)
    assert not pillar_completed(data, variant, count - 1)
    assert not pillar_completed(
        {**data, "final_position": [1280, 80, 1285]}, variant, count
    )
    data["development_hill"]["changes"][0]["position"][2] += 1
    assert not pillar_completed(data, variant, count)


def test_gap_arrival_requires_real_jump_no_fall_and_no_edits():
    data = {
        "status": "success",
        "minimum_feet_y": 80,
        "maximum_feet_y": 81.25,
        "damage_taken": 0,
        "died": False,
        "disconnected": False,
        "kit_blocks_spent_adjusted": 0,
        "development_hill": {"added": 0, "removed": 0, "changes": []},
    }
    assert gap_completed(data, 1)
    assert not gap_completed(data, 0)
    for changed in (
        {"maximum_feet_y": 80},
        {"minimum_feet_y": 79.8},
        {"damage_taken": 1},
        {"kit_blocks_spent_adjusted": 1},
        {"maximum_feet_y": float("nan")},
        {"status": "invalid"},
    ):
        assert not gap_completed({**data, **changed}, 1)


def test_water_audit_requires_real_contact_and_stressed_air_recovery():
    data = {
        "status": "success",
        "damage_taken": 0,
        "died": False,
        "disconnected": False,
        "water_ticks": 30,
        "final_position": [1280.5, 80, 1290.5],
        "kit_blocks_spent_adjusted": 0,
        "minimum_air": 90,
        "final_air": 300,
        "development_hill": {
            "added": 0,
            "removed": 0,
            "changes": [],
            "low_air_injected": True,
        },
    }
    assert water_completed(data, 0, 0)
    assert water_completed(data, 1, 1)
    assert not water_completed(data, 1, 0)
    for changed in (
        {"minimum_air": 300},
        {"final_air": 100},
        {"water_ticks": 0},
        {"damage_taken": 1},
        {"kit_blocks_spent_adjusted": 1},
        {"final_position": [1280.5, 79, 1290.5]},
    ):
        assert not water_completed({**data, **changed}, 1, 1)
