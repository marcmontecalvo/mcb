import json
from pathlib import Path

from test_build_plan import make_build

from mclab.companion_jobs import FOOD_KEEP, _food_topup, build_job
from mclab.gather_fake import FakeGatherBody
from mclab.gather_runner import GatherState
from mclab.knowledge import load_pack

ROOT = Path(__file__).parent.parent
K = load_pack(
    ROOT / "tests/fixtures/knowledge/mini",
    transforms=ROOT / "knowledge/transforms/vanilla-26.3.json",
    foods=ROOT / "knowledge/sources/foods-26.3.json",
)
M = "minecraft:"
PLANKS, COBBLE = M + "oak_planks", M + "cobblestone"


def small_template(tmp_path):
    cells = {(x, 0, z): PLANKS for x in range(3) for z in range(3)}
    cells.update({(x, 1, 0): COBBLE for x in range(3)})
    path = tmp_path / "small.build.json"
    path.write_text(json.dumps(make_build(cells, (3, 2, 3))), encoding="utf-8")
    return path


def world():
    w = {(x, -1, z): M + "dirt" for x in range(-6, 40) for z in range(-6, 40)}
    w |= {(12, 0, i * 2): M + "oak_log" for i in range(10)}
    w |= {(i * 2, 0, 14): M + "stone" for i in range(10)}
    return w


def test_empty_inventory_to_a_finished_small_template(tmp_path):
    body = FakeGatherBody(K, world())

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K)

    assert report.stop == "built", report
    assert report.needed == {PLANKS: 9, COBBLE: 3}
    assert report.shortfall == report.needed
    assert report.gather.stop == "done"
    # stone tools first; the house's logs are then cut with the stone axe
    assert "Craft:minecraft:stone_axe" in report.starter.done
    assert "Craft:minecraft:wooden_axe" not in report.starter.done
    assert report.gather.done[:2] == ["Equip:", "Gather:minecraft:oak_log"]
    # every footprint cell is delivered already: no survey walk is needed
    assert report.build.surveyed == 0 and report.build.progress() == 1.0
    assert body.w[(4, 0, 4)] == PLANKS and body.w[(6, 1, 4)] == COBBLE


def test_a_midway_shortfall_refills_through_on_need_materials(tmp_path):
    # The fake loses planks while building (a creeper, say): the hook re-gathers.
    body = FakeGatherBody(K, world(), {PLANKS: 9, COBBLE: 3})
    real_place = body.place_block
    lost = []

    def leaky(pos, state, item):
        result = real_place(pos, state, item)
        if item == PLANKS and len(lost) == 0 and body.inv.get(PLANKS, 0) == 4:
            lost.append(body.inv.pop(PLANKS))
        return result

    body.place_block = leaky

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K)

    assert lost and report.refills, report
    assert report.stop == "built"


def test_remembered_containers_are_withdrawn_before_gathering(tmp_path):
    w = world()
    w[(2, 0, 2)] = M + "chest"
    body = FakeGatherBody(K, w, {})
    body.boxes[(2, 0, 2)] = {PLANKS: 9, COBBLE: 3}
    state = GatherState(containers={(2, 0, 2): {PLANKS: 9, COBBLE: 3}})

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K, state=state)

    # the plan withdraws from the chest instead of mining: nothing is missing
    # and no task touches the world
    assert report.shortfall == {}
    assert all(d.startswith("Withdraw") for d in report.gather.done)
    assert report.stop == "built"


def test_a_supply_chest_is_read_first_shulkers_taken_scaffolding_kept_until_building(
    tmp_path,
):
    scaffolding, shulker = M + "scaffolding", M + "shulker_box"
    w = world()
    w[(2, 0, 2)] = M + "chest"
    body = FakeGatherBody(K, w)
    body.boxes[(2, 0, 2)] = {scaffolding: 64, shulker: 2}
    state = GatherState()
    seen = {}
    real_place = body.place_block

    def watch(pos, st, item):
        if item in (PLANKS, COBBLE):  # a house block, not a table or a chest
            seen.setdefault("scaffolding_at_first_place", body.inv.get(scaffolding, 0))
        return real_place(pos, st, item)

    body.place_block = watch

    report = build_job(
        small_template(tmp_path), (4, 0, 4), 0, body, K, state=state, chests=[(2, 0, 2)]
    )

    assert (2, 0, 2) in state.containers  # the chest is remembered
    assert body.boxes[(2, 0, 2)][shulker] == 0  # shulkers were taken at the start
    assert body.boxes[(2, 0, 2)][scaffolding] == 0
    assert seen["scaffolding_at_first_place"] == 64  # scaffolding taken before building
    assert report.stop == "built"


