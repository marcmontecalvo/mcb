package com.vulpine.mcb.lab;

import com.google.gson.JsonObject;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.AABB;

import com.google.gson.JsonArray;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Lab-only: sets a body's inventory and its surroundings (the body never grants itself items). */
final class LabKit {
    private LabKit() {}

    private static final int MAX_RADIUS = 24, MAX_HEIGHT = 16, MAX_PLACE = 64, MAX_STACKS = 64, MAX_PROBE = 2048;

    /**
     * The cell kit offsets count from: the body's feet, or a fixed {@code at} cell (a course laid
     * out for a person who stands anywhere; scripts/check-scaffold-challenges.py).
     */
    static int[] origin(JsonObject request, int x, int y, int z) {
        if (!request.has("at")) return new int[]{x, y, z};
        var at = request.get("at");
        if (!at.isJsonArray() || at.getAsJsonArray().size() != 3) throw new IllegalArgumentException("invalid_at");
        var a = at.getAsJsonArray();
        return new int[]{a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt()};
    }

    /**
     * {@code probe}: offsets whose block ids the kit reports (after its other work), so a
     * course can be checked as laid without a body's sensing (a person attempts it).
     */
    static List<int[]> probeOffsets(JsonArray list) {
        if (list.size() > MAX_PROBE) throw new IllegalArgumentException("too_many_probes");
        var out = new java.util.ArrayList<int[]>();
        for (var element : list) {
            if (!element.isJsonArray() || element.getAsJsonArray().size() != 3) throw new IllegalArgumentException("invalid_probe");
            var a = element.getAsJsonArray();
            int[] p = {a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt()};
            if (Math.abs(p[0]) > MAX_RADIUS || Math.abs(p[2]) > MAX_RADIUS || Math.abs(p[1]) > 64) throw new IllegalArgumentException("invalid_probe");
            out.add(p);
        }
        return out;
    }

    /** {@code home: true} moves the subject to the {@code at} cell first; it needs {@code at}. */
    static boolean home(JsonObject request) {
        if (!request.has("home") || !request.get("home").getAsBoolean()) return false;
        if (!request.has("at")) throw new IllegalArgumentException("home_needs_at");
        return true;
    }

    /** Item stacks as asked, shape and counts checked; the item ids are checked when given. */
    static List<Map.Entry<String, Integer>> stackSpecs(JsonArray list) {
        if (list.size() > MAX_STACKS) throw new IllegalArgumentException("too_many_stacks");
        var out = new ArrayList<Map.Entry<String, Integer>>();
        for (var element : list) {
            var entry = element.getAsJsonObject();
            String item = entry.get("item").getAsString();
            int count = entry.get("count").getAsInt();
            if (item.isEmpty() || Identifier.tryParse(item) == null) throw new IllegalArgumentException("unknown_item");
            if (count < 1 || count > 2048) throw new IllegalArgumentException("invalid_count");
            out.add(Map.entry(item, count));
        }
        return List.copyOf(out);
    }

    /** Checks every id against the item registry before anything is given. */
    static List<ItemStack> stacks(List<Map.Entry<String, Integer>> specs) {
        var out = new ArrayList<ItemStack>();
        for (var spec : specs) {
            var id = Identifier.tryParse(spec.getKey());
            if (id == null || !BuiltInRegistries.ITEM.containsKey(id)) throw new IllegalArgumentException("unknown_item");
            out.add(new ItemStack(BuiltInRegistries.ITEM.getValue(id), spec.getValue()));
        }
        return out;
    }

    /** Empties the inventory and gives the stacks, the first selected. */
    static int give(ServerPlayer player, List<ItemStack> stacks) {
        player.getInventory().clearContent();
        for (var stack : stacks)
            if (!player.getInventory().add(stack.copy())) throw new IllegalArgumentException("inventory_full");
        player.getInventory().setSelectedSlot(0);
        player.containerMenu.broadcastChanges();
        return stacks.size();
    }

