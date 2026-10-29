package com.vulpine.mcb.body;

import carpet.CarpetSettings;
import carpet.fakes.ServerPlayerInterface;
import carpet.helpers.EntityPlayerActionPack;
import carpet.patches.EntityPlayerMPFake;
import carpet.script.utils.Tracer;
import com.vulpine.mcb.body.mixin.PlayerStateAccessor;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.players.NameAndId;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.Pose;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.food.FoodData;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** All Carpet implementation imports live here. Calls are exclusively on the server thread. */
public final class CarpetBodyAdapter {
    /** Faster than any fall (terminal ~3.9 blocks/tick): the body was moved by someone else. */
    private static final double TELEPORT_DISTANCE_SQR = 16;

    private final MinecraftServer server;
    private final Vec3 spawnAt;
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
    private boolean mining;
    private boolean using;
    private boolean guardInteractions;

    public CarpetBodyAdapter(MinecraftServer server, Vec3 spawnAt, String name, float yaw, float pitch) {
        if (!Float.isFinite(yaw) || !Float.isFinite(pitch) || yaw < -180 || yaw >= 180
                || pitch < -90 || pitch > 90) throw new IllegalArgumentException("invalid_initial_heading");
        this.server = server;
        this.spawnAt = spawnAt;
        this.name = name;
        this.initialYaw = yaw;
        this.initialPitch = pitch;
    }

    public String name() { return name; }

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
            return command("player " + name + " spawn at " + spawnAt.x + " " + spawnAt.y + " "
                    + spawnAt.z + " facing " + initialYaw + " " + initialPitch + " in minecraft:overworld in survival");
        }
        return EntityPlayerMPFake.createFake(name, server, spawnAt, initialYaw, initialPitch,
                server.overworld().dimension(), GameType.SURVIVAL, false) ? 1 : 0;
    }

    /**
     * Ready after registration, explicit state overwrite and settling. A body that the lab
     * (or anyone) parks in another game mode or teleports must settle again before it is ready.
     */
    public String pollReady() {
        var player = player();
        if (retired) {
            if (player != null) ((EntityPlayerMPFake) player).kill(Component.literal("Retired lab spawn"));
            return "retired";
        }
        if (player == null) return sawPlayer ? "disconnected_or_dead" : "resolving_profile";
        sawPlayer = true;
        if (!initialized) {
            resetState();
            initialized = true;
        }
        if (!player.isAlive()) return "dead";
        var position = player.position();
        if (!player.gameMode.isSurvival()) {
            ready = false; stableTicks = 0; lastPosition = position;
            return "not_survival";
        }
        if (lastPosition != null && position.distanceToSqr(lastPosition) > TELEPORT_DISTANCE_SQR) {
            ready = false; stableTicks = 0;
        }
        if (ready) { lastPosition = position; return "ready"; }
        if (lastPosition != null && position.distanceToSqr(lastPosition) < 1e-8 && player.onGround()) {
            stableTicks++;
        } else stableTicks = 0;
        lastPosition = position;
        ready = stableTicks >= 3;
        return ready ? "ready" : "settling";
    }

    public int age() { return server.getTickCount() - spawnedAt; }

    private void resetState() {
        var player = player();
        if (player == null) throw new IllegalStateException("body_not_registered");
        release();
        resetPlayer(player);
        player.getInventory().setSelectedSlot(0);
        player.onUpdateAbilities();
        stableTicks = 0;
        ready = false;
        lastPosition = null;
    }

    /** Ordinary survival player state at the spawn pose; overwrites anything Carpet restored. */
    private void resetPlayer(ServerPlayer player) {
        player.stopRiding();
        player.gameMode.changeGameModeForPlayer(GameType.SURVIVAL);
        player.getInventory().clearContent();
        for (var slot : EquipmentSlot.values()) player.setItemSlot(slot, ItemStack.EMPTY);
        player.removeAllEffects();
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
        ((PlayerStateAccessor) player).mcbbody$setFoodData(new FoodData());
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
        player.teleportTo(server.overworld(), spawnAt.x, spawnAt.y, spawnAt.z, Set.of(), initialYaw, initialPitch, true);
        player.setDeltaMovement(Vec3.ZERO);
    }

    public int commandProof(String operation) throws Exception {
        String suffix = switch (operation) {
            case "forward" -> "move forward";
            case "left" -> "move left";
            case "stop" -> "stop";
            case "look_south" -> "look south";
            case "look_target" -> "look at " + spawnAt.x + " " + (spawnAt.y + 0.5) + " " + (spawnAt.z + 2);
            case "look_floor" -> "look at " + spawnAt.x + " " + (spawnAt.y - 0.01) + " " + (spawnAt.z + 2);
            case "jump" -> "jump once";
            case "mine" -> "attack continuous";
            case "place" -> "use once";
            default -> throw new IllegalArgumentException("unknown_command_proof");
        };
        if (operation.equals("mine") || operation.equals("place")) guardInteractions = true;
        return command("player " + name + " " + suffix);
    }

    public void controls(BodyRuntime.Controls control) {
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
                Map.entry("inventory", List.copyOf(inventory)), Map.entry("ray", hit),
                Map.entry("block_reach", player.blockInteractionRange()),
                Map.entry("entity_reach", player.entityInteractionRange()),
                Map.entry("attack_strength", player.getAttackStrengthScale(0)),
                Map.entry("spawn_method", commandedSpawn ? "carpet_command" : "carpet_api"));
    }
}
