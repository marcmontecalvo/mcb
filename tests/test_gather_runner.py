from pathlib import Path

from mclab.build_runner import Result
from mclab.gather_fake import FakeGatherBody
from mclab.gather_plan import plan
from mclab.gather_runner import run_gather
from mclab.gather_tasks import (
    Deposit,
    Hunt,
    PlaceContainer,
    PlaceStation,
    PlanResult,
    Shear,
    Withdraw,
)
from mclab.knowledge import load_pack

ROOT = Path(__file__).parent.parent
K = load_pack(
    ROOT / "tests/fixtures/knowledge/mini",
    transforms=ROOT / "knowledge/transforms/vanilla-26.3.json",
    foods=ROOT / "knowledge/sources/foods-26.3.json",
)
M = "minecraft:"


def floor():
    return {(x, -1, z): M + "dirt" for x in range(-6, 40) for z in range(-6, 40)}


def trees_and_stone(n_logs=8, n_stone=8, far=0):
    world = floor()
    world |= {(10 + far, 0, i * 2): M + "oak_log" for i in range(n_logs)}
    world |= {(i * 2, 0, 10 + far): M + "stone" for i in range(n_stone)}
    return world


def run(targets, body, **kw):
    kw.setdefault("starter_tools", False)  # the mini pack has no shovel recipes
    return run_gather(plan(targets, body.inventory(), K), body, K, targets, **kw)


def test_stone_pickaxe_from_an_empty_inventory():
    body = FakeGatherBody(K, trees_and_stone())

    report = run({M + "stone_pickaxe": 1}, body)

    assert report.stop == "done", report
    assert body.inventory()[M + "stone_pickaxe"] == 1
    assert report.replans >= 5 and not report.failures
    assert report.state.stations["crafting_table"] is not None
    assert body.w[report.state.stations["crafting_table"]] == M + "crafting_table"


def test_iron_pickaxe_smelts_in_a_placed_furnace():
    world = trees_and_stone(n_logs=14, n_stone=12)
    world.update({(i, 0, -10): M + "iron_ore" for i in range(4)})
    world.update({(i, 0, -14): M + "coal_ore" for i in range(2)})
    body = FakeGatherBody(K, world)

    report = run({M + "iron_pickaxe": 1}, body)

    assert report.stop == "done", report
    assert body.inventory()[M + "iron_pickaxe"] == 1
    assert "furnace" in report.state.stations


def test_unknown_blocks_are_found_by_expanding_rings_and_capped():
    world = {(32, 0, 0): M + "oak_log", (32, 0, 2): M + "oak_log"}
    body = FakeGatherBody(K, world, sight=8)

    found = run({M + "oak_planks": 4}, body, explore_cap=48)

    assert found.stop == "done", found
    assert ("walk", (32, 0, 0)) in body.log
    nothing = FakeGatherBody(K, {}, sight=8)
    lost = run({M + "oak_planks": 4}, nothing, explore_cap=32, max_failures=2)
    assert lost.stop == "failed"
    assert all(r == "not_found" for _, r in lost.failures)
    assert (
        max(abs(c) for call in nothing.log if call[0] == "walk" for c in call[1]) <= 32
    )
    # cap 0 (a build's refill) never leaves the site
    home = FakeGatherBody(K, {}, sight=8)
    run({M + "oak_planks": 4}, home, explore_cap=0, max_failures=2)
    assert not [call for call in home.log if call[0] == "walk"]


def test_a_wrong_stance_retries_another_stance():
    world = trees_and_stone()
    body = FakeGatherBody(K, world)
    original = body.break_block
    calls = []

    def flaky(pos, tool=None):
        calls.append(pos)
        return (
            original(pos, tool)
            if len(calls) > 1
            else __import__("mclab.build_runner", fromlist=["Result"]).Result(
                False, "out_of_reach"
            )
        )

    body.break_block = flaky
    report = run({M + "oak_planks": 4}, body)

    assert report.stop == "done"
    gotos = [c for c in body.log if c[0] == "goto"]
    assert len(gotos) >= 2


def test_shearing_and_hunting_collect_the_drops_and_reapproach():
    sheep = {"uuid": "s1", "type": M + "sheep", "position": [5.5, 0, 5.5]}
    cow = {"uuid": "c1", "type": M + "cow", "position": [-6.5, 0, 4.5]}
    body = FakeGatherBody(
        K, {}, {M + "shears": 1}, entities=[sheep, cow], drift={"c1": 1}
    )
    tasks = PlanResult((Shear(M + "sheep", 1), Hunt(M + "cow", 1)), (), 0.0)

    report = run_gather(tasks, body, K)

    assert report.stop == "done", report
    assert body.inventory()[M + "white_wool"] == 2
    assert body.inventory()[M + "beef"] >= 1
    walks = [c for c in body.log if c[0] == "walk"]
    assert len(walks) >= 4  # approach, collect, re-approach after drift, collect


