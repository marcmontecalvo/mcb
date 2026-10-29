package com.vulpine.mcb.lab;

import com.google.gson.JsonObject;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.GameType;
import net.minecraft.world.phys.Vec3;

import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Course reset for any named player: a Carpet body, a human or a Baritone client.
 * The subject waits in spectator above the arena while CourseReset builds and verifies it,
 * is then reset to the course spawn, settles, and is fingerprinted across a second terrain
 * verification. course_begin repeats the terrain and subject check before scoring starts.
 */
final class SubjectCourse {
    private static final int PARK_HEIGHT = 30; // above the reset envelope's +20 ceiling
    private static final int SETTLE_TICKS = 20;

    private final MinecraftServer server;
    private final UUID uuid;
    private final String name;
    private final CourseReset reset;
    private final JsonObject spec;
    private final Vec3 spawn;
    private String phase = "build";
    private int placedTick = -1;
    private String firstTerrain = "";
    private String firstSubject = "";

    SubjectCourse(MinecraftServer server, String name, JsonObject spec, String profileId, JsonObject initial) {
        var player = LabFixture.named(server, name);
        this.server = server;
        this.uuid = player.getUUID();
        this.name = name;
        this.spec = spec.deepCopy();
        var o = spec.getAsJsonArray("origin"); var s = spec.getAsJsonArray("spawn");
        spawn = new Vec3(o.get(0).getAsInt() + s.get(0).getAsDouble(), o.get(1).getAsInt() + s.get(1).getAsDouble(),
                o.get(2).getAsInt() + s.get(2).getAsDouble());
        park();
        reset = new CourseReset(server, spec, profileId, initial, uuid);
        reset.cleanEntities();
        reset.resetEnvironment();
    }

    ServerPlayer player() { return server.getPlayerList().getPlayer(uuid); }
    boolean owns(ServerPlayer p) { return p.getUUID().equals(uuid); }
    boolean ready() { return phase.equals("ready"); }
    JsonObject spec() { return spec; }
    String specHash() { return reset.specHash(); }
    Map<String, Object> progress() { return reset.progress(); }

    /** Spectator above the arena: outside the reset bounds and unaffected by rebuilt blocks. */
    void park() {
        var p = player();
        if (p == null) return;
        p.gameMode.changeGameModeForPlayer(GameType.SPECTATOR);
        p.teleportTo(server.overworld(), spawn.x, spawn.y + PARK_HEIGHT, spawn.z, Set.of(), 0, 0, true);
        p.setDeltaMovement(Vec3.ZERO);
    }

    private ServerPlayer present() {
        var p = player();
        if (p == null) throw new IllegalStateException("subject_disconnected");
        return p;
    }

    /** Advances the reset; returns the completion payload once the subject is verified at spawn. */
    Map<String, Object> tick() {
        var p = present();
        reset.ensureHumanClear();
        switch (phase) {
            case "build" -> {
                reset.step();
                if (!reset.done()) return null;
                firstTerrain = reset.terrainDigest();
                LabFixture.resetPlayer(server, p, spawn.x, spawn.y, spawn.z, yaw(), pitch());
                placedTick = server.getTickCount();
                phase = "settle";
            }
            case "settle" -> {
                if (server.getTickCount() < placedTick + SETTLE_TICKS || !p.onGround()) return null;
                if (p.position().distanceToSqr(spawn) > 1e-8 || p.getYRot() != yaw() || p.getXRot() != pitch())
                    throw new IllegalStateException("reset_spawn_mismatch");
                firstSubject = CourseReset.digest(LabFixture.fingerprint(server, p));
                reset.beginVerify();
                phase = "second_verification";
            }
            case "second_verification" -> {
                reset.step();
                if (!reset.done()) return null;
                var physical = LabFixture.fingerprint(server, p);
                String current = CourseReset.digest(physical);
                if (!current.equals(firstSubject) || !reset.terrainDigest().equals(firstTerrain))
                    throw new IllegalStateException("reset_state_changed_during_warmed_verification");
                reset.ensureOnlyBody(name);
                phase = "ready";
                return Map.ofEntries(
                        Map.entry("state_digest", CourseReset.digest(Map.of("terrain", firstTerrain,
                                "body", physical, "spec_hash", specHash()))),
                        Map.entry("terrain_digest", firstTerrain), Map.entry("body_digest", current),
                        Map.entry("spec_hash", specHash()), Map.entry("body_state", physical),
                        Map.entry("profile_id", reset.profileId()), Map.entry("course_id", reset.courseId()),
                        Map.entry("yaw", yaw()), Map.entry("pitch", pitch()), Map.entry("player", name));
            }
            default -> { }
        }
        return null;
    }

    /** Starts the pre-scoring re-verification of terrain and subject. */
    void begin() {
        if (!ready()) throw new IllegalStateException("course_not_reset_ready");
        reset.ensureHumanClear();
        reset.ensureOnlyBody(name);
        if (!CourseReset.digest(LabFixture.fingerprint(server, present())).equals(firstSubject))
            throw new IllegalStateException("body_changed_since_reset");
        reset.beginVerify();
        phase = "begin_verification";
    }

    /** True once begin verification has passed; throws if anything changed since the reset. */
    boolean tickBegin() {
        var p = present();
        reset.ensureHumanClear();
        reset.ensureOnlyBody(name);
        reset.step();
        if (!reset.done()) return false;
        if (!reset.terrainDigest().equals(firstTerrain)
                || !CourseReset.digest(LabFixture.fingerprint(server, p)).equals(firstSubject))
            throw new IllegalStateException("state_changed_since_reset");
        phase = "started";
        return true;
    }

    private float yaw() { return spec.get("yaw").getAsFloat(); }
    private float pitch() { return spec.get("pitch").getAsFloat(); }
}
