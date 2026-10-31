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
        body["survival"].update(air=300, in_water=False, eye_in_water=False)
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


# --- review-note fixtures (open-world builder, not the 1-wide channel) -------
from mclab import collision_geometry  # noqa: E402
from mclab import water_movement as wm  # noqa: E402

AIR = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}
STONE = {
    "block": "minecraft:stone",
    "fluid": "minecraft:empty",
    "collision_boxes": [[0, 0, 0, 1, 1, 1]],
}


def water(level="0", block="minecraft:water"):
    return {
        "block": block,
        "fluid": "minecraft:water",
        "properties": {"level": level},
        "collision_boxes": [],
    }


def pool(size=7, depth=3, top=0):
    """Air above y=top-1, water down `depth` cells, stone floor below."""
    cells = {}
    for x in range(size):
        for z in range(size):
            for y in range(top - depth - 1, top + 12):
                cells[x, y, z] = (
                    STONE if y == top - depth - 1 else water() if y < top else AIR
                )
    return cells


def swimmer(y=-1.5, air=100, **survival):
    return {
        "position": [3.5, y, 3.5],
        "yaw": 0,
        "pitch": 0,
        "survival": {"in_water": True, "air": air, "max_air": 300, **survival},
    }


SWIM = MovementPolicy(allow_swim=True)


def test_constants_match_baritone_costs():
    assert wm.SWIM_TICKS == pytest.approx(9.0909, abs=1e-3)
    assert wm.WALK_ON_WATER_PENALTY == 3.0


def test_bubble_column_is_a_hazard_and_not_still_water():
    cell = water(block="minecraft:bubble_column")
    assert "minecraft:bubble_column" in collision_geometry.HAZARDS
    assert not wm.still_water(cell) and not wm.flowing_water(cell)
    assert wm.water_cost_multiplier(cell) is None
    cells = pool()
    cells[3, -1, 3] = cell
    assert not wm.surface_node(cells, (3, 0, 3))


def test_flowing_water_costs_more_than_still():
    assert wm.water_cost_multiplier(water()) == 1.0
    assert wm.water_cost_multiplier(water("3")) == 1.5
    assert wm.water_cost_multiplier(water("8")) == 1.5  # falling water
    assert wm.water_cost_multiplier(AIR) is None
    assert not wm.surface_node({**pool(), (3, -1, 3): water("2")}, (3, 0, 3))


def test_deep_water_surfaces_straight_up_beyond_old_2_4_limit():
    cells = pool(depth=8)
    body = swimmer(y=-7.5)
    assert wm.can_surface(cells, body)
    assert wm.air_recovery_command(cells, body, MovementPolicy()) == {"jump": True}


def test_overhang_recovery_routes_sideways_to_open_air():
    cells = pool()
    for x in (2, 3, 4):  # ice/stone roof directly over the swimmer
        for z in (2, 3, 4):
            cells[x, 0, z] = STONE
    body = swimmer()
    assert not wm.can_surface(cells, body)
    assert wm.air_recovery_command(cells, body, MovementPolicy()) == {}
    cmd = wm.air_recovery_command(cells, body, SWIM)
    assert cmd["jump"] and (cmd.get("forward") or abs(cmd["yaw_delta"]) > 8)


def test_overhang_beyond_remaining_air_returns_nothing():
    cells = pool(size=40)
    for x in range(40):
        for z in range(40):
            cells[x, 0, z] = STONE
    cells[39, 0, 3] = AIR
    body = {**swimmer(air=20), "position": [3.5, -1.5, 3.5]}
    assert wm.air_recovery_command(cells, body, SWIM) == {}


@pytest.mark.parametrize("roof", ["minecraft:ice", "minecraft:lily_pad"])
def test_ice_or_lily_pad_ceiling_blocks_straight_surface(roof):
    cells = pool()
    box = [0, 0, 0, 1, 0.015625, 1] if roof.endswith("pad") else [0, 0, 0, 1, 1, 1]
    cells[3, 0, 3] = {**STONE, "block": roof, "collision_boxes": [box]}
    assert not wm.can_surface(cells, swimmer())
    assert wm.air_recovery_command(cells, swimmer(), SWIM)  # sidestep exists


def test_recovery_ends_when_head_is_out_of_water():
    cells = pool()
    out = swimmer(y=-0.5, eye_in_water=False)
    assert wm.head_out_of_water(out["survival"])
    assert wm.air_recovery_command(cells, out, SWIM) == {}
    assert not wm.head_out_of_water(swimmer(eye_in_water=True)["survival"])
    # fallback: no eye flag -> in_water decides
    assert not wm.head_out_of_water({"in_water": True})
    assert wm.head_out_of_water({"in_water": False})
    # eye flag wins over in_water
    assert wm.head_out_of_water({"in_water": True, "eye_in_water": False})


