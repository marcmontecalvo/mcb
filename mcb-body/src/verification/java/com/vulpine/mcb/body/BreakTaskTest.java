package com.vulpine.mcb.body;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The entity-in-the-way rule of a break (run 20261111T135214868983Z: a sheep froze a break for 15 s). */
public final class BreakTaskTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    /** Held block counts in inventory order, one stack of each. */
    private static Map<String, Integer> held(String... ids) {
        var out = new LinkedHashMap<String, Integer>();
        for (String id : ids) out.put(id, 64);
        return out;
    }

    public static void main(String[] args) {
        // Hit points by distance; a mob covers the two nearest rays.
        var points = List.of(1.0, 2.0, 3.0);
        var mob = Set.of(1.0, 2.0);
        check(BreakTask.clearest(points, d -> d, mob::contains) == 3.0, "aims past the mob at the nearest clear point");
        check(BreakTask.clearest(points, d -> d, d -> false) == 1.0, "no entity: nearest point");
        check(BreakTask.clearest(points, d -> d, d -> true) == null, "every ray blocked: no point");

        // Every ray blocked every tick: the break holds the attack and fails fast, never at the 400-tick timeout.
        int blocked = 0, failedAt = -1;
        for (int tick = 1; tick <= BreakTask.TIMEOUT_TICKS && failedAt < 0; tick++) {
            try { blocked = BreakTask.blockedAfter(blocked, false); }
            catch (BodyActions.Fail fail) { check(fail.getMessage().equals("entity_in_way"), "reason"); failedAt = tick; }
        }
        check(failedAt == BreakTask.ENTITY_WAIT_TICKS + 1, "entity_in_way after the wait, got " + failedAt);

        // A clear ray lets the break go on but keeps the count: blocked ticks add up over the task.
        blocked = 0;
        for (int i = 0; i < BreakTask.ENTITY_WAIT_TICKS; i++) blocked = BreakTask.blockedAfter(blocked, false);
        check(BreakTask.blockedAfter(blocked, true) == BreakTask.ENTITY_WAIT_TICKS, "clear ray keeps the count");

        // A grazing sheep that crosses the ray one tick in three (each crossing aborts the dig): still
        // entity_in_way, after at most ENTITY_WAIT_TICKS held ticks, not the 400-tick timeout.
        blocked = 0; failedAt = -1;
        for (int tick = 1; tick <= BreakTask.TIMEOUT_TICKS && failedAt < 0; tick++) {
            try { blocked = BreakTask.blockedAfter(blocked, tick % 3 != 0); }
            catch (BodyActions.Fail fail) { failedAt = tick; }
        }
        check(failedAt == 3 * (BreakTask.ENTITY_WAIT_TICKS + 1), "flickering mob: entity_in_way, got " + failedAt);
        check(BreakTask.ENTITY_WAIT_TICKS <= 20, "held (still) ticks stay at or under 1 s");

        // Plug-the-leak material (a fluid flowed into the broken cell): cobblestone, dirt, then other solids,
        // each only beyond what the build still has to place (plug_keep).
        java.util.function.Predicate<String> solid = id -> !id.endsWith("torch") && !id.endsWith("_slab");
        check("minecraft:cobblestone".equals(LeakPlug.choose(
                held("minecraft:stone", "minecraft:dirt", "minecraft:cobblestone"), Map.of(), solid)),
                "cobblestone first");
        check("minecraft:dirt".equals(LeakPlug.choose(
                held("minecraft:stone", "minecraft:dirt"), Map.of(), solid)), "dirt second");
        check("minecraft:stone".equals(LeakPlug.choose(held("minecraft:torch", "minecraft:stone"), Map.of(), solid)),
                "other solid");
        check(LeakPlug.choose(Map.of(), Map.of(), solid) == null, "no block: no plug");
        check(LeakPlug.choose(held("minecraft:torch", "minecraft:oak_slab"), Map.of(), solid) == null,
                "non-solid only: no plug");
        check(LeakPlug.choose(held("minecraft:sand", "minecraft:red_sand", "minecraft:gravel",
                "minecraft:white_concrete_powder"), Map.of(), id -> true) == null, "gravity-only: no plug");
        check(LeakPlug.choose(held("minecraft:diamond_ore", "minecraft:deepslate_iron_ore", "minecraft:iron_block",
                "minecraft:gold_block", "minecraft:raw_iron_block"), Map.of(), id -> true) == null, "valuables are kept");
        check("minecraft:sandstone".equals(LeakPlug.choose(held("minecraft:sand", "minecraft:sandstone"), Map.of(),
                id -> true)), "sandstone is not a gravity block");
        // Run 20261121T180806599463Z plugged site-prep water with 21 of the house's cobblestone and ran short.
        var cobble = new java.util.LinkedHashMap<String, Integer>();
        cobble.put("minecraft:cobblestone", 39);
        cobble.put("minecraft:stone_bricks", 64);
        check("minecraft:stone_bricks".equals(LeakPlug.choose(cobble, Map.of("minecraft:cobblestone", 39), solid)),
                "the build's own cobblestone is kept back");
        cobble.put("minecraft:cobblestone", 40);
        check("minecraft:cobblestone".equals(LeakPlug.choose(cobble, Map.of("minecraft:cobblestone", 39), solid)),
                "spare cobblestone beyond the build plugs");
        check(LeakPlug.choose(held("minecraft:dirt"), Map.of("minecraft:dirt", 64), solid) == null,
                "nothing spare: no plug");
        check(LeakPlug.keep(com.google.gson.JsonParser.parseString(
                "{\"items\":{\"minecraft:cobblestone\":39}}").getAsJsonObject())
                .equals(Map.of("minecraft:cobblestone", 39)), "plug_keep parses");
        for (String bad : List.of("{\"items\":{\"cobblestone\":1}}", "{\"items\":{\"minecraft:dirt\":-1}}",
                "{\"items\":[]}", "{}")) {
            boolean refused = false;
            try { LeakPlug.keep(com.google.gson.JsonParser.parseString(bad).getAsJsonObject()); }
            catch (RuntimeException expected) { refused = true; }
            check(refused, "plug_keep refuses " + bad);
        }
        System.out.println("BreakTaskTest ok");
    }
}
