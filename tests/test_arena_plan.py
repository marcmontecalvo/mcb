"""The arena plan model: voxelising, JSON, walking rules and every validator check."""

import json
from dataclasses import replace

import pytest

from mclab.arena import plan as ap
from mclab.arena.plan import Capacity, Fill, Plan, Team

AIR = "minecraft:air"
STONE = "minecraft:stone_bricks"
LAMP = "minecraft:sea_lantern"
GLASS = "minecraft:glass"


def good_plan() -> Plan:
    """A tiny sealed room that satisfies every rule (floor top y=3, feet y=4)."""
    fills = [
        Fill((0, 0, 0), (23, 11, 31), AIR),
        Fill((0, 0, 0), (23, 11, 31), STONE),
        Fill((2, 4, 2), (21, 9, 29), AIR),
        # gallery: raised box in the north wall with a glass window
        Fill((8, 4, 2), (15, 9, 5), STONE),
        Fill((9, 5, 2), (14, 9, 4), AIR),
        Fill((10, 6, 5), (13, 7, 5), GLASS),
        Fill((10, 4, 3), (10, 4, 3), LAMP),
        Fill((13, 4, 3), (13, 4, 3), LAMP),
    ]
    for x in range(2, 22, 4):
        for z in range(2, 30, 4):
            if 8 <= x <= 15 and z <= 5:
                continue
            fills.append(Fill((x, 3, z), (x, 3, z), LAMP))
    return Plan(
        name="room",
        size=(24, 12, 32),
        fills=fills,
        teams={
            "red": Team(
                pads=[(4, 4, 27), (6, 4, 27), (8, 4, 27), (10, 4, 27)], entry=(7, 4, 28)
            )
        },
        mob_spawns=[(18, 4, 8)],
        spectator=(11, 5, 3),
        capacity=Capacity(teams=1, per_team=4, mobs=5),
    )


def with_fills(plan: Plan, *extra: Fill) -> Plan:
    return replace(plan, fills=[*plan.fills, *extra])


def codes(plan: Plan) -> set[str]:
    return {problem.split(":", 1)[0] for problem in ap.validate(plan)}


def test_good_plan_is_clean():
    assert ap.validate(good_plan()) == []


# --- model, JSON ------------------------------------------------------------


def test_json_round_trip_and_compact_fills():
    plan = good_plan()
    text = ap.to_json(plan)
    assert ap.from_json(text) == plan
    data = json.loads(text)
    assert data["schema"] == 1
    assert data["fills"][0] == {"from": [0, 0, 0], "to": [23, 11, 31], "block": AIR}
    assert data["markers"]["teams"]["red"]["entry"] == [7, 4, 28]
    assert data["capacity"] == {"teams": 1, "per_team": 4, "mobs": 5}
    # one fill per line keeps committed diffs readable
    assert text.count("\n") < len(plan.fills) + 40
    assert ap.to_json(plan) == text  # deterministic


def test_from_json_rejects_garbage():
    with pytest.raises(ValueError):
        ap.from_json("[]")
    with pytest.raises(ValueError):
        ap.from_json('{"schema": 1}')


def test_block_helpers():
    assert ap.base_name("minecraft:lantern[hanging=true]") == "lantern"
    assert ap.base_name("stone") == "stone"
    assert (
        ap.is_glass("glass")
        and ap.is_glass("tinted_glass")
        and ap.is_glass("blue_stained_glass_pane")
    )
    assert not ap.is_glass("stone")
    assert ap.is_passable("air") and ap.is_passable("red_wall_banner")
    assert not ap.is_passable("lantern")
    assert ap.is_banned("sand") and ap.is_banned("black_concrete_powder")
    assert ap.is_banned("birch_leaves") and ap.is_banned("redstone_lamp")
    assert not ap.is_banned("sandstone") and not ap.is_banned("stone_bricks")


# --- voxelising -------------------------------------------------------------


