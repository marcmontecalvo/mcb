# ruff: noqa: E501
"""Underground kit (gather_kit): runs 20261113T174328610209Z and 20261113T175858389732Z."""

from collections import Counter

from mclab.build_runner import Result
from mclab.companion_jobs import load_vanilla_pack
from mclab.gather_fake import FakeGatherBody
from mclab.gather_options import GatherOptions
from mclab.gather_plan import Craft, Gather, PlaceStation, Smelt, plan
from mclab.gather_runner import GatherState, _Exec
from mclab.gather_tasks import Gather as GatherTask

K = load_vanilla_pack()
M = "minecraft:"
IRON, COAL = M + "iron_ore", M + "coal_ore"
HELD = {M + "wooden_pickaxe": 1, M + "stone_pickaxe": 1, M + "crafting_table": 1}


def crafts(r):
    c = Counter()
    for t in r.tasks:
        if isinstance(t, Craft):
            c[t.result.removeprefix(M)] += t.times
    return c


def test_plan_carries_spares_table_and_logs_before_ore():
    r = plan({M + "raw_iron": 3}, HELD, K, {}, kit=GatherOptions())
    c = crafts(r)
    assert c["stone_pickaxe"] == 2  # 1 held + 2 = 3 spares
    assert sum(t.count for t in r.tasks if isinstance(t, Gather) and "log" in t.block) >= 16
    ore = next(i for i, t in enumerate(r.tasks) if isinstance(t, Gather) and t.block == IRON)
    assert all(i < ore for i, t in enumerate(r.tasks) if isinstance(t, Craft))
    assert not plan({M + "raw_iron": 3}, HELD, K, {}).tasks == r.tasks  # opt-in


def test_raw_iron_goal_keeps_stone_tools_and_never_smelts():
    r = plan({M + "raw_iron": 3}, HELD, K, {}, kit=GatherOptions())
    assert not any(isinstance(t, Smelt) for t in r.tasks)
    assert not {"iron_pickaxe", "furnace", "iron_ingot"} & set(crafts(r))
    assert not any(isinstance(t, PlaceStation) and "furnace" in t.kind for t in r.tasks)
    r = plan({M + "iron_pickaxe": 1}, HELD, K, {}, kit=GatherOptions())
    assert crafts(r)["iron_pickaxe"] == 1  # the pickaxe goal does smelt


class Brittle(FakeGatherBody):
    """The stone pickaxe breaks after ``life`` blocks, as the live ones did."""

    life = 2

    def break_block(self, pos, tool=None, drops_optional=False):
        res = super().break_block(pos, tool, drops_optional)
        if res.ok and tool == M + "stone_pickaxe":
            self.life -= 1
            if self.life <= 0:
                self.inv.pop(M + "stone_pickaxe", None)
        return res


def pocket():
    w = {
        (x, y, z): M + "stone" for x in range(-6, 12) for y in range(10, 30) for z in range(-6, 12)
    }
    for x in range(-1, 3):
        for z in range(-1, 3):
            for y in (20, 21, 22):
                w[x, y, z] = M + "air"
    for i in range(3, 12):
        w[i, 20, 0] = IRON
    return w


def test_pickaxe_breaks_below_and_is_crafted_with_the_carried_table():
    inv = {M + "stone_pickaxe": 1, M + "crafting_table": 1, M + "oak_log": 4}
    inv[M + "cobblestone"] = 3
    body = Brittle(K, pocket(), inv, pos=(0, 20, 0))
    ex = _Exec(body, K, GatherState(), 64, opts=GatherOptions(ore_vision=True))
    res = ex.gather(GatherTask(IRON, 3, "pickaxe", "stone", M + "raw_iron", 3))
    assert body.inv.get(M + "raw_iron", 0) >= 1, res
    assert body.life <= 0  # the first pickaxe really broke
    assert body.inv.get(M + "crafting_table", 0) == 1  # picked back up
    assert body.position()[1] <= 21  # never climbed to the surface (y 30)


