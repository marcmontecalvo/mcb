from maps import floor_map

from mclab.collision_geometry import (
    clear_body,
    clear_column,
    edit_stance,
    standable,
)


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


def test_a_top_slab_or_a_stair_high_half_is_a_floor_edits_start_from():
    cells = floor_map([(x, z) for x in range(3) for z in range(1)])
    cells[1, 0, 0].update(
        block="minecraft:birch_slab", collision_boxes=[[0, 0.5, 0, 1, 1, 1]]
    )
    assert edit_stance(cells, (1, 1, 0))
    cells[1, 0, 0].update(collision_boxes=[[0, 0, 0, 1, 0.5, 1]])  # bottom slab
    assert not edit_stance(cells, (1, 1, 0))
    cells[1, 0, 0].update(
        block="minecraft:oak_stairs",
        collision_boxes=[[0, 0, 0, 1, 0.5, 1], [0, 0.5, 0, 1, 1, 0.5]],
    )
    assert edit_stance(cells, (1, 1, 0))


def test_scaffolding_in_the_feet_cell_does_not_block_a_stance():
    # Run 20261117T214048223018Z: the sensor reports scaffolding with the shape a
    # body above it meets (legs and top plate). In or beside it vanilla gives no
    # collision, yet the ledge the body stood on read as no stance and every plan
    # was "unreachable, 0 expanded" until the run ended trapped.
    cells = floor_map([(x, z) for x in range(3) for z in range(3)])
    cells[1, 1, 1].update(
        block="minecraft:scaffolding",
        collision_boxes=[[0, 0, 0, 0.125, 1, 0.125], [0, 0.875, 0, 1, 1, 1]],
    )
    assert standable(cells, (1, 1, 1))
    assert clear_body(cells, 1.1, 1.0, 1, 2.8)


def test_any_real_shape_topped_within_its_cell_is_a_floor_but_a_fence_is_not():
    # Run 20261117T221236234752Z ended trapped on a lily pad: only cubes, slabs,
    # stairs and scaffolding were floors, so the body had no stance at all.
    cells = floor_map([(x, z) for x in range(3) for z in range(3)])
    cells[1, 1, 1] = {
        "position": [1, 1, 1],
        "block": "minecraft:lily_pad",
        "collision_boxes": [[0, 0, 0, 1, 0.09375, 1]],
    }
    assert standable(cells, (1, 1.09375, 1))
    cells[1, 1, 1] = {
        "position": [1, 1, 1],
        "block": "minecraft:oak_fence",
        "collision_boxes": [[0.375, 0, 0.375, 0.625, 1.5, 0.625]],
    }
    assert not standable(cells, (1, 2.5, 1))
