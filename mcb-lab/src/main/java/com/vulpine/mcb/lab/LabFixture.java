package com.vulpine.mcb.lab;

import com.vulpine.mcb.lab.mixin.FoodStateAccessor;
import com.vulpine.mcb.lab.mixin.PlayerStateAccessor;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.Pose;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.food.FoodData;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * Lab-side player and fixture operations. Works on any named player (Carpet body, human,
 * Baritone client); never imports Carpet. Calls are exclusively on the server thread.
 */
final class LabFixture {
    private LabFixture() {}

    static ServerPlayer named(MinecraftServer server, String name) {
        var player = server.getPlayerList().getPlayerByName(name);
        if (player == null) throw new IllegalArgumentException("player_not_present");
        return player;
    }

    static Map<String, Object> reference(ServerPlayer player) {
        var input = player.getLastClientInput();
        var velocity = player.getDeltaMovement();
        return Map.ofEntries(Map.entry("name", player.getName().getString()),
                Map.entry("position", List.of(player.getX(), player.getY(), player.getZ())),
                Map.entry("velocity", List.of(velocity.x, velocity.y, velocity.z)),
                Map.entry("on_ground", player.onGround()),
                Map.entry("horizontal_collision", player.horizontalCollision),
                Map.entry("sprinting", player.isSprinting()),
                Map.entry("attack_strength", player.getAttackStrengthScale(0)),
                Map.entry("pose", player.getPose().toString()),
                Map.entry("yaw", player.getYRot()), Map.entry("pitch", player.getXRot()),
                Map.entry("mode", player.gameMode.getGameModeForPlayer().getName()),
                Map.entry("health", player.getHealth()),
                Map.entry("block_reach", player.blockInteractionRange()),
                Map.entry("entity_reach", player.entityInteractionRange()),
                Map.entry("inventory", inventory(player)),
                Map.entry("buttons", Map.of("forward", input.forward(), "backward", input.backward(),
                        "left", input.left(), "right", input.right(), "jump", input.jump(),
                        "sneak", input.shift(), "sprint", input.sprint())),
                Map.entry("alignment", "server-observed latest client input; client timestamp and network delay unknown"));
    }

    static List<Map<String, Object>> inventory(ServerPlayer player) {
        var result = new ArrayList<Map<String, Object>>();
        for (int i = 0; i < player.getInventory().getContainerSize(); i++) {
            var stack = player.getInventory().getItem(i);
            if (!stack.isEmpty()) result.add(Map.of("slot", i, "item",
                    BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(), "count", stack.getCount()));
        }
        return List.copyOf(result);
    }

    /** Any player other than the subject inside the region. */
    static void requireNoOtherPlayer(MinecraftServer server, ServerPlayer subject, AABB region, String reason) {
        for (var other : server.getPlayerList().getPlayers()) {
            if (other != subject && other.level() == server.overworld() && other.getBoundingBox().intersects(region))
                throw new IllegalStateException(reason);
        }
    }

