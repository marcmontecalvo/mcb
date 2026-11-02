import pytest

from mclab.companion_body import StuckGuard
from mclab.companion_jobs import load_vanilla_pack
from mclab.gather_dig import Digger
from mclab.gather_fake import FakeGatherBody
from mclab.gather_plan import plan
from mclab.gather_runner import GatherState, _Exec, run_gather

K = load_vanilla_pack()
M = "minecraft:"
BOX = [(x, z) for x in range(-12, 45) for z in range(-8, 9)]


def layered(dirt_to: int, stone_from: int | None = None) -> dict:
    """Ground top at y=-1: dirt down to ``dirt_to``, stone from ``stone_from`` down."""
    world = {}
    for x, z in BOX:
        for y in range(-1, dirt_to - 1, -1):
            stone = stone_from is not None and y <= stone_from
            world[(x, y, z)] = M + ("stone" if stone else "dirt")
    return world


def tracked(body):
    broken = []
    original = body.break_block

    def wrapper(pos, tool=None):
        result = original(pos, tool)
        if result.ok:
            broken.append(pos)
        return result

    body.break_block = wrapper
    return broken


def test_empty_inventory_to_stone_pickaxe_digs_down_for_cobblestone():
    world = layered(-30, stone_from=-10)  # stone too deep to walk to: dig down
    world |= {(-3, y, z): M + "oak_log" for y in (0, 1) for z in range(-6, 7)}
    body = FakeGatherBody(K, world, pos=(0, 0, 0))
    broken = tracked(body)
    targets = {M + "stone_pickaxe": 1}

    report = run_gather(
        plan(targets, body.inventory(), K, starter_tools=True), body, K, targets
    )

    inv = body.inventory()
    assert report.stop == "done", report
    for tool in ("pickaxe", "axe", "shovel"):
        assert inv.get(M + "stone_" + tool) == 1, inv
    assert inv.get(M + "wooden_axe") and inv.get(M + "wooden_shovel")
    assert min(y for _, y, _ in broken) <= -10  # reached the stone
    assert sum(1 for p in broken if body.w[p] == M + "air" and p[1] <= -10) >= 7


def test_stair_stops_at_liquid_without_breaking_it():
    for liquid in ("water", "lava"):
        world = layered(-30) | {(3, -3, 0): M + liquid}
        body = FakeGatherBody(K, world, {M + "wooden_pickaxe": 1}, pos=(0, 0, 0))
        broken = tracked(body)
        digger = Digger(_Exec(body, K, GatherState(), 64))

        digger.run(M + "cobblestone", 7)

        assert digger.reason == "liquid"
        assert (3, -3, 0) not in broken and body.w[(3, -3, 0)] == M + liquid
        assert body.pos[1] > -3


def test_stair_never_digs_unknown_cells_or_straight_down():
    body = FakeGatherBody(K, layered(-30), default=None, pos=(0, 0, 0))
    broken = tracked(body)
    digger = Digger(_Exec(body, K, GatherState(), 64))

    assert not digger.run(M + "cobblestone", 7)
    assert digger.reason == "unknown" and not broken

    body = FakeGatherBody(K, layered(-30), pos=(0, 0, 0))
    broken = tracked(body)
    Digger(_Exec(body, K, GatherState(), 64)).run(M + "cobblestone", 7)
    assert all(p[0] != 0 or p[2] != 0 for p in broken)  # never under its own feet


def test_stair_stops_at_the_depth_cap():
    body = FakeGatherBody(K, layered(-60), pos=(0, 0, 0))
    digger = Digger(_Exec(body, K, GatherState(), 64))

    digger.run(M + "cobblestone", 7)

    assert digger.reason == "depth_cap" and body.pos[1] == -16


def test_drops_out_of_reach_are_collected_by_stepping_onto_them(monkeypatch):
    monkeypatch.setattr("mclab.gather_fake.PICKUP", 0.8)  # walking by picks up nothing
    world = layered(-30, stone_from=-1) | {
        (x, y, z): M + "stone" for x, z in BOX for y in (0, 1)
    }  # a solid mass: the head-height drops land out of reach of the new step
    body = FakeGatherBody(K, world, {M + "wooden_pickaxe": 1}, pos=(0, 0, 0))
    broken = tracked(body)
    digger = Digger(_Exec(body, K, GatherState(), 64))

    digger.run(M + "cobblestone", 7)

    assert digger.reason == "done"
    assert body.inventory()[M + "cobblestone"] == len(broken) >= 7
    assert ("wait", 20) in body.log  # waited, then stepped onto the leftover drops


