package com.vulpine.mcb.arena;

import com.google.gson.JsonObject;
import com.mojang.brigadier.exceptions.CommandSyntaxException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.TamableAnimal;
import net.minecraft.world.entity.animal.equine.AbstractHorse;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraft.world.phys.AABB;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The arena runtime: the registry of arenas, the placing jobs, the matches, and the six ops. It lives for one server run
 * (created at SERVER_STARTED, closed at SERVER_STOPPING) and never needs the body: arena ops reach it before the body's
 * identity checks. Everything here runs on the server thread.
 */
public final class ArenaRuntime {
    private static final Logger LOG = LoggerFactory.getLogger("mcb-arena");
    private static volatile ArenaRuntime active;
    private static volatile ArenaEmit emitter = (type, data) -> { };

    /** The time one tick may spend placing blocks, across every job. */
    static final long BUDGET_NANOS = 4_000_000L;
    static final int MAX_BUILDS = 4;
    static final String TRIAL_TAG = "mcb_trial";
    /** Names the arena's own scoreboard teams carry (ArenaLogic.teamName), swept at start after a crash. */
    private static final java.util.regex.Pattern TEAM_NAME = java.util.regex.Pattern.compile("mcb_[0-9a-f]{8}");
    /** The kill boards' and series boards' names (ArenaLogic.objectiveName, seriesBoardName), swept the same way. */
    private static final java.util.regex.Pattern BOARD_NAME = java.util.regex.Pattern.compile("mcb[ks]_[0-9a-f]{8}");
    /**
     * Who is healing right now (HealOrHarmMobEffect's thrower or drinker, set around its instant effect), else null: the
     * heal that follows is theirs. Server thread only.
     */
    private static Entity healer;

    private final MinecraftServer server;
    private final ArenaRegistry registry;
    private final ChunkForce force = new ChunkForce();
    private final List<ArenaBuild> builds = new ArrayList<>();
    private final Map<String, ArenaMatch> matches = new LinkedHashMap<>();
    /** Arenas whose match holds its chunks forceloaded right now. */
    private final Set<String> holding = new HashSet<>();
    /** Players who died in a keep_inventory match and are waiting to respawn with what they carried. */
    private final Set<UUID> keepOnRespawn = new HashSet<>();
    private final Map<String, Integer> blocked = new HashMap<>();
    private final List<Runnable> afterAck = new ArrayList<>();

    private ArenaRuntime(MinecraftServer server, ArenaRegistry registry) {
        this.server = server;
        this.registry = registry;
    }

    public static ArenaRuntime active() { return active; }

    /** BodyRuntime hands over the event log; until then (or without a bridge) events go nowhere. */
    public static void setEmitter(ArenaEmit emit) { emitter = emit; }

    public static boolean handles(String op) { return ArenaLogic.isOperationName(op); }

    /** Loads the registry, repairs what a crash left (jobs that cannot resume, chunks left forced, stale mobs and teams). */
    public static ArenaRuntime start(MinecraftServer server) {
        Path file = server.getWorldPath(LevelResource.ROOT).resolve("mcb_arenas.json");
        var runtime = new ArenaRuntime(server, ArenaRegistry.load(file));
        runtime.recover();
        active = runtime;
        LOG.info("MCB arena runtime loaded: {} arena(s)", runtime.registry.all().size());
        return runtime;
    }

    private void recover() {
        boolean changed = false;
        for (var record : registry.all()) {
            if (record.state().equals("building") || record.state().equals("clearing")) { record.setState("incomplete"); changed = true; }
            if (!record.forced().isEmpty()) {
                var level = levelOf(record.dimension());
                if (level != null) ChunkForce.unforceAll(level, record.forced());
                record.setForced(List.of());
                changed = true;
            }
        }
        if (changed) registry.save();
        var scoreboard = server.getScoreboard();
        for (var team : new ArrayList<>(scoreboard.getPlayerTeams())) {
            if (TEAM_NAME.matcher(team.getName()).matches()) scoreboard.removePlayerTeam(team);
        }
        for (var objective : new ArrayList<>(scoreboard.getObjectives())) {
            if (BOARD_NAME.matcher(objective.getName()).matches()) scoreboard.removeObjective(objective);
        }
        for (var level : server.getAllLevels()) {
            for (var entity : level.getAllEntities()) if (hasArenaTag(entity)) entity.discard();
        }
    }