    static void prepareInteractions(MinecraftServer server, ServerPlayer player, Vec3 fixture,
                                    boolean combatWall) throws Exception {
        cleanupInteractions(server, fixture);
        int empty = -1;
        for (int i = 8; i >= 0; i--) if (player.getInventory().getItem(i).isEmpty()) { empty = i; break; }
        if (empty < 0) throw new IllegalStateException("human_needs_empty_hotbar_slot");
        int x = (int) (fixture.x + 9.5), y = (int) fixture.y, z = (int) (fixture.z + 11.5);
        var log = new BlockPos(x, y, z+12);
        if (player.getBoundingBox().intersects(new AABB(log))) throw new IllegalStateException("human_inside_log_setup");
        if (combatWall) {
            // Reusable, visible development fixture: approach the cow from +Z (the log side).
            var wall = new AABB(x, y, z+3, x+5, y+2, z+4);
            for (var any : server.getPlayerList().getPlayers()) {
                if (any.getBoundingBox().intersects(wall)) throw new IllegalStateException("human_inside_combat_wall_setup");
            }
            for (int dx = 0; dx < 5; dx++) for (int dy = 0; dy < 2; dy++) {
                var state = server.overworld().getBlockState(new BlockPos(x+dx, y+dy, z+3));
                if (!state.isAir() && !state.is(Blocks.STONE)) {
                    throw new IllegalStateException("combat_wall_obstructed");
                }
            }
            for (int dx = 0; dx < 5; dx++) for (int dy = 0; dy < 2; dy++) {
                server.overworld().setBlockAndUpdate(new BlockPos(x+dx, y+dy, z+3),
                        Blocks.STONE.defaultBlockState());
            }
        }
        server.overworld().setBlockAndUpdate(log, Blocks.OAK_LOG.defaultBlockState());
        player.getInventory().setItem(empty, new ItemStack(Items.DIRT, 16));
        player.containerMenu.broadcastChanges();
        server.getCommands().getDispatcher().execute("summon minecraft:cow " + (x+2.5) + " " + y + " " + (z+4.5)
                + " {Silent:1b,PersistenceRequired:1b,Tags:[\"mclab_reference\"]}", server.createCommandSourceStack());
        for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class,
                new AABB(x-8, y-4, z-8, x+8, y+8, z+20))) {
            if (entity.entityTags().contains("mclab_reference")) entity.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(0);
        }
    }

    static void cleanupInteractions(MinecraftServer server, Vec3 fixture) {
        int x = (int) (fixture.x + 9.5), y = (int) fixture.y, z = (int) (fixture.z + 11.5);
        for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class,
                new AABB(x-8, y-4, z-8, x+8, y+8, z+20))) {
            if (entity.entityTags().contains("mclab_reference")) entity.discard();
        }
    }

    /** Ordinary survival player state at a pose; shared by every scored subject. */
    static void resetPlayer(MinecraftServer server, ServerPlayer player, double x, double y, double z,
                            float yaw, float pitch) {
        player.stopRiding();
        player.gameMode.changeGameModeForPlayer(GameType.SURVIVAL);
        player.getInventory().clearContent();
        for (var slot : EquipmentSlot.values()) player.setItemSlot(slot, ItemStack.EMPTY);
        player.removeAllEffects();
        // Reset every instantiated attribute to its ordinary player default.
        var defaults = Player.createAttributes().build();
        for (var value : BuiltInRegistries.ATTRIBUTE) {
            var attribute = BuiltInRegistries.ATTRIBUTE.wrapAsHolder(value);
            var instance = player.getAttribute(attribute);
            if (instance != null) {
                instance.removeModifiers();
                instance.setBaseValue(defaults.getBaseValue(attribute));
            }
        }
        player.setHealth(player.getMaxHealth());
        ((PlayerStateAccessor) player).mcblab$setFoodData(new FoodData());
        player.getAbilities().flying = false;
        player.getAbilities().mayfly = false;
        player.getAbilities().instabuild = false;
        player.getAbilities().invulnerable = false;
        player.getAbilities().mayBuild = true;
        player.getAbilities().setWalkingSpeed(0.1F);
        player.getAbilities().setFlyingSpeed(0.05F);
        player.setPermanentlyInvulnerable(false);
        player.setPose(Pose.STANDING);
        player.clearFire();
        player.clearFreeze();
        player.fallDistance = 0;
        player.hurtTime = 0;
        player.setInvulnerableTime(0);
        player.teleportTo(server.overworld(), x, y, z, Set.of(), yaw, pitch, true);
        player.setDeltaMovement(Vec3.ZERO);
        player.getInventory().setSelectedSlot(0);
        player.onUpdateAbilities();
    }

    /** The fixture subject (by name) may stand inside; anyone else blocks the rebuild. */
    static void restoreFixture(MinecraftServer server, Vec3 fixture, String subjectName) {
        var region = new AABB(fixture.x-3.5, fixture.y-4, fixture.z-2.5,
                fixture.x+3.5, fixture.y+7, fixture.z+8.5);
        for (var player : server.getPlayerList().getPlayers()) {
            if (!player.getName().getString().equals(subjectName) && player.getBoundingBox().intersects(region))
                throw new IllegalStateException("human_in_mechanics_fixture");
        }
        for (int x = -3; x <= 3; x++) for (int z = -2; z <= 8; z++) {
            for (int y = -4; y <= 5; y++) {
                var pos = BlockPos.containing(fixture.x+x, fixture.y+y, fixture.z+z);
                server.overworld().setBlockAndUpdate(pos, y < 0 ? Blocks.STONE.defaultBlockState()
                        : Blocks.AIR.defaultBlockState());
            }
        }
        for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class, region)) {
            if (entity.entityTags().contains("mclab_fixture")) entity.discard();
        }
    }

    /**
     * Rebuilds the mechanics fixture and resets the subject at its spawn. The subject is
     * excluded from the fixture-occupancy check (it is about to be reset in place).
     * Returns the reference setup for reference_* mechanics, otherwise an empty map.
     */
    static Map<String, Object> setup(MinecraftServer server, ServerPlayer subject, Vec3 fixture,
                                     boolean log, int dirt, String mechanic) throws Exception {
        boolean reference = Set.of("reference_collision", "reference_step", "reference_ledge", "reference_fall")
                .contains(mechanic);
        if (reference && (log || dirt != 0)) throw new IllegalArgumentException("reference_fixture_requires_empty_inventory");
        if (!reference && !Set.of("flat", "collision", "step", "ledge", "fall", "combat_near", "combat_far").contains(mechanic)) {
            throw new IllegalArgumentException("unknown_fixture_mechanic");
        }
        restoreFixture(server, fixture, subject.getName().getString());
        resetPlayer(server, subject, fixture.x, fixture.y, fixture.z, 0, 0);
        if (reference) return HumanReferenceFixture.prepare(server, subject, fixture, mechanic.substring("reference_".length()));
        if (dirt > 0) subject.getInventory().setItem(0, new ItemStack(Items.DIRT, dirt));
        var target = BlockPos.containing(fixture.x, fixture.y, fixture.z + 2);
        // Disposable mechanics fixture only; never world-wide cleanup.
        for (var item : server.overworld().getEntitiesOfClass(ItemEntity.class,
                new AABB(target).inflate(4))) item.discard();
        server.overworld().setBlockAndUpdate(target, log ? Blocks.OAK_LOG.defaultBlockState()
                : Blocks.AIR.defaultBlockState());
        server.overworld().setBlockAndUpdate(target.above(), Blocks.AIR.defaultBlockState());
        switch (mechanic) {
            case "collision" -> {
                server.overworld().setBlockAndUpdate(target, Blocks.STONE.defaultBlockState());
                server.overworld().setBlockAndUpdate(target.above(), Blocks.STONE.defaultBlockState());
            }
            case "step" -> server.overworld().setBlockAndUpdate(target, Blocks.STONE_SLAB.defaultBlockState());
            case "ledge" -> {
                for (int x = -3; x <= 3; x++) for (int z = 2; z <= 7; z++) for (int y = -4; y < 0; y++) {
                    server.overworld().setBlockAndUpdate(BlockPos.containing(fixture.x+x, fixture.y+y, fixture.z+z),
                            Blocks.AIR.defaultBlockState());
                }
            }
            case "fall" -> {
                subject.teleportTo(server.overworld(), fixture.x, fixture.y+5, fixture.z, Set.of(), 0, 0, true);
                subject.setDeltaMovement(Vec3.ZERO);
                subject.setOnGround(false);
            }
            case "combat_near", "combat_far" -> {
                double z = fixture.z + (mechanic.equals("combat_near") ? 2 : 4.2);
                server.getCommands().getDispatcher().execute("summon minecraft:cow " + fixture.x + " " + fixture.y + " " + z
                        + " {Silent:1b,PersistenceRequired:1b,Tags:[\"mclab_fixture\"]}", server.createCommandSourceStack());
                for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class,
                        new AABB(target).inflate(8))) {
                    if (entity.entityTags().contains("mclab_fixture")) {
                        entity.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(0);
                    }
                }
            }
            default -> { }
        }
        return Map.of();
    }

    /** What the qualification fixture looks like now: target block and tagged entities. */
    static Map<String, Object> fixtureState(MinecraftServer server, Vec3 fixture) {
        var target = BlockPos.containing(fixture.x, fixture.y, fixture.z + 2);
        return Map.of("qualification_target_block", BuiltInRegistries.BLOCK.getKey(
                        server.overworld().getBlockState(target).getBlock()).toString(),
                "qualification_entities", server.overworld().getEntitiesOfClass(LivingEntity.class,
                        new AABB(fixture.x-4, fixture.y-3, fixture.z-4,
                                fixture.x+4, fixture.y+5, fixture.z+9)).stream()
                        .filter(e -> e.entityTags().contains("mclab_fixture"))
                        .map(e -> Map.<String, Object>of("uuid", e.getUUID().toString(), "health", e.getHealth(),
                                "position", List.of(e.getX(), e.getY(), e.getZ()))).toList());
    }

    static void deathProof(MinecraftServer server, ServerPlayer player) {
        player.hurtServer(server.overworld(), player.damageSources().genericKill(), 1000);
    }

    static void knockbackProof(ServerPlayer player) {
        player.knockback(0.4, 0, -1, player.damageSources().generic(), 1);
    }

    /** Authoritative initial-state fingerprint of a reset subject. Throws if not pristine. */
    static Map<String, Object> fingerprint(MinecraftServer server, ServerPlayer player) {
        if (player == null || !player.isAlive() || !player.onGround()
                || player.getHealth() != 20 || player.getFoodData().getFoodLevel() != 20
                || !inventory(player).isEmpty() || !player.getActiveEffects().isEmpty()) {
            throw new IllegalStateException("reset_body_initial_state_invalid");
        }
        var p = player.position();
        var v = player.getDeltaMovement();
        var result = new TreeMap<String, Object>();
        result.put("position", List.of(p.x, p.y, p.z));
        result.put("velocity", List.of(v.x, v.y, v.z));
        result.put("yaw", player.getYRot());
        result.put("pitch", player.getXRot());
        result.put("on_ground", player.onGround());
        result.put("health", player.getHealth());
        result.put("food", player.getFoodData().getFoodLevel());
        result.put("mode", player.gameMode.getGameModeForPlayer().getName());
        result.put("pose", player.getPose().toString());
        result.put("alive", player.isAlive());
        result.put("inventory", inventory(player));
        result.put("block_reach", player.blockInteractionRange());
        result.put("entity_reach", player.entityInteractionRange());
        result.put("attack_strength", player.getAttackStrengthScale(0));
        result.put("sprinting", player.isSprinting());
        var abilities = player.getAbilities();
        result.put("abilities", Map.of("flying", abilities.flying, "mayfly", abilities.mayfly,
                "invulnerable", abilities.invulnerable, "instabuild", abilities.instabuild,
                "mayBuild", abilities.mayBuild, "walk_speed", abilities.getWalkingSpeed()));
        result.put("saturation", player.getFoodData().getSaturationLevel());
        result.put("exhaustion", ((FoodStateAccessor)player.getFoodData()).mcblab$getExhaustion());
        result.put("food_tick_timer", ((FoodStateAccessor)player.getFoodData()).mcblab$getTickTimer());
        result.put("effects_empty", player.getActiveEffects().isEmpty());
        result.put("equipment_empty", java.util.Arrays.stream(EquipmentSlot.values())
                .allMatch(slot -> player.getItemBySlot(slot).isEmpty()));
        result.put("on_fire", player.isOnFire());
        result.put("frozen_ticks", player.getTicksFrozen());
        result.put("air", player.getAirSupply());
        result.put("environment", CourseReset.environmentFingerprint(server));
        return result;
    }
}
