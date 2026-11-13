import math
from dataclasses import replace

import pytest

from mclab.terrain_placement import placement_target
from mclab.terrain_steps import StepExecutor, TerrainPolicy, prepare_step


def staircase_map(cliff=4):
    cells = {}
    for x in range(-2, 3):
        for y in range(-1, 8):
            for z in range(-1, cliff + 5):
                solid = y < 0 or abs(x) == 1 or (x == 0 and z >= cliff and y < 4)
                cells[x, y, z] = {
                    "position": [x, y, z],
                    "block": "minecraft:bedrock" if solid else "minecraft:air",
                    "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
                }
    return cells


def stock(count=8):
    return [{"slot": 3, "item": "minecraft:cobblestone", "count": count}]


def test_stack_limits_stock_protection_and_unknown_base_are_fail_closed():
    cells = staircase_map()
    # A previously confirmed two-high source, next to a three-block support stack.
    for y in (0, 1):
        cells[0, y, 2].update(
            block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
        )
    source, target = (0, 2, 2), (0, 3, 3)
    policy = TerrainPolicy(allow_break=False, max_support_stack=3)
    assert prepare_step(cells, source, target, policy, stock(3)).places == (
        (0, 0, 3),
        (0, 1, 3),
        (0, 2, 3),
    )
    for restricted in (
        replace(policy, max_support_stack=2),
        replace(policy, place_budget=2),
        replace(policy, reserve_blocks=1),
        replace(policy, protected_positions=((0, 1, 3),)),
    ):
        assert prepare_step(cells, source, target, restricted, stock(3)) is None
    del cells[0, -1, 3]
    assert prepare_step(cells, source, target, policy, stock(3)) is None


def test_low_support_stack_uses_sneak_edge_stance_for_visible_top_face():
    cells = staircase_map()
    placement = placement_target(cells, (0, 2, 2), (0, 0, 3))
    assert placement.face == "UP" and placement.support == (0, -1, 3)
    assert placement.approach == (0.5, 2, 3.15)


@pytest.mark.parametrize("limit", [0, 4, True, 2.0])
def test_invalid_stack_limits_rejected(limit):
    with pytest.raises(ValueError, match="Support stacks"):
        TerrainPolicy(max_support_stack=limit)


def test_stack_executor_confirms_each_layer_before_aiming_at_the_next():
    cells = staircase_map()
    for y in (0, 1):
        cells[0, y, 2].update(
            block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
        )
    policy = TerrainPolicy(allow_break=False, max_support_stack=3)
    plan = prepare_step(cells, (0, 2, 2), (0, 3, 3), policy, stock(3))
    with pytest.raises(ValueError, match="permissions/budgets"):
        StepExecutor(plan, replace(policy, max_support_stack=1))
    executor = StepExecutor(plan, policy)
    body = {
        "position": [0.5, 2, 3.12],
        "on_ground": True,
        "survival": {"eye_height": 1.27},
        "building_pickups": {},
        "inventory": stock(3),
        "yaw": 0,
        "pitch": 0,
    }
    observation = {"tick": 1, "body": body}
    for point in plan.places:
        if point[1] >= plan.source[1]:
            body["position"][2] = 2.55
        target = placement_target(cells, plan.source, point)
        body["pitch"] = math.degrees(
            math.atan2(
                body["position"][1] + 1.27 - target.aim[1],
                target.aim[2] - body["position"][2],
            )
        )
        body["ray"] = {"position": list(target.support), "face": "UP"}
        command = executor.decide(observation, cells)
        assert command.get("use_pulse") and not command.get("forward")
        assert executor.places_confirmed == plan.places.index(point)
        # Debit alone cannot advance the operation or turn its prediction live.
        body["inventory"][0]["count"] -= 1
        observation["tick"] += 1
        assert not executor.decide(observation, cells).get("use_pulse")
        assert executor.index == plan.places.index(point)
        cells[point].update(
            block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
        )
        observation["tick"] += 1
        executor.decide(observation, cells)
        observation["tick"] += 10
    executor.decide(observation, cells)
    assert executor.state == "ready" and executor.places_confirmed == 3


def test_stack_upper_layer_retreats_before_placing_into_leaning_body():
    cells = staircase_map()
    for p in ((0, 0, 1), (0, 0, 2)):
        cells[p].update(
            block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
        )
    policy = TerrainPolicy(allow_break=False, max_support_stack=3)
    plan = prepare_step(cells, (0, 1, 1), (0, 2, 2), policy, stock())
    body = {
        "position": [0.5, 1, 2.16],
        "on_ground": True,
        "survival": {"eye_height": 1.27},
        "inventory": stock(),
        "yaw": 180,
        "pitch": 78,
        "ray": {"position": [0, 0, 2], "face": "UP"},
    }
    command = StepExecutor(plan, policy).decide({"tick": 1, "body": body}, cells)
    assert command["sneak"] and command["forward"] > 0
    assert not command.get("use_pulse")
