import json
import math
import shutil
from dataclasses import replace
from pathlib import Path

from mclab.gather_check import credited
from mclab.gather_plan import (
    Craft,
    Deposit,
    Equip,
    Gather,
    Hunt,
    PlaceContainer,
    PlaceStation,
    Shear,
    Smelt,
    Withdraw,
    check_order,
    food_needed,
    plan,
)
from mclab.knowledge import load_pack

MINI = Path(__file__).parent / "fixtures" / "knowledge" / "mini"
M = "minecraft:"
K = load_pack(MINI)
K_FOOD = load_pack(
    MINI, foods=Path(__file__).parents[1] / "knowledge" / "sources" / "foods-26.3.json"
)


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


# ---- G1 mobs, F1 food, G2 tools, G4 chance, G6 wear, G5 storage ---------------
SHULKER = M + "shulker_box"


def _ok(r, inv=None, storage=None):
    assert check_order(r.tasks, inv or {}, K, None, storage) == []
    return r


def test_wool_is_sheared_with_shears_made_from_two_iron_ingots():
    r = _ok(plan({M + "white_wool": 4}, {}, K))

    shear = _of(r, Shear)[0]
    assert (shear.entity_type, shear.item, shear.item_count) == (
        M + "sheep",
        M + "white_wool",
        4,
    )
    assert shear.count == 3  # 4 wool at 2 per sheep, plus one spare for the range
    assert not _of(r, Hunt)
    shears = [t for t in _of(r, Craft) if t.result == M + "shears"]
    assert dict(shears[0].ingredients) == {M + "iron_ingot": 2}
    assert _index(r, lambda t: isinstance(t, Equip) and t.item == M + "shears") < (
        _index(r, lambda t: isinstance(t, Shear))
    )


def test_bed_takes_sheared_wool_through_the_wool_tag():
    r = _ok(plan({M + "white_bed": 1}, {}, K))

    assert _of(r, Shear)
    assert _crafts(r)["white_bed"] == 1


def test_leather_is_hunted_with_a_margin_for_the_chance():
    r = _ok(plan({M + "leather": 3}, {}, K))

    hunt = _of(r, Hunt)[0]
    assert (hunt.entity_type, hunt.item_count, hunt.weapon) == (M + "cow", 3, None)
    assert hunt.count == math.ceil(3 / 0.6) + 1


def test_a_held_sword_is_equipped_for_the_hunt_and_discounts_it():
    inv = {M + "iron_sword": 1}

    bare = plan({M + "leather": 3}, {}, K)
    armed = _ok(plan({M + "leather": 3}, inv, K), inv)

    assert _of(armed, Hunt)[0].weapon == M + "iron_sword"
    assert _index(armed, lambda t: isinstance(t, Equip)) < _index(
        armed, lambda t: isinstance(t, Hunt)
    )
    assert armed.estimated_cost < bare.estimated_cost


def test_known_mob_entities_are_cheaper_to_find():
    known = plan({M + "leather": 3}, {}, K, known_blocks={M + "cow"})

    assert known.estimated_cost < plan({M + "leather": 3}, {}, K).estimated_cost


def test_food_target_hunts_and_cooks_the_cheapest_meat():
    r = _ok(plan({}, {}, K_FOOD, food_target=16))

    cooked = [t for t in _of(r, Smelt) if t.result.startswith(M + "cooked_")]
    points = sum(t.result_count * K_FOOD.foods[t.result]["nutrition"] for t in cooked)
    assert points >= 16
    assert _of(r, Hunt)
    assert _index(r, lambda t: isinstance(t, Hunt)) < _index(
        r, lambda t: isinstance(t, Smelt)
    )


def test_held_food_counts_toward_the_food_target():
    r = plan({}, {M + "cooked_beef": 2}, K_FOOD, food_target=16)

    assert r.tasks == ()


def test_food_without_a_food_table_is_reported():
    assert plan({}, {}, K, food_target=8).missing[0].item == "food"


def test_food_needed_spends_food_above_the_sprint_floor_first():
    assert food_needed(800, 20) == 0
    assert food_needed(2400, 10) == 30 - 4
    assert food_needed(0, 0) == 0


def test_no_wooden_pickaxe_spam_for_a_mixed_mining_job():
    want = {M + "cobblestone": 400, M + "coal": 40, M + "raw_iron": 20}

    r = _ok(plan(want, {}, K))

    assert _crafts(r).get("wooden_pickaxe", 0) <= 1
    assert _crafts(r).get("stone_pickaxe", 0) >= 1


