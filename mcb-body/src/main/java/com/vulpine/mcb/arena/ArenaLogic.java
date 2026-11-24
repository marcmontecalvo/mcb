package com.vulpine.mcb.arena;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The arena runtime's rules without Minecraft (checked by ArenaLogicTest): which items an arena refuses, which spawns
 * count as made by a wave mob, the wave clock, team assignment and results, spawn order, and small helpers. The
 * runtime (ArenaRuntime, ArenaMatch) feeds these the world's facts and acts on the answers.
 */
public final class ArenaLogic {
    private ArenaLogic() {}

    /** The ops the arena runtime owns. */
    public static final Set<String> OPS = Set.of("arena_build", "arena_remove", "arena_list", "arena_status", "arena_start", "arena_stop",
            "arena_spawn", "arena_op", "arena_kit");

    public static boolean isOperationName(String op) { return OPS.contains(op); }

    /**
     * Whether a joining (or bedless respawning) player starts exactly at the world spawn, given the state of the arena
     * holding it (null for none). Outside adventure mode the game lifts them to the top block above the spawn, which
     * in the arena server's void world is the lobby's roof; inside a built arena the spawn was put there on purpose.
     */
    public static boolean keepsSpawnExact(String arenaState) { return "built".equals(arenaState); }

    /**
     * A match never takes, kits or heals a player on the death screen: healed, the game takes them for alive and
     * ignores their respawn click; sent to a pad, their team is out before the match begins. The caller waits for
     * them to respawn ({@code /mcb ready} again) instead.
     */
    public static void requireAlive(String name, boolean dead) {
        if (dead) throw new ArenaFail("participant_dead", name + " is dead: wait for them to respawn");
    }

    /** An item used within this many blocks of an arena's bounds counts as used on it (a bucket's ray reaches). */
    public static final int INTERACT_MARGIN = 6;
    /** A derived spawn (a vex, a split slime) is credited to a wave mob this near. */
    public static final double DERIVED_RANGE = 32.0;

    // ---- protection

    /** Items an arena refuses to anyone: they place or remove fluid, start fire or grow plants. Block items are refused too (the runtime tests the item class). */
    public static boolean restrictedItem(String id) {
        if (id.equals("minecraft:milk_bucket")) return false;  // only drinks
        return id.endsWith("_bucket") || id.equals("minecraft:bucket") || id.equals("minecraft:flint_and_steel")
                || id.equals("minecraft:fire_charge") || id.equals("minecraft:bone_meal");
    }

    /** Items that put an entity into the world (a boat, a minecart, an armor stand, an end crystal): refused inside the bounds. */
    public static boolean entityItem(String id) {
        return id.endsWith("_boat") || id.endsWith("_raft") || id.endsWith("minecart") || id.equals("minecraft:armor_stand")
                || id.equals("minecraft:end_crystal");
    }

    // ---- who counts as made by a wave mob

    /** Which spawn reasons a child type may have, and which wave mob types may be its parent. */
    public record Rule(Set<String> reasons, Set<String> parents) {}

    private static final Set<String> ZOMBIES = Set.of("minecraft:zombie", "minecraft:husk", "minecraft:drowned", "minecraft:zombie_villager");
    private static final Set<String> RAIDERS = Set.of("minecraft:pillager", "minecraft:vindicator", "minecraft:evoker",
            "minecraft:ravager", "minecraft:witch");
    private static final Map<String, List<Rule>> RULES = new LinkedHashMap<>();

    private static void rule(Set<String> children, Set<String> reasons, Set<String> parents) {
        for (var child : children) RULES.computeIfAbsent(child, c -> new ArrayList<>()).add(new Rule(reasons, parents));
    }

    static {
        rule(Set.of("minecraft:vex"), Set.of("MOB_SUMMONED"), Set.of("minecraft:evoker"));
        rule(ZOMBIES, Set.of("REINFORCEMENT"), ZOMBIES);
        rule(Set.of("minecraft:slime"), Set.of("TRIGGERED"), Set.of("minecraft:slime"));
        rule(Set.of("minecraft:magma_cube"), Set.of("TRIGGERED"), Set.of("minecraft:magma_cube"));
        rule(RAIDERS, Set.of("EVENT", "PATROL"), RAIDERS);
        rule(Set.of("minecraft:skeleton"), Set.of("JOCKEY"), Set.of("minecraft:spider"));
        rule(Set.of("minecraft:chicken"), Set.of("JOCKEY"), ZOMBIES);
    }

    /** The rules that let this child type be made by a wave mob, or null when none can. */
    public static List<Rule> rule(String child) { return RULES.get(child); }