    static Map<String, Object> apply(MinecraftServer server, ServerPlayer player, JsonObject request) {
        var level = server.overworld();
        BlockPos here = player.blockPosition();
        int[] o = origin(request, here.getX(), here.getY(), here.getZ());
        BlockPos feet = new BlockPos(o[0], o[1], o[2]);
        boolean fixed = request.has("at");
        if (fixed && !level.isLoaded(feet)) throw new IllegalArgumentException("at_not_loaded");
        if (home(request)) {
            // A course laid or restored round a fixed origin: the subject goes back to
            // its start first, so no cell of the course is left out for standing in it
            // (the bottom of a shaft stayed open after a restore).
            player.teleportTo(level, feet.getX() + 0.5, feet.getY(), feet.getZ() + 0.5, java.util.Set.of(), player.getYRot(), player.getXRot(), true);
            player.setDeltaMovement(net.minecraft.world.phys.Vec3.ZERO);
            player.fallDistance = 0;
        }
        BlockPos body = player.blockPosition();
        int cleared = 0, placed = 0, stacks = 0, skipped = 0;
        if (request.has("clear")) {
            var c = request.getAsJsonArray("clear");
            int dx = c.get(0).getAsInt(), dy = c.get(1).getAsInt(), dz = c.get(2).getAsInt();
            if (dx < 0 || dz < 0 || dy < 0 || dx > MAX_RADIUS || dz > MAX_RADIUS || dy > MAX_HEIGHT) throw new IllegalArgumentException("invalid_clear");
            var air = Blocks.AIR.defaultBlockState();
            for (int x = -dx; x <= dx; x++) for (int z = -dz; z <= dz; z++) {
                var floor = feet.offset(x, -1, z);
                if (level.getBlockState(floor).canBeReplaced()) level.setBlock(floor, Blocks.STONE.defaultBlockState(), 2);
                for (int y = 0; y <= dy; y++) {
                    // The body's own cells too: air never harms it, and with home the body stands on
                    // the start, where scaffolding left by the attempt would outlive the restore
                    // (review of e029b74). Only place keeps out of them.
                    var pos = feet.offset(x, y, z);
                    if (!level.getBlockState(pos).isAir()) { level.setBlock(pos, air, 2); cleared++; }
                }
            }
            var box = new AABB(feet.getX() - dx, feet.getY(), feet.getZ() - dz, feet.getX() + dx + 1, feet.getY() + dy + 1, feet.getZ() + dz + 1);
            for (var item : level.getEntitiesOfClass(ItemEntity.class, box)) item.discard();
            for (var mob : level.getEntitiesOfClass(net.minecraft.world.entity.Mob.class, box)) mob.discard(); // fixtures from earlier kits
        }
        if (request.has("place")) {
            var list = request.getAsJsonArray("place");
            if (list.size() > MAX_PLACE) throw new IllegalArgumentException("too_many_blocks");
            for (var element : list) {
                var entry = element.getAsJsonObject();
                var offset = entry.getAsJsonArray("offset");
                BlockPos pos = feet.offset(offset.get(0).getAsInt(), offset.get(1).getAsInt(), offset.get(2).getAsInt());
                if (pos.equals(body) || pos.equals(body.above())) {
                    // Offsets from the body never name its own cells; a fixed origin may, where a
                    // person happens to stand, and those cells are left as they are.
                    if (!fixed) throw new IllegalArgumentException("offset_inside_body");
                    skipped++;
                    continue;
                }
                try {
                    var parsed = BlockStateParser.parseForBlock(BuiltInRegistries.BLOCK, entry.get("state").getAsString(), false);
                    level.setBlock(pos, parsed.blockState(), 2);
                    if (entry.has("contents")) fill(level, pos, entry.getAsJsonArray("contents"));
                } catch (com.mojang.brigadier.exceptions.CommandSyntaxException e) {
                    throw new IllegalArgumentException("invalid_state");
                }
                placed++;
            }
        }
        if (request.has("inventory")) stacks = give(player, stacks(stackSpecs(request.getAsJsonArray("inventory"))));
        if (request.has("food")) {
            int food = request.get("food").getAsInt();
            if (food < 0 || food > 20) throw new IllegalArgumentException("invalid_food");
            player.getFoodData().setFoodLevel(food);
            player.getFoodData().setSaturation(0f);
        }
        var summoned = request.has("summon") ? summon(level, feet, request.getAsJsonArray("summon")) : java.util.List.<Map<String, Object>>of();
        var out = new LinkedHashMap<String, Object>();
        if (request.has("nearby")) out.put("nearby", nearby(level, player, request.get("nearby").getAsDouble()));
        out.put("summoned", summoned.size());
        out.put("summoned_entities", summoned);
        out.put("cleared", cleared);
        out.put("placed", placed);
        out.put("skipped", skipped);
        out.put("stacks", stacks);
        out.put("feet", List.of(body.getX(), body.getY(), body.getZ()));
        if (request.has("probe")) {
            var ids = new java.util.ArrayList<String>();
            for (int[] p : probeOffsets(request.getAsJsonArray("probe")))
                ids.add(BuiltInRegistries.BLOCK.getKey(level.getBlockState(feet.offset(p[0], p[1], p[2])).getBlock()).toString());
            out.put("probed", ids);
        }
        return out;
    }

