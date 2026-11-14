# ruff: noqa: E501
from pathlib import Path

from mclab.gather_fake import FakeGatherBody
from mclab.gather_options import GatherOptions, pick_tool, wanted_ores
from mclab.gather_runner import GatherState, _Exec
from mclab.gather_tasks import Gather
from mclab.knowledge import load_pack

ROOT = Path(__file__).parent.parent
K = load_pack(
    ROOT / "tests/fixtures/knowledge/mini",
    transforms=ROOT / "knowledge/transforms/vanilla-26.3.json",
    foods=ROOT / "knowledge/sources/foods-26.3.json",
)
M = "minecraft:"
ORE = M + "iron_ore"
ALL = {M + "wooden_pickaxe": 1, M + "stone_pickaxe": 1, M + "iron_pickaxe": 1}


def exec_with(opts, table=(500, 0, 500)):
    cells = [(x, y, z) for x in range(-2, 40) for y in (-6, -5, -4) for z in range(-2, 3)]
    world = dict.fromkeys(cells, M + "stone")
    world[(30, -5, 0)] = ORE
    body = FakeGatherBody(K, world, pos=(0, 0, 0))
    body.dig_to = lambda goal: False
    state = GatherState(stations={"crafting_table": table})
    return _Exec(body, K, state, 64, opts=opts)


def test_vision_finds_buried_ore_from_the_body_not_the_table():
    ex = exec_with(GatherOptions(vision_radius=32))
    seen = []
    ex.body.dig_to = lambda goal: seen.append(goal) or False
    ex.dig_to_stone(ORE, ignore_stair=True, vision=True)
    assert seen == [(30, -4, 0)]


def test_vision_radius_bounds_the_search():
    ex = exec_with(GatherOptions(vision_radius=20))
    seen = []
    ex.body.dig_to = lambda goal: seen.append(goal) or False
    ex.dig_to_stone(ORE, ignore_stair=True, vision=True)
    assert seen == []


def test_without_vision_the_far_table_hides_buried_ore():
    ex = exec_with(GatherOptions(ore_vision=False))
    seen = []
    ex.body.dig_to = lambda goal: seen.append(goal) or False
    ex.dig_to_stone(ORE, ignore_stair=True)
    assert seen == []


def test_tool_choice():
    low = lambda b, inv=ALL: pick_tool(K, inv, b, "lowest_sufficient")  # noqa: E731
    assert low(ORE) == M + "stone_pickaxe"
    assert low(M + "coal_ore") == M + "wooden_pickaxe"
    assert low(M + "diamond_ore") == M + "iron_pickaxe"
    assert pick_tool(K, ALL, ORE, "best") == M + "iron_pickaxe"
    assert pick_tool(K, {M + "wooden_pickaxe": 1}, ORE, "best") is None


def test_greedy_wanted_ores_follow_the_held_pickaxes():
    task = Gather(ORE, 1, "pickaxe", "stone", M + "raw_iron", 1)
    opts = GatherOptions()
    inv = {M + "stone_pickaxe": 1}
    got = wanted_ores(task, inv, K, opts)
    assert got[0] == ORE and M + "coal_ore" in got and M + "diamond_ore" not in got
    assert wanted_ores(task, inv, K, GatherOptions(ore_collect="specific")) == [ORE]