def test_a_precise_stance_needs_the_body_out_of_the_target_cell():
    from mclab.companion_body import CompanionBody

    body = CompanionBody.__new__(CompanionBody)
    body._obs = {"body": {"position": [4.6, 0.0, 4.5]}}  # box x 4.3..4.9: clear of x=5
    assert not body._overlaps((5, 0, 4))
    body._obs = {"body": {"position": [4.75, 0.0, 4.5]}}  # box x 4.45..5.05
    assert body._overlaps((5, 0, 4))
    body._obs = {"body": {"position": [4.5, 0.0, 4.5]}}
    assert not body._overlaps((5, 0, 4))


def test_a_digging_body_is_kept_out_of_the_template(tmp_path):
    from mclab.companion_body import DIG_TERRAIN

    body = FakeGatherBody(K, world())
    body.terrain_policy = DIG_TERRAIN
    ticks = iter(range(0, 1000, 100))

    report = build_job(
        small_template(tmp_path), (4, 0, 4), 0, body, K, clock=lambda: next(ticks)
    )

    assert report.stop == "built"
    protected = set(body.terrain_policy.protected_positions)
    # every template cell, air included, plus the site's ground (5x5 at margin 1)
    assert len(protected) == 3 * 2 * 3 + 5 * 5
    assert (3, -1, 3) in protected and (7, -1, 7) in protected
    assert (4, 0, 4) in protected and (6, 1, 6) in protected  # (6,1,6) is air
    assert report.ticks == {"gather": 100, "build": 100}


def test_a_stalled_gather_ends_the_job_before_building(tmp_path, monkeypatch):
    from mclab import gather_runner

    monkeypatch.setattr(gather_runner, "STALL_S", -1.0)  # every check is a stall
    body = FakeGatherBody(K, world())

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K)

    assert report.stop == "gather_failed" and report.build is None
    assert report.starter.stop == "stalled"


def test_junk_is_what_the_build_does_not_want_and_never_tools_or_scaffolding():
    from mclab.companion_jobs import junk

    held = {
        "minecraft:dirt": 29,
        "minecraft:orange_tulip": 34,
        "minecraft:oak_planks": 247,
        "minecraft:iron_axe": 1,
        "minecraft:scaffolding": 64,
        "minecraft:shulker_box": 2,
    }
    # dirt stays: it is the fallback scaffold and the fill under a cell over water
    assert junk(held, {"minecraft:oak_planks": 287}) == {"minecraft:orange_tulip": 34}
    # once the site is level and scaffolding is the foothold, dirt the build does
    # not place goes too
    assert junk(held, {"minecraft:oak_planks": 287}, stow_dirt=True) == {
        "minecraft:orange_tulip": 34,
        "minecraft:dirt": 29,
    }
    assert junk(
        held, {"minecraft:oak_planks": 287, "minecraft:dirt": 20}, stow_dirt=True
    ) == {"minecraft:orange_tulip": 34, "minecraft:dirt": 9}


def test_site_prep_dirt_goes_to_the_chests_and_two_scaffolding_stacks_come_out(
    tmp_path,
):
    # A supplied build: the body is to build from what the chests hold, not carry
    # site-prep dirt around to put down as steps (run 20261121T180806599463Z left a
    # dirt step on the roof and tunnelled through a wall to reach it).
    dirt, scaffold = M + "dirt", M + "scaffolding"
    w = world()
    w[(2, 0, 2)] = M + "chest"
    body = FakeGatherBody(K, w, {dirt: 40})
    stock = {PLANKS: 9, COBBLE: 3, scaffold: 128}
    body.boxes[(2, 0, 2)] = dict(stock)
    state = GatherState(containers={(2, 0, 2): dict(stock)})

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K, state=state)

    assert report.stop == "built", report
    assert body.inv.get(dirt, 0) == 0
    assert body.boxes[(2, 0, 2)].get(dirt, 0) >= 40
    assert body.inv.get(scaffold) == 128


