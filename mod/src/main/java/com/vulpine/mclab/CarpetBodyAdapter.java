package com.vulpine.mclab;

import carpet.CarpetSettings;
import carpet.fakes.ServerPlayerInterface;
import carpet.helpers.EntityPlayerActionPack;
import carpet.patches.EntityPlayerMPFake;
import carpet.script.utils.Tracer;
import com.vulpine.mclab.mixin.PlayerStateAccessor;
import com.vulpine.mclab.mixin.FoodStateAccessor;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.players.NameAndId;
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
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.food.FoodData;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** All Carpet implementation imports live here. Calls are exclusively on the server thread. */
public final class CarpetBodyAdapter {
    private final MinecraftServer server;
    private final Vec3 fixture;
    private final String name;
    private final float initialYaw;
    private final float initialPitch;
    private boolean retired;
    private boolean initialized;
    private int stableTicks;
    private Vec3 lastPosition;
    private boolean commandedSpawn;
    private int spawnedAt;
    private boolean sawPlayer;
    private boolean ready;
    private Map<String, Object> referenceSetup = Map.of();

    public CarpetBodyAdapter(MinecraftServer server, Vec3 fixture, String name) {
        this(server, fixture, name, 0, 0);
    }

    public CarpetBodyAdapter(MinecraftServer server, Vec3 fixture, String name, float yaw, float pitch) {
        if (!Float.isFinite(yaw) || !Float.isFinite(pitch) || yaw < -180 || yaw >= 180
                || pitch < -90 || pitch > 90) throw new IllegalArgumentException("invalid_initial_heading");
        this.server = server;
        this.fixture = fixture;
        this.name = name;
        this.initialYaw = yaw;
        this.initialPitch = pitch;
    }

    public String name() { return name; }

    public static boolean isHuman(ServerPlayer player) {
        return !(player instanceof EntityPlayerMPFake);
    }

