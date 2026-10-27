from dataclasses import replace

from mclab.navigation import standable
from mclab.terrain_route import TerrainRouteSearch, known_step
from mclab.terrain_steps import TerrainPolicy


def corridor():
    cells = {}
    for x in range(-2, 3):
        for y in range(8):
            for z in range(-2, 7):
                solid = y == 0 or (abs(x) == 1 and y < 7)
                cells[x, y, z] = {
                    "position": [x, y, z],
                    "block": "minecraft:bedrock" if solid else "minecraft:air",
                    "collision_boxes": [[0, 0, 0, 1, 1, 1]] if solid else [],
                }
    return cells


def solid(cells, p, name="minecraft:stone"):
    cells[p] = {
        "position": list(p),
        "block": name,
        "collision_boxes": [[0, 0, 0, 1, 1, 1]],
    }


def finish(search):
    expanded = 0
    for _ in range(1000):
        result = search.advance(node_limit=2, budget_ms=100)
        assert result.expanded - expanded <= 2
        expanded = result.expanded
        if result.status != "searching":
            return result
    raise AssertionError("Terrain horizon did not terminate")


def test_four_move_forecast_contains_future_cut_place_and_walks():
    cells = corridor()
    solid(cells, (0, 1, 1))
    solid(cells, (0, 2, 1))
    for z in range(3, 7):
        for y in (1, 2):
            solid(cells, (0, y, z), "minecraft:bedrock")
    inventory = [
        {"slot": 0, "item": "minecraft:iron_pickaxe", "count": 1},
        {"slot": 3, "item": "minecraft:cobblestone", "count": 1},
    ]
    result = finish(
        TerrainRouteSearch(
            cells,
            (0, 1, 0),
            (0, 3, 4),
            map_revision=5,
            policy=TerrainPolicy(),
            inventory=inventory,
        )
    )
    assert result.status == "reached"
    assert len(result.steps) == 4
    assert result.route == ((0, 1, 0), (0, 2, 1), (0, 2, 2), (0, 3, 3), (0, 3, 4))
    assert result.steps[0].breaks == ((0, 2, 1),)
    assert result.steps[1].places == ((0, 1, 2),)
    assert all(not s.breaks and not s.places for s in result.steps[2:])
    # Forecasting must not edit the actual observed map.
    assert cells[0, 2, 1]["block"] == "minecraft:stone"
    assert cells[0, 1, 2]["block"] == "minecraft:air"


def test_forecast_accounts_for_all_future_material_and_edit_reservations():
    cells = corridor()
    solid(cells, (0, 1, 0), "minecraft:cobblestone")
    for z in range(2, 7):
        for y in (1, 2):
            solid(cells, (0, y, z), "minecraft:bedrock")
    inventory = [{"slot": 3, "item": "minecraft:cobblestone", "count": 1}]
    policy = TerrainPolicy(allow_break=False, place_budget=1)
    result = finish(
        TerrainRouteSearch(
            cells,
            (0, 2, 0),
            (0, 3, 4),
            map_revision=2,
            policy=policy,
            inventory=inventory,
            protected={(0, 1, 0)},
        )
    )
    assert result.status == "reached" and len(result.steps) == 4
    assert sum(len(s.places) for s in result.steps) == 1
    no_stock = finish(
        TerrainRouteSearch(
            cells,
            (0, 2, 0),
            (0, 3, 4),
            map_revision=2,
            policy=replace(policy, reserve_blocks=1),
            inventory=inventory,
            protected={(0, 1, 0)},
        )
    )
    assert no_stock.status == "incomplete" and not no_stock.steps


def test_unknown_geometry_does_not_become_a_future_foothold():
    cells = corridor()
    solid(cells, (0, 1, 0), "minecraft:cobblestone")
    del cells[0, 0, 1]
    policy = TerrainPolicy(allow_break=False, place_budget=1)
    result = finish(
        TerrainRouteSearch(
            cells,
            (0, 2, 0),
            (0, 3, 4),
            map_revision=2,
            policy=policy,
            inventory=[],
            protected={(0, 1, 0)},
        )
    )
    assert result.status == "incomplete"
    assert not result.steps
    assert not known_step(cells, (0, 2, 0), (0, 2, 1))
    assert standable(cells, (0, 2, 0))
