package com.vulpine.mcb.arena;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.ai.attributes.Attributes;

/**
 * One match in one arena: waves of mobs against the participants, or teams against each other. The clock and every rule
 * that needs no Minecraft live in {@link ArenaLogic}; this class feeds the clock the world's facts (which wave mobs are
 * alive, which participants are alive inside the bounds) and does what the clock asks. Server thread only.
 */
final class ArenaMatch {
    enum State { RUNNING, WON, LOST, STOPPED }

    /** A wave mob's last known type and position, so a mob in an unloaded chunk still counts as alive. */
    private static final class Tracked {
        final String type;
        double x, y, z;
        double vx, vy, vz;  // blocks per tick: where a thrown potion will meet it
        float health;
        Tracked(String type, double x, double y, double z, float health) { this.type = type; this.x = x; this.y = y; this.z = z; this.health = health; }
    }

    private static final int RETARGET_TICKS = 10;

    private final MinecraftServer server;
    private final ArenaEmit emit;
    final ArenaRecord arena;
    final ServerLevel level;
    final String mode;
    private final List<ArenaRequests.Wave> waves;
    private final ArenaLogic.WaveClock clock;
    private final List<UUID> participants;
    private final Map<UUID, String> names = new HashMap<>();
    /** teams mode: team to its players, in plan order; empty in waves mode. */
    private final Map<String, List<UUID>> teams;
    private final Map<UUID, String> teamOf = new HashMap<>();
    private final Map<UUID, Tracked> mobs = new LinkedHashMap<>();
    private final String tag;
    private final double followRange;
    private State state = State.RUNNING;
    private String winner, reason;
    private int startTick, endTick, ticks, spawned, killed;
    /** The mob being spawned now: its jockey, added during finalizeSpawn, is already its child. */
    private Mob spawning;

    ArenaMatch(MinecraftServer server, ArenaEmit emit, ArenaRecord arena, ServerLevel level, String mode,
               List<ArenaRequests.Wave> waves, List<ServerPlayer> players, Map<String, List<ServerPlayer>> teams) {
        this.server = server;
        this.emit = emit;
        this.arena = arena;
        this.level = level;
        this.mode = mode;
        this.waves = waves;
        this.tag = ArenaLogic.waveTag(arena.name());
        this.followRange = ArenaLogic.followRange(arena.box());
        this.participants = new ArrayList<>();
        for (var p : players) {
            participants.add(p.getUUID());
            names.put(p.getUUID(), p.getName().getString());
        }
        this.teams = new LinkedHashMap<>();
        teams.forEach((team, members) -> {
            var ids = new ArrayList<UUID>();
            for (var p : members) { ids.add(p.getUUID()); teamOf.put(p.getUUID(), team); }
            this.teams.put(team, ids);
        });
        this.clock = mode.equals("waves") ? new ArenaLogic.WaveClock(waves.stream().map(ArenaRequests.Wave::delay).toList()) : null;
    }

    State state() { return state; }

    boolean running() { return state == State.RUNNING; }

    void start() {
        startTick = server.getTickCount();
        if (mode.equals("teams")) enterTeams();
        else enterWaves();
        var data = base();
        data.put("mode", mode);
        data.put("participants", participantNames());
        if (clock != null) data.put("waves", waves.size());
        else data.put("teams", teamNames());
        emit.emit("arena_match_started", data);
    }

    // ---- the tick

    void tick(int serverTick) {
        if (state != State.RUNNING) return;
        ticks++;
        if (clock == null) { tickTeams(); return; }
        prune();
        boolean alive = anyLive();
        var step = clock.tick(mobs.size(), alive);
        switch (step.kind()) {
            case SPAWN -> spawnWave(step.wave());
            case CLEARED -> {
                var data = base();
                data.put("wave", step.wave());
                data.put("waves", waves.size());
                emit.emit("arena_wave_cleared", data);
            }
            case WON -> finish(State.WON, null, "arena_match_won");
            case LOST -> finish(State.LOST, "participants_down", "arena_match_lost");
            case NONE -> { }
        }
        if (state == State.RUNNING && ticks % RETARGET_TICKS == 0) retarget();
    }

