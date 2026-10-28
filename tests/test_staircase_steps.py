import math
from dataclasses import replace

import pytest
from test_spatial_memory import identity

from mclab.terrain_placement import placement_target
from mclab.terrain_route import TerrainRouteSearch
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


def test_staircase_forecast_reserves_all_six_bottom_up_supports():
    cells = staircase_map()
    search = TerrainRouteSearch(
        cells,
        (0, 0, 0),
        (0, 4, 4),
        map_revision=1,
        policy=TerrainPolicy(allow_break=False, max_support_stack=3),
        inventory=stock(6),
    )
    for _ in range(50):
        result = search.advance(node_limit=1000, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "reached" and len(result.steps) == 4
    assert [s.places for s in result.steps] == [
        ((0, 0, 1),),
        ((0, 0, 2), (0, 1, 2)),
        ((0, 0, 3), (0, 1, 3), (0, 2, 3)),
        (),
    ]
    assert cells[0, 0, 3]["block"] == "minecraft:air"


@pytest.mark.parametrize("cliff", [5, 13])
def test_staircase_macro_reserves_approach_space_before_walking_to_the_cliff(cliff):
    cells = staircase_map(cliff)
    search = TerrainRouteSearch(
        cells,
        (0, 0, 0),
        (0, 4, cliff + 4),
        map_revision=1,
        policy=TerrainPolicy(allow_break=False, max_support_stack=3),
        inventory=stock(6),
    )
    for _ in range(500):
        result = search.advance(node_limit=2, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "segment" and len(result.steps) == 4
    assert result.route == tuple((0, max(0, z - (cliff - 4)), z) for z in range(5))
    assert all(p[2] >= cliff - 3 for s in result.steps for p in s.places)


def test_partial_scan_keeps_staircase_approach_until_upper_headroom_arrives():
    cells = staircase_map(5)
    # Native near refresh stops at body+4; the wider scan fills upper headroom.
    partial = {p: c for p, c in cells.items() if p[1] <= 4}
    for view, expected in ((partial, "incomplete"), (cells, "segment")):
        search = TerrainRouteSearch(
            view,
            (0, 0, 0),
            (0, 4, 9),
            map_revision=1,
            policy=TerrainPolicy(allow_break=False, max_support_stack=3),
            inventory=stock(6),
        )
        for _ in range(500):
            result = search.advance(node_limit=2, budget_ms=100)
            if result.status != "searching":
                break
        assert result.status == expected
        if expected == "incomplete":
            assert not result.steps and "known" in result.reason
        else:
            assert result.steps[1].places == ((0, 0, 2),)


def test_controller_hands_off_before_walking_lookahead_consumes_staircase_entry(
    tmp_path,
):
    from mclab.navigation_segmented import SegmentedController
    from mclab.spatial_memory import MapArchive, SpatialMemory

    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "staircase")
    controller = SegmentedController(
        (0, 4, 10),
        memory,
        radius=0.7,
        search_nodes=1000,
        search_ms=20,
        terrain_policy=TerrainPolicy(allow_break=False, max_support_stack=3),
    )
    observation = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(staircase_map(6).values()),
        "body": {
            "position": [0.5, 0, 0.5],
            "yaw": 0,
            "pitch": 0,
            "on_ground": True,
            "inventory": stock(6),
        },
    }
    try:
        for _ in range(100):
            command = controller.decide(observation)
            assert not command.get("forward")
            if controller.terrain_forecast:
                break
        assert controller.terrain_forecast[0].source == (0, 0, 0)
        assert controller.terrain_forecast[2].places == ((0, 0, 3),)
        assert controller.terrain_forecast[-1].places == ((0, 0, 4), (0, 1, 4))
    finally:
        controller.close()
        memory.close()


@pytest.mark.parametrize("cliff", range(4, 14))
def test_rolling_staircase_macro_reaches_each_exit_with_only_six_blocks(cliff):
    cells, inventory = staircase_map(cliff), stock(6)
    node, placed = (0, 0, 0), set()
    for _ in range(6):
        search = TerrainRouteSearch(
            cells,
            node,
            (0, 4, cliff + 4),
            map_revision=1,
            policy=TerrainPolicy(allow_break=False, max_support_stack=3),
            inventory=inventory,
            protected=placed,
        )
        for _ in range(500):
            result = search.advance(node_limit=2, budget_ms=100)
            if result.status != "searching":
                break
        assert result.status in {"segment", "reached"}
        assert len(result.steps) == 4 or result.status == "reached"
        for step in result.steps:
            assert not step.breaks
            for point in step.places:
                assert point not in placed
                placed.add(point)
                inventory[0]["count"] -= 1
                cells[point].update(
                    block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
                )
        node = result.steps[-1].target
        if result.status == "reached":
            break
    assert node == (0, 4, cliff + 4)
    assert placed == {
        (0, y, cliff - 4 + height) for height in (1, 2, 3) for y in range(height)
    }
    assert inventory[0]["count"] == 0


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


@pytest.mark.parametrize("stance", [5.4344476997376, 5.43, 5.44, 5.5])
def test_jump_support_sampling_matches_actual_body_width_at_step_edge(tmp_path, stance):
    from mclab.movement_policy import MovementPolicy
    from mclab.navigation_segmented import SegmentedController
    from mclab.spatial_memory import MapArchive, SpatialMemory

    cells = staircase_map(9)
    cells[0, 0, 6].update(
        block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
    )
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "stance")
    controller = SegmentedController(
        (0, 1, 6),
        memory,
        movement_policy=MovementPolicy(allow_shapes=True),
    )
    observation = {
        "sensor_profile": "local-visibility-v1",
        "tick": 1,
        "visible_blocks": list(cells.values()),
        "body": {"position": [0.5, 0, stance], "yaw": 0, "pitch": 0, "on_ground": True},
    }
    try:
        controller._admit(observation)
        command = controller._move(observation, [0.5, 1, 6.5])
        assert command.get("forward", 0) > 0 and command["jump"]
        cells[0, 3, 6].update(
            block="minecraft:bedrock", collision_boxes=[[0, 0, 0, 1, 1, 1]]
        )
        observation["tick"] = 2
        observation["visible_blocks"] = list(cells.values())
        controller._admit(observation)
        assert not controller._move(observation, [0.5, 1, 6.5]).get("forward")
    finally:
        controller.close()
        memory.close()
