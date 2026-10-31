"""Baritone movement kinds in the planner: each enabled by a flag, each refusable."""

import json
from pathlib import Path

import pytest

from mclab.movement_policy import MovementPolicy
from mclab.route_moves import (
    LADDER_UP,
    prepare_descend,
    prepare_downward,
    prepare_pillar_edge,
)
from mclab.route_search import JUMP, WALK, WALK_OFF, RouteSearch, fall_ticks
from mclab.terrain_steps import (
    BREAK_EXTRA_TICKS,
    TerrainPolicy,
    break_ticks,
    prepare_step,
)
from mclab.water_movement import SWIM_TICKS

KIT = (
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 1, "item": "minecraft:iron_shovel", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
)
AIR, STONE, DIRT, SAND = (
    "minecraft:air",
    "minecraft:stone",
    "minecraft:dirt",
    "minecraft:sand",
)
OBSIDIAN = "minecraft:obsidian"  # not breakable by the planner
LADDER, WATER = "minecraft:ladder", "minecraft:water"


def cell(p, block, fluid="minecraft:empty", level="0"):
    if block == LADDER:
        boxes = [[0, 0, 0.8, 1, 1, 1]]
    elif block in (AIR, WATER, "minecraft:vine"):
        boxes = []
    else:
        boxes = [[0, 0, 0, 1, 1, 1]]
    return {
        "position": list(p),
        "block": block,
        "fluid": fluid,
        "collision_boxes": boxes,
        "properties": {"level": level},
    }


def world(xs, zs, ys, fill):
    return {
        (x, y, z): cell((x, y, z), fill(x, y, z) or AIR)
        for x in xs
        for z in zs
        for y in ys
    }


def put_water(cells, p, level="0"):
    cells[p] = cell(p, WATER, "minecraft:water", level)


def policy(**kw):
    return TerrainPolicy(
        planner_tick_costs=True, planner_edits=True, allow_pillar=True, **kw
    )


def run(cells, start, goal, pol, move=None):
    s = RouteSearch(
        cells,
        start,
        goal,
        map_revision=1,
        terrain_policy=pol,
        inventory=KIT,
        movement_policy=move,
    )
    while (
        r := s.advance(node_limit=4096, budget_ms=1000, segment_length=200)
    ).status == "searching":
        pass
    return r, s


# ---- dig down ---------------------------------------------------------------


def dig_world():
    return world(
        range(-2, 3),
        range(-2, 3),
        range(0, 7),
        lambda x, y, z: STONE if y <= 3 else None,
    )


def test_dig_down_reaches_a_cell_buried_in_the_floor():
    cells = dig_world()
    off, _ = run(cells, (0, 4, 0), (0, 3, 0), policy())
    on, _ = run(cells, (0, 4, 0), (0, 3, 0), policy(baritone_moves=True))
    assert off.status == "unreachable"
    assert on.status == "reached"
    (plan,) = on.steps
    assert (plan.kind, plan.source, plan.target) == ("downward", (0, 4, 0), (0, 3, 0))
    assert plan.breaks == ((0, 3, 0),) and plan.places == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop((0, 2, 0)),  # unknown support
        lambda c: c.__setitem__((0, 2, 0), cell((0, 2, 0), AIR)),  # no support
        lambda c: put_water(c, (1, 3, 0)),  # liquid neighbour
    ],
)
def test_dig_down_refuses_unknown_floor_and_liquid_neighbour(mutate):
    cells = dig_world()
    mutate(cells)
    assert prepare_downward(cells, (0, 4, 0), policy(baritone_moves=True), KIT) is None


def test_dig_down_refuses_protected_no_tool_budget_and_flag_off():
    cells, ok = dig_world(), policy(baritone_moves=True)
    assert prepare_downward(cells, (0, 4, 0), ok, KIT)
    assert prepare_downward(cells, (0, 4, 0), ok, KIT, protected=((0, 3, 0),)) is None
    assert prepare_downward(cells, (0, 4, 0), ok, ()) is None
    no_budget = policy(baritone_moves=True, break_budget=0)
    assert prepare_downward(cells, (0, 4, 0), no_budget, KIT) is None
    assert prepare_downward(cells, (0, 4, 0), policy(), KIT) is None


