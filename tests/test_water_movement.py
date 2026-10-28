from dataclasses import replace

import pytest
from test_spatial_memory import identity

from mclab.movement_policy import MovementPolicy
from mclab.navigation_segmented import SegmentedController
from mclab.route_search import RouteSearch
from mclab.spatial_memory import MapArchive, SpatialMemory
from mclab.water_movement import surface_height, surface_node, swim_command


def water_map(width=4, start=2):
    cells = {}
    for x in range(-2, 3):
        for y in range(-3, 7):
            for z in range(-2, start + width + 6):
                wet = x == 0 and start <= z < start + width and y in (-2, -1)
                solid = abs(x) == 1 or (y < 0 and not wet)
                cells[x, y, z] = {
                    "position": [x, y, z],
                    "block": "minecraft:water"
                    if wet
                    else "minecraft:bedrock"
                    if solid
                    else "minecraft:air",
                    "fluid": "minecraft:water" if wet else "minecraft:empty",
                    "properties": {"level": "0"} if wet else {},
                    "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
                }
    return cells


def test_surface_nodes_keep_water_level_separate_from_actual_body_depth():
    cells = water_map()
    assert surface_node(cells, (0, 0, 3))
    assert surface_height(cells, [0.5, -2, 3.5]) == 0
    assert surface_height(cells, [0.5, -0.4, 3.5]) == 0
    assert not surface_node(cells, (0, -1, 3))
    for changed in ({"fluid": "minecraft:lava"}, {"properties": {"level": "1"}}):
        altered = dict(cells)
        altered[0, -1, 3] = {**cells[0, -1, 3], **changed}
        assert not surface_node(altered, (0, 0, 3))
    del cells[0, 1, 3]
    assert not surface_node(cells, (0, 0, 3))


@pytest.mark.parametrize("enabled", [False, True])
def test_water_route_requires_explicit_permission_and_known_air_above_pool(enabled):
    cells = water_map()
    search = RouteSearch(
        cells,
        (0, 0, 0),
        (0, 0, 9),
        map_revision=1,
        movement_policy=MovementPolicy(allow_swim=enabled),
    )
    for _ in range(100):
        result = search.advance(node_limit=100, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == ("reached" if enabled else "unreachable")
    if enabled:
        assert (0, 0, 3) in result.route


def test_swim_motor_checks_water_and_land_then_holds_jump_to_surface():
    cells = water_map()
    body = {
        "position": [0.5, -0.4, 3.5],
        "yaw": 0,
        "pitch": 0,
        "survival": {"in_water": True, "air": 300, "max_air": 300},
    }
    command = swim_command(cells, body, [0.5, 0, 4.5], MovementPolicy(allow_swim=True))
    assert command["jump"] and command["forward"] > 0 and not command.get("sprint")
    assert swim_command(cells, body, [0.5, 0, 4.5], MovementPolicy()) == {}
    cells[0, -1, 4]["fluid"] = "minecraft:lava"
    assert not swim_command(
        cells, body, [0.5, 0, 4.5], MovementPolicy(allow_swim=True)
    ).get("forward")


def test_controller_air_recovery_waits_for_breath_before_route_progress(tmp_path):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "water")
    controller = SegmentedController(
        (0, 0, 9),
        memory,
        search_nodes=1000,
        search_ms=20,
        movement_policy=MovementPolicy(allow_swim=True),
    )
    body = {
        "position": [0.5, -1.5, 3.5],
        "yaw": 0,
        "pitch": 0,
        "on_ground": False,
        "survival": {"in_water": True, "air": 90, "max_air": 300},
    }
    obs = {
        "tick": 1,
        "sensor_profile": "local-visibility-v1",
        "visible_blocks": list(water_map().values()),
        "body": body,
    }
    try:
        command = controller.decide(obs)
        assert command["jump"] and not command.get("forward")
        assert controller.air_recovering
        body["position"][1] = -0.4
        body["survival"]["air"] = 200
        obs["tick"] = 2
        assert not controller.decide(obs).get("forward")
        body["survival"]["air"] = 300
        obs["tick"] = 3
        controller.decide(obs)
        assert (
            not controller.air_recovering and controller.air_recoveries_completed == 1
        )
        assert controller.route[0] == (0, 0, 3)
        controller.cancel()
        assert not controller.air_recovering
    finally:
        controller.close()
        memory.close()


def test_water_profile_roundtrip_and_strict_permission():
    assert MovementPolicy.from_payload(
        MovementPolicy(allow_swim=True).payload()
    ).allow_swim
    with pytest.raises(ValueError):
        replace(MovementPolicy(), allow_swim=1)


@pytest.mark.parametrize("field", ["air", "max_air", "in_water"])
def test_missing_survival_telemetry_cannot_move_through_water(field):
    body = {
        "position": [0.5, -0.4, 3.5],
        "yaw": 0,
        "pitch": 0,
        "survival": {"in_water": True, "air": 300, "max_air": 300},
    }
    body["survival"].pop(field)
    assert (
        swim_command(water_map(), body, [0.5, 0, 4.5], MovementPolicy(allow_swim=True))
        == {}
    )


@pytest.mark.parametrize("variant", range(10))
def test_water_fixture_distance_and_width_are_connected(variant):
    start, width = 2 + variant // 2, 3 + variant % 2
    cells = water_map(width, start)
    search = RouteSearch(
        cells,
        (0, 0, 0),
        (0, 0, start + width + 3),
        map_revision=1,
        movement_policy=MovementPolicy(allow_swim=True, allow_shapes=True),
    )
    for _ in range(100):
        result = search.advance(node_limit=100, budget_ms=100)
        if result.status != "searching":
            break
    assert result.status == "reached"
    assert all((0, 0, z) in result.route for z in range(start, start + width))
