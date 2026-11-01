package com.vulpine.mcb.lab;

import com.google.gson.JsonObject;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.players.NameAndId;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.List;

/**
 * Development follow course: a Carpet stand-in player ("the leader") walks a
 * polyline over the surface at a fixed speed, pausing at each waypoint, while the
 * subject follows it. The leader is moved by teleport each tick, so it crosses any
 * terrain; the follower must find its own way.
 */
final class LeaderWalk {
    static final String NAME = "Leader";
    private static final double MAX_SPEED = 0.4;  // blocks per tick (sprinting is ~0.28)
    private static final int MAX_WAYPOINTS = 32, MAX_PAUSE = 400;

    private final MinecraftServer server;
    private final List<double[]> waypoints = new ArrayList<>();  // x, z
    private final double speed;
    private final int pauseTicks;
    private int next = 1, pauseLeft;
    private double x, z;
    private boolean spawned, finished;

    LeaderWalk(MinecraftServer server, JsonObject spec, Vec3 start) {
        this.server = server;
        speed = spec.get("speed").getAsDouble();
        pauseTicks = spec.get("pause_ticks").getAsInt();
        for (var e : spec.getAsJsonArray("waypoints")) {
            var a = e.getAsJsonArray();
            double wx = a.get(0).getAsInt() + 0.5, wz = a.get(1).getAsInt() + 0.5;
            if (Math.hypot(wx - start.x, wz - start.z) > NaturalRun.MAX_PROBE_RADIUS)
                throw new IllegalArgumentException("leader_waypoint_bounds");
            waypoints.add(new double[]{wx, wz});
        }
        if (!(speed > 0 && speed <= MAX_SPEED) || pauseTicks < 0 || pauseTicks > MAX_PAUSE
                || waypoints.size() < 2 || waypoints.size() > MAX_WAYPOINTS)
            throw new IllegalArgumentException("leader_bounds");
        x = waypoints.get(0)[0]; z = waypoints.get(0)[1];
        pauseLeft = pauseTicks;
    }

    ServerPlayer player() { return server.getPlayerList().getPlayerByName(NAME); }
    boolean finished() { return finished; }
    boolean present() { return player() != null; }

    /** Surface standing height (fluids count as surface, leaves do not). */
    private double surface(double px, double pz) {
        var level = server.overworld();
        return level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, (int) Math.floor(px), (int) Math.floor(pz));
    }

    void spawn() throws Exception {
        if (spawned) return;
        spawned = true;
        var stale = player();
        if (stale != null) command("player " + NAME + " kill");
        command("carpet allowSpawningOfflinePlayers true");
        // Offline identity: no Mojang lookup on the server thread (as the body does).
        server.services().nameToIdCache().add(NameAndId.createOffline(NAME));
        command("player " + NAME + " spawn at " + x + " " + surface(x, z) + " " + z
                + " facing 0 0 in minecraft:overworld in survival");
    }

    /** One tick of walking; call only while the course runs. */
    void tick() throws Exception {
        if (finished || player() == null) return;
        if (pauseLeft > 0) { pauseLeft--; return; }
        var target = waypoints.get(next);
        double dx = target[0] - x, dz = target[1] - z, left = Math.hypot(dx, dz);
        float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
        if (left <= speed) {
            x = target[0]; z = target[1];
            pauseLeft = pauseTicks;
            if (++next >= waypoints.size()) finished = true;
        } else {
            x += dx / left * speed; z += dz / left * speed;
        }
        command(String.format(java.util.Locale.ROOT, "tp %s %.3f %.3f %.3f %.1f 0", NAME, x, surface(x, z), z, yaw));
    }

    void remove() {
        try { if (player() != null) command("player " + NAME + " kill"); }
        catch (Exception ignored) { /* the leader is gone already */ }
    }

    private void command(String command) throws Exception {
        server.getCommands().getDispatcher().execute(command, server.createCommandSourceStack());
    }
}
