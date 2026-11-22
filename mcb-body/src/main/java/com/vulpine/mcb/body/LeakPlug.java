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

    /** Cobblestone first: a plug is under the surface, where stone belongs (dirt is the surface layer). */
    private static final List<String> PREFERRED = List.of("minecraft:cobblestone", "minecraft:dirt");
    private static final int MAX_KEEP_KINDS = 512;
    /** Never spent on a plug: gravity blocks fall out of the hole, and these are worth keeping. */
    private static final List<String> VALUABLE = List.of("_ore", "diamond_block", "iron_block", "gold_block",
            "emerald_block", "netherite_block", "lapis_block", "redstone_block", "coal_block", "copper_block",
            "ancient_debris", "raw_", "concrete_powder");
    private static final List<String> GRAVITY = List.of("minecraft:sand", "minecraft:red_sand", "minecraft:gravel");

    private LeakPlug() {}

    /**
     * The item to plug with: cobblestone, then dirt, then any other held block that {@code solid} accepts, each
     * only when more is held than {@code keep} (what the build still has to place) holds back. Run
     * 20261121T180806599463Z plugged site-prep water with 21 of the house's 54 cobblestone and ran short.
     */
    static String choose(Map<String, Integer> held, Map<String, Integer> keep, Predicate<String> solid) {
        Predicate<String> spare = id -> held.getOrDefault(id, 0) > keep.getOrDefault(id, 0);
        for (String id : PREFERRED) if (spare.test(id)) return id;
        for (String id : held.keySet()) {
            if (GRAVITY.contains(id) || VALUABLE.stream().anyMatch(id::contains)) continue;
            if (spare.test(id) && solid.test(id)) return id;
        }
        return null;
    }

    /** A {@code plug_keep} request's {@code items}: block id -> count the plug must not spend. */
    static Map<String, Integer> keep(JsonObject request) {
        if (!request.has("items") || !request.get("items").isJsonObject())
            throw new IllegalArgumentException("plug_keep_items");
        var items = request.getAsJsonObject("items");
        if (items.size() > MAX_KEEP_KINDS) throw new IllegalArgumentException("plug_keep_items");
        var out = new LinkedHashMap<String, Integer>();
        for (var e : items.entrySet()) {
            int n = BodyRuntime.exactInt(e.getValue(), "plug_keep_items");
            if (!e.getKey().startsWith("minecraft:") || n < 0 || n > 1_000_000)
                throw new IllegalArgumentException("plug_keep_items");
            out.put(e.getKey(), n);
        }
        return Map.copyOf(out);
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
    static Map<String, Object> plug(ServerPlayer player, BlockPos pos, Map<String, Integer> keep) {
        var data = new LinkedHashMap<String, Object>();
        data.put("pos", List.of(pos.getX(), pos.getY(), pos.getZ()));
        data.put("fluid", fluidName(player, pos));
        var held = new LinkedHashMap<String, Integer>();
        var inventory = player.getInventory();
        for (int i = 0; i < 36; i++) {
            var stack = inventory.getItem(i);
            if (!stack.isEmpty() && stack.getItem() instanceof BlockItem)
                held.merge(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(), stack.getCount(), Integer::sum);
        }
        String item = choose(held, keep, LeakPlug::solidBlock);
        if (item == null) { data.put("ok", false); data.put("reason", "no_block"); return data; }
        data.put("item", item);
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
