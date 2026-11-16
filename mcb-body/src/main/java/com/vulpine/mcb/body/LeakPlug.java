package com.vulpine.mcb.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.level.block.EntityBlock;
import net.minecraft.world.level.block.FallingBlock;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Predicate;

/**
 * The plug-the-leak reflex: a fluid appears in a cell the body just broke, so a held solid block goes
 * straight back into it. Runs on the server thread (a Python round trip is 0.5-1 s, water spreads a
 * block every 5 ticks); it restores the state before the break, so it needs no route permission.
 */
final class LeakPlug {
    /** Ticks a broken cell is watched (lava in the overworld spreads every 30 ticks). */
    static final int WINDOW = 40;
    /** Ticks a finished break_block waits for a fluid beside the cell to arrive before it reports done. */
    static final int SETTLE = 8;

    private static final List<String> PREFERRED = List.of("minecraft:cobblestone", "minecraft:dirt");
    /** Never spent on a plug: gravity blocks fall out of the hole, and these are worth keeping. */
    private static final List<String> VALUABLE = List.of("_ore", "diamond_block", "iron_block", "gold_block",
            "emerald_block", "netherite_block", "lapis_block", "redstone_block", "coal_block", "copper_block",
            "ancient_debris", "raw_", "concrete_powder");
    private static final List<String> GRAVITY = List.of("minecraft:sand", "minecraft:red_sand", "minecraft:gravel");

    private LeakPlug() {}

    /** The item to plug with: cobblestone, then dirt, then any other held block that {@code solid} accepts. */
    static String choose(List<String> held, Predicate<String> solid) {
        for (String id : PREFERRED) if (held.contains(id)) return id;
        for (String id : held) {
            if (GRAVITY.contains(id) || VALUABLE.stream().anyMatch(id::contains)) continue;
            if (solid.test(id)) return id;
        }
        return null;
    }

    /** A full solid block that is neither a gravity block nor a block entity (chest, furnace, ...). */
    static boolean solidBlock(String id) {
        Identifier key = Identifier.tryParse(id);
        if (key == null || !BuiltInRegistries.BLOCK.containsKey(key)) return false;
        var block = BuiltInRegistries.BLOCK.getValue(key);
        return !(block instanceof FallingBlock) && !(block instanceof EntityBlock)
                && block.defaultBlockState().isCollisionShapeFullBlock(EmptyBlockGetter.INSTANCE, BlockPos.ZERO);
    }

    static boolean fluidAt(ServerPlayer player, BlockPos pos) {
        return !player.level().getFluidState(pos).isEmpty();
    }

    static boolean fluidBeside(ServerPlayer player, BlockPos pos) {
        for (var d : net.minecraft.core.Direction.values()) if (fluidAt(player, pos.relative(d))) return true;
        return false;
    }

    static String fluidName(ServerPlayer player, BlockPos pos) {
        return player.level().getFluidState(pos).is(FluidTags.LAVA) ? "lava" : "water";
    }

    /** Places the best held block into pos; the event data, ok=false with a reason when it cannot. */
    static Map<String, Object> plug(ServerPlayer player, BlockPos pos) {
        var data = new LinkedHashMap<String, Object>();
        data.put("pos", List.of(pos.getX(), pos.getY(), pos.getZ()));
        data.put("fluid", fluidName(player, pos));
        var held = new ArrayList<String>();
        var inventory = player.getInventory();
        for (int i = 0; i < 36; i++) {
            var stack = inventory.getItem(i);
            if (!stack.isEmpty() && stack.getItem() instanceof BlockItem)
                held.add(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
        }
        String item = choose(held, LeakPlug::solidBlock);
        data.put("item", item);
        if (item == null) { data.put("ok", false); data.put("reason", "no_block"); return data; }
        var request = new JsonObject();
        var at = new JsonArray();
        at.add(pos.getX()); at.add(pos.getY()); at.add(pos.getZ());
        request.add("pos", at);
        request.addProperty("state", item);
        request.addProperty("item", item);
        try {
            BodyBuild.place(player, request);
            data.put("ok", true);
        } catch (RuntimeException error) {
            data.put("ok", false);
            data.put("reason", error instanceof BodyActions.Fail ? error.getMessage() : error.getClass().getSimpleName());
        }
        return data;
    }
}