def test_lava_cell_is_never_routed_through():
    cells = pool()
    for x in (2, 3, 4):
        for z in (2, 3, 4):
            cells[x, 0, z] = STONE
    for z in range(7):
        cells[2, -1, z] = {**AIR, "block": "minecraft:lava", "fluid": "minecraft:lava"}
        cells[4, -1, z] = {**AIR, "block": "minecraft:lava", "fluid": "minecraft:lava"}
    path = wm._air_route(cells, swimmer(), 30)
    assert path is None or all(
        cells[(x, y, z)].get("fluid") != "minecraft:lava" for x, y, z in path
    )


def test_air_recovery_reflex_when_swimming_disabled_only_surfaces_or_steps():
    cells = pool()
    policy = MovementPolicy()
    assert wm.air_recovery_command(cells, swimmer(), policy) == {"jump": True}
    assert wm.swim_command(cells, swimmer(), [3.5, 0, 5.5], policy) == {}
    # knockback into water beside a roof: adjacent open column only
    for x in range(7):
        for z in range(7):
            if (x, z) != (4, 3):
                cells[x, 0, z] = STONE
    cmd = wm.air_recovery_command(cells, swimmer(), policy)
    assert cmd.get("jump") and (cmd.get("forward") or "yaw_delta" in cmd)
    cells[4, 0, 3] = STONE  # no adjacent opening: refuse to route
    assert wm.air_recovery_command(cells, swimmer(), policy) == {}


def bank(height=1):
    cells = pool()
    for x in range(7, 10):
        for z in range(7):
            for y in range(-4, 12):
                cells[x, y, z] = STONE if y < height else AIR
    return cells


def test_raised_bank_exit_up_one_block():
    cells = bank(1)
    cells[6, -1, 3] = water()  # keep pool column continuous to x=6
    assert wm.water_exit_up(cells, (6, 0, 3), (7, 1, 3))
    assert not wm.water_exit_up(cells, (6, 0, 3), (7, 2, 3))  # two high
    assert not wm.water_exit_up(cells, (6, 0, 3), (7, 0, 3))  # flush is water_edge
    cells[7, 2, 3] = STONE  # no headroom
    assert not wm.water_exit_up(cells, (6, 0, 3), (7, 1, 3))


def test_land_to_water_drop_needs_known_floor_and_still_water():
    cells = bank(5)
    assert wm.water_landing(cells, (7, 5, 3), (6, 0, 3), 10)
    assert not wm.water_landing(cells, (7, 5, 3), (6, 0, 3), 4)  # height cap
    del cells[6, -4, 3]  # unknown floor
    assert not wm.water_landing(cells, (7, 5, 3), (6, 0, 3), 10)
    cells = bank(5)
    cells[6, -4, 3] = {**AIR, "block": "minecraft:lava", "fluid": "minecraft:lava"}
    assert not wm.water_landing(cells, (7, 5, 3), (6, 0, 3), 10)
    cells = bank(5)
    cells[6, -2, 3] = water("1")
    assert not wm.water_landing(cells, (7, 5, 3), (6, 0, 3), 10)


def test_air_recovery_surfaces_without_allow_swim_and_ends_with_head_out(tmp_path):
    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "water")
    controller = SegmentedController(
        (0, 0, 9),
        memory,
        search_nodes=1000,
        search_ms=20,
        movement_policy=MovementPolicy(),
    )
    body = {
        "position": [0.5, -1.5, 3.5],
        "yaw": 0,
        "pitch": 0,
        "on_ground": False,
        "survival": {"in_water": True, "eye_in_water": True, "air": 90, "max_air": 300},
    }
    obs = {
        "tick": 1,
        "sensor_profile": "local-visibility-v1",
        "visible_blocks": list(water_map().values()),
        "body": body,
    }
    try:
        assert controller.decide(obs)["jump"]
        assert controller.air_recovering and controller.mode == "air_recovery"
        body["survival"].update(eye_in_water=False, air=120)  # not yet 90% air
        obs["tick"] = 2
        controller.decide(obs)
        assert not controller.air_recovering
        assert controller.air_recoveries_completed == 1
    finally:
        controller.close()


def test_previous_route_is_favored_only_with_baritone_search(tmp_path):
    from mclab.terrain_steps import TerrainPolicy

    memory = SpatialMemory(MapArchive(tmp_path / "map.db", identity()), "water")
    for flag in (False, True):
        controller = SegmentedController(
            (0, 0, 9),
            memory,
            terrain_policy=TerrainPolicy(baritone_search=flag),
        )
        controller.previous_route = ((0, 0, 1), (0, 0, 2))
        try:
            search = controller._new_search(water_map(), (0, 0, 0), 1)
            assert search.favored == (
                frozenset({(0, 0, 1), (0, 0, 2)}) if flag else frozenset()
            )
        finally:
            controller.close()
