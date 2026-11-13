"""Step C planner core: tick costs, planning through unknown space, edit edges."""

import re
from pathlib import Path
from types import SimpleNamespace

from mclab import native_driver
from mclab.terrain_steps import WIDE_EXCAVATABLE, TerrainPolicy

KIT = (
    {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
    {"slot": 1, "item": "minecraft:iron_shovel", "count": 1},
    {"slot": 2, "item": "minecraft:iron_axe", "count": 1},
    {"slot": 3, "item": "minecraft:cobblestone", "count": 64},
)


def cell(p, block):
    solid = block != "minecraft:air"
    return {
        "position": list(p),
        "block": block,
        "fluid": "minecraft:empty",
        "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
    }


def world(xs, zs, solid, top=10):
    """Cells for every (x, y, z) in the box; solid(x, y, z) -> block or None."""
    return {
        (x, y, z): cell((x, y, z), solid(x, y, z) or "minecraft:air")
        for x in xs
        for z in zs
        for y in range(0, top + 1)
    }


def test_native_motor_is_offered_only_the_route_before_a_planned_edit():
    plan = SimpleNamespace(source=(2, 1, 0), target=(3, 2, 0))
    route = [(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 2, 0), (4, 2, 0)]
    controller = SimpleNamespace(
        route=route,
        route_steps={(plan.source, plan.target): plan},
        last_result=None,
        terrain_policy=None,
    )
    controller.walkable_route = lambda: route[:3]
    assert native_driver.offered_route(controller) == route[:3]


def test_body_step_motor_allows_exactly_the_python_breakable_set():
    java = (
        Path(__file__).parents[1]
        / "mcb-body/src/main/java/com/vulpine/mcb/body/StepMotor.java"
    )
    entries = dict(re.findall(r'Map\.entry\("([^"]+)","([^"]+)"\)', java.read_text()))
    assert entries == {block: spec[1] for block, spec in WIDE_EXCAVATABLE.items()}


# --- Baritone v1.20.0 search mechanics (baritone_search / baritone_diagonals) ---


def _wading_pocket():
    # Stone to y=1 with one water cell let into it at (0, 2, 0); a gallery of air at
    # y=3..4 and a dirt roof at y=5. A step up out of the water needs four clear
    # cells over the feet, and the roof is the fourth.
    def solid(x, y, z):
        if y <= 2:
            return "minecraft:stone"
        return "minecraft:dirt" if y == 5 else None

    cells = world(range(-2, 5), range(-2, 3), solid, 8)
    cells[0, 2, 0] = {
        "position": [0, 2, 0],
        "block": "minecraft:water",
        "fluid": "minecraft:water",
        "properties": {"level": "0"},
        "collision_boxes": [],
    }
    return cells


def test_a_step_is_prepared_from_a_wading_stance_but_not_a_swimming_one():
    from mclab.terrain_steps import prepare_step

    cells = _wading_pocket()
    policy = TerrainPolicy(planner_tick_costs=True, planner_edits=True)
    step = prepare_step(cells, (0, 2, 0), (1, 3, 0), policy, list(KIT))
    assert step is not None and set(step.breaks) == {(0, 5, 0), (1, 5, 0)}
    cells[0, 3, 0] = dict(cells[0, 2, 0], position=[0, 3, 0])  # head under water
    assert prepare_step(cells, (0, 2, 0), (1, 3, 0), policy, list(KIT)) is None
