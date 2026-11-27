"""Comprehensive TDD test suite for mclab.arena_kits (Delivery Item 7)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mclab.arena_kits import (
    BUILTIN_KITS,
    COMBAT_POTIONS,
    KIT_MODES,
    MAX_ARROWS,
    MAX_ENCHANTMENTS_PER_ITEM,
    MAX_FOOD,
    MAX_GIVE,
    MAX_GOLDEN_APPLES,
    MAX_POTIONS,
    MAX_WEAPONS,
    RANDOM_SENTINEL,
    KitError,
    KitLibrary,
    KitValidationError,
    catalog_metadata,
    compile_kit,
    resolve_match_kits,
    roll_respawn_kit,
    score_kit,
    validate_kit,
)


def _minimal_kit(**overrides) -> dict:
    base = {
        "name": "Custom Duelist",
        "description": "Minimal valid test kit",
        "items": [
            {"item": "minecraft:iron_sword", "count": 1},
            {"item": "minecraft:cooked_beef", "count": 8},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Built-in starter classes
# ---------------------------------------------------------------------------


class TestBuiltinKits:
    def test_exact_four_builtins_present_and_valid(self):
        assert tuple(BUILTIN_KITS.keys()) == ("fighter", "archer", "tank", "skirmisher")
        for kit_id, spec in BUILTIN_KITS.items():
            normalized = validate_kit(spec)
            assert normalized["id"] == kit_id
            compiled = compile_kit(normalized)
            assert compiled["clear_inventory"] is True
            assert compiled["heal"] is True
            assert compiled["wear"] is True
            assert 1 <= len(compiled["give"]) <= MAX_GIVE

    def test_fighter_contents(self):
        fighter = BUILTIN_KITS["fighter"]
        assert fighter["name"] == "Fighter"
        by_item = {(e["item"], e.get("potion")): e for e in fighter["items"]}
        assert by_item[("minecraft:iron_sword", None)]["enchantments"] == {
            "minecraft:sharpness": 2
        }
        for piece in ("helmet", "chestplate", "leggings", "boots"):
            assert by_item[(f"minecraft:iron_{piece}", None)]["enchantments"] == {
                "minecraft:protection": 1
            }
        assert by_item[("minecraft:shield", None)]["count"] == 1
        assert by_item[("minecraft:cooked_beef", None)]["count"] == 16
        assert by_item[("minecraft:golden_apple", None)]["count"] == 2
        assert (
            by_item[("minecraft:splash_potion", "minecraft:strong_healing")]["count"]
            == 2
        )

    def test_archer_contents(self):
        archer = BUILTIN_KITS["archer"]
        assert archer["name"] == "Archer"
        by_item = {(e["item"], e.get("potion")): e for e in archer["items"]}
        assert by_item[("minecraft:bow", None)]["enchantments"] == {
            "minecraft:power": 2,
            "minecraft:punch": 1,
        }
        assert by_item[("minecraft:arrow", None)]["count"] == 64
        assert by_item[("minecraft:stone_sword", None)]["count"] == 1
        assert by_item[("minecraft:chainmail_boots", None)]["enchantments"] == {
            "minecraft:protection": 1,
            "minecraft:feather_falling": 1,
        }
        assert by_item[("minecraft:potion", "minecraft:swiftness")]["count"] == 1

    def test_tank_contents(self):
        tank = BUILTIN_KITS["tank"]
        assert tank["name"] == "Tank"
        by_item = {(e["item"], e.get("potion")): e for e in tank["items"]}
        assert by_item[("minecraft:iron_axe", None)]["count"] == 1
        assert by_item[("minecraft:diamond_chestplate", None)]["enchantments"] == {
            "minecraft:protection": 2
        }
        assert by_item[("minecraft:shield", None)]["enchantments"] == {
            "minecraft:unbreaking": 2
        }
        assert by_item[("minecraft:golden_apple", None)]["count"] == 3
        assert by_item[("minecraft:potion", "minecraft:regeneration")]["count"] == 1

    def test_skirmisher_contents(self):
        skirm = BUILTIN_KITS["skirmisher"]
        assert skirm["name"] == "Skirmisher"
        by_item = {(e["item"], e.get("potion")): e for e in skirm["items"]}
        assert by_item[("minecraft:iron_sword", None)]["enchantments"] == {
            "minecraft:knockback": 1
        }
        assert by_item[("minecraft:crossbow", None)]["enchantments"] == {
            "minecraft:quick_charge": 1
        }
        assert by_item[("minecraft:arrow", None)]["count"] == 32
        assert by_item[("minecraft:trident", None)]["enchantments"] == {
            "minecraft:loyalty": 2
        }
        assert by_item[("minecraft:potion", "minecraft:swiftness")]["count"] == 2
        assert (
            by_item[("minecraft:splash_potion", "minecraft:strong_healing")]["count"]
            == 2
        )


# ---------------------------------------------------------------------------
# 2. Strict survival-legal validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_accepts_slot_organized_spec_and_normalizes(self):
        spec = {
            "name": "Slot Kit",
            "description": "Built via slots",
            "slots": {
                "primary": {
                    "item": "minecraft:diamond_sword",
                    "count": 1,
                    "enchantments": {"minecraft:sharpness": 3},
                },
                "secondary": {"item": "minecraft:bow", "count": 1},
                "helmet": {"item": "minecraft:turtle_helmet", "count": 1},
                "chestplate": {"item": "minecraft:iron_chestplate", "count": 1},
                "leggings": {"item": "minecraft:iron_leggings", "count": 1},
                "boots": {
                    "item": "minecraft:iron_boots",
                    "count": 1,
                    "enchantments": {"minecraft:depth_strider": 2},
                },
                "offhand": {"item": "minecraft:shield", "count": 1},
                "ammo": [{"item": "minecraft:spectral_arrow", "count": 32}],
                "food": [
                    {"item": "minecraft:golden_carrot", "count": 16},
                    {"item": "minecraft:golden_apple", "count": 2},
                ],
                "potions": [
                    {
                        "item": "minecraft:lingering_potion",
                        "count": 1,
                        "potion": "minecraft:strong_healing",
                    }
                ],
            },
        }
        normalized = validate_kit(spec)
        assert normalized["name"] == "Slot Kit"
        assert "slots" not in normalized
        assert len(normalized["items"]) == 11

    def test_rejects_both_items_and_slots_or_neither(self):
        with pytest.raises(KitValidationError):
            validate_kit(
                {
                    "name": "Both",
                    "items": [{"item": "minecraft:iron_sword", "count": 1}],
                    "slots": {"primary": {"item": "minecraft:iron_sword", "count": 1}},
                }
            )
        with pytest.raises(KitValidationError):
            validate_kit({"name": "Neither"})
        with pytest.raises(KitValidationError):
            validate_kit({"name": "Empty", "items": []})

    def test_rejects_unknown_slot_names(self):
        with pytest.raises(KitValidationError):
            validate_kit(
                {
                    "name": "Bad Slot",
                    "slots": {
                        "creative_tab": {"item": "minecraft:iron_sword", "count": 1}
                    },
                }
            )

    @pytest.mark.parametrize(
        "extra_field",
        [
            {"nbt": "{Damage:0}"},
            {"components": {"minecraft:damage": 5000000}},
            {"attribute_modifiers": [{"id": "attack_damage", "amount": 9999}]},
            {"command": "/give @p diamond_sword 1"},
            {"unknown_top_level": True},
        ],
    )
    def test_rejects_nbt_components_commands_and_unknown_top_fields(self, extra_field):
        spec = _minimal_kit(**extra_field)
        with pytest.raises(KitValidationError):
            validate_kit(spec)

    @pytest.mark.parametrize(
        "extra_item_field",
        [
            {"nbt": "{Enchantments:[{id:'sharpness',lvl:255}]}"},
            {"components": {"minecraft:attribute_modifiers": []}},
            {"attribute_modifiers": [{"amount": 999}]},
            {"tag": "{}"},
            {"command": "/effect give @s strength 999 255"},
        ],
    )
    def test_rejects_nbt_and_unknown_item_fields(self, extra_item_field):
        item = {"item": "minecraft:iron_sword", "count": 1, **extra_item_field}
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(items=[item]))

    @pytest.mark.parametrize(
        "forbidden_item",
        [
            "minecraft:enchanted_golden_apple",
            "minecraft:lava_bucket",
            "minecraft:water_bucket",
            "minecraft:flint_and_steel",
            "minecraft:fire_charge",
            "minecraft:tnt",
            "minecraft:end_crystal",
            "minecraft:ender_pearl",
            "minecraft:chorus_fruit",
            "minecraft:command_block",
            "minecraft:bedrock",
            "minecraft:barrier",
            "minecraft:diamond_pickaxe",
            "iron_sword",
            "/give @s diamond_sword",
        ],
    )
    def test_rejects_grief_escape_creative_and_non_catalog_items(self, forbidden_item):
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(items=[{"item": forbidden_item, "count": 1}]))

    def test_rejects_bad_names_ids_and_counts(self):
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(name=""))
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(name="A" * 80))
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(name="/give @p sword"))
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(id="BAD ID!"))
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(description="x" * 300))
        for bad_count in (0, -1, True, 1.5, "1"):
            with pytest.raises(KitValidationError):
                validate_kit(
                    _minimal_kit(
                        items=[{"item": "minecraft:iron_sword", "count": bad_count}]
                    )
                )

    def test_enforces_item_and_consumable_caps(self):
        # Weapons cap (> 4 rejected, 4 accepted)
        four_weapons = [
            {"item": "minecraft:iron_sword", "count": 1},
            {"item": "minecraft:iron_axe", "count": 1},
            {"item": "minecraft:bow", "count": 1},
            {"item": "minecraft:trident", "count": 1},
        ]
        assert len(validate_kit(_minimal_kit(items=four_weapons))["items"]) == 4
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=four_weapons + [{"item": "minecraft:mace", "count": 1}]
                )
            )
        # Single weapon count > 1 rejected
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(items=[{"item": "minecraft:iron_sword", "count": 2}])
            )

        # Duplicate armor slot rejected
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {"item": "minecraft:iron_helmet", "count": 1},
                        {"item": "minecraft:turtle_helmet", "count": 1},
                    ]
                )
            )

        # Offhand cap: 1 shield + 1 totem ok, 2 shields or 2 totems rejected
        validate_kit(
            _minimal_kit(
                items=[
                    {"item": "minecraft:shield", "count": 1},
                    {"item": "minecraft:totem_of_undying", "count": 1},
                ]
            )
        )
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(items=[{"item": "minecraft:shield", "count": 2}]))
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {"item": "minecraft:totem_of_undying", "count": 1},
                        {"item": "minecraft:totem_of_undying", "count": 1},
                    ]
                )
            )

        # Arrows cap: 128 ok, 129 rejected
        validate_kit(
            _minimal_kit(
                items=[
                    {"item": "minecraft:arrow", "count": 64},
                    {"item": "minecraft:spectral_arrow", "count": 64},
                ]
            )
        )
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {"item": "minecraft:arrow", "count": 64},
                        {"item": "minecraft:spectral_arrow", "count": 65},
                    ]
                )
            )

        # Food cap: 64 ok, 65 rejected
        validate_kit(
            _minimal_kit(
                items=[
                    {"item": "minecraft:cooked_beef", "count": 32},
                    {"item": "minecraft:golden_carrot", "count": 32},
                ]
            )
        )
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {"item": "minecraft:cooked_beef", "count": 40},
                        {"item": "minecraft:bread", "count": 25},
                    ]
                )
            )

        # Golden apple cap: 8 ok, 9 rejected
        validate_kit(
            _minimal_kit(items=[{"item": "minecraft:golden_apple", "count": 8}])
        )
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(items=[{"item": "minecraft:golden_apple", "count": 9}])
            )

        # Potions cap: 6 ok, 7 rejected
        validate_kit(
            _minimal_kit(
                items=[
                    {
                        "item": "minecraft:splash_potion",
                        "count": 4,
                        "potion": "minecraft:strong_healing",
                    },
                    {
                        "item": "minecraft:potion",
                        "count": 2,
                        "potion": "minecraft:swiftness",
                    },
                ]
            )
        )
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {
                            "item": "minecraft:splash_potion",
                            "count": 4,
                            "potion": "minecraft:strong_healing",
                        },
                        {
                            "item": "minecraft:potion",
                            "count": 3,
                            "potion": "minecraft:swiftness",
                        },
                    ]
                )
            )

        # Max 36 stacks / inventory slots
        too_many_stacks = [
            {"item": "minecraft:cooked_beef", "count": 1} for _ in range(MAX_GIVE + 1)
        ]
        with pytest.raises(KitValidationError):
            validate_kit(_minimal_kit(items=too_many_stacks))

    def test_potion_rules_and_allowlist(self):
        # Every allowlisted potion works on potion, splash_potion, lingering_potion, tipped_arrow
        assert len(COMBAT_POTIONS) == 31
        for pid in (
            "minecraft:strong_healing",
            "minecraft:turtle_master",
            "minecraft:slow_falling",
        ):
            assert pid in COMBAT_POTIONS
            validate_kit(
                _minimal_kit(
                    items=[{"item": "minecraft:potion", "count": 1, "potion": pid}]
                )
            )
            validate_kit(
                _minimal_kit(
                    items=[
                        {"item": "minecraft:tipped_arrow", "count": 16, "potion": pid}
                    ]
                )
            )

        # Potion item without potion id rejected
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(items=[{"item": "minecraft:splash_potion", "count": 1}])
            )
        # Tipped arrow without potion id rejected
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(items=[{"item": "minecraft:tipped_arrow", "count": 16}])
            )
        # Disallowed potion id rejected
        for bad_potion in (
            "minecraft:invisibility",
            "minecraft:water",
            "minecraft:luck",
            "strong_healing",
        ):
            with pytest.raises(KitValidationError):
                validate_kit(
                    _minimal_kit(
                        items=[
                            {
                                "item": "minecraft:potion",
                                "count": 1,
                                "potion": bad_potion,
                            }
                        ]
                    )
                )
        # Potion field on non-potion item rejected
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {
                            "item": "minecraft:iron_sword",
                            "count": 1,
                            "potion": "minecraft:healing",
                        }
                    ]
                )
            )

    def test_enchantment_applicability(self):
        # Valid category pairings
        valid_pairs = [
            (
                "minecraft:netherite_sword",
                {
                    "minecraft:sharpness": 5,
                    "minecraft:knockback": 2,
                    "minecraft:fire_aspect": 2,
                    "minecraft:sweeping_edge": 3,
                },
            ),
            (
                "minecraft:diamond_axe",
                {"minecraft:sharpness": 5, "minecraft:unbreaking": 3},
            ),
            (
                "minecraft:iron_spear",
                {"minecraft:unbreaking": 3, "minecraft:mending": 1},
            ),
            (
                "minecraft:bow",
                {
                    "minecraft:power": 5,
                    "minecraft:punch": 2,
                    "minecraft:flame": 1,
                    "minecraft:infinity": 1,
                },
            ),
            (
                "minecraft:crossbow",
                {
                    "minecraft:quick_charge": 3,
                    "minecraft:multishot": 1,
                    "minecraft:unbreaking": 3,
                },
            ),
            ("minecraft:crossbow", {"minecraft:piercing": 4, "minecraft:mending": 1}),
            (
                "minecraft:trident",
                {
                    "minecraft:loyalty": 3,
                    "minecraft:impaling": 5,
                    "minecraft:channeling": 1,
                },
            ),
            ("minecraft:trident", {"minecraft:riptide": 3, "minecraft:unbreaking": 3}),
            (
                "minecraft:mace",
                {
                    "minecraft:density": 5,
                    "minecraft:wind_burst": 3,
                    "minecraft:unbreaking": 3,
                },
            ),
            ("minecraft:mace", {"minecraft:breach": 4, "minecraft:mending": 1}),
            (
                "minecraft:diamond_helmet",
                {
                    "minecraft:protection": 4,
                    "minecraft:thorns": 3,
                    "minecraft:unbreaking": 3,
                },
            ),
            (
                "minecraft:diamond_boots",
                {
                    "minecraft:feather_falling": 4,
                    "minecraft:depth_strider": 3,
                    "minecraft:soul_speed": 3,
                    "minecraft:protection": 4,
                },
            ),
            ("minecraft:shield", {"minecraft:unbreaking": 3, "minecraft:mending": 1}),
        ]
        for item_id, enchants in valid_pairs:
            validate_kit(
                _minimal_kit(
                    items=[{"item": item_id, "count": 1, "enchantments": enchants}]
                )
            )

        # Invalid category pairings
        invalid_pairs = [
            ("minecraft:bow", {"minecraft:sharpness": 1}),
            ("minecraft:iron_axe", {"minecraft:knockback": 1}),
            ("minecraft:iron_axe", {"minecraft:fire_aspect": 1}),
            ("minecraft:iron_axe", {"minecraft:sweeping_edge": 1}),
            ("minecraft:iron_sword", {"minecraft:power": 1}),
            ("minecraft:crossbow", {"minecraft:infinity": 1}),
            ("minecraft:bow", {"minecraft:quick_charge": 1}),
            ("minecraft:iron_sword", {"minecraft:loyalty": 1}),
            ("minecraft:iron_sword", {"minecraft:density": 1}),
            ("minecraft:diamond_helmet", {"minecraft:feather_falling": 1}),
            ("minecraft:diamond_chestplate", {"minecraft:depth_strider": 1}),
            ("minecraft:diamond_leggings", {"minecraft:soul_speed": 1}),
            ("minecraft:iron_sword", {"minecraft:protection": 1}),
            ("minecraft:cooked_beef", {"minecraft:unbreaking": 1}),
            ("minecraft:totem_of_undying", {"minecraft:mending": 1}),
            ("minecraft:iron_spear", {"minecraft:sharpness": 1}),
        ]
        for item_id, enchants in invalid_pairs:
            with pytest.raises(KitValidationError):
                validate_kit(
                    _minimal_kit(
                        items=[{"item": item_id, "count": 1, "enchantments": enchants}]
                    )
                )

    def test_enchantment_levels_conflicts_and_max_count(self):
        # Level violations (0, negative, above vanilla max, non-int)
        for item_id, ench, lvl in [
            ("minecraft:diamond_sword", "minecraft:sharpness", 6),
            ("minecraft:diamond_sword", "minecraft:sharpness", 100),
            ("minecraft:diamond_sword", "minecraft:sharpness", 0),
            ("minecraft:diamond_sword", "minecraft:sharpness", -1),
            ("minecraft:diamond_sword", "minecraft:sharpness", True),
            ("minecraft:bow", "minecraft:flame", 2),
            ("minecraft:bow", "minecraft:punch", 3),
            ("minecraft:crossbow", "minecraft:multishot", 2),
            ("minecraft:diamond_chestplate", "minecraft:protection", 5),
            ("minecraft:shield", "minecraft:unbreaking", 4),
            ("minecraft:mace", "minecraft:breach", 5),
            ("minecraft:trident", "minecraft:loyalty", 4),
        ]:
            with pytest.raises(KitValidationError):
                validate_kit(
                    _minimal_kit(
                        items=[
                            {
                                "item": item_id,
                                "count": 1,
                                "enchantments": {ench: lvl},
                            }
                        ]
                    )
                )

        # Unknown enchantment
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {
                            "item": "minecraft:diamond_sword",
                            "count": 1,
                            "enchantments": {"minecraft:super_crit": 1},
                        }
                    ]
                )
            )

        # Conflict groups
        conflicts = [
            (
                "minecraft:diamond_sword",
                {"minecraft:sharpness": 3, "minecraft:smite": 3},
            ),
            (
                "minecraft:diamond_sword",
                {"minecraft:sharpness": 2, "minecraft:bane_of_arthropods": 2},
            ),
            ("minecraft:mace", {"minecraft:density": 3, "minecraft:breach": 2}),
            ("minecraft:mace", {"minecraft:density": 3, "minecraft:smite": 2}),
            (
                "minecraft:diamond_chestplate",
                {"minecraft:protection": 2, "minecraft:fire_protection": 2},
            ),
            (
                "minecraft:diamond_chestplate",
                {"minecraft:blast_protection": 2, "minecraft:projectile_protection": 2},
            ),
            ("minecraft:crossbow", {"minecraft:multishot": 1, "minecraft:piercing": 2}),
            ("minecraft:bow", {"minecraft:infinity": 1, "minecraft:mending": 1}),
            ("minecraft:trident", {"minecraft:loyalty": 2, "minecraft:riptide": 1}),
            ("minecraft:trident", {"minecraft:channeling": 1, "minecraft:riptide": 2}),
            (
                "minecraft:diamond_boots",
                {"minecraft:depth_strider": 2, "minecraft:frost_walker": 1},
            ),
        ]
        for item_id, enchants in conflicts:
            with pytest.raises(KitValidationError):
                validate_kit(
                    _minimal_kit(
                        items=[{"item": item_id, "count": 1, "enchantments": enchants}]
                    )
                )

        # Max 4 enchantments per item (5 rejected)
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {
                            "item": "minecraft:netherite_sword",
                            "count": 1,
                            "enchantments": {
                                "minecraft:sharpness": 5,
                                "minecraft:knockback": 2,
                                "minecraft:fire_aspect": 2,
                                "minecraft:sweeping_edge": 3,
                                "minecraft:unbreaking": 3,
                            },
                        }
                    ]
                )
            )


# ---------------------------------------------------------------------------
# 3. Power Tier & Bot-Readiness + Compilation
# ---------------------------------------------------------------------------


class TestScoringAndCompilation:
    def test_power_tier_progression_and_breakdown(self):
        starter = score_kit(
            _minimal_kit(
                items=[
                    {"item": "minecraft:wooden_sword", "count": 1},
                    {"item": "minecraft:leather_chestplate", "count": 1},
                    {"item": "minecraft:bread", "count": 8},
                ]
            )
        )
        assert starter["tier"] == "Starter"
        assert starter["score"] > 0
        assert set(starter["breakdown"]) == {
            "melee",
            "ranged",
            "armor",
            "offhand",
            "enchantments",
            "consumables",
        }

        fighter_score = score_kit(BUILTIN_KITS["fighter"])
        archer_score = score_kit(BUILTIN_KITS["archer"])
        tank_score = score_kit(BUILTIN_KITS["tank"])
        assert fighter_score["tier"] == "Balanced"
        assert archer_score["tier"] == "Balanced"
        assert tank_score["tier"] in ("Balanced", "Heavy")
        assert fighter_score["score"] > starter["score"]

        champion = score_kit(
            _minimal_kit(
                items=[
                    {
                        "item": "minecraft:netherite_sword",
                        "count": 1,
                        "enchantments": {
                            "minecraft:sharpness": 5,
                            "minecraft:fire_aspect": 2,
                        },
                    },
                    {
                        "item": "minecraft:bow",
                        "count": 1,
                        "enchantments": {"minecraft:power": 5, "minecraft:flame": 1},
                    },
                    {"item": "minecraft:arrow", "count": 64},
                    {
                        "item": "minecraft:netherite_helmet",
                        "count": 1,
                        "enchantments": {"minecraft:protection": 4},
                    },
                    {
                        "item": "minecraft:netherite_chestplate",
                        "count": 1,
                        "enchantments": {"minecraft:protection": 4},
                    },
                    {
                        "item": "minecraft:netherite_leggings",
                        "count": 1,
                        "enchantments": {"minecraft:protection": 4},
                    },
                    {
                        "item": "minecraft:netherite_boots",
                        "count": 1,
                        "enchantments": {
                            "minecraft:protection": 4,
                            "minecraft:feather_falling": 4,
                        },
                    },
                    {"item": "minecraft:totem_of_undying", "count": 1},
                    {"item": "minecraft:golden_apple", "count": 8},
                    {
                        "item": "minecraft:splash_potion",
                        "count": 6,
                        "potion": "minecraft:strong_healing",
                    },
                ]
            )
        )
        assert champion["tier"] == "Champion"
        assert champion["score"] > tank_score["score"]

    def test_bot_readiness_notes(self):
        fighter_notes = score_kit(BUILTIN_KITS["fighter"])["bot_notes"]
        assert fighter_notes == []

        tricky = score_kit(
            _minimal_kit(
                items=[
                    {"item": "minecraft:mace", "count": 1},
                    {"item": "minecraft:crossbow", "count": 1},
                    {"item": "minecraft:totem_of_undying", "count": 1},
                    {
                        "item": "minecraft:lingering_potion",
                        "count": 1,
                        "potion": "minecraft:harming",
                    },
                ]
            )
        )
        joined = " ".join(tricky["bot_notes"]).lower()
        assert "crossbow" in joined
        assert "mace" in joined
        assert "totem" in joined
        assert "lingering" in joined
        assert "arrow" in joined
        assert "food" in joined

    def test_compile_kit_expands_potions_and_large_arrow_stacks(self):
        spec = _minimal_kit(
            items=[
                {
                    "item": "minecraft:bow",
                    "count": 1,
                    "enchantments": {"minecraft:power": 3},
                },
                {"item": "minecraft:arrow", "count": 128},
                {
                    "item": "minecraft:splash_potion",
                    "count": 2,
                    "potion": "minecraft:strong_healing",
                },
            ]
        )
        compiled = compile_kit(spec)
        assert compiled["clear_inventory"] is True
        assert compiled["heal"] is True
        assert compiled["wear"] is True
        arrow_entries = [g for g in compiled["give"] if g["item"] == "minecraft:arrow"]
        assert [g["count"] for g in arrow_entries] == [64, 64]
        potion_entries = [
            g for g in compiled["give"] if g["item"] == "minecraft:splash_potion"
        ]
        assert len(potion_entries) == 2
        assert all(
            p["count"] == 1 and p["potion"] == "minecraft:strong_healing"
            for p in potion_entries
        )


# ---------------------------------------------------------------------------
# 4. Persistent KitLibrary store & host clone/tweak
# ---------------------------------------------------------------------------


class TestKitLibrary:
    def test_crud_persistence_and_builtin_protection(self, tmp_path: Path):
        store_path = tmp_path / "arena-kits.json"
        lib = KitLibrary(store_path)

        # Built-ins available immediately before file exists
        kits = lib.list_kits()
        assert [k["id"] for k in kits] == ["fighter", "archer", "tank", "skirmisher"]
        assert all(k["builtin"] is True for k in kits)
        assert all("power" in k and "compiled" in k for k in kits)
        assert not store_path.exists()

        # Cannot update or delete built-in kits
        with pytest.raises(KitError) as exc_upd:
            lib.update_kit("fighter", _minimal_kit(name="Hacked Fighter"))
        assert exc_upd.value.code == "readonly_builtin"
        with pytest.raises(KitError) as exc_del:
            lib.delete_kit("archer")
        assert exc_del.value.code == "readonly_builtin"
        with pytest.raises(KitError) as exc_dup_builtin:
            lib.create_kit(_minimal_kit(id="tank", name="Fake Tank"))
        assert exc_dup_builtin.value.code == "readonly_builtin"

        # Create custom kit -> persisted to JSON
        created = lib.create_kit(_minimal_kit(name="Shadow Striker"))
        assert created["id"] == "shadow-striker"
        assert created["builtin"] is False
        assert store_path.is_file()

        # Duplicate name gets unique slug automatically
        created2 = lib.create_kit(_minimal_kit(name="Shadow Striker"))
        assert created2["id"] == "shadow-striker-2"

        # Explicit duplicate id rejected
        with pytest.raises(KitError) as exc_dup:
            lib.create_kit(_minimal_kit(id="shadow-striker", name="Again"))
        assert exc_dup.value.code == "duplicate_id"

        # Reload from fresh KitLibrary instance
        lib2 = KitLibrary(store_path)
        listed = lib2.list_kits()
        assert [k["id"] for k in listed[:4]] == [
            "fighter",
            "archer",
            "tank",
            "skirmisher",
        ]
        assert [k["id"] for k in listed[4:]] == ["shadow-striker", "shadow-striker-2"]

        # Update custom kit
        updated = lib2.update_kit(
            "shadow-striker",
            _minimal_kit(
                name="Shadow Striker II",
                items=[
                    {
                        "item": "minecraft:diamond_sword",
                        "count": 1,
                        "enchantments": {"minecraft:sharpness": 2},
                    },
                    {"item": "minecraft:cooked_beef", "count": 16},
                ],
            ),
        )
        assert updated["name"] == "Shadow Striker II"
        assert lib2.get_kit("shadow-striker")["name"] == "Shadow Striker II"

        # Duplicate built-in kit
        dup = lib2.duplicate_kit("fighter", "Arena Fighter+")
        assert dup["builtin"] is False
        assert dup["name"] == "Arena Fighter+"
        assert len(dup["items"]) == len(BUILTIN_KITS["fighter"]["items"])

        # Delete custom kit
        deleted = lib2.delete_kit("shadow-striker-2")
        assert deleted == {"deleted": "shadow-striker-2"}
        assert not lib2.has_kit("shadow-striker-2")
        with pytest.raises(KitError) as exc_missing:
            lib2.get_kit("shadow-striker-2")
        assert exc_missing.value.code == "not_found"

    def test_clone_and_tweak_validates_strictly(self, tmp_path: Path):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        tweaked = lib.clone_and_tweak(
            "fighter",
            "Flame Fighter",
            remove_items=["minecraft:golden_apple"],
            replace_items={
                "minecraft:iron_sword": {
                    "item": "minecraft:diamond_sword",
                    "count": 1,
                    "enchantments": {"minecraft:sharpness": 3},
                }
            },
            add_items=[
                {
                    "item": "minecraft:potion",
                    "count": 1,
                    "potion": "minecraft:swiftness",
                }
            ],
            enchant_tweaks={
                "minecraft:diamond_sword": {"minecraft:fire_aspect": 1},
                "minecraft:iron_boots": {
                    "minecraft:protection": None,
                    "minecraft:fire_protection": 2,
                },
            },
        )
        assert tweaked["builtin"] is False
        by_item = {e["item"]: e for e in tweaked["items"]}
        assert "minecraft:golden_apple" not in by_item
        assert by_item["minecraft:diamond_sword"]["enchantments"] == {
            "minecraft:sharpness": 3,
            "minecraft:fire_aspect": 1,
        }
        assert by_item["minecraft:iron_boots"]["enchantments"] == {
            "minecraft:fire_protection": 2
        }
        assert "minecraft:potion" in by_item

        # Illegal tweak (Sharpness 100 or conflicting protection) rejected without saving
        with pytest.raises(KitValidationError):
            lib.clone_and_tweak(
                "fighter",
                "Illegal Fighter",
                enchant_tweaks={"minecraft:iron_sword": {"minecraft:sharpness": 100}},
            )
        with pytest.raises(KitValidationError):
            lib.clone_and_tweak(
                "fighter",
                "Conflict Fighter",
                enchant_tweaks={
                    "minecraft:iron_helmet": {"minecraft:fire_protection": 2}
                },
            )
        assert not lib.has_kit("illegal-fighter")
        assert not lib.has_kit("conflict-fighter")

    def test_atomic_save_retries_windows_permission_error(
        self, tmp_path: Path, monkeypatch
    ):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked by antivirus")
            return real_replace(src, dst)

        monkeypatch.setattr("mclab.arena_kits.os.replace", flaky_replace)
        monkeypatch.setattr("mclab.arena_kits.time.sleep", lambda _s: None)
        created = lib.create_kit(_minimal_kit(name="Retry Safe"))
        assert created["id"] == "retry-safe"
        assert calls["n"] == 3

    def test_corrupt_store_raises_kit_error(self, tmp_path: Path):
        bad = tmp_path / "arena-kits.json"
        bad.write_text("{not json", encoding="utf-8")
        lib = KitLibrary(bad)
        with pytest.raises(KitError) as exc:
            lib.list_kits()
        assert exc.value.code == "corrupt_store"

    def test_catalog_metadata_shape(self):
        meta = catalog_metadata()
        assert tuple(meta["kit_modes"]) == KIT_MODES
        assert meta["random_sentinel"] == RANDOM_SENTINEL
        assert meta["caps"]["max_give"] == MAX_GIVE
        assert meta["caps"]["max_enchantments_per_item"] == MAX_ENCHANTMENTS_PER_ITEM
        assert meta["caps"]["max_weapons"] == MAX_WEAPONS
        assert meta["caps"]["max_arrows"] == MAX_ARROWS
        assert meta["caps"]["max_food"] == MAX_FOOD
        assert meta["caps"]["max_golden_apples"] == MAX_GOLDEN_APPLES
        assert meta["caps"]["max_potions"] == MAX_POTIONS
        item_ids = {i["id"] for i in meta["items"]}
        assert "minecraft:iron_sword" in item_ids
        assert "minecraft:mace" in item_ids
        assert "minecraft:turtle_helmet" in item_ids
        assert "minecraft:enchanted_golden_apple" not in item_ids
        assert len(meta["potions"]) == 31
        ench_map = {e["id"]: e for e in meta["enchantments"]}
        assert ench_map["minecraft:sharpness"]["max_level"] == 5
        assert "minecraft:smite" in ench_map["minecraft:sharpness"]["conflicts"]


# ---------------------------------------------------------------------------
# 5. Lobby Kit Modes & Deterministic Random Kit Resolver
# ---------------------------------------------------------------------------


class TestMatchKitResolution:
    def test_uniform_mode(self, tmp_path: Path):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        res = resolve_match_kits(
            lib,
            mode="uniform",
            participants=["Dad", "Son", "MCBot"],
            kit_id="tank",
            seed=42,
        )
        assert res["mode"] == "uniform"
        assert res["shared_kit"] == "tank"
        assert res["seed"] == 42
        for p in ("Dad", "Son", "MCBot"):
            assert res["assignments"][p]["kit_id"] == "tank"
            assert res["assignments"][p]["random_rolled"] is False
            assert res["assignments"][p]["compiled"]["wear"] is True
        assert "Tank" in res["recap_summary"]["text"]

    def test_per_team_mode_fixed_and_random(self, tmp_path: Path):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        res = resolve_match_kits(
            lib,
            mode="per_team",
            participants=["Dad", "MCBot", "Son", "MCBot2"],
            teams={"red": ["Dad", "MCBot"], "blue": ["Son", "MCBot2"]},
            team_kits={"red": "fighter", "blue": RANDOM_SENTINEL},
            pool=["archer", "tank", "skirmisher"],
            seed=123,
        )
        assert res["assignments"]["Dad"]["kit_id"] == "fighter"
        assert res["assignments"]["MCBot"]["kit_id"] == "fighter"
        assert res["assignments"]["Dad"]["random_rolled"] is False
        # Both blue members get the SAME rolled kit
        blue_kit = res["assignments"]["Son"]["kit_id"]
        assert blue_kit in ("archer", "tank", "skirmisher")
        assert res["assignments"]["MCBot2"]["kit_id"] == blue_kit
        assert res["assignments"]["Son"]["random_rolled"] is True

        # Missing team kit raises error
        with pytest.raises(KitError) as exc:
            resolve_match_kits(
                lib,
                mode="per_team",
                participants=["Dad", "Son"],
                teams={"red": ["Dad"], "blue": ["Son"]},
                team_kits={"red": "fighter"},
                seed=1,
            )
        assert exc.value.code == "missing_team_kit"

    def test_class_picker_with_individual_random_and_pool_validation(
        self, tmp_path: Path
    ):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        res1 = resolve_match_kits(
            lib,
            mode="class_picker",
            participants=["Dad", "Son", "MCBot"],
            player_choices={"Dad": "archer", "Son": RANDOM_SENTINEL},
            pool=["fighter", "archer", "tank"],
            seed=99,
        )
        res2 = resolve_match_kits(
            lib,
            mode="class_picker",
            participants=["Dad", "Son", "MCBot"],
            player_choices={"Dad": "archer", "Son": RANDOM_SENTINEL},
            pool=["fighter", "archer", "tank"],
            seed=99,
        )
        assert res1 == res2  # 100% deterministic for same seed
        assert res1["assignments"]["Dad"]["kit_id"] == "archer"
        assert res1["assignments"]["Dad"]["random_rolled"] is False
        assert res1["assignments"]["Son"]["requested"] == RANDOM_SENTINEL
        assert res1["assignments"]["Son"]["random_rolled"] is True
        assert res1["assignments"]["Son"]["kit_id"] in ("fighter", "archer", "tank")
        # Unset player falls back to random when fallback enabled
        assert res1["assignments"]["MCBot"]["random_rolled"] is True

        # Choice outside host pool is rejected
        with pytest.raises(KitError) as exc:
            resolve_match_kits(
                lib,
                mode="class_picker",
                participants=["Dad"],
                player_choices={"Dad": "skirmisher"},
                pool=["fighter", "archer"],
                seed=99,
            )
        assert exc.value.code == "invalid_choice"

    def test_everyone_random_ignores_individual_picks_and_is_deterministic(
        self, tmp_path: Path
    ):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        players = ["P1", "P2", "P3", "P4", "P5", "P6"]
        a = resolve_match_kits(
            lib,
            mode="everyone_random",
            participants=players,
            player_choices={"P1": "fighter"},
            seed=2026,
        )
        b = resolve_match_kits(
            lib,
            mode="everyone_random",
            participants=players,
            player_choices={"P1": "tank"},
            seed=2026,
        )
        assert a == b
        assert a["shared_kit"] is None
        assert all(a["assignments"][p]["random_rolled"] is True for p in players)
        assert all(a["assignments"][p]["requested"] == RANDOM_SENTINEL for p in players)
        # Across 6 players & 4 kits, seed=2026 shouldn't force everyone identical unless chance
        # Check auto-seed works when seed is None
        auto = resolve_match_kits(
            lib, mode="everyone_random", participants=["P1", "P2"]
        )
        assert isinstance(auto["seed"], int) and auto["seed"] >= 0

    def test_random_shared_assigns_single_rolled_kit_to_all(self, tmp_path: Path):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        res = resolve_match_kits(
            lib,
            mode="random_shared",
            participants=["Dad", "Son", "MCBot"],
            pool=["fighter", "archer", "tank", "skirmisher"],
            reroll_on_respawn=True,
            seed=777,
        )
        shared = res["shared_kit"]
        assert shared in ("fighter", "archer", "tank", "skirmisher")
        assert res["reroll_on_respawn"] is True
        for p in ("Dad", "Son", "MCBot"):
            assert res["assignments"][p]["kit_id"] == shared
            assert res["assignments"][p]["random_rolled"] is True

    def test_invalid_mode_empty_pool_and_no_participants(self, tmp_path: Path):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        with pytest.raises(KitError) as e_mode:
            resolve_match_kits(lib, mode="chaos", participants=["Dad"])
        assert e_mode.value.code == "invalid_mode"

        with pytest.raises(KitError) as e_pool:
            resolve_match_kits(
                lib, mode="everyone_random", participants=["Dad"], pool=[]
            )
        assert e_pool.value.code == "empty_pool"

        with pytest.raises(KitError) as e_who:
            resolve_match_kits(lib, mode="uniform", participants=[])
        assert e_who.value.code == "no_participants"

    def test_roll_respawn_kit_deterministic_and_avoids_repeat(self, tmp_path: Path):
        lib = KitLibrary(tmp_path / "arena-kits.json")
        pool = ["fighter", "archer"]
        first = roll_respawn_kit(
            lib,
            pool=pool,
            seed=55,
            player="Dad",
            respawn_index=1,
            previous_kit_id="fighter",
        )
        first_again = roll_respawn_kit(
            lib,
            pool=pool,
            seed=55,
            player="Dad",
            respawn_index=1,
            previous_kit_id="fighter",
        )
        assert first == first_again
        assert first["kit_id"] == "archer"
        assert first["random_rolled"] is True
        assert first["compiled"]["clear_inventory"] is True

        # Single-kit pool still works cleanly
        single = roll_respawn_kit(
            lib,
            pool=["tank"],
            seed=55,
            player="Dad",
            respawn_index=1,
            previous_kit_id="tank",
        )
        assert single["kit_id"] == "tank"

        # Invalid arguments to roll_respawn_kit
        with pytest.raises(KitError) as e_seed:
            roll_respawn_kit(lib, pool=pool, seed=True, player="Dad", respawn_index=1)  # type: ignore[arg-type]
        assert e_seed.value.code == "invalid_seed"
        with pytest.raises(KitError) as e_idx:
            roll_respawn_kit(lib, pool=pool, seed=55, player="Dad", respawn_index=0)
        assert e_idx.value.code == "invalid_respawn_index"
        with pytest.raises(KitError) as e_player:
            roll_respawn_kit(lib, pool=pool, seed=55, player=" ", respawn_index=1)
        assert e_player.value.code == "invalid_player"

    def test_additional_edge_cases(self, tmp_path: Path, monkeypatch):
        lib = KitLibrary(tmp_path / "arena-kits.json")

        # Non-dict spec and bad slot/item structures
        with pytest.raises(KitValidationError):
            validate_kit("not-a-dict")  # type: ignore[arg-type]
        with pytest.raises(KitValidationError):
            validate_kit({"name": "Bad Slots", "slots": "nope"})
        with pytest.raises(KitValidationError):
            validate_kit({"name": "Bad Slot Entry", "slots": {"primary": 123}})
        with pytest.raises(KitValidationError):
            validate_kit({"name": "Bad Items Type", "items": "nope"})
        with pytest.raises(KitValidationError):
            validate_kit({"name": "Bad Entry", "items": ["not-a-dict"]})
        with pytest.raises(KitValidationError):
            validate_kit(
                _minimal_kit(
                    items=[
                        {
                            "item": "minecraft:iron_sword",
                            "count": 1,
                            "enchantments": "sharpness",
                        }
                    ]
                )
            )

        # Duplicate with default name + update rename refusal + missing update/delete
        dup_default = lib.duplicate_kit("archer")
        assert dup_default["name"] == "Archer Copy"
        with pytest.raises(KitValidationError):
            lib.update_kit(dup_default["id"], _minimal_kit(id="other-id"))
        with pytest.raises(KitError) as e_upd_miss:
            lib.update_kit("nonexistent", _minimal_kit())
        assert e_upd_miss.value.code == "not_found"
        with pytest.raises(KitError) as e_del_miss:
            lib.delete_kit("nonexistent")
        assert e_del_miss.value.code == "not_found"

        # clone_and_tweak clearing all enchantments on an item
        stripped = lib.clone_and_tweak(
            "fighter",
            "Plain Fighter",
            enchant_tweaks={"minecraft:iron_sword": {"minecraft:sharpness": None}},
        )
        sword = next(e for e in stripped["items"] if e["item"] == "minecraft:iron_sword")
        assert "enchantments" not in sword

        # Resolution edge cases: uniform random, missing team, random_fallback=False, bad seed
        uni_rand = resolve_match_kits(
            lib,
            mode="uniform",
            participants=["Dad", "Son"],
            kit_id=RANDOM_SENTINEL,
            seed=7,
        )
        assert uni_rand["assignments"]["Dad"]["random_rolled"] is True
        assert (
            uni_rand["assignments"]["Dad"]["kit_id"]
            == uni_rand["assignments"]["Son"]["kit_id"]
        )

        with pytest.raises(KitError) as e_team:
            resolve_match_kits(lib, mode="per_team", participants=["Dad"])
        assert e_team.value.code == "missing_team"
        with pytest.raises(KitError) as e_member:
            resolve_match_kits(
                lib,
                mode="per_team",
                participants=["Dad", "Ghost"],
                teams={"red": ["Dad"]},
                team_kits={"red": "fighter"},
            )
        assert e_member.value.code == "missing_team"
        with pytest.raises(KitError) as e_choice:
            resolve_match_kits(
                lib,
                mode="class_picker",
                participants=["Dad"],
                player_choices={},
                random_fallback=False,
            )
        assert e_choice.value.code == "missing_choice"
        with pytest.raises(KitError) as e_seed:
            resolve_match_kits(lib, mode="uniform", participants=["Dad"], seed=False)  # type: ignore[arg-type]
        assert e_seed.value.code == "invalid_seed"

        # Atomic save cleans up temp file when PermissionError persists
        def always_locked(_src, _dst):
            raise PermissionError("locked forever")

        monkeypatch.setattr("mclab.arena_kits.os.replace", always_locked)
        monkeypatch.setattr("mclab.arena_kits.time.sleep", lambda _s: None)
        with pytest.raises(PermissionError):
            lib.create_kit(_minimal_kit(name="Locked Kit"))
        assert list(tmp_path.glob("*.tmp")) == []

    def test_copper_tier_and_default_library(self, tmp_path: Path, monkeypatch):
        from mclab import arena_kits

        spec = validate_kit(
            {
                "name": "Copper Vanguard",
                "items": [
                    {"item": "minecraft:copper_sword", "count": 1},
                    {"item": "minecraft:copper_chestplate", "count": 1},
                ],
            }
        )
        score = score_kit(spec)
        assert score["breakdown"]["melee"] == 10
        assert score["breakdown"]["armor"] == 7

        target = tmp_path / "custom-kits.json"
        monkeypatch.setattr(arena_kits, "DEFAULT_KITS_PATH", target)
        default_lib = arena_kits.default_library()
        assert default_lib.path == target
        assert default_lib.has_kit("fighter")

