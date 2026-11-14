from dataclasses import replace

import pytest

from mclab.movement_policy import MovementPolicy
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


def test_surface_height_follows_a_deep_column_and_stops_at_a_ceiling():
    # Cohort 18 R5 rep 2: on a lake bed 10 below the surface the 8-cell scan found
    # nothing, so the start was "not known standable" until the stall cutoff.
    water = {"block": "minecraft:water", "fluid": "minecraft:water"}
    water |= {"properties": {"level": "0"}, "collision_boxes": []}
    air = {"block": "minecraft:air", "fluid": "minecraft:empty"}
    air |= {"properties": {}, "collision_boxes": []}
    stone = {"block": "minecraft:stone", "fluid": "minecraft:empty"}
    stone |= {"properties": {}, "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
    deep = {(0, y, 0): water if y < 12 else air for y in range(0, 20)}
    assert surface_height(deep, [0.5, 0, 0.5]) == 12
    capped = dict(deep) | {(0, 5, 0): stone}  # a pocket under rock: no surface
    assert surface_height(capped, [0.5, 0, 0.5]) is None


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


def test_minecraft_flowing_water_fluid_is_wadeable_and_passable():
    flowing = {
        "block": "minecraft:water",
        "fluid": "minecraft:flowing_water",
        "collision_boxes": [],
        "properties": {"level": "1"},
    }
    assert collision_geometry.wadeable(flowing)
    assert wm.flowing_water(flowing)
    assert not wm.still_water(flowing)
    assert wm._passable(flowing)
    assert wm.water_cost_multiplier(flowing) == wm.FLOWING_WATER_MULTIPLIER
    shallow = {(0, 61, 0): STONE, (0, 62, 0): flowing, (0, 63, 0): AIR}
    assert collision_geometry.standable(shallow, (0, 62, 0))
    assert collision_geometry.edit_stance(shallow, (0, 62, 0))
