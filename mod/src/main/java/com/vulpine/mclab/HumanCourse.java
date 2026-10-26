package com.vulpine.mclab;

import com.google.gson.JsonObject;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.GameType;
import net.minecraft.world.phys.Vec3;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Course reset for a named human client (e.g. the Baritone reference). The human waits
 * in spectator above the arena while the shared CourseReset builds and verifies it, then
 * is reset to the course spawn. Scoring reuses CourseEpisode's causal ledger unchanged.
 */
final class HumanCourse {
    private static final int PARK_HEIGHT = 30; // above the reset envelope's +20 ceiling
    private static final int SETTLE_TICKS = 20;

    private final MinecraftServer server;
    private final UUID uuid;
    private final CourseReset reset;
    private final JsonObject spec;
    private final Vec3 spawn;
    private int placedTick = -1;
    private boolean ready;

    HumanCourse(MinecraftServer server, String name, JsonObject spec, String profileId, JsonObject initial) {
        var player = server.getPlayerList().getPlayerByName(name);
        if (player == null || !CarpetBodyAdapter.isHuman(player)) throw new IllegalArgumentException("human_not_present");
        this.server = server;
        this.uuid = player.getUUID();
        this.spec = spec.deepCopy();
        var o = spec.getAsJsonArray("origin"); var s = spec.getAsJsonArray("spawn");
        spawn = new Vec3(o.get(0).getAsInt() + s.get(0).getAsDouble(), o.get(1).getAsInt() + s.get(1).getAsDouble(),
                o.get(2).getAsInt() + s.get(2).getAsDouble());
        park();
        reset = new CourseReset(server, spec, profileId, initial);
        reset.cleanEntities();
        reset.resetEnvironment();
    }

    ServerPlayer player() { return server.getPlayerList().getPlayer(uuid); }
    boolean owns(ServerPlayer p) { return p.getUUID().equals(uuid); }
    boolean ready() { return ready; }
    JsonObject spec() { return spec; }
    String specHash() { return reset.specHash(); }

    /** Spectator above the arena: outside the reset bounds and unaffected by rebuilt blocks. */
    void park() {
        var p = player();
        if (p == null) return;
        p.gameMode.changeGameModeForPlayer(GameType.SPECTATOR);
        p.teleportTo(server.overworld(), spawn.x, spawn.y + PARK_HEIGHT, spawn.z, Set.of(), 0, 0, true);
        p.setDeltaMovement(Vec3.ZERO);
    }

    /** Advances the reset; returns the completion payload once the human is settled at spawn. */
    Map<String, Object> tick() {
        var p = player();
        if (p == null) throw new IllegalStateException("human_disconnected");
        if (!reset.done()) { reset.step(); return null; }
        if (placedTick < 0) {
            CarpetBodyAdapter.resetPlayer(server, p, spawn.x, spawn.y, spawn.z, yaw(), pitch());
            p.getInventory().setSelectedSlot(0);
            p.onUpdateAbilities();
            placedTick = server.getTickCount();
            return null;
        }
        if (server.getTickCount() < placedTick + SETTLE_TICKS || !p.onGround()) return null;
        var body = verifyAtSpawn();
        ready = true;
        return Map.of("state_digest", CourseReset.digest(Map.of("terrain", reset.terrainDigest(),
                        "spec_hash", specHash(), "subject", "human")),
                "terrain_digest", reset.terrainDigest(), "spec_hash", specHash(), "body_state", body,
                "course_id", reset.courseId(), "player", p.getName().getString());
    }

    /** The client must still be at the reset pose with an empty inventory. */
    Map<String, Object> verifyAtSpawn() {
        var p = player();
        if (p == null) throw new IllegalStateException("human_disconnected");
        String problem = p.position().distanceToSqr(spawn) > 1e-8 ? "position:" + p.position()
                : p.getYRot() != yaw() || p.getXRot() != pitch() ? "heading:" + p.getYRot() + "," + p.getXRot()
                : !p.getInventory().isEmpty() ? "inventory"
                : !p.gameMode.isSurvival() ? "game_mode"
                : p.getHealth() != p.getMaxHealth() ? "health:" + p.getHealth() : null;
        if (problem != null) throw new IllegalStateException("human_changed_since_reset:" + problem);
        return Map.of("position", List.of(p.getX(), p.getY(), p.getZ()), "yaw", p.getYRot(),
                "pitch", p.getXRot(), "inventory", List.of());
    }

    private float yaw() { return spec.get("yaw").getAsFloat(); }
    private float pitch() { return spec.get("pitch").getAsFloat(); }
}