    /** Whether a mob of this type, added for this spawn reason, may be the child of a wave mob of the parent type. */
    public static boolean derived(String reason, String child, String parent) {
        var rules = RULES.get(child);
        if (rules == null) return false;
        for (var r : rules) if (r.reasons().contains(reason) && r.parents().contains(parent)) return true;
        return false;
    }

    // ---- the wave clock

    public enum Kind { NONE, SPAWN, CLEARED, WON, LOST }

    /** What the clock asks for this tick: spawn wave {@code wave}, say it was cleared, or the match ended. */
    public record Step(Kind kind, int wave) {}

    private static final Step NOTHING = new Step(Kind.NONE, -1);

    private enum Phase { WAITING, ACTIVE, FINISHING, DONE }

    /**
     * The waves of a match in order (pure state machine, one {@link #tick} per server tick). Each wave waits its delay
     * from the match start or the clearing of the one before; it is cleared when no live mob of it is left; the last
     * clearing wins; no live participant loses at once, whatever the phase.
     */
    public static final class WaveClock {
        private final List<Integer> delays;
        private Phase phase = Phase.WAITING;
        private int index, remaining;

        public WaveClock(List<Integer> delays) {
            if (delays.isEmpty()) throw new ArenaFail("bad_request", "a match needs at least one wave");
            for (int d : delays) if (d < 0) throw new ArenaFail("bad_request", "a delay is never negative");
            this.delays = List.copyOf(delays);
            this.remaining = this.delays.get(0);
        }

        public int waves() { return delays.size(); }

        /** The wave being waited for or fought. */
        public int index() { return index; }

        /** Ticks until the next wave spawns while waiting, else 0. */
        public int nextIn() { return phase == Phase.WAITING ? remaining : 0; }

        public boolean finished() { return phase == Phase.DONE; }

        public boolean waiting() { return phase == Phase.WAITING; }

        public Step tick(int liveWaveMobs, boolean participantsAlive) {
            if (phase == Phase.DONE) return NOTHING;
            if (!participantsAlive) {
                phase = Phase.DONE;
                return new Step(Kind.LOST, index);
            }
            switch (phase) {
                case WAITING -> {
                    if (remaining > 0) remaining--;
                    if (remaining == 0) {
                        phase = Phase.ACTIVE;
                        return new Step(Kind.SPAWN, index);
                    }
                }
                case ACTIVE -> {
                    if (liveWaveMobs == 0) {
                        int cleared = index++;
                        if (index == delays.size()) phase = Phase.FINISHING;
                        else { phase = Phase.WAITING; remaining = delays.get(index); }
                        return new Step(Kind.CLEARED, cleared);
                    }
                }
                case FINISHING -> {
                    phase = Phase.DONE;
                    return new Step(Kind.WON, delays.size() - 1);
                }
                default -> { }
            }
            return NOTHING;
        }
    }

    // ---- spawning

    /** Mob number {@code i} of a wave goes to this spawn marker: round robin. */
    public static int spawnMarker(int mobIndex, int markers) { return mobIndex % markers; }

    /** Cells to try, nearest first, when the marker itself has no room: the marker, then a ring and a step up. */
    public static List<int[]> searchOffsets() {
        var out = new ArrayList<int[]>();
        for (int dy = 0; dy <= 2; dy++) {
            for (int r = 0; r <= 2; r++) {
                for (int dx = -r; dx <= r; dx++) {
                    for (int dz = -r; dz <= r; dz++) {
                        if (Math.max(Math.abs(dx), Math.abs(dz)) == r) out.add(new int[] {dx, dy, dz});
                    }
                }
            }
        }
        return out;
    }

    /** Far enough to see anyone in the arena from anywhere in it (the follow range attribute tops out at 2048). */
    public static double followRange(ArenaBox box) {
        double diagonal = Math.sqrt((double) (box.maxX() - box.minX() + 1) * (box.maxX() - box.minX() + 1)
                + (double) (box.maxY() - box.minY() + 1) * (box.maxY() - box.minY() + 1)
                + (double) (box.maxZ() - box.minZ() + 1) * (box.maxZ() - box.minZ() + 1));
        return Math.max(20.0, Math.min(2048.0, Math.ceil(diagonal) + 16.0));
    }

    /** The index of the closest live position to (x,y,z), or -1 when none is live. */
    public static int nearest(double x, double y, double z, double[][] positions, boolean[] live) {
        int best = -1;
        double bestSq = Double.MAX_VALUE;
        for (int i = 0; i < positions.length; i++) {
            if (!live[i]) continue;
            double dx = positions[i][0] - x, dy = positions[i][1] - y, dz = positions[i][2] - z;
            double sq = dx * dx + dy * dy + dz * dz;
            if (sq < bestSq) { bestSq = sq; best = i; }
        }
        return best;
    }

    // ---- teams

