from pathlib import Path

from mclab.gather_fake import FakeGatherBody
from mclab.gather_plan import plan
from mclab.gather_runner import run_gather
from mclab.gather_tasks import (
    Deposit,
    Hunt,
    PlaceContainer,
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
