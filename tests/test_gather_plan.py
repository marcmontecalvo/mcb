import json
import shutil
from dataclasses import replace
from pathlib import Path

from mclab.gather_plan import (
    Craft,
    Equip,
    Gather,
    PlaceStation,
    Smelt,
    check_order,
    plan,
)
from mclab.knowledge import load_pack

MINI = Path(__file__).parent / "fixtures" / "knowledge" / "mini"
M = "minecraft:"
K = load_pack(MINI)


def _variant(tmp_path, *, drop_recipes=(), extra_items=(), extra_recipes=()):
    root = tmp_path / "pack"
    shutil.copytree(MINI, root)
    rec = json.loads((root / "recipes.json").read_text(encoding="utf-8"))
    rec["recipes"] = [r for r in rec["recipes"] if r["id"] not in drop_recipes] + list(
        extra_recipes
    )
    (root / "recipes.json").write_text(json.dumps(rec), encoding="utf-8")
    items = json.loads((root / "items.json").read_text(encoding="utf-8"))
    for i in extra_items:
        items["items"][i] = {"stack": 64}
    (root / "items.json").write_text(json.dumps(items), encoding="utf-8")
    return load_pack(root)


def _of(result, cls):
    return [t for t in result.tasks if isinstance(t, cls)]


def _index(result, pred):
    return next(i for i, t in enumerate(result.tasks) if pred(t))


def _crafts(result):
    return {t.recipe_id.removeprefix(M): t.times for t in _of(result, Craft)}


def test_stone_pickaxe_from_nothing_follows_the_altoclef_chain():
    r = plan({M + "stone_pickaxe": 1}, {}, K)

    assert r.ok
    assert _crafts(r) == {
        "oak_planks": 3,
        "crafting_table": 1,
        "stick": 1,
        "wooden_pickaxe": 1,
        "stone_pickaxe": 1,
    }
    logs, stone = _of(r, Gather)
    assert (logs.block, logs.count) == (M + "oak_log", 3)
    assert (stone.block, stone.count, stone.tool_kind, stone.min_tier) == (
        M + "stone",
        3,
        "pickaxe",
        "wood",
    )
    chain = [
        lambda t: isinstance(t, Gather) and t.item == M + "oak_log",
        lambda t: isinstance(t, Craft) and t.result == M + "oak_planks",
        lambda t: isinstance(t, Craft) and t.result == M + "crafting_table",
        lambda t: isinstance(t, PlaceStation) and t.kind == "crafting_table",
        lambda t: isinstance(t, Craft) and t.result == M + "wooden_pickaxe",
        lambda t: isinstance(t, Equip) and t.item == M + "wooden_pickaxe",
        lambda t: isinstance(t, Gather) and t.item == M + "cobblestone",
        lambda t: isinstance(t, Craft) and t.result == M + "stone_pickaxe",
    ]
    positions = [_index(r, p) for p in chain]
    assert positions == sorted(positions)
    stick_at = _index(r, lambda t: isinstance(t, Craft) and t.result == M + "stick")
    assert stick_at < positions[4]


def test_iron_pickaxe_from_nothing_adds_furnace_fuel_and_smelting():
    r = plan({M + "iron_pickaxe": 1}, {}, K)

    assert r.ok
    ore = next(t for t in _of(r, Gather) if t.item == M + "raw_iron")
    assert (ore.block, ore.count, ore.min_tier) == (M + "iron_ore", 3, "stone")
    smelt = _of(r, Smelt)[0]
    # 3 smelts = 600 ticks: 2 planks (300 each) are cheaper than mining coal
    assert (smelt.count, smelt.fuel, smelt.fuel_count) == (3, M + "oak_planks", 2)
    assert M + "furnace" in {t.result for t in _of(r, Craft)}
    ore_at = _index(r, lambda t: isinstance(t, Gather) and t.item == M + "raw_iron")
    equip_at = _index(
        r, lambda t: isinstance(t, Equip) and t.item == M + "stone_pickaxe"
    )
    place_at = _index(r, lambda t: isinstance(t, PlaceStation) and t.kind == "furnace")
    smelt_at = _index(r, lambda t: isinstance(t, Smelt))
    assert equip_at < ore_at < smelt_at
    assert place_at < smelt_at


def test_held_items_are_skipped():
    inv = {
        M + "oak_planks": 12,
        M + "stick": 4,
        M + "cobblestone": 3,
        M + "wooden_pickaxe": 1,
    }

    r = plan(
        {M + "stone_pickaxe": 1}, inv, K, stations={"crafting_table": [[0, 64, 0]]}
    )

    assert r.ok
    assert [type(t) for t in r.tasks] == [Craft]
    assert _of(r, Craft)[0].result == M + "stone_pickaxe"