    private void tickTeams() {
        var live = new LinkedHashMap<String, Integer>();
        teams.forEach((team, members) -> live.put(team, (int) members.stream().filter(this::live).count()));
        var result = ArenaLogic.teamResult(live);
        if (result == null) return;
        winner = result.isEmpty() ? null : result;
        if (winner == null) finish(State.LOST, "draw", "arena_match_lost");
        else finish(State.WON, null, "arena_match_won");
    }

    // ---- participants

    private boolean live(UUID id) {
        var player = server.getPlayerList().getPlayer(id);
        return player != null && player.isAlive() && player.level() == level
                && arena.box().containsPoint(player.getX(), player.getY(), player.getZ());
    }

    private boolean anyLive() {
        for (var id : participants) if (live(id)) return true;
        return false;
    }

    private List<String> participantNames() {
        var out = new ArrayList<String>();
        for (var id : participants) out.add(names.get(id));
        return out;
    }

    private List<String> teamNames() { return new ArrayList<>(teams.keySet()); }

    // ---- teams

    private void enterTeams() {
        var scoreboard = server.getScoreboard();
        for (var entry : teams.entrySet()) {
            String name = ArenaLogic.teamName(arena.name(), entry.getKey());
            var old = scoreboard.getPlayerTeam(name);
            if (old != null) scoreboard.removePlayerTeam(old);
            var team = scoreboard.addPlayerTeam(name);
            team.setAllowFriendlyFire(false);
            team.setSeeFriendlyInvisibles(true);
            var pads = arena.teams().get(entry.getKey()).pads();
            int i = 0;
            for (var id : entry.getValue()) {
                var player = server.getPlayerList().getPlayer(id);
                if (player == null) continue;
                scoreboard.addPlayerToTeam(player.getScoreboardName(), team);
                place(player, pads.get(i++));
            }
        }
    }

    /** Every participant onto a pad (ArenaRuntime checked there are enough): the waves come to them there. */
    private void enterWaves() {
        var seats = ArenaLogic.wavePads(ArenaRuntime.padCounts(arena), participants.size(), Integer.MAX_VALUE);
        for (int i = 0; i < participants.size(); i++) {
            var player = server.getPlayerList().getPlayer(participants.get(i));
            if (player == null) continue;
            var seat = seats.get(i);
            place(player, arena.teams().get(seat.team()).pads().get(seat.index()));
        }
    }

    /** A player onto a pad cell, facing the arena's centre. */
    private void place(ServerPlayer player, int[] padCell) {
        var box = arena.box();
        double cx = (box.minX() + box.maxX() + 1) / 2.0, cz = (box.minZ() + box.maxZ() + 1) / 2.0;
        var pad = arena.world(padCell);
        double dx = cx - (pad[0] + .5), dz = cz - (pad[2] + .5);
        float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
        player.teleportTo(level, pad[0] + .5, pad[1], pad[2] + .5, Set.of(), yaw, 0f, true);
    }

    private void leaveTeams() {
        var scoreboard = server.getScoreboard();
        for (var team : teams.keySet()) {
            var existing = scoreboard.getPlayerTeam(ArenaLogic.teamName(arena.name(), team));
            if (existing != null) scoreboard.removePlayerTeam(existing);
        }
    }

    // ---- wave mobs

    /** Drops mobs that died or were removed; a mob in an unloaded chunk stays, as alive as it was. */
    private void prune() {
        for (Iterator<Map.Entry<UUID, Tracked>> it = mobs.entrySet().iterator(); it.hasNext(); ) {
            var entry = it.next();
            Entity entity = level.getEntityInAnyDimension(entry.getKey());
            var t = entry.getValue();
            if (entity == null) {
                if (level.getChunkSource().hasChunk((int) Math.floor(t.x) >> 4, (int) Math.floor(t.z) >> 4)) { it.remove(); killed++; }
            } else if (entity.isRemoved() || !entity.isAlive()) {
                it.remove();
                killed++;
            } else {
                t.x = entity.getX(); t.y = entity.getY(); t.z = entity.getZ();
                var v = entity.getDeltaMovement();
                t.vx = v.x; t.vy = v.y; t.vz = v.z;
                if (entity instanceof LivingEntity living) t.health = living.getHealth();
            }
        }
    }

