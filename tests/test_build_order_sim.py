"""Offline build-order measurement: the planner driven over a template, flat ground."""

import json

import pytest

from mclab import build_order_sim as sim
from mclab import build_plan as bp
from mclab.companion_jobs import load_vanilla_pack
from mclab.paths import ROOT

SCHEMATICS = ROOT / "tests/fixtures/schematics"


@pytest.fixture(scope="module")
def blocks():
    return load_vanilla_pack().blocks


def _template(name, origin=(0, 1, 0)):
    build = json.loads((SCHEMATICS / name).read_text())
    return bp.Template.from_build(build, origin, 0)


def test_the_small_house_is_built_whole_and_measured(blocks):
    result = sim.simulate(_template("small-house.build.json"), blocks)
    assert result.missing == 0
    assert result.placed == result.cells
    assert result.walks > 0 and result.walk_distance > 0
    assert result.climb >= 0 and result.descent >= 0
    # every walk is logged, so the swings can be found again
    assert len(result.stances) == result.walks + 1


def test_a_stance_stands_on_something_with_room_for_the_body(blocks):
    world = sim.World(ground_y=0)
    assert sim.standable(world, (0, 1, 0), blocks)  # on the ground
    assert not sim.standable(world, (0, 0, 0), blocks)  # inside the ground
    assert not sim.standable(world, (0, 3, 0), blocks)  # in the air
    world.set((0, 3, 0), "minecraft:oak_planks")
    assert sim.standable(world, (0, 4, 0), blocks)
    assert not sim.standable(world, (0, 2, 0), blocks)  # head in the planks


def test_reach_is_measured_from_the_eye():
    assert sim.in_reach((0, 1, 0), (0, 5, 0))  # 4 up from the feet, 2.4 from the eye
    assert not sim.in_reach((0, 1, 0), (5, 1, 0))
    assert sim.in_reach((0, 1, 0), (4, 1, 0))


def test_the_report_names_the_big_swings():
    # up a floor and back down again between two placements is a swing
    stances = [(0, 1, 0), (0, 9, 0), (0, 1, 0), (1, 1, 0)]
    m = sim.measure(stances)
    assert m["walks"] == 3
    assert m["climb"] == 8 and m["descent"] == 8
    assert m["swings"] == 2  # two level changes of 3 or more
