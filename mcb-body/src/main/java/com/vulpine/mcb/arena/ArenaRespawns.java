package com.vulpine.mcb.arena;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Pure PvP respawn state machine (checked by ArenaLogicTest): configuration and normalization, atomic once-per-death
 * reservation (elimination, per-participant, or equal per-team shared pool), countdown timers, pad selection,
 * disconnect/forfeit resolution without indefinite waits, team elimination calculation, and board/status summaries.
 */
public final class ArenaRespawns {
    public static final int DEFAULT_DELAY_TICKS = 60;
    public static final int MAX_RESPAWNS = 99;
    public static final int MAX_DELAY_TICKS = 1200;
    public static final int DEFAULT_REENTRY_GRACE_TICKS = 200;

    public enum Mode {
        ELIMINATION("elimination"),
        PLAYER("player"),
        TEAM("team");

        private final String wire;

        Mode(String wire) { this.wire = wire; }

        public String wireName() { return wire; }

        public static Mode parse(String raw) {
            if (raw == null) throw new ArenaFail("bad_request", "respawn_mode must be elimination, player or team");
            return switch (raw) {
                case "elimination" -> ELIMINATION;
                case "player" -> PLAYER;
                case "team" -> TEAM;
                default -> throw new ArenaFail("bad_request", "respawn_mode must be elimination, player or team (got " + raw + ")");
            };
        }
    }

    /** Normalized respawn rule for a match: 0 extra respawns always normalizes to {@link Mode#ELIMINATION}. */
    public record Config(Mode mode, int count, int delayTicks) {
        public static final Config ELIMINATION = new Config(Mode.ELIMINATION, 0, DEFAULT_DELAY_TICKS);

        public static Config of(Mode mode, int count, int delayTicks) {
            if (mode == null) throw new ArenaFail("bad_request", "respawn_mode is required");
            if (count < 0 || count > MAX_RESPAWNS)
                throw new ArenaFail("bad_request", "respawn_count must be 0.." + MAX_RESPAWNS);
            if (delayTicks < 0 || delayTicks > MAX_DELAY_TICKS)
                throw new ArenaFail("bad_request", "respawn_delay_ticks must be 0.." + MAX_DELAY_TICKS);
            if (mode == Mode.ELIMINATION && count != 0)
                throw new ArenaFail("bad_request", "elimination allows 0 respawns");
            if (mode != Mode.ELIMINATION && count == 0)
                return new Config(Mode.ELIMINATION, 0, delayTicks);
            return new Config(mode, count, delayTicks);
        }

        public boolean enabled() { return mode != Mode.ELIMINATION && count > 0; }
    }

    public enum ParticipantState {
        ALIVE,
        RETURNING,
        ELIMINATED,
        FORFEITED;

        public String wireName() { return name().toLowerCase(Locale.ROOT); }
    }

    public record DeathOutcome(String player, String team, String killer, int tick, boolean reserved, boolean ignored,
                               int reservationId, int returnAtTick, int delayTicks, int respawnsLeft,
                               ParticipantState state) {}

    public record ReturnOutcome(String player, String team, int tick, int reservationId, int[] pad, int respawnsLeft) {}

    public record CancelOutcome(String player, String team, int tick, int reservationId, String reason,
                                boolean hadPendingReturn) {}

    public record TeamBoard(int alive, int returning, int respawnsLeft) {}

    public record BoardSummary(Mode mode, boolean enabled, Map<String, TeamBoard> teams) {}

    private static final class Entry {
        final String name;
        final String team;
        final boolean fake;
        final int padSlot;
        ParticipantState state = ParticipantState.ALIVE;
        int deaths;
        int respawnsUsed;
        int personalRemaining;
        int reservationId;
        int returnAtTick = -1;
        int lastAnnouncedSecond = -1;

        Entry(String name, String team, boolean fake, int padSlot, int personalRemaining) {
            this.name = name;
            this.team = team;
            this.fake = fake;
            this.padSlot = padSlot;
            this.personalRemaining = personalRemaining;
        }
    }

    private final Config config;
    private final int reentryGraceTicks;
    private final Map<String, List<String>> teams = new LinkedHashMap<>();
    private final Map<String, Entry> entries = new LinkedHashMap<>();
    private final Map<String, Integer> teamPools = new LinkedHashMap<>();
    private final List<Map<String, Object>> eventLog = new ArrayList<>();
    private int nextReservationId;

