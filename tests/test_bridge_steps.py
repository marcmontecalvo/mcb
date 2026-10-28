import math
from dataclasses import replace

from mclab.terrain_placement import placement_target
from mclab.terrain_route import TerrainRouteSearch
from mclab.terrain_steps import StepExecutor, TerrainPolicy, prepare_step


def bridge_map():
    cells = {}
    for x in range(-2, 3):
        for y in range(-3, 6):
            for z in range(-1, 7):
                solid = y < 0 and not (1 <= z <= 3 and y >= -2)
                cells[x, y, z] = {
                    "position": [x, y, z],
                    "block": "minecraft:stone" if solid else "minecraft:air",
                    "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
                }
                if abs(x) == 1 and y >= 0:
                    cells[x, y, z].update(
                        block="minecraft:bedrock", collision_boxes=[[0, 0, 0, 1, 1, 1]]
                    )
    return cells


def stock(count=4):
    return [{"slot": 3, "item": "minecraft:cobblestone", "count": count}]


def test_bridge_requires_explicit_permission_known_anchor_and_reserved_stock():
    cells = bridge_map()
    policy = TerrainPolicy(allow_bridge=True, allow_break=False)
    args = (cells, (0, 0, 0), (0, 0, 1))
    plan = prepare_step(*args, policy, stock())
    assert plan.places == ((0, -1, 1),)
    assert prepare_step(*args, replace(policy, allow_bridge=False), stock()) is None
    assert prepare_step(*args, replace(policy, reserve_blocks=4), stock()) is None
    assert prepare_step(*args, policy, stock(), protected=((0, -1, 1),)) is None
    del cells[0, -2, 1]
    assert prepare_step(*args, policy, stock()) is None


def test_four_move_bridge_forecast_preserves_actual_map_and_stock():
    cells = bridge_map()
    search = TerrainRouteSearch(
        cells,
        (0, 0, 0),
        (0, 0, 4),
        map_revision=1,
        policy=TerrainPolicy(allow_bridge=True, allow_break=False),
        inventory=stock(3),
    )
    for _ in range(50):
        result = search.advance(node_limit=1000, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "reached"
    assert len(result.steps) == 4
    assert sum(len(s.places) for s in result.steps) == 3
    assert all(cells[0, -1, z]["block"] == "minecraft:air" for z in range(1, 4))


def test_bridge_sneak_approach_side_face_and_observed_debit_confirmation():
    cells = bridge_map()
    policy = TerrainPolicy(allow_bridge=True, allow_break=False)
    plan = prepare_step(cells, (0, 0, 0), (0, 0, 1), policy, stock())
    target = placement_target(cells, plan.source, plan.places[0], allow_bridge=True)
    assert target.support == (0, -1, 0) and target.face == "SOUTH"
    executor = StepExecutor(plan, policy)
    body = {
        "position": [0.5, 0, 0.5],
        "yaw": 0,
        "pitch": 0,
        "on_ground": True,
        "inventory": stock(),
        "survival": {"eye_height": 1.62},
        "building_pickups": {},
    }
    obs = {"tick": 1, "body": body}
    command = executor.decide(obs, cells)
    assert command["sneak"] and command.get("forward", 0) > 0
    assert not command.get("use_pulse")
    body["position"][2] = 1.12
    body["yaw"] = 180
    body["survival"]["eye_height"] = 1.27
    body["pitch"] = math.degrees(
        math.atan2(1.27 - target.aim[1], abs(target.aim[2] - 1.12))
    )
    body["ray"] = {"position": [0, -1, 0], "face": "UP"}
    assert not executor.decide(obs, cells).get("use_pulse")
    body["ray"]["face"] = "SOUTH"
    assert executor.decide(obs, cells)["use_pulse"]
    body["inventory"][0]["count"] -= 1
    cells[0, -1, 1].update(
        block="minecraft:cobblestone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
    )
    obs["tick"] = 6
    executor.decide(obs, cells)
    obs["tick"] = 7
    executor.decide(obs, cells)
    assert executor.state == "ready" and executor.places_confirmed == 1


def test_approach_to_distant_bridge_does_not_build_a_horizon_filler():
    cells = bridge_map()
    # The gap starts three cells away, so a four-move horizon cannot cross it.
    for z in (1, 2):
        for y in (-2, -1):
            cells[0, y, z].update(
                block="minecraft:stone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
            )
    for z in (4, 5):
        for y in (-2, -1):
            cells[0, y, z].update(block="minecraft:air", collision_boxes=[])
    search = TerrainRouteSearch(
        cells,
        (0, 0, 0),
        (0, 0, 10),
        map_revision=1,
        policy=TerrainPolicy(allow_bridge=True, allow_break=False),
        inventory=stock(8),
    )
    for _ in range(50):
        result = search.advance(node_limit=1000, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "segment"
    assert result.route == ((0, 0, 0), (0, 0, 1), (0, 0, 2), (0, 0, 3), (0, 0, 4))
    assert [p for s in result.steps for p in s.places] == [(0, -1, 3), (0, -1, 4)]


def test_full_walking_horizon_competes_with_unnecessary_construction():
    cells = bridge_map()
    for z in range(1, 5):
        for y in (-2, -1):
            cells[0, y, z].update(
                block="minecraft:stone", collision_boxes=[[0, 0, 0, 1, 1, 1]]
            )
    search = TerrainRouteSearch(
        cells,
        (0, 0, 0),
        (0, 0, 10),
        map_revision=1,
        policy=TerrainPolicy(allow_bridge=True, allow_break=False),
        inventory=stock(8),
    )
    for _ in range(50):
        result = search.advance(node_limit=1000, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "segment" and len(result.steps) == 4
    assert result.route[-1] == (0, 0, 4)
    assert all(not s.places and not s.breaks for s in result.steps)


def test_disabled_bridge_does_not_construct_an_unnecessary_retreat_step():
    cells = bridge_map()
    # Stand at the edge with four known walkable retreat cells behind us.
    for z in range(-4, 0):
        for y in range(-3, 6):
            cells[0, y, z] = {
                "position": [0, y, z],
                "block": "minecraft:stone" if y < 0 else "minecraft:air",
                "collision_boxes": [[0, 0, 0, 1, 1, 1]] if y < 0 else [],
            }
    search = TerrainRouteSearch(
        cells,
        (0, 0, 0),
        (0, 0, 10),
        map_revision=1,
        policy=TerrainPolicy(allow_bridge=False, allow_break=False),
        inventory=stock(8),
    )
    for _ in range(50):
        result = search.advance(node_limit=1000, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "incomplete" and not result.steps