    public ArenaRegistry registry() { return registry; }

    private ServerLevel levelOf(String dimension) {
        var id = Identifier.tryParse(dimension);
        return id == null ? null : server.getLevel(ResourceKey.create(Registries.DIMENSION, id));
    }

    private ServerLevel requireLevel(String dimension) {
        var level = levelOf(dimension);
        if (level == null) throw new ArenaFail("unknown_dimension", dimension);
        return level;
    }

    private void emit(String type, Map<String, Object> data) { emitter.emit(type, data); }

    // ---- the ops

    public void handle(String op, JsonObject request, long sequence) {
        String requestId = request.has("request_id") && request.get("request_id").isJsonPrimitive()
                ? request.get("request_id").getAsString() : null;
        afterAck.clear();
        var data = new LinkedHashMap<String, Object>();
        try {
            switch (op) {
                case "arena_build" -> data.putAll(build(request));
                case "arena_remove" -> data.putAll(remove(request));
                case "arena_list" -> data.putAll(list());
                case "arena_status" -> data.putAll(status(request));
                case "arena_start" -> data.putAll(start(request));
                case "arena_stop" -> data.putAll(stop(request));
                case "arena_spawn" -> data.putAll(spawn(request));
                case "arena_op" -> data.putAll(op(request));
                case "arena_kit" -> data.putAll(kit(request));
                case "arena_board" -> data.putAll(board(request));
                case "arena_recap" -> data.putAll(recap(request));
                default -> throw new ArenaFail("unknown_op", op);
            }
        } catch (ArenaFail fail) {
            afterAck.clear();
            failed(op, sequence, requestId, fail.reason, fail.detail);
            return;
        } catch (RuntimeException error) {
            afterAck.clear();
            LOG.error("{} failed", op, error);
            failed(op, sequence, requestId, "internal", error.getClass().getSimpleName() + ": " + error.getMessage());
            return;
        }
        data.put("request_sequence", sequence);
        if (requestId != null) data.put("request_id", requestId);
        emit(op + "_done", data);
        var later = new ArrayList<>(afterAck);
        afterAck.clear();
        later.forEach(Runnable::run);
    }

    private void failed(String op, long sequence, String requestId, String reason, String detail) {
        var data = new LinkedHashMap<String, Object>();
        data.put("request_sequence", sequence);
        if (requestId != null) data.put("request_id", requestId);
        data.put("reason", reason);
        if (detail != null && !detail.isEmpty()) data.put("detail", detail);
        emit(op + "_failed", data);
    }

    private Map<String, Object> build(JsonObject request) {
        var req = ArenaRequests.parseBuild(request);
        var level = requireLevel(req.dimension());
        if (builds.size() >= MAX_BUILDS) throw new ArenaFail("busy", builds.size() + " jobs are running");
        var plan = req.plan();
        var box = plan.box(req.origin());
        ArenaLogic.checkHeight(box, level.getMinY(), level.getMaxY());
        var cache = new HashMap<String, BlockState>();
        var states = new BlockState[plan.fills().size()];
        for (int i = 0; i < states.length; i++) {
            String block = plan.fills().get(i).block();
            var state = cache.get(block);
            if (state == null) { state = parseBlock(block, "fills[" + i + "]"); cache.put(block, state); }
            states[i] = state;
        }
        var record = ArenaRecord.of(plan, req.name(), req.dimension(), req.origin());
        registry.add(record);
        holdChunks(record, level);
        registry.save();
        builds.add(new ArenaBuild(record, level, ArenaBuild.Kind.BUILD, plan.fills(), states));
        var out = new LinkedHashMap<String, Object>();
        out.put("name", record.name());
        out.put("state", record.state());
        out.put("cells", plan.totalCells());
        out.put("chunks", box.chunks().length);
        out.put("bounds", bounds(record));
        return out;
    }

