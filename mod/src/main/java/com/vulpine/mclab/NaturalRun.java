package com.vulpine.mclab;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.clock.WorldClocks;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.BiConsumer;

/**
 * Development-only natural-terrain route: reach a goal area in an ordinary generated
 * world. Breaking and placing are unrestricted; the world is restored from a snapshot
 * between attempts by the operator. Not part of any qualified course profile.
 */
final class NaturalRun {
    static final int MAX_PROBE_RADIUS = 192;
    private static final int GOAL_DY = 4;
    private static final int HUMAN_SETTLE_TICKS = 20;
    private static final int KIT_BLOCKS = 64;

    private final MinecraftServer server;
    private final BiConsumer<String, Map<String, Object>> events;
    private final CarpetBodyAdapter body; // null for a human subject
    private final HumanCourse.Subject human;
    private final Vec3 start, goal;
    private final float yaw;
    private final double radius;
    private final int tickLimit;
    private final boolean mobs;
    private String status = "preparing", reason = "";
    private int readyTick = -1, startTick = -1, deadlineTick = -1, endTick = -1;
    private int blocksBroken, peakHostiles;
    private double damageTaken, minDistance = Double.MAX_VALUE;
    private float lastHealth = -1;

    NaturalRun(MinecraftServer server, JsonObject request, CarpetBodyAdapter body, HumanCourse.Subject human,
               BiConsumer<String, Map<String, Object>> events) {
        this.server = server; this.events = events; this.body = body; this.human = human;
        start = cell(request.getAsJsonArray("start"));
        goal = cell(request.getAsJsonArray("goal"));
        yaw = request.get("yaw").getAsFloat();
        radius = request.get("goal_radius").getAsDouble();
        tickLimit = request.get("tick_limit").getAsInt();
        mobs = request.get("mobs").getAsBoolean();
        if (radius < 1 || radius > 8 || tickLimit < 200 || tickLimit > 24000 || yaw < -180 || yaw >= 180)
            throw new IllegalArgumentException("natural_bounds");
        if (start.distanceTo(goal) > MAX_PROBE_RADIUS * 2) throw new IllegalArgumentException("natural_route_too_long");
        prepareWorld();
        if (human != null) {
            var p = human.player();
            if (p == null) throw new IllegalArgumentException("human_not_present");
            CarpetBodyAdapter.resetPlayer(server, p, start.x, start.y, start.z, yaw, 0);
            readyTick = server.getTickCount() + HUMAN_SETTLE_TICKS;
        }
    }

    static Vec3 cell(JsonArray a) {
        return new Vec3(a.get(0).getAsInt() + 0.5, a.get(1).getAsInt(), a.get(2).getAsInt() + 0.5);
    }

    Vec3 start() { return start; }
    float yaw() { return yaw; }
    boolean running() { return status.equals("running"); }
    boolean terminal() { return !status.equals("preparing") && !running(); }

    ServerPlayer subject() { return body != null ? body.player() : human.player(); }

    boolean owns(ServerPlayer p) {
        return body != null ? body.owns(p) : human.owns(p);
    }

    /** Same time, weather, mob rule and empty slate for every attempt and both subjects. */
    private void prepareWorld() {
        var clock = server.registryAccess().lookupOrThrow(Registries.WORLD_CLOCK).getOrThrow(WorldClocks.OVERWORLD);
        var manager = server.overworld().clockManager();
        manager.setTotalTicks(clock, mobs ? 18000 : 6000); // midnight with mobs, noon without
        manager.setRate(clock, 1);
        manager.setPaused(clock, true);
        var weather = server.overworld().getWeatherData();
        weather.setRaining(false); weather.setThundering(false);
        server.overworld().setRainLevel(0); server.overworld().setThunderLevel(0);
        try {
            // Setting a rule to its current value is reported as a failure, so flip it first.
            var dispatcher = server.getCommands().getDispatcher();
            dispatcher.execute("gamerule minecraft:spawn_mobs " + !mobs, server.createCommandSourceStack());
            dispatcher.execute("gamerule minecraft:spawn_mobs " + mobs, server.createCommandSourceStack());
        } catch (Exception error) { throw new IllegalStateException("spawn_mobs_rule:" + error.getMessage(), error); }
        var area = routeArea();
        server.overworld().getEntitiesOfClass(Mob.class, area, m -> m instanceof Enemy).forEach(Mob::discard);
        server.overworld().getEntitiesOfClass(ItemEntity.class, area).forEach(ItemEntity::discard);
    }

    private AABB routeArea() {
        return new AABB(start, goal).inflate(64, 64, 64);
    }

