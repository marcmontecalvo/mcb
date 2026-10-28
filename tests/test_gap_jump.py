from dataclasses import replace

import pytest

from mclab.gap_jump import GapJumpExecutor, prepare_gap
from mclab.movement_policy import MovementPolicy
from mclab.route_search import RouteSearch


def gap_map(width=1, start=2):
    cells = {}
    for x in range(-2, 3):
        for y in range(-3, 6):
            for z in range(-2, 12):
                solid = (y < 0 and not (start <= z < start + width and y >= -2)) or abs(
                    x
                ) == 1
                cells[x, y, z] = {
                    "position": [x, y, z],
                    "block": "minecraft:bedrock" if solid else "minecraft:air",
                    "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
                }
    return cells


@pytest.mark.parametrize("width", [1, 2])
def test_known_gap_edge_requires_permission_runway_clearance_and_landing(width):
    cells = gap_map(width)
    policy = MovementPolicy(max_gap=width, allow_sprint=True)
    source, target = (0, 0, 1), (0, 0, 2 + width)
    plan = prepare_gap(cells, source, target, policy)
    assert plan.width == width and plan.requires_sprint == (width == 2)
    assert prepare_gap(cells, source, target, replace(policy, max_gap=0)) is None
    if width == 2:
        assert (
            prepare_gap(cells, source, target, replace(policy, allow_sprint=False))
            is None
        )
    for point in ((0, -1, 0), (0, 3, 2), (0, -1, 2 + width), (0, -1, 4 + width)):
        altered = dict(cells)
        del altered[point]
        assert prepare_gap(altered, source, target, policy) is None


@pytest.mark.parametrize("width", [1, 2])
def test_astar_routes_across_only_checked_gap_edges(width):
    cells = gap_map(width)
    search = RouteSearch(
        cells,
        (0, 0, 0),
        (0, 0, 8),
        map_revision=1,
        movement_policy=MovementPolicy(max_gap=width, allow_sprint=True),
    )
    for _ in range(100):
        result = search.advance(node_limit=10, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "reached"
    assert ((0, 0, 1), (0, 0, width + 2)) in tuple(
        zip(result.route, result.route[1:], strict=False)
    )


def test_executor_revalidates_before_takeoff_and_never_places_blocks():
    cells, policy = gap_map(2), MovementPolicy(max_gap=2, allow_sprint=True)
    plan = prepare_gap(cells, (0, 0, 1), (0, 0, 4), policy)
    executor = GapJumpExecutor(plan, policy)
    body = {
        "position": [0.5, 0, 0.5],
        "yaw": 0,
        "pitch": 0,
        "food": 20,
        "on_ground": True,
        "velocity": [0, 0, 0],
    }
    obs = {"tick": 1, "body": body}
    executor.decide(obs, cells)
    body.update(position=[0.5, 0, 1.07], velocity=[0, 0, 0.245])
    obs["tick"] = 7
    command = executor.decide(obs, cells)
    assert command["jump"] and command["sprint"] and command["forward"] > 0
    assert not command.get("use_pulse") and not command.get("mine")
    body.update(position=[0.5, 0.8, 2.1], on_ground=False)
    obs["tick"] = 10
    assert executor.decide(obs, cells)["forward"] > 0
    body.update(position=[0.5, 0, 4.4], on_ground=True)
    obs["tick"] = 19
    executor.decide(obs, cells)
    assert executor.state == "ready"


def test_missing_landing_or_low_food_never_launches():
    cells, policy = gap_map(2), MovementPolicy(max_gap=2, allow_sprint=True)
    plan = prepare_gap(cells, (0, 0, 1), (0, 0, 4), policy)
    for low_food in (False, True):
        body = {
            "position": [0.5, 0, 0.5],
            "yaw": 0,
            "pitch": 0,
            "food": 6 if low_food else 20,
            "on_ground": True,
            "velocity": [0, 0, 0],
        }
        fresh = dict(cells)
        if not low_food:
            del fresh[0, -1, 4]
        executor = GapJumpExecutor(plan, policy)
        command = executor.decide({"tick": 1, "body": body}, fresh)
        assert not command.get("jump") and not command.get("forward")
        assert executor.state == "failed"


@pytest.mark.parametrize("value", [-1, 3, True, 1.0])
def test_invalid_gap_permissions_rejected(value):
    with pytest.raises(ValueError):
        MovementPolicy(max_gap=value)


def test_controller_owns_gap_flight_and_cancel_clears_commitment(tmp_path):
    from test_spatial_memory import identity

    from mclab.navigation_segmented import SegmentedController
    from mclab.spatial_memory import MapArchive, SpatialMemory

    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "gap")
    controller = SegmentedController(
        (0, 0, 8),
        memory,
        search_nodes=1000,
        search_ms=20,
        movement_policy=MovementPolicy(max_gap=2, allow_sprint=True),
    )
    obs = {
        "tick": 1,
        "sensor_profile": "local-visibility-v1",
        "visible_blocks": list(gap_map(2).values()),
        "body": {
            "position": [0.5, 0, 1.2],
            "yaw": 0,
            "pitch": 0,
            "on_ground": True,
            "velocity": [0, 0, 0],
            "food": 20,
        },
    }
    try:
        for _ in range(20):
            controller.decide(obs)
            if controller.gap_jump:
                break
        assert controller.gap_jump is not None
        assert controller.gap_jump.plan.target == (0, 0, 4)
        controller.cancel()
        assert controller.gap_jump is None and controller.decide(obs) == {}
    finally:
        controller.close()
        memory.close()


