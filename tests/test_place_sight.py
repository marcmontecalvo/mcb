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


@pytest.mark.parametrize("plant", ["short_grass", "tall_grass", "fern", "dead_bush"])
def test_dry_grass_in_the_target_cell_is_broken_before_the_click(plant):
    # BodyBuild.place destroys a dry, instantly broken, replaceable occupant before
    # its face search: the grass hides nothing (run 20261120T075123420431Z)
    cells = pond(depth=1)
    target = (-1, 63, 0)  # a ground cell of the bank, dug and grown over
    # as the sensor delivers it
    cells[target] = {
        "block": "minecraft:" + plant,
        "collision_boxes": [],
        "replaceable": True,
        "fluid": "minecraft:empty",
    }
    assert ps.takes_block(cells[target])
    assert ps.clickable_from(cells, eye_at(-3, 64, 0), target)


def test_a_flower_in_the_target_cell_still_hides_the_faces_behind_it():
    # not replaceable: the server refuses "occupied", the cell is broken first
    cells = pond(depth=1)
    cells[-1, 63, 0] = tulip = {
        "block": "minecraft:orange_tulip",
        "collision_boxes": [],
        "replaceable": False,
        "fluid": "minecraft:empty",
    }
    assert not ps.takes_block(tulip)
    assert not ps.clickable_from(cells, eye_at(-3, 64, 0), (-1, 63, 0))


@pytest.mark.parametrize(
    ("cell", "takes"),
    [
        (None, True),  # unknown: the server decides
        ({"block": "minecraft:air"}, True),
        (water(), True),
        (seagrass(), True),
        ({"block": "minecraft:snow", "collision_boxes": []}, True),
        ({"block": "minecraft:snow", "collision_boxes": [[0, 0, 0, 1, 0.5, 1]]}, False),
        ({"block": "minecraft:wall_torch"}, False),
        (solid(), False),
        # the sensor's flag wins over the name
        ({"block": "minecraft:short_grass", "replaceable": False}, False),
    ],
)
def test_takes_block(cell, takes):
    assert ps.takes_block(cell) is takes


BOTTOM_HALF = [[0, 0, 0, 1, 0.5, 1]]
TARGET = (0, 64, 0)
WEST = (-1, 64, 0)  # its face toward the target is the plane x = 0


def _upper_half_in_view(source_boxes):
    """A source west of the target and an eye east of it at y 64.62, a bottom slab
    between them: of the source's face only the upper half shows."""
    return {
        WEST: {"block": "minecraft:oak_planks", "collision_boxes": source_boxes},
        (1, 64, 0): {"block": "minecraft:oak_slab", "collision_boxes": BOTTOM_HALF},
    }, eye_at(3, 63, 0)


def test_a_point_off_a_partial_source_is_no_click():
    # The server's ray ends 0.05 past the point: beside a bottom slab, in the air
    # over it, it meets nothing (run 20261120T083111010944Z: "visible" slab sides
    # the server never hit)
    cells, eye = _upper_half_in_view(BOTTOM_HALF)
    assert not ps.clickable_from(cells, eye, TARGET)
    cells, eye = _upper_half_in_view(FULL)
    assert ps.clickable_from(cells, eye, TARGET)


@pytest.mark.parametrize(
    ("state", "clickable"),
    [
        (None, True),
        ("minecraft:oak_slab[type=top,waterlogged=false]", True),
        # the upper half of a side face makes a top slab or stair: 20 of 28
        # wrong_state failures in run 20261120T083111010944Z
        ("minecraft:oak_slab[type=bottom,waterlogged=false]", False),
        ("minecraft:oak_stairs[facing=north,half=bottom,shape=straight]", False),
        ("minecraft:stripped_oak_log[axis=x]", True),
        ("minecraft:stripped_oak_log[axis=y]", False),
    ],
)
def test_only_points_that_give_the_state_count(state, clickable):
    cells, eye = _upper_half_in_view(FULL)
    assert ps.clickable_from(cells, eye, TARGET, state=state) is clickable


STABLE = [
    [0, 0.875, 0, 1, 1, 1],
    [0, 0, 0, 0.125, 1, 0.125],
    [0.875, 0, 0, 1, 1, 0.125],
    [0, 0, 0.875, 0.125, 1, 1],
    [0.875, 0, 0.875, 1, 1, 1],
]


@pytest.mark.parametrize("sensed", [STABLE, []])
def test_scaffolding_hides_what_is_behind_it_only_while_scaffolding_is_held(sensed):
    # ScaffoldingBlock.getShape: a full cube to a player holding scaffolding, else
    # the top and four legs, whatever collision the sensor reported (run
    # 20261120T083111010944Z: three scaffolding clicks through a column, 0 visible)
    cells = {
        (0, 63, 0): solid(),
        (1, 65, 0): {"block": "minecraft:scaffolding", "collision_boxes": sensed},
    }
    eye = eye_at(2, 64, 0)
    assert ps.clickable_from(cells, eye, TARGET, state="minecraft:stone")
    assert not ps.clickable_from(cells, eye, TARGET, state="minecraft:scaffolding")


@pytest.mark.parametrize(
    "cell",
    [
        {
            "block": "minecraft:scaffolding",
            "state": "minecraft:scaffolding[bottom=true]",
        },
        # as the sensor delivers it: properties, no state string
        {"block": "minecraft:scaffolding", "properties": {"bottom": "true"}},
    ],
)
def test_an_unstable_scaffolding_has_a_bottom_plate(cell):
    assert ps._SCAFFOLD_PLATE in ps.outline(cell)
    cell = {**cell, "state": None, "properties": {"bottom": "false"}}
    assert ps._SCAFFOLD_PLATE not in ps.outline(cell)


def test_water_and_unknown_cells_are_no_support_and_no_obstacle():
    cells = pond(depth=1)
    del cells[1, 61, 0]  # the floor under the target is unknown
    eye = eye_at(2, 64, 0)  # over the pond, facing the bank's side
    assert not ps.clickable_from(cells, eye, (1, 62, 0), sources=[(1, 61, 0)])
    assert ps.clickable_from(cells, eye, (1, 62, 0))  # through the water to the bank