    public ArenaRespawns(Config config, Map<String, List<String>> teams, Set<String> fakePlayers) {
        this(config, teams, fakePlayers, DEFAULT_REENTRY_GRACE_TICKS);
    }

    public ArenaRespawns(Config config, Map<String, List<String>> teams, Set<String> fakePlayers, int reentryGraceTicks) {
        if (reentryGraceTicks < 0) throw new ArenaFail("bad_request", "reentryGraceTicks must be nonnegative");
        this.config = config == null ? Config.ELIMINATION : config;
        this.reentryGraceTicks = reentryGraceTicks;
        var fakes = fakePlayers == null ? Set.<String>of() : fakePlayers;
        for (var entry : teams.entrySet()) {
            String team = entry.getKey();
            var members = List.copyOf(entry.getValue());
            this.teams.put(team, members);
            if (!members.isEmpty()) {
                this.teamPools.put(team, this.config.mode() == Mode.TEAM ? this.config.count() : 0);
            } else {
                this.teamPools.put(team, 0);
            }
            for (int i = 0; i < members.size(); i++) {
                String player = members.get(i);
                int personal = this.config.mode() == Mode.PLAYER ? this.config.count() : 0;
                this.entries.put(player, new Entry(player, team, fakes.contains(player), i, personal));
            }
        }
    }

    /** Co-op / non-sided match tracker (always elimination, no teams). */
    public static ArenaRespawns coop(List<String> participants, Set<String> fakePlayers) {
        var tracker = new ArenaRespawns(Config.ELIMINATION, Map.of(), fakePlayers, DEFAULT_REENTRY_GRACE_TICKS);
        var fakes = fakePlayers == null ? Set.<String>of() : fakePlayers;
        for (int i = 0; i < participants.size(); i++) {
            String player = participants.get(i);
            tracker.entries.put(player, new Entry(player, "", fakes.contains(player), i, 0));
        }
        return tracker;
    }

    public Config config() { return config; }

    public ParticipantState state(String player) {
        var e = entries.get(player);
        return e == null ? ParticipantState.ELIMINATED : e.state;
    }

    public boolean isReturning(String player) { return state(player) == ParticipantState.RETURNING; }

    public int respawnsUsed(String player) {
        var e = entries.get(player);
        return e == null ? 0 : e.respawnsUsed;
    }

    public int padSlot(String player) {
        var e = entries.get(player);
        return e == null ? 0 : e.padSlot;
    }

    public int respawnsLeft(String player) {
        var e = entries.get(player);
        if (e == null) return 0;
        return switch (config.mode()) {
            case ELIMINATION -> 0;
            case PLAYER -> e.personalRemaining;
            case TEAM -> teamPools.getOrDefault(e.team, 0);
        };
    }

    public int teamRespawnsLeft(String team) {
        return switch (config.mode()) {
            case ELIMINATION -> 0;
            case TEAM -> teamPools.getOrDefault(team, 0);
            case PLAYER -> {
                int sum = 0;
                for (var name : teams.getOrDefault(team, List.of())) {
                    var e = entries.get(name);
                    if (e != null && (e.state == ParticipantState.ALIVE || e.state == ParticipantState.RETURNING)) {
                        sum += e.personalRemaining;
                    }
                }
                yield sum;
            }
        };
    }

    public int ticksRemaining(String player, int tick) {
        var e = entries.get(player);
        if (e == null || e.state != ParticipantState.RETURNING) return 0;
        return Math.max(0, e.returnAtTick - tick);
    }

    public int secondsRemaining(String player, int tick) {
        int rem = ticksRemaining(player, tick);
        return rem <= 0 ? 0 : (rem + 19) / 20;
    }

    /** Returns the next countdown second to announce for a returning player, or -1 if unchanged. */
    public int pollCountdownSecond(String player, int tick) {
        var e = entries.get(player);
        if (e == null || e.state != ParticipantState.RETURNING) return -1;
        int sec = secondsRemaining(player, tick);
        if (sec <= 0 || sec == e.lastAnnouncedSecond) return -1;
        e.lastAnnouncedSecond = sec;
        return sec;
    }