def test_fall_or_changed_landing_finishes_as_failure():
    cells, policy = gap_map(), MovementPolicy(max_gap=1)
    plan = prepare_gap(cells, (0, 0, 1), (0, 0, 3), policy)
    executor = GapJumpExecutor(plan, policy)
    executor.phase, executor.launch_tick, executor.air_seen = "flight", 1, True
    body = {
        "position": [0.5, -0.2, 2.1],
        "yaw": 0,
        "pitch": 0,
        "on_ground": False,
        "velocity": [0, -0.1, 0.2],
    }
    assert executor.decide({"tick": 10, "body": body}, cells) == {}
    assert executor.state == "failed"


def test_final_runway_positioning_slows_before_three_tick_overshoot():
    cells, policy = gap_map(), MovementPolicy(max_gap=1)
    plan = prepare_gap(cells, (0, 0, 1), (0, 0, 3), policy)
    body = {
        "position": [0.5, 0, 0.33347976842],
        "yaw": 0,
        "pitch": 0,
        "on_ground": True,
        "velocity": [0, 0, 0.01],
    }
    command = GapJumpExecutor(plan, policy).decide({"tick": 1, "body": body}, cells)
    assert 0 < command["forward"] <= 0.1 and not command.get("jump")


@pytest.mark.parametrize("hazard", ["minecraft:magma_block", "minecraft:cactus"])
def test_hazardous_landing_is_not_a_gap_exit(hazard):
    cells = gap_map()
    cells[0, -1, 3]["block"] = hazard
    assert prepare_gap(cells, (0, 0, 1), (0, 0, 3), MovementPolicy(max_gap=1)) is None


@pytest.mark.parametrize("turns", range(4))
def test_gap_geometry_supports_all_cardinal_directions(turns):
    def rotate(point):
        x, y, z = point
        for _ in range(turns):
            x, z = z, -x
        return x, y, z

    cells = {
        rotate(p): {**c, "position": list(rotate(p))} for p, c in gap_map(2).items()
    }
    plan = prepare_gap(
        cells,
        rotate((0, 0, 1)),
        rotate((0, 0, 4)),
        MovementPolicy(max_gap=2, allow_sprint=True),
    )
    assert plan.width == 2 and plan.runway == rotate((0, 0, 0))
