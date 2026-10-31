import itertools

from test_collision_geometry import slab_map
from test_gap_jump import gap_map
from test_route_search import floor_map
from test_staircase_steps import staircase_map

from mclab import collision_geometry, navigation
from mclab.gap_jump import prepare_gap
from mclab.grid_cache import Grid, OverlayGrid
from mclab.movement_policy import MovementPolicy

AIR = {"block": "minecraft:air", "fluid": "minecraft:empty", "collision_boxes": []}


def points(cells, pad=2):
    xs, ys, zs = zip(*cells, strict=True)
    return itertools.product(
        range(min(xs) - pad, max(xs) + pad + 1),
        range(min(ys) - pad, max(ys) + pad + 1),
        range(min(zs) - pad, max(zs) + pad + 1),
    )


def test_memoised_grid_answers_like_the_plain_dict():
    policy = MovementPolicy(max_gap=2, allow_sprint=True)
    for cells in (
        floor_map([(x, z) for x in range(4) for z in range(3)]),
        slab_map(),
        staircase_map(),
        gap_map(width=2),
    ):
        grid = Grid(cells)
        for _ in range(2):  # second pass reads the memo
            for p in points(cells):
                x, y, z = p
                assert navigation.standable(grid, p) == navigation.standable(cells, p)
                assert collision_geometry.standable(
                    grid, p
                ) == collision_geometry.standable(cells, p)
                assert collision_geometry.surfaces(
                    grid, x, z, y - 3, y + 1
                ) == collision_geometry.surfaces(cells, x, z, y - 3, y + 1)
                assert collision_geometry.clear_column(
                    grid, x, z, y, y + 1.8
                ) == collision_geometry.clear_column(cells, x, z, y, y + 1.8)
                for dx, dz in navigation.DIRECTIONS:
                    target = (x + 2 * dx, y, z + 2 * dz)
                    assert (prepare_gap(grid, p, target, policy) is None) == (
                        prepare_gap(cells, p, target, policy) is None
                    )


def test_overlay_does_not_leak_into_the_shared_memo():
    cells = floor_map([(x, z) for x in range(4) for z in range(3)])
    grid = Grid(cells)
    p = (1, 1, 1)
    assert navigation.standable(grid, p) and collision_geometry.standable(grid, p)
    ground = (1, 0, 1)
    overlay = OverlayGrid({ground: {**cells[ground], **AIR}}, grid)
    assert not navigation.standable(overlay, p)
    assert not collision_geometry.standable(overlay, p)
    assert navigation.standable(grid, p) and collision_geometry.standable(grid, p)
