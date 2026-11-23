from pathlib import Path

import pytest

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


class LateDropBody(FakeGatherBody):
    """The drop of a kill shows in the next observation, not the one the kill's
    receipt arrives with (live trial 20261123T185609Z: 2 porkchops seen the same
    tick, the hunt looked before them and went exploring for another pig)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pending = []

    def attack_entity(self, entity, weapon=None):
        before = {id(e) for e in self.ents}
        result = super().attack_entity(entity, weapon)
        self.pending = [e for e in self.ents if id(e) not in before]
        self.ents = [e for e in self.ents if id(e) in before]
        return result

    def wait_ticks(self, ticks):
        super().wait_ticks(ticks)
        self.ents += self.pending
        self.pending = []


def test_a_hunt_waits_for_the_drop_of_its_kill_to_show():
    cow = {"uuid": "c1", "type": M + "cow", "position": [3.5, 0, 3.5]}
    body = LateDropBody(K, floor(), entities=[cow])

    report = run_gather(PlanResult((Hunt(M + "cow", 1),), (), 0.0), body, K)

    assert report.stop == "done", report
    assert body.inventory()[M + "beef"] >= 1
    assert not [c for c in body.log if c[0] == "walk" and abs(c[1][0]) > 8]


def test_a_hunt_collects_where_the_animal_died_not_where_it_was_seen():
    cow = {"uuid": "c1", "type": M + "cow", "position": [3.5, 0, 3.5]}
    body = FakeGatherBody(K, floor(), entities=[cow])

    def fled_and_died(entity, weapon=None):  # it ran 16 blocks before the last hit
        body.ents = [e for e in body.ents if e["uuid"] != entity]
        body._drop_item(M + "beef", 1, (20, 0, 4))
        return Result(True, data={"killed": True, "at": [20.5, 0.0, 4.5]})

    body.attack_entity = fled_and_died
    report = run_gather(PlanResult((Hunt(M + "cow", 1),), (), 0.0), body, K)

    assert report.stop == "done", report
    assert body.inventory()[M + "beef"] == 1


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


def test_a_table_on_the_chest_is_broken_and_put_down_elsewhere():
    # Run 20261117T013027902540Z: the crafting table sat on the supply chest and
    # every withdraw failed cannot_open until the job gave up.
    from mclab.gather_runner import GatherState

    chest, table = (3, 0, 0), (3, 1, 0)
    world = floor() | {chest: M + "chest", table: M + "crafting_table"}
    body = FakeGatherBody(K, world)
    body.boxes[chest] = {M + "oak_log": 4}
    state = GatherState(stations={"crafting_table": table})
    tasks = PlanResult((Withdraw(((M + "oak_log", 2),), chest),), (), 0.0)

    report = run_gather(tasks, body, K, state=state)

    assert report.stop == "done", report
    assert body.inventory()[M + "oak_log"] == 2
    moved = state.stations["crafting_table"]
    assert moved != table and body.w[moved] == M + "crafting_table"
    assert body.w.get(table) in (None, M + "air")


@pytest.mark.parametrize("reason", ["no_line_of_sight", "out_of_reach"])
def test_a_chest_out_of_sight_from_one_stance_is_opened_from_another(reason):
    # Run 20261121T171016886377Z: a shulker box the bot stored into stood between
    # its one stance and the supply chest; every withdraw from that chest failed
    # no_line_of_sight there and the house stopped 27 cells short of done.
    chest = (3, 0, 0)
    world = floor() | {chest: M + "chest"}
    body = FakeGatherBody(K, world)
    body.boxes[chest] = {M + "oak_log": 4}
    opened = body.container
    stances = []

    def container(pos, action, items=None):
        stances.append(body.position())
        if len(stances) == 1:
            return Result(False, reason)  # the view from the first stance is blocked
        return opened(pos, action, items)

    body.container = container
    tasks = PlanResult((Withdraw(((M + "oak_log", 2),), chest),), (), 0.0)

    report = run_gather(tasks, body, K)

    assert report.stop == "done", report
    assert body.inventory()[M + "oak_log"] == 2
    assert len(stances) == 2 and stances[0] != stances[1]


def test_a_run_whose_walks_never_arrive_stops_stalled():
    # The treeless-shore guard (run 20261102T120137588638Z: 100,000 ticks): the
    # watchdog counts an arrived ring leg as progress, so the guard for a body
    # that walks nowhere is still the inventory clock.
    body = FakeGatherBody(K, floor())
    body.walk_to = lambda pos, radius=1.5: False
    now = [0.0]

    def clock():
        now[0] += 10.0  # every check is ten seconds later
        return now[0]

    report = run({M + "oak_planks": 4}, body, stall_s=120, clock=clock)

    assert report.stop == "stalled", report
    assert now[0] < 400  # ended within a few checks of the limit, not after the rings


def test_arrived_but_fruitless_rings_end_the_task_not_the_watchdog():
    # Iron run 20261113T012352828934Z: healthy long walks between quiet rings
    # crossed the 120 s inventory watchdog and stopped the sweep before its last
    # two rings. Arrivals now keep the clock quiet; the ring spiral itself bounds
    # the search, so the task fails honestly with not_found.
    body = FakeGatherBody(K, floor())
    now = [0.0]

    def clock():
        now[0] += 10.0
        return now[0]

    report = run({M + "oak_planks": 4}, body, stall_s=120, clock=clock)

    assert report.stop == "failed", report
    assert all(reason == "not_found" for _, reason in report.failures)
    radii = {radius for radius, _ in report.state.explored}
    assert radii == {16, 32, 48, 64}  # every ring was swept


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


def test_station_is_never_put_on_a_chest_or_shulker_box():
    # Run 20261117T013027902540Z: the table went on the supply chest, which
    # then failed cannot_open on every withdraw.
    from mclab.gather_runner import GatherState, _Exec

    world = floor()
    world[(-1, -1, 0)] = M + "chest[facing=north,type=single]"
    world[(0, -1, -1)] = M + "red_shulker_box[facing=up]"
    body = FakeGatherBody(K, world, {M + "crafting_table": 1})
    state = GatherState()

    result = _Exec(body, K, state, 0).place_station(PlaceStation("crafting_table"))

    assert result.ok
    assert state.stations["crafting_table"] not in {(-1, 0, 0), (0, 0, -1)}


def test_station_is_never_put_over_a_flower():
    # Run 20261117T030711461347Z: the furnace spot was the air over a red tulip,
    # which has no face to click; three tasks failed no_stance on it.
    from mclab.gather_runner import GatherState, _Exec

    world = floor()
    world[(-1, -1, 0)] = M + "red_tulip"
    body = FakeGatherBody(K, world, {M + "furnace": 1})
    state = GatherState()

    result = _Exec(body, K, state, 0).place_station(PlaceStation("furnace"))

    assert result.ok and state.stations["furnace"] != (-1, 0, 0)


def test_station_support_is_judged_by_the_real_collision_shape():
    # a modded or unlisted block with no collision box is no support either
    from mclab.gather_runner import GatherState, _Exec

    body = FakeGatherBody(K, floor(), {M + "furnace": 1})
    body.has_face = lambda x, y, z: (x, y, z) != (-1, -1, 0)
    state = GatherState()

    result = _Exec(body, K, state, 0).place_station(PlaceStation("furnace"))

    assert result.ok and state.stations["furnace"] != (-1, 0, 0)


def test_a_spot_with_no_stance_moves_on_to_the_next_spot():
    from mclab.gather_runner import GatherState, _Exec

    body = FakeGatherBody(K, floor(), {M + "furnace": 1})
    real = body.goto_stance
    body.goto_stance = lambda pos, *a, **k: pos != (-1, 0, 0) and real(pos, *a, **k)
    state = GatherState()

    result = _Exec(body, K, state, 0).place_station(PlaceStation("furnace"))

    assert result.ok and state.stations["furnace"] != (-1, 0, 0)


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
    assert state.stations["furnace"][1] == 71
    assert body.w[(58, 67, -74)] == M + "air"  # do not block the mine exit


def test_station_fails_closed_if_worksite_is_unreachable():
    from mclab.gather_runner import GatherState, _Exec

    table = (59, 71, -74)
    body = FakeGatherBody(K, {}, {M + "furnace": 1}, default=None)
    body.pos = (59, 67, -74)
    ex = _Exec(body, K, GatherState(stations={"crafting_table": table}), 0)

    result = ex.place_station(PlaceStation("furnace"))

    assert not result.ok and result.reason == "no_free_spot"
    assert body.inventory()[M + "furnace"] == 1


def test_station_scans_table_base_when_body_is_on_top_of_worksite():
    # Retry 20261112T141516131352Z arrived atop the table at y=72.
    from mclab.gather_runner import GatherState, _Exec

    table = (59, 71, -74)
    world = {(x, 70, z): M + "stone" for x in range(57, 62) for z in range(-76, -71)}
    world[table] = M + "crafting_table"
    body = FakeGatherBody(K, world, {M + "furnace": 1})
    body.pos = (59, 72, -74)
    state = GatherState(stations={"crafting_table": table})
    result = _Exec(body, K, state, 0).place_station(PlaceStation("furnace"))
    assert result.ok
    assert state.stations["furnace"][1] == 71


def test_station_retains_item_if_known_worksite_has_no_reachable_stance():
    from mclab.gather_runner import GatherState, _Exec

    table = (59, 71, -74)
    world = {(x, 70, z): M + "stone" for x in range(57, 62) for z in range(-76, -71)}
    world[table] = M + "crafting_table"
    body = FakeGatherBody(K, world, {M + "furnace": 1})
    body.pos = (59, 67, -74)
    body.goto_stance = lambda *args, **kwargs: False
    ex = _Exec(body, K, GatherState(stations={"crafting_table": table}), 0)
    result = ex.place_station(PlaceStation("furnace"))
    assert not result.ok and result.reason == "no_stance"
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
        K,
        floor() | {furnace: M + "furnace"},
        {M + "raw_iron": 2, M + "coal": 2},
    )
    smelt = Smelt("x", 1, M + "coal", 1, "furnace", M + "iron_ingot", M + "raw_iron")
    calls = []

    def work():
        calls.append(1)
        return len(calls) == 2

    report = run_gather(
        PlanResult((smelt, smelt), (), 0.0),
        body,
        K,
        state=GatherState(stations={"furnace": furnace}),
        idle_work=work,
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
        K,
        floor() | {furnace: M + "furnace", near: M + "oak_log", far: M + "oak_log"},
        {M + "raw_iron": 1, M + "coal": 1},
        sight=100,
    )
    smelt = Smelt("x", 1, M + "coal", 1, "furnace", M + "iron_ingot", M + "raw_iron")
    gather = Gather(M + "oak_log", 2, None, "hand", M + "oak_log", 2)

    report = run_gather(
        PlanResult((smelt, gather), (), 0.0),
        body,
        K,
        state=GatherState(stations={"furnace": furnace}),
    )

    assert report.stop == "done"
    assert body.inventory()[M + "oak_log"] == 2


def test_ore_found_while_exploring_is_mined_though_far_from_the_table():
    # Live run 20261112T181731902927Z: two ore blocks near the table, then seven
    # minutes of ring walking past ore that was "not near home".
    from mclab.gather_runner import GatherState, _Exec
    from mclab.gather_tasks import Gather

    world = floor() | {(38, 0, 0): M + "iron_ore", (38, 0, 2): M + "iron_ore"}
    body = FakeGatherBody(K, world, {M + "stone_pickaxe": 1}, sight=10)
    state = GatherState()
    state.stations["crafting_table"] = (0, 0, 0)
    ex = _Exec(body, K, state, 48)

    result = ex.gather(Gather(M + "iron_ore", 2, "pickaxe", "stone", M + "raw_iron", 2))

    assert result.ok, result
    assert body.inventory()[M + "raw_iron"] == 2


def test_ore_gather_without_a_harvesting_tool_stops_instead_of_digging():
    # Tools all broken: never tunnel toward ore the hand cannot mine. The task ends
    # with missing_tool, and run_gather's replan crafts new tools first.
    from mclab.gather_runner import GatherState, _Exec
    from mclab.gather_tasks import Gather

    world = floor() | {(3, 0, 0): M + "iron_ore"}
    iron = Gather(M + "iron_ore", 1, "pickaxe", "stone", M + "raw_iron", 1)
    for held in ({}, {M + "wooden_pickaxe": 1}):  # bare hand, and a tier too low
        body = FakeGatherBody(K, world, held)
        result = _Exec(body, K, GatherState(), 48).gather(iron)
        assert result.reason == "missing_tool", (held, result)
        assert body.world(3, 0, 0) == M + "iron_ore"
    body = FakeGatherBody(K, world, {M + "stone_pickaxe": 1})
    assert _Exec(body, K, GatherState(), 48).gather(iron).ok


def test_buried_without_tools_climbs_a_staircase_to_open_sky():
    # All tools broke underground: dig a staircase up by hand (stone drops nothing
    # by hand, so drops are optional) until open sky, then the re-plan makes tools.
    from mclab.gather_dig import Digger
    from mclab.gather_runner import GatherReport, GatherState, _escape, _Exec

    world = {
        (x, y, z): M + ("dirt" if y >= 7 else "stone")
        for x in range(-12, 13)
        for y in range(-1, 10)
        for z in range(-12, 13)
    }
    world[(0, 0, 0)] = world[(0, 1, 0)] = M + "air"  # the pocket the body stands in
    body = FakeGatherBody(K, world, {}, pos=(0, 0, 0), default=M + "air")
    ex = _Exec(body, K, GatherState(), 48)
    report = GatherReport()
    assert _escape(ex, report), report.failures
    assert body.position()[1] >= 8 and Digger(ex).open_sky()  # head at the top layer
    assert report.failures[-1] == ("escape", "open_sky")
    assert M + "cobblestone" not in body.inventory()  # stone by hand gives nothing
    assert not _escape(ex, report)  # already under the sky: nothing to do


def test_smelt_wait_logs_item_and_reason():
    from mclab.gather_runner import GatherState
    from mclab.gather_tasks import Craft, Smelt

    furnace = (0, 0, -3)
    recorded = []
    body = FakeGatherBody(
        K,
        floor() | {furnace: M + "furnace"},
        {M + "raw_iron": 1, M + "coal": 1},
    )
    body.bridge = type(
        "B", (), {"record": lambda _s, kind, row: recorded.append((kind, row))}
    )()
    original_furnace = body.furnace
    collects = 0

    def delayed_furnace(pos, action, **fields):
        nonlocal collects
        if action == "collect":
            collects += 1
            if collects == 1:
                return Result(True)  # still smelting on first collect
        return original_furnace(pos, action, **fields)

    body.furnace = delayed_furnace
    smelt = Smelt("x", 1, M + "coal", 1, "furnace", M + "iron_ingot", M + "raw_iron")
    next_craft = Craft(M + "iron_pickaxe", 1, "crafting_table", M + "iron_pickaxe", 1)

    report = run_gather(
        PlanResult((smelt, next_craft), (), 0.0),
        body,
        K,
        state=GatherState(stations={"furnace": furnace}),
    )

    assert report.stop == "done"
    notes = [
        r for r in body.log if isinstance(r, dict) and r.get("gather") == "smelt_wait"
    ]
    assert len(notes) == 1
    note = notes[0]
    assert note["item"] == M + "iron_ingot"
    assert note["ingredient"] == M + "raw_iron"
    assert note["remaining"] == 1
    assert note["ticks"] == 200
    assert "no_open_face_blocks_within_24_or_drops" in note["why"]
    assert "Craft:" in note["why"]
    assert recorded == [("gather_note", note)]


def test_craft_far_from_the_table_crafts_where_the_body_stands():
    # iron run 20261114T201928310227Z: with the station on the surface the bot
    # livelocked 430 s at the bottom of its shaft instead of placing the carried
    # table a few blocks from where it stood.
    from mclab.gather_runner import GatherState
    from mclab.gather_tasks import Craft

    far = (20, 0, 0)
    world = floor() | {far: M + "crafting_table"}
    body = FakeGatherBody(
        K,
        world,
        {M + "iron_ingot": 3, M + "stick": 2, M + "crafting_table": 1},
    )
    state = GatherState(stations={"crafting_table": far})
    craft = Craft(M + "iron_pickaxe", 1, "crafting_table", M + "iron_pickaxe", 1)

    report = run_gather(
        PlanResult((craft,), (), 0.0),
        body,
        K,
        state=state,
    )

    assert report.stop == "done", report.failures
    assert body.inventory()[M + "iron_pickaxe"] == 1
    assert body.inventory()[M + "crafting_table"] == 1  # taken back up after
    assert state.stations["crafting_table"] == far  # the station record survives
    assert ("goto", far) not in body.log  # never walked to the surface table
    assert sum(1 for p, b in body.w.items() if b == M + "crafting_table") == 1


def test_craft_needing_gathered_stock_still_walks_to_the_station():
    from mclab.gather_runner import GatherState
    from mclab.gather_tasks import Craft

    far = (20, 0, 0)
    world = floor() | {far: M + "crafting_table"} | {(3, 0, 0): M + "oak_log"}
    body = FakeGatherBody(
        K, world, {M + "iron_ingot": 3, M + "stick": 2}, sight=8
    )  # no table, no planks: local crafting cannot, walking can
    craft = Craft(M + "iron_pickaxe", 1, "crafting_table", M + "iron_pickaxe", 1)

    report = run_gather(
        PlanResult((craft,), (), 0.0),
        body,
        K,
        state=GatherState(stations={"crafting_table": far}),
    )

    assert report.stop == "done", report.failures
    assert body.inventory()[M + "iron_pickaxe"] == 1
    assert ("goto", far) in body.log


def test_explore_skips_tree_canopy():
    from mclab.gather_runner import GatherState, _Exec

    world = floor()
    # floor() top solid block is y=-1 (standable surface y=0); tree canopy sits above
    for y in range(4, 8):
        world[(16, y, 0)] = M + "oak_leaves"
    body = FakeGatherBody(K, world, pos=(0, 0, 0), sight=32)
    ex = _Exec(body, K, GatherState(), 16)
    assert ex._surface_y(16, 0, 0) == 0
    assert ex.explore(lambda: True) is True
    walked = [c[1] for c in body.log if c[0] == "walk"]
    assert all(p[1] <= 1 for p in walked)


def test_a_new_state_knows_the_chests_a_gather_placed(tmp_path):
    from test_spatial_memory import identity

    from mclab.place_graph import PlaceGraph
    from mclab.spatial_memory import MapArchive

    archive = MapArchive(tmp_path / "map.db", identity())
    body = FakeGatherBody(K, floor(), {M + "chest": 1, M + "oak_log": 5})
    body.places = PlaceGraph(archive)
    tasks = PlanResult(
        (PlaceContainer("chest"), Deposit(((M + "oak_log", 3),))), (), 0.0
    )
    first = run_gather(tasks, body, K)

    later = FakeGatherBody(K, floor())
    later.places = PlaceGraph(MapArchive(archive.path, identity()))
    second = run_gather(PlanResult((), (), 0.0), later, K)

    assert first.state.containers and second.state.containers == first.state.containers


def test_a_second_chest_is_placed_near_the_first_but_not_touching_it():
    body = FakeGatherBody(K, floor(), {M + "chest": 2})
    state = run_gather(PlanResult((PlaceContainer("chest"),), (), 0.0), body, K).state
    (a,) = state.containers

    run_gather(PlanResult((PlaceContainer("chest"),), (), 0.0), body, K, state=state)

    (b,) = [p for p in state.containers if p != a]
    gap = abs(b[0] - a[0]) + abs(b[2] - a[2])
    assert b[1] == a[1] and 2 <= gap <= 4


def test_a_second_gather_walks_to_the_stored_sighting_instead_of_exploring(
    tmp_path, monkeypatch
):
    from test_spatial_memory import identity

    from mclab.gather_runner import _Exec
    from mclab.place_graph import PlaceGraph
    from mclab.spatial_memory import MapArchive

    world = floor() | {(30, 0, 0): M + "oak_log"}
    places = PlaceGraph(MapArchive(tmp_path / "map.db", identity()))
    places.note_sightings(M + "oak_log", [(30, 0, 0)])
    body = FakeGatherBody(K, world, sight=10)
    body.places = places
    monkeypatch.setattr(
        _Exec, "explore", lambda *a: pytest.fail("explored despite a sighting")
    )

    report = run({M + "oak_log": 1}, body)

    assert report.stop == "done", report
    assert body.inventory()[M + "oak_log"] >= 1


def test_a_sighting_already_within_reach_is_forgotten_not_walked_to(
    tmp_path, monkeypatch
):
    from test_spatial_memory import identity

    from mclab.gather_runner import _Exec
    from mclab.place_graph import PlaceGraph
    from mclab.spatial_memory import MapArchive

    places = PlaceGraph(MapArchive(tmp_path / "map.db", identity()))
    body = FakeGatherBody(K, floor())
    here = body.position()
    near = (here[0] + 1, here[1], here[2])  # the log is gone: the world has none
    places.note_sightings(M + "oak_log", [near])
    body.places = places
    body.walk_to = lambda *a, **k: pytest.fail("walked to a sighting within reach")

    class Explored(Exception):
        pass

    def explore(*a):
        raise Explored

    monkeypatch.setattr(_Exec, "explore", explore)
    with pytest.raises(Explored):
        run({M + "oak_log": 1}, body)
    assert places.nearest_sighting(M + "oak_log", here, set()) is None


def test_a_body_that_dies_mid_run_ends_it_died_not_crashed():
    from mclab.progress_watch import BodyDied

    body = FakeGatherBody(K, floor())

    def dead(*a, **k):
        raise BodyDied("dead")

    body.walk_to = dead
    body.goto_stance = dead
    report = run({M + "oak_planks": 4}, body)
    assert report.stop == "died", report


def test_noting_sightings_keeps_the_near_bound_for_the_next_scan(tmp_path):
    # 20261116T142743554621Z: the sightings list reused the name ``near`` and the
    # next scan crashed on a list where a centre position belonged.
    from test_spatial_memory import identity

    from mclab.gather_runner import GatherState, _Exec
    from mclab.gather_tasks import Gather
    from mclab.place_graph import PlaceGraph
    from mclab.spatial_memory import MapArchive

    logs = {(4, 0, 0): M + "oak_log", (6, 0, 0): M + "oak_log"}
    body = FakeGatherBody(K, floor() | logs, sight=100)
    body.places = PlaceGraph(MapArchive(tmp_path / "map.db", identity()))
    executor = _Exec(body, K, GatherState(), 0)
    task = Gather(M + "oak_log", 2, None, "hand", M + "oak_log", 2)

    result = executor.gather(task, near=(0, 0, 0), radius=24)

    assert result.ok, result
    assert body.inventory()[M + "oak_log"] >= 2


def test_a_surface_gather_never_targets_logs_buried_far_below_the_feet():
    # 20261116T150256102133Z: sensed sections held mineshaft logs 43 blocks down;
    # the gather planned to them until it stalled instead of taking surface trees
    from mclab.gather_runner import SURFACE_BELOW, GatherState, _Exec
    from mclab.gather_tasks import Gather

    buried = (3, -(SURFACE_BELOW + 5), 0)
    surface = (20, 0, 0)
    body = FakeGatherBody(
        K, floor() | {buried: M + "oak_log", surface: M + "oak_log"}, sight=100
    )
    attempted = []
    stance = body.goto_stance

    def record(pos, *args, **kw):
        attempted.append(tuple(pos))
        return stance(pos, *args, **kw)

    body.goto_stance = record
    executor = _Exec(body, K, GatherState(), 0)
    task = Gather(M + "oak_log", 1, None, "hand", M + "oak_log", 1)

    result = executor.gather(task)

    assert result.ok, result
    assert buried not in attempted