# ---- descend with breaks ------------------------------------------------------


def descend_world():
    def fill(x, y, z):
        if x == 1:
            return STONE if y <= 1 else (DIRT if y <= 4 else None)
        return STONE if y <= 2 else None

    return world(range(-1, 4), range(-1, 2), range(0, 8), fill)


def test_descend_with_breaks_clears_feet_head_and_above():
    cells = descend_world()
    off, _ = run(cells, (0, 3, 0), (1, 2, 0), policy())
    on, _ = run(cells, (0, 3, 0), (1, 2, 0), policy(baritone_moves=True))
    assert off.status == "unreachable"
    assert on.status == "reached"
    (plan,) = on.steps
    assert (plan.kind, plan.source, plan.target) == ("descend", (0, 3, 0), (1, 2, 0))
    assert set(plan.breaks) == {(1, 2, 0), (1, 3, 0), (1, 4, 0)} and plan.places == ()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop((1, 1, 0)),  # unknown landing floor
        lambda c: put_water(c, (2, 3, 0)),  # liquid next to a cut
        lambda c: c.pop((2, 2, 0)),  # unknown neighbour of a cut
    ],
)
def test_descend_refuses_unknown_floor_liquid_and_unknown_neighbour(mutate):
    cells = descend_world()
    mutate(cells)
    pol = policy(baritone_moves=True)
    assert prepare_descend(cells, (0, 3, 0), (1, 2, 0), pol, KIT) is None


def test_descend_refuses_protected_no_tool_budget_and_flag_off():
    cells, pol = descend_world(), policy(baritone_moves=True)
    args = (cells, (0, 3, 0), (1, 2, 0))
    assert prepare_descend(*args, pol, KIT)
    assert prepare_descend(*args, pol, KIT, protected=((1, 3, 0),)) is None
    assert prepare_descend(*args, pol, ()) is None
    assert (
        prepare_descend(*args, policy(baritone_moves=True, break_budget=2), KIT) is None
    )
    assert prepare_descend(*args, policy(), KIT) is None


# ---- pillar -------------------------------------------------------------------


def shaft(head=None):
    def fill(x, y, z):
        if (x, z) != (0, 0):
            return STONE if y <= 6 else None
        if y == 0:
            return STONE
        return head if y == 3 else None

    return world(range(-1, 2), range(-1, 2), range(0, 8), fill)


def test_pillar_edge_rises_one_in_a_closed_shaft():
    kw = dict(break_budget=0)
    off, _ = run(shaft(), (0, 1, 0), (0, 2, 0), policy(**kw))
    on, s = run(shaft(), (0, 1, 0), (0, 2, 0), policy(baritone_moves=True, **kw))
    assert off.status == "unreachable"
    assert on.status == "reached"
    (plan,) = on.steps
    assert (plan.kind, plan.source, plan.target) == ("pillar", (0, 1, 0), (0, 2, 0))
    assert plan.places == ((0, 1, 0),) and plan.breaks == ()
    assert s.costs[(0, 2, 0)] > JUMP + 20  # JUMP + PLACE_TICKS plus MCB overheads


def test_pillar_edge_refuses_a_solid_cell_above_the_head():
    # The body's pillar step admits no breaks, so the planner must not emit one.
    assert (
        prepare_pillar_edge(shaft(STONE), (0, 1, 0), policy(baritone_moves=True), KIT)
        is None
    )
    plan = prepare_pillar_edge(shaft(), (0, 1, 0), policy(baritone_moves=True), KIT)
    assert plan.breaks == () and plan.places == ((0, 1, 0),)


def test_pillar_edge_refusals():
    cells, pol = shaft(), policy(baritone_moves=True)
    assert prepare_pillar_edge(cells, (0, 1, 0), pol, KIT)
    assert (
        prepare_pillar_edge(cells, (0, 1, 0), pol, KIT, protected=((0, 1, 0),)) is None
    )
    assert prepare_pillar_edge(cells, (0, 1, 0), pol, ()) is None  # no blocks
    for kw in ({"place_budget": 0}, {"allow_pillar": False}):
        other = TerrainPolicy(baritone_moves=True, **{"allow_pillar": True, **kw})
        assert prepare_pillar_edge(cells, (0, 1, 0), other, KIT) is None
    assert prepare_pillar_edge(shaft(), (0, 1, 0), policy(), KIT) is None
    unknown = shaft()
    del unknown[0, 4, 0]
    assert prepare_pillar_edge(unknown, (0, 1, 0), pol, KIT) is None