    private static BlockState parseBlock(String block, String where) {
        try {
            return BlockStateParser.parseForBlock(BuiltInRegistries.BLOCK, block, false).blockState();
        } catch (CommandSyntaxException error) {
            throw new ArenaFail("bad_block", where + " " + block + ": " + error.getRawMessage().getString());
        }
    }

    private void holdChunks(ArenaRecord record, ServerLevel level) {
        long[] chunks = record.box().chunks();
        force.hold(level, record.dimension(), chunks);
        record.setForced(force.owned(record.dimension(), chunks));
    }

    private void releaseChunks(ArenaRecord record, ServerLevel level) {
        force.release(level, record.dimension(), record.box().chunks());
        record.setForced(List.of());
    }

    private Map<String, Object> remove(JsonObject request) {
        var req = ArenaRequests.parseRemove(request);
        var record = require(req.name());
        var level = requireLevel(record.dimension());
        var match = matches.get(record.name());
        if (match != null && match.running()) { match.stop("removed"); releaseMatch(record.name()); }
        if (match != null) match.hideBoard();
        var series = seriesBoards.remove(record.name());
        if (series != null) series.hide();
        matches.remove(record.name());
        for (Iterator<ArenaBuild> it = builds.iterator(); it.hasNext(); ) {
            var job = it.next();
            if (job.record == record) { it.remove(); releaseChunks(record, level); }
        }
        var out = new LinkedHashMap<String, Object>();
        out.put("name", record.name());
        if (req.restore()) {
            record.setState("clearing");
            holdChunks(record, level);
            registry.save();
            builds.add(ArenaBuild.restore(record, level));
            out.put("restoring", true);
        } else {
            registry.remove(record.name());
            registry.save();
            out.put("restoring", false);
            afterAck.add(() -> emit("arena_removed", Map.of("name", record.name(), "restored", false)));
        }
        return out;
    }

    private Map<String, Object> list() {
        var out = new ArrayList<Map<String, Object>>();
        for (var record : registry.all()) out.add(summary(record));
        return Map.of("arenas", out, "protocol", ArenaLogic.PROTOCOL);
    }

    private Map<String, Object> status(JsonObject request) {
        var record = require(ArenaRequests.parseName(request));
        var out = summary(record);
        for (var job : builds) if (job.record == record) out.put("build", job.progress());
        var match = matches.get(record.name());
        if (match != null) out.put("match", match.status());
        out.put("blocked_spawns", blocked.getOrDefault(record.name(), 0));
        return out;
    }

    private Map<String, Object> summary(ArenaRecord record) {
        var out = new LinkedHashMap<String, Object>();
        out.put("name", record.name());
        out.put("dimension", record.dimension());
        out.put("state", record.state());
        out.put("origin", cell(record.origin()));
        out.put("size", cell(record.size()));
        out.put("bounds", bounds(record));
        var cap = new LinkedHashMap<String, Object>();
        cap.put("teams", record.capacity().teams());
        cap.put("per_team", record.capacity().perTeam());
        cap.put("mobs", record.capacity().mobs());
        out.put("capacity", cap);
        var teams = new LinkedHashMap<String, Object>();
        record.teams().forEach((team, t) -> teams.put(team, t.pads().size()));
        out.put("teams", teams);
        out.put("mob_spawns", record.mobSpawns().size());
        if (record.spectator() != null) out.put("spectator", cell(record.world(record.spectator())));
        var match = matches.get(record.name());
        out.put("match_state", match == null ? "none" : match.state().name().toLowerCase());
        return out;
    }

    private static Map<String, Object> bounds(ArenaRecord record) {
        var box = record.box();
        var out = new LinkedHashMap<String, Object>();
        out.put("min", cell(box.min()));
        out.put("max", cell(box.max()));
        return out;
    }