    /** Players dealt out round robin over the arena's teams, in plan order. */
    public static Map<String, List<String>> assignTeams(List<String> teams, List<String> participants) {
        if (teams.size() < 2) throw new ArenaFail("not_enough_teams", "the arena has " + teams.size() + " team marker(s)");
        if (participants.size() < 2) throw new ArenaFail("not_enough_participants", "a team match needs two or more players");
        var out = new LinkedHashMap<String, List<String>>();
        for (var team : teams) out.put(team, new ArrayList<>());
        for (int i = 0; i < participants.size(); i++) out.get(teams.get(i % teams.size())).add(participants.get(i));
        return out;
    }

    /** An explicit assignment checked against the arena: known teams, no one twice, two sides, a pad and a place for each. */
    public static void checkTeams(Map<String, List<String>> assignment, Set<String> arenaTeams, Map<String, Integer> pads, int perTeam) {
        var seen = new LinkedHashSet<String>();
        int sides = 0;
        for (var entry : assignment.entrySet()) {
            if (!arenaTeams.contains(entry.getKey())) throw new ArenaFail("unknown_team", entry.getKey());
            for (var player : entry.getValue()) if (!seen.add(player)) throw new ArenaFail("duplicate_participant", player);
            if (!entry.getValue().isEmpty()) sides++;
        }
        if (sides < 2) throw new ArenaFail("not_enough_teams", "a match needs two teams with players");
        for (var entry : assignment.entrySet()) {
            int n = entry.getValue().size();
            if (n > pads.getOrDefault(entry.getKey(), 0)) throw new ArenaFail("not_enough_pads", entry.getKey());
            if (n > perTeam) throw new ArenaFail("over_capacity", entry.getKey() + " has " + n + " players, the arena allows " + perTeam);
        }
    }

    /** A start pad: the team marker it belongs to and its index among that team's pads. */
    public record Pad(String team, int index) {}

    /**
     * Where a waves match's players start: one pad each, every team's pads in plan order (they all
     * fight the waves on one side). `pads`: pad count per team in plan order; `capacity`: the most
     * players the arena takes.
     */
    public static List<Pad> wavePads(Map<String, Integer> pads, int players, int capacity) {
        if (players < 1) throw new ArenaFail("no_participants", "a waves match needs a player");
        if (players > capacity) throw new ArenaFail("over_capacity", players + " players, the arena allows " + capacity);
        var out = new ArrayList<Pad>();
        for (var entry : pads.entrySet())
            for (int i = 0; i < entry.getValue() && out.size() < players; i++) out.add(new Pad(entry.getKey(), i));
        if (out.size() < players) throw new ArenaFail("not_enough_pads", players + " players, " + out.size() + " pads");
        return out;
    }

    /** null while two or more teams have live members; else the one team left, or "" when none is (a draw). */
    public static String teamResult(Map<String, Integer> liveByTeam) {
        String last = "";
        int alive = 0;
        for (var entry : liveByTeam.entrySet()) {
            if (entry.getValue() > 0) { alive++; last = entry.getKey(); }
        }
        return alive >= 2 ? null : last;
    }

    /** A vanilla scoreboard team name for one arena's team: 12 characters, unique per arena and team. */
    public static String teamName(String arena, String team) {
        return String.format("mcb_%08x", (arena + "/" + team).hashCode());
    }

    /** The match's kill board (the sidebar objective): 13 characters, one per arena. */
    public static String objectiveName(String arena) { return String.format("mcbk_%08x", arena.hashCode()); }

    // ---- the games

    /**
     * waves: everyone on one side against the mob waves (co-op). teams: team against team, the last team standing wins;
     * mob waves come too when the match has any. race: teams on their own pads race to kill the most wave mobs.
     */
    public static final Set<String> MODES = Set.of("waves", "teams", "race");

    /** Whether the game's players start on their own team's pads (else every pad is one side's). */
    public static boolean sided(String mode) { return mode.equals("teams") || mode.equals("race"); }

    /** Whether participants may hurt each other: only team against team (and never a teammate: friendly fire is off). */
    public static boolean pvp(String mode) { return mode.equals("teams"); }

    /** How a match ended: won (by {@code winner} when teams play), else lost; {@code reason} says why when it is not plain. */
    public record Outcome(boolean won, String winner, String reason) {}