def test_voxelise_later_fill_wins_and_strips_states():
    grid = ap.voxelize(good_plan())
    assert grid.base(0, 0, 0) == "stone_bricks"
    assert grid.base(5, 5, 5) == "air"
    assert grid.block(11, 6, 5) == GLASS
    assert grid.base(10, 4, 3) == "sea_lantern"
    assert not grid.in_bounds(24, 0, 0)
    assert grid.in_bounds(23, 11, 31)


def test_voxelise_keeps_block_states():
    plan = with_fills(
        good_plan(),
        Fill((5, 4, 5), (5, 4, 5), "minecraft:stone_brick_stairs[facing=north]"),
    )
    grid = ap.voxelize(plan)
    assert grid.block(5, 4, 5) == "minecraft:stone_brick_stairs[facing=north]"
    assert grid.base(5, 4, 5) == "stone_brick_stairs"


def test_split_fill_covers_same_cells_in_limit():
    big = Fill((0, 0, 0), (99, 49, 99), STONE)
    parts = ap.split_fill(big)
    assert all(ap.fill_volume(part) <= ap.MAX_FILL_VOLUME for part in parts)
    assert sum(ap.fill_volume(part) for part in parts) == 100 * 50 * 100
    assert ap.split_fill(Fill((0, 0, 0), (3, 3, 3), STONE)) == [
        Fill((0, 0, 0), (3, 3, 3), STONE)
    ]


# --- walking rules ----------------------------------------------------------


def terrain(heights, *, z=4, sy=14):
    """Columns along x; solid up to heights[x] (feet at heights[x] + 1), full across z."""
    fills = [Fill((0, 0, 0), (len(heights) - 1, sy - 1, z - 1), AIR)]
    for x, h in enumerate(heights):
        if h >= 0:
            fills.append(Fill((x, 0, 0), (x, h, z - 1), STONE))
    return ap.voxelize(
        Plan("t", (len(heights), sy, z), fills, {}, [], (0, 1, 0), Capacity(1, 1, 0))
    )


def reach_x(heights, walker=ap.PLAYER, z=1):
    grid = terrain(heights)
    start = (0, heights[0] + 1, z)
    return {cell[0] for cell in ap.flood(grid, walker, [start])}


def test_player_climbs_one_block_steps_but_not_two():
    assert reach_x([0, 1, 2, 3]) == {0, 1, 2, 3}
    assert reach_x([0, 2, 2]) == {0}


def test_player_drops_three_but_not_four():
    assert reach_x([4, 1, 1]) == {0, 1, 2}
    assert reach_x([5, 1, 1]) == {0}


def test_player_needs_two_air_to_pass():
    grid = ap.voxelize(
        Plan(
            "t",
            (6, 8, 4),
            [
                Fill((0, 0, 0), (5, 7, 3), AIR),
                Fill((0, 0, 0), (5, 0, 3), STONE),
                Fill((3, 2, 0), (3, 2, 3), STONE),
            ],
            {},
            [],
            (0, 1, 0),
            Capacity(1, 1, 0),
        )
    )
    # a 1-high gap under a ceiling block stops the player (needs 2)
    assert {c[0] for c in ap.flood(grid, ap.PLAYER, [(0, 1, 1)])} == {0, 1, 2}


def test_ravager_needs_a_two_wide_four_high_lane():
    wide = Fill((0, 0, 0), (9, 9, 5), AIR)
    floor = Fill((0, 0, 0), (9, 0, 5), STONE)
    wall_gap1 = [Fill((5, 1, 0), (5, 8, 5), STONE), Fill((5, 1, 2), (5, 8, 2), AIR)]
    wall_gap2 = [Fill((5, 1, 0), (5, 8, 5), STONE), Fill((5, 1, 2), (5, 8, 3), AIR)]
    low_gap = [Fill((5, 1, 0), (5, 8, 5), STONE), Fill((5, 1, 2), (5, 3, 3), AIR)]

    def reach(extra):
        grid = ap.voxelize(
            Plan(
                "t",
                (10, 10, 6),
                [wide, floor, *extra],
                {},
                [],
                (0, 1, 0),
                Capacity(1, 1, 0),
            )
        )
        return {c[0] for c in ap.flood(grid, ap.RAVAGER, [(1, 1, 1)])}

    assert 8 not in reach(wall_gap1)
    assert 8 in reach(wall_gap2)
    assert 8 not in reach(low_gap)  # 3 high is not enough for a ravager