    private static final int MAX_SUMMON = 16;

    /**
     * Fixture simplification: summoned mobs get setNoAi(true) and persistence so they stay where put
     * (they neither wander nor despawn); a live animal would walk out of reach during a check.
     */
    private static java.util.List<Map<String, Object>> summon(net.minecraft.server.level.ServerLevel level, BlockPos feet, com.google.gson.JsonArray list) {
        if (list.size() > MAX_SUMMON) throw new IllegalArgumentException("too_many_entities");
        var made = new java.util.ArrayList<Map<String, Object>>();
        for (var element : list) {
            var entry = element.getAsJsonObject();
            var id = Identifier.tryParse(entry.get("entity").getAsString());
            if (id == null || !BuiltInRegistries.ENTITY_TYPE.containsKey(id)) throw new IllegalArgumentException("unknown_entity");
            var offset = entry.getAsJsonArray("offset");
            var entity = BuiltInRegistries.ENTITY_TYPE.getValue(id).create(level, net.minecraft.world.entity.EntitySpawnReason.COMMAND);
            if (entity == null) throw new IllegalArgumentException("cannot_create_entity");
            entity.setPos(feet.getX() + offset.get(0).getAsInt() + 0.5, feet.getY() + offset.get(1).getAsInt(), feet.getZ() + offset.get(2).getAsInt() + 0.5);
            if (entity instanceof net.minecraft.world.entity.Mob mob) {
                mob.setPersistenceRequired();
                mob.setNoAi(true);
                if (entry.has("baby") && entry.get("baby").getAsBoolean() && mob instanceof net.minecraft.world.entity.AgeableMob ageable) ageable.setAge(-24000);
            }
            level.addFreshEntity(entity);
            made.add(Map.of("entity", id.toString(), "uuid", entity.getUUID().toString()));
        }
        return made;
    }

    /** Puts stacks into a chest, barrel or shulker box (first free slots; stacks split at their maximum size). */
    private static void fill(net.minecraft.server.level.ServerLevel level, BlockPos pos, com.google.gson.JsonArray contents) {
        if (!(level.getBlockEntity(pos) instanceof net.minecraft.world.Container container)) throw new IllegalArgumentException("not_a_container");
        for (var element : contents) {
            var entry = element.getAsJsonObject();
            var id = Identifier.tryParse(entry.get("item").getAsString());
            int count = entry.get("count").getAsInt();
            if (id == null || !BuiltInRegistries.ITEM.containsKey(id)) throw new IllegalArgumentException("unknown_item");
            if (count < 1 || count > 2048) throw new IllegalArgumentException("invalid_count");
            var item = BuiltInRegistries.ITEM.getValue(id);
            while (count > 0) {
                var stack = new ItemStack(item, Math.min(count, new ItemStack(item).getMaxStackSize()));
                int slot = 0;
                while (slot < container.getContainerSize() && !container.getItem(slot).isEmpty()) slot++;
                if (slot >= container.getContainerSize()) throw new IllegalArgumentException("container_full");
                container.setItem(slot, stack);
                count -= stack.getCount();
            }
        }
        container.setChanged();
    }

    /** Entities (not the body) within a radius of the body: type, uuid, position, item stacks, sheep state. */
    private static java.util.List<Map<String, Object>> nearby(net.minecraft.server.level.ServerLevel level, ServerPlayer player, double radius) {
        if (radius <= 0 || radius > 32) throw new IllegalArgumentException("invalid_radius");
        var out = new java.util.ArrayList<Map<String, Object>>();
        for (var e : level.getEntities(player, player.getBoundingBox().inflate(radius))) {
            var entry = new LinkedHashMap<String, Object>();
            entry.put("type", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
            entry.put("uuid", e.getUUID().toString());
            entry.put("position", java.util.List.of(e.getX(), e.getY(), e.getZ()));
            if (e instanceof ItemEntity item) {
                entry.put("item", BuiltInRegistries.ITEM.getKey(item.getItem().getItem()).toString());
                entry.put("count", item.getItem().getCount());
                var held = item.getItem().get(net.minecraft.core.component.DataComponents.CONTAINER);
                if (held != null) {
                    var inside = new java.util.ArrayList<Map<String, Object>>();
                    held.nonEmptyItemCopyStream().forEach(s -> inside.add(Map.of("item", BuiltInRegistries.ITEM.getKey(s.getItem()).toString(), "count", s.getCount())));
                    entry.put("container", inside);
                }
            }
            if (e instanceof net.minecraft.world.entity.animal.sheep.Sheep sheep) entry.put("sheared", sheep.isSheared());
            out.add(entry);
        }
        return out;
    }
}