    public boolean anyReturning() {
        for (var e : entries.values()) if (e.state == ParticipantState.RETURNING) return true;
        return false;
    }

    /**
     * Records a participant death and atomically reserves at most one respawn. Duplicate death notifications while a
     * participant is already returning/eliminated/forfeited are ignored so respawns can never be double-spent.
     */
    public DeathOutcome onDeath(String player, int tick, String killer) {
        var e = entries.get(player);
        if (e == null || e.state != ParticipantState.ALIVE) {
            return new DeathOutcome(player, e == null ? "" : e.team, killer, tick, false, true, 0, -1, 0,
                    e == null ? 0 : respawnsLeft(player), e == null ? ParticipantState.ELIMINATED : e.state);
        }
        e.deaths++;
        boolean reserved = false;
        if (config.mode() == Mode.PLAYER && e.personalRemaining > 0) {
            e.personalRemaining--;
            reserved = true;
        } else if (config.mode() == Mode.TEAM) {
            int pool = teamPools.getOrDefault(e.team, 0);
            if (pool > 0) {
                teamPools.put(e.team, pool - 1);
                reserved = true;
            }
        }
        int left = respawnsLeft(player);
        if (reserved) {
            int id = ++nextReservationId;
            e.state = ParticipantState.RETURNING;
            e.reservationId = id;
            e.returnAtTick = tick + config.delayTicks();
            e.lastAnnouncedSecond = -1;
            recordDeathEvent(e, killer, tick, true, id, config.delayTicks(), left);
            var res = new LinkedHashMap<String, Object>();
            res.put("type", "reserved");
            res.put("player", e.name);
            if (!e.team.isEmpty()) res.put("team", e.team);
            res.put("tick", tick);
            res.put("return_at_tick", e.returnAtTick);
            res.put("delay_ticks", config.delayTicks());
            res.put("respawn_mode", config.mode().wireName());
            res.put("respawns_left", left);
            res.put("reservation_id", id);
            eventLog.add(Collections.unmodifiableMap(res));
            return new DeathOutcome(e.name, e.team, killer, tick, true, false, id, e.returnAtTick, config.delayTicks(),
                    left, e.state);
        }
        e.state = ParticipantState.ELIMINATED;
        e.reservationId = 0;
        e.returnAtTick = -1;
        e.lastAnnouncedSecond = -1;
        recordDeathEvent(e, killer, tick, false, 0, 0, left);
        return new DeathOutcome(e.name, e.team, killer, tick, false, false, 0, -1, 0, left, e.state);
    }

    private void recordDeathEvent(Entry e, String killer, int tick, boolean reserved, int reservationId,
                                  int returnInTicks, int respawnsLeft) {
        var ev = new LinkedHashMap<String, Object>();
        ev.put("type", "death");
        ev.put("player", e.name);
        if (!e.team.isEmpty()) ev.put("team", e.team);
        if (killer != null) ev.put("killer", killer);
        ev.put("tick", tick);
        ev.put("reserved", reserved);
        ev.put("respawn_mode", config.mode().wireName());
        ev.put("respawns_left", respawnsLeft);
        ev.put("return_in_ticks", returnInTicks);
        if (reserved) ev.put("reservation_id", reservationId);
        eventLog.add(Collections.unmodifiableMap(ev));
    }

    /** Returning participants whose countdown has finished and whose entity is online and alive. */
    public List<String> dueToReturn(int tick, Map<String, Boolean> aliveOnline) {
        var ready = new ArrayList<Entry>();
        for (var e : entries.values()) {
            if (e.state == ParticipantState.RETURNING && tick >= e.returnAtTick
                    && Boolean.TRUE.equals(aliveOnline.get(e.name))) {
                ready.add(e);
            }
        }
        ready.sort(Comparator.comparingInt(e -> e.reservationId));
        var out = new ArrayList<String>(ready.size());
        for (var e : ready) out.add(e.name);
        return out;
    }

