package com.vulpine.mcb.arena;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
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
            "arena_spawn", "arena_op", "arena_kit", "arena_board", "arena_say");

    public static boolean isOperationName(String op) { return OPS.contains(op); }

    /**
     * The arena protocol, reported by arena_list so the lab can refuse a mismatch before a
     * match starts (mclab.arena.client.PROTOCOL; a test keeps the two equal). Raise it
     * whenever an op is added or a request gains or changes a field. 1: no report.
     * 2: arena_board, arena_say and arena_start's series.
     */
    public static final int PROTOCOL = 2;

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

    /**
     * A wave's mobs in spawn order, with at most {@code cap} wave mobs alive at once (the arena's {@code capacity.mobs}):
     * the cap comes when the wave starts and the rest as wave mobs fall, so a wave may be bigger than the arena holds.
     * Kinds take turns (a zombie, a skeleton, a zombie...), so a wave fed in parts stays mixed. Mobs a wave mob made
     * (vexes, reinforcements) count as alive too: they hold reinforcements back like any other wave mob.
     */
    public static final class WaveFeed {
        private final List<String> order = new ArrayList<>();
        private final int cap;
        private int next;

        public WaveFeed(Map<String, Integer> mobs, int cap) {
            if (cap < 1) throw new ArenaFail("over_capacity", "the arena takes no mobs");
            this.cap = cap;
            var left = new LinkedHashMap<>(mobs);
            while (!left.isEmpty()) {
                for (var it = left.entrySet().iterator(); it.hasNext(); ) {
                    var entry = it.next();
                    order.add(entry.getKey());
                    if (entry.getValue() <= 1) it.remove();
                    else entry.setValue(entry.getValue() - 1);
                }
            }
        }

        public int total() { return order.size(); }

        /** Mobs of the wave not spawned yet. */
        public int pending() { return order.size() - next; }

        /** The types to spawn now, with {@code alive} wave mobs alive: as many as the cap has room for. */
        public List<String> take(int alive) {
            int n = Math.min(Math.max(0, cap - alive), pending());
            var out = List.copyOf(order.subList(next, next + n));
            next += n;
            return out;
        }
    }

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

    /** The series standings board a lobby shows between matches (the arena_board op): one per arena. */
    public static String seriesBoardName(String arena) { return String.format("mcbs_%08x", arena.hashCode()); }

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
     * A match's tally: kills (wave mobs per participant, and so per team; in a team match, enemy players), downs, and the
     * health each participant took off enemies and wave mobs (dealt), lost (taken) and gave back to themselves and their
     * team (healing). {@code teamOf}: every participant's team, "" in co-op; {@code teams}: the teams in plan order.
     */
    public static final class Tally {
        private final Map<String, String> teamOf;
        private final List<String> teams;
        private final Map<String, Integer> mobs = new LinkedHashMap<>(), players = new LinkedHashMap<>(), deaths = new LinkedHashMap<>();
        private final Map<String, Double> dealt = new LinkedHashMap<>(), taken = new LinkedHashMap<>(), healing = new LinkedHashMap<>();

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

        private boolean enemies(String a, String b) {
            String team = teamOf.get(a);
            return team != null && !team.isEmpty() && teamOf.containsKey(b) && !team.equals(teamOf.get(b));
        }

        /**
         * Health taken off {@code victim} (a participant's name, else null) by {@code attacker} (a participant's name, else
         * null or an outsider's): taken by a participant whoever hit; dealt by a participant only to a wave mob or an enemy.
         */
        public void hurt(String attacker, String victim, boolean waveMob, double amount) {
            if (amount <= 0) return;
            if (victim != null && teamOf.containsKey(victim)) taken.merge(victim, amount, Double::sum);
            if (attacker == null || !teamOf.containsKey(attacker) || attacker.equals(victim)) return;
            if (waveMob || (victim != null && enemies(attacker, victim))) dealt.merge(attacker, amount, Double::sum);
        }

        /**
         * Health given back to {@code target}: by {@code healer} (a participant's name, "" for anything else) when it threw
         * or drank the potion, else (null) by the target's own body (regeneration, food). Healing is credited to a
         * participant who healed themselves or a teammate, never an enemy.
         */
        public void healed(String healer, String target, double amount) {
            if (amount <= 0 || !teamOf.containsKey(target)) return;
            String by = healer == null ? target : healer;
            if (!teamOf.containsKey(by)) return;
            if (!by.equals(target) && !teamOf.get(by).equals(teamOf.get(target))) return;
            healing.merge(by, amount, Double::sum);
        }

        /** A participant went down (died). */
        public void down(String victim) { if (victim != null && teamOf.containsKey(victim)) deaths.merge(victim, 1, Integer::sum); }

        public int mobs(String name) { return mobs.getOrDefault(name, 0); }

        public int players(String name) { return players.getOrDefault(name, 0); }

        public int deaths(String name) { return deaths.getOrDefault(name, 0); }

        public double dealt(String name) { return dealt.getOrDefault(name, 0.0); }

        public double taken(String name) { return taken.getOrDefault(name, 0.0); }

        public double healing(String name) { return healing.getOrDefault(name, 0.0); }

        /** Every participant's line, in participant order. */
        public List<Stat> stats() {
            var out = new ArrayList<Stat>();
            teamOf.forEach((name, team) -> out.add(new Stat(name, team, mobs(name), players(name), deaths(name), dealt(name), taken(name),
                    healing(name))));
            return out;
        }

        /** What the kill board shows for a player: every kill. */
        public int score(String name) { return mobs(name) + players(name); }

        /** Each team's wave mob kills, every team in plan order (the race's count). */
        public Map<String, Integer> byTeam() {
            var out = new LinkedHashMap<String, Integer>();
            for (var team : teams) out.put(team, 0);
            mobs.forEach((name, n) -> { var team = teamOf.get(name); if (out.containsKey(team)) out.merge(team, n, Integer::sum); });
            return out;
        }

        /** Per participant, in order: kills (mobs, players), downs, health dealt, taken and healed, team (when there are teams). */
        public Map<String, Map<String, Object>> rows() {
            var out = new LinkedHashMap<String, Map<String, Object>>();
            teamOf.forEach((name, team) -> {
                var row = new LinkedHashMap<String, Object>();
                row.put("mobs", mobs(name));
                row.put("players", players(name));
                row.put("deaths", deaths(name));
                row.put("damage_dealt", tenth(dealt(name)));
                row.put("damage_taken", tenth(taken(name)));
                row.put("healing", tenth(healing(name)));
                if (!team.isEmpty()) row.put("team", team);
                out.put(name, row);
            });
            return out;
        }
    }

    public static double tenth(double v) { return Math.round(v * 10) / 10.0; }

    /** One participant's match: kills, downs, and health dealt, taken and healed. */
    public record Stat(String name, String team, int mobKills, int playerKills, int deaths, double dealt, double taken, double healing) {
        public int kills() { return mobKills + playerKills; }
    }

    // ---- the sidebar and the recap

    /** The chat colours a board line or a recap part may wear (ChatFormatting's, lower case). */
    public static final Set<String> COLORS = Set.of("black", "dark_blue", "dark_green", "dark_aqua", "dark_red", "dark_purple", "gold",
            "gray", "dark_gray", "blue", "green", "aqua", "red", "light_purple", "yellow", "white");
    private static final List<String> TEAM_PALETTE = List.of("red", "blue", "green", "yellow", "light_purple", "aqua", "gold", "white");
    public static final int BOARD_LINES = 15;

    /** A team named for a colour wears it; any other takes the palette's colour for its place in the plan. */
    public static String teamColor(String team, int index) {
        String lower = team.toLowerCase(Locale.ROOT);
        return COLORS.contains(lower) ? lower : TEAM_PALETTE.get(Math.floorMod(index, TEAM_PALETTE.size()));
    }

    /** "red" as "Red". */
    public static String title(String word) { return word.isEmpty() ? word : Character.toUpperCase(word.charAt(0)) + word.substring(1); }

    /** One sidebar line: its text, the value shown at its right ("" for none), a colour of {@link #COLORS}, bold, struck through. */
    public record Line(String text, String value, String color, boolean bold, boolean struck) {}

    /** A block of the sidebar: a head line that always shows (null for none) and members that fold when space runs out. */
    public record Section(Line head, List<Line> members) {}

    /** A participant as the match board shows them. */
    public record Player(String name, String team, int kills, boolean alive) {}

    /** The wave the match is on (1-based), of how many, and the wave mobs still alive. */
    public record WaveInfo(int wave, int waves, int left) {}

    /**
     * The sections into {@code max} lines: every head stays; members share what is left (round robin, in their order),
     * and a section that cannot show them all ends with "+n more".
     */
    public static List<Line> fit(List<Section> sections, int max) {
        int heads = 0, total = 0;
        for (var s : sections) { if (s.head() != null) heads++; total += s.members().size(); }
        var out = new ArrayList<Line>();
        int[] quota = new int[sections.size()];
        if (heads + total <= max) {
            for (int i = 0; i < quota.length; i++) quota[i] = sections.get(i).members().size();
        } else {
            int folded = 0;
            for (int pass = 0; pass <= sections.size(); pass++) {
                deal(sections, quota, Math.max(0, max - heads - folded));
                int now = 0;
                for (int i = 0; i < quota.length; i++) if (quota[i] < sections.get(i).members().size()) now++;
                if (now == folded) break;
                folded = now;
            }
        }
        for (int i = 0; i < sections.size(); i++) {
            var s = sections.get(i);
            if (s.head() != null) out.add(s.head());
            out.addAll(s.members().subList(0, quota[i]));
            int rest = s.members().size() - quota[i];
            if (rest > 0) out.add(new Line("  +" + rest + " more", "", "gray", false, false));
        }
        return out.size() > max ? out.subList(0, max) : out;
    }

    private static void deal(List<Section> sections, int[] quota, int free) {
        java.util.Arrays.fill(quota, 0);
        boolean any = true;
        while (free > 0 && any) {
            any = false;
            for (int i = 0; i < quota.length && free > 0; i++) {
                if (quota[i] < sections.get(i).members().size()) { quota[i]++; free--; any = true; }
            }
        }
    }

    /**
     * The match's sidebar: the wave and the mobs left (when it has waves), then each team (its colour, how many are up,
     * its kills) with its players by kills, or in co-op everyone by kills. A downed player is greyed out.
     */
    public static List<Section> matchBoard(String mode, List<String> teams, List<Player> players, WaveInfo waves) {
        var out = new ArrayList<Section>();
        if (waves != null) {
            out.add(new Section(new Line("Wave", waves.wave() + "/" + waves.waves(), "yellow", false, false), List.of()));
            out.add(new Section(new Line("Mobs left", Integer.toString(waves.left()), "yellow", false, false), List.of()));
        }
        if (!sided(mode)) {
            out.add(new Section(null, playerLines(players, "white", "")));
            return out;
        }
        for (int i = 0; i < teams.size(); i++) {
            String team = teams.get(i), color = teamColor(team, i);
            var members = players.stream().filter(p -> p.team().equals(team)).toList();
            int alive = (int) members.stream().filter(Player::alive).count();
            int kills = members.stream().mapToInt(Player::kills).sum();
            var head = new Line(title(team) + " · " + alive + " alive", Integer.toString(kills), color, true, false);
            out.add(new Section(head, playerLines(members, color, " ")));
        }
        return out;
    }

    private static List<Line> playerLines(List<Player> players, String color, String indent) {
        var sorted = new ArrayList<>(players);
        sorted.sort((a, b) -> Integer.compare(b.kills(), a.kills()));
        var out = new ArrayList<Line>();
        for (var p : sorted) {
            out.add(new Line(indent + p.name(), Integer.toString(p.kills()), p.alive() ? color : "dark_gray", false, !p.alive()));
        }
        return out;
    }

    /** The live board's title: the match of its series, else the arena. */
    public static String matchTitle(String arena, int[] series) {
        return "Kills · " + (series == null ? arena : "Match " + series[0] + "/" + series[1]);
    }

    /** How a match ended, in a few words ({@code state}: won, lost or stopped). */
    public static String endTitle(String mode, String state, String winner, String reason) {
        if (state.equals("stopped")) return "Stopped";
        if (winner != null && !winner.isEmpty()) return title(winner) + " wins!";
        if ("draw".equals(reason)) return "Draw";
        return state.equals("won") ? "Victory!" : "Defeated";
    }

    /** A number of health points to a tenth, without a trailing ".0". */
    public static String fmt(double v) {
        double t = tenth(v);
        return t == Math.rint(t) ? Long.toString((long) t) : Double.toString(t);
    }

    /** The recap's first line: the match of the series, how it ended, how long it took (m:ss). */
    public static String recapHeader(String end, int[] series, int ticks) {
        int seconds = Math.max(0, ticks) / 20;
        String time = (seconds / 60) + ":" + String.format(Locale.ROOT, "%02d", seconds % 60);
        return (series == null ? "" : "Match " + series[0] + " of " + series[1] + " · ") + end + " · " + time;
    }

    /** A part of a chat line: its text, a colour of {@link #COLORS}, bold. */
    public record Seg(String text, String color, boolean bold) {}

    /**
     * The chat recap after a match: the header, then each team (its kills and damage dealt) with its players, or in
     * co-op everyone, each by damage dealt: kills, downs, health dealt, taken and healed. The top damage dealer is starred.
     */
    public static List<List<Seg>> recap(String header, List<String> teams, List<Stat> stats) {
        var out = new ArrayList<List<Seg>>();
        out.add(List.of(new Seg(header, "gold", true)));
        Stat top = null;
        for (var s : stats) if (s.dealt() > 0 && (top == null || s.dealt() > top.dealt())) top = s;
        if (teams.isEmpty()) {
            for (var s : byDealt(stats)) out.add(recapLine(s, "white", s == top));
            return out;
        }
        for (int i = 0; i < teams.size(); i++) {
            String team = teams.get(i), color = teamColor(team, i);
            var members = byDealt(stats.stream().filter(s -> s.team().equals(team)).toList());
            int kills = members.stream().mapToInt(Stat::kills).sum();
            double dealt = members.stream().mapToDouble(Stat::dealt).sum();
            out.add(List.of(new Seg(title(team), color, true), new Seg(" · " + count(kills, "kill") + " · dealt " + fmt(dealt), "gray", false)));
            for (var s : members) out.add(recapLine(s, color, s == top));
        }
        return out;
    }

    private static List<Stat> byDealt(List<Stat> stats) {
        var sorted = new ArrayList<>(stats);
        sorted.sort((a, b) -> Double.compare(b.dealt(), a.dealt()));
        return sorted;
    }

    private static String count(int n, String what) { return n + " " + what + (n == 1 ? "" : "s"); }

    private static List<Seg> recapLine(Stat s, String color, boolean top) {
        var line = new ArrayList<Seg>();
        line.add(new Seg(" ", "white", false));
        if (top) line.add(new Seg("★ ", "gold", true));
        line.add(new Seg(s.name(), color, false));
        var what = new StringBuilder(": ").append(count(s.kills(), "kill"));
        if (s.mobKills() > 0 && s.playerKills() > 0) what.append(" (").append(count(s.playerKills(), "player")).append(")");
        if (s.deaths() > 0) what.append(", ").append(count(s.deaths(), "down"));
        what.append(" · dealt ").append(fmt(s.dealt())).append(" · took ").append(fmt(s.taken())).append(" · healed ").append(fmt(s.healing()));
        line.add(new Seg(what.toString(), "gray", false));
        return line;
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
