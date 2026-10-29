package com.vulpine.mcb.lab;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.particles.DustParticleOptions;
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
import java.util.UUID;
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
    private final String subjectName;
    private final String subjectKind; // reporting label only: carpet_body, human, baritone, ...
    private UUID subjectId;
    private int presentTicks;
    private final int subjectDeadline;
    private final Vec3 start, goal;
    private final float yaw;
    private final double radius;
    private final int tickLimit;
    private final boolean mobs;
    private String status = "preparing", reason = "";
    private int readyTick = -1, startTick = -1, deadlineTick = -1, endTick = -1;
    private int blocksBroken, peakHostiles;
    private double damageTaken, minDistance = Double.MAX_VALUE;
    private double minFeetY,maxFeetY;
    private int hostileExposureTicks;
    private int waterTicks, minimumAir;
    private boolean died, disconnected;
    private float lastHealth = -1;
    private final DevelopmentHill hill;
    private ServerPlayer lastSubject;
    private int kitBlocksPickedUp;
    private final Map<String,Integer> buildingPickups = new LinkedHashMap<>();
    // Viewing aids and the stall cutoff. Both are optional and default off.
    private static final DustParticleOptions PATH_DUST = new DustParticleOptions(0xFF2020, 1.0f);
    private static final DustParticleOptions GOAL_DUST = new DustParticleOptions(0x20FF40, 2.0f);
    private static final int MARKER_INTERVAL_TICKS = 4, MAX_PATH_POINTS = 64;
    private static final double STALL_MOVE_BLOCKS = 1.0;
    private final boolean showMarkers;
    private final int stallTicks;
    private List<Vec3> path = List.of();
    private Vec3 stallAnchor;
    private int stallSince;

    /** The subject may join after this request (a body spawned in parallel); it is resolved by name. */
    NaturalRun(MinecraftServer server, JsonObject request, String subjectName, String subjectKind,
               BiConsumer<String, Map<String, Object>> events) {
        this.server = server; this.events = events;
        this.subjectName = subjectName; this.subjectKind = subjectKind;
        subjectDeadline = server.getTickCount() + LabRuntime.SUBJECT_JOIN_TICKS;
        start = cell(request.getAsJsonArray("start"));
        goal = cell(request.getAsJsonArray("goal"));
        yaw = request.get("yaw").getAsFloat();
        radius = request.get("goal_radius").getAsDouble();
        tickLimit = request.get("tick_limit").getAsInt();
        mobs = request.get("mobs").getAsBoolean();
        showMarkers = request.has("show_markers") && request.get("show_markers").getAsBoolean();
        stallTicks = request.has("stall_ticks") ? request.get("stall_ticks").getAsInt() : 0;
        if (stallTicks != 0 && (stallTicks < 40 || stallTicks > 2400))
            throw new IllegalArgumentException("natural_stall_ticks");
        if (radius < 1 || radius > 8 || tickLimit < 200 || tickLimit > 24000 || yaw < -180 || yaw >= 180)
            throw new IllegalArgumentException("natural_bounds");
        if (start.distanceTo(goal) > MAX_PROBE_RADIUS * 2) throw new IllegalArgumentException("natural_route_too_long");
        if(request.has("development_hill") && !subjectKind.equals("carpet_body"))
            throw new IllegalArgumentException("development_hill_requires_carpet_body");
        hill=request.has("development_hill")?new DevelopmentHill(server,request,start,goal):null;
        try { prepareWorld(); }
        catch(RuntimeException error) { restoreDevelopmentFixture(); throw error; }
    }

    static Vec3 cell(JsonArray a) {
        return new Vec3(a.get(0).getAsInt() + 0.5, a.get(1).getAsInt(), a.get(2).getAsInt() + 0.5);
    }

    Vec3 start() { return start; }
    float yaw() { return yaw; }
    boolean running() { return status.equals("running"); }
    boolean terminal() { return !status.equals("preparing") && !running(); }
    void restoreDevelopmentFixture() { if(hill!=null) hill.restore(); }

    ServerPlayer subject() { return subjectId == null ? null : server.getPlayerList().getPlayer(subjectId); }

    boolean owns(ServerPlayer p) { return subjectId != null && p.getUUID().equals(subjectId); }

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
        // Setting a rule to its current value is reported as a failure, so flip it first.
        // The flip itself fails when the rule already holds !mobs; only the final set must succeed.
        var dispatcher = server.getCommands().getDispatcher();
        try {
            dispatcher.execute("gamerule minecraft:spawn_mobs " + !mobs, server.createCommandSourceStack());
        } catch (Exception alreadyFlipped) { /* rule already holds !mobs */ }
        try {
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

    /** Waits for the named subject to join and settle, then places it at the start. */
    private void prepare() {
        if (subjectId == null) {
            var joined = server.getPlayerList().getPlayerByName(subjectName);
            presentTicks = joined == null ? 0 : presentTicks + 1;
            if (presentTicks < LabRuntime.SUBJECT_PRESENT_TICKS) {
                if (server.getTickCount() >= subjectDeadline) finish("failed", "subject_not_present");
                return;
            }
            subjectId = joined.getUUID();
            LabFixture.resetPlayer(server, joined, start.x, start.y, start.z, yaw, 0);
            readyTick = server.getTickCount() + HUMAN_SETTLE_TICKS;
        }
    }

    void tick() {
        if (status.equals("preparing")) prepare();
        var p = subject();
        if (status.equals("preparing")) {
            if (p == null) return;
            if (server.getTickCount() < readyTick || !p.onGround()) return;
            giveKit(p);
            status = "running"; startTick = server.getTickCount(); deadlineTick = startTick + tickLimit;
            lastHealth = p.getHealth();
            minFeetY = maxFeetY = p.getY();
            minimumAir=p.getAirSupply();
            lastSubject=p;
            events.accept("course_started", Map.of("start_tick", startTick, "deadline_tick", deadlineTick,
                    "subject", subjectKind, "mobs", mobs));
            return;
        }
        if (!running()) return;
        // Account for the final health loss before returning on a lethal hit.
        var healthSubject=p!=null?p:(lastSubject!=null && !lastSubject.isAlive()?lastSubject:null);
        if (healthSubject != null) {
            if (healthSubject.getHealth() < lastHealth) damageTaken += lastHealth - healthSubject.getHealth();
            lastHealth = healthSubject.getHealth();
            if(lastHealth<=0 || !healthSubject.isAlive()) died=true;
        }
        if(p!=null) { lastSubject=p; minFeetY=Math.min(minFeetY,p.getY()); maxFeetY=Math.max(maxFeetY,p.getY()); }
        if(p==null && !died) disconnected=true;
        if (p == null || !p.isAlive()) { finish("failed", "death_or_disconnect"); return; }
        if(hill!=null) hill.diagnosticTick(p);
        minimumAir=Math.min(minimumAir,p.getAirSupply());
        if(p.isInWater()) waterTicks++;
        int hostiles = server.overworld().getEntitiesOfClass(Mob.class, p.getBoundingBox().inflate(16),
                m -> m instanceof Enemy).size();
        peakHostiles = Math.max(peakHostiles, hostiles);
        if(hostiles>0) hostileExposureTicks++;
        double horizontal = Math.hypot(p.getX() - goal.x, p.getZ() - goal.z);
        minDistance = Math.min(minDistance, horizontal);
        if (horizontal <= radius && Math.abs(p.getY() - goal.y) <= (hill==null?GOAL_DY:0.25)
                && (hill==null || p.onGround())) { finish("success", "reached_goal"); return; }
        if (server.getTickCount() >= deadlineTick) { finish("failed", "tick_limit"); return; }
        // Stall: the body has not left a 1-block neighbourhood for stallTicks. This also
        // ends oscillation (planner and motor disagreeing) rather than waiting out the clock.
        var position = p.position();
        if (stallAnchor == null || position.distanceTo(stallAnchor) > STALL_MOVE_BLOCKS) {
            stallAnchor = position; stallSince = server.getTickCount();
        } else if (stallTicks > 0 && server.getTickCount() - stallSince >= stallTicks) {
            finish("failed", "stalled"); return;
        }
        if (showMarkers && server.getTickCount() % MARKER_INTERVAL_TICKS == 0) drawMarkers();
    }

    /** Latest planned path from the controller; drawn only when markers are on. */
    void showPath(JsonArray points) {
        if (points.size() > MAX_PATH_POINTS) throw new IllegalArgumentException("natural_path_length");
        var parsed = new ArrayList<Vec3>();
        for (var value : points) {
            var point = cell(value.getAsJsonArray());
            if (point.distanceTo(start) > MAX_PROBE_RADIUS * 2) throw new IllegalArgumentException("natural_path_bounds");
            parsed.add(point);
        }
        path = List.copyOf(parsed);
    }

    /** Server particles every client sees: red dust along the path, a green ring and beam at the goal. */
    private void drawMarkers() {
        var level = server.overworld();
        for (int i = 1; i < path.size(); i++) {
            var a = path.get(i - 1); var b = path.get(i);
            for (int k = 0; k < 4; k++) {
                var q = a.lerp(b, k / 4.0);
                level.sendParticles(PATH_DUST, true, true, q.x, q.y + 0.15, q.z, 1, 0, 0, 0, 0);
            }
        }
        for (int k = 0; k < 24; k++) {
            double angle = Math.PI * 2 * k / 24;
            level.sendParticles(GOAL_DUST, true, true, goal.x + Math.cos(angle) * radius, goal.y + 0.2,
                    goal.z + Math.sin(angle) * radius, 1, 0, 0, 0, 0);
        }
        for (int h = 0; h < 24; h += 2)
            level.sendParticles(GOAL_DUST, true, true, goal.x, goal.y + h, goal.z, 1, 0, 0, 0, 0);
    }

    void broke(ServerPlayer p, boolean success) {
        if (running() && success && owns(p)) blocksBroken++;
    }

    void pickup(ServerPlayer p,String kind,int before,int after) {
        if(running() && owns(p) && (kind.equals("minecraft:cobblestone") || kind.equals("minecraft:dirt"))) {
            int count=Math.max(0,before-after);
            kitBlocksPickedUp+=count;
            buildingPickups.merge(kind,count,Integer::sum);
        }
    }

    Map<String,Integer> buildingPickups() { return Map.copyOf(buildingPickups); }

    void healthChanged(ServerPlayer p,float before,float after) {
        if(running() && owns(p)) {
            if(after<before) damageTaken+=before-after;
            if(after<=0) died=true;
            lastHealth=after;
        }
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
        result.put("minimum_feet_y",minFeetY);
        result.put("maximum_feet_y",maxFeetY);
        result.put("water_ticks",waterTicks);
        result.put("minimum_air",minimumAir);
        result.put("final_air",p==null?-1:p.getAirSupply());
        result.put("died",died);
        result.put("disconnected",disconnected);
        result.put("hostile_exposure_ticks_within_16",hostileExposureTicks);
        result.put("peak_hostiles_within_16", peakHostiles);
        result.put("blocks_broken", blocksBroken);
        int left = p == null ? -1 : p.getInventory().countItem(Items.COBBLESTONE) + p.getInventory().countItem(Items.DIRT);
        // Kit blocks used; picked-up dirt/cobblestone reduces this, so it is a lower bound on placements.
        result.put("kit_blocks_used_estimate", p == null ? -1 : 2 * KIT_BLOCKS - left);
        result.put("kit_blocks_picked_up",kitBlocksPickedUp);
        result.put("kit_blocks_spent_adjusted",p==null || !p.isAlive()?-1:2*KIT_BLOCKS+kitBlocksPickedUp-left);
        if (p != null) result.put("final_position", List.of(p.getX(), p.getY(), p.getZ()));
        result.put("mobs", mobs);
        result.put("stall_ticks", stallTicks);
        if(hill!=null) result.put("development_hill",hill.audit());
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
