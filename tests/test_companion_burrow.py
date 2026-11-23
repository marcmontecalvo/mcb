"""Burrow: dig down three and seal the hole above (COMPANION_DEFENSE_PLAN section 4)."""

import pytest

from mclab import companion_burrow as cb
from mclab.build_runner import Result
from mclab.companion_jobs import load_vanilla_pack

M = "minecraft:"
FEET = (0, 64, 0)
SOLID = {"block": M + "dirt", "collision_boxes": [[0, 0, 0, 1, 1, 1]]}
AIR = {"block": M + "air", "collision_boxes": []}
WATER = {"block": M + "water", "collision_boxes": []}


@pytest.fixture(scope="module")
def k():
    return load_vanilla_pack()


def ground(top=63, bottom=56, block="dirt", radius=2):
    """Solid `block` from `bottom` to `top`, air above, round the origin."""
    cells = {}
    for x in range(-radius, radius + 1):
        for z in range(-radius, radius + 1):
            for y in range(bottom, 70):
                cell = {**SOLID, "block": M + block} if y <= top else AIR
                cells[(x, y, z)] = cell
    return cells


def test_plans_three_digs_and_a_seal_above_the_head(k):
    plan = cb.plan_burrow(ground(), FEET, {M + "cobblestone": 3}, k)
    assert isinstance(plan, cb.Burrow)
    assert plan.digs == ((0, 63, 0), (0, 62, 0), (0, 61, 0))
    assert plan.seal == (0, 63, 0)
    assert plan.item == M + "cobblestone"
    assert plan.feet == (0, 61, 0)


def test_the_dug_dirt_is_the_seal_when_no_block_is_held(k):
    plan = cb.plan_burrow(ground(), FEET, {}, k)
    assert plan.item == M + "dirt"


def test_grass_gives_dirt(k):
    cells = ground()
    cells[(0, 63, 0)] = {**SOLID, "block": M + "grass_block"}
    assert cb.plan_burrow(cells, FEET, {}, k).item == M + "dirt"


def test_a_falling_block_or_a_station_is_never_the_seal(k):
    plan = cb.plan_burrow(
        ground(), FEET, {M + "sand": 9, M + "crafting_table": 1, M + "dirt": 1}, k
    )
    assert plan.item == M + "dirt"


@pytest.mark.parametrize(
    ("cell", "why"),
    [
        ((1, 62, 0), "wall"),  # beside the head: a mob walks in
        ((0, 61, -1), "wall"),  # beside the feet
        ((0, 60, 0), "floor"),  # a cave below
    ],
)
def test_an_open_side_or_floor_is_no_shelter(k, cell, why):
    cells = ground()
    cells[cell] = AIR
    assert why in cb.plan_burrow(cells, FEET, {}, k)


def test_water_beside_the_hole_is_no_shelter(k):
    cells = ground()
    cells[(1, 61, 0)] = WATER
    assert "wall" in cb.plan_burrow(cells, FEET, {}, k)


def test_unknown_ground_is_not_dug_blind(k):
    cells = ground()
    del cells[(0, 62, 0)]
    assert "unknown" in cb.plan_burrow(cells, FEET, {}, k)


def test_stone_needs_a_pickaxe_to_be_quick(k):
    stone = ground(block="stone")
    assert "slow" in cb.plan_burrow(stone, FEET, {}, k)
    plan = cb.plan_burrow(stone, FEET, {M + "stone_pickaxe": 1}, k)
    assert plan.item == M + "cobblestone"
    assert plan.tools == (M + "stone_pickaxe",) * 3


def test_bedrock_is_never_dug(k):
    cells = ground()
    cells[(0, 61, 0)] = {**SOLID, "block": M + "bedrock"}
    assert "slow" in cb.plan_burrow(cells, FEET, {}, k)


def test_no_seal_block_at_all_is_no_shelter(k):
    # sand would fall into the hole; no other block held
    sand = ground(block="sand")
    for y in range(56, 61):
        for x in range(-2, 3):
            for z in range(-2, 3):
                sand[(x, y, z)] = SOLID
    assert "seal" in cb.plan_burrow(sand, FEET, {}, k)


def test_the_seal_needs_a_side_to_be_placed_against(k):
    cells = ground()
    for side in ((1, 63, 0), (-1, 63, 0), (0, 63, 1), (0, 63, -1)):
        cells[side] = AIR
    assert "seal" in cb.plan_burrow(cells, FEET, {M + "dirt": 1}, k)


# --- running it ---------------------------------------------------------------------


class Body:
    """Digs fall the body one block; place puts the seal."""

    def __init__(self, fail=None):
        self.feet = FEET
        self.calls = []
        self.fail = fail or {}

    def position(self):
        return self.feet

    def break_block(self, pos, tool=None, drops_optional=False):
        self.calls.append(("break", pos, tool))
        if self.fail.get(("break", pos)):
            return Result(False, self.fail[("break", pos)])
        self.feet = pos
        return Result(True)

    def place_block(self, pos, state, item):
        self.calls.append(("place", pos, item))
        return Result(not self.fail.get(("place", pos)), self.fail.get(("place", pos)))

    def wait_ticks(self, n):
        self.calls.append(("wait", n))


def _plan(k):
    return cb.plan_burrow(ground(), FEET, {M + "cobblestone": 2}, k)


def test_dig_and_seal(k):
    body = Body()
    assert cb.dig_in(body, _plan(k)).ok
    acts = [c for c in body.calls if c[0] != "wait"]
    assert acts == [
        ("break", (0, 63, 0), None),
        ("break", (0, 62, 0), None),
        ("break", (0, 61, 0), None),
        ("place", (0, 63, 0), M + "cobblestone"),
    ]


def test_a_failed_dig_stops_and_says_why(k):
    body = Body(fail={("break", (0, 62, 0)): "entity_in_way"})
    result = cb.dig_in(body, _plan(k))
    assert (result.ok, result.reason) == (False, "entity_in_way")
    assert not any(c[0] == "place" for c in body.calls)


def test_a_body_that_did_not_drop_is_not_sealed_in(k):
    body = Body()
    body.break_block = lambda pos, tool=None, drops_optional=False: Result(True)
    result = cb.dig_in(body, _plan(k))
    assert (result.ok, result.reason) == (False, "did_not_drop")


def test_dig_out_breaks_the_seal(k):
    body = Body()
    assert cb.dig_out(body, _plan(k)).ok
    assert body.calls == [("break", (0, 63, 0), None)]
