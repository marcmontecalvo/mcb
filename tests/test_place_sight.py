"""The Python twin of the server's place_block face search (BodyBuild.hits)."""

import pytest

from mclab import place_sight as ps

FULL = [[0, 0, 0, 1, 1, 1]]
LILY = [[0.0625, 0, 0.0625, 0.9375, 0.09375, 0.9375]]


def solid(block="minecraft:dirt"):
    return {"block": block, "collision_boxes": FULL}


def water():
    return {"block": "minecraft:water", "fluid": "minecraft:water"}


def seagrass():
    return {"block": "minecraft:seagrass", "fluid": "minecraft:water"}


def pond(depth=2, land_y=63):
    """Land at y=land_y (stand at land_y+1) for x<=0; a pond at x>=1 with its
    floor at land_y-depth-1, water up to land_y-1 and air at land_y."""
    cells = {}
    for x in range(-3, 6):
        for z in range(-3, 4):
            floor = land_y if x <= 0 else land_y - depth - 1
            for y in range(land_y - 6, land_y + 4):
                if y <= floor:
                    cells[x, y, z] = solid()
                elif x >= 1 and y < land_y:
                    cells[x, y, z] = water()
                else:
                    cells[x, y, z] = {"block": "minecraft:air"}
    return cells


def eye_at(x, y, z):
    return (x + 0.5, y + 1.62, z + 0.5)


def test_a_floor_face_in_reach_and_in_view_is_clickable():
    cells = pond(depth=1)  # one water cell: floor at 61, water 62
    assert ps.clickable_from(cells, eye_at(0, 64, 0), (1, 62, 0))


def test_the_pond_bottom_two_below_the_rim_is_out_of_reach_from_the_land():
    # the floor's top face is 4.62 under a standing eye: the 4.5 reach ends above it
    cells = pond(depth=2)
    assert not ps.clickable_from(cells, eye_at(0, 64, 0), (1, 61, 0))


def test_a_face_turned_away_from_the_eye_is_never_clicked():
    # the body stands on the filled column: its east face looks away from the eye
    cells = pond(depth=2)
    cells[1, 61, 0] = cells[1, 62, 0] = solid()
    cells[2, 60, 0] = {"block": "minecraft:barrier"}  # no floor face either
    assert not ps.clickable_from(cells, eye_at(1, 63, 0), (2, 61, 0))


def test_a_filled_row_neighbour_is_clicked_from_behind_the_target():
    cells = pond(depth=2)
    cells[1, 61, 0] = solid()  # the column beside the target along the row
    assert ps.clickable_from(cells, eye_at(0, 64, 1), (1, 61, 1))


def test_one_step_down_onto_a_filled_column_reaches_the_next_pond_bottom():
    cells = pond(depth=2)
    cells[1, 61, 0] = cells[1, 62, 0] = solid()
    assert ps.clickable_from(cells, eye_at(1, 63, 0), (2, 61, 0))


@pytest.mark.parametrize(
    "cover",
    [seagrass, lambda: {"block": "minecraft:lily_pad", "collision_boxes": LILY}],
)
def test_a_pond_plant_in_the_way_hides_the_face(cover):
    # the server clips by OUTLINE: a plant without collision still stops the ray
    cells = pond(depth=1)
    assert ps.clickable_from(cells, eye_at(0, 64, 0), (2, 62, 0))
    for z in (-1, 0, 1):
        cells[1, 63, z] = cells[1, 62, z] = cells[2, 63, z] = cover()
    assert not ps.clickable_from(cells, eye_at(0, 64, 0), (2, 62, 0))


def test_seagrass_in_the_target_cell_hides_every_face_behind_it():
    cells = pond(depth=1)
    cells[1, 62, 0] = seagrass()
    assert not ps.clickable_from(cells, eye_at(0, 64, 0), (1, 62, 0))


def test_water_and_unknown_cells_are_no_support_and_no_obstacle():
    cells = pond(depth=1)
    del cells[1, 61, 0]  # the floor under the target is unknown
    eye = eye_at(2, 64, 0)  # over the pond, facing the bank's side
    assert not ps.clickable_from(cells, eye, (1, 62, 0), sources=[(1, 61, 0)])
    assert ps.clickable_from(cells, eye, (1, 62, 0))  # through the water to the bank