    private static List<Integer> cell(int[] c) { return List.of(c[0], c[1], c[2]); }

    private ArenaRecord require(String name) {
        var record = registry.get(name);
        if (record == null) throw new ArenaFail("unknown_arena", name);
        return record;
    }

    // ---- matches

    /** Each team marker's pad count, in plan order. */
    static Map<String, Integer> padCounts(ArenaRecord record) {
        var pads = new LinkedHashMap<String, Integer>();
        record.teams().forEach((team, t) -> pads.put(team, t.pads().size()));
        return pads;
    }

    private Map<String, Object> start(JsonObject request) {
        var req = ArenaRequests.parseStart(request);
        var record = require(req.name());
        if (!record.state().equals("built")) throw new ArenaFail("arena_not_ready", "state is " + record.state());
        var existing = matches.get(record.name());
        if (existing != null && existing.running()) throw new ArenaFail("match_running", record.name());
        var level = requireLevel(record.dimension());
        boolean teamMode = ArenaLogic.sided(req.mode());
        if (!req.waves().isEmpty()) checkWaves(record, level, req.waves());
        if (req.exit() != null) ArenaLogic.checkHeight(ArenaBox.of(req.exit(), new int[] {1, 2, 1}), level.getMinY(), level.getMaxY());
        var players = new ArrayList<ServerPlayer>();
        var teamPlayers = new LinkedHashMap<String, List<ServerPlayer>>();
        if (teamMode) {
            var arenaTeams = record.teams().keySet();
            var names = req.participants() != null ? req.participants() : inBounds(record, level);
            var assignment = req.teams() != null ? req.teams() : ArenaLogic.assignTeams(new ArrayList<>(arenaTeams), names);
            ArenaLogic.checkTeams(assignment, new LinkedHashSet<>(arenaTeams), padCounts(record), record.capacity().perTeam());
            assignment.forEach((team, members) -> {
                var resolved = new ArrayList<ServerPlayer>();
                for (var name : members) resolved.add(living(name));
                teamPlayers.put(team, resolved);
                players.addAll(resolved);
            });
        } else {
            var names = req.participants() != null ? req.participants() : inBounds(record, level);
            if (names.isEmpty()) throw new ArenaFail("no_participants", "no player is inside " + record.name());
            var seen = new LinkedHashSet<String>();
            for (var name : names) {
                if (!seen.add(name)) throw new ArenaFail("duplicate_participant", name);
                players.add(living(name));
            }
            ArenaLogic.wavePads(padCounts(record), players.size(), record.capacity().teams() * record.capacity().perTeam());
        }
        sweep(record, level);
        var match = new ArenaMatch(server, emitter, record, level, req.mode(), req.waves(), players, teamPlayers, req.exit(), req.series(),
                req.keepInventory(), req.respawns());
        matches.put(record.name(), match);
        holdChunks(record, level);
        registry.save();
        holding.add(record.name());
        blocked.remove(record.name());
        var out = new LinkedHashMap<String, Object>();
        out.put("name", record.name());
        out.put("mode", req.mode());
        out.put("participants", players.stream().map(p -> p.getName().getString()).toList());
        out.put("respawn_mode", req.respawns().mode().wireName());
        out.put("respawn_count", req.respawns().count());
        out.put("respawn_delay_ticks", req.respawns().delayTicks());
        if (!req.waves().isEmpty()) out.put("waves", req.waves().size());
        if (teamMode) {
            var assigned = new LinkedHashMap<String, Object>();
            teamPlayers.forEach((team, members) -> assigned.put(team, members.stream().map(p -> p.getName().getString()).toList()));
            out.put("teams", assigned);
        }
        afterAck.add(match::start);
        return out;
    }

    // ---- the world and its players (the arena server's lobby, operators, kits)

