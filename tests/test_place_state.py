"""Which click gives the requested block state (vanilla getStateForPlacement)."""

import pytest

from mclab import place_state as pst

BOTTOM_SLAB = "minecraft:oak_slab[type=bottom,waterlogged=false]"
TOP_SLAB = "minecraft:birch_slab[type=top,waterlogged=false]"
TOP_STAIRS = (
    "minecraft:oak_stairs[facing=south,half=top,shape=straight,waterlogged=false]"
)
BOTTOM_STAIRS = "minecraft:oak_stairs[facing=north,half=bottom,shape=straight]"


@pytest.mark.parametrize(
    ("state", "face", "frac", "gives"),
    [
        # a slab is bottom on a floor, top under a ceiling, and on a side face by
        # the half of the face clicked (SlabBlock: y - pos.y > 0.5 is top)
        (BOTTOM_SLAB, "up", 0.0, True),
        (BOTTOM_SLAB, "down", 1.0, False),
        (BOTTOM_SLAB, "east", 0.25, True),
        (BOTTOM_SLAB, "east", 0.75, False),
        (TOP_SLAB, "down", 1.0, True),
        (TOP_SLAB, "up", 0.0, False),
        (TOP_SLAB, "south", 0.94, True),
        (TOP_SLAB, "south", 0.06, False),
        # the centre line is a float away from either half: no promise either way
        (BOTTOM_SLAB, "east", 0.5, False),
        (TOP_SLAB, "east", 0.5, False),
        # a double slab is two clicks, either half first (BodyBuild.placeHalfSlab)
        ("minecraft:oak_slab[type=double]", "east", 0.75, True),
        ("minecraft:oak_slab[type=double]", "up", 0.0, True),
        # stairs: the same halves; facing comes from the yaw, which the server turns
        (TOP_STAIRS, "north", 0.75, True),
        (TOP_STAIRS, "north", 0.25, False),
        (TOP_STAIRS, "down", 1.0, True),
        (BOTTOM_STAIRS, "up", 0.0, True),
        (BOTTOM_STAIRS, "west", 0.75, False),
    ],
)
def test_half_and_type_follow_the_face_and_height_clicked(state, face, frac, gives):
    assert pst.click_gives(state, face, frac) is gives


@pytest.mark.parametrize(
    ("state", "face", "frac", "gives"),
    [
        # on a side face a trapdoor takes the face's direction and the half clicked
        ("minecraft:oak_trapdoor[facing=east,half=top,open=false]", "east", 0.75, True),
        (
            "minecraft:oak_trapdoor[facing=east,half=top,open=false]",
            "east",
            0.25,
            False,
        ),
        (
            "minecraft:oak_trapdoor[facing=east,half=top,open=false]",
            "west",
            0.75,
            False,
        ),
        # on a floor or ceiling the half is the face's and the facing the yaw's
        ("minecraft:oak_trapdoor[facing=north,half=bottom]", "up", 0.0, True),
        ("minecraft:oak_trapdoor[facing=north,half=bottom]", "down", 1.0, False),
        ("minecraft:oak_trapdoor[facing=north,half=top]", "down", 1.0, True),
    ],
)
def test_a_trapdoor_on_a_wall_faces_away_from_it(state, face, frac, gives):
    assert pst.click_gives(state, face, frac) is gives


@pytest.mark.parametrize(
    ("state", "face", "gives"),
    [
        # a pillar's axis is the clicked face's (RotatedPillarBlock)
        ("minecraft:stripped_oak_log[axis=y]", "up", True),
        ("minecraft:stripped_oak_log[axis=y]", "down", True),
        ("minecraft:stripped_oak_log[axis=y]", "east", False),
        ("minecraft:oak_log[axis=x]", "west", True),
        ("minecraft:oak_log[axis=x]", "north", False),
        ("minecraft:iron_chain[axis=z]", "south", True),
        # a hopper faces away from the face clicked, down from a floor or ceiling
        ("minecraft:hopper[enabled=true,facing=west]", "east", True),
        ("minecraft:hopper[enabled=true,facing=west]", "west", False),
        ("minecraft:hopper[enabled=true,facing=west]", "up", False),
        ("minecraft:hopper[enabled=true,facing=down]", "up", True),
        ("minecraft:hopper[enabled=true,facing=down]", "down", True),
        ("minecraft:hopper[enabled=true,facing=down]", "north", False),
    ],
)
def test_axis_and_hopper_facing_follow_the_face(state, face, gives):
    assert pst.click_gives(state, face, 0.25) is gives


@pytest.mark.parametrize(
    "state",
    [
        None,
        "minecraft:stone",
        "minecraft:scaffolding",
        "minecraft:furnace[facing=north,lit=false]",  # from the yaw
        "minecraft:birch_door[facing=east,half=lower,hinge=right,open=false]",
    ],
)
def test_a_state_no_click_decides_takes_any_click(state):
    for face in ("up", "down", "north", "east"):
        assert pst.click_gives(state, face, 0.75)
        assert pst.click_gives(state, face, 0.25)