def test_a_spare_is_crafted_locally_while_the_same_pickaxe_is_held():
    # 20261116T135356630645Z: two stone picks held, the plan wanted a third; the
    # local craft planned "own 1" (nothing) and the body walked for the far table
    from mclab.gather_kit import craft_local

    inv = {M + "stone_pickaxe": 2, M + "crafting_table": 1, M + "oak_log": 4}
    inv[M + "cobblestone"] = 3
    body = FakeGatherBody(K, pocket(), inv, pos=(0, 20, 0))
    ex = _Exec(body, K, GatherState(), 64)

    assert craft_local(ex, M + "stone_pickaxe", 1)
    assert body.inventory()[M + "stone_pickaxe"] == 3
    assert body.inventory()[M + "crafting_table"] == 1  # picked back up


def strip_run(plan_ores):
    w = {
        (x, y, z): M + "stone" for x in range(-4, 60) for y in range(8, 31) for z in range(-4, 40)
    }
    w.update({(17, 16, 10): IRON, (15, 17, 12): COAL})
    pick = {M + "wooden_pickaxe": 1, M + "stone_pickaxe": 1}
    body = FakeGatherBody(K, w, pick, pos=(3, 31, 3))
    o = GatherOptions(ore_vision=False, strip_leg=16, strip_max_legs=2, ore_collect="specific")
    ex = _Exec(body, K, GatherState(), 64, opts=o)
    ex.plan_ores = plan_ores
    ex.gather(GatherTask(IRON, 99, "pickaxe", "stone", M + "raw_iron", 99))
    return body


def test_coal_is_taken_along_the_strip_only_when_the_plan_needs_it():
    assert strip_run({COAL}).inv.get(M + "coal", 0) >= 1
    assert strip_run(set()).inv.get(M + "coal", 0) == 0


def test_kit_gathers_and_crafts_all_come_before_the_first_ore():
    r = plan({M + "raw_iron": 3}, {}, K, {}, kit=GatherOptions())
    ore = next(i for i, t in enumerate(r.tasks) if isinstance(t, Gather) and t.block == IRON)
    later = [t for t in r.tasks[ore:] if isinstance(t, Craft) or (isinstance(t, Gather) and t.block != IRON)]
    assert not later, later


class Wrong(FakeGatherBody):
    """The first break reports wrong_tool (the pickaxe went away just then)."""

    spent = False

    def break_block(self, pos, tool=None, drops_optional=False):
        if not self.spent:
            self.spent = True
            self.inv.pop(M + "stone_pickaxe", None)
            return Result(False, "wrong_tool")
        return super().break_block(pos, tool, drops_optional)


def test_wrong_tool_underground_crafts_a_pickaxe_and_retries_before_any_escape():
    inv = {M + "stone_pickaxe": 1, M + "crafting_table": 1, M + "oak_log": 4, M + "cobblestone": 3}
    body = Wrong(K, pocket(), inv, pos=(0, 20, 0))
    ex = _Exec(body, K, GatherState(), 64, opts=GatherOptions(ore_vision=True))
    res = ex.gather(GatherTask(IRON, 1, "pickaxe", "stone", M + "raw_iron", 1))
    assert res.ok and body.inv.get(M + "raw_iron", 0) >= 1, res


def test_placing_a_station_asks_for_a_precise_stance():
    seen = []

    class Spy(FakeGatherBody):
        def place_block(self, pos, state, item):
            seen.append(self.precise_stance)
            return super().place_block(pos, state, item)

    w = {(x, 0, z): M + "stone" for x in range(-3, 4) for z in range(-3, 4)}
    body = Spy(K, w, {M + "crafting_table": 1}, pos=(0, 1, 0))
    body.precise_stance = False
    ex = _Exec(body, K, GatherState(), 64, opts=GatherOptions())
    res, _ = ex.place_free(M + "crafting_table", M + "crafting_table")
    assert res.ok and seen == [True]
    assert body.precise_stance is False  # restored