    private void spawnWave(int index) {
        var wave = waves.get(index);
        var markers = arena.mobSpawns();
        int n = 0, failed = 0;
        for (var entry : wave.mobs().entrySet()) {
            var type = BuiltInRegistries.ENTITY_TYPE.getValue(Identifier.parse(entry.getKey()));
            for (int i = 0; i < entry.getValue(); i++) {
                var marker = arena.world(markers.get(ArenaLogic.spawnMarker(n++, markers.size())));
                if (!spawnOne(type.create(level, EntitySpawnReason.COMMAND), marker)) failed++;
            }
        }
        var data = base();
        data.put("wave", index);
        data.put("waves", waves.size());
        data.put("mobs", mobs.size());
        data.put("failed", failed);
        emit.emit("arena_wave_started", data);
        if (mobs.isEmpty()) finish(State.STOPPED, "spawn_failed", "arena_match_stopped");
    }

    private boolean spawnOne(Entity created, int[] marker) {
        if (!(created instanceof Mob mob)) return false;
        boolean placed = false;
        float yaw = level.getRandom().nextFloat() * 360f;
        for (int[] offset : ArenaLogic.searchOffsets()) {
            double x = marker[0] + offset[0] + .5, y = marker[1] + offset[1], z = marker[2] + offset[2] + .5;
            if (!arena.box().containsPoint(x, y, z)) continue;
            mob.snapTo(x, y, z, yaw, 0f);
            if (level.noCollision(mob)) { placed = true; break; }
        }
        if (!placed) { mob.discard(); return false; }
        mob.addTag(tag);
        mob.setPersistenceRequired();
        var follow = mob.getAttribute(Attributes.FOLLOW_RANGE);
        if (follow != null) follow.setBaseValue(followRange);
        spawning = mob;
        try {
            mob.finalizeSpawn(level, level.getCurrentDifficultyAt(mob.blockPosition()), EntitySpawnReason.COMMAND, null);
            track(mob);
            for (var rider : mob.getIndirectPassengers()) rider.addTag(tag);
            level.addFreshEntityWithPassengers(mob);
            for (var rider : mob.getIndirectPassengers()) adopt(rider);
        } finally { spawning = null; }
        spawned++;
        return true;
    }

    private void track(Entity entity) {
        entity.addTag(tag);
        mobs.put(entity.getUUID(), new Tracked(BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString(),
                entity.getX(), entity.getY(), entity.getZ(), entity instanceof LivingEntity l ? l.getHealth() : 0f));
        if (entity instanceof Mob mob) mob.setPersistenceRequired();
    }

    private void adopt(Entity entity) { if (!mobs.containsKey(entity.getUUID())) track(entity); }

    /**
     * A mob added in the arena that is not already a wave mob: kept (and made a member of this wave) when its spawn reason
     * and a wave mob of a fitting type nearby say a wave mob made it (a vex from an evoker, reinforcements, a split slime).
     */
    boolean adoptDerived(Mob child, EntitySpawnReason why) {
        if (state != State.RUNNING || clock == null) return false;
        String type = BuiltInRegistries.ENTITY_TYPE.getKey(child.getType()).toString();
        if (ArenaLogic.rule(type) == null) return false;
        if (spawning != null && near(child, spawning.getX(), spawning.getY(), spawning.getZ())
                && ArenaLogic.derived(why.name(), type, BuiltInRegistries.ENTITY_TYPE.getKey(spawning.getType()).toString())) {
            adopt(child);
            return true;
        }
        for (var entry : mobs.entrySet()) {
            var t = entry.getValue();
            if (near(child, t.x, t.y, t.z) && ArenaLogic.derived(why.name(), type, t.type)) { adopt(child); return true; }
        }
        return false;
    }

    private static boolean near(Entity child, double x, double y, double z) {
        double dx = child.getX() - x, dy = child.getY() - y, dz = child.getZ() - z;
        return dx * dx + dy * dy + dz * dz <= ArenaLogic.DERIVED_RANGE * ArenaLogic.DERIVED_RANGE;
    }

    boolean owns(UUID id) { return mobs.containsKey(id); }