def test_known_crafting_table_is_not_rebuilt():
    r = plan({M + "wooden_pickaxe": 1}, {}, K, stations={"crafting_table": [[1, 2, 3]]})

    assert M + "crafting_table" not in {t.result for t in _of(r, Craft)}
    assert not _of(r, PlaceStation)


def test_house_material_list_collapses_to_raw_gathers_and_ordered_crafts():
    want = {
        M + "oak_planks": 200,
        M + "oak_stairs": 30,
        M + "glass": 12,
        M + "cobblestone": 64,
        M + "torch": 8,
    }

    r = plan(want, {}, K)

    assert r.ok
    assert {t.block for t in _of(r, Gather)} == {
        M + "oak_log",
        M + "stone",
        M + "sand",
        M + "coal_ore",
    }
    crafts = _crafts(r)
    assert crafts["oak_stairs"] == 8
    assert crafts["torch"] == 2
    plank_crafts = [t for t in _of(r, Craft) if t.result == M + "oak_planks"]
    assert len(plank_crafts) == 1
    assert (
        4 * plank_crafts[0].times >= 200 + 48 + 12
    )  # target + stairs + tools/table/sticks
    glass = _of(r, Smelt)
    assert len(glass) == 1 and glass[0].count == 12


def test_two_hundred_cobblestone_replaces_or_upgrades_the_tool():
    r = plan({M + "cobblestone": 200}, {}, K)

    assert r.ok
    wooden = _crafts(r).get("wooden_pickaxe", 0)
    stone_or_better = {"stone_pickaxe", "iron_pickaxe"} & set(_crafts(r))
    assert wooden * 59 >= 200 or stone_or_better


def test_held_tool_durability_is_used_before_crafting_another():
    r = plan(
        {M + "cobblestone": 50}, {M + "wooden_pickaxe": 1, M + "crafting_table": 1}, K
    )

    assert "wooden_pickaxe" not in _crafts(r)
    assert _of(r, Equip)[0].item == M + "wooden_pickaxe"


def test_cycle_between_ingot_and_block_terminates():
    r = plan({M + "iron_block": 1}, {}, K)

    assert r.ok
    assert _crafts(r)["iron_block"] == 1
    assert _of(r, Smelt)[0].count == 9


def test_cycle_with_a_held_block_terminates():
    r = plan({M + "iron_ingot": 18}, {M + "iron_block": 1}, K)

    assert r.ok


def test_unknown_recipe_type_is_reported_as_missing(tmp_path):
    mixing = {
        "id": "create:mixing/brass",
        "type": "create:mixing",
        "station": None,
        "raw": {},
        "result": {"item": "create:brass_ingot", "count": 1},
    }
    k = _variant(tmp_path, extra_items=["create:brass_ingot"], extra_recipes=[mixing])

    r = plan({"create:brass_ingot": 1}, {}, k)

    assert not r.ok
    assert r.missing[0].item == "create:brass_ingot"
    assert r.missing[0].reason == "needs unhandled recipe type create:mixing"


def test_item_with_no_source_is_reported_as_missing():
    r = plan({M + "nether_star": 1}, {}, K)

    assert [(m.item, m.reason) for m in r.missing] == [(M + "nether_star", "no source")]
    assert r.tasks == ()


def test_unreachable_tier_is_reported_as_missing(tmp_path):
    k = _variant(tmp_path, drop_recipes=[M + "iron_pickaxe"])

    r = plan({M + "diamond": 1}, {}, k)

    assert [(m.item, m.reason) for m in r.missing] == [
        (M + "diamond", "needs tier iron")
    ]


def test_diamond_is_planned_when_an_iron_pickaxe_can_be_built():
    r = plan({M + "diamond": 2}, {}, K)

    assert r.ok
    dia = next(t for t in _of(r, Gather) if t.item == M + "diamond")
    assert (dia.block, dia.min_tier) == (M + "diamond_ore", "iron")


def test_block_outside_sources_is_gatherable_only_when_known():
    sources = {k: v for k, v in K.sources.items() if k != M + "sand"}
    k = replace(K, sources=sources)

    assert not plan({M + "sand": 1}, {}, k).ok
    assert plan({M + "sand": 1}, {}, k, known_blocks={M + "sand"}).ok


def test_restricted_executable_stations_make_table_recipes_unreachable():
    r = plan({M + "wooden_pickaxe": 1}, {}, K, executable_stations={"inventory"})

    assert not r.ok
    assert r.missing[0].reason == "needs station crafting_table"


def test_replan_after_progress_shrinks_the_plan():
    first = plan({M + "stone_pickaxe": 1}, {}, K)

    second = plan({M + "stone_pickaxe": 1}, {M + "oak_planks": 12, M + "stick": 4}, K)

    assert len(second.tasks) < len(first.tasks)


def test_check_order_accepts_every_mini_plan():
    for target in ("stone_pickaxe", "iron_pickaxe", "furnace"):
        r = plan({M + target: 1}, {}, K)
        assert check_order(r.tasks, {}, K) == []