def test_a_refill_records_what_was_asked_gained_and_still_short():
    from mclab.companion_jobs import _note_refill

    class Bridge:
        def __init__(self):
            self.rows = []

        def record(self, kind, row):
            self.rows.append((kind, row))

    class Body:
        bridge = Bridge()

        def inventory(self):
            return {"minecraft:oak_planks": 10}

    body = Body()
    _note_refill(
        body,
        {"minecraft:oak_planks": 20, "minecraft:stone": 4},
        {"minecraft:oak_planks": 4},
        "gather:done",
        True,
    )
    [(kind, row)] = body.bridge.rows
    assert kind == "refill"
    assert row["gained"] == {"minecraft:oak_planks": 6}
    assert row["short"] == {"minecraft:oak_planks": 10, "minecraft:stone": 4}
    assert row["source"] == "gather:done" and row["ok"] is True


def test_the_body_carries_food_from_the_supply_chest_into_the_build(tmp_path):
    beef = M + "cooked_beef"
    w = world()
    w[(2, 0, 2)] = M + "chest"
    body = FakeGatherBody(K, w, {})
    body.boxes[(2, 0, 2)] = {PLANKS: 9, COBBLE: 3, beef: 32}
    state = GatherState(containers={(2, 0, 2): {PLANKS: 9, COBBLE: 3, beef: 32}})

    report = build_job(small_template(tmp_path), (4, 0, 4), 0, body, K, state=state)

    assert report.stop == "built"
    assert body.inv.get(beef) == FOOD_KEEP
    assert body.boxes[(2, 0, 2)][beef] == 32 - FOOD_KEEP


def test_a_food_only_refill_takes_food_and_never_gathers(tmp_path):
    class Hungry:
        foods = {M + "bread": {"nutrition": 5}, M + "cooked_beef": {"nutrition": 8}}

        def __init__(self, held):
            self.held = held

        def inventory(self):
            return dict(self.held)

    chest = {M + "bread": 40, M + "cooked_beef": 3}
    stored = GatherState(containers={(0, 0, 0): chest})
    assert _food_topup(Hungry({}), stored) == {M + "cooked_beef": 3}
    assert _food_topup(Hungry({M + "bread": FOOD_KEEP}), stored) == {}
    assert _food_topup(Hungry({}), GatherState()) == {}


def test_a_refill_gather_never_puts_the_builds_scaffolding_or_blocks_away():
    # Run 20261118T023332974582Z: a refill's gather deposited the whole inventory,
    # 51 scaffolding with it, and the build pillared dirt from afar from then on.
    from mclab import gather_plan as gp
    from mclab.companion_jobs import _keeping
    from mclab.gather_tasks import Deposit

    scaffolding = M + "scaffolding"
    held = {scaffolding: 51, COBBLE: 64 * 20, M + "dirt": 64 * 14}
    box = [{"kind": "chest", "pos": [2, 0, 2], "contents": {}}]

    class Body:
        foods = {M + "cooked_beef": {}}

    keep = _keeping({COBBLE: 64}, Body())
    plan = gp.plan({PLANKS: 64}, held, K, {}, storage=box, keep=keep)

    put_away = {i for t in plan.tasks if isinstance(t, Deposit) for i, _ in t.items}
    assert put_away and not put_away & {scaffolding, COBBLE}
    assert {scaffolding, COBBLE, M + "cooked_beef"} <= keep


