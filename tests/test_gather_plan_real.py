"""Gathering planner against the real vanilla pack dumped from the game."""

import dataclasses
import json
import math
from collections import Counter
from pathlib import Path

import pytest

from mclab.gather_plan import (
    Craft,
    Equip,
    Gather,
    Hunt,
    PlaceContainer,
    Shear,
    Smelt,
    Transform,
    check_order,
    plan,
    summarize,
)
from mclab.knowledge import load_pack
from mclab.schematic import load_litematic, material_list

ROOT = Path(__file__).parent.parent
PACK = ROOT / "knowledge" / "26.3-f7dba04d8b5b"
K = load_pack(
    PACK,
    sources=ROOT / "knowledge" / "sources" / "vanilla-26.3.json",
    transforms=ROOT / "knowledge" / "transforms" / "vanilla-26.3.json",
    foods=ROOT / "knowledge" / "sources" / "foods-26.3.json",
)
REAL_MOBS = PACK / "mobs.json"
# Until the Java re-dump lands, the contract-shaped vanilla mobs fixture stands in.
K_MOBS = (
    K
    if REAL_MOBS.is_file()
    else load_pack(
        PACK,
        sources=ROOT / "knowledge" / "sources" / "vanilla-26.3.json",
        transforms=ROOT / "knowledge" / "transforms" / "vanilla-26.3.json",
        foods=ROOT / "knowledge" / "sources" / "foods-26.3.json",
        mobs=ROOT / "tests" / "fixtures" / "knowledge" / "vanilla-mobs.json",
    )
)
K_NO_MOBS = dataclasses.replace(K, mobs={})
M = "minecraft:"
MOB_DROP_ITEMS = {
    M + "bookshelf",
    M + "book",
    M + "leather",
    M + "red_bed",
    M + "white_wool",
    M + "red_wool",
}


def _of(result, cls):
    return [t for t in result.tasks if isinstance(t, cls)]


def _idx(result, pred):
    return next(i for i, t in enumerate(result.tasks) if pred(t))


def _planned(targets, inventory=None, k=K_NO_MOBS, **kw):
    r = plan(targets, inventory or {}, k, **kw)
    problems = check_order(
        r.tasks, inventory or {}, k, kw.get("stations"), kw.get("storage")
    )
    assert problems == []
    return r


def test_tool_is_made_before_anything_that_needs_it():
    r = _planned({M + "iron_pickaxe": 1})

    assert r.ok
    wooden_at = _idx(
        r, lambda t: isinstance(t, Craft) and t.result == M + "wooden_pickaxe"
    )
    first_equip = _idx(r, lambda t: isinstance(t, Equip))
    first_mine = _idx(r, lambda t: isinstance(t, Gather) and t.tool_kind)
    assert wooden_at < first_equip < first_mine


def test_check_order_flags_a_gather_without_its_tool():
    bad = (Gather(M + "stone", 3, "pickaxe", "wood", M + "cobblestone", 3),)

    assert check_order(bad, {}, K)


def test_smelting_fuel_is_whole_items_not_a_coal_block():
    r = _planned({M + "iron_ingot": 3})

    smelt = _of(r, Smelt)[0]
    assert smelt.fuel != M + "coal_block"
    assert smelt.fuel_count == math.ceil(3 * 200 / K.items[smelt.fuel]["fuel_ticks"])
    assert M + "coal_block" not in {t.result for t in _of(r, Craft)}


def test_held_fuel_is_used_before_buying_more():
    inv = {M + "coal": 1}

    r = _planned({M + "iron_ingot": 3}, inv)

    smelt = _of(r, Smelt)[0]
    assert (smelt.fuel, smelt.fuel_count) == (M + "coal", 1)
    assert not any(t.block == M + "coal_ore" for t in _of(r, Gather))


def test_planks_are_crafted_once_and_leftovers_are_counted():
    r = _planned({M + "stick": 4, M + "crafting_table": 1})

    planks = [t for t in _of(r, Craft) if t.result.endswith("_planks")]
    assert len(planks) == 1
    assert planks[0].times == 2  # 2 for sticks + 4 for the table = 6 -> 8 planks
    assert r.surplus[planks[0].result] == 2