def test_eats_when_hungry_and_holding_food():
    body = FakeGatherBody(K, trees_and_stone(), {M + "cooked_beef": 2}, food=12)

    report = run({M + "oak_planks": 4}, body)

    assert report.ate >= 1
    assert body.food() > 12


def test_adds_a_food_target_when_starving_without_food():
    cow = {"uuid": "c1", "type": M + "cow", "position": [3.5, 0, 3.5]}
    body = FakeGatherBody(
        K, trees_and_stone(), {M + "wooden_sword": 1}, entities=[cow], food=8
    )

    report = run({M + "oak_planks": 4}, body)

    assert report.food_targets
    assert ("Hunt:" + M + "cow") in report.done[-1]
    assert report.ate >= 1  # the raw beef from the hunt is eaten at once


def test_stops_after_consecutive_failures_with_a_report():
    body = FakeGatherBody(K, trees_and_stone(n_stone=0))

    report = run({M + "stone_pickaxe": 1}, body, max_failures=2, explore_cap=16)

    assert report.stop == "failed"
    assert len(report.failures) == 2


def test_containers_record_position_and_contents():
    body = FakeGatherBody(K, floor(), {M + "chest": 1, M + "oak_log": 5})
    tasks = PlanResult(
        (
            PlaceContainer("chest"),
            Deposit(((M + "oak_log", 3),)),
            Withdraw(((M + "oak_log", 1),)),
        ),
        (),
        0.0,
    )

    report = run_gather(tasks, body, K)

    assert report.stop == "done", report
    (pos,) = report.state.containers
    assert report.state.containers[pos] == {M + "oak_log": 2}
    assert body.inventory()[M + "oak_log"] == 3


def test_a_run_that_collects_nothing_stops_stalled():
    # no trees anywhere: exploring finds nothing, and the watchdog ends the run
    body = FakeGatherBody(K, floor())
    now = [0.0]

    def clock():
        now[0] += 10.0  # every check is ten seconds later
        return now[0]

    report = run({M + "oak_planks": 4}, body, stall_s=120, clock=clock)

    assert report.stop == "stalled", report
    assert now[0] < 400  # ended within a few checks of the limit, not after the rings


def test_an_unreachable_patch_is_skipped_as_a_whole():
    # near shore patch (no stance anywhere in it), dry sand further away
    world = floor()
    shore = [(4 + i, 0, 4) for i in range(4)]
    dry = [(20 + i, 0, 20) for i in range(4)]
    world |= {p: M + "sand" for p in shore + dry}
    body = FakeGatherBody(K, world)
    real_goto, tried = body.goto_stance, []

    def goto(target, reach, avoid=()):
        if tuple(target) in shore:
            tried.append(tuple(target))
            return False
        return real_goto(target, reach, avoid)

    body.goto_stance = goto

    report = run({M + "sand": 2}, body)

    assert report.stop == "done", report
    assert len(tried) == 1  # one try, then the whole patch is skipped


def test_explore_rings_walk_the_surface_not_the_origin_plane():
    # large-house attempt 6 (20261107T031538873657Z): the origin was recorded while
    # the bot stood at feet y=57, so every ring point was 4-18 blocks under the
    # terrain it walked to, buried in solid ground
    world = {
        (x, y, z): M + "dirt"
        for x in range(-48, 49)
        for z in range(-48, 49)
        for y in range(0, 6)
    }
    world |= {(x, 6, z): M + "oak_log" for x in (30, 31) for z in (4, 5)}
    body = FakeGatherBody(K, world, pos=(0, -5, 0), sight=8)

    report = run({M + "oak_planks": 4}, body, explore_cap=32)

    assert report.stop == "done", report
    walked = [c[1] for c in body.log if c[0] == "walk"]
    rings = [
        p
        for p in walked
        if p[0] * p[0] + p[2] * p[2] > 64  # ignore the initial short collect walks
    ]
    assert rings and all(y == 6 for _, y, _ in rings)


def test_a_station_cell_without_support_rotates_to_the_next_free_cell():
    # run 20261106T182245230212Z: PlaceStation ended the task because the body
    # refused no_support_face on the one cell free_spot kept re-picking
    body = FakeGatherBody(K, floor(), {M + "crafting_table": 1})
    original, first = body.place_block, []

    def refuse_once(pos, state, item):
        if not first:
            first.append(tuple(pos))
            return Result(False, "no_support_face")
        return original(pos, state, item)

    body.place_block = refuse_once
    tasks = PlanResult((PlaceStation("crafting_table"),), (), 0.0)

    report = run_gather(tasks, body, K)

    assert report.stop == "done", report
    assert report.state.stations["crafting_table"] not in first
    assert first  # the refused cell was actually tried