    private void retarget() {
        var live = new ArrayList<ServerPlayer>();
        for (var id : participants) {
            var player = server.getPlayerList().getPlayer(id);
            if (player != null && live(id)) live.add(player);
        }
        if (live.isEmpty()) return;
        var positions = new double[live.size()][];
        var flags = new boolean[live.size()];
        for (int i = 0; i < live.size(); i++) {
            positions[i] = new double[] {live.get(i).getX(), live.get(i).getY(), live.get(i).getZ()};
            flags[i] = true;
        }
        for (var id : mobs.keySet()) {
            if (!(level.getEntityInAnyDimension(id) instanceof Mob mob) || mob.level() != level) continue;
            int best = ArenaLogic.nearest(mob.getX(), mob.getY(), mob.getZ(), positions, flags);
            if (best >= 0 && mob.getTarget() != live.get(best)) mob.setTarget(live.get(best));
        }
    }

    // ---- ending

    void stop(String why) { finish(State.STOPPED, why, "arena_match_stopped"); }

    /** Ends the match: every remaining wave mob is discarded, the scoreboard teams go, the event is sent once. */
    private void finish(State end, String why, String event) {
        if (state != State.RUNNING) return;
        state = end;
        reason = why;
        endTick = server.getTickCount();
        discardMobs();
        if (mode.equals("teams")) leaveTeams();
        var data = base();
        data.put("mode", mode);
        if (winner != null) data.put("winner", winner);
        if (why != null) data.put("reason", why);
        if (clock != null) {
            data.put("waves_cleared", clock.index());
            data.put("waves", waves.size());
        }
        data.put("duration_ticks", endTick - startTick);
        emit.emit(event, data);
    }

    void discardMobs() {
        for (var id : mobs.keySet()) {
            var entity = level.getEntityInAnyDimension(id);
            if (entity != null) entity.discard();
        }
        mobs.clear();
    }

    /** The server is going down: end the match quietly (mobs and teams cleaned up, no event). */
    void abandon() {
        if (state != State.RUNNING) return;
        state = State.STOPPED;
        reason = "server_stopping";
        discardMobs();
        if (mode.equals("teams")) leaveTeams();
    }

    // ---- reporting

    private Map<String, Object> base() {
        var out = new LinkedHashMap<String, Object>();
        out.put("name", arena.name());
        return out;
    }

    Map<String, Object> status() {
        var out = new LinkedHashMap<String, Object>();
        out.put("mode", mode);
        out.put("state", state.name().toLowerCase(Locale.ROOT));
        if (reason != null) out.put("reason", reason);
        if (winner != null) out.put("winner", winner);
        out.put("ticks", (state == State.RUNNING ? server.getTickCount() : endTick) - startTick);
        if (clock != null) {
            out.put("wave_index", clock.index());
            out.put("waves", waves.size());
            out.put("next_wave_in_ticks", clock.nextIn());
            out.put("spawned", spawned);
            out.put("killed", killed);
            var list = new ArrayList<Map<String, Object>>();
            for (var entry : mobs.entrySet()) {
                var t = entry.getValue();
                var m = new LinkedHashMap<String, Object>();
                m.put("uuid", entry.getKey().toString());
                m.put("type", t.type);
                m.put("position", List.of(round(t.x), round(t.y), round(t.z)));
                m.put("health", (double) t.health);
                m.put("velocity", List.of(round(t.vx * 20), round(t.vy * 20), round(t.vz * 20)));  // blocks/s
                list.add(m);
            }
            out.put("wave_mobs", list);
        }
        var people = new ArrayList<Map<String, Object>>();
        for (var id : participants) {
            var p = new LinkedHashMap<String, Object>();
            p.put("uuid", id.toString());
            p.put("name", names.get(id));
            p.put("alive", live(id));
            if (teamOf.containsKey(id)) p.put("team", teamOf.get(id));
            people.add(p);
        }
        out.put("participants", people);
        if (!teams.isEmpty()) {
            var byTeam = new LinkedHashMap<String, Object>();
            teams.forEach((team, members) -> byTeam.put(team, members.stream().filter(this::live).count()));
            out.put("teams_alive", byTeam);
        }
        return out;
    }

    private static double round(double v) { return Math.round(v * 100.0) / 100.0; }
}