def test_chance_drops_get_a_margin_in_blocks():
    r = _ok(plan({M + "flint": 2}, {}, K))

    g = _of(r, Gather)[0]
    assert (g.block, g.item_count) == (M + "gravel", 2)
    assert g.margin == 5
    assert g.count == math.ceil(2 / 0.1) + 5
    assert credited(g) == 2
    assert credited(replace(g, count=g.margin + 10)) < 2


def test_inventory_list_with_damage_uses_remaining_durability():
    table = {"item": M + "crafting_table", "count": 1}
    worn = {"item": M + "wooden_pickaxe", "count": 1, "damage": 57, "max_damage": 59}
    inv = [worn, table]

    r_worn = plan({M + "cobblestone": 30}, inv, K)
    r_fresh = plan(
        {M + "cobblestone": 30}, {M + "wooden_pickaxe": 1, M + "crafting_table": 1}, K
    )

    assert "wooden_pickaxe" in _crafts(r_worn) or "stone_pickaxe" in _crafts(r_worn)
    assert "wooden_pickaxe" not in _crafts(r_fresh)
    assert check_order(r_worn.tasks, inv, K) == []


def test_check_order_flags_a_tool_that_wears_out():
    held = {M + "wooden_pickaxe": 1}
    gather = Gather(M + "stone", 100, "pickaxe", "wood", M + "cobblestone", 100)
    tasks = (Equip(M + "wooden_pickaxe"), gather)

    assert check_order(tasks, held, K)
    assert not check_order((tasks[0], replace(gather, count=59)), held, K)


def test_a_weapon_only_wears_down_its_own_use():
    hunt = Hunt(M + "cow", 5, M + "beef", 5, M + "wooden_sword", 1.0, 0)
    inv = [{"item": M + "wooden_sword", "count": 1, "damage": 58, "max_damage": 59}]

    assert check_order((hunt,), inv, K) == []


BIG = {M + "oak_planks": 2400}


def test_big_jobs_are_batched_into_a_chest_that_is_crafted_first():
    r = _ok(plan(BIG, {}, K))

    place = _index(r, lambda t: isinstance(t, PlaceContainer))
    first_deposit = _index(r, lambda t: isinstance(t, Deposit))
    chest = _index(r, lambda t: isinstance(t, Craft) and t.result == M + "chest")
    assert chest < place < first_deposit
    assert _of(r, PlaceContainer)[0].kind == "chest"
    assert r.stored[M + "oak_planks"] > 0
    assert len(_of(r, Gather)) > 1  # batches


def test_a_carried_shulker_box_is_placed_instead_of_crafting_a_chest():
    r = _ok(plan(BIG, {SHULKER: 1}, K), {SHULKER: 1})

    assert _of(r, PlaceContainer)[0].kind == "shulker_box"
    assert M + "chest" not in {t.result for t in _of(r, Craft)}


def test_shulker_boxes_in_a_known_chest_are_withdrawn_and_preferred():
    pos = (3, 64, -2)
    box = [{"kind": "chest", "pos": list(pos), "contents": {SHULKER: 2}}]

    r = _ok(plan(BIG, {}, K, storage=box), storage=box)

    first = _index(r, lambda t: isinstance(t, Withdraw))
    assert r.tasks[first] == Withdraw(((SHULKER, 1),), pos)
    assert first < _index(r, lambda t: isinstance(t, PlaceContainer))
    assert _of(r, PlaceContainer)[0].kind == "shulker_box"
    assert M + "chest" not in {t.result for t in _of(r, Craft)}
    assert _of(r, Deposit)


def test_a_known_chest_without_shulkers_takes_the_deposits_in_place():
    box = [{"kind": "chest", "pos": [0, 64, 0], "contents": {}}]

    r = _ok(plan(BIG, {}, K, storage=box), storage=box)

    assert not _of(r, PlaceContainer)
    assert {t.pos for t in _of(r, Deposit)} == {(0, 64, 0)}


def test_stored_items_count_as_available_behind_a_withdraw():
    box = [{"kind": "chest", "pos": [1, 64, 1], "contents": {M + "oak_planks": 64}}]

    r = _ok(plan({M + "oak_planks": 40}, {}, K, storage=box), storage=box)

    assert not _of(r, Gather)
    assert [type(t) for t in r.tasks] == [Withdraw]


def test_crafts_withdraw_stored_ingredients_first():
    box = [{"kind": "chest", "pos": [1, 64, 1], "contents": {M + "oak_planks": 64}}]

    r = _ok(plan({M + "stick": 8}, {}, K, storage=box), storage=box)

    assert not _of(r, Gather)
    assert isinstance(r.tasks[0], Withdraw) and isinstance(r.tasks[1], Craft)


def test_small_jobs_need_no_storage():
    r = plan({M + "stone_pickaxe": 1}, {}, K)

    assert not any(isinstance(t, PlaceContainer | Deposit | Withdraw) for t in r.tasks)