def test_station_returns_to_worksite_from_one_wide_mine_staircase():
    # Iron run 20261112T140455643180Z ended in a narrow, unsupported shaft;
    # the crafting table was on the surface above the entrance.
    from mclab.gather_runner import GatherState

    table = (59, 71, -74)
    world = {(x, 67, z): M + "stone" for x in range(57, 62) for z in range(-76, -71)}
    world.update({(58, 67, -74): M + "air", (59, 67, -74): M + "air"})
    world[(58, 66, -74)] = M + "air"  # open stair beneath the adjacent cell
    world.update(
        {(x, 70, z): M + "stone" for x in range(57, 62) for z in range(-76, -71)}
    )
    world[table] = M + "crafting_table"
    body = FakeGatherBody(K, world, {M + "furnace": 1})
    body.pos = (59, 67, -74)
    state = GatherState(stations={"crafting_table": table})

    report = run_gather(
        PlanResult((PlaceStation("furnace"),), (), 0.0), body, K, state=state
    )

    assert report.stop == "done"
    assert ("walk", table) in body.log
    assert state.stations["furnace"][1] == 71
    assert body.w[(58, 67, -74)] == M + "air"  # do not block the mine exit


def test_station_fails_closed_if_worksite_is_unreachable():
    from mclab.gather_runner import GatherState, _Exec

    table = (59, 71, -74)
    body = FakeGatherBody(K, {}, {M + "furnace": 1}, default=None)
    body.pos = (59, 67, -74)
    walks = []
    body.walk_to = lambda pos, radius: walks.append(pos) and False
    ex = _Exec(body, K, GatherState(stations={"crafting_table": table}), 0)

    result = ex.place_station(PlaceStation("furnace"))

    assert not result.ok and result.reason == "no_free_spot"
    assert walks == [table]
    assert body.inventory()[M + "furnace"] == 1


def test_smelt_works_instead_of_waiting_for_the_furnace():
    world = trees_and_stone(n_logs=14, n_stone=12)
    world.update({(i, 0, -10): M + "iron_ore" for i in range(4)})
    world.update({(i, 0, -14): M + "coal_ore" for i in range(2)})
    body = FakeGatherBody(K, world)
    slices = []
    work = lambda: slices.append(1) or len(slices) < 4  # noqa: E731
    report = run({M + "iron_pickaxe": 1}, body, idle_work=work)
    assert report.stop == "done" and len(slices) >= 1


class DropBody:
    """Two drops; the log is still falling for its first ``fall`` pickup tries."""

    def __init__(self, fall):
        self.fall, self.waits, self.picked = fall, 0, []
        self.drops = {"log": (3.5, 1.5, 0.5), "stick": (1.5, 0.5, 0.5)}

    def position(self):
        return (0.5, 0, 0.5)

    def entities(self):
        return [
            {"uuid": u, "item": "x", "position": list(p)} for u, p in self.drops.items()
        ]

    def walk_to(self, cell, radius):
        raise AssertionError("walk_to_drop is the pickup")

    def walk_to_drop(self, cell, radius):
        uuid = next(u for u, p in self.drops.items() if cell[0] == int(p[0]))
        if uuid == "log" and self.fall > 0:
            return None  # still in the air
        self.picked.append(uuid)
        del self.drops[uuid]
        return True

    def wait_ticks(self, ticks):
        self.waits += 1
        self.fall -= 1


def _collect(body):
    from mclab import gather_runner as gr

    ex = gr._Exec.__new__(gr._Exec)
    ex.body, ex.stalled = body, lambda: False
    ex.collect(None, (2.5, 0.5, 0.5))


def test_collect_does_other_drops_first_then_retries_a_falling_one_after_a_wait():
    body = DropBody(fall=1)
    _collect(body)
    assert body.picked == ["stick", "log"] and body.waits == 1


def test_collect_gives_up_on_an_item_that_never_lands():
    body = DropBody(fall=10**6)  # stuck on a leaf block
    _collect(body)
    assert body.picked == ["stick"] and 1 <= body.waits <= 3


def test_a_mob_in_the_way_retries_the_same_block_from_another_stance():
    body = FakeGatherBody(K, trees_and_stone())
    original = body.break_block
    calls = []

    def sheep(pos, tool=None):
        calls.append(pos)
        return original(pos, tool) if len(calls) > 1 else Result(False, "entity_in_way")

    body.break_block = sheep
    report = run({M + "oak_planks": 4}, body)

    assert report.stop == "done"
    assert calls[1] == calls[0]  # not skipped for another log