    /** Completes a pending return once the player/bot has been placed back on a team pad. */
    public ReturnOutcome completeReturn(String player, int tick, int[] padWorld) {
        var e = entries.get(player);
        if (e == null || e.state != ParticipantState.RETURNING) return null;
        int id = e.reservationId;
        e.state = ParticipantState.ALIVE;
        e.respawnsUsed++;
        e.reservationId = 0;
        e.returnAtTick = -1;
        e.lastAnnouncedSecond = -1;
        int left = respawnsLeft(player);
        int[] padCopy = padWorld == null ? null : padWorld.clone();
        var ev = new LinkedHashMap<String, Object>();
        ev.put("type", "returned");
        ev.put("player", e.name);
        if (!e.team.isEmpty()) ev.put("team", e.team);
        ev.put("tick", tick);
        ev.put("reservation_id", id);
        if (padCopy != null) ev.put("pad", List.of(padCopy[0], padCopy[1], padCopy[2]));
        ev.put("respawns_left", left);
        eventLog.add(Collections.unmodifiableMap(ev));
        return new ReturnOutcome(e.name, e.team, tick, id, padCopy, left);
    }

    /**
     * Resolves an explicit disconnect: an alive participant forfeits immediately without spending a respawn; a returning
     * human cancels immediately so the match never waits indefinitely. A returning fake player is handled by
     * {@link #tickDisconnects} with {@code reentryGraceTicks} because Carpet disconnects fake players on death.
     */
    public CancelOutcome onDisconnect(String player, int tick) {
        var e = entries.get(player);
        if (e == null || e.state == ParticipantState.ELIMINATED || e.state == ParticipantState.FORFEITED) return null;
        if (e.state == ParticipantState.RETURNING && e.fake) return null;
        return cancelOrForfeit(e, tick, "disconnected");
    }

    /** Explicit forfeit (cancels any pending return and marks the participant forfeited). */
    public CancelOutcome forfeit(String player, int tick, String reason) {
        var e = entries.get(player);
        if (e == null || e.state == ParticipantState.ELIMINATED || e.state == ParticipantState.FORFEITED) return null;
        return cancelOrForfeit(e, tick, reason == null || reason.isEmpty() ? "forfeited" : reason);
    }

    /** Checks offline participants and stalled pending returns so no match can hang indefinitely. */
    public List<CancelOutcome> tickDisconnects(int tick, Map<String, Boolean> online) {
        return tickDisconnects(tick, online, null);
    }

    /** Checks offline participants and stalled pending returns (including players stuck on the death screen). */
    public List<CancelOutcome> tickDisconnects(int tick, Map<String, Boolean> online, Map<String, Boolean> alive) {
        var out = new ArrayList<CancelOutcome>();
        for (var e : entries.values()) {
            boolean isOnline = Boolean.TRUE.equals(online.get(e.name));
            if (e.state == ParticipantState.ALIVE) {
                if (!isOnline) {
                    var outcome = cancelOrForfeit(e, tick, "disconnected");
                    if (outcome != null) out.add(outcome);
                }
            } else if (e.state == ParticipantState.RETURNING) {
                if (!e.fake && !isOnline) {
                    var outcome = cancelOrForfeit(e, tick, "disconnected");
                    if (outcome != null) out.add(outcome);
                } else if (tick >= e.returnAtTick + reentryGraceTicks) {
                    boolean isAlive = alive == null ? isOnline : Boolean.TRUE.equals(alive.get(e.name));
                    if (!isOnline || !isAlive) {
                        var outcome = cancelOrForfeit(e, tick, "respawn_timeout");
                        if (outcome != null) out.add(outcome);
                    }
                }
            }
        }
        return out;
    }

    private CancelOutcome cancelOrForfeit(Entry e, int tick, String reason) {
        boolean pending = e.state == ParticipantState.RETURNING;
        int id = e.reservationId;
        e.state = ParticipantState.FORFEITED;
        e.reservationId = 0;
        e.returnAtTick = -1;
        e.lastAnnouncedSecond = -1;
        var ev = new LinkedHashMap<String, Object>();
        ev.put("type", pending ? "cancelled" : "forfeited");
        ev.put("player", e.name);
        if (!e.team.isEmpty()) ev.put("team", e.team);
        ev.put("tick", tick);
        if (pending) ev.put("reservation_id", id);
        ev.put("reason", reason);
        eventLog.add(Collections.unmodifiableMap(ev));
        return new CancelOutcome(e.name, e.team, tick, id, reason, pending);
    }

