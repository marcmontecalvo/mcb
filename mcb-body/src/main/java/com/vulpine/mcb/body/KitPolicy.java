package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Pure survival-legal combat kit policy for arena matches (verified standalone by {@code TrialLogicTest}).
 * Mirrors {@code mclab.arena_kits} so disallowed items, over-leveled enchantments, incompatible enchantments,
 * conflicting enchantments, missing/disallowed potions, and consumable/equipment caps fail identically on both sides.
 */
public final class KitPolicy {
    private KitPolicy() {}

    public static final int MAX_WEAPONS = 6;
    public static final int MAX_ARROWS = 128;
    public static final int MAX_FOOD = 64;
    public static final int MAX_GOLDEN_APPLES = 8;
    public static final int MAX_POTIONS = 16;

    private record ItemSpec(String category, String group, int maxStack, boolean requiresPotion) {}

    private record EnchantSpec(int maxLevel, Set<String> categories) {}

    private static final Map<String, ItemSpec> ITEMS = buildItems();
    private static final Map<String, EnchantSpec> ENCHANTMENTS = buildEnchantments();
    private static final List<Set<String>> CONFLICT_GROUPS = List.of(
            Set.of("minecraft:sharpness", "minecraft:smite", "minecraft:bane_of_arthropods"),
            Set.of("minecraft:density", "minecraft:breach", "minecraft:smite", "minecraft:bane_of_arthropods"),
            Set.of("minecraft:protection", "minecraft:fire_protection", "minecraft:blast_protection",
                    "minecraft:projectile_protection"),
            Set.of("minecraft:multishot", "minecraft:piercing"),
            Set.of("minecraft:infinity", "minecraft:mending"),
            Set.of("minecraft:loyalty", "minecraft:riptide"),
            Set.of("minecraft:channeling", "minecraft:riptide"),
            Set.of("minecraft:depth_strider", "minecraft:frost_walker")
    );

    public static final Set<String> ALLOWED_POTIONS = Set.of(
            "minecraft:healing", "minecraft:strong_healing",
            "minecraft:regeneration", "minecraft:long_regeneration", "minecraft:strong_regeneration",
            "minecraft:swiftness", "minecraft:long_swiftness", "minecraft:strong_swiftness",
            "minecraft:strength", "minecraft:long_strength", "minecraft:strong_strength",
            "minecraft:fire_resistance", "minecraft:long_fire_resistance",
            "minecraft:leaping", "minecraft:long_leaping", "minecraft:strong_leaping",
            "minecraft:harming", "minecraft:strong_harming",
            "minecraft:poison", "minecraft:long_poison", "minecraft:strong_poison",
            "minecraft:weakness", "minecraft:long_weakness",
            "minecraft:slowness", "minecraft:long_slowness", "minecraft:strong_slowness",
            "minecraft:turtle_master", "minecraft:long_turtle_master", "minecraft:strong_turtle_master",
            "minecraft:slow_falling", "minecraft:long_slow_falling"
    );

