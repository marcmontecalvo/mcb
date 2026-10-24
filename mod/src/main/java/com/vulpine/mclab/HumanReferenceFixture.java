package com.vulpine.mclab;

import net.minecraft.core.BlockPos;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.List;
import java.util.Map;
import java.util.Set;

/** Explicit G1 setup only. It never drives human inputs or evaluates a scored episode. */
public final class HumanReferenceFixture {
    private HumanReferenceFixture() {}

    private static BlockState state(String block) {
        return switch (block) {
            case "stone" -> Blocks.STONE.defaultBlockState();
            case "stone_slab" -> Blocks.STONE_SLAB.defaultBlockState();
            case "air" -> Blocks.AIR.defaultBlockState();
            default -> throw new IllegalStateException("invalid_reference_block");
        };
    }

    public static Map<String, Object> prepare(MinecraftServer server, ServerPlayer player,
                                               Vec3 fixture, String mechanic) {
        var layout = ReferenceGeometry.plan(mechanic, (int)Math.floor(fixture.x),
                (int)Math.floor(fixture.y), (int)Math.floor(fixture.z));
        var level = server.overworld();
        if (player.level() != level || !player.isAlive() || player.isPassenger()
                || player.gameMode.getGameModeForPlayer() != GameType.SURVIVAL
                || player.getAbilities().flying || player.getMaxHealth() != 20
                || !player.getActiveEffects().isEmpty()) {
            throw new IllegalStateException("reference_requires_ordinary_overworld_survival");
        }
        for (var slot : List.of(EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET)) {
            if (!player.getItemBySlot(slot).isEmpty()) {
                throw new IllegalStateException("reference_requires_no_armor");
            }
        }
        var bounds = new AABB(layout.x()-3, layout.y()-5, layout.z()-2,
                layout.x()+4, layout.y()+6, layout.z()+9);
        for (var other : server.getPlayerList().getPlayers()) {
            if (other != player && CarpetBodyAdapter.isHuman(other) && other.level() == level
                    && other.getBoundingBox().intersects(bounds)) {
                throw new IllegalStateException("other_human_in_reference_fixture");
            }
        }
        for (var cell : layout.cells()) {
            if (!level.getChunkSource().hasChunk(cell.x() >> 4, cell.z() >> 4)) {
                throw new IllegalStateException("reference_fixture_chunks_not_ready");
            }
        }
        var before = Map.of("position", List.of(player.getX(), player.getY(), player.getZ()),
                "health", player.getHealth(), "food", player.getFoodData().getFoodLevel());
        // Preflight completes before any edits. The named player is relocated in this same
        // server-thread setup operation; other humans cannot be inside the modified region.
        for (var cell : layout.cells()) {
            level.setBlockAndUpdate(new BlockPos(cell.x(), cell.y(), cell.z()), state(cell.block()));
        }
        for (var cell : layout.cells()) {
            if (!level.getBlockState(new BlockPos(cell.x(), cell.y(), cell.z())).equals(state(cell.block()))) {
                throw new IllegalStateException("reference_geometry_verification_failed");
            }
        }
        player.setHealth(20);
        player.getFoodData().setFoodLevel(20);
        player.getFoodData().setSaturation(5);
        player.clearFire();
        player.fallDistance = 0;
        player.setSprinting(false);
        var spawn = layout.spawn();
        player.teleportTo(level, spawn.x(), spawn.y(), spawn.z(), Set.of(), 0, 0, true);
        player.setDeltaMovement(Vec3.ZERO);
        player.setOnGround(!mechanic.equals("fall"));
        return Map.of("version", "human-reference-geometry-v1", "mechanic", mechanic,
                "origin", List.of(layout.x(), layout.y(), layout.z()),
                "spawn", List.of(spawn.x(), spawn.y(), spawn.z()),
                "changed_cells", layout.cells().size(), "before", before,
                "verified_cells", layout.cells().size(),
                "initial_health", 20, "initial_food", 20,
                "persistent_development_fixture", true);
    }
}