    /**
     * Active participants per team: currently alive inside the arena PLUS returning participants due back. A team is
     * eliminated if and only if its active count is 0.
     */
    public Map<String, Integer> activeByTeam(Map<String, Boolean> liveInArena) {
        var out = new LinkedHashMap<String, Integer>();
        for (var entry : teams.entrySet()) {
            int count = 0;
            for (var name : entry.getValue()) {
                var e = entries.get(name);
                if (e == null) continue;
                if (e.state == ParticipantState.RETURNING
                        || (e.state == ParticipantState.ALIVE && Boolean.TRUE.equals(liveInArena.get(name)))) {
                    count++;
                }
            }
            out.put(entry.getKey(), count);
        }
        return out;
    }

    public Map<String, Integer> returningByTeam() {
        var out = new LinkedHashMap<String, Integer>();
        for (var entry : teams.entrySet()) {
            int count = 0;
            for (var name : entry.getValue()) {
                if (state(name) == ParticipantState.RETURNING) count++;
            }
            out.put(entry.getKey(), count);
        }
        return out;
    }

    public Map<String, Integer> respawnsLeftByTeam() {
        var out = new LinkedHashMap<String, Integer>();
        for (var team : teams.keySet()) out.put(team, teamRespawnsLeft(team));
        return out;
    }

    public BoardSummary boardSummary(Map<String, Boolean> liveInArena, int tick) {
        var byTeam = new LinkedHashMap<String, TeamBoard>();
        for (var entry : teams.entrySet()) {
            int alive = 0, returning = 0;
            for (var name : entry.getValue()) {
                var e = entries.get(name);
                if (e == null) continue;
                if (e.state == ParticipantState.RETURNING) returning++;
                else if (e.state == ParticipantState.ALIVE && Boolean.TRUE.equals(liveInArena.get(name))) alive++;
            }
            byTeam.put(entry.getKey(), new TeamBoard(alive, returning, teamRespawnsLeft(entry.getKey())));
        }
        return new BoardSummary(config.mode(), config.enabled(), Collections.unmodifiableMap(byTeam));
    }

    public Map<String, Object> statusSnapshot(Map<String, Boolean> liveInArena, int tick) {
        var out = new LinkedHashMap<String, Object>();
        out.put("mode", config.mode().wireName());
        out.put("count", config.count());
        out.put("delay_ticks", config.delayTicks());
        var pending = new ArrayList<Map<String, Object>>();
        for (var e : entries.values()) {
            if (e.state != ParticipantState.RETURNING) continue;
            var row = new LinkedHashMap<String, Object>();
            row.put("player", e.name);
            if (!e.team.isEmpty()) row.put("team", e.team);
            row.put("reservation_id", e.reservationId);
            row.put("ticks_left", ticksRemaining(e.name, tick));
            row.put("due", tick >= e.returnAtTick);
            pending.add(row);
        }
        pending.sort(Comparator.comparingInt(r -> (Integer) r.get("reservation_id")));
        out.put("pending", pending);
        if (!teams.isEmpty()) {
            var teamRows = new LinkedHashMap<String, Object>();
            for (var entry : teams.entrySet()) {
                int alive = 0, returning = 0, eliminated = 0;
                for (var name : entry.getValue()) {
                    var e = entries.get(name);
                    if (e == null) continue;
                    if (e.state == ParticipantState.RETURNING) returning++;
                    else if (e.state == ParticipantState.ALIVE && Boolean.TRUE.equals(liveInArena.get(name))) alive++;
                    else eliminated++;
                }
                var t = new LinkedHashMap<String, Object>();
                t.put("alive", alive);
                t.put("returning", returning);
                t.put("eliminated", eliminated);
                t.put("respawns_left", teamRespawnsLeft(entry.getKey()));
                teamRows.put(entry.getKey(), t);
            }
            out.put("teams", teamRows);
        }
        out.put("events", events());
        return out;
    }

    public List<Map<String, Object>> events() {
        return List.copyOf(eventLog);
    }

    /** Deterministic team pad selection on respawn, cycling cleanly across the team's pads. */
    public static int choosePadIndex(int assignedSlot, int padCount, int returnCount) {
        if (padCount <= 0) return 0;
        return Math.floorMod(assignedSlot + Math.max(0, returnCount), padCount);
    }
}