# --- light ------------------------------------------------------------------


def test_light_spreads_and_stops_at_solid():
    plan = Plan(
        "t",
        (12, 6, 5),
        [
            Fill((0, 0, 0), (11, 5, 4), AIR),
            Fill((0, 0, 0), (11, 0, 4), STONE),
            Fill((0, 1, 2), (0, 1, 2), LAMP),
            Fill((3, 1, 0), (3, 5, 4), STONE),
        ],
        {},
        [],
        (0, 1, 0),
        Capacity(1, 1, 0),
    )
    grid = ap.voxelize(plan)
    light = ap.light_levels(grid)
    assert light[grid.idx(0, 1, 2)] == 15
    assert light[grid.idx(1, 1, 2)] == 14
    assert light[grid.idx(2, 1, 2)] == 13
    assert light[grid.idx(5, 1, 2)] == 0  # behind the wall


# --- validator: structure ---------------------------------------------------


def test_schema_and_name():
    assert "schema" in codes(replace(good_plan(), schema=2))
    assert "name" in codes(replace(good_plan(), name=""))
    assert "name" in codes(replace(good_plan(), name="Bad Name"))


def test_size_limits():
    assert "size" in codes(replace(good_plan(), size=(0, 5, 5)))
    assert "size" in codes(replace(good_plan(), size=(20, ap.MAX_HEIGHT + 1, 20)))


def test_fill_out_of_bounds_and_order():
    assert "fill-bounds" in codes(
        with_fills(good_plan(), Fill((0, 0, 0), (99, 5, 5), STONE))
    )
    assert "fill-bounds" in codes(
        with_fills(good_plan(), Fill((-1, 0, 0), (3, 3, 3), STONE))
    )
    assert "fill-order" in codes(
        with_fills(good_plan(), Fill((5, 5, 5), (4, 5, 5), STONE))
    )


def test_fill_volume_cap():
    big = Plan(
        "big",
        (40, 30, 30),
        [Fill((0, 0, 0), (39, 29, 29), AIR)],
        {},
        [],
        (0, 1, 0),
        Capacity(1, 1, 0),
    )
    assert "fill-volume" in codes(big)


def test_first_fill_must_clear_everything():
    plan = good_plan()
    assert "clear-first" in codes(replace(plan, fills=plan.fills[1:]))
    short = replace(plan, fills=[Fill((0, 0, 0), (5, 5, 5), AIR), *plan.fills[1:]])
    assert "clear-first" in codes(short)


def test_block_string_format():
    assert "block-format" in codes(
        with_fills(good_plan(), Fill((5, 4, 5), (5, 4, 5), "stone"))
    )
    assert "block-format" in codes(
        with_fills(good_plan(), Fill((5, 4, 5), (5, 4, 5), "minecraft:Stone"))
    )


@pytest.mark.parametrize(
    "block",
    [
        "minecraft:sand",
        "minecraft:red_sand",
        "minecraft:gravel",
        "minecraft:white_concrete_powder",
        "minecraft:anvil",
        "minecraft:water",
        "minecraft:lava",
        "minecraft:oak_leaves",
        "minecraft:wheat",
        "minecraft:fire",
        "minecraft:oak_door[half=lower]",
        "minecraft:oak_trapdoor",
        "minecraft:stone_button",
        "minecraft:oak_pressure_plate",
        "minecraft:redstone_wire",
        "minecraft:redstone_lamp",
        "minecraft:lever",
        "minecraft:tnt",
        "minecraft:red_bed",
        "minecraft:chest",
        "minecraft:barrel",
        "minecraft:spawner",
        "minecraft:white_carpet",
    ],
)
def test_banned_blocks(block):
    plan = with_fills(good_plan(), Fill((12, 4, 12), (12, 4, 12), block))
    assert "banned" in codes(plan)


