package com.vulpine.mcb.body;

import com.google.gson.JsonParser;

public final class TrialLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static TrialLogic.Trial parse(String json) {
        return TrialLogic.parse(JsonParser.parseString(json).getAsJsonObject());
    }

    private static void rejects(String json, String reason) {
        try { parse(json); }
        catch (BodyActions.Fail fail) { check(fail.getMessage().equals(reason), json + " -> " + fail.getMessage()); return; }
        throw new AssertionError("accepted " + json);
    }

    public static void main(String[] args) {
        var none = parse("{}");
        check(none.clear() == 0 && !none.clearInventory() && !none.heal() && none.time() == null, "defaults do nothing");
        check(none.give().isEmpty() && none.summon().isEmpty(), "nothing given or summoned");
        check(!none.wear() && parse("{\"wear\":true}").wear(), "wear puts the kit's gear on before the summon");
        rejects("{\"wear\":1}", "bad_request");

        var full = parse("{\"clear\":32,\"clear_inventory\":true,\"heal\":true,\"time\":18000,"
                + "\"give\":[{\"item\":\"minecraft:iron_sword\",\"count\":1},{\"item\":\"minecraft:arrow\",\"count\":64}],"
                + "\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[8,0,-2],\"helmet\":true},"
                + "{\"type\":\"minecraft:pig\",\"offset\":[-3,0,0]}]}");
        check(full.clear() == 32 && full.clearInventory() && full.heal() && full.time() == 18000, "flags and time");
        check(full.give().size() == 2 && full.give().get(1).item().equals("minecraft:arrow")
                && full.give().get(1).count() == 64, "items in order");
        var zombie = full.summon().get(0);
        check(zombie.type().equals("minecraft:zombie") && zombie.dx() == 8 && zombie.dy() == 0 && zombie.dz() == -2
                && zombie.helmet(), "summon with offset and a helmet (undead do not burn by day)");
        check(!full.summon().get(1).helmet(), "helmet off by default");
        check(!full.surface() && !none.surface(), "the body stays where it is by default");
        check(parse("{\"surface\":true}").surface(), "surface: the scene starts on open ground");
        rejects("{\"surface\":\"yes\"}", "bad_request");

        // where a summon stands: on its column's ground with surface, else level with the body
        check(TrialLogic.summonY(true, 71, 0, 77) == 77, "surface: the summon column's ground");
        check(TrialLogic.summonY(true, 71, 2, 77) == 79, "surface: dy above that ground");
        check(TrialLogic.summonY(false, 71, 0, 77) == 71, "no surface: the body's feet level");
        check(TrialLogic.summonY(false, 71, -1, 77) == 70, "no surface: dy from the feet");

        // distance instead of an offset: anywhere about that far, in sight, on ground level with the body
        var seen = parse("{\"summon\":[{\"type\":\"minecraft:skeleton\",\"distance\":8}]}").summon().get(0);
        check(seen.distance() == 8 && seen.dx() == 0 && seen.dz() == 0, "a distance summon has no offset");
        check(full.summon().get(0).distance() == null, "an offset summon has no distance");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"distance\":8,\"offset\":[8,0,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"distance\":1}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"distance\":33}]}", "bad_request");
        var ring = TrialLogic.ring(8);
        check(ring.get(0)[0] == 8 && ring.get(0)[1] == 0, "east first, where an offset summon stood");
        var keys = new java.util.HashSet<Long>();
        for (int[] c : ring) {
            check(keys.add(((long) c[0] << 32) ^ (c[1] & 0xffffffffL)), "no cell twice");
            double r = Math.hypot(c[0], c[1]);
            check(r >= 5.5 && r <= 10.5, "every cell within two blocks of the distance: " + r);
        }
        check(ring.size() >= 48, "several rings of headings: " + ring.size());
        check(Math.abs(Math.hypot(ring.get(15)[0], ring.get(15)[1]) - 8) <= 0.75, "the exact distance all round first");
        check(TrialLogic.ring(2).stream().allMatch(c -> Math.hypot(c[0], c[1]) >= 1.5), "never on the body");
        check(TrialLogic.level(71, 71) && TrialLogic.level(71, 72) && TrialLogic.level(71, 70), "a step is level");
        check(!TrialLogic.level(71, 73) && !TrialLogic.level(71, 69), "a rise or a drop is not");

        // structured fields only: never a command string
        rejects("{\"clear\":-1}", "bad_request"); rejects("{\"clear\":49}", "bad_request");
        rejects("{\"time\":24000}", "bad_request"); rejects("{\"time\":-1}", "bad_request");
        rejects("{\"heal\":1}", "bad_request"); rejects("{\"give\":{}}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:stone\",\"count\":0}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:stone\",\"count\":257}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"stone; op me\",\"count\":1}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:stone\"}]}", "bad_request");
        // an item may carry enchantments by id and level (a trident's Loyalty)
        var loyal = TrialLogic.parse(JsonParser.parseString(
                "{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,"
                + "\"enchantments\":{\"minecraft:loyalty\":3}}]}").getAsJsonObject());
        check(loyal.give().get(0).enchantments().equals(java.util.Map.of("minecraft:loyalty", 3)), "loyalty III");
        check(full.give().get(0).enchantments().isEmpty(), "no enchantments by default");
        rejects("{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"minecraft:loyalty\":0}}]}",
                "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"minecraft:loyalty\":11}}]}",
                "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"loyalty; op\":1}}]}",
                "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":[1]}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"a:a\":1,\"a:b\":1,"
                + "\"a:c\":1,\"a:d\":1,\"a:e\":1}}]}", "bad_request");
        // a potion item may name its potion (the registry id; BodyTrial refuses an unknown one as unknown_potion)
        var potions = parse("{\"give\":[{\"item\":\"minecraft:splash_potion\",\"count\":2,"
                + "\"potion\":\"minecraft:strong_healing\"},{\"item\":\"minecraft:potion\",\"count\":1}]}");
        check("minecraft:strong_healing".equals(potions.give().get(0).potion()) && potions.give().get(0).count() == 2,
                "a give with a potion parses");
        check(potions.give().get(1).potion() == null && full.give().get(0).potion() == null, "the potion is optional");
        rejects("{\"give\":[{\"item\":\"minecraft:potion\",\"count\":1,\"potion\":\"heal; op me\"}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:potion\",\"count\":1,\"potion\":7}]}", "bad_request");
        rejects("{\"give\":[{\"item\":\"minecraft:potion\",\"count\":1,\"potion\":\"healing\"}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[33,0,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[1,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[1.5,0,0]}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\"}]}", "bad_request");
        rejects("{\"summon\":[{\"type\":\"minecraft:zombie\",\"offset\":[1,0,0],\"nbt\":\"{}\"}]}", "bad_request");
        rejects("{\"command\":\"/op Marc\"}", "bad_request");
        // a boss turns the world over: never summoned by a trial
        rejects("{\"summon\":[{\"type\":\"minecraft:ender_dragon\",\"offset\":[0,10,0]}]}", "not_allowed");
        rejects("{\"summon\":[{\"type\":\"minecraft:wither\",\"offset\":[0,0,5]}]}", "not_allowed");
        var many = new StringBuilder("{\"summon\":[");
        for (int i = 0; i < 9; i++) many.append(i == 0 ? "" : ",").append("{\"type\":\"minecraft:zombie\",\"offset\":[1,0,0]}");
        rejects(many.append("]}").toString(), "bad_request");
        kitPolicy();
        System.out.println("TrialLogicTest ok");
    }

    private static void acceptsKit(String json) {
        KitPolicy.validateArenaKit(parse(json).give());
    }

    private static void rejectsKit(String json, String reason) {
        try {
            KitPolicy.validateArenaKit(parse(json).give());
        } catch (BodyActions.Fail fail) {
            check(fail.getMessage().equals(reason), json + " -> " + fail.getMessage() + " (expected " + reason + ")");
            return;
        }
        throw new AssertionError("accepted illegal arena kit " + json);
    }

    private static void kitPolicy() {
        // Built-in starter classes + copper tier + legacy DEFAULT_KIT must all pass
        acceptsKit("{\"give\":["
                + "{\"item\":\"minecraft:iron_sword\",\"count\":1,\"enchantments\":{\"minecraft:sharpness\":2}},"
                + "{\"item\":\"minecraft:iron_helmet\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:iron_chestplate\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:iron_leggings\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:iron_boots\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:shield\",\"count\":1},"
                + "{\"item\":\"minecraft:cooked_beef\",\"count\":16},"
                + "{\"item\":\"minecraft:golden_apple\",\"count\":2},"
                + "{\"item\":\"minecraft:splash_potion\",\"count\":2,\"potion\":\"minecraft:strong_healing\"}"
                + "]}");
        acceptsKit("{\"give\":["
                + "{\"item\":\"minecraft:bow\",\"count\":1,\"enchantments\":{\"minecraft:power\":2,\"minecraft:punch\":1}},"
                + "{\"item\":\"minecraft:arrow\",\"count\":64},"
                + "{\"item\":\"minecraft:stone_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:chainmail_helmet\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:chainmail_chestplate\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:chainmail_leggings\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:chainmail_boots\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1,\"minecraft:feather_falling\":1}},"
                + "{\"item\":\"minecraft:cooked_beef\",\"count\":16},"
                + "{\"item\":\"minecraft:potion\",\"count\":1,\"potion\":\"minecraft:swiftness\"}"
                + "]}");
        acceptsKit("{\"give\":["
                + "{\"item\":\"minecraft:iron_axe\",\"count\":1},"
                + "{\"item\":\"minecraft:diamond_chestplate\",\"count\":1,\"enchantments\":{\"minecraft:protection\":2}},"
                + "{\"item\":\"minecraft:iron_helmet\",\"count\":1,\"enchantments\":{\"minecraft:protection\":2}},"
                + "{\"item\":\"minecraft:iron_leggings\",\"count\":1,\"enchantments\":{\"minecraft:protection\":2}},"
                + "{\"item\":\"minecraft:iron_boots\",\"count\":1,\"enchantments\":{\"minecraft:protection\":2}},"
                + "{\"item\":\"minecraft:shield\",\"count\":1,\"enchantments\":{\"minecraft:unbreaking\":2}},"
                + "{\"item\":\"minecraft:cooked_beef\",\"count\":16},"
                + "{\"item\":\"minecraft:golden_apple\",\"count\":3},"
                + "{\"item\":\"minecraft:potion\",\"count\":1,\"potion\":\"minecraft:regeneration\"}"
                + "]}");
        acceptsKit("{\"give\":["
                + "{\"item\":\"minecraft:iron_sword\",\"count\":1,\"enchantments\":{\"minecraft:knockback\":1}},"
                + "{\"item\":\"minecraft:crossbow\",\"count\":1,\"enchantments\":{\"minecraft:quick_charge\":1}},"
                + "{\"item\":\"minecraft:arrow\",\"count\":32},"
                + "{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"minecraft:loyalty\":2}},"
                + "{\"item\":\"minecraft:chainmail_helmet\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:chainmail_chestplate\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:chainmail_leggings\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:chainmail_boots\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}},"
                + "{\"item\":\"minecraft:cooked_beef\",\"count\":16},"
                + "{\"item\":\"minecraft:potion\",\"count\":2,\"potion\":\"minecraft:swiftness\"},"
                + "{\"item\":\"minecraft:splash_potion\",\"count\":2,\"potion\":\"minecraft:strong_healing\"}"
                + "]}");
        // Copper tier + legacy DEFAULT_KIT (5 weapons + shield + armor + food + 14 potions)
        acceptsKit("{\"give\":["
                + "{\"item\":\"minecraft:copper_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:copper_chestplate\",\"count\":1}"
                + "]}");
        acceptsKit("{\"give\":["
                + "{\"item\":\"minecraft:iron_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_axe\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_spear\",\"count\":1},"
                + "{\"item\":\"minecraft:bow\",\"count\":1},"
                + "{\"item\":\"minecraft:arrow\",\"count\":64},"
                + "{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"minecraft:loyalty\":3}},"
                + "{\"item\":\"minecraft:shield\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_helmet\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_chestplate\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_leggings\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_boots\",\"count\":1},"
                + "{\"item\":\"minecraft:cooked_beef\",\"count\":16},"
                + "{\"item\":\"minecraft:golden_apple\",\"count\":2},"
                + "{\"item\":\"minecraft:potion\",\"count\":6,\"potion\":\"minecraft:strong_healing\"},"
                + "{\"item\":\"minecraft:splash_potion\",\"count\":8,\"potion\":\"minecraft:strong_healing\"}"
                + "]}");

        // Illegal enchantment levels (including above vanilla max and level <= 10 that TrialLogic allows)
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_sword\",\"count\":1,\"enchantments\":{\"minecraft:sharpness\":6}}]}", "illegal_enchantment_level");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_helmet\",\"count\":1,\"enchantments\":{\"minecraft:protection\":5}}]}", "illegal_enchantment_level");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"minecraft:loyalty\":4}}]}", "illegal_enchantment_level");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:bow\",\"count\":1,\"enchantments\":{\"minecraft:flame\":2}}]}", "illegal_enchantment_level");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:bow\",\"count\":1,\"enchantments\":{\"minecraft:power\":6}}]}", "illegal_enchantment_level");
        rejects("{\"give\":[{\"item\":\"minecraft:iron_sword\",\"count\":1,\"enchantments\":{\"minecraft:sharpness\":100}}]}", "bad_request");

        // Inapplicable enchantments
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_sword\",\"count\":1,\"enchantments\":{\"minecraft:power\":2}}]}", "incompatible_enchantment");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:bow\",\"count\":1,\"enchantments\":{\"minecraft:sharpness\":2}}]}", "incompatible_enchantment");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_sword\",\"count\":1,\"enchantments\":{\"minecraft:protection\":1}}]}", "incompatible_enchantment");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_axe\",\"count\":1,\"enchantments\":{\"minecraft:loyalty\":1}}]}", "incompatible_enchantment");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_helmet\",\"count\":1,\"enchantments\":{\"minecraft:feather_falling\":1}}]}", "incompatible_enchantment");

        // Conflicting enchantments
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_sword\",\"count\":1,\"enchantments\":{\"minecraft:sharpness\":2,\"minecraft:smite\":2}}]}", "conflicting_enchantments");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_chestplate\",\"count\":1,\"enchantments\":{\"minecraft:protection\":2,\"minecraft:blast_protection\":2}}]}", "conflicting_enchantments");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:crossbow\",\"count\":1,\"enchantments\":{\"minecraft:multishot\":1,\"minecraft:piercing\":1}}]}", "conflicting_enchantments");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:trident\",\"count\":1,\"enchantments\":{\"minecraft:loyalty\":1,\"minecraft:riptide\":1}}]}", "conflicting_enchantments");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:bow\",\"count\":1,\"enchantments\":{\"minecraft:infinity\":1,\"minecraft:mending\":1}}]}", "conflicting_enchantments");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:mace\",\"count\":1,\"enchantments\":{\"minecraft:density\":2,\"minecraft:breach\":2}}]}", "conflicting_enchantments");

        // Disallowed / creative / grief items
        for (String badItem : new String[] {
                "minecraft:enchanted_golden_apple", "minecraft:lava_bucket", "minecraft:water_bucket",
                "minecraft:ender_pearl", "minecraft:chorus_fruit", "minecraft:tnt",
                "minecraft:end_crystal", "minecraft:command_block", "minecraft:bedrock",
                "minecraft:flint_and_steel"
        }) {
            rejectsKit("{\"give\":[{\"item\":\"" + badItem + "\",\"count\":1}]}", "item_not_allowed");
        }

        // Potion requirements & allowlist
        rejectsKit("{\"give\":[{\"item\":\"minecraft:potion\",\"count\":1}]}", "potion_required");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:splash_potion\",\"count\":1}]}", "potion_required");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:lingering_potion\",\"count\":1}]}", "potion_required");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:tipped_arrow\",\"count\":8}]}", "potion_required");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:potion\",\"count\":1,\"potion\":\"minecraft:invisibility\"}]}", "potion_not_allowed");

        // Cap violations
        rejectsKit("{\"give\":[{\"item\":\"minecraft:golden_apple\",\"count\":9}]}", "kit_cap_exceeded");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:totem_of_undying\",\"count\":2}]}", "kit_cap_exceeded");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:shield\",\"count\":2}]}", "kit_cap_exceeded");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:iron_helmet\",\"count\":1},{\"item\":\"minecraft:diamond_helmet\",\"count\":1}]}", "kit_cap_exceeded");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:arrow\",\"count\":129}]}", "kit_cap_exceeded");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:cooked_beef\",\"count\":65}]}", "kit_cap_exceeded");
        rejectsKit("{\"give\":[{\"item\":\"minecraft:splash_potion\",\"count\":17,\"potion\":\"minecraft:strong_healing\"}]}", "kit_cap_exceeded");
        rejectsKit("{\"give\":["
                + "{\"item\":\"minecraft:wooden_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:stone_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:diamond_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:netherite_sword\",\"count\":1},"
                + "{\"item\":\"minecraft:iron_axe\",\"count\":1},"
                + "{\"item\":\"minecraft:bow\",\"count\":1}"
                + "]}", "kit_cap_exceeded");
    }
}
