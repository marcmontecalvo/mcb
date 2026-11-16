package com.vulpine.mcb.body;

import java.util.List;
import java.util.Set;

/** The entity-in-the-way rule of a break (run 20261111T135214868983Z: a sheep froze a break for 15 s). */
public final class BreakTaskTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

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

        // Plug-the-leak material (a fluid flowed into the broken cell): cobblestone, dirt, then other solids.
        java.util.function.Predicate<String> solid = id -> !id.endsWith("torch") && !id.endsWith("_slab");
        check("minecraft:cobblestone".equals(LeakPlug.choose(
                List.of("minecraft:stone", "minecraft:dirt", "minecraft:cobblestone"), solid)), "cobblestone first");
        check("minecraft:dirt".equals(LeakPlug.choose(List.of("minecraft:stone", "minecraft:dirt"), solid)), "dirt second");
        check("minecraft:stone".equals(LeakPlug.choose(List.of("minecraft:torch", "minecraft:stone"), solid)), "other solid");
        check(LeakPlug.choose(List.of(), solid) == null, "no block: no plug");
        check(LeakPlug.choose(List.of("minecraft:torch", "minecraft:oak_slab"), solid) == null, "non-solid only: no plug");
        check(LeakPlug.choose(List.of("minecraft:sand", "minecraft:red_sand", "minecraft:gravel",
                "minecraft:white_concrete_powder"), id -> true) == null, "gravity-only: no plug");
        check(LeakPlug.choose(List.of("minecraft:diamond_ore", "minecraft:deepslate_iron_ore", "minecraft:iron_block",
                "minecraft:gold_block", "minecraft:raw_iron_block"), id -> true) == null, "valuables are kept");
        check("minecraft:sandstone".equals(LeakPlug.choose(List.of("minecraft:sand", "minecraft:sandstone"), id -> true)),
                "sandstone is not a gravity block");
        System.out.println("BreakTaskTest ok");
    }
}
