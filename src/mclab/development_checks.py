"""Fail-closed authoritative checks for deliberately prohibited development actions."""

import math


def drop_refused(result, start_height):
    minimum = result.get("minimum_feet_y")
    edits = result.get("development_hill", {})
    return (
        result.get("status") == "failed"
        and result.get("reason") == "tick_limit"
        and not result.get("controller_error")
        and type(minimum) in (int, float)
        and math.isfinite(minimum)
        and minimum >= start_height - 0.1
        and type(result.get("damage_taken")) in (int, float)
        and result.get("damage_taken") == 0
        and isinstance(edits, dict)
        and type(edits.get("added")) is int
        and type(edits.get("removed")) is int
        and edits.get("added") == edits.get("removed") == 0
    )


def bridge_completed(result, variant, confirmed):
    start = 1282 + variant % 4
    expected = {(1280, 79, z) for z in range(start, start + 2 + variant % 3)}
    return _construction_completed(result, expected, confirmed)


def staircase_completed(result, variant, confirmed):
    cliff = 1284 + variant
    expected = {
        (1280, y, cliff - 4 + height)
        for height in (1, 2, 3)
        for y in range(80, 80 + height)
    }
    position = result.get("final_position")
    return (
        isinstance(position, list)
        and len(position) == 3
        and all(type(v) in (int, float) and math.isfinite(v) for v in position)
        and position[1] >= 83.9
        and _construction_completed(result, expected, confirmed)
    )


def _construction_completed(result, expected, confirmed):
    minimum = result.get("minimum_feet_y")
    edits = result.get("development_hill", {})
    changes = edits.get("changes", [])
    if not isinstance(changes, list) or any(not isinstance(c, dict) for c in changes):
        return False
    actual = set()
    for change in changes:
        position = change.get("position")
        if (
            not isinstance(position, list)
            or len(position) != 3
            or any(type(v) is not int for v in position)
            or change.get("before") != "Block{minecraft:air}"
            or change.get("after")
            not in {"Block{minecraft:cobblestone}", "Block{minecraft:dirt}"}
        ):
            return False
        actual.add(tuple(position))
    return (
        result.get("status") == "success"
        and not result.get("controller_error")
        and type(minimum) in (int, float)
        and math.isfinite(minimum)
        and minimum >= 79.9
        and type(result.get("damage_taken")) in (int, float)
        and result["damage_taken"] == 0
        and result.get("died") is False
        and result.get("disconnected") is False
        and len(changes) == len(actual) == len(expected)
        and actual == expected
        and edits.get("added")
        == result.get("kit_blocks_spent_adjusted")
        == confirmed
        == len(expected)
        and edits.get("removed") == 0
    )


def pillar_completed(result, variant, confirmed):
    height = 3 + variant % 2
    expected = {(1280, y, 1280 + variant // 2) for y in range(80, 80 + height - 1)}
    position = result.get("final_position")
    return (
        isinstance(position, list)
        and len(position) == 3
        and all(type(v) in (int, float) and math.isfinite(v) for v in position)
        and position[1] >= 80 + height - 0.1
        and _construction_completed(result, expected, confirmed)
    )


def gap_completed(result, confirmed):
    minimum, maximum = result.get("minimum_feet_y"), result.get("maximum_feet_y")
    edits = result.get("development_hill", {})
    return (
        result.get("status") == "success"
        and not result.get("controller_error")
        and type(minimum) in (int, float)
        and math.isfinite(minimum)
        and minimum >= 79.9
        and type(maximum) in (int, float)
        and math.isfinite(maximum)
        and maximum >= 80.9
        and type(result.get("damage_taken")) in (int, float)
        and result["damage_taken"] == 0
        and result.get("died") is False
        and result.get("disconnected") is False
        and type(confirmed) is int
        and confirmed == 1
        and result.get("kit_blocks_spent_adjusted") == 0
        and edits.get("added") == edits.get("removed") == 0
        and edits.get("changes") == []
    )


def water_completed(result, variant, recoveries):
    edits = result.get("development_hill", {})
    position = result.get("final_position")
    return (
        result.get("status") == "success"
        and not result.get("controller_error")
        and result.get("died") is False
        and result.get("disconnected") is False
        and type(result.get("water_ticks")) is int
        and result["water_ticks"] >= 3
        and type(result.get("damage_taken")) in (int, float)
        and result["damage_taken"] == 0
        and isinstance(position, list)
        and len(position) == 3
        and all(type(v) in (int, float) and math.isfinite(v) for v in position)
        and position[1] >= 79.9
        and result.get("kit_blocks_spent_adjusted") == 0
        and edits.get("added") == edits.get("removed") == 0
        and edits.get("changes") == []
        and (
            variant % 2 == 0
            or (
                edits.get("low_air_injected") is True
                and type(result.get("minimum_air")) is int
                and 0 < result["minimum_air"] <= 90
                and type(result.get("final_air")) is int
                and result["final_air"] >= 270
                and type(recoveries) is int
                and recoveries >= 1
            )
        )
    )