    private static Map<String, ItemSpec> buildItems() {
        var map = new HashMap<String, ItemSpec>();
        for (String tier : List.of("wooden", "stone", "copper", "golden", "iron", "diamond", "netherite")) {
            for (String weapon : List.of("sword", "axe", "spear")) {
                map.put("minecraft:" + tier + "_" + weapon, new ItemSpec(weapon, "weapon", 1, false));
            }
        }
        map.put("minecraft:mace", new ItemSpec("mace", "weapon", 1, false));
        map.put("minecraft:trident", new ItemSpec("trident", "weapon", 1, false));
        map.put("minecraft:bow", new ItemSpec("bow", "weapon", 1, false));
        map.put("minecraft:crossbow", new ItemSpec("crossbow", "weapon", 1, false));

        for (String tier : List.of("leather", "chainmail", "copper", "golden", "iron", "diamond", "netherite")) {
            for (String piece : List.of("helmet", "chestplate", "leggings", "boots")) {
                map.put("minecraft:" + tier + "_" + piece, new ItemSpec(piece, piece, 1, false));
            }
        }
        map.put("minecraft:turtle_helmet", new ItemSpec("helmet", "helmet", 1, false));

        map.put("minecraft:shield", new ItemSpec("shield", "shield", 1, false));
        map.put("minecraft:totem_of_undying", new ItemSpec("totem", "totem", 1, false));

        map.put("minecraft:arrow", new ItemSpec("ammo", "ammo", 64, false));
        map.put("minecraft:spectral_arrow", new ItemSpec("ammo", "ammo", 64, false));
        map.put("minecraft:tipped_arrow", new ItemSpec("ammo", "ammo", 64, true));

        for (String food : List.of("cooked_beef", "cooked_porkchop", "cooked_chicken", "cooked_mutton",
                "bread", "golden_carrot")) {
            map.put("minecraft:" + food, new ItemSpec("food", "food", 64, false));
        }
        map.put("minecraft:golden_apple", new ItemSpec("golden_apple", "golden_apple", 64, false));

        map.put("minecraft:potion", new ItemSpec("potion", "potion", 64, true));
        map.put("minecraft:splash_potion", new ItemSpec("potion", "potion", 64, true));
        map.put("minecraft:lingering_potion", new ItemSpec("potion", "potion", 64, true));
        return Map.copyOf(map);
    }

    private static Map<String, EnchantSpec> buildEnchantments() {
        var armor = Set.of("helmet", "chestplate", "leggings", "boots");
        var damageable = Set.of("sword", "axe", "spear", "mace", "trident", "bow", "crossbow",
                "helmet", "chestplate", "leggings", "boots", "shield");
        var map = new HashMap<String, EnchantSpec>();
        map.put("minecraft:sharpness", new EnchantSpec(5, Set.of("sword", "axe")));
        map.put("minecraft:smite", new EnchantSpec(5, Set.of("sword", "axe", "mace")));
        map.put("minecraft:bane_of_arthropods", new EnchantSpec(5, Set.of("sword", "axe", "mace")));
        map.put("minecraft:knockback", new EnchantSpec(2, Set.of("sword")));
        map.put("minecraft:fire_aspect", new EnchantSpec(2, Set.of("sword")));
        map.put("minecraft:sweeping_edge", new EnchantSpec(3, Set.of("sword")));

        map.put("minecraft:power", new EnchantSpec(5, Set.of("bow")));
        map.put("minecraft:punch", new EnchantSpec(2, Set.of("bow")));
        map.put("minecraft:flame", new EnchantSpec(1, Set.of("bow")));
        map.put("minecraft:infinity", new EnchantSpec(1, Set.of("bow")));

        map.put("minecraft:quick_charge", new EnchantSpec(3, Set.of("crossbow")));
        map.put("minecraft:multishot", new EnchantSpec(1, Set.of("crossbow")));
        map.put("minecraft:piercing", new EnchantSpec(4, Set.of("crossbow")));

        map.put("minecraft:loyalty", new EnchantSpec(3, Set.of("trident")));
        map.put("minecraft:impaling", new EnchantSpec(5, Set.of("trident")));
        map.put("minecraft:riptide", new EnchantSpec(3, Set.of("trident")));
        map.put("minecraft:channeling", new EnchantSpec(1, Set.of("trident")));

        map.put("minecraft:density", new EnchantSpec(5, Set.of("mace")));
        map.put("minecraft:breach", new EnchantSpec(4, Set.of("mace")));
        map.put("minecraft:wind_burst", new EnchantSpec(3, Set.of("mace")));

        map.put("minecraft:protection", new EnchantSpec(4, armor));
        map.put("minecraft:fire_protection", new EnchantSpec(4, armor));
        map.put("minecraft:blast_protection", new EnchantSpec(4, armor));
        map.put("minecraft:projectile_protection", new EnchantSpec(4, armor));
        map.put("minecraft:thorns", new EnchantSpec(3, armor));

        map.put("minecraft:respiration", new EnchantSpec(3, Set.of("helmet")));
        map.put("minecraft:aqua_affinity", new EnchantSpec(1, Set.of("helmet")));
        map.put("minecraft:swift_sneak", new EnchantSpec(3, Set.of("leggings")));
        map.put("minecraft:feather_falling", new EnchantSpec(4, Set.of("boots")));
        map.put("minecraft:depth_strider", new EnchantSpec(3, Set.of("boots")));
        map.put("minecraft:soul_speed", new EnchantSpec(3, Set.of("boots")));
        map.put("minecraft:frost_walker", new EnchantSpec(2, Set.of("boots")));

        map.put("minecraft:unbreaking", new EnchantSpec(3, damageable));
        map.put("minecraft:mending", new EnchantSpec(1, damageable));
        return Map.copyOf(map);
    }