def test_scaffolding_spent_mid_build_is_taken_back_from_the_chests():
    from mclab.companion_jobs import SCAFFOLD_CARRY, _scaffold_topup

    scaffolding = M + "scaffolding"

    class Body:
        def __init__(self, held):
            self.held = held

        def inventory(self):
            return {scaffolding: self.held}

    assert SCAFFOLD_CARRY == 128  # two stacks
    state = GatherState(containers={(2, 0, 2): {scaffolding: 151}})
    assert _scaffold_topup(Body(0), state) == {scaffolding: 128}
    assert _scaffold_topup(Body(20), state) == {scaffolding: SCAFFOLD_CARRY - 20}
    assert _scaffold_topup(Body(SCAFFOLD_CARRY // 2), state) == {}
    few = GatherState(containers={(2, 0, 2): {scaffolding: 51}})
    assert _scaffold_topup(Body(0), few) == {scaffolding: 51}
    assert _scaffold_topup(Body(0), GatherState(containers={})) == {}


def test_a_full_refill_puts_waiting_blocks_back_to_take_what_it_needs():
    # Run 20261118T042119862404Z: 34 of 36 slots held details waiting on the
    # structure, and the refill withdrew 4 of the 223 planks it was short.
    from mclab.companion_jobs import _load, _make_room, _take

    scaffolding, lantern = M + "scaffolding", M + "lantern"
    w = world()
    w[(2, 0, 2)] = M + "chest"
    held = {scaffolding: 53, lantern: 64 * 20, COBBLE: 64 * 14, M + "iron_axe": 1}
    body = FakeGatherBody(K, w, held, slots=36)
    body.boxes[(2, 0, 2)] = {PLANKS: 223}
    state = GatherState(containers={(2, 0, 2): {PLANKS: 223}})
    wanted = {PLANKS: 223}
    load = _load(wanted, body, K, state)

    _make_room(load, frozenset(load), body, K, state)
    _take(load, body, K, state)

    assert body.inv.get(PLANKS) == 223
    assert body.inv.get(scaffolding) == 53 and body.inv.get(M + "iron_axe") == 1
    assert body.boxes[(2, 0, 2)].get(lantern, 0) > 0  # what waits went back


def test_a_refill_of_the_whole_bill_carries_the_structure_first():
    # Run 20261118T050156629666Z: the whole remaining bill was asked for at once;
    # details filled the slots and the 119 oak stairs never left the chest.
    from mclab.companion_jobs import _load, _make_room, _take

    stairs, lantern, barrel = M + "oak_stairs", M + "lantern", M + "barrel"
    w = world()
    w[(2, 0, 2)] = M + "chest"
    held = {M + "scaffolding": 53, M + "iron_axe": 1, lantern: 64 * 24, barrel: 64 * 9}
    body = FakeGatherBody(K, w, held, slots=36)
    body.boxes[(2, 0, 2)] = {lantern: 64 * 2, PLANKS: 223, stairs: 119}
    state = GatherState(containers={(2, 0, 2): dict(body.boxes[(2, 0, 2)])})
    wanted = {lantern: 64 * 2, barrel: 64, PLANKS: 223, stairs: 119}

    load = _load(wanted, body, K, state)
    _make_room(load, frozenset(load), body, K, state)
    _take(load, body, K, state)

    assert body.inv.get(stairs) == 119 and body.inv.get(PLANKS) == 223
    assert body.inv.get(M + "scaffolding") == 53


def test_refill_load_and_delta_withdraw_top_up_instead_of_reusing_total_target():
    # Body already holds 167 planks and chest holds 49 (wanted=216): _load must
    # request the 49 delta and _take(..., delta=True) must withdraw all 49.
    from mclab.companion_jobs import _load, _take, junk

    w = world()
    w[(2, 0, 2)] = M + "chest"
    bread = M + "bread"
    held = {PLANKS: 167, bread: 5}
    body = FakeGatherBody(K, w, held, slots=36)
    body.foods = {bread: {"nutrition": 5}}
    body.boxes[(2, 0, 2)] = {PLANKS: 49, bread: 20}
    state = GatherState(containers={(2, 0, 2): dict(body.boxes[(2, 0, 2)])})

    assert junk(body.inventory(), {PLANKS: 216}, body.foods) == {}

    load = _load({PLANKS: 216}, body, K, state)
    assert load == {PLANKS: 49}
    _take(load, body, K, state, delta=True)
    assert body.inv.get(PLANKS) == 216
    assert body.boxes[(2, 0, 2)].get(PLANKS, 0) == 0

    food_delta = _food_topup(body, state)
    assert food_delta == {bread: FOOD_KEEP - 5}
    _take(food_delta, body, K, state, delta=True)
    assert body.inv.get(bread) == FOOD_KEEP


def test_load_and_make_room_count_fragmented_slots_and_prioritize_missing_structure():
    from mclab.companion_jobs import _load, _make_room, _take

    w = world()
    w[(2, 0, 2)] = M + "chest"
    # 62 birch logs in 2 slots (44+18), 245 oak planks in 5 slots (instead of 4),
    # 27 slots of spruce planks, 1 scaffolding, 1 lantern = 36 real slots occupied
    # (slots_used({item: count}) alone would undercount 34 slots).
    birch = M + "stripped_birch_log"
    spruce = M + "spruce_planks"
    stairs = M + "oak_stairs"
    lantern = M + "lantern"
    held = {
        M + "scaffolding": 50,
        birch: 62,
        PLANKS: 245,
        spruce: 64 * 27,
        lantern: 4,
    }
    body = FakeGatherBody(K, w, held, slots=36)
    rows = [{"slot": 0, "item": M + "scaffolding", "count": 50}]
    rows.extend(
        [
            {"slot": 1, "item": birch, "count": 44},
            {"slot": 2, "item": birch, "count": 18},
            {"slot": 3, "item": PLANKS, "count": 48},
            {"slot": 4, "item": PLANKS, "count": 59},
            {"slot": 5, "item": PLANKS, "count": 64},
            {"slot": 6, "item": PLANKS, "count": 13},
            {"slot": 7, "item": PLANKS, "count": 61},
            {"slot": 8, "item": lantern, "count": 4},
        ]
    )
    for idx in range(27):
        rows.append({"slot": 9 + idx, "item": spruce, "count": 64})
    body._obs = {"body": {"inventory": rows}}

    body.boxes[(2, 0, 2)] = {stairs: 9}
    state = GatherState(containers={(2, 0, 2): {stairs: 9}})
    # Already-held oak_planks / birch / spruce come before oak_stairs in wanted,
    # yet missing structure (oak_stairs) must still be loaded and lantern evicted.
    wanted = {PLANKS: 245, birch: 62, spruce: 64 * 27, lantern: 4, stairs: 9}

    load = _load(wanted, body, K, state)
    assert load == {stairs: 9}
    _make_room(load, frozenset(load), body, K, state, wanted=wanted)
    assert body.boxes[(2, 0, 2)].get(lantern, 0) == 4
    _take(load, body, K, state, delta=True)
    assert body.inv.get(stairs) == 9


def test_load_does_not_evict_already_held_wanted_details_for_other_details():
    from mclab.companion_jobs import _load

    w = world()
    w[(2, 0, 2)] = M + "chest"
    anvil, barrel = M + "anvil", M + "barrel"
    spruce = M + "spruce_planks"
    held = {M + "scaffolding": 50, spruce: 64 * 34, anvil: 1}
    body = FakeGatherBody(K, w, held, slots=36)
    body.boxes[(2, 0, 2)] = {barrel: 1}
    state = GatherState(containers={(2, 0, 2): {barrel: 1}})
    wanted = {spruce: 64 * 34, anvil: 1, barrel: 1}

    # Inventory is 36/36 with wanted structure + anvil: barrel (also a detail)
    # must NOT load and evict anvil into a ping-pong loop.
    assert _load(wanted, body, K, state) == {}


def test_refill_skips_gather_when_chests_cover_wanted_and_only_gathers_missing(
    tmp_path, monkeypatch
):
    from mclab import gather_plan as gp
    from mclab.companion_jobs import MAX_REFILLS

    anvil = M + "anvil"
    spruce = M + "spruce_planks"
    # Use a 2-block template that triggers a refill without failing build_job
    cells = {(0, 0, 0): PLANKS, (1, 0, 0): COBBLE}
    path = tmp_path / "mini.build.json"
    path.write_text(json.dumps(make_build(cells, (2, 1, 1))), encoding="utf-8")

    # Test build_job's refill hook directly via a hooked on_need_materials
    seen_targets = []
    real_plan = gp.plan

    def spy_plan(targets, *args, **kwargs):
        seen_targets.append(dict(targets))
        return real_plan(targets, *args, **kwargs)

    monkeypatch.setattr(gp, "plan", spy_plan)

    # Case 1: inventory already full of wanted structure + lantern in chest
    # -> missing == {}, gp.plan is not called, and refill returns False.
    w2 = world()
    w2[(2, 0, 2)] = M + "chest"
    body1 = FakeGatherBody(K, w2, {PLANKS: 1, COBBLE: 1, spruce: 64 * 33, anvil: 1})
    body1.boxes[(2, 0, 2)] = {M + "lantern": 4}
    state1 = GatherState(containers={(2, 0, 2): {M + "lantern": 4}})
    # Wrap run_build to capture on_need_materials without needing real house-large
    captured = {}
    from mclab import companion_jobs

    def fake_run_build(template, b, blocks, *, on_need_materials, **kw):
        captured["refill"] = on_need_materials
        return companion_jobs.BuildReport(stop="done")

    monkeypatch.setattr(companion_jobs, "run_build", fake_run_build)
    build_job(path, (4, 0, 4), 0, body1, K, state=state1, build_only=True)
    seen_targets.clear()
    # Full 36 slots (1 scaffolding + 34 structure + 1 anvil), lantern in chest
    body1.inv = {
        M + "scaffolding": 50,
        spruce: 64 * 34,
        anvil: 1,
    }
    state1.containers[(2, 0, 2)] = {M + "lantern": 4}
    ok = captured["refill"]({spruce: 64 * 34, anvil: 1, M + "lantern": 4})
    assert ok is False
    assert seen_targets == []

    # Case 2: cobblestone: 3 is missing from held+stored, while lantern is stocked:
    # refill frees 3 slots and calls gp.plan only with {COBBLE: 3}.
    assert MAX_REFILLS >= 2
    state1.containers[(2, 0, 2)] = {M + "lantern": 4}
    body1.boxes[(2, 0, 2)] = {M + "lantern": 4}
    body1.inv = {
        M + "scaffolding": 50,
        M + "iron_pickaxe": 1,
        spruce: 64 * 33,
        anvil: 1,
    }
    ok2 = captured["refill"]({spruce: 64 * 33, anvil: 1, M + "lantern": 4, COBBLE: 3})
    assert ok2 is True
    assert seen_targets and all(t == {COBBLE: 3} for t in seen_targets)
    assert body1.inv.get(COBBLE, 0) >= 3


def test_phase_clock_tracks_phases_and_stages():
    from mclab.companion_jobs import PhaseClock

    now = [10.0]
    clk = PhaseClock(lambda: now[0])
    clk.enter("reading chests")
    now[0] += 2.0
    clk.enter("gathering materials")
    now[0] += 5.0
    prev = clk.enter("site prep (gathering)")
    now[0] += 3.0
    clk.enter(prev)
    now[0] += 4.0
    clk.enter("placing")
    now[0] += 6.0
    clk.enter("placing details")
    now[0] += 1.5
    clk.enter("cleanup")
    now[0] += 0.5
    total, phases, stages = clk.finish()
    assert total == 22.0
    assert phases == {
        "chests_setup": 2.0,
        "gathering": 9.0,
        "clearing_and_site_prep": 3.0,
        "building_structure": 6.0,
        "placing_details": 1.5,
        "cleanup": 0.5,
    }
    assert stages["gathering materials"] == 9.0
    assert stages["site prep (gathering)"] == 3.0


def test_protect_keeps_sealed_attic_cavities_out_of_build_air():
    from types import SimpleNamespace

    from mclab import build_plan as bp
    from mclab.companion_jobs import protect
    from mclab.terrain_steps import TerrainPolicy

    cells = {(x, y, z): M + "air" for x in range(5) for y in range(6) for z in range(5)}
    for x in range(5):
        for z in range(5):
            cells[(x, 0, z)] = M + "oak_planks"
            cells[(x, 3, z)] = M + "oak_planks"
            cells[(x, 5, z)] = M + "oak_planks"
    for x in range(5):
        for y in (1, 2, 4):
            cells[(x, y, 0)] = M + "oak_planks"
            cells[(x, y, 4)] = M + "oak_planks"
    for z in range(5):
        for y in (1, 2, 4):
            cells[(0, y, z)] = M + "oak_planks"
            cells[(4, y, z)] = M + "oak_planks"
    cells[(2, 1, 0)] = M + "oak_door[facing=north,half=lower,hinge=left,open=false]"
    cells[(2, 2, 0)] = M + "oak_door[facing=north,half=upper,hinge=left,open=false]"
    t = bp.Template(cells, (5, 6, 5), (0, 0, 0), "attic")
    body = SimpleNamespace(terrain_policy=TerrainPolicy())
    protect(body, t)
    # Sealed attic cell stays protected (not in build_air); open room stays in.
    assert (2, 4, 2) in body.terrain_policy.protected_positions
    assert (2, 4, 2) not in body.build_air
    assert (2, 1, 2) in body.build_air