def test_banned_block_is_flagged_even_if_overwritten():
    plan = with_fills(
        good_plan(),
        Fill((12, 4, 12), (12, 4, 12), "minecraft:sand"),
        Fill((12, 4, 12), (12, 4, 12), AIR),
    )
    assert "banned" in codes(plan)


def test_too_many_distinct_blocks():
    fills = [Fill((0, 0, 0), (9, 9, 9), AIR)]
    fills += [
        Fill((0, 0, i % 10), (0, 0, i % 10), f"minecraft:x{i}") for i in range(300)
    ]
    plan = Plan("t", (10, 10, 10), fills, {}, [], (0, 1, 0), Capacity(1, 1, 0))
    assert "palette" in codes(plan)


# --- validator: markers -----------------------------------------------------


def test_marker_outside_bounds():
    plan = good_plan()
    bad = replace(
        plan,
        teams={
            "red": Team(pads=[*plan.teams["red"].pads, (99, 4, 3)], entry=(7, 4, 28))
        },
    )
    assert "marker-bounds" in codes(bad)
    assert "marker-bounds" in codes(replace(plan, spectator=(0, 0, -1)))
    assert "marker-bounds" in codes(replace(plan, mob_spawns=[(18, 4, 99)]))


def test_team_names_and_counts():
    plan = good_plan()
    assert "team" in codes(replace(plan, teams={"purple": plan.teams["red"]}))
    assert "team" in codes(replace(plan, teams={}))
    assert "team" in codes(
        replace(plan, teams={"red": Team(pads=[], entry=(7, 4, 28))})
    )


def test_pad_needs_floor_and_two_air():
    plan = good_plan()
    solid_pad = with_fills(plan, Fill((6, 4, 27), (6, 4, 27), STONE))
    assert "pad-standable" in codes(solid_pad)
    low_ceiling = with_fills(plan, Fill((6, 5, 27), (6, 5, 27), STONE))
    assert "pad-standable" in codes(low_ceiling)
    floating = with_fills(plan, Fill((6, 3, 27), (6, 3, 27), AIR))
    assert "pad-standable" in codes(floating)


def test_entry_must_be_standable():
    plan = good_plan()
    bad = replace(
        plan, teams={"red": Team(pads=plan.teams["red"].pads, entry=(7, 5, 28))}
    )
    assert "entry-standable" in codes(bad)


def test_spawn_needs_solid_3x3_floor_and_3_air():
    plan = good_plan()
    hole = with_fills(plan, Fill((17, 3, 7), (17, 3, 7), AIR))
    assert "spawn-floor" in codes(hole)
    low = with_fills(plan, Fill((19, 6, 9), (19, 6, 9), STONE))
    assert "spawn-air" in codes(low)


def test_spawn_must_be_far_from_pads():
    plan = good_plan()
    near = replace(plan, mob_spawns=[(12, 4, 22)])
    assert "spawn-distance" in codes(near)


def test_capacity_checks():
    plan = good_plan()
    assert "capacity" in codes(replace(plan, capacity=Capacity(2, 4, 5)))
    assert "capacity" in codes(replace(plan, capacity=Capacity(1, 9, 5)))
    assert "capacity" in codes(replace(plan, capacity=Capacity(1, 4, 5), mob_spawns=[]))
    assert "capacity" in codes(replace(plan, capacity=Capacity(1, 4, -1)))


# --- validator: shell, roof, light ------------------------------------------


def test_shell_must_be_sealed():
    assert "shell-open" in codes(
        with_fills(good_plan(), Fill((12, 11, 12), (12, 11, 12), AIR))
    )
    assert "shell-open" in codes(
        with_fills(good_plan(), Fill((0, 5, 12), (0, 5, 12), AIR))
    )


