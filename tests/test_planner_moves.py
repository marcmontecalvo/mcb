"""Baritone movement kinds in the planner: each enabled by a flag, each refusable."""

import json
from pathlib import Path

from mclab.terrain_steps import (
    TerrainPolicy,
    prepare_step,
)

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


def put_water(cells, p, level="0"):
    cells[p] = cell(p, WATER, "minecraft:water", level)


def policy(**kw):
    return TerrainPolicy(
        planner_tick_costs=True, planner_edits=True, allow_pillar=True, **kw
    )


# ---- dig down ---------------------------------------------------------------


# ---- descend with breaks ------------------------------------------------------


# ---- pillar -------------------------------------------------------------------


# ---- water landings --------------------------------------------------------------


# ---- falling blocks ---------------------------------------------------------------


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


# ---- swim edges in ticks -----------------------------------------------------------


def test_profile_enables_the_new_flags():
    path = (
        Path(__file__).parent.parent / "config/stepc/terrain-stepc-baritone-moves.json"
    )
    pol = TerrainPolicy.from_payload(json.loads(path.read_text()))
    assert pol.baritone_moves and pol.planner_swim and pol.baritone_search
