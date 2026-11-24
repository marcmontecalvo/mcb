"""What the bot should put on (companion_gear): armor and a shield carried, not worn."""

from mclab import companion_gear as cg

M = "minecraft:"


def row(slot, item, count=1):
    return {"slot": slot, "item": M + item, "count": count}


def test_gear_is_armor_or_a_shield():
    for item in ("iron_helmet", "turtle_helmet", "diamond_chestplate",
                 "leather_leggings", "netherite_boots", "shield"):  # fmt: skip
        assert cg.is_gear(M + item), item
    for item in ("iron_sword", "elytra", "carved_pumpkin", "iron_ingot", "bow"):
        assert not cg.is_gear(M + item), item


def test_unworn_gear_is_what_the_main_inventory_carries():
    rows = [
        row(0, "iron_sword"),
        row(3, "iron_chestplate"),
        row(9, "shield"),
        row(38, "leather_chestplate"),  # worn (the chest slot)
        row(40, "torch", 12),  # the off hand
    ]
    assert cg.unworn(rows) == ((3, M + "iron_chestplate"), (9, M + "shield"))
    assert cg.unworn([row(39, "iron_helmet"), row(0, "bread", 5)]) == ()
    assert cg.unworn(None) == ()


def test_the_dress_signature_changes_with_what_is_carried_or_worn():
    a = [row(3, "iron_chestplate"), row(38, "leather_chestplate")]
    b = [row(3, "leather_chestplate"), row(38, "iron_chestplate")]
    assert cg.signature(a) != cg.signature(b)
    assert cg.signature(a) == cg.signature(list(reversed(a)))
    assert cg.signature([row(0, "bread", 5)] + a) == cg.signature(a)  # not gear