def test_stuck_guard_fails_a_goto_that_does_not_move():
    guard = StuckGuard(ticks=200)
    assert not guard.stuck((1.0, 5.0, 1.0), 0)
    assert not guard.stuck((1.2, 5.0, 1.1), 199)
    assert guard.stuck((1.2, 5.0, 1.1), 200)
    assert not guard.stuck((4.0, 5.0, 1.0), 201)  # moved: the clock restarts
    assert not guard.stuck((4.0, 5.0, 1.0), 400)
    assert guard.stuck((4.0, 5.0, 1.0), 401)


@pytest.mark.parametrize("has_pickaxe", [False, True])
def test_starter_tools_plan(has_pickaxe):
    inv = {M + "iron_pickaxe": 1} if has_pickaxe else {}
    result = plan({M + "stone_pickaxe": 1}, inv, K, starter_tools=True)
    made = {getattr(t, "recipe_id", "") for t in result.tasks}
    assert not result.missing
    assert (M + "wooden_axe" in made) == (not has_pickaxe)
    assert M + "stone_axe" in made and M + "stone_shovel" in made


def test_stair_does_not_step_under_gravel_over_the_dug_column():
    # first +x step digs (1, 2..-1, 0); gravel at (1, 3, 0) would fall into it
    world = layered(-30) | {(1, 3, 0): M + "gravel"}
    body = FakeGatherBody(K, world, pos=(0, 0, 0))
    broken = tracked(body)

    Digger(_Exec(body, K, GatherState(), 64)).run(M + "cobblestone", 7)

    assert (1, -1, 0) not in broken


def test_dirt_broken_by_the_stair_is_not_counted_as_mined_stone():
    from mclab.gather_tasks import Gather

    world = layered(-30) | {(x, -4, z): M + "water" for x, z in BOX}  # no stone
    body = FakeGatherBody(K, world, {M + "wooden_pickaxe": 1}, pos=(0, 0, 0))
    task = Gather(M + "stone", 3, "pickaxe", "wood", M + "cobblestone", 3)

    result = _Exec(body, K, GatherState(), 64).gather(task)

    assert not body.inventory().get(M + "cobblestone")
    assert not result.ok


def test_surface_stone_touching_lava_is_never_mined():
    from mclab.gather_tasks import Gather

    world = layered(-30) | {(4, -1, 0): M + "stone", (5, -1, 0): M + "lava"}
    body = FakeGatherBody(K, world, {M + "wooden_pickaxe": 1}, pos=(0, 0, 0))
    broken = tracked(body)
    task = Gather(M + "stone", 3, "pickaxe", "wood", M + "cobblestone", 3)

    _Exec(body, K, GatherState(), 64).gather(task)

    assert (4, -1, 0) not in broken and body.w[(5, -1, 0)] == M + "lava"


def test_planner_digs_to_buried_stone_when_digging_is_allowed():
    # deeper than NEAR_DY: the fake shows buried stone as minable, the real body not
    world = layered(-30, stone_from=-8)
    world |= {(-3, y, z): M + "oak_log" for y in (0, 1) for z in range(-6, 7)}
    body = FakeGatherBody(K, world, pos=(0, 0, 0), can_dig=True)
    targets = {M + "stone_pickaxe": 1}

    report = run_gather(
        plan(targets, body.inventory(), K, starter_tools=True), body, K, targets
    )

    assert report.stop == "done", report
    digs = [step[1] for step in body.log if step[0] == "dig_to"]
    assert digs and digs[0][1] == -7  # feet on the top of the buried stone
    assert body.inventory().get(M + "stone_axe") == 1


def test_buried_tops_skip_columns_under_liquid_and_far_or_deep_ones():
    from mclab.gather_runner import buried_tops

    world = layered(-30, stone_from=-5)
    world[(2, -4, 0)] = M + "water"  # stone at (2, -5, 0) lies under water
    body = FakeGatherBody(K, world, pos=(0, 0, 0))

    tops = buried_tops(body, M + "stone", (0, 0, 0))

    assert (2, -5, 0) not in tops and (0, -5, 0) in tops
    assert all(p[1] == -5 for p in tops)  # only the top of each column
    assert all(abs(p[0]) <= 24 for p in tops)


def test_planner_digs_with_any_tier_of_the_tool():
    from mclab.terrain_steps import Inventory, hotbar

    rows = [
        {"item": M + "wooden_shovel", "count": 1, "slot": 3},
        {"item": M + "stone_shovel", "count": 1, "slot": 5},
        {"item": M + "dirt", "count": 9, "slot": 1},
    ]
    for inv in (rows, Inventory(rows)):
        assert hotbar(inv, M + "iron_shovel") == 5  # best tier held
        assert hotbar(inv, M + "iron_pickaxe") is None
        assert hotbar(inv, M + "dirt") == 1  # materials unchanged