    private static void giveKit(ServerPlayer p) {
        var inventory = p.getInventory();
        inventory.clearContent();
        inventory.setItem(0, new ItemStack(Items.IRON_PICKAXE));
        inventory.setItem(1, new ItemStack(Items.IRON_SHOVEL));
        inventory.setItem(2, new ItemStack(Items.IRON_AXE));
        inventory.setItem(3, new ItemStack(Items.COBBLESTONE, KIT_BLOCKS));
        inventory.setItem(4, new ItemStack(Items.DIRT, KIT_BLOCKS));
        inventory.setSelectedSlot(0);
        p.containerMenu.broadcastChanges();
    }

    /** Called every tick; bodyReady reports the Carpet body's settle state. */
    void tick(boolean bodyReady) {
        var p = subject();
        if (status.equals("preparing")) {
            if (p == null) return;
            boolean ready = body != null ? bodyReady : server.getTickCount() >= readyTick && p.onGround();
            if (!ready) return;
            giveKit(p);
            status = "running"; startTick = server.getTickCount(); deadlineTick = startTick + tickLimit;
            lastHealth = p.getHealth();
            events.accept("course_started", Map.of("start_tick", startTick, "deadline_tick", deadlineTick,
                    "subject", body != null ? "carpet_body" : "human", "mobs", mobs));
            return;
        }
        if (!running()) return;
        if (p == null || !p.isAlive()) { finish("failed", "death_or_disconnect"); return; }
        if (p.getHealth() < lastHealth) damageTaken += lastHealth - p.getHealth();
        lastHealth = p.getHealth();
        int hostiles = server.overworld().getEntitiesOfClass(Mob.class, p.getBoundingBox().inflate(16),
                m -> m instanceof Enemy).size();
        peakHostiles = Math.max(peakHostiles, hostiles);
        double horizontal = Math.hypot(p.getX() - goal.x, p.getZ() - goal.z);
        minDistance = Math.min(minDistance, horizontal);
        if (horizontal <= radius && Math.abs(p.getY() - goal.y) <= GOAL_DY) { finish("success", "reached_goal"); return; }
        if (server.getTickCount() >= deadlineTick) finish("failed", "tick_limit");
    }

    void broke(ServerPlayer p, boolean success) {
        if (running() && success && owns(p)) blocksBroken++;
    }

    void finish(String outcome, String why) {
        if (terminal()) return;
        status = outcome; reason = why; endTick = server.getTickCount();
        events.accept("course_episode_complete", fullState());
    }

    Map<String, Object> publicState() {
        return Map.of("status", status, "reason", reason, "start_tick", startTick, "deadline_tick", deadlineTick);
    }

    private Map<String, Object> fullState() {
        var result = new LinkedHashMap<String, Object>(publicState());
        var p = subject();
        result.put("end_tick", endTick);
        result.put("ticks", startTick < 0 ? -1 : endTick - startTick);
        result.put("min_goal_distance", minDistance == Double.MAX_VALUE ? -1 : minDistance);
        result.put("damage_taken", damageTaken);
        result.put("peak_hostiles_within_16", peakHostiles);
        result.put("blocks_broken", blocksBroken);
        int left = p == null ? -1 : p.getInventory().countItem(Items.COBBLESTONE) + p.getInventory().countItem(Items.DIRT);
        // Kit blocks used; picked-up dirt/cobblestone reduces this, so it is a lower bound on placements.
        result.put("kit_blocks_used_estimate", p == null ? -1 : 2 * KIT_BLOCKS - left);
        if (p != null) result.put("final_position", List.of(p.getX(), p.getY(), p.getZ()));
        result.put("mobs", mobs);
        return Map.copyOf(result);
    }

    /** Surface heights (no leaves) and water flags on a grid; generates chunks as needed. */
    static List<List<Object>> probe(MinecraftServer server, int cx, int cz, int radius, int stride) {
        if (radius < 8 || radius > MAX_PROBE_RADIUS || stride < 2 || stride > 16)
            throw new IllegalArgumentException("probe_bounds");
        var level = server.overworld();
        var rows = new ArrayList<List<Object>>();
        for (int x = cx - radius; x <= cx + radius; x += stride) {
            for (int z = cz - radius; z <= cz + radius; z += stride) {
                level.getChunk(x >> 4, z >> 4);
                int h = level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z);
                var ground = level.getBlockState(new BlockPos(x, h - 1, z));
                rows.add(List.of(x, z, h, !ground.getFluidState().isEmpty(),
                        net.minecraft.core.registries.BuiltInRegistries.BLOCK.getKey(ground.getBlock()).getPath()));
            }
        }
        return rows;
    }
}
