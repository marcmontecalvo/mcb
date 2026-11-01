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

import java.util.LinkedHashMap;
import java.util.Map;

/** Lab-only: sets a body's inventory and its surroundings (the body never grants itself items). */
final class LabKit {
    private LabKit() {}

    private static final int MAX_RADIUS = 24, MAX_HEIGHT = 16, MAX_PLACE = 64, MAX_STACKS = 64;

    static Map<String, Object> apply(MinecraftServer server, ServerPlayer player, JsonObject request) {
        var level = server.overworld();
        BlockPos feet = player.blockPosition();
        int cleared = 0, placed = 0, stacks = 0;
        if (request.has("clear")) {
            var c = request.getAsJsonArray("clear");
            int dx = c.get(0).getAsInt(), dy = c.get(1).getAsInt(), dz = c.get(2).getAsInt();
            if (dx < 0 || dz < 0 || dy < 0 || dx > MAX_RADIUS || dz > MAX_RADIUS || dy > MAX_HEIGHT) throw new IllegalArgumentException("invalid_clear");
            var air = Blocks.AIR.defaultBlockState();
            for (int x = -dx; x <= dx; x++) for (int z = -dz; z <= dz; z++) {
                var floor = feet.offset(x, -1, z);
                if (level.getBlockState(floor).canBeReplaced()) level.setBlock(floor, Blocks.STONE.defaultBlockState(), 2);
                for (int y = 0; y <= dy; y++) {
                    var pos = feet.offset(x, y, z);
                    if (pos.equals(feet) || pos.equals(feet.above())) continue; // the body stands here
                    if (!level.getBlockState(pos).isAir()) { level.setBlock(pos, air, 2); cleared++; }
                }
            }
            var box = new AABB(feet.getX() - dx, feet.getY(), feet.getZ() - dz, feet.getX() + dx + 1, feet.getY() + dy + 1, feet.getZ() + dz + 1);
            for (var item : level.getEntitiesOfClass(ItemEntity.class, box)) item.discard();
        }
        if (request.has("place")) {
            var list = request.getAsJsonArray("place");
            if (list.size() > MAX_PLACE) throw new IllegalArgumentException("too_many_blocks");
            for (var element : list) {
                var entry = element.getAsJsonObject();
                var offset = entry.getAsJsonArray("offset");
                BlockPos pos = feet.offset(offset.get(0).getAsInt(), offset.get(1).getAsInt(), offset.get(2).getAsInt());
                if (pos.equals(feet) || pos.equals(feet.above())) throw new IllegalArgumentException("offset_inside_body");
                try {
                    var parsed = BlockStateParser.parseForBlock(BuiltInRegistries.BLOCK, entry.get("state").getAsString(), false);
                    level.setBlock(pos, parsed.blockState(), 2);
                } catch (com.mojang.brigadier.exceptions.CommandSyntaxException e) {
                    throw new IllegalArgumentException("invalid_state");
                }
                placed++;
            }
        }
        if (request.has("inventory")) {
            var list = request.getAsJsonArray("inventory");
            if (list.size() > MAX_STACKS) throw new IllegalArgumentException("too_many_stacks");
            player.getInventory().clearContent();
            for (var element : list) {
                var entry = element.getAsJsonObject();
                var id = Identifier.tryParse(entry.get("item").getAsString());
                int count = entry.get("count").getAsInt();
                if (id == null || !BuiltInRegistries.ITEM.containsKey(id)) throw new IllegalArgumentException("unknown_item");
                if (count < 1 || count > 2048) throw new IllegalArgumentException("invalid_count");
                if (!player.getInventory().add(new ItemStack(BuiltInRegistries.ITEM.getValue(id), count)))
                    throw new IllegalArgumentException("inventory_full");
                stacks++;
            }
        }
        var out = new LinkedHashMap<String, Object>();
        out.put("cleared", cleared);
        out.put("placed", placed);
        out.put("stacks", stacks);
        return out;
    }
}