# ---- water landings --------------------------------------------------------------


def pool(drop_floor=True, level="0"):
    cells = world(
        range(-1, 4),
        range(-1, 2),
        range(0, 10),
        lambda x, y, z: STONE if (x <= 0 and y <= 7) or (x > 0 and y == 0) else None,
    )
    for x in (1, 2, 3):
        for z in (-1, 0, 1):
            for y in (1, 2, 3):
                put_water(cells, (x, y, z), level)
    if not drop_floor:
        for x in (1, 2, 3):
            for z in (-1, 0, 1):
                del cells[x, 0, z]
    return cells


SWIMMER = MovementPolicy(allow_swim=True)


def test_water_landing_allows_a_tall_fall_into_still_water():
    off, _ = run(pool(), (0, 8, 0), (1, 4, 0), policy(), SWIMMER)
    on, s = run(pool(), (0, 8, 0), (1, 4, 0), policy(baritone_moves=True), SWIMMER)
    assert off.status == "unreachable"
    assert on.status == "reached" and on.steps == ()
    assert s.costs[(1, 4, 0)] == pytest.approx(WALK_OFF + fall_ticks(4))


@pytest.mark.parametrize(
    ("cells", "move", "flag"),
    [
        (pool(drop_floor=False), SWIMMER, True),  # unknown floor
        (pool(level="1"), SWIMMER, True),  # flowing water
        (pool(), MovementPolicy(), True),  # motor cannot swim
        (pool(), SWIMMER, False),
    ],
)
def test_water_landing_refusals(cells, move, flag):
    r, _ = run(cells, (0, 8, 0), (1, 4, 0), policy(baritone_moves=flag), move)
    assert r.status == "unreachable"


# ---- falling blocks ---------------------------------------------------------------


def plug(stack=1):
    def fill(x, y, z):
        if y == 0 or y >= 5 or z != 0:
            return OBSIDIAN
        if x == 2 and y in (1, 2):
            return DIRT
        if x == 2 and 3 <= y < 3 + stack:
            return SAND
        return STONE if y >= 3 and x != 2 else None

    return world(range(0, 5), range(-1, 2), range(0, 7), fill)


def test_falling_block_above_a_cut_is_priced_not_refused():
    off, _ = run(plug(), (0, 1, 0), (4, 1, 0), policy())
    one, s1 = run(plug(1), (0, 1, 0), (4, 1, 0), policy(baritone_moves=True))
    two, s2 = run(plug(2), (0, 1, 0), (4, 1, 0), policy(baritone_moves=True))
    assert off.status == "unreachable"
    assert one.status == two.status == "reached"
    extra = break_ticks(SAND) + BREAK_EXTRA_TICKS
    assert s2.costs[(4, 1, 0)] - s1.costs[(4, 1, 0)] == pytest.approx(extra)


def test_falling_neighbours_still_refused_when_unsupported_wet_or_unknown():
    pol = policy(baritone_moves=True)
    cells = {
        (x, y, z): cell((x, y, z), STONE)
        for x in range(-1, 2)
        for y in range(0, 3)
        for z in range(-1, 2)
    }
    for p in ((-1, 1, 0), (-1, 2, 0), (0, 2, 0)):
        cells[p] = cell(p, AIR)
    cells[0, 1, 0] = cell((0, 1, 0), DIRT)
    cells[0, 1, 1] = cell((0, 1, 1), SAND)  # side falling block, stone below it
    step = ((-1, 1, 0), (0, 1, 0))
    assert prepare_step(cells, *step, pol, KIT)
    assert prepare_step(cells, *step, policy(), KIT) is None  # flag off: refused
    cells[0, 0, 1] = cell((0, 0, 1), AIR)  # unsupported side falling block
    assert prepare_step(cells, *step, pol, KIT) is None
    cells[0, 0, 1] = cell((0, 0, 1), STONE)
    cells[0, 1, 1] = cell((0, 1, 1), STONE)
    put_water(cells, (0, 1, -1))  # liquid neighbour
    assert prepare_step(cells, *step, pol, KIT) is None
    del cells[0, 1, -1]  # unknown neighbour
    assert prepare_step(cells, *step, pol, KIT) is None