def test_two_hundred_cobblestone_upgrades_to_stone_pickaxes():
    r = _planned({M + "cobblestone": 200})

    crafts = Counter()
    for t in _of(r, Craft):
        crafts[t.result] += t.times
    assert crafts[M + "stone_pickaxe"] >= 1
    assert crafts[M + "wooden_pickaxe"] == 1
    mined = sum(t.count for t in _of(r, Gather) if t.item == M + "cobblestone")
    assert mined >= 200 + 3
    first = next(t for t in _of(r, Gather) if t.item == M + "cobblestone")
    stone_picks = crafts[M + "stone_pickaxe"]
    assert first.count == 3 * stone_picks  # mined with the wooden pickaxe


def test_oak_is_preferred_by_default_and_known_blocks_override():
    default = _planned({M + "oak_planks": 4})
    assert {t.block for t in _of(default, Gather)} == {M + "oak_log"}

    spruce = _planned({M + "stick": 4}, known_blocks={M + "spruce_log"})
    assert {t.block for t in _of(spruce, Gather)} == {M + "spruce_log"}


def test_prefer_multiplier_breaks_ties():
    r = _planned({M + "stick": 4}, prefer={M + "birch_log": 0.5})

    assert {t.block for t in _of(r, Gather)} == {M + "birch_log"}


def test_stripped_log_uses_an_axe_transform():
    r = _planned({M + "stripped_oak_log": 2})

    tr = _of(r, Transform)[0]
    assert (tr.transform_id, tr.count, tr.input) == (
        "strip_oak_log",
        2,
        M + "oak_log",
    )
    assert _idx(r, lambda t: isinstance(t, Equip) and "axe" in t.item) < _idx(
        r, lambda t: isinstance(t, Transform)
    )


def test_in_place_transforms_are_not_item_sources():
    r = plan({M + "farmland": 1}, {}, K)

    assert not _of(r, Transform)


def test_mossy_cobblestone_is_craftable():
    r = _planned({M + "mossy_cobblestone": 4})

    assert r.ok
    assert {t.item for t in _of(r, Gather)} & {M + "moss_block", M + "vine"}


def test_vine_needs_shears():
    r = _planned({M + "vine": 2})

    assert r.ok
    assert any(t.tool_kind == "shears" for t in _of(r, Gather))


def test_stone_pickaxe_from_nothing_real_pack():
    r = _planned({M + "stone_pickaxe": 1})

    assert r.ok
    assert len(r.tasks) <= 12


def _house():
    schem = load_litematic(
        ROOT / "tests" / "fixtures" / "schematics" / "house-154fc7cd.litematic"
    )
    blocks = json.loads((PACK / "blocks.json").read_text(encoding="utf-8"))
    return dict(material_list(schem, blocks)[0])


def test_house_without_mob_data_reports_only_mob_drops_missing():
    r = _planned(_house())

    assert {m.item for m in r.missing} <= MOB_DROP_ITEMS
    assert {m.reason for m in r.missing} == {"needs mob drop"}
    assert len(r.tasks) < 150


def test_house_plans_fully_with_shearing_hunting_tools_and_storage():
    r = _planned(_house(), k=K_MOBS)

    print(summarize(r))
    assert r.ok
    assert len(r.tasks) < 150
    assert {t.entity_type for t in _of(r, Shear)} == {M + "sheep"}
    assert {t.entity_type for t in _of(r, Hunt)} == {M + "cow"}
    assert _of(r, PlaceContainer)
    wooden = sum(t.times for t in _of(r, Craft) if t.result == M + "wooden_pickaxe")
    assert wooden == 1
    assert not any(t.result == M + "diamond_axe" for t in _of(r, Craft))
    assert r.stored


@pytest.mark.skipif(not REAL_MOBS.is_file(), reason="pack has no mobs.json yet")
def test_real_pack_mobs_give_leather_and_wool():
    r = _planned({M + "leather": 3, M + "white_wool": 3}, k=K_MOBS)

    assert _of(r, Hunt) and _of(r, Shear)