    /**
     * Whether the match ends this tick, and how; null plays on. {@code step}: the wave clock's answer this tick (null when the
     * match has no waves); {@code live}: each team's live participants; {@code kills}: each team's wave mob kills.
     */
    public static Outcome decide(String mode, Step step, Map<String, Integer> live, Map<String, Integer> kills) {
        var kind = step == null ? Kind.NONE : step.kind();
        switch (mode) {
            case "waves" -> {
                if (kind == Kind.WON) return new Outcome(true, null, null);
                if (kind == Kind.LOST) return new Outcome(false, null, "participants_down");
                return null;
            }
            case "teams" -> {
                var left = teamResult(live);
                if (left == null) return null;
                return left.isEmpty() ? new Outcome(false, null, "draw") : new Outcome(true, left, null);
            }
            case "race" -> {
                if (kind != Kind.WON && kind != Kind.LOST) return null;
                var best = raceWinner(kills);
                if (best.isEmpty()) return new Outcome(false, null, "draw");
                return new Outcome(true, best, kind == Kind.LOST ? "participants_down" : null);
            }
            default -> throw new ArenaFail("bad_request", "unknown game " + mode);
        }
    }

    /** The team with the most kills, or "" when the top is shared (none at all included). */
    public static String raceWinner(Map<String, Integer> kills) {
        String best = "";
        int top = -1;
        boolean shared = false;
        for (var entry : kills.entrySet()) {
            if (entry.getValue() > top) { top = entry.getValue(); best = entry.getKey(); shared = false; }
            else if (entry.getValue() == top) shared = true;
        }
        return shared || top <= 0 ? "" : best;
    }

    /**
     * A match's kills: wave mobs per participant (and so per team), and in a team match, enemy players. {@code teamOf}:
     * every participant's team, "" in co-op; {@code teams}: the teams in plan order.
     */
    public static final class Tally {
        private final Map<String, String> teamOf;
        private final List<String> teams;
        private final Map<String, Integer> mobs = new LinkedHashMap<>(), players = new LinkedHashMap<>();

        public Tally(Map<String, String> teamOf, List<String> teams) {
            this.teamOf = new LinkedHashMap<>(teamOf);
            this.teams = List.copyOf(teams);
        }

        /** A wave mob killed by {@code killer}: counted when the killer is a participant. */
        public boolean mob(String killer) {
            if (killer == null || !teamOf.containsKey(killer)) return false;
            mobs.merge(killer, 1, Integer::sum);
            return true;
        }

        /** {@code victim} taken down by {@code killer}: counted only for an enemy on another team. */
        public boolean player(String killer, String victim) {
            if (killer == null || killer.equals(victim) || !teamOf.containsKey(killer) || !teamOf.containsKey(victim)) return false;
            String team = teamOf.get(killer);
            if (team.isEmpty() || team.equals(teamOf.get(victim))) return false;
            players.merge(killer, 1, Integer::sum);
            return true;
        }

        public int mobs(String name) { return mobs.getOrDefault(name, 0); }

        public int players(String name) { return players.getOrDefault(name, 0); }

        /** What the kill board shows for a player: every kill. */
        public int score(String name) { return mobs(name) + players(name); }

        /** Each team's wave mob kills, every team in plan order (the race's count). */
        public Map<String, Integer> byTeam() {
            var out = new LinkedHashMap<String, Integer>();
            for (var team : teams) out.put(team, 0);
            mobs.forEach((name, n) -> { var team = teamOf.get(name); if (out.containsKey(team)) out.merge(team, n, Integer::sum); });
            return out;
        }

        /** Per participant, in order: mobs, players and team (when there are teams). */
        public Map<String, Map<String, Object>> rows() {
            var out = new LinkedHashMap<String, Map<String, Object>>();
            teamOf.forEach((name, team) -> {
                var row = new LinkedHashMap<String, Object>();
                row.put("mobs", mobs(name));
                row.put("players", players(name));
                if (!team.isEmpty()) row.put("team", team);
                out.put(name, row);
            });
            return out;
        }
    }

    // ---- tags and small helpers

    public static final String TAG_PREFIX = "mcb_arena:";

    public static String waveTag(String arena) { return TAG_PREFIX + arena; }

    /** The arena a wave tag names, or null for any other tag. */
    public static String arenaOfTag(String tag) {
        return tag.startsWith(TAG_PREFIX) && tag.length() > TAG_PREFIX.length() ? tag.substring(TAG_PREFIX.length()) : null;
    }

    public static int percent(long done, long total) { return total <= 0 ? 100 : (int) (done * 100 / total); }

    /** The bounds must sit inside the world's build height. */
    public static void checkHeight(ArenaBox box, int minY, int maxY) {
        if (box.minY() < minY || box.maxY() > maxY || Math.abs(box.minX()) > 29_999_000 || Math.abs(box.maxX()) > 29_999_000
                || Math.abs(box.minZ()) > 29_999_000 || Math.abs(box.maxZ()) > 29_999_000)
            throw new ArenaFail("out_of_world", "y " + box.minY() + ".." + box.maxY() + " must be inside " + minY + ".." + maxY
                    + " and the world border");
    }
}