    /** The overworld's spawn (new players, and everyone without a bed, appear there), exactly: no spawn radius. */
    private Map<String, Object> spawn(JsonObject request) {
        var cell = ArenaRequests.parseSpawn(request);
        var level = server.overworld();
        ArenaLogic.checkHeight(ArenaBox.of(cell, new int[] {1, 2, 1}), level.getMinY(), level.getMaxY());
        var pos = new net.minecraft.core.BlockPos(cell[0], cell[1], cell[2]);
        server.setRespawnData(net.minecraft.world.level.storage.LevelData.RespawnData.of(level.dimension(), pos, 0f, 0f));
        server.getGameRules().set(net.minecraft.world.level.gamerules.GameRules.RESPAWN_RADIUS, 0, server);
        return Map.of("position", cell(cell));
    }

    /** Operator status for an online player: an op can use every command (teleporting to an arena included). */
    private Map<String, Object> op(JsonObject request) {
        var req = ArenaRequests.parseOp(request);
        var player = online(req.player());
        var list = server.getPlayerList();
        var who = new net.minecraft.server.players.NameAndId(player.getGameProfile());
        boolean was = list.isOp(who);
        if (req.op() && !was) list.op(who);
        if (!req.op() && was) list.deop(who);
        return Map.of("player", player.getName().getString(), "op", req.op(), "changed", req.op() != was);
    }

    /** The same kit for every player named, all checked before anyone's inventory changes. */
    private Map<String, Object> kit(JsonObject request) {
        var req = ArenaRequests.parseKit(request);
        var players = new ArrayList<ServerPlayer>();
        for (var name : req.players()) players.add(living(name));
        var given = new LinkedHashMap<String, Object>();
        try {
            var kit = com.vulpine.mcb.body.PlayerKit.check(players.get(0), req.kit());
            for (var player : players) given.put(player.getName().getString(), kit.dress(player));
        } catch (com.vulpine.mcb.body.PlayerKit.Refused refused) {
            throw new ArenaFail(refused.reason, refused.getMessage());
        }
        return Map.of("players", given);
    }

    // ---- the series board and chat (what the lobby shows between matches)

    private final Map<String, ArenaBoard> seriesBoards = new HashMap<>();

    /** The arena's series board on the sidebar (until a match's own board takes it), or taken down. */
    private Map<String, Object> board(JsonObject request) {
        var req = ArenaRequests.parseBoard(request);
        var record = require(req.name());
        var board = seriesBoards.computeIfAbsent(record.name(), n -> new ArenaBoard(server, ArenaLogic.seriesBoardName(n)));
        if (req.clear()) board.hide();
        else board.show(req.title(), req.rows());
        return Map.of("name", record.name(), "rows", req.rows().size(), "shown", !req.clear());
    }

    /** Chat lines for everyone in the arena's world. */
    /** The after-action table Python sends at a series' end (its totals), drawn like a match's own. */
    private Map<String, Object> recap(JsonObject request) {
        var req = ArenaRequests.parseRecap(request);
        var record = require(req.name());
        var level = requireLevel(record.dimension());
        int told = tell(level, List.of(), ArenaRecap.table(req.title(), req.lines(), req.teams(), req.players()));
        return Map.of("name", record.name(), "players", told);
    }

    /** Chat lines for every player in {@code level} and for {@code also} wherever they are; how many heard them. */
    static int tell(ServerLevel level, java.util.Collection<UUID> also, List<List<ArenaLogic.Seg>> lines) {
        var heard = new java.util.LinkedHashSet<ServerPlayer>(level.players());
        for (var id : also) {
            var player = level.getServer().getPlayerList().getPlayer(id);
            if (player != null) heard.add(player);
        }
        for (var line : lines) {
            var component = ArenaBoard.line(line);
            for (var player : heard) player.sendSystemMessage(component);
        }
        return heard.size();
    }

    // ---- damage and healing (the stats mixins)

    /** LivingEntity.hurtServer ended having taken {@code amount} of health and absorption: every running match hears of it. */
    public static void hurt(LivingEntity entity, DamageSource source, float amount) {
        var runtime = active;
        if (runtime == null || amount <= 0 || runtime.matches.isEmpty()) return;
        for (var match : runtime.matches.values()) match.hurt(entity, source, amount);
    }

