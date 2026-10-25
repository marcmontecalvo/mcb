import copy
import math

import pytest

from mclab.navigation import NavigationController, path_to, walkable_nodes


def cell(x, y, z, block="minecraft:air", tick=100):
    return {
        "position": [x, y, z],
        "block": block,
        "observed_tick": tick,
        "collision_boxes": [] if block == "minecraft:air" else [[0, 0, 0, 1, 1, 1]],
        "fluid": "minecraft:empty",
    }


def world(radius=7):
    return {
        (x, y, z): cell(x, y, z, "minecraft:stone" if y == 79 else "minecraft:air")
        for x in range(-radius, radius + 1)
        for z in range(-radius, radius + 1)
        for y in range(79, 85)
    }


def observation(cells=None, **body_changes):
    body = {
        "position": [0.5, 80, 0.5],
        "yaw": 0,
        "pitch": 0,
        "ray": {},
        "on_ground": True,
        "inventory": [],
    }
    body.update(body_changes)
    return {
        "sensor_profile": "local-visibility-v1",
        "tick": 100,
        "body": body,
        "visible_blocks": list((world() if cells is None else cells).values()),
        "visible_entities": [],
    }


def test_known_grid_routes_around_two_block_wall_without_climbing_it():
    cells = world()
    for x in (-1, 0, 1):
        for z in (3, 4):
            for y in (80, 81):
                cells[x, y, z] = cell(x, y, z, "minecraft:stone")
    route = path_to(cells, (0, 80, 0), {(0, 80, 6)})
    assert route and route[-1] == (0, 80, 6)
    assert any(abs(n[0]) >= 2 for n in route)
    assert all(n[1] == 80 for n in route)


def test_full_width_crest_requires_one_block_up_and_down_edges():
    cells = world()
    for x in range(-7, 8):
        for z in (3, 4):
            cells[x, 80, z] = cell(x, 80, z, "minecraft:stone")
    route = path_to(cells, (0, 80, 0), {(0, 80, 6)})
    assert route and any(n[1] == 81 for n in route)
    assert route[-1][1] == 80
    assert all(abs(a[1] - b[1]) <= 1 for a, b in zip(route, route[1:], strict=False))


def test_unknown_and_low_ceiling_do_not_supply_walkable_or_jump_nodes():
    cells = world()
    del cells[0, 79, 1]
    assert (0, 80, 1) not in walkable_nodes(cells)
    for x in range(-7, 8):
        cells[x, 80, 3] = cell(x, 80, 3, "minecraft:stone")
        cells[x, 83, 2] = cell(x, 83, 2, "minecraft:stone")
        cells[x, 83, 3] = cell(x, 83, 3, "minecraft:stone")
    assert not path_to(cells, (0, 80, 0), {(0, 80, 6)})


def test_target_is_discovered_only_from_visible_cells_and_state_is_per_episode():
    controller = NavigationController()
    controller.decide({**observation(), "evaluator_target": [12, 80, 0]})
    assert controller.target is None
    cells = world()
    cells[0, 80, 6] = cell(0, 80, 6, "minecraft:oak_log")
    controller.decide(observation(cells))
    assert controller.target == (0, 80, 6)
    assert NavigationController().target is None
    assert not NavigationController().cells


def test_mining_requires_current_log_ray_and_fully_aligned_aim():
    cells = world()
    cells[0, 80, 3] = cell(0, 80, 3, "minecraft:oak_log")
    pitch = math.degrees(math.atan2(1.12, 3))
    controller = NavigationController()
    o = observation(
        cells, pitch=pitch, ray={"block": "minecraft:oak_log", "position": [0, 80, 3]}
    )
    assert controller.decide(o)["mine"]
    o["body"]["ray"] = {}
    assert not controller.decide(o)["mine"]
    o["body"]["ray"] = {"block": "minecraft:oak_log", "position": [0, 80, 3]}
    o["body"]["yaw"] = 90
    assert not controller.decide(o)["mine"]


def test_observed_air_after_mining_switches_to_pickup_without_evaluator_state():
    cells = world()
    cells[0, 80, 3] = cell(0, 80, 3, "minecraft:oak_log")
    controller = NavigationController()
    controller.decide(
        observation(
            cells,
            pitch=math.degrees(math.atan2(1.12, 3)),
            ray={"block": "minecraft:oak_log", "position": [0, 80, 3]},
        )
    )
    cells[0, 80, 3] = cell(0, 80, 3)
    o = observation(cells)
    o["visible_entities"] = [
        {
            "uuid": "drop-visible",
            "item": "minecraft:oak_log",
            "position": [0.5, 80.1, 3.5],
        }
    ]
    controller.decide(o)
    assert controller.broken and controller.mode == "collecting"
    assert controller.drop == [0.5, 80.1, 3.5]
    o["body"]["inventory"] = [{"item": "minecraft:oak_log", "count": 1}]
    assert controller.decide(o) == {}


def test_stale_support_cannot_move_even_when_old_memory_can_plan():
    cells = world()
    controller = NavigationController()
    fresh = observation(cells)
    controller._observe(fresh)
    waypoint = [0.5, 80, 1.5]
    assert controller._move(fresh, waypoint)["forward"] > 0
    stale = copy.deepcopy(fresh)
    stale["tick"] = 106
    assert not controller._move(stale, waypoint).get("forward")


def test_jump_command_requires_fresh_landing_and_headroom():
    cells = world()
    cells[0, 80, 1] = cell(0, 80, 1, "minecraft:stone")
    controller = NavigationController()
    o = observation(cells)
    controller._observe(o)
    result = controller._move(o, [0.5, 81, 1.5])
    assert result["jump"] and result["forward"] > 0
    del controller.cells[0, 83, 1]
    assert not controller._move(o, [0.5, 81, 1.5]).get("forward")


def test_unsupported_sensor_rejected_before_map_mutation():
    controller = NavigationController()
    with pytest.raises(ValueError, match="sensor"):
        controller.decide({"sensor_profile": "camera"})
    assert not controller.cells
