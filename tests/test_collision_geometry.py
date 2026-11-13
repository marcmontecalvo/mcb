from maps import floor_map

from mclab.collision_geometry import clear_body, clear_column, standable


def slab_map():
    cells = floor_map([(x, z) for x in range(-1, 4) for z in range(-1, 2)])
    for z in range(-1, 2):
        cells[1, 0, z].update(
            block="minecraft:stone_slab", collision_boxes=[[0, 0, 0, 1, 0.5, 1]]
        )
    return cells


def test_off_center_body_checks_partial_side_collision_and_unknown_neighbor():
    cells = floor_map([(x, z) for x in range(4) for z in range(2)])
    cells[1, 1, 0].update(
        block="minecraft:glass_pane", collision_boxes=[[0.9, 0, 0, 1, 1, 1]]
    )
    assert clear_column(cells, 1, 0, 1, 2.8)
    assert not clear_body(cells, 1.75, 0.5, 1, 2.8)
    cells[1, 1, 0].update(block="minecraft:air", collision_boxes=[])
    assert clear_body(cells, 1.75, 0.5, 1, 2.8)
    del cells[2, 2, 0]
    assert not clear_body(cells, 1.75, 0.5, 1, 2.8)


def test_stair_support_and_waterlogged_hazard_rejection():
    cells = slab_map()
    cells[1, 0, 0].update(
        block="minecraft:stone_stairs",
        collision_boxes=[[0, 0, 0, 1, 0.5, 1], [0, 0.5, 0.5, 1, 1, 1]],
    )
    assert standable(cells, (1, 1, 0))
    assert not standable(cells, (1, 0.5, 0))
    cells[1, 0, 0]["fluid"] = "minecraft:water"
    assert not standable(cells, (1, 1, 0))
    cells[1, 0, 0].pop("fluid")
    cells[1, 1, 0]["block"] = "minecraft:fire"
    assert not standable(cells, (1, 1, 0))