    /** LivingEntity.heal is about to give {@code amount} health back (already capped at what was missing). */
    public static void healed(LivingEntity entity, float amount) {
        var runtime = active;
        if (runtime == null || amount <= 0 || runtime.matches.isEmpty()) return;
        for (var match : runtime.matches.values()) match.healed(entity, healer, amount);
    }

    /** Around an instant health effect: who it came from (the thrower or the drinker); returns who was set before. */
    public static Entity healing(Entity by) {
        var before = healer;
        healer = by;
        return before;
    }

    // ---- deaths and hits (the entity events)

    /** AFTER_DEATH: every running match hears of it (a wave mob's killer, a participant downed). */
    public static void died(LivingEntity entity, DamageSource source) {
        var runtime = active;
        if (runtime == null) return;
        for (var match : runtime.matches.values()) match.died(entity, source);
    }

    /** ALLOW_DAMAGE: false refuses a hit one participant lands on another outside team against team. */
    public static boolean allowDamage(LivingEntity entity, DamageSource source) {
        var runtime = active;
        if (runtime == null) return true;
        for (var match : runtime.matches.values()) if (!match.allowsDamage(entity, source)) return false;
        return true;
    }

    private ArenaLogic.DeathInventory deathRule(ServerPlayer player) {
        boolean inMatch = false, keep = false;
        for (var match : matches.values()) {
            if (match.running() && match.hasParticipant(player.getUUID())) {
                inMatch = true;
                if (match.keepsInventory()) keep = true;
            }
        }
        boolean inArena = ArenaGuard.protectedPoint(player.level(), player.getX(), player.getY(), player.getZ());
        return ArenaLogic.onPlayerDeath(inArena, inMatch, keep);
    }

    /**
     * Player.dropEquipment: decides whether a dying player drops items (vanilla), clears their inventory without
     * dropping, or keeps it across respawn (for a {@code keep_inventory} match). Remembers KEEP until respawn,
     * since the match may finish while the player is still on the death screen.
     */
    public static ArenaLogic.DeathInventory deathInventory(ServerPlayer player) {
        var runtime = active;
        if (runtime == null) return ArenaLogic.DeathInventory.DROP;
        var action = runtime.deathRule(player);
        if (action == ArenaLogic.DeathInventory.KEEP) {
            runtime.keepOnRespawn.add(player.getUUID());
            for (var match : runtime.matches.values()) match.captureDeathInventory(player);
        } else runtime.keepOnRespawn.remove(player.getUUID());
        return action;
    }

    /** Player.getBaseExperienceReward: true when a dying player is in an arena or match and should drop no XP orbs. */
    public static boolean suppressDeathExperience(ServerPlayer player) {
        var runtime = active;
        return runtime != null && runtime.deathRule(player) != ArenaLogic.DeathInventory.DROP;
    }

    /** ServerPlayer.restoreFrom: true once when this player died in a {@code keep_inventory} match and keeps their gear. */
    public static boolean consumeKeepOnRespawn(UUID playerId) {
        var runtime = active;
        return runtime != null && runtime.keepOnRespawn.remove(playerId);
    }

    /** ServerPlayer.restoreFrom: notifies running matches that a participant respawned (e.g. awaiting countdown re-entry). */
    public static void respawned(ServerPlayer player) {
        var runtime = active;
        if (runtime == null || runtime.matches.isEmpty()) return;
        for (var match : runtime.matches.values()) match.onPlayerRespawned(player);
    }