def test_pure_gather_smelt_mines_the_next_plan_item_while_waiting():
    # run 20261112T045003403293Z stood ~10 s per smelt: smelt had no idle work
    from mclab.gather_runner import GatherState
    from mclab.gather_tasks import Gather, Smelt

    world = trees_and_stone(n_stone=4)
    world[(0, 0, -3)] = M + "furnace"
    body = FakeGatherBody(
        K, world, {M + "raw_iron": 1, M + "coal": 1, M + "wooden_pickaxe": 1}
    )
    smelt = Smelt("x", 1, M + "coal", 1, "furnace", M + "iron_ingot", M + "raw_iron")
    stone = Gather(M + "stone", 2, "pickaxe", "wood", M + "cobblestone", 2)
    state = GatherState(stations={"furnace": (0, 0, -3)})

    report = run_gather(PlanResult((smelt, stone), (), 0.0), body, K, state=state)

    assert report.stop == "done", report
    assert body.inventory()[M + "iron_ingot"] == 1
    assert body.inventory().get(M + "cobblestone", 0) >= 2
    assert report.done == ["Smelt:x"]  # the gather ran inside the smelt's wait

def test_pure_gather_smelt_mines_spares_when_no_plan_item_is_left():
    # iron run 20261112T045003403293Z: only the pickaxe followed the smelt, so the
    # bot stood 10 s; it now mines spares of what the plan already gathered
    from mclab.gather_runner import GatherState
    from mclab.gather_tasks import Gather, Smelt

    world = trees_and_stone(n_stone=6)
    world[(0, 0, -3)] = M + "furnace"
    body = FakeGatherBody(
        K, world, {M + "raw_iron": 1, M + "coal": 1, M + "wooden_pickaxe": 1}
    )
    stone = Gather(M + "stone", 1, "pickaxe", "wood", M + "cobblestone", 1)
    smelt = Smelt("x", 1, M + "coal", 1, "furnace", M + "iron_ingot", M + "raw_iron")
    state = GatherState(stations={"furnace": (0, 0, -3)})

    report = run_gather(PlanResult((stone, smelt), (), 0.0), body, K, state=state)

    assert report.stop == "done", report
    assert body.inventory()[M + "iron_ingot"] == 1
    assert body.inventory().get(M + "cobblestone", 0) >= 2


def test_smelt_gather_does_not_try_distant_known_blocks():
    from mclab.gather_runner import GatherState, _Exec
    from mclab.gather_tasks import Gather

    near, far = (5, 0, 0), (30, 0, 0)
    body = FakeGatherBody(
        K, floor() | {near: M + "oak_log", far: M + "oak_log"}, sight=100
    )
    attempted = []
    body.goto_stance = lambda pos, *args: attempted.append(pos) and False
    executor = _Exec(body, K, GatherState(), 0)
    task = Gather(M + "oak_log", 1, None, "hand", M + "oak_log", 1)

    result = executor.gather(task, near=(0, 0, 0), radius=24)

    assert not result.ok
    assert near in attempted
    assert far not in attempted


def test_empty_smelt_work_is_retried_for_a_later_batch():
    from mclab.gather_runner import GatherState
    from mclab.gather_tasks import Smelt

    furnace = (0, 0, -3)
    body = FakeGatherBody(
        K, floor() | {furnace: M + "furnace"},
        {M + "raw_iron": 2, M + "coal": 2},
    )
    smelt = Smelt("x", 1, M + "coal", 1, "furnace", M + "iron_ingot", M + "raw_iron")
    calls = []

    def work():
        calls.append(1)
        return len(calls) == 2

    report = run_gather(
        PlanResult((smelt, smelt), (), 0.0), body, K,
        state=GatherState(stations={"furnace": furnace}), idle_work=work,
    )

    assert report.stop == "done"
    assert len(calls) >= 2
    assert body.inventory()[M + "iron_ingot"] == 2


def test_smelt_partial_ahead_gather_keeps_remaining_task():
    from mclab.gather_runner import GatherState
    from mclab.gather_tasks import Gather, Smelt

    furnace = (0, 0, -3)
    near, far = (5, 0, 0), (30, 0, 0)
    body = FakeGatherBody(
        K, floor() | {furnace: M + "furnace", near: M + "oak_log", far: M + "oak_log"},
        {M + "raw_iron": 1, M + "coal": 1}, sight=100,
    )
    smelt = Smelt("x", 1, M + "coal", 1, "furnace", M + "iron_ingot", M + "raw_iron")
    gather = Gather(M + "oak_log", 2, None, "hand", M + "oak_log", 2)

    report = run_gather(
        PlanResult((smelt, gather), (), 0.0), body, K,
        state=GatherState(stations={"furnace": furnace}),
    )

    assert report.stop == "done"
    assert body.inventory()[M + "oak_log"] == 2