    /** Validates every stack and aggregate kit limit for arena PvP/PvE play. */
    public static void validateArenaKit(List<TrialLogic.Give> gives) {
        if (gives == null) throw new BodyActions.Fail("bad_request");
        if (gives.size() > TrialLogic.MAX_GIVE) throw new BodyActions.Fail("kit_cap_exceeded");

        int weapons = 0;
        int shields = 0;
        int totems = 0;
        int arrows = 0;
        int food = 0;
        int goldenApples = 0;
        int potions = 0;
        var armorSlots = new HashSet<String>();

        for (var give : gives) {
            var spec = ITEMS.get(give.item());
            if (spec == null) throw new BodyActions.Fail("item_not_allowed");
            if (give.count() < 1 || give.count() > spec.maxStack()) {
                throw new BodyActions.Fail("kit_cap_exceeded");
            }
            if (spec.requiresPotion()) {
                if (give.potion() == null) throw new BodyActions.Fail("potion_required");
                if (!ALLOWED_POTIONS.contains(give.potion())) throw new BodyActions.Fail("potion_not_allowed");
            } else if (give.potion() != null) {
                throw new BodyActions.Fail("bad_request");
            }

            if (give.enchantments() != null && !give.enchantments().isEmpty()) {
                if (give.enchantments().size() > TrialLogic.MAX_ENCHANTMENTS) {
                    throw new BodyActions.Fail("bad_request");
                }
                var seen = new ArrayList<String>();
                for (var entry : give.enchantments().entrySet()) {
                    String enchId = entry.getKey();
                    int lvl = entry.getValue();
                    var ench = ENCHANTMENTS.get(enchId);
                    if (ench == null || !ench.categories().contains(spec.category())) {
                        throw new BodyActions.Fail("incompatible_enchantment");
                    }
                    if (lvl < 1 || lvl > ench.maxLevel()) {
                        throw new BodyActions.Fail("illegal_enchantment_level");
                    }
                    for (String prev : seen) {
                        if (conflicts(prev, enchId)) throw new BodyActions.Fail("conflicting_enchantments");
                    }
                    seen.add(enchId);
                }
            }

            switch (spec.group()) {
                case "weapon" -> weapons += give.count();
                case "helmet", "chestplate", "leggings", "boots" -> {
                    if (!armorSlots.add(spec.group())) throw new BodyActions.Fail("kit_cap_exceeded");
                }
                case "shield" -> shields += give.count();
                case "totem" -> totems += give.count();
                case "ammo" -> arrows += give.count();
                case "food" -> food += give.count();
                case "golden_apple" -> goldenApples += give.count();
                case "potion" -> potions += give.count();
                default -> { }
            }
        }

        if (weapons > MAX_WEAPONS || shields > 1 || totems > 1 || arrows > MAX_ARROWS
                || food > MAX_FOOD || goldenApples > MAX_GOLDEN_APPLES || potions > MAX_POTIONS) {
            throw new BodyActions.Fail("kit_cap_exceeded");
        }
    }

    private static boolean conflicts(String a, String b) {
        if (a.equals(b)) return true;
        for (var group : CONFLICT_GROUPS) {
            if (group.contains(a) && group.contains(b)) return true;
        }
        return false;
    }
}