    /**
     * Every wave names a real mob; one creation per type proves it can be spawned. A wave may be bigger than the arena's
     * mob cap: the rest come in as wave mobs fall ({@link ArenaLogic.WaveFeed}). An arena with no mob room takes none.
     */
    private void checkWaves(ArenaRecord record, ServerLevel level, List<ArenaRequests.Wave> waves) {
        if (record.mobSpawns().isEmpty()) throw new ArenaFail("no_mob_spawns", record.name() + " has no mob_spawns marker");
        if (record.capacity().mobs() < 1) throw new ArenaFail("over_capacity", record.name() + " takes no mobs");
        var checked = new HashSet<String>();
        for (int i = 0; i < waves.size(); i++) {
            var wave = waves.get(i);
            for (var id : wave.mobs().keySet()) {
                if (!checked.add(id)) continue;
                var key = Identifier.tryParse(id);
                if (key == null || !BuiltInRegistries.ENTITY_TYPE.containsKey(key)) throw new ArenaFail("unknown_entity", id);
                Entity probe = BuiltInRegistries.ENTITY_TYPE.getValue(key).create(level, EntitySpawnReason.COMMAND);
                boolean mob = probe instanceof Mob;
                if (probe != null) probe.discard();
                if (!mob) throw new ArenaFail("not_a_mob", id);
            }
        }
    }

    private List<String> inBounds(ArenaRecord record, ServerLevel level) {
        var out = new ArrayList<String>();
        var box = record.box();
        for (var player : level.players()) {
            if (player.isAlive() && box.containsPoint(player.getX(), player.getY(), player.getZ())) out.add(player.getName().getString());
        }
        return out;
    }

    private ServerPlayer online(String name) {
        var player = server.getPlayerList().getPlayerByName(name);
        if (player == null) throw new ArenaFail("participant_offline", name);
        return player;
    }

    /** An online player who is not on the death screen: a match's participant, or one to kit. */
    private ServerPlayer living(String name) {
        var player = online(name);
        ArenaLogic.requireAlive(player.getName().getString(), player.isDeadOrDying());
        return player;
    }

    private Map<String, Object> stop(JsonObject request) {
        var record = require(ArenaRequests.parseName(request));
        var match = matches.get(record.name());
        if (match == null || !match.running()) throw new ArenaFail("no_match", record.name());
        match.stop("stopped");
        releaseMatch(record.name());
        return Map.of("name", record.name(), "state", "stopped");
    }

    private void releaseMatch(String name) {
        if (!holding.remove(name)) return;
        var record = registry.get(name);
        if (record == null) return;
        var level = levelOf(record.dimension());
        if (level == null) return;
        int cleared = clearLitter(record, level);  // while its chunks are still held: the floor is clean for the next match
        if (cleared > 0) emit("arena_cleared", Map.of("name", name, "litter", cleared));
        releaseChunks(record, level);
        registry.save();
    }

    // ---- the tick

    public void tick() {
        if (!builds.isEmpty()) {
            long deadline = System.nanoTime() + BUDGET_NANOS;
            for (Iterator<ArenaBuild> it = builds.iterator(); it.hasNext(); ) {
                var job = it.next();
                if (job.tick(Math.max(1L, deadline - System.nanoTime()), emitter)) {
                    it.remove();
                    finishBuild(job);
                }
            }
        }
        if (!matches.isEmpty()) {
            int now = server.getTickCount();
            for (var entry : matches.entrySet()) {
                var match = entry.getValue();
                match.tick(now);
                if (!match.running()) releaseMatch(entry.getKey());
            }
        }
    }

    private void finishBuild(ArenaBuild job) {
        var record = job.record;
        var level = job.level();
        releaseChunks(record, level);
        var data = new LinkedHashMap<String, Object>(job.progress());
        data.put("ticks", job.ticks());
        if (job.aborted() != null) {
            record.setState("incomplete");
            registry.save();
            data.put("reason", job.aborted());
            emit("arena_build_aborted", data);
            return;
        }
        if (job.kind == ArenaBuild.Kind.BUILD) {
            record.setState("built");
            sweep(record, level);
            registry.save();
            emit("arena_built", data);
        } else {
            registry.remove(record.name());
            matches.remove(record.name());
            registry.save();
            emit("arena_removed", Map.of("name", record.name(), "restored", true));
        }
    }

    /**
     * Removes mobs the arena does not own from its bounds (what stood there before it was built, or wandered in) and
     * the litter a match leaves (drops, experience, projectiles, potion clouds), so every match starts on a clean floor.
     */
    private void sweep(ArenaRecord record, ServerLevel level) {
        var area = area(record);
        for (var mob : level.getEntitiesOfClass(Mob.class, area, m -> !spared(m))) mob.discard();
        clearLitter(record, level);
    }