def test_no_glass_in_the_roof():
    top_glass = with_fills(good_plan(), Fill((12, 11, 12), (12, 11, 12), GLASS))
    assert "shell-open" in codes(top_glass)
    inner_glass = with_fills(good_plan(), Fill((12, 10, 12), (12, 11, 12), GLASS))
    found = codes(inner_glass)
    assert "roof-glass" in found


def test_roof_must_exist_over_floor():
    hole = with_fills(good_plan(), Fill((12, 10, 12), (12, 11, 12), AIR))
    found = codes(hole)
    assert "roof-open" in found and "shell-open" in found


def test_dark_corners_are_reported():
    plan = good_plan()
    no_lamps = replace(plan, fills=[f for f in plan.fills if f.block != LAMP])
    assert "light" in codes(no_lamps)


# --- validator: walking -----------------------------------------------------


def test_spectator_must_stand_on_floor_inside_gallery():
    assert "spectator-standable" in codes(replace(good_plan(), spectator=(11, 6, 3)))


def test_spectator_cut_off_from_floor():
    opened = with_fills(good_plan(), Fill((11, 5, 5), (11, 7, 5), AIR))
    assert "spectator-path" in codes(opened)


def test_spectator_needs_a_window_onto_the_floor():
    sealed = with_fills(good_plan(), Fill((10, 6, 5), (13, 7, 5), STONE))
    assert "spectator-view" in codes(sealed)
    no_glass = with_fills(good_plan(), Fill((10, 6, 5), (13, 7, 5), AIR))
    assert "spectator-view" in codes(no_glass) or "spectator-path" in codes(no_glass)


def test_unreachable_perch_is_reported():
    pillar = with_fills(good_plan(), Fill((12, 4, 15), (12, 5, 15), STONE))
    assert "floor-unreachable" in codes(pillar)


def test_one_step_block_is_not_a_perch():
    step = with_fills(good_plan(), Fill((12, 4, 15), (12, 4, 15), STONE))
    assert ap.validate(step) == []


def test_pit_you_cannot_climb_out_of_is_a_trap():
    pit = with_fills(good_plan(), Fill((12, 2, 15), (14, 3, 17), AIR))
    assert "floor-trap" in codes(pit)


def test_markers_in_a_sealed_off_part_are_unreachable():
    wall = with_fills(good_plan(), Fill((2, 4, 24), (21, 9, 24), STONE))
    found = codes(wall)
    assert "marker-unreachable" in found


def test_ravager_blocked_by_a_one_wide_gap():
    gate = with_fills(
        good_plan(),
        Fill((2, 4, 20), (21, 9, 20), STONE),
        Fill((10, 4, 20), (10, 9, 20), AIR),
        Fill((10, 3, 20), (10, 3, 20), LAMP),
    )
    found = codes(gate)
    assert "ravager" in found
    assert "floor-unreachable" not in found and "marker-unreachable" not in found


def test_ravager_passes_a_wide_gate():
    gate = with_fills(
        good_plan(),
        Fill((2, 4, 20), (21, 9, 20), STONE),
        Fill((9, 4, 20), (11, 9, 20), AIR),
    )
    assert "ravager" not in codes(gate)


def test_a_big_clear_may_be_split_into_pieces():
    plan = good_plan()
    clear = ap.split_fill(Fill((0, 0, 0), (23, 11, 31), AIR), limit=2000)
    assert len(clear) > 1
    assert ap.validate(replace(plan, fills=[*clear, *plan.fills[1:]])) == []
    partial = replace(plan, fills=[*clear[:-1], *plan.fills[1:]])
    assert "clear-first" in codes(partial)


def test_ravager_straddles_a_stair_edge_but_not_a_two_deep_drop():
    def reach(heights):
        grid = terrain(heights, z=6)
        return {c[0] for c in ap.flood(grid, ap.RAVAGER, [(0, heights[0] + 1, 1)])}

    assert 5 in reach([1, 1, 2, 3, 3, 3, 3, 3])
    assert 7 not in reach([1, 1, 3, 3, 3, 3, 3, 3])