    public static Map<String, Object> humanReference(ServerPlayer player) {
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

    private static List<Map<String, Object>> inventory(ServerPlayer player) {
        var result = new ArrayList<Map<String, Object>>();
        for (int i = 0; i < player.getInventory().getContainerSize(); i++) {
            var stack = player.getInventory().getItem(i);
            if (!stack.isEmpty()) result.add(Map.of("slot", i, "item",
                    BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(), "count", stack.getCount()));
        }
        return List.copyOf(result);
    }

    public static void prepareHumanInteractions(MinecraftServer server, ServerPlayer player, Vec3 fixture,
                                                boolean combatWall) throws Exception {
        cleanupHumanInteractions(server, fixture);
        int empty = -1;
        for (int i = 8; i >= 0; i--) if (player.getInventory().getItem(i).isEmpty()) { empty = i; break; }
        if (empty < 0) throw new IllegalStateException("human_needs_empty_hotbar_slot");
        int x = (int) (fixture.x + 9.5), y = (int) fixture.y, z = (int) (fixture.z + 11.5);
        var log = new BlockPos(x, y, z+12);
        if (player.getBoundingBox().intersects(new AABB(log))) throw new IllegalStateException("human_inside_log_setup");
        if (combatWall) {
            // Reusable, visible development fixture: approach the cow from +Z (the log side).
            var wall = new AABB(x, y, z+3, x+5, y+2, z+4);
            for (var human : server.getPlayerList().getPlayers()) {
                if (isHuman(human) && human.getBoundingBox().intersects(wall)) {
                    throw new IllegalStateException("human_inside_combat_wall_setup");
                }
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

    public static void cleanupHumanInteractions(MinecraftServer server, Vec3 fixture) {
        int x = (int) (fixture.x + 9.5), y = (int) fixture.y, z = (int) (fixture.z + 11.5);
        for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class,
                new AABB(x-8, y-4, z-8, x+8, y+8, z+20))) {
            if (entity.entityTags().contains("mclab_reference")) entity.discard();
        }
    }

    public ServerPlayer player() {
        var player = server.getPlayerList().getPlayerByName(name);
        return player instanceof EntityPlayerMPFake ? player : null;
    }

    public boolean owns(ServerPlayer player) {
        return name.equals(player.getName().getString()) && player instanceof EntityPlayerMPFake;
    }

    private EntityPlayerActionPack pack() {
        var player = player();
        if (player == null) throw new IllegalStateException("body_not_registered");
        return ((ServerPlayerInterface) player).getActionPack();
    }

    private int command(String command) throws Exception {
        return server.getCommands().getDispatcher().execute(command, server.createCommandSourceStack());
    }

    public int spawn(boolean viaCommand) throws Exception {
        if (retired) throw new IllegalStateException("body_retired");
        if (server.getPlayerList().getPlayerByName(name) != null) {
            throw new IllegalStateException("name_already_registered");
        }
        // This is a dedicated loopback lab; authenticated human joins stay enabled.
        command("carpet allowSpawningOfflinePlayers true");
        if (!CarpetSettings.allowSpawningOfflinePlayers) throw new IllegalStateException("offline_fakes_disabled");
        // Seed only this newly allocated lab identity. Cached resolver add() is memory-only
        // in pinned 26.3; this avoids OldUsersConverter's synchronous Mojang lookup.
        server.services().nameToIdCache().add(NameAndId.createOffline(name));
        commandedSpawn = viaCommand;
        spawnedAt = server.getTickCount();
        if (viaCommand) {
            return command("player " + name + " spawn at " + fixture.x + " " + fixture.y + " "
                    + fixture.z + " facing " + initialYaw + " " + initialPitch + " in minecraft:overworld in survival");
        }
        return EntityPlayerMPFake.createFake(name, server, fixture, initialYaw, initialPitch,
                server.overworld().dimension(), GameType.SURVIVAL, false) ? 1 : 0;
    }

    /** Returns readiness only after registration, explicit state overwrite, and settling. */
    public String pollReady() {
        var player = player();
        if (retired) {
            if (player != null) ((EntityPlayerMPFake) player).kill(Component.literal("Retired lab spawn"));
            return "retired";
        }
        if (player == null) return sawPlayer ? "disconnected_or_dead" : "resolving_profile";
        sawPlayer = true;
        if (!initialized) {
            resetState(0);
            initialized = true;
        }
        if (!player.isAlive()) return "dead";
        if (!player.gameMode.isSurvival()) throw new IllegalStateException("body_not_survival");
        if (ready) return "ready";
        var position = player.position();
        if (lastPosition != null && position.distanceToSqr(lastPosition) < 1e-8 && player.onGround()) {
            stableTicks++;
        } else stableTicks = 0;
        lastPosition = position;
        ready = stableTicks >= 3;
        return ready ? "ready" : "settling";
    }

    public int age() { return server.getTickCount() - spawnedAt; }

    public void resetState(int dirt) {
        referenceSetup = Map.of();
        var player = player();
        if (player == null) throw new IllegalStateException("body_not_registered");
        release();
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
        ((PlayerStateAccessor) player).mclab$setFoodData(new FoodData());
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
        player.teleportTo(server.overworld(), fixture.x, fixture.y, fixture.z, Set.of(), initialYaw, initialPitch, true);
        player.setDeltaMovement(Vec3.ZERO);
        player.getInventory().setSelectedSlot(0);
        if (dirt > 0) player.getInventory().setItem(0, new ItemStack(Items.DIRT, dirt));
        player.onUpdateAbilities();
        stableTicks = 0;
        ready = false;
        lastPosition = null;
    }

    public void restoreFixture() {
        var region = new AABB(fixture.x-3.5, fixture.y-4, fixture.z-2.5,
                fixture.x+3.5, fixture.y+7, fixture.z+8.5);
        for (var player : server.getPlayerList().getPlayers()) {
            if (isHuman(player) && player.getBoundingBox().intersects(region)) {
                throw new IllegalStateException("human_in_mechanics_fixture");
            }
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

    public void setupFixture(boolean log, int dirt, String mechanic) throws Exception {
        if (Set.of("reference_collision", "reference_step", "reference_ledge", "reference_fall").contains(mechanic)) {
            if (log || dirt != 0) throw new IllegalArgumentException("reference_fixture_requires_empty_inventory");
            restoreFixture();
            resetState(0);
            referenceSetup = HumanReferenceFixture.prepare(server, player(), fixture,
                    mechanic.substring("reference_".length()));
            return;
        }
        if (!Set.of("flat", "collision", "step", "ledge", "fall", "combat_near", "combat_far").contains(mechanic)) {
            throw new IllegalArgumentException("unknown_fixture_mechanic");
        }
        restoreFixture();
        resetState(dirt);
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
                var player = player();
                player.teleportTo(server.overworld(), fixture.x, fixture.y+5, fixture.z, Set.of(), 0, 0, true);
                player.setDeltaMovement(Vec3.ZERO);
                player.setOnGround(false);
            }
            case "combat_near", "combat_far" -> {
                double z = fixture.z + (mechanic.equals("combat_near") ? 2 : 4.2);
                command("summon minecraft:cow " + fixture.x + " " + fixture.y + " " + z
                        + " {Silent:1b,PersistenceRequired:1b,Tags:[\"mclab_fixture\"]}");
                for (var entity : server.overworld().getEntitiesOfClass(LivingEntity.class,
                        new AABB(target).inflate(8))) {
                    if (entity.entityTags().contains("mclab_fixture")) {
                        entity.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(0);
                    }
                }
            }
            default -> { }
        }
    }

    public void deathProof() {
        var player = player();
        if (player == null) throw new IllegalStateException("body_not_registered");
        player.hurtServer(server.overworld(), player.damageSources().genericKill(), 1000);
    }

    public void knockbackProof() {
        var player = player();
        if (player == null) throw new IllegalStateException("body_not_registered");
        player.knockback(0.4, 0, -1, player.damageSources().generic(), 1);
    }

    public int commandProof(String operation) throws Exception {
        String suffix = switch (operation) {
            case "forward" -> "move forward";
            case "left" -> "move left";
            case "stop" -> "stop";
            case "look_south" -> "look south";
            case "look_target" -> "look at " + fixture.x + " " + (fixture.y + 0.5) + " " + (fixture.z + 2);
            case "look_floor" -> "look at " + fixture.x + " " + (fixture.y - 0.01) + " " + (fixture.z + 2);
            case "jump" -> "jump once";
            case "mine" -> "attack continuous";
            case "place" -> "use once";
            default -> throw new IllegalArgumentException("unknown_command_proof");
        };
        if (operation.equals("mine") || operation.equals("place")) guardInteractions = true;
        return command("player " + name + " " + suffix);
    }

    public void controls(BridgeRuntime.Controls control) {
        var pack = pack();
        // Preserve a continuous mining action across renewed holds (do not reset break progress).
        pack.setForward((float) control.forward());
        pack.setStrafing((float) control.strafe());
        pack.setSneaking(control.sneak());
        pack.setSprinting(control.sprint());
        pack.turn((float) control.yaw_delta(), (float) control.pitch_delta());
        pack.setSlot(control.hotbar_slot() + 1);
        pack.start(EntityPlayerActionPack.ActionType.JUMP,
                control.jump() ? EntityPlayerActionPack.Action.once() : null);
        if (control.mine()) {
            if (!mining) pack.start(EntityPlayerActionPack.ActionType.ATTACK, EntityPlayerActionPack.Action.continuous());
        } else {
            pack.start(EntityPlayerActionPack.ActionType.ATTACK, control.attack_pulse()
                    ? EntityPlayerActionPack.Action.once() : null);
        }
        mining = control.mine();
        if (control.use_hold()) {
            if (!using) pack.start(EntityPlayerActionPack.ActionType.USE, EntityPlayerActionPack.Action.continuous());
        } else pack.start(EntityPlayerActionPack.ActionType.USE, control.use_pulse()
                ? EntityPlayerActionPack.Action.once() : null);
        using = control.use_hold();
        guardInteractions = control.mine() || control.attack_pulse()
                || control.use_hold() || control.use_pulse();
    }

    private boolean mining;
    private boolean using;
    private boolean guardInteractions;

    /** Carpet traces to 4.5 for entities too. Apply ordinary player's actual reach checks. */
    public boolean guardReach() {
        var player = player();
        if (player == null || !guardInteractions) return false;
        var hit = Tracer.rayTrace(player, 1, 4.5, false);
        boolean legal = true;
        if (hit instanceof EntityHitResult entity) {
            legal = player.isWithinEntityInteractionRange(entity.getEntity(), 0);
        } else if (hit instanceof BlockHitResult block && hit.getType() == HitResult.Type.BLOCK) {
            legal = player.isWithinBlockInteractionRange(block.getBlockPos(), 0);
        }
        if (!legal) {
            pack().start(EntityPlayerActionPack.ActionType.ATTACK, null);
            pack().start(EntityPlayerActionPack.ActionType.USE, null);
            mining = false; using = false; guardInteractions = false;
            return true;
        }
        return false;
    }

    public void release() {
        if (player() != null) pack().stopAll();
        mining = false;
        using = false;
        guardInteractions = false;
    }

    public void retire() {
        release();
        retired = true;
        var player = player();
        if (player != null) ((EntityPlayerMPFake) player).kill(Component.literal("Lab body removed"));
    }

    public Map<String, Object> resetFingerprint() {
        var player = player();
        if (player == null || !player.isAlive() || !player.onGround()
                || player.getHealth() != 20 || player.getFoodData().getFoodLevel() != 20
                || !inventory(player).isEmpty() || !player.getActiveEffects().isEmpty()) {
            throw new IllegalStateException("reset_body_initial_state_invalid");
        }
        var snapshot = observe();
        var result = new java.util.TreeMap<String, Object>();
        for (String key : List.of("position", "velocity", "yaw", "pitch", "on_ground", "health",
                "food", "mode", "pose", "alive", "inventory", "block_reach", "entity_reach",
                "attack_strength", "sprinting")) result.put(key, snapshot.get(key));
        var abilities = player.getAbilities();
        result.put("abilities", Map.of("flying", abilities.flying, "mayfly", abilities.mayfly,
                "invulnerable", abilities.invulnerable, "instabuild", abilities.instabuild,
                "mayBuild", abilities.mayBuild, "walk_speed", abilities.getWalkingSpeed()));
        result.put("saturation", player.getFoodData().getSaturationLevel());
        result.put("exhaustion", ((FoodStateAccessor)player.getFoodData()).mclab$getExhaustion());
        result.put("food_tick_timer", ((FoodStateAccessor)player.getFoodData()).mclab$getTickTimer());
        result.put("effects_empty", player.getActiveEffects().isEmpty());
        result.put("equipment_empty", java.util.Arrays.stream(EquipmentSlot.values())
                .allMatch(slot -> player.getItemBySlot(slot).isEmpty()));
        result.put("on_fire", player.isOnFire());
        result.put("frozen_ticks", player.getTicksFrozen());
        result.put("air", player.getAirSupply());
        result.put("environment", CourseReset.environmentFingerprint(server));
        return result;
    }

    public Map<String, Object> observe() {
        var player = player();
        if (player == null) return Map.of("registered", false, "name", name);
        var inventory = new ArrayList<Map<String, Object>>();
        for (int i = 0; i < player.getInventory().getContainerSize(); i++) {
            var stack = player.getInventory().getItem(i);
            if (!stack.isEmpty()) inventory.add(Map.of("slot", i, "item",
                    BuiltInRegistries.ITEM.getKey(stack.getItem()).toString(), "count", stack.getCount()));
        }
        var p = player.position();
        var v = player.getDeltaMovement();
        var ray = player.pick(player.blockInteractionRange(), 1, false);
        Map<String, Object> hit = Map.of("type", ray.getType().toString());
        if (ray instanceof BlockHitResult block && ray.getType() == HitResult.Type.BLOCK) {
            var pos = block.getBlockPos();
            hit = Map.of("type", "BLOCK", "position", List.of(pos.getX(), pos.getY(), pos.getZ()),
                    "face", block.getDirection().toString(), "block",
                    BuiltInRegistries.BLOCK.getKey(player.level().getBlockState(pos).getBlock()).toString());
        }
        var target = BlockPos.containing(fixture.x, fixture.y, fixture.z + 2);
        return Map.ofEntries(
                Map.entry("registered", true), Map.entry("name", name),
                Map.entry("position", List.of(p.x, p.y, p.z)),
                Map.entry("velocity", List.of(v.x, v.y, v.z)),
                Map.entry("yaw", player.getYRot()), Map.entry("pitch", player.getXRot()),
                Map.entry("on_ground", player.onGround()), Map.entry("health", player.getHealth()),
                Map.entry("food", player.getFoodData().getFoodLevel()),
                Map.entry("mode", player.gameMode.getGameModeForPlayer().getName()),
                Map.entry("pose", player.getPose().toString()), Map.entry("alive", player.isAlive()),
                Map.entry("sprinting", player.isSprinting()),
                Map.entry("reference_setup", referenceSetup),
                Map.entry("inventory", List.copyOf(inventory)), Map.entry("ray", hit),
                Map.entry("qualification_target_block", BuiltInRegistries.BLOCK.getKey(
                        server.overworld().getBlockState(target).getBlock()).toString()),
                Map.entry("block_reach", player.blockInteractionRange()),
                Map.entry("entity_reach", player.entityInteractionRange()),
                Map.entry("attack_strength", player.getAttackStrengthScale(0)),
                Map.entry("qualification_entities", server.overworld().getEntitiesOfClass(LivingEntity.class,
                        new AABB(fixture.x-4, fixture.y-3, fixture.z-4,
                                fixture.x+4, fixture.y+5, fixture.z+9)).stream()
                        .filter(e -> e.entityTags().contains("mclab_fixture"))
                        .map(e -> Map.of("uuid", e.getUUID().toString(), "health", e.getHealth(),
                                "position", List.of(e.getX(), e.getY(), e.getZ()))).toList()),
                Map.entry("spawn_method", commandedSpawn ? "carpet_command" : "carpet_api"));
    }
}
