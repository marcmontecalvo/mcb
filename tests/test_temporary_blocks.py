import functools

from mclab import temporary_blocks as tb
from mclab.companion_jobs import load_vanilla_pack


@functools.cache
def eligible():
    return tb.eligible(load_vanilla_pack())


def test_only_plain_blocks_that_come_back_as_themselves_are_temporaries():
    e = eligible()
    for item in ("scaffolding", "dirt", "cobblestone", "oak_planks", "oak_log"):
        assert f"minecraft:{item}" in e, item
    # stone breaks to cobblestone, glass to nothing, a bookshelf to books; sand falls;
    # a slab or a chest is no cube to stand on
    for item in ("stone", "glass", "bookshelf", "sand", "oak_slab", "chest"):
        assert f"minecraft:{item}" not in e, item


def test_scaffolding_then_dirt_then_cobblestone_then_the_most_spare():
    held = {
        "minecraft:oak_planks": 40,
        "minecraft:cobblestone": 3,
        "minecraft:scaffolding": 10,
        "minecraft:dirt": 5,
        "minecraft:oak_log": 12,
        "minecraft:stone": 64,
    }
    assert tb.order(held, {}, eligible()) == [
        "minecraft:scaffolding",
        "minecraft:dirt",
        "minecraft:cobblestone",
        "minecraft:oak_planks",
        "minecraft:oak_log",
    ]


def test_the_builds_own_blocks_are_not_spare():
    held = {"minecraft:oak_planks": 40, "minecraft:oak_log": 12}
    need = {"minecraft:oak_planks": 40, "minecraft:oak_log": 2}
    assert tb.order(held, need, eligible()) == ["minecraft:oak_log"]
    assert tb.spare(held, need, "minecraft:oak_log") == 10