    /** The litter in the arena's bounds goes; how much there was. */
    private static int clearLitter(ArenaRecord record, ServerLevel level) {
        var litter = level.getEntities((Entity) null, area(record), ArenaRuntime::litter);
        for (var entity : litter) entity.discard();
        return litter.size();
    }

    private static AABB area(ArenaRecord record) {
        var box = record.box();
        return new AABB(box.minX(), box.minY(), box.minZ(), box.maxX() + 1.0, box.maxY() + 1.0, box.maxZ() + 1.0);
    }

    /** What a match leaves lying about: dropped items, experience orbs, arrows and other projectiles, potion clouds. */
    static boolean litter(Entity entity) {
        return entity instanceof net.minecraft.world.entity.item.ItemEntity
                || entity instanceof net.minecraft.world.entity.ExperienceOrb
                || entity instanceof net.minecraft.world.entity.projectile.Projectile
                || entity instanceof net.minecraft.world.entity.AreaEffectCloud;
    }

    /**
     * Litter loaded from a chunk file inside an arena whose match is running: left from an earlier match, in chunks that
     * were not loaded when this one started (a running match holds its chunks, so its own litter never loads from disk).
     */
    boolean staleLitter(Entity entity, ServerLevel level) {
        if (!litter(entity)) return false;
        var arena = registry.atPoint(ArenaGuard.dimension(level), entity.getX(), entity.getY(), entity.getZ());
        if (arena == null) return false;
        var match = matches.get(arena.name());
        return match != null && match.running();
    }

    // ---- the mob rules (called from the entity load event)

    private static boolean tamed(Mob mob) {
        return (mob instanceof TamableAnimal pet && pet.isTame()) || (mob instanceof AbstractHorse horse && horse.isTamed());
    }

    private boolean spared(Mob mob) {
        var tags = mob.entityTags();
        if (tags.contains(TRIAL_TAG) || tamed(mob)) return true;
        for (var tag : tags) {
            var arena = ArenaLogic.arenaOfTag(tag);
            if (arena != null && matches.get(arena) != null && matches.get(arena).owns(mob.getUUID())) return true;
        }
        return false;
    }

    private static boolean hasArenaTag(Entity entity) {
        for (var tag : entity.entityTags()) if (ArenaLogic.arenaOfTag(tag) != null) return true;
        return false;
    }

    /** A mob loaded from a chunk file that carries an arena's wave tag, when no match of that arena is running: left over. */
    boolean staleWaveMob(Entity entity) {
        for (var tag : entity.entityTags()) {
            var arena = ArenaLogic.arenaOfTag(tag);
            if (arena == null) continue;
            var match = matches.get(arena);
            if (match == null || !match.running()) return true;
        }
        return false;
    }

    /** Whether a mob added inside this arena may be: a wave mob, a trial mob, a pet, or made by a wave mob. */
    boolean admitMob(ArenaRecord arena, Mob mob, EntitySpawnReason reason) {
        if (mob.entityTags().contains(ArenaLogic.waveTag(arena.name())) || mob.entityTags().contains(TRIAL_TAG) || tamed(mob)) return true;
        var match = matches.get(arena.name());
        if (match != null && match.adoptDerived(mob, reason)) return true;
        blocked.merge(arena.name(), 1, Integer::sum);
        return false;
    }

    // ---- shutdown

    public void close() {
        for (var entry : matches.entrySet()) {
            entry.getValue().abandon();
            releaseMatch(entry.getKey());
        }
        for (var job : builds) {
            job.record.setState("incomplete");
            releaseChunks(job.record, job.level());
        }
        builds.clear();
        keepOnRespawn.clear();
        try { registry.save(); } catch (RuntimeException error) { LOG.error("cannot save the arena registry", error); }
        if (active == this) active = null;
    }
}
