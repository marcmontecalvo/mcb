package com.vulpine.mcb.arena;

import carpet.patches.EntityPlayerMPFake;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import net.minecraft.core.Holder;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntitySpawnReason;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.monster.piglin.AbstractPiglin;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.ProjectileWeaponItem;
import net.minecraft.world.item.TridentItem;
import net.minecraft.world.item.alchemy.Potion;
import net.minecraft.world.item.alchemy.PotionContents;
import net.minecraft.world.phys.Vec3;

/**
 * One match in one arena: co-op against mob waves, team against team (with mob waves or without), or a kill race between
 * teams. The clock, how a game ends and the kill tally live in {@link ArenaLogic}; this class feeds them the world's
 * facts (which wave mobs are alive, which participants are alive inside the bounds, who killed what) and does what they
 * ask. A sidebar kill board shows every participant's kills while it runs. Server thread only.
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

    /** A counted allied team mob's last known state. */
    private static final class AlliedTracked {
        final String team;
        final String type;
        final String customName;
        double x, y, z;
        double vx, vy, vz;
        float health;
        boolean dead;

        AlliedTracked(String team, String type, String customName, double x, double y, double z, float health) {
            this.team = team;
            this.type = type;
            this.customName = customName;
            this.x = x;
            this.y = y;
            this.z = z;
            this.health = health;
        }
    }

    private static final int RETARGET_TICKS = 10;
    /** How often the board looks again (who is up, mobs left); kills and downs redraw it at once. Only changed lines are sent. */
    private static final int BOARD_TICKS = 5;

    private final MinecraftServer server;
    private final ArenaEmit emit;
    final ArenaRecord arena;
    final ServerLevel level;
    final String mode;
    private final List<ArenaRequests.Wave> waves;
    private final ArenaLogic.WaveClock clock;
    private final List<UUID> participants;
    private final Map<UUID, String> names = new HashMap<>();
    private final Map<String, UUID> idByName = new HashMap<>();
    private final Set<UUID> fakePlayers = new HashSet<>();
    /** teams mode: team to its players, in plan order; empty in waves mode. */
    private final Map<String, List<UUID>> teams;
    private final Map<UUID, String> teamOf = new HashMap<>();
    private final Map<UUID, Tracked> mobs = new LinkedHashMap<>();
    private final List<ArenaAlliedMobs.MemberSpec> alliedSpecs;
    private final Map<UUID, AlliedTracked> alliedMobs = new LinkedHashMap<>();
    private final Map<String, Integer> alliedTeamKills = new LinkedHashMap<>();
    private final boolean hasAlliedMobs;
    private final Map<UUID, List<ItemStack>> startKits = new HashMap<>();
    private final Map<UUID, List<ItemStack>> deathKits = new HashMap<>();
    private final String tag;
    private final double followRange;
    /** Where participants still in the arena go when it ends, or null (they stay). */
    private final int[] exit;
    private final ArenaLogic.Tally tally;
    private final ArenaRespawns respawns;
    /** {match, of} when this match is one of a series, else null. */
    private final int[] series;
    private final boolean keepInventory;
    private final ArenaBoard board;
    private State state = State.RUNNING;
    private String winner, reason;
    private int startTick, endTick, ticks, spawned, killed;
    /** The wave being fought: its mobs not spawned yet (null before the first wave); {@code fed}: its mobs placed so far. */
    private ArenaLogic.WaveFeed feed;
    private int fed;
    /** The mob being spawned now: its jockey, added during finalizeSpawn, is already its child. */
    private Mob spawning;

    ArenaMatch(MinecraftServer server, ArenaEmit emit, ArenaRecord arena, ServerLevel level, String mode,
               List<ArenaRequests.Wave> waves, List<ServerPlayer> players, Map<String, List<ServerPlayer>> teams, int[] exit,
               int[] series, boolean keepInventory, ArenaRespawns.Config respawnConfig,
               List<ArenaAlliedMobs.MemberSpec> alliedSpecs) {
        this.server = server;
        this.emit = emit;
        this.arena = arena;
        this.level = level;
        this.mode = mode;
        this.waves = waves;
        this.exit = exit;
        this.series = series;
        this.keepInventory = keepInventory;
        this.alliedSpecs = alliedSpecs == null ? List.of() : List.copyOf(alliedSpecs);
        this.hasAlliedMobs = !this.alliedSpecs.isEmpty();
        this.board = new ArenaBoard(server, ArenaLogic.objectiveName(arena.name()));
        this.tag = ArenaLogic.waveTag(arena.name());
        this.followRange = ArenaLogic.followRange(arena.box());
        this.participants = new ArrayList<>();
        var fakeNames = new HashSet<String>();
        for (var p : players) {
            participants.add(p.getUUID());
            String name = p.getName().getString();
            names.put(p.getUUID(), name);
            idByName.put(name, p.getUUID());
            if (p instanceof EntityPlayerMPFake) {
                fakePlayers.add(p.getUUID());
                fakeNames.add(name);
            }
        }
        this.teams = new LinkedHashMap<>();
        var teamRoster = new LinkedHashMap<String, List<String>>();
        teams.forEach((team, members) -> {
            var ids = new ArrayList<UUID>();
            var memberNames = new ArrayList<String>();
            for (var p : members) {
                ids.add(p.getUUID());
                teamOf.put(p.getUUID(), team);
                memberNames.add(p.getName().getString());
            }
            this.teams.put(team, ids);
            teamRoster.put(team, memberNames);
            this.alliedTeamKills.put(team, 0);
        });
        var sides = new LinkedHashMap<String, String>();
        for (var id : participants) sides.put(names.get(id), teamOf.getOrDefault(id, ""));
        this.tally = new ArenaLogic.Tally(sides, new ArrayList<>(this.teams.keySet()));
        this.respawns = this.teams.isEmpty()
                ? ArenaRespawns.coop(participantNames(), fakeNames)
                : new ArenaRespawns(respawnConfig == null ? ArenaRespawns.Config.ELIMINATION : respawnConfig, teamRoster, fakeNames);
        this.clock = waves.isEmpty() ? null : new ArenaLogic.WaveClock(waves.stream().map(ArenaRequests.Wave::delay).toList());
    }

    State state() { return state; }

    boolean running() { return state == State.RUNNING; }

    boolean hasParticipant(UUID id) { return names.containsKey(id); }

    boolean keepsInventory() { return keepInventory; }

    void start() {
        startTick = server.getTickCount();
        if (ArenaLogic.sided(mode)) enterTeams();
        else enterWaves();
        for (var id : participants) {
            var player = resolvePlayer(id);
            if (player != null) startKits.put(id, copyInventory(player));
        }
        refreshBoard();
        var data = base();
        data.put("mode", mode);
        data.put("participants", participantNames());
        data.put("respawn_mode", respawns.config().mode().wireName());
        data.put("respawn_count", respawns.config().count());
        data.put("respawn_delay_ticks", respawns.config().delayTicks());
        if (clock != null) data.put("waves", waves.size());
        if (!teams.isEmpty()) data.put("teams", teamNames());
        if (hasAlliedMobs) data.put("team_mobs", alliedAliveByTeam());
        emit.emit("arena_match_started", data);
        if (hasAlliedMobs && !alliedMobs.isEmpty()) retarget();
    }

    // ---- the tick

    void tick(int serverTick) {
        if (state != State.RUNNING) return;
        ticks++;
        pruneAlliedMobs();
        processRespawns();
        ArenaLogic.Step step = null;
        if (clock != null) {
            prune();
            reinforce();
            step = clock.tick(mobs.size() + queued(), anyLive() || respawns.anyReturning() || !alliedMobs.isEmpty());
            switch (step.kind()) {
                case SPAWN -> spawnWave(step.wave());
                case CLEARED -> {
                    var data = base();
                    data.put("wave", step.wave());
                    data.put("waves", waves.size());
                    emit.emit("arena_wave_cleared", data);
                }
                default -> { }
            }
            if (state != State.RUNNING) return;  // a wave nothing of could spawn stopped it
        }
        var outcome = ArenaLogic.decide(mode, step, activeByTeam(), tally.byTeam());
        if (outcome != null) {
            winner = outcome.winner();
            if (outcome.won()) finish(State.WON, outcome.reason(), "arena_match_won");
            else finish(State.LOST, outcome.reason(), "arena_match_lost");
            return;
        }
        if ((clock != null || !alliedMobs.isEmpty()) && ticks % RETARGET_TICKS == 0) retarget();
        if (ticks % BOARD_TICKS == 0) refreshBoard();
    }

    private void processRespawns() {
        var online = new HashMap<String, Boolean>();
        var aliveOnline = new HashMap<String, Boolean>();
        for (var id : participants) {
            String name = names.get(id);
            var player = resolvePlayer(id);
            boolean isOnline = player != null;
            boolean isAlive = isOnline && player.isAlive();
            online.put(name, isOnline);
            aliveOnline.put(name, isAlive);
        }
        boolean boardDirty = false;
        for (var cancel : respawns.tickDisconnects(ticks, online, aliveOnline)) {
            if (cancel.hadPendingReturn()) {
                var data = base();
                data.put("player", cancel.player());
                if (!cancel.team().isEmpty()) data.put("team", cancel.team());
                data.put("tick", cancel.tick());
                data.put("reservation_id", cancel.reservationId());
                data.put("reason", cancel.reason());
                emit.emit("arena_respawn_cancelled", data);
                announce(cancel.player() + " forfeited their respawn (" + cancel.reason() + ").", "gray");
            }
            boardDirty = true;
        }
        if (respawns.config().enabled()) {
            for (var id : participants) {
                String name = names.get(id);
                int sec = respawns.pollCountdownSecond(name, ticks);
                if (sec > 0) {
                    var player = resolvePlayer(id);
                    if (player != null) {
                        player.sendSystemMessage(ArenaBoard.text("Returning to arena in " + sec + "s...", "yellow", true, false), true);
                    }
                    boardDirty = true;
                }
            }
            for (var playerName : respawns.dueToReturn(ticks, aliveOnline)) {
                UUID id = idByName.get(playerName);
                if (id == null) continue;
                var player = resolvePlayer(id);
                if (player == null || !player.isAlive()) continue;
                String team = teamOf.get(id);
                if (team == null || !arena.teams().containsKey(team)) continue;
                var pads = arena.teams().get(team).pads();
                int padIndex = ArenaRespawns.choosePadIndex(respawns.padSlot(playerName), pads.size(), respawns.respawnsUsed(playerName));
                int[] padCell = pads.get(padIndex);
                ensureScoreboardTeam(player, team);
                restoreKitOnReturn(id, player);
                healForReturn(player);
                place(player, padCell);
                var ret = respawns.completeReturn(playerName, ticks, arena.world(padCell));
                if (ret != null) {
                    var data = base();
                    data.put("player", ret.player());
                    data.put("team", ret.team());
                    data.put("tick", ret.tick());
                    data.put("reservation_id", ret.reservationId());
                    if (ret.pad() != null) data.put("pad", List.of(ret.pad()[0], ret.pad()[1], ret.pad()[2]));
                    data.put("respawns_left", ret.respawnsLeft());
                    emit.emit("arena_respawn_returned", data);
                    announce(playerName + " returned to the fight!", ArenaLogic.teamColor(team, teamNames().indexOf(team)));
                    boardDirty = true;
                }
            }
        }
        if (boardDirty) refreshBoard();
    }

    private Map<String, Boolean> liveMap() {
        var map = new HashMap<String, Boolean>();
        for (var id : participants) map.put(names.get(id), live(id));
        return map;
    }

    private Map<String, Integer> alliedAliveByTeam() {
        var out = new LinkedHashMap<String, Integer>();
        for (var team : teams.keySet()) out.put(team, 0);
        for (var t : alliedMobs.values()) {
            if (!t.dead) out.merge(t.team, 1, Integer::sum);
        }
        return out;
    }

    private Map<String, Integer> totalAliveByTeam(Map<String, Boolean> live) {
        var allied = alliedAliveByTeam();
        var out = new LinkedHashMap<String, Integer>();
        for (var entry : teams.entrySet()) {
            int pAlive = 0;
            for (var id : entry.getValue()) {
                if (Boolean.TRUE.equals(live.get(names.get(id)))) pAlive++;
            }
            out.put(entry.getKey(), pAlive + allied.getOrDefault(entry.getKey(), 0));
        }
        return out;
    }

    private Map<String, Integer> activeByTeam() {
        var playerActive = respawns.activeByTeam(liveMap());
        return hasAlliedMobs ? ArenaAlliedMobs.mergeActive(playerActive, alliedAliveByTeam()) : playerActive;
    }

    /** Captures a participant's inventory on death in {@code keep_inventory} matches so bots can restore it on return. */
    void captureDeathInventory(ServerPlayer player) {
        if (state == State.RUNNING && keepInventory && names.containsKey(player.getUUID())) {
            deathKits.put(player.getUUID(), copyInventory(player));
        }
    }

    /** Notifies a human returning from the death screen how long remains on their respawn countdown. */
    void onPlayerRespawned(ServerPlayer player) {
        if (state != State.RUNNING || !respawns.config().enabled()) return;
        String name = names.get(player.getUUID());
        if (name == null || !respawns.isReturning(name)) return;
        int sec = respawns.secondsRemaining(name, ticks);
        String msg = sec > 0 ? "Returning to arena in " + sec + "s..." : "Returning to arena...";
        player.sendSystemMessage(ArenaBoard.text(msg, "yellow", true, false), true);
    }

    private static List<ItemStack> copyInventory(ServerPlayer player) {
        var inv = player.getInventory();
        var copy = new ArrayList<ItemStack>(inv.getContainerSize());
        for (int i = 0; i < inv.getContainerSize(); i++) copy.add(inv.getItem(i).copy());
        return copy;
    }

    private void restoreKitOnReturn(UUID id, ServerPlayer player) {
        if (!keepInventory) {
            var saved = startKits.get(id);
            if (saved != null) applyInventory(player, saved);
            return;
        }
        if (fakePlayers.contains(id) && player.getInventory().isEmpty()) {
            var saved = deathKits.remove(id);
            if (saved != null) applyInventory(player, saved);
        } else {
            deathKits.remove(id);
        }
    }

    private static void applyInventory(ServerPlayer player, List<ItemStack> snapshot) {
        var inv = player.getInventory();
        inv.clearContent();
        player.containerMenu.setCarried(ItemStack.EMPTY);
        int limit = Math.min(inv.getContainerSize(), snapshot.size());
        for (int i = 0; i < limit; i++) inv.setItem(i, snapshot.get(i).copy());
        player.containerMenu.broadcastChanges();
    }

    private static void healForReturn(ServerPlayer player) {
        player.setHealth(player.getMaxHealth());
        player.getFoodData().setFoodLevel(20);
        player.getFoodData().setSaturation(5f);
        player.clearFire();
        player.removeAllEffects();
        player.setDeltaMovement(Vec3.ZERO);
        player.resetFallDistance();
    }

    private void ensureScoreboardTeam(ServerPlayer player, String teamName) {
        var scoreboard = server.getScoreboard();
        var sbTeam = scoreboard.getPlayerTeam(ArenaLogic.teamName(arena.name(), teamName));
        if (sbTeam != null) scoreboard.addPlayerToTeam(player.getScoreboardName(), sbTeam);
    }

    private void announce(String text, String color) {
        ArenaRuntime.tell(level, participants, List.of(List.of(new ArenaLogic.Seg(text, color, false))));
    }

    // ---- kills

    /** An entity died: wave and enemy allied mobs credit the killer; allied-mob kills credit allied_team_kills. */
    void died(LivingEntity entity, DamageSource source) {
        if (state != State.RUNNING) return;
        Entity killerEntity = source.getEntity();
        if (killerEntity == null) killerEntity = entity.getKillCredit();
        String killer = participantName(killerEntity);
        if (killer == null) killer = participantName(entity.getKillCredit());
        String killerAlliedTeam = alliedMobTeam(killerEntity);
        if (killerAlliedTeam == null) killerAlliedTeam = alliedMobTeam(entity.getKillCredit());

        if (mobs.containsKey(entity.getUUID())) {
            if (killer != null) tally.mob(killer);
            else if (killerAlliedTeam != null) alliedTeamKills.merge(killerAlliedTeam, 1, Integer::sum);
        } else if (alliedMobs.containsKey(entity.getUUID())) {
            var tracked = alliedMobs.get(entity.getUUID());
            if (tracked == null || tracked.dead) return;
            tracked.dead = true;
            if (killer != null) {
                UUID killerId = idByName.get(killer);
                String killerTeam = killerId == null ? "" : teamOf.getOrDefault(killerId, "");
                if (!tracked.team.equals(killerTeam)) tally.mob(killer);
            } else if (killerAlliedTeam != null && !killerAlliedTeam.equals(tracked.team)) {
                alliedTeamKills.merge(killerAlliedTeam, 1, Integer::sum);
            }
        } else if (names.containsKey(entity.getUUID())) {
            String victim = names.get(entity.getUUID());
            String victimTeam = teamOf.getOrDefault(entity.getUUID(), "");
            tally.down(victim);
            tally.player(killer, victim);
            if (killer == null && killerAlliedTeam != null && !killerAlliedTeam.equals(victimTeam)) {
                alliedTeamKills.merge(killerAlliedTeam, 1, Integer::sum);
            }
            var outcome = respawns.onDeath(victim, ticks, killer);
            if (!outcome.ignored()) {
                var diedData = base();
                diedData.put("player", outcome.player());
                if (!outcome.team().isEmpty()) diedData.put("team", outcome.team());
                if (killer != null) diedData.put("killer", killer);
                diedData.put("tick", outcome.tick());
                diedData.put("reserved", outcome.reserved());
                diedData.put("respawn_mode", respawns.config().mode().wireName());
                diedData.put("respawns_left", outcome.respawnsLeft());
                diedData.put("return_in_ticks", outcome.delayTicks());
                if (outcome.reserved()) diedData.put("reservation_id", outcome.reservationId());
                emit.emit("arena_participant_died", diedData);
                if (outcome.reserved()) {
                    var resData = base();
                    resData.put("player", outcome.player());
                    if (!outcome.team().isEmpty()) resData.put("team", outcome.team());
                    resData.put("tick", outcome.tick());
                    resData.put("return_at_tick", outcome.returnAtTick());
                    resData.put("delay_ticks", outcome.delayTicks());
                    resData.put("respawn_mode", respawns.config().mode().wireName());
                    resData.put("respawns_left", outcome.respawnsLeft());
                    resData.put("reservation_id", outcome.reservationId());
                    emit.emit("arena_respawn_reserved", resData);
                    if (respawns.config().enabled()) {
                        int sec = respawns.secondsRemaining(victim, ticks);
                        String leftText = outcome.respawnsLeft() == 1 ? "1 respawn left" : outcome.respawnsLeft() + " respawns left";
                        announce(victim + " down · returning in " + sec + "s (" + leftText + ")", "yellow");
                    }
                } else if (respawns.config().enabled()) {
                    announce(victim + " eliminated!", "gray");
                }
            }
        } else return;
        refreshBoard();
    }

    /** Health (and absorption) an entity lost to a hit: a participant's taken, dealt to an enemy, enemy allied mob, or wave mob. */
    void hurt(LivingEntity entity, DamageSource source, float amount) {
        if (state != State.RUNNING) return;
        String victim = names.get(entity.getUUID());
        boolean waveMob = mobs.containsKey(entity.getUUID());
        String attacker = participantName(source.getEntity());
        boolean enemyAlliedMob = false;
        if (alliedMobs.containsKey(entity.getUUID()) && attacker != null) {
            var tracked = alliedMobs.get(entity.getUUID());
            UUID attackerId = idByName.get(attacker);
            String attackerTeam = attackerId == null ? "" : teamOf.getOrDefault(attackerId, "");
            enemyAlliedMob = tracked != null && !tracked.team.equals(attackerTeam);
        }
        if (victim == null && !((waveMob || enemyAlliedMob) && attacker != null)) return;
        tally.hurt(attacker, victim, waveMob || enemyAlliedMob, amount);
    }

    /** Health given back to a participant: by {@code by} (who threw or drank the potion), else (null) by their own body. */
    void healed(LivingEntity entity, Entity by, float amount) {
        if (state != State.RUNNING) return;
        String target = names.get(entity.getUUID());
        if (target == null) return;
        String healer = by == null ? null : names.getOrDefault(by.getUUID(), "");
        tally.healed(healer, target, amount);
    }

    private ArenaAlliedMobs.Affiliation affiliationOf(Entity entity) {
        if (entity == null) return ArenaAlliedMobs.Affiliation.OUTSIDER;
        UUID id = entity.getUUID();
        if (names.containsKey(id)) return ArenaAlliedMobs.Affiliation.PLAYER;
        if (alliedMobs.containsKey(id)) return ArenaAlliedMobs.Affiliation.ALLIED_MOB;
        if (mobs.containsKey(id)) return ArenaAlliedMobs.Affiliation.WAVE_MOB;
        return ArenaAlliedMobs.Affiliation.OUTSIDER;
    }

    private String teamOfEntity(Entity entity) {
        if (entity == null) return "";
        UUID id = entity.getUUID();
        if (teamOf.containsKey(id)) return teamOf.get(id);
        var tracked = alliedMobs.get(id);
        return tracked == null ? "" : tracked.team;
    }

    boolean canMobTarget(Mob mob, LivingEntity target) {
        if (state != State.RUNNING) return true;
        var mobAff = affiliationOf(mob);
        if (mobAff == ArenaAlliedMobs.Affiliation.OUTSIDER) return true;
        var targetAff = affiliationOf(target);
        if (targetAff == ArenaAlliedMobs.Affiliation.PLAYER && !live(target.getUUID())) return false;
        return ArenaAlliedMobs.canMobTarget(mobAff, teamOfEntity(mob), targetAff, teamOfEntity(target));
    }

    /** Whether this hit may land: blocks all same-team damage in teams mode (including projectiles/explosions) and PvP outside teams. */
    boolean allowsDamage(LivingEntity target, DamageSource source) {
        if (state != State.RUNNING) return true;
        Entity by = source.getEntity();
        if (by == null) by = source.getDirectEntity();
        if (by == null) return true;
        return ArenaAlliedMobs.allowsDamage(mode, affiliationOf(by), teamOfEntity(by),
                affiliationOf(target), teamOfEntity(target), by == target);
    }

    private String participantName(Entity entity) {
        return entity instanceof ServerPlayer player ? names.get(player.getUUID()) : null;
    }

    private String alliedMobTeam(Entity entity) {
        if (entity == null) return null;
        var tracked = alliedMobs.get(entity.getUUID());
        return tracked == null ? null : tracked.team;
    }

    // ---- the kill board and the recap

    /** The live sidebar (ArenaLogic.matchBoard): the waves, each team and its players by kills, who is down or returning. */
    private void refreshBoard() {
        var live = liveMap();
        var players = new ArrayList<ArenaLogic.Player>();
        for (var id : participants) {
            String name = names.get(id);
            boolean returning = respawns.isReturning(name);
            players.add(new ArenaLogic.Player(name, teamOf.getOrDefault(id, ""), tally.score(name),
                    Boolean.TRUE.equals(live.get(name)), returning, respawns.respawnsLeft(name),
                    returning ? respawns.secondsRemaining(name, ticks) : 0));
        }
        ArenaLogic.WaveInfo info = clock == null ? null
                : new ArenaLogic.WaveInfo(Math.min(clock.index() + 1, waves.size()), waves.size(), mobs.size() + queued());
        var boardSummary = respawns.boardSummary(live, ticks);
        var lines = ArenaLogic.fit(ArenaLogic.matchBoard(mode, teamNames(), players, info, boardSummary,
                hasAlliedMobs ? alliedAliveByTeam() : null, hasAlliedMobs ? alliedTeamKills : null), ArenaLogic.BOARD_LINES);
        board.show(ArenaLogic.matchTitle(arena.name(), series), lines);
    }

    private String endTitle() {
        return ArenaLogic.endTitle(mode, state.name().toLowerCase(Locale.ROOT), winner, reason);
    }

    /** The match's final board stays up, titled with how it ended, and every player there reads the recap in chat. */
    private void closeBoard() {
        refreshBoard();
        board.retitle(endTitle() + (series == null ? "" : " · " + series[0] + "/" + series[1]));
        var header = ArenaRecap.header(endTitle(), series, endTick - startTick);
        ArenaRuntime.tell(level, participants, ArenaRecap.table(header, List.of(), teamNames(), tally.stats()));
    }

    void hideBoard() { board.hide(); }

    // ---- participants

    private ServerPlayer resolvePlayer(UUID id) {
        var player = server.getPlayerList().getPlayer(id);
        if (player != null) return player;
        String name = names.get(id);
        return name == null ? null : server.getPlayerList().getPlayerByName(name);
    }

    private boolean live(UUID id) {
        var player = resolvePlayer(id);
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
            var tag = ArenaLogic.nameTag(entry.getKey(), teamNames().indexOf(entry.getKey()));
            team.setColor(tag.nameColor().equals("reset") ? java.util.Optional.empty()
                    : java.util.Optional.of(net.minecraft.world.scores.TeamColor.valueOf(tag.nameColor().toUpperCase(Locale.ROOT))));
            team.setPlayerPrefix(ArenaBoard.text(tag.badge(), tag.badgeColor(), false, false));
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
        if (hasAlliedMobs) spawnAlliedMobs();
    }

    // ---- allied mobs

    private static final EquipmentSlot[] MOB_SLOTS = {
            EquipmentSlot.MAINHAND, EquipmentSlot.OFFHAND,
            EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET
    };

    /** Validates every allied mob entity type and every kit item/potion/enchantment id before a match starts. */
    static void validateAlliedMobs(ServerLevel level, List<ArenaAlliedMobs.MemberSpec> specs) {
        var checkedTypes = new HashSet<String>();
        for (var spec : specs) {
            if (checkedTypes.add(spec.type())) {
                var key = Identifier.tryParse(spec.type());
                if (key == null || !BuiltInRegistries.ENTITY_TYPE.containsKey(key))
                    throw new ArenaFail("unknown_entity", spec.type());
                Entity probe = BuiltInRegistries.ENTITY_TYPE.getValue(key).create(level, EntitySpawnReason.COMMAND);
                boolean isMob = probe instanceof Mob;
                if (probe != null) probe.discard();
                if (!isMob) throw new ArenaFail("not_a_mob", spec.type());
            }
            if (spec.effectiveKit() != null) {
                buildMobStacks(level, spec.effectiveKit().give());
            }
        }
    }

    private static List<ItemStack> buildMobStacks(ServerLevel level, List<ArenaAlliedMobs.Give> gives) {
        var stacks = new ArrayList<ItemStack>(gives.size());
        var enchantments = level.registryAccess().lookupOrThrow(Registries.ENCHANTMENT);
        for (var give : gives) {
            var itemId = Identifier.tryParse(give.item());
            if (itemId == null || !BuiltInRegistries.ITEM.containsKey(itemId))
                throw new ArenaFail("unknown_item", give.item());
            var stack = new ItemStack(BuiltInRegistries.ITEM.getValue(itemId), give.count());
            if (give.potion() != null) {
                var potionKey = Identifier.tryParse(give.potion());
                Holder<Potion> potion = potionKey == null ? null
                        : BuiltInRegistries.POTION.get(potionKey).map(h -> (Holder<Potion>) h).orElse(null);
                if (potion == null) throw new ArenaFail("unknown_potion", give.potion());
                stack.set(DataComponents.POTION_CONTENTS, new PotionContents(potion));
            }
            for (var entry : give.enchantments().entrySet()) {
                var enchKey = Identifier.tryParse(entry.getKey());
                var holder = enchKey == null ? null
                        : enchantments.get(ResourceKey.create(Registries.ENCHANTMENT, enchKey)).orElse(null);
                if (holder == null) throw new ArenaFail("unknown_enchantment", entry.getKey());
                stack.enchant(holder, entry.getValue());
            }
            stacks.add(stack);
        }
        return stacks;
    }

    private static boolean isMobWeapon(ItemStack stack) {
        if (stack.isEmpty()) return false;
        if (stack.has(DataComponents.WEAPON) || stack.has(DataComponents.PIERCING_WEAPON)
                || stack.has(DataComponents.KINETIC_WEAPON)) return true;
        if (stack.getItem() instanceof ProjectileWeaponItem || stack.getItem() instanceof TridentItem) return true;
        if (stack.has(DataComponents.FOOD) || stack.has(DataComponents.POTION_CONTENTS)) return false;
        String id = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
        return id.endsWith("_sword") || id.endsWith("_axe") || id.endsWith("_spear")
                || id.equals("minecraft:bow") || id.equals("minecraft:crossbow")
                || id.equals("minecraft:trident") || id.equals("minecraft:mace");
    }

    private void applyMobKit(Mob mob, ArenaAlliedMobs.MobKit kit) {
        if (kit == null) return;
        if (kit.clearInventory()) {
            for (var slot : MOB_SLOTS) mob.setItemSlot(slot, ItemStack.EMPTY);
        }
        var stacks = buildMobStacks(level, kit.give());
        boolean mainhandSet = false;
        for (var stack : stacks) {
            if (stack.has(DataComponents.BLOCKS_ATTACKS)) {
                mob.setItemSlot(EquipmentSlot.OFFHAND, stack.copy());
                mob.setDropChance(EquipmentSlot.OFFHAND, 0f);
                continue;
            }
            var equippable = stack.get(DataComponents.EQUIPPABLE);
            if (equippable != null) {
                var eqSlot = equippable.slot();
                if (eqSlot == EquipmentSlot.HEAD || eqSlot == EquipmentSlot.CHEST
                        || eqSlot == EquipmentSlot.LEGS || eqSlot == EquipmentSlot.FEET) {
                    mob.setItemSlot(eqSlot, stack.copy());
                    mob.setDropChance(eqSlot, 0f);
                    continue;
                }
            }
            if (!mainhandSet && isMobWeapon(stack)) {
                mob.setItemSlot(EquipmentSlot.MAINHAND, stack.copy());
                mob.setDropChance(EquipmentSlot.MAINHAND, 0f);
                mainhandSet = true;
            }
        }
        if (kit.heal()) {
            mob.setHealth(mob.getMaxHealth());
            mob.clearFire();
            mob.removeAllEffects();
        }
    }

    private void spawnAlliedMobs() {
        var scoreboard = server.getScoreboard();
        var box = arena.box();
        double cx = (box.minX() + box.maxX() + 1) / 2.0, cz = (box.minZ() + box.maxZ() + 1) / 2.0;
        var offsets = ArenaAlliedMobs.mobSearchOffsets();
        var perTeamSpawned = new HashMap<String, Integer>();
        for (var spec : alliedSpecs) {
            var teamMarker = arena.teams().get(spec.team());
            if (teamMarker == null || teamMarker.pads().isEmpty()) continue;
            var pads = teamMarker.pads();
            int teamPlayersCount = teams.getOrDefault(spec.team(), List.of()).size();
            int idx = perTeamSpawned.merge(spec.team(), 1, Integer::sum) - 1;
            int[] anchor = arena.world(pads.get((teamPlayersCount + idx) % pads.size()));
            var entityType = BuiltInRegistries.ENTITY_TYPE.getValue(Identifier.parse(spec.type()));
            Entity created = entityType.create(level, EntitySpawnReason.COMMAND);
            if (!(created instanceof Mob mob)) {
                if (created != null) created.discard();
                continue;
            }
            double dx = cx - (anchor[0] + .5), dz = cz - (anchor[2] + .5);
            float yaw = (float) Math.toDegrees(Math.atan2(-dx, dz));
            boolean placed = false;
            for (int[] offset : offsets) {
                double x = anchor[0] + offset[0] + .5, y = anchor[1] + offset[1], z = anchor[2] + offset[2] + .5;
                if (!box.containsPoint(x, y, z)) continue;
                mob.snapTo(x, y, z, yaw, 0f);
                if (level.noCollision(mob)) { placed = true; break; }
            }
            if (!placed) { mob.discard(); continue; }
            mob.addTag(tag);
            mob.setPersistenceRequired();
            mob.setCanPickUpLoot(false);
            if (mob instanceof AbstractPiglin piglin) piglin.setImmuneToZombification(true);
            var follow = mob.getAttribute(Attributes.FOLLOW_RANGE);
            if (follow != null) follow.setBaseValue(followRange);
            mob.finalizeSpawn(level, level.getCurrentDifficultyAt(mob.blockPosition()), EntitySpawnReason.COMMAND, null);
            var riders = new ArrayList<Entity>();
            mob.getIndirectPassengers().forEach(riders::add);
            for (var rider : riders) rider.discard();
            mob.ejectPassengers();
            applyMobKit(mob, spec.effectiveKit());
            for (var slot : MOB_SLOTS) mob.setDropChance(slot, 0f);
            if (ArenaAlliedMobs.needsDaylightHelmet(spec.type()) && mob.getItemBySlot(EquipmentSlot.HEAD).isEmpty()) {
                mob.setItemSlot(EquipmentSlot.HEAD, new ItemStack(Items.LEATHER_HELMET));
                mob.setDropChance(EquipmentSlot.HEAD, 0f);
            }
            if (spec.customName() != null) {
                mob.setCustomName(Component.literal(spec.customName()));
                mob.setCustomNameVisible(true);
            }
            var sbTeam = scoreboard.getPlayerTeam(ArenaLogic.teamName(arena.name(), spec.team()));
            if (sbTeam != null) scoreboard.addPlayerToTeam(mob.getScoreboardName(), sbTeam);
            alliedMobs.put(mob.getUUID(), new AlliedTracked(spec.team(), spec.type(), spec.customName(),
                    mob.getX(), mob.getY(), mob.getZ(), mob.getHealth()));
            level.addFreshEntity(mob);
        }
    }

    private void pruneAlliedMobs() {
        for (Iterator<Map.Entry<UUID, AlliedTracked>> it = alliedMobs.entrySet().iterator(); it.hasNext(); ) {
            var entry = it.next();
            Entity entity = level.getEntityInAnyDimension(entry.getKey());
            var t = entry.getValue();
            if (entity == null) {
                if (level.getChunkSource().hasChunk((int) Math.floor(t.x) >> 4, (int) Math.floor(t.z) >> 4)) it.remove();
            } else if (entity.isRemoved() || !entity.isAlive()) {
                it.remove();
            } else {
                t.x = entity.getX(); t.y = entity.getY(); t.z = entity.getZ();
                var v = entity.getDeltaMovement();
                t.vx = v.x; t.vy = v.y; t.vz = v.z;
                if (entity instanceof LivingEntity living) t.health = living.getHealth();
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

    /** The arena's cap of wave mobs comes at once; the rest of the wave waits in {@link #feed} for room. */
    private void spawnWave(int index) {
        var wave = waves.get(index);
        feed = new ArenaLogic.WaveFeed(wave.mobs(), arena.capacity().mobs());
        fed = 0;
        int failed = spawnFed(feed.take(mobs.size()));
        var data = base();
        data.put("wave", index);
        data.put("waves", waves.size());
        data.put("size", feed.total());
        data.put("mobs", mobs.size());
        data.put("queued", feed.pending());
        data.put("failed", failed);
        emit.emit("arena_wave_started", data);
        if (mobs.isEmpty()) finish(State.STOPPED, "spawn_failed", "arena_match_stopped");
    }

    /** As wave mobs fall, the wave's queued mobs take their places, up to the arena's cap. */
    private void reinforce() {
        if (feed == null || feed.pending() == 0) return;
        spawnFed(feed.take(mobs.size()));
    }

    private int queued() { return feed == null ? 0 : feed.pending(); }

    /** Spawns these types round-robin over the spawn markers, continuing the wave's count; how many found no room. */
    private int spawnFed(List<String> types) {
        var markers = arena.mobSpawns();
        int failed = 0;
        for (var id : types) {
            var type = BuiltInRegistries.ENTITY_TYPE.getValue(Identifier.parse(id));
            var marker = arena.world(markers.get(ArenaLogic.spawnMarker(fed++, markers.size())));
            if (!spawnOne(type.create(level, EntitySpawnReason.COMMAND), marker)) failed++;
        }
        return failed;
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

    boolean owns(UUID id) { return mobs.containsKey(id) || alliedMobs.containsKey(id); }

    private void retarget() {
        var entities = new ArrayList<LivingEntity>();
        var candidates = new ArrayList<ArenaAlliedMobs.Combatant>();
        for (var id : participants) {
            var player = resolvePlayer(id);
            if (player != null && live(id)) {
                int idx = entities.size();
                entities.add(player);
                candidates.add(new ArenaAlliedMobs.Combatant(idx, ArenaAlliedMobs.Affiliation.PLAYER,
                        teamOf.getOrDefault(id, ""), player.getX(), player.getY(), player.getZ(), true));
            }
        }
        var liveAllied = new ArrayList<Map.Entry<Mob, AlliedTracked>>();
        for (var entry : alliedMobs.entrySet()) {
            if (entry.getValue().dead) continue;
            if (!(level.getEntityInAnyDimension(entry.getKey()) instanceof Mob mob) || mob.level() != level
                    || !mob.isAlive() || mob.isRemoved()) continue;
            int idx = entities.size();
            entities.add(mob);
            candidates.add(new ArenaAlliedMobs.Combatant(idx, ArenaAlliedMobs.Affiliation.ALLIED_MOB,
                    entry.getValue().team, mob.getX(), mob.getY(), mob.getZ(), true));
            liveAllied.add(Map.entry(mob, entry.getValue()));
        }
        var liveWave = new ArrayList<Mob>();
        for (var id : mobs.keySet()) {
            if (!(level.getEntityInAnyDimension(id) instanceof Mob mob) || mob.level() != level
                    || !mob.isAlive() || mob.isRemoved()) continue;
            int idx = entities.size();
            entities.add(mob);
            candidates.add(new ArenaAlliedMobs.Combatant(idx, ArenaAlliedMobs.Affiliation.WAVE_MOB,
                    "", mob.getX(), mob.getY(), mob.getZ(), true));
            liveWave.add(mob);
        }
        for (var entry : liveAllied) {
            var mob = entry.getKey();
            var tracked = entry.getValue();
            if (mob.getLastHurtByMob() != null && !canMobTarget(mob, mob.getLastHurtByMob())) mob.setLastHurtByMob(null);
            if (mob.getTarget() != null && !canMobTarget(mob, mob.getTarget())) mob.setTarget(null);
            int best = ArenaAlliedMobs.pickAlliedTarget(tracked.team, mob.getX(), mob.getY(), mob.getZ(), candidates);
            if (best >= 0 && mob.getTarget() != entities.get(best)) mob.setTarget(entities.get(best));
        }
        for (var mob : liveWave) {
            if (mob.getTarget() != null && !canMobTarget(mob, mob.getTarget())) mob.setTarget(null);
            int best = ArenaAlliedMobs.pickWaveTarget(mob.getX(), mob.getY(), mob.getZ(), candidates);
            if (best >= 0 && mob.getTarget() != entities.get(best)) mob.setTarget(entities.get(best));
        }
    }

    // ---- ending

    void stop(String why) { finish(State.STOPPED, why, "arena_match_stopped"); }

    /**
     * Ends the match: the kill board stays up with how it ended and the recap goes to chat, every remaining wave mob is
     * discarded, the scoreboard teams go, participants still in the arena go to the exit (when there is one), the event
     * is sent once with the tally.
     */
    private void finish(State end, String why, String event) {
        if (state != State.RUNNING) return;
        state = end;
        reason = why;
        endTick = server.getTickCount();
        closeBoard();  // before the mobs go and the players leave: the board shows the end as it was
        var finalAlliedAlive = hasAlliedMobs ? alliedAliveByTeam() : null;
        var finalTotalAlive = hasAlliedMobs ? totalAliveByTeam(liveMap()) : null;
        discardMobs();
        leaveTeams();
        sendToExit();
        var data = base();
        data.put("mode", mode);
        data.put("respawn_mode", respawns.config().mode().wireName());
        data.put("respawn_count", respawns.config().count());
        data.put("respawn_delay_ticks", respawns.config().delayTicks());
        if (winner != null) data.put("winner", winner);
        if (why != null) data.put("reason", why);
        if (clock != null) {
            data.put("waves_cleared", clock.index());
            data.put("waves", waves.size());
        }
        data.put("kills", tally.rows());
        if (!teams.isEmpty()) data.put("team_kills", tally.byTeam());
        if (hasAlliedMobs) {
            data.put("allied_team_kills", new LinkedHashMap<>(alliedTeamKills));
            data.put("teams_allied_mobs_alive", finalAlliedAlive);
            data.put("teams_total_alive", finalTotalAlive);
        }
        data.put("duration_ticks", endTick - startTick);
        startKits.clear();
        deathKits.clear();
        emit.emit(event, data);
    }

    /** Participants still inside the arena step out to the exit cell (the arena server's lobby). */
    private void sendToExit() {
        if (exit == null) return;
        for (var id : participants) {
            var player = server.getPlayerList().getPlayer(id);
            if (player == null || !live(id)) continue;
            player.teleportTo(level, exit[0] + .5, exit[1], exit[2] + .5, Set.of(), player.getYRot(), 0f, true);
        }
    }

    void discardMobs() {
        for (var id : mobs.keySet()) {
            var entity = level.getEntityInAnyDimension(id);
            if (entity != null) entity.discard();
        }
        mobs.clear();
        for (var id : alliedMobs.keySet()) {
            var entity = level.getEntityInAnyDimension(id);
            if (entity != null) entity.discard();
        }
        alliedMobs.clear();
    }

    /** The server is going down: end the match quietly (mobs and teams cleaned up, no event). */
    void abandon() {
        if (state != State.RUNNING) return;
        state = State.STOPPED;
        reason = "server_stopping";
        discardMobs();
        leaveTeams();
        hideBoard();
        startKits.clear();
        deathKits.clear();
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
        out.put("respawn_mode", respawns.config().mode().wireName());
        out.put("respawn_count", respawns.config().count());
        out.put("respawn_delay_ticks", respawns.config().delayTicks());
        if (series != null) out.put("series", List.of(series[0], series[1]));
        if (reason != null) out.put("reason", reason);
        if (winner != null) out.put("winner", winner);
        out.put("ticks", (state == State.RUNNING ? server.getTickCount() : endTick) - startTick);
        if (clock != null) {
            out.put("wave_index", clock.index());
            out.put("waves", waves.size());
            out.put("next_wave_in_ticks", clock.nextIn());
            out.put("spawned", spawned);
            out.put("killed", killed);
            out.put("queued", queued());
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
        var live = liveMap();
        var people = new ArrayList<Map<String, Object>>();
        for (var id : participants) {
            var p = new LinkedHashMap<String, Object>();
            String name = names.get(id);
            p.put("uuid", id.toString());
            p.put("name", name);
            p.put("alive", Boolean.TRUE.equals(live.get(name)));
            p.put("state", respawns.state(name).wireName());
            p.put("returning", respawns.isReturning(name));
            p.put("return_in_ticks", respawns.ticksRemaining(name, ticks));
            p.put("respawns_left", respawns.respawnsLeft(name));
            p.put("respawns_used", respawns.respawnsUsed(name));
            if (teamOf.containsKey(id)) p.put("team", teamOf.get(id));
            var player = resolvePlayer(id);
            if (player != null) p.put("position", List.of(round(player.getX()), round(player.getY()), round(player.getZ())));
            p.put("mob_kills", tally.mobs(name));
            p.put("player_kills", tally.players(name));
            p.put("deaths", tally.deaths(name));
            p.put("damage_dealt", ArenaLogic.tenth(tally.dealt(name)));
            p.put("damage_taken", ArenaLogic.tenth(tally.taken(name)));
            p.put("healing", ArenaLogic.tenth(tally.healing(name)));
            people.add(p);
        }
        out.put("participants", people);
        out.put("respawns", respawns.statusSnapshot(live, ticks));
        if (hasAlliedMobs || !alliedMobs.isEmpty()) {
            var alliedList = new ArrayList<Map<String, Object>>();
            for (var entry : alliedMobs.entrySet()) {
                var t = entry.getValue();
                if (t.dead) continue;
                var m = new LinkedHashMap<String, Object>();
                m.put("uuid", entry.getKey().toString());
                m.put("team", t.team);
                m.put("type", t.type);
                if (t.customName != null) m.put("name", t.customName);
                m.put("position", List.of(round(t.x), round(t.y), round(t.z)));
                m.put("health", (double) t.health);
                m.put("velocity", List.of(round(t.vx * 20), round(t.vy * 20), round(t.vz * 20)));
                alliedList.add(m);
            }
            out.put("team_mobs", alliedList);
        }
        if (!teams.isEmpty()) {
            var byTeam = new LinkedHashMap<String, Object>();
            teams.forEach((team, members) -> byTeam.put(team, members.stream().filter(this::live).count()));
            out.put("teams_alive", byTeam);
            out.put("teams_allied_mobs_alive", alliedAliveByTeam());
            out.put("teams_total_alive", totalAliveByTeam(live));
            out.put("allied_team_kills", new LinkedHashMap<>(alliedTeamKills));
            out.put("teams_returning", respawns.returningByTeam());
            out.put("teams_respawns_left", respawns.respawnsLeftByTeam());
            out.put("team_kills", tally.byTeam());
        }
        return out;
    }

    private static double round(double v) { return Math.round(v * 100.0) / 100.0; }
}