# ---- ladders / vines ------------------------------------------------------------


def ladder_world(block=LADDER, gap=None, fluid="minecraft:empty"):
    cells = world(
        range(0, 3),
        range(-1, 3),
        range(0, 9),
        lambda x, y, z: STONE if y == 0 or (z >= 1 and y <= 5) else None,
    )
    for y in range(1, 6):
        cells[1, y, 0] = cell((1, y, 0), block, fluid)
    if gap:
        cells[1, gap, 0] = cell((1, gap, 0), AIR)
    return cells


@pytest.mark.parametrize("block", [LADDER, "minecraft:vine"])
def test_climbable_column_gives_vertical_edges(block):
    kw = dict(allow_place=False, allow_break=False)
    cells = ladder_world(block)
    off, _ = run(cells, (1, 1, -1), (1, 6, 1), policy(**kw))
    on, s = run(cells, (1, 1, -1), (1, 6, 1), policy(baritone_moves=True, **kw))
    assert off.status == "unreachable"
    assert on.status == "reached" and on.steps == ()
    assert (1, 3, 0) in on.route
    assert s.costs[(1, 5, 0)] - s.costs[(1, 4, 0)] == pytest.approx(LADDER_UP)


@pytest.mark.parametrize(
    "cells", [ladder_world(gap=3), ladder_world(fluid="minecraft:water")]
)
def test_ladder_gaps_and_waterlogged_cells_are_not_climbable(cells):
    kw = dict(allow_place=False, allow_break=False)
    r, _ = run(cells, (1, 1, -1), (1, 6, 1), policy(baritone_moves=True, **kw))
    assert r.status == "unreachable"


# ---- swim edges in ticks -----------------------------------------------------------


def swim_world(flow_at=None):
    def fill(x, y, z):
        if z != 0:
            return OBSIDIAN if y <= 4 else None
        if x <= 0 or x >= 5:
            return STONE if y == 0 or (x >= 5 and y <= 1) else None
        return STONE if y == -1 else None

    cells = world(range(0, 6), range(-1, 2), range(0, 7), fill)
    for x in range(1, 5):
        put_water(cells, (x, 0, 0), "1" if x == flow_at else "0")
    return cells


def test_swim_edges_cost_swim_ticks_times_water_multiplier_and_exit_up():
    exit_up = max(JUMP, WALK)
    off, _ = run(swim_world(), (0, 1, 0), (5, 2, 0), policy(), SWIMMER)
    assert off.status == "unreachable"  # no surface -> land exit without the flag
    on, s = run(swim_world(), (0, 1, 0), (5, 2, 0), policy(planner_swim=True), SWIMMER)
    assert on.status == "reached"
    assert s.costs[(5, 2, 0)] == pytest.approx(4 * SWIM_TICKS + exit_up)
    mixed = swim_world(flow_at=2)  # one edge enters flowing water at 1.5x
    flowing, s = run(mixed, (0, 1, 0), (5, 2, 0), policy(planner_swim=True), SWIMMER)
    assert flowing.status == "reached"
    assert s.costs[(5, 2, 0)] == pytest.approx(4.5 * SWIM_TICKS + exit_up)
    legacy, _ = run(mixed, (0, 1, 0), (4, 1, 0), policy(), SWIMMER)
    assert legacy.status == "unreachable"  # flowing water needs planner_swim


def test_profile_enables_the_new_flags():
    path = (
        Path(__file__).parent.parent / "config/stepc/terrain-stepc-baritone-moves.json"
    )
    pol = TerrainPolicy.from_payload(json.loads(path.read_text()))
    assert pol.baritone_moves and pol.planner_swim and pol.baritone_search
